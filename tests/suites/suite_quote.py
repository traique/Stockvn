"""Gõ mã là có giá (vnbot/quote.py), offline: DNSE faked (nến ngày và tick khớp gần nhất).

- which messages are a price lookup: "FPT", "fpt hpg", "giá FPT?", "/FPT", "/fpt@Bot", VNINDEX/VNI/VN-Index,
  VN30, HNX, UPCOM, commas; anything with another word ("FPT có nên mua", "hello", "help") is not;
- the reply: realtime only during the session (tick of today, its phase named), the last close outside
  (weekend, after 15:00, before 9:00, a holiday weekday), the session's date when it is not today;
  change and % against the previous close, open/high/low/volume, Vietnamese numbers; an index with
  decimals and no tick asked; a match today before the chart has today's bar; a new listing (no
  previous close); an unknown code said so with the coin hint; DNSE down said so; at most 5 codes;
- Telegram and Zoom: a message of codes gets the prices, anything else the command list as before,
  "/alt ..." with no bridge never read as codes, a stranger gets nothing.
"""
import time

import _common  # noqa: F401 - clean env, no .env, offline, temp folder, repo on sys.path

_common.need("httpx")
import os  # noqa: E402

os.environ.update({"TELEGRAM_TOKEN": "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawQ", "TELEGRAM_ALLOWED_IDS": "111"})
from datetime import datetime  # noqa: E402

import pandas as pd  # noqa: E402

import _web_harness as wh  # noqa: E402
from tradingagents.dataflows.errors import NoMarketDataError, VendorUnavailableError  # noqa: E402
from tradingagents.market import vn_rules  # noqa: E402
from vnbot import quote  # noqa: E402
from vnbot import telegram_bot as tb  # noqa: E402

t = _common.Checks()


def at(day, hh, mm):
    return datetime(int(day[:4]), int(day[5:7]), int(day[8:10]), hh, mm, tzinfo=vn_rules.VN_TZ)


# ── DNSE faked: BARS[code] = [(date, open, high, low, close, volume)], TICKS[(code, day)] = price ──
BARS = {
    "FPT": [("2026-11-18", 47000, 47500, 46800, 46950, 16204800), ("2026-11-19", 46950, 47950, 45600, 45700, 18437700),
            ("2026-11-20", 45600, 46000, 44650, 45000, 15670800)],
    "HPG": [("2026-11-19", 20400, 20600, 20300, 20450, 9000000), ("2026-11-20", 20300, 20500, 20150, 20450, 15650000)],
    "VNINDEX": [("2026-11-19", 1700.0, 1702.16, 1683.43, 1689.62, 642165100),
                ("2026-11-20", 1671.25, 1688.79, 1666.41, 1688.79, 719800000)],
    "NEW": [("2026-11-20", 10000, 11500, 10000, 11000, 120000)],
}
TICKS = {}
CALLS = []
DOWN = set()


def fake_bars(code, start, end):
    CALLS.append(("bars", code, start, end))
    if code in DOWN:
        raise VendorUnavailableError("DNSE request failed: timeout")
    rows = [r for r in BARS.get(code, []) if start <= r[0] <= end]
    if code not in BARS:
        raise NoMarketDataError(code, code, "DNSE does not know this symbol")
    if not rows:
        raise NoMarketDataError(code, code, "no bars")
    return pd.DataFrame(rows, columns=["Date", "Open", "High", "Low", "Close", "Volume"])


def fake_tick(code, day):
    CALLS.append(("tick", code, day))
    return TICKS.get((code, day))


quote._bars, quote._tick = fake_bars, fake_tick
STOCK_DAYS = {}                  # VNDirect's sessions as traded: code -> rows (Date, Close, Ref, High, Low, Volume)
VND_DOWN = set()


