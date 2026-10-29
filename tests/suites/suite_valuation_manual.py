"""Valuation cards entered by hand (/tsdinhgia, the /taisan page): the command and its number
formats, the store that outlives a sale, the zone alerts in the session (once per entry, a
buffer, a memory that survives a restart), expiry and ex-dates, the shadow results of the
"re" and "dat" alerts, the web form behind the password (offline, prices from a fake market)."""
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

import _common  # noqa: F401 - clean env, no .env, offline, temp folder, repo on sys.path
_common.need("httpx")

ROOT = Path(tempfile.mkdtemp(prefix="dinhgia-"))
os.environ.update({"DATA_DIR": str(ROOT / "app"), "WEB_PASSWORD": "s3cret-pass", "TRADE_MODE": "paper",
                   "TRADINGAGENTS_PARAMS_PATH": str(ROOT / "qp.json"),
                   "TELEGRAM_TOKEN": "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawQ", "TELEGRAM_ALLOWED_IDS": "111"})

from vnbot import settings as settings_module  # noqa: E402

SETTINGS = settings_module.load()

from tradingagents.market import vn_rules  # noqa: E402
from tradingagents.trading.ledger import Ledger  # noqa: E402
from vnbot import taisan as ts  # noqa: E402
from vnbot.intraday import IntradayMonitor  # noqa: E402
from vnbot.taisan import TaiSan  # noqa: E402

t = _common.Checks()
NOW = {"t": datetime(2026, 10, 29, 10, 0, tzinfo=vn_rules.VN_TZ)}      # Thursday, in the session
vn_rules.now_vn = lambda: NOW["t"]


def at(day, hhmm="10:00"):
    y, mo, d = map(int, day.split("-"))
    h, m = map(int, hhmm.split(":"))
    return datetime(y, mo, d, h, m, tzinfo=vn_rules.VN_TZ)


SESSIONS = []
_d = datetime(2026, 9, 1).date()
while _d.isoformat() <= "2026-12-31":
    if vn_rules.is_session_day(_d.isoformat()):
        SESSIONS.append(_d.isoformat())
    _d = _d.fromordinal(_d.toordinal() + 1)


class FakeMarket:
    """Live prices from ``price``; closes rising 0.1% a session up to ``last``; events per symbol."""

    def __init__(self):
        self.price = {"CTG": 30_700.0, "FPT": 100_000.0, "VCB": 60_000.0, "VNINDEX": 1_250.0}
        self.ev, self.calls, self.last, self.fail = {}, [], "2026-12-31", False

    def daily_closes(self, symbol, end):
        base = self.price.get(symbol, 10_000.0)
        return {d: round(base * (1 + 0.001 * i), 2) for i, d in enumerate(SESSIONS) if d <= min(end, self.last)}

    def unadjusted_closes(self, symbol, start, end):
        return {d: v for d, v in self.daily_closes(symbol, end).items() if d >= start}

    def live(self, symbols, day):
        return {s: self.price[s] for s in symbols if s in self.price}

    def events(self, symbol, start, end):
        self.calls.append(("events", symbol, start, end))
        if self.fail:
            raise OSError("VCI down")
        return [e for e in self.ev.get(symbol, []) if not e["exrightDate"] or start <= e["exrightDate"][:10] <= end]

    def exchange(self, symbol):
        return "HOSE"


def account(name):
    return TaiSan(ROOT / name, market=FakeMarket(), site=None)


def raises(fn, needle=""):
    try:
        fn()
    except ValueError as exc:
        return needle in str(exc) or print("   message:", exc)
    return False


# ───────────── 1. the command: numbers, keywords, errors ─────────────
a = account("cmd")
say = a.handle
reply = say("/tsdinhgia", "ctg 27.904 30,164 33309 eab 26.039 han 30/12/2026 nguon hồ sơ PDF 28/10")
card = a.valuations()["CTG"]
t.ok("dots, commas and plain digits are all whole đồng", (card["bear"], card["base"], card["bull"], card["eab"])
     == (27_904, 30_164, 33_309, 26_039), card)
