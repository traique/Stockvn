"""Đột biến khối lượng (/dotbien, vnbot.dotbien), soi nhanh một mã (/soi, vnbot.soi) and the shark
flow (/camap, vnbot.camap), offline: VNDirect, DNSE and VCI faked, FPT's statements from the
fixture of 30/10/2026.

- the market history keeps volumes; a file saved before v1.20.0 (two-element rows) still reads,
  and the 21 recent sessions are fetched again when volumes are asked for;
- the list after the close: ratio against the 20 sessions BEFORE the session, the threshold,
  the liquidity filter, the order, the % change, the headline cap; in the session from the
  board, said "tạm tính"; the board without volumes; the history short: progress and a fill;
- RS scores: percentiles 1-99 of 2 x the last quarter + the quarter before, {} while short;
- /soi: the session from VNDirect's posted row and from DNSE's tick in the session, ceiling and
  floor from the band, the volume ratio and the averages over the 20 sessions before, the
  foreign line in shares, P/B at the price shown, SEPA from the fixture (FPT Q3/2026: YoY
  -5,94%, QoQ +6,18%, 2/5 quarters, the annual +19,15%, the cash by VCI's label), MA50/150/200,
  CANSLIM; the reading in its four states, the holder/buyer advice;
- /camap: ticks into orders, the classes, active buy against sell, the verdict (confirms the
  move, against it, balanced, too few big orders), the biggest orders;
- Telegram and Zoom: /dotbien, /soi FPT, /ptn, /camap FPT; the help and /docs list them.
"""
import copy
import json
from datetime import datetime
from pathlib import Path

import _common  # noqa: F401 - clean env, no .env, offline, temp folder, repo on sys.path

_common.need("httpx")
import os  # noqa: E402

os.environ.update({"TELEGRAM_TOKEN": "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawQ", "TELEGRAM_ALLOWED_IDS": "111"})
import pandas as pd  # noqa: E402

import _web_harness as wh  # noqa: E402
from tradingagents.dataflows.config import set_config  # noqa: E402
from tradingagents.dataflows.errors import NoMarketDataError, VendorUnavailableError  # noqa: E402
from tradingagents.dataflows.vendors import flows, vci  # noqa: E402
from tradingagents.market import vn_rules  # noqa: E402
from vnbot import camap, dotbien, market_scan as ms, quote, soi  # noqa: E402
from vnbot import telegram_bot as tb  # noqa: E402

t = _common.Checks()
DATA = Path(wh.SETTINGS.data_dir)
set_config({"data_cache_dir": str(DATA / "cache")})


def at(day, hh, mm):
    return datetime(int(day[:4]), int(day[5:7]), int(day[8:10]), hh, mm, tzinfo=vn_rules.VN_TZ)


def _raises(fn):
    try:
        fn()
    except ValueError:
        return True
    return False


# ═══════════════ 1. the market history with volumes ═══════════════
DAYS = ms.sessions("2026-11-26", 140)
MARKET: dict[str, dict] = {}          # day -> {code: (close, value, volume)}
CODES = ["AAA", "BBB", "CCC", "DDD", "EEE", "FPT", "TINY"]
for i, day in enumerate(DAYS):
    rows = {}
    for k, code in enumerate(CODES):
        close = 10_000 + 100 * k + (i * 20 if code in ("AAA", "FPT") else -i * 10 if code == "BBB" else 0)
        volume = 100_000 * (k + 1)
        value = volume * close
        if code == "TINY":
            volume, value = 1_000, 1_000 * close           # 10 million a day: below the liquidity floor
        rows[code] = (close, value, volume)
    MARKET[day] = rows
# the last session: spikes
MARKET["2026-11-26"]["AAA"] = (12_900, 12_900 * 400_000, 400_000)      # 400% of 100.000, +1,1% on 12.760
MARKET["2026-11-26"]["CCC"] = (10_200, 10_200 * 600_000, 600_000)      # 200% of 300.000
MARKET["2026-11-26"]["DDD"] = (10_300, 10_300 * 560_000, 560_000)      # 140%: under 150
MARKET["2026-11-26"]["TINY"] = (10_600, 10_600 * 9_000, 9_000)         # 900% but illiquid
FETCHED = []


def fake_market_day(day):
    FETCHED.append(day)
    if day not in MARKET:
        return []
    return [{"code": c, "floor": "HOSE", "close": v[0], "raw": v[0], "value": v[1], "volume": v[2]}
            for c, v in MARKET[day].items()]


ms.fetch_day = fake_market_day
ms.FETCH_PAUSE = 0.0
NOW = {"t": at("2026-11-26", 16, 0)}
vn_rules.now_vn = lambda: NOW["t"]

closes = ms.MarketCloses(DATA)
# a file from before v1.20.0: two-element rows for every session
old = {"basis": "2026-11-25", "floors": {c: "HOSE" for c in CODES}, "empty": [], "pre": {"day": "", "factor": {}},
       "days": {d: {c: [v[0], v[1]] for c, v in MARKET[d].items()} for d in DAYS[:-1]}}
