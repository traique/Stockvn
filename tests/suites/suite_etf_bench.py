"""The ETF buy-and-hold benchmark (vnbot.etf_bench): bought at the start session's close in lots
of 100 with the buy fee, NAV and "if sold today" (sell fee + tax) each session, holidays (no bar,
no row), a re-adjusted series (an ex-date) moves nothing, BENCH_ETF_START, a source failure
keeps the previous state, the texts; the lines of /danhmuc and the comparison (offline)."""
import os
from types import SimpleNamespace as NS

import _common  # noqa: F401 - clean env, no .env, offline, temp folder, repo on sys.path

import pandas as pd  # noqa: E402

from tradingagents.market import vn_rules  # noqa: E402
from vnbot import etf_bench as eb  # noqa: E402

t = _common.Checks()
DATA = _common.ROOT / "etf-data"
DATA.mkdir()
CAPITAL = 1_000_000_000.0
DAYS = ["2026-12-01", "2026-12-02", "2026-12-03", "2026-12-04", "2026-12-07"]       # 05-06: the weekend
CLOSES = [25_000.0, 26_000.0, 25_500.0, 24_000.0, 27_000.0]


def frame(days=DAYS, closes=None, scale=1.0):
    closes = CLOSES[:len(days)] if closes is None else closes
    return pd.DataFrame({"Date": pd.to_datetime(days), "Open": [c * scale for c in closes], "High": [c * scale * 1.01 for c in closes],
                         "Low": [c * scale * 0.99 for c in closes], "Close": [c * scale for c in closes], "Volume": [1e6] * len(days)})


