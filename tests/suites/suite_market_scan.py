"""The bot's market-wide scan (vnbot.market_scan): the history (one request a session, re-based
after a dividend), the most traded stocks, the scan and its levels, the comparison with the
site, the pushed list and comparison, /stvthitruong (offline)."""
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

import _common  # noqa: F401 - clean env, no .env, offline, temp folder, repo on sys.path

DATA = Path(tempfile.mkdtemp(prefix="market-"))
os.environ.update({"DATA_DIR": str(DATA), "TRADE_MODE": "paper", "SIEUTINHIEU": "1", "STV_MARKET_SIZE": "50",
                   "STV_ESTIMATE": "0",
                   "TELEGRAM_TOKEN": "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawQ", "TELEGRAM_ALLOWED_IDS": "111",
                   "TRADINGAGENTS_PARAMS_PATH": str(DATA / "quant_params.json")})
from vnbot import settings as settings_module  # noqa: E402

SETTINGS = settings_module.load()
from tradingagents.market import vn_rules  # noqa: E402
from tradingagents.dataflows.vendors import sieutinhieu as stv  # noqa: E402
from vnbot import external  # noqa: E402
from vnbot import market_scan as ms  # noqa: E402
from vnbot import trend_estimate as te  # noqa: E402
import vnbot.trader as T  # noqa: E402

t = _common.Checks()
NOW = {"t": datetime(2026, 10, 22, 10, 0, tzinfo=vn_rules.VN_TZ)}
vn_rules.now_vn = lambda: NOW["t"]


def at(hh, mm, day=22):
    NOW["t"] = datetime(2026, 10, day, hh, mm, tzinfo=vn_rules.VN_TZ)


# ───────────── 1. sessions ─────────────
s5 = ms.sessions("2026-10-22", 5)
t.ok("sessions: the n session days up to a day, oldest first, weekends out",
     s5 == ["2026-10-16", "2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22"], s5)
t.ok("sessions: from a Sunday, the Friday before", ms.sessions("2026-10-25", 1) == ["2026-10-23"])

# ───────────── 2. a synthetic market ─────────────
DAYS = ms.sessions("2026-10-22", 140)
UP = [10_000 + 40 * i + (150 if i % 3 == 0 else -90) for i in range(140)]
DOWN = [70_000 - 120 * i for i in range(140)]
SERIES, VALUE, FLOOR = {}, {}, {}
for k in range(60):
    code = f"S{k:02d}"
    SERIES[code] = [round(p * (1 + k / 100)) for p in (UP if k % 2 == 0 else DOWN)]
    VALUE[code] = 1e9 * (100 - k)                       # S00 most traded
    FLOOR[code] = "HOSE" if k < 50 else "UPCOM"
for k in range(10):                                     # new listings: too short a history
    code = f"N{k}"
    SERIES[code] = [None] * 40 + [20_000] * 100
    VALUE[code] = 9e12
    FLOOR[code] = "HOSE"
fetch_calls = []
SCALE = {}                                              # a dividend since: VNDirect's closes adjusted


def fake_day(day):
    fetch_calls.append(day)
    if day == "2026-09-02":                             # a holiday the source has nothing for
        return []
    i = DAYS.index(day)
    out = []
    for code, ser in SERIES.items():
        if ser[i] is None:
            continue
        out.append({"code": code, "floor": FLOOR[code], "close": round(ser[i] * SCALE.get(code, 1.0), 1),
                    "value": VALUE[code]})
    return out


ms.FETCH_PAUSE = 0
mc = ms.MarketCloses(DATA / "m1")
n, err = mc.update("2026-10-21", fetch=fake_day, budget=-1, today="2026-10-21")
t.ok("update: the time budget is respected", n == 0 and fetch_calls == [] and mc.progress("2026-10-21")[0] == 0, fetch_calls)
n, err = mc.update("2026-10-21", fetch=fake_day, budget=60, today="2026-10-21")
have, want = mc.progress("2026-10-21")
t.ok("update: the rest newest first, up to 130 + 5 sessions", not err and have == want == ms.HISTORY_SESSIONS
     and len(mc.days()) == ms.HISTORY_SESSIONS + ms.KEEP_EXTRA and fetch_calls[:2] == ["2026-10-21", "2026-10-20"],
     (n, err, have, want))
