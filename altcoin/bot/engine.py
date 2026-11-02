"""Engine giao dịch: PaperEngine (mô phỏng) và LiveEngine (OKX thật) – cùng giao diện.

Cách thoát lệnh (EXIT_MODE): fixed | hybrid ("ăn sóng": chốt 1 phần rồi bám sóng) | trail | auto.
Logic thoát nằm ở exits.py và được dùng y hệt trong mô phỏng bóng của bộ tự học.

v3.3 – paper phản chiếu live:
  * Vào lệnh ở giá last MỚI lấy lúc quyết định (stamp), không phải giá đóng nến 15m đã cũ vài phút. SL giữ nguyên mức
    giá theo cấu trúc; 1R, TP1, cỡ lệnh tính lại từ giá khớp. Giá khớp đã vượt SL (range: vượt TP cố định) → bỏ, ghi lý
    do (fill_check) vào tín hiệu bóng; FILL_RECHECK=true bật thêm cổng chiến lược xét lại ở giá mới.
  * Paper: giá khớp = giá last ± SLIPPAGE, phí taker hai chiều (costs.py – cùng mô hình với mô phỏng bóng & tự học).
    Live: cỡ lệnh theo giá last mới + trượt giá dự tính; khớp xong đo rủi ro thật từ giá khớp thật, vượt kế hoạch quá
    LIVE_RISK_TOL (hoặc vượt MIN_LOT_RISK_MULT × rủi ro dự tính) thì cắt bớt hợp đồng, không cắt được thì đóng nếu vượt
    MIN_LOT_RISK_MULT, và báo.
  * Theo dõi lệnh (_track, paper và live dùng chung): đọc MỌI nến 1m giá last ĐÃ ĐÓNG kể từ lần xét trước (bar_ts),
    mỗi nến đúng một lần: SL trước, rồi TP/TP1, rồi trailing – không bỏ sót râu nến giữa hai lần kiểm tra (bản cũ chỉ
    đọc nến 1m đang chạy). Giá last là loại giá SL/TP trên sàn kích hoạt (exchange.open_position) và là giá của mô
    phỏng bóng / backtest; bản cũ live dùng giá mark cho TP1/trailing. Một lần chạm được xử lý chậm nhất ~1 phút +
    MONITOR_INTERVAL. Khác biệt còn lại: live chốt TP1 bằng lệnh market lúc phát hiện, paper khớp đúng mức TP1.
  * Nến đóng lúc bot KHÔNG theo dõi (vừa khởi động / tiếp quản, vòng lặp kẹt quá MAX_PASS_GAP – _watch_from): chỉ lệnh
    SL/TP đang nằm trên sàn có thể khớp (live: sàn tự khớp; paper: khớp như sàn); TP1, dời SL, STALL/TIMEOUT không chạy
    lại trên nến cũ – live không gửi lệnh market theo giá của mấy giờ trước, paper làm y như vậy.
  * Live: cột sl = SL bot muốn, sl_exch = SL đang nằm trên sàn. Sàn chưa nhận SL mới (từ chối, lỗi mạng) → bot tự giữ SL
    đó (giá last đã vượt → đóng bằng lệnh market) và thử đặt lại mỗi lượt. Lệnh đóng lỗi → lệnh vẫn mở (close_req), thử
    lại lượt sau; đọc vị thế lỗi → bỏ qua lượt đối chiếu; sàn không còn vị thế mà chưa thấy lệnh khớp đóng → chờ xác nhận.
  * Chốt TP1 làm tròn XUỐNG theo bước lot của sàn (cả paper lẫn live); lot không chia được → giữ toàn bộ làm phần bám sóng.
  * Cột fee = phí hai chiều của mọi phần đã đóng; risk_usdt / risk_mult = rủi ro THẬT (sau làm tròn lot, theo giá khớp).
"""
import atexit
import json
import logging
import math
import signal
import threading
import time
from contextlib import contextmanager
from .config import cfg
from .risk import position_notional, margin_for, risk_mult, risk_budget, risk_of
from . import costs
from . import exits
from . import strategy
from .exchange import algo_orders

log = logging.getLogger("engine")

# Vùng găng (v3.2.1): lệnh trên sàn và các bước theo sau nó (ghi DB, dời SL trên sàn, báo tin) là MỘT bước,
# SIGTERM không được cắt ngang. Luồng chính: SIGTERM/SIGINT rơi vào giữa được giữ lại, ra khỏi vùng mới phát
# lại (bot.main biến SIGTERM thành KeyboardInterrupt); vùng lồng nhau phát lại một lần, sau vùng ngoài cùng.
# Luồng khác (/close qua chat, web) giữ _ORDER, và lúc thoát tiến trình chờ nó xong, tối đa EXIT_WAIT giây
# (host gửi SIGKILL 15 s sau SIGTERM).
_ORDER = threading.RLock()
EXIT_WAIT = 12
TERMINAL = ("SL", "TP", "STALL", "TIMEOUT")
CLOSED_LAG = 5          # giây: nến 1m coi là đã đóng khi đã qua giờ đóng 5 giây (lệch đồng hồ, sàn cập nhật trễ)
MAX_PASS_GAP = 600      # giây: hai lượt kiểm tra cách nhau lâu hơn → giữa hai lượt bot không theo dõi (kẹt, mất leader)


@contextmanager
def critical():
    with _ORDER:
        if threading.current_thread() is not threading.main_thread():
            yield
            return
        held = []
        old = {s: signal.signal(s, lambda n, _f: held.append(n)) for s in (signal.SIGTERM, signal.SIGINT)}
        try:
            yield
        finally:
            for s, h in old.items():
                signal.signal(s, signal.SIG_DFL if h is None else h)
            if held:
                signal.raise_signal(held[0])


atexit.register(lambda: _ORDER.acquire(timeout=EXIT_WAIT))


def current_exit_mode():
    if cfg.EXIT_MODE in exits.MODES:
        return cfg.EXIT_MODE
    return strategy.LEARNED.get("exit_mode") or "hybrid"      # auto: bộ tự học chọn