closes.path.parent.mkdir(parents=True, exist_ok=True)
closes.path.write_text(json.dumps(old), encoding="utf-8")
have, want = closes.volume_progress("2026-11-26")
t.ok("an old file: 0 of 21 sessions have volumes", (have, want) == (0, 21), (have, want))
FETCHED.clear()
closes.update("2026-11-26", budget=30, volumes=True)
t.ok("update(volumes=True): the missing session and the 20 before fetched again (and the last saved for the re-base)",
     sorted(set(FETCHED)) == sorted(set(DAYS[-21:])), (len(FETCHED), FETCHED[:3]))
t.ok("...now 21/21 with volumes, the older sessions kept as they were",
     closes.volume_progress("2026-11-26") == (21, 21) and closes.progress("2026-11-26") == (130, 130))
FETCHED.clear()
closes.update("2026-11-26", budget=30, volumes=True)
t.ok("a second update the same day asks nothing more", FETCHED == [], FETCHED)
vols = closes.volumes("2026-11-26", 21)
t.ok("volumes(): per code, the 21 sessions oldest first", len(vols["AAA"]["days"]) == 21
     and vols["AAA"]["days"][-1] == "2026-11-26" and vols["AAA"]["volume"][-1] == 400_000 and vols["AAA"]["floor"] == "HOSE")
series, floors, value = closes.series(["AAA"], "2026-11-26")
t.ok("series() still reads rows with and without volumes", len(series["AAA"]) >= 130 and series["AAA"][-1] == 12_900, (len(series["AAA"]), series["AAA"][-1]))
t.ok("universe() too", "AAA" in closes.universe(6, "2026-11-26") and "TINY" not in closes.universe(6, "2026-11-26"))

# ─── RS scores ───
scores = closes.rs_scores("2026-11-26")
t.ok("rs_scores: every code with 127 closes scored 1-99", set(scores) == set(CODES) and all(1 <= s <= 99 for s in scores.values()), scores)
t.ok("...the riser (AAA) highest, the faller (BBB) lowest, the flat ones between",
     scores["AAA"] == max(scores.values()) and scores["BBB"] == min(scores.values()) and scores["BBB"] < scores["CCC"] < scores["AAA"], scores)
t.ok("...FPT rises like AAA: the same score", scores["FPT"] == scores["AAA"] or abs(scores["FPT"] - scores["AAA"]) <= 14, scores)
short = ms.MarketCloses(DATA / "short")
short.path.parent.mkdir(parents=True, exist_ok=True)
short.path.write_text(json.dumps({**old, "days": {d: old["days"][d] for d in DAYS[-30:-1]}}), encoding="utf-8")
t.ok("a short history: no scores", short.rs_scores("2026-11-26") == {})
t.ok("rs_label: the five bands", (soi.rs_label(19), soi.rs_label(20), soi.rs_label(85)) ==
     ("tiêu cực, hạn chế tham gia", "yếu", "mạnh, nhóm dẫn dắt"))

# ═══════════════ 2. /dotbien ═══════════════
r = dotbien.scan(DATA, NOW["t"])
rows = {x["symbol"]: x for x in r["rows"]}
t.ok("after the close: the session's list, AAA 400% then CCC 200%; DDD (140%) and TINY (illiquid) out",
     [x["symbol"] for x in r["rows"]] == ["AAA", "CCC"] and not r["live"] and r["day"] == "2026-11-26", r)
t.ok("...the ratio is against the 20 sessions BEFORE the session, the % change from the saved closes",
     rows["AAA"]["ratio"] == 400.0 and rows["AAA"]["avg"] == 100_000 and rows["AAA"]["change"] == 1.1
     and rows["CCC"]["change"] == 0.0, rows)
t.ok("...candidates: the liquid codes", r["candidates"] == 6, r["candidates"])
text = dotbien.text(r)
t.ok("the text: the header with the day, the standard, the headline codes, the detail rows",
     text.splitlines()[0] == "🚀 DANH SÁCH ĐỘT BIẾN KHỐI LƯỢNG (26/11/2026)"
     and text.splitlines()[1] == "(Tiêu chuẩn VolRelative >= 150% MA20 phiên)"
     and "🔥 Các mã nổi bật: AAA, CCC" in text and "• AAA 400%: 400 nghìn / 100 nghìn · +1,10%" in text
     and "• CCC 200%: 600 nghìn / 300 nghìn · 0,00%" in text, text)
r2 = dotbien.scan(DATA, NOW["t"], threshold=130)
t.ok("another threshold: DDD (140%) in", [x["symbol"] for x in r2["rows"]] == ["AAA", "CCC", "DDD"], r2["rows"])
t.ok("/dotbien 130 reads the threshold; a bad one refused", "AAA, CCC, DDD" in dotbien.command("130%", DATA)
     and _raises(lambda: dotbien.command("abc", DATA)) and _raises(lambda: dotbien.command("50", DATA)))

# in the session: the board's volumes so far
BOARD = {"calls": 0, "rows": {c: {"price": 10_000, "ref": 10_000, "exchange": "HOSE", "volume": None} for c in CODES}}


def fake_board(codes):
    BOARD["calls"] += 1
    return {c: dict(BOARD["rows"][c]) for c in codes if c in BOARD["rows"]}