t.ok("update: nothing asked again when complete (same day)", mc.update("2026-10-21", fetch=fake_day, today="2026-10-21") == (0, "")
     and len(fetch_calls) == n)
t.ok("update: one file, small enough", mc.path.stat().st_size < 400_000, mc.path.stat().st_size)

# the next day: the last saved session asked again; a stock VNDirect adjusted is re-based
before = json.loads(mc.path.read_text())["days"]["2026-10-01"]["S03"][0]
SCALE["S03"] = 0.95
n_calls = len(fetch_calls)
n, err = mc.update("2026-10-22", fetch=fake_day, today="2026-10-22")
data = json.loads(mc.path.read_text())
t.ok("next day: the last saved session asked again, then the new one", fetch_calls[n_calls:] == ["2026-10-21", "2026-10-22"]
     and data["basis"] == "2026-10-22", fetch_calls[n_calls:])
t.ok("a stock adjusted since: its saved closes re-based, the others untouched",
     abs(data["days"]["2026-10-01"]["S03"][0] / before - 0.95) < 1e-4
     and data["days"]["2026-10-01"]["S02"][0] == SERIES["S02"][DAYS.index("2026-10-01")], data["days"]["2026-10-01"]["S03"])
t.ok("the oldest sessions beyond 135 dropped", len(data["days"]) == ms.HISTORY_SESSIONS + ms.KEEP_EXTRA
     and min(data["days"]) == DAYS[-(ms.HISTORY_SESSIONS + ms.KEEP_EXTRA)])

mc_err = ms.MarketCloses(DATA / "m2")


def failing_after(day):
    if day < "2026-10-16":
        raise RuntimeError("VNDirect down")
    return fake_day(day)


n2, err2 = mc_err.update("2026-10-21", fetch=failing_after, today="2026-10-21")
t.ok("a failing source: what came is kept, the error said, asked again later",
     n2 == 4 and "VNDirect down" in err2 and mc_err.progress("2026-10-21")[0] == 4, (n2, err2))

# ───────────── 3. the most traded, the scan ─────────────
uni = mc.universe(50, "2026-10-21")
t.ok("universe: the most traded first, a short history left out (new listings)",
     uni == [f"S{k:02d}" for k in range(50)] and not any(c.startswith("N") for c in uni), uni[:5])
lv_s00 = te.levels([SERIES["S00"][DAYS.index(d)] for d in DAYS if d <= "2026-10-21"][-130:])
lv_s01 = te.levels([SERIES["S01"][DAYS.index(d)] for d in DAYS if d <= "2026-10-21"][-130:])
t.ok("test series: even codes BUY, odd codes SELL by the rule", lv_s00["rule"] == "BUY" and lv_s01["rule"] == "SELL")
BOARD = {}
board_calls = []


def fake_board(codes):
    board_calls.append(list(codes))
    return {c: dict(BOARD.get(c, {})) for c in codes if c in BOARD}


def prev_close(code):
    return SERIES[code][DAYS.index("2026-10-21")] * SCALE.get(code, 1.0)


for k in range(50):
    code = f"S{k:02d}"
    BOARD[code] = {"price": prev_close(code), "ref": prev_close(code), "exchange": FLOOR[code]}
