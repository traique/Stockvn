"""The valuation card of a bank (tradingagents.dataflows.vn_valuation.card) and the Output Guard's
fair-value check: the PDF profile's Base reproduced, the book point-in-time (cash dividends after
the quarter's end, the bonus issue), the annual ROE by its publication, the labels, the card in the
engine's report, the fundamentals tool and /nhanh, and fair values off the card flagged in the
fundamentals report and the guarded outputs. CTG data recorded from VCI on 29/10/2026
(tests/fixtures/valuation_CTG_2026-10-28.json)."""
import json
import os
import re
import tempfile
import time
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import _common  # noqa: F401 - clean env, no .env, offline, temp folder, repo on sys.path

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from tradingagents.dataflows.config import set_config  # noqa: E402

set_config({"data_cache_dir": tempfile.mkdtemp(prefix="valuation-card-")})

from tradingagents.agents import output_guard, quant_engine as qe  # noqa: E402
from tradingagents.dataflows import money_flows, ohlcv, valuation_config  # noqa: E402
from tradingagents.dataflows import vn_fundamentals as vf  # noqa: E402
from tradingagents.dataflows import vn_valuation as vv  # noqa: E402
from tradingagents.dataflows.errors import NoMarketDataError, VendorUnavailableError  # noqa: E402
from tradingagents.dataflows.vendors import flows, vci  # noqa: E402
from tradingagents.market import vn_rules  # noqa: E402
from tradingagents.quant import engine, report_format as rfmt  # noqa: E402
from vnbot import service  # noqa: E402

t = _common.Checks()
FX = {s: json.loads((_common.FIXTURES / f"valuation_{s}_2026-10-28.json").read_text(encoding="utf-8"))
      for s in ("CTG", "FPT")}


def fixture_prices(sym: str) -> pd.DataFrame:
    """VCI price-history as recorded (see suite_valuation): day gaps from the first, shares as change points."""
    p = FX[sym]["prices"]
    days = [pd.Timestamp(p["start"])]
    for gap in p["gaps"]:
        days.append(days[-1] + pd.Timedelta(days=gap))
    changes = [(pd.Timestamp(d), s) for d, s in p["TotalShares"]]
    shares = [next(s for d, s in reversed(changes) if d <= day) for day in days]
    return pd.DataFrame({"Date": days, "Close": [float(c) for c in p["Close"]], "TotalShares": [float(s) for s in shares]})


PRICES = {s: fixture_prices(s) for s in FX}
CALLS = {"statement": 0, "events": []}


def fake_flows(symbol, start, end):
    f = PRICES[symbol]
    return f[(f["Date"] >= pd.Timestamp(start)) & (f["Date"] <= pd.Timestamp(end))].assign(ForeignBuy=0.0)


def fake_statement(symbol, section):
    if symbol not in FX:
        raise NoMarketDataError(symbol, symbol, "not in the fixtures")
    CALLS["statement"] += 1
    key = {"BALANCE_SHEET": "balance_sheet", "INCOME_STATEMENT": "income_statement"}[section]
    years = FX[symbol].get("annual_statements", []) if section == "INCOME_STATEMENT" else []
    return {"quarters": [dict(r) for r in FX[symbol][key]], "years": [dict(r) for r in years]}


def fake_ratios(symbol):
    if symbol not in FX:
        raise NoMarketDataError(symbol, symbol, "not in the fixtures")
    return [dict(r) for r in FX[symbol]["ratios"] + FX[symbol].get("annual_ratios", [])]


def fake_events(symbol, from_date, to_date, codes="DIV,ISS,DDIND,DDINS,DDRP,AGME,AGMR,EGME", size=50):
    """VCI's events filter: the ex-date or the public date within the range."""
    CALLS["events"].append((symbol, from_date, to_date, codes))
    out = []
    for e in FX.get(symbol, {}).get("events", []):
        days = {d[:10] for d in (e["exrightDate"], e["publicDate"]) if d}
        if e["eventCode"] in codes.split(",") and any(from_date <= d <= to_date for d in days):
            out.append(dict(e))
    return out


def fake_bars(symbol, as_of_date, fill_gaps=True):
    f = PRICES[symbol]
    return f[f["Date"] <= pd.Timestamp(as_of_date)][["Date", "Close"]].reset_index(drop=True)


def no_details(symbol):
    raise VendorUnavailableError("offline")


