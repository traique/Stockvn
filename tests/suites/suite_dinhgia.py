"""The bot's own valuation card of every stock (v1.17.0, vnbot.dinhgia and the /dinhgia pages): the
profiles and their Ke, the reliability line, the refusals (negative equity, too few annual ROEs, a
loss), the rows kept per (day, stock) in the history store or a JSON file and "what changed since the
last view", /dinhgia on Telegram and Zoom (one dispatcher, the 3.900-character split), the pages
behind the password, the evening job (bounded, skips what is done, retried, stamped), the banks'
medians. FPT, HPG and VHM recorded from VCI on 30/10/2026 (tests/fixtures/dinhgia_*_2026-10-30.json:
every 5th session since 2018 and the last 15), CTG from v1.16.0's fixture; the rest synthetic."""
import copy
import dataclasses
import json
import os
import re
import tempfile
import threading
import time
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import _common  # noqa: F401 - clean env, no .env, offline, temp folder, repo on sys.path
_common.need("httpx")

ROOT = Path(tempfile.mkdtemp(prefix="dinhgia-auto-"))
os.environ.update({"DATA_DIR": str(ROOT / "app"), "WEB_PASSWORD": "s3cret-pass", "TRADE_MODE": "paper",
                   "TRADINGAGENTS_PARAMS_PATH": str(ROOT / "qp.json"), "PUBLIC_URL": "https://bot.example",
                   "TELEGRAM_TOKEN": "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawQ", "TELEGRAM_ALLOWED_IDS": "111"})

import pandas as pd  # noqa: E402

from tradingagents.dataflows.config import set_config  # noqa: E402

CACHE = ROOT / "cache"
set_config({"data_cache_dir": str(CACHE)})

from vnbot import settings as settings_module  # noqa: E402

SETTINGS = settings_module.load()

from tradingagents.dataflows import money_flows, ohlcv, valuation_config  # noqa: E402
from tradingagents.dataflows import vn_valuation as vv  # noqa: E402
from tradingagents.dataflows.errors import NoMarketDataError, VendorUnavailableError  # noqa: E402
from tradingagents.dataflows.vendors import flows, vci  # noqa: E402
from tradingagents.market import vn_rules  # noqa: E402
from vnbot import dinhgia as dg, store as store_mod  # noqa: E402

t = _common.Checks()
FX = {s: json.loads((_common.FIXTURES / f"dinhgia_{s}_2026-10-30.json").read_text(encoding="utf-8"))
      for s in ("FPT", "HPG", "VHM")}
FX["CTG"] = json.loads((_common.FIXTURES / "valuation_CTG_2026-10-28.json").read_text(encoding="utf-8"))
# every listed bank's KPIs as the evening job read them on 30/10/2026 (newest 5 quarters), CTG's raw ratio rows
PEERS_FX = json.loads((_common.FIXTURES / "bank_peers_2026-10-30.json").read_text(encoding="utf-8"))
FX["CTG"]["ratios"] = PEERS_FX["ctg_ratios"]


def fixture_prices(fx: dict) -> pd.DataFrame:
    p = fx["prices"]
    days = [pd.Timestamp(p["start"])]
    for gap in p["gaps"]:
        days.append(days[-1] + pd.Timedelta(days=gap))
    changes = [(pd.Timestamp(d), s) for d, s in p["TotalShares"]]
    shares = [next((s for d, s in reversed(changes) if d <= day), changes[0][1]) for day in days]
    return pd.DataFrame({"Date": days, "Close": [float(c) for c in p["Close"]], "TotalShares": [float(s) for s in shares]})


# ── synthetic stocks: HPG's data with one thing wrong ──
def variant(name: str, change) -> None:
    fx = copy.deepcopy(FX["HPG"])
    change(fx)
    FX[name] = fx


def negative_equity(fx):
    for r in fx["balance_sheet"]:
        if (r["yearReport"], r["lengthReport"]) == (2026, 3):
            r["bsa78"] = -5e12


def a_loss(fx):
    for r in fx["income_statement"]:
        if r["yearReport"] == 2026 or (r["yearReport"], r["lengthReport"]) == (2025, 4):
            r["isa22"] = -1e12


def heavy_losses(fx):
    """DLG on 30/10/2026: annual ROE −30,5%, −77%, −86,6% among small profits; the median of 5 years stays positive."""
    for r in fx["annual_ratios"]:
        r["roe"] = {2018: -0.80, 2019: -0.866}.get(r["yearReport"], r["roe"])


variant("NEGEQ", negative_equity)
variant("LOSSY", a_loss)
variant("SWING", heavy_losses)
variant("YOUNG", lambda fx: fx.update(annual_ratios=fx["annual_ratios"][-2:]))
variant("OUTG", lambda fx: None)        # HPG's data, its price history never fetched: VCI down for it
PRICES = {s: fixture_prices(fx) for s, fx in FX.items()}
LISTING = [{"symbol": s, "type": "STOCK", "board": "HSX", "organName": f"Công ty {s}", "icbCode2": icb}
           for s, icb in (("NEGEQ", "1700"), ("LOSSY", "1700"), ("SWING", "1700"), ("YOUNG", "1700"), ("OUTG", "1700"),
                          ("XYZBDS", "8600"),
                          ("PGB", "8300"), ("FPT", "9500"), ("HPG", "1700"), ("VHM", "8600"), ("CTG", "8300"))]
LISTING[1]["organName"] = "Công ty Cổ phần Tập đoàn Hòa Phát"
LISTING_DOWN = {"on": False, "nodata": False}
CALLS = {"flows": 0}
FLOWS_DOWN: set[str] = set()


def fake_listing():
    if LISTING_DOWN["on"]:
        raise VendorUnavailableError("offline")
    if LISTING_DOWN["nodata"]:
        raise NoMarketDataError("*", "*", "VCI answered without a listing")
    return [dict(r) for r in LISTING]


def fake_flows(symbol, start, end):
    CALLS["flows"] += 1
    if symbol in FLOWS_DOWN:
        raise VendorUnavailableError("VCI price-history: read timed out")
    f = PRICES[symbol]
    return f[(f["Date"] >= pd.Timestamp(start)) & (f["Date"] <= pd.Timestamp(end))].assign(ForeignBuy=0.0)


def fake_statement(symbol, section):
    if symbol not in FX:
        raise NoMarketDataError(symbol, symbol, "not in the fixtures")
    key = {"BALANCE_SHEET": "balance_sheet", "INCOME_STATEMENT": "income_statement"}[section]
    years = FX[symbol].get("annual_statements", []) if section == "INCOME_STATEMENT" else []
    return {"quarters": [dict(r) for r in FX[symbol][key]], "years": [dict(r) for r in years]}


def fake_ratios(symbol):
    if symbol not in FX:
        raise NoMarketDataError(symbol, symbol, "not in the fixtures")
    return [dict(r) for r in FX[symbol]["ratios"] + FX[symbol].get("annual_ratios", [])]


def fake_events(symbol, from_date, to_date, codes="DIV,ISS", size=50):
    out = []
    for e in FX.get(symbol, {}).get("events", []):
        days = {d[:10] for d in (e.get("exrightDate"), e.get("publicDate")) if d}
        if e["eventCode"] in codes.split(",") and any(from_date <= d <= to_date for d in days):
            out.append(dict(e))
    return out


def fake_bars(symbol, as_of_date, fill_gaps=True):
    f = PRICES[symbol]
    return f[f["Date"] <= pd.Timestamp(as_of_date)][["Date", "Close"]].reset_index(drop=True)


flows.fetch_stock_flows = fake_flows
vci.fetch_statement = fake_statement
vci.fetch_ratios = fake_ratios
vci.fetch_events = fake_events
vci.fetch_listing = fake_listing
ohlcv.load_ohlcv = fake_bars
NOW = {"t": datetime(2026, 10, 30, 10, 0, tzinfo=vn_rules.VN_TZ)}       # Friday, in the session
vn_rules.now_vn = lambda: NOW["t"]


def raises(fn, text: str) -> bool:
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        return text in str(exc)
    return False


# ───────────── 1. profiles, Ke, reliability ─────────────
t.ok("profiles: the sector map first (CTG bank, VHM real estate, FPT/HPG default), then the listing's ICB code",
     [vv.card_profile(s) for s in ("CTG", "VHM", "FPT", "HPG", "SSI", "BVH", "XYZBDS", "PGB")]
     == ["banking", "realestate", "default", "default", "securities", "insurance", "realestate", "banking"])
LISTING_DOWN["on"] = True
from tradingagents.dataflows import symbols as symbols_mod  # noqa: E402

saved_listing = symbols_mod._saved_listing
symbols_mod._saved_listing = lambda: None
t.ok("the listing unreadable: an unmapped stock is 'default' (no failure)", vv.card_profile("QQQ") == "default")
symbols_mod._saved_listing = saved_listing
LISTING_DOWN["on"] = False
t.ok("Ke: banks 12%, every other profile 13% (VALUATION_KE_DEFAULT), λ 0,4, g 0, margin 13,7%",
     valuation_config.assumptions("banking").ke == 0.12
     and all(valuation_config.assumptions(k) == valuation_config.Assumptions(0.13, 0.4, 0.0, 0.137)
             for k in ("default", "realestate", "securities", "insurance")))
os.environ["VALUATION_KE_DEFAULT"] = "14,5"
t.ok("VALUATION_KE_DEFAULT=14,5 moves the non-banks only", valuation_config.assumptions("default").ke == 0.145
     and valuation_config.assumptions("banking").ke == 0.12)
