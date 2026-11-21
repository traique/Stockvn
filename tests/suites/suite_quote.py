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

# ── 1. which messages are a price lookup ──
cases = {"FPT": ["FPT"], "fpt hpg": ["FPT", "HPG"], "Giá FPT?": ["FPT"], "gia fpt": ["FPT"], "/FPT": ["FPT"],
         "/fpt@VnBot": ["FPT"], "fpt, hpg, vcb": ["FPT", "HPG", "VCB"], "FPT FPT": ["FPT"], "VNINDEX": ["VNINDEX"],
         "vn-index": ["VNINDEX"], "VNI": ["VNINDEX"], "vn30 hnx": ["VN30", "HNX"], "upcom": ["UPCOM"]}
for text, want in cases.items():
    t.ok(f"{text!r} is a price lookup: {want}", quote.stock_codes(text) == want, quote.stock_codes(text))
for text in ("FPT có nên mua", "hello", "help", "ok", "", "/", "giá", "giá vàng hôm nay", "E1VFVN30", "alt status"):
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
t.ok("the command list names the lookup", "FPT · FPT HPG - gõ mã là có giá realtime · /alt btc - giá coin trên OKX"
     in help_text)
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

# ── 6. the docs page lists it ──
from vnbot import pages  # noqa: E402

t.ok("/docs lists 'FPT · FPT HPG' and 'alt btc · alt eth sol'",
     "<code>FPT · FPT HPG</code>" in pages.docs_body() and "<code>alt btc · alt eth sol</code>" in pages.docs_body())

t.done()
