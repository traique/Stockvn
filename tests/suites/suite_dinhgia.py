"""The bot's own valuation card of every stock (v1.17.0, vnbot.dinhgia and the /dinhgia pages): the
profiles and their Ke, the reliability line, the refusals (negative equity, too few annual ROEs, a
loss), the rows kept per (day, stock) in the history store or a JSON file and "what changed since the
last view", /dinhgia on Telegram and Zoom (one dispatcher, the 3.900-character split), the pages
behind the password, the evening job (bounded, skips what is done, retried, stamped), the banks'
medians. FPT, HPG and VHM recorded from VCI on 30/10/2026 (tests/fixtures/dinhgia_*_2026-10-30.json:
every 5th session since 2018 and the last 15), CTG from v1.16.0's fixture; the rest synthetic."""
import copy
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


variant("NEGEQ", negative_equity)
variant("LOSSY", a_loss)
variant("YOUNG", lambda fx: fx.update(annual_ratios=fx["annual_ratios"][-2:]))
PRICES = {s: fixture_prices(fx) for s, fx in FX.items()}
LISTING = [{"symbol": s, "type": "STOCK", "board": "HSX", "organName": f"Công ty {s}", "icbCode2": icb}
           for s, icb in (("NEGEQ", "1700"), ("LOSSY", "1700"), ("YOUNG", "1700"), ("XYZBDS", "8600"),
                          ("PGB", "8300"), ("FPT", "9500"), ("HPG", "1700"), ("VHM", "8600"), ("CTG", "8300"))]
LISTING[1]["organName"] = "Công ty Cổ phần Tập đoàn Hòa Phát"
LISTING_DOWN = {"on": False}
CALLS = {"flows": 0}


def fake_listing():
    if LISTING_DOWN["on"]:
        raise VendorUnavailableError("offline")
    return [dict(r) for r in LISTING]


def fake_flows(symbol, start, end):
    CALLS["flows"] += 1
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
     and round(fpt.base) == 29_212 and round(fpt.bear) == 25_700 and round(fpt.bull) == 29_263 and fpt.zone == "Đắt"
     and abs(fpt.base - fpt.book * vv.justified_pb(fpt.roe_base, 0.13, 0.4)) < 1e-6 and fpt.period == "Q3/2026",
     fpt)
t.ok("... reliability THẤP with its reasons: technology, ROE far above Ke, the book growing ≥ 20%/year",
     fpt.reliability == "thấp" and "công ty công nghệ" in fpt.reliability_why and "ROE bền vững 28,10%" in fpt.reliability_why
     and "vốn chủ sở hữu tăng 21,96%/năm trong 3 năm" in fpt.reliability_why, fpt.reliability_why)
t.ok("... P/E is its own multiple: both ranks there, P/B too", fpt.pe_rank is not None and fpt.pb_rank is not None
     and fpt.pe_rank.metric == "pe" and round(fpt.pe_rank.value, 2) == 9.66, (fpt.pe_rank, fpt.pb_rank))
t.ok("HPG: a cyclical, ROE 11,07% under Ke: Base below the price, Hợp lý, reliability TRUNG BÌNH (no reason to drop)",
     round(hpg.book) == 17_624 and round(hpg.base) == 16_983 and round(hpg.bull) == 21_440 and hpg.zone == "Hợp lý"
     and hpg.reliability == "trung bình" and hpg.reliability_why == valuation_config.RELIABILITY["default"][1]
     and hpg.bear < hpg.base < hpg.bull, hpg)
t.ok("VHM: real estate, Ke 13%, reliability THẤP (land at cost, profit by handover); the 6.000đ dividend went ex "
     "29/06, inside Q2/2026: already out of its book, over the shares doubled on 06/08",
     vhm.profile == "realestate" and vhm.assumptions.ke == 0.13 and vhm.reliability == "thấp"
     and "giá gốc" in vhm.reliability_why and round(vhm.book) == 32_088 and round(vhm.base) == 36_669
     and vhm.dividends == () and vhm.shares == 8_214_824_008, vhm)
old_fpt = vv.assess("FPT", "2026-10-28")
t.ok("point-in-time: on 28/10 FPT's Q3/2026 (published 29/10) is not there yet", old_fpt.period == "Q2/2026"
     and old_fpt.day == date(2026, 10, 28), old_fpt.period)
