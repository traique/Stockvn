"""The bot's estimate of sieutinhieu.vn's in-session trend changes (vnbot.trend_estimate):
the levels, the rule, the real levels of 22/10/2026, the scan's alerts (holdings, watchlist,
one alert a stock and side, the close that does not keep a change), /stvdoichieu (offline)."""
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

import _common  # noqa: F401 - clean env, no .env, offline, temp folder, repo on sys.path

DATA = Path(tempfile.mkdtemp(prefix="trend-"))
os.environ.update({"STV_MARKET": "0", "DATA_DIR": str(DATA), "TRADE_MODE": "paper", "SIEUTINHIEU": "1",
                   "TELEGRAM_TOKEN": "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawQ", "TELEGRAM_ALLOWED_IDS": "111",
                   "TRADINGAGENTS_PARAMS_PATH": str(DATA / "quant_params.json")})
from vnbot import settings as settings_module  # noqa: E402

SETTINGS = settings_module.load()
from tradingagents.market import vn_rules  # noqa: E402
from tradingagents.dataflows.vendors import sieutinhieu as stv  # noqa: E402
from vnbot import external  # noqa: E402
from vnbot import trend_estimate as te  # noqa: E402
import vnbot.intraday as intraday_module  # noqa: E402
import vnbot.trader as T  # noqa: E402

t = _common.Checks()
NOW = {"t": datetime(2026, 10, 22, 9, 54, tzinfo=vn_rules.VN_TZ)}
vn_rules.now_vn = lambda: NOW["t"]


def at(hh, mm, day=22):
    NOW["t"] = datetime(2026, 10, day, hh, mm, tzinfo=vn_rules.VN_TZ)


# ───────────── 1. the levels ─────────────
up = [10_000 + 40 * i + (150 if i % 3 == 0 else -90) for i in range(90)]
lv = te.levels(up)
t.ok("too few closes: no levels", te.levels(up[:59]) is None and te.levels(up[:60]) is not None)
t.ok("a rising series: the rule says BUY", lv["rule"] == "BUY", lv)
s_lv = lv["sma"]
t.ok("closing at the SMA level: the close equals SMA20 (with it)", abs(sum(up[-19:] + [s_lv]) / 20 - s_lv) < 0.06, s_lv)
m_lv = lv["macd"]
e1, e2 = te._ema(up + [m_lv], 12), te._ema(up + [m_lv], 26)
macd = [a - b for a, b in zip(e1, e2)]
t.ok("closing at the MACD level: MACD equals its signal line", abs(macd[-1] - te._ema(macd, 9)[-1]) < 0.01)
t.ok("sell_below = min(sma, macd); buy_above = max(sma, macd, previous close)",
     lv["sell_below"] == min(lv["sma"], lv["macd"]) and lv["buy_above"] == max(lv["sma"], lv["macd"], up[-1]), lv)
t.ok("BUY flips under sell_below only (strictly)", te.flip("BUY", lv["sell_below"], lv) is None
     and te.flip("BUY", lv["sell_below"] - 1, lv) == "SELL" and te.flip("BUY", lv["buy_above"] + 1, lv) is None)
t.ok("SELL flips over buy_above only (strictly)", te.flip("SELL", lv["buy_above"], lv) is None
     and te.flip("SELL", lv["buy_above"] + 1, lv) == "BUY" and te.flip("SELL", lv["sell_below"] - 1, lv) is None)
p = lv["sell_below"] * 1.02
t.ok("distance in % of the price, > 0 before the level", abs(te.distance_pct("BUY", p, lv) - 0.02 / 1.02 * 100) < 1e-6
     and te.distance_pct("BUY", lv["sell_below"] * 0.99, lv) < 0)
down = up + [up[-1] - 120 * i for i in range(1, 30)]
t.ok("then a fall: the rule says SELL", te.levels(down)["rule"] == "SELL")

