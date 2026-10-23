"""Kiểm tra các bộ lọc ưu tiên winrate (v2.8): cổng BTC, chống râu nến, SL cấu trúc,
trigger động lượng không cộng trùng, điểm dòng tiền, mô tả nến cho AI."""
import os, sys, random
os.environ.setdefault("DATA_DIR", "/tmp/okx_test_strategy")
sys.path.insert(0, os.path.dirname(__file__))
from types import SimpleNamespace as NS
from bot.config import cfg
from bot import strategy as S
from bot.indicators import to_frame, enrich

def row(**k):
    base = dict(adx=30, atr_pct=1.0, vol_ratio=1.2, close=101, open=100, ema21=100.5, atr=1.0, rsi=60, close_pos=0.8)
    base.update(k); return NS(**base)

# 1) cổng BTC
assert S._gates(row(), "long", 1, {"btc_trend_1h": -1}) == "BTC đang xu hướng giảm"
assert S._gates(row(), "long", 1, {"btc_trend_1h": 0, "btc_chg_1h": -0.7}) == "BTC đang xu hướng giảm"
assert S._gates(row(), "long", 1, {"btc_trend_1h": 0, "btc_chg_1h": -0.3}) == ""
assert S._gates(row(close=99, open=100, ema21=99.5, rsi=40, close_pos=0.2), "short", -1, {"btc_trend_1h": 1}) == "BTC đang xu hướng tăng"
assert S._gates(row(close=99, open=100, ema21=99.5, rsi=40, close_pos=0.2), "short", -1, {"btc_chg_1h": 0.8}) == "BTC đang xu hướng tăng"
assert S._gates(row(), "long", 1, None) == ""
print("✓ cổng BTC")

# 2) chống râu nến
W = cfg.WICK_CLOSE_POS
assert "râu trên" in S._gates(row(close_pos=W - 0.05), "long", 1)
assert S._gates(row(close_pos=W + 0.01), "long", 1) == ""
assert "râu dưới" in S._gates(row(close=99, open=100, ema21=99.5, rsi=40, close_pos=1 - W + 0.05), "short", -1)
assert S._gates(row(close=99, open=100, ema21=99.5, rsi=40, close_pos=1 - W - 0.01), "short", -1) == ""
assert "chạy quá xa" in S._gates(row(close=102, ema21=100.5), "long", 1)      # cách EMA21 1.5 ATR > MAX_EXT_ATR
print("✓ chống râu nến")

# 3) SL theo cấu trúc – không bao giờ kéo vào giữa râu
df = NS(low=[95, 99, 99.5, 99.6, 99.7, 99.8], high=[101] * 6)
sl, tp = S._levels(df, "long", 100, 1.0)
assert abs(sl - (95 - 0.15)) < 1e-9, sl          # dưới đáy cấu trúc, không bị gọt về 97.5
df2 = NS(low=[99.2] * 6, high=[101] * 6)
sl2, _ = S._levels(df2, "long", 100, 1.0)
assert abs(sl2 - (100 - cfg.ATR_SL_MULT)) < 1e-9, sl2   # đáy gần → dùng ATR_SL_MULT × ATR
print("✓ SL cấu trúc: đáy xa → SL", round(sl, 2), "(diagnose sẽ bỏ vì > MAX_SL_ATR)")

# 4) trigger động lượng không cộng trùng
r = NS(ema9=3, ema21=2, ema50=1, close=1, ema200=5, adx=10, vol_ratio=1, close_pos=0.5, body_pct=0.3, vwap=9)
h = NS(di_plus=0, di_minus=1, macd_hist=-1, rsi=40)
fr = NS(row=lambda i: r); hf = NS(row=lambda i: h)
s_mom, _ = S._score(fr, hf, "long", ["MACD cắt lên", "EMA9 cắt lên EMA21"], 0, 0)
s_one, _ = S._score(fr, hf, "long", ["MACD cắt lên"], 0, 0)
s_mix, _ = S._score(fr, hf, "long", ["MACD cắt lên", "Pullback EMA21 + nến bật"], 0, 0)
assert s_mom == s_one, (s_mom, s_one)             # MACD + EMA = chỉ 1 trigger
assert s_mix == s_one + 1.5, (s_mix, s_one)       # động lượng + cấu trúc = 2 trigger
print(f"✓ trigger: MACD+EMA = {s_mom} (bằng 1 trigger), MACD+Pullback = {s_mix}")