ctg = vv.assess("CTG", "2026-10-28")
t.ok("CTG keeps v1.16.0's card: Ke 12%, Base 27.643, reliability CAO", ctg.profile == "banking" and round(ctg.base) == 27_643
     and ctg.reliability == "cao" and vv.render_card(ctg).splitlines()[0].endswith("- ngân hàng ---"))
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
t.ok("Zoom ('dinhgia FPT', the same dispatcher)", sent[-1][0] == "zoom:7" and sent[-1][1].startswith("📐 ĐỊNH GIÁ FPT")
     and "Độ tin cậy: THẤP - công ty công nghệ" in sent[-1][1])
tg._handle({"chat": {"id": 111}, "from": {"id": 111}, "text": "/dinhgia NEGEQ"})
t.ok("no card: the reason, no levels", sent[-1][1].startswith("📐 ĐỊNH GIÁ NEGEQ · doanh nghiệp · bot tự tính\nBot không lập thẻ: "
                                                            "vốn chủ sở hữu công ty mẹ Q3/2026 âm") and "Base" not in sent[-1][1])
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
t.ok("... a non-bank: its own KPIs, no peer column", "Biên lãi gộp" in prof and "Trung vị ngân hàng cùng kỳ" not in prof)
nq = web.get("/dinhgia/NEGEQ", auth=AUTH).text
t.ok("no card: the reason on the page", "Bot không lập thẻ định giá cho NEGEQ: vốn chủ sở hữu công ty mẹ Q3/2026 âm" in nq)
t.ok("a bad code: said, with the box", "Mã không hợp lệ" in web.get("/dinhgia/%3Cb%3E", auth=AUTH).text)
links = sorted({h for h in re.findall(r'href="(/[^"#?]*)', page.text + prof)})
broken = [h for h in links if web.get(h, auth=AUTH).status_code >= 400]
t.ok("every link on the two pages answers", not broken and "/dinhgia/FPT" in links, broken)

# a bank: the medians of every listed bank, from the evening job's file (the page never reads VCI for them)
peers = {"day": "2026-10-29", "banks": {
    "CTG": {"q": {"2026-2": {"roe": 0.2176, "cir": 0.24, "npl": 0.012}}, "roe5": 0.1712},
    "AAA": {"q": {"2026-2": {"roe": 0.10, "cir": 0.40, "npl": 0.02}}, "roe5": 0.12},
    "BBB": {"q": {"2026-2": {"roe": 0.14, "cir": 0.30, "npl": 0.0}, "2026-3": {"roe": 0.5}}, "roe5": 0.20}}}
(CACHE / "valuation").mkdir(parents=True, exist_ok=True)
(CACHE / "valuation" / "peers-8300-2026-10-29.json").write_text(json.dumps(peers), encoding="utf-8")
med = dg.peer_medians(peers, "2026-2")
t.ok("medians of the banks with the same quarter; VCI's 0 (no figure) left out", med["roe"] == (0.14, 3)
     and med["npl"] == (0.016, 2) and med["cir"] == (0.30, 3) and dg.latest_bank_peers("2026-10-30")["day"] == "2026-10-29"
     and dg.latest_bank_peers("2026-10-28") is None)
money_flows._down_until.clear()
read = dg.bank_peers("2026-10-30", compute=True)
t.ok("the evening job's read of every listed bank: PGB without ratio rows skipped (no breaker tripped), CTG's quarters "
     "published by the day and its 5-year median ROE, kept on disk for the day",
     sorted(read["banks"]) == ["CTG"] and sorted(read["banks"]["CTG"]["q"]) == ["2025-4", "2026-1", "2026-2"]
     and round(read["banks"]["CTG"]["roe5"], 4) == 0.1712 and not money_flows._down_until
     and dg.bank_peers("2026-10-30") == read and dg.bank_peers("2026-10-31") is None)
(CACHE / "valuation" / "peers-8300-2026-10-30.json").unlink()
dinhgia_web.look("CTG", SETTINGS.data_dir, wait=30)
bank = web.get("/dinhgia/CTG", auth=AUTH).text
t.ok("CTG's page: the bank's KPIs against the medians, its 5-year ROE rank",
     "Trung vị ngân hàng cùng kỳ" in bank and "14,00%" in bank and "(3 NH)" in bank and "hạng 2/3" in bank, bank[-3000:])


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
    if symbol == "NONE":
        return vv.NoCard("NONE", date(2026, 10, 30), "default", "đang lỗ")
    return replace(hpg, symbol=symbol, day=date(2026, 10, 28) if symbol == "OLD" else hpg.day)


