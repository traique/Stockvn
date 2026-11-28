"""v3.5 – sổ Grid spot chạy ảo (grid.py): chia lưới, khớp theo nến 1m đã đóng (đi qua mới khớp), nến chạm nhiều mức, dừng khi
phá vùng, lấy bù nến sau mất kết nối cho cùng kết quả, không gọi API đặt lệnh, vòng mới chỉ khi GRID_ROUND tăng."""
import json
import os
import shutil
import sys
import time
import traceback

D = "/tmp/okx_test_grid"
shutil.rmtree(D, ignore_errors=True)
os.environ.update(MODE="paper", DATA_DIR=D, AI_ENABLED="false", DATABASE_URL="", GRID_ENABLED="true",
                  GRID_PAIRS="BTC-USDT", GRID_CAPITAL="100", GRID_LEVELS="11", GRID_RANGE_DAYS="3", GRID_SPACING="arithmetic",
                  GRID_MAKER_FEE="0.001", GRID_TAKER_FEE="0.001", GRID_STOP_OUT_PCT="0.05")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bot.config import cfg
from bot import grid as G
from bot.storage import Store

FAILED = []


def section(name):
    def run(fn):
        try:
            fn()
            print("✓", name)
        except Exception:
            FAILED.append(name)
            print("✗", name, "\n" + traceback.format_exc(limit=4))
        return fn
    return run


def bar(i, o, h, l, c, t0=1_800_000_000):
    return [(t0 + 60 * i) * 1000, o, h, l, c, 1.0]


# Lưới 100..200, 11 mức → 10 ô, mỗi ô 10 USDT, bước 10
def book(price=150.0):
    return G.GridBook.start(100.0, 200.0, 11, "arithmetic", 100.0, price, 1_800_000_000, 0.001, 0.001)


@section("chia lưới: geometric tỉ lệ đều, arithmetic khoảng đều; vùng giá từ nến ngày đã đóng (bỏ nến đang chạy)")
def _():
    lv = G.grid_levels(100, 200, 11, "arithmetic")
    assert lv[0] == 100 and lv[-1] == 200 and abs(lv[5] - 150) < 1e-9
    lg = G.grid_levels(100, 400, 3, "geometric")
    assert abs(lg[1] - 200) < 1e-9 and lg[-1] == 400
    daily = [[0, 1, 120, 90, 100, 0], [1, 1, 130, 95, 100, 0], [2, 1, 125, 99, 100, 0], [3, 1, 500, 1, 100, 0]]
    assert G.price_range(daily, 2) == (95, 130)          # 2 nến đã đóng cuối: bỏ nến đang chạy (500 / 1)
    assert G.spot_symbol("BTC-USDT") == "BTC/USDT" and G.spot_symbol("ETH/USDT") == "ETH/USDT"


@section("mở vòng: ô dưới giá đặt mua; ô trên giá mua sẵn coin (phí taker), đặt bán ở mức trên; HODL = mua hết ở giá đầu")
def _():
    b = book(150.0)
    s = b.s
    buys = [o for o in s["orders"] if o["side"] == "buy"]
    sells = [o for o in s["orders"] if o["side"] == "sell"]
    assert [o["price"] for o in buys] == [100, 110, 120, 130, 140] and [o["price"] for o in sells] == [160, 170, 180, 190, 200]
    coin = sum(10 / lo for lo in (150, 160, 170, 180, 190))
    assert abs(s["coin"] - coin) < 1e-9
    cost = coin * 150
    assert abs(s["usdt"] - (100 - cost * 1.001)) < 1e-9 and abs(s["fees"] - cost * 0.001) < 1e-9
    assert abs(b.value() - (100 - cost * 0.001)) < 1e-9          # giá trị = vốn trừ phí mua sẵn
    assert abs(b.hodl() - 100 * 0.999) < 1e-9 and abs(b.hodl(300) - 100 * 0.999 * 2) < 1e-9


