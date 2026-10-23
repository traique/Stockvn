"""Logic thoát lệnh – dùng chung cho engine (paper/live) và mô phỏng bóng (learner).

Ba cách thoát:
  fixed  : TP cố định (ATR_TP_MULT × ATR), SL kéo về hoà vốn khi lời 1.5R, trailing 1R.
  hybrid : "ăn sóng" – chốt TP1_FRACTION khi lời TP1_R; phần còn lại không có TP gần,
           bám theo sóng bằng chandelier stop = đỉnh/đáy kể từ khi vào ∓ TRAIL_ATR_MULT × ATR
           (siết chặt hơn khi đã lời lớn); thoát nếu sóng đuối (không lập đỉnh mới quá STALL_HOURS).
  trail  : như hybrid nhưng không chốt phần đầu – toàn bộ vị thế bám sóng.

Mọi hàm ở đây thuần tính toán (không gọi sàn), để engine và learner dùng y hệt nhau
→ kết quả mô phỏng bóng phản ánh đúng cách bot giao dịch thật.
"""
from .config import cfg

MODES = ("fixed", "hybrid", "trail")


TP1_CANDIDATES = (1.0, 1.3, 1.6, 2.0, 2.5)   # các mức TP1 (R) bộ tự học thử song song trên dữ liệu bóng


def current_tp1_r():
    """TP1 (R) đang dùng: bộ tự học chọn mức cho lợi nhuận kỳ vọng cao nhất, mặc định cfg.TP1_R."""
    from . import strategy
    v = strategy.LEARNED.get("tp1_r")
    return float(v) if v else cfg.TP1_R


def levels(side, entry, sl, atr, mode, tp1_r=None, tp_fixed=None):
    """Trả về (tp, tp1): tp = TP cuối (fixed) hoặc TP an toàn rất xa (hybrid/trail); tp1 = mức chốt phần đầu.
    tp_fixed: TP do setup quy định sẵn (vd. range → dải giữa Bollinger) – chỉ dùng với mode fixed."""
    risk = abs(entry - sl)
    sign = 1 if side == "long" else -1
    if mode == "fixed":
        if tp_fixed:
            return tp_fixed, None
        tp = entry + sign * max(cfg.ATR_TP_MULT * atr, cfg.MIN_RR * 1.2 * risk)
        return tp, None
    tp = entry + sign * cfg.RUNNER_MAX_TP_ATR * atr
    tp1 = entry + sign * (tp1_r or current_tp1_r()) * risk if mode == "hybrid" else None
    return tp, tp1


def breakeven(side, entry):
    return entry * (1 + 3 * cfg.TAKER_FEE) if side == "long" else entry * (1 - 3 * cfg.TAKER_FEE)


def trail_stop(side, entry, peak, atr, risk, r_now):
    """Chandelier stop; siết dần khi lợi nhuận lớn."""
    mult = cfg.TRAIL_ATR_MULT
    if r_now >= cfg.TRAIL_TIGHTEN_R:
        mult = cfg.TRAIL_ATR_MULT_TIGHT
    dist = max(mult * atr, 0.5 * risk)          # không siết chặt hơn 0.5R
    return peak - dist if side == "long" else peak + dist


