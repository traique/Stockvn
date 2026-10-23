"""Kiểm tra rủi ro v3.1: hệ số theo chất lượng & chuỗi thua, trần tổng rủi ro khi mở 2 lệnh, chặn lot tối thiểu quá to."""
import os, shutil
os.environ.update(MODE="paper", DATA_DIR="/tmp/okx-risk-test", AI_ENABLED="false", DATABASE_URL="")
shutil.rmtree("/tmp/okx-risk-test", ignore_errors=True)
from bot.config import cfg
from bot import risk as R
from bot.storage import Store
from bot.engine import PaperEngine
from bot.strategy import Signal

sig = Signal("X/USDT:USDT", "long", 6.0, 100.0, 1.0, 97.0, 106.0, [], {})

# 1) hệ số chất lượng
assert R.quality_mult(sig, {"confidence": 70}) == 1.0
assert R.quality_mult(Signal("X", "long", 7.5, 100, 1, 97, 106), {"confidence": 85}) == 1.25 * 1.2
assert R.quality_mult(sig, {"confidence": 55, "weak": True}) == 0.5
assert R.quality_mult(sig, {"confidence": 40, "override": True}) == 0.5
assert R.quality_mult(sig, {"fallback": True}) == 0.6
assert R.quality_mult(Signal("X", "long", 5.0, 100, 1, 97, 106), {"confidence": 62}) == 0.8 * 0.85
print("✓ hệ số chất lượng (điểm KT × AI)")

# 2) chuỗi thua: lệnh thua thứ 2 trở đi mới co
L, W = {"pnl": -1}, {"pnl": 1}
assert R.streak_mult([W, L, L]) == 1.0
assert R.streak_mult([L]) == 1.0
assert abs(R.streak_mult([L, L]) - cfg.LOSS_STREAK_CUT) < 1e-9
assert abs(R.streak_mult([L, L, L, W, L]) - cfg.LOSS_STREAK_CUT ** 2) < 1e-9
assert R.risk_mult(sig, {"confidence": 40, "override": True}, [L] * 6) == cfg.RISK_MIN_MULT      # kẹp sàn
assert R.risk_mult(Signal("X", "long", 8, 100, 1, 97, 106), {"confidence": 90}, []) == cfg.RISK_MAX_MULT  # kẹp trần
print("✓ chuỗi thua & kẹp [%.2f, %.2f]" % (cfg.RISK_MIN_MULT, cfg.RISK_MAX_MULT))

# 3) khối lượng: chạm SL mất đúng RISK × mult × vốn (gồm phí)
eq = 100.0
n = R.position_notional(eq, 100, 97, mult=1.0)
assert abs(R.risk_of(n, 100, 97) - eq * cfg.RISK_PER_TRADE) < 1e-6
n2 = R.position_notional(eq, 100, 97, mult=1.0, budget=eq * cfg.RISK_PER_TRADE * 0.6)
assert abs(R.risk_of(n2, 100, 97) - eq * cfg.RISK_PER_TRADE * 0.6) < 1e-6     # bị ngân sách cắt
assert R.position_notional(eq, 100, 97, mult=1.0, budget=eq * cfg.RISK_PER_TRADE * 0.3) == 0   # còn quá ít → không mở
print("✓ khối lượng theo rủi ro & ngân sách")

# 4) 2 lệnh: tổng rủi ro đang mở không vượt MAX_TOTAL_RISK
class FakeOKX:
    def __init__(self, lot=0.001): self.lot = lot
    def market(self, s): return {"contractSize": 1, "limits": {"amount": {"min": self.lot}}, "precision": {"amount": self.lot}}
    def contracts_for_notional(self, s, notional, price):
        c = int(notional / price / self.lot) * self.lot
        return (round(c, 6), 0.0) if c >= self.lot else (0.0, self.lot * price)
msgs = []
st = Store(); st.set("paper_balance", 100.0)
eng = PaperEngine(FakeOKX(), st, msgs.append)
t1 = eng.open(Signal("A/USDT:USDT", "long", 6.0, 100.0, 1.0, 97.0, 106.0, [], {}), {"confidence": 70})
assert t1 and abs(t1["risk_mult"] - 1.0) < 1e-9
open_risk = R.open_risk_usdt(st.open_trades())
assert abs(open_risk - 100 * cfg.RISK_PER_TRADE) < 0.05, open_risk                     # ≈ 3 USDT (gồm phí)
t2 = eng.open(Signal("B/USDT:USDT", "long", 6.0, 50.0, 0.5, 48.5, 53.0, [], {}), {"confidence": 70})
assert t2 is not None
assert R.risk_of(t1["notional"], t1["entry"], t1["sl"]) + R.risk_of(t2["notional"], t2["entry"], t2["sl"]) <= 100 * cfg.MAX_TOTAL_RISK + 1e-6
t3 = eng.open(Signal("C/USDT:USDT", "long", 6.0, 10.0, 0.1, 9.7, 10.6, [], {}), {"confidence": 70})
assert t3 is None, "đã kín ngân sách rủi ro → không mở lệnh thứ 3"
print(f"✓ 2 lệnh: rủi ro mở {R.risk_of(t1['notional'], t1['entry'], t1['sl']) + R.risk_of(t2['notional'], t2['entry'], t2['sl']):.3f} ≤ trần {100 * cfg.MAX_TOTAL_RISK:.2f} USDT, lệnh 3 bị từ chối")

# 5) lot tối thiểu quá to so với rủi ro dự tính → bỏ; vừa phải → nâng lên lot tối thiểu
shutil.rmtree("/tmp/okx-risk-test", ignore_errors=True)
st2 = Store(); st2.set("paper_balance", 10.0)
big = PaperEngine(FakeOKX(lot=1.0), st2, msgs.append)             # 1 hợp đồng = 100 USDT → rủi ro 3 USDT ≫ 0.3 dự tính
assert big.open(sig, {"confidence": 70}) is None
ok = PaperEngine(FakeOKX(lot=0.15), st2, msgs.append)             # 15 USDT → rủi ro ~0.46 < 2× dự tính 0.3
t = ok.open(sig, {"confidence": 70}); assert t and abs(t["notional"] - 15.0) < 1e-6, t
print("✓ lot tối thiểu: quá to → bỏ, vừa phải → dùng lot tối thiểu")
print("TẤT CẢ OK")
