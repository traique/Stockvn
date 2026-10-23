"""Round-2 fixes: VNINDEX intraday alert, late bars, stale BUY, factor ranking guard, liquidity."""
import os
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path

import _common  # noqa: E402,F401 - clean env, no .env, offline, temp folder, repo on sys.path
DATA = Path(tempfile.mkdtemp(prefix="round2-"))
os.environ.update({"DATA_DIR": str(DATA), "TRADE_MODE": "signal", "SIGNAL_STRATEGY": "momentum",
                   "TRADINGAGENTS_PARAMS_PATH": str(DATA / "quant_params.json")})
from vnbot import settings as settings_module  # noqa: E402

SETTINGS = settings_module.load()
import pandas as pd  # noqa: E402
from tradingagents.market import vn_rules  # noqa: E402

FAILS, PASSES = [], 0


def check(name, cond, info=""):
    global PASSES
    if cond:
        PASSES += 1
    else:
        FAILS.append(name)
        print("FAIL:", name, info)


# ───────────── 1. VNINDEX intraday ─────────────
from tradingagents.dataflows.vendors import dnse  # noqa: E402
import vnbot.intraday as I  # noqa: E402

calls = []


def fake_index(rows):
    def fetch(symbol, start, end):
        calls.append((symbol, start, end))
        return pd.DataFrame({"Date": [d for d, _ in rows], "Close": [c for _, c in rows]})
    return fetch


dnse.fetch_ohlcv = fake_index([("2026-10-19", 1700.0), ("2026-10-20", 1720.0), ("2026-10-21", 1634.0)])
ch = I.vnindex_change("2026-10-21")
check("VNINDEX change from the forming bar", ch is not None and abs(ch - (1634 / 1720 - 1) * 100) < 1e-9, ch)
check("asked the index feed", calls and calls[-1][0] == "VNINDEX" and calls[-1][2] == "2026-10-21", calls)
dnse.fetch_ohlcv = fake_index([("2026-10-19", 1700.0), ("2026-10-20", 1720.0)])
check("no bar today -> None", I.vnindex_change("2026-10-21") is None)
dnse.fetch_ohlcv = fake_index([("2026-10-16", 1700.0), ("2026-10-19", 1680.0), ("2026-10-21", 1650.0)])
check("hole in the index feed -> None", I.vnindex_change("2026-10-21") is None)
dnse.fetch_ohlcv = fake_index([("2026-10-19", 1700.0), ("2026-10-20", 1720.0), ("2026-10-21", 1634.0)])
from tradingagents.trading.ledger import Ledger  # noqa: E402


class IT:
    def real(self):
        return Ledger(cash=1e9, start_cash=1e9)

    def paper(self):
        return Ledger(cash=1e9, start_cash=1e9)


sent = []
s2 = settings_module.Settings(**{**SETTINGS.__dict__, "data_dir": Path(tempfile.mkdtemp())})
mon = I.IntradayMonitor(s2, IT(), sent.append, price=lambda c, d: None, exchange_of=lambda c: "HOSE",
                        closes=lambda c, on, d: None)
out = mon.check("2026-10-21", now=datetime(2026, 10, 21, 10, 0, tzinfo=vn_rules.VN_TZ))
check("VNINDEX -5% alert fires (2% and 4%)", any("ngưỡng −2%" in a for a in out) and any("ngưỡng −4%" in a for a in out), out)

# ───────────── 2. late bars in the trading run ─────────────
import vnbot.trader as T  # noqa: E402
from tradingagents.trading import momentum  # noqa: E402
from tradingagents.trading.ledger import Bar  # noqa: E402

SERIES = {
    "VNINDEX": [("2026-10-15", 1700), ("2026-10-16", 1710), ("2026-10-19", 1705), ("2026-10-20", 1702)],
    "KKK": [("2026-10-15", 52_000), ("2026-10-16", 51_500), ("2026-10-19", 51_000)],          # late on 10-20
    "SSS": [("2026-10-01", 20_000)],                                                         # long suspended
    "MMM": [("2026-10-15", 30_000), ("2026-10-16", 30_100), ("2026-10-19", 30_200), ("2026-10-20", 30_300)],
    "THN": [("2026-10-15", 9_000), ("2026-10-19", 9_100)],                                   # thin: skips sessions
    "VVV": [("2026-10-15", 80_000), ("2026-10-16", 80_500), ("2026-10-19", 81_000)],          # VN30 name, late
}
import tradingagents.dataflows.money_flows as MF  # noqa: E402
MF.index_members = lambda group="VN30": ["VVV", "MMM"]


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
        return Bar(day, c, c, c, c, rows[-2][1] if len(rows) > 1 else None, "HOSE", 1e6)