NOW["t"] = at("2026-11-27", 10, 30)
r = dotbien.scan(DATA, NOW["t"], board=fake_board)
t.ok("in the session, the board without volumes: said so", "bảng giá chưa cho khối lượng" in r.get("error", ""), r)
BOARD["rows"]["BBB"]["volume"] = 500_000                 # 250% of 200.000 by 10:30
BOARD["rows"]["AAA"]["volume"] = 50_000
r = dotbien.scan(DATA, NOW["t"], board=fake_board)
t.ok("...with volumes: the session's volume so far against the 20 sessions' average, live",
     r["live"] and [x["symbol"] for x in r["rows"]] == ["BBB"] and r["rows"][0]["ratio"] == 250.0
     and r["rows"][0]["avg"] == 200_000 and r["day"] == "2026-11-27", r)
text = dotbien.text(r)
t.ok("...the text says it is provisional", "tới 10:30, tạm tính" in text.splitlines()[0] and "Trong phiên:" in text, text)

# the history short: progress, a fill
fresh = DATA / "fresh"
fresh.mkdir(exist_ok=True)
FETCHED.clear()
NOW["t"] = at("2026-11-26", 16, 0)
r = dotbien.scan(fresh, NOW["t"], budget=0.0)           # no time to download
t.ok("no history and no budget: the progress, a background fill started",
     "đang tải lịch sử khối lượng toàn thị trường (0/21 phiên)" in r["error"] and r["progress"] == (0, 21), r)
t.ok("...the text tells the owner to ask again", "hỏi lại /dotbien sau ít phút" in dotbien.text(r))
import time  # noqa: E402

end = time.monotonic() + 20
while dotbien.history(fresh).progress("2026-11-26")[0] < 130 and time.monotonic() < end:
    time.sleep(0.1)
t.ok("...the background thread filled the whole history", dotbien.history(fresh).progress("2026-11-26") == (130, 130))
r = dotbien.scan(fresh, NOW["t"])
t.ok("...and the list answers", [x["symbol"] for x in r["rows"]] == ["AAA", "CCC"])
t.ok("topup_due: once a day from 15:30 on a session day", dotbien.topup_due(fresh, at("2026-11-26", 15, 29)) is None
     and dotbien.topup_due(fresh, at("2026-11-26", 15, 31)) == "2026-11-26"
     and dotbien.topup_due(fresh, at("2026-11-28", 16, 0)) is None)
(fresh / "last_market_history.txt").write_text("2026-11-26", encoding="utf-8")
t.ok("...not again the same day", dotbien.topup_due(fresh, at("2026-11-26", 16, 0)) is None)


# ═══════════════ 3. /camap: ticks into orders ═══════════════
TICKS = [
    {"time": "09:15:01.021", "price": 45_600, "shares": 200, "side": 0},
    {"time": "09:20:00.100", "price": 45_500, "shares": 10_000, "side": 1},     # 455 triệu: sói, buyer
    {"time": "09:20:00.100", "price": 45_500, "shares": 15_000, "side": 1},     # the same order: 1,1375 tỷ -> cá mập
    {"time": "09:20:00.100", "price": 45_400, "shares": 100, "side": 1},        # another price: another order
    {"time": "10:00:00.000", "price": 45_000, "shares": 1_000, "side": 2},      # 45 triệu: cừu, seller
    {"time": "10:00:00.000", "price": 45_000, "shares": 1_000, "side": 1},      # same ms, other side: another order
    {"time": "14:00:00.000", "price": 44_600, "shares": 50_000, "side": 2},     # 2,23 tỷ: cá mập, seller
]
orders = camap.orders(TICKS)
t.ok("orders: the same ms/side/price merged, the rest apart", len(orders) == 6 and orders[1]["shares"] == 25_000
     and orders[1]["value"] == 25_000 * 45_500, orders)
s = camap.summarize(TICKS)
t.ok("classes: cá mập buy 1,1375 tỷ / sell 2,23 tỷ; sói none; cừu buy 2 small, sell 1",
     s["classes"]["shark"]["buy"] == 25_000 * 45_500 and s["classes"]["shark"]["sell"] == 50_000 * 44_600
     and s["classes"]["wolf"]["n_buy"] + s["classes"]["wolf"]["n_sell"] == 0
     and s["classes"]["sheep"]["n_buy"] == 2 and s["classes"]["sheep"]["n_sell"] == 1 and s["auction"] == 200 * 45_600, s["classes"])
icon, say = camap.verdict(s, -2.0)
t.ok("verdict: big money sold net into a fall: confirms the fall", icon == "🔴" and "BÁN ròng chủ động" in say
     and "xác nhận nhịp giảm" in say, say)