t.ok("expiry dd/mm/yyyy, the source text, the day entered and the channel kept",
     card["expires"] == "2026-12-30" and card["note"] == "hồ sơ PDF 28/10" and card["day"] == "2026-10-29"
     and card["source"] == "telegram", card)
t.ok("the reply: the card, today's zone and MOS, how the alerts work",
     reply.startswith("✅ Thẻ định giá CTG (anh nhập, chỉ để tham khảo; không đổi cắt lỗ, mục tiêu hay quyết định của bot)")
     and "Bear 27.904 · eab 26.039 · Base 30.164 · Bull 33.309 · hạn 30/12/2026 · nguồn: hồ sơ PDF 28/10" in reply
     and "Giá 30.700 (khớp 10:00) → Hợp lý, MOS -1,8%" in reply and "vào vùng Rẻ hoặc Đắt" in reply, reply)
say("/tsdinhgia", "VCB 50000 58000 70000 han 2026-12-31", source="zoom")
t.ok("expiry as YYYY-MM-DD too (not taken for an entry's day); Zoom as the channel",
     a.valuations()["VCB"]["expires"] == "2026-12-31" and a.valuations()["VCB"]["source"] == "zoom", a.valuations()["VCB"])
for words, needle in (("CTG 27,9 30164 33309", "không nhận số lẻ"), ("CTG 27904 30.164,5 33309", "Base không hợp lệ"),
                      ("CTG 28 30 33", "Bear không hợp lệ"), ("CTG 30164 27904 33309", "Cần Bear < Base < Bull"),
                      ("CTG 27904 30164 30164", "Cần Bear < Base < Bull"), ("CTG 27904 30164", "Dùng: /tsdinhgia"),
                      ("CTG 27904 30164 33309 eab 30164", "eab phải thấp hơn Base"),
                      ("CTG 27904 30164 33309 han 28/10/2026", "đã qua"),
                      ("CTG 27904 30164 33309 han 31/02/2027", "Hạn dùng không hợp lệ"),
                      ("CTG 27904 30164 33309 gia 1", "Không hiểu 'gia'"), ("CTG 27904 30164 33309 eab", "Thiếu giá trị"),
                      ("C@G 27904 30164 33309", "Mã không hợp lệ"), ("VNINDEX 1 2 3", "chỉ số")):
    t.ok(f"refused: /tsdinhgia {words}", raises(lambda: say("/tsdinhgia", words), needle))
t.ok("a refusal leaves the card as it was", a.valuations()["CTG"]["base"] == 30_164)
listing = say("/tsdinhgia", "")
t.ok("/tsdinhgia lists every card with the price label", listing.startswith("📐 THẺ ĐỊNH GIÁ ANH NHẬP · khớp 10:00")
     and "• CTG Bear 27.904 · eab 26.039 · Base 30.164 · Bull 33.309 → giá 30.700: Hợp lý, MOS -1,8% · nhập 29/10/2026 · "
         "hạn 30/12/2026 · nguồn: hồ sơ PDF 28/10" in listing
     and "• VCB Bear 50.000 · Base 58.000 · Bull 70.000 → giá 60.000: Hợp lý, MOS -3,4%" in listing, listing)
t.ok("/tsdinhgia MÃ: that card only", "• CTG" in say("/tsdinhgia", "ctg") and "VCB" not in say("/tsdinhgia", "CTG").split("\n")[1])
t.ok("/tsdinhgia MÃ without a card says so", say("/tsdinhgia", "HPG").startswith("Chưa có thẻ định giá HPG."))
t.ok("/tsdinhgia MÃ xoa", say("/tsdinhgia", "VCB xoa") == "✅ Đã xoá thẻ định giá VCB." and "VCB" not in a.valuations())
t.ok("removing a card that is not there is refused", raises(lambda: say("/tsdinhgia", "VCB xoá"), "Không có thẻ định giá VCB"))
t.ok("an empty list says how to add", say("/tsdinhgia", "") .startswith("📐") and account("empty").handle(
    "/tsdinhgia", "").startswith("Chưa có thẻ định giá nào. Dùng: /tsdinhgia CTG 27904 30164 33309"))