T._Market = FakeMarket
T.final_day = lambda now=None: "2026-10-20"
NOW = {"t": datetime(2026, 10, 20, 16, 0, tzinfo=vn_rules.VN_TZ)}
T._now_vn = lambda: NOW["t"]


class WL:
    def get(self):
        return []


bot = T.TradingBot(SETTINGS, WL())
bot.set_capital(500_000_000)
bot.record_fill("mua", "SSS", 1000, 20_000, "2026-10-01")
bot.record_fill("mua", "KKK", 1000, 52_000, "2026-10-15")
bot.record_fill("mua", "MMM", 1000, 30_000, "2026-10-15")
bot.record_fill("mua", "THN", 1000, 9_000, "2026-10-15")
bot.universe = lambda: []
processed = []
bot._engine_session = lambda day, *a, **k: processed.append(("engine", day)) or {"day": day, "signals": [],
                                                                                "real_only": {}, "errors": []}
bot._factor_session = lambda s, *a, **k: processed.append(("factor", s))
bot._momentum_session = lambda s, *a, **k: processed.append(("momentum", s)) or momentum.Decision(True, False, ["KKK"])
bot._factor_text = bot._paper_report = bot._momentum_text = lambda *a, **k: "(paper)"
bot._comparison = lambda *a, **k: "(cmp)"
for path, book in ((bot.paper_path, bot.paper()), (bot.momentum_path, bot.momentum_book()),
                   (bot.factor_path, bot.factor_book())):
    book.meta["last_day"] = "2026-10-19"
    book.save(path)
bot._write_json("last_report.json", {"day": "2026-10-19", "text": "report 10-19"})

market = FakeMarket("2026-10-20")
books = (bot.paper(), bot.momentum_book(), bot.factor_book())
gaps = bot._fresh_gaps(market.sessions("VNINDEX"), market, books, bot.real())
check("fresh gap: KKK late; SSS suspended, THN thin, MMM fine not counted", gaps == ["KKK"], gaps)
from tradingagents.trading.ledger import Order  # noqa: E402
pb = bot.paper()
pb.orders.append(Order("OLDORD", "BUY", 100, 10_000, "2026-10-15"))
SERIES["OLDORD"] = [("2026-10-13", 10_000), ("2026-10-14", 10_000), ("2026-10-15", 10_000), ("2026-10-16", 10_000),
                    ("2026-10-19", 10_000)]
g2 = bot._fresh_gaps(market.sessions("VNINDEX"), market, (pb, bot.momentum_book(), bot.factor_book()), bot.real())
check("an order that lapses before the session causes no wait", "OLDORD" not in g2, g2)
from tradingagents.quant.params import DEFAULT as P0  # noqa: E402
names = bot._ranking_names("2026-10-20", bot.momentum_book(), bot.factor_book(), P0)
check("ranking names include VN30 when the rotation is due", "VVV" in names, names)
bot._write_json("factor_universe.json", {"days": {"2026-09-30": {"size": 50, "universe": ["CAP1", "CAP2"], "pool": []}}})
fb = bot.factor_book()
fb.meta.update(fb_on=True, fb_basket=["CAP1"])
mb = bot.momentum_book()
mb.meta.update(mom_since=0, mom_market_ok=True, mom_basket=["MMM"])
mid = bot._ranking_names("2026-10-20", mb, fb, P0)
end = bot._ranking_names("2026-10-30", mb, fb, P0)
check("mid-month, rotation not due: nothing ranked to wait for", mid == [], mid)
check("rebuild month end: cached cap universe waited for", end == ["CAP1", "CAP2"], end)
fb.meta.update(fb_basket=None)
check("defensive book in cash at a week end: universe waited for",
      bot._ranking_names("2026-10-23", mb, fb, P0) == ["CAP1", "CAP2"])

res = bot.catch_up("2026-10-20")
check("16:00: session waits (held KKK + ranked VVV)", res.day == "2026-10-19" and res.waiting == ["KKK", "VVV"]
      and not processed, (res, processed))
check("16:00: books not advanced", T._book_day(bot.paper()) == "2026-10-19")

notes = []
bot.session_day = lambda: "2026-10-20"
msg = bot.start_run(notify=notes.append)
for t in threading.enumerate():
    if t.name == "bot-run-now":
        t.join(10)
check("/chay explains the wait", notes and "chưa có nến phiên 2026-10-20 của KKK" in notes[-1], notes)