def plan_exit(sig, entry):
    """(mode, tp, tp1) cho 1 tín hiệu. Setup range (đảo chiều trong vùng đi ngang) có TP riêng = dải giữa
    Bollinger → luôn thoát kiểu fixed; setup xu hướng dùng cách thoát hiện hành (bộ tự học chọn)."""
    if (sig.context or {}).get("setup") == "range":
        return "fixed", sig.tp, None
    mode = current_exit_mode()
    tp, tp1 = exits.levels(sig.side, entry, sig.sl, sig.atr, mode)
    return mode, tp, tp1


def stamp(okx, sig):
    """Giá last MỚI lúc quyết định → sig.ref_price / sig.ref_ts. Đã có thì giữ: lệnh vào và dòng tín hiệu bóng của
    nó dùng cùng một giá. Không lấy được (mạng) → None."""
    if sig.ref_price:
        return sig.ref_price
    try:
        p = float(okx.price(sig.symbol))
    except Exception as e:
        log.warning("%s: không lấy được giá mới: %s", sig.symbol, str(e)[:120])
        return None
    if p > 0:
        sig.ref_price, sig.ref_ts = p, time.time()
        return p
    return None


def fill_check(sig, entry, ref=None):
    """Kế hoạch còn đặt được không khi vào ở giá khớp `entry` (= ref ± trượt giá; ref: giá last lúc quyết định).
    '' = vào được, ngược lại là lý do bỏ. SL giữ nguyên mức giá theo cấu trúc, R tính lại từ giá khớp.
    Mặc định (sửa lỗi, KHÔNG đổi chiến lược – lệnh live bản cũ cũng vào ở giá thị trường mới): chỉ bỏ khi SL đã nằm sai
    phía giá khớp, hoặc setup range (TP cố định = dải giữa) mà giá đã vượt TP – sàn không nhận SL/TP như vậy.
    FILL_RECHECK=true bật thêm các cổng CHIẾN LƯỢC xét lại ở giá last (đổi tập lệnh được vào – người dùng tự quyết):
      * giá không được chạy ngược về phía SL quá MAX_FILL_DRIFT_R (theo R kế hoạch, từ giá đóng nến tín hiệu);
      * SL cách giá ≤ MAX_SL_ATR ATR (range: RANGE_MAX_SL_ATR) và – setup range – R:R tới TP thật (dải giữa) ≥ RANGE_MIN_RR.
        Setup xu hướng không có cổng R:R (strategy.min_rr_for): TP dựng lại từ giá khớp, luôn ≥ 1.2 × MIN_RR × R."""
    ref = entry if ref is None else ref
    sign = 1 if sig.side == "long" else -1
    if (entry - sig.sl) * sign <= 0 or (ref - sig.sl) * sign <= 0:
        return f"giá đã vượt SL (vào {entry:.6g}, SL {sig.sl:.6g})"
    if (sig.context or {}).get("setup") == "range" and sig.tp and \
            ((sig.tp - entry) * sign <= 0 or (sig.tp - ref) * sign <= 0):
        return f"giá đã vượt TP (vào {entry:.6g}, TP {sig.tp:.6g})"
    if not cfg.FILL_RECHECK:
        return ""
    plan = abs(sig.price - sig.sl)
    drift = sign * (ref - sig.price) / plan if plan else 0.0
    if drift < -cfg.MAX_FILL_DRIFT_R:
        return f"giá đã chạy ngược {-drift:.2f}R về phía SL từ lúc đóng nến tín hiệu (tối đa {cfg.MAX_FILL_DRIFT_R:g}R)"
    risk = abs(ref - sig.sl)
    is_range = (sig.context or {}).get("setup") == "range"
    max_atr = cfg.RANGE_MAX_SL_ATR if is_range else cfg.MAX_SL_ATR
    if sig.atr and risk > max_atr * sig.atr + 1e-12:
        return f"giá đã chạy xa: SL cách giá {risk / sig.atr:.1f} ATR > {max_atr:g}"
    need = strategy.min_rr_for(sig)
    if need is not None:
        _, tp, _ = plan_exit(sig, ref)
        rr = sign * (tp - ref) / risk
        if rr < need - 1e-9:
            return f"R:R từ giá hiện tại còn {rr:.2f} < {need:g}"
    return ""


