"""Sinh tín hiệu kỹ thuật – phiên bản "khắt khe" để giảm tối đa số lần hỏi AI.

Nguyên tắc:
  * Chỉ dùng NẾN ĐÃ ĐÓNG (bỏ nến đang hình thành) → tín hiệu không đổi ý giữa chừng.
  * Có các CỔNG BẮT BUỘC (gate) – thiếu 1 cổng là loại ngay, không chấm điểm.
  * Phải có ít nhất 1 TRIGGER rõ ràng (sự kiện vừa xảy ra trong 1–3 nến gần nhất).
  * Sau đó mới chấm điểm hợp lưu (confluence); chỉ ứng viên tốt nhất mới được hỏi AI.
"""
import logging
import time
from dataclasses import dataclass, field, asdict
from .indicators import to_frame, enrich
from .config import cfg

log = logging.getLogger("strategy")

# Tham số do bộ tự học (learner.py) cập nhật. Mặc định = không can thiệp.
LEARNED = {"min_score": None, "trigger_w": {}, "blocks": [], "unblocked": [], "exit_mode": None, "ai_veto_conf": None,
           "tp1_r": None}

TRIGGER_KEYS = {"MACD": "macd_cross", "EMA9": "ema_cross", "Breakout": "breakout", "Breakdown": "breakout",
                "Pullback": "pullback", "Nến nhấn chìm": "engulf", "Đảo chiều tại biên": "range_revert",
                "Bật tại VWAP": "vwap_bounce", "Gãy tại VWAP": "vwap_bounce"}
TRIGGER_NAMES = {"macd_cross": "MACD cắt", "ema_cross": "EMA9 cắt EMA21", "breakout": "Breakout/Breakdown 20 nến",
                 "pullback": "Pullback EMA21", "engulf": "Nến nhấn chìm", "vwap_bounce": "Hồi về VWAP rồi bật",
                 "range_revert": "Đảo chiều tại biên Bollinger (vùng đi ngang)"}


def trig_key(name):
    for prefix, k in TRIGGER_KEYS.items():
        if name.startswith(prefix):
            return k
    return "other"


