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
c = lambda t, h, l, cl, o=100: [t * 1000, o, h, l, cl, 1]     # o: giá mở (v3.3: nến mở vượt SL → khớp ở giá mở)
atr = 2 / 1.5
o, r = exits.simulate("long", 100, 98, atr, "fixed", 1000, [c(1000, 101, 99, 100), c(1300, 104.5, 99.5, 104)], 0.00125)
assert o == "TP" and 1.9 < r < 2.0, (o, r)
assert exits.simulate("long", 100, 98, atr, "fixed", 1000, [c(1000, 101, 97.9, 98)], 0.00125)[0] == "SL"
assert exits.simulate("long", 100, 98, atr, "fixed", 1000, [c(1000, 105, 97, 100)], 0.00125)[0] == "SL"   # cùng nến → SL
# hybrid: chốt 50% ở TP1 (2R = 104), sau đó sóng chạy tới 112 (6R) rồi quay đầu → trailing chốt phần còn lại
cs = [c(1000, 101, 99, 100), c(1300, 104.5, 100, 104), c(1600, 108, 103.5, 108, 104), c(1900, 112, 107, 111.5, 108),
      c(2200, 111.6, 104, 104.5, 111.5)]
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

# 3b) v3.2: không bao giờ chặn cả một phía dù nhóm đó thua rõ
st.set("learn_last", {})
for i in range(40): add("short", 55, "pullback", 6, -1.05, "ai_rejected")
props2 = lr.learn()
assert not any(p["key"] in ("short_side", "long_side") for p in props2), props2
assert "short_side" not in LEARNED["blocks"]
print("✓ v3.2: không chặn cả phía short dù 55 short thua")

# 4) khôi phục mặc định
lr.reset(); assert LEARNED["blocks"] == [] and LEARNED["min_score"] is None and LEARNED["schema"] == Learner.LEARNED_SCHEMA
print("✓ khôi phục mặc định")

# 4b) v3.2: tham số bản cũ (schema 1) được đưa về mặc định khi khởi động, giữ ngưỡng phủ quyết AI
st.set("learned", {"min_score": 5.5, "trigger_w": {"pullback": 1.2}, "blocks": ["short_side", "weak_adx"],
                   "unblocked": [], "exit_mode": "trail", "ai_veto_conf": 55, "tp1_r": None})
st.set("learn_last", {"rule:short_side": time.time(), "exit_mode": time.time(), "ai_veto_conf": time.time()})
LEARNED.clear(); LEARNED.update({"min_score": None, "trigger_w": {}, "blocks": [], "unblocked": [], "exit_mode": None, "ai_veto_conf": None, "tp1_r": None})
lr2 = Learner(st, None, None, notes.append)
assert LEARNED["blocks"] == [] and LEARNED["exit_mode"] is None and LEARNED["min_score"] is None and LEARNED["trigger_w"] == {}
assert LEARNED["ai_veto_conf"] == 55 and LEARNED["schema"] == Learner.LEARNED_SCHEMA
assert "exit_mode" not in st.get("learn_last") and "ai_veto_conf" in st.get("learn_last")
assert st.learn_log(1)[0]["kind"] == "migrate"
lr3 = Learner(st, None, None, notes.append); assert st.learn_log(1)[0]["kind"] == "migrate" and len([r for r in st.learn_log(50) if r["kind"] == "migrate"]) == 1
print("✓ v3.2: tham số bản cũ về mặc định 1 lần, giữ ai_veto_conf")

# 5) chế độ suggest: tạo đề xuất chờ duyệt, áp dụng qua apply_pending
st.set("learn_last", {}); lr.mode = "suggest"
lr.learn()
pend = [r for r in st.learn_log() if r["status"] == "pending"]
assert pend and LEARNED["blocks"] == [], "suggest không được tự áp dụng"
n = lr.apply_pending(); assert n == len(pend) and "short_htf_oversold" in LEARNED["blocks"]
print(f"✓ chế độ chờ duyệt: {len(pend)} đề xuất, áp dụng thủ công OK")
print("Thống kê AI chặn đúng:", lr.last_stats["ai_veto"]["veto_accuracy"], "%")

