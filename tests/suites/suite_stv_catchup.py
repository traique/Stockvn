"""The comparison book inside the evening run: shown, isolated from failures (offline)."""
import os
import sys
import tempfile
from pathlib import Path

import _common  # noqa: E402,F401 - clean env, no .env, offline, temp folder, repo on sys.path
DATA = Path(tempfile.mkdtemp(prefix="stvrun-"))
os.environ.update({"STV_MARKET": "0", "DATA_DIR": str(DATA), "TRADE_MODE": "paper", "SIEUTINHIEU": "1",
                   "TRADINGAGENTS_PARAMS_PATH": str(DATA / "quant_params.json")})
from vnbot import settings as settings_module  # noqa: E402

SETTINGS = settings_module.load()
import pandas as pd  # noqa: E402
import vnbot.trader as T  # noqa: E402
from tradingagents.trading import momentum  # noqa: E402
from tradingagents.trading.ledger import Bar, Signal  # noqa: E402
from tradingagents.dataflows.vendors import sieutinhieu as stv  # noqa: E402

SERIES = {"VNINDEX": [("2026-10-16", 1710), ("2026-10-19", 1705), ("2026-10-20", 1702)],
          "GEX": [("2026-10-16", 22_000), ("2026-10-19", 22_750), ("2026-10-20", 23_850)],
          "FPT": [("2026-10-16", 90_000), ("2026-10-19", 91_000), ("2026-10-20", 92_000)]}


class FakeMarket:
    def __init__(self, as_of):
        self.as_of = as_of

    def _rows(self, s, day):
        return [(d, c) for d, c in SERIES.get(s, []) if d <= day]

    def sessions(self, s):
        return [d for d, _ in SERIES[s]]

    def frame(self, s, day, required=False):
        rows = self._rows(s, day)
        return None if not rows else pd.DataFrame({"Date": pd.to_datetime([d for d, _ in rows]),
                                                   "Close": [c for _, c in rows]})

    def exchange(self, s):
        return "HOSE"

    def close(self, s, day):
        rows = self._rows(s, day)
        return float(rows[-1][1]) if rows else None

    def bar(self, s, day, required=False):
        rows = self._rows(s, day)
        if not rows or rows[-1][0] != day:
            return None
        c = rows[-1][1]
        return Bar(day, c, c, c, c, rows[-2][1] if len(rows) > 1 else None, "HOSE", 1e6)


T._Market = FakeMarket
T.final_day = lambda now=None: "2026-10-20"


class WL:
    def get(self):
        return ["GEX", "FPT"]


def make_bot():
    bot = T.TradingBot(SETTINGS, WL())
    bot.universe = lambda: ["GEX", "FPT"]
    wl_file = bot.settings.data_dir / "external" / "watchlist.json"      # the Siêu tín hiệu watchlist (its own)
    wl_file.parent.mkdir(parents=True, exist_ok=True)
    wl_file.write_text('["GEX", "FPT"]', encoding="utf-8")
    def eng(day, sessions, paper, *a, **k):
        sigs = [Signal("GEX", "NO_TRADE", 0.5, 23_850), Signal("FPT", "BUY", 0.8, 92_000, 91_000, 92_000, 88_000, 99_000)]
        bot._write_json("signals.json", {"day": day, "signals": {x.symbol: x.__dict__ for x in sigs}})
        paper.meta["last_day"] = day
        paper.save(bot.paper_path)
        return {"day": day, "real_only": {}, "errors": [], "signals": sigs}

    def fac(day, sessions, book, *a, **k):
        book.meta["last_day"] = day
        book.save(bot.factor_path)

    def mom(day, sessions, book, *a, **k):
        book.meta["last_day"] = day
        book.save(bot.momentum_path)
        return momentum.Decision(True, False, [])
    bot._engine_session = eng
    bot._factor_session = fac
    bot._momentum_session = mom
    bot._factor_text = bot._paper_report = bot._momentum_text = lambda *a, **k: "(paper)"
    bot._comparison = lambda *a, **k: "(cmp)"
    for path, book in ((bot.paper_path, bot.paper()), (bot.momentum_path, bot.momentum_book()),
                       (bot.factor_path, bot.factor_book())):
        book.meta["last_day"] = "2026-10-19"
        book.save(path)
    return bot


def row(day, side, price, created, strength=80):
    return {"timestamp": f"{day}T00:00:00Z", "signal_type": side, "price": price, "price_basis": "bar_close",
            "created_at": created, "strength_pct": strength}


