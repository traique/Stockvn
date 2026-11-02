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
# v3.3: bộ tự học cần mẫu trải trên ≥ LEARN_MIN_DAYS ngày và t gom cụm theo ngày → dữ liệu giả rải đều 10 ngày gần nhất, lúc
# 21h (giờ của bot) – phiên không bị luật chặn sẵn nào chặn (tín hiệu bị chặn không được tính cho trọng số trigger / ngưỡng)
DAY0 = time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d"))
_k = [0]
def add(side, htf_rsi, trig, score, R, source, day=None, hour=21):
    sig = Signal("X/USDT:USDT", side, score, 100, 1, 98 if side == "long" else 102, 104 if side == "long" else 96, [],
                 {"htf_rsi": htf_rsi, "bb_pos": 0.5, "adx": 30, "change24h": 0, "hour": hour}, triggers=[trig])
    k = _k[0]; _k[0] += 1
    sig.ref_ts = DAY0 - (1 + (k % 10 if day is None else day)) * 86400 + hour * 3600 + k % 3000
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

# 4b) v3.2: tham số bản cũ (schema 1) được đưa về mặc định khi khởi động, giữ ngưỡng phủ quyết AI.
# CHỈ phần v3.2: cờ learn_reset_3_3 đặt sẵn (như DB đã chạy 3.3) để lần đưa về mặc định v3.3 không chạy – đường nâng cấp
# thật từ schema 1 lên 3.3 (cả hai bước) ở 4c.
st.set("learn_reset_3_3", time.time())
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

# 4c) review B 10: nâng cấp thật từ schema 1 (chưa có cờ v3.3), chạy ảo + LEARN_MODE=auto: bước v3.2 rồi bước v3.3 – mọi
# tham số về mặc định (cả ai_veto_conf), hai dòng migrate
st.db.run("DELETE FROM kv WHERE k IN ('learn_reset_3_3', 'learn_notice')"); st.db.run("DELETE FROM learn_log")
st.set("learned", {"min_score": 5.5, "trigger_w": {"pullback": 1.2}, "blocks": ["short_side", "weak_adx"],
                   "unblocked": [], "exit_mode": "trail", "ai_veto_conf": 55, "tp1_r": None})
LEARNED.clear(); LEARNED.update(st.get("learned"))
Learner(st, None, None, notes.append)
assert LEARNED["ai_veto_conf"] is None and LEARNED["blocks"] == [] and LEARNED["trigger_w"] == {} and LEARNED["schema"] == 3, LEARNED
assert sorted(r["k"] for r in st.learn_log(50) if r["kind"] == "migrate") == ["v3.2", "v3.3"], st.learn_log(50)
print("✓ review B 10: nâng cấp thật schema 1 → 3.3: hai bước, mọi tham số (cả ai_veto_conf) về mặc định")

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
    def __init__(self, last): self.last, self.candles, self.ex, self.secs = last, [], Ex(self), []
    def seconds(self, s, start, end): return [x for x in self.secs if start * 1000 <= x[0] < end * 1000]
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

# (giá nằm trên SL của biến thể SL 2.5 ATR: biến thể còn chạy, không được giữ kết quả chính – review B 5)
t, rp, row = paper_vs_shadow("SLX/USDT:USDT", lambda t: [bar(1, 1.002, 1.003, 1.0, 1.001), bar(2, 1.001, 1.002, 0.989, 0.991)] +
                             [bar(i, 0.991, 0.992, 0.99, 0.991) for i in range(3, 61)])
f = json.loads(row["features"])
assert row["entry"] == 1.002 and f["signal_close"] == 1.0 and f["entry_ref"] == "last", (row["entry"], f)
assert row["outcome"] == "SL" and row["r_sl25_trail"] is None and row["alt_open"] == 1, dict(row)   # biến thể còn chạy
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