icon, say = camap.verdict(s, +1.0)
t.ok("...the same flow against a rise: said against the price", icon == "🔴" and "NGƯỢC với giá" in say, say)
balanced = copy.deepcopy(s)
balanced["big"] = {"buy": 1e9, "sell": 1.1e9, "net": -1e8}
t.ok("balanced big money: 🟡", camap.verdict(balanced, None)[0] == "🟡")
few = copy.deepcopy(s)
few["big"] = {"buy": 1e7, "sell": 1e7, "net": 0}
few["total"] = 1e10
t.ok("big orders under 5% of the day: no reading", camap.verdict(few, None)[0] == "⚪" and "đứng ngoài" in camap.verdict(few, None)[1])
t.ok("no big order at all: no shark", camap.verdict({"big": {"buy": 0, "sell": 0, "net": 0}, "total": 1e9}, None)[0] == "⚪")
text = camap.text("FPT", "2026-11-26", s, -2.0)
t.ok("the text: the head, a line a class, the split, the verdict, the biggest orders",
     text.splitlines()[0] == "🦈 DÒNG TIỀN CÁ MẬP: FPT · phiên 26/11" and "• Cá mập (≥ 1 tỷ/lệnh): mua 1,1 tỷ · bán 2,2 tỷ → ròng −1,1 tỷ (2 lệnh)" in text
     and "• Sói (200 triệu – 1 tỷ): mua 0 · bán 0 → ròng 0 (0 lệnh)" in text
     and "Mua chủ động 34% · bán chủ động 66%" in text and "Lệnh lớn nhất: 14:00:00 bán 2,2 tỷ @44.600; 09:20:00 mua 1,1 tỷ @45.500; 10:00:00 bán 45 triệu" in text, text)
t.ok("no ticks: said so", camap.lines(camap.summarize([]))[0].startswith("Chưa có lệnh khớp nào"))

# ═══════════════ 4. /soi FPT ═══════════════
FX = json.loads((_common.FIXTURES / "dinhgia_FPT_2026-10-30.json").read_text(encoding="utf-8"))
# the fixture's statements carry isa22 (the parent's profit) and bsa78/bsa210; cash is added under a label
for row in FX["balance_sheet"]:
    row["bsa2"] = 1.0e13 if row["lengthReport"] == 4 else 8.0e12
FX["annual_balance"] = [{"yearReport": y, "lengthReport": 5, "publicDate": f"{y + 1}-03-15T00:00:00", "bsa2": 10_522.1e9 + (y - 2025) * 1e12}
                        for y in (2023, 2024, 2025)]


def fake_statement(symbol, section):
    if symbol != "FPT":
        raise NoMarketDataError(symbol, symbol, "not in the fixtures")
    if section == "INCOME_STATEMENT":
        return {"quarters": [dict(r) for r in FX["income_statement"]], "years": [dict(r) for r in FX["annual_statements"]]}
    if section == "BALANCE_SHEET":
        return {"quarters": [dict(r) for r in FX["balance_sheet"]], "years": [dict(r) for r in FX["annual_balance"]]}
    return {"quarters": [], "years": []}


def fake_labels(symbol):
    return {"BALANCE_SHEET": [{"field": "bsa1", "titleVi": "TÀI SẢN NGẮN HẠN", "level": 1},
                              {"field": "bsa2", "titleVi": "Tiền và tương đương tiền", "level": 2},
                              {"field": "bsa78", "titleVi": "VỐN CHỦ SỞ HỮU", "level": 1}],
            "CASH_FLOW": [{"field": "cfa1", "titleVi": "Lưu chuyển tiền thuần từ hoạt động kinh doanh", "level": 1}]}


vci.fetch_statement = fake_statement
vci.fetch_metric_labels = fake_labels
vci.fetch_ratios = lambda symbol: [dict(r) for r in FX["ratios"] + FX["annual_ratios"]]
vci.fetch_listing = lambda: [{"symbol": "FPT", "type": "STOCK", "board": "HSX", "organName": "FPT", "icbCode2": "9500"}]
fund = soi.fundamentals("FPT")
# the fixture carries isa22 (the parent's profit, what the valuation uses); live VCI has isa20 too, read first
Q = sorted(FX["income_statement"], key=lambda r: (r["yearReport"], r["lengthReport"]), reverse=True)
PROFIT = [r["isa22"] for r in Q]
Y = sorted(FX["annual_statements"], key=lambda r: r["yearReport"], reverse=True)
positive = sum(1 for i in range(5) if PROFIT[i] > PROFIT[i + 4])
t.ok("SEPA from the fixture (to 30/10/2026, the parent's profit): Q3/2026 against Q3/2025 and Q2/2026, the quarters up, the year",
     round(fund["yoy"], 2) == round((PROFIT[0] - PROFIT[4]) / abs(PROFIT[4]) * 100, 2)
     and round(fund["qoq"], 2) == round((PROFIT[0] - PROFIT[1]) / abs(PROFIT[1]) * 100, 2) and fund["positive"] == positive
     and round(fund["annual"], 2) == round((Y[0]["isa22"] - Y[1]["isa22"]) / abs(Y[1]["isa22"]) * 100, 2)
     and fund["yoy_label"] == "Q3/2026 so với Q3/2025"
     and fund["qoq_label"] == "Q3/2026 so với Q2/2026" and fund["annual_label"] == "năm 2025 so với 2024", fund)
t.ok("...the cash by VCI's label (not the cash-flow line), the newest year", fund["cash"] == 10_522.1e9 and fund["cash_year"] == 2025, fund)
t.ok("the cash field: by label, banks by 'tiền mặt', none without a label",
     soi._cash_field("FPT") == "bsa2")
