"""Engine giao dịch: PaperEngine (mô phỏng) và LiveEngine (OKX thật) – cùng giao diện.

Cách thoát lệnh (EXIT_MODE): fixed | hybrid ("ăn sóng": chốt 1 phần rồi bám sóng) | trail | auto.
Logic thoát nằm ở exits.py và được dùng y hệt trong mô phỏng bóng của bộ tự học.

v3.3 – paper phản chiếu live:
  * Vào lệnh ở giá last MỚI lấy lúc quyết định (stamp), không phải giá đóng nến 15m đã cũ vài phút. SL giữ nguyên mức
    giá theo cấu trúc; 1R, TP1, cỡ lệnh tính lại từ giá khớp. Giá đã chạy quá xa (SL sai phía, ngược > MAX_FILL_DRIFT_R,
    SL > MAX_SL_ATR, R:R) → bỏ, ghi lý do (fill_check) vào tín hiệu bóng.
  * Paper: giá khớp = giá last ± SLIPPAGE, phí taker hai chiều (costs.py – cùng mô hình với mô phỏng bóng & tự học).
    Live: cỡ lệnh theo giá last mới + trượt giá dự tính; khớp xong đo rủi ro thật từ giá khớp thật, vượt kế hoạch quá
    LIVE_RISK_TOL thì cắt bớt hợp đồng (không cắt được thì đóng nếu vượt MIN_LOT_RISK_MULT) và báo.
  * Theo dõi lệnh (_track, paper và live dùng chung): đọc MỌI nến 1m giá last ĐÃ ĐÓNG kể từ lần xét trước (bar_ts),
    mỗi nến đúng một lần: SL trước, rồi TP/TP1, rồi trailing – không bỏ sót râu nến giữa hai lần kiểm tra (bản cũ chỉ
    đọc nến 1m đang chạy). Giá last là loại giá SL/TP trên sàn kích hoạt (exchange.open_position) và là giá của mô
    phỏng bóng / backtest; bản cũ live dùng giá mark cho TP1/trailing. Một lần chạm được xử lý chậm nhất ~1 phút +
    MONITOR_INTERVAL. Khác biệt còn lại: live chốt TP1 bằng lệnh market lúc phát hiện, paper khớp đúng mức TP1.
  * Chốt TP1 làm tròn XUỐNG theo bước lot của sàn (cả paper lẫn live); lot không chia được → giữ toàn bộ làm phần bám sóng.
  * Cột fee = phí hai chiều của mọi phần đã đóng; risk_usdt / risk_mult = rủi ro THẬT (sau làm tròn lot, theo giá khớp).
"""
import atexit
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
    """Kế hoạch còn hợp lệ không khi vào ở giá khớp `entry` (= ref ± trượt giá; ref: giá last lúc quyết định).
    '' = vào được, ngược lại là lý do bỏ. SL giữ nguyên mức giá theo cấu trúc, R tính lại từ giá khớp:
      * SL phải nằm đúng phía giá khớp;
      * giá không được chạy ngược về phía SL quá MAX_FILL_DRIFT_R (theo R kế hoạch, từ giá đóng nến tín hiệu);
      * SL cách giá ≤ MAX_SL_ATR ATR (range: RANGE_MAX_SL_ATR) và R:R tới TP thật sẽ dùng ≥ MIN_RR (range: RANGE_MIN_RR,
        TP = dải giữa cố định; xu hướng tính TP/TP1 lại từ giá vào nên luôn đạt) – đúng các luật diagnose xét ở giá đóng
        nến, nay xét ở giá last (trượt giá dự tính không tính vào, để chỉ việc giá đã chạy mới làm bỏ tín hiệu)."""
    ref = entry if ref is None else ref
    sign = 1 if sig.side == "long" else -1
    if (entry - sig.sl) * sign <= 0 or (ref - sig.sl) * sign <= 0:
        return f"giá đã vượt SL (vào {entry:.6g}, SL {sig.sl:.6g})"
    plan = abs(sig.price - sig.sl)
    drift = sign * (ref - sig.price) / plan if plan else 0.0
    if drift < -cfg.MAX_FILL_DRIFT_R:
        return f"giá đã chạy ngược {-drift:.2f}R về phía SL từ lúc đóng nến tín hiệu (tối đa {cfg.MAX_FILL_DRIFT_R:g}R)"
    risk = abs(ref - sig.sl)
    is_range = (sig.context or {}).get("setup") == "range"
    max_atr = cfg.RANGE_MAX_SL_ATR if is_range else cfg.MAX_SL_ATR
    if sig.atr and risk > max_atr * sig.atr + 1e-12:
        return f"giá đã chạy xa: SL cách giá {risk / sig.atr:.1f} ATR > {max_atr:g}"
    _, tp, _ = plan_exit(sig, ref)
    rr = sign * (tp - ref) / risk
    need = strategy.min_rr_for(sig)
    if rr < need - 1e-9:
        return f"R:R từ giá hiện tại còn {rr:.2f} < {need:g}"
    return ""