flows.fetch_stock_flows = fake_flows
vci.fetch_statement = fake_statement
vci.fetch_ratios = fake_ratios
vci.fetch_events = fake_events
vci.fetch_details = no_details
ohlcv.load_ohlcv = fake_bars
NOW = {"t": datetime(2026, 10, 29, 10, 0, tzinfo=vn_rules.VN_TZ)}
vn_rules.now_vn = lambda: NOW["t"]

BS = {(r["yearReport"], r["lengthReport"]): r for r in FX["CTG"]["balance_sheet"]}
SHARES = PRICES["CTG"].set_index("Date")["TotalShares"]
LABEL = re.compile(r"^\[(FACT|THỐNG KÊ) \d{2}/\d{2}\] ")


def labelled(text: str) -> bool:
    """Heading, header, then every line labelled [FACT dd/mm] or [THỐNG KÊ dd/mm]."""
    lines = text.splitlines()
    return (lines[0].startswith(vv.CARD_HEADING) and lines[1].startswith("Giá phiên ")
            and len(lines) > 2 and all(LABEL.match(line) for line in lines[2:]))


# ───────────── 1. the formula: the PDF profile's Base ─────────────
pdf_base = 25_166 * vv.justified_pb(0.1746, 0.0794, 0.4)
t.ok("PDF: book 25.166, ROE 17,46%, Ke 7,94%, λ 0,4, g 0 → Base ≈ 30.163 (PDF 30.164), within 0,5%",
     abs(pdf_base / 30_163 - 1) <= 0.005 and abs(pdf_base / 30_164 - 1) <= 0.005 and round(pdf_base) == 30_164, pdf_base)
implied = 0.0794 + (30_700 / 25_166 - 1) * (1 + 0.0794 - 0.6)
t.ok("... and the ROE the price 30.700 implies is 18,48% (PDF: 18,48%)", round(implied * 100, 2) == 18.48, implied)
t.ok("ROE = Ke: P/B 1 whatever λ and g; the excess return fades with λ, grows with g",
     all(abs(vv.justified_pb(0.12, 0.12, lam, g) - 1) < 1e-12 for lam in (0.2, 0.4, 1.0) for g in (0.0, 0.03))
     and vv.justified_pb(0.18, 0.12, 0.6) < vv.justified_pb(0.18, 0.12, 0.4) < vv.justified_pb(0.18, 0.12, 0.4, 0.03))

# ───────────── 2. CTG on 28/10/2026, Ke 12% by default ─────────────
c = vv.card("CTG", "2026-10-28")
equity_q2 = vv.parent_equity(BS[(2026, 2)])
t.ok("book/share = (Q2/2026 parent equity − 450đ × shares on 23/07) / shares of 28/10 = 25.166 (PDF: 25.166)",
     c.period == "Q2/2026" and c.published == date(2026, 8, 19) and c.period_end == date(2026, 6, 30)
     and c.dividends == ((date(2026, 7, 23), 450.0),) and round(c.book) == 25_166
     and abs(c.book - (equity_q2 - 450 * SHARES[pd.Timestamp("2026-07-23")]) / SHARES[pd.Timestamp("2026-10-28")]) < 1e-6, c)
roes = [r["roe"] for r in FX["CTG"]["annual_ratios"]]
t.ok("sustainable ROE = median of the 5 newest annual ROEs (2021-2025) = 17,12% (PDF: 17,1%)",
     c.roe_base == float(np.median(roes[-5:])) and round(c.roe_base * 100, 2) == 17.12
     and [y for y, _ in c.roes] == list(range(2018, 2026)) and c.annual_published == date(2026, 3, 31), c.roes)
t.ok("Bear/Bull: the stock's own annual ROE p20/p80 (8 years 2018-2025)",
     (c.roe_bear, c.roe_bull) == tuple(float(v) for v in np.percentile(roes, [20, 80])), (c.roe_bear, c.roe_bull))
a = c.assumptions
t.ok("default assumptions for banks: Ke 12%, λ 0,4, g 0, margin 13,7%",
     (a.ke, a.lam, a.g, a.margin) == (0.12, 0.4, 0.0, 0.137))