# the real closes up to 21/10/2026 and the site's free rows of 22/10/2026
fx = json.loads((_common.FIXTURES / "trend_2026-10-21.json").read_text(encoding="utf-8"))
for sym in ("HDB", "MSR"):
    r_lv, site = te.levels(fx["closes"][sym]), fx["site_rows"][sym]
    t.ok(f"{sym}: the bot's level within 0,15% of the site's ({site['trigger']:,})",
         r_lv["rule"] == "BUY" and abs(r_lv["sell_below"] / site["trigger"] - 1) < 0.0015, r_lv)
    t.ok(f"{sym}: at the site's price ({site['price_at']:,} at {site['at']}) the bot flips to SELL too",
         te.flip("BUY", site["price_at"], r_lv) == "SELL")
hdb_lv = te.levels(fx["closes"]["HDB"])
t.ok("HDB at 22.250 (14:27, the site's row no longer met): no flip", te.flip("BUY", 22_250, hdb_lv) is None)

# ───────────── 2. assess ─────────────
lvs = {"AAA": hdb_lv, "BBB": hdb_lv, "CCC": hdb_lv, "DDD": hdb_lv, "EEE": hdb_lv}
rows, why = te.assess(["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"],
                      {"AAA": "BUY", "BBB": "BUY", "CCC": "BUY", "EEE": "BUY", "FFF": "BUY"}, lvs,
                      {"AAA": 21_900, "BBB": 15_000, "CCC": None, "DDD": 22_000, "EEE": 19_200},
                      exchange_of=lambda s: "UPCOM" if s == "EEE" else "HOSE")
t.ok("assess: a row with the flip and the level", rows[0]["symbol"] == "AAA" and rows[0]["flip"] == "SELL"
     and rows[0]["level"] == hdb_lv["sell_below"] and rows[0]["dist"] < 0, rows[:1])
t.ok("assess: a price far outside the band left out (an ex-date not adjusted yet)",
     why.get("BBB") == "giá ngoài biên độ so với phiên trước, nghi ngày GDKHQ", why)
t.ok("assess: no price, no site state: left out with the reason",
     why.get("CCC") == "chưa có giá" and why.get("DDD") == "chưa có tín hiệu của trang", why)
t.ok("assess: the band of the stock's exchange (UPCoM -13% is a real move)", any(r["symbol"] == "EEE" for r in rows), why)
t.ok("assess: no levels: left out (the cache says why)", "FFF" not in why and not any(r["symbol"] == "FFF" for r in rows))

# an ex-date: the board's reference price re-bases the levels (HOSE/HNX only)
reb = te.rebase(hdb_lv, 22_100 * 0.95, "HOSE")
t.ok("rebase: every level scaled by reference / last close on an ex-date",
     reb["rebased"] == 0.95 and abs(reb["sell_below"] - hdb_lv["sell_below"] * 0.95) < 0.2 and reb["prev"] == 20_995)
t.ok("rebase: nothing on a normal day, nothing from UPCoM's reference (an average price)",
     te.rebase(hdb_lv, 22_100, "HOSE") is hdb_lv and te.rebase(hdb_lv, 21_000, "UPCOM") is hdb_lv)
rows_x, _ = te.assess(["AAA"], {"AAA": "BUY"}, {"AAA": hdb_lv}, {"AAA": 21_000}, refs={"AAA": (20_995, "HOSE")})
t.ok("assess with the reference: no false SELL on an ex-date", rows_x and rows_x[0]["flip"] is None
     and rows_x[0]["rebased"] == 0.95 and te.flip("BUY", 21_000, hdb_lv) == "SELL", rows_x)

# ───────────── 3. the level cache: once a session ─────────────
asked_closes = []
flaky = {"n": 0}


def closes_fake(sym, basis):
    asked_closes.append((sym, basis))
    if sym == "ZZZ":
        flaky["n"] += 1
        if flaky["n"] == 1:
            raise ValueError("nguồn giá chưa có phiên")
    if sym == "NEW":
        return up[:30]
    return up