NOW["t"] = datetime(2026, 10, 20, 18, 5, tzinfo=vn_rules.VN_TZ)
res = bot.catch_up("2026-10-20")
check("18:05: session processed", res.day == "2026-10-20" and not res.waiting and ("engine", "2026-10-20") in processed,
      (res.day, res.waiting, processed))
check("18:05: report says KKK taken as suspended", "Không có nến phiên 2026-10-20 của KKK" in res.text, res.text[:400])
check("18:05: report names the late ranked stock", "VVV (rổ xếp hạng" in res.text, res.text[:600])

# Catch-up of an older session never waits.
NOW["t"] = datetime(2026, 10, 21, 10, 0, tzinfo=vn_rules.VN_TZ)
check("not today -> no wait", T._before_cutoff("2026-10-20") is False)

# ───────────── 3. no BUY on stale data ─────────────
from tradingagents.quant import engine  # noqa: E402


class R:
    def __init__(self, symbol, action, last):
        self.symbol, self.action, self.confidence, self.price = symbol, action, 0.8, 30_000
        self.entry_low, self.entry_high, self.stop_price = 29_500, 30_000, 28_000
        self.target_price, self.target2, self.position_size_pct, self.last_bar_date = 34_000, None, 8, last


engine.analyze = lambda symbol, day, **k: R(symbol, "BUY", "2026-10-19" if symbol == "OLD" else day)
sigs, real_only, errors = bot._signals_for("2026-10-20", Ledger(cash=1e9, start_cash=1e9), None, ["OLD", "NEW"],
                                           __import__("tradingagents.quant.params", fromlist=["DEFAULT"]).DEFAULT)
by = {s.symbol: s for s in sigs}
check("stale BUY downgraded", by["OLD"].action == "WATCH" and any("OLD" in e for e in errors), (by["OLD"], errors))
check("fresh BUY kept", by["NEW"].action == "BUY")

# ───────────── 4. factor ranking waits when many names are late ─────────────
from tradingagents.quant.params import DEFAULT  # noqa: E402
from tradingagents.trading import factor  # noqa: E402

names = [f"U{i}" for i in range(10)]
for i, n in enumerate(names):
    rows = [("2026-10-16", 10_000), ("2026-10-19", 10_000)]
    if i >= 3:                                     # 3 of 10 late (30% > 20%)
        rows.append(("2026-10-20", 10_100))
    SERIES[n] = rows
bot._factor_universe = lambda day, sessions, market, params: list(names)
factor.rank = lambda closes, order: [factor.Ranked(s, 1.0, 0.1) if hasattr(factor, "Ranked") else s for s in order]
NOW["t"] = datetime(2026, 10, 20, 16, 0, tzinfo=vn_rules.VN_TZ)
try:
    bot._factor_ranking("2026-10-20", ["2026-10-16", "2026-10-19", "2026-10-20"], FakeMarket("2026-10-20"), DEFAULT)
    check("ranking waits before cutoff", False)
except RuntimeError as exc:
    check("ranking waits before cutoff", "Phòng thủ" in str(exc), exc)
NOW["t"] = datetime(2026, 10, 20, 18, 5, tzinfo=vn_rules.VN_TZ)
try:
    out = bot._factor_ranking("2026-10-20", ["2026-10-16", "2026-10-19", "2026-10-20"], FakeMarket("2026-10-20"),
                              DEFAULT)
    check("ranking proceeds after cutoff", True)
except RuntimeError as exc:
    check("ranking proceeds after cutoff", "Phòng thủ" not in str(exc), exc)
except Exception:  # noqa: BLE001 - rank stub shape; only our guard matters here
    check("ranking proceeds after cutoff", True)

# ───────────── 5. liquidity in VND ─────────────
import importlib  # noqa: E402
engine = importlib.reload(engine)


def frame(price, volume, n=80):
    days = pd.bdate_range("2026-06-01", periods=n)
    return pd.DataFrame({"Date": days, "Open": price, "High": price * 1.01, "Low": price * 0.99, "Close": price,
                         "Volume": volume})


cheap = engine.build_inputs("AAA", "2026-09-18", frame(4_000, 150_000), None, exchange="HOSE")
dear = engine.build_inputs("BBB", "2026-09-18", frame(150_000, 60_000), None, exchange="HOSE")
check("4.000đ x 150k cp (~0,6 tỷ) is thin", cheap.liquidity.is_thin, cheap.liquidity)
check("150.000đ x 60k cp (~9 tỷ) is not thin", not dear.liquidity.is_thin, dear.liquidity)

print(f"\n{PASSES} passed, {len(FAILS)} failed")
sys.exit(1 if FAILS else 0)