def fake_stock_days(code, start, end):
    CALLS.append(("vnd", code, start, end))
    if code in VND_DOWN:
        raise VendorUnavailableError("VNDirect request failed: timeout")
    rows = [r for r in STOCK_DAYS.get(code, []) if start <= r["Date"] <= end]
    return pd.DataFrame(rows, columns=["Date", "Exchange", "Ref", "Ceiling", "Floor", "Open", "High", "Low", "Close",
                                       "Volume", "Value", "PutThroughVolume"])


quote._stock_days = fake_stock_days

# ── 1. which messages are a price lookup ──
cases = {"FPT": ["FPT"], "fpt hpg": ["FPT", "HPG"], "Giá FPT?": ["FPT"], "gia fpt": ["FPT"], "/FPT": ["FPT"],
         "/fpt@VnBot": ["FPT"], "fpt, hpg, vcb": ["FPT", "HPG", "VCB"], "FPT FPT": ["FPT"], "VNINDEX": ["VNINDEX"],
         "vn-index": ["VNINDEX"], "VNI": ["VNINDEX"], "vn30 hnx": ["VN30", "HNX"], "upcom": ["UPCOM"]}
for text, want in cases.items():
    t.ok(f"{text!r} is a price lookup: {want}", quote.stock_codes(text) == want, quote.stock_codes(text))
for text in ("FPT có nên mua", "hello", "help", "ok", "", "/", "giá", "giá vàng hôm nay", "E1VFVN30", "alt status",
             "7d", "14D", "FPT có nên mua 7d"):
    t.ok(f"{text!r} is not", quote.stock_codes(text) is None, quote.stock_codes(text))

# ── 2. during the session: the tick ──
TICKS[("FPT", "2026-11-20")] = 45300
CALLS.clear()
r = quote.stocks_reply("FPT", now=at("2026-11-20", 10, 32))
t.ok("in the session: 'realtime' with the time and the phase", r.splitlines()[0] == "💹 Giá realtime lúc 10:32 20/11 (đang khớp lệnh)", r)
t.ok("...the tick, against yesterday's close, Vietnamese numbers; open/high/low/volume of the session",
     r.splitlines()[1] == "🔴 FPT: 45.300 -400 (-0,88%)"
     and r.splitlines()[2] == "   TC 45.700 · Mở 45.600 · Cao 46.000 · Thấp 44.650 · KL 15,67 triệu", r)
t.ok("...bars of the last 20 days and today's tick asked", ("bars", "FPT", "2026-10-31", "2026-11-20") in CALLS
     and ("tick", "FPT", "2026-11-20") in CALLS, CALLS)
for hh, mm, phase in ((9, 5, "phiên ATO"), (12, 0, "nghỉ trưa"), (14, 35, "phiên ATC"), (14, 50, "sau ATC, giao dịch thoả thuận")):
    head = quote.stocks_reply("FPT", now=at("2026-11-20", hh, mm)).splitlines()[0]
    t.ok(f"{hh}:{mm:02d}: {phase}", head.endswith(f"({phase})"), head)
del TICKS[("FPT", "2026-11-20")]
r = quote.stocks_reply("HPG", now=at("2026-11-20", 10, 0))
t.ok("no tick yet: the close of today's bar; unchanged is 🟡 0 (0,00%)", r.splitlines()[1] == "🟡 HPG: 20.450 0 (0,00%)", r)

# a match today, the chart not yet: price = tick, reference = the last close, no session detail
TICKS[("FPT", "2026-11-23")] = 45900
r = quote.stocks_reply("fpt", now=at("2026-11-23", 9, 20))
t.ok("a match today before the chart's bar: the tick against the last close, the reference only",
     r.splitlines()[1:] == ["🟢 FPT: 45.900 +900 (+2,00%)", "   TC 45.000"], r)
del TICKS[("FPT", "2026-11-23")]