cache = te.LevelCache(DATA / "c1")
got, err = cache.get(["AAA", "ZZZ", "NEW"], "2026-10-21", closes_fake)
got2, err2 = cache.get(["AAA", "ZZZ"], "2026-10-21", closes_fake)
t.ok("cache: computed once per basis, an error retried next time",
     asked_closes == [("AAA", "2026-10-21"), ("ZZZ", "2026-10-21"), ("NEW", "2026-10-21"), ("ZZZ", "2026-10-21")]
     and "ZZZ" in err and "ZZZ" in got2 and not err2, asked_closes)
t.ok("cache: a too short history says so", err.get("NEW") == "lịch sử giá quá ngắn", err)
slow, slow_err = te.LevelCache(DATA / "c2").get(["AAA", "BBB"], "2026-10-21", closes_fake, budget=-1)
t.ok("cache: a time budget - the rest waits for the next scan, nothing cached wrong",
     not slow and slow_err == {"AAA": "nguồn giá chậm, tính lượt sau", "BBB": "nguồn giá chậm, tính lượt sau"}
     and "AAA" in te.LevelCache(DATA / "c2").get(["AAA"], "2026-10-21", closes_fake)[0], slow_err)
cache.get(["AAA"], "2026-10-22", closes_fake)
t.ok("cache: a new session computes again", asked_closes[-1] == ("AAA", "2026-10-22")
     and json.loads(cache.path.read_text())["basis"] == "2026-10-22")

# ───────────── 4. the scan: alerts from the estimate ─────────────
def row(sym, day, side, price, created, strength=80):
    return {"symbol": sym, "timestamp": f"{day}T00:00:00Z", "signal_type": side, "price": price,
            "price_basis": "bar_close", "created_at": created, "strength_pct": strength}


HIST = {"HDB": [row("HDB", "2026-10-02", "BUY", 21.88, "2026-10-02T08:07:00Z", 74)],
        "MSR": [row("MSR", "2026-07-28", "BUY", 35.54, "2026-07-28T08:07:00Z")],
        "FPT": [row("FPT", "2026-09-18", "SELL", 65.18, "2026-09-18T08:07:00Z")],
        "GEX": [row("GEX", "2026-10-21", "BUY", 23.85, "2026-10-21T08:08:00Z", 90)]}
fail_fetch = set()


def fake_fetch(symbol):
    if symbol in fail_fetch:
        raise stv.VendorUnavailableError("site down")
    return sorted([stv.normalize(r) for r in HIST.get(symbol, [])], key=lambda s: s["day"], reverse=True)


fpt_closes = [70_000 - 120 * i for i in range(120)]
CLOSES = {"HDB": fx["closes"]["HDB"], "MSR": fx["closes"]["MSR"], "FPT": fpt_closes, "GEX": up, "SSI": up, "VNM": up}
history_calls = []


def history_fake(sym, basis):
    history_calls.append((sym, basis))
    if sym not in CLOSES:
        raise ValueError("không có giá")
    return CLOSES[sym] + ([CLOSES[sym][-1] + 100] if basis == "2026-10-22" else [])


te.history_closes = history_fake
refresh_calls = []
_orig_refresh = external.SignalStore.refresh


def refresh_spy(self, symbols, **kw):
    refresh_calls.append((list(symbols), kw.get("fresh_after"), kw.get("max_age_hours")))
    return _orig_refresh(self, symbols, fetch=fake_fetch, **{k: v for k, v in kw.items() if k != "fetch"})


external.SignalStore.refresh = refresh_spy
LIVE = {"BUY": [], "SELL": []}
stv.fetch_live_changes = lambda side: {"changes": list(LIVE[side]), "total": 30, "locked": 28, "during": True}
PRICES = {}
price_calls = []


def price_fake(code, day):
    price_calls.append(code)
    return PRICES.get(code)


intraday_module.live_price = price_fake
intraday_module.exchange = lambda code: "HOSE"

at(15, 30, day=21)                                   # the site's signals of 21/10 fetched after its close
external.SignalStore(DATA).refresh(list(HIST), fetch=fake_fetch, fresh_after="2099-01-01T00:00")


class WL:
    def get(self):
        return []