os.environ["VALUATION_KE_DEFAULT"] = "99"
t.ok("... out of 4-30: ignored", valuation_config.assumptions("default").ke == 0.13)
del os.environ["VALUATION_KE_DEFAULT"]

fpt = vv.assess("FPT", None)
hpg = vv.assess("HPG", None)
vhm = vv.assess("VHM", None)
t.ok("FPT 30/10/2026 (live check 29/10 close 52.700): book 22.736, Base 29.212 = book × P/B(28,10%, Ke 13%), Đắt",
     isinstance(fpt, vv.ValuationCard) and fpt.day == date(2026, 10, 29) and round(fpt.book) == 22_736
     and round(fpt.base) == 29_212 and round(fpt.bear) == 25_700 and round(fpt.bull) == 30_358 and fpt.zone == "Đắt"
     and abs(fpt.base - fpt.book * vv.justified_pb(fpt.roe_base, 0.13, 0.4)) < 1e-6 and fpt.period == "Q3/2026",
     fpt)
t.ok("... a rising ROE: p80 28,21% would put Bull 0,17% above Base (29.263, the Hợp lý band ~50đ); not a bank, so "
     "Bull is the sensitivity line's Base at Ke 11%, and the card says why",
     round(fpt.book * vv.justified_pb(fpt.roe_bull, 0.13, 0.4)) == 29_263 and fpt.bull_ke == 0.11
     and fpt.bull == fpt.sensitivity[0][1] and vv.bull_basis(fpt) == "= Base với Ke 11% (ROE p80 28,21% sát Base)",
     (fpt.roe_bull, fpt.bull_ke, vv.bull_basis(fpt)))
t.ok("... reliability THẤP with its reasons: technology, ROE far above Ke, the book growing ≥ 20%/year",
     fpt.reliability == "thấp" and "công ty công nghệ" in fpt.reliability_why and "ROE bền vững 28,10%" in fpt.reliability_why
     and "vốn chủ sở hữu tăng 21,96%/năm trong 3 năm" in fpt.reliability_why, fpt.reliability_why)
t.ok("... P/E is its own multiple: both ranks there, P/B too", fpt.pe_rank is not None and fpt.pb_rank is not None
     and fpt.pe_rank.metric == "pe" and round(fpt.pe_rank.value, 2) == 9.66, (fpt.pe_rank, fpt.pb_rank))
t.ok("HPG: a cyclical, ROE 11,07% under Ke: Base below the price, Hợp lý, reliability TRUNG BÌNH (no reason to drop)",
     round(hpg.book) == 17_624 and round(hpg.base) == 16_983 and round(hpg.bull) == 21_440 and hpg.zone == "Hợp lý"
     and hpg.reliability == "trung bình" and hpg.reliability_why == valuation_config.RELIABILITY["default"][1]
     and hpg.bear < hpg.base < hpg.bull and hpg.bull_ke is None and vv.bull_basis(hpg) == "theo ROE p80 24,48%", hpg)
t.ok("VHM: real estate, Ke 13%, reliability THẤP (land at cost, profit by handover); the 6.000đ dividend went ex "
     "29/06, inside Q2/2026: already out of its book, over the shares doubled on 06/08",
     vhm.profile == "realestate" and vhm.assumptions.ke == 0.13 and vhm.reliability == "thấp"
     and "giá gốc" in vhm.reliability_why and round(vhm.book) == 32_088 and round(vhm.base) == 36_669
     and vhm.dividends == () and vhm.shares == 8_214_824_008, vhm)
old_fpt = vv.assess("FPT", "2026-10-28")
t.ok("point-in-time: on 28/10 FPT's Q3/2026 (published 29/10) is not there yet", old_fpt.period == "Q2/2026"
     and old_fpt.day == date(2026, 10, 28), old_fpt.period)
ctg = vv.assess("CTG", "2026-10-28")
t.ok("CTG keeps v1.16.0's card: Ke 12%, Base 27.643, Bull 28.066 by ROE p80 (under Base at Ke 10%, 28.749: no floor "
     "for a bank), reliability CAO", ctg.profile == "banking" and round(ctg.base) == 27_643 and round(ctg.bull) == 28_066
     and ctg.bull_ke is None and ctg.reliability == "cao" and vv.render_card(ctg).splitlines()[0].endswith("- ngân hàng ---"))
fast = pd.DataFrame({"Period": ["Q3/2023", "Q3/2026"], "Base": [100.0, 300.0]})     # 44%/year
t.ok("ROE ≥ 20% and a fast-growing book drop only the doanh nghiệp profile: a bank (ACB's 5-year median ROE 23,9%), "
     "an insurer, a broker stay CAO (README, NOTES)",
     all(vv._reliability(k, "ACB", 0.239, fast) == valuation_config.RELIABILITY[k]
         for k in ("banking", "insurance", "securities"))
     and vv._reliability("default", "HPG", 0.239, fast)[0] == "thấp")
swing = vv.assess("SWING", None)
t.ok("a heavy loss year (DLG-like: ROE 2018 −80%, 2019 −86,6%): ROE p20 −44,4% gives a negative P/B, Bear kept at 0 "
     "and said why; reliability THẤP with the year, so /dinhgia lists the card after the others",
     isinstance(swing, vv.ValuationCard) and swing.bear == 0.0 and swing.bear <= swing.eab < swing.base < swing.bull
     and swing.roe_bear < -0.4 and swing.reliability == "thấp"
     and "có năm lỗ nặng (ROE 2019 -86,60%, dưới −Ke 13%)" in swing.reliability_why
     and "cho P/B âm nên Bear giữ 0" in swing.reliability_why
     and [r["symbol"] for r in dg.list_order([dg.row_of(swing, "2026-10-30"), dg.row_of(hpg, "2026-10-30")])]
     == ["HPG", "SWING"], (swing.bear, swing.roe_bear, swing.reliability_why))
steady = pd.DataFrame({"Period": ["Q3/2026"], "Base": [1.0]})
t.ok("... any profile but the banks: a broker or an insurer drops to THẤP, real estate keeps its own why first; a bank "
     "stays CAO; a loss year above −Ke (HAG 2020 −11,5%) drops nothing",
     all(vv._reliability(k, "XYZ", 0.1, steady, ((2020, -0.2), (2021, 0.1)), 0.13)[0] == "thấp"
         for k in ("securities", "insurance", "default"))
     and vv._reliability("realestate", "XYZ", 0.1, steady, ((2020, -0.2),), 0.13)[1].startswith("quỹ đất")
     and vv._reliability("banking", "XYZ", 0.1, steady, ((2020, -0.2),), 0.12)[0] == "cao"
     and vv._reliability("securities", "XYZ", 0.1, steady, ((2020, -0.115),), 0.13)[0] == "cao")
growth = vv._book_growth(pd.DataFrame({"Period": ["Q3/2023", "Q4/2025", "Q3/2026"], "Base": [100.0, 150.0, 200.0]}))
t.ok("book growth: the same quarter three years before (100 → 200: 25,99%/year); none without it",
     abs(growth - (2 ** (1 / 3) - 1)) < 1e-12
     and vv._book_growth(pd.DataFrame({"Period": ["Q4/2025", "Q3/2026"], "Base": [150.0, 200.0]})) is None)

# ───────────── 2. refusals, said why ─────────────
neg, loss, young = (vv.assess(s, None) for s in ("NEGEQ", "LOSSY", "YOUNG"))
t.ok("negative equity: no card, the reason says it", isinstance(neg, vv.NoCard) and not neg
     and "vốn chủ sở hữu công ty mẹ Q3/2026 âm (-5.772,9 tỷ)" in neg.reason and vv.card("NEGEQ", None) is None, neg)
t.ok("a loss: no card, the four quarters' profit in the reason", isinstance(loss, vv.NoCard)
     and loss.reason.startswith("đang lỗ: LNST công ty mẹ 4 quý tới Q3/2026 là -4.000,0 tỷ"), loss)
t.ok("two annual ROEs: no card", isinstance(young, vv.NoCard) and "mới có 2 năm ROE" in young.reason, young)
t.ok("not at VCI: a NoCard, not a failure", isinstance(vv.assess("ZZZ", None), vv.NoCard))
t.ok("the engine's report and /nhanh stay bank-only: no card text for HPG",
     vv.card_text("HPG", None) == "" and vv.quick_line("HPG", None) == "" and vv.card_text("CTG", "2026-10-28") != "")

from tradingagents.agents import output_guard  # noqa: E402

GUARD_HPG = {"company_of_interest": "HPG", "trade_date": "2026-10-30", "quant_report": "Giá 21.200. Hỗ trợ 19.500, kháng cự 23.000.",
             "quant_decision": {"action": "NO_TRADE", "price": 21_200.0, "deterministic_summary": "HPG"}}
GUARD_FPT = {"company_of_interest": "FPT", "trade_date": "2026-10-30", "quant_report": "Giá 52.700.",
             "quant_decision": {"action": "HOLD", "price": 52_700.0, "deterministic_summary": "FPT"},
             "market_report": "Kháng cự 56.000, hỗ trợ 50.000."}
debates = [(GUARD_FPT, "- **Bull**: 56.000 nếu vượt đỉnh cũ.\n- **Bear**: 50.000 khi thủng MA50."),
           (GUARD_FPT, "| Bull | 56.000 |\n| Bear | 50.000 |"),
           (GUARD_HPG, "Phe Bull: 23.000 là kháng cự gần nhất, phe Bear lo ngại thủng 19.500.")]