t.ok("Base/Bear/Bull = book × justified P/B; eab = Base × (1 − 13,7%); MOS = (Base − price)/Base",
     abs(c.base - c.book * vv.justified_pb(c.roe_base, 0.12, 0.4)) < 1e-6
     and abs(c.bear - c.book * vv.justified_pb(c.roe_bear, 0.12, 0.4)) < 1e-6
     and abs(c.bull - c.book * vv.justified_pb(c.roe_bull, 0.12, 0.4)) < 1e-6
     and abs(c.eab - c.base * 0.863) < 1e-6 and abs(c.mos - (c.base - 30_700) / c.base) < 1e-12
     and c.bear < c.base < c.bull, c)
t.ok("CTG 28/10 at Ke 12%: Base 27.643, Bull 28.066, price 30.700 ≥ Bull → Đắt, MOS −11,06%",
     (round(c.base), round(c.bull), c.close, c.zone, round(c.mos * 100, 2)) == (27_643, 28_066, 30_700, "Đắt", -11.06),
     (c.base, c.bull, c.zone, c.mos))
t.ok("sensitivity: Base at Ke 8/10/12/14%, falling with Ke, the 12% one is the Base",
     [k for k, _ in c.sensitivity] == [0.08, 0.10, 0.12, 0.14] and c.sensitivity[2][1] == c.base
     and all(x[1] > y[1] for x, y in zip(c.sensitivity, c.sensitivity[1:])), c.sensitivity)
t.ok("no ex-date scheduled within 45 days of 28/10 (checked live)", c.next_ex is None)
t.ok("the events are asked from the day after the quarter's end to 45 days after the run date",
     CALLS["events"][-1] == ("CTG", "2026-07-01", "2026-12-12", "DIV,ISS"), CALLS["events"])

# ───────────── 3. point-in-time ─────────────
equity_q1 = vv.parent_equity(BS[(2026, 1)])
jul = vv.card("CTG", "2026-07-22")
t.ok("as_of 22/07/2026 (the day before the 450đ ex-date): Q1/2026 book, nothing deducted",
     jul.period == "Q1/2026" and jul.dividends == () and jul.paid == 0
     and abs(jul.book - equity_q1 / SHARES[pd.Timestamp("2026-07-22")]) < 1e-6, jul)
t.ok("... the card expires on the announced ex-date 23/07 (announced 14/07)", jul.next_ex == date(2026, 7, 23)
     and "tới ngày GDKHQ kế tiếp (23/07)" in vv.render_card(jul).splitlines()[1], vv.render_card(jul).splitlines()[1])
aug = vv.card("CTG", "2026-08-18")
t.ok("as_of 18/08/2026: still Q1/2026 (Q2 out 19/08), the 450đ gone ex 23/07 deducted",
     aug.period == "Q1/2026" and aug.dividends == ((date(2026, 7, 23), 450.0),)
     and abs(aug.book - (equity_q1 - 450 * SHARES[pd.Timestamp("2026-07-23")]) / SHARES[pd.Timestamp("2026-08-18")]) < 1e-6
     and round(jul.book - aug.book) == 450, (jul.book, aug.book))
t.ok("as_of 13/07/2026: the 23/07 ex-date not announced yet (14/07): no expiry date",
     vv.card("CTG", "2026-07-13").next_ex is None)
jan = vv.card("CTG", "2026-01-15")
equity_q3 = vv.parent_equity(BS[(2025, 3)])
before_bonus = SHARES[pd.Timestamp("2025-10-14")]
t.ok("as_of 15/01/2026: Q3/2025 book over the shares after the 44,6% bonus (17/12/2025); the 450đ of "
     "14/10/2025 paid on the shares of that day",
     jan.period == "Q3/2025" and jan.dividends == ((date(2025, 10, 14), 450.0),)
     and abs(SHARES[pd.Timestamp("2026-01-15")] / before_bonus - 1.446) < 0.001
     and abs(jan.book - (equity_q3 - 450 * before_bonus) / SHARES[pd.Timestamp("2026-01-15")]) < 1e-6, jan)
# A rights issue gone ex after the quarter's end (MBB 11/08/2026, live): its shares are counted, its cash is not.
FX["CTG"]["events"].append({"eventCode": "ISS", "publicDate": "2026-05-04T00:00:00", "exrightDate": "2026-05-12T00:00:00",
                            "valuePerShare": None, "exerciseRatio": 0.1,
                            "eventTitleVi": "Phát hành cổ phiếu - Quyền mua CP cho Cổ đông hiện hữu tỉ lệ 10.0%"})