@section("khớp: giá đi qua mức mới khớp, chạm sát không khớp; mua ở mức i → đặt bán mức i+1; bán → đặt mua lại; lãi đã khoá trừ phí")
def _():
    b = book(150.0)
    assert b.step(bar(0, 150, 152, 140, 145)) == []              # low = 140 chạm sát mức mua 140: không khớp
    f = b.step(bar(1, 145, 146, 139, 141))                      # đi qua 140 → mua
    assert len(f) == 1 and f[0][3] == "buy" and f[0][2] == 140 and abs(f[0][4] - 10 / 140) < 1e-12
    assert any(o["side"] == "sell" and o["price"] == 150 for o in b.s["orders"])
    assert not any(o["side"] == "buy" and o["price"] == 140 for o in b.s["orders"])
    assert b.step(bar(2, 141, 150, 141, 149)) == []              # high = 150 chạm sát mức bán: không khớp
    f = b.step(bar(3, 149, 151, 148, 150.5))                    # đi qua 150 → bán
    assert len(f) == 1 and f[0][3] == "sell" and f[0][2] == 150
    amt = 10 / 140
    profit = amt * (150 - 140) - amt * 150 * 0.001 - amt * 140 * 0.001
    assert abs(f[0][6] - profit) < 1e-12 and abs(b.s["realized"] - profit) < 1e-12 and b.s["rounds"] == 1
    assert any(o["side"] == "buy" and o["price"] == 140 for o in b.s["orders"])      # đặt mua lại ở 140
    m = b.metrics()
    assert m["rounds"] == 1 and m["in_range_pct"] == 100.0 and m["bars"] == 4


@section("nến chạm nhiều mức: nến tăng thấp trước rồi cao (mua 130, 120 rồi bán 140, 150); nến giảm cao trước; mỗi mức một lần")
def _():
    b = book(150.0)
    f = b.step(bar(0, 135, 155, 115, 150))       # tăng: 135 → 115 (mua 130, 120) → 155 (bán 130→140? không: bán đặt ở 140 và 130) → 150
    sides = [(x[3], x[2]) for x in f]
    assert sides == [("buy", 130), ("buy", 120), ("sell", 130), ("sell", 140)], sides
    assert b.s["rounds"] == 2
    b2 = book(150.0)
    f2 = b2.step(bar(0, 165, 185, 135, 140))     # giảm: 165 → 185 (bán 170, 180) → 135 (mua 140, 160, 170? mua đặt lại ở 160, 170 rồi 140) → 140
    sides2 = [(x[3], x[2]) for x in f2]
    assert sides2 == [("sell", 170), ("sell", 180), ("buy", 170), ("buy", 160), ("buy", 140)], sides2
    # mỗi mức tối đa một lần mỗi nến: nến nảy 2 lần qua 140 chỉ khớp 1 lần
    b3 = book(150.0)
    f3 = b3.step(bar(0, 150, 151, 139, 139.5))
    assert [(x[3], x[2]) for x in f3] == [("buy", 140)]


@section("chuỗi nến đi ngang: lãi grid dương đúng số vòng; HODL đứng yên; sụt tối đa ghi theo nến")
def _():
    b = book(150.0)
    bars = []
    for k in range(10):
        bars.append(bar(2 * k, 150, 151, 138, 139))        # qua 140 → mua
        bars.append(bar(2 * k + 1, 139, 151, 138, 150.5))  # qua 150 → bán
    for x in bars:
        b.step(x)
    amt = 10 / 140
    per = amt * 10 - amt * (150 + 140) * 0.001
    m = b.metrics()
    assert m["rounds"] == 10 and abs(b.s["realized"] - 10 * per) < 1e-9 and m["realized"] > 0
    assert abs(m["hodl"] - 100 * 0.999 * 150.5 / 150) < 1e-9
    assert m["max_dd_pct"] > 0 and m["in_range_pct"] == 100.0 and m["rounds_per_day"] == round(10 / (20 / 1440), 2)


@section("phá vùng: giá đóng ra ngoài quá 5% → dừng, huỷ lệnh ảo, giữ coin/USDT, không khớp thêm")
def _():
    b = book(150.0)
    assert b.step(bar(0, 150, 209, 149, 208)) and b.s["stopped"] is None          # 208 < 210: chưa dừng (đã bán hết ô trên)
    b.step(bar(1, 208, 212, 207, 211))
    assert b.s["stopped"] and "vượt đỉnh" in b.s["stopped"] and b.s["orders"] == []
    coin, usdt = b.s["coin"], b.s["usdt"]
    assert b.step(bar(2, 211, 100, 90, 95)) == [] and b.s["coin"] == coin and b.s["usdt"] == usdt
    b2 = book(150.0)
    b2.step(bar(0, 150, 150, 94, 94.9))
    assert b2.s["stopped"] and "thủng đáy" in b2.s["stopped"]