t.ok("the non-banks' cards are kept under the run date's key (/dinhgia, the evening job, the Tài sản page), yet the "
     "Output Guard stays bank-only: no card for HPG or FPT, CTG's is there",
     vv.kept("HPG", "2026-10-30") and vv.kept("FPT", "2026-10-30") and vv.cached_card("HPG", "2026-10-30") is None
     and vv.cached_card("FPT", "2026-10-30") is None and vv.cached_card("CTG", "2026-10-28") is not None)
t.ok("... the debaters' Bull/Bear pass; a price in no data after Bull keeps the old note, not 'off the card'; the "
     "card's Bull (21.440, which no agent saw) is not taken as known; the fundamentals report's scenario not flagged",
     all(output_guard.guard_text(x, st, label="Research Manager")[1] is None for st, x in debates)
     and output_guard.guard_text("Phe Bull: 36.000.", GUARD_HPG, label="Research Manager")[1]
     == "Research Manager: có mốc giá ngoài dữ liệu (36.000)"
     and output_guard.guard_text("Phe Bull: 21.440.", GUARD_HPG, label="Research Manager")[1]
     == "Research Manager: có mốc giá ngoài dữ liệu (21.440)"
     and output_guard.guard_fundamentals({**GUARD_HPG, "fundamentals_report": "Kịch bản Bull 26.000 nếu giá thép hồi phục."})[1]
     is None, [output_guard.guard_text(x, st, label="Research Manager")[1] for st, x in debates])

# ───────────── 3. the rows and the changes ─────────────
DATA = ROOT / "rows"
DATA.mkdir()


def scenario(cards: dg.Cards) -> tuple:
    a18 = vv.assess("CTG", "2026-08-18")
    a19 = vv.assess("CTG", "2026-08-19")
    first, none = cards.record(a18, "2026-08-18")
    row19, prev19 = cards.record(a19, "2026-08-19")
    again, prev_again = cards.record(a19, "2026-08-19")
    return first, none, row19, prev19, again, prev_again


store_mod.reset()
json_rows = scenario(dg.Cards(DATA))
db = store_mod.open_store(ROOT / "db")
db_rows = scenario(dg.Cards(DATA))
for name, (first, none, row19, prev19, again, prev_again) in (("JSON file (store off)", json_rows), ("history store", db_rows)):
    what = dg.changes(row19, prev19)
    t.ok(f"{name}: the first view has nothing to compare with", none is None
         and dg.changes_line(first, none).endswith("lần đầu bot tính mã này, chưa có lần trước để so."))
    t.ok(f"{name}: CTG 18/08 → 19/08: the new statement (Q1 → Q2/2026) and the moved levels",
         prev19["day"] == "2026-08-18" and "BCTC mới: Q1/2026 → Q2/2026" in what
         and any(x.startswith("Base: ") for x in what), what)
    t.ok(f"{name}: a second view the same day compares with the day before again (same answer), one row a day",
         prev_again["day"] == "2026-08-18" and dg.changes(again, prev_again) == what)
t.ok("the store keeps one row a (day, stock), newest first", [r["day"] for r in db.valuation_rows("CTG", 5)]
     == ["2026-08-19", "2026-08-18"] and db.counts()["valuations"] == 2)
t.ok("the JSON file keeps the two newest days of each stock",
     [r["day"] for r in json.loads((DATA / dg.STATE_FILE).read_text(encoding="utf-8"))["CTG"]] == ["2026-08-19", "2026-08-18"])
base = dict(json_rows[2])
same = dict(base, ts=1.0)
t.ok("nothing moved: 'không đổi'", dg.changes(same, base) == []
     and dg.changes_line(same, base) == "Có gì đổi so với lần xem trước (19/08): không đổi")
t.ok("a level moved 0,29%: not said; 0,31%: said with both prices",
     dg.changes(dict(base, base=base["base"] * 1.0029), base) == []
     and dg.changes(dict(base, bull=base["bull"] * 1.0031), base)[0].startswith(f"Bull: {dg.price(base['bull'])} → "))
t.ok("the zone, the assumptions, the reliability", dg.changes(dict(base, zone="Rẻ"), dict(base, zone="Đắt")) == ["vùng: Đắt → Rẻ"]
     and dg.changes(dict(base, ke=0.13), base) == ["Ke 12% → 13%"] and dg.changes(dict(base, lam=0.5), base) == ["λ 0,4 → 0,5"]
     and dg.changes(dict(base, reliability="thấp"), base) == ["độ tin cậy: cao → thấp"])
nocard = dg.row_of(neg, "2026-10-30")
t.ok("a card that appears or goes away is said", dg.changes(base, nocard)[0].startswith("có thẻ (lần trước không: vốn chủ")
     and dg.changes(nocard, base)[0].startswith("không còn thẻ: vốn chủ") and dg.changes(nocard, dict(nocard)) == [])
t.ok("the store's table: in the exports and the /dulieu downloads, pruned with the history",
     "valuations" in store_mod.EXPORTS and db.prune("2029-01-01")["valuations"] == 2 and db.counts()["valuations"] == 0)

# ───────────── 4. /dinhgia on Telegram and Zoom ─────────────
from vnbot.jobs import JobQueue  # noqa: E402
from vnbot.service import Watchlist  # noqa: E402
from vnbot.taisan import TaiSan  # noqa: E402
from vnbot.telegram_bot import MAX_MESSAGE, ZOOM_USER, TelegramBot, chunks  # noqa: E402


class FakeMarket:
    def live(self, symbols, day):
        return {}

    def daily_closes(self, symbol, end):
        return {}

    def card_events(self, symbol, start, end):
        return []

    def events(self, symbol, start, end):
        return []


account = TaiSan(ROOT / "ts", market=FakeMarket(), site=None)
account.set_valuation("HPG", 15_000, 20_000, 25_000, note="<b>CTCK</b>")
watch = Watchlist(SETTINGS)
watch.set(["FPT", "HPG", "VHM", "NEGEQ"])
tg = TelegramBot(SETTINGS, JobQueue(SETTINGS), watch)
tg.taisan = account
sent = []
tg.send = lambda chat_id, text: sent.append((chat_id, text)) or True
vv._cards.clear()
tg._handle({"chat": {"id": 111}, "from": {"id": 111}, "text": "/dinhgia hpg"})
text = sent[-1][1]
t.ok("Telegram /dinhgia HPG: told first while it is built, then the card", sent[-2][1].startswith("⏳ Đang tính định giá HPG")
     and text.startswith("📐 ĐỊNH GIÁ HPG · doanh nghiệp · bot tự tính"), sent[-2:])
lines = text.splitlines()
t.ok("... zone and MOS first, then the levels with their label, the rule, the multiples' percentile",
     lines[1] == "Giá 21.200 (phiên 29/10) → vùng HỢP LÝ · MOS -24,8% so với Base 16.983"
     and lines[2] == "Bear 16.587 · eab 14.656 · Base 16.983 · Bull 21.440 [THỐNG KÊ 29/10]"
     and lines[3] == dg.ZONE_RULE and lines[4].startswith("P/E ") and "· P/B " in lines[4] and lines[4].endswith("[THỐNG KÊ 29/10]"),
     lines[:5])
t.ok("... what Base rests on (book FACT, ROE THỐNG KÊ with its date), the sensitivity, the reliability, the dates",
     lines[5].startswith("Base = sổ sách 17.624 đ/cp [FACT 29/10] × 0,96 lần: ROE bền vững 11,07% (trung vị 2021-2025) "
                         "[THỐNG KÊ 30/03] · Ke 13% đặt tay · λ 0,4 · g 0%")
     and lines[6] == "Độ nhạy theo Ke: 11% → 17.649 · 13% → 16.983 · 15% → 16.365"
     and lines[5].endswith("g 0% · eab = Base × (1 − 13,7%)")
     and lines[7].startswith("Độ tin cậy: TRUNG BÌNH - ") and lines[8].startswith("BCTC Q3/2026 công bố 29/10 · Hạn dùng:"),
     lines[5:9])
t.ok("... the changes, the owner's card named as his with the gap, the link, the disclaimer",
     lines[9].startswith("Có gì đổi so với lần xem trước: lần đầu")
     and lines[10] == "✍️ Thẻ anh nhập (/tsdinhgia, không phải của bot, nhập 30/10): Bear 15.000 · Base 20.000 · Bull 25.000 "
                      "→ Base của anh cao hơn bot 17,8%"
     and lines[11] == "Chi tiết: https://bot.example/dinhgia/HPG" and lines[12] == dg.DISCLAIMER and len(text) < 2000, lines[9:])
n = len(sent)
tg._handle({"chat": {"id": 111}, "from": {"id": 111}, "text": "/dg HPG"})
t.ok("/dg is /dinhgia; kept an hour: no '⏳' the second time", len(sent) == n + 1 and sent[-1][1].startswith("📐 ĐỊNH GIÁ HPG"))
tg._handle({"chat": {"id": "zoom:7"}, "from": {"id": ZOOM_USER}, "text": "/dinhgia FPT"})
zoom_lines = sent[-1][1].splitlines()
t.ok("Zoom ('dinhgia FPT', the same dispatcher); a low-reliability card says so on the zone line, and why Bull is where it is",
     sent[-1][0] == "zoom:7" and sent[-1][1].startswith("📐 ĐỊNH GIÁ FPT") and "Độ tin cậy: THẤP - công ty công nghệ" in sent[-1][1]
     and zoom_lines[1] == "Giá 52.700 (phiên 29/10) → vùng ĐẮT (tin cậy THẤP) · MOS -80,4% so với Base 29.212"
     and zoom_lines[2] == "Bear 25.700 · eab 25.210 · Base 29.212 · Bull 30.358 (= Base với Ke 11% (ROE p80 28,21% sát Base)) "
                          "[THỐNG KÊ 29/10]", zoom_lines[:3])