# 5) điểm dòng tiền
def sig(side, **ctx):
    return S.Signal("X/USDT:USDT", side, 5.3, 100, 1, 98, 104, [], ctx)
a = sig("long", oi_chg_1h=2.0, px_chg_1h=1.0); assert S.flow_adjust(a)[0] == 0.5
assert S.apply_flow(a) == "" and a.score == 5.8          # 5.3 + 0.5 ≥ 5.5 → đạt
b = sig("long", oi_chg_1h=-2.0, px_chg_1h=1.0); assert S.flow_adjust(b)[0] == -1.0
assert S.apply_flow(b).startswith("điểm thấp")
c = sig("short", oi_chg_1h=2.0, px_chg_1h=1.0); assert S.flow_adjust(c)[0] == 0   # giá ngược chiều lệnh
d = sig("long", funding=0.002); assert S.flow_adjust(d)[0] == -1.0
print("✓ điểm dòng tiền OI/funding")

# 6) mô tả nến chuẩn hoá ATR
random.seed(1)
t, p, rows = 0, 100.0, []
for i in range(60):
    o = p; p = p * (1 + random.uniform(-0.01, 0.012)); hi = max(o, p) * 1.003; lo = min(o, p) * 0.997
    rows.append([t, o, hi, lo, p, random.uniform(100, 200)]); t += 900_000
f = enrich(to_frame(rows))
desc = S.describe_candles(f, f.row(-1).atr)
assert len(desc) == 7 and desc[-2].startswith("nến -1 (tín hiệu)") and "ATR" in desc[0] and "tới đỉnh 20 nến" in desc[-1]
print("✓ mô tả nến:", desc[-2])

# 7) luật chặn sẵn & gỡ chặn
from bot.learner import Learner
S.LEARNED.update({"blocks": [], "unblocked": []})
assert "afternoon_session" not in S.active_blocks() and "setup_range" in S.active_blocks()   # mặc định v2.9.2
_saved = cfg.DEFAULT_BLOCKS; cfg.DEFAULT_BLOCKS = ["afternoon_session", "setup_range"]      # người dùng tự bật chặn
assert "afternoon_session" in S.active_blocks()
sg = S.Signal("X/USDT:USDT", "long", 6, 100, 1, 98, 104, [], {"hour": 15})
assert "13h–19h" in S.blocked_by(sg)
sg.context["hour"] = 21; assert S.blocked_by(sg) == ""
Learner._apply(NS(store=NS(set=lambda *a: None, get=lambda *a: {}), _touch=lambda k: None),
               {"kind": "unblock", "key": "afternoon_session", "new": False})
assert "afternoon_session" not in S.active_blocks() and "afternoon_session" in S.LEARNED["unblocked"]
S.LEARNED.update({"blocks": [], "unblocked": []}); cfg.DEFAULT_BLOCKS = _saved
print("✓ luật chặn sẵn (phiên 13h–19h, setup range) + bộ tự học gỡ được")

# 8) setup range: TP riêng → thoát fixed tại dải giữa
from bot.engine import plan_exit
rs = S.Signal("X/USDT:USDT", "long", 5, 100, 1, 99, 101.5, [], {"setup": "range"})
assert plan_exit(rs, 100) == ("fixed", 101.5, None)
assert S.min_rr_for(rs) == cfg.RANGE_MIN_RR and S.min_score_for(rs) == cfg.RANGE_MIN_SCORE
ts_ = S.Signal("X/USDT:USDT", "long", 5, 100, 1, 98, 104, [], {"setup": "trend"})
m, tp, tp1 = plan_exit(ts_, 100)
assert tp1 is None or abs(tp1 - (100 + 2 * __import__("bot.exits", fromlist=["x"]).current_tp1_r())) < 1e-9
print("✓ setup range thoát fixed tại dải giữa; trend dùng TP1 tự học")
print("TẤT CẢ OK")
