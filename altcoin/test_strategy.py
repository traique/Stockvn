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
h = NS(di_plus=0, di_minus=1, macd_hist=-1, rsi=40, adx=20)
fr = NS(row=lambda i: r); hf = NS(row=lambda i: h)
s_mom, _ = S._score(fr, hf, "long", ["MACD cắt lên", "EMA9 cắt lên EMA21"], 0, 0)
s_one, _ = S._score(fr, hf, "long", ["MACD cắt lên"], 0, 0)
s_mix, _ = S._score(fr, hf, "long", ["MACD cắt lên", "Pullback EMA21 + nến bật"], 0, 0)
assert s_mom == s_one, (s_mom, s_one)             # MACD + EMA = chỉ 1 trigger
assert s_mix == s_one + 1.5, (s_mix, s_one)       # động lượng + cấu trúc = 2 trigger
print(f"✓ trigger: MACD+EMA = {s_mom} (bằng 1 trigger), MACD+Pullback = {s_mix}")

# 4b) v3.2: volume cao trào & xu hướng 1h đã chạy lâu bị TRỪ điểm (không cộng)
r_cl = NS(**{**vars(r), "vol_ratio": cfg.CLIMAX_VOL})
s_cl, why_cl = S._score(NS(row=lambda i: r_cl), hf, "long", ["MACD cắt lên"], 0, 0)
assert s_cl == s_one - 1 and any("cao trào" in w for w in why_cl), (s_cl, s_one)
h_late = NS(**{**vars(h), "adx": cfg.LATE_TREND_ADX})
s_late, why_late = S._score(fr, NS(row=lambda i: h_late), "long", ["MACD cắt lên"], 0, 0)
assert s_late == s_one - 1 and any("chạy lâu" in w for w in why_late), (s_late, s_one)
print("✓ v3.2: volume cao trào -1, ADX 1h ≥ 35 -1")

# 4c) v3.2: cổng "ngược nhịp 1h của coin"
assert "ngược nhịp 1h" in S._gates(row(), "long", 1, None, chg1h=-cfg.CHASE_1H_PCT)
assert S._gates(row(), "long", 1, None, chg1h=-cfg.CHASE_1H_PCT + 0.1) == ""
sh = row(close=99, open=100, ema21=99.5, rsi=40, close_pos=0.2)
assert "ngược nhịp 1h" in S._gates(sh, "short", -1, None, chg1h=cfg.CHASE_1H_PCT)
assert S._gates(sh, "short", -1, None, chg1h=-1.0) == ""
assert S._gates(sh, "short", -1, {"btc_chg_1h": cfg.BTC_GATE_CHG + 0.01}) == "BTC đang xu hướng tăng"
print("✓ v3.2: cổng ngược nhịp 1h; cổng BTC %.1f%%" % cfg.BTC_GATE_CHG)

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
assert {"afternoon_session", "setup_range", "night_session", "climax_volume"} <= S.active_blocks()   # mặc định v3.2
S.LEARNED["blocks"] = ["short_side", "weak_adx"]
assert "short_side" not in S.active_blocks() and "weak_adx" in S.active_blocks()   # không bao giờ chặn cả phía
S.LEARNED["blocks"] = []
_saved = cfg.DEFAULT_BLOCKS; cfg.DEFAULT_BLOCKS = ["afternoon_session", "setup_range"]
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

# ======================================================================= v3.3 phần B: quét, luật chặn, trọng số, danh sách coin
# Mỗi phần chạy riêng (lỗi một phần không che phần sau) – phần nào đỏ trên code cũ thì in ✗ rồi cả file thoát mã lỗi.
import json, logging, shutil, time, traceback
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


S.LEARNED.update({"min_score": None, "trigger_w": {}, "blocks": [], "unblocked": []})
cfg.DATA_DIR = "/tmp/okx_test_strategy/scan"
shutil.rmtree(cfg.DATA_DIR, ignore_errors=True)
from bot.storage import Store
from bot import main as M
STORE = Store()


