"""Engine giao dịch: PaperEngine (mô phỏng) và LiveEngine (OKX thật) – cùng giao diện.

Cách thoát lệnh (EXIT_MODE): fixed | hybrid ("ăn sóng": chốt 1 phần rồi bám sóng) | trail | auto.
Logic thoát nằm ở exits.py và được dùng y hệt trong mô phỏng bóng của bộ tự học.
"""
import logging
import time
from .config import cfg
from .risk import position_notional, margin_for, risk_mult, risk_budget, risk_of
from . import exits
from . import strategy

log = logging.getLogger("engine")


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


class BaseEngine:
    def __init__(self, okx, store, notify):
        self.okx, self.store, self.notify = okx, store, notify
        self.on_close = None   # callback(trade, exit, pnl, pnl_pct, reason) – Bot gắn vào để AI tổng kết

    # ---------- helpers
    def _pnl(self, side, entry, exit_, notional):
        qty = notional / entry
        gross = (exit_ - entry) * qty if side == "long" else (entry - exit_) * qty
        fee = (notional + qty * exit_) * cfg.TAKER_FEE
        return gross - fee, fee

    def _size(self, sig, ai, equity, entry=None):
        """(notional, hệ số rủi ro): rủi ro cơ bản × chất lượng tín hiệu × chuỗi thua, trong ngân sách rủi ro còn lại."""
        try:
            recent = self.store.last_closed(6)
        except Exception:
            recent = []
        mult = risk_mult(sig, ai, recent)
        budget = risk_budget(equity, self.store.open_trades())
        notional = position_notional(equity, entry or sig.price, sig.sl, mult=mult, budget=budget)
        if notional < 1:
            log.info("%s: không mở – ngân sách rủi ro còn %.3f USDT (trần %.0f%% vốn)", sig.symbol, budget, cfg.MAX_TOTAL_RISK * 100)
        return notional, mult

    def _fit_lot(self, sig, notional, free):
        """Làm tròn theo lot của sàn. Trả về (contracts, notional thật) hoặc (0, 0) nếu không nên vào:
        lot tối thiểu vượt ký quỹ, hoặc làm rủi ro thật gấp > MIN_LOT_RISK_MULT lần rủi ro dự tính."""
        contracts, min_needed = self.okx.contracts_for_notional(sig.symbol, notional, sig.price)
        if contracts == 0:
            if min_needed > free * cfg.LEVERAGE * 0.9:
                log.info("%s: không đủ vốn cho lot tối thiểu (%.2f USDT)", sig.symbol, min_needed)
                return 0.0, 0.0
            if risk_of(min_needed, sig.price, sig.sl) > cfg.MIN_LOT_RISK_MULT * risk_of(notional, sig.price, sig.sl):
                log.info("%s: lot tối thiểu %.2f USDT làm rủi ro gấp >%gx dự tính (%.2f USDT) → bỏ", sig.symbol, min_needed,
                         cfg.MIN_LOT_RISK_MULT, notional)
                return 0.0, 0.0
            contracts, _ = self.okx.contracts_for_notional(sig.symbol, min_needed * 1.01, sig.price)
            if contracts == 0:
                return 0.0, 0.0
        m = self.okx.market(sig.symbol)
        return contracts, contracts * float(m.get("contractSize") or 1) * sig.price

    def _trade_row(self, sig, ai, entry, notional, contracts, order_id="", mult=1.0):
        mode, tp, tp1 = plan_exit(sig, entry)
        return dict(
            mode=cfg.MODE, symbol=sig.symbol, side=sig.side, entry=entry, exit=None,
            sl=sig.sl, tp=tp, notional=notional, margin=margin_for(notional), contracts=contracts,
            pnl=0, pnl_pct=0, fee=0, opened_at=time.time(), closed_at=None, status="open",
            reason_open="; ".join(sig.reasons) + (f" | AI: {ai.get('reason','')}" if ai else ""),
            reason_close="", ai_model=(ai or {}).get("model"), ai_conf=(ai or {}).get("confidence"),
            score=sig.score, order_id=order_id,
            tp1=tp1, tp1_done=0, peak=entry, peak_ts=time.time(), realized=0.0, atr=sig.atr,
            notional0=notional, exit_mode=mode, sl0=sig.sl, risk_mult=round(mult, 3), tp1_ts=None,
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
        self.notify(
            f"🟢 MỞ LỆNH {t['side'].upper()} {t['symbol']}\n"
            f"Giá: {t['entry']:.6g} | SL: {t['sl']:.6g} | {plan}\n"
            f"Khối lượng: {t['notional']:.2f} USDT (ký quỹ {t['margin']:.2f}, x{cfg.LEVERAGE}) | "
            f"rủi ro {risk_of(t['notional'], t['entry'], t['sl']):.3f} USDT ({t.get('risk_mult') or 1:.2f}× cơ bản)\n"
            f"Điểm KT: {t['score']} | AI: {t['ai_model']} ({t['ai_conf']}%)\n"
            f"Lý do: {t['reason_open'][:300]}")

    def _announce_partial(self, t, price, pnl):
        self.notify(
            f"💰 CHỐT {cfg.TP1_FRACTION*100:.0f}% {t['side'].upper()} {t['symbol']} tại {price:.6g}: {pnl:+.4f} USDT\n"
            f"Phần còn lại bám sóng, SL dời về hoà vốn {t['sl']:.6g}")

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
        """Tính PnL tổng (phần còn lại + phần đã chốt), ghi DB, thông báo."""
        pnl_rest, fee = self._pnl(t["side"], t["entry"], exit_, t["notional"])
        pnl = pnl_rest + float(t.get("realized") or 0)
        margin0 = margin_for(t.get("notional0") or t["notional"])
        pnl_pct = 100 * pnl / margin0 if margin0 else 0
        self.store.close_trade(t["id"], exit_, pnl, pnl_pct, fee, reason)
        t["pnl"] = pnl
        self._announce_close(t, exit_, pnl, pnl_pct, reason)
        return pnl

    def _events(self, t, hi, lo, price):
        st = self._state(t)
        risk0 = abs(t["entry"] - (t.get("sl0") or t["sl"]))
        ev = st.step(time.time(), hi, lo, price, risk0)
        upd = {}
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
        slip = cfg.SLIPPAGE if sig.side == "long" else -cfg.SLIPPAGE
        entry = sig.price * (1 + slip)
        notional, mult = self._size(sig, ai, equity, entry)     # tính theo giá khớp (đã trượt) để rủi ro đúng như dự tính
        if notional < 1:
            return None
        contracts, notional = self._fit_lot(sig, notional, equity)
        if not contracts:
            return None
        t = self._trade_row(sig, ai, entry, notional, contracts, mult=mult)
        t["id"] = self.store.open_trade(t)
        self._announce_open(t)
        return t

    def _fill(self, side, price):
        slip = -cfg.SLIPPAGE if side == "long" else cfg.SLIPPAGE
        return price * (1 + slip)

    def close(self, t, price, reason):
        exit_ = self._fill(t["side"], price)
        pnl_rest, _ = self._pnl(t["side"], t["entry"], exit_, t["notional"])
        self.store.set("paper_balance", self.balance() + pnl_rest)   # phần đã chốt (realized) đã cộng lúc chốt TP1
        self._finalize(t, exit_, reason)

    def partial(self, t, price):
        exit_ = self._fill(t["side"], price)
        part = t["notional"] * cfg.TP1_FRACTION
        pnl, _ = self._pnl(t["side"], t["entry"], exit_, part)
        self.store.set("paper_balance", self.balance() + pnl)
        t["notional"] -= part
        t["contracts"] = t["contracts"] * (1 - cfg.TP1_FRACTION)
        t["margin"] = margin_for(t["notional"])
        t["realized"] = float(t.get("realized") or 0) + pnl
        t["tp1_done"] = 1
        t["tp1_ts"] = time.time()
        self.store.update_trade(t["id"], notional=t["notional"], contracts=t["contracts"], margin=t["margin"],
                                realized=t["realized"], tp1_done=1, tp1_ts=t["tp1_ts"])
        return pnl

    def monitor(self):
        """Kiểm tra SL/TP/TP1/trailing bằng nến 1m gần nhất (high/low)."""
        opens = self.store.open_trades()
        if not opens:
            return
        for t in opens:
            try:
                candles = self.okx.ohlcv(t["symbol"], "1m", limit=2)
                last = candles[-1]
                hi, lo, price = float(last[2]), float(last[3]), float(last[4])
            except Exception as e:
                log.warning("monitor %s: %s", t["symbol"], e)
                continue
            ev, upd, st = self._events(t, hi, lo, price)
            closed = False
            for kind, p in ev:
                if kind == "TP1":
                    pnl = self.partial(t, p)
                    t["sl"] = st.sl
                    upd["sl"] = st.sl
                    self._announce_partial(t, p, pnl)
                    log.info("%s: chốt %.0f%% tại %.6g (%+.4f USDT)", t["symbol"], cfg.TP1_FRACTION * 100, p, pnl)
                elif kind == "SL_MOVE":
                    upd["sl"] = p
                    log.info("%s: dời SL -> %.6g", t["symbol"], p)
                elif kind in ("SL", "TP", "STALL", "TIMEOUT"):
                    exit_ = self._fill(t["side"], p)
                    pnl_rest, _ = self._pnl(t["side"], t["entry"], exit_, t["notional"])
                    self.store.set("paper_balance", self.balance() + pnl_rest)
                    self._finalize(t, exit_, self._reason(kind, t))
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
        notional, mult = self._size(sig, ai, total)
        if notional < 1:
            return None
        contracts, notional = self._fit_lot(sig, notional, free)
        if not contracts:
            return None
        if margin_for(notional) > free * 0.95:
            log.info("%s: ký quỹ không đủ", sig.symbol)
            return None
        _, tp, _ = plan_exit(sig, sig.price)
        oid, fill = self.okx.open_position(sig.symbol, sig.side, contracts, sig.sl, tp)
        entry = fill or sig.price
        t = self._trade_row(sig, ai, entry, notional, contracts, order_id=oid, mult=mult)
        t["id"] = self.store.open_trade(t)
        self._announce_open(t)
        return t

    def _replace_algo(self, t, sl, tp):
        """Đặt SL/TP MỚI trước, rồi mới huỷ lệnh cũ → vị thế không bao giờ trống SL trên sàn dù bước nào lỗi
        (bản cũ huỷ trước – đặt mới lỗi là mất SL)."""
        ex = self.okx.ex
        old = ex.fetch_open_orders(t["symbol"], params={"ordType": "conditional"})
        side = "sell" if t["side"] == "long" else "buy"
        new = ex.create_order(t["symbol"], "market", side, t["contracts"], params={
            "tdMode": "isolated", "reduceOnly": True,
            "stopLossPrice": ex.price_to_precision(t["symbol"], sl),
            "takeProfitPrice": ex.price_to_precision(t["symbol"], tp),
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
        """Đóng TP1_FRACTION vị thế bằng lệnh market reduceOnly, đặt lại SL/TP cho phần còn lại."""
        ex = self.okx.ex
        part_ct = float(ex.amount_to_precision(t["symbol"], t["contracts"] * cfg.TP1_FRACTION))
        if part_ct <= 0 or part_ct >= t["contracts"]:
            return 0.0
        side = "sell" if t["side"] == "long" else "buy"
        o = ex.create_order(t["symbol"], "market", side, part_ct, params={"tdMode": "isolated", "reduceOnly": True})
        try:
            fill = float(ex.fetch_order(o["id"], t["symbol"]).get("average") or price)
        except Exception:
            fill = price
        frac = part_ct / t["contracts"]
        part = t["notional"] * frac
        pnl, _ = self._pnl(t["side"], t["entry"], fill, part)
        t["contracts"] -= part_ct
        t["notional"] -= part
        t["margin"] = margin_for(t["notional"])
        t["realized"] = float(t.get("realized") or 0) + pnl
        t["tp1_done"] = 1
        t["tp1_ts"] = time.time()
        self.store.update_trade(t["id"], notional=t["notional"], contracts=t["contracts"], margin=t["margin"],
                                realized=t["realized"], tp1_done=1, tp1_ts=t["tp1_ts"])
        return pnl

    def close(self, t, price, reason):
        try:
            self.okx.close_position(t["symbol"], t["side"], t["contracts"])
        except Exception as e:
            log.error("close_position %s: %s", t["symbol"], e)
        self._finalize(t, price, reason)

    def monitor(self):
        """Đồng bộ với vị thế thật trên OKX; quản lý TP1/trailing bằng giá mark."""
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
            price = float(live[t["symbol"]].get("markPrice") or self.okx.price(t["symbol"]))
            ev, upd, st = self._events(t, price, price, price)
            closed, need_algo = False, False
            for kind, p in ev:
                if kind == "TP1":
                    try:
                        pnl = self.partial(t, p)
                        t["sl"] = st.sl
                        upd["sl"] = st.sl
                        need_algo = True
                        self._announce_partial(t, p, pnl)
                    except Exception as e:
                        log.error("chốt phần đầu %s thất bại: %s", t["symbol"], e)
                elif kind == "SL_MOVE":
                    upd["sl"] = p
                    need_algo = True
                elif kind in ("STALL", "TIMEOUT"):
                    self.close(t, price, self._reason(kind, t))
                    closed = True
                    break
                # SL/TP thật do sàn khớp – xử lý ở nhánh "không còn vị thế" lần sau
            if closed:
                continue
            if need_algo:
                try:
                    self._replace_algo(t, upd.get("sl", t["sl"]), t["tp"])
                    log.info("%s: cập nhật SL trên sàn -> %.6g", t["symbol"], upd.get("sl", t["sl"]))
                except Exception as e:
                    log.warning("đặt lại SL %s thất bại: %s", t["symbol"], e)
                    upd.pop("sl", None)
            if upd:
                self.store.update_trade(t["id"], **upd)