rights = vv.build_card("CTG", "2026-08-18")
rights_text = vv.render_card(rights)
t.ok("a rights issue after the quarter's end: said on the card (book and levels too low until the next statement)",
     rights.rights == ((date(2026, 5, 12), 0.1),) and rights.book == aug.book and labelled(rights_text)
     and "[FACT 12/05] Phát hành quyền mua 10,0% (GDKHQ 12/05/2026): số cổ phiếu đã tính phần mới, tiền thu về chưa "
         "có trong BCTC Q1/2026" in rights_text, rights_text)
t.ok("... not said once the statement of the quarter it fell in is out (as_of 28/10: Q2/2026)",
     vv.build_card("CTG", "2026-10-28").rights == ())
FX["CTG"]["events"].pop()
mar30, mar31 = vv.card("CTG", "2026-03-30"), vv.card("CTG", "2026-03-31")
t.ok("annual ROE by its report's publicDate: 2025 out 31/03/2026 (median 2020-2024 the day before)",
     [y for y, _ in mar30.roes] == list(range(2018, 2025)) and mar30.annual_published == date(2025, 3, 5)
     and mar30.roe_base == float(np.median(roes[2:7])) and [y for y, _ in mar31.roes][-1] == 2025, (mar30.roes, mar31.roes))
y2022 = vv.card("CTG", "2022-06-01")
t.ok("fewer than 5 years published (2018-2021 on 01/06/2022): the median of what exists, said so",
     [y for y, _ in y2022.roes] == [2018, 2019, 2020, 2021] and y2022.roe_base == float(np.median(roes[:4]))
     and "chỉ có 4 năm 2018-2021, chưa đủ 5" in vv.render_card(y2022), vv.render_card(y2022))
t.ok(f"fewer than {vv.MIN_ROE_YEARS} annual ROEs (2018-2019 on 01/03/2021): no card",
     vv.card("CTG", "2021-03-01") is None)

# ───────────── 4. labels, header, note ─────────────
text = vv.render_card(c)
lines = text.splitlines()
t.ok("heading: 'tham khảo, không vào quyết định; không phải khuyến nghị'",
     lines[0] == "--- Định giá (tham khảo, không vào quyết định; không phải khuyến nghị) - ngân hàng ---", lines[0])
t.ok("header: session, statement and its publication, the expiry",
     lines[1] == "Giá phiên 28/10 · BCTC Q2/2026 công bố 19/08 · Hạn dùng: tới khi có BCTC quý mới hoặc tới ngày "
                 "GDKHQ kế tiếp (chưa có lịch)", lines[1])
t.ok("100% of the card's lines labelled [FACT dd/mm] / [THỐNG KÊ dd/mm] (with and without a dividend, few years)",
     all(labelled(vv.render_card(x)) for x in (c, jul, aug, jan, y2022)), text)
t.ok("FACT for the statement (its publication day) and the dividend (its ex-date); THỐNG KÊ for the ROE "
     "(the annual report's day) and the model (the session)",
     lines[2].startswith("[FACT 19/08] Vốn chủ sở hữu công ty mẹ Q2/2026") and lines[3].startswith("[FACT 23/07] Cổ tức tiền")
     and lines[4].startswith("[FACT 28/10] Sổ sách/cp 25.166") and lines[5].startswith("[THỐNG KÊ 31/03] ROE bền vững 17,12%")
     and lines[6].startswith("[THỐNG KÊ 28/10] Base (giá trị hợp lý) 27.643 VND (-9,96% so với giá hiện tại)"), text)
t.ok("every level with its distance from the session's close; the zone and the sensitivity printed",
     "Bear 26.236 VND (-14,54% so với giá hiện tại)" in text and "Bull 28.066 VND (-8,58% so với giá hiện tại)" in text
     and "eab 23.856 VND (-22,29% so với giá hiện tại) = Base × (1 − 13,7%)" in text and "→ vùng Đắt" in text
     and "Độ nhạy Base theo Ke: 8% 29.946 · 10% 28.749 · 12% 27.643 · 14% 26.619" in text
     and "Ke 12% (giả định đặt tay), λ 0,4, g 0%" in text, text)
t.ok("the card's prices for the Output Guard: Bear, eab, Base, Bull, book, price, sensitivity",
     vv.card_prices("báo cáo\n\n" + text) == {26_236, 23_856, 27_643, 28_066, 25_166, 30_700, 29_946, 28_749, 26_619}
     and vv.card_prices("no card here") == set(), vv.card_prices(text))