# Danh mục luật lọc mà bộ tự học ĐƯỢC PHÉP bật/tắt (không tự sinh luật mới)
RULES = {
    "setup_range": ("Setup đảo chiều trong vùng đi ngang (range)", lambda side, c: c.get("setup") == "range"),
    "short_htf_oversold": ("Short khi RSI 1h < 32 (đã quá bán)", lambda side, c: side == "short" and c.get("htf_rsi", 50) < 32),
    "long_htf_overbought": ("Long khi RSI 1h > 68 (đã quá mua)", lambda side, c: side == "long" and c.get("htf_rsi", 50) > 68),
    "short_bb_bottom": ("Short sát đáy Bollinger", lambda side, c: side == "short" and c.get("bb_pos", 0.5) < 0.08),
    "long_bb_top": ("Long sát đỉnh Bollinger", lambda side, c: side == "long" and c.get("bb_pos", 0.5) > 0.92),
    "chase_24h": ("Đánh theo hướng đã chạy >10% trong 24h",
                  lambda side, c: (side == "long" and c.get("change24h", 0) > 10) or (side == "short" and c.get("change24h", 0) < -10)),
    "night_session": ("Vào lệnh 0h–7h sáng (giờ VN, thanh khoản thấp)", lambda side, c: c.get("hour", 12) < 7),
    "afternoon_session": ("Vào lệnh 13h–19h (giờ VN, phiên chiều Á – hay phá vỡ giả)", lambda side, c: 13 <= c.get("hour", 12) < 19),
    "weak_adx": ("ADX 15m dưới 25", lambda side, c: c.get("adx", 30) < 25),
    "long_side": ("Mọi lệnh LONG", lambda side, c: side == "long"),
    "short_side": ("Mọi lệnh SHORT", lambda side, c: side == "short"),
    # ---- BTC dẫn dắt altcoin
    "long_btc_down": ("Long altcoin khi BTC 1h đang giảm (>0.5%)",
                      lambda side, c: side == "long" and _v(c, "btc_chg_1h", 0) < -0.5),
    "short_btc_up": ("Short altcoin khi BTC 1h đang tăng (>0.5%)",
                     lambda side, c: side == "short" and _v(c, "btc_chg_1h", 0) > 0.5),
    "against_btc_trend": ("Ngược xu hướng BTC khung 1h",
                          lambda side, c: _v(c, "btc_trend_1h", 0) == (-1 if side == "long" else 1)),
    "btc_volatile": ("BTC biến động mạnh (>1.5% trong 1h)", lambda side, c: abs(_v(c, "btc_chg_1h", 0)) > 1.5),
    "btc_dump_4h": ("BTC giảm >3% trong 4h", lambda side, c: _v(c, "btc_chg_4h", 0) < -3),
    # ---- Open Interest: có tiền mới vào hay chỉ là đóng vị thế
    "long_oi_falling": ("Long khi giá tăng nhưng OI giảm (không có tiền mới)",
                        lambda side, c: side == "long" and _v(c, "px_chg_1h", 0) > 0 and _v(c, "oi_chg_1h", 0) < -1),
    "short_oi_falling": ("Short khi giá giảm nhưng OI giảm (không có short mới)",
                         lambda side, c: side == "short" and _v(c, "px_chg_1h", 0) < 0 and _v(c, "oi_chg_1h", 0) < -1),
    "oi_spike": ("OI tăng đột biến >8%/1h (đòn bẩy dồn, dễ quét)", lambda side, c: _v(c, "oi_chg_1h", 0) > 8),
    # ---- Đám đông
    "crowded_long": ("Long khi tài khoản long/short > 2.5 (đám đông đã long)",
                     lambda side, c: side == "long" and _v(c, "ls_ratio", 1) > 2.5),
    "crowded_short": ("Short khi tài khoản long/short < 0.7 (đám đông đã short)",
                      lambda side, c: side == "short" and _v(c, "ls_ratio", 1) < 0.7),
    "funding_hot": ("Funding cực đoan cùng chiều lệnh (>0.05%)",
                    lambda side, c: (side == "long" and _v(c, "funding", 0) > 0.0005) or (side == "short" and _v(c, "funding", 0) < -0.0005)),
}


def active_blocks():
    """Luật đang chặn = bộ tự học chặn + luật chặn sẵn (DEFAULT_BLOCKS) mà bộ tự học chưa gỡ.
    Tín hiệu bị chặn vẫn được theo dõi bóng → nếu thực tế có lãi, bộ tự học tự gỡ chặn."""
    learned = set(LEARNED.get("blocks") or [])
    unblocked = set(LEARNED.get("unblocked") or [])
    return learned | {r for r in cfg.DEFAULT_BLOCKS if r in RULES and r not in unblocked}


def blocked_by(sig):
    """Luật đang chặn tín hiệu này (sau khi đã có đủ dữ liệu BTC/OI). Trả về mô tả hoặc ''."""
    blocks = active_blocks()
    for rule in matching_rules(sig.side, sig.context):
        if rule in blocks:
            return RULES[rule][0]
    return ""


def _v(c, k, default):
    v = c.get(k)
    return default if v is None else v


def matching_rules(side, ctx):
    return [k for k, (_, fn) in RULES.items() if fn(side, ctx)]


@dataclass
class Signal:
    symbol: str
    side: str                 # long | short
    score: float
    price: float
    atr: float
    sl: float
    tp: float
    reasons: list = field(default_factory=list)
    context: dict = field(default_factory=dict)
    rejected: str = ""        # lý do bị loại (chỉ để log/debug)
    triggers: list = field(default_factory=list)   # mã trigger: macd_cross, ema_cross, breakout, pullback, engulf

    @property
    def rr(self):
        risk = abs(self.price - self.sl)
        return abs(self.tp - self.price) / risk if risk else 0

    def to_dict(self):
        d = asdict(self)
        d["rr"] = round(self.rr, 2)
        return d