vci.fetch_metric_labels = lambda symbol: {"BALANCE_SHEET": [{"field": "bsb1", "titleVi": "Tiền mặt, vàng bạc, đá quý"}]}
t.ok("...a bank's", soi._cash_field("CTG") == "bsb1")
vci.fetch_metric_labels = lambda symbol: {}
t.ok("...no label: None", soi._cash_field("FPT") is None)
vci.fetch_metric_labels = fake_labels

# prices: VNDirect's sessions, DNSE's bars, the foreign flow, the valuation's history
SESSIONS = ms.sessions("2026-11-26", 30)
STOCK_DAYS = {"FPT": pd.DataFrame([{
    "Date": d, "Exchange": "HOSE", "Ref": 46_000.0, "Ceiling": 49_200.0, "Floor": 42_800.0, "Open": 46_000.0,
    "High": 46_500.0, "Low": 45_500.0, "Close": 46_000.0, "Volume": 10_000_000.0 + 100_000 * i,
    "Value": 4.6e11, "PutThroughVolume": 0.0} for i, d in enumerate(SESSIONS[:-1])])}
STOCK_DAYS["FPT"].loc[len(STOCK_DAYS["FPT"])] = ["2026-11-26", "HOSE", 46_050.0, 49_250.0, 42_850.0, 45_600.0, 46_450.0, 44_500.0,
                                                 44_500.0, 13_993_100.0, 6.374011e11, 57_000.0]
FOREIGN = pd.DataFrame([{"Date": "2026-11-25", "ForeignBuyVol": 2_371_397.0, "ForeignSellVol": 2_354_940.0, "ForeignNetVol": 16_457.0,
                         "ForeignBuyVal": 1.1e11, "ForeignSellVal": 1.1e11, "ForeignRoom": 4.5e8},
                        {"Date": "2026-11-26", "ForeignBuyVol": 3_041_259.0, "ForeignSellVol": 2_724_576.0, "ForeignNetVol": 316_683.0,
                         "ForeignBuyVal": 1.39e11, "ForeignSellVal": 1.23e11, "ForeignRoom": 4.6e8}])
DOWN = set()


def fake_stock_days(code, today):
    start = (datetime.strptime(today, "%Y-%m-%d") - pd.Timedelta(days=60)).strftime("%Y-%m-%d")
    end = today
    if "days" in DOWN:
        raise VendorUnavailableError("VNDirect request failed")
    f = STOCK_DAYS.get(code)
    if f is None:
        return pd.DataFrame(columns=flows.STOCK_DAY_COLUMNS)
    return f[(f["Date"] >= start) & (f["Date"] <= end)].reset_index(drop=True)


def fake_foreign(code, size=30):
    if "foreign" in DOWN:
        raise VendorUnavailableError("VNDirect request failed")
    return FOREIGN.copy()


BAR_DAYS = ms.sessions("2026-11-26", 260)
closes_list = [63_000 - 70 * i for i in range(260)]                  # a long fall: under every average
BARS = pd.DataFrame({"Date": pd.to_datetime(BAR_DAYS), "Open": closes_list, "High": [c + 400 for c in closes_list],
                     "Low": [c - 400 for c in closes_list], "Close": closes_list, "Volume": [1e7] * 260})
BARS.loc[259, ["Close", "High", "Low"]] = [44_500.0, 46_450.0, 44_500.0]


def fake_bars(code, as_of):
    if code != "FPT":
        raise NoMarketDataError(code, code, "No market data: unknown")
    frame = BARS[BARS["Date"] <= pd.Timestamp(as_of)]
    if NOW["t"].strftime("%Y-%m-%d") == as_of and NOW["t"].strftime("%H:%M") < "15:05":   # the forming bar dropped
        frame = frame[frame["Date"] < pd.Timestamp(as_of)]
    return frame.reset_index(drop=True)


def fake_pb(code):
    return {"shares": 1_885_727_648.0, "equity": 42.8e12, "period": "Q3/2026", "published": None}


TICK_DAYS = {"2026-11-26": TICKS}
soi._stock_days = fake_stock_days
soi._foreign = fake_foreign
soi._bars = fake_bars
soi._pb_basis = fake_pb
camap._ticks = lambda symbol, day: TICK_DAYS.get(day, []) if symbol == "FPT" else []
quote._bars = lambda code, start, end: pd.DataFrame(
    [("2026-11-25", 46_000, 46_500, 45_500, 46_050, 9_304_700), ("2026-11-26", 45_600, 46_450, 44_500, 44_500, 13_993_100)]
    if code == "FPT" else [], columns=["Date", "Open", "High", "Low", "Close", "Volume"])
quote._tick = lambda code, day: 44_650 if (code, day) == ("FPT", "2026-11-26") else None

NOW["t"] = at("2026-11-26", 16, 0)
card = soi.build("FPT", DATA, NOW["t"])
s = card["session"]
t.ok("after the close: VNDirect's posted row - price, reference, ceiling, floor, volume, value",
     (s["day"], s["live"], s["price"], s["ref"], s["ceiling"], s["floor"], s["volume"]) ==
     ("2026-11-26", False, 44_500.0, 46_050.0, 49_250.0, 42_850.0, 13_993_100.0) and s["source"] == "VNDirect", s)
