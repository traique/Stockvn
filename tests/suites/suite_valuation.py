"""A stock's valuation against its own history (tradingagents.dataflows.vn_valuation) and the
fundamentals report around it: the percentile math, the book switching on its publicDate, the
unadjusted price across a bonus issue, the P/E path of a non-bank, the day cache, VCI's minus
signs. Data recorded from VCI on 29/10/2026 (tests/fixtures/valuation_*_2026-10-28.json)."""
import json
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

import _common  # noqa: F401 - clean env, no .env, offline, temp folder, repo on sys.path

import pandas as pd  # noqa: E402

from tradingagents.dataflows.config import set_config  # noqa: E402

CACHE = Path(tempfile.mkdtemp(prefix="valuation-cache-"))
set_config({"data_cache_dir": str(CACHE)})

from tradingagents.dataflows import vn_fundamentals as vf  # noqa: E402
from tradingagents.dataflows import vn_valuation as vv  # noqa: E402
from tradingagents.dataflows.errors import NoMarketDataError, VendorUnavailableError  # noqa: E402
from tradingagents.dataflows.vendors import flows, vci  # noqa: E402
from tradingagents.market import vn_rules  # noqa: E402

t = _common.Checks()
FX = {s: json.loads((_common.FIXTURES / f"valuation_{s}_2026-10-28.json").read_text(encoding="utf-8"))
      for s in ("CTG", "FPT")}


def fixture_prices(sym: str) -> pd.DataFrame:
    """VCI price-history as recorded: the sessions as day gaps from the first, the shares as change
    points (CTG every session since 2018; FPT every 5th, the last 10 and 10-24/12/2025)."""
    p = FX[sym]["prices"]
    days = [pd.Timestamp(p["start"])]
    for gap in p["gaps"]:
        days.append(days[-1] + pd.Timedelta(days=gap))
    changes = [(pd.Timestamp(d), s) for d, s in p["TotalShares"]]
    shares = [next(s for d, s in reversed(changes) if d <= day) for day in days]
    return pd.DataFrame({"Date": days, "Close": [float(c) for c in p["Close"]], "TotalShares": [float(s) for s in shares]})


PRICES = {s: fixture_prices(s) for s in FX}
STATEMENTS = {s: {"BALANCE_SHEET": FX[s]["balance_sheet"], "INCOME_STATEMENT": FX[s]["income_statement"]} for s in FX}
CALLS: list[tuple[str, str, str]] = []
FAIL = {"flows": None}


def fake_flows(symbol, start, end):
    CALLS.append((symbol, start, end))
    if FAIL["flows"]:
        raise FAIL["flows"]
    f = PRICES[symbol]
    return f[(f["Date"] >= pd.Timestamp(start)) & (f["Date"] <= pd.Timestamp(end))].assign(ForeignBuy=0.0)


def fake_statement(symbol, section):
    if symbol not in STATEMENTS:
        raise NoMarketDataError(symbol, symbol, "not in the fixtures")
    return {"quarters": [dict(r) for r in STATEMENTS[symbol][section]], "years": []}


def fake_ratios(symbol):
    if symbol not in FX:
        raise NoMarketDataError(symbol, symbol, "not in the fixtures")
    return [dict(r) for r in FX[symbol]["ratios"]]


def no_adjusted_prices(*args, **kwargs):
    raise AssertionError("the valuation must not read adjusted prices (vci.fetch_ohlcv)")


def no_details(symbol):
    raise VendorUnavailableError("offline")


flows.fetch_stock_flows = fake_flows
vci.fetch_statement = fake_statement
vci.fetch_ratios = fake_ratios
vci.fetch_ohlcv = no_adjusted_prices
vci.fetch_details = no_details
NOW = {"t": datetime(2026, 10, 29, 10, 0, tzinfo=vn_rules.VN_TZ)}
vn_rules.now_vn = lambda: NOW["t"]

# ───────────── 1. the percentile math ─────────────
days = pd.bdate_range("2025-01-01", periods=205)
synthetic = pd.DataFrame({"Date": days, "Close": 1.0, "Base": 1.0, "Period": "Q4/2024",
                          "PeriodPublished": date(2025, 1, 1),
                          "Multiple": [float("nan")] * 5 + [1.0] * 50 + [2.0] * 100 + [3.0] * 49 + [2.0]})
rank = vv.rank_of(synthetic, "pb")
t.ok("Y = share of sessions with a HIGHER multiple (ties not counted), today included in the count",
     rank is not None and rank.sessions == 200 and abs(rank.higher_pct - 49 / 200 * 100) < 1e-9, rank)