# ───────────── 5. Ke from the environment (VALUATION_KE_BANK, in %) ─────────────
os.environ["VALUATION_KE_BANK"] = "7,94"
pdf_ke = vv.card("CTG", "2026-10-28")
t.ok("VALUATION_KE_BANK=7,94: the PDF's Ke; Base within 1% of its 30.164 (ROE 17,12% here, 17,46% there)",
     pdf_ke.assumptions.ke == 0.0794 and abs(pdf_ke.base / 30_164 - 1) < 0.01 and round(pdf_ke.base) == 29_984
     and "Ke 7,94% (giả định đặt tay)" in vv.render_card(pdf_ke), pdf_ke.base)
t.ok("... its Bull is 30.443, not the PDF's 33.309 (ROE p80 18% here; the PDF's implies ~23,5%): 30.700 is Đắt",
     round(pdf_ke.bull) == 30_443 and pdf_ke.zone == "Đắt", (pdf_ke.bull, pdf_ke.zone))
for bad in ("abc", "50", "2"):
    os.environ["VALUATION_KE_BANK"] = bad
    t.ok(f"VALUATION_KE_BANK={bad!r}: ignored, Ke 12%", valuation_config.assumptions("banking").ke == 0.12)
del os.environ["VALUATION_KE_BANK"]
t.ok("no profile for non-banks", valuation_config.assumptions("default") is None)

# ───────────── 6. the cache per (stock, run date) ─────────────
before = CALLS["statement"]
t.ok("a past run date's card is kept: no new statement read", vv.card("CTG", "2026-07-22") is jul
     and CALLS["statement"] == before)
NOW["t"] = datetime(2026, 10, 28, 20, 0, tzinfo=vn_rules.VN_TZ)
today = vv.card("CTG", None)
t.ok("today's card kept within the hour", vv.card("CTG", "2026-10-28") is today)
key = next(k for k in vv._cards if k[:2] == ("CTG", "2026-10-28"))
vv._cards[key] = (time.time() - money_flows.RECHECK_SECONDS - 1, today)
t.ok("... rebuilt after an hour (the session's close may be out)", vv.card("CTG", None) is not today
     and vv.card("CTG", None) == today)
NOW["t"] = datetime(2026, 10, 29, 10, 0, tzinfo=vn_rules.VN_TZ)

# ───────────── 7. banks only; the engine report, the fundamentals tool, /nhanh ─────────────
t.ok("banks by the sector map (CTG yes, FPT no) without asking VCI", vv.is_bank("CTG") and not vv.is_bank("FPT"))
vci.fetch_details = lambda s: {"comTypeCode": "NH"}
t.ok("a bank outside the sector map: by VCI's company type NH", vv.is_bank("PGB"))
vci.fetch_details = no_details
t.ok("unknown company type (VCI down): no card", not vv.is_bank("PGB") and vv.card_text("PGB", "2026-10-28") == "")
t.ok("card_text: the bank's card; '' for a non-bank", vv.card_text("CTG", "2026-10-28") == text
     and vv.card_text("FPT", "2026-10-28") == "")

R = SimpleNamespace(report="=== QUYẾT ĐỊNH HỆ THỐNG ĐỊNH LƯỢNG - X ===\nGiá 30.700 VND.", action="HOLD",
                    deterministic_summary="CTG - 30.700 VND", to_state=lambda: {"action": "HOLD", "price": 30_700.0})
analyzed = []
engine.analyze = lambda symbol, day, **k: analyzed.append((symbol, day)) or R
node = qe.create_quant_engine()
out = node({"company_of_interest": "CTG", "trade_date": "2026-10-28"})
t.ok("quant_engine node: the card after the engine's report, the decision untouched",
     out["quant_report"] == R.report + "\n\n" + text and out["quant_decision"] == {"action": "HOLD", "price": 30_700.0}, out)
t.ok("... a non-bank: the report alone", node({"company_of_interest": "FPT", "trade_date": "2026-10-28"})["quant_report"] == R.report)
real_card_text = vv.card_text
vv.card_text = lambda *a: (_ for _ in ()).throw(RuntimeError("boom"))
t.ok("... the card failing: the report alone, the run goes on",
     node({"company_of_interest": "CTG", "trade_date": "2026-10-28"})["quant_report"] == R.report)