BOARD["S00"]["price"] = int(lv_s00["sell_below"]) - 50                     # BUY -> SELL
BOARD["S01"]["price"] = int(lv_s01["buy_above"]) + 50                      # SELL -> BUY
at(10, 0)
cache = {}
snap = ms.scan(mc, 50, NOW["t"], board=fake_board, levels_cache=cache)
flips = {r["symbol"]: r["flip"] for r in snap["rows"] if r.get("flip")}
t.ok("scan: the stocks past their level, both sides, most traded first",
     flips == {"S00": "SELL", "S01": "BUY"} and snap["rows"][0]["symbol"] == "S00" and snap["size"] == 50
     and snap["basis"] == "2026-10-21", flips)
t.ok("scan: one board request for all of them", len(board_calls) == 1 and len(board_calls[0]) == 50)
n_cache = id(cache["lv"])
ms.scan(mc, 50, NOW["t"], board=fake_board, levels_cache=cache)
t.ok("scan: the levels computed once a session", id(cache["lv"]) == n_cache)

# an ex-date on HOSE: the reference price re-bases the levels; UPCoM's reference says nothing
BOARD["S02"]["ref"] = round(prev_close("S02") * 0.9)
BOARD["S02"]["price"] = round(prev_close("S02") * 0.9)
BOARD["S50"] = {"price": prev_close("S50"), "ref": round(prev_close("S50") * 0.97), "exchange": "UPCOM"}
cache.clear()
snap2 = ms.scan(mc, 51, NOW["t"], board=fake_board, levels_cache=cache)
rows2 = {r["symbol"]: r for r in snap2["rows"]}
t.ok("an ex-date (HOSE): levels on the new basis, no false SELL", rows2["S02"].get("rebased") == 0.9
     and not rows2["S02"].get("flip"), rows2["S02"])
t.ok("UPCoM: its reference (an average price) does not re-base", "rebased" not in rows2["S50"], rows2["S50"])
BOARD["S02"] = {"price": prev_close("S02"), "ref": prev_close("S02"), "exchange": "HOSE"}
BOARD["S50"]["ref"] = prev_close("S50")
t.ok("scan: outside the session's hours, no scan", "error" in ms.scan(mc, 50, datetime(2026, 10, 22, 9, 0, tzinfo=vn_rules.VN_TZ),
                                                                     board=fake_board))
t.ok("scan: history not complete -> says how far", "đang tải" in ms.scan(mc_err, 50, NOW["t"], board=fake_board).get("error", ""))

# ───────────── 4. compare and texts ─────────────
snap["site"] = ms.site_view({
    "SELL": {"total": 36, "locked": 34, "changes": [
        {"symbol": "S00", "at": "2026-10-22T09:54", "from": "BUY", "met": True, "stage": "intraday"},
        {"symbol": "S04", "at": "2026-10-22T09:01", "from": "BUY", "met": False, "stage": "intraday"}]},
    "BUY": {"total": 67, "locked": 65, "changes": [
        {"symbol": "VNINDEX", "at": "2026-10-22T07:00", "from": "SELL", "met": None, "stage": "confirmed"},
        {"symbol": "S03", "at": "2026-10-22T10:00", "from": "SELL", "met": None, "stage": "retracted"}]}})
lines = ms.site_lines(snap)
t.ok("site lines: counts, and each row the site shows against the bot",
     any("Trang đếm 67 lần đổi sang MUA, 36 sang BÁN" in x for x in lines)
     and "• Trang: S00 → BÁN lúc 09:54 · bot cũng báo ✓" in lines
     and any(x.startswith("• Trang: S04 → BÁN lúc 09:01 (trang: giá đã quay lại) · bot: chưa qua mức (cách ") for x in lines)
     and "• Trang: VNINDEX → MUA (đã chốt) · ngoài các mã bot quét" in lines
     and any(x.startswith("• Trang: S03 → MUA lúc 10:00, đóng cửa không giữ · ") for x in lines), lines)
summ = ms.summary_text(snap, {"S01"})
t.ok("summary: both sides, followed stocks marked, the site's view, where to look",
     "🟢 Sang MUA (1): S01*" in summ and "🔴 Sang BÁN (1): S00" in summ and "(* mã anh đang giữ" in summ
     and "/stvthitruong" in summ, summ)