# ======================================================================= v3.3 phần B: thống kê tự học, đưa về mặc định, biến thể A/B
# Mỗi phần chạy riêng (lỗi một phần không che phần sau) – phần nào đỏ trên code cũ thì in ✗ rồi cả file thoát mã lỗi.
import traceback
from bot import learner as LM
FAILED = []


def section(name):
    def run(fn):
        try:
            fn()
            print("✓", name)
        except Exception:
            FAILED.append(name)
            print("✗", name, "\n" + traceback.format_exc(limit=3))
        return fn
    return run


def fresh():
    lr.reset(); lr.mode = "auto"; lr.okx = None
    st.db.run("DELETE FROM signals"); st.set("learn_last", {})


@section("bug 9: t gom cụm theo ngày – 80 tín hiệu dồn theo ngày (t thường -2.2, t theo ngày -0.7) không đổi tham số")
def _():
    fresh()
    for d in range(8):                       # 6 ngày thua cả, 2 ngày thắng cả: chỉ 8 "lần quan sát" độc lập
        for _i in range(10):
            add("short", 25, "ema_cross", 6, -1.05 if d < 6 else 1.9, "not_sent", day=d)
    props = lr.learn()
    assert not [p for p in props if p["key"] in ("short_htf_oversold", "ema_cross")], props
    v = lr.last_stats["by_rule"]["short_htf_oversold"]
    vals = [-1.05] * 60 + [1.9] * 20
    _, t_iid = LM._tstat(vals)
    assert t_iid <= -2.0 and v["days"] == 8 and abs(v["t"]) < 1.0, (t_iid, v)


@section("bug 9: cần mẫu trải trên ≥ LEARN_MIN_DAYS ngày – 40 tín hiệu thua của 2 ngày không đủ để chặn")
def _():
    fresh()
    for k in range(40):
        add("short", 25, "ema_cross", 6, -1.05, "not_sent", day=k % 2)
    props = lr.learn()
    assert not [p for p in props if p["key"] == "short_htf_oversold"], props
    v = lr.last_stats["by_rule"]["short_htf_oversold"]
    assert v["n"] == 40 and v["days"] == 2 and cfg.LEARN_MIN_DAYS > 2, v
    for k in range(40):                     # cùng dữ liệu trải 10 ngày → đủ điều kiện, chặn
        add("short", 25, "ema_cross", 6, -1.05, "not_sent", day=2 + k % 8)
    assert any(p["kind"] == "block" and p["key"] == "short_htf_oversold" for p in lr.learn()), lr.last_stats["by_rule"]


@section("bug 9: trọng số trigger & ngưỡng điểm không học từ tín hiệu luật chặn sẵn đã chặn (pullback 0.82: 22/32 bị chặn)")
def _():
    fresh()
    for k in range(30):                      # phiên 13h–19h (chặn sẵn): pullback thua cả
        add("long", 55, "pullback", 6, -1.05, "blocked", hour=15)
    for k in range(15):                      # tín hiệu vào lệnh được: pullback hoà vốn
        add("long", 55, "pullback", 6, [1.9, -1.05, -1.05][k % 3], "not_sent")
    for k in range(20):                      # suýt đạt nhưng bị chặn sẵn: thắng – hạ ngưỡng cũng không vào được
        add("long", 55, "macd_cross", 4.5, 1.9, "below_score", hour=15)
    props = lr.learn()
    s_ = lr.last_stats
    assert s_["by_trigger"]["pullback"]["n"] == 15 and s_["trigger_blocked_excluded"] == 30, s_["by_trigger"]
    assert not [p for p in props if p["kind"] in ("trigger_w", "min_score")], props
    assert LEARNED["trigger_w"] == {} and LEARNED["min_score"] is None, LEARNED