t.ok("...the change against the reference", round(s["change_pct"], 2) == -3.37 and s["change"] == -1_550.0, s)
avg = sum(10_000_000 + 100_000 * i for i in range(9, 29)) / 20
t.ok("...the averages over the 20 sessions BEFORE the session (not the session itself)",
     s["avg_volume"] == avg and s["avg_sessions"] == 20 and round(s["volume_ratio"], 4) == round(13_993_100 / avg, 4), (s["avg_volume"], avg))
text = soi.text(card)
lines = text.splitlines()
t.ok("the card: the head, the price line, the reference line", lines[0] == "📊 PHÂN TÍCH NHANH: FPT · phiên 26/11"
     and lines[2] == "🔴 Giá khớp: 44.500 (-3,37% | -1.550)" and lines[3] == "• Tham chiếu: 46.050 | Trần: 49.250 | Sàn: 42.850", lines[:4])
t.ok("...the volume against MA20", lines[4] == f"• Khối lượng khớp: 13.993.100 CP ({quote._vn(13_993_100 / avg * 100, 1)}% so với MA20)", lines[4])
t.ok("...the foreign line in shares, the session's row", lines[5] == "• Khối ngoại: Mua 3.041.259 | Bán 2.724.576 (Ròng: +316.683 CP)", lines[5])
t.ok("...the averages", lines[6] == "• Giá trị trung bình 20 phiên: 460,0 tỷ" and lines[7] == f"• Khối lượng trung bình 20 phiên: {quote._vn(avg)} CP", lines[6:8])
t.ok("...P/B at the price shown", lines[8] == "• Định giá P/B hiện tại: 1,96 (sổ sách Q3/2026, giá 44.500)", lines[8])
t.ok("...the RS score from the market history", lines[9].startswith("• Điểm RS: ") and f"{scores['FPT']} (xếp hạng" in lines[9], lines[9])
t.ok("SEPA: the four growth lines with their marks, the cash line",
     f"• Lợi nhuận sau thuế quý hiện tại so với quý cùng kỳ năm trước: {soi._pct(fund['yoy'])} (Q3/2026 so với Q3/2025) ✅" in text
     and f"• Lợi nhuận sau thuế quý hiện tại so với quý trước: {soi._pct(fund['qoq'])} (Q3/2026 so với Q2/2026) ✅" in text
     and "• Tiền và tương đương tiền cuối kỳ năm gần nhất: 10.522,1 tỷ đồng (năm 2025)" in text
     and f"• Lợi nhuận sau thuế năm gần nhất so với năm trước đó: {soi._pct(fund['annual'])} (năm 2025 so với 2024) ✅" in text
     and f"5 quý gần nhất so với cùng kỳ: Dương ({positive}/5 quý dương, cần từ 3/5) ✅" in text, text)
# a quarter down on the year and only 2 of 5 quarters up: the marks turn
t.ok("the marks: a fall on the year ❌, 2 of 5 quarters ❌",
     "cùng kỳ năm trước: -5,94% (Q3/2026 so với Q3/2025) ❌" in "\n".join(soi._sepa_lines(
         {**fund, "yoy": -5.94, "positive": 2}, card["tech"], 44_500.0)[0])
     and "Chưa ổn định (2/5 quý dương, cần từ 3/5) ❌" in "\n".join(soi._sepa_lines({**fund, "positive": 2}, card["tech"], 44_500.0)[0]))
tech = card["tech"]
t.ok("the averages from DNSE's bars with the session's close last", tech["ma50"] == sum(closes_list[-50:-1] + [44_500]) / 50
     and tech["ma200"] is not None and tech["ma20"] is not None)
t.ok("...the three MA lines fail, CANSLIM fails",
     f"  - Giá ≥ MA50: ❌ Không đạt (Giá: 44.500 | MA50: {quote._vn(tech['ma50'])})" in text
     and "  - Giá ≥ MA200: ❌ Không đạt" in text and "Bổ sung: Tiêu chí CANSLIM: ❌ Không Đạt (đạt khi thoả mãn tất cả tiêu chí bên trên)" in text, text)
t.ok("the shark block in the card, with the day's direction", "🦈 Dòng tiền cá mập phiên 26/11" in text
     and "🔴 Dòng tiền lớn BÁN ròng chủ động" in text and "xác nhận nhịp giảm của phiên" in text, text)
t.ok("the reading: under MA20 and MA50, RSI oversold, the range, the shark sentence; the holder and buyer advice of a downtrend",
     "Nhận định nhanh: FPT đang ở mức 44.500, giảm 3,37%, nằm dưới MA20 tại" in text and "đã thủng hỗ trợ MA50 quanh" in text
     and "ở vùng quá bán" in text and "Biên độ 5 phiên gần nhất dao động" in text and "Dòng tiền cá mập: Dòng tiền lớn BÁN" in text
     and "Nếu đang giữ, do giá đã đánh mất mốc hỗ trợ MA50" in text and "Mua mới thì nên kiên nhẫn đứng ngoài quan sát" in text
     and "(RSI quá bán chỉ nói giá đã rơi nhanh, không nói đáy)" in text, text)
t.ok("...no AI, the closing line names /nhanh", lines[-1] == "Tổng hợp từ số liệu, không phải khuyến nghị. Quyết định của hệ thống định lượng: /nhanh FPT")

