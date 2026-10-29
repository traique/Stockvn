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
    def amount_to_precision(self, s, x): return f"{x:.2f}"
    def create_order(self, *a, **k): _term_here(); return {"id": "tp1"}
    def fetch_order(self, *a): return {"average": 0.104}


class FakeLive(FakeOKX):
    ex = FakeEx()
    def balance_usdt(self): return 100.0, 100.0
    def open_position(self, s, side, contracts, sl, tp): _term_here(); return "oid-1", 0.1
    def close_position(self, s, side, contracts): _term_here()


def interrupted(fn, *a):
    try:
        fn(*a)
    except KeyboardInterrupt:
        return True
    return False


term = lambda *_: (_ for _ in ()).throw(KeyboardInterrupt())     # như bot.main.main()
signal.signal(signal.SIGTERM, term)
live = LiveEngine(FakeLive(), st, msgs.append)
assert interrupted(live.open, sig, {"model": "test", "confidence": 80, "reason": "ok"}), "SIGTERM bị nuốt"
row = [x for x in st.open_trades() if x["order_id"] == "oid-1"]
assert row, "lệnh đã khớp trên sàn mà DB không có"
assert signal.getsignal(signal.SIGTERM) is term and signal.getsignal(signal.SIGINT) is signal.default_int_handler
t3 = row[0]
assert interrupted(live.partial, t3, 0.104)
assert [x for x in st.open_trades() if x["id"] == t3["id"]][0]["tp1_done"] == 1, "chốt một phần mà DB chưa ghi"
assert interrupted(live.close, t3, 0.105, "Đóng thủ công qua Zoom")
assert not [x for x in st.open_trades() if x["id"] == t3["id"]], "đóng trên sàn mà DB vẫn mở"
print("Vùng găng: SIGTERM giữa open/partial/close – DB đã ghi, KeyboardInterrupt tới sau")

# /close chạy ở luồng khác (lệnh /alt close, threadpool web là luồng daemon): SIGTERM làm luồng chính thoát,
# tiến trình chờ lệnh đóng xong rồi mới tắt.
code = r'''
import os, signal, threading, time
from bot.storage import Store
from bot.engine import LiveEngine
class Slow:
    def balance_usdt(self): return 100.0, 100.0
    def close_position(self, *a): time.sleep(1.0)
st = Store()
t = [x for x in st.open_trades() if x["order_id"] == "oid-2"][0]
signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
threading.Thread(target=LiveEngine(Slow(), st, print).close, args=(t, 0.1, "Đóng thủ công qua Zoom"), daemon=True).start()
time.sleep(0.3)
try:
    os.kill(os.getpid(), signal.SIGTERM)
    time.sleep(5)
except KeyboardInterrupt:
    print("thoát")
'''
t4 = live._trade_row(sig, None, 0.1, 50.0, 0.5, order_id="oid-2")
t4["id"] = st.open_trade(t4)
done = subprocess.run([sys.executable, "-c", code], cwd=os.path.dirname(os.path.abspath(__file__)),
                      capture_output=True, text=True, timeout=60)
assert "thoát" in done.stdout, done.stdout + done.stderr
closed = [x for x in st.trades(20) if x["id"] == t4["id"]][0]
assert closed["status"] == "closed" and closed["reason_close"] == "Đóng thủ công qua Zoom", (closed, done.stderr)
print("Vùng găng: luồng khác đang đóng lệnh – tiến trình chờ xong mới thoát")