vv.card_text = real_card_text

report = vf.get_fundamentals("CTG", "2026-10-28")
t.ok("get_fundamentals (bank): one card line for the analyst, labelled",
     "- Định giá tham khảo (thẻ của hệ thống, không vào quyết định) [THỐNG KÊ 28/10]: Bear 26.236 · eab 23.856 · "
     "Base 27.643 · Bull 28.066 VND; giá 30.700 → vùng Đắt (MOS -11,06%)" in report, report)
t.ok("get_fundamentals (non-bank): no card line", "Định giá tham khảo" not in vf.get_fundamentals("FPT", "2026-10-28"))
real_card = vv.card
vv.card = lambda *a: None
t.ok("get_fundamentals (bank, no card): 'chưa đủ dữ liệu'",
     "- Định giá tham khảo (thẻ của hệ thống): chưa đủ dữ liệu" in vf.get_fundamentals("CTG", "2026-10-28"))
vv.card = real_card

quick = service.quick_text("CTG", "2026-10-28")
t.ok("/nhanh (bank): one line after the engine's summary",
     quick == "CTG - 30.700 VND\n📐 Định giá tham khảo (không vào quyết định): Bear 26.236 · Base 27.643 · "
              "Bull 28.066 → Đắt, MOS -11,1% (Ke 12%, BCTC Q2/2026)", quick)
t.ok("/nhanh (non-bank): the summary alone", service.quick_text("FPT", "2026-10-28") == "CTG - 30.700 VND")

# ───────────── 8. the Output Guard: fair values must be the card's ─────────────
t.ok("fair-value numbers: after the phrases (same clause, ≤ 30 characters) and right after Base/Bear/Bull/eab",
     rfmt.fair_value_numbers("Giá trị hợp lý của CTG theo mô hình là 27.643 VND. Bear: 26.236; **Bull**: 28.066") == [26_236, 27_643, 28_066]
     and rfmt.fair_value_numbers("| Giá trị hợp lý (Base) | 27.643 VND |") == [27_643]
     and rfmt.fair_value_numbers("Base ở mức 27.643, Bull là 28.066") == [27_643, 28_066]
     and rfmt.fair_value_numbers("định giá hợp lý khoảng 35.000 đồng") == [35_000]
     and rfmt.fair_value_numbers("Base ~27.700") == [27_700], rfmt.fair_value_numbers("Base ~27.700"))
t.ok("not fair values: a debater ('phe Bull nhắm kháng cự 35.000'), another sentence or clause, a multiple, a market cap",
     rfmt.fair_value_numbers("Phe Bull nhắm kháng cự 35.000.") == []
     and rfmt.fair_value_numbers("Về định giá. Giá đóng cửa 30.700") == []
     and rfmt.fair_value_numbers("Về định giá, kháng cự 35.000 vẫn là mốc chính") == []
     and rfmt.fair_value_numbers("định giá P/B 1,20 lần, vùng 30.700") == []
     and rfmt.fair_value_numbers("định giá vốn hoá 229.513 tỷ") == [], rfmt.fair_value_numbers("Phe Bull nhắm kháng cự 35.000."))

QUANT = R.report + "\nKháng cự: 35.000 VND (+14,01% so với giá hiện tại)\n\n" + text
STATE = {"quant_decision": {"action": "HOLD", "price": 30_700.0, "deterministic_summary": "TÓM TẮT HỆ THỐNG"},
         "quant_report": QUANT}
guard = output_guard.create_output_guard()
bad = {**STATE, "fundamentals_report": "CTG: giá trị hợp lý 35.000 VND, P/B 1,20.",
       "final_trade_decision": "**Rating**: HOLD\n\nGiữ. Giá trị hợp lý 35.000 theo phân tích cơ bản."}
upd = guard(bad)
t.ok("fundamentals_report with 'giá trị hợp lý 35.000' (not on the card): flagged, text kept",
     upd["fundamentals_report"].startswith(output_guard.UNKNOWN_PRICE_NOTE) and bad["fundamentals_report"] in upd["fundamentals_report"]
     and "Fundamentals Analyst: giá trị hợp lý không có trên thẻ định giá (35.000)" in upd["guard_notes"], upd)