# in the session: DNSE's tick, the reference is the previous close, the band's ceiling and floor
NOW["t"] = at("2026-11-26", 10, 30)
STOCK_DAYS["FPT"] = STOCK_DAYS["FPT"].iloc[:-1]            # VNDirect has not posted the session
card = soi.build("FPT", DATA, NOW["t"])
s = card["session"]
t.ok("in the session: the tick, the previous close as the reference, the ceiling/floor from HOSE's band, the forming bar's volume",
     (s["day"], s["live"], s["price"], s["ref"], s["ceiling"], s["floor"], s["volume"], s["source"]) ==
     ("2026-11-26", True, 44_650.0, 46_050.0, 49_250.0, 42_850.0, 13_993_100, "DNSE"), s)
text = soi.text(card)
t.ok("...the head says realtime, the volume 'tới lúc này'", text.splitlines()[0] == "📊 PHÂN TÍCH NHANH: FPT · realtime lúc 10:30 26/11"
     and "CP (" in text.splitlines()[4] and text.splitlines()[4].endswith("so với MA20) · tới lúc này"), text.splitlines()[:5])
t.ok("...the technicals close the session with the tick", card["tech"]["ma50"] == sum(closes_list[-50:-1] + [44_650]) / 50)
STOCK_DAYS["FPT"].loc[len(STOCK_DAYS["FPT"])] = ["2026-11-26", "HOSE", 46_050.0, 49_250.0, 42_850.0, 45_600.0, 46_450.0, 44_500.0,
                                                 44_500.0, 13_993_100.0, 6.374011e11, 57_000.0]

# before the open: the last session, said so
NOW["t"] = at("2026-11-27", 8, 0)
card = soi.build("FPT", DATA, NOW["t"])
t.ok("before 9:00 the next day: the last posted session, 'phiên gần nhất'", card["session"]["day"] == "2026-11-26"
     and not card["session"]["live"] and soi.text(card).splitlines()[0] == "📊 PHÂN TÍCH NHANH: FPT · phiên 26/11 (phiên gần nhất)")

# sources down, one at a time
NOW["t"] = at("2026-11-26", 16, 0)
DOWN.add("foreign")
text = soi.text(soi.build("FPT", DATA, NOW["t"]))
t.ok("VNDirect's foreign flow down: its line says so, the rest intact", "• Khối ngoại: chưa lấy được (VNDirect)" in text and "🔴 Giá khớp: 44.500" in text)
DOWN.clear()
DOWN.add("days")
card = soi.build("FPT", DATA, NOW["t"])
text = soi.text(card)
t.ok("VNDirect's sessions down after the close: the price from DNSE (the last tick), no averages, said so",
     card["session"]["price"] == 44_650 and card["session"]["source"] == "DNSE" and "• Trung bình 20 phiên: chưa lấy được (VNDirect)" in text, text)
DOWN.clear()
soi._pb_basis = lambda code: None
t.ok("no book: the P/B line says so", "• Định giá P/B hiện tại: chưa tính được" in soi.text(soi.build("FPT", DATA, NOW["t"])))
soi._pb_basis = fake_pb
vci.fetch_statement = lambda symbol, section: (_ for _ in ()).throw(VendorUnavailableError("VCI down"))
text = soi.text(soi.build("FPT", DATA, NOW["t"]))
t.ok("VCI down: the statements line says so, CANSLIM 'chưa đủ số'", "• Báo cáo tài chính: chưa lấy được từ VCI" in text
     and "Tiêu chí CANSLIM: ⚪ chưa đủ số" in text, text)
vci.fetch_statement = fake_statement
t.ok("an unknown code: said so", soi.command("XYZ", DATA, NOW["t"]) == "❔ XYZ: không có mã này trên HOSE/HNX/UPCoM.")
t.ok("no code, two codes: refused", _raises(lambda: soi.command("", DATA)) and _raises(lambda: soi.command("FPT HPG", DATA)))
t.ok("an index: refused", _raises(lambda: soi.command("VNINDEX", DATA)))

# the reading's other states
base = {"price": 100.0, "change_pct": 1.0, "change": 1.0, "volume": 1e6, "volume_ratio": 1.0, "live": False, "day": "2026-11-26"}
up = {"ma20": 95.0, "ma50": 90.0, "rsi": 55.0, "macd": None, "bb": None, "range_lo": 94.0, "range_hi": 101.0}
text = "\n".join(soi.narrative("ABC", base, up, None))
t.ok("uptrend: above both averages, hold with the stop under MA20, buy only on a pullback",
     "nằm trên MA20 tại 95 và MA50 tại 90" in text and "dời điểm dừng lỗ lên dưới MA20 (95)" in text and "không mua đuổi" in text, text)
