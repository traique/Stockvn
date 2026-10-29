"""A stock's valuation against its own history (tradingagents.dataflows.vn_valuation) and the
fundamentals report around it: the percentile math, the book switching on its publicDate, the
unadjusted price across a bonus issue, the P/E path of a non-bank, the session VCI has not posted
yet (from the engine's bars), the cache (hourly re-ask, VCI breaker), VCI's minus signs, VCI's own
period multiples withheld from a past run. Data recorded from VCI on 29/10/2026
(tests/fixtures/valuation_*_2026-10-28.json)."""
import json
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import _common  # noqa: F401 - clean env, no .env, offline, temp folder, repo on sys.path

import pandas as pd  # noqa: E402

from tradingagents.dataflows.config import set_config  # noqa: E402

CACHE = Path(tempfile.mkdtemp(prefix="valuation-cache-"))
set_config({"data_cache_dir": str(CACHE)})

from tradingagents.dataflows import money_flows, ohlcv  # noqa: E402
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


BARS: dict[str, dict] = {}     # the engine's bars beyond / instead of the price-history closes
BAR_CALLS: list[tuple[str, str]] = []


def fake_bars(symbol, as_of_date, fill_gaps=True):
    """ohlcv.load_ohlcv: closed sessions up to the run date, the price-history closes unless BARS says otherwise."""
    BAR_CALLS.append((symbol, as_of_date))
    closes = dict(zip(PRICES[symbol]["Date"], PRICES[symbol]["Close"])) | BARS.get(symbol, {})
    rows = sorted((d, c) for d, c in closes.items() if d <= pd.Timestamp(as_of_date))
    if not rows:
        raise NoMarketDataError(symbol, symbol, "no bars")
    return pd.DataFrame(rows, columns=["Date", "Close"])


def no_adjusted_prices(*args, **kwargs):
    raise AssertionError("the valuation reads bars only through ohlcv.load_ohlcv, never VCI's adjusted history")


def no_details(symbol):
    raise VendorUnavailableError("offline")


flows.fetch_stock_flows = fake_flows
vci.fetch_statement = fake_statement
vci.fetch_ratios = fake_ratios
vci.fetch_ohlcv = no_adjusted_prices
ohlcv.load_ohlcv = fake_bars
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
real_history = vv.multiple_history
for multiples, want in (([1.0] + [2.0] * 398 + [1.5], "rẻ hơn 99% số phiên"),    # 99,5%, one cheaper session
                        ([2.0] * 399 + [1.0], "rẻ hơn 100% số phiên"),            # 99,75%, the cheapest ever
                        ([3.0] + [2.0] * 199, "rẻ hơn 1% số phiên"),              # 0,5%, one dearer session
                        ([1.0] * 199 + [2.0], "rẻ hơn 0% số phiên")):             # the dearest ever
    frame = pd.DataFrame({"Date": pd.bdate_range("2024-01-01", periods=len(multiples)), "Close": 1.0, "Base": 1.0,
                          "Period": "Q4/2023", "PeriodPublished": date(2024, 1, 1), "Multiple": multiples})
    vv.multiple_history = lambda *a, frame=frame: frame
    t.ok(f"rounded, never 100% with a cheaper session nor 0% with a dearer one: {want}",
         f"): {want} từ" in vv.multiple_line("CTG", "pb", None), vv.multiple_line("CTG", "pb", None))
vv.multiple_history = real_history

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
t.ok("VCI has the run date's session: the engine's bars are not read", BAR_CALLS == [], BAR_CALLS)

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
peer_calls = []
real_peers = vf._peer_benchmark
vf._peer_benchmark = lambda *a: peer_calls.append(a) or real_peers(*a)
report = vf.get_fundamentals("CTG", "2026-10-28")
t.ok("get_fundamentals: CIR printed positive", "CIR: 24,09%" in report and "CIR: -" not in report, report)
t.ok("get_fundamentals: today's P/B line replaces the 20-quarter 'PB hiện tại' line",
     line in report and "PB hiện tại" not in report and "bách phân vị" not in report, report)
# VCI prices a period's P/E, P/B, market value on its update day: Q2/2026 (out 19/08) at the 14/10 close
q2 = FX["CTG"]["ratios"][-1]
t.ok("VCI's Q2/2026 figures are priced at 29.550 (close of 14/10/2026), not at 30/06 nor at the publication",
     q2["marketCap"] / q2["numberOfSharesMktCap"] == 29_550
     and PRICES["CTG"].set_index("Date").loc[pd.Timestamp("2026-10-14"), "Close"] == 29_550
     and PRICES["CTG"].set_index("Date").loc[pd.Timestamp("2026-06-30"), "Close"] == 33_950)