many = account("many")
for i in range(ts.MAX_VALUATIONS):
    many.set_valuation(f"A{i:02d}", 1000, 2000, 3000)
t.ok(f"at most {ts.MAX_VALUATIONS} cards; replacing one is still allowed",
     raises(lambda: many.set_valuation("ZZZ", 1000, 2000, 3000), "tối đa")
     and many.set_valuation("A00", 1100, 2000, 3000)["bear"] == 1100)
t.ok("in both command lists and the help", "/tsdinhgia" in TaiSan.COMMANDS and "/tsdinhgia CTG 27904 30164 33309" in TaiSan.HELP)

# ── Telegram and Zoom share the dispatcher ──
from vnbot.jobs import JobQueue  # noqa: E402
from vnbot.service import Watchlist  # noqa: E402
from vnbot.telegram_bot import TAISAN_COMMANDS, ZOOM_USER, TelegramBot  # noqa: E402

tg = TelegramBot(SETTINGS, JobQueue(SETTINGS), Watchlist(SETTINGS))
tg.taisan = account("tg")
sent = []
tg.send = lambda chat_id, text: sent.append((chat_id, text))
tg._handle({"chat": {"id": 111}, "from": {"id": 111}, "text": "/tsdinhgia HPG 20000 24.000 28,000"})
t.ok("Telegram: /tsdinhgia saved", "/tsdinhgia" in TAISAN_COMMANDS and sent[-1][1].startswith("✅ Thẻ định giá HPG")
     and tg.taisan.valuations()["HPG"]["source"] == "telegram", sent[-1:])
tg._handle({"chat": {"id": "zoom:1"}, "from": {"id": ZOOM_USER}, "text": "/tsdinhgia MBB 20000 24000 28000 nguon zoom"})
t.ok("Zoom: the same command through the same dispatcher", sent[-1][0] == "zoom:1" and sent[-1][1].startswith("✅ Thẻ định giá MBB")
     and tg.taisan.valuations()["MBB"]["source"] == "zoom", sent[-1:])
tg._handle({"chat": {"id": 111}, "from": {"id": 111}, "text": "/tsdinhgia HPG 1 2"})
t.ok("Telegram: a bad card is a ⚠️ message", sent[-1][1].startswith("⚠️ Dùng: /tsdinhgia"), sent[-1:])
tg._handle({"chat": {"id": 999}, "from": {"id": 999}, "text": "/tsdinhgia HPG xoa"})
t.ok("a stranger cannot touch the cards", "HPG" in tg.taisan.valuations() and "TELEGRAM_ALLOWED_IDS" in sent[-1][1])
tg._handle({"chat": {"id": 111}, "from": {"id": 111}, "text": "/trogiup"})
t.ok("/trogiup lists /tsdinhgia", "/tsdinhgia CTG 27904 30164 33309" in sent[-1][1])

# ───────────── 2. the store: not a level, kept after a sale, an undo, a reset ─────────────
p = account("prune")
p.add("DEPOSIT", "2026-10-01", amount=100_000_000)
p.add("BUY", "2026-10-02", symbol="FPT", qty=100, price=95_000)
p.set_levels("FPT", 90_000, 110_000)
p.set_valuation("FPT", 80_000, 100_500, 120_000)
p.set_valuation("CTG", 27_904, 30_164, 33_309)
st = p.state()
t.ok("a card is not a level of the portfolio", st["levels"]["FPT"]["stop"] == 90_000 and "CTG" not in st["levels"])
p.record_sell("FPT", 100, 101_000, "2026-10-28")
st = p.state()
t.ok("selling the whole position prunes its levels, keeps its card", "FPT" not in st["levels"]
     and set(st["valuations"]) == {"FPT", "CTG"}, (st["levels"], list(st["valuations"])))