t.ok("... final_trade_decision repeating it: flagged too (35.000 is a known resistance, but not a fair value)",
     output_guard.UNKNOWN_PRICE_NOTE in upd["final_trade_decision"]
     and "Portfolio Manager: giá trị hợp lý không có trên thẻ định giá (35.000)" in upd["guard_notes"], upd["guard_notes"])
plain = {**STATE, "quant_report": R.report + "\n\n" + text, "fundamentals_report": bad["fundamentals_report"],
         "final_trade_decision": "**Rating**: HOLD\n\nGiữ, chờ giá về 35.000."}
upd = guard(plain)
t.ok("an invented fair value is no longer a known price: 'chờ giá về 35.000' flagged as outside the data",
     "Portfolio Manager: có mốc giá ngoài dữ liệu (35.000)" in upd["guard_notes"], upd["guard_notes"])
good = {**STATE, "fundamentals_report": "Giá trị hợp lý (Base) 27.643 [THỐNG KÊ 28/10], Bull khoảng 28.100.",
        "final_trade_decision": "**Rating**: HOLD\n\nGiá 30.700 trên Bull 28.066, định giá hiện tại 30.700; "
                                "phe Bull nhắm kháng cự 35.000.",
        "investment_plan": "Base 27.643, eab 23.856, Bear 26.236."}
upd = guard(good)
t.ok("card values pass (28.100 is within 0,5% of Bull 28.066); the session price after 'định giá'; a debater",
     upd["guard_notes"] == "" and "fundamentals_report" not in upd and "investment_plan" not in upd
     and upd["final_trade_decision"].startswith(good["final_trade_decision"]), upd)
off = {**STATE, "investment_plan": "Base khoảng 27.900."}
t.ok("0,9% off the card's Base: flagged", "Research Manager: giá trị hợp lý không có trên thẻ định giá (27.900)"
     in guard(off)["guard_notes"])
nocard = {"quant_decision": {"action": "HOLD", "price": 54_000.0, "deterministic_summary": "FPT"},
          "quant_report": "Giá 54.000 VND.", "fundamentals_report": "Định giá hiện tại 54.000, P/E 10,18 lần.",
          "trader_investment_plan": "Giá trị hợp lý 60.000."}
upd = guard(nocard)
t.ok("no card (non-bank): any fair value is invented; the session price is not",
     "fundamentals_report" not in upd and upd["guard_notes"] == "Trader: giá trị hợp lý không có trên thẻ định giá (60.000)",
     upd["guard_notes"])
t.ok("other numbers keep the old check and note", output_guard.guard_text(
     "Mua quanh 41.000.", STATE, label="Trader")[1] == "Trader: có mốc giá ngoài dữ liệu (41.000)")
early = output_guard.guard_early(lambda s: {"investment_plan": "Giá trị hợp lý 35.000."}, "investment_plan",
                                 "Research Manager")(STATE)
t.ok("guard_early (Research Manager, Trader) applies the fair-value check too",
     early["investment_plan"].startswith(output_guard.UNKNOWN_PRICE_NOTE) and "(35.000)" in early["guard_notes"], early)

# ───────────── 9. the prompts and informational only ─────────────
from tradingagents.agents import vn_context  # noqa: E402

t.ok("GROUNDING_RULES: quoted numbers keep their labels; fair values only from the card",
     "[FACT dd/mm] hoặc [THỐNG KÊ dd/mm] khi trích phải giữ nguyên nhãn" in vn_context.GROUNDING_RULES
     and "chỉ được lấy đúng số trên thẻ" in vn_context.GROUNDING_RULES)
repo = _common.REPO
readers = sorted(p.relative_to(repo).as_posix() for d in ("tradingagents", "vnbot") for p in (repo / d).rglob("*.py")
                 if re.search(r"vn_valuation|valuation_config", p.read_text(encoding="utf-8", errors="replace"))
                 and p.name not in ("vn_valuation.py", "valuation_config.py"))
t.ok("who reads the card: the fundamentals tool, the graph's engine node, the Output Guard, /nhanh - not the "
     "engine, policy, simulator, calibration or params",
     readers == ["tradingagents/agents/output_guard.py", "tradingagents/agents/quant_engine.py",
                 "tradingagents/dataflows/vn_fundamentals.py", "vnbot/service.py"], readers)
t.ok("engine.analyze is not where the card is made (it runs for every stock of a scan)",
     "vn_valuation" not in Path(engine.__file__).read_text(encoding="utf-8") and analyzed)

t.done()