class Ex:
    """Sàn giả: chỉ fetch_ohlcv (API công khai); mọi thứ khác (đặt lệnh, số dư...) là lỗi."""
    def __init__(self, daily, minutes): self.daily, self.minutes, self.calls = daily, minutes, []
    def parse_timeframe(self, tf): return 60 if tf == "1m" else 86400
    def fetch_ohlcv(self, sym, tf, since=None, limit=300):
        self.calls.append((sym, tf, since, limit))
        rows = self.daily if tf == "1d" else self.minutes
        if since is not None:
            rows = [r for r in rows if r[0] >= since]
        return rows[:limit] if tf == "1m" else rows[-limit:]
    def __getattr__(self, k): raise AssertionError(f"API không được phép bị gọi: {k}")


class OKX:
    def __init__(self, ex): self.ex = ex
    def price(self, sym): return 150.0
    def ohlcv_since(self, symbol, timeframe, since_s, max_bars=2000):
        step = self.ex.parse_timeframe(timeframe) * 1000
        cur, out = int(since_s * 1000), []
        while len(out) < max_bars:
            rows = [r for r in self.ex.fetch_ohlcv(symbol, timeframe, since=cur, limit=300) if r[0] >= cur]
            if not rows:
                break
            out += rows
            cur = int(rows[-1][0]) + step
        return out
    def open_position(self, *a, **k): raise AssertionError("open_position")
    def close_position(self, *a, **k): raise AssertionError("close_position")


T0 = 1_800_000_000
DAILY = [[(T0 - 86400 * (4 - k)) * 1000, 150, 200 if k < 4 else 999, 100 if k < 4 else 1, 150, 0] for k in range(5)]   # nến cuối đang chạy


def minutes(n, start=T0):
    out = []
    p = 150.0
    for i in range(n):
        o = p
        if i % 2 == 0:
            h, l, c = o + 1, 138, 139
        else:
            h, l, c = 151, o - 1, 150.5
        out.append([(start + 60 * i) * 1000, o, h, l, c, 1.0])
        p = c
    return out


def runner(ex, store, sent):
    return G.GridRunner(OKX(ex), store, sent.append, pairs=["BTC-USDT"], active=lambda: True)


@section("GridRunner: mở vòng từ nến ngày đã đóng, xử lý nến 1m đã đóng, ghi grid_runs / grid_fills / grid_equity; không API đặt lệnh")
def _():
    st = Store()
    sent = []
    mins = minutes(40)
    ex = Ex(DAILY, mins)
    r = runner(ex, st, sent)
    now0 = T0 + 30                   # phút T0 đang chạy
    r.tick(now=now0)
    run = st.grid_open_run("BTC-USDT")
    assert run and run["low"] == 100 and run["high"] == 200 and run["round"] == 1 and run["levels"] == 11
    assert any("Grid BTC-USDT vòng 1 mở" in m for m in sent)
    r.tick(now=T0 + 60 * 40 + 10)     # 40 nến đã đóng (nến cuối đóng lúc T0+2400, +5 s trễ)
    fills = st.grid_fills(run["id"], limit=500)
    assert len(fills) == 40 and sum(1 for f in fills if f["side"] == "sell") == 20, len(fills)
    eq = st.grid_equity(run["id"])
    assert len(eq) == 3 and all(int(e["ts"]) % 900 == 0 for e in eq), [e["ts"] for e in eq]   # 15 phút một dòng (T0 chia hết 900)
    state = json.loads(st.grid_open_run("BTC-USDT")["state"])
    assert state["rounds"] == 20 and state["realized"] > 0 and state["last_bar"] == T0 + 60 * 39
    assert all(c[1] in ("1d", "1m") for c in ex.calls)
    snap = r.snapshot()[0]
    assert snap["state"] == "đang chạy" and snap["rounds"] == 20 and len(snap["curve"]) == 3
    assert "GRID SPOT" in r.text() and "20 vòng" in r.text()