n = len(sent)
tg._handle({"chat": {"id": 111}, "from": {"id": 111}, "text": "/dinhgia NEGEQ"})
t.ok("no card: the reason, no levels", sent[-1][1].startswith("📐 ĐỊNH GIÁ NEGEQ · doanh nghiệp · bot tự tính\nBot không lập thẻ: "
                                                            "vốn chủ sở hữu công ty mẹ Q3/2026 âm") and "Base" not in sent[-1][1])
tg._handle({"chat": {"id": 111}, "from": {"id": 111}, "text": "/dinhgia NEGEQ"})
t.ok("'⏳' only while the card is built: the first NEGEQ, not the second (its kept no-card answers at once)",
     [m for _, m in sent[n:]].count("⏳ Đang tính định giá NEGEQ (lần đầu một mã khoảng 10-20 giây)...") == 1
     and len(sent) == n + 3, [m[:40] for _, m in sent[n:]])
key = vv._key("HPG", None)
vv._cards[key] = (vv._cards[key][0] - money_flows.RECHECK_SECONDS - 1, *vv._cards[key][1:])
tg._handle({"chat": {"id": 111}, "from": {"id": 111}, "text": "/dinhgia HPG"})
t.ok("... and again once the kept card is past the hour (assess builds it from VCI again)",
     sent[-2][1].startswith("⏳ Đang tính định giá HPG") and sent[-1][1].startswith("📐 ĐỊNH GIÁ HPG"))
account.valuation_status = lambda symbol, card, day=None: {"stale": {"ex": "2026-10-20", "what": "cổ tức"}, "expired": False,
                                                           "checked": True}
stale_line = dg._manual_line("HPG", account, hpg.base)
del account.valuation_status
t.ok("the owner's card past an ex-date: 'cần cập nhật', no gap to the bot's (his levels on the old share basis)",
     "cần cập nhật (GDKHQ 20/10/2026)" in stale_line and "Base của anh" not in stale_line
     and "Base của anh cao hơn bot 17,8%" in dg._manual_line("HPG", account, hpg.base), stale_line)
tg._handle({"chat": {"id": 111}, "from": {"id": 111}, "text": "/dinhgia FPT HPG"})
t.ok("two codes: the usage", sent[-1][1] == "⚠️ " + dg.USAGE)
tg._handle({"chat": {"id": 111}, "from": {"id": 111}, "text": "/dinhgia VNINDEX"})
t.ok("an index: refused in Vietnamese", sent[-1][1].startswith("⚠️ VNINDEX là chỉ số"), sent[-1])
tg._handle({"chat": {"id": 999}, "from": {"id": 999}, "text": "/dinhgia FPT"})
t.ok("a stranger gets nothing", "TELEGRAM_ALLOWED_IDS" in sent[-1][1])
tg._handle({"chat": {"id": 111}, "from": {"id": 111}, "text": "/dinhgia"})
listing = sent[-1][1]
t.ok("/dinhgia alone: the tracked stocks (watchlist, then the holding/card HPG once), cheapest against Base first",
     listing.startswith("📐 ĐỊNH GIÁ BOT TỰ TÍNH · 4 mã theo dõi") and listing.index("• HPG Hợp lý") < listing.index("• FPT Đắt")
     and "✍️ có thẻ anh nhập" in listing.split("• HPG", 1)[1].split("\n", 1)[0]
     and "• NEGEQ: không lập thẻ - vốn chủ" in listing and "Chưa tính: VHM" in listing, listing)
ordered = dg.list_order([{"symbol": "LOW", "base": 1.0, "mos": 0.5, "reliability": "thấp"},
                         {"symbol": "MID", "base": 1.0, "mos": 0.1, "reliability": "trung bình"},
                         {"symbol": "NIL", "base": None, "mos": None},
                         {"symbol": "TOP", "base": 1.0, "mos": 0.3, "reliability": "cao"}])
t.ok("the list: the cheapest against Base first, a low-reliability card after the others (its MOS from a weak model); "
     "said in the heading", [r["symbol"] for r in ordered] == ["TOP", "MID", "LOW"]
     and "rẻ nhất so với Base trước, tin cậy thấp xếp sau" in listing.splitlines()[0])
real_assess = vv.assess
vv.assess = lambda *a: (_ for _ in ()).throw(VendorUnavailableError("VCI-info failed recently"))
tg._handle({"chat": {"id": 111}, "from": {"id": 111}, "text": "/dinhgia VCB"})
vv.assess = real_assess
t.ok("VCI down: a ⚠️ to try later, nothing kept", sent[-1][1].startswith("⚠️ VCI đang lỗi hoặc chậm, chưa tính được định giá VCB")
     and dg.Cards(SETTINGS.data_dir).rows("VCB") == [])
many = [f"M{i:03d}" for i in range(60)]
for i, sym in enumerate(many):
    dg.Cards(SETTINGS.data_dir).save(dict(dg.row_of(hpg, "2026-10-30"), symbol=sym, mos=-0.01 * i,
                                         reliability="trung bình"))
long_list = dg.list_text(many, SETTINGS.data_dir)
parts = chunks(long_list)
t.ok("60 stocks: split into Telegram messages of at most 3.900 characters, whole lines", len(parts) >= 2
     and all(len(p) <= MAX_MESSAGE for p in parts) and "".join(parts) == long_list
     and all(p.endswith("\n") or p == parts[-1] for p in parts), [len(p) for p in parts])
t.ok("/trogiup lists /dinhgia, still one message", "/dinhgia FPT" in tg._help() and len(tg._help()) <= MAX_MESSAGE)
from vnbot import pages  # noqa: E402

t.ok("/docs lists dinhgia (Zoom)", any(c == "dinhgia FPT" for c, _, _ in pages.COMMANDS))

# ───────────── 5. the pages ─────────────
from fastapi.testclient import TestClient  # noqa: E402
from vnbot import dinhgia_web  # noqa: E402
from vnbot.channels import Switches  # noqa: E402
from vnbot.web import create_app  # noqa: E402

app = create_app(SETTINGS, JobQueue(SETTINGS), watch, switches=Switches(SETTINGS.data_dir, {}))
app.state.taisan.market = FakeMarket()
app.state.taisan.set_valuation("FPT", 40_000, 50_000, 60_000, note="<script>alert(1)</script>")
web = TestClient(app, raise_server_exceptions=False)
AUTH = ("x", "s3cret-pass")
t.ok("both pages need the password", web.get("/dinhgia").status_code == 401 and web.get("/dinhgia/FPT").status_code == 401)
page = web.get("/dinhgia", auth=AUTH)
t.ok("/dinhgia: the nav entry on, the tracked stocks with links, the box to look at any stock", page.status_code == 200
     and 'href="/dinhgia" class="nav-item on" aria-current="page"' in page.text and 'href="/dinhgia/HPG"' in page.text
     and 'name="ma"' in page.text and 'action="/dinhgia"' in page.text and "không lập thẻ" in page.text, page.status_code)
r = web.get("/dinhgia?ma=fpt", auth=AUTH, follow_redirects=False)
t.ok("the box: a code goes to its page, an index is said", r.status_code == 303 and r.headers["location"] == "/dinhgia/FPT"
     and "là chỉ số" in web.get("/dinhgia?ma=VNINDEX", auth=AUTH).text)
dinhgia_web.WAIT_SECONDS = 30
prof = web.get("/dinhgia/FPT", auth=AUTH).text
sections = ("Độ mới của dữ liệu", "30 giây", "Vùng giá — rẻ · hợp lý · đắt", "Biên an toàn (MOS)", "P/E hôm nay", "P/B hôm nay",
            "Base đứng trên:", "Độ nhạy theo Ke", "Độ tin cậy của thẻ", "Có gì đổi so với lần xem trước", "Sổ sách và ROE",
            "Chỉ số chính", "Thẻ anh nhập (không phải của bot)", "Không phải khuyến nghị đầu tư.")
t.ok("/dinhgia/FPT: the PDF's sections in its order", all(s in prof for s in sections)
     and [prof.index(s) for s in sections[:4]] == sorted(prof.index(s) for s in sections[:4]),
     [s for s in sections if s not in prof])
t.ok("... the number line (book, eab, Bear, Base, Bull, the price beyond them), labels with dates, the company's name",
     '<svg viewBox="0 0 520 168"' in prof and "Sổ sách/cp" in prof and "giá 52.700 →" in prof
     and "THỐNG KÊ 29/10" in prof and "FACT 29/10" in prof and "Công ty FPT · HOSE" in prof)
t.ok("... the owner's card apart and escaped, never as the bot's",
     "&lt;script&gt;" in prof and "<script>alert(1)</script>" not in prof
     and prof.index("Thẻ anh nhập (không phải của bot)") > prof.index("Chỉ số chính"))
t.ok("... a non-bank: its own KPIs, no peer column; VCI's debtToEquity named for what it is (total liabilities)",
     "Biên lãi gộp" in prof and "Trung vị ngân hàng cùng kỳ" not in prof and "Nợ phải trả/VCSH" in prof and "Nợ vay" not in prof)
t.ok("... a tracked stock (watchlist): recomputed after 16:00; the low reliability next to the zone in '30 giây'",
     "Bot tự tính lại sau 16:00 mỗi phiên" in prof and "Độ tin cậy THẤP</b> với mã này" in prof
     and prof.index("Độ tin cậy THẤP") < prof.index("Biên an toàn (MOS)"))