# ── 3. outside the session: the last close, its date ──
for label, now in (("weekend", at("2026-11-21", 20, 15)), ("before 9:00", at("2026-11-23", 8, 15)),
                   ("holiday weekday 10:00 (24/11)", at("2026-11-24", 10, 0))):
    CALLS.clear()
    r = quote.stocks_reply("FPT", now=now)
    t.ok(f"{label}: no 'realtime', the last close with its session's date",
         "ngoài giờ giao dịch: giá đóng cửa phiên gần nhất" in r.splitlines()[0]
         and r.splitlines()[1] == "🔴 FPT: 45.000 -700 (-1,53%) · phiên 20/11", r)
    if label != "holiday weekday 10:00 (24/11)":
        t.ok(f"{label}: no tick asked", not [c for c in CALLS if c[0] == "tick"], CALLS)
r = quote.stocks_reply("FPT", now=at("2026-11-20", 16, 0))
t.ok("after 15:00 the same day: the close, no date (it is today's)",
     "ngoài giờ" in r.splitlines()[0] and r.splitlines()[1] == "🔴 FPT: 45.000 -700 (-1,53%)", r)

# ── 4. an index, a new listing, unknown, DNSE down, too many ──
CALLS.clear()
r = quote.stocks_reply("vnindex", now=at("2026-11-20", 10, 32))
t.ok("an index: points with decimals, no tick asked", r.splitlines()[1] == "🔴 VNINDEX: 1.688,79 điểm -0,83 (-0,05%)"
     and r.splitlines()[2].startswith("   TC 1.689,62 · Mở 1.671,25") and not [c for c in CALLS if c[0] == "tick"], r)
r = quote.stocks_reply("NEW", now=at("2026-11-20", 16, 0))
t.ok("a first session (no previous close): the price, no change", r.splitlines()[1] == "⚪ NEW: 11.000"
     and r.splitlines()[2] == "   Mở 10.000 · Cao 11.500 · Thấp 10.000 · KL 120.000", r)
r = quote.stocks_reply("XYZ FPT", now=at("2026-11-20", 16, 0))
t.ok("an unknown code: said so, the others answered, the coin hint at the end",
     "❔ XYZ: không có mã này trên HOSE/HNX/UPCoM" in r and "🔴 FPT: 45.000" in r
     and r.splitlines()[-1] == "Giá coin: gõ alt btc. Danh sách lệnh: /trogiup.", r)
DOWN.add("FPT")
r = quote.stocks_reply("FPT HPG", now=at("2026-11-20", 16, 0))
DOWN.clear()
t.ok("DNSE down for one: said so, the other answered, no coin hint",
     "⚠️ FPT: chưa lấy được giá (nguồn giá DNSE không trả lời), thử lại sau ít phút" in r and "HPG: 20.450" in r
     and "Giá coin" not in r, r)
CALLS.clear()
r = quote.stocks_reply("FPT HPG VNINDEX NEW AAA BBB CCC", now=at("2026-11-20", 16, 0))
t.ok("more than 5 codes: the first 5, said so", r.splitlines()[-2] == "(Mỗi lần tra tối đa 5 mã.)"
     and "AAA" in r and "BBB" not in r and {c[1] for c in CALLS} == {"FPT", "HPG", "VNINDEX", "NEW", "AAA"}, r)
t.ok("not a lookup: None (the command list)", quote.stocks_reply("hello") is None)

# ── 5. Telegram and Zoom ──
FIXED = at("2026-11-20", 10, 32)
vn_rules.now_vn = lambda: FIXED
TICKS[("FPT", "2026-11-20")] = 45300
SENT = []


def fake_call(method, http_timeout=30, **params):
    SENT.append((method, params))
    return {}


bot = tb.TelegramBot(wh.SETTINGS, wh.JobQueue(wh.SETTINGS), wh.Watchlist(wh.SETTINGS))
bot._call = fake_call


def last(chat):
    found = [p["text"] for m, p in SENT if m == "sendMessage" and p["chat_id"] == chat]
    return found[-1] if found else ""


def msg(user, chat, text):
    return {"chat": {"id": chat}, "from": {"id": user}, "text": text}