t.ok("sessions before the first statement (no multiple) are left out; 'since' is the first with one",
     rank.since == days[5].date() and rank.low == 1.0 and rank.high == 3.0 and rank.day == days[-1].date(), rank)
t.ok(f"fewer than {vv.MIN_SESSIONS} sessions with a multiple: no rank",
     vv.rank_of(synthetic.iloc[-vv.MIN_SESSIONS + 1:], "pb") is None
     and vv.rank_of(synthetic.iloc[-vv.MIN_SESSIONS:], "pb") is not None)
t.ok("no multiple on the last session: no rank", vv.rank_of(synthetic.assign(Multiple=synthetic["Multiple"].where(
    synthetic.index < len(synthetic) - 1)), "pb") is None)
t.ok("an empty history: no rank", vv.rank_of(synthetic.iloc[0:0], "pb") is None)

# ───────────── 2. CTG on 28/10/2026, as the PDF profile (P/B 1,20; 73%) ─────────────
ctg = vv.rank_of(vv.multiple_history("CTG", "pb", "2026-10-28"), "pb")
book_q2 = 200_214_302e6 - 1_257_755e6
t.ok("CTG 28/10: P/B = unadjusted close × shares of the day / (bsa78 − bsa210) of Q2/2026",
     ctg.day == date(2026, 10, 28) and ctg.close == 30_700 and ctg.period == "Q2/2026"
     and ctg.published == date(2026, 8, 19) and abs(ctg.value - 30_700 * 7_766_944_637 / book_q2) < 1e-9, ctg)
t.ok("CTG 28/10: P/B 1,20 and 74% of 2.123 sessions since 02/05/2018 dearer (PDF: 1,20 and 73%)",
     round(ctg.value, 2) == 1.20 and abs(ctg.higher_pct - 74.4) < 0.1 and ctg.sessions == 2123
     and ctg.since == date(2018, 5, 2), ctg)
line = vv.multiple_line("CTG", "pb", "2026-10-28")
t.ok("the line: today's P/B, its session and book, 'rẻ hơn 74% số phiên từ 05/2018 (2.123 phiên'",
     line.startswith("- P/B hôm nay 1,20 (giá đóng cửa 28/10/2026: 30.700; vốn chủ sở hữu công ty mẹ tới Q2/2026, "
                     "công bố 19/08/2026): rẻ hơn 74% số phiên từ 05/2018 (2.123 phiên;"), line)

# ───────────── 3. point-in-time: the book of the quarter published by each session ─────────────
before, after = (vv.rank_of(vv.multiple_history("CTG", "pb", d), "pb") for d in ("2026-08-18", "2026-08-19"))
book_q1 = 188_692_158e6 - 1_249_495e6
t.ok("as_of 18/08/2026: Q1/2026 book (Q2/2026 is published 19/08)",
     before.day == date(2026, 8, 18) and before.period == "Q1/2026" and before.published == date(2026, 4, 29)
     and abs(before.value - before.close * 7_766_944_637 / book_q1) < 1e-9, before)
t.ok("as_of 19/08/2026: Q2/2026 book from its publication day", after.period == "Q2/2026"
     and after.published == date(2026, 8, 19), after)
t.ok("as_of 18/08/2026: no session after the run date is counted (2.075 phiên)", before.sessions == 2075, before.sessions)
t.ok("no statement published yet (as_of 01/05/2018): 'chưa đủ dữ liệu'",
     vv.multiple_line("CTG", "pb", "2018-05-01") == "- P/B hôm nay so với lịch sử của chính mã: chưa đủ dữ liệu")

# ───────────── 4. the unadjusted price across the 17/12/2025 bonus issue (44,6%) ─────────────
hist = vv.multiple_history("CTG", "pb", "2025-12-31").set_index("Date")
d16, d17 = hist.loc[pd.Timestamp("2025-12-16")], hist.loc[pd.Timestamp("2025-12-17")]
t.ok("16→17/12/2025: the unadjusted close falls 32% and the shares rise 44,6% the same day",
     d16["Close"] == 51_000 and d17["Close"] == 34_700 and abs(d17["TotalShares"] / d16["TotalShares"] - 1.446) < 0.001)
t.ok("... so the P/B stays continuous (< 3% apart), on the same Q3/2025 book",
     d16["Period"] == d17["Period"] == "Q3/2025" and abs(d17["Multiple"] / d16["Multiple"] - 1) < 0.03,
     (d16["Multiple"], d17["Multiple"]))

