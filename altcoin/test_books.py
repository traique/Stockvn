"""v3.5 – sổ (book): cột book, sổ "không AI" chạy ảo song song (books.py), so sánh sổ, mặc định rủi ro khi live.

Chạy riêng từng phần (như test_strategy phần B): lỗi một phần không che phần sau.
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import traceback
from types import SimpleNamespace as NS

D = "/tmp/okx_test_books"
shutil.rmtree(D, ignore_errors=True)
os.makedirs(D)
os.environ.update(MODE="paper", DATA_DIR=D, AI_ENABLED="false", DATABASE_URL="", NOAI_ENABLED="true")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
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


# ---------------------------------------------------------------- 1) migration: DB cũ không có cột book
@section("migration: DB cũ (không cột book) – dữ liệu cũ đọc là sổ main, số liệu y hệt; equity cũ cũng là main")
def _():
    db = os.path.join(D, "bot.db")
    c = sqlite3.connect(db)
    c.executescript("""CREATE TABLE trades(id INTEGER PRIMARY KEY AUTOINCREMENT, mode TEXT, symbol TEXT, side TEXT,
        entry REAL, exit REAL, sl REAL, tp REAL, notional REAL, margin REAL, contracts REAL, pnl REAL, pnl_pct REAL, fee REAL,
        opened_at REAL, closed_at REAL, status TEXT, reason_open TEXT, reason_close TEXT, ai_model TEXT, ai_conf INTEGER,
        score REAL, order_id TEXT);
        CREATE TABLE equity(ts REAL, equity REAL, mode TEXT);
        CREATE TABLE kv(k TEXT PRIMARY KEY, v TEXT);
        INSERT INTO trades(mode,symbol,side,entry,exit,sl,tp,notional,margin,contracts,pnl,pnl_pct,fee,opened_at,closed_at,status)
          VALUES('paper','A/USDT:USDT','long',1,1.1,0.9,1.2,10,1,1,0.5,5,0.02,1000,2000,'closed');
        INSERT INTO trades(mode,symbol,side,entry,exit,sl,tp,notional,margin,contracts,pnl,pnl_pct,fee,opened_at,closed_at,status)
          VALUES('paper','B/USDT:USDT','short',1,NULL,1.1,0.8,10,1,1,0,0,0,3000,NULL,'open');
        INSERT INTO equity VALUES(1000,10,'paper'); INSERT INTO equity VALUES(2000,10.5,'paper');
        INSERT INTO kv VALUES('paper_balance','10.5');""")
    c.commit(); c.close()
    from bot.storage import Store
    st = Store()
    cols = [r["name"] for r in st.db.run("PRAGMA table_info(trades)", fetch=True)]
    assert "book" in cols and "sid" in cols and "ai_verdict" in cols, cols
    assert [t["symbol"] for t in st.open_trades()] == ["B/USDT:USDT"]
    assert st.open_trades("noai") == [] and st.stats()["trades"] == 1 and st.stats("noai")["trades"] == 0
    assert st.stats()["total_pnl"] == 0.5 and len(st.equity_curve()) == 2 and st.equity_curve(book="noai") == []
    assert st.last_closed(5)[0]["symbol"] == "A/USDT:USDT" and st.closed_trades_since(0)[0]["pnl"] == 0.5
    st.log_equity(11.0, "noai")
    assert [e for _, e in st.equity_curve()] == [10, 10.5] and [e for _, e in st.equity_curve(book="noai")] == [11.0]
    # lệnh mới không ghi book → main (test cũ, bản cũ)
    tid = st.open_trade(dict(mode="paper", symbol="C/USDT:USDT", side="long", entry=1, sl=0.9, tp=1.2, notional=5,
                             status="open", opened_at=time.time()))
    assert st.db.run("SELECT book FROM trades WHERE id=?", (tid,), fetch=True)[0]["book"] == "main"
    assert len(st.open_trades()) == 2 and st.open_trades("noai") == []
    st.db.run("DELETE FROM trades"); st.db.run("DELETE FROM equity")


from bot.config import cfg
from bot.storage import Store
from bot.engine import PaperEngine
from bot import strategy as S
from bot import main as M
from bot import books

STORE = Store()
STORE.set("paper_balance", 10.0)


class ScanOKX:
    def __init__(self, syms, price=100.0, prices=None): self.syms, self.p, self.asked, self.seq = syms, price, 0, list(prices or [])
    def top_altcoins(self, n): return [(s, 1e6 * (len(self.syms) - i), 0.0) for i, s in enumerate(self.syms)]
    def ohlcv(self, *a, **k): return []
    def ohlcv_since(self, *a, **k): return []
    def seconds(self, *a, **k): return []
    def price(self, s):
        self.asked += 1
        return self.seq.pop(0) if self.seq else self.p
    def prices(self, syms): return {s: self.p for s in syms}
    def market(self, s): return {"contractSize": 1, "limits": {"amount": {"min": 0.001}}, "precision": {"amount": 0.001}}
    def contracts_for_notional(self, s, notional, price):
        c = int(notional / price / 0.001) * 0.001
        return (round(c, 3), 0.0) if c >= 0.001 else (0.0, 0.001 * price)
    class ex:
        def __getattr__(self, k): raise RuntimeError("không có mạng / không được gọi API riêng")
    ex = ex()


class FakeAI:
    def __init__(self, decision=None, conf=80): self.asked, self.decision, self.conf = [], decision, conf
    def analyze(self, sig):
        self.asked.append(sig.symbol)
        return {"decision": self.decision or sig.side.upper(), "confidence": self.conf, "model": "fake", "reason": "ok"}


CANDLE = int(time.time() // 900) * 900 - 900


def trend_sig(sym, score, side="long"):
    c = {"setup": "trend", "hour": 21, "adx": 30, "vol_ratio": 1.1, "atr_pct": 0.6}
    g = S.Signal(sym, side, score, 100.0, 0.6, 98.8 if side == "long" else 101.2, 103.6 if side == "long" else 96.4,
                 ["t"], c, triggers=["pullback"])
    g.candle_ts = CANDLE
    return g


SENT = []


def make_bot(results, ai=None, noai=True, okx=None):
    for tb in ("signals", "scans", "trades", "equity"):
        STORE.db.run(f"DELETE FROM {tb}")
    STORE.set("paper_balance", 10.0); STORE.set("paper_balance_noai", 10.0)
    STORE.set("noai_halted", False); STORE.set("noai_peak_equity", None)
    b = M.Bot.__new__(M.Bot)
    b.store, b.okx = STORE, okx or ScanOKX(list(results))
    b.ai = ai or FakeAI()
    b.engine = PaperEngine(b.okx, STORE, SENT.append)
    b.noai = PaperEngine(b.okx, STORE, SENT.append, book="noai", quiet=True, capital=10.0) if noai else None
    b.notify = NS(send=SENT.append)
    b.cooldown, b.shadow_seen, b.last_btc, b.last_scan_summary, b.last_prices = {}, {}, {}, None, {}
    b.paused, b.period, b.start_equity = False, {"ts": time.time() - 60, "equity": 10.0, "noai_equity": 10.0}, 10.0
    M.diagnose = lambda sym, *a, **k: (results[sym][0], results[sym][1], {"score": results[sym][0].score if results[sym][0] else 0})
    M.mkt.btc_context = lambda okx: {}
    M.mkt.symbol_context = lambda okx, sym: {}
    return b


def sig_rows():
    return [(r["symbol"].split("/")[0], r["source"], r["sid"]) for r in STORE.db.run("SELECT * FROM signals ORDER BY rowid", fetch=True)]


@section("sổ noai: cùng ứng viên, vào không hỏi AI, gắn sid & phán quyết AI; sổ chính y hệt khi không có noai (cùng giá, cùng cỡ)")
def _():
    sigs = {"AAA/USDT:USDT": (trend_sig("AAA/USDT:USDT", 7.0), ""), "BBB/USDT:USDT": (trend_sig("BBB/USDT:USDT", 6.0), "")}
    bot0 = make_bot({k: (trend_sig(k, v[0].score), "") for k, v in sigs.items()}, noai=False)
    bot0.scan()
    rows0, main0 = sig_rows(), [(t["symbol"], round(t["entry"], 6), round(t["notional"], 4)) for t in STORE.trades()]
    asked0 = list(bot0.ai.asked)
    SENT.clear()
    bot = make_bot({k: (trend_sig(k, v[0].score), "") for k, v in sigs.items()})
    bot.scan()
    rows1 = sig_rows()
    assert bot.ai.asked == asked0 == ["AAA/USDT:USDT"], (bot.ai.asked, asked0)
    assert [(s, src) for s, src, _ in rows1] == [(s, src) for s, src, _ in rows0], (rows1, rows0)
    main1 = [(t["symbol"], round(t["entry"], 6), round(t["notional"], 4)) for t in STORE.trades()]
    assert main1 == main0, (main1, main0)                     # sổ chính: cùng lệnh, cùng giá, cùng cỡ
    noai = STORE.open_trades("noai")
    assert len(noai) == 1 and noai[0]["symbol"] == "AAA/USDT:USDT" and noai[0]["book"] == "noai", noai
    main = STORE.open_trades("main")
    assert len(main) == 1 and main[0]["book"] == "main"
    assert abs(noai[0]["entry"] - main[0]["entry"]) < 1e-12          # giá giả không đổi → cùng giá
    assert noai[0]["ai_model"] == "noai" and noai[0]["ai_conf"] is None
    assert abs(noai[0]["risk_mult"] - 1.0) < 0.05, noai[0]["risk_mult"]        # 1,0× (sổ chính AI 80% → 1,2×)
    assert abs(main[0]["risk_mult"] - 1.2) < 0.05, main[0]["risk_mult"]
    sid = [s for sym, src, s in rows1 if sym == "AAA"][0]
    assert noai[0]["sid"] == sid and main[0]["sid"] == sid and noai[0]["ai_verdict"] == "LONG 80%", (noai[0], main[0])
    assert not any("MỞ LỆNH" in m and "AAA" in m for m in SENT if "không AI" in m)     # không tin từng lệnh của noai
    assert sum(1 for m in SENT if "MỞ LỆNH" in m) == 1                                  # chỉ sổ chính báo
    # BBB: slot_taken ở sổ chính, noai không mở lệnh thứ hai trong cùng lượt
    assert [src for sym, src, _ in rows1 if sym == "BBB"] == ["slot_taken"]
    assert len(STORE.open_trades("noai")) == 1


@section("sổ chính lấy giá MỚI sau khi AI trả lời (như trước 3.5), không dùng giá sổ noai đã lấy lúc quét; dòng tín hiệu ghi giá đó")
def _():
    okx = ScanOKX(["HHH/USDT:USDT"], prices=[100.0, 100.8])      # lần 1: sổ noai lúc quét; lần 2: sổ chính sau AI
    bot = make_bot({"HHH/USDT:USDT": (trend_sig("HHH/USDT:USDT", 6.5), "")}, okx=okx)
    bot.scan()
    noai, main = STORE.open_trades("noai")[0], STORE.open_trades("main")[0]
    assert abs(noai["ref_price"] - 100.0) < 1e-9 and abs(main["ref_price"] - 100.8) < 1e-9, (noai["ref_price"], main["ref_price"])
    assert abs(STORE.db.run("SELECT entry FROM signals", fetch=True)[0]["entry"] - 100.8) < 1e-9
    assert okx.asked == 2


@section("lượt quét sau trong cùng nến: lệnh noai lấy sid của dòng đã có; sổ chính hỏi AI sau đó → sid lệnh noai chuyển sang dòng hỏi AI")
def _():
    sigs = {"III/USDT:USDT": (trend_sig("III/USDT:USDT", 6.5), "")}
    bot = make_bot(sigs)
    STORE.set("paper_balance_noai", 0.01)             # lượt 1: noai không vào được (ký quỹ), sổ chính đủ lệnh → bóng max_positions
    for i in range(cfg.MAX_OPEN_POSITIONS):
        STORE.open_trade(dict(mode="paper", symbol=f"OPEN{i}/USDT:USDT", side="long", entry=1.0, sl=0.9, tp=1.2, notional=5.0,
                              status="open", opened_at=time.time()))
    bot.scan()
    rows = sig_rows()
    assert rows == [("III", "max_positions", rows[0][2])] and STORE.open_trades("noai") == []
    sid_shadow = rows[0][2]
    STORE.set("paper_balance_noai", 10.0)             # lượt 2 (5 phút sau, cùng nến): noai vào; _shadow không ghi lại (đã thấy)
    bot.scan()
    assert sig_rows() == rows                          # không có dòng tín hiệu mới
    t = STORE.open_trades("noai")[0]
    assert t["sid"] == sid_shadow, t["sid"]
    STORE.db.run("DELETE FROM trades WHERE symbol LIKE 'OPEN%'")     # lượt 3: sổ chính trống chỗ, hỏi AI → dòng mới, bóng cũ DUP
    bot.cooldown.clear()
    bot.scan()
    rows3 = sig_rows()
    assert [(s, src) for s, src, _ in rows3] == [("III", "max_positions"), ("III", "taken")], rows3
    sid_ai = rows3[1][2]
    assert STORE.db.run("SELECT outcome FROM signals WHERE sid=?", (sid_shadow,), fetch=True)[0]["outcome"] == "DUP"
    assert STORE.open_trades("noai")[0]["sid"] == sid_ai and STORE.open_trades("main")[0]["sid"] == sid_ai


@section("sổ chính dừng vì cầu dao: vẫn quét cho sổ noai (main_active=False) – noai vào, sổ chính không hỏi AI, không ghi bóng")
def _():
    bot = make_bot({"JJJ/USDT:USDT": (trend_sig("JJJ/USDT:USDT", 6.5), "")})
    bot.scan(main_active=False)
    assert len(STORE.open_trades("noai")) == 1 and STORE.open_trades("main") == [] and bot.ai.asked == []
    assert sig_rows() == [] and STORE.open_trades("noai")[0]["sid"] is None
    assert bot._noai_active() is True
    STORE.set("noai_halted", True)
    assert bot._noai_active() is False


@section("AI từ chối: sổ chính không vào, sổ noai vẫn vào; lệnh noai ghi phán quyết AI; sig.skip của noai không lẫn sang sổ chính")
def _():
    bot = make_bot({"CCC/USDT:USDT": (trend_sig("CCC/USDT:USDT", 6.5), "")}, ai=FakeAI(decision="SKIP", conf=95))
    bot.scan()
    assert STORE.open_trades("main") == [] and len(STORE.open_trades("noai")) == 1
    r = sig_rows()
    assert r[0][1] == "ai_rejected", r
    assert STORE.open_trades("noai")[0]["ai_verdict"] == "SKIP 95%"
    # noai không vào được (ký quỹ không đủ) → lý do không ghi vào dòng tín hiệu của sổ chính
    bot = make_bot({"DDD/USDT:USDT": (trend_sig("DDD/USDT:USDT", 6.5), "")}, ai=FakeAI(decision="SKIP", conf=95))
    STORE.set("paper_balance_noai", 0.01)
    bot.scan()
    assert STORE.open_trades("noai") == []
    f = json.loads(STORE.db.run("SELECT features FROM signals", fetch=True)[0]["features"])
    assert "skip" not in f, f


@section("cầu dao riêng của sổ noai: sụt 15% từ đỉnh → ngừng mở lệnh mới, báo một lần; sổ chính vẫn vào; /resume mở lại")
def _():
    bot = make_bot({"EEE/USDT:USDT": (trend_sig("EEE/USDT:USDT", 6.5), "")})
    STORE.set("noai_peak_equity", 10.0); STORE.set("paper_balance_noai", 8.4)
    SENT.clear()
    bot.scan()
    assert STORE.open_trades("noai") == [] and STORE.get("noai_halted") is True
    assert len(STORE.open_trades("main")) == 1
    assert sum(1 for m in SENT if "Sổ không AI dừng" in m) == 1, SENT
    bot.cooldown.clear(); STORE.db.run("DELETE FROM trades"); STORE.db.run("DELETE FROM signals")
    bot2 = make_bot({"FFF/USDT:USDT": (trend_sig("FFF/USDT:USDT", 6.5), "")})
    STORE.set("noai_halted", True); STORE.set("noai_peak_equity", 10.0)
    bot2.scan()
    assert STORE.open_trades("noai") == [] and sum(1 for m in SENT if "Sổ không AI dừng" in m) == 1   # không báo lại
    assert bot2.handle_command("resume").startswith("▶️") and STORE.get("noai_halted") is False


@section("compare: thống kê từng sổ, hiệu theo cặp tín hiệu (main không vào → R_main = 0), văn bản /compare")
def _():
    for tb in ("signals", "trades", "equity"):
        STORE.db.run(f"DELETE FROM {tb}")
    now = time.time()
    def closed(book, sid, pnl, day, sym="X/USDT:USDT"):
        tid = STORE.open_trade(dict(mode="paper", book=book, symbol=sym, side="long", entry=100.0, sl=98.0, sl0=98.0, tp=104,
                                    notional=10.0, notional0=10.0, margin=1, contracts=1, status="open",
                                    opened_at=now - day * 86400, sid=sid, fee=0.02))
        STORE.close_trade(tid, 101, pnl, 10, 0.02, "TP")
        STORE.db.run("UPDATE trades SET closed_at=? WHERE id=?", (now - day * 86400 + 600, tid))
    # rủi ro 1R = 10 × 2% = 0.2 USDT
    closed("noai", "s1", 0.2, 3); closed("main", "s1", 0.1, 3)      # cùng tín hiệu: noai +1R, main +0.5R
    closed("noai", "s2", -0.2, 2)                                   # AI từ chối: main 0
    closed("noai", "s3", 0.4, 1); closed("main", "s3", 0.4, 1)
    closed("main", "s4", -0.2, 1)                                   # main vào mà noai không (đủ lệnh)
    for b, e in (("main", 10.0), ("main", 10.3), ("main", 9.9), ("noai", 10.0), ("noai", 10.4)):
        STORE.log_equity(e, b)
    period = {"ts": now - 10 * 86400, "equity": 10.0, "noai_equity": 10.0}
    c = books.compare(STORE, period, {"main": 10.1, "noai": 10.4}, {"main": 10.0, "noai": 10.0}, {"main": False, "noai": False})
    m, n = c["books"]["main"], c["books"]["noai"]
    assert m["n"] == 3 and n["n"] == 3 and abs(m["mean_r"] - (0.5 + 2 - 1) / 3) < 1e-3 and abs(n["mean_r"] - (1 - 1 + 2) / 3) < 1e-3
    assert m["max_dd_pct"] == round(100 * (10.3 - 9.9) / 10.3, 2) and n["max_dd_pct"] == 0
    assert abs(m["fees"] - 0.06) < 1e-9 and m["ret_pct"] == 1.0 and n["ret_pct"] == 4.0
    p = c["paired"]
    assert p["n"] == 3 and p["matched"] == 2 and p["main_only"] == 1 and abs(p["diff"] - (0.5 - 1 + 0) / 3) < 1e-3, p
    txt = books.compare_text(c)
    assert "Sổ không AI" in txt and "Theo cặp tín hiệu (3 lệnh noai, 2 có lệnh main" in txt and "Chưa đủ dữ liệu" in txt, txt
    # lệnh main cùng sid còn mở → cặp chờ
    STORE.open_trade(dict(mode="paper", book="main", symbol="Y/USDT:USDT", side="long", entry=100.0, sl=98.0, tp=104,
                          notional=10.0, status="open", opened_at=now - 3600, sid="s5"))
    closed("noai", "s5", 0.2, 0)
    p2 = books.compare(STORE, period, {"main": 10.1, "noai": 10.4}, {"main": 10.0, "noai": 10.0})["paired"]
    assert p2["pending"] == 1 and p2["n"] == 3, p2


@section("MODE=live: sổ noai vẫn là PaperEngine – không gọi API đặt lệnh; mặc định x3 và 1%/lệnh khi live, x10 và 3% khi paper")
def _():
    code = r'''
import os, sys
D = sys.argv[1]
os.environ.update(MODE="live", DATA_DIR=D, AI_ENABLED="false", DATABASE_URL="", OKX_API_KEY="k", OKX_API_SECRET="s",
                  OKX_PASSPHRASE="p", I_UNDERSTAND_THE_RISK="yes")
sys.path.insert(0, sys.argv[2])
from bot.config import cfg
assert cfg.MODE == "live" and cfg.LEVERAGE == 3 and abs(cfg.RISK_PER_TRADE - 0.01) < 1e-12, (cfg.LEVERAGE, cfg.RISK_PER_TRADE)
from bot.storage import Store
from bot.engine import PaperEngine
from bot.strategy import Signal
class Ex:
    def __getattr__(self, k): raise AssertionError("API riêng của sàn bị gọi: " + k)
class OKX:
    ex = Ex()
    def price(self, s): return 100.0
    def market(self, s): return {"contractSize": 1, "limits": {"amount": {"min": 1}}, "precision": {"amount": 1}}
    def contracts_for_notional(self, s, notional, price):
        c = int(notional / price); return (float(c), 0.0) if c >= 1 else (0.0, price)
    def ohlcv_since(self, *a, **k): return []
    def seconds(self, *a, **k): return []
    def open_position(self, *a, **k): raise AssertionError("open_position bị gọi")
    def close_position(self, *a, **k): raise AssertionError("close_position bị gọi")
    def balance_usdt(self): raise AssertionError("balance_usdt bị gọi")
st = Store()
eng = PaperEngine(OKX(), st, lambda t: None, book="noai", quiet=True, capital=100.0)
sig = Signal("X/USDT:USDT", "long", 6.0, 100.0, 0.6, 98.8, 103.6, ["t"], {"setup": "trend"})
t = eng.open(sig, {"decision": "LONG", "confidence": None, "model": "noai"})
assert t and t["mode"] == "paper" and t["book"] == "noai", t
eng.close(t, 103.6, "TP")
assert st.stats("noai")["trades"] == 1 and st.stats("main")["trades"] == 0
print("LIVE-OK")
'''
    out = subprocess.run([sys.executable, "-c", code, os.path.join(D, "live"), HERE], capture_output=True, text=True, timeout=120)
    assert "LIVE-OK" in out.stdout, out.stdout + out.stderr
    assert cfg.MODE == "paper" and cfg.LEVERAGE == 10 and abs(cfg.RISK_PER_TRADE - 0.03) < 1e-12


@section("snapshot / status: có khối books, noai; /compare trả văn bản")
def _():
    bot = make_bot({"GGG/USDT:USDT": (trend_sig("GGG/USDT:USDT", 6.5), "")})
    bot.scan()
    bot.started_at, bot.instance, bot.is_leader, bot.learner = time.time(), "t", True, None
    bot.switches = NS(all=lambda: {})
    bot.ai.last_model, bot.ai.calls_today, bot.ai.public_stats, bot.ai.stats = "m", (lambda: 0), (lambda: {}), {"fails": 0}
    snap = bot.snapshot()
    assert snap["books"]["books"]["noai"]["open"] == 1 and snap["noai"]["enabled"] and len(snap["noai"]["open"]) == 1
    assert snap["books"]["books"]["main"]["open"] == 1
    txt = bot.handle_command("compare")
    assert txt.startswith("⚖️") and "Sổ không AI" in txt, txt
    assert "Sổ không AI:" in bot.status_text()


assert not FAILED, f"{len(FAILED)} phần lỗi: {FAILED}"
print("TẤT CẢ OK")