MOMENTUM_TRIGGERS = {"macd_cross", "ema_cross"}   # chỉ báo động lượng trễ – tính chung 1 nhóm
PULLBACK_TRIGGERS = {"pullback", "vwap_bounce"}   # cùng là "hồi về giá trị rồi bật" (EMA21 ≈ VWAP) – tính chung 1 nhóm


def _htf_trend(htf):
    """Xu hướng khung lớn: 1 tăng, -1 giảm, 0 sideway (EMA21/50 + ADX)."""
    r = htf.row(-1)
    if r.adx < cfg.HTF_MIN_ADX:
        return 0
    if r.ema21 > r.ema50 and r.close > r.ema21:
        return 1
    if r.ema21 < r.ema50 and r.close < r.ema21:
        return -1
    return 0


def _triggers(df, side):
    """Trả về danh sách trigger vừa xảy ra trong 3 nến đã đóng gần nhất."""
    r, p = df.row(-1), df.row(-2)
    n = df.n
    last3 = range(n - 3, n)
    out = []

    def crossed(up):
        for i in last3:
            h, hp = df.macd_hist[i], df.macd_hist[i - 1]
            if up and h > 0 and hp <= 0:
                return True
            if not up and h < 0 and hp >= 0:
                return True
        return False

    def ema_cross(up):
        for i in last3:
            a, b = df.ema9[i] - df.ema21[i], df.ema9[i - 1] - df.ema21[i - 1]
            if up and a > 0 and b <= 0:
                return True
            if not up and a < 0 and b >= 0:
                return True
        return False

    if side == "long":
        if crossed(True):
            out.append("MACD cắt lên")
        if ema_cross(True):
            out.append("EMA9 cắt lên EMA21")
        if r.close > p.hh20 and r.vol_ratio >= cfg.BREAKOUT_VOL:
            out.append(f"Breakout đỉnh 20 nến, vol x{r.vol_ratio:.1f}")
        touched = any(df.low[i] <= df.ema21[i] * 1.002 for i in last3)
        if touched and r.close > r.ema21 and r.close > r.open and r.close_pos >= 0.6:
            out.append("Pullback EMA21 + nến bật")
        # v3.1: hồi về VWAP (giá trị trung bình theo volume trong ngày) rồi bật – cấu trúc hay gặp khi EMA21 ở xa
        touched_v = any(df.low[i] <= df.vwap[i] * 1.002 for i in last3)
        if touched_v and r.close > r.vwap and r.close > r.open and r.close_pos >= 0.6:
            out.append("Bật tại VWAP")
        if p.close < p.open and r.close > p.open and r.open <= p.close * 1.001 and r.vol_ratio >= 1.0:
            out.append("Nến nhấn chìm tăng")
    else:
        if crossed(False):
            out.append("MACD cắt xuống")
        if ema_cross(False):
            out.append("EMA9 cắt xuống EMA21")
        if r.close < p.ll20 and r.vol_ratio >= cfg.BREAKOUT_VOL:
            out.append(f"Breakdown đáy 20 nến, vol x{r.vol_ratio:.1f}")
        touched = any(df.high[i] >= df.ema21[i] * 0.998 for i in last3)
        if touched and r.close < r.ema21 and r.close < r.open and r.close_pos <= 0.4:
            out.append("Pullback EMA21 + nến gãy")
        touched_v = any(df.high[i] >= df.vwap[i] * 0.998 for i in last3)
        if touched_v and r.close < r.vwap and r.close < r.open and r.close_pos <= 0.4:
            out.append("Gãy tại VWAP")
        if p.close > p.open and r.close < p.open and r.open >= p.close * 0.999 and r.vol_ratio >= 1.0:
            out.append("Nến nhấn chìm giảm")
    return out


