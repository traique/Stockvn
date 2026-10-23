"""Kiểm tra bộ tự học với dữ liệu giả: mô phỏng kết quả bóng, đề xuất chặn luật, chỉnh trọng số, ngưỡng điểm."""
import os, shutil, json, time, random
os.environ.update(MODE="paper", DATA_DIR="/tmp/okx-learn-test", AI_ENABLED="false", LEARN_MODE="auto", DATABASE_URL="", LEARN_MIN_SAMPLES="12")   # dữ liệu giả 14–15 mẫu/nhóm
shutil.rmtree("/tmp/okx-learn-test", ignore_errors=True)
from bot.storage import Store
from bot.learner import Learner
from bot.strategy import Signal, LEARNED, diagnose
from bot import strategy

# 1) mô phỏng kết quả từ nến
from bot import exits
from bot.config import cfg
c = lambda t, h, l, cl: [t * 1000, 100, h, l, cl, 1]
atr = 2 / 1.5
o, r = exits.simulate("long", 100, 98, atr, "fixed", 1000, [c(1000, 101, 99, 100), c(1300, 104.5, 99.5, 104)], 0.00125)
assert o == "TP" and 1.9 < r < 2.0, (o, r)
assert exits.simulate("long", 100, 98, atr, "fixed", 1000, [c(1000, 101, 97.9, 98)], 0.00125)[0] == "SL"
assert exits.simulate("long", 100, 98, atr, "fixed", 1000, [c(1000, 105, 97, 100)], 0.00125)[0] == "SL"   # cùng nến → SL
# hybrid: chốt 50% ở TP1 (2R = 104), sau đó sóng chạy tới 112 (6R) rồi quay đầu → trailing chốt phần còn lại
cs = [c(1000, 101, 99, 100), c(1300, 104.5, 100, 104), c(1600, 108, 103.5, 108), c(1900, 112, 107, 111.5), c(2200, 111.6, 104, 104.5)]
o, r = exits.simulate("long", 100, 98, atr, "hybrid", 1000, cs, 0.00125, tp1_r=2.0)
assert o == "TRAIL" and r > 3.0, (o, r)
o1, r1 = exits.simulate("long", 100, 98, atr, "hybrid", 1000, cs, 0.00125, tp1_r=1.0)
assert o1 == "TRAIL" and 2.5 < r1 < r, (o1, r1)        # TP1 1R chốt sớm hơn → ít hơn TP1 2R trên sóng dài
of, rf = exits.simulate("long", 100, 98, atr, "fixed", 1000, cs, 0.00125)
assert rf < r, "ăn sóng phải hơn TP cố định trên sóng dài"
print(f"✓ mô phỏng đúng: sóng 6R → TP cố định {rf:+.2f}R, ăn sóng {r:+.2f}R")

# 2) dữ liệu giả: short khi RSI 1h < 32 luôn thua; pullback thắng; breakout thua; suýt đạt điểm 4.5 lại thắng
st = Store(); notes = []
lr = Learner(st, None, None, notes.append)
random.seed(1)
def add(side, htf_rsi, trig, score, R, source):
    sig = Signal("X/USDT:USDT", side, score, 100, 1, 98 if side == "long" else 102, 104 if side == "long" else 96, [], 
                 {"htf_rsi": htf_rsi, "bb_pos": 0.5, "adx": 30, "change24h": 0, "hour": 15}, triggers=[trig])
    st.log_signal(sig, None, source == "taken", source=source)
    sid = st.db.run("SELECT sid FROM signals ORDER BY rowid DESC LIMIT 1", fetch=True)[0]["sid"]
    st.set_outcome(sid, "TP" if R > 0 else "SL", R)
for i in range(15): add("short", 25, "ema_cross", 6, -1.05, "ai_rejected")
for i in range(15): add("long", 55, "pullback", 6, random.choice([1.9, 1.9, -1.05]), "taken")
for i in range(14): add("long", 55, "breakout", 5.5, ([-1.05] * 12 + [1.9, 1.9])[i], "not_sent")   # breakout thua rõ (12/14) → đủ ý nghĩa thống kê
for i in range(14): add("long", 55, "pullback", 4.5, random.choice([1.9, 1.9, -1.05]), "below_score")
props = lr.learn()
for p in props: print(" →", p["reason"])
assert "short_htf_oversold" in LEARNED["blocks"], LEARNED
assert LEARNED["trigger_w"].get("pullback", 1) > 1 and LEARNED["trigger_w"].get("breakout", 1) < 1
assert LEARNED["min_score"] == cfg.MIN_SCORE - 0.5, (LEARNED["min_score"], cfg.MIN_SCORE)   # mặc định 5.0 → 4.5
assert LEARNED.get("ai_veto_conf") is not None   # AI chặn đúng → hạ ngưỡng phủ quyết
print("✓ học đúng: chặn short quá bán, tăng trọng số pullback, giảm breakout, hạ ngưỡng điểm")

# 3) giới hạn: chạy lại ngay → không đổi tiếp (cooldown 24h)
assert lr.learn() == [], "phải có cooldown"
print("✓ cooldown 24h hoạt động")

# 4) khôi phục mặc định
lr.reset(); assert LEARNED["blocks"] == [] and LEARNED["min_score"] is None
print("✓ khôi phục mặc định")

# 5) chế độ suggest: tạo đề xuất chờ duyệt, áp dụng qua apply_pending
st.set("learn_last", {}); lr.mode = "suggest"
lr.learn()
pend = [r for r in st.learn_log() if r["status"] == "pending"]
assert pend and LEARNED["blocks"] == [], "suggest không được tự áp dụng"
n = lr.apply_pending(); assert n == len(pend) and "short_htf_oversold" in LEARNED["blocks"]
print(f"✓ chế độ chờ duyệt: {len(pend)} đề xuất, áp dụng thủ công OK")
print("Thống kê AI chặn đúng:", lr.last_stats["ai_veto"]["veto_accuracy"], "%")