rec = ms.compare(snap, ["S00", "S01", "S02", "S03", "ZZZ"], {"S01": "BUY", "S02": "SELL"},
                 {"S00": "BUY", "S01": "BUY", "S02": "SELL", "S03": "SELL"}, {"BUY": 67, "SELL": 36})
f = rec["followed"]
t.ok("compare: matched, extra (the bot only), missed (the site only), states",
     f["n"] == 4 and f["matched"] == ["S01"] and f["extra"] == ["S00"] and f["missed"] == ["S02"]
     and f["agree"] == 2 and f["states"] == 4 and rec["bot"] == {"BUY": 1, "SELL": 1} and rec["site"]["SELL"] == 36, rec)
log = ms.CompareLog(DATA / "m1")
log.add("2026-10-21", rec)
log.add("2026-10-22", rec)
tot = log.totals()
t.ok("totals over the sessions", tot == {"sessions": 2, "matched": 2, "extra": 2, "missed": 2, "agree": 4, "states": 8}, tot)
ctext = ms.compare_text("2026-10-22", rec, tot)
t.ok("comparison text: bot vs site counts, the followed stocks, the running total",
     ctext.startswith("🌐 ĐỐI CHIẾU VỚI SIÊU TÍN HIỆU · phiên 22/10") and "Bot (50 mã, giá đóng cửa): 1 MUA · 1 BÁN" in ctext
     and "67 MUA · 36 BÁN" in ctext and "bot đúng 1/2, báo thừa 1 (S00), sót S02" in ctext
     and "khớp trang 2/4 mã" in ctext and "Cộng dồn 2 phiên: bot đúng 2/4 tín hiệu của trang (50%), báo thừa 2" in ctext, ctext)

# ───────────── 5. in the bot's scan ─────────────
ms.fetch_day = fake_day
ms.FETCH_PAUSE = 0
te.board_prices = fake_board
LIVE = {"BUY": {"changes": [], "total": 60, "locked": 58, "during": True},
        "SELL": {"changes": [], "total": 30, "locked": 28, "during": True}}
stv.fetch_live_changes = lambda side: LIVE[side]
_refresh = external.SignalStore.refresh
external.SignalStore.refresh = lambda self, symbols, **kw: (0, "")


class WL:
    def get(self):
        return []


bot = T.TradingBot(SETTINGS, WL())
bot.stv_watchlist = lambda: ["S01", "S02", "S03"]
SCALE.clear()
for k in range(50):
    code = f"S{k:02d}"
    BOARD[code] = {"price": prev_close(code), "ref": prev_close(code), "exchange": FLOOR[code]}
BOARD["S00"]["price"] = int(lv_s00["sell_below"]) - 50
BOARD["S01"]["price"] = int(lv_s01["buy_above"]) + 50
at(10, 0)
fetch_calls.clear()
out1 = bot.stv_scan()
t.ok("first scan of the day: the history downloaded (no message before 14:46)", out1 is None
     and len(fetch_calls) >= ms.HISTORY_SESSIONS, len(fetch_calls))
rep = bot.stv_scan(report=True)
t.ok("/stvquet: the market line", "Toàn thị trường (50 mã): 1 qua mức MUA, 1 qua mức BÁN · /stvthitruong" in rep, rep)
n_fetch = len(fetch_calls)
at(10, 15)
bot.stv_scan()
t.ok("next scans: no history request", len(fetch_calls) == n_fetch)
at(14, 47)
out2 = bot.stv_scan()
print(out2)
t.ok("from 14:46: the day's list pushed once (alone when nothing else is new)",
     out2 and out2.startswith("🌐 TOÀN THỊ TRƯỜNG · bot quét 50 mã · 14:47 22/10") and "S01*" in out2, out2)
