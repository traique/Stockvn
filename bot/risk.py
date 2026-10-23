"""Quản lý rủi ro & tính khối lượng lệnh.

v3.1 – rủi ro theo chất lượng: thay vì chặn cứng tín hiệu "vừa đủ" và vào hết cỡ với tín hiệu "đẹp",
mỗi lệnh có hệ số rủi ro = chất lượng (điểm KT + AI) × chuỗi thua gần đây, kẹp trong [RISK_MIN_MULT, RISK_MAX_MULT].
Tổng rủi ro mọi lệnh đang mở không vượt MAX_TOTAL_RISK vốn.
"""
from .config import cfg


def quality_mult(sig, ai):
    """Hệ số rủi ro theo chất lượng tín hiệu (1.0 = lệnh chuẩn: điểm ~6, AI đồng ý ≥ AI_MIN_CONFIDENCE)."""
    ai = ai or {}
    m = 1.0
    score = float(getattr(sig, "score", 0) or 0)
    if score >= 7.0:
        m *= 1.25
    elif score < 5.5:
        m *= 0.8
    conf = ai.get("confidence")
    if ai.get("fallback"):                       # AI lỗi – vào theo điểm KT
        m *= 0.6
    elif ai.get("override") or ai.get("weak"):   # AI phản đối yếu / đồng ý nhưng chưa chắc
        m *= 0.5
    elif conf is not None:
        try:
            conf = float(conf)
            if conf >= 80:
                m *= 1.2
            elif conf < 65:
                m *= 0.85
        except (TypeError, ValueError):
            pass
    return m


def streak_mult(closed_recent):
    """Giảm rủi ro sau chuỗi thua: lệnh thua liên tiếp thứ 2 trở đi nhân LOSS_STREAK_CUT mỗi lệnh.
    closed_recent: các lệnh đã đóng, MỚI NHẤT TRƯỚC."""
    streak = 0
    for t in closed_recent or []:
        if (t.get("pnl") or 0) <= 0:
            streak += 1
        else:
            break
    return cfg.LOSS_STREAK_CUT ** max(0, streak - 1)


def risk_mult(sig, ai, closed_recent=None):
    m = quality_mult(sig, ai) * streak_mult(closed_recent)
    return max(cfg.RISK_MIN_MULT, min(cfg.RISK_MAX_MULT, m))


def open_risk_usdt(open_trades):
    """USDT sẽ mất (gồm phí + trượt giá) nếu mọi lệnh đang mở chạm SL hiện tại (SL đã dời về hoà vốn → chỉ còn phí)."""
    total = 0.0
    for t in open_trades or []:
        entry, sl, notional = t.get("entry") or 0, t.get("sl") or 0, t.get("notional") or 0
        if not entry:
            continue
        loss = (entry - sl) if t.get("side") == "long" else (sl - entry)
        total += (max(0.0, loss / entry) + 2 * cfg.TAKER_FEE + cfg.SLIPPAGE) * notional
    return total


def risk_budget(equity, open_trades):
    """USDT còn được phép rủi ro thêm (trần MAX_TOTAL_RISK trừ rủi ro đang mở)."""
    return equity * cfg.MAX_TOTAL_RISK - open_risk_usdt(open_trades)


def position_notional(equity, entry, sl, mult=1.0, budget=None):
    """Giá trị lệnh (USDT) sao cho nếu chạm SL thì mất RISK_PER_TRADE * mult * equity,
    không vượt ngân sách rủi ro còn lại (budget) và ký quỹ tối đa (equity * leverage * 0.9)."""
    risk_usdt = equity * cfg.RISK_PER_TRADE * mult
    if budget is not None:
        if budget < 0.4 * risk_usdt:         # còn quá ít chỗ → không mở thêm
            return 0.0
        risk_usdt = min(risk_usdt, budget)
    sl_dist = abs(entry - sl) / entry
    if sl_dist <= 0:
        return 0.0
    # trừ phí vào/ra + trượt giá vào tổng rủi ro
    cost = 2 * cfg.TAKER_FEE + cfg.SLIPPAGE
    notional = risk_usdt / (sl_dist + cost)
    max_notional = equity * cfg.LEVERAGE * 0.9
    return max(0.0, min(notional, max_notional))


def risk_of(notional, entry, sl):
    """USDT mất nếu lệnh `notional` chạm SL (gồm phí + trượt giá)."""
    return notional * (abs(entry - sl) / entry + 2 * cfg.TAKER_FEE + cfg.SLIPPAGE) if entry else 0.0


def margin_for(notional):
    return notional / cfg.LEVERAGE


class CircuitBreaker:
    """Dừng giao dịch khi lỗ ngày / drawdown vượt ngưỡng."""

    def __init__(self, state):
        self.state = state

    def check(self, equity, today_start_equity, peak_equity):
        if today_start_equity > 0 and (today_start_equity - equity) / today_start_equity >= cfg.MAX_DAILY_LOSS:
            return False, f"Chạm giới hạn lỗ ngày ({cfg.MAX_DAILY_LOSS*100:.0f}%)"
        if peak_equity > 0 and (peak_equity - equity) / peak_equity >= cfg.MAX_DRAWDOWN:
            return False, f"Chạm giới hạn drawdown ({cfg.MAX_DRAWDOWN*100:.0f}%)"
        return True, ""