sept = vf.get_fundamentals("CTG", "2026-09-01")
t.ok("a past run (01/09/2026): VCI's P/E, P/B, market value withheld (priced 14/10, after the run date)",
     "- P/E, P/B, P/S, EV/EBITDA, vốn hoá và số cổ phiếu của VCI: bị giữ lại cho lượt phân tích ngày 2026-09-01" in sept
     and "P/E: 5,71" not in sept and "P/B: 1,15" not in sept and "Vốn hoá" not in sept and "229.513" not in sept, sept)
t.ok("... and the peer average of VCI's figures: withheld, not asked",
     "- So sánh ngành: bị giữ lại cho lượt phân tích ngày 2026-09-01" in sept and "trung bình" not in sept
     and peer_calls == [], peer_calls)
t.ok("... the run date's own P/B stays: 1,25 at the last close before it (31/08-02/09 are holidays)",
     "- P/B phiên gần nhất có dữ liệu 1,25 (giá đóng cửa 28/08/2026:" in sept, sept)
live = vf.get_fundamentals("CTG")
t.ok("a live run: VCI's P/E, P/B, market value, labelled as priced when VCI updated them",
     "- P/E: 5,71 | P/B: 1,15 | P/S:" in live and "(số VCI của kỳ, tính theo giá lúc VCI cập nhật, không phải giá hôm nay)" in live
     and "- Vốn hoá theo giá VCI cập nhật: 229.513,2 tỷ | Số cổ phiếu: 7.766.944.637" in live
     and "bị giữ lại cho lượt" not in live.split("## Hồ sơ hiện tại")[0] and len(peer_calls) == 1, live)
t.ok("a live run at 10:00 (today's bar not final, VCI not posted): 'phiên gần nhất có dữ liệu', the 28/10 close",
     "- P/B phiên gần nhất có dữ liệu 1,20 (giá đóng cửa 28/10/2026: 30.700;" in live, live)
vf._peer_benchmark = real_peers
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
t.ok("next day, VCI down: the cached rows",
     len(stale) == 2201 and stale["Date"].iloc[-1] == pd.Timestamp("2026-10-28") and len(CALLS) == 1)
FAIL["flows"] = None
t.ok("... and the shared VCI breaker trips: the next minutes get the cached rows at once, VCI not asked",
     len(vv.price_history("CTG")) == 2201 and len(CALLS) == 1, CALLS)
money_flows.reset_breaker()
fresh = vv.price_history("CTG")
t.ok("next day: fetched from the last cached session on, merged without duplicates",
     CALLS[-1] == ("CTG", "2026-10-28", "2026-10-30") and len(fresh) == 2202 and fresh["Date"].is_unique
     and fresh["Close"].iloc[-2] == 30_750 and fresh["Close"].iloc[-1] == 31_000, CALLS)
vv.price_history("CTG")
vv.multiple_line("CTG", "pb", None)
t.ok("... then not asked again while it holds the last session due (29/10 before 15:30)", len(CALLS) == 2, CALLS)


def age_stamp(sym: str, seconds: float) -> None:
    saved = json.loads((CACHE / "valuation" / f"{sym}-prices.json").read_text(encoding="utf-8"))
    saved["asked_at"] = time.time() - seconds
    (CACHE / "valuation" / f"{sym}-prices.json").write_text(json.dumps(saved), encoding="utf-8")


NOW["t"] = datetime(2026, 10, 30, 16, 0, tzinfo=vn_rules.VN_TZ)   # 30/10 due from 15:30, not posted yet
vv.price_history("CTG")
t.ok("stamped today, the 30/10 session due and missing: not asked again within the hour", len(CALLS) == 2, CALLS)
age_stamp("CTG", money_flows.RECHECK_SECONDS + 1)
vv.price_history("CTG")
vv.price_history("CTG")
t.ok("... asked again an hour later (from the last cached session), once", len(CALLS) == 3
     and CALLS[-1] == ("CTG", "2026-10-29", "2026-10-30"), CALLS)
PRICES["CTG"] = pd.concat([PRICES["CTG"], pd.DataFrame(
    {"Date": [pd.Timestamp("2026-10-30")], "Close": [31_200.0], "TotalShares": [7_766_944_637.0]})], ignore_index=True)
