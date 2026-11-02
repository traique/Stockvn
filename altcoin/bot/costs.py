"""Mô hình giá khớp & chi phí DÙNG CHUNG (v3.3) cho paper, live (tính cỡ lệnh), mô phỏng bóng và bộ tự học.

Mỗi chiều vào / ra là một lệnh market:
  * giá khớp = giá tham chiếu trượt SLIPPAGE theo hướng bất lợi (mua cao hơn, bán thấp hơn);
    giá tham chiếu lúc VÀO là giá last MỚI lúc ra quyết định (không phải giá đóng nến 15m đã cũ vài phút),
    lúc RA là mức SL/TP/TP1 bị chạm (hoặc giá mở cửa nếu nến mở vượt qua SL – xem exits.ExitState.step);
  * phí taker TAKER_FEE × giá trị khớp, cả hai chiều.
Một vòng vào–ra tốn ≈ 2 × (TAKER_FEE + SLIPPAGE) giá trị lệnh (mặc định 0.20%).

Khoảng cách SL, 1R, TP1 luôn tính từ GIÁ KHỚP VÀO (đã trượt). Sau khi đã vào, chi phí còn lại là phí hai chiều +
trượt giá chiều ra = after_fill_cost(); đó là con số risk.py dùng để tính cỡ lệnh và exits.simulate dùng để trừ vào R.
"""
from .config import cfg


def entry_fill(side, price):
    """Giá khớp lệnh market MỞ vị thế từ giá tham chiếu `price`."""
    return price * (1 + cfg.SLIPPAGE) if side == "long" else price * (1 - cfg.SLIPPAGE)


def exit_fill(side, price):
    """Giá khớp lệnh market ĐÓNG vị thế khi mức `price` bị chạm."""
    return price * (1 - cfg.SLIPPAGE) if side == "long" else price * (1 + cfg.SLIPPAGE)


def fee(value):
    """Phí taker của một chiều có giá trị khớp `value` (USDT)."""
    return abs(value) * cfg.TAKER_FEE


def after_fill_cost():
    """Chi phí (tỉ lệ trên giá trị lệnh) còn phải trả kể từ giá khớp vào: phí vào + phí ra + trượt giá chiều ra."""
    return 2 * cfg.TAKER_FEE + cfg.SLIPPAGE


def round_trip_cost():
    """Tổng chi phí một vòng tính từ giá tham chiếu chưa trượt: phí + trượt giá, cả hai chiều."""
    return 2 * (cfg.TAKER_FEE + cfg.SLIPPAGE)