class BaseEngine:
    def __init__(self, okx, store, notify):
        self.okx, self.store, self.notify = okx, store, notify
        self.on_close = None   # callback(trade, exit, pnl, pnl_pct, reason) – Bot gắn vào để AI tổng kết
        self._last_pass = None  # giờ của lượt monitor trước (trong tiến trình này) – xem _watch_from
        self._missing = {}      # live: id lệnh → số lượt liên tiếp sàn không còn vị thế mà chưa thấy lệnh khớp đóng
        self._sl_warned = set()  # live: id lệnh đã báo "sàn chưa nhận SL mới" (báo một lần)

    # ---------- helpers
    def _pnl(self, side, entry, exit_, notional):
        """(PnL ròng, phí hai chiều) của phần `notional` (giá trị lúc vào) vào ở `entry`, ra ở `exit_` (giá khớp)."""
        qty = notional / entry
        gross = (exit_ - entry) * qty if side == "long" else (entry - exit_) * qty
        fee = costs.fee(notional) + costs.fee(qty * exit_)
        return gross - fee, fee

    def _prepare(self, sig):
        """Giá khớp vào dự tính từ giá last mới: (ref, entry) hoặc None (sig.skip ghi lý do)."""
        ref = stamp(self.okx, sig)
        if not ref:
            sig.skip = "không lấy được giá mới"
            return None
        entry = costs.entry_fill(sig.side, ref)
        why = fill_check(sig, entry, ref)
        if why:
            sig.skip = why
            log.info("%s %s: không vào – %s (giá đóng nến %.6g, giá last %.6g)", sig.symbol, sig.side, why, sig.price, ref)
            return None
        return ref, entry

    def _size(self, sig, ai, equity, entry):
        """(notional, hệ số rủi ro): rủi ro cơ bản × chất lượng tín hiệu × chuỗi thua, trong ngân sách rủi ro còn lại.
        entry: giá khớp vào (dự tính) – khoảng cách SL thật."""
        try:
            recent = self.store.last_closed(6)
        except Exception:
            recent = []
        mult = risk_mult(sig, ai, recent)
        budget = risk_budget(equity, self.store.open_trades())
        notional = position_notional(equity, entry, sig.sl, mult=mult, budget=budget)
        if notional < 1:
            sig.skip = f"hết ngân sách rủi ro (còn {budget:.3f} USDT)"
            log.info("%s: không mở – ngân sách rủi ro còn %.3f USDT (trần %.0f%% vốn)", sig.symbol, budget, cfg.MAX_TOTAL_RISK * 100)
        return notional, mult

    def _lot(self, symbol):
        m = self.okx.market(symbol)
        lot = float(m["limits"]["amount"].get("min") or 1)
        step = float((m.get("precision") or {}).get("amount") or lot)
        return lot, step, float(m.get("contractSize") or 1)

    def _lot_floor(self, symbol, contracts):
        """Làm tròn XUỐNG theo bước lot của sàn (như ccxt amount_to_precision của OKX); dưới lot tối thiểu → 0."""
        lot, step, _ = self._lot(symbol)
        n = round(math.floor(contracts / step + 1e-9) * step, 10)
        return n if n >= lot - 1e-12 else 0.0

    def _fit_lot(self, sig, notional, free, entry):
        """Làm tròn theo lot của sàn ở giá khớp `entry`. Trả về (contracts, notional thật) hoặc (0, 0) nếu không nên vào:
        lot tối thiểu vượt ký quỹ, hoặc làm rủi ro thật gấp > MIN_LOT_RISK_MULT lần rủi ro dự tính."""
        contracts, min_needed = self.okx.contracts_for_notional(sig.symbol, notional, entry)
        if contracts == 0:
            if min_needed > free * cfg.LEVERAGE * 0.9:
                sig.skip = f"không đủ vốn cho lot tối thiểu ({min_needed:.2f} USDT)"
                log.info("%s: không đủ vốn cho lot tối thiểu (%.2f USDT)", sig.symbol, min_needed)
                return 0.0, 0.0
            if risk_of(min_needed, entry, sig.sl) > cfg.MIN_LOT_RISK_MULT * risk_of(notional, entry, sig.sl):
                sig.skip = f"lot tối thiểu làm rủi ro gấp >{cfg.MIN_LOT_RISK_MULT:g}× dự tính"
                log.info("%s: lot tối thiểu %.2f USDT làm rủi ro gấp >%gx dự tính (%.2f USDT) → bỏ", sig.symbol, min_needed,
                         cfg.MIN_LOT_RISK_MULT, notional)
                return 0.0, 0.0
            contracts, _ = self.okx.contracts_for_notional(sig.symbol, min_needed * 1.01, entry)
            if contracts == 0:
                sig.skip = "không làm tròn được theo lot"
                return 0.0, 0.0
        return contracts, contracts * self._lot(sig.symbol)[2] * entry

    def _trade_row(self, sig, ai, entry, notional, contracts, order_id="", mult=1.0, equity=None, tp=None,
                   risk_plan=None):
        mode, tp_plan, tp1 = plan_exit(sig, entry)
        tp = tp_plan if tp is None else tp       # live: TP đã gửi lên sàn
        risk_usdt = risk_of(notional, entry, sig.sl)
        base = (equity or 0) * cfg.RISK_PER_TRADE
        return dict(
            mode=cfg.MODE, symbol=sig.symbol, side=sig.side, entry=entry, exit=None,
            sl=sig.sl, tp=tp, notional=notional, margin=margin_for(notional), contracts=contracts,
            pnl=0, pnl_pct=0, fee=0, opened_at=time.time(), closed_at=None, status="open",
            reason_open="; ".join(sig.reasons) + (f" | AI: {ai.get('reason','')}" if ai else ""),
            reason_close="", ai_model=(ai or {}).get("model"), ai_conf=(ai or {}).get("confidence"),
            score=sig.score, order_id=order_id,
            tp1=tp1, tp1_done=0, peak=entry, peak_ts=time.time(), realized=0.0, atr=sig.atr,
            notional0=notional, exit_mode=mode, sl0=sig.sl, tp1_ts=None,
            # v3.3: rủi ro THẬT (sau làm tròn lot, theo giá khớp) – risk_mult = bội số của rủi ro cơ bản; mult_plan = hệ số
            # chất lượng × chuỗi thua dự tính; sig_price = giá đóng nến tín hiệu, ref_price = giá last lúc vào
            risk_usdt=risk_usdt, risk_plan=risk_plan, risk_mult=round(risk_usdt / base, 3) if base else round(mult, 3),
            mult_plan=round(mult, 3), sig_price=sig.price, ref_price=sig.ref_price, bar_ts=None,
        )

    @staticmethod
    def _state(t):
        return exits.ExitState(t["side"], t["entry"], t["sl"], t["tp"], t.get("tp1"), t.get("atr") or 0,
                               t.get("exit_mode") or "fixed", t["opened_at"], peak=t.get("peak"),
                               peak_ts=t.get("peak_ts"), tp1_done=bool(t.get("tp1_done")),
                               frac=(t["notional"] / t["notional0"]) if t.get("notional0") else 1.0)

    def _announce_open(self, t):
        mode = t.get("exit_mode") or "fixed"
        if mode == "hybrid":
            plan = f"TP1 {t['tp1']:.6g} (chốt {cfg.TP1_FRACTION*100:.0f}%), phần còn lại bám sóng"
        elif mode == "trail":
            plan = "bám sóng toàn bộ (trailing ATR)"
        else:
            plan = f"TP {t['tp']:.6g}"
        ref = f" (nến tín hiệu đóng {t['sig_price']:.6g})" if t.get("sig_price") else ""
        self.notify(
            f"🟢 MỞ LỆNH {t['side'].upper()} {t['symbol']}\n"
            f"Giá: {t['entry']:.6g}{ref} | SL: {t['sl']:.6g} | {plan}\n"
            f"Khối lượng: {t['notional']:.2f} USDT (ký quỹ {t['margin']:.2f}, x{cfg.LEVERAGE}) | "
            f"rủi ro {risk_of(t['notional'], t['entry'], t['sl']):.3f} USDT ({t.get('risk_mult') or 1:.2f}× cơ bản)\n"
            f"Điểm KT: {t['score']} | AI: {t['ai_model']} ({t['ai_conf']}%)\n"
            f"Lý do: {t['reason_open'][:300]}")

    def _announce_partial(self, t, fill, pnl, part_ct):
        """fill: giá KHỚP thật của phần đã chốt (live: giá lệnh market; paper: mức TP1 ± trượt giá) – không phải mức kích
        hoạt; không chốt được phần nào (lot) thì là mức TP1."""
        be = exits.breakeven(t["side"], t["entry"])
        sl = be if (be > t["sl"] if t["side"] == "long" else be < t["sl"]) else t["sl"]
        if not part_ct:
            self.notify(f"💰 TP1 {t['side'].upper()} {t['symbol']} tại {fill:.6g}: {t['contracts']:g} hợp đồng không chia "
                        f"được theo lot → giữ toàn bộ bám sóng, SL dời về hoà vốn {sl:.6g}")
            return
        self.notify(
            f"💰 CHỐT {part_ct:g} hợp đồng {t['side'].upper()} {t['symbol']} – khớp {fill:.6g}: {pnl:+.4f} USDT\n"
            f"Phần còn lại ({t['contracts']:g} hợp đồng) bám sóng, SL dời về hoà vốn {sl:.6g}")

    def _announce_close(self, t, exit_, pnl, pnl_pct, reason):
        ico = "✅" if pnl > 0 else "❌"
        extra = f" (gồm {t['realized']:+.4f} đã chốt trước)" if t.get("realized") else ""
        self.notify(
            f"{ico} ĐÓNG {t['side'].upper()} {t['symbol']} – {reason}\n"
            f"Vào {t['entry']:.6g} → Ra {exit_:.6g}\n"
            f"PnL: {pnl:+.4f} USDT ({pnl_pct:+.1f}% ký quỹ){extra}\n"
            f"Vốn hiện tại: {self.equity():.4f} USDT")
        if self.on_close:
            try:
                self.on_close(t, exit_, pnl, pnl_pct, reason)
            except Exception as e:
                log.warning("on_close callback lỗi: %s", e)

    def _finalize(self, t, exit_, reason):
        """Tính PnL tổng (phần còn lại + phần đã chốt), ghi DB (phí = mọi phần, cả hai chiều), thông báo."""
        pnl_rest, fee = self._pnl(t["side"], t["entry"], exit_, t["notional"])
        pnl = pnl_rest + float(t.get("realized") or 0)
        fee_total = float(t.get("fee") or 0) + fee
        margin0 = margin_for(t.get("notional0") or t["notional"])
        pnl_pct = 100 * pnl / margin0 if margin0 else 0
        self.store.close_trade(t["id"], exit_, pnl, pnl_pct, fee_total, reason)
        t["pnl"], t["fee"] = pnl, fee_total
        self._announce_close(t, exit_, pnl, pnl_pct, reason)
        return pnl

    def _book_part(self, t, part_ct, fill):
        """Ghi sổ việc đóng `part_ct` hợp đồng ở giá khớp `fill`: PnL vào realized, phí hai chiều vào cột fee."""
        frac = part_ct / t["contracts"]
        part = t["notional"] * frac
        pnl, fee = self._pnl(t["side"], t["entry"], fill, part)
        t["contracts"] = round(t["contracts"] - part_ct, 10)
        t["notional"] -= part
        t["margin"] = margin_for(t["notional"])
        t["realized"] = float(t.get("realized") or 0) + pnl
        t["fee"] = float(t.get("fee") or 0) + fee
        return pnl

    def _watch_from(self, now):
        """Mốc "bot đang theo dõi" của lượt kiểm tra này (gọi một lần mỗi lượt, ghi lại giờ lượt).
        Nến 1m đóng TRƯỚC mốc là nến của lúc bot không theo dõi: tiến trình vừa khởi động / vừa tiếp quản vai trò giao dịch,
        hoặc lượt trước cách quá MAX_PASS_GAP (vòng lặp kẹt). Lượt trước cách ≤ MAX_PASS_GAP (vòng lặp bận quét, gọi AI,
        tự học) vẫn là theo dõi liên tục: mốc lùi về lượt trước. Lề MONITOR_INTERVAL + 60 + CLOSED_LAG: nến vừa đóng sát
        lượt trước, hay lượt trước đọc nến lỗi mạng, vẫn được xét đủ."""
        prev, self._last_pass = self._last_pass, now
        anchor = prev if prev is not None and 0 <= now - prev <= MAX_PASS_GAP else now
        return anchor - (cfg.MONITOR_INTERVAL + 60 + CLOSED_LAG)

    @staticmethod
    def _foreign(t):
        """Lệnh mà SL/đỉnh trong DB đã được cập nhật bởi bản KHÁC tới giờ (không đọc lại nến cũ bằng SL đó – sẽ đóng nhầm):
        lệnh mở từ bản cũ (trước 3.3: chưa có bar_ts / risk_usdt), hoặc SL trong DB khác SL lúc xét tới nến bar_ts
        (bar_sl – bản cũ quản lý tiếp lệnh khi quay lui bản, dời SL mà không ghi bar_ts)."""
        if t.get("bar_ts") is None:
            return t.get("risk_usdt") is None
        b = t.get("bar_sl")
        return b is not None and abs(float(b) - float(t["sl"])) > 1e-9 * max(1.0, abs(float(t["sl"])))

    def _track(self, t, now=None, watch_from=None, held_sl=None):
        """Nến 1m giá last ĐÃ ĐÓNG kể từ lần xét trước (t['bar_ts']), theo thứ tự thời gian – paper và live dùng chung.
        Mỗi nến xét đúng một lần bằng exits.ExitState.step (SL trước, rồi TP/TP1, rồi đỉnh & trailing – như backtest
        và mô phỏng bóng). Nến chứa lúc vào lệnh bị bỏ: high/low của nó lẫn giá TRƯỚC lúc vào.
        Nến đóng trước watch_from (bot không theo dõi – _watch_from): chỉ SL/TP đang nằm trên sàn được xét (held_sl: SL
        trên sàn, mặc định SL trong DB) – không TP1, không dời SL, không cập nhật đỉnh, không STALL/TIMEOUT.
        Trả về (events [(kind, price, sl_in_force, fresh)], upd, st); fresh=False: sự kiện của nến lúc không theo dõi."""
        now = time.time() if now is None else now
        st = self._state(t)
        since = max(int(float(t["opened_at"]) // 60) * 60 + 60, int(t.get("bar_ts") or 0) + 60)
        if self._foreign(t):
            # SL/đỉnh đã cập nhật tới giờ bởi bản khác – chỉ xét từ nến 1m vừa đóng
            since = max(since, int(now // 60) * 60 - 60)
        if since + 60 + CLOSED_LAG > now:
            return [], {}, st
        risk0 = abs(t["entry"] - (t.get("sl0") or t["sl"]))
        held = st.sl if held_sl is None else held_sl
        ev, last = [], None
        for c in self.okx.ohlcv_since(t["symbol"], "1m", since):
            ots = c[0] / 1000
            if ots < since or ots + 60 + CLOSED_LAG > now:
                continue
            last = ots
            op, hi, lo, cl = float(c[1]), float(c[2]), float(c[3]), float(c[4])
            if watch_from is not None and ots + 60 < watch_from:
                out = exits.held_hit(st.side, held, st.tp, hi, lo, op)
                ev += [(k, p, held, False) for k, p in out]
            else:
                level = st.sl
                out = st.step(ots + 60, hi, lo, cl, risk0, op=op)
                ev += [(k, p, level, True) for k, p in out]
            if any(k in TERMINAL for k, _ in out):
                break
        upd = {}
        if last is not None:
            upd["bar_ts"], upd["bar_sl"] = last, st.sl
        if st.peak != t.get("peak"):
            upd["peak"], upd["peak_ts"] = st.peak, st.peak_ts
        return ev, upd, st

    REASONS = {"SL": "Chạm SL", "TP": "Chạm TP", "STALL": "Sóng đuối – thoát", "TIMEOUT": "Hết thời gian giữ lệnh"}

    def _reason(self, kind, t):
        if kind == "SL" and t.get("tp1_done"):
            return "Trailing stop – chốt phần còn lại"
        return self.REASONS.get(kind, kind)


class PaperEngine(BaseEngine):
    def __init__(self, okx, store, notify):
        super().__init__(okx, store, notify)
        if store.get("paper_balance") is None:
            store.set("paper_balance", cfg.PAPER_START_BALANCE)

    def balance(self):
        return float(self.store.get("paper_balance"))

    def equity(self, prices=None):
        eq = self.balance()
        for t in self.store.open_trades():
            p = (prices or {}).get(t["symbol"])
            if p:
                pnl, _ = self._pnl(t["side"], t["entry"], p, t["notional"])
                eq += pnl
        return eq

    def open(self, sig, ai):
        equity = self.balance()
        plan = self._prepare(sig)             # giá last mới ± trượt giá, như lệnh market live
        if not plan:
            return None
        _, entry = plan
        notional, mult = self._size(sig, ai, equity, entry)
        if notional < 1:
            return None
        risk_plan = risk_of(notional, entry, sig.sl)
        contracts, notional = self._fit_lot(sig, notional, equity, entry)
        if not contracts:
            return None
        t = self._trade_row(sig, ai, entry, notional, contracts, mult=mult, equity=equity, risk_plan=risk_plan)
        t["id"] = self.store.open_trade(t)
        self._announce_open(t)
        return t

    def close(self, t, price, reason):
        exit_ = costs.exit_fill(t["side"], price)
        pnl_rest, _ = self._pnl(t["side"], t["entry"], exit_, t["notional"])
        self.store.set("paper_balance", self.balance() + pnl_rest)   # phần đã chốt (realized) đã cộng lúc chốt TP1
        self._finalize(t, exit_, reason)
        return True

    def partial(self, t, price):
        """Chốt TP1_FRACTION vị thế ở mức `price` (khớp trượt giá), làm tròn xuống theo lot như live.
        Trả về (pnl, số hợp đồng đã chốt, giá khớp); lot không chia được → (0, 0, price), cả vị thế thành phần bám sóng."""
        part_ct = self._lot_floor(t["symbol"], t["contracts"] * cfg.TP1_FRACTION)
        pnl, fill = 0.0, price
        if 0 < part_ct < t["contracts"]:
            fill = costs.exit_fill(t["side"], price)
            pnl = self._book_part(t, part_ct, fill)
            self.store.set("paper_balance", self.balance() + pnl)
        else:
            part_ct = 0
        t["tp1_done"] = 1
        t["tp1_ts"] = time.time()
        self.store.update_trade(t["id"], notional=t["notional"], contracts=t["contracts"], margin=t["margin"],
                                realized=float(t.get("realized") or 0), fee=float(t.get("fee") or 0), tp1_done=1,
                                tp1_ts=t["tp1_ts"])
        return pnl, part_ct, fill

    def monitor(self):
        """SL/TP/TP1/trailing trên mọi nến 1m giá last đã đóng kể từ lần kiểm tra trước (_track). Nến lúc bot không theo
        dõi: chỉ SL/TP đang đặt được khớp, như lệnh trên sàn của live (xem _watch_from)."""
        now = time.time()
        watch = self._watch_from(now)
        for t in self.store.open_trades():
            try:
                ev, upd, st = self._track(t, now, watch)
            except Exception as e:
                log.warning("monitor %s: %s", t["symbol"], e)
                continue
            closed = False
            for kind, p, _, _ in ev:
                if kind == "TP1":
                    pnl, part, fill = self.partial(t, p)
                    self._announce_partial(t, fill, pnl, part)
                    log.info("%s: TP1 tại %.6g – chốt %g hợp đồng (%+.4f USDT)", t["symbol"], p, part, pnl)
                elif kind == "SL_MOVE":
                    t["sl"] = upd["sl"] = p
                    log.info("%s: dời SL -> %.6g", t["symbol"], p)
                elif kind in TERMINAL:
                    self.close(t, p, self._reason(kind, t))
                    closed = True
                    break
            if not closed and upd:
                self.store.update_trade(t["id"], **upd)


class LiveEngine(BaseEngine):
    def balance(self):
        total, _ = self.okx.balance_usdt()
        return total

    def equity(self, prices=None):
        try:
            return self.balance()
        except Exception:
            return float(self.store.get("last_equity", 0))

    def open(self, sig, ai):
        total, free = self.okx.balance_usdt()
        plan = self._prepare(sig)             # cỡ lệnh theo giá last mới + trượt giá dự tính, không theo giá đóng nến cũ
        if not plan:
            return None
        _, entry_est = plan
        notional, mult = self._size(sig, ai, total, entry_est)
        if notional < 1:
            return None
        risk_plan = risk_of(notional, entry_est, sig.sl)
        contracts, notional = self._fit_lot(sig, notional, free, entry_est)
        if not contracts:
            return None
        if margin_for(notional) > free * 0.95:
            sig.skip = "ký quỹ không đủ"
            log.info("%s: ký quỹ không đủ", sig.symbol)
            return None
        _, tp, _ = plan_exit(sig, entry_est)
        planned = risk_of(notional, entry_est, sig.sl)      # rủi ro của đúng số hợp đồng này nếu khớp như dự tính
        with critical():        # lệnh đã khớp trên sàn thì DB phải có nó
            oid, fill = self.okx.open_position(sig.symbol, sig.side, contracts, sig.sl, tp)
            entry = fill or entry_est
            notional = contracts * self._lot(sig.symbol)[2] * entry
            t = self._trade_row(sig, ai, entry, notional, contracts, order_id=oid, mult=mult, equity=total, tp=tp,
                                risk_plan=risk_plan)
            t["sl_exch"] = sig.sl           # SL gắn kèm lệnh mở
            t["id"] = self.store.open_trade(t)
            self._announce_open(t)      # trong vùng: SIGTERM không làm mất tin MỞ LỆNH
            self._check_fill(t, planned, risk_plan)
        if t.get("status") != "open":
            sig.skip = "khớp xong rủi ro thật vượt kế hoạch – đã đóng ngay"
            return None
        return t

    def _order_fill(self, order, symbol, default):
        """Giá khớp trung bình của lệnh market vừa gửi; không đọc được thì giá last hiện tại, cuối cùng là `default`."""
        try:
            o = self.okx.ex.fetch_order(order["id"], symbol)
            p = float(o.get("average") or o.get("price") or 0)
            if p > 0:
                return p
        except Exception:
            pass
        try:
            p = float(self.okx.price(symbol))
            if p > 0:
                return p
        except Exception:
            pass
        return default

    @staticmethod
    def _own(t):
        """Mã các lệnh bot tự đóng một phần vị thế (cắt bớt sau khi khớp, chốt TP1)."""
        try:
            return [str(x) for x in json.loads(t.get("own_orders") or "[]")]
        except Exception:
            return []

    def _add_own(self, t, order):
        oid = (order or {}).get("id")
        if oid:
            t["own_orders"] = json.dumps(self._own(t) + [str(oid)])

    @staticmethod
    def _beyond(t, a, b):
        """a xa hơn b về phía có lời (long: cao hơn)."""
        return a > b if t["side"] == "long" else a < b

    def _check_fill(self, t, planned, risk_plan):
        """Sau khi khớp: rủi ro thật (giá khớp thật → SL) so với kế hoạch. Trần = min(kế hoạch × (1 + LIVE_RISK_TOL),
        MIN_LOT_RISK_MULT × rủi ro dự tính) – lệnh lot tối thiểu (kế hoạch đã gần 2×) không được vượt luật lot tối thiểu.
        Vượt trần → cắt bớt hợp đồng về đúng kế hoạch; làm tròn ra dưới 1 lot thì giữ 1 lot nếu 1 lot còn trong luật lot tối
        thiểu; không cắt được (hoặc lệnh cắt lỗi) thì đóng hẳn nếu vượt MIN_LOT_RISK_MULT × dự tính, không thì giữ và báo.
        SL đã nằm sai phía giá khớp → đóng ngay."""
        sym, long = t["symbol"], t["side"] == "long"
        if (t["entry"] - t["sl"]) * (1 if long else -1) <= 0:
            self.notify(f"⚠️ {sym}: giá khớp {t['entry']:.6g} đã vượt SL {t['sl']:.6g} – đóng ngay")
            if self.close(t, t["entry"], "Giá khớp vượt SL – đóng ngay"):
                t["status"] = "closed"
            return
        base = risk_plan or planned
        hard = cfg.MIN_LOT_RISK_MULT * base
        real = risk_of(t["notional"], t["entry"], t["sl"])
        if real <= min(planned * (1 + cfg.LIVE_RISK_TOL), hard) + 1e-12:
            return
        n0, lot = t["contracts"], self._lot(sym)[0]
        keep = self._lot_floor(sym, n0 * planned / real)
        if keep <= 0 and lot < n0 and real * lot / n0 <= hard + 1e-12:
            keep = lot              # 1 lot còn trong luật lot tối thiểu – giữ 1 lot thay vì đóng cả
        if 0 < keep < n0:
            cut = round(n0 - keep, 10)
            try:
                o = self.okx.ex.create_order(sym, "market", "sell" if long else "buy", cut,
                                             params={"tdMode": "isolated", "reduceOnly": True})
            except Exception as e:
                log.error("cắt bớt %s: %s", sym, e)
                self.notify(f"⚠️ {sym}: khớp {t['entry']:.6g} làm rủi ro thật {real:.3f} USDT > kế hoạch {planned:.3f}, "
                            f"lệnh cắt bớt {cut:g} hợp đồng lỗi: {str(e)[:120]}")
                o = None
            if o is not None:
                pnl = self._book_part(t, cut, self._order_fill(o, sym, t["entry"]))
                self._add_own(t, o)
                t["notional0"] = t["notional"]
                t["risk_usdt"] = risk_of(t["notional"], t["entry"], t["sl"])
                t["risk_mult"] = round(t["risk_usdt"] * (t["risk_mult"] or 0) / real, 3) if real else t["risk_mult"]
                self.store.update_trade(t["id"], contracts=t["contracts"], notional=t["notional"], notional0=t["notional0"],
                                        margin=t["margin"], realized=t["realized"], fee=t["fee"],
                                        risk_usdt=t["risk_usdt"], risk_mult=t["risk_mult"], own_orders=t.get("own_orders"))
                try:
                    self._replace_algo(t, t["sl"], t["tp"])
                except Exception as e:
                    log.warning("đặt lại SL/TP %s sau khi cắt: %s", sym, e)
                self.notify(f"⚠️ {sym}: khớp {t['entry']:.6g} làm rủi ro thật {real:.3f} USDT > kế hoạch {planned:.3f} – "
                            f"đã cắt {cut:g} hợp đồng, còn {t['contracts']:g} (rủi ro {t['risk_usdt']:.3f}, {pnl:+.4f} USDT)")
                return
        if real > hard + 1e-12:
            self.notify(f"⚠️ {sym}: khớp {t['entry']:.6g} làm rủi ro thật {real:.3f} USDT > {cfg.MIN_LOT_RISK_MULT:g}× "
                        f"dự tính {base:.3f}, không cắt được theo lot – đóng ngay")
            if self.close(t, t["entry"], "Rủi ro thật vượt kế hoạch – đóng ngay"):
                t["status"] = "closed"
            return
        self.notify(f"⚠️ {sym}: khớp {t['entry']:.6g} làm rủi ro thật {real:.3f} USDT > kế hoạch {planned:.3f} "
                    f"(không cắt được theo lot, vẫn trong {cfg.MIN_LOT_RISK_MULT:g}× dự tính – giữ)")

    def _replace_algo(self, t, sl, tp):
        """Đặt SL/TP MỚI trước, rồi mới huỷ lệnh cũ → vị thế không bao giờ trống SL trên sàn dù bước nào lỗi
        (bản cũ huỷ trước – đặt mới lỗi là mất SL). v3.3: huỷ cả SL/TP loại oco (bản cũ chỉ tìm conditional nên
        SL/TP gắn kèm lệnh mở không bị huỷ), kích hoạt theo giá last."""
        ex = self.okx.ex
        old = algo_orders(ex, t["symbol"])
        side = "sell" if t["side"] == "long" else "buy"
        new = ex.create_order(t["symbol"], "market", side, t["contracts"], params={
            "tdMode": "isolated", "reduceOnly": True,
            "stopLossPrice": ex.price_to_precision(t["symbol"], sl),
            "takeProfitPrice": ex.price_to_precision(t["symbol"], tp),
            "slTriggerPxType": "last", "tpTriggerPxType": "last",
        })
        new_id = (new or {}).get("id")
        for a in old:
            if a["id"] == new_id:
                continue
            try:
                ex.cancel_order(a["id"], t["symbol"], params={"stop": True})
            except Exception as e:
                log.warning("huỷ SL/TP cũ %s %s: %s (lệnh mới đã đặt, vị thế vẫn có SL)", t["symbol"], a["id"], e)

    def partial(self, t, price):
        """Đóng TP1_FRACTION vị thế bằng lệnh market reduceOnly (làm tròn xuống theo lot của sàn).
        Trả về (pnl, số hợp đồng đã chốt, giá khớp thật); lot không chia được → (0, 0, price): đánh dấu TP1, cả vị thế
        bám sóng (bản cũ: amount_to_precision báo lỗi hoặc trả 0 → TP1 bị thử lại và báo CHỐT ở mọi lần kiểm tra)."""
        ex = self.okx.ex
        part_ct = self._lot_floor(t["symbol"], t["contracts"] * cfg.TP1_FRACTION)
        side = "sell" if t["side"] == "long" else "buy"
        pnl, fill = 0.0, price
        with critical():        # chốt một phần trên sàn → DB ghi tp1_done, nếu không lần sau chốt lại lần nữa
            if 0 < part_ct < t["contracts"]:
                o = ex.create_order(t["symbol"], "market", side, part_ct, params={"tdMode": "isolated", "reduceOnly": True})
                fill = self._order_fill(o, t["symbol"], price)
                pnl = self._book_part(t, part_ct, fill)
                self._add_own(t, o)
            else:
                part_ct = 0
            t["tp1_done"] = 1
            t["tp1_ts"] = time.time()
            self.store.update_trade(t["id"], notional=t["notional"], contracts=t["contracts"], margin=t["margin"],
                                    realized=float(t.get("realized") or 0), fee=float(t.get("fee") or 0), tp1_done=1,
                                    tp1_ts=t["tp1_ts"], own_orders=t.get("own_orders"))
        return pnl, part_ct, fill

    def close(self, t, price, reason):
        """Đóng vị thế trên sàn rồi ghi sổ. Trả True khi đã đóng. Lệnh đóng lỗi → False: lệnh vẫn mở trong DB với
        close_req = lý do (vẫn tính vào số lệnh mở và trần rủi ro), SL trên sàn giữ nguyên, báo một lần; lượt kiểm tra
        sau thử đóng lại, hoặc ghi sổ nếu sàn báo vị thế đã hết (bản cũ ghi đóng dù lệnh đóng lỗi → bot bỏ mặc vị thế thật)."""
        with critical():
            try:
                o = self.okx.close_position(t["symbol"], t["side"], t["contracts"])
            except Exception as e:
                log.error("close_position %s: %s", t["symbol"], e)
                first = not t.get("close_req")
                t["close_req"] = reason
                self.store.update_trade(t["id"], close_req=reason)
                if first:
                    self.notify(f"⚠️ Đóng {t['side'].upper()} {t['symbol']} thất bại ({reason}): {str(e)[:120]}\n"
                                f"Vị thế vẫn mở trên sàn, SL trên sàn giữ nguyên – bot thử đóng lại ở lần kiểm tra sau")
                return False
            exit_ = self._order_fill(o, t["symbol"], price) if o and o.get("id") else price
            self._finalize(t, exit_, reason)
            return True

    def monitor(self):
        """Đồng bộ với vị thế thật trên OKX; quản lý TP1/trailing bằng nến 1m giá last đã đóng (_track, như paper).
        SL/TP đang nằm trên sàn do sàn tự khớp; SL bot muốn mà sàn chưa có (sl ≠ sl_exch) do bot tự giữ (_sync_sl).
        Không đọc được vị thế → bỏ cả lượt (bản cũ coi như sàn đã đóng mọi lệnh)."""
        now = time.time()
        watch = self._watch_from(now)
        opens = self.store.open_trades()
        if not opens:
            return
        try:
            live = {p["symbol"]: p for p in self.okx.fetch_positions()}
        except Exception as e:
            log.warning("không đọc được vị thế trên sàn – lượt này không đối chiếu, không quản lý lệnh: %s", str(e)[:160])
            return
        for t in opens:
            if t["symbol"] not in live:
                self._gone(t)
                continue
            self._missing.pop(t["id"], None)
            if t.get("close_req"):          # lần đóng trước lỗi – thử lại
                try:
                    p = float(self.okx.price(t["symbol"]))
                except Exception:
                    p = t["entry"]
                self.close(t, p, t["close_req"])
                continue
            try:
                self._manage(t, now, watch)
            except Exception as e:
                log.warning("monitor %s: %s", t["symbol"], e)

    def _gone(self, t):
        """Sàn không còn vị thế (lần đọc vị thế thành công): sàn đã khớp SL/TP (hoặc lệnh đóng của bot đã khớp dù báo lỗi,
        hoặc người dùng tự đóng). Giá khớp thật từ lịch sử giao dịch, trừ các lệnh bot tự đóng một phần; chưa thấy lệnh
        khớp đóng thì chờ thêm một lượt xác nhận rồi mới ước lượng bằng mức SL/TP gần giá hiện tại hơn."""
        since = (t.get("tp1_ts") if t.get("tp1_done") else None) or t["opened_at"]
        fill = self.okx.close_fill_price(t["symbol"], since, t["side"], exclude=self._own(t))
        if fill is None:
            n = self._missing[t["id"]] = self._missing.get(t["id"], 0) + 1
            if n < 2:
                log.warning("%s: sàn không còn vị thế nhưng chưa thấy lệnh khớp đóng – chờ xác nhận ở lượt sau", t["symbol"])
                return
        self._missing.pop(t["id"], None)
        ref = fill
        if ref is None:
            try:
                ref = self.okx.price(t["symbol"])
            except Exception:
                ref = t["entry"]
        near_tp = abs(ref - t["tp"]) < abs(ref - t["sl"])
        exch_sl = t["sl_exch"] if t.get("sl_exch") is not None else t["sl"]
        exit_ = fill if fill else (t["tp"] if near_tp else exch_sl)
        if t.get("close_req"):
            reason = t["close_req"]
        else:
            reason = "Chạm TP (sàn)" if near_tp else ("Trailing stop (sàn)" if t.get("tp1_done") else "Chạm SL (sàn)")
        if not fill:
            reason += " – giá ước lượng"
        self._finalize(t, exit_, reason)

    def _manage(self, t, now, watch):
        """TP1 / dời SL / thoát do bot quyết định trên các nến 1m mới (_track). Nến lúc bot không theo dõi chỉ để biết SL/TP
        trên sàn bị chạm (sàn tự khớp) – không gửi lệnh market theo giá cũ."""
        exch_sl = t["sl_exch"] if t.get("sl_exch") is not None else t["sl"]     # SL đang nằm trên sàn
        ev, upd, st = self._track(t, now, watch, held_sl=exch_sl)
        with critical():    # TP1: chốt trên sàn → dời SL trên sàn → ghi DB → báo tin, SIGTERM chờ hết
            resize = False
            for kind, p, level, fresh in ev:
                if not fresh:
                    if kind in TERMINAL:
                        break       # SL/TP trên sàn bị chạm lúc bot không theo dõi – sàn tự khớp, lượt sau ghi giá khớp
                    continue
                if kind == "TP1":
                    try:
                        pnl, part, fill = self.partial(t, p)
                    except Exception as e:
                        log.error("chốt phần đầu %s thất bại: %s", t["symbol"], e)
                        return      # không ghi bar_ts: lần sau xét lại đúng các nến này
                    resize = resize or bool(part)
                    self._announce_partial(t, fill, pnl, part)
                elif kind == "SL_MOVE":
                    t["sl"] = upd["sl"] = p
                elif kind == "SL" and self._beyond(t, level, exch_sl):
                    self.close(t, p, self._reason(kind, t) + " (bot)")     # SL sàn chưa có bị chạm → bot đóng
                    return
                elif kind in ("STALL", "TIMEOUT"):
                    self.close(t, p, self._reason(kind, t))
                    return
                elif kind in TERMINAL:
                    break       # SL/TP đang nằm trên sàn – sàn tự khớp, lần sau ghi giá khớp thật
            if (t["sl"] != exch_sl or resize) and self._sync_sl(t, exch_sl, upd):
                return
            if upd:
                self.store.update_trade(t["id"], **upd)

    def _sync_sl(self, t, exch_sl, upd):
        """Đưa SL bot muốn (t['sl']) lên sàn. Giá last đã vượt SL đó → đóng bằng lệnh market (paper khớp ở giá mở nến
        sau) và trả True. Sàn từ chối / lỗi mạng → giữ SL muốn trong DB (sl), sl_exch giữ SL cũ; bot tự giữ SL đó và
        thử đặt lại mỗi lượt (bản cũ bỏ luôn việc dời SL)."""
        sym = t["symbol"]
        if t["sl"] != exch_sl:
            try:
                last = float(self.okx.price(sym))
            except Exception:
                last = None
            if last is not None and not self._beyond(t, last, t["sl"]):
                log.info("%s: giá last %.6g đã vượt SL %.6g (sàn chưa có) – bot đóng", sym, last, t["sl"])
                self.close(t, last, self._reason("SL", t) + " (bot)")
                return True
        try:
            self._replace_algo(t, t["sl"], t["tp"])
            upd["sl_exch"] = t["sl"]
            self._sl_warned.discard(t["id"])
            log.info("%s: cập nhật SL trên sàn -> %.6g", sym, t["sl"])
        except Exception as e:
            log.warning("đặt SL %s lên sàn thất bại: %s – bot tự giữ SL %.6g, thử lại lượt sau", sym, e, t["sl"])
            if t["sl"] != exch_sl and t["id"] not in self._sl_warned:
                self._sl_warned.add(t["id"])
                self.notify(f"⚠️ {sym}: sàn chưa nhận SL mới {t['sl']:.6g} (SL trên sàn vẫn {exch_sl:.6g}): {str(e)[:120]}\n"
                            f"Bot tự giữ SL {t['sl']:.6g} (giá chạm thì đóng bằng lệnh market) và thử đặt lại mỗi lượt")
        return False
