"""Kiểm tra PaperEngine: mở lệnh giả, ép chạm TP/SL, kiểm tra số dư cập nhật đúng."""
import os, shutil
os.environ["MODE"] = "paper"; os.environ["DATA_DIR"] = "/tmp/okx-engine-test"; os.environ["AI_ENABLED"] = "false"
shutil.rmtree("/tmp/okx-engine-test", ignore_errors=True)
from bot.storage import Store
from bot.engine import PaperEngine
from bot.strategy import Signal
from bot.config import cfg

class FakeOKX:
    candles = []                     # nến 1m giá last [ts_ms, o, h, l, c, v]
    def market(self, s): return {"contractSize": 1000, "limits": {"amount": {"min": 0.01}}, "precision": {"amount": 0.01}}
    def contracts_for_notional(self, s, notional, price):
        c = int(notional / (price * 1000) / 0.01) * 0.01
        return (round(c, 2), 0.0) if c >= 0.01 else (0.0, 0.01 * 1000 * price)
    def price(self, s): return 0.10              # v3.3: giá last lúc vào lệnh
    def ohlcv_since(self, s, tf, since): return [c for c in self.candles if c[0] >= since * 1000]

msgs = []
st = Store(); eng = PaperEngine(FakeOKX(), st, msgs.append)
sig = Signal("DOGE/USDT:USDT", "long", 4.0, 0.10, 0.002, 0.097, 0.106, ["test"], {})
t = eng.open(sig, {"model": "test", "confidence": 80, "reason": "ok"})
assert t and t["notional"] <= 10 * cfg.LEVERAGE * 0.9, t
print("Mở:", t["notional"], "USDT, ký quỹ", round(t["margin"], 3))
eng.close(t, 0.106, "Chạm TP")
bal = eng.balance()
print("Sau TP, số dư:", round(bal, 4))
assert bal > 10
sig2 = Signal("DOGE/USDT:USDT", "short", 4.0, 0.10, 0.002, 0.103, 0.094, ["test"], {})
t2 = eng.open(sig2, None); eng.close(t2, 0.103, "Chạm SL")
print("Sau SL, số dư:", round(eng.balance(), 4), "| lỗ lệnh ≈", round(bal - eng.balance(), 4), "(mục tiêu ≈", round(bal * cfg.RISK_PER_TRADE, 4), ")")
print("Stats:", st.stats())
print("\n".join(msgs))

# Vùng găng (v3.2.1): SIGTERM (Botkeep khởi động lại) rơi giữa lúc đặt lệnh trên sàn và lúc ghi DB
# không được bỏ dở bước ghi DB; KeyboardInterrupt tới sau khi ra khỏi vùng.
import signal, subprocess, sys, time
from bot.engine import LiveEngine


def _term_here():
    os.kill(os.getpid(), signal.SIGTERM)
    time.sleep(0.2)          # bản cũ: KeyboardInterrupt ngay đây, lệnh đã khớp mà DB không biết


class FakeEx:
    orders = []
    def amount_to_precision(self, s, x): return f"{x:.2f}"
    def price_to_precision(self, s, x): return f"{x:.6f}"
    def create_order(self, *a, params=None): self.orders.append(params); _term_here(); return {"id": f"o{len(self.orders)}"}
    def fetch_order(self, *a): return {"average": 0.104}
    def fetch_open_orders(self, *a, **k): return []


class FakeLive(FakeOKX):
    ex = FakeEx()
    def balance_usdt(self): return 100.0, 100.0
    def open_position(self, s, side, contracts, sl, tp): _term_here(); return "oid-1", 0.1
    def close_position(self, s, side, contracts): _term_here()
    def fetch_positions(self): return [{"symbol": sig.symbol, "markPrice": 0.1035}]


def interrupted(fn, *a):
    try:
        fn(*a)
    except KeyboardInterrupt:
        return True
    return False