nq = web.get("/dinhgia/NEGEQ", auth=AUTH).text
t.ok("no card: the reason on the page", "Bot không lập thẻ định giá cho NEGEQ: vốn chủ sở hữu công ty mẹ Q3/2026 âm" in nq)
t.ok("a bad code: said, with the box", "Mã không hợp lệ" in web.get("/dinhgia/%3Cb%3E", auth=AUTH).text)
links = sorted({h for h in re.findall(r'href="(/[^"#?]*)', page.text + prof)})
broken = [h for h in links if web.get(h, auth=AUTH).status_code >= 400]
t.ok("every link on the two pages answers", not broken and "/dinhgia/FPT" in links, broken)

# a bank: its KPIs against the median of the banks with the same quarter (the evening job's read, point-in-time)
import statistics  # noqa: E402

from tradingagents.dataflows import vn_fundamentals as vf, vn_peers as vp  # noqa: E402

VALDIR = CACHE / "valuation"
VALDIR.mkdir(parents=True, exist_ok=True)
t.ok("VCI's signs dropped (CIR, cover, credit cost come negative); a bank's 0 is VCI's 'no figure', another stock's is 0",
     vp.value({"cir": -0.2409}, "cir") == 0.2409 and vp.value({"loansLossReservesToNPLs": -1.34}, "loansLossReservesToNPLs") == 1.34
     and vp.value({"provisionToOutstandingLoans": -0.0095}, "provisionToOutstandingLoans") == 0.0095
     and vp.value({"npl": 0.0}, "npl") is None and vp.value({"grossMargin": 0.0}, "grossMargin", bank=False) == 0.0)
ctg_own = vp.own("CTG", "2026-10-30")
rows = {k.field: k for k in ctg_own.rows}
t.ok("CTG's own KPIs of Q2/2026 (published 19/08) and of Q2/2025 beside them, signs fixed; its 5-year median ROE "
     "2021-2025 (the card's sustainable ROE)", ctg_own.period == "Q2/2026" and ctg_own.published == date(2026, 8, 19)
     and round(rows["cir"].value, 4) == 0.2409 and round(rows["cir"].prior, 4) == 0.2795
     and round(rows["loansLossReservesToNPLs"].value, 4) == 1.3395 and round(rows["npl"].value, 4) == 0.0120
     and round(rows["provisionToOutstandingLoans"].value, 4) == 0.0095 and ctg_own.roe5_years == (2021, 2022, 2023, 2024, 2025)
     and round(ctg_own.roe5, 4) == 0.1712 and ctg_own.peers_read is None, ctg_own)
peers = PEERS_FX["peers"]
(VALDIR / "peers-8300-2026-10-29.json").write_text(json.dumps(dict(peers, day="2026-10-29")), encoding="utf-8")
(VALDIR / "peers-8300-2026-10-30.json").write_text(json.dumps({"day": "2026-10-30", "banks": {"CTG": {"q": {}, "roe5": 0.17}}}),
                                                   encoding="utf-8")       # v1.17.0 phase A's layout: no publication days
t.ok("the newest read kept, an older layout (no publication days) passed over", vp.latest_bank_peers()["day"] == "2026-10-29"
     and vp.latest_bank_peers("2026-10-28") is None and vp.bank_peers("2026-10-30") is None)
for old_day in ("2026-10-27", "2026-10-28"):
    (VALDIR / f"peers-8300-{old_day}.json").write_text(json.dumps(dict(peers, day=old_day)), encoding="utf-8")
    vp.latest_bank_peers(old_day)
vp.latest_bank_peers()
t.ok(f"a new day's read parsed each day: only the newest {vp.PARSED_MAX} kept in memory (not one per day of uptime)",
     sorted(os.path.basename(k) for k in vp._parsed) == ["peers-8300-2026-10-28.json", "peers-8300-2026-10-29.json"],
     list(vp._parsed))
for old_day in ("2026-10-27", "2026-10-28"):
    (VALDIR / f"peers-8300-{old_day}.json").unlink()
ctg = vp.kpi_table("CTG", "2026-10-30")
rows = {k.field: k for k in ctg.rows}
same = [b["q"]["2026-2"] for c, b in peers["banks"].items() if c != "CTG" and "2026-2" in b["q"]]
t.ok("CTG against the 27 banks with Q2/2026 (15 of them have Q3 out already, not mixed in): CIR 24,1% (median 35,2%, "
     "3rd), NPL 1,20% (1,87%, 6th), cover 134,0% (66,8%, 3rd) - the PDF's own figures, the medians the bot's",
     ctg.with_period == 27 and ctg.banks == 27 and ctg.newer == 15
     and vp.fmt(rows["cir"].value) == "24,1%" and vp.fmt(rows["cir"].median) == "35,2%" and rows["cir"].rank == 3
     and vp.fmt(rows["npl"].value) == "1,20%" and vp.fmt(rows["npl"].median) == "1,87%" and rows["npl"].rank == 6
     and vp.fmt(rows["loansLossReservesToNPLs"].value) == "134,0%" and vp.fmt(rows["loansLossReservesToNPLs"].median) == "66,8%"
     and rows["loansLossReservesToNPLs"].rank == 3 and rows["cir"].n == 27
     and rows["cir"].median == statistics.median([q["cir"] for q in same] + [rows["cir"].value]),
     [(k.field, vp.fmt(k.value), vp.fmt(k.median), k.rank, k.n) for k in ctg.rows])
t.ok("... ranks by the KPI's way: lower is better for CIR, NPL, credit cost; higher for ROE, NIM, cover, CASA; LDR not "
     "ranked", [k.better for k in ctg.rows] == ["high", "high", "low", "low", "high", "low", "high", None]
     and rows["ldrLoanDepositRatio"].rank is None and rows["ldrLoanDepositRatio"].median is not None)
t.ok("... its 5-year median ROE 17,1%: 11th of 27 banks, their median 14,9%", vp.fmt(ctg.roe5) == "17,1%"
     and (ctg.roe5_rank, ctg.roe5_of) == (11, 27) and vp.fmt(ctg.roe5_median) == "14,9%")
vpb_q = peers["banks"]["VPB"]["q"]["2026-3"]
vpb = vp.KpiTable(symbol="VPB", profile="banking", as_of=date(2026, 10, 25), key="2026-3", period="Q3/2026",
                  published=date(2026, 10, 19), rows=tuple(vp.Kpi(f, label, kind, better, vpb_q[f], None)
                                                           for f, label, kind, better in vp.BANK_KPIS))
early = vp.compare(vpb, peers)
late = vp.compare(dataclasses.replace(vpb, as_of=date(2026, 10, 30)), peers)
q3 = {c: b["q"]["2026-3"] for c, b in peers["banks"].items() if "2026-3" in b["q"]}
t.ok("mixed periods: VPB's Q3/2026 against the banks that had published Q3 by the run date only - 6 on 25/10 (VPB, TCB, "
     "LPB, ABB, PGB, SGB), 15 on 30/10; a Q2 bank's figures never in its median",
     early.with_period == 6 and late.with_period == 15 and early.banks == late.banks == 27
     and {k.field: k for k in early.rows}["cir"].median == statistics.median(
         q["cir"] for c, q in q3.items() if q["pub"] <= "2026-10-25" and q["cir"] is not None)
     and {k.field: k for k in late.rows}["npl"].median == statistics.median(
         q["npl"] for q in q3.values() if q["npl"] is not None),
     (early.with_period, late.with_period))
season = vp.compare(dataclasses.replace(vpb, as_of=date(2026, 10, 21)), peers)
t.ok(f"... on 21/10 only 3 banks had Q3 out (under {vp.MIN_PEERS}): no median nor rank, said so, the 5-year ROE still ranked",
     season.with_period == 3 and all(k.median is None and k.rank is None for k in season.rows)
     and vp.fundamentals_lines(season)[0] == "- So với các ngân hàng: mới 3/27 ngân hàng đã công bố Q3/2026 tới 21/10/2026, "
                                              "chưa đủ để lấy trung vị cùng kỳ"
     and "chưa có trung vị các ngân hàng: mới 3 ngân hàng đã có Q3/2026" in dg.bank_line(season), vp.fundamentals_lines(season))
aug = vp.kpi_table("CTG", "2026-08-18")
aug_rows = {k.field: k for k in aug.rows}
q2_by = sum(1 for c, b in peers["banks"].items() if c != "CTG" and b["q"].get("2026-2", {}).get("pub", "9") <= "2026-08-18")
t.ok("a past run date, point-in-time: 18/08 is before CTG's Q2 (19/08) - its Q1/2026 against the banks' Q1, the banks "
     "with Q2 out by then counted as newer", aug.period == "Q1/2026" and aug.with_period == 27 and aug.newer == q2_by
     and 0 < q2_by < 26 and aug_rows["cir"].median == statistics.median(
         [b["q"]["2026-1"]["cir"] for c, b in peers["banks"].items() if c != "CTG"] + [aug_rows["cir"].value]),
     (aug.period, aug.newer, q2_by))
aug_line = vp.fundamentals_lines(aug)[0]
t.ok("... its text dated by the run date, never by the later read (29/10) the analyst would quote",
     "(27/27 ngân hàng đã công bố kỳ này tới 18/08/2026) [THỐNG KÊ 18/08]: " in aug_line and "29/10" not in aug_line, aug_line)