@section("bug 9: một lần đưa tham số học bằng thống kê cũ về mặc định (pullback 0.82) – log, lịch sử, thông báo qua leader")
def _():
    fresh()
    st.db.run("DELETE FROM kv WHERE k IN ('learn_reset_3_3', 'learn_notice')"); st.db.run("DELETE FROM learn_log")
    st.set("learned", {"min_score": None, "trigger_w": {"pullback": 0.82}, "blocks": [], "unblocked": [], "exit_mode": None,
                       "ai_veto_conf": None, "tp1_r": None, "schema": 2})
    st.set("learn_last", {"trig:pullback": time.time()})
    st.add_learn("trigger_w", "vwap_bounce", 1.0, 0.9, "đề xuất cũ", 30, -0.3, "pending")
    LEARNED.clear(); LEARNED.update(st.get("learned"))
    notes.clear()
    lm = Learner(st, None, None, notes.append)
    assert LEARNED["trigger_w"] == {} and st.get("learned")["trigger_w"] == {} and st.get("learned")["schema"] == 3, LEARNED
    assert st.get("learn_last") == {} and not [r for r in st.learn_log(50) if r["status"] == "pending"]
    mig = [r for r in st.learn_log(50) if r["kind"] == "migrate" and r["k"] == "v3.3"]
    assert len(mig) == 1 and "0.82" in mig[0]["reason"], mig
    assert not notes, notes                  # lúc khởi tạo bot chưa là leader → chưa gửi, chờ tick của leader
    lm.tick()
    assert len([n for n in notes if n.startswith("🧬 TỰ HỌC") and "0.82" in n]) == 1, notes
    lm2 = Learner(st, None, None, notes.append); lm2.tick()
    assert len([n for n in notes if "0.82" in n]) == 1 and \
        len([r for r in st.learn_log(50) if r["kind"] == "migrate" and r["k"] == "v3.3"]) == 1, "chỉ chạy một lần"


@section("biến thể A/B: ghi R của 'SL 2.5 ATR + bám sóng' trên mọi tín hiệu bóng xu hướng, so sánh trên trang tự học")
def _():
    fresh()
    E = 1.0 * (1 + cfg.SLIPPAGE)
    cost = costs.after_fill_cost()
    up = [1.0 + 0.003 * i for i in range(11)]          # 1.000 → 1.030
    dip = [bar(1, 1.0, 1.001, 0.9995, 1.0), bar(2, 1.0, 1.0005, 0.9885, 0.995)]  # thủng SL 0.99, chưa tới 0.9875
    rally = [bar(3 + i, up[i - 1] if i else 0.995, up[i], min(up[i - 1] if i else 0.995, up[i]), up[i]) for i in range(11)]
    hold = [bar(i, 1.028, 1.03, 1.026, 1.028) for i in range(14, 21)]
    dn = [1.028 - 0.003 * i for i in range(21)]        # rơi dần (không nhảy cóc qua SL/trailing): 1.028 → 0.968
    drop = [bar(21 + i, dn[i], dn[i], dn[i + 1], dn[i + 1]) for i in range(20)] + \
           [bar(i, 0.968, 0.969, 0.966, 0.968) for i in range(41, 61)]
    path = dip + rally + hold + drop
    dive = [bar(1, 1.0, 1.001, 0.9995, 1.0), bar(2, 1.0, 1.0, 0.97, 0.975)] + \
           [bar(i, 0.975, 0.976, 0.97, 0.975) for i in range(3, 61)]      # nến 2 mở trên SL rồi thủng cả hai SL

    def shadow(sym, sl, setup="trend", tp=1.015):
        g = Signal(sym, "long", 6.0, 1.000, 0.005, sl, tp, ["t"], {"setup": setup, "atr_pct": 0.5})
        g.ref_price, g.ref_ts = 1.000, T0
        st.log_signal(g, None, False, source="not_sent")

    shadow("AW/USDT:USDT", 0.990)                     # SL 2 ATR: biến thể SL 1.0 - 2.5 × 0.005 = 0.9875
    shadow("AR/USDT:USDT", 0.990, setup="range", tp=1.01)
    for sym, p in (("AW", path), ("AR", path)):
        o = Okx(1.0); o.candles = p; lr.okx = o; lr.resolve_pending()
    shadow("AD/USDT:USDT", 0.990)                     # thủng sâu: cả hai SL
    shadow("AS/USDT:USDT", 0.985)                     # SL cấu trúc 3 ATR xa hơn 2.5 ATR → biến thể giữ SL cấu trúc
    o = Okx(1.0); o.candles = dive; lr.okx = o; lr.resolve_pending()
    R = {r["symbol"].split("/")[0]: r for r in st.db.run("SELECT * FROM signals", fetch=True)}
    assert R["AW"]["r_trail"] < -1 and R["AW"]["r_sl25_trail"] > 0.5, (R["AW"]["r_trail"], R["AW"]["r_sl25_trail"])
    assert R["AR"]["outcome"] != "PENDING" and R["AR"]["r_sl25_trail"] is None, dict(R["AR"])
    want = -1 - cost * E / (E - 0.9875)
    assert abs(R["AD"]["r_sl25_trail"] - want) < 1e-3, (R["AD"]["r_sl25_trail"], want)
    assert abs(R["AS"]["r_sl25_trail"] - R["AS"]["r_trail"]) < 1e-9, (R["AS"]["r_sl25_trail"], R["AS"]["r_trail"])
    s_ = lr.compute_stats()[0]
    a = s_["exit_compare"]["alt"]
    cur = s_["exit_compare"]["current"]
    trend = [R[k] for k in ("AW", "AD", "AS")]
    assert a["n"] == 3 and abs(a["exp_r"] - sum(r["r_sl25_trail"] for r in trend) / 3) < 1e-3, a
    assert abs(a["diff"] - sum(r["r_sl25_trail"] - r[f"r_{cur}"] for r in trend) / 3) < 1e-3, a
    from bot.dashboard import APP_JS
    assert "altRow(ec.alt)" in APP_JS and "altNote(" in APP_JS and "max_positions:" in APP_JS and "slot_taken:" in APP_JS