# 6) v3.3: tín hiệu bóng dùng ĐÚNG giá & phí của lệnh paper – bản cũ vào ở giá đóng nến 15m đã cũ và chỉ trừ 0.15%
# (thiếu trượt giá chiều vào) trong khi paper trừ 0.20% → bộ tự học thấy đẹp hơn thực tế
import math, types
import bot.engine as ENG
from bot import costs
from bot.engine import PaperEngine
LEARNED.update({"exit_mode": None, "tp1_r": None})
CUR = int(time.time() // 60) * 60
T0 = CUR - 3600 + 10                      # vào lệnh 1 giờ trước (giây thứ 10 của phút 0)


class Clock:
    """Đồng hồ giả của engine: lệnh paper được theo dõi như bot chạy thật – một lượt kiểm tra mỗi phút."""
    now = 0.0
    def time(self): return self.now
    def sleep(self, s): pass


CLK = Clock()


def run_paper(okx, sig, t_open, minutes):
    """Mở lệnh paper lúc t_open rồi kiểm tra ở giây 30 của từng phút trong `minutes` (giờ mở cửa của phút, giây)."""
    real, ENG.time = ENG.time, CLK
    try:
        CLK.now = t_open
        eng = PaperEngine(okx, st, notes.append)
        t = eng.open(sig, {"confidence": 70})
        yield t
        for m in minutes:
            CLK.now = m + 30
            eng.monitor()
    finally:
        ENG.time = real


def bar(i, o, h, l, cl):
    return [(CUR - 3600 + 60 * i) * 1000, o, h, l, cl, 1]


class Ex:
    def __init__(self, okx): self.okx = okx
    def fetch_ohlcv(self, s, tf, since=None, limit=None): return [x for x in self.okx.candles if x[0] >= (since or 0) - 300_000]


class Okx:
    def __init__(self, last): self.last, self.candles, self.ex = last, [], Ex(self)
    def price(self, s): return self.last
    def market(self, s): return {"contractSize": 1, "limits": {"amount": {"min": 0.01}}, "precision": {"amount": 0.01}}
    def contracts_for_notional(self, s, n, p):
        k = math.floor(n / p / 0.01 + 1e-9) * 0.01
        return (round(k, 8), 0.0) if k >= 0.01 else (0.0, 0.01 * p)
    def ohlcv_since(self, s, tf, since): return [x for x in self.candles if x[0] >= since * 1000]


def paper_vs_shadow(sym, path):
    """Cùng một tín hiệu (giá đóng nến 1.000, giá last lúc vào 1.002), cùng đường giá: R của lệnh paper và R bóng."""
    sig = Signal(sym, "long", 6.0, 1.000, 0.005, 0.990, 1.015, ["t"], {"setup": "trend", "atr_pct": 0.5})
    okx = Okx(1.002)
    st.set("paper_balance", 10.0)
    run = run_paper(okx, sig, T0, [CUR - 3600 + 60 * m for m in range(1, 61)])
    t = next(run)
    okx.candles = path(t)
    for _ in run:
        pass
    assert sig.ref_ts == T0
    st.log_signal(sig, {"confidence": 70}, True, source="taken")
    tr = [x for x in st.trades(50) if x["id"] == t["id"]][0]
    assert tr["status"] == "closed", tr
    r_paper = tr["pnl"] / (tr["notional0"] * abs(tr["entry"] - tr["sl0"]) / tr["entry"])
    lr.okx = okx
    lr.resolve_pending()
    row = st.db.run("SELECT * FROM signals WHERE symbol=?", (sym,), fetch=True)[0]
    return t, r_paper, row


# 6a) bộ tự học: vào ở giá ghi trong tín hiệu ± trượt giá, trừ phí hai chiều + trượt chiều ra = 0.20% như paper
# (bản cũ: vào đúng giá ghi, chỉ trừ 0.15%)
sg = Signal("FEE/USDT:USDT", "long", 6.0, 1.002, 0.005, 0.997, 1.02, ["t"], {"setup": "trend", "atr_pct": 0.5})
st.log_signal(sg, None, False, source="below_score")
st.db.run("UPDATE signals SET ts=? WHERE symbol=?", (T0, "FEE/USDT:USDT"))
okx = Okx(1.002)
okx.candles = [bar(1, 1.002, 1.003, 1.0, 1.001), bar(2, 1.001, 1.002, 0.996, 0.997)] + [bar(i, 0.99, 0.991, 0.989, 0.99) for i in range(3, 61)]
lr.okx = okx
lr.resolve_pending()
row = st.db.run("SELECT * FROM signals WHERE symbol=?", ("FEE/USDT:USDT",), fetch=True)[0]
E = 1.002 * (1 + cfg.SLIPPAGE)
want = -1 - (2 * cfg.TAKER_FEE + cfg.SLIPPAGE) * E / (E - 0.997)
assert abs(row["r_hybrid"] - want) < 1e-3, ("bộ tự học phải dùng cùng giá khớp & phí với paper", row["r_hybrid"], want)
print(f"✓ v3.3: bộ tự học trừ đủ phí + trượt giá hai chiều ({row['r_hybrid']:+.3f}R khi chạm SL cách 0.5%)")

t, rp, row = paper_vs_shadow("SLX/USDT:USDT", lambda t: [bar(1, 1.002, 1.003, 1.0, 1.001), bar(2, 1.001, 1.002, 0.989, 0.991)] +
                             [bar(i, 0.991, 0.992, 0.99, 0.991) for i in range(3, 61)])
f = json.loads(row["features"])
assert row["entry"] == 1.002 and f["signal_close"] == 1.0 and f["entry_ref"] == "last", (row["entry"], f)
risk = t["entry"] - 0.990
assert abs(row["r_hybrid"] - (-1 - costs.after_fill_cost() * t["entry"] / risk)) < 1e-3, row["r_hybrid"]
assert abs(row["r_hybrid"] - rp) < 0.01, ("R bóng phải bằng R lệnh paper", row["r_hybrid"], rp)
t, rp2, row2 = paper_vs_shadow("TPX/USDT:USDT", lambda t: [bar(1, 1.002, 1.003, 1.0, 1.001),
                               bar(2, 1.001, t["tp1"] + 0.001, 1.0, t["tp1"]), bar(3, t["tp1"], t["tp1"], 1.005, 1.006),
                               bar(4, 1.006, 1.006, 1.0, 1.001)] + [bar(i, 1.001, 1.002, 1.0, 1.001) for i in range(5, 50)] +
                              [bar(i, 0.985, 0.986, 0.98, 0.985) for i in range(50, 61)])      # cách thoát khác cũng kết thúc
assert row2["outcome"] == "TRAIL" and abs(row2["r_hybrid"] - rp2) < 0.01, ("TP1 rồi về hoà vốn: R bóng = R paper", row2, rp2)
print(f"✓ v3.3: R bóng = R lệnh paper trên cùng đường giá (SL {row['r_hybrid']:+.3f} / {rp:+.3f}, "
      f"TP1+hoà vốn {row2['r_hybrid']:+.3f} / {rp2:+.3f}); vào ở giá last mới, phí {costs.round_trip_cost()*100:.2f}%")

# tín hiệu bóng (suýt đạt, bị chặn…) cũng vào ở giá last lúc ghi, không ở giá đóng nến 15m
from bot.main import Bot
sg = Signal("ZZ/USDT:USDT", "short", 4.5, 2.0, 0.01, 2.03, 1.95, ["t"], {"setup": "trend"})
Bot._shadow(types.SimpleNamespace(shadow_seen={}, okx=Okx(1.99), store=st), sg, "below_score")
row = st.db.run("SELECT * FROM signals WHERE symbol=?", ("ZZ/USDT:USDT",), fetch=True)[0]
assert row["entry"] == 1.99 and json.loads(row["features"])["signal_close"] == 2.0, row["entry"]
print("✓ v3.3: tín hiệu bóng ghi giá last lúc quyết định, giữ giá đóng nến tín hiệu trong features")

# 6d) review 11: lúc vào nằm giữa khung 5m. Bóng bỏ phút chứa lúc vào như lệnh paper: râu TRƯỚC lúc vào không tính (bản cũ
# tính – SL "chạm" từ trước khi vào), phần SAU lúc vào trong cùng khung 5m vẫn tính (bản cũ bỏ cả khung khi OKX chỉ trả nến
# mở từ lúc vào).
B = int((CUR - 7200) // 300) * 300
T1 = B + 210                                   # 3 phút 30 giây vào khung 5m


def m1(k, o, h, l, c):
    return [(B + 60 * k) * 1000, o, h, l, c, 1]


def agg5(rows):
    out = {}
    for r in rows:
        k = r[0] // 300000 * 300000
        if k not in out:
            out[k] = [k, r[1], r[2], r[3], r[4], r[5]]
        else:
            b = out[k]
            b[2], b[3], b[4], b[5] = max(b[2], r[2]), min(b[3], r[3]), r[4], b[5] + r[5]
    return [out[k] for k in sorted(out)]


class Ex2:
    """OKX giả theo khung (1m; 5m gộp từ 1m). strict: chỉ trả nến mở từ `since` như OKX; không strict: gồm cả nến 5m chứa
    `since` (tín hiệu khác cùng mã cũ hơn kéo `since` về sớm hơn)."""
    def __init__(self, rows, strict): self.rows, self.strict = rows, strict
    def fetch_ohlcv(self, s, tf, since=None, limit=None):
        rows = self.rows if tf == "1m" else agg5(self.rows)
        lo = since or 0
        if not self.strict:
            lo = lo // 300000 * 300000
        return [r for r in rows if r[0] >= lo][:limit or None]


def paper_shadow_1m(sym, rows, strict):
    sig = Signal(sym, "long", 6.0, 1.000, 0.005, 0.990, 1.015, ["t"], {"setup": "trend", "atr_pct": 0.5})
    okx = Okx(1.002)
    okx.candles, okx.ex = rows, Ex2(rows, strict)
    st.set("paper_balance", 10.0)
    run = run_paper(okx, sig, T1, [B + 60 * k for k in range(4, 60)])
    t = next(run)
    for _ in run:
        pass
    st.log_signal(sig, {"confidence": 70}, True, source="taken")
    lr.okx = okx
    lr.resolve_pending()
    tr = [x for x in st.trades(50) if x["id"] == t["id"]][0]
    rp = tr["pnl"] / (tr["notional0"] * abs(tr["entry"] - tr["sl0"]) / tr["entry"]) if tr["status"] == "closed" else None
    return t, tr, rp, st.db.run("SELECT * FROM signals WHERE symbol=?", (sym,), fetch=True)[0]


# râu xuống 0.985 (dưới SL 0.99) ở phút 1 – TRƯỚC lúc vào; sau đó lên TP1 rồi về hoà vốn, cuối cùng thủng SL (mọi cách thoát xong)
up = [1.003 + 0.0026 * i for i in range(6)]                       # phút 4..9: 1.003 → 1.016
dn = [1.0155 - 0.00075 * i for i in range(30)]                    # phút 10..39: 1.0155 → 0.9938
rows = [m1(0, 1.002, 1.0025, 1.0015, 1.002), m1(1, 1.002, 1.0025, 0.985, 1.002), m1(2, 1.002, 1.0025, 1.0015, 1.002),
        m1(3, 1.002, 1.0025, 1.0015, 1.002)]
prev = 1.002
for k, c in enumerate(up + dn, start=4):
    rows.append(m1(k, prev, max(prev, c), min(prev, c), c)); prev = c
rows += [m1(k, 0.986, 0.987, 0.985, 0.986) for k in range(40, 60)]
t, tr, rp, row = paper_shadow_1m("PRE/USDT:USDT", rows, strict=False)
assert tr["status"] == "closed" and tr["tp1_done"] == 1, tr
assert row["outcome"] == "TRAIL" and abs(row["r_hybrid"] - rp) < 0.01, ("râu trước lúc vào không được tính", row["outcome"],
                                                                          row["r_hybrid"], rp)
# chạm SL ở phút 4 – SAU lúc vào, cùng khung 5m; OKX chỉ trả nến 5m mở từ lúc vào → phải lấy nến 1m của khung đó
rows = [m1(k, 1.002, 1.0025, 1.0015, 1.002) for k in range(0, 4)] + [m1(4, 1.002, 1.0025, 0.989, 0.995)] + \
       [m1(k, 0.995, 0.996, 0.994, 0.995) for k in range(5, 60)]
t, tr, rp, row = paper_shadow_1m("POST/USDT:USDT", rows, strict=True)
assert tr["status"] == "closed" and tr["reason_close"] == "Chạm SL", tr
assert row["outcome"] == "SL" and abs(row["r_hybrid"] - rp) < 0.01, ("SL sau lúc vào trong khung 5m bị bỏ", row["outcome"],
                                                                   row["r_hybrid"], rp)
print(f"✓ review 11: bóng bỏ phút chứa lúc vào như paper – râu trước lúc vào không tính ({row['r_hybrid']:+.3f} = paper), "
      "SL sau lúc vào trong cùng khung 5m vẫn tính")

# 7) review 6: kết quả bóng của bản trước 3.3 (vào ở giá đóng nến cũ, phí 0.15%) không trộn vào thống kê và đề xuất
lr.reset(); lr.mode = "auto"
st.db.run("DELETE FROM signals")
for i in range(20):
    add("short", 25, "ema_cross", 6, -1.05, "ai_rejected")       # nhóm "short khi RSI 1h < 32" thua rõ
old_f = json.dumps({"htf_rsi": 25, "bb_pos": 0.5, "adx": 30, "change24h": 0, "hour": 15, "triggers": ["ema_cross"]})
st.db.run("UPDATE signals SET features=?", (old_f,))            # bản cũ: features chưa có entry_ref
props = lr.learn()
assert not props and lr.last_stats["legacy_excluded"] == 20 and lr.last_stats["total"]["n"] == 0, \
    ("tín hiệu bản cũ không được dùng để học", props, lr.last_stats.get("legacy_excluded"))
st.db.run("UPDATE signals SET features=?", (json.dumps({**json.loads(old_f), "entry_ref": "last"}),))
props = lr.learn()
assert any(p["kind"] == "block" and p["key"] == "short_htf_oversold" for p in props), props
print("✓ review 6: tín hiệu bóng bản trước 3.3 không vào thống kê / đề xuất của bộ tự học (chờ đủ mẫu mới)")