bot = T.TradingBot(SETTINGS, WL())
bot.stv_watchlist = lambda: ["MSR", "FPT", "GEX", "HDB", "VNM"]
bot.set_capital(100_000_000, book="stv")
at(10, 0, day=15)
bot.record_fill("mua", "HDB", 1000, 21_500, "2026-10-15", book="stv")
fpt_lv = te.levels(fpt_closes)
t.ok("FPT test series: the rule agrees with the site (SELL)", fpt_lv["rule"] == "SELL", fpt_lv)

FPT_STILL, FPT_UP = int(fpt_lv["buy_above"]) - 300, int(fpt_lv["buy_above"]) + 300
PRICES.update({"HDB": 21_950, "MSR": 63_300, "FPT": FPT_STILL, "GEX": 13_700})
at(9, 54)
scan1 = bot.stv_scan()
print(scan1)
t.ok("scan: a held stock past its level -> the holdings block, as the bot's estimate",
     scan1 and scan1.startswith("📡 SIÊU TÍN HIỆU · 09:54 22/10\n\n🔻 DANH MỤC CỦA ANH · tín hiệu BÁN")
     and "• HDB 1.000 cp · bot ước tính đổi sang BÁN lúc 09:54 (giá 21.950 < mức 22.018)" in scan1
     and "(ước tính của bot, chưa phải tín hiệu của trang; có thể đổi khi đóng cửa)" in scan1
     and "→ BÁN 1.000 cp" in scan1, scan1)
t.ok("scan: nothing else past its level, no estimate block", "🧮" not in scan1 and "MSR" not in scan1, scan1)
t.ok("scan: a watched stock without the site's signal fetched once (VNM), not the fresh ones",
     any(c[0] == ["VNM"] and c[1] == "2026-10-21T15:10" for c in refresh_calls), refresh_calls)
t.ok("scan: the levels from the closes up to the session before",
     sorted(history_calls) == sorted((s, "2026-10-21") for s in ("MSR", "FPT", "GEX", "HDB", "VNM")), history_calls)
t.ok("scan: the last price only for stocks with a state and levels (VNM: none)",
     set(price_calls) == {"FPT", "GEX", "HDB", "MSR"}, price_calls)
snap = te.Snapshot(DATA).read()
t.ok("scan: the estimate saved for /stvdoichieu", snap.get("at") == "2026-10-22T09:54" and snap.get("live") is True
     and snap["skipped"].get("VNM") == "chưa có tín hiệu của trang" and len(snap["rows"]) == 4, snap)
n_refresh, n_hist = len(refresh_calls), len(history_calls)
at(10, 9)
t.ok("scan: announced once", bot.stv_scan() is None)
t.ok("next scans: no level computed again, VNM not fetched again",
     len(history_calls) == n_hist and not any(c[0] == ["VNM"] for c in refresh_calls[n_refresh:]), refresh_calls[n_refresh:])

PRICES.update({"MSR": 61_800, "FPT": FPT_UP})
at(10, 15)
scan2 = bot.stv_scan()
print(scan2)
t.ok("scan: the watchlist's estimates in their own block, both sides",
     scan2 and "🧮 Bot ước tính đổi chiều trong phiên (giá đã qua mức đổi chiều; chưa chốt)" in scan2
     and "🔴 MSR MUA → BÁN · giá 61.800 < mức 61.942" in scan2
     and f"🟢 FPT BÁN → MUA · giá {T._fmt(FPT_UP)} > mức {T._fmt(fpt_lv['buy_above'])}" in scan2
     and "chi tiết: /stvdoichieu" in scan2 and "HDB" not in scan2, scan2)
LIVE["SELL"] = [{"symbol": "MSR", "side": "SELL", "day": "2026-10-22", "at": "2026-10-22T10:01", "price": 61_800,
                 "now": 61_700, "from": "BUY", "stage": "intraday"}]