def _gates(r, side, trend, market=None):
    """Các điều kiện bắt buộc. Trả về chuỗi lý do loại, hoặc '' nếu qua hết."""
    want = 1 if side == "long" else -1
    if trend != want:
        return "khung lớn không cùng hướng"
    m = market or {}
    # Cổng BTC bắt buộc: altcoin đi theo BTC, không đánh ngược xu hướng / động lượng BTC
    if side == "long" and (m.get("btc_trend_1h") == -1 or (m.get("btc_chg_1h") or 0) < -cfg.BTC_GATE_CHG):
        return "BTC đang xu hướng giảm"
    if side == "short" and (m.get("btc_trend_1h") == 1 or (m.get("btc_chg_1h") or 0) > cfg.BTC_GATE_CHG):
        return "BTC đang xu hướng tăng"
    if r.adx < cfg.MIN_ADX:
        return f"ADX thấp ({r.adx:.0f} < {cfg.MIN_ADX:.0f})"
    if r.atr_pct < cfg.MIN_ATR_PCT:
        return f"biến động thấp ({r.atr_pct:.2f}%)"
    if r.vol_ratio < cfg.MIN_VOL_RATIO:
        return f"volume yếu (x{r.vol_ratio:.2f})"
    if abs(r.close - r.ema21) > cfg.MAX_EXT_ATR * r.atr:
        return "giá đã chạy quá xa EMA21 (đuổi giá)"
    if side == "long":
        if not (cfg.RSI_LONG_MIN <= r.rsi <= cfg.RSI_LONG_MAX):
            return f"RSI ngoài vùng ({r.rsi:.0f})"
        if r.close < r.open:
            return "nến tín hiệu ngược hướng"
        if r.close_pos < cfg.WICK_CLOSE_POS:
            return f"râu trên dài – bị từ chối giá (close_pos {r.close_pos:.2f})"
    else:
        if not (cfg.RSI_SHORT_MIN <= r.rsi <= cfg.RSI_SHORT_MAX):
            return f"RSI ngoài vùng ({r.rsi:.0f})"
        if r.close > r.open:
            return "nến tín hiệu ngược hướng"
        if r.close_pos > 1 - cfg.WICK_CLOSE_POS:
            return f"râu dưới dài – bị rút chân (close_pos {r.close_pos:.2f})"
    return ""


def _score(df, htf, side, triggers, funding, change24h):
    r = df.row(-1)
    h = htf.row(-1)
    s, why = 0.0, []
    tw = LEARNED.get("trigger_w") or {}
    # MACD cắt & EMA9 cắt cùng là chỉ báo động lượng trễ (tương quan cao) → chỉ tính 1 cái mạnh nhất.
    # Pullback EMA21 & bật tại VWAP cùng một cấu trúc → cũng chỉ tính 1. Tối đa 2 nhóm trigger được cộng điểm.
    keys = [trig_key(t) for t in triggers]
    mom = [tw.get(k, 1.0) for k in keys if k in MOMENTUM_TRIGGERS]
    pb = [tw.get(k, 1.0) for k in keys if k in PULLBACK_TRIGGERS]
    struct = [tw.get(k, 1.0) for k in keys if k not in MOMENTUM_TRIGGERS and k not in PULLBACK_TRIGGERS]
    weights = sorted(([max(mom)] if mom else []) + ([max(pb)] if pb else []) + struct, reverse=True)[:2]
    s += sum(1.5 * w for w in weights); why += triggers
    # EMA stack đồng bộ
    if (side == "long" and r.ema9 > r.ema21 > r.ema50) or (side == "short" and r.ema9 < r.ema21 < r.ema50):
        s += 1; why.append("EMA 9/21/50 xếp đúng thứ tự")
    # EMA200 cùng phía
    if (side == "long" and r.close > r.ema200) or (side == "short" and r.close < r.ema200):
        s += 0.5; why.append("Đúng phía EMA200")
    # ADX mạnh
    if r.adx >= 30:
        s += 1; why.append(f"ADX {r.adx:.0f} mạnh")
    elif r.adx >= 25:
        s += 0.5; why.append(f"ADX {r.adx:.0f}")
    # Khung lớn: DI đồng thuận
    if (side == "long" and h.di_plus > h.di_minus) or (side == "short" and h.di_minus > h.di_plus):
        s += 0.5; why.append("DI khung 1h đồng thuận")
    # Khung lớn: RSI & MACD cùng hướng
    if (side == "long" and h.macd_hist > 0 and 50 < h.rsi < 72) or (side == "short" and h.macd_hist < 0 and 28 < h.rsi < 50):
        s += 1; why.append("MACD+RSI khung 1h đồng thuận")
    # VWAP
    if (side == "long" and r.close > r.vwap) or (side == "short" and r.close < r.vwap):
        s += 0.5; why.append("Đúng phía VWAP")
    # Volume mạnh
    if r.vol_ratio >= 1.5:
        s += 0.5; why.append(f"Volume x{r.vol_ratio:.1f}")
    # Nến đóng mạnh
    if (side == "long" and r.close_pos >= 0.7 and r.body_pct >= 0.5) or (side == "short" and r.close_pos <= 0.3 and r.body_pct >= 0.5):
        s += 0.5; why.append("Nến đóng dứt khoát")
    # Funding: ngược đám đông được cộng, cùng đám đông quá nóng bị trừ
    if side == "long" and funding < -0.0003:
        s += 0.5; why.append(f"Funding âm {funding*100:.3f}%")
    if side == "short" and funding > 0.0003:
        s += 0.5; why.append(f"Funding dương {funding*100:.3f}%")
    if side == "long" and funding > 0.001:
        s -= 1
    if side == "short" and funding < -0.001:
        s -= 1
    # Đã chạy quá xa trong 24h
    if (side == "long" and change24h > 20) or (side == "short" and change24h < -20):
        s -= 1.5; why.append("Đã biến động mạnh 24h (trừ điểm)")
    return round(s, 2), why