p.undo()
p.undo()
t.ok("an undo (prune again) keeps the cards", set(p.valuations()) == {"FPT", "CTG"})
p.reset()
t.ok("a reset of the portfolio keeps the cards", set(p.valuations()) == {"FPT", "CTG"})
t.ok("an older state file without the key reads as no card", account("old").valuations() == {})

# ───────────── 3. zones, the buffer, the alerts in the session ─────────────
c = {"bear": 27_904.0, "base": 30_164.0, "bull": 33_309.0}
t.ok("zones: Rẻ ≤ Base, Đắt ≥ Bull", [ts.card_zone(c, x) for x in (30_164, 30_165, 33_308, 33_309)]
     == ["Rẻ", "Hợp lý", "Hợp lý", "Đắt"])
t.ok("buffer 0,5%: Rẻ kept up to Base × 1,005, Đắt down to Bull × 0,995",
     ts.card_zone(c, 30_300, "Rẻ") == "Rẻ" and ts.card_zone(c, 30_316, "Rẻ") == "Hợp lý"
     and ts.card_zone(c, 33_150, "Đắt") == "Đắt" and ts.card_zone(c, 33_140, "Đắt") == "Hợp lý"
     and ts.card_zone(c, 30_300, "Hợp lý") == "Hợp lý")
t.ok("MOS = (Base − giá)/Base", ts.mos_text(c, 30_700) == "MOS -1,8%" and ts.mos_text(c, 30_100) == "MOS +0,2%")


class FakeTrader:
    def __init__(self, acc, real=None):
        self.taisan, self._real = acc, real

    def real(self, name=None):
        return self._real or Ledger(cash=0, start_cash=0)

    def paper(self):
        return Ledger(cash=1e9, start_cash=1e9)


z = account("zones")
z.set_valuation("CTG", 27_904, 30_164, 33_309, eab=26_039, expires="2026-12-31")
PRICE = {"CTG": 30_700.0}
DIR = ROOT / "intraday"
DIR.mkdir()
ISETTINGS = settings_module.Settings(**{**SETTINGS.__dict__, "data_dir": DIR})
notes = []


def monitor(acc=z, real=None):
    """A new monitor at each pass: every check is also a restart."""
    return IntradayMonitor(ISETTINGS, FakeTrader(acc, real), notes.append, price=lambda code, day: PRICE.get(code),
                           index_change=lambda day: None, exchange_of=lambda code: "HOSE")


def path(prices, day="2026-10-29", acc=z):
    out = []
    for i, v in enumerate(prices):
        PRICE["CTG"] = v
        NOW["t"] = at(day, f"10:{10 + i:02d}")
        out.append(monitor(acc).check(day, now=NOW["t"]))
    return out


got = path([30_700, 30_100, 30_300, 33_400])
alerts = [x for out in got for x in out]
t.ok("30,7 → 30,1 → 30,3 → 33,4: exactly 2 alerts, entering Rẻ then Đắt", len(alerts) == 2
     and got[1] == ["🟢 CTG vào vùng Rẻ: giá 30.100 ≤ Base 30.164, MOS +0,2% (thẻ anh nhập 29/10: Bear 27.904 · eab 26.039 · "
                    "Base 30.164 · Bull 33.309; tham khảo, không phải lệnh)"]
     and got[3][0].startswith("🔺 CTG vào vùng Đắt: giá 33.400 ≥ Bull 33.309, MOS -10,7%"), got)
t.ok("sent in the session's message, kept in today's log", notes[-1].startswith("🔔 Cảnh báo trong phiên 10:13 (không gọi AI)\n🔺 CTG")
     and [x["text"][:1] for x in monitor().today("2026-10-29")] == ["🟢", "🔺"], notes[-1:])
t.ok("restart at the same price: nothing again", path([33_400, 33_200]) == [[], []])
t.ok("the memory is its own file: the last zone of the card", json.loads((DIR / "valuation_zones.json").read_text(
    encoding="utf-8"))["CTG"]["zone"] == "Đắt")
