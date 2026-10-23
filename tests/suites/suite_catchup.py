"""_catch_up in signal mode, end to end with a fake market (no network)."""
import os
import sys
import tempfile
from pathlib import Path

import _common  # noqa: E402,F401 - clean env, no .env, offline, temp folder, repo on sys.path
DATA = Path(tempfile.mkdtemp(prefix="catchup-"))
os.environ.update({"DATA_DIR": str(DATA), "TRADE_MODE": "signal", "SIGNAL_STRATEGY": "momentum",
                   "TRADINGAGENTS_PARAMS_PATH": str(DATA / "quant_params.json")})
from vnbot import settings as settings_module  # noqa: E402

SETTINGS = settings_module.load()
import pandas as pd  # noqa: E402
import vnbot.trader as T  # noqa: E402
from tradingagents.trading import momentum  # noqa: E402
from tradingagents.trading.ledger import Bar  # noqa: E402

SERIES = {
    "VNINDEX": [("2026-10-15", 1700), ("2026-10-16", 1710), ("2026-10-19", 1705), ("2026-10-20", 1702)],
    # GGG: ex-date 2026-10-20 that the source has NOT re-adjusted (61.000 -> 46.500)
    "GGG": [("2026-10-15", 60_000), ("2026-10-16", 60_500), ("2026-10-19", 61_000), ("2026-10-20", 46_500)],
    # KKK: an ordinary stop hit (stop 50.000, low 49.000)
    "KKK": [("2026-10-15", 52_000), ("2026-10-16", 51_500), ("2026-10-19", 51_000), ("2026-10-20", 49_500)],
}


class FakeMarket:
    def __init__(self, as_of):
        self.as_of = as_of

    def _rows(self, s, day):
        return [(d, c) for d, c in SERIES.get(s, []) if d <= day]

    def sessions(self, s):
        return [d for d, _ in SERIES[s]]

    def frame(self, s, day, required=False):
        rows = self._rows(s, day)
        if not rows:
            return None
        return pd.DataFrame({"Date": pd.to_datetime([d for d, _ in rows]), "Close": [c for _, c in rows]})

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
        low = c - 500 if s == "KKK" else c
        return Bar(day, c, c, low, c, rows[-2][1] if len(rows) > 1 else None, "HOSE", 1e6)


T._Market = FakeMarket
T.final_day = lambda now=None: "2026-10-20"


class WL:
    def get(self):
        return []


bot = T.TradingBot(SETTINGS, WL())
bot.set_capital(500_000_000)
bot.record_fill("mua", "GGG", 1000, 60_000, "2026-10-15")
bot.record_fill("mua", "KKK", 1000, 52_000, "2026-10-15")
# momentum strategy stops: pct from params; set explicit stops for the test
real = bot.real()
real.positions["GGG"].stop = 55_000
real.positions["KKK"].stop = 50_000
real.meta["close_marks"] = 1
real.save(bot.real_path)
# a first run marks the book at 2026-10-19
for _book in (bot.paper(), bot.momentum_book(), bot.factor_book()):
    pass
bot.universe = lambda: []
bot._engine_session = lambda day, *a, **k: {"day": day, "signals": [], "real_only": {}, "errors": []}
bot._factor_session = lambda *a, **k: None
dec = momentum.Decision(True, False, ["GGG", "KKK"])
bot._momentum_session = lambda *a, **k: dec
bot._factor_text = bot._paper_report = bot._momentum_text = lambda *a, **k: "(paper)"
bot._comparison = lambda *a, **k: "(cmp)"
# Pretend all paper books are at 2026-10-19 so only 10-20 is processed.
for path, book in ((bot.paper_path, bot.paper()), (bot.momentum_path, bot.momentum_book()),
                   (bot.factor_path, bot.factor_book())):
    book.meta["last_day"] = "2026-10-19"
    book.save(path)
real = bot.real()
real.positions["GGG"].last_price, real.positions["GGG"].last_date = 61_000, "2026-10-19"
real.positions["KKK"].last_price, real.positions["KKK"].last_date = 51_000, "2026-10-19"
real.meta["close_marks"] = 1
real.save(bot.real_path)

res = bot.catch_up("2026-10-20")
text = res.text
print(text)
real = bot.real()
fails = []


def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


check("GGG gap note shown", "⚠️ GGG" in text and "GDKHQ" in text)
check("no fake stop for GGG", "⛔ GGG" not in text)
check("GGG pending estimate saved", abs(real.positions["GGG"].pending_ratio - 61_000 / 46_500) < 1e-4)
check("GGG levels kept in the book", real.positions["GGG"].stop == 55_000)
check("real stop for KKK shown", "⛔ KKK: chạm stop 50.000" in text)
check("note right after the title", text.split("📣")[1].splitlines()[1].startswith("- ⚠️ GGG"))
check("GGG mark held before the gap", real.positions["GGG"].last_date == "2026-10-19")
check("KKK marked to 10-20", real.positions["KKK"].last_price == 49_500 and real.positions["KKK"].last_date == "2026-10-20")
check("fills untouched", len(real.fills) == 2 and real.meta.get("last_day") == "2026-10-15")
print("FAILED:", fails if fails else "none")
sys.exit(1 if fails else 0)