def _levels(df, side, price, a):
    """SL theo cấu trúc (đáy/đỉnh 6 nến) – KHÔNG bao giờ kéo SL vào giữa vùng râu nến.
    Cấu trúc quá xa → diagnose() bỏ tín hiệu thay vì đặt SL sai chỗ."""
    if side == "long":
        swing = min(df.low[-6:])
        sl = min(price - cfg.ATR_SL_MULT * a, swing - 0.15 * a)
        tp = price + max(cfg.ATR_TP_MULT * a, cfg.MIN_RR * 1.2 * (price - sl))   # luôn đảm bảo RR >= MIN_RR
    else:
        swing = max(df.high[-6:])
        sl = max(price + cfg.ATR_SL_MULT * a, swing + 0.15 * a)
        tp = price - max(cfg.ATR_TP_MULT * a, cfg.MIN_RR * 1.2 * (sl - price))
    return sl, tp


def flow_adjust(sig):
    """Điểm dòng tiền (OI / funding) – gọi SAU khi đã lấy symbol_context, TRƯỚC khi xét ngưỡng & xếp hạng.
    Trả về (điểm cộng/trừ, lý do)."""
    c = sig.context or {}
    d, why = 0.0, []
    sign = 1 if sig.side == "long" else -1
    oi, px = c.get("oi_chg_1h"), c.get("px_chg_1h")
    if oi is not None and c.get("setup") != "range":     # range: giá đang đi ngược hướng lệnh → luật OI không áp dụng
        if oi > 1.0 and px is not None and sign * px > 0:
            d += 0.5; why.append(f"OI +{oi:.1f}%/1h cùng giá chạy (tiền mới vào)")
        elif oi < -1.5:
            d -= 1.0; why.append(f"OI {oi:.1f}%/1h – giá chạy nhưng tiền rút (trừ điểm)")
    f = c.get("funding")
    if f is not None:
        if (sig.side == "long" and f < -0.0003) or (sig.side == "short" and f > 0.0003):
            d += 0.5; why.append(f"Funding ngược đám đông {f*100:+.3f}%")
        if (sig.side == "long" and f > 0.001) or (sig.side == "short" and f < -0.001):
            d -= 1.0; why.append(f"Funding quá nóng {f*100:+.3f}% (trừ điểm)")
    return round(d, 2), why