t.ok("the next day, still Đắt: no repeat (the day's keys are not the memory)", path([33_500], day="2026-10-30") == [[]])
got = path([30_100, 30_300, 30_150, 30_316, 30_100], day="2026-10-30")
t.ok("around Base: one Rẻ, held inside the buffer, again only after leaving it", [len(x) for x in got] == [1, 0, 0, 0, 1], got)
st = z.state()["shadow"]
t.ok("kept in the shadow results as re/dat, once a day per level, no AI hook",
     [(r["day"], r["level"], r["price"]) for r in st] == [("2026-10-29", "re", 30_100.0), ("2026-10-29", "dat", 33_400.0),
                                                          ("2026-10-30", "re", 30_100.0)], st)
hooked = []
z.on_alert = lambda *args: hooked.append(args)
z.set_valuation("CTG", 27_000, 30_200, 33_000)
t.ok("a card entered again starts afresh (price already in Rẻ: said)", path([30_100], day="2026-11-02") != [[]] and hooked == [])
z.clear_valuation("CTG")
t.ok("no card: no zone alert, the memory emptied", path([25_000], day="2026-11-02") == [[]]
     and json.loads((DIR / "valuation_zones.json").read_text(encoding="utf-8")) == {})

# a stock not held, next to a holding whose stop is hit: both alerts, the old one unchanged
h = account("held")
h.set_valuation("CTG", 27_904, 30_164, 33_309)
book = Ledger(cash=1e9, start_cash=1e9)
book.record_fill("BUY", "BBB", 1000, 60_000, "2026-10-15", stop=55_000, target1=68_000)
SETTINGS_SIGNAL = settings_module.Settings(**{**ISETTINGS.__dict__, "trade_mode": "signal", "data_dir": ROOT / "signal"})
(ROOT / "signal").mkdir()
PRICE.update({"CTG": 30_000, "BBB": 54_000})
mon = IntradayMonitor(SETTINGS_SIGNAL, FakeTrader(h, book), notes.append, price=lambda code, day: PRICE.get(code),
                      index_change=lambda day: None, exchange_of=lambda code: "HOSE")
out = mon.check("2026-11-02", now=at("2026-11-02", "10:30"))
t.ok("a card of a stock not held alerts; the stop alert of a book is as before",
     any(x.startswith("⛔ 💼 Thật BBB: giá 54.000 ≤ stop 55.000") for x in out) and any(x.startswith("🟢 CTG") for x in out), out)
out = mon.check("2026-11-02", now=at("2026-11-02", "10:40"))
t.ok("... and neither repeats in the next pass", out == [], out)

# ───────────── 4. expiry and ex-dates silence a card, said once ─────────────
e = account("expiry")
NOW["t"] = at("2026-10-29")
e.set_valuation("CTG", 27_904, 30_164, 33_309, expires="2026-10-30")
e.set_valuation("VCB", 50_000, 58_000, 70_000)
e.market.ev["VCB"] = [{"eventCode": "DIV", "exrightDate": "2026-10-20T00:00:00", "valuePerShare": 300.0},     # before the card
                      {"eventCode": "DIV", "exrightDate": "2026-11-03T00:00:00", "valuePerShare": 450.0}]
PRICE.update({"CTG": 30_000, "VCB": 57_000})
NOW["t"] = at("2026-10-30")
out = monitor(e).check("2026-10-30", now=NOW["t"])
t.ok("on its last day a card still alerts; an ex-date before the card does not make it stale",
     sorted(x[:5] for x in out) == ["🟢 CTG", "🟢 VCB"], out)
t.ok("the events are asked once a day per card", sum(1 for x in e.market.calls if x[1] == "VCB") == 1
     and e.valuations()["VCB"]["checked"] == "2026-10-30" and monitor(e).check("2026-10-30", now=at("2026-10-30", "10:30")) == []
     and sum(1 for x in e.market.calls if x[1] == "VCB") == 1, e.market.calls)
PRICE.update({"CTG": 34_000, "VCB": 75_000})
NOW["t"] = at("2026-11-03")
out = monitor(e).check("2026-11-03", now=NOW["t"])
t.ok("past its date: said once that the alerts stop, no Đắt alert",
     any(x == "⌛ CTG: thẻ định giá hết hạn 30/10/2026 → bot ngừng báo vùng Rẻ/Đắt; nhập lại: /tsdinhgia CTG BEAR BASE BULL"
         for x in out) and not any("vào vùng Đắt" in x for x in out), out)