old = vp.compare(dataclasses.replace(vpb, as_of=date(2023, 6, 1), key="2023-1", period="Q1/2023"), peers)
t.ok("a run date before the read's quarters: 'không phủ', not '1/1 bank published' (all of them had)",
     not old.covered and vp.fundamentals_lines(old)[0] == "- So với các ngân hàng: số đọc sẵn không phủ kỳ Q1/2023 (bot giữ 12 "
                                                           "quý gần nhất của mỗi ngân hàng), không so được cùng kỳ"
     and "số đọc sẵn không phủ kỳ này" in dg.bank_line(old) and all(k.median is None for k in old.rows),
     vp.fundamentals_lines(old))


def twelve(first: tuple[int, int]) -> dict:
    """A bank's read of its 12 newest quarters from ``first``, each published 4 weeks after its quarter."""
    out, (y, q) = {}, first
    for _ in range(vp.PEER_QUARTERS):
        out[f"{y}-{q}"] = {"pub": date(y + (q == 4), 1 if q == 4 else 3 * q + 1, 28).isoformat(), "cir": 0.3, "npl": 0.02}
        y, q = (y + 1, 1) if q == 4 else (y, q + 1)
    return {"q": out, "y": {}}


cut = {"day": "2026-10-30", "banks": {**{f"A{i}": twelve((2023, 4)) for i in range(6)},      # Q3/2026 out: Q3/2023 cut
                                      **{f"L{i}": twelve((2023, 3)) for i in range(5)}}}      # Q2/2026 newest: Q3/2023 kept
q3_23 = vp.compare(dataclasses.replace(vpb, as_of=date(2024, 3, 1), key="2023-3", period="Q3/2023"), cut)
t.ok("... a bank whose kept quarters start after the stock's is left out (its Q3/2023 was cut, not unpublished): "
     "5 banks with it kept + the stock", q3_23.covered and (q3_23.banks, q3_23.with_period) == (6, 6), (q3_23.banks, q3_23.with_period))
t.ok("... and none kept that far back: not covered",
     not vp.compare(dataclasses.replace(vpb, as_of=date(2024, 3, 1), key="2023-2", period="Q2/2023"), cut).covered)
march = vp.kpi_table("CTG", "2026-03-30")
t.ok("... the 5-year ROE of 30/03/2026: the 2025 annual report (31/03) not out yet - 2020-2024, the banks' likewise",
     march.roe5_years == (2020, 2021, 2022, 2023, 2024) and march.roe5_published == date(2025, 3, 5)
     and march.roe5_of == 1 + sum(1 for c, b in peers["banks"].items() if c != "CTG"
                                  and sum(1 for e in b["y"].values() if e["pub"] <= "2026-03-30") >= vp.MIN_ROE_YEARS),
     (march.roe5_years, march.roe5_published, march.roe5_of))
lines = vp.fundamentals_lines(ctg)
t.ok("the fundamentals report's two lines: same quarter, n/M banks, the read's date, labels, the same quarter a year before",
     lines[0].startswith("- So với trung vị các ngân hàng cùng kỳ Q2/2026 (27/27 ngân hàng đã công bố kỳ này tới ngày VCI "
                         "đọc 29/10/2026) [THỐNG KÊ 29/10]: ")
     and "Chi phí/thu nhập (CIR) 24,1% (cùng kỳ 28,0%; trung vị 35,2%, hạng 3/27)" in lines[0]
     and "Cho vay/huy động (LDR) 110,7% (cùng kỳ 110,4%; trung vị 110,6%)" in lines[0] and "quý quy năm" in lines[0]
     and lines[1] == "- ROE trung vị 5 năm 2021-2025: 17,1%, hạng 11/27 ngân hàng (trung vị nhóm 14,9%) [THỐNG KÊ 31/03]",
     lines)
report = vf.get_fundamentals("CTG")
t.ok("get_fundamentals (the analyst): the bank line with the signs fixed, then the comparison and the rank",
     "CIR: 24,09% | NPL: 1,20% | Bao phủ nợ xấu: 133,95% | Chi phí tín dụng: 0,95%" in report
     and lines[0] in report and lines[1] in report, report[:1500])
t.ok("... a past run date reads the same file point-in-time",
     vp.fundamentals_lines(aug)[0] in vf.get_fundamentals("CTG", "2026-08-18"))
real_rows = vf.published_ratio_rows
vf.published_ratio_rows = lambda sym, as_of: [dict(real_rows(sym, as_of)[0], npl=0.0, loansLossReservesToNPLs=0.0)]
zero = vf.get_fundamentals("CTG")
vf.published_ratio_rows = real_rows
t.ok("... VCI's 0 for a bank (VBB Q3/2026: NPL and cover 0) is no figure: N/A, not 'NPL 0,00%' to the analyst",
     "| NPL: N/A | Bao phủ nợ xấu: N/A | Chi phí tín dụng: 0,95%" in zero and "NPL: 0,00%" not in zero
     and "Bao phủ nợ xấu: 0,00%" not in zero,
     zero[:900])
t.ok("... no bank's read kept: said, nothing asked of VCI", vp.fundamentals_lines(ctg_own)[0].startswith(
    "- So với các ngân hàng: chưa có số đọc sẵn"))
money_flows._down_until.clear()
(VALDIR / "peers-8300-2026-10-30.json").unlink()
read = vp.bank_peers("2026-10-30", compute=True)
ctg_read = read["banks"]["CTG"]
t.ok("the evening job's read of every listed bank: PGB without ratio rows skipped (no breaker tripped), CTG's quarters with "
     "their publication day, signs fixed, its annual ROEs; kept on disk for the day",
     sorted(read["banks"]) == ["CTG"] and read["format"] == vp.FORMAT and ctg_read["q"]["2026-2"]["pub"] == "2026-08-19"
     and ctg_read["q"]["2026-2"]["cir"] > 0 and len(ctg_read["q"]) == 6 and ctg_read["y"]["2025"]["pub"] == "2026-03-31"
     and not money_flows._down_until and vp.bank_peers("2026-10-30") == read and vp.bank_peers("2026-10-31") is None)
(VALDIR / "peers-8300-2026-10-30.json").unlink()
LISTING_DOWN["nodata"] = True
try:
    vp.bank_peers("2026-10-30", compute=True)
    refused = False
except NoMarketDataError:
    refused = True
LISTING_DOWN["nodata"] = False
t.ok("VCI's listing without banks: nothing kept for the day (an empty file would hide 29/10's), raised for the job to retry",
     refused and not (VALDIR / "peers-8300-2026-10-30.json").exists() and vp.latest_bank_peers()["day"] == "2026-10-29")
dinhgia_web.look("CTG", SETTINGS.data_dir, wait=30)
bank = web.get("/dinhgia/CTG", auth=AUTH).text
t.ok("CTG's page: the PDF's table - chỉ số · CTG · median of the same quarter · rank and note · label and date",
     all(x in bank for x in ("Trung vị ngân hàng cùng kỳ", "Hạng · ghi chú", "Nhãn · ngày", "24,1%", "35,2% <span",
                             "(27 NH)", "hạng 3/27 (thấp là tốt) · cùng kỳ Q2/2025: 28,0%", "ROE trung vị 5 năm",
                             "hạng 11/27 (cao là tốt)", "15 ngân hàng đã có kỳ mới hơn nhưng vẫn so cùng kỳ",
                             "FACT 19/08", "THỐNG KÊ 29/10", "THỐNG KÊ 31/03")),
     [x for x in ("Trung vị ngân hàng cùng kỳ", "Hạng · ghi chú", "Nhãn · ngày", "24,1%", "35,2% <span",
                  "(27 NH)", "hạng 3/27 (thấp là tốt) · cùng kỳ Q2/2025: 28,0%", "ROE trung vị 5 năm",
                  "hạng 11/27 (cao là tốt)", "15 ngân hàng đã có kỳ mới hơn nhưng vẫn so cùng kỳ",
                  "FACT 19/08", "THỐNG KÊ 29/10", "THỐNG KÊ 31/03") if x not in bank])
t.ok("... CTG followed by nothing (watchlist, Tài sản, cards): computed when viewed, not said to be recomputed at 16:00",
     "bot tính khi có người xem, không tự tính sau phiên" in bank and "Bot tự tính lại sau" not in bank)
tg._handle({"chat": {"id": 111}, "from": {"id": 111}, "text": "/dinhgia CTG"})
bank_text = sent[-1][1]
t.ok("/dinhgia CTG: one line of its KPIs against the banks (median · rank) with the read's date and label",
     "🏦 So với 27 ngân hàng đã có Q2/2026 (trong ngoặc: trung vị nhóm · hạng, 1 là tốt nhất; 15 NH đã có kỳ mới hơn; VCI "
     "đọc 29/10) [THỐNG KÊ 29/10]: NIM 2,69% (2,69% · 14/27) · CIR 24,1% (35,2% · 3/27) · NPL 1,20% (1,87% · 6/27) · "
     "bao phủ nợ xấu 134,0% (66,8% · 3/27) · chi phí tín dụng 0,95% (0,99% · 13/27) · ROE 5 năm 17,1% (14,9% · 11/27)"
     in bank_text, bank_text)
t.ok("... a stock not a bank: no such line", "🏦" not in text)
t.ok("... no banks' read: the bank's own figures, and why there is no median",
     dg.bank_line(ctg_own).startswith("🏦 Chỉ số Q2/2026: NIM 2,69% · CIR 24,1%") and "chưa có trung vị" in dg.bank_line(ctg_own))