HIST = {"GEX": [row("2026-10-20", "BUY", 23.85, "2026-10-20T08:08:00Z", 90)],
        "FPT": [row("2026-10-08", "SELL", 95.0, "2026-10-08T08:00:00Z", 20)]}
MODE = {"fail": False}


def fake_fetch(symbol):
    if MODE["fail"]:
        raise RuntimeError("site down")
    return [stv.normalize(r) for r in HIST.get(symbol, [])]


stv.fetch_signals = fake_fetch
fails = []


def check(name, cond, info=""):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f"  [{info}]"))
    if not cond:
        fails.append(name)


bot = make_bot()
res = bot.catch_up("2026-10-20")
text = res.text
check("report has the comparison section", "📡 SỔ ẢO SIÊU TÍN HIỆU" in text, text[-800:])
check("section header", "📡 SỔ ẢO SIÊU TÍN HIỆU · phiên 20/10" in text, text[-800:])
check("new signal listed with the bot's decision", "🟢 GEX 23.850 · mạnh 90% · bot: ĐỨNG NGOÀI" in text, text[-800:])
check("bot BUY vs site SELL flagged", "⚠️ Bot MUA nhưng trang đang BÁN: FPT (từ 08/10)" in text, text[-800:])
check("book plans the buy at the next open", "Lệnh phiên tới (giá mở cửa):\n🟢 MUA GEX 4.000 cp · tín hiệu 20/10 @ 23.850" in text,
      text[-800:])
check("bot run unaffected", res.day == "2026-10-20" and "(paper)" in text)

# The site down: the bot's run still completes, the section says so.
DATA2 = Path(tempfile.mkdtemp(prefix="stvrun2-"))
SETTINGS2 = settings_module.Settings(**{**SETTINGS.__dict__, "data_dir": DATA2})
T_settings = SETTINGS
SETTINGS = SETTINGS2
MODE["fail"] = True
bot2 = make_bot()
res2 = bot2.catch_up("2026-10-20")
check("site down: run completes", res2.day == "2026-10-20" and "(paper)" in res2.text)
check("site down: section reports it", "Lấy tín hiệu chưa trọn" in res2.text or "chưa cập nhật được" in res2.text,
      res2.text[-500:])


# A crash inside the comparison book never fails the run.
def boom(*a, **k):
    raise ValueError("bug")


bot2._stv_session = boom
for path, book in ((bot2.paper_path, bot2.paper()), (bot2.momentum_path, bot2.momentum_book()),
                   (bot2.factor_path, bot2.factor_book())):
    book.meta["last_day"] = "2026-10-19"
    book.save(path)
bot2.stv_path.unlink(missing_ok=True)
res3 = bot2.catch_up("2026-10-20")
check("crash in the comparison book is contained", res3.day == "2026-10-20" and "chưa cập nhật được (bug)" in res3.text,
      res3.text[-300:])
# The comparison book runs outside the trading lock.
import threading  # noqa: E402
seen = {}
orig = T.TradingBot._stv_run


def spy(self, *a, **k):
    got = []

    def probe():
        ok = self._lock.acquire(blocking=False)
        got.append(ok)
        if ok:
            self._lock.release()
    t = threading.Thread(target=probe)
    t.start()
    t.join()
    seen["free"] = bool(got and got[0])
    return orig(self, *a, **k)


T.TradingBot._stv_run = spy
MODE["fail"] = False
DATA3 = Path(tempfile.mkdtemp(prefix="stvrun3-"))
SETTINGS = settings_module.Settings(**{**SETTINGS2.__dict__, "data_dir": DATA3})
bot3 = make_bot()
bot3.catch_up("2026-10-20")
check("trading lock is free while the comparison book runs", seen.get("free") is True, seen)
T.TradingBot._stv_run = orig
# A comparison book behind (e.g. a reset): not "done", and its section comes on its own.
bot3._write_json("last_report.json", {"day": "2026-10-20", "text": "x"})
bot3.stv_path.unlink()
check("behind comparison book: session not done", bot3.is_done("2026-10-20") is False)
res4 = bot3.catch_up("2026-10-20")
check("only the comparison book was new: sent as extra", not res4.new and "SỔ ẢO SIÊU TÍN HIỆU" in res4.extra, res4)
check("then done", bot3.is_done("2026-10-20") is True)
print(bot.stv_text())
print("FAILED:", fails or "none")
sys.exit(1 if fails else 0)
