"""Kiểm tra PaperEngine: mở lệnh giả, ép chạm TP/SL, kiểm tra số dư cập nhật đúng."""
import os, shutil
os.environ["MODE"] = "paper"; os.environ["DATA_DIR"] = "/tmp/okx-engine-test"; os.environ["AI_ENABLED"] = "false"
shutil.rmtree("/tmp/okx-engine-test", ignore_errors=True)
from bot.storage import Store
from bot.engine import PaperEngine
from bot.strategy import Signal
from bot.config import cfg

class FakeOKX:
    def market(self, s): return {"contractSize": 1000, "limits": {"amount": {"min": 0.01}}, "precision": {"amount": 0.01}}
    def contracts_for_notional(self, s, notional, price):
        c = int(notional / (price * 1000) / 0.01) * 0.01
        return (round(c, 2), 0.0) if c >= 0.01 else (0.0, 0.01 * 1000 * price)

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
t5.update(exit_mode="hybrid", tp1=0.103, tp=0.106)
t5["id"] = st.open_trade(t5)
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