term = lambda *_: (_ for _ in ()).throw(KeyboardInterrupt())     # như bot.main.main()
signal.signal(signal.SIGTERM, term)
live = LiveEngine(FakeLive(), st, msgs.append)
msgs.clear()
assert interrupted(live.open, sig, {"model": "test", "confidence": 80, "reason": "ok"}), "SIGTERM bị nuốt"
row = [x for x in st.open_trades() if x["order_id"] == "oid-1"]
assert row, "lệnh đã khớp trên sàn mà DB không có"
assert any(m.startswith("🟢 MỞ LỆNH") for m in msgs), "lệnh đã mở mà không báo"
assert signal.getsignal(signal.SIGTERM) is term and signal.getsignal(signal.SIGINT) is signal.default_int_handler
t3 = row[0]
assert interrupted(live.partial, t3, 0.104)
assert [x for x in st.open_trades() if x["id"] == t3["id"]][0]["tp1_done"] == 1, "chốt một phần mà DB chưa ghi"
assert interrupted(live.close, t3, 0.105, "Đóng thủ công qua Zoom")
assert not [x for x in st.open_trades() if x["id"] == t3["id"]], "đóng trên sàn mà DB vẫn mở"

# monitor chạm TP1: SIGTERM lúc chốt một phần không bỏ dở dời SL trên sàn, ghi DB, báo tin
t5 = live._trade_row(sig, None, 0.1, 50.0, 0.5, order_id="oid-5")
t5.update(exit_mode="hybrid", tp1=0.103, tp=0.106, opened_at=time.time() - 300)
t5["id"] = st.open_trade(t5)
_m = int(time.time() // 60) * 60
FakeOKX.candles = [[(_m - 180) * 1000, 0.1, 0.1036, 0.0999, 0.1035, 1]]     # v3.3: TP1 theo nến 1m giá last đã đóng
FakeEx.orders.clear(); msgs.clear()
assert interrupted(live.monitor)
row = [x for x in st.open_trades() if x["id"] == t5["id"]][0]
assert row["tp1_done"] == 1 and row["sl"] > t5["sl"], ("SL chưa dời trong DB", row)
assert any(o and "stopLossPrice" in o for o in FakeEx.orders), ("chưa dời SL trên sàn", FakeEx.orders)
assert any(m.startswith("💰 CHỐT") for m in msgs), msgs
assert interrupted(live.close, row, 0.104, "dọn")
print("Vùng găng: SIGTERM giữa open/partial/close/monitor TP1 – DB, SL trên sàn, tin báo đủ; KeyboardInterrupt tới sau")

# /close chạy ở luồng khác (lệnh /alt close, threadpool web là luồng daemon): SIGTERM làm luồng chính thoát,
# tiến trình chờ đóng HẾT các lệnh rồi mới tắt (không chỉ lệnh đang đóng).
code = r'''
import os, signal, threading, time, types
from bot.storage import Store
from bot.engine import LiveEngine
from bot.main import Bot
class Slow:
    def balance_usdt(self): return 100.0, 100.0
    def price(self, s): time.sleep(0.2); return 0.1
    def close_position(self, *a): time.sleep(1.0)
st = Store()
bot = types.SimpleNamespace(store=st, okx=Slow())
bot.engine = LiveEngine(bot.okx, st, print)
signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
threading.Thread(target=Bot.close_all, args=(bot, "Đóng thủ công qua Zoom"), daemon=True).start()
time.sleep(0.5)
try:
    os.kill(os.getpid(), signal.SIGTERM)
    time.sleep(5)
except KeyboardInterrupt:
    print("thoát")
'''
ids = []
for oid in ("oid-2", "oid-3"):
    t4 = live._trade_row(sig, None, 0.1, 50.0, 0.5, order_id=oid)
    ids.append(st.open_trade(t4))
done = subprocess.run([sys.executable, "-c", code], cwd=os.path.dirname(os.path.abspath(__file__)),
                      capture_output=True, text=True, timeout=60)
assert "thoát" in done.stdout, done.stdout + done.stderr
closed = [x for x in st.trades(20) if x["id"] in ids]
assert len(closed) == 2 and all(x["status"] == "closed" and x["reason_close"] == "Đóng thủ công qua Zoom" for x in closed), \
    (closed, done.stderr)
print("Vùng găng: luồng khác đang đóng 2 lệnh – tiến trình chờ đóng hết mới thoát")


# ============================== v3.3: paper phản chiếu live (giá vào, rủi ro live, nến 1m, lot TP1, phí) ==============
import math
from bot import costs, exits
from bot.engine import PaperEngine, fill_check
from bot.risk import risk_of
cfg.DATA_DIR = "/tmp/okx-engine-test/v33"
st3 = Store()
CUR = int(time.time() // 60) * 60                 # nến 1m đang chạy
SLIP, FEE = cfg.SLIPPAGE, cfg.TAKER_FEE


def bar(i, o, h, l, c):
    """Nến 1m thứ i kể từ 60 phút trước (i = 60 là nến đang chạy)."""
    return [(CUR - 3600 + 60 * i) * 1000, o, h, l, c, 1]


def flat(i, p=100.2):
    return bar(i, p, p * 1.001, p * 0.999, p)


class Fake33:
    """Sàn giả: lot/contractSize theo market, giá last, nến 1m giá last."""
    def __init__(self, last, ct=1.0, lot=0.01, candles=()):
        self.last, self.ct, self.lot, self.candles = last, ct, lot, list(candles)
    def market(self, s): return {"contractSize": self.ct, "limits": {"amount": {"min": self.lot}}, "precision": {"amount": self.lot}}
    def contracts_for_notional(self, s, notional, price):
        c = math.floor(notional / (price * self.ct) / self.lot + 1e-9) * self.lot
        return (round(c, 8), 0.0) if c >= self.lot else (0.0, self.lot * self.ct * price)
    def price(self, s): return self.last
    def ohlcv_since(self, s, tf, since): return [c for c in self.candles if c[0] >= since * 1000]
    def ohlcv(self, s, tf, limit=2): return self.candles[-limit:]          # bản cũ chỉ đọc nến 1m đang chạy


class Ex33:
    def __init__(self, fill=None): self.orders, self.cancels, self.fill = [], [], fill
    def create_order(self, sym, typ, side, amount, params=None):
        self.orders.append((side, amount, params or {})); return {"id": f"x{len(self.orders)}"}
    def fetch_order(self, oid, sym): return {"average": self.fill}
    def fetch_open_orders(self, sym, params=None):          # SL/TP gắn kèm lệnh mở nằm ở loại oco
        return [{"id": "oco-1"}] if (params or {}).get("ordType") == "oco" else []
    def cancel_order(self, oid, sym, params=None): self.cancels.append(oid)
    def price_to_precision(self, s, x): return f"{x:.8f}"
    def amount_to_precision(self, s, x): return f"{x:.2f}"
    def set_leverage(self, *a, **k): pass


class Live33(Fake33):
    def __init__(self, last, fill, pos=(), mark=None, **k):
        super().__init__(last, **k); self.ex = Ex33(fill); self.fill = fill; self.pos = pos; self.mark = mark
    def balance_usdt(self): return 10.0, 10.0
    def open_position(self, s, side, contracts, sl, tp): return "oid33", self.fill
    def close_position(self, s, side, contracts): self.ex.orders.append(("close", contracts, {})); return {"id": "c1"}
    def fetch_positions(self): return [{"symbol": s, "markPrice": self.mark} for s in self.pos]


def reset(balance=10.0):
    st3.db.run("DELETE FROM trades"); st3.set("paper_balance", balance); msgs.clear()


def S(price=100.0, sl=98.0, atr=1.2, side="long", ctx=None, sym="A/USDT:USDT"):
    return Signal(sym, side, 6.0, price, atr, sl, 106.0 if side == "long" else 94.0, ["t"], ctx or {"setup": "trend"})


def at_minute(t, i, sec=10):
    """Dời giờ vào lệnh về phút thứ i (để có nến 1m đã đóng sau đó)."""
    t["opened_at"] = CUR - 3600 + 60 * i + sec
    st3.update_trade(t["id"], opened_at=t["opened_at"])


AI70 = {"confidence": 70}

# ---- Bug 1: paper vào ở giá last MỚI ± trượt giá (bản cũ: giá đóng nến 15m đã cũ), 1R/TP1 tính từ giá khớp
reset()
pe = PaperEngine(Fake33(100.6), st3, msgs.append)
sA = S()
tA = pe.open(sA, AI70)
assert tA and abs(tA["entry"] - 100.6 * (1 + SLIP)) < 1e-9, ("paper phải khớp ở giá last mới + trượt giá", tA and tA["entry"])
assert tA["sig_price"] == 100.0 and tA["ref_price"] == 100.6 and sA.ref_price == 100.6
assert abs(tA["tp1"] - (tA["entry"] + exits.current_tp1_r() * (tA["entry"] - 98.0))) < 1e-9, "1R/TP1 từ giá khớp"
assert abs(tA["risk_plan"] - 10 * cfg.RISK_PER_TRADE) < 1e-9, "cỡ lệnh theo khoảng SL thật (từ giá khớp)"
assert risk_of(tA["notional"], tA["entry"], 98.0) <= tA["risk_plan"] + 1e-9         # lot làm tròn xuống
# giá đã chạy quá xa → bỏ, có lý do (không vào ở giá cũ như bản cũ)
for last, want in ((97.9, "vượt SL"), (98.9, "chạy ngược"), (101.9, "chạy xa")):
    reset(); s_ = S()
    assert pe.__class__(Fake33(last), st3, msgs.append).open(s_, AI70) is None and want in s_.skip, (last, s_.skip)
reset(); s_ = S()
assert PaperEngine(Fake33(99.2), st3, msgs.append).open(s_, AI70), "ngược 0.4R < MAX_FILL_DRIFT_R 0.5 → vẫn vào"
rs = S(sl=99.0, atr=1.0, ctx={"setup": "range"}); rs.tp = 101.5
assert fill_check(rs, costs.entry_fill("long", 100.4), 100.4).startswith("R:R"), "range: R:R tới dải giữa từ giá hiện tại"
assert fill_check(rs, costs.entry_fill("long", 100.0), 100.0) == ""
print("✓ bug 1: paper vào ở giá last mới ± trượt giá; SL sai phía / ngược >0.5R / SL >3 ATR / R:R range → bỏ có lý do")

# ---- Bug 2: live tính cỡ lệnh theo giá mới (bản cũ: giá đóng nến cũ, SL cố định → rủi ro thật 1.5× kế hoạch)
reset()
lv = Live33(101.0, fill=101.0 * (1 + SLIP))
le = LiveEngine(lv, st3, msgs.append)
tL = le.open(S(), AI70)
assert tL and abs(tL["entry"] - 101.0 * (1 + SLIP)) < 1e-9
real = risk_of(tL["notional"], tL["entry"], tL["sl"])
assert real <= 10 * cfg.RISK_PER_TRADE * 1.001, ("rủi ro thật vượt kế hoạch", real)
assert abs(tL["risk_usdt"] - real) < 1e-9 and abs(tL["risk_mult"] - real / (10 * cfg.RISK_PER_TRADE)) < 1e-3
# khớp trượt xa hơn dự tính → rủi ro thật > kế hoạch 20% → cắt bớt hợp đồng, đặt lại SL/TP (huỷ cả oco gắn kèm), báo
reset()
lv = Live33(100.2, fill=100.9)
tL = LiveEngine(lv, st3, msgs.append).open(S(), AI70)
cut = [o for o in lv.ex.orders if o[2].get("reduceOnly") and "stopLossPrice" not in o[2]]
assert tL and cut and cut[0][0] == "sell", lv.ex.orders
plan = risk_of(tL["notional"] / tL["contracts"] * (tL["contracts"] + cut[0][1]) * 100.25 / 100.9, 100.25, 98.0)
row = st3.open_trades()[0]
assert row["contracts"] == tL["contracts"] and risk_of(row["notional"], row["entry"], row["sl"]) <= plan * (1 + cfg.LIVE_RISK_TOL)
assert any("stopLossPrice" in o[2] and o[2].get("slTriggerPxType") == "last" for o in lv.ex.orders) and "oco-1" in lv.ex.cancels
assert any("đã cắt" in m for m in msgs), msgs
# giá khớp đã vượt SL → đóng ngay
reset()
lv = Live33(100.2, fill=97.5)
s_ = S()
assert LiveEngine(lv, st3, msgs.append).open(s_, AI70) is None and not st3.open_trades() and s_.skip
assert any(o[0] == "close" for o in lv.ex.orders) and any("vượt SL" in m for m in msgs)
# lot tối thiểu: không cắt được; vượt 2× kế hoạch → đóng, chưa vượt → giữ và báo; lot quá to từ đầu → bỏ như cũ
for fill, kept in ((100.9, True), (104.5, False)):
    reset()
    lv = Live33(100.2, fill=fill, ct=0.1, lot=1)
    t_ = LiveEngine(lv, st3, msgs.append).open(S(), AI70)
    assert bool(t_) == kept and bool(st3.open_trades()) == kept, (fill, t_, msgs)
reset(); s_ = S()
assert LiveEngine(Live33(100.2, fill=100.25, ct=1, lot=1), st3, msgs.append).open(s_, AI70) is None and "lot tối thiểu" in s_.skip
# SL/TP gắn kèm lệnh mở kích hoạt theo giá last (cùng giá paper/mô phỏng bóng dùng)
from bot.exchange import OKX
ok = OKX.__new__(OKX); ok.ex = Ex33(fill=100.0)
_sleep, time.sleep = time.sleep, (lambda s: None)
try:
    OKX.open_position(ok, "A/USDT:USDT", "long", 1, 98.0, 110.0)
finally:
    time.sleep = _sleep
algo = ok.ex.orders[0][2]["attachAlgoOrds"][0]
assert algo["slTriggerPxType"] == "last" and algo["tpTriggerPxType"] == "last", algo
print("✓ bug 2: live cỡ lệnh theo giá mới; khớp xấu → cắt về kế hoạch / đóng; SL/TP sàn theo giá last")

# ---- nến 1m kể từ lần kiểm tra trước: OKX trả tối đa 300 nến/lần → lật trang
class PagEx:
    def parse_timeframe(self, tf): return 60
    def fetch_ohlcv(self, s, tf, since=None, limit=None):
        end = CUR * 1000
        return [[t, 1, 1, 1, 1, 1] for t in range(since, min(end, since + limit * 60000) + 1, 60000)]
from bot.exchange import OKX
pg = OKX.__new__(OKX); pg.ex = PagEx()
got = OKX.ohlcv_since(pg, "A/USDT:USDT", "1m", CUR - 700 * 60)
assert len(got) == 701 and all(b[0] - a[0] == 60000 for a, b in zip(got, got[1:])), len(got)

# ---- Bug 3: paper đọc MỌI nến 1m đã đóng từ lần kiểm tra trước (bản cũ chỉ đọc nến đang chạy → sót SL lúc 05:39)
def paper_trade(candles, last=100.2, i=0, **k):
    reset()
    okx = Fake33(last, candles=candles, **k)
    eng = PaperEngine(okx, st3, msgs.append)
    t = eng.open(S(), AI70)
    at_minute(t, i)
    return eng, t


eng, t = paper_trade([flat(i) for i in range(0, 61)])
risk = t["entry"] - 98.0
path = [flat(0), flat(1), bar(2, 100.2, 100.3, 97.9, 99.0)] + [flat(i, 100.4) for i in range(3, 61)]
eng.okx.candles = path
eng.monitor()
row = st3.trades(5)[0]
assert row["status"] == "closed" and row["reason_close"] == "Chạm SL", ("râu chạm SL giữa 2 lần kiểm tra bị bỏ sót", row)
assert abs(row["exit"] - 98.0 * (1 - SLIP)) < 1e-9
# nến mở cửa đã vượt SL (giá nhảy cóc) → khớp quanh giá mở, không phải đúng mức SL
eng, t = paper_trade([flat(0), flat(1), bar(2, 97.5, 97.8, 97.2, 97.6)] + [flat(i) for i in range(3, 61)])
eng.monitor()
row = st3.trades(5)[0]
assert row["status"] == "closed" and abs(row["exit"] - 97.5 * (1 - SLIP)) < 1e-9, row
# cùng một nến chạm cả TP1 và SL → SL trước (thận trọng, như backtest)
eng, t = paper_trade([flat(0), flat(1), bar(2, 100.2, 103.5, 97.9, 100)] + [flat(i) for i in range(3, 61)])
eng.monitor()
row = st3.trades(5)[0]
assert row["status"] == "closed" and not row["tp1_done"] and row["reason_close"] == "Chạm SL", row
# TP1 ở phút 2, quay về hoà vốn ở phút 4 – cả hai giữa 2 lần kiểm tra; nến đang chạy (phút 60) không được dùng
eng, t = paper_trade([])
tp1 = t["tp1"]
be = exits.breakeven("long", t["entry"])
eng.okx.candles = [flat(0), flat(1), bar(2, 100.3, tp1 + 0.05, 100.2, tp1 - 0.1), flat(3, tp1 - 0.2),
                   bar(4, tp1 - 0.2, tp1 - 0.1, be - 0.05, be + 0.1)] + [flat(i, be + 0.2) for i in range(5, 60)] + \
                  [bar(60, be, be, 90.0, 95.0)]
eng.monitor()
row = st3.trades(5)[0]
assert row["status"] == "closed" and row["tp1_done"] == 1 and row["reason_close"].startswith("Trailing"), row
assert abs(row["exit"] - be * (1 - SLIP)) < 1e-9, (row["exit"], be)
# chỉ xét mỗi nến một lần: lần kiểm tra sau không đọc lại nến đã xét
eng, t = paper_trade([flat(i) for i in range(0, 61)])
eng.monitor()
row = st3.open_trades()[0]
assert row["bar_ts"] == CUR - 60, row["bar_ts"]
eng.okx.candles = [bar(5, 100, 100, 90, 95)] + [flat(i) for i in range(59, 61)]     # nến cũ đã xét: không đọc lại
eng.monitor()
assert st3.open_trades(), "nến đã xét không được xét lại"
# lệnh mở từ bản cũ (chưa có bar_ts / risk_usdt), đã chốt TP1, SL ở hoà vốn: không đọc lại nến cũ bằng SL đã dời
eng, t = paper_trade([flat(i) for i in range(0, 61)])
be = exits.breakeven("long", t["entry"])
st3.update_trade(t["id"], risk_usdt=None, tp1_done=1, sl=be)
eng.okx.candles = [bar(2, 100, 100, 99, 99.5)] + [flat(i, be + 0.3) for i in range(3, 61)]
eng.monitor()
assert st3.open_trades() and st3.open_trades()[0]["bar_ts"] == CUR - 60, "lệnh bản cũ bị đóng nhầm bằng nến cũ"
print("✓ bug 3: paper đọc mọi nến 1m đã đóng từ lần kiểm tra trước; giá nhảy cóc qua SL; SL trước TP1 trong cùng nến")

# ---- Bug 3 (live): TP1/trailing theo cùng nến 1m giá last như paper (bản cũ: giá mark lúc kiểm tra)
reset()
lv = Live33(100.2, fill=100.2 * (1 + SLIP), pos=("A/USDT:USDT",), mark=100.5)
le = LiveEngine(lv, st3, msgs.append)
t = le.open(S(), AI70)
at_minute(t, 0)
lv.candles = [flat(0), flat(1), bar(2, 100.3, t["tp1"] + 0.05, 100.2, t["tp1"] - 0.1)] + \
             [flat(i, t["tp1"] - 0.3) for i in range(3, 61)]
lv.ex.orders.clear(); lv.ex.cancels.clear()
le.monitor()
row = st3.open_trades()[0]
assert row["tp1_done"] == 1 and row["sl"] > 98.0, ("live phải thấy TP1 trên nến 1m giá last giữa 2 lần kiểm tra", row)
assert any(o[2].get("reduceOnly") and "stopLossPrice" not in o[2] for o in lv.ex.orders), lv.ex.orders
assert any(o[2].get("stopLossPrice") for o in lv.ex.orders)
# SL vừa dời (hoà vốn) trong cùng lượt mà đã bị chạm – sàn chưa có SL đó → bot tự đóng
reset()
lv = Live33(100.2, fill=100.2 * (1 + SLIP), pos=("A/USDT:USDT",), mark=100.5)
le = LiveEngine(lv, st3, msgs.append)
t = le.open(S(), AI70)
at_minute(t, 0)
be = exits.breakeven("long", t["entry"])
lv.candles = [flat(0), flat(1), bar(2, 100.3, t["tp1"] + 0.05, 100.2, t["tp1"] - 0.1),
              bar(3, t["tp1"] - 0.1, t["tp1"] - 0.1, be - 0.05, be + 0.05)] + [flat(i) for i in range(4, 61)]
le.monitor()
row = st3.trades(5)[0]
assert row["status"] == "closed" and "(bot)" in row["reason_close"], row
# SL gốc (đang nằm trên sàn) bị chạm → để sàn khớp, không đóng hai lần
reset()
lv = Live33(100.2, fill=100.2 * (1 + SLIP), pos=("A/USDT:USDT",), mark=100.5)
le = LiveEngine(lv, st3, msgs.append)
t = le.open(S(), AI70)
at_minute(t, 0)
lv.candles = [flat(0), flat(1), bar(2, 100.2, 100.3, 97.9, 98.5)] + [flat(i) for i in range(3, 61)]
lv.ex.orders.clear()
le.monitor()
assert st3.open_trades() and not any(o[0] == "close" for o in lv.ex.orders)
print("✓ bug 3 (live): TP1/trailing theo nến 1m giá last như paper; SL vừa dời bị chạm → bot đóng; SL gốc → sàn khớp")

# ---- Bug 5: chốt TP1 làm tròn theo lot của sàn (bản cũ paper: 5 → 2.5 hợp đồng), cả paper lẫn live
def tp1_path(t):
    return [flat(0, 1.002), flat(1, 1.002), bar(2, 1.003, t["tp1"] * 1.001, 1.002, t["tp1"])] + \
           [flat(i, t["tp1"]) for i in range(3, 61)]


reset()
okx = Fake33(1.002, ct=1, lot=1)
eng = PaperEngine(okx, st3, msgs.append)
t = eng.open(S(1.0, 0.98, 0.012), AI70)
n0 = t["contracts"]
assert n0 == int(n0) and n0 >= 3, n0
at_minute(t, 0); okx.candles = tp1_path(t)
eng.monitor()
row = st3.open_trades()[0]
assert row["contracts"] == n0 - math.floor(n0 * cfg.TP1_FRACTION) and row["contracts"] == int(row["contracts"]), row["contracts"]
assert abs(row["notional"] - t["notional0"] * row["contracts"] / n0) < 1e-9
# 1 hợp đồng: không chia được → không chốt, cả vị thế bám sóng với SL hoà vốn (không báo CHỐT lặp lại)
reset()
okx = Fake33(1.002, ct=10, lot=1)
eng = PaperEngine(okx, st3, msgs.append)
t = eng.open(S(1.0, 0.98, 0.012), AI70)
assert t["contracts"] == 1
at_minute(t, 0); okx.candles = tp1_path(t)
eng.monitor(); eng.monitor()
row = st3.open_trades()[0]
assert row["contracts"] == 1 and row["tp1_done"] == 1 and row["sl"] > 0.98 and row["realized"] == 0
assert sum("không chia" in m for m in msgs) == 1, msgs
# live: lệnh chốt đúng bước lot của sàn; 1 hợp đồng → không gửi lệnh chốt (bản cũ gửi 0.5)
for ct, want in ((1, None), (10, 0)):
    reset()
    lv = Live33(1.002, fill=1.002 * (1 + SLIP), pos=("A/USDT:USDT",), ct=ct, lot=1)
    le = LiveEngine(lv, st3, msgs.append)
    t = le.open(S(1.0, 0.98, 0.012), AI70)
    n0 = t["contracts"]
    lv.ex.orders.clear()
    pnl, part = le.partial(t, t["tp1"])
    sent = [o[1] for o in lv.ex.orders]
    if want is None:
        assert n0 >= 3 and sent == [math.floor(n0 * cfg.TP1_FRACTION)] == [part], (n0, sent, part)
    else:
        assert sent == [] and part == 0 and st3.open_trades()[0]["tp1_done"] == 1
print("✓ bug 5: chốt TP1 làm tròn xuống theo lot (paper & live); 1 hợp đồng → giữ toàn bộ bám sóng")

# ---- Cột fee = phí cả hai chiều của mọi phần (bản cũ: thiếu phần đã chốt ở TP1); risk_mult = rủi ro thật
reset()
okx = Fake33(100.2, candles=[])
eng = PaperEngine(okx, st3, msgs.append)
t = eng.open(S(), AI70)
N, E, n0 = t["notional"], t["entry"], t["contracts"]
at_minute(t, 0)
okx.candles = [flat(0), flat(1), bar(2, 100.3, t["tp1"] + 0.05, 100.2, t["tp1"])] + [flat(i, t["tp1"]) for i in range(3, 61)]
eng.monitor()
part = n0 - st3.open_trades()[0]["contracts"]
eng.close(st3.open_trades()[0], 101.0, "Đóng thủ công")
row = st3.trades(5)[0]
f = part / n0
x1, x2 = t["tp1"] * (1 - SLIP), 101.0 * (1 - SLIP)
want = FEE * (N * f + N * f * x1 / E + N * (1 - f) + N * (1 - f) * x2 / E)
assert abs(row["fee"] - want) < 1e-9, ("phí phải gồm cả phần đã chốt ở TP1", row["fee"], want)
assert abs(row["risk_mult"] - row["risk_usdt"] / (10 * cfg.RISK_PER_TRADE)) < 1e-3 and row["mult_plan"] == 1.0
print("✓ cột fee gồm phí hai chiều của mọi phần; risk_usdt / risk_mult = rủi ro thật")
print("TẤT CẢ OK")