at(10, 30)
t.ok("the site's own row for a change the bot announced: not announced twice", bot.stv_scan() is None)
LIVE["SELL"] = []
rep = bot.stv_scan(report=True)
t.ok("/stvquet: the estimate in the status", "Bot ước tính đổi chiều: 4 mã có giá, " in rep
     and "HDB → BÁN" in rep and "MSR → BÁN" in rep and "FPT → MUA" in rep and "/stvdoichieu" in rep, rep)

os.environ["STV_ESTIMATE"] = "0"
PRICES["GEX"] = 10_000
n_price = len(price_calls)
at(10, 45)
t.ok("STV_ESTIMATE=0: no estimate", bot.stv_scan() is None and len(price_calls) == n_price)
del os.environ["STV_ESTIMATE"]
PRICES["GEX"] = 13_700

# the price board: all last prices in one request, DNSE only for a stock the board does not know
board_asked = []
te.board_prices = lambda syms: board_asked.append(list(syms)) or {
    s_: {"price": PRICES.get(s_), "ref": None, "exchange": "HOSE"} for s_ in syms if s_ != "GEX"}
n_price = len(price_calls)
at(10, 40)
bot.stv_scan()
t.ok("the price board first: one request, DNSE for the stock missing from it only",
     len(board_asked) == 1 and sorted(board_asked[0]) == ["FPT", "GEX", "HDB", "MSR"] and price_calls[n_price:] == ["GEX"],
     (board_asked, price_calls[n_price:]))
te.board_prices = lambda syms: (_ for _ in ()).throw(RuntimeError("VCI down"))

# DNSE slow: the prices stop at their time budget, the scan goes on
te.PRICES_BUDGET_SECONDS = -1
n_price = len(price_calls)
at(10, 50)
bot.stv_scan()
snap = te.Snapshot(DATA).read()
t.ok("prices past their time budget: left out for this scan, no request",
     len(price_calls) == n_price and snap["skipped"].get("HDB") == "nguồn giá chậm, xem lượt sau" and not snap["rows"], snap)
te.PRICES_BUDGET_SECONDS = 90

# a site state not current: fetched again; when that fails, used only if the rule agrees
store = external.SignalStore(DATA)
data = store._read()
data["symbols"]["SSI"] = {"fetched": "2026-10-20T16:00",
                          "signals": [stv.normalize(row("SSI", "2026-09-11", "SELL", 20.3, "2026-09-11T08:00:00Z"))]}
store._write(data)
fail_fetch.add("SSI")
bot.stv_watchlist = lambda: ["MSR", "FPT", "GEX", "HDB", "VNM", "SSI"]
PRICES["SSI"] = 10_000
at(11, 0)
bot.stv_scan()
snap = te.Snapshot(DATA).read()
t.ok("a stale site state the rule disagrees with: left out, not guessed",
     snap["skipped"].get("SSI") == "trạng thái của trang chưa cập nhật" and not any(r["symbol"] == "SSI" for r in snap["rows"])
     and any(c[0] == ["SSI"] for c in refresh_calls), snap["skipped"])
fail_fetch.discard("SSI")
bot.stv_watchlist = lambda: ["MSR", "FPT", "GEX", "HDB", "VNM"]

# ───────────── 5. /stvdoichieu in the session ─────────────
at(11, 10)
n_price = len(price_calls)
txt = bot.stv_trend_text()
print(txt)
t.ok("/stvdoichieu in the session: past the level, then the closest; holdings tagged",
     txt.startswith("🧮 ĐỔI CHIỀU TRONG PHIÊN · 11:10 22/10") and "Đã qua mức đổi chiều (3):" in txt
     and "🔴 HDB MUA → BÁN · giá 21.950 < 22.018 · đang giữ" in txt and "🔴 MSR MUA → BÁN" in txt
     and "🟢 FPT BÁN → MUA" in txt and "không phải tín hiệu của trang" in txt and "VNM (chưa có tín hiệu của trang)" in txt, txt)