os.environ["VALUATION_KE_BANK"] = "10"
ke10 = dinhgia_web._book_card(vv.assess("CTG", "2026-10-28"))
del os.environ["VALUATION_KE_BANK"]
t.ok("the Ke note follows VALUATION_KE_BANK (10%): 'ngân hàng 10%', the row in use 10%",
     "ngân hàng 10%, mã khác 13%" in ke10 and "ngân hàng 12%" not in ke10
     and '>10%<br><span class="muted small">(đang dùng)</span></td>' in ke10)   # the label under the rate: fits a phone


# the card built off the request: "đang tính" and a reload, and a cap on the pages' builds
gate = threading.Event()


def slow(symbol, as_of):
    gate.wait(10)
    return real_assess(symbol, as_of)


vv.assess = slow
dinhgia_web.WAIT_SECONDS = 0.2
wait_page = web.get("/dinhgia/VHM", auth=AUTH).text
for sym in ("FPT", "HPG"):
    dinhgia_web.look(sym, SETTINGS.data_dir, wait=0)
busy = web.get("/dinhgia/CTG", auth=AUTH).text
gate.set()
for th in list(dinhgia_web._looks.values()):
    th.join(10)
vv.assess = real_assess
t.ok("not built within the wait: 'đang tính' and the page reloads", "Đang tính định giá VHM" in wait_page
     and 'http-equiv="refresh" content="5"' in wait_page)
t.ok(f"{dinhgia_web.MAX_LOOKS} cards being built: another one waits its turn", "đang tính định giá vài mã khác" in busy)
t.ok("... then the page has it", "Độ mới của dữ liệu" in web.get("/dinhgia/VHM", auth=AUTH).text)

# ───────────── 6. the evening job ─────────────
JOB = ROOT / "job"
JOB.mkdir()
store_mod.reset()
active, peak, lock = [0], [0], threading.Lock()


def counted(symbol, as_of):
    with lock:
        active[0] += 1
        peak[0] = max(peak[0], active[0])
    time.sleep(0.05)
    with lock:
        active[0] -= 1
    if symbol == "BAD":
        raise VendorUnavailableError("VCI-info failed recently")
    if symbol in ("NONE", "MORN"):
        return vv.NoCard(symbol, date(2026, 10, 30), "default", "đang lỗ")
    return replace(hpg, symbol=symbol, day=date(2026, 10, 28) if symbol == "OLD" else hpg.day)


vv.assess = counted
asked_peers = []
real_peers = vp.bank_peers
vp.bank_peers = lambda day, compute=False: asked_peers.append((day, compute))
symbols = ["A01", "A02", "A03", "A04", "OLD", "NONE", "BAD"]
out = dg.run_job(symbols, JOB, session="2026-10-29", today="2026-10-30")
t.ok("the job: each stock's outcome; at most two built at once",
     out == {"done": ["A01", "A02", "A03", "A04"], "behind": ["OLD"], "none": ["NONE"], "kept": [], "failed": ["BAD"],
             "peers": []}
     and peak[0] <= dg.JOB_WORKERS, (out, peak))
t.ok("... no bank among them: the banks' KPIs not read", asked_peers == [])
again = dg.run_job(symbols, JOB, session="2026-10-29", today="2026-10-30")
t.ok("run again the same day: done and refused ones kept, the older price and the failure tried again",
     again["kept"] == ["A01", "A02", "A03", "A04", "NONE"] and again["behind"] == ["OLD"] and again["failed"] == ["BAD"])
dg.run_job(["CTG"], JOB, session="2026-10-29", today="2026-10-30")
t.ok("a bank among them: the day's banks' KPIs asked for", asked_peers == [("2026-10-30", True)])
vp.bank_peers = lambda day, compute=False: (_ for _ in ()).throw(VendorUnavailableError("VCI-info failed recently"))
t.ok("... not read: said in the outcome (the scheduler tries again)",
     dg.run_job(["CTG"], JOB, session="2026-10-29", today="2026-10-30")["peers"] == ["failed"])
dg.Cards(JOB).record(vv.NoCard("MORN", date(2026, 10, 30), "default", "đang lỗ"), "2026-10-30")   # a view at 10:00
NOW["t"] = datetime(2026, 10, 30, 16, 5, tzinfo=vn_rules.VN_TZ)
morning = dg.run_job(["MORN"], JOB, session="2026-10-30", today="2026-10-30")
evening = dg.run_job(["MORN"], JOB, session="2026-10-30", today="2026-10-30")
t.ok("a refusal from a morning view is built again by the evening job (a statement may be out since), then kept",
     morning["none"] == ["MORN"] and evening["kept"] == ["MORN"], (morning, evening))
NOW["t"] = datetime(2026, 10, 30, 10, 0, tzinfo=vn_rules.VN_TZ)
vv.assess = real_assess
vp.bank_peers = real_peers

# VCI's price history down for a stock never fetched: an outage, not "no card"
money_flows._down_until.clear()
FLOWS_DOWN.add("OUTG")
first = dg.run_job(["OUTG"], JOB, session="2026-10-29", today="2026-10-30")
t.ok("price history down for a stock not cached yet: failed (retried), nothing kept, no 'no card' cached, the breaker "
     "tripped", first["failed"] == ["OUTG"] and dg.Cards(JOB).rows("OUTG") == [] and not vv.kept("OUTG", None)
     and money_flows.VCI_INFO in money_flows._down_until, (first, dg.Cards(JOB).rows("OUTG")))
tg._handle({"chat": {"id": 111}, "from": {"id": 111}, "text": "/dinhgia OUTG"})
t.ok("... /dinhgia says VCI is down, not 'chưa có giá phiên'", sent[-1][1].startswith("⚠️ VCI đang lỗi hoặc chậm, chưa tính "
                                                                                    "được định giá OUTG"), sent[-1])
FLOWS_DOWN.clear()
money_flows._down_until.clear()
second = dg.run_job(["OUTG"], JOB, session="2026-10-29", today="2026-10-30")
t.ok("... VCI back: the card", second["done"] == ["OUTG"] and dg.Cards(JOB).rows("OUTG")[0]["base"] is not None, second)

t.ok("tracked: the watchlist, then the holdings and the owner's cards; once each, no index or bad code",
     dg.tracked(["fpt", "VNINDEX", "FPT", "ABCDEFGHIJK"], SimpleNamespace(book=lambda: SimpleNamespace(holdings={"VCB": 1, "FPT": 2}),
                                                                   valuations=lambda: {"HPG": {}, "VCB": {}}))
     == ["FPT", "VCB", "HPG"])

from vnbot.service import DailyScheduler  # noqa: E402

runs = []
results = [{"done": [], "behind": ["X"], "none": [], "kept": [], "failed": [], "peers": []},
           {"done": [], "behind": [], "none": [], "kept": ["X"], "failed": [], "peers": ["failed"]},
           {"done": [], "behind": [], "none": [], "kept": ["X"], "failed": [], "peers": []}]
real_run = dg.run_job
dg.run_job = lambda syms, data_dir, session: runs.append((syms, session)) or results[len(runs) - 1]
sched = DailyScheduler(SETTINGS, JobQueue(SETTINGS), watch, bot=SimpleNamespace(taisan=account))
NOW["t"] = datetime(2026, 10, 30, 15, 59, tzinfo=vn_rules.VN_TZ)
t.ok("never run (a first start), before 16:00: the session before's job is caught up", sched._valuation_due() == "2026-10-29")
(SETTINGS.data_dir / "last_valuation_run.txt").write_text("2026-10-29", encoding="utf-8")
t.ok("before 16:00, the session before's job done: not due", sched._valuation_due() is None)
NOW["t"] = datetime(2026, 10, 30, 16, 0, tzinfo=vn_rules.VN_TZ)
day = sched._valuation_due()
sched._valuation_run(day)
t.ok("16:00 on a session day: the tracked stocks (watchlist + Tài sản); an older price: tried again in an hour",
     day == "2026-10-30" and runs[0] == (["FPT", "HPG", "VHM", "NEGEQ"], "2026-10-30")
     and sched._valuation_due() is None and sched._valuation_next > time.time() + 3500)
sched._valuation_next = 0.0
sched._valuation_run(sched._valuation_due())
t.ok("... the banks' medians not read: tried again in an hour too", len(runs) == 2 and sched._valuation_due() is None
     and (SETTINGS.data_dir / "last_valuation_run.txt").read_text(encoding="utf-8") == "2026-10-29")
sched._valuation_next = 0.0
sched._valuation_run(sched._valuation_due())
t.ok("... all done: stamped, not due again today", len(runs) == 3 and sched._valuation_due() is None
     and (SETTINGS.data_dir / "last_valuation_run.txt").read_text(encoding="utf-8") == "2026-10-30")
NOW["t"] = datetime(2026, 10, 31, 17, 0, tzinfo=vn_rules.VN_TZ)
t.ok("a Saturday: not due", sched._valuation_due() is None)
(SETTINGS.data_dir / "last_valuation_run.txt").write_text("2026-10-29", encoding="utf-8")
NOW["t"] = datetime(2026, 11, 2, 8, 30, tzinfo=vn_rules.VN_TZ)
sched._valuation_next = 0.0                     # a restart
t.ok("the bot down on Friday evening: Monday 08:30 catches up Friday's job (the session's alerts need its cards)",
     sched._valuation_due() == "2026-10-30")
dg.run_job = real_run