at(14, 55)
txt = bot.stv_market_text()
print(txt)
t.ok("/stvthitruong in the session: lines past the level, the site's view, no comparison yet",
     txt.startswith("🌐 TOÀN THỊ TRƯỜNG · bot quét 50 mã · 14:55 22/10") and "🟢 S01 " in txt and "🔴 S00 " in txt
     and "So với Siêu tín hiệu" in txt and "Chưa có phiên đối chiếu nào." in txt
     and "không phải danh sách của trang" in txt, txt)
at(15, 0)
t.ok("the list not pushed twice", bot.stv_scan() is None)

# after the close: compared once the site's signals of the day are out
store = external.SignalStore(DATA)
st = store._read()
for sym, side, day in (("S01", "BUY", "2026-10-22"), ("S02", "SELL", "2026-09-01"), ("S03", "SELL", "2026-09-01")):
    st["symbols"][sym] = {"fetched": "2026-10-22T15:16", "signals": [
        stv.normalize({"symbol": sym, "timestamp": f"{day}T00:00:00Z", "signal_type": side, "price": 20.0,
                       "price_basis": "bar_close", "created_at": f"{day}T08:08:00Z", "strength_pct": 50})]}
st["market"] = {s: {"day": "2026-10-22", "fetched": "2026-10-22T15:16", "total": n, "locked": n - 2, "signals": []}
                for s, n in (("BUY", 67), ("SELL", 36))}
store._write(st)
at(15, 10)
t.ok("before 15:15: not compared yet", bot.stv_scan() is None)
at(15, 20)
out3 = bot.stv_scan()
print(out3)
t.ok("after the close: the comparison pushed, with the site's counts and the followed stocks",
     out3 and "\n\n🌐 ĐỐI CHIẾU VỚI SIÊU TÍN HIỆU · phiên 22/10" in out3 and "67 MUA · 36 BÁN" in out3
     and "Trên 3 mã anh theo dõi" in out3 and "bot đúng 1/1" in out3, out3)
t.ok("comparison saved once", ms.CompareLog(DATA).get("2026-10-22") is not None and bot.stv_scan() is None)

# /stvthitruong after the close
at(20, 0)
txt2 = bot.stv_market_text()
t.ok("/stvthitruong after the close: the scan at the close and the day's comparison",
     "(giá đóng cửa)" in txt2 and "Đối chiếu phiên 22/10 trên 3 mã" in txt2 and "Cộng dồn 1 phiên" in txt2, txt2)

os.environ["STV_MARKET"] = "0"
t.ok("STV_MARKET=0: off", "đang tắt" in bot.stv_market_text())
del os.environ["STV_MARKET"]

from vnbot import pages  # noqa: E402
from vnbot import telegram_bot as tb  # noqa: E402
from vnbot.jobs import JobQueue  # noqa: E402
from vnbot.service import Watchlist  # noqa: E402

tg = tb.TelegramBot(SETTINGS, JobQueue(SETTINGS), Watchlist(SETTINGS))
tg.trader = bot
out = []
tg.send = lambda chat, text: out.append(text)
tg._handle({"chat": {"id": 111}, "from": {"id": 111}, "text": "/stvthitruong"})
tg._handle({"chat": {"id": 111}, "from": {"id": 111}, "text": "/stvtoanthitruong"})
t.ok("/stvthitruong (and /stvtoanthitruong) routed: a wait notice, then the scan",
     len(out) == 4 and out[0].startswith("⏳") and out[1].startswith("🌐") and out[3].startswith("🌐"), out[:2])
tg._handle({"chat": {"id": 111}, "from": {"id": 111}, "text": "/trogiup"})
t.ok("listed in /trogiup and on the web's command list", "/stvthitruong" in out[-1]
     and any(c == "stvthitruong" for c, _, _ in pages.COMMANDS))
external.SignalStore.refresh = _refresh
t.done()