@section("review B 5: biến thể A/B còn chạy không giữ kết quả chính; ghi R biến thể sau (30 phút/lần) hoặc hết giờ")
def _():
    fresh()
    E = 1.0 * (1 + cfg.SLIPPAGE)
    cost = costs.after_fill_cost()
    calls = []

    class CountEx(Ex):
        def fetch_ohlcv(self, s, tf, since=None, limit=None):
            calls.append((s, tf)); return super().fetch_ohlcv(s, tf, since, limit)

    def shadow(sym, ts):
        g = Signal(sym, "long", 6.0, 1.000, 0.005, 0.990, 1.015, ["t"], {"setup": "trend", "atr_pct": 0.5})
        g.ref_price, g.ref_ts = 1.000, ts
        st.log_signal(g, None, False, source="not_sent")

    def get(sym):
        return st.db.run("SELECT * FROM signals WHERE symbol=?", (sym,), fetch=True)[0]
    # SL 0.990 thủng ở nến 2 (mọi cách thoát đang có kết thúc), giá nằm 0.991 – trên SL biến thể 0.9875 → biến thể còn chạy
    shadow("ALT1/USDT:USDT", T0)
    o = Okx(1.0); o.ex = CountEx(o)
    o.candles = [bar(1, 1.0, 1.001, 0.9995, 1.0), bar(2, 1.0, 1.0005, 0.989, 0.991)] + \
                [bar(i, 0.991, 0.992, 0.99, 0.991) for i in range(3, 31)]
    lr.okx, lr.last_alt = o, 0
    lr.resolve_pending()
    r = get("ALT1/USDT:USDT")
    assert r["outcome"] == "SL" and r["r_hybrid"] is not None and r["r_sl25_trail"] is None and r["alt_open"] == 1, dict(r)
    assert st.pending_signals(50) == [] and len(st.alt_pending_signals()) == 1
    # trong 30 phút: mã không còn tín hiệu chờ kết quả chính → không đọc lại 300 nến 5m chỉ để xét biến thể
    o.candles += [bar(i, 0.991, 0.992, 0.98, 0.985) for i in range(31, 41)]       # giá thủng SL biến thể 0.9875
    n0 = len(calls); lr.resolve_pending()
    assert len(calls) == n0 and get("ALT1/USDT:USDT")["alt_open"] == 1, calls[n0:]
    lr.last_alt = time.time() - lr.ALT_RECHECK_S
    lr.resolve_pending()
    r = get("ALT1/USDT:USDT")
    want = -1 - cost * E / (E - 0.9875)
    assert r["alt_open"] is None and abs(r["r_sl25_trail"] - want) < 1e-3 and r["outcome"] == "SL", (dict(r), want)
    # biến thể không kết thúc sau RUNNER_MAX_HOLD_HOURS + 1h → TIMEOUT theo giá cuối, kết quả chính không chờ
    T26 = CUR - int((cfg.RUNNER_MAX_HOLD_HOURS + 2) * 3600)
    shadow("ALT2/USDT:USDT", T26 + 10)
    o2 = Okx(1.0)
    o2.candles = [[(T26 + 60 * i) * 1000, 1.0, 1.001, 0.9995, 1.0, 1] for i in range(1, 2)] + \
                 [[(T26 + 60 * 2) * 1000, 1.0, 1.0005, 0.989, 0.991, 1]] + \
                 [[(T26 + 60 * i) * 1000, 0.991, 0.992, 0.99, 0.991, 1] for i in range(3, 61)]
    lr.okx = o2
    lr.resolve_pending()
    r = get("ALT2/USDT:USDT")
    want = (0.991 - E) / (E - 0.9875) - cost * E / (E - 0.9875)
    assert r["outcome"] == "SL" and r["alt_open"] is None and abs(r["r_sl25_trail"] - want) < 1e-3, (dict(r), want)