# ───────────── 1. the buy and the NAV ─────────────
t.ok("default: E1VFVN30, no start by hand", eb.symbol() == "E1VFVN30" and eb.start_env() == "")
state = eb.refresh(DATA, CAPITAL, "2026-12-01", frame=frame(DAYS[:1]))
fee = vn_rules.BUY_FEE_PCT / 100
shares = int(CAPITAL / (25_000 * (1 + fee)) // 100) * 100
stake = shares * 25_000
cash = CAPITAL - stake * (1 + fee)
t.ok("the first run starts the benchmark that session and buys at its close: lots of 100, the buy fee, the rest cash",
     state["start"] == "2026-12-01" and state["bought"] == "2026-12-01" and state["shares"] == shares == 39_900
     and state["stake"] == round(stake) and state["cash"] == round(cash) and state["entry"] == 25_000.0, state)
h0 = state["history"][0]
net = 1 - (vn_rules.SELL_FEE_PCT + vn_rules.SELL_TAX_PCT) / 100
t.ok("NAV of the start session = cash + stake (the buy fee is the only loss); sold today: minus sell fee and tax",
     h0["nav"] == round(cash + stake) == 998_503_750 and h0["nav_net"] == round(cash + stake * net), h0)
t.ok("saved under data/results", eb.path(DATA).exists() and eb.load(DATA)["symbol"] == "E1VFVN30")

state = eb.refresh(DATA, CAPITAL, "2026-12-07", frame=frame())
hist = {h["date"]: h for h in state["history"]}
t.ok("later sessions: NAV = cash + stake x close / start close; the weekend has no row",
     list(hist) == DAYS and hist["2026-12-02"]["nav"] == round(cash + stake * 26 / 25)
     and hist["2026-12-07"]["nav"] == round(cash + stake * 27 / 25), [(d, h["nav"]) for d, h in hist.items()])
st = eb.stats(state)
t.ok("stats: return, return if sold, max drawdown from the NAV peak, sessions",
     st["symbol"] == "E1VFVN30" and st["start"] == "2026-12-01" and st["last"] == "2026-12-07" and st["sessions"] == 5
     and st["return_pct"] == round((hist["2026-12-07"]["nav"] / CAPITAL - 1) * 100, 2)
     and st["return_net_pct"] == round((hist["2026-12-07"]["nav_net"] / CAPITAL - 1) * 100, 2)
     and abs(st["max_drawdown_pct"] - (1 - hist["2026-12-04"]["nav"] / hist["2026-12-02"]["nav"]) * 100) < 0.01, st)

# ───────────── 2. an ex-date: the source re-adjusts the whole series ─────────────
before = [h["nav"] for h in state["history"]]
state = eb.refresh(DATA, CAPITAL, "2026-12-07", frame=frame(scale=0.9))
t.ok("every close rescaled (a dividend went ex): the NAVs do not move - the ratio to the start close is the total return",
     [h["nav"] for h in state["history"]] == before and state["basis"] == 22_500.0 and state["entry"] == 25_000.0,
     [h["nav"] for h in state["history"]][:3])

# ───────────── 3. a holiday the frame skips, a failure, a missing close ─────────────
state = eb.refresh(DATA, CAPITAL, "2026-12-08", frame=frame(DAYS + ["2026-12-09"], CLOSES + [27_500.0]))
t.ok("a session without a bar (closure) has no row; the next one does", "2026-12-08" not in {h["date"] for h in state["history"]}
     and state["history"][-1]["date"] == "2026-12-09" and state["updated"] == "2026-12-08")


def boom(*a, **k):
    raise RuntimeError("DNSE down")


from tradingagents.dataflows import ohlcv  # noqa: E402

real = ohlcv.load_ohlcv
ohlcv.load_ohlcv = boom
try:
    state = eb.refresh(DATA, CAPITAL, "2026-12-10")
finally:
    ohlcv.load_ohlcv = real
t.ok("the source failing: the previous history stays, the error is recorded, nothing raised",
     state["history"][-1]["date"] == "2026-12-09" and "DNSE down" in state["error"] and state["updated"] == "2026-12-08")
t.ok("...and the line says so", "⚠️ RuntimeError: DNSE down" in eb.line(state) and "NAV" in eb.line(state))
state = eb.refresh(DATA, CAPITAL, "2026-12-10", frame=frame(DAYS, [25_000.0, float("nan"), 25_500.0, 24_000.0, 27_000.0]))
t.ok("a NaN close is skipped, the error cleared after a good refresh",
     "2026-12-02" not in {h["date"] for h in state["history"]} and state["error"] == "" and len(state["history"]) == 4)

# ───────────── 4. BENCH_ETF_START and another ETF ─────────────
DATA2 = _common.ROOT / "etf-data-2"
DATA2.mkdir()
os.environ["BENCH_ETF_START"] = "2026-12-03"
state = eb.refresh(DATA2, CAPITAL, "2026-12-07", frame=frame())
t.ok("BENCH_ETF_START: bought at the close of that session, nothing before it",
     state["start"] == "2026-12-03" == state["bought"] and state["entry"] == 25_500.0
     and [h["date"] for h in state["history"]] == DAYS[2:] and state["history"][0]["nav"] < CAPITAL)
os.environ["BENCH_ETF_START"] = "2026-12-06"      # a Saturday: the first session after it
state2 = eb.refresh(DATA2, CAPITAL, "2026-12-07", frame=frame())
t.ok("a start that is no session: the state already started is kept (the start is frozen once bought)",
     state2["bought"] == "2026-12-03" and len(state2["history"]) == 3)
os.environ["BENCH_ETF_START"] = "bogus"
t.ok("a malformed BENCH_ETF_START is ignored", eb.start_env() == "")
os.environ.pop("BENCH_ETF_START")
os.environ["BENCH_ETF"] = "fuevfvnd"
state3 = eb.refresh(DATA2, CAPITAL, "2026-12-07", frame=frame())
t.ok("BENCH_ETF changed: a new benchmark of that ETF starts (upper-cased), the old one is replaced",
     state3["symbol"] == "FUEVFVND" and state3["start"] == "2026-12-07" and len(state3["history"]) == 1
     and eb.load(DATA2)["symbol"] == "FUEVFVND")
os.environ.pop("BENCH_ETF")

# ───────────── 5. the texts and the series ─────────────
t.ok("no state: a line that says so", eb.line(None).startswith("📈 ETF E1VFVN30 mua-giữ: chưa có phiên nào")
     and eb.stats(None) is None and eb.series(None) == [])
line = eb.line(eb.load(DATA))
t.ok("the line: NAV, return since the start, if sold today after fee and tax, DD",
     line.startswith("📈 ETF E1VFVN30 mua-giữ: NAV ") and "từ 2026-12-01" in line and "bán hôm nay" in line and "DD " in line, line)
t.ok("series for the chart: {date, nav}", eb.series(eb.load(DATA))[0] == {"date": "2026-12-01", "nav": 998_503_750})

# ───────────── 6. the trader's lines ─────────────
from vnbot import trader as tr  # noqa: E402

text = tr.TradingBot._comparison(NS(stats=lambda: {"return_pct": 1.0, "max_drawdown_pct": 2.0}, start_date="2026-12-01"),
                                 NS(stats=lambda: {"return_pct": -1.0, "max_drawdown_pct": 3.0}, start_date="2026-12-01"),
                                 None, eb.load(DATA))
t.ok("the comparison of the evening report ends with the ETF line", text.splitlines()[-1].startswith("📈 ETF E1VFVN30 mua-giữ"), text)
calls = []
fake = NS(settings=NS(data_dir=DATA), paper_capital=lambda: CAPITAL, notify=calls.append)
ohlcv.load_ohlcv = boom
try:
    got = tr.TradingBot._etf_refresh(fake, "2026-12-11")
finally:
    ohlcv.load_ohlcv = real
t.ok("the run's refresh never raises; on a failure it returns the state kept", got is not None and "DNSE down" in got["error"])

t.done()