pull = {**up, "ma20": 105.0, "ma50": 90.0}
text = "\n".join(soi.narrative("ABC", base, pull, None))
t.ok("a pullback holding MA50", "nhưng còn giữ trên MA50 quanh 90" in text and "điểm dừng lỗ dưới MA50" in text, text)
recover = {**up, "ma20": 95.0, "ma50": 110.0}
text = "\n".join(soi.narrative("ABC", base, recover, None))
t.ok("a recovery over MA20 under MA50", "đã lấy lại MA20 tại 95 nhưng còn dưới MA50 quanh 110" in text and "mốc cần vượt để xác nhận đảo chiều là MA50" in text, text)
text = "\n".join(soi.narrative("ABC", {**base, "volume_ratio": 2.1}, {**up, "rsi": 75.0}, None))
t.ok("volume spike and RSI overbought read", "đột biến, có dòng tiền vào/ra mạnh" in text and "ở vùng quá mua" in text and "RSI quá mua nên cân nhắc chốt một phần" in text, text)
text = "\n".join(soi.narrative("ABC", base, {**up, "ma20": None}, None))
t.ok("too short a history: said so", text.startswith("Nhận định nhanh: ABC chưa đủ lịch sử giá"))

# ═══════════════ 5. Telegram and Zoom ═══════════════
SENT = []
bot = tb.TelegramBot(wh.SETTINGS, wh.JobQueue(wh.SETTINGS), wh.Watchlist(wh.SETTINGS))
bot._call = lambda method, http_timeout=30, **params: SENT.append((method, params)) or {}


def sent(chat):
    return [p["text"] for m, p in SENT if m == "sendMessage" and p["chat_id"] == chat]


def msg(user, chat, text):
    return {"chat": {"id": chat}, "from": {"id": user}, "text": text}


bot._handle(msg(111, 111, "/dotbien"))
t.ok("Telegram /dotbien: the list", sent(111)[-1].startswith("🚀 DANH SÁCH ĐỘT BIẾN KHỐI LƯỢNG (26/11/2026)") and "AAA, CCC" in sent(111)[-1], sent(111)[-1][:120])
SENT.clear()
bot._handle(msg(111, 111, "/soi fpt"))
t.ok("Telegram /soi fpt: the progress line, then the card", sent(111)[0].startswith("⏳ Đang soi FPT")
     and sent(111)[1].startswith("📊 PHÂN TÍCH NHANH: FPT"), [x[:60] for x in sent(111)])
SENT.clear()
bot._handle(msg(111, 111, "/ptn FPT"))
t.ok("/ptn and /phantichnhanh: the same", sent(111)[-1].startswith("📊 PHÂN TÍCH NHANH: FPT"))
SENT.clear()
bot._handle(msg(111, 111, "/camap FPT"))
t.ok("Telegram /camap FPT: the flow of the session", sent(111)[-1].startswith("🦈 DÒNG TIỀN CÁ MẬP: FPT · phiên 26/11")
     and "Lệnh lớn nhất:" in sent(111)[-1], sent(111)[-1][:100])
SENT.clear()
bot._handle(msg(111, 111, "/camap"))
t.ok("/camap without a code: its usage", sent(111)[-1] == "⚠️ Nhập mã cổ phiếu, ví dụ: /camap FPT", sent(111))
SENT.clear()
bot._handle(msg(222, 222, "/soi FPT"))
t.ok("a stranger: refused", sent(222)[-1].startswith("Bot chưa cho phép"))
help_text = bot._help()
t.ok("the help lists the three, still one message", "/soi FPT - soi nhanh một mã" in help_text
     and "/dotbien - đột biến khối lượng toàn thị trường" in help_text and "/camap FPT - dòng tiền cá mập" in help_text
     and len(help_text) <= tb.MAX_MESSAGE, len(help_text))
from vnbot import pages  # noqa: E402

t.ok("/docs lists them", all(f"<code>{c}</code>" in pages.docs_body() for c in ("soi FPT", "dotbien · dotbien 200", "camap FPT")))

# Zoom: the same handler (the harness's app)
app, zoom, handled, _, _, sw = wh.build()
zoom.handler = bot.zoom_command
bot.zoom = zoom
zoom.pair("a@xmpp.zoom.us", "A")
from fastapi.testclient import TestClient  # noqa: E402

zc = TestClient(app, raise_server_exceptions=False)
body = json.dumps({"event": "bot_notification", "payload": {
    "userJid": "a@xmpp.zoom.us", "cmd": "dotbien", "userName": "A", "accountId": "acc", "toJid": "bot@xmpp.zoom.us",
    "messageId": "m1"}}).encode()
zc.post("/webhook/zoom", content=body, headers=wh.sign("zsecret", body))
end = time.monotonic() + 10
while zoom.queue.empty() and time.monotonic() < end:
    time.sleep(0.02)
got = zoom.queue.get_nowait()[1] if not zoom.queue.empty() else ""
t.ok("Zoom 'dotbien': the list back to Zoom", got.startswith("🚀 DANH SÁCH ĐỘT BIẾN KHỐI LƯỢNG"), got[:80])

# the scheduler's daily top-up
from vnbot.service import DailyScheduler  # noqa: E402

NOW["t"] = at("2026-11-26", 15, 40)
sched = DailyScheduler(wh.SETTINGS, wh.JobQueue(wh.SETTINGS), wh.Watchlist(wh.SETTINGS))
FETCHED.clear()
sched._market_history()
stamp = DATA / "last_market_history.txt"
t.ok("the scheduler tops the history up after 15:30 and stamps the day", stamp.read_text(encoding="utf-8") == "2026-11-26")
sched._market_history()
t.ok("...once", FETCHED == [] or len(FETCHED) <= 1, FETCHED)

t.done()