bot._handle(msg(111, 111, "FPT"))
t.ok("Telegram 'FPT': the price, not the command list", last(111).startswith("💹 Giá realtime lúc 10:32 20/11")
     and "🔴 FPT: 45.300 -400 (-0,88%)" in last(111), last(111))
bot._handle(msg(111, 111, "giá fpt hpg"))
t.ok("Telegram 'giá fpt hpg': both", "FPT: 45.300" in last(111) and "HPG: 20.450" in last(111), last(111))
help_text = bot._help()
for text in ("hello", "FPT có nên mua không", "/khongcolenhnay"):
    bot._handle(msg(111, 111, text))
    t.ok(f"Telegram {text!r}: the command list as before", last(111) == help_text, last(111)[:80])
t.ok("the command list names the lookup", "FPT · FPT HPG - giá realtime · FPT 7d - 7 phiên gần nhất · /alt btc · /alt btc 7d - giá coin OKX"
     in help_text, help_text[:300])
n = len(CALLS)
for text in ("/alt btc", "alt btc", "Alt BTC", "altcoin eth"):
    bot._handle(msg(111, 111, text))
    t.ok(f"no bridge: {text!r} is never read as stock codes", last(111) == help_text and len(CALLS) == n)
bot._handle(msg(222, 222, "FPT"))
t.ok("a stranger: refused, no price", last(222).startswith("Bot chưa cho phép") and len(CALLS) == n, last(222))
bot._handle(msg(111, 111, "/nhanh"))
t.ok("a stock command still runs as before (/nhanh without a code: its own error)", last(111) != help_text
     and "FPT: 45.300" not in last(111), last(111))

app, zoom, handled, _, _, sw = wh.build()
zoom.handler = bot.zoom_command
bot.zoom = zoom
zoom.pair("a@xmpp.zoom.us", "A")
from fastapi.testclient import TestClient  # noqa: E402
import json  # noqa: E402

zc = TestClient(app, raise_server_exceptions=False)
body = json.dumps({"event": "bot_notification", "payload": {
    "userJid": "a@xmpp.zoom.us", "cmd": "fpt", "userName": "A", "accountId": "acc", "toJid": "bot@xmpp.zoom.us",
    "messageId": "m1"}}).encode()
zc.post("/webhook/zoom", content=body, headers=wh.sign("zsecret", body))
end = time.monotonic() + 10
while zoom.queue.empty() and time.monotonic() < end:
    time.sleep(0.02)
got = zoom.queue.get_nowait()[1] if not zoom.queue.empty() else ""
t.ok("Zoom 'fpt' (the '/' Zoom adds): the price back to Zoom", "🔴 FPT: 45.300 -400 (-0,88%)" in got, got)

# ── 6. several sessions: "FPT 7d" ──
periods = {"FPT 7d": (["FPT"], 7), "fpt 14D": (["FPT"], 14), "fpt hpg 7d": (["FPT", "HPG"], 7), "/fpt 7d": (["FPT"], 7),
           "giá FPT 7 ngày": (["FPT"], 7), "FPT 7ngày": (["FPT"], 7), "FPT 2w": (["FPT"], 10), "FPT 2 tuần": (["FPT"], 10),
           "VNINDEX 1m": (["VNINDEX"], 22), "7d FPT": (["FPT"], 7), "FPT 10 phiên": (["FPT"], 10), "FPT": (["FPT"], None)}
for text, want in periods.items():
    t.ok(f"{text!r}: {want}", quote.parse_lookup(text) == want, quote.parse_lookup(text))
t.ok("stock_codes still gives the codes of 'FPT 7d'", quote.stock_codes("FPT 7d") == ["FPT"])