@section("review B 3: chạy thật / LEARN_MODE suggest, off – v3.3 không tự đổi tham số tự học, ghi đề xuất Khôi phục chờ duyệt")
def _():
    user = {"min_score": 5.5, "trigger_w": {"pullback": 0.82}, "blocks": ["weak_adx"], "unblocked": ["afternoon_session"],
            "exit_mode": None, "ai_veto_conf": 55, "tp1_r": 1.3, "schema": 3}
    for mode, lmode in (("live", "auto"), ("live", ""), ("paper", "suggest"), ("paper", "off")):
        fresh()
        st.db.run("DELETE FROM kv WHERE k IN ('learn_reset_3_3', 'learn_notice')"); st.db.run("DELETE FROM learn_log")
        st.set("learned", user); st.set("learn_last", {"rule:weak_adx": 123.0})
        st.add_learn("min_score", "min_score", 5.0, 5.5, "đề xuất cũ", 30, 0.2, "pending")
        LEARNED.clear(); LEARNED.update(st.get("learned"))
        notes.clear()
        m0, l0 = cfg.MODE, cfg.LEARN_MODE
        cfg.MODE, cfg.LEARN_MODE = mode, lmode
        try:
            lm = Learner(st, None, None, notes.append)
        finally:
            cfg.MODE, cfg.LEARN_MODE = m0, l0
        tag = f"{mode}/{lmode or 'mặc định'}"
        assert {k: LEARNED.get(k) for k in user} == user and st.get("learned") == user, (tag, dict(LEARNED))
        assert st.get("learn_last") == {"rule:weak_adx": 123.0}, tag
        log_ = st.learn_log(50)
        pend = [r for r in log_ if r["status"] == "pending"]
        assert len(pend) == 1 and pend[0]["kind"] == "reset" and pend[0]["k"] == "v3.3" and "0.82" in pend[0]["reason"], (tag, log_)
        assert [r["status"] for r in log_ if r["kind"] == "min_score"] == ["rejected"], (tag, log_)
        lm.tick()
        assert len([n for n in notes if "chờ duyệt" in n and "0.82" in n]) == 1, (tag, notes)
        Learner(st, None, None, notes.append)                       # chỉ một lần
        assert len([r for r in st.learn_log(50) if r["kind"] == "reset" and r["k"] == "v3.3"]) == 1, tag
        assert lm.apply_pending() == 1                              # người dùng duyệt → về mặc định
        assert LEARNED["trigger_w"] == {} and LEARNED["blocks"] == [] and LEARNED["unblocked"] == [] and \
            LEARNED["ai_veto_conf"] is None and LEARNED["tp1_r"] is None and LEARNED["min_score"] is None, (tag, dict(LEARNED))
        assert st.get("learned")["trigger_w"] == {} and st.get("learn_last") == {}, tag