def apply_flow(sig):
    """Cộng điểm dòng tiền vào tín hiệu và xét lại ngưỡng. Trả về lý do loại ('' nếu đạt)."""
    d, why = flow_adjust(sig)
    if d:
        sig.score = round(sig.score + d, 2)
        sig.reasons = list(sig.reasons) + why
    min_score = min_score_for(sig)
    if sig.score < min_score:
        sig.rejected = f"điểm thấp ({sig.score} < {min_score})"
        return sig.rejected
    sig.rejected = ""
    return ""


def describe_candles(df, a, n=6):
    """Mô tả n nến đã đóng gần nhất bằng chữ, chuẩn hoá theo ATR (LLM đọc số lẻ thô rất kém)."""
    out = []
    rows = df.rows(-n)
    for k, x in enumerate(rows):
        rng = (x.high - x.low) or 1e-12
        body = x.close - x.open
        up_w = (x.high - max(x.open, x.close)) / rng
        dn_w = (min(x.open, x.close) - x.low) / rng
        tag = f"nến -{n - k}" + (" (tín hiệu)" if k == n - 1 else "")
        out.append(f"{tag}: {'tăng' if body >= 0 else 'giảm'} {body / a:+.1f} ATR, biên độ {rng / a:.1f} ATR, "
                   f"thân {abs(body) / rng * 100:.0f}%, râu trên {up_w * 100:.0f}%, râu dưới {dn_w * 100:.0f}%, "
                   f"close_pos {x.close_pos:.2f}, vol x{x.vol_ratio:.1f}")
    r = df.row(-1)
    out.append(f"khoảng cách: tới đỉnh 20 nến {(r.hh20 - r.close) / a:+.1f} ATR, tới đáy 20 nến {(r.close - r.ll20) / a:+.1f} ATR, "
               f"tới EMA21 {(r.close - r.ema21) / a:+.1f} ATR")
    return out


def _context(df, htf, trend, funding, change24h, market, a, setup):
    r, h = df.row(-1), htf.row(-1)
    ctx = {
        "setup": setup,
        "price": float(r.close), "rsi": round(float(r.rsi), 1), "adx": round(float(r.adx), 1),
        "atr_pct": round(float(r.atr_pct), 2), "vol_ratio": round(float(r.vol_ratio), 2),
        "ema9": round(float(r.ema9), 6), "ema21": round(float(r.ema21), 6), "ema50": round(float(r.ema50), 6),
        "ema200": round(float(r.ema200), 6), "vwap": round(float(r.vwap), 6),
        "macd_hist": round(float(r.macd_hist), 6), "close_pos": round(float(r.close_pos), 2),
        "htf_trend": trend, "htf_rsi": round(float(h.rsi), 1), "htf_adx": round(float(h.adx), 1),
        "funding": funding, "change24h": round(change24h, 2), "hour": time.localtime().tm_hour,
        "bb_pos": round(float((r.close - r.bb_lo) / (r.bb_up - r.bb_lo)), 2) if r.bb_up != r.bb_lo else 0.5,
        "last_candles": describe_candles(df, a),
    }
    if market:
        ctx.update(market)
    return ctx