class ExitState:
    """Trạng thái 1 vị thế trong quá trình quản lý (dùng cho engine & mô phỏng)."""
    __slots__ = ("side", "entry", "sl", "tp", "tp1", "atr", "mode", "peak", "peak_ts", "tp1_done", "frac", "opened_at")

    def __init__(self, side, entry, sl, tp, tp1, atr, mode, opened_at, peak=None, peak_ts=None, tp1_done=False, frac=1.0):
        self.side, self.entry, self.sl, self.tp, self.tp1, self.atr = side, entry, sl, tp, tp1, atr
        self.mode, self.opened_at = mode, opened_at
        self.peak = peak if peak is not None else entry
        self.peak_ts = peak_ts if peak_ts is not None else opened_at
        self.tp1_done, self.frac = tp1_done, frac

    def better(self, a, b):
        return a > b if self.side == "long" else a < b

    def step(self, ts, hi, lo, close, risk0):
        """Xử lý 1 nến (hoặc 1 tick: hi=lo=close). Trả về danh sách sự kiện:
           ("SL", price) | ("TP", price) | ("TP1", price) | ("STALL", price) | ("TIMEOUT", price) | ("SL_MOVE", new_sl)
        Thứ tự thận trọng: SL trước, rồi TP/TP1, rồi cập nhật đỉnh & trailing."""
        ev = []
        long = self.side == "long"
        hit_sl = lo <= self.sl if long else hi >= self.sl
        if hit_sl:
            return [("SL", self.sl)]
        hit_tp = hi >= self.tp if long else lo <= self.tp
        if hit_tp:
            return [("TP", self.tp)]
        if self.mode == "hybrid" and not self.tp1_done and self.tp1 is not None:
            hit_tp1 = hi >= self.tp1 if long else lo <= self.tp1
            if hit_tp1:
                self.tp1_done = True
                self.frac = 1 - cfg.TP1_FRACTION
                ev.append(("TP1", self.tp1))
                be = breakeven(self.side, self.entry)
                if self.better(be, self.sl):
                    self.sl = be
                    ev.append(("SL_MOVE", be))
        # cập nhật đỉnh/đáy
        ext = hi if long else lo
        if self.better(ext, self.peak):
            self.peak, self.peak_ts = ext, ts
        r_now = ((close - self.entry) if long else (self.entry - close)) / risk0 if risk0 else 0
        r_peak = ((self.peak - self.entry) if long else (self.entry - self.peak)) / risk0 if risk0 else 0
        # trailing
        active = (self.mode == "fixed" and r_now >= cfg.TRAIL_ACTIVATE_R) or \
                 (self.mode == "hybrid" and self.tp1_done) or \
                 (self.mode == "trail" and r_peak >= cfg.TRAIL_ACTIVATE_R)
        if active:
            if self.mode == "fixed":
                new_sl = max(breakeven(self.side, self.entry), close - risk0) if long else min(breakeven(self.side, self.entry), close + risk0)
            else:
                new_sl = trail_stop(self.side, self.entry, self.peak, self.atr, risk0, r_peak)
                be = breakeven(self.side, self.entry)
                new_sl = max(new_sl, be) if long else min(new_sl, be)
            if self.better(new_sl, self.sl):
                self.sl = new_sl
                ev.append(("SL_MOVE", new_sl))
        # sóng đuối: đã chốt phần đầu / đang bám sóng mà lâu không lập đỉnh mới
        if self.mode != "fixed" and active and ts - self.peak_ts > cfg.STALL_HOURS * 3600:
            ev.append(("STALL", close))
            return ev
        max_hold = cfg.MAX_HOLD_HOURS if self.mode == "fixed" else cfg.RUNNER_MAX_HOLD_HOURS
        if ts - self.opened_at > max_hold * 3600:
            ev.append(("TIMEOUT", close))
        return ev


def simulate(side, entry, sl, atr, mode, t0, candles, cost_frac, max_wait_hours=None, tp1_r=None, tp_fixed=None):
    """Mô phỏng 1 tín hiệu trên nến (list [ts_ms,o,h,l,c,v]) theo cách thoát `mode`.
    Trả về (outcome, R) – R đã trừ phí+trượt giá; None nếu chưa kết thúc."""
    risk = abs(entry - sl)
    if risk <= 0:
        return "NODATA", 0.0
    tp, tp1 = levels(side, entry, sl, atr, mode, tp1_r=tp1_r, tp_fixed=tp_fixed)
    st = ExitState(side, entry, sl, tp, tp1, atr, mode, t0)
    cost_r = cost_frac * entry / risk
    realized = 0.0      # R đã chốt (phần đầu)
    frac = 1.0

    def r_of(price):
        return ((price - entry) if side == "long" else (entry - price)) / risk

    last_close = None
    for c in candles:
        ts = c[0] / 1000
        if ts + 300 <= t0:
            continue
        hi, lo, cl = float(c[2]), float(c[3]), float(c[4])
        last_close = cl
        for kind, price in st.step(ts + 300, hi, lo, cl, risk):
            if kind == "TP1":
                realized += r_of(price) * cfg.TP1_FRACTION
                frac = 1 - cfg.TP1_FRACTION
            elif kind in ("SL", "TP", "STALL", "TIMEOUT"):
                total = realized + r_of(price) * frac - cost_r
                label = kind if kind != "STALL" else "TRAIL"
                if kind == "SL" and st.tp1_done:
                    label = "TRAIL"
                return label, round(total, 3)
    return None, None
