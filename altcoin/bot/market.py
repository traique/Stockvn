"""Bối cảnh thị trường – dữ liệu ngoài biểu đồ của từng coin.

1) BTC: altcoin đi theo BTC rất chặt. Mỗi lượt quét lấy BTC 15m + 1h một lần:
   xu hướng 1h, % thay đổi 1h / 4h, RSI 1h.
2) Riêng từng tín hiệu (chỉ gọi cho vài tín hiệu đã qua bộ lọc → ít request):
   - Open Interest thay đổi trong 1h (có tiền mới vào hay chỉ là đóng vị thế)
   - Tỉ lệ tài khoản long/short (đám đông lệch về một phía)
   - Funding rate
Tất cả được đưa vào ngữ cảnh tín hiệu → AI đọc được, bộ tự học dùng để thống kê/chặn.
Lỗi API không làm hỏng tín hiệu: trường nào không lấy được thì bỏ trống.
"""
import logging
import time
from .indicators import to_frame, enrich

log = logging.getLogger("market")

BTC = "BTC/USDT:USDT"


def _pct(a, b):
    return round(100 * (a - b) / b, 2) if b else 0.0


def btc_context(okx):
    try:
        m15 = okx.ohlcv(BTC, "15m", 40)
        h1 = enrich(to_frame(okx.ohlcv(BTC, "1h", 80)[:-1]))
    except Exception as e:
        log.warning("không lấy được dữ liệu BTC: %s", e)
        return {}
    closes = [float(c[4]) for c in m15]
    last = closes[-1]
    r = h1.row(-1)
    if r.ema21 > r.ema50 and r.close > r.ema21:
        trend = 1
    elif r.ema21 < r.ema50 and r.close < r.ema21:
        trend = -1
    else:
        trend = 0
    return {
        "btc_price": round(last, 1),
        "btc_chg_1h": _pct(last, closes[-5]),
        "btc_chg_4h": _pct(last, closes[-17]),
        "btc_trend_1h": trend,
        "btc_rsi_1h": round(float(r.rsi), 1),
        "btc_adx_1h": round(float(r.adx), 1),
    }


def symbol_context(okx, symbol):
    out = {}
    ex = okx.ex
    try:
        oi = ex.fetch_open_interest_history(symbol, "5m", limit=13)
        # bỏ các điểm OI = 0 (nến 5m chưa chốt / sàn trả thiếu) để tránh báo sai -100%
        pts = sorted((x for x in oi if x.get("timestamp")), key=lambda x: x["timestamp"])
        vals = [float(x.get("openInterestValue") or x.get("openInterestAmount") or 0) for x in pts]
        vals = [v for v in vals if v > 0]
        if len(vals) >= 2:
            a, b = vals[-1], vals[0]
            chg = _pct(a, b)
            if abs(chg) < 80:            # biến động OI >80%/1h gần như chắc chắn là dữ liệu lỗi
                out["oi_chg_1h"] = chg
            out["oi_usd"] = round(a)
    except Exception as e:
        log.debug("OI %s: %s", symbol, e)
    try:
        c = okx.ohlcv(symbol, "5m", 14)
        out["px_chg_1h"] = _pct(float(c[-1][4]), float(c[-13][4]))
    except Exception:
        pass
    try:
        ls = ex.fetch_long_short_ratio_history(symbol, "5m", limit=2)
        if ls:
            out["ls_ratio"] = round(float(ls[-1]["longShortRatio"]), 2)
    except Exception as e:
        log.debug("L/S %s: %s", symbol, e)
    try:
        out["funding"] = float(ex.fetch_funding_rate(symbol).get("fundingRate") or 0)
    except Exception:
        pass
    return out


def describe(ctx):
    """Chuỗi ngắn cho log / Telegram."""
    parts = []
    if "btc_chg_1h" in ctx:
        tr = {1: "tăng", -1: "giảm", 0: "đi ngang"}.get(ctx.get("btc_trend_1h"), "?")
        parts.append(f"BTC {ctx['btc_chg_1h']:+.2f}%/1h, xu hướng 1h {tr}")
    if "oi_chg_1h" in ctx:
        parts.append(f"OI {ctx['oi_chg_1h']:+.1f}%/1h")
    if "ls_ratio" in ctx:
        parts.append(f"L/S {ctx['ls_ratio']}")
    if ctx.get("funding"):
        parts.append(f"funding {ctx['funding']*100:+.3f}%")
    return " | ".join(parts)