# ───────────── 5. P/E of a non-bank (FPT): market value / isa22 of 4 published quarters ─────────────
fpt = vv.rank_of(vv.multiple_history("FPT", "pe", "2026-10-28"), "pe")
inc = {(r["yearReport"], r["lengthReport"]): r for r in FX["FPT"]["income_statement"]}
ttm = sum(inc[k]["isa22"] for k in ((2025, 3), (2025, 4), (2026, 1), (2026, 2)))
t.ok("FPT 28/10: P/E = 54.000 × 1.885.727.648 / Σ isa22 Q3/2025..Q2/2026 (≈ 10,18)",
     fpt.close == 54_000 and fpt.period == "Q2/2026" and fpt.published == date(2026, 8, 22)
     and abs(fpt.value - 54_000 * 1_885_727_648 / ttm) < 1e-9 and round(fpt.value, 2) == 10.18, fpt)
ctg_pe = vv.rank_of(vv.multiple_history("CTG", "pe", "2026-10-28"), "pe")
t.ok("not from the quarterly EPS: CTG's isa23 is 0 in every quarter, yet its P/E is VCI's 5,71 at today's price",
     all(r["isa23"] == 0 for r in FX["CTG"]["income_statement"])
     and abs(ctg_pe.value / (FX["CTG"]["ratios"][-1]["pe"] * 30_700 / 29_550) - 1) < 0.01, ctg_pe)
q4_2018 = vf.published_on(inc[(2018, 4)])
t.ok("FPT: the first P/E once 4 quarters are out (the first session after Q4/2018 is published)",
     q4_2018.year == 2019 and fpt.since == min(d.date() for d in PRICES["FPT"]["Date"] if d.date() >= q4_2018),
     (q4_2018, fpt.since))
pre = vv.rank_of(vv.multiple_history("FPT", "pe", "2026-08-21"), "pe")
ttm_q1 = sum(inc[k]["isa22"] for k in ((2025, 2), (2025, 3), (2025, 4), (2026, 1)))
pre_shares = PRICES["FPT"].set_index("Date").loc[pd.Timestamp(pre.day), "TotalShares"]
t.ok("FPT 21/08/2026: the 4 quarters up to Q1/2026 (Q2/2026 out 22/08)",
     pre.period == "Q1/2026" and abs(pre.value - pre.close * pre_shares / ttm_q1) < 1e-9, pre)
gap = [r for r in FX["FPT"]["income_statement"] if (r["yearReport"], r["lengthReport"]) != (2025, 3)]
STATEMENTS["FPT"]["INCOME_STATEMENT"] = gap
steps = vv._base_steps("FPT", "pe").set_index("Period")
t.ok("a missing quarter: no P/E while the 4 newest are not consecutive",
     pd.isna(steps.loc["Q2/2026", "Base"]) and pd.isna(steps.loc["Q4/2025", "Base"])
     and steps.loc["Q2/2025", "Base"] > 0, steps["Base"].tail(5).tolist())
STATEMENTS["FPT"]["INCOME_STATEMENT"] = [dict(r, isa22=-r["isa22"]) if r["yearReport"] == 2026 else r
                                         for r in FX["FPT"]["income_statement"]]
t.ok("4-quarter profit ≤ 0: P/E not computed, said so",
     vv.multiple_line("FPT", "pe", "2026-10-28").startswith("- P/E hôm nay: không tính được (LNST công ty mẹ 4 quý tới Q2/2026 ≤ 0)"))
STATEMENTS["FPT"]["INCOME_STATEMENT"] = FX["FPT"]["income_statement"]

# ───────────── 6. VCI's minus signs ─────────────
row = FX["CTG"]["ratios"][-1]
signed = ("cir", "costToIncome", "loansLossReservesToNPLs", "provisionToOutstandingLoans", "averageCostOfFinancing")
t.ok("CTG Q2/2026 from VCI: the five bank ratios come negative", all(row[f] < 0 for f in signed), {f: row[f] for f in signed})
t.ok("ratio_value: printed positive (CIR 24,09%, cover 133,95%, provision 0,95%, cost of funds 3,40%)",
     all(vf.ratio_value(row, f) == -row[f] for f in signed) and round(vf.ratio_value(row, "cir"), 4) == 0.2409
     and round(vf.ratio_value(row, "loansLossReservesToNPLs"), 4) == 1.3395, [vf.ratio_value(row, f) for f in signed])
t.ok("ratio_value: other fields keep their sign (a negative growth stays negative)",
     vf.ratio_value({"loansGrowth": -0.02}, "loansGrowth") == -0.02 and vf.ratio_value({"cir": None}, "cir") is None)