t.ok("an ex-date since the card: cần cập nhật, said once, no Đắt alert",
     any(x.startswith("⚠️ VCB: thẻ định giá cần cập nhật - GDKHQ 03/11/2026 (cổ tức tiền 450 đ/cp) sau ngày anh nhập 29/10/2026")
         for x in out) and len(out) == 2, out)
t.ok("then silent", monitor(e).check("2026-11-03", now=at("2026-11-03", "11:00")) == []
     and monitor(e).check("2026-11-04", now=at("2026-11-04", "10:00")) == [])
listing = e.handle("/tsdinhgia", "")
t.ok("the list marks both", "· ⌛ HẾT HẠN 30/10/2026" in listing
     and "· ⚠️ cần cập nhật: GDKHQ 03/11/2026 (cổ tức tiền 450 đ/cp)" in listing, listing)
e.set_valuation("VCB", 49_000, 57_000, 69_000)
t.ok("a card entered after the ex-date is current again", not e.valuation_status("VCB", e.valuations()["VCB"])["stale"])
f = account("fail")
NOW["t"] = at("2026-11-04")
f.set_valuation("MBB", 20_000, 24_000, 28_000)
f.market.ev["MBB"] = [{"eventCode": "ISS", "exrightDate": "2026-11-05T00:00:00", "exerciseRatio": 0.1,
                       "eventTitleVi": "Phát hành cổ phiếu - Quyền mua CP cho Cổ đông hiện hữu tỉ lệ 10.0%"},
                      {"eventCode": "ISS", "exrightDate": None, "exerciseRatio": 0.03, "eventTitleVi": "Phát hành cho CBCNV"}]
f.market.fail = True
NOW["t"] = at("2026-11-05")
t.ok("events unavailable: not stale, asked again at the next look",
     not f.valuation_status("MBB", f.valuations()["MBB"])["stale"] and "checked" not in f.valuations()["MBB"])
f.market.fail = False
t.ok("rights to buy are an ex-date too (ESOP without one is not)",
     f.valuation_status("MBB", f.valuations()["MBB"])["stale"] == {"ex": "2026-11-05", "what": "phát hành thêm 10%"})
t.ok("a stock dividend and bonus shares read as such", ts.ex_event(
    {"eventCode": "ISS", "exrightDate": "2025-12-17T00:00:00", "exerciseRatio": 0.44636584,
     "eventTitleVi": "Phát hành cổ phiếu - Trả Cổ tức bằng Cổ phiếu tỉ lệ 44.6%"})
    == {"ex": "2025-12-17", "what": "cổ phiếu thưởng/cổ tức bằng cổ phiếu 44,6%"})

# ───────────── 5. shadow results: the direction of each level ─────────────
s = account("shadow")
for level in ("re", "dat", "stop"):
    s.record_alert(level, "FPT", 100_000, "2026-10-01", "10:00", hook=False)
s.settle_shadow("2026-10-30")
stats = {x["level"]: x for x in s.shadow_stats()}
t.ok("prices rising after the alerts: Rẻ right, Đắt and the cut-loss wrong",
     stats["re"]["r10"] > 0 and stats["re"]["right"] == 100 and stats["dat"]["right"] == 0 and stats["stop"]["right"] == 0, stats)
s2 = account("shadow2")
s2.market.price["FPT"] = 100_000.0
s2.daily = s2.market.daily_closes
s2.market.daily_closes = lambda symbol, end: {d: v * (1 - 0.003 * i) for i, (d, v) in enumerate(sorted(s2.daily(symbol, end).items()))}
for level in ("re", "dat"):
    s2.record_alert(level, "FPT", 100_000, "2026-10-01", "10:00", hook=False)