def _range_setup(symbol, df, htf, funding, change24h, market, info):
    """Setup đảo chiều khi khung 1h đi ngang: giá chạm biên Bollinger 15m, RSI cực trị rồi quay đầu,
    nến từ chối rõ ràng → mục tiêu dải giữa Bollinger (TP gần, tỉ lệ thắng cao). Trả về (sig, lý do)."""
    r, p, h = df.row(-1), df.row(-2), htf.row(-1)
    if h.adx >= cfg.RANGE_MAX_HTF_ADX or r.adx >= cfg.RANGE_MAX_ADX:
        return None, "1h sideway nhưng 15m đang có lực (không đánh range)"
    if r.atr_pct < cfg.MIN_ATR_PCT:
        return None, f"biến động thấp ({r.atr_pct:.2f}%)"
    if r.vol_ratio < cfg.MIN_VOL_RATIO:
        return None, f"volume yếu (x{r.vol_ratio:.2f})"
    n = df.n
    rsi_min = min(df.rsi[n - 3:n]); rsi_max = max(df.rsi[n - 3:n])
    touch_lo = any(df.low[i] <= df.bb_lo[i] for i in range(n - 2, n))
    touch_hi = any(df.high[i] >= df.bb_up[i] for i in range(n - 2, n))
    side = None
    if touch_lo and rsi_min <= cfg.RANGE_RSI_LONG and r.rsi > p.rsi and r.close > r.open \
            and r.close_pos >= cfg.RANGE_CLOSE_POS and r.close > r.bb_lo:
        side = "long"
    elif touch_hi and rsi_max >= 100 - cfg.RANGE_RSI_LONG and r.rsi < p.rsi and r.close < r.open \
            and r.close_pos <= 1 - cfg.RANGE_CLOSE_POS and r.close < r.bb_up:
        side = "short"
    if side is None:
        return None, "1h sideway – chưa có nhịp đảo chiều tại biên"
    info["side"] = side
    m = market or {}
    if side == "long" and (m.get("btc_trend_1h") == -1 or (m.get("btc_chg_1h") or 0) < -cfg.BTC_GATE_CHG):
        return None, "BTC đang xu hướng giảm"
    if side == "short" and (m.get("btc_trend_1h") == 1 or (m.get("btc_chg_1h") or 0) > cfg.BTC_GATE_CHG):
        return None, "BTC đang xu hướng tăng"
    price, a = float(r.close), float(r.atr)
    if side == "long":
        sl = min(df.low[n - 3:n]) - 0.25 * a
        tp = float(r.bb_mid)
    else:
        sl = max(df.high[n - 3:n]) + 0.25 * a
        tp = float(r.bb_mid)
    risk = abs(price - sl)
    if risk < 0.6 * a:                                   # SL quá sát → dễ bị quét nhiễu
        sl = price - 0.6 * a if side == "long" else price + 0.6 * a
        risk = 0.6 * a
    if risk > cfg.RANGE_MAX_SL_ATR * a:
        return None, f"range: SL cấu trúc quá xa ({risk / a:.1f} ATR)"
    rr = abs(tp - price) / risk
    if rr < cfg.RANGE_MIN_RR:
        return None, f"range: không đủ chỗ tới dải giữa (RR {rr:.2f})"

    # điểm
    tw = LEARNED.get("trigger_w") or {}
    trig = "Đảo chiều tại biên Bollinger + RSI quay đầu"
    s_, why = 1.5 * tw.get("range_revert", 1.0), [trig, "Chạm biên Bollinger & đóng lại trong biên"]
    s_ += 1.0
    ext = rsi_min if side == "long" else 100 - rsi_max
    if ext <= 28:
        s_ += 1.0; why.append(f"RSI cực trị ({rsi_min if side == 'long' else rsi_max:.0f})")
    elif ext <= 32:
        s_ += 0.5; why.append(f"RSI mạnh ({rsi_min if side == 'long' else rsi_max:.0f})")
    if (side == "long" and r.close_pos >= 0.75) or (side == "short" and r.close_pos <= 0.25):
        s_ += 0.5; why.append("Nến từ chối mạnh")
    if r.vol_ratio >= 1.3:
        s_ += 0.5; why.append(f"Volume x{r.vol_ratio:.1f}")
    if r.adx < 18:
        s_ += 0.5; why.append(f"Đi ngang rõ (ADX15 {r.adx:.0f})")
    if 40 <= h.rsi <= 60:
        s_ += 0.5; why.append("RSI 1h trung tính")
    if (side == "long" and (m.get("btc_chg_1h") or 0) >= 0) or (side == "short" and (m.get("btc_chg_1h") or 0) <= 0):
        s_ += 0.5; why.append("BTC không đi ngược")
    if rr >= 1.8:
        s_ += 0.5; why.append(f"RR tới dải giữa {rr:.1f}")
    if (side == "long" and change24h < -15) or (side == "short" and change24h > 15):
        s_ -= 1.0; why.append("Đang rơi/tăng mạnh 24h (dao rơi – trừ điểm)")
    score = round(s_, 2)
    info["score"] = score
    ctx = _context(df, htf, 0, funding, change24h, market, a, "range")
    ctx["bb_mid"] = round(tp, 8)
    sig = Signal(symbol, side, score, price, a, sl, tp, why, ctx, triggers=["range_revert"])
    ms = min_score_for(sig)
    if score < ms:
        sig.rejected = f"điểm thấp ({score} < {ms})"
        return (sig if score >= ms - 1.5 else None), sig.rejected
    return sig, ""