@section("kiểm tra cuối: log khởi động 'Tự học đã đổi so với mặc định' chỉ liệt kê tham số học được, không có 'schema'")
def _():
    from bot import main as M
    fresh_l = {"min_score": None, "trigger_w": {}, "blocks": [], "unblocked": [], "exit_mode": None, "ai_veto_conf": None,
               "tp1_r": None, "schema": 3}
    assert M.learned_changes(fresh_l) == "chưa có", M.learned_changes(fresh_l)     # bản cũ: "schema=3" trên DB mới
    got = M.learned_changes(dict(fresh_l, trigger_w={"pullback": 0.82}, blocks=["weak_adx"]))
    assert "pullback" in got and "weak_adx" in got and "schema" not in got, got


@section("kiểm tra cuối: khởi động lại giữa nến không ghi bóng lần hai cùng tín hiệu, cùng nguồn (CRV short bị chặn 2 dòng)")
def _():
    fresh()
    t0 = time.time() - 600
    candle = (t0 // 900) * 900 - 900

    def blocked_sig(c=candle, ts=t0):
        s = Signal("CRV/USDT:USDT", "short", 5.0, 0.2734, 0.0013, 0.27598, 0.26875, [],
                   {"setup": "trend", "hour": 17}, triggers=["pullback"], candle_ts=c)
        s.ref_price, s.ref_ts = 0.2728, ts
        return s
    assert st.log_signal(blocked_sig(), None, False, source="blocked")
    # tiến trình mới (shadow_seen trống): lượt quét đầu, cùng nến 15m → không ghi thêm (bản cũ: dòng thứ hai, tự học đếm 2 lần)
    assert st.log_signal(blocked_sig(ts=t0 + 150), None, False, source="blocked") is None
    rows = st.db.run("SELECT source FROM signals WHERE symbol='CRV/USDT:USDT'", fetch=True)
    assert [r["source"] for r in rows] == ["blocked"], rows
    assert st.log_signal(blocked_sig(c=candle + 900, ts=t0 + 900), None, False, source="blocked")   # nến sau: tín hiệu mới
    assert st.log_signal(blocked_sig(ts=t0 + 200), None, False, source="below_score")   # nguồn khác (điểm đổi theo dòng tiền)
    assert len(st.db.run("SELECT sid FROM signals WHERE symbol='CRV/USDT:USDT'", fetch=True)) == 3


@section("final review 2-2: bóng xét phút vào lệnh trên nến 1 giây như lệnh paper – râu chạm SL ngay sau lúc vào")
def _():
    fresh()
    # exits.simulate: secs xét trước mọi nến khác, chỉ SL/TP đang đặt (không TP1); không có secs thì như cũ
    k = lambda ts, o, h, l, cl: [ts * 1000, o, h, l, cl, 1]       # noqa: E731
    secs = [k(1001, 100.5, 100.6, 100.4, 100.5), k(1002, 100.5, 100.5, 97.9, 99.0)]
    later = [k(1060, 100, 103, 99.5, 102.5), k(1300, 102.5, 104.5, 100, 104)]
    assert exits.simulate("long", 100, 98, atr, "hybrid", 1000, later, 0.00125, secs=secs) == ("SL", round(-1 - 0.00125 * 50, 3))
    assert exits.simulate("long", 100, 98, atr, "hybrid", 1000, later, 0.00125, secs=secs[:1])[0] != "SL"
    tp1_touch = [k(1001, 100.5, 102.5, 100.4, 100.5)]           # mức TP1 1R = 102 chạm trong phút vào: không chốt
    o1, r1 = exits.simulate("long", 100, 98, atr, "hybrid", 1000, [k(1300, 100, 101, 97.5, 98)], 0.00125, tp1_r=1.0,
                            secs=tp1_touch)
    assert o1 == "SL" and r1 < -1, (o1, r1)
    # cùng tín hiệu, cùng đường giá: lệnh paper và bóng cùng đóng ở SL bởi râu ngay sau lúc vào (giây 40 của phút vào)
    sig = Signal("SEC/USDT:USDT", "long", 6.0, 1.000, 0.005, 0.990, 1.015, ["t"], {"setup": "trend", "atr_pct": 0.5})
    okx = Okx(1.002)
    t_ent = CUR - 3600 + 10
    okx.secs = [[(t_ent - 5) * 1000, 1.002, 1.002, 0.980, 1.002, 1]] + \
               [[(t_ent + k) * 1000, 1.002, 1.0025, 1.0015, 1.002, 1] for k in range(1, 30)] + \
               [[(t_ent + 30) * 1000, 1.002, 1.002, 0.9895, 0.999, 1]] + \
               [[(t_ent + k) * 1000, 0.999, 0.9995, 0.9985, 0.999, 1] for k in range(31, 50)]
    st.set("paper_balance", 10.0)
    run = run_paper(okx, sig, t_ent, [CUR - 3600 + 60 * m for m in range(1, 61)])
    t = next(run)
    okx.candles = [bar(0, 1.002, 1.0025, 0.980, 0.999), bar(1, 0.999, 1.004, 0.998, 1.003)] + \
                  [bar(i, 1.003, 1.004, 1.002, 1.003) for i in range(2, 61)]
    for _i in run:
        pass
    st.log_signal(sig, {"confidence": 70}, True, source="taken")
    tr = [x for x in st.trades(50) if x["id"] == t["id"]][0]
    assert tr["status"] == "closed" and tr["reason_close"] == "Chạm SL", ("paper: râu sau lúc vào phải chạm SL", tr)
    r_paper = tr["pnl"] / (tr["notional0"] * abs(tr["entry"] - tr["sl0"]) / tr["entry"])
    lr.okx = okx
    lr.resolve_pending()
    row = st.db.run("SELECT * FROM signals WHERE symbol=?", ("SEC/USDT:USDT",), fetch=True)[0]
    assert row["outcome"] == "SL" and abs(row["r_hybrid"] - r_paper) < 0.01 and row["r_fixed"] == row["r_hybrid"], \
        ("bóng phải thấy râu chạm SL trong phút vào lệnh như paper", row["outcome"], row["r_hybrid"], r_paper)
    # khung 5m chứa lúc vào chưa đóng hẳn: chưa đọc (không lưu mãi bộ nến dở)
    real = LM.time
    try:
        LM.time = types.SimpleNamespace(time=lambda: (int(T0 // 300) * 300 + 302.0))
        assert lr._entry_bars("SEC/USDT:USDT", {"sid": "fresh-x", "ts": T0}) is False and "fresh-x" not in lr._first
    finally:
        LM.time = real


assert not FAILED, f"{len(FAILED)} phần lỗi: {FAILED}"
print("TẤT CẢ OK")