@section("mất kết nối: chạy lại lấy bù nến thiếu, kết quả y hệt chạy liên tục; nến đang chạy không xét")
def _():
    sent = []
    mins = minutes(60)
    # liên tục: mỗi phút một tick
    st1 = Store(); st1.db.run("DELETE FROM grid_runs"); st1.db.run("DELETE FROM grid_fills"); st1.db.run("DELETE FROM grid_equity")
    r1 = runner(Ex(DAILY, mins), st1, sent)
    for i in range(61):
        r1.tick(now=T0 + 60 * i + 10)
    s1 = json.loads(st1.grid_open_run("BTC-USDT")["state"])
    # gián đoạn: tick lúc đầu, rồi "tắt" 50 phút, chạy lại với tiến trình mới (đọc DB)
    shutil.rmtree(D + "2", ignore_errors=True); cfg.DATA_DIR = D + "2"
    st2 = Store()
    r2 = runner(Ex(DAILY, mins), st2, sent)
    r2.tick(now=T0 + 10)
    r2.tick(now=T0 + 60 * 8 + 10)
    r3 = runner(Ex(DAILY, mins), st2, sent)      # tiến trình mới: nạp vòng từ DB
    r3.tick(now=T0 + 60 * 60 + 10)
    s2 = json.loads(st2.grid_open_run("BTC-USDT")["state"])
    for k in ("usdt", "coin", "realized", "rounds", "fees", "bars", "last_bar", "max_dd"):
        assert abs(float(s1[k]) - float(s2[k])) < 1e-9, (k, s1[k], s2[k])
    assert len(st1.grid_fills(limit=1000)) == len(st2.grid_fills(limit=1000)) == 60
    id1, id2 = st1.grid_open_run("BTC-USDT")["id"], st2.grid_open_run("BTC-USDT")["id"]
    assert [e["value"] for e in st1.grid_equity(id1)] == [e["value"] for e in st2.grid_equity(id2)] and len(st1.grid_equity(id1)) == 4
    cfg.DATA_DIR = D


@section("dừng vì phá vùng: ghi ended_at + lý do, báo một tin; vòng mới chỉ mở khi GRID_ROUND tăng")
def _():
    shutil.rmtree(D + "3", ignore_errors=True); cfg.DATA_DIR = D + "3"
    st = Store()
    sent = []
    mins = [bar(0, 150, 151, 149, 150, T0), bar(1, 150, 215, 149, 212, T0), bar(2, 212, 213, 211, 212, T0)]
    ex = Ex(DAILY, mins)
    r = runner(ex, st, sent)
    r.tick(now=T0 + 10)
    r.tick(now=T0 + 60 * 3 + 10)
    runs = st.grid_runs("BTC-USDT")
    assert len(runs) == 1 and runs[0]["ended_at"] == T0 + 60 and "vượt đỉnh" in runs[0]["reason"]
    assert sum(1 for m in sent if "Grid BTC-USDT dừng" in m) == 1
    eq = st.grid_equity(runs[0]["id"])
    assert eq and eq[-1]["ts"] == T0 + 60           # dòng vốn lúc dừng
    r.tick(now=T0 + 60 * 10)
    assert len(st.grid_runs("BTC-USDT")) == 1 and st.grid_open_run("BTC-USDT") is None    # không tự mở vòng mới
    r2 = runner(ex, st, sent)                         # khởi động lại, GRID_ROUND vẫn 1
    r2.tick(now=T0 + 60 * 10)
    assert len(st.grid_runs("BTC-USDT")) == 1 and r2.snapshot()[0]["state"] == "đã dừng"
    cfg.GRID_ROUND = 2
    r3 = runner(ex, st, sent)
    r3.tick(now=T0 + 60 * 10)
    runs = st.grid_runs("BTC-USDT")
    assert len(runs) == 2 and runs[0]["round"] == 2 and runs[0]["ended_at"] is None
    cfg.GRID_ROUND = 1
    cfg.DATA_DIR = D


@section("tắt: GRID_ENABLED=false → không luồng, /grid báo tắt; bật → lệnh grid trả văn bản, snapshot có khối grid")
def _():
    from types import SimpleNamespace as NS
    from bot import main as M
    b = M.Bot.__new__(M.Bot)
    st = Store()
    b.store, b.grid = st, runner(Ex(DAILY, minutes(5)), st, [])
    cfg.GRID_ENABLED = False
    assert "đang tắt" in b.handle_command("grid")
    cfg.GRID_ENABLED = True
    assert b.handle_command("grid").startswith("🪙 GRID")
    r = G.GridRunner(OKX(Ex(DAILY, [])), st, lambda t: None)
    cfg.GRID_ENABLED = False
    assert r.start() is None
    cfg.GRID_ENABLED = True


assert not FAILED, f"{len(FAILED)} phần lỗi: {FAILED}"
print("TẤT CẢ OK")