s2.settle_shadow("2026-10-30")
stats = {x["level"]: x for x in s2.shadow_stats()}
t.ok("prices falling: Đắt right, Rẻ wrong", stats["dat"]["right"] == 100 and stats["re"]["right"] == 0, stats)
lines = "\n".join(s._shadow_lines())
t.ok("the weekly lines say which way is right", "🟢 Vào vùng Rẻ: 1 lần" in lines and "giá cao hơn 100% số lần" in lines
     and "🔺 Vào vùng Đắt: 1 lần" in lines and "sau 🟢 Rẻ: giá tăng là đúng" in lines, lines)

import vnbot.trader as T  # noqa: E402


class WL:
    def get(self):
        return []


bot = T.TradingBot(SETTINGS, WL())
bot.taisan.market, bot.taisan.site = FakeMarket(), None
bot.taisan.record_alert("re", "CTG", 30_100, "2026-10-01", "10:00", hook=False)
t.ok("no holding at all: the zone alerts are still settled in the evening",
     bot.taisan_day("2026-10-30") == "" and bot.taisan.state()["shadow"][0].get("r10") is not None)

# ───────────── 6. the web page ─────────────
from fastapi.testclient import TestClient  # noqa: E402
from vnbot.channels import Switches  # noqa: E402
from vnbot.web import create_app  # noqa: E402

from tradingagents.dataflows import vn_valuation  # noqa: E402
from tradingagents.dataflows.valuation_config import PROFILES  # noqa: E402


class BotCard:
    def __init__(self, base):
        self.bear, self.base, self.bull, self.period, self.assumptions = base * 0.95, base, base * 1.02, "Q2/2026", PROFILES["banking"]


BOT = {"CTG": BotCard(27_643.0), "VCB": BotCard(52_000.0)}
vn_valuation.is_bank = lambda symbol: symbol in ("CTG", "VCB", "TCB")
vn_valuation.card = lambda symbol, as_of: BOT.get(symbol)
NOW["t"] = at("2026-10-29")
app = create_app(SETTINGS, JobQueue(SETTINGS), Watchlist(SETTINGS), switches=Switches(SETTINGS.data_dir, {}))
w = app.state.taisan
w.market, w.site = FakeMarket(), None
web = TestClient(app, raise_server_exceptions=False)
AUTH, SAME = ("x", "s3cret-pass"), {"origin": "http://testserver"}


def post(data):
    from urllib.parse import unquote

    r = web.post("/taisan/dinhgia", data=data, auth=AUTH, headers=SAME, follow_redirects=False)
    return r.status_code, unquote(r.headers.get("location", ""))


t.ok("POST /taisan/dinhgia without password -> 401",
     web.post("/taisan/dinhgia", data={"symbol": "CTG", "bear": "1000", "base": "2000", "bull": "3000"}).status_code == 401)
r = web.post("/taisan/dinhgia", data={"symbol": "CTG", "bear": "1000", "base": "2000", "bull": "3000"}, auth=AUTH,
             headers={"origin": "https://evil.example"})
t.ok("POST /taisan/dinhgia cross-site -> 403, nothing saved", r.status_code == 403 and w.valuations() == {}, r.status_code)
page = web.get("/taisan", auth=AUTH)
t.ok("empty portfolio: the cards section is there to start with", page.status_code == 200 and 'id="dinhgia"' in page.text
     and "Chưa có thẻ định giá nào." in page.text and 'action="/taisan/dinhgia"' in page.text)
code, loc = post({"symbol": "ctg", "bear": "27.904", "base": "30,164", "bull": "33309", "eab": "26.039",
                  "han": "2026-12-30", "nguon": "<script>alert(1)</script>", "action": "save"})
t.ok("same-origin form saves a card of a stock not held, message back", code == 303 and "#dinhgia" in loc
     and "✅ Thẻ định giá CTG" in loc and w.valuations()["CTG"]["source"] == "web"
     and w.valuations()["CTG"]["expires"] == "2026-12-30", loc)