def min_rr_for(sig):
    return cfg.RANGE_MIN_RR if (sig.context or {}).get("setup") == "range" else cfg.MIN_RR


def min_score_for(sig):
    """Ngưỡng điểm theo setup: xu hướng dùng MIN_SCORE (bộ tự học chỉnh), range dùng RANGE_MIN_SCORE."""
    if (sig.context or {}).get("setup") == "range":
        return cfg.RANGE_MIN_SCORE
    return LEARNED.get("min_score") or cfg.MIN_SCORE


def diagnose(symbol, ohlcv_ltf, ohlcv_htf, funding=0.0, change24h=0.0, market=None):
    """Trả về (signal, lý do loại, số liệu tóm tắt).
    - lý do == ""          → tín hiệu đạt, gửi AI
    - signal != None + lý do → tín hiệu "suýt đạt" (điểm thấp / RR thấp / bị luật tự học chặn) – vẫn được
      ghi lại để theo dõi kết quả bóng, giúp bộ tự học biết ngưỡng có quá chặt không.
    - signal == None       → bị loại ở cổng bắt buộc."""
    if len(ohlcv_ltf) < 120 or len(ohlcv_htf) < 80:
        return None, "thiếu dữ liệu", {}
    df = enrich(to_frame(ohlcv_ltf[:-1]))      # bỏ nến đang hình thành → chỉ dùng nến đã đóng
    htf = enrich(to_frame(ohlcv_htf[:-1]))
    r, h = df.row(-1), htf.row(-1)
    info = {"adx15": round(r.adx, 1), "adx1h": round(h.adx, 1), "rsi": round(r.rsi, 1),
            "atr%": round(r.atr_pct, 2), "vol": round(r.vol_ratio, 2)}
    trend = _htf_trend(htf)
    if trend == 0:
        if cfg.RANGE_ENABLED:
            sig, why = _range_setup(symbol, df, htf, funding, change24h, market, info)
            return sig, why, info
        why = "1h sideway" if h.adx < cfg.HTF_MIN_ADX else "1h không rõ xu hướng (giá/EMA lệch nhau)"
        return None, why, info
    side = "long" if trend == 1 else "short"
    info["side"] = side
    gate = _gates(r, side, trend, market)
    if gate:
        return None, gate, info
    triggers = _triggers(df, side)
    if not triggers:
        return None, "không có trigger (MACD/EMA cắt, breakout, pullback, nhấn chìm)", info
    score, reasons = _score(df, htf, side, triggers, funding, change24h)
    info["score"] = score

    price, a = float(r.close), float(r.atr)
    sl, tp = _levels(df, side, price, a)
    if abs(price - sl) > cfg.MAX_SL_ATR * a:
        return None, f"SL theo cấu trúc quá xa ({abs(price - sl) / a:.1f} ATR)", info
    ctx = _context(df, htf, trend, funding, change24h, market, a, "trend")
    sig = Signal(symbol, side, score, price, a, sl, tp, reasons, ctx,
                 triggers=sorted({trig_key(t) for t in triggers}))

    min_score = min_score_for(sig)
    if score < min_score:
        sig.rejected = f"điểm thấp ({score} < {min_score})"
        return (sig if score >= min_score - 1.5 else None), sig.rejected, info
    if sig.rr < cfg.MIN_RR:
        sig.rejected = f"RR thấp ({sig.rr:.2f})"
        return None, sig.rejected, info
    return sig, "", info


def analyze(symbol, ohlcv_ltf, ohlcv_htf, funding=0.0, change24h=0.0):
    sig, why, _ = diagnose(symbol, ohlcv_ltf, ohlcv_htf, funding, change24h)
    return sig if (sig and not why) else None
