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
    last = 0.10
    def price(self, s): return self.last
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
live.okx.last = 0.1035
live._last_pass = time.time() - 200          # bot đã theo dõi liên tục (nến cũ lúc không theo dõi không chốt TP1)
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
# Đồng hồ của engine là đồng hồ GIẢ (CLK): kết quả không phụ thuộc giây đang chạy test (bản trước: ~8% lần chạy đỏ khi
# monitor() rơi vào 5 giây đầu phút – nến 1m vừa đóng chưa qua CLOSED_LAG).
import math
import bot.engine as E
from bot import costs, exits
from bot.engine import PaperEngine, fill_check
from bot.exchange import OKX
from bot.risk import risk_of
cfg.DATA_DIR = "/tmp/okx-engine-test/v33"
st3 = Store()
CUR = int(time.time() // 60) * 60                 # mốc: nến 1m thứ 60 (bar(60)) mở lúc CUR
SLIP, FEE = cfg.SLIPPAGE, cfg.TAKER_FEE
SYM = "A/USDT:USDT"


class Clock:
    def __init__(self): self.now = float(CUR + 30)
    def time(self): return self.now
    def sleep(self, s): pass


CLK = Clock()
E.time = CLK


def at(i, sec=30):
    """Đồng hồ ở giây `sec` của phút i (phút i = nến bar(i))."""
    CLK.now = float(CUR - 3600 + 60 * i + sec)


def watch(eng, frm, to, sec=30):
    """Bot chạy liên tục: một lượt kiểm tra mỗi phút, từ phút frm tới phút to."""
    for m in range(frm, to + 1):
        at(m, sec)
        eng.monitor()


def bar(i, o, h, l, c):
    """Nến 1m thứ i kể từ 60 phút trước (i = 60 là nến mở lúc CUR)."""
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
    """ccxt okx giả: lệnh (side, amount, params, id), lệnh SL/TP đang treo theo loại (algos), lịch sử khớp (trades).
    Như OKX: lệnh SL nằm sai phía giá last bị từ chối. fail_sl / fail_reduce: số lần tới lỗi mạng."""
    def __init__(self, okx, fill=None):
        self.okx, self.fill = okx, fill
        self.orders, self.cancels, self.trades = [], [], []
        self.algos = {"oco-1": "oco"}                  # SL/TP gắn kèm lệnh mở nằm ở loại oco
        self.fail_sl = self.fail_reduce = 0
        self.pos_error = None
    def create_order(self, sym, typ, side, amount, params=None):
        p = params or {}
        if "stopLossPrice" in p:
            if self.fail_sl:
                self.fail_sl -= 1
                raise Exception("RequestTimeout okx POST /api/v5/trade/order-algo")
            slp = float(p["stopLossPrice"])
            if (side == "sell" and slp >= self.okx.last) or (side == "buy" and slp <= self.okx.last):
                raise Exception("okx 51280 SL trigger price must be lower/higher than the last price")
        elif p.get("reduceOnly") and self.fail_reduce:
            self.fail_reduce -= 1
            raise Exception("RequestTimeout okx POST /api/v5/trade/order")
        oid = f"x{len(self.orders) + 1}"
        self.orders.append((side, amount, p, oid))
        if "stopLossPrice" in p:
            self.algos[oid] = "oco"                    # stopLossPrice + takeProfitPrice → ccxt gửi ordType oco
        return {"id": oid}
    def fetch_order(self, oid, sym): return {"average": self.fill}
    def fetch_open_orders(self, sym, params=None):
        k = (params or {}).get("ordType")
        return [{"id": i} for i, kind in self.algos.items() if kind == k]
    def cancel_order(self, oid, sym, params=None):
        self.cancels.append(oid)
        self.algos.pop(oid, None)
    def fetch_my_trades(self, sym, since=None, limit=None):
        return [x for x in self.trades if x["timestamp"] >= (since or 0)]
    def fetch_positions(self, params=None):
        if self.pos_error:
            raise Exception(self.pos_error)
        return [{"symbol": s, "contracts": 1} for s in self.okx.pos]
    def price_to_precision(self, s, x): return f"{x:.8f}"
    def amount_to_precision(self, s, x): return f"{x:.2f}"
    def set_leverage(self, *a, **k): pass


class Live33(Fake33):
    """Sàn live giả; fetch_positions / close_fill_price là code thật của exchange.OKX trên Ex33."""
    def __init__(self, last, fill, pos=None, **k):
        super().__init__(last, **k)
        self.ex = Ex33(self)            # lệnh market sau lệnh mở khớp ở giá last (ex.fill để ép giá khác)
        self.fill = fill
        self.pos = [SYM] if pos is None else list(pos)
        self.close_error = 0
    def balance_usdt(self): return 10.0, 10.0
    def open_position(self, s, side, contracts, sl, tp): return "oid33", self.fill
    def close_position(self, s, side, contracts):
        if self.close_error:
            self.close_error -= 1
            raise Exception("RequestTimeout okx POST /api/v5/trade/order")
        self.ex.orders.append(("close", contracts, {}, "c1"))
        return {"id": "c1"}
    fetch_positions = OKX.fetch_positions
    close_fill_price = OKX.close_fill_price


def reset(balance=10.0):
    st3.db.run("DELETE FROM trades"); st3.set("paper_balance", balance); msgs.clear()


def S(price=100.0, sl=98.0, atr=1.2, side="long", ctx=None, sym=SYM, tp=None):
    return Signal(sym, side, 6.0, price, atr, sl, tp or (106.0 if side == "long" else 94.0), ["t"], ctx or {"setup": "trend"})


def opened(eng, sig, i=0, sec=10, ai=None):
    """Mở lệnh ở giây `sec` của phút i."""
    at(i, sec)
    return eng.open(sig, ai or AI70)


def row_of(t):
    return [x for x in st3.trades(50) if x["id"] == t["id"]][0]


def orders(okx, kind):
    o = okx.ex.orders
    if kind == "sl":
        return [x for x in o if x[2].get("stopLossPrice")]
    if kind == "reduce":
        return [x for x in o if x[2].get("reduceOnly") and "stopLossPrice" not in x[2]]
    return [x for x in o if x[0] == "close"]


AI70 = {"confidence": 70}

# ---- Bug 1: paper vào ở giá last MỚI ± trượt giá (bản cũ: giá đóng nến 15m đã cũ), 1R/TP1 tính từ giá khớp
reset()
pe = PaperEngine(Fake33(100.6), st3, msgs.append)
sA = S()
tA = opened(pe, sA)
assert tA and abs(tA["entry"] - 100.6 * (1 + SLIP)) < 1e-9, ("paper phải khớp ở giá last mới + trượt giá", tA and tA["entry"])
assert tA["sig_price"] == 100.0 and tA["ref_price"] == 100.6 and sA.ref_price == 100.6
assert abs(tA["tp1"] - (tA["entry"] + exits.current_tp1_r() * (tA["entry"] - 98.0))) < 1e-9, "1R/TP1 từ giá khớp"
assert abs(tA["risk_plan"] - 10 * cfg.RISK_PER_TRADE) < 1e-9, "cỡ lệnh theo khoảng SL thật (từ giá khớp)"
assert risk_of(tA["notional"], tA["entry"], 98.0) <= tA["risk_plan"] + 1e-9         # lot làm tròn xuống
reset(); s_ = S()
assert PaperEngine(Fake33(97.9), st3, msgs.append).open(s_, AI70) is None and "vượt SL" in s_.skip, s_.__dict__
print("✓ bug 1: paper vào ở giá last mới ± trượt giá; giá khớp đã vượt SL → bỏ có lý do")

# ---- Review 13: cổng chiến lược ở giá mới (ngược >0.5R, SL >3 ATR, R:R range) chỉ chạy khi FILL_RECHECK=true
# Mặc định chỉ sửa lỗi: vào ở giá mới như lệnh live bản cũ; chỉ bỏ khi SL/TP cố định đã nằm sai phía giá khớp.
for last in (98.9, 101.9):
    reset(); s_ = S()
    assert opened(PaperEngine(Fake33(last), st3, msgs.append), s_), ("mặc định không thêm cổng chiến lược", last, s_.skip)
rs = S(sl=99.0, atr=1.0, ctx={"setup": "range"}, tp=101.5)
assert fill_check(rs, costs.entry_fill("long", 100.4), 100.4) == "", "R:R range thấp: cổng chiến lược, mặc định tắt"
assert "vượt TP" in fill_check(rs, costs.entry_fill("long", 101.6), 101.6), "range: giá đã vượt TP cố định → bỏ"
rss = S(sl=101.0, atr=1.0, side="short", ctx={"setup": "range"}, tp=98.5)
assert "vượt TP" in fill_check(rss, costs.entry_fill("short", 98.4), 98.4) and fill_check(rss, 99.9, 100.0) == ""
cfg.FILL_RECHECK = True
try:
    for last, want in ((98.9, "chạy ngược"), (101.9, "chạy xa")):
        reset(); s_ = S()
        assert PaperEngine(Fake33(last), st3, msgs.append).open(s_, AI70) is None and want in s_.skip, (last, s_.skip)
    reset()
    assert opened(PaperEngine(Fake33(99.2), st3, msgs.append), S()), "ngược 0.4R < MAX_FILL_DRIFT_R 0.5 → vẫn vào"
    assert fill_check(rs, costs.entry_fill("long", 100.4), 100.4).startswith("R:R")
    assert fill_check(rs, costs.entry_fill("long", 100.0), 100.0) == ""
    ss = S(sl=102.0, atr=1.5, side="short")                     # short: ngược = giá đi LÊN về phía SL
    assert "chạy ngược" in fill_check(ss, costs.entry_fill("short", 101.1), 101.1)
    assert fill_check(ss, costs.entry_fill("short", 98.9), 98.9) == ""
finally:
    cfg.FILL_RECHECK = False
print("✓ review 13: mặc định chỉ bỏ khi SL/TP cố định sai phía giá khớp; FILL_RECHECK=true bật lại các cổng chiến lược")

# ---- Bug 2: live tính cỡ lệnh theo giá mới (bản cũ: giá đóng nến cũ, SL cố định → rủi ro thật 1.5× kế hoạch)
reset()
lv = Live33(101.0, fill=101.0 * (1 + SLIP))
le = LiveEngine(lv, st3, msgs.append)
tL = opened(le, S())
assert tL and abs(tL["entry"] - 101.0 * (1 + SLIP)) < 1e-9
real = risk_of(tL["notional"], tL["entry"], tL["sl"])
assert real <= 10 * cfg.RISK_PER_TRADE * 1.001, ("rủi ro thật vượt kế hoạch", real)
assert abs(tL["risk_usdt"] - real) < 1e-9 and abs(tL["risk_mult"] - real / (10 * cfg.RISK_PER_TRADE)) < 1e-3
# khớp trượt xa hơn dự tính → rủi ro thật > kế hoạch 20% → cắt bớt hợp đồng, đặt lại SL/TP (huỷ cả oco gắn kèm), báo
reset()
lv = Live33(100.2, fill=100.9)
tL = opened(LiveEngine(lv, st3, msgs.append), S())
cut = orders(lv, "reduce")
assert tL and cut and cut[0][0] == "sell", lv.ex.orders
plan = risk_of(tL["notional"] / tL["contracts"] * (tL["contracts"] + cut[0][1]) * 100.25 / 100.9, 100.25, 98.0)
row = st3.open_trades()[0]
assert row["contracts"] == tL["contracts"] and risk_of(row["notional"], row["entry"], row["sl"]) <= plan * (1 + cfg.LIVE_RISK_TOL)
assert any(o[2].get("slTriggerPxType") == "last" for o in orders(lv, "sl")) and "oco-1" in lv.ex.cancels
assert any("đã cắt" in m for m in msgs), msgs
# giá khớp đã vượt SL → đóng ngay
reset()
lv = Live33(100.2, fill=97.5)
s_ = S()
assert opened(LiveEngine(lv, st3, msgs.append), s_) is None and not st3.open_trades() and s_.skip
assert orders(lv, "close") and any("vượt SL" in m for m in msgs)
# lot tối thiểu: không cắt được; vượt 2× kế hoạch → đóng, chưa vượt → giữ và báo; lot quá to từ đầu → bỏ như cũ
for fill, kept in ((100.9, True), (104.5, False)):
    reset()
    lv = Live33(100.2, fill=fill, ct=0.1, lot=1)
    t_ = opened(LiveEngine(lv, st3, msgs.append), S())
    assert bool(t_) == kept and bool(st3.open_trades()) == kept, (fill, t_, msgs)
reset(); s_ = S()
assert LiveEngine(Live33(100.2, fill=100.25, ct=1, lot=1), st3, msgs.append).open(s_, AI70) is None and "lot tối thiểu" in s_.skip
# SL/TP gắn kèm lệnh mở kích hoạt theo giá last (cùng giá paper/mô phỏng bóng dùng)
ok = OKX.__new__(OKX); ok.ex = Ex33(Fake33(100.0), fill=100.0)
_sleep, time.sleep = time.sleep, (lambda s: None)
try:
    OKX.open_position(ok, SYM, "long", 1, 98.0, 110.0)
finally:
    time.sleep = _sleep
algo = ok.ex.orders[0][2]["attachAlgoOrds"][0]
assert algo["slTriggerPxType"] == "last" and algo["tpTriggerPxType"] == "last", algo
print("✓ bug 2: live cỡ lệnh theo giá mới; khớp xấu → cắt về kế hoạch / đóng; SL/TP sàn theo giá last")

# ---- Review 5: _check_fill – trần là min(kế hoạch × 1.2, MIN_LOT_RISK_MULT × dự tính); dưới 1 lot thì giữ 1 lot; lệnh cắt lỗi
# lot tối thiểu (kế hoạch đã ~1.9× dự tính): khớp làm rủi ro 2.12× dự tính → đóng (bản cũ: giữ vì chưa vượt kế hoạch × 1.2)
reset()
lv = Live33(100.2, fill=100.5, ct=0.24, lot=1)
s_ = S()
t_ = opened(LiveEngine(lv, st3, msgs.append), s_)
assert t_ is None and not st3.open_trades() and orders(lv, "close"), ("vượt luật lot tối thiểu 2× phải đóng", msgs)
# 3 lot, khớp xấu 3.2× kế hoạch: làm tròn ra 0 lot – giữ 1 lot (1.05× kế hoạch) thay vì đóng cả (bản cũ đóng cả)
reset()
lv = Live33(100.2, fill=105.425, ct=0.04, lot=1)
t_ = opened(LiveEngine(lv, st3, msgs.append), S())
row = st3.open_trades()
assert t_ and row and row[0]["contracts"] == 1 and [o[1] for o in orders(lv, "reduce")] == [2], (lv.ex.orders, msgs)
assert risk_of(row[0]["notional"], row[0]["entry"], 98.0) <= cfg.MIN_LOT_RISK_MULT * row[0]["risk_plan"]
# lệnh cắt bớt lỗi: không văng lỗi khỏi open() sau khi DB đã ghi – báo, vẫn trong 2× → giữ
reset()
lv = Live33(100.2, fill=100.9)
lv.ex.fail_reduce = 1
t_ = opened(LiveEngine(lv, st3, msgs.append), S())
assert t_ and st3.open_trades() and any("lỗi" in m for m in msgs), msgs
print("✓ review 5: live khớp xấu – trần gồm luật lot tối thiểu; giữ 1 lot khi làm tròn ra 0; lệnh cắt lỗi được báo")

# ---- nến 1m kể từ lần kiểm tra trước: OKX trả tối đa 300 nến/lần → lật trang
class PagEx:
    def parse_timeframe(self, tf): return 60
    def fetch_ohlcv(self, s, tf, since=None, limit=None):
        end = CUR * 1000
        return [[t, 1, 1, 1, 1, 1] for t in range(since, min(end, since + limit * 60000) + 1, 60000)]


pg = OKX.__new__(OKX); pg.ex = PagEx()
got = OKX.ohlcv_since(pg, SYM, "1m", CUR - 700 * 60)
assert len(got) == 701 and all(b[0] - a[0] == 60000 for a, b in zip(got, got[1:])), len(got)


def paper_trade(candles, last=100.2, i=0, side="long", **k):
    """Lệnh paper mở ở giây 10 của phút i; bot kiểm tra ngay phút sau (bot đang chạy)."""
    reset()
    okx = Fake33(last, candles=candles, **k)
    eng = PaperEngine(okx, st3, msgs.append)
    t = opened(eng, S() if side == "long" else S(sl=102.0, side="short"), i)
    at(i + 1); eng.monitor()
    return eng, t


# ---- Bug 3a: paper đọc MỌI nến 1m đã đóng từ lần kiểm tra trước (bản cũ chỉ đọc nến đang chạy → sót SL lúc 05:39)
eng, t = paper_trade([flat(0), flat(1), bar(2, 100.2, 100.3, 97.9, 99.0)] + [flat(i, 100.4) for i in range(3, 61)])
at(5); eng.monitor()                                      # lượt sau cách 4 phút (vòng lặp bận quét, gọi AI)
row = row_of(t)
assert row["status"] == "closed" and row["reason_close"] == "Chạm SL", ("râu chạm SL giữa 2 lần kiểm tra bị bỏ sót", row)
assert abs(row["exit"] - 98.0 * (1 - SLIP)) < 1e-9

# ---- Bug 3b: nến mở cửa đã vượt SL (giá nhảy cóc) → khớp quanh giá mở, không phải đúng mức SL
eng, t = paper_trade([flat(0), flat(1), bar(2, 97.5, 97.8, 97.2, 97.6)] + [flat(i) for i in range(3, 61)])
at(5); eng.monitor()
row = row_of(t)
assert row["status"] == "closed" and abs(row["exit"] - 97.5 * (1 - SLIP)) < 1e-9, row

# ---- Bug 3c: cùng một nến chạm cả TP1 và SL → SL trước (thận trọng, như backtest)
eng, t = paper_trade([flat(0), flat(1), bar(2, 100.2, 103.5, 97.9, 100)] + [flat(i) for i in range(3, 61)])
at(5); eng.monitor()
row = row_of(t)
assert row["status"] == "closed" and not row["tp1_done"] and row["reason_close"] == "Chạm SL", row

# ---- Bug 3d: TP1 ở phút 2, quay về hoà vốn ở phút 4 – cả hai giữa 2 lần kiểm tra; nến đang chạy không được dùng
eng, t = paper_trade([])
tp1, be = t["tp1"], exits.breakeven("long", t["entry"])
eng.okx.candles = [flat(0), flat(1), bar(2, 100.3, tp1 + 0.05, 100.2, tp1 - 0.1), flat(3, tp1 - 0.2),
                   bar(4, tp1 - 0.2, tp1 - 0.1, be - 0.05, be + 0.1), flat(5, be + 0.2), bar(6, be, be, 90.0, 95.0)]
at(6); eng.monitor()                                      # nến 6 (đang chạy) sập về 90 – chưa đóng, không dùng
row = row_of(t)
assert row["status"] == "closed" and row["tp1_done"] == 1 and row["reason_close"].startswith("Trailing"), row
assert abs(row["exit"] - be * (1 - SLIP)) < 1e-9, (row["exit"], be)

# ---- Bug 3e: chỉ xét mỗi nến một lần: lần kiểm tra sau không đọc lại nến đã xét
eng, t = paper_trade([flat(i) for i in range(0, 61)])
watch(eng, 2, 60)
row = st3.open_trades()[0]
assert row["bar_ts"] == CUR - 60, (row["bar_ts"], CUR - 60)
eng.okx.candles = [bar(5, 100, 100, 90, 95)] + [flat(i) for i in range(59, 61)]     # nến cũ đã xét: không đọc lại
at(60, 50); eng.monitor()
assert st3.open_trades(), "nến đã xét không được xét lại"

# ---- Bug 3f: lệnh mở từ bản cũ (chưa có bar_ts / risk_usdt), đã chốt TP1, SL ở hoà vốn: không đọc lại nến cũ bằng SL đã dời
eng, t = paper_trade([flat(i) for i in range(0, 61)])
be = exits.breakeven("long", t["entry"])
st3.update_trade(t["id"], risk_usdt=None, tp1_done=1, sl=be)
eng = PaperEngine(eng.okx, st3, msgs.append)             # bản mới vừa khởi động
eng.okx.candles = [bar(2, 100, 100, 99, 99.5)] + [flat(i, be + 0.3) for i in range(3, 61)]
at(60); eng.monitor()
assert st3.open_trades() and st3.open_trades()[0]["bar_ts"] == CUR - 60, "lệnh bản cũ bị đóng nhầm bằng nến cũ"

# ---- Review 7: nến 1m chỉ coi là đã đóng sau CLOSED_LAG giây (đồng hồ giả – không còn phụ thuộc giây chạy test)
eng, t = paper_trade([flat(i) for i in range(0, 61)], i=57)
at(60, E.CLOSED_LAG - 1); eng.monitor()
assert st3.open_trades()[0]["bar_ts"] == CUR - 120, "nến 59 chưa qua CLOSED_LAG – chưa được xét"
at(60, E.CLOSED_LAG); eng.monitor()
assert st3.open_trades()[0]["bar_ts"] == CUR - 60
print("✓ bug 3: paper đọc mọi nến 1m đã đóng từ lần kiểm tra trước; giá nhảy cóc qua SL; SL trước TP1 trong cùng nến")


def live_trade(last=100.2, side="long", fill=None, **k):
    """Lệnh live mở ở phút 0; bot kiểm tra ở phút 1 (bot đang chạy)."""
    reset()
    lv = Live33(last, fill=fill or costs.entry_fill(side, last), **k)
    le = LiveEngine(lv, st3, msgs.append)
    t = opened(le, S() if side == "long" else S(sl=102.0, side="short"))
    at(1); le.monitor()
    lv.ex.orders.clear(); lv.ex.cancels.clear()
    return lv, le, t


# ---- Bug 3 (live): TP1/trailing theo cùng nến 1m giá last như paper (bản cũ: giá mark lúc kiểm tra)
lv, le, t = live_trade()
lv.candles = [flat(0), flat(1), bar(2, 100.3, t["tp1"] + 0.05, 100.2, t["tp1"] - 0.1)] + \
             [flat(i, t["tp1"] - 0.3) for i in range(3, 61)]
lv.last = t["tp1"] - 0.3
at(4); le.monitor()
row = st3.open_trades()[0]
assert row["tp1_done"] == 1 and row["sl"] > 98.0, ("live phải thấy TP1 trên nến 1m giá last giữa 2 lần kiểm tra", row)
assert orders(lv, "reduce") and orders(lv, "sl")
# SL vừa dời (hoà vốn) mà đã bị chạm ở nến sau trong cùng lượt – sàn chưa có SL đó → bot tự đóng
lv, le, t = live_trade()
be = exits.breakeven("long", t["entry"])
lv.candles = [flat(0), flat(1), bar(2, 100.3, t["tp1"] + 0.05, 100.2, t["tp1"] - 0.1),
              bar(3, t["tp1"] - 0.1, t["tp1"] - 0.1, be - 0.05, be + 0.05)] + [flat(i, be + 0.05) for i in range(4, 61)]
lv.last = be + 0.05
at(5); le.monitor()
row = row_of(t)
assert row["status"] == "closed" and "(bot)" in row["reason_close"], row
# SL gốc (đang nằm trên sàn) bị chạm → để sàn khớp, không đóng hai lần
lv, le, t = live_trade()
lv.candles = [flat(0), flat(1), bar(2, 100.2, 100.3, 97.9, 98.5)] + [flat(i, 98.5) for i in range(3, 61)]
lv.last = 98.5
at(4); le.monitor()
assert st3.open_trades() and not orders(lv, "close")
print("✓ bug 3 (live): TP1/trailing theo nến 1m giá last như paper; SL vừa dời bị chạm → bot đóng; SL gốc → sàn khớp")

# ---- Review 1: SL bot muốn mà sàn chưa nhận (từ chối / lỗi mạng) không bị bỏ: bot tự giữ và thử đặt lại mỗi lượt
# (a) nến chạm TP1 rồi đóng DƯỚI hoà vốn; mỗi lượt một nến; sàn từ chối SL bán ≥ giá last → bot đóng phần còn lại
#     bằng lệnh market (bản cũ: bỏ việc dời SL, lượt nào cũng dời lại rồi bị từ chối, lệnh không bao giờ đóng)
lv, le, t = live_trade()
be = exits.breakeven("long", t["entry"])
lv.candles = [flat(0), flat(1), bar(2, 100.3, t["tp1"] + 0.05, 100.0, be - 0.4)]
lv.last = be - 0.4
at(3); le.monitor()
for i in range(3, 8):
    if row_of(t)["status"] == "closed":
        break
    lv.candles.append(flat(i, be - 0.5)); lv.last = be - 0.5
    at(i + 1); le.monitor()
row = row_of(t)
assert row["status"] == "closed" and "(bot)" in row["reason_close"] and orders(lv, "close"), (row, lv.ex.orders)
# (b) fixed: SL dời theo giá đóng nến; một lần lỗi mạng không làm mất SL mới – lượt sau đặt lại được
cfg.EXIT_MODE = "fixed"
try:
    lv, le, t = live_trade()
    risk0 = t["entry"] - 98.0
    want = max(exits.breakeven("long", t["entry"]), 103.0 - risk0)
    lv.candles = [flat(0), flat(1), bar(2, 100.3, 103.1, 100.2, 103.0), flat(3, 103.0)]
    lv.last, lv.ex.fail_sl = 103.0, 1
    at(3); le.monitor()
    row = st3.open_trades()[0]
    assert abs(row["sl"] - want) < 1e-9 and row["sl_exch"] == 98.0, ("SL muốn phải được giữ trong DB", row["sl"], row["sl_exch"])
    assert any("sàn chưa nhận SL" in m for m in msgs), msgs
    at(4); le.monitor()
    row = st3.open_trades()[0]
    assert abs(row["sl_exch"] - want) < 1e-9 and [float(o[2]["stopLossPrice"]) for o in orders(lv, "sl")] == \
        [float(f"{want:.8f}")], (row, lv.ex.orders)
    # (c) sàn vẫn chưa có SL mới; nến sau chạm SL mới (giá last đã hồi lên) → bot đóng, không chờ SL cũ 98 trên sàn
    lv, le, t = live_trade()
    lv.candles = [flat(0), flat(1), bar(2, 100.3, 103.1, 100.2, 103.0)]
    lv.last, lv.ex.fail_sl = 103.0, 99
    at(3); le.monitor()
    lv.candles.append(bar(3, 103.0, 103.0, want - 0.05, 101.5)); lv.last = 101.5
    at(4); le.monitor()
    row = row_of(t)
    assert row["status"] == "closed" and "(bot)" in row["reason_close"] and abs(row["exit"] - 101.5) < 1e-9, row
finally:
    cfg.EXIT_MODE = "auto"
print("✓ review 1: SL dời mà sàn chưa nhận – giữ trong DB, giá đã vượt thì đóng market, lỗi thoáng qua thì đặt lại lượt sau")

# ---- Review 2: sàn khớp SL sau khi bot đã cắt bớt hợp đồng – giá đóng không lẫn lệnh cắt (PnL phần cắt đã ở realized)
reset()
lv = Live33(100.2, fill=100.9)
le = LiveEngine(lv, st3, msgs.append)
t = opened(le, S())
cut = orders(lv, "reduce")[0]
rest = st3.open_trades()[0]
ms = int((t["opened_at"] + 1) * 1000)
lv.ex.trades = [{"order": cut[3], "side": "sell", "price": 100.9, "amount": cut[1], "timestamp": ms},
                {"order": "sl-1", "side": "sell", "price": 98.0, "amount": rest["contracts"], "timestamp": ms + 60000}]
lv.pos = []
at(5); le.monitor()
row = row_of(t)
pnl_rest = le._pnl("long", rest["entry"], 98.0, rest["notional"])[0]
assert row["status"] == "closed" and abs(row["exit"] - 98.0) < 1e-9, ("giá đóng lẫn lệnh cắt", row["exit"])
assert abs(row["pnl"] - (rest["realized"] + pnl_rest)) < 1e-9
print("✓ review 2: giá khớp đóng của sàn không tính lẫn lệnh bot tự cắt bớt / chốt TP1")

# ---- Review 3: đọc vị thế lỗi không làm bot ghi đóng mọi lệnh; lệnh đóng lỗi thì lệnh vẫn mở và được thử lại
ok = OKX.__new__(OKX); ok.ex = Ex33(Fake33(100.0)); ok.ex.pos_error = "RequestTimeout okx GET /api/v5/account/positions"
try:
    OKX.fetch_positions(ok)
    raise AssertionError("fetch_positions lỗi phải ném lỗi (bản cũ trả [] → coi như sàn đã đóng mọi lệnh)")
except AssertionError:
    raise
except Exception:
    pass
lv, le, t = live_trade()
lv.ex.pos_error = "RequestTimeout okx GET /api/v5/account/positions"
at(3); le.monitor()
assert st3.open_trades() and not lv.ex.orders, "lỗi đọc vị thế → không đối chiếu lượt này"
# sàn không còn vị thế, chưa thấy lệnh khớp đóng: chờ thêm một lượt xác nhận rồi mới ước lượng
lv.ex.pos_error, lv.pos = None, []
at(4); le.monitor()
assert st3.open_trades(), "một lần không thấy vị thế (chưa có lệnh khớp) chưa đủ để ghi đóng"
at(5); le.monitor()
row = row_of(t)
assert row["status"] == "closed" and "ước lượng" in row["reason_close"], row
# lệnh đóng lỗi: lệnh vẫn mở (close_req), báo một lần; lượt sau đóng lại với đúng lý do
lv, le, t = live_trade()
lv.close_error = 2
assert le.close(t, 100.3, "Đóng thủ công qua Zoom") is False
row = row_of(t)
assert row["status"] == "open" and row["close_req"] == "Đóng thủ công qua Zoom" and any("thất bại" in m for m in msgs)
at(3); le.monitor()
assert row_of(t)["status"] == "open" and sum("thất bại" in m for m in msgs) == 1
at(4); le.monitor()
row = row_of(t)
assert row["status"] == "closed" and row["reason_close"] == "Đóng thủ công qua Zoom" and orders(lv, "close"), row
print("✓ review 3: lỗi đọc vị thế → bỏ lượt; mất vị thế không có lệnh khớp → chờ xác nhận; lệnh đóng lỗi → giữ mở, thử lại")

# ---- Review 4/9: bot đóng lệnh thì huỷ MỌI SL/TP còn treo – cả loại oco (SL/TP gắn kèm lệnh mở, lệnh dời SL)
ok = OKX.__new__(OKX); ok.ex = Ex33(Fake33(100.0))
ok.ex.algos = {"c-1": "conditional", "att-1": "oco"}
OKX.close_position(ok, SYM, "long", 1)
assert ok.ex.algos == {} and sorted(ok.ex.cancels) == ["att-1", "c-1"], (ok.ex.algos, ok.ex.cancels)
ok.ex.algos = {"att-2": "oco"}; ok.ex.fail_reduce = 1
try:
    OKX.close_position(ok, SYM, "long", 1)
    raise AssertionError("lệnh đóng lỗi phải ném lỗi")
except AssertionError:
    raise
except Exception:
    pass
assert ok.ex.algos == {"att-2": "oco"}, "lệnh đóng lỗi thì không được huỷ SL (vị thế còn mở)"
print("✓ review 4/9: đóng vị thế huỷ cả SL/TP loại conditional lẫn oco; đóng lỗi thì giữ SL")

# ---- Review 8: sau thời gian bot không theo dõi (khởi động lại, mất leader), nến cũ không sinh lệnh market theo giá cũ
# TP1 chạm lúc bot tắt (phút 20), giờ giá 98.7 (SL 98 trên sàn chưa chạm): live không bán TP1 dưới giá vào rồi đóng nốt ở
# SL hoà vốn sàn chưa từng có; paper làm y như vậy (chỉ SL/TP đang đặt mới khớp được lúc bot tắt).
def gap_path(tp1):
    return [flat(i) for i in range(0, 20)] + [bar(20, 100.3, tp1 + 0.05, 100.2, tp1 - 0.1)] + \
           [flat(i, 99.5) for i in range(21, 40)] + [flat(i, 98.7) for i in range(40, 61)]


lv, le, t = live_trade()
lv.candles = gap_path(t["tp1"]); lv.last = 98.7
le2 = LiveEngine(lv, st3, msgs.append)                  # tiến trình mới
at(55); le2.monitor()
row = st3.open_trades()[0]
assert not lv.ex.orders and row["tp1_done"] == 0 and row["sl"] == 98.0 and row["bar_ts"] == CUR - 3600 + 60 * 54, \
    ("không được chốt TP1 / đóng theo nến của lúc bot tắt", lv.ex.orders, row)
watch(le2, 56, 58)
assert st3.open_trades() and not lv.ex.orders
eng, t = paper_trade([])
eng.okx.candles = gap_path(t["tp1"])
eng = PaperEngine(eng.okx, st3, msgs.append)
at(55); eng.monitor()
row = st3.open_trades()[0]
assert row["tp1_done"] == 0 and row["realized"] == 0, ("paper phải như live: không chốt TP1 lúc bot tắt", row)
# lúc bot tắt giá chạm SL đang đặt (sàn tự khớp) → paper đóng ở SL như sàn
eng, t = paper_trade([])
eng.okx.candles = [flat(i) for i in range(0, 30)] + [bar(30, 99.0, 99.1, 97.9, 98.2)] + [flat(i, 99.0) for i in range(31, 61)]
eng = PaperEngine(eng.okx, st3, msgs.append)
at(55); eng.monitor()
row = row_of(t)
assert row["status"] == "closed" and abs(row["exit"] - 98.0 * (1 - SLIP)) < 1e-9, row
# vòng lặp chỉ bận (lượt trước cách ≤ MAX_PASS_GAP): nến từ lượt trước vẫn được xét đủ, báo giá khớp thật của TP1
lv, le, t = live_trade()
lv.candles = [flat(0), flat(1), bar(2, 100.3, t["tp1"] + 0.05, 100.2, t["tp1"] - 0.1)] + \
             [flat(i, t["tp1"] - 0.2) for i in range(3, 10)]
lv.last, lv.ex.fill = t["tp1"] - 0.2, round(t["tp1"] - 0.15, 4)
at(8); le.monitor()                                      # lượt trước ở phút 1 – cách 7 phút
row = st3.open_trades()[0]
msg = [m for m in msgs if m.startswith("💰")]
assert row["tp1_done"] == 1 and msg and f"{lv.ex.fill:.6g}" in msg[0] and f"{t['tp1']:.6g}" not in msg[0], (row, msg)
print("✓ review 8: nến lúc bot không theo dõi chỉ khớp SL/TP đang đặt (paper = live), không lệnh market theo giá cũ; "
      "tin CHỐT ghi giá khớp thật")

# ---- Review 10: SL trong DB do bản khác dời (quay lui bản) – không đọc lại nến cũ từ bar_ts bằng SL đó
eng, t = paper_trade([flat(i) for i in range(0, 10)])
watch(eng, 2, 9)
row = st3.open_trades()[0]
assert row["bar_ts"] == CUR - 3600 + 60 * 8, row
be = exits.breakeven("long", t["entry"])
st3.update_trade(t["id"], tp1_done=1, sl=be, peak=t["tp1"] + 0.1)        # bản cũ chốt TP1 ở phút 60, dời SL hoà vốn
eng.okx.candles = [flat(i) for i in range(0, 30)] + [bar(30, 100.2, 100.3, 99.95, 100.1)] + \
                  [flat(i, 102.1) for i in range(31, 61)]
eng = PaperEngine(eng.okx, st3, msgs.append)
at(60); eng.monitor()
row = row_of(t)
assert row["status"] == "open" and row["bar_ts"] == CUR - 60, ("đóng nhầm bằng nến lúc SL còn 98", row)
print("✓ review 10: SL do bản khác dời (bar_sl ≠ sl) → chỉ xét từ nến vừa đóng")

# ---- Review 12: phía short (gương của các ca long)
# paper short: SL sai phía giá khớp → bỏ; râu chạm SL; nến mở vượt SL → khớp ở giá mở
reset(); s_ = S(sl=102.0, side="short")
assert PaperEngine(Fake33(102.1), st3, msgs.append).open(s_, AI70) is None and "vượt SL" in s_.skip
eng, t = paper_trade([flat(0), flat(1), bar(2, 100.2, 102.1, 100.1, 101.0)] + [flat(i, 100.0) for i in range(3, 61)],
                     side="short")
assert abs(t["entry"] - 100.2 * (1 - SLIP)) < 1e-9
at(5); eng.monitor()
row = row_of(t)
assert row["status"] == "closed" and abs(row["exit"] - 102.0 * (1 + SLIP)) < 1e-9, row
eng, t = paper_trade([flat(0), flat(1), bar(2, 102.5, 102.8, 102.2, 102.6)] + [flat(i) for i in range(3, 61)], side="short")
at(5); eng.monitor()
row = row_of(t)
assert row["status"] == "closed" and abs(row["exit"] - 102.5 * (1 + SLIP)) < 1e-9, row
# live short: khớp xấu (thấp hơn) → cắt bằng lệnh MUA
reset()
lv = Live33(100.2, fill=99.5)
t_ = opened(LiveEngine(lv, st3, msgs.append), S(sl=102.0, side="short"))
cut = orders(lv, "reduce")
assert t_ and cut and cut[0][0] == "buy", lv.ex.orders
# live short: TP1 rồi nến sau chạm SL hoà vốn (sàn chưa có) → bot đóng; SL gốc 102 trên sàn bị chạm → sàn khớp
lv, le, t = live_trade(side="short")
be = exits.breakeven("short", t["entry"])
lv.candles = [flat(0), flat(1), bar(2, 100.1, 100.2, t["tp1"] - 0.05, t["tp1"] + 0.1),
              bar(3, t["tp1"] + 0.1, be + 0.05, t["tp1"] + 0.1, be - 0.05)] + [flat(i, be - 0.05) for i in range(4, 61)]
lv.last = be - 0.05
at(5); le.monitor()
row = row_of(t)
assert row["status"] == "closed" and "(bot)" in row["reason_close"] and orders(lv, "reduce")[0][0] == "buy", row
lv, le, t = live_trade(side="short")
lv.candles = [flat(0), flat(1), bar(2, 100.2, 102.1, 100.1, 101.5)] + [flat(i, 101.5) for i in range(3, 61)]
lv.last = 101.5
at(4); le.monitor()
assert st3.open_trades() and not orders(lv, "close")
# live short: SL muốn (hoà vốn) mà giá last đã vượt lên trên → đóng bằng lệnh market
lv, le, t = live_trade(side="short")
be = exits.breakeven("short", t["entry"])
lv.candles = [flat(0), flat(1), bar(2, 100.1, be + 0.5, t["tp1"] - 0.05, be + 0.4)]
lv.last = be + 0.4
at(3); le.monitor()
row = row_of(t)
assert row["status"] == "closed" and "(bot)" in row["reason_close"], row
print("✓ review 12: phía short – SL sai phía, râu/nhảy cóc qua SL, cắt bằng lệnh mua, bot đóng ở SL sàn chưa có")

# ---- Bug 5: chốt TP1 làm tròn theo lot của sàn (bản cũ paper: 5 → 2.5 hợp đồng), cả paper lẫn live
def tp1_path(t):
    return [flat(0, 1.002), flat(1, 1.002), bar(2, 1.003, t["tp1"] * 1.001, 1.002, t["tp1"])] + \
           [flat(i, t["tp1"]) for i in range(3, 61)]


reset()
okx = Fake33(1.002, ct=1, lot=1)
eng = PaperEngine(okx, st3, msgs.append)
t = opened(eng, S(1.0, 0.98, 0.012))
n0 = t["contracts"]
assert n0 == int(n0) and n0 >= 3, n0
okx.candles = tp1_path(t)
watch(eng, 1, 4)
row = st3.open_trades()[0]
assert row["contracts"] == n0 - math.floor(n0 * cfg.TP1_FRACTION) and row["contracts"] == int(row["contracts"]), row["contracts"]
assert abs(row["notional"] - t["notional0"] * row["contracts"] / n0) < 1e-9
# 1 hợp đồng: không chia được → không chốt, cả vị thế bám sóng với SL hoà vốn (không báo CHỐT lặp lại)
reset()
okx = Fake33(1.002, ct=10, lot=1)
eng = PaperEngine(okx, st3, msgs.append)
t = opened(eng, S(1.0, 0.98, 0.012))
assert t["contracts"] == 1
okx.candles = tp1_path(t)
watch(eng, 1, 5)
row = st3.open_trades()[0]
assert row["contracts"] == 1 and row["tp1_done"] == 1 and row["sl"] > 0.98 and row["realized"] == 0
assert sum("không chia" in m for m in msgs) == 1, msgs
# live: lệnh chốt đúng bước lot của sàn; 1 hợp đồng → không gửi lệnh chốt (bản cũ gửi 0.5)
for ct, want in ((1, None), (10, 0)):
    reset()
    lv = Live33(1.002, fill=1.002 * (1 + SLIP), ct=ct, lot=1)
    le = LiveEngine(lv, st3, msgs.append)
    t = opened(le, S(1.0, 0.98, 0.012))
    n0 = t["contracts"]
    lv.ex.orders.clear()
    pnl, part = le.partial(t, t["tp1"])[:2]
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
t = opened(eng, S())
N, E0, n0 = t["notional"], t["entry"], t["contracts"]
okx.candles = [flat(0), flat(1), bar(2, 100.3, t["tp1"] + 0.05, 100.2, t["tp1"])] + [flat(i, t["tp1"]) for i in range(3, 61)]
watch(eng, 1, 4)
part = n0 - st3.open_trades()[0]["contracts"]
eng.close(st3.open_trades()[0], 101.0, "Đóng thủ công")
row = st3.trades(5)[0]
f = part / n0
x1, x2 = t["tp1"] * (1 - SLIP), 101.0 * (1 - SLIP)
want = FEE * (N * f + N * f * x1 / E0 + N * (1 - f) + N * (1 - f) * x2 / E0)
assert abs(row["fee"] - want) < 1e-9, ("phí phải gồm cả phần đã chốt ở TP1", row["fee"], want)
assert abs(row["risk_mult"] - row["risk_usdt"] / (10 * cfg.RISK_PER_TRADE)) < 1e-3 and row["mult_plan"] == 1.0
print("✓ cột fee gồm phí hai chiều của mọi phần; risk_usdt / risk_mult = rủi ro thật")
print("TẤT CẢ OK")