# VNDirect's sessions as traded, with each session's reference; an ex-date on 20/11 (cash dividend 1.000đ)
DAYS7 = ["2026-11-11", "2026-11-12", "2026-11-13", "2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19", "2026-11-20"]
CLOSE7 = [50_000, 50_500, 51_000, 50_000, 49_000, 49_500, 48_500, 47_000]
REF7 = [49_800, 50_000, 50_500, 51_000, 50_000, 49_000, 49_500, 47_500]          # 20/11: 48.500 - 1.000
STOCK_DAYS["FPT"] = [{"Date": d, "Exchange": "HOSE", "Ref": float(r), "Ceiling": 0.0, "Floor": 0.0, "Open": float(c),
                      "High": float(c + 300), "Low": float(c - 300), "Close": float(c), "Volume": 1_000_000.0 * (i + 1),
                      "Value": 0.0, "PutThroughVolume": 0.0} for i, (d, c, r) in enumerate(zip(DAYS7, CLOSE7, REF7))]
CALLS.clear()
r = quote.stocks_reply("FPT 7d", now=at("2026-11-21", 20, 0))
lines = r.splitlines()
t.ok("FPT 7d on a weekend: the header, the last 7 sessions as traded, newest first",
     lines[0] == "📅 Giá 7 phiên gần nhất · lúc 20:00 21/11 (giá đóng cửa)"
     and lines[2].startswith("🔴 FPT: 47.000 ") and "sau 7 phiên (12/11 – 20/11)" in lines[2]
     and lines[5] == "   20/11: 47.000 🔴 -1,05% · KL 8,00 triệu · GDKHQ"
     and lines[6] == "   19/11: 48.500 🔴 -2,02% · KL 7,00 triệu" and lines[11] == "   12/11: 50.500 🟢 +1,00% · KL 2,00 triệu", r)
growth = 1.0
for c, ref in zip(CLOSE7[1:], REF7[1:]):
    growth *= c / ref
t.ok("...across an ex-date: the % compounded over each session's reference, no VND difference, the ex-date named",
     f"FPT: 47.000 {quote._signed((growth - 1) * 100, 2)}% sau 7 phiên" in lines[2]
     and lines[3] == "   Qua ngày GDKHQ 20/11: % cả kỳ cộng dồn theo giá tham chiếu từng phiên, không tính phần chia cổ tức/quyền", r)
t.ok("...high, low (with their days), the average volume", lines[4] == "   Cao nhất 51.300 (13/11) · Thấp nhất 46.700 (20/11) · KL TB 5,00 triệu", lines[4])
t.ok("...VNDirect asked, not DNSE, no tick outside the session", [c[0] for c in CALLS] == ["vnd"], CALLS)
t.ok("...the footer: traded prices, the other periods", "Giá khớp thực tế từng phiên (VNDirect)" in r and "Kỳ khác: FPT 14d" in r)
r = quote.stocks_reply("FPT 3d", now=at("2026-11-19", 20, 0))
t.ok("no ex-date in the window: the VND change against the first session's reference, and the %",
     "🔴 FPT: 48.500 -1.500 (-3,00%) sau 3 phiên (17/11 – 19/11)" in r and "Qua ngày GDKHQ" not in r and "GDKHQ" not in r, r)

# in the session: VNDirect has not posted today; DNSE's forming bar and the last match close it
# VNDirect's 18/11 and 19/11 as DNSE's fake bars have them (no ex-date)
STOCK_DAYS["FPT"] = [{"Date": d, "Exchange": "HOSE", "Ref": ref, "Ceiling": 0.0, "Floor": 0.0, "Open": o, "High": h, "Low": lo,
                      "Close": c, "Volume": v, "Value": 0.0, "PutThroughVolume": 0.0}
                     for d, ref, o, h, lo, c, v in (("2026-11-18", 47_000.0, 47_000.0, 47_500.0, 46_800.0, 46_950.0, 16_204_800.0),
                                                    ("2026-11-19", 46_950.0, 46_950.0, 47_950.0, 45_600.0, 45_700.0, 18_437_700.0))]