# ───────────── 7. the fundamentals report ─────────────
report = vf.get_fundamentals("CTG", "2026-10-28")
t.ok("get_fundamentals: CIR printed positive", "CIR: 24,09%" in report and "CIR: -" not in report, report)
t.ok("get_fundamentals: today's P/B line replaces the 20-quarter 'PB hiện tại' line",
     line in report and "PB hiện tại" not in report and "bách phân vị" not in report, report)
t.ok("get_fundamentals: VCI's own P/E/P/B marked as not today's price", "không phải giá hôm nay" in report)
old = vf.get_fundamentals("CTG", "2026-08-18")
t.ok("get_fundamentals as_of 18/08/2026: Q1/2026 period and Q1/2026 book",
     "kỳ báo cáo mới nhất đã công bố: Q1/2026" in old and "tới Q1/2026, công bố 29/04/2026" in old
     and "CIR: 24,94%" in old, old)
fpt_report = vf.get_fundamentals("FPT", "2026-10-28")
t.ok("get_fundamentals FPT: the P/E line", "- P/E hôm nay 10,18 (giá đóng cửa 28/10/2026: 54.000; LNST công ty mẹ 4 quý"
     in fpt_report, fpt_report)
saved_history = vv.multiple_history
vv.multiple_history = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
t.ok("the history line failing: 'chưa đủ dữ liệu', the report still comes",
     "- P/B hôm nay so với lịch sử của chính mã: chưa đủ dữ liệu" in vf.get_fundamentals("CTG", "2026-10-28"))
vv.multiple_history = saved_history

# ───────────── 8. the day cache ─────────────
cache_file = CACHE / "valuation" / "CTG-prices.json"
first = [c for c in CALLS if c[0] == "CTG"]
t.ok("cache: one VCI fetch for CTG today, from 2018 (every run above read the file)",
     first == [("CTG", vv.HISTORY_START, "2026-10-29")] and cache_file.exists(), first)
CALLS.clear()
NOW["t"] += timedelta(days=1)
PRICES["CTG"] = pd.concat([PRICES["CTG"], pd.DataFrame(
    {"Date": [pd.Timestamp("2026-10-29")], "Close": [31_000.0], "TotalShares": [7_766_944_637.0]})], ignore_index=True)
PRICES["CTG"].loc[PRICES["CTG"]["Date"] == pd.Timestamp("2026-10-28"), "Close"] = 30_750.0   # a late correction
FAIL["flows"] = VendorUnavailableError("down")
stale = vv.price_history("CTG")
t.ok("next day, VCI down: the cached rows, and asked again on the next call",
     len(stale) == 2201 and stale["Date"].iloc[-1] == pd.Timestamp("2026-10-28") and len(CALLS) == 1)
FAIL["flows"] = None
fresh = vv.price_history("CTG")
t.ok("next day: fetched from the last cached session on, merged without duplicates",
     CALLS[-1] == ("CTG", "2026-10-28", "2026-10-30") and len(fresh) == 2202 and fresh["Date"].is_unique
     and fresh["Close"].iloc[-2] == 30_750 and fresh["Close"].iloc[-1] == 31_000, CALLS)
vv.price_history("CTG")
vv.multiple_line("CTG", "pb", None)
t.ok("... then not asked again that day", len(CALLS) == 2, CALLS)
(CACHE / "valuation" / "FPT-prices.json").unlink()
FAIL["flows"] = VendorUnavailableError("down")
t.ok("no cache and VCI down: 'chưa đủ dữ liệu'", vv.price_history("FPT").empty
     and vv.multiple_line("FPT", "pe", None) == "- P/E hôm nay so với lịch sử của chính mã: chưa đủ dữ liệu")
FAIL["flows"] = None
PRICES["NEW"] = PRICES["FPT"].iloc[0:0]
t.ok("a stock VCI has no sessions for: an empty history, kept for the day", vv.price_history("NEW").empty
     and vv.price_history("NEW").empty and [c[0] for c in CALLS].count("NEW") == 1, CALLS)

# ───────────── 9. informational only ─────────────
repo = _common.REPO
readers = sorted(p.relative_to(repo).as_posix() for d in ("tradingagents", "vnbot") for p in (repo / d).rglob("*.py")
                 if "vn_valuation" in p.read_text(encoding="utf-8", errors="replace"))
t.ok("only the fundamentals report reads vn_valuation (no engine, policy, simulator, calibration)",
     readers == ["tradingagents/dataflows/vn_fundamentals.py"], readers)

t.done()