# the zones the evening job moved: one block of the next watchlist report, said once
ZJ = ROOT / "zonejob"
ZJ.mkdir()
# HPG's card: Base 16.983, Bull 21.440; the zones come from the close (the card's own zone field is not read)
for sym, close in (("ZA", 19_000.0), ("ZB", 21_500.0), ("ZC", 19_000.0)):
    dg.Cards(ZJ).save(dict(dg.row_of(replace(hpg, close=close), "2026-10-29"), symbol=sym))
CLOSES = {"ZA": 16_900.0, "ZB": 21_200.0, "ZC": 19_000.0}
vv.assess = lambda symbol, as_of: replace(hpg, symbol=symbol, close=CLOSES[symbol])
dg.run_job(["ZA", "ZB", "ZC"], ZJ, session="2026-10-30", today="2026-10-30")
moved = json.loads((ZJ / dg.ZONES_FILE).read_text(encoding="utf-8"))
t.ok("the evening job keeps the zones it moved against each stock's row of an earlier day (ZC did not move), and "
     "each stock's evening zone",
     moved["day"] == "2026-10-30" and moved["changes"] == {
         "ZA": {"from": "Hợp lý", "to": "Rẻ", "close": 16_900.0, "base": hpg.base, "bull": hpg.bull},
         "ZB": {"from": "Đắt", "to": "Hợp lý", "close": 21_200.0, "base": hpg.base, "bull": hpg.bull}}
     and moved["zones"]["ZA"] == {"day": "2026-10-30", "from": "Hợp lý", "zone": "Rẻ"}
     and moved["zones"]["ZC"]["zone"] == "Hợp lý", moved)
CLOSES["ZA"] = 19_000.0
dg.run_job(["ZA"], ZJ, session="2026-10-30", today="2026-10-30")
t.ok("... a later run of the day that finds ZA back where it was: no move kept for it (judged from the zone the "
     "session started from, not from its own first run)",
     set(json.loads((ZJ / dg.ZONES_FILE).read_text(encoding="utf-8"))["changes"]) == {"ZB"})

# the alerts' rule in the evening too: Đắt from Base + 2% when Bull is a tick above Base, the 0,5% buffer, remembered
ZM = ROOT / "zonemove"
ZM.mkdir()
TICK = replace(hpg, symbol="ZM", bull=hpg.base * 1.0037)     # MBB 30/10/2026: Bull +0,37% above Base
said = {}
for session, close in (("2026-10-29", 16_950.0), ("2026-10-30", 17_050.0), ("2026-11-02", 16_950.0),
                       ("2026-11-03", 16_960.0), ("2026-11-04", 17_100.0), ("2026-11-05", 17_400.0)):
    vv.assess = lambda symbol, as_of, close=close: replace(TICK, close=close)
    dg.run_job(["ZM"], ZM, session=session, today=session)
    said[session] = dg.night_block(ZM, session)
vv.assess = real_assess
t.ok("a close a tick past Bull (17.050 > 17.046) stays Rẻ: the alerts' Đắt is Base + 2% (17.323) and Rẻ is left only "
     "0,5% past Base (17.068); back under Base, and just under it again: nothing (no 'Rẻ → Đắt', no 'Đắt → Rẻ')",
     [said[d] for d in ("2026-10-29", "2026-10-30", "2026-11-02", "2026-11-03")] == ["", "", "", ""], said)
t.ok("... 17.100 is past the buffer: Rẻ → Hợp lý once; 17.400 Hợp lý → Đắt, with where the alerts' Đắt starts",
     "ZM Rẻ → Hợp lý (giá 17.100, Base 16.983, Bull 17.046, Đắt từ 17.323)" in said["2026-11-04"]
     and "ZM Hợp lý → Đắt (giá 17.400, Base 16.983, Bull 17.046, Đắt từ 17.323)" in said["2026-11-05"], said)
t.ok("... the close's zone on the alerts' levels: Base Rẻ, a tick past Bull Hợp lý, Base + 2% Đắt",
     [dg.close_zone(dg.row_of(replace(TICK, close=c), "2026-11-05")) for c in (TICK.base, 17_050.0, TICK.base * 1.02)]
     == ["Rẻ", "Hợp lý", "Đắt"])
block = dg.night_block(ZJ, "2026-10-30")
t.ok("the job of 30/10 done: one message, the move at the close with its levels",
     block == "📐 Thẻ định giá của bot đổi vùng (tính sau phiên 30/10, theo giá đóng cửa): ZB Đắt → Hợp lý (giá 21.200, "
              "Base 16.983, Bull 21.440). Chi tiết: /dinhgia MÃ (tham khảo, không vào quyết định).", block)
t.ok("... said once", dg.night_block(ZJ, "2026-10-30") == "")
ZJ2 = ROOT / "zonejob2"
ZJ2.mkdir()
dg._keep_zone_changes(ZJ2, "2026-10-30", {"ZC": {"from": "Hợp lý", "to": "Đắt", "close": 1.0, "base": 1.0, "bull": 1.0}})
t.ok("another session's job (the moves of 30/10 asked for 02/11): not said, not marked as said",
     dg.night_block(ZJ2, "2026-11-02") == "" and json.loads((ZJ2 / dg.ZONES_FILE).read_text(encoding="utf-8"))["reported"] == "")
t.ok("... its own session's: said", dg.night_block(ZJ2, "2026-10-30").startswith(
    "📐 Thẻ định giá của bot đổi vùng (tính sau phiên 30/10"))

# a view after the bars (15:35) and before the job: built again by the job, so the next session's alerts use it
from vnbot.intraday import IntradayMonitor  # noqa: E402

VJ = ROOT / "viewjob"
VJ.mkdir()
NOW["t"] = datetime(2026, 10, 29, 16, 10, tzinfo=vn_rules.VN_TZ)
for sym in ("VW", "VK"):
    dg.Cards(VJ).record(replace(hpg, symbol=sym, zone="Hợp lý"), "2026-10-29")             # the job of 29/10
NOW["t"] = datetime(2026, 10, 30, 15, 35, tzinfo=vn_rules.VN_TZ)
dg.Cards(VJ).record(replace(hpg, symbol="VW", close=16_900.0, day=date(2026, 10, 30)), "2026-10-30")   # /dinhgia VW, 15:35
NOW["t"] = datetime(2026, 10, 30, 16, 0, 30, tzinfo=vn_rules.VN_TZ)
dg.Cards(VJ).record(replace(hpg, symbol="VK", close=16_900.0, day=date(2026, 10, 30)), "2026-10-30")   # /dinhgia VK, 16:00
NOW["t"] = datetime(2026, 10, 30, 16, 5, tzinfo=vn_rules.VN_TZ)
vv.assess = lambda symbol, as_of: replace(hpg, symbol=symbol, close=16_900.0, day=date(2026, 10, 30))
viewed = dg.run_job(["VW", "VK"], VJ, session="2026-10-30")
vv.assess = real_assess
t.ok("the job: a view at 15:35 (the session's close, before 16:00) built again, one after 16:00 kept",
     viewed["done"] == ["VW"] and viewed["kept"] == ["VK"], viewed)
t.ok("... both zone moves kept for the message, the kept card's too",
     set(json.loads((VJ / dg.ZONES_FILE).read_text(encoding="utf-8"))["changes"]) == {"VW", "VK"})
NOW["t"] = datetime(2026, 11, 2, 9, 20, tzinfo=vn_rules.VN_TZ)
mon = IntradayMonitor(SimpleNamespace(data_dir=VJ), None, print, watchlist=lambda: ["VW", "VK"])
t.ok("... and the next session's alerts use both cards (the job's rule is theirs: built after 16:00 of 30/10)",
     sorted(mon._bot_cards("2026-11-02", ["VW", "VK"])) == ["VK", "VW"])
NOW["t"] = datetime(2026, 10, 30, 10, 0, tzinfo=vn_rules.VN_TZ)

dg._keep_zone_changes(SETTINGS.data_dir, "2026-10-29", {"FPT": {"from": "Hợp lý", "to": "Đắt", "close": 52_700.0,
                                                               "base": 29_212.0, "bull": 30_358.0}})
reported = []
rep = DailyScheduler(SETTINGS, JobQueue(SETTINGS), watch, bot=None, notify=reported.append)
rep._is_trading_day = lambda day: True
rep._scan_codes = lambda: ["FPT"]
rep._screen = lambda codes, day: ([], "📋 Quét watchlist 2026-10-30: Quant Engine đã xét 1 mã (không tốn lượt AI).")
rep._record_screen = lambda day: ""
rep._report("2026-10-30")
t.ok("the watchlist report (15:30) no longer carries the moves of the session before's job", len(reported) == 1
     and reported[0].startswith("📋 Quét watchlist 2026-10-30") and "📐" not in reported[0], reported)
dg.run_job = lambda syms, data_dir, session: {k: [] for k in ("done", "behind", "none", "kept", "failed", "peers")}
rep._valuation_run("2026-10-29")
dg.run_job = real_run
t.ok("... the job done for its session sends them at once, once", len(reported) == 2 and reported[1].startswith(
    "📐 Thẻ định giá của bot đổi vùng (tính sau phiên 29/10, theo giá đóng cửa): FPT Hợp lý → Đắt")
     and dg.night_block(SETTINGS.data_dir, "2026-10-29") == "", reported)

# ───────────── 7. informational only ─────────────
repo = _common.REPO
quant = [p.relative_to(repo).as_posix() for p in (repo / "tradingagents").rglob("*.py")
         if re.search(r"^\s*(from|import)\s+vnbot", p.read_text(encoding="utf-8", errors="replace"), re.M)]
t.ok("nothing of the Quant Engine reads /dinhgia or its rows", quant == [], quant)

t.done()