vv.assess = counted
asked_peers = []
real_peers = dg.bank_peers
dg.bank_peers = lambda day, compute=False: asked_peers.append((day, compute))
symbols = ["A01", "A02", "A03", "A04", "OLD", "NONE", "BAD"]
out = dg.run_job(symbols, JOB, session="2026-10-29", today="2026-10-30")
t.ok("the job: each stock's outcome; at most two built at once",
     out == {"done": ["A01", "A02", "A03", "A04"], "behind": ["OLD"], "none": ["NONE"], "kept": [], "failed": ["BAD"]}
     and peak[0] <= dg.JOB_WORKERS, (out, peak))
t.ok("... no bank among them: the banks' KPIs not read", asked_peers == [])
again = dg.run_job(symbols, JOB, session="2026-10-29", today="2026-10-30")
t.ok("run again the same day: done and refused ones kept, the older price and the failure tried again",
     again["kept"] == ["A01", "A02", "A03", "A04", "NONE"] and again["behind"] == ["OLD"] and again["failed"] == ["BAD"])
dg.run_job(["CTG"], JOB, session="2026-10-29", today="2026-10-30")
t.ok("a bank among them: the day's banks' KPIs asked for", asked_peers == [("2026-10-30", True)])
vv.assess = real_assess
dg.bank_peers = real_peers

t.ok("tracked: the watchlist, then the holdings and the owner's cards; once each, no index or bad code",
     dg.tracked(["fpt", "VNINDEX", "FPT", "ABCDEFGHIJK"], SimpleNamespace(book=lambda: SimpleNamespace(holdings={"VCB": 1, "FPT": 2}),
                                                                   valuations=lambda: {"HPG": {}, "VCB": {}}))
     == ["FPT", "VCB", "HPG"])

from vnbot.service import DailyScheduler  # noqa: E402

runs = []
results = [{"done": [], "behind": ["X"], "none": [], "kept": [], "failed": []},
           {"done": ["X"], "behind": [], "none": [], "kept": [], "failed": []}]
real_run = dg.run_job
dg.run_job = lambda syms, data_dir, session: runs.append((syms, session)) or results[len(runs) - 1]
sched = DailyScheduler(SETTINGS, JobQueue(SETTINGS), watch, bot=SimpleNamespace(taisan=account))
NOW["t"] = datetime(2026, 10, 30, 15, 59, tzinfo=vn_rules.VN_TZ)
t.ok("before 16:00: not due", sched._valuation_due() is None)
NOW["t"] = datetime(2026, 10, 30, 16, 0, tzinfo=vn_rules.VN_TZ)
day = sched._valuation_due()
sched._valuation_run(day)
t.ok("16:00 on a session day: the tracked stocks (watchlist + Tài sản); an older price: tried again in an hour",
     day == "2026-10-30" and runs[0] == (["FPT", "HPG", "VHM", "NEGEQ"], "2026-10-30")
     and sched._valuation_due() is None and sched._valuation_next > time.time() + 3500)
sched._valuation_next = 0.0
sched._valuation_run(sched._valuation_due())
t.ok("... all done: stamped, not due again today", len(runs) == 2 and sched._valuation_due() is None
     and (SETTINGS.data_dir / "last_valuation_run.txt").read_text(encoding="utf-8") == "2026-10-30")
NOW["t"] = datetime(2026, 10, 31, 17, 0, tzinfo=vn_rules.VN_TZ)
t.ok("a Saturday: not due", sched._valuation_due() is None)
dg.run_job = real_run

# ───────────── 7. informational only ─────────────
repo = _common.REPO
quant = [p.relative_to(repo).as_posix() for p in (repo / "tradingagents").rglob("*.py")
         if re.search(r"^\s*(from|import)\s+vnbot", p.read_text(encoding="utf-8", errors="replace"), re.M)]
t.ok("nothing of the Quant Engine reads /dinhgia or its rows", quant == [], quant)

t.done()