age_stamp("CTG", money_flows.RECHECK_SECONDS + 1)
got = vv.price_history("CTG")
age_stamp("CTG", money_flows.RECHECK_SECONDS + 1)
vv.price_history("CTG")
t.ok("... VCI posts 30/10: it is kept, and not asked again that day however long after",
     got["Date"].iloc[-1] == pd.Timestamp("2026-10-30") and got["Close"].iloc[-1] == 31_200 and len(CALLS) == 4, CALLS)
(CACHE / "valuation" / "FPT-prices.json").unlink()
FAIL["flows"] = VendorUnavailableError("down")
t.ok("no cache and VCI down: 'chưa đủ dữ liệu'", vv.price_history("FPT").empty
     and vv.multiple_line("FPT", "pe", None) == "- P/E hôm nay so với lịch sử của chính mã: chưa đủ dữ liệu")
FAIL["flows"] = None
money_flows.reset_breaker()
PRICES["NEW"] = PRICES["FPT"].iloc[0:0]
t.ok("a stock VCI has no sessions for: an empty history, kept for the day", vv.price_history("NEW").empty
     and vv.price_history("NEW").empty and [c[0] for c in CALLS].count("NEW") == 1, CALLS)

# ───────────── 9. the run date's session VCI has not posted yet: from the engine's bars ─────────────
# Live 29/10/2026 22:09: VCI price-history ends 28/10, the engine's bars hold 29/10 (FPT 52.700, CTG 30.700).
NOW["t"] = datetime(2026, 10, 29, 22, 0, tzinfo=vn_rules.VN_TZ)
PRICES["FPT"] = fixture_prices("FPT")
(CACHE / "valuation" / "FPT-prices.json").unlink(missing_ok=True)
BARS["FPT"] = {pd.Timestamp("2026-10-29"): 52_700.0}
BAR_CALLS.clear()
topped = vv.rank_of(vv.multiple_history("FPT", "pe", None), "pe")
t.ok("VCI lacks the run date's session (29/10): its close from the engine's bars, the shares carried forward",
     topped.day == date(2026, 10, 29) and topped.close == 52_700 and topped.period == "Q2/2026"
     and abs(topped.value - 52_700 * 1_885_727_648 / ttm) < 1e-9 and topped.sessions == fpt.sessions + 1
     and BAR_CALLS == [("FPT", "2026-10-29")], (topped, BAR_CALLS))
t.ok("... the line says 'hôm nay' with the 29/10 close",
     vv.multiple_line("FPT", "pe", None).startswith("- P/E hôm nay 9,94 (giá đóng cửa 29/10/2026: 52.700;"),
     vv.multiple_line("FPT", "pe", None))
t.ok("... the session is not saved in the cache: VCI's own row replaces it once posted",
     vv.price_history("FPT")["Date"].iloc[-1] == pd.Timestamp("2026-10-28"))
BARS["FPT"][pd.Timestamp("2026-10-28")] = 53_200.0     # the bars' 28/10 is adjusted: an ex-date since
t.ok("the bars' close of VCI's last session differs (a dividend or bonus issue went ex): not used, "
     "'phiên gần nhất có dữ liệu'",
     vv.multiple_line("FPT", "pe", None).startswith("- P/E phiên gần nhất có dữ liệu 10,18 (giá đóng cửa 28/10/2026: 54.000;"),
     vv.multiple_line("FPT", "pe", None))
BARS["FPT"] = {}
ohlcv.load_ohlcv = lambda *a, **k: (_ for _ in ()).throw(VendorUnavailableError("bars down"))
t.ok("the bars unavailable: VCI's last session, said so",
     vv.multiple_line("FPT", "pe", "2026-10-29").startswith("- P/E phiên gần nhất có dữ liệu 10,18 (giá đóng cửa 28/10/2026"))
ohlcv.load_ohlcv = fake_bars

# ───────────── 10. informational only ─────────────
repo = _common.REPO
readers = sorted(p.relative_to(repo).as_posix() for d in ("tradingagents", "vnbot") for p in (repo / d).rglob("*.py")
                 if "vn_valuation" in p.read_text(encoding="utf-8", errors="replace")
                 and p.name not in ("vn_valuation.py", "valuation_config.py"))
t.ok("only text writers read vn_valuation: the fundamentals report, the card after the engine's report, the Output "
     "Guard, /nhanh, the /taisan page (no engine, policy, simulator, calibration)",
     readers == ["tradingagents/agents/output_guard.py", "tradingagents/agents/quant_engine.py",
                 "tradingagents/dataflows/vn_fundamentals.py", "vnbot/service.py", "vnbot/taisan_web.py"], readers)

t.done()