TICKS[("FPT", "2026-11-20")] = 45_300
CALLS.clear()
r = quote.stocks_reply("fpt 3d", now=at("2026-11-20", 10, 32))
lines = r.splitlines()
t.ok("in the session: the header says so, today's row from the last match against DNSE's previous close, '(đang khớp)'",
     lines[0] == "📅 Giá 3 phiên gần nhất · lúc 10:32 20/11 (phiên hôm nay theo giá khớp, đang chạy)"
     and lines[4] == "   20/11: 45.300 🔴 -0,88% · KL 15,67 triệu (đang khớp)", r)
t.ok("...the period against the first session's reference",
     lines[2] == "🔴 FPT: 45.300 -1.700 (-3,62%) sau 3 phiên (18/11 – 20/11)", lines[2])
t.ok("...today's high/low from the forming bar; the session's volume out of the average",
     lines[3] == "   Cao nhất 47.950 (19/11) · Thấp nhất 44.650 (20/11) · KL TB 17,32 triệu", lines[3])
t.ok("...asked: VNDirect, DNSE's bars, the tick", {c[0] for c in CALLS} == {"vnd", "bars", "tick"}, CALLS)
del TICKS[("FPT", "2026-11-20")]

# an index, VNDirect down, unknown, too many sessions
CALLS.clear()
r = quote.stocks_reply("VNINDEX 2d", now=at("2026-11-21", 20, 0))
t.ok("an index: DNSE's points, the reference the close before, VNDirect not asked",
     "🔴 VNINDEX: 1.688,79 điểm -0,83 (-0,05%) sau 1 phiên (19/11 – 20/11)" in r
     and "   20/11: 1.688,79 🔴 -0,05% · KL 719,80 triệu" in r and "   19/11: 1.689,62 · KL 642,17 triệu" in r
     and not [c for c in CALLS if c[0] == "vnd"], r)
VND_DOWN.add("FPT")
r = quote.stocks_reply("FPT 2d", now=at("2026-11-21", 20, 0))
VND_DOWN.clear()
t.ok("VNDirect down: DNSE's adjusted bars, said so", "🔴 FPT: 45.000 -1.950 (-4,15%) sau 2 phiên (19/11 – 20/11)" in r
     and "Mã ghi giá DNSE đã điều chỉnh cổ tức/thưởng" in r, r)
r = quote.stocks_reply("XYZ 7d", now=at("2026-11-21", 20, 0))
t.ok("an unknown code: said so, the coin hint", "❔ XYZ: không có mã này trên HOSE/HNX/UPCoM" in r and "Giá coin: gõ alt btc" in r, r)
r = quote.stocks_reply("FPT 100d", now=at("2026-11-21", 20, 0))
t.ok("more than 60 sessions: 60, said so", r.splitlines()[0].startswith("📅 Giá 60 phiên gần nhất")
     and "(Mỗi lần xem tối đa 60 phiên.)" in r, r[:200])
r = quote.stocks_reply("NEW 7d", now=at("2026-11-21", 20, 0))
t.ok("a stock listed for fewer sessions than asked: its sessions, no change without a reference",
     "⚪ NEW: 11.000 · 1 phiên từ ngày niêm yết (20/11 – 20/11)" in r and "   20/11: 11.000 · KL 120.000" in r, r)
bot._handle(msg(111, 111, "fpt 2d"))
t.ok("Telegram 'fpt 2d': the sessions", last(111).startswith("📅 Giá 2 phiên gần nhất"), last(111)[:80])

# ── 7. the docs page lists it ──
from vnbot import pages  # noqa: E402

t.ok("/docs lists 'FPT · FPT HPG', 'FPT 7d · FPT 14d' and 'alt btc · alt eth sol'",
     "<code>FPT · FPT HPG</code>" in pages.docs_body() and "<code>FPT 7d · FPT 14d</code>" in pages.docs_body()
     and "<code>alt btc · alt eth sol</code>" in pages.docs_body())

t.done()