code, loc = post({"symbol": "VCB", "bear": "50000", "base": "60.000", "bull": "70000"})
code, loc = post({"symbol": "FPT", "bear": "90000", "base": "95000", "bull": "120000"})
code, loc = post({"symbol": "HPG", "bear": "30000", "base": "25000", "bull": "35000"})
t.ok("a bad card: the message back, nothing saved", "⚠️ Cần Bear < Base < Bull" in loc and "HPG" not in w.valuations(), loc)
code, loc = post({"symbol": "HPG", "bear": "27,9", "base": "30000", "bull": "35000"})
t.ok("a number in thousands refused", "⚠️ Bear không hợp lệ" in loc, loc)
w.market.ev["FPT"] = [{"eventCode": "DIV", "exrightDate": "2026-10-30T00:00:00", "valuePerShare": 1000.0}]
w.set_valuation("TCB", 20_000, 30_000, 40_000, expires="2026-10-29")
NOW["t"] = at("2026-10-30")
page = web.get("/taisan", auth=AUTH).text
section = page.split('id="dinhgia"', 1)[-1].split("</details>", 1)[0]
row = {sym: section.split(f"<b>{sym}</b>", 1)[-1].split("</tr>", 1)[0] for sym in ("CTG", "VCB", "FPT", "TCB")}
t.ok("CTG: zone, MOS, expiry, the bot's card beside it, the gap under 10% not flagged",
     'chip-neg">Đắt</span>' not in row["CTG"] and ">Hợp lý</span>" in row["CTG"] and "-1,78%" in row["CTG"]
     and "hạn 30/12/2026" in row["CTG"] and "Base 27.643" in row["CTG"] and "+9,12%" in row["CTG"]
     and "lệch" not in row["CTG"] and "Ke 12% đặt tay" in row["CTG"], row["CTG"])
t.ok("VCB: Base 15% over the bot's: flagged", "lệch +15,38%" in row["VCB"] and "chip-neg" in row["VCB"]
     and "quá 10%: xem lại giả định" in row["VCB"], row["VCB"])
t.ok("FPT: not a bank, no bot card; an ex-date since the card: cần cập nhật",
     "chỉ có cho ngân hàng" in row["FPT"] and "cần cập nhật · GDKHQ 30/10" in row["FPT"], row["FPT"])
t.ok("TCB: HẾT HẠN, Rẻ", "HẾT HẠN 29/10/2026" in row["TCB"] and 'chip-pos">Rẻ</span>' in row["TCB"], row["TCB"])
t.ok("the source text escaped", "<script>alert(1)</script>" not in page and "&lt;script&gt;" in section)
w.add("DEPOSIT", "2026-10-01", amount=100_000_000)
w.add("BUY", "2026-10-02", symbol="CTG", qty=100, price=29_000)
page = web.get("/taisan", auth=AUTH).text
section = page.split('id="dinhgia"', 1)[-1].split("</details>", 1)[0]
t.ok("full page: the section, a held stock marked", "<b>CTG</b> <span class=\"badge b-info\">đang giữ</span>" in section
     and '<option value="CTG">' in section)
t.ok("the shadow table names both directions", "Kết quả cảnh báo bán và vùng định giá" in page and "Đúng hướng" in page)
vn_valuation.card = lambda symbol, as_of: (_ for _ in ()).throw(RuntimeError("VCI down"))
page = web.get("/taisan", auth=AUTH)
t.ok("the bot's card failing costs only its cell", page.status_code == 200 and "chưa tính được" in page.text)
code, loc = post({"symbol": "VCB", "action": "clear"})
t.ok("the form removes a card", "✅ Đã xoá thẻ định giá VCB." in loc and "VCB" not in w.valuations(), loc)

# ───────────── 7. informational only ─────────────
repo = _common.REPO
readers = sorted(x.relative_to(repo).as_posix() for x in (repo / "tradingagents").rglob("*.py")
                 if "vnbot" in x.read_text(encoding="utf-8", errors="replace"))
t.ok("nothing of the Quant Engine reads the bot's side (cards, alerts)", not any(
    "taisan" in x.read_text(encoding="utf-8", errors="replace") for x in (repo / "tradingagents").rglob("*.py")), readers)

t.done()