t.ok("/stvdoichieu: computed again when the last estimate is older than 5 minutes", len(price_calls) - n_price == 4)
at(11, 12)
n_price = len(price_calls)
bot.stv_trend_text()
t.ok("/stvdoichieu: a recent estimate reused (no request)", len(price_calls) == n_price)
PRICES.update({"HDB": 22_250, "MSR": 63_300, "FPT": FPT_STILL})
at(11, 30)
txt2 = bot.stv_trend_text()
t.ok("/stvdoichieu: the closest stocks with their level and distance",
     "Sát mức, cách ≤ 2% (2):" in txt2 and "• HDB MUA · 22.250 · BÁN nếu < 22.018 (cách 1,0%) · đang giữ" in txt2
     and txt2.index("• FPT BÁN · ") < txt2.index("• HDB MUA") and "Đã qua mức" not in txt2, txt2)

# the estimate runs in the session only (the lunch break included: the last match of the morning)
n_price = len(price_calls)
at(12, 0)
bot.stv_scan()
at(15, 5)
bot.stv_scan()
t.ok("the estimate only in the session (09:00-15:00)", len(price_calls) - n_price == 4, price_calls[n_price:])

# ───────────── 6. after the close: the changes the close did not keep ─────────────
HIST["HDB"].append(row("HDB", "2026-10-22", "SELL", 21.95, "2026-10-22T08:08:00Z", 40))    # 15:08: confirmed
at(15, 20)
scan3 = bot.stv_scan()
print(scan3)
t.ok("after the close: the held stock's SELL again, now the site's final signal",
     scan3 and "• HDB 1.000 cp · trang BÁN 22/10 @ 21.950 · mạnh 40%" in scan3, scan3)
t.ok("after the close: the in-session changes the close did not keep, each named once",
     scan3 and "↩️ Đóng cửa không giữ đổi chiều trong phiên (trang vẫn như cũ): " in scan3
     and "MSR (BÁN)" in scan3 and "FPT (MUA)" in scan3 and "HDB (BÁN)" not in scan3, scan3)
at(15, 35)
t.ok("after the close: announced once", bot.stv_scan() is None)

# ───────────── 7. /stvdoichieu outside the session: the levels for the next session ─────────────
at(20, 0)
n_price = len(price_calls)
txt3 = bot.stv_trend_text()
print(txt3)
t.ok("/stvdoichieu after the close: the levels for the next session, from today's close",
     txt3.startswith("🧮 MỨC ĐỔI CHIỀU PHIÊN TỚI (theo đóng cửa 22/10)") and "nếu đóng cửa <" in txt3
     and "• HDB BÁN · đóng " in txt3 and len(price_calls) == n_price, txt3)


def history_no_today(sym, basis):
    if basis == "2026-10-22":
        raise ValueError("nguồn giá chưa có phiên 22/10")
    return history_fake(sym, basis)


te.history_closes = history_no_today
te.LevelCache(DATA).path.unlink()
txt4 = bot.stv_trend_text()
t.ok("/stvdoichieu: today's close not in the source yet -> the session before", "(theo đóng cửa 21/10)" in txt4, txt4[:80])
te.history_closes = history_fake

from vnbot import telegram_bot as tb  # noqa: E402
from vnbot.jobs import JobQueue  # noqa: E402
from vnbot.service import Watchlist  # noqa: E402
from vnbot import pages  # noqa: E402

tg = tb.TelegramBot(SETTINGS, JobQueue(SETTINGS), Watchlist(SETTINGS))
tg.trader = bot
out = []
tg.send = lambda chat, text: out.append(text)
tg._handle({"chat": {"id": 111}, "from": {"id": 111}, "text": "/stvdoichieu"})
t.ok("/stvdoichieu routed: a wait notice, then the text", len(out) == 2 and out[0].startswith("⏳")
     and out[1].startswith("🧮"), out[:1])
tg._handle({"chat": {"id": 111}, "from": {"id": 111}, "text": "/trogiup"})
t.ok("listed in /trogiup and on the web's command list", "/stvdoichieu" in out[-1]
     and any(c == "stvdoichieu" for c, _, _ in pages.COMMANDS))

external.SignalStore.refresh = _orig_refresh
t.done()