class BaseEngine:
    def __init__(self, okx, store, notify):
        self.okx, self.store, self.notify = okx, store, notify
        self.on_close = None   # callback(trade, exit, pnl, pnl_pct, reason) – Bot gắn vào để AI tổng kết

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

    def _announce_partial(self, t, price, pnl, part_ct):
        be = exits.breakeven(t["side"], t["entry"])
        sl = be if (be > t["sl"] if t["side"] == "long" else be < t["sl"]) else t["sl"]
        if not part_ct:
            self.notify(f"💰 TP1 {t['side'].upper()} {t['symbol']} tại {price:.6g}: {t['contracts']:g} hợp đồng không chia "
                        f"được theo lot → giữ toàn bộ bám sóng, SL dời về hoà vốn {sl:.6g}")
            return
        self.notify(
            f"💰 CHỐT {part_ct:g} hợp đồng {t['side'].upper()} {t['symbol']} tại {price:.6g}: {pnl:+.4f} USDT\n"
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

    def _track(self, t, now=None):
        """Nến 1m giá last ĐÃ ĐÓNG kể từ lần xét trước (t['bar_ts']), theo thứ tự thời gian – paper và live dùng chung.
        Mỗi nến xét đúng một lần bằng exits.ExitState.step (SL trước, rồi TP/TP1, rồi đỉnh & trailing – như backtest
        và mô phỏng bóng). Nến chứa lúc vào lệnh bị bỏ: high/low của nó lẫn giá TRƯỚC lúc vào.
        Trả về (events [(kind, price, sl_in_force)], upd, st)."""
        now = time.time() if now is None else now
        st = self._state(t)
        since = max(int(float(t["opened_at"]) // 60) * 60 + 60, int(t.get("bar_ts") or 0) + 60)
        if t.get("bar_ts") is None and t.get("risk_usdt") is None:
            # lệnh mở từ bản cũ (trước 3.3): SL/đỉnh đã cập nhật tới giờ – chỉ xét từ nến 1m vừa đóng, không đọc lại
            # nến cũ bằng SL đã dời (sẽ đóng nhầm)
            since = max(since, int(now // 60) * 60 - 60)
        if since + 60 + CLOSED_LAG > now:
            return [], {}, st
        risk0 = abs(t["entry"] - (t.get("sl0") or t["sl"]))
        ev, last = [], None
        for c in self.okx.ohlcv_since(t["symbol"], "1m", since):
            ots = c[0] / 1000
            if ots < since or ots + 60 + CLOSED_LAG > now:
                continue
            last, level = ots, st.sl
            out = st.step(ots + 60, float(c[2]), float(c[3]), float(c[4]), risk0, op=float(c[1]))
            ev += [(k, p, level) for k, p in out]
            if any(k in TERMINAL for k, _ in out):
                break
        upd = {}
        if last is not None:
            upd["bar_ts"] = last
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

    def partial(self, t, price):
        """Chốt TP1_FRACTION vị thế ở mức `price` (khớp trượt giá), làm tròn xuống theo lot như live.
        Trả về (pnl, số hợp đồng đã chốt); lot không chia được → (0, 0), cả vị thế thành phần bám sóng."""
        part_ct = self._lot_floor(t["symbol"], t["contracts"] * cfg.TP1_FRACTION)
        pnl = 0.0
        if 0 < part_ct < t["contracts"]:
            pnl = self._book_part(t, part_ct, costs.exit_fill(t["side"], price))
            self.store.set("paper_balance", self.balance() + pnl)
        else:
            part_ct = 0
        t["tp1_done"] = 1
        t["tp1_ts"] = time.time()
        self.store.update_trade(t["id"], notional=t["notional"], contracts=t["contracts"], margin=t["margin"],
                                realized=float(t.get("realized") or 0), fee=float(t.get("fee") or 0), tp1_done=1,
                                tp1_ts=t["tp1_ts"])
        return pnl, part_ct

    def monitor(self):
        """SL/TP/TP1/trailing trên mọi nến 1m giá last đã đóng kể từ lần kiểm tra trước (_track)."""
        for t in self.store.open_trades():
            try:
                ev, upd, st = self._track(t)
            except Exception as e:
                log.warning("monitor %s: %s", t["symbol"], e)
                continue
            closed = False
            for kind, p, _ in ev:
                if kind == "TP1":
                    pnl, part = self.partial(t, p)
                    self._announce_partial(t, p, pnl, part)
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
            t["id"] = self.store.open_trade(t)
            self._announce_open(t)      # trong vùng: SIGTERM không làm mất tin MỞ LỆNH
            self._check_fill(t, planned, risk_plan)
        if t.get("status") != "open":
            sig.skip = "khớp xong rủi ro thật vượt kế hoạch – đã đóng ngay"
            return None
        return t

    def _order_fill(self, order, symbol, default):
        try:
            o = self.okx.ex.fetch_order(order["id"], symbol)
            return float(o.get("average") or o.get("price") or default)
        except Exception:
            return default

    def _check_fill(self, t, planned, risk_plan):
        """Sau khi khớp: rủi ro thật (giá khớp thật → SL) so với kế hoạch. Vượt quá LIVE_RISK_TOL → cắt bớt hợp đồng về
        đúng kế hoạch; lot không cắt được thì đóng hẳn nếu vượt MIN_LOT_RISK_MULT × kế hoạch (luật lot tối thiểu), không
        thì chỉ báo. SL đã nằm sai phía giá khớp → đóng ngay."""
        sym, long = t["symbol"], t["side"] == "long"
        if (t["entry"] - t["sl"]) * (1 if long else -1) <= 0:
            self.notify(f"⚠️ {sym}: giá khớp {t['entry']:.6g} đã vượt SL {t['sl']:.6g} – đóng ngay")
            self.close(t, t["entry"], "Giá khớp vượt SL – đóng ngay")
            t["status"] = "closed"
            return
        real = risk_of(t["notional"], t["entry"], t["sl"])
        if real <= planned * (1 + cfg.LIVE_RISK_TOL):
            return
        keep = self._lot_floor(sym, t["contracts"] * planned / real)
        if 0 < keep < t["contracts"]:
            cut = round(t["contracts"] - keep, 10)
            o = self.okx.ex.create_order(sym, "market", "sell" if long else "buy", cut,
                                         params={"tdMode": "isolated", "reduceOnly": True})
            pnl = self._book_part(t, cut, self._order_fill(o, sym, t["entry"]))
            t["notional0"] = t["notional"]
            t["risk_usdt"] = risk_of(t["notional"], t["entry"], t["sl"])
            t["risk_mult"] = round(t["risk_usdt"] * (t["risk_mult"] or 0) / real, 3) if real else t["risk_mult"]
            self.store.update_trade(t["id"], contracts=t["contracts"], notional=t["notional"], notional0=t["notional0"],
                                    margin=t["margin"], realized=t["realized"], fee=t["fee"],
                                    risk_usdt=t["risk_usdt"], risk_mult=t["risk_mult"])
            try:
                self._replace_algo(t, t["sl"], t["tp"])
            except Exception as e:
                log.warning("đặt lại SL/TP %s sau khi cắt: %s", sym, e)
            self.notify(f"⚠️ {sym}: khớp {t['entry']:.6g} làm rủi ro thật {real:.3f} USDT > kế hoạch {planned:.3f} – "
                        f"đã cắt {cut:g} hợp đồng, còn {t['contracts']:g} (rủi ro {t['risk_usdt']:.3f}, {pnl:+.4f} USDT)")
            return
        if real > cfg.MIN_LOT_RISK_MULT * (risk_plan or planned):
            self.notify(f"⚠️ {sym}: khớp {t['entry']:.6g} làm rủi ro thật {real:.3f} USDT > {cfg.MIN_LOT_RISK_MULT:g}× "
                        f"kế hoạch {risk_plan or planned:.3f}, không cắt được theo lot – đóng ngay")
            self.close(t, t["entry"], "Rủi ro thật vượt kế hoạch – đóng ngay")
            t["status"] = "closed"
            return
        self.notify(f"⚠️ {sym}: khớp {t['entry']:.6g} làm rủi ro thật {real:.3f} USDT > kế hoạch {planned:.3f} "
                    f"(không cắt được theo lot, vẫn trong {cfg.MIN_LOT_RISK_MULT:g}× – giữ)")

    def _algo_orders(self, symbol):
        """Lệnh SL/TP đang treo: cả loại conditional (một mức) lẫn oco (SL + TP, gồm SL/TP gắn kèm lệnh mở)."""
        seen, out = set(), []
        for kind in ("conditional", "oco"):
            for a in self.okx.ex.fetch_open_orders(symbol, params={"ordType": kind}) or []:
                if a.get("id") not in seen:
                    seen.add(a.get("id"))
                    out.append(a)
        return out

    def _replace_algo(self, t, sl, tp):
        """Đặt SL/TP MỚI trước, rồi mới huỷ lệnh cũ → vị thế không bao giờ trống SL trên sàn dù bước nào lỗi
        (bản cũ huỷ trước – đặt mới lỗi là mất SL). v3.3: huỷ cả SL/TP loại oco (bản cũ chỉ tìm conditional nên
        SL/TP gắn kèm lệnh mở không bị huỷ), kích hoạt theo giá last."""
        ex = self.okx.ex
        old = self._algo_orders(t["symbol"])
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
        Trả về (pnl, số hợp đồng đã chốt); lot không chia được → (0, 0): đánh dấu TP1, cả vị thế bám sóng
        (bản cũ: amount_to_precision báo lỗi hoặc trả 0 → TP1 bị thử lại và báo CHỐT ở mọi lần kiểm tra)."""
        ex = self.okx.ex
        part_ct = self._lot_floor(t["symbol"], t["contracts"] * cfg.TP1_FRACTION)
        side = "sell" if t["side"] == "long" else "buy"
        pnl = 0.0
        with critical():        # chốt một phần trên sàn → DB ghi tp1_done, nếu không lần sau chốt lại lần nữa
            if 0 < part_ct < t["contracts"]:
                o = ex.create_order(t["symbol"], "market", side, part_ct, params={"tdMode": "isolated", "reduceOnly": True})
                pnl = self._book_part(t, part_ct, self._order_fill(o, t["symbol"], price))
            else:
                part_ct = 0
            t["tp1_done"] = 1
            t["tp1_ts"] = time.time()
            self.store.update_trade(t["id"], notional=t["notional"], contracts=t["contracts"], margin=t["margin"],
                                    realized=float(t.get("realized") or 0), fee=float(t.get("fee") or 0), tp1_done=1,
                                    tp1_ts=t["tp1_ts"])
        return pnl, part_ct

    def close(self, t, price, reason):
        with critical():
            exit_ = price
            try:
                o = self.okx.close_position(t["symbol"], t["side"], t["contracts"])
                if o and o.get("id"):
                    exit_ = self._order_fill(o, t["symbol"], price)
            except Exception as e:
                log.error("close_position %s: %s", t["symbol"], e)
            self._finalize(t, exit_, reason)

    def monitor(self):
        """Đồng bộ với vị thế thật trên OKX; quản lý TP1/trailing bằng nến 1m giá last đã đóng (_track, như paper).
        SL/TP đang có trên sàn do sàn tự khớp; SL vừa dời trong lượt này (sàn chưa có) mà đã bị chạm → bot đóng."""
        opens = self.store.open_trades()
        if not opens:
            return
        live = {p["symbol"]: p for p in self.okx.fetch_positions()}
        for t in opens:
            if t["symbol"] not in live:
                # sàn đã khớp SL/TP: lấy giá khớp thật từ lịch sử giao dịch (sau lần chốt TP1 nếu có);
                # không lấy được mới ước lượng bằng mức SL/TP gần giá hiện tại hơn
                since = (t.get("tp1_ts") if t.get("tp1_done") else None) or t["opened_at"]
                fill = self.okx.close_fill_price(t["symbol"], since, t["side"])
                ref = fill
                if ref is None:
                    try:
                        ref = self.okx.price(t["symbol"])
                    except Exception:
                        ref = t["entry"]
                near_tp = abs(ref - t["tp"]) < abs(ref - t["sl"])
                exit_ = fill if fill else (t["tp"] if near_tp else t["sl"])
                reason = "Chạm TP (sàn)" if near_tp else ("Trailing stop (sàn)" if t.get("tp1_done") else "Chạm SL (sàn)")
                if not fill:
                    reason += " – giá ước lượng"
                self._finalize(t, exit_, reason)
                continue
            try:
                ev, upd, st = self._track(t)
            except Exception as e:
                log.warning("monitor %s: %s", t["symbol"], e)
                continue
            exch_sl = t["sl"]                 # SL đang nằm trên sàn
            with critical():    # TP1: chốt trên sàn → dời SL trên sàn → ghi DB → báo tin, SIGTERM chờ hết
                closed, need_algo, failed = False, False, False
                for kind, p, level in ev:
                    if kind == "TP1":
                        try:
                            pnl, part = self.partial(t, p)
                            need_algo = True
                            self._announce_partial(t, p, pnl, part)
                        except Exception as e:
                            log.error("chốt phần đầu %s thất bại: %s", t["symbol"], e)
                            failed = True       # không ghi bar_ts: lần sau xét lại đúng các nến này
                            break
                    elif kind == "SL_MOVE":
                        t["sl"] = upd["sl"] = p
                        need_algo = True
                    elif kind == "SL" and (level > exch_sl if t["side"] == "long" else level < exch_sl):
                        self.close(t, p, self._reason(kind, t) + " (bot)")
                        closed = True
                        break
                    elif kind in ("STALL", "TIMEOUT"):
                        self.close(t, p, self._reason(kind, t))
                        closed = True
                        break
                    elif kind in TERMINAL:
                        break       # SL/TP đang nằm trên sàn – sàn tự khớp, lần sau ghi giá khớp thật
                if closed or failed:
                    continue
                if need_algo:
                    try:
                        self._replace_algo(t, t["sl"], t["tp"])
                        log.info("%s: cập nhật SL trên sàn -> %.6g", t["symbol"], t["sl"])
                    except Exception as e:
                        log.warning("đặt lại SL %s thất bại: %s", t["symbol"], e)
                        upd.pop("sl", None)
                if upd:
                    self.store.update_trade(t["id"], **upd)