class ScanOKX:
    """Sàn giả cho Bot.scan: danh sách coin cố định, nến rỗng (diagnose được thay bằng kết quả dựng sẵn)."""
    def __init__(self, syms): self.syms = syms
    def top_altcoins(self, n): return [(s, 1e6 * (len(self.syms) - i), 0.0) for i, s in enumerate(self.syms)]
    def ohlcv(self, *a, **k): return []
    def price(self, s): return 100.0
    class ex:
        def __getattr__(self, k): raise RuntimeError("không có mạng")
    ex = ex()


class FakeAI:
    def __init__(self): self.asked = []
    def analyze(self, sig):
        self.asked.append(sig.symbol)
        return {"decision": sig.side.upper(), "confidence": 80, "model": "fake", "reason": "ok"}


class FakeEngine:
    def __init__(self, take=True): self.opened, self.take, self.monitored = [], take, []
    def open(self, sig, ai):
        self.opened.append(sig.symbol)
        return {"id": len(self.opened)} if self.take else None
    def monitor(self): self.monitored.append(M.time.time())


CANDLE = int(time.time() // 900) * 900 - 900       # nến 15m tín hiệu (đã đóng) – như diagnose ghi vào sig.candle_ts


def trend_sig(sym, score, side="long", **ctx):
    c = {"setup": "trend", "hour": 21, "adx": 30, "vol_ratio": 1.1, "atr_pct": 0.6, **ctx}
    g = S.Signal(sym, side, score, 100.0, 0.6, 98.8 if side == "long" else 101.2, 103.6 if side == "long" else 96.4,
                 ["t"], c, triggers=["pullback"])
    g.candle_ts = CANDLE
    return g


def make_bot(results, opens=0, ai=None, engine=None):
    """Bot rút gọn chỉ đủ cho scan(): diagnose trả kết quả dựng sẵn theo mã (sig, lý do)."""
    STORE.db.run("DELETE FROM signals"); STORE.db.run("DELETE FROM scans"); STORE.db.run("DELETE FROM trades")
    for i in range(opens):
        STORE.open_trade(dict(mode="paper", symbol=f"OPEN{i}/USDT:USDT", side="long", entry=1.0, sl=0.9, tp=1.2,
                              notional=5.0, status="open", opened_at=time.time()))
    b = M.Bot.__new__(M.Bot)
    b.store, b.okx = STORE, ScanOKX(list(results))
    b.ai, b.engine = ai or FakeAI(), engine or FakeEngine()
    b.cooldown, b.shadow_seen, b.last_btc, b.last_scan_summary = {}, {}, {}, None
    M.diagnose = lambda sym, *a, **k: (results[sym][0], results[sym][1], {"score": results[sym][0].score if results[sym][0] else 0})
    return b


def rows():
    out = {}
    for r in STORE.db.run("SELECT * FROM signals ORDER BY rowid", fetch=True):
        out[r["symbol"].split("/")[0]] = (r["source"], json.loads(r["features"]))
    return out


class Grab(logging.Handler):
    def __init__(self): super().__init__(); self.msgs = []
    def emit(self, rec): self.msgs.append(rec.getMessage())


GRAB = Grab()
logging.getLogger("main").addHandler(GRAB)


@section("bug 6: đủ lệnh mở vẫn quét, ghi log quét và tín hiệu bóng max_positions – không hỏi AI, không vào lệnh")
def _():
    a, b2 = trend_sig("AAA/USDT:USDT", 7.0), trend_sig("BBB/USDT:USDT", 6.0)
    blk = trend_sig("EEE/USDT:USDT", 6.5, hour=15)              # phiên 13h–19h: luật chặn sẵn
    low = trend_sig("FFF/USDT:USDT", 4.5); low.rejected = "điểm thấp (4.5 < 5.0)"
    ai, eng = FakeAI(), FakeEngine()
    bot = make_bot({"AAA/USDT:USDT": (a, ""), "BBB/USDT:USDT": (b2, ""), "EEE/USDT:USDT": (blk, ""),
                    "FFF/USDT:USDT": (low, low.rejected)}, opens=cfg.MAX_OPEN_POSITIONS, ai=ai, engine=eng)
    bot.scan()
    got = rows()
    assert {k: v[0] for k, v in got.items()} == {"AAA": "max_positions", "BBB": "max_positions", "EEE": "blocked",
                                                 "FFF": "below_score"}, got
    assert ai.asked == [] and eng.opened == [], (ai.asked, eng.opened)
    sc = STORE.scans(5)
    assert len(sc) == 1 and sc[0]["passed"] == 2 and sc[0]["n_open"] == cfg.MAX_OPEN_POSITIONS, sc
    assert bot.last_scan_summary["n_open"] == cfg.MAX_OPEN_POSITIONS


@section("bug 7: ứng viên xếp sau ứng viên vừa vào lệnh được ghi bóng slot_taken; hỏi AI theo thứ tự điểm")
def _():
    sigs = {"CCC/USDT:USDT": trend_sig("CCC/USDT:USDT", 5.5), "AAA/USDT:USDT": trend_sig("AAA/USDT:USDT", 7.0),
            "BBB/USDT:USDT": trend_sig("BBB/USDT:USDT", 6.0), "DDD/USDT:USDT": trend_sig("DDD/USDT:USDT", 5.2)}
    ai, eng = FakeAI(), FakeEngine(take=True)
    bot = make_bot({k: (v, "") for k, v in sigs.items()}, opens=0, ai=ai, engine=eng)
    _k, cfg.AI_CANDIDATES_PER_SCAN = cfg.AI_CANDIDATES_PER_SCAN, 3
    try:
        bot.scan()
    finally:
        cfg.AI_CANDIDATES_PER_SCAN = _k
    got = {k: v[0] for k, v in rows().items()}
    assert ai.asked == ["AAA/USDT:USDT"] and eng.opened == ["AAA/USDT:USDT"], (ai.asked, eng.opened)
    assert got == {"AAA": "taken", "BBB": "slot_taken", "CCC": "slot_taken", "DDD": "not_sent"}, got
    # ứng viên #1 AI từ chối → hỏi tiếp #2 (thiết kế cũ giữ nguyên), #2 vào lệnh → #3 slot_taken
    class Picky(FakeAI):
        def analyze(self, sig):
            self.asked.append(sig.symbol)
            ok = sig.symbol != "AAA/USDT:USDT"
            return {"decision": sig.side.upper() if ok else "SKIP", "confidence": 80 if ok else 90, "model": "f", "reason": "x"}
    ai = Picky()
    bot = make_bot({k: (v, "") for k, v in sigs.items()}, opens=0, ai=ai, engine=FakeEngine(take=True))
    bot.scan()
    got = {k: v[0] for k, v in rows().items()}
    assert ai.asked == ["AAA/USDT:USDT", "BBB/USDT:USDT"], ai.asked
    assert got == {"AAA": "ai_rejected", "BBB": "taken", "CCC": "slot_taken", "DDD": "not_sent"}, got


@section("bug 9: log & tín hiệu bóng ghi đúng luật chặn sẵn / luật tự học")
def _():
    blk = trend_sig("EEE/USDT:USDT", 6.5, hour=15)                 # luật chặn sẵn: phiên chiều
    lrn = trend_sig("WWW/USDT:USDT", 6.5, adx=22)                  # weak_adx – chỉ bộ tự học chặn
    S.LEARNED["blocks"] = ["weak_adx"]
    try:
        GRAB.msgs.clear()
        bot = make_bot({"EEE/USDT:USDT": (blk, ""), "WWW/USDT:USDT": (lrn, "")})
        bot.scan()
    finally:
        S.LEARNED["blocks"] = []
    got = rows()
    line_e = [m for m in GRAB.msgs if m.startswith("EEE/USDT:USDT")]
    line_w = [m for m in GRAB.msgs if m.startswith("WWW/USDT:USDT")]
    assert line_e and "luật chặn sẵn" in line_e[0] and "tự học" not in line_e[0], line_e
    assert line_w and "luật tự học" in line_w[0], line_w
    assert got["EEE"][1].get("block_origin") == "default" and got["EEE"][1].get("blocked_rule") == "afternoon_session", got["EEE"]
    assert got["WWW"][1].get("block_origin") == "learned" and got["WWW"][1]["rejected"].startswith("luật tự học"), got["WWW"]
    sc = STORE.scans(1)[0]["reasons"]
    assert sc.get("luật chặn sẵn chặn") == 1 and sc.get("luật tự học chặn") == 1, sc


@section("bug 9: trọng số trigger học được áp dụng cả khi tín hiệu có trigger cùng nhóm (pullback + VWAP)")
def _():
    r_ = NS(ema9=3, ema21=2, ema50=1, close=1, ema200=5, adx=10, vol_ratio=1, close_pos=0.5, body_pct=0.3, vwap=9)
    h_ = NS(di_plus=0, di_minus=1, macd_hist=-1, rsi=40, adx=20)
    fr_, hf_ = NS(row=lambda i: r_), NS(row=lambda i: h_)
    both = ["Pullback EMA21 + nến bật", "Bật tại VWAP"]
    S.LEARNED["trigger_w"] = {}
    s0, _w = S._score(fr_, hf_, "long", both, 0, 0)
    S.LEARNED["trigger_w"] = {"pullback": 0.82}
    try:
        s1, _w = S._score(fr_, hf_, "long", both, 0, 0)
        s_pb, _w = S._score(fr_, hf_, "long", both[:1], 0, 0)
        S.LEARNED["trigger_w"] = {"pullback": 1.2}
        s_up, _w = S._score(fr_, hf_, "long", both, 0, 0)
    finally:
        S.LEARNED["trigger_w"] = {}
    # trọng số nhóm = trung bình (0.82 + 1.0) / 2 = 0.91 → điểm trigger 1.5 × 0.91; bản cũ max(0.82, 1.0) = 1.0 → bỏ qua
    assert abs(s1 - (s0 - 1.5 * (1 - 0.91))) < 0.011, (s0, s1)
    assert abs(s_pb - (s0 - 1.5 * (1 - 0.82))) < 0.011, (s0, s_pb)
    assert s_up > s0, (s0, s_up)                                     # tăng trọng số cũng có tác dụng
    S.LEARNED["trigger_w"] = {}
    assert S._score(fr_, hf_, "long", both + ["MACD cắt lên"], 0, 0)[0] == S._score(fr_, hf_, "long", both[:1] + ["MACD cắt lên"], 0, 0)[0]


@section("bug 10: danh sách coin lọc theo instCategory của OKX (cổ phiếu / hàng hoá bị loại dù tên không trong EXCLUDE)")
def _():
    from bot.exchange import OKX
    mk = {}
    tick = {}
    for base, cat, vol in (("SOL", "1", 9), ("SKHY", "3", 8), ("CL", "4", 7), ("NEWC", None, 6), ("BTC", "1", 10),
                           ("UNITREE", "3", 5), ("PEPE", "1", 4)):
        sym = f"{base}/USDT:USDT"
        info = {"instCategory": cat} if cat else {}
        mk[sym] = {"swap": True, "quote": "USDT", "active": True, "base": base, "info": info}
        tick[sym] = {"last": 1.0, "percentage": 1.0, "info": {"volCcy24h": vol * 1e6}}

    class Ex:
        def fetch_tickers(self, params=None): return tick
    o = OKX.__new__(OKX)
    o.ex, o._markets, o._markets_ts, o.last_filtered, o._no_category = Ex(), mk, time.time(), set(), set()
    got = [s.split("/")[0] for s, _, _ in o.top_altcoins(10)]
    assert got == ["SOL", "NEWC", "PEPE"], got                  # BTC: loại theo tên; NEWC: sàn không ghi loại → giữ
    assert o.last_filtered == {"SKHY", "CL", "UNITREE"}, o.last_filtered


@section("MIN_RR: setup xu hướng không có cổng R:R (TP dựng ≥ 1.2 × MIN_RR × R), range giữ cổng RANGE_MIN_RR")
def _():
    import random as _r
    _r.seed(3)
    worst = 99
    for _i in range(2000):                                      # cổng cũ "RR ≥ MIN_RR" không bao giờ chặn được
        a = _r.uniform(0.1, 5); p = _r.uniform(10, 1000)
        lows = [p - _r.uniform(0, 4) * a for _ in range(6)]
        highs = [p + _r.uniform(0, 4) * a for _ in range(6)]
        for side in ("long", "short"):
            sl, tp = S._levels(NS(low=lows, high=highs), side, p, a)
            worst = min(worst, abs(tp - p) / abs(p - sl))
    assert worst >= 1.2 * cfg.MIN_RR - 1e-9, worst
    tsig = S.Signal("X/USDT:USDT", "long", 6, 100, 1, 98, 101, [], {"setup": "trend"})          # RR 0.5 – cổng không áp dụng
    rsig = S.Signal("X/USDT:USDT", "long", 6, 100, 1, 99, 101, [], {"setup": "range"})          # RR 1.0 < RANGE_MIN_RR
    assert S.min_rr_for(tsig) is None and S.rr_ok(tsig)
    assert S.min_rr_for(rsig) == cfg.RANGE_MIN_RR and not S.rr_ok(rsig)
    from bot.engine import fill_check
    _fr = cfg.FILL_RECHECK; cfg.FILL_RECHECK = True
    try:
        assert "R:R" in fill_check(S.Signal("X/USDT:USDT", "long", 6, 100, 1, 99, 101.1, [], {"setup": "range"}), 100.1, 100.1)
        assert fill_check(tsig, 100.0, 100.0) == ""
    finally:
        cfg.FILL_RECHECK = _fr


# ======================================================================= v3.3 sửa sau review phần B
from bot.learner import Learner, TECH_SOURCES


def tech_n():
    """Bộ tự học: số tín hiệu kỹ thuật (mọi dòng chưa có kết quả được gán kết quả giả +1R)."""
    for r in STORE.db.run("SELECT sid FROM signals WHERE outcome='PENDING'", fetch=True):
        STORE.set_outcome(r["sid"], "TP", 1.0, 1.0, 1.0, 1.0)
    lrn = Learner(STORE, None, None, lambda m: None)
    st_, rows_, tech = lrn.compute_stats()
    return st_["tech"]["n"], sorted((r["symbol"].split("/")[0], r["source"]) for r in tech)


def all_rows():
    return [(r["symbol"].split("/")[0], r["source"], r["outcome"])
            for r in STORE.db.run("SELECT * FROM signals ORDER BY rowid", fetch=True)]


@section("review B 1: đủ lệnh → max_positions; trống một chỗ → lượt sau vào lệnh cùng tín hiệu; bộ tự học đếm 1 lần")
def _():
    ai, eng = FakeAI(), FakeEngine()
    bot = make_bot({"ARB/USDT:USDT": (trend_sig("ARB/USDT:USDT", 6.0), "")}, opens=cfg.MAX_OPEN_POSITIONS, ai=ai, engine=eng)
    bot.scan()
    STORE.db.run("DELETE FROM trades WHERE symbol=?", ("OPEN0/USDT:USDT",))      # một lệnh đóng → trống một chỗ
    M.diagnose = lambda sym, *a, **k: (trend_sig(sym, 6.0), "", {"score": 6.0})  # lượt sau: cùng nến 15m, tín hiệu mới
    bot.scan()
    got = all_rows()
    assert [(s_, src) for s_, src, _o in got] == [("ARB", "max_positions"), ("ARB", "taken")] and eng.opened == ["ARB/USDT:USDT"], got
    assert got[0][2] == "DUP" and got[1][2] == "PENDING", got          # dòng bóng trùng không chờ kết quả, không vào thống kê
    assert not [r for r in STORE.pending_signals(50) if r["source"] == "max_positions"]
    n, tech = tech_n()
    assert n == 1 and tech == [("ARB", "taken")], (n, tech)


@section("review B 4: lượt 1 AAA vào, BBB slot_taken; lượt 2 BBB vào lệnh – bộ tự học thấy BBB một lần")
def _():
    sigs = lambda: {"AAA/USDT:USDT": (trend_sig("AAA/USDT:USDT", 7.0), ""), "BBB/USDT:USDT": (trend_sig("BBB/USDT:USDT", 6.0), "")}
    eng = FakeEngine()
    bot = make_bot(sigs(), opens=0, engine=eng)
    bot.scan()
    res = sigs()
    M.diagnose = lambda sym, *a, **k: (res[sym][0], "", {"score": res[sym][0].score})
    bot.scan()                                              # AAA đang cooldown sau khi vào; BBB được hỏi AI
    got = all_rows()
    assert [(s_, src) for s_, src, _o in got] == [("AAA", "taken"), ("BBB", "slot_taken"), ("BBB", "taken")], got
    assert got[1][2] == "DUP", got
    n, tech = tech_n()
    assert n == 2 and tech == [("AAA", "taken"), ("BBB", "taken")], (n, tech)
    # khởi động lại (mất cooldown / shadow_seen) lúc đủ lệnh: tín hiệu đã có dòng hỏi AI không bị ghi bóng thêm
    bot2 = make_bot({"BBB/USDT:USDT": (trend_sig("BBB/USDT:USDT", 6.0), "")}, opens=cfg.MAX_OPEN_POSITIONS)
    STORE.db.run("DELETE FROM signals")
    for g, src in ((trend_sig("BBB/USDT:USDT", 6.0), "ai_rejected"),):
        g.ref_price, g.ref_ts = 100.0, time.time()
        STORE.log_signal(g, {"decision": "SKIP", "confidence": 80}, False, source=src)
    bot2.scan()
    assert [(s_, src) for s_, src, _o in all_rows()] == [("BBB", "ai_rejected")], all_rows()
    # nến tín hiệu khác (tín hiệu mới) vẫn được ghi
    nxt = trend_sig("BBB/USDT:USDT", 6.0); nxt.candle_ts = CANDLE + 900
    bot2.shadow_seen.clear()
    M.diagnose = lambda sym, *a, **k: (nxt, "", {"score": 6.0})
    bot2.scan()
    assert [(s_, src) for s_, src, _o in all_rows()] == [("BBB", "ai_rejected"), ("BBB", "max_positions")], all_rows()


@section("review B 2: giờ ghi trong features = giờ luật phiên đã xét (chẩn đoán 06:59:50, ghi giá 07:00:05)")
def _():
    STORE.db.run("DELETE FROM signals")
    day = time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d")) - 86400
    blk = trend_sig("NNN/USDT:USDT", 6.0, hour=6)          # chẩn đoán 06:59:50 → luật 0h–7h chặn
    assert S.blocking_rule(blk) == "night_session"
    blk.ref_price, blk.ref_ts = 100.0, day + 7 * 3600 + 5
    STORE.log_signal(blk, None, False, source="blocked")
    tak = trend_sig("TTT/USDT:USDT", 6.0, hour=12)         # chẩn đoán 12:59:50 → không bị chặn, vào lệnh 13:00:05
    assert S.blocking_rule(tak) == ""
    tak.ref_price, tak.ref_ts = 100.0, day + 13 * 3600 + 5
    STORE.log_signal(tak, {"decision": "LONG", "confidence": 80}, True, source="taken")
    f = {r["symbol"].split("/")[0]: json.loads(r["features"]) for r in STORE.db.run("SELECT * FROM signals", fetch=True)}
    assert f["NNN"]["hour"] == 6 and f["TTT"]["hour"] == 12, (f["NNN"]["hour"], f["TTT"]["hour"])
    for r in STORE.db.run("SELECT sid FROM signals", fetch=True):
        STORE.set_outcome(r["sid"], "TP", 1.0, 1.0, 1.0, 1.0)
    st_ = Learner(STORE, None, None, lambda m: None).compute_stats()[0]
    assert st_["trigger_blocked_excluded"] == 1 and st_["by_trigger"]["pullback"]["n"] == 1, st_["by_trigger"]
    assert st_["by_rule"]["night_session"]["n"] == 1 and st_["by_rule"]["afternoon_session"]["n"] == 0, st_["by_rule"]


@section("review B 6: đang đủ lệnh, lượt quét (~30 giây) vẫn quản lý lệnh mở mỗi MONITOR_INTERVAL")
def _():
    clock = [1_000_000.0]
    fake = NS(time=lambda: clock[0], sleep=lambda s: None)

    class SlowOKX(ScanOKX):
        def ohlcv(self, *a, **k):
            clock[0] += 0.3                                  # 2 lần đọc nến / coin → 0.6 giây / coin
            return []
    syms = [f"C{i}/USDT:USDT" for i in range(50)]
    eng = FakeEngine()
    bot = make_bot({s_: (None, "1h sideway") for s_ in syms}, opens=cfg.MAX_OPEN_POSITIONS, engine=eng)
    bot.okx = SlowOKX(syms)
    real, M.time = M.time, fake
    try:
        t0 = clock[0]
        bot.scan()
        t1 = clock[0]
    finally:
        M.time = real
    marks = [t0] + eng.monitored + [t1]
    assert t1 - t0 >= 29 and len(eng.monitored) >= 1, (t1 - t0, eng.monitored)
    assert max(b - a for a, b in zip(marks, marks[1:])) <= cfg.MONITOR_INTERVAL + 0.61, marks


@section("review B 7: bảng scans giữ 6 cột như 3.2.x (quay lui về server cũ vẫn ghi được lượt quét); n_open đọc lại đúng")
def _():
    cols = [r["name"] for r in STORE.db.run("PRAGMA table_info(scans)", fetch=True)]
    assert cols == ["ts", "scanned", "passed", "reasons", "near", "top"], cols
    STORE.db.run("DELETE FROM scans")
    STORE.add_scan({"ts": 1.0, "scanned": 50, "passed": 1, "reasons": {"1h sideway": 30}, "near": [], "top": ["A long 6"],
                    "n_open": 2})
    STORE.db.run("INSERT INTO scans VALUES(?,?,?,?,?,?)", (2.0, 50, 0, "{}", "[]", "[]"))     # câu lệnh của bản 3.2.1
    sc = {r["ts"]: r for r in STORE.scans(5)}
    assert sc[1.0]["n_open"] == 2 and sc[1.0]["reasons"] == {"1h sideway": 30} and sc[2.0]["n_open"] == 0, sc


@section("review B 9: /status và thẻ 'Lần quét cuối' báo đang đủ lệnh – chỉ ghi bóng, không hỏi AI")
def _():
    bot = make_bot({"AAA/USDT:USDT": (trend_sig("AAA/USDT:USDT", 7.0), "")}, opens=cfg.MAX_OPEN_POSITIONS)
    bot.scan()
    bot.engine.equity = lambda prices=None: 10.0
    bot.engine._pnl = lambda *a: (0.0, 0.0)
    bot.ai = NS(last_model="m", calls_today=lambda: 0, stats={"fails": 0})
    bot.paused, bot.start_equity, bot.last_prices, bot.last_btc = False, 10.0, {}, {}
    line = [x for x in M.Bot.status_text(bot).splitlines() if x.startswith("Quét cuối")][0]
    assert "1 đạt – đủ %d lệnh, chỉ ghi bóng" % cfg.MAX_OPEN_POSITIONS in line, line
    from bot.dashboard import APP_JS
    assert "ls.n_open?` – đủ ${ls.n_open} lệnh, chỉ ghi bóng`" in APP_JS


assert not FAILED, f"{len(FAILED)} phần lỗi: {FAILED}"
print("TẤT CẢ OK")
