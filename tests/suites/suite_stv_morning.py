"""Morning brief + the owner's Tài sản portfolio watched with sieutinhieu.vn's signals (offline).

The separate real account of the site (/stvkhop ...) was replaced by the Tài sản journal
(vnbot.taisan, /ts...): prices as the owner enters them, no fee or tax taken off."""
import os
import sys
import tempfile
import time
import dataclasses
import shutil
from datetime import datetime
from pathlib import Path

import _common  # noqa: E402,F401 - clean env, no .env, offline, temp folder, repo on sys.path
DATA = Path(tempfile.mkdtemp(prefix="stv2-"))
os.environ.update({"STV_MARKET": "0", "DATA_DIR": str(DATA), "TRADE_MODE": "paper", "SIEUTINHIEU": "1", "STV_SLOTS": "5",
                   "TELEGRAM_TOKEN": "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawQ", "TELEGRAM_ALLOWED_IDS": "111",
                   "TELEGRAM_REPORT_CHAT_IDS": "-100500",
                   "TRADINGAGENTS_PARAMS_PATH": str(DATA / "quant_params.json")})
from vnbot import settings as settings_module  # noqa: E402

SETTINGS = settings_module.load()
import pandas as pd  # noqa: E402
from tradingagents.market import vn_rules  # noqa: E402
from tradingagents.dataflows.vendors import sieutinhieu as stv  # noqa: E402
from tradingagents.dataflows.vendors import http  # noqa: E402
from tradingagents.trading.ledger import Bar  # noqa: E402
from vnbot import external  # noqa: E402
import vnbot.trader as T  # noqa: E402

FAILS, PASSES = [], 0


def check(name, cond, info=""):
    global PASSES
    if cond:
        PASSES += 1
    else:
        FAILS.append(name)
        print("FAIL:", name, info)


def row(sym, day, side, price, created, strength=80, plan=True):
    r = {"symbol": sym, "timestamp": f"{day}T00:00:00Z", "signal_type": side, "price": price,
         "price_basis": "bar_close", "created_at": created, "strength_pct": strength}
    if plan and side == "BUY":
        r["plan"] = {"entry_low": round(price * 0.99, 1), "entry_high": round(price * 1.01, 1),
                     "tp1": round(price * 1.09, 1), "sl": round(price * 0.94, 1)}
    return r


# ───────────── 1. free market list ─────────────
class Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


calls = []


def fake_request(method, url, **kw):
    calls.append((url, kw.get("params"), kw.get("headers")))
    side = (kw.get("params") or {}).get("signal_type", "BUY")
    if side == "BUY":
        return Resp(200, {"signals": [row("MSN", "2026-10-21", "BUY", 73.7, "2026-10-21T08:07:01Z", 60),
                                      row("GEX", "2026-10-21", "BUY", 23.85, "2026-10-21T08:08:38Z", 90)],
                          "total": 109, "locked_count": 107, "is_teaser": True, "session_confirmed": True})
    return Resp(200, {"signals": [row("VIC", "2026-10-21", "SELL", 150.0, "2026-10-21T08:09:00Z", 20)],
                      "total": 61, "locked_count": 60, "is_teaser": True, "session_confirmed": True})


http.request = fake_request
stv.MIN_INTERVAL_SECONDS = 0
m = stv.fetch_market_changes("BUY")
check("teaser parsed: shown, total, locked", len(m["signals"]) == 2 and m["total"] == 109 and m["locked"] == 107, m)
check("plan scaled to VND", m["signals"][1]["entry_high"] == 24100 and m["signals"][1]["sl"] == 22400, m["signals"][1])
check("market list asks the trend endpoint once, honest headers",
      calls[-1][0].endswith("/today-trend-changes") and "Origin" not in calls[-1][2], calls[-1])

store = external.SignalStore(DATA)
NOW = {"t": datetime(2026, 10, 21, 10, 0, tzinfo=vn_rules.VN_TZ)}
external._now = lambda: NOW["t"]
n0 = len(calls)
check("not read in the morning (would be the new day's list)", store.refresh_market("2026-10-21") == "" and len(calls) == n0)
NOW["t"] = datetime(2026, 10, 21, 15, 30, tzinfo=vn_rules.VN_TZ)
store.refresh_market("2026-10-21")
check("read after the close and saved", store.market("BUY", "2026-10-21")["locked"] == 107
      and store.market("SELL", "2026-10-21")["signals"][0]["symbol"] == "VIC")
n1 = len(calls)
store.refresh_market("2026-10-21")
check("not read twice", len(calls) == n1)
check("other session: none", store.market("BUY", "2026-10-20") is None)
check("rows of the free list kept per stock (their plans)",
      store.listed_state("MSN", "2026-10-21")["sl"] == 69_300 and store.listed_state("MSN", "2026-10-20") is None)
store_b = external.SignalStore(Path(tempfile.mkdtemp(prefix="stv2b-")))


def fetch_unconfirmed(side):
    return {"signals": [stv.normalize(row("MSN", "2026-10-21", side, 73.7, "2026-10-21T08:07:01Z"))],
            "total": 5, "locked": 4, "teaser": True, "confirmed": False}


err = store_b.refresh_market("2026-10-21", fetch=fetch_unconfirmed)
check("unconfirmed list not saved", store_b.market("BUY", "2026-10-21") is None and "chưa chốt" in err
      and store_b.market_missing("2026-10-21"), err)


def fetch_old(side):
    return {"signals": [stv.normalize(row("MSN", "2026-10-20", side, 73.7, "2026-10-20T08:07:01Z"))],
            "total": 5, "locked": 4, "teaser": True, "confirmed": True}


err = store_b.refresh_market("2026-10-21", fetch=fetch_old)
check("the session before's rows under today: not saved", store_b.market("BUY", "2026-10-21") is None and err, err)


def fetch_none(side):
    return {"signals": [], "total": 0, "locked": 0, "teaser": True, "confirmed": True}


store_b.refresh_market("2026-10-21", fetch=fetch_none)
check("confirmed empty session saved (0 changes)", store_b.market("BUY", "2026-10-21") == {
    "day": "2026-10-21", "fetched": "2026-10-21T15:30", "total": 0, "locked": 0, "signals": []}
      and not store_b.market_missing("2026-10-21"))


def fetch_refused(side):
    raise stv.Refused("HTTP 403")


store_c = external.SignalStore(Path(tempfile.mkdtemp(prefix="stv2c-")))
err = store_c.refresh_market("2026-10-21", fetch=fetch_refused)
check("a refusal of the market list is kept across restarts",
      float(store_c._read().get("refused_until") or 0) > time.time() + 3600 and err, err)
stv._refused_until = 0.0

# ───────────── 2. the separate real account ─────────────
HIST = {"GEX": [row("GEX", "2026-10-21", "BUY", 23.85, "2026-10-21T08:08:38Z", 90)],
        "HPG": [row("HPG", "2026-10-12", "BUY", 20.7, "2026-10-12T08:00:00Z"),
                row("HPG", "2026-10-21", "SELL", 21.0, "2026-10-21T08:00:00Z", 15)],
        "FPT": [row("FPT", "2026-10-09", "BUY", 100.0, "2026-10-09T08:00:00Z")],
        "VNM": [row("VNM", "2026-10-21", "BUY", 60.0, "2026-10-21T08:00:00Z", 70)],
        "SSB": [row("SSB", "2026-10-14", "BUY", 20.0, "2026-10-14T08:00:00Z")]}


def fake_fetch(symbol):
    return sorted([stv.normalize(r) for r in HIST.get(symbol, [])], key=lambda s: s["day"], reverse=True)


store.refresh(list(HIST), fetch=fake_fetch, fresh_after="2099-01-01T00:00")


class WL:
    def get(self):
        return ["GEX", "HPG", "FPT", "VNM", "SSB"]


bot = T.TradingBot(SETTINGS, WL())
bot.universe = lambda: ["GEX", "HPG", "FPT", "VNM", "SSB"]
# the Siêu tín hiệu watchlist is its own (not the bot's watchlist nor VN30)
stw = external.Watchlist(DATA)
check("watchlist: the default 60 until changed", stw.get() == list(external.DEFAULT_WATCHLIST)
      and len(stw.get()) == 60 and not stw.is_custom() and bot.stv_watchlist() == stw.get())
os.environ["STV_WATCHLIST"] = "gex, hpg FPT"
check("watchlist: STV_WATCHLIST when set (commas or spaces)", stw.get() == ["GEX", "HPG", "FPT"]
      and stw.source() == "từ STV_WATCHLIST")
os.environ["STV_WATCHLIST"] = "GEX,???"
check("watchlist: a bad STV_WATCHLIST falls back to the default, and says so",
      stw.get() == list(external.DEFAULT_WATCHLIST) and stw.source() == "mặc định")
del os.environ["STV_WATCHLIST"]
try:
    stw.add(["HAG"])
    check("watchlist: adding to the full default list refused", False)
except ValueError as exc:
    check("watchlist: adding to the full default list says how", "60/60" in str(exc) and "/stvtheodoi bo" in str(exc), str(exc))
import tradingagents.dataflows.vendors.vci as vci_module  # noqa: E402
_listing = vci_module.fetch_listing
vci_module.fetch_listing = lambda: [{"symbol": c} for c in list(external.DEFAULT_WATCHLIST) + ["HAG", "KBC", "DIG"]]
try:
    stw.set(["GEX", "XYZQ"])
    check("watchlist: a code the listing does not know refused", False)
except ValueError as exc:
    check("watchlist: a code the listing does not know refused", "Không có trên sàn: XYZQ" in str(exc) and not stw.is_custom(), str(exc))
check("watchlist: set", stw.set(["gex", "HPG", "GEX", "FPT", "VNM", "SSB"]) == ["GEX", "HPG", "FPT", "VNM", "SSB"]
      and stw.is_custom())
check("watchlist: add/remove report what changed", stw.add(["HAG", "GEX"]) == (["GEX", "HPG", "FPT", "VNM", "SSB", "HAG"], ["HAG"])
      and stw.remove(["HAG", "KBC"]) == (["GEX", "HPG", "FPT", "VNM", "SSB"], ["HAG"]))
try:
    stw.set([f"A{i:02d}" for i in range(61)])
    check("watchlist: more than 60 refused", False)
except ValueError as exc:
    check("watchlist: more than 60 refused", "tối đa 60" in str(exc), str(exc))
try:
    stw.add(["???"])
    check("watchlist: a bad code refused", False)
except ValueError as exc:
    check("watchlist: a bad code refused, list unchanged", "Mã không hợp lệ" in str(exc) and len(stw.get()) == 5)
from vnbot.service import Watchlist as BotWatchlist  # noqa: E402
bot_wl = BotWatchlist(SETTINGS)
bot_wl.set(["VCB"])
stw.add(["MWG"])
check("watchlist: separate from the bot's /theodoi (other file, untouched)",
      bot_wl.get() == ["VCB"] and "MWG" in stw.get() and stw.path.parent.name == "external", (bot_wl.get(), stw.get()))
stw.remove(["MWG"])
vci_module.fetch_listing = _listing
check("real('stv') is the Tài sản view", bot.real("stv").meta.get("taisan") and bot._book_path("real").name == "real.json")
print("  ", bot.taisan.handle("/tsnap", "500000000 2026-10-13"))
msg = bot.taisan.handle("/tsmua", "HPG 2000 20800 2026-10-13")
check("buy takes the site's cut-loss and target", "19.500" in msg and "22.600" in msg, msg)
pos = bot.real("stv").positions["HPG"]
check("levels from the site's plan", pos.stop == 19_500 and pos.target1 == 22_600, (pos.stop, pos.target1))
bot.taisan.handle("/tsmua", "FPT 1000 101000 2026-10-13")
bot.taisan.handle("/tsmua", "SSB 1000 20000 2026-10-15")
check("main real account untouched", not bot.real("real").positions and not bot.real_path.exists())
msg = bot.taisan.handle("/tsthuong", "FPT 150 2026-10-16")
check("bonus shares on the Tài sản portfolio", bot.real("stv").positions["FPT"].qty == 1150, msg)

SERIES = {
    "VNINDEX": [("2026-10-20", 1700, 1700, 1700, 1700), ("2026-10-21", 1700, 1740, 1695, 1738)],
    "GEX": [("2026-10-20", 22900, 22900, 22350, 22750), ("2026-10-21", 23100, 24100, 23000, 23850)],
    "HPG": [("2026-10-20", 21000, 21200, 20900, 21100), ("2026-10-21", 21100, 21200, 20900, 21000)],
    "FPT": [("2026-10-20", 99000, 99500, 93000, 94000), ("2026-10-21", 94000, 94500, 93000, 93500)],
    "VNM": [("2026-10-20", 59000, 59500, 58500, 59000), ("2026-10-21", 59000, 60500, 58800, 60000)],
    "SSB": [("2026-10-20", 20500, 21900, 20400, 21500), ("2026-10-21", 21500, 22000, 21400, 21800)],
}


class FakeMarket:
    def __init__(self, as_of):
        self.as_of = as_of

    def _rows(self, s, day):
        return [r for r in SERIES.get(s, []) if r[0] <= day]

    def sessions(self, s):
        return [r[0] for r in SERIES[s]]

    def frame(self, s, day, required=False):
        rows = self._rows(s, day)
        return None if not rows else pd.DataFrame({"Date": pd.to_datetime([r[0] for r in rows]),
                                                   "Close": [r[4] for r in rows]})

    def exchange(self, s):
        return "HOSE"

    def close(self, s, day):
        rows = self._rows(s, day)
        return float(rows[-1][4]) if rows else None

    def bar(self, s, day, required=False):
        rows = self._rows(s, day)
        if not rows or rows[-1][0] != day:
            return None
        _, o, h, low, c = rows[-1]
        return Bar(day, o, h, low, c, rows[-2][4] if len(rows) > 1 else None, "HOSE", 1e6)


T._Market = FakeMarket
SSB_stop = bot.real("stv").positions["SSB"].stop
lines = bot._stv_real_lines("2026-10-21", store, FakeMarket("2026-10-21"))
text = "\n".join(lines)
print(text)
hpg = bot.real("stv").positions["HPG"]
check("P&L at the price as entered (no fee, no tax taken off)", hpg.avg_price == 20_800, hpg.avg_price)
check("holdings: one per bullet with the P&L, levels, then the advice",
      f"• HPG 2.000 cp · vốn 20.800 → 21.000 · +400.000 ({external.pct(21_000 / 20_800 * 100 - 100)})\n"
      "   cắt lỗ 19.500 · mục tiêu 22.600\n   🔴 Trang BÁN 21/10 @ 21.000 → BÁN 2.000 cp phiên tới" in text, text)
check("close under the site's cut-loss -> stop line", "   ⛔ Đóng cửa 93.500 ≤ cắt lỗ 94.000 → BÁN 1.150 cp" in text, text)
check("target reached -> 🎯", "   🎯 Chạm mục tiêu 21.800 (cao nhất 22.000) → cân nhắc chốt lời" in text, (SSB_stop, text))
check("total assets and P&L at the top", text.splitlines()[0] == "💼 TÀI SẢN CỦA ANH"
      and text.splitlines()[1].startswith("Tổng tài sản ") and "theo giá đóng cửa 21/10" in text.splitlines()[1]
      and "phí bán" not in text, text)
check("no buy suggested: the owner buys", "🟢" not in text and "cp · giá ≤" not in text and "phần vốn" not in text, text)
check("short lines (phone): none over 90 characters", max(len(line) for line in text.splitlines()) <= 90,
      max(text.splitlines(), key=len))


def fetch_locked(side):
    return {"signals": [], "total": 109, "locked": 107, "teaser": True, "confirmed": True}


store_d = external.SignalStore(Path(tempfile.mkdtemp(prefix="stv2e-")))
err = store_d.refresh_market("2026-10-21", fetch=fetch_locked)
check("all rows locked: not saved as today's (could be yesterday's counts)", store_d.market_missing("2026-10-21") and err, err)

# a stock bought from the free market list (not followed) gets the site's plan; messages name /stv* commands
D2 = Path(tempfile.mkdtemp(prefix="stv2d-"))
(D2 / "external").mkdir()
shutil.copy(DATA / "external" / "sieutinhieu.json", D2 / "external" / "sieutinhieu.json")
bot2 = T.TradingBot(dataclasses.replace(SETTINGS, data_dir=D2), WL())
bot2.universe = lambda: ["GEX", "HPG", "FPT", "VNM", "SSB"]
bot2.taisan.handle("/tsnap", "100000000 2026-10-21")
msg2 = bot2.taisan.handle("/tsmua", "MSN 200 73900 2026-10-21")
p2 = bot2.real("stv").positions["MSN"]
check("teaser stock: the site's cut-loss/target from the market list", p2.stop == 69_300 and p2.target1 == 80_300, (msg2, p2.stop, p2.target1))
msg_odd = bot2.taisan.handle("/tsmua", "MSN 50 73800 2026-10-21")
check("an odd lot (50 cp) is recorded, added to the holding", bot2.real("stv").positions["MSN"].qty == 250
      and "MUA MSN 50 cp" in msg_odd, msg_odd)
bot2.set_capital(10_000_000)
bot2.record_fill("mua", "VCB", 30, 56_000, "2026-10-21")
check("the main real account takes odd lots too (/khop)", bot2.real("real").positions["VCB"].qty == 30)
try:
    bot2.record_fill("mua", "VCB", 0, 56_000, "2026-10-21")
    check("0 shares still refused", False)
except ValueError as exc:
    check("0 shares still refused", "số nguyên dương" in str(exc), str(exc))
big = bot2.taisan.handle("/tsmua", "MSN 100000 73900 2026-10-21")
check("a buy over the cash is recorded with a warning (the owner may have deposited)",
      "⚠️ Tiền mặt âm" in big and "/tsnap" in big, big)
undo = bot2.taisan.handle("/tshuy", "")
check("/tshuy undoes it", "Đã huỷ" in undo and bot2.real("stv").positions["MSN"].qty == 250, undo)
try:
    bot2.taisan.handle("/tsmua", "MSN 100")
    check("usage names /tsmua", False)
except ValueError as exc:
    check("usage names /tsmua", "Dùng: /tsmua" in str(exc), str(exc))
try:
    bot2.record_fill("mua", "MSN", 100, 73_900, "2026-10-21", book="stv")
    check("the old account is gone: /khop on it refused", False)
except ValueError as exc:
    check("the old account is gone: /khop on it refused, pointing to /ts", "/tsmua" in str(exc), str(exc))
check("main account's messages unchanged", "/von" in T._real_adjustment_lines(
    [{"kind": "basis", "symbol": "X", "ratio": 1.2, "qty": 100, "free_estimate": 20}])[0])

# the evening run marks first: its corporate-action note is kept for the morning brief
bot2.taisan.handle("/tsmua", "SSB 1000 21000 2026-10-21")
bot2._stv_real_lines("2026-10-21", bot2_store := external.SignalStore(D2), FakeMarket("2026-10-21"))
SERIES_ADJ = {k: list(v) for k, v in SERIES.items()}
SERIES_ADJ["SSB"] = [("2026-10-20", 17083, 18250, 17000, 17917), ("2026-10-21", 17917, 18333, 17833, 18167),
                     ("2026-10-22", 18200, 18400, 18100, 18300)]
SERIES_ADJ["VNINDEX"] = SERIES["VNINDEX"] + [("2026-10-22", 1738, 1745, 1730, 1740)]


class FakeMarketAdj(FakeMarket):
    def _rows(self, s, day):
        return [r for r in SERIES_ADJ.get(s, []) if r[0] <= day]

    def sessions(self, s):
        return [r[0] for r in SERIES_ADJ[s]]


_, evening_notes = bot2._mark_real(bot2.real("stv"), "2026-10-22", FakeMarketAdj("2026-10-22"), book="stv")
kept = bot2.real("stv").meta.get("notes") or {}
check("evening mark: note kept in the book", evening_notes and kept.get("day") == "2026-10-22"
      and kept["notes"][0]["symbol"] == "SSB", (evening_notes, kept))
morning = "\n".join(bot2._stv_real_lines("2026-10-22", bot2_store, FakeMarketAdj("2026-10-22")))
check("morning brief shows it once, with the /ts* commands", morning.count("⚖️ SSB") == 1 and "/tsthuong SSB" in morning
      and "/tscotuc" in morning and " /von" not in morning, morning)
check("the evening mark rebased the Tài sản levels", bot2.real("stv").positions["SSB"].last_date == "2026-10-22")

# ───────────── 3. the morning brief + Zoom first ─────────────
bot.session_day = lambda: "2026-10-21"
T._now_vn = lambda: datetime(2026, 10, 22, 8, 30, tzinfo=vn_rules.VN_TZ)
brief = bot.stv_morning()
print(brief)
check("brief: header", brief.startswith("☀️ SIÊU TÍN HIỆU · sáng 22/10\nTín hiệu phiên 21/10 của sieutinhieu.vn"), brief[:200])
check("brief: market lists with locked counts and the site's plan under each BUY",
      "📊 TOÀN THỊ TRƯỜNG\nMUA: 109 mã đổi tín hiệu · xem miễn phí 2 · khoá 107 (cần Premium)\n"
      "🟢 MSN 73.700 · mạnh 60%\n   vào 73.000–74.400 · mục tiêu 80.300 · cắt lỗ 69.300\n🟢 GEX 23.850 · mạnh 90%\n"
      "   vào 23.600–24.100 · mục tiêu 26.000 · cắt lỗ 22.400\nBÁN: 61 mã đổi tín hiệu" in brief, brief[:800])
check("brief: its own watchlist, new signals and states", "👀 WATCHLIST SIÊU TÍN HIỆU · 5 mã" in brief
      and "Tín hiệu mới phiên 21/10 (" in brief and "Đang ở trạng thái MUA (4/5 mã):\nFPT, SSB, GEX, VNM" in brief, brief)
check("brief: the owner's portfolio, no buy order, 'anh tự quyết định mua'",
      "💼 TÀI SẢN CỦA ANH" in brief and "cp · giá ≤" not in brief
      and "Anh tự quyết định mua; mua xong báo /tsmua MÃ KL GIÁ" in brief, brief)
check("brief: sections separated by blank lines", brief.count("\n\n") >= 4)
check("brief: short lines (phone)", max(len(line) for line in brief.splitlines()) <= 100, max(brief.splitlines(), key=len))
before_own = stw.get()
stw.remove(["VNM"])                               # still in the bot's own watchlist (WL)
brief_own = bot.stv_morning()
check("brief: only its own watchlist (VNM dropped there, still in the bot's)",
      "VNM" not in brief_own and "👀 WATCHLIST SIÊU TÍN HIỆU · 4 mã" in brief_own, brief_own)
stw.set(before_own)
wsec = brief[brief.index("👀 WATCHLIST"):]
check("brief: new signals, BUYs strongest first (with the plan) then SELLs",
      wsec.index("🟢 GEX 23.850 · mạnh 90%\n   vào 23.600") < wsec.index("🟢 VNM 60.000") < wsec.index("🔴 HPG 21.000"), wsec)

# /stvdanhmuc: the comparison book; /ts: the owner's portfolio; /stv: the overview; /danhmuc: a pointer
pf = bot.stv_portfolio_text()
print(pf)
check("/stvdanhmuc: the comparison book, and where the owner's money is", pf.startswith("📒 SỔ ẢO SO SÁNH SIÊU TÍN HIỆU")
      and "/ts" in pf and "HPG 2.000" not in pf, pf)
import vnbot.intraday as intraday_module  # noqa: E402

_live = intraday_module.live_price
_vn_now0 = vn_rules.now_vn
intraday_module.live_price = lambda code, day: {"HPG": 21_500, "FPT": None}.get(code)
T._now_vn = lambda: datetime(2026, 10, 22, 10, 45, tzinfo=vn_rules.VN_TZ)
vn_rules.now_vn = lambda: datetime(2026, 10, 22, 10, 45, tzinfo=vn_rules.VN_TZ)
bot.taisan._forget_live()
ts_live = bot.taisan.overview_text()
print(ts_live)
check("/ts in the session: the last matched price, the close where there is none",
      ts_live.startswith("💼 TÀI SẢN · khớp 10:45") and "• HPG 2.000 cp · vốn 20.800 → 21.500 · +1.400.000" in ts_live
      and "• FPT 1.150 cp · vốn 87.826 → 93.500" in ts_live, ts_live)
ov_live = bot.stv_text()
check("/stv in the session: the Tài sản line at the matched prices", "💼 Tài sản của anh: 3 mã · tổng " in ov_live
      and "giá khớp 10:45" in ov_live, ov_live)
intraday_module.live_price = _live
vn_rules.now_vn = _vn_now0
T._now_vn = lambda: datetime(2026, 10, 22, 8, 30, tzinfo=vn_rules.VN_TZ)
ov = bot.stv_text()
print(ov)
check("/stv: overview with every book's return, one per line", ov.startswith("📡 SIÊU TÍN HIỆU · tổng quan")
      and "\n• Siêu tín hiệu " in ov and "\n• Phòng thủ " in ov and "\n• Quant Engine " in ov and "\n• Động lượng " in ov
      and "👀 Watchlist: 5 mã (đã chỉnh) · quét danh mục 15 phút/lần" in ov, ov)
bot_pf = bot.portfolio_text()
check("bot's /danhmuc: no Tài sản holdings, a pointer instead",
      "💼 Danh mục tiền thật của anh: /ts" in bot_pf and "HPG 2.000" not in bot_pf
      and "Thật (Siêu tín hiệu)" not in bot_pf, bot_pf)

from vnbot import telegram_bot as tb  # noqa: E402
from vnbot.jobs import JobQueue  # noqa: E402
from vnbot.service import Watchlist, DailyScheduler  # noqa: E402

tg = tb.TelegramBot(SETTINGS, JobQueue(SETTINGS), Watchlist(SETTINGS))
sent = []
tg.send = lambda chat, text: sent.append((chat, text))


class FakeZoom:
    notify_on = True

    def __init__(self, ok=True):
        self.got, self.ok = [], ok

    def send(self, text, on_fail=None):
        self.got.append(text)
        if not self.ok and on_fail:
            on_fail()


z = FakeZoom()
tg.zoom = z
tg.zoom_first("hello zoom")
check("Zoom first when Zoom can deliver", z.got == ["hello zoom"] and not sent, (z.got, sent))
tg.zoom = None
tg.zoom_first("no zoom")
check("falls back to the Telegram report chats", sent == [(-100500, "no zoom")], sent)
tg.zoom = FakeZoom(ok=False)
tg.zoom_first("zoom fails")
check("Zoom refuses -> Telegram", sent[-1] == (-100500, "zoom fails"), sent)
tg.zoom = None

from vnbot import zoom as zm  # noqa: E402


class ZResp:
    def __init__(self, code):
        self.status_code, self.text = code, "x"


for code, want in ((500, True), (200, False)):
    fails = []
    zc = zm.ZoomChannel(zm.ZoomConfig(webhook_url="https://hooks.example/x", webhook_token="t"),
                        state_path=DATA / f"zoom{code}.json", post=lambda *a, _c=code, **k: ZResp(_c))
    zc.send("brief", on_fail=lambda _f=fails: _f.append(1))
    for _ in range(50):
        if fails or zc.queue.unfinished_tasks == 0 and zc.queue.empty():
            time.sleep(0.1)
            break
        time.sleep(0.05)
    check(f"real ZoomChannel: webhook {code} -> fallback {want}", bool(fails) == want, fails)

# the brief on stale prices: warned, no buys
bot.session_day = lambda: "2026-10-20"
stale = bot.stv_morning()
check("stale session: warned", "⚠️ Nguồn giá chưa có phiên 21/10" in stale, stale)
bot.session_day = lambda: "2026-10-21"

# ───────────── 4. scheduler ─────────────
got = []


class BotStub:
    def __init__(self):
        self.n = 0

    def stv_morning(self):
        self.n += 1
        return "BRIEF"


clock = {"t": datetime(2026, 10, 22, 8, 20, tzinfo=vn_rules.VN_TZ)}
svc_vn_now = vn_rules.now_vn
vn_rules.now_vn = lambda: clock["t"]
sched = DailyScheduler(SETTINGS, JobQueue(SETTINGS), Watchlist(SETTINGS), bot=BotStub(), notify=lambda t: got.append(("all", t)),
                       zoom_notify=lambda t: got.append(("zoom", t)))
check("not due before the time", sched._stv_due() is None)
clock["t"] = datetime(2026, 10, 22, 8, 31, tzinfo=vn_rules.VN_TZ)
day = sched._stv_due()
check("due at the time on a session day", day == "2026-10-22")
sched._stv_morning(day)
check("sent via zoom_notify, stamped", got == [("zoom", "BRIEF")] and sched._stv_due() is None, got)
clock["t"] = datetime(2026, 9, 2, 8, 31, tzinfo=vn_rules.VN_TZ)
check("holiday: not due", sched._stv_due() is None)
clock["t"] = datetime(2026, 10, 23, 12, 0, tzinfo=vn_rules.VN_TZ)
check("too late (bot was down): skipped", sched._stv_due() is None)
# the free market list: retried while missing
sched2 = DailyScheduler(SETTINGS, JobQueue(SETTINGS), Watchlist(SETTINGS), bot=BotStub())
mkt_calls = []
orig_rm = external.SignalStore.refresh_market
external.SignalStore.refresh_market = lambda self, day, fetch=None: mkt_calls.append(day) or "chưa chốt"
clock["t"] = datetime(2026, 10, 22, 15, 0, tzinfo=vn_rules.VN_TZ)
check("market list: not before 15:15", sched2._stv_market_due() is None)
clock["t"] = datetime(2026, 10, 22, 15, 20, tzinfo=vn_rules.VN_TZ)
check("market list: due while missing", sched2._stv_market_due() == "2026-10-22")
for _ in range(10):
    sched2._stv_market("2026-10-22")
check("market list: at most 8 tries a day", sched2._stv_market_due() is None and len(mkt_calls) == 10
      and sched2._stv_market_tries == 10)
clock["t"] = datetime(2026, 10, 21, 16, 0, tzinfo=vn_rules.VN_TZ)
check("market list: already saved -> not due", sched2._stv_market_due() is None)
clock["t"] = datetime(2026, 10, 22, 21, 5, tzinfo=vn_rules.VN_TZ)
sched2._stv_market_day = ""
check("market list: not after 21:00", sched2._stv_market_due() is None)
external.SignalStore.refresh_market = orig_rm


class BadBot:
    def stv_morning(self):
        raise RuntimeError("down")


sched3 = DailyScheduler(SETTINGS, JobQueue(SETTINGS), Watchlist(SETTINGS), bot=BadBot(), zoom_notify=lambda t: got.append(("z3", t)))
sched3._stv_stamp = DATA / "stamp3.txt"
sched3._stv_morning("2026-10-22")
sched3._stv_morning("2026-10-22")
check("two failures: retry later, nothing sent", sched3._stv_tries == 2 and not [g for g in got if g[0] == "z3"])
sched3._stv_morning("2026-10-23")
check("a new day starts the tries again", sched3._stv_tries == 1, sched3._stv_tries)
vn_rules.now_vn = svc_vn_now

# ───────────── 5. telegram commands ─────────────
calls_t = []


class TStub:
    def set_capital(self, cash, book="real"):
        calls_t.append(("von", cash, book))
        return "ok"

    def record_fill(self, side, code, qty, price, day=None, book="real"):
        calls_t.append(("khop", side, code, qty, price, book))
        return "ok"

    def record_free_shares(self, code, qty, day=None, book="real"):
        calls_t.append(("thuong", code, qty, book))
        return "ok"

    def stv_morning(self):
        calls_t.append(("sang",))
        return "brief"

    def stv_portfolio_text(self):
        calls_t.append(("danhmuc",))
        return "pf"

    def stv_scan(self, report=False):
        calls_t.append(("quet", report))
        return "scan"

    def stv_existing(self, code, qty, price):
        calls_t.append(("cosan", code, qty, price))
        return "ok"

    def stv_settled(self, codes):
        calls_t.append(("dave", codes))
        return "ok"


tg.trader = TStub()


def msg(text):
    return {"chat": {"id": 111}, "from": {"id": 111}, "text": text}


n_sent = len(sent)
for t in ("/stvvon 200.000.000", "/stvkhop mua GEX 800 23.900", "/stvkhop ban HPG 1150 21000", "/stvthuong FPT 150",
          "/stvcosan GEX 100 31.347", "/stvxoa danhmuc", "/stvsang", "/stvdanhmuc"):
    tg._handle(msg(t))
moved = [text for _, text in sent[n_sent:] if text.startswith("⚠️ Danh mục tiền thật đã chuyển sang Tài sản")]
check("the old account's commands point to /ts, nothing recorded", len(moved) == 6
      and calls_t == [("sang",), ("danhmuc",)] and "/tsmua" in moved[1] and "/tsthuong" in moved[3], (calls_t, moved))
tg._handle({"chat": {"id": 222}, "from": {"id": 222}, "text": "/stvsang"})
tg._handle({"chat": {"id": 222}, "from": {"id": 222}, "text": "/stvtheodoi dat GEX"})
tg._handle({"chat": {"id": 222}, "from": {"id": 222}, "text": "/tsnap 1000000000"})
check("strangers cannot use them", len(calls_t) == 2 and stw.get() == ["GEX", "HPG", "FPT", "VNM", "SSB"])

# /stvtheodoi: its own watchlist
five = stw.get()
tg._handle(msg("/stvtheodoi"))
check("/stvtheodoi shows it, ten a line, and how to change it",
      "👀 WATCHLIST SIÊU TÍN HIỆU · 5/60 mã (đã chỉnh)" in sent[-1][1] and "GEX, HPG, FPT, VNM, SSB" in sent[-1][1]
      and "/stvtheodoi them" in sent[-1][1], sent[-1])
tg._handle(msg("/stvtheodoi them HAG, KBC"))
check("them: adds", "✅ Đã thêm: HAG, KBC" in sent[-1][1] and stw.get() == five + ["HAG", "KBC"], sent[-1])
tg._handle(msg("/stvtheodoi +DIG"))
tg._handle(msg("/stvtheodoi bo HAG"))
tg._handle(msg("/stvtheodoi -KBC,DIG"))
check("+ / bo / -: add and remove", stw.get() == five, stw.get())
tg._handle(msg("/stvtheodoi them"))
check("them without a code: how to use it", sent[-1][1].startswith("⚠️ Ghi kèm mã"), sent[-1])
for typo in ("/stvtheodoi bo,NVL", "/stvtheodoi them,HAG", "/stvtheodoi xem"):
    tg._handle(msg(typo))
check("a comma after the word still reads as the word; never a new list",
      stw.get() == five + ["HAG"] and sent[-1][1].startswith("👀 WATCHLIST"), (stw.get(), sent[-1]))
stw.set(five)
tg._handle(msg("/stvtheodoi GEX HPG"))
check("a bare list is refused (needs 'dat')", stw.get() == five and "Không hiểu" in sent[-1][1], sent[-1])
tg._handle(msg("/stvtheodoi macdinh"))
check("macdinh: the default 60", stw.get() == list(external.DEFAULT_WATCHLIST) and "60/60 mã" in sent[-1][1], sent[-1])
tg._handle(msg("/stvtheodoi hoantac"))
check("hoantac: the list before, back", stw.get() == five, stw.get())
tg._handle(msg("/stvtheodoi dat GEX HPG"))
check("dat: a whole new list", stw.get() == ["GEX", "HPG"], stw.get())
tg._handle(msg("/stvtheodoi hoan tac"))
check("'hoan tac' in two words works too", stw.get() == five, stw.get())
tg._handle(msg("/stvtheodoi macdinh"))
tg._handle(msg("/stvtheodoi macdinh"))                 # sent twice by mistake
tg._handle(msg("/stvtheodoi hoantac"))
check("macdinh twice, then hoantac: the custom list is still recoverable", stw.get() == five, stw.get())
tg._handle(msg("/stvtheodoi them GEX"))
check("nothing changed: no 'hoàn tác' offered", "Hoàn tác" not in sent[-1][1] and "đã có" in sent[-1][1], sent[-1])

# ───────────── 5b. the comparison book does not buy the owner's holdings ─────────────
seen_syms, refreshed = [], []
orig_ref, orig_rm2 = external.SignalStore.refresh, external.SignalStore.refresh_market
external.SignalStore.refresh = lambda self, symbols, **kw: refreshed.extend(symbols) or (0, "")
external.SignalStore.refresh_market = lambda self, day, fetch=None: ""
external.Watchlist(D2).set(["GEX", "HPG"])       # MSN (held) is not in this watchlist
bot2._stv_session = lambda s, sessions, book, market, store, symbols: seen_syms.extend(symbols)
bot2._saved_signals = lambda target: (None, None, None)
bot2.watchlist = WL()
book2 = bot2.stv_book()
bot2._stv_run(["2026-10-20", "2026-10-21"], FakeMarket("2026-10-21"), "2026-10-21", book2)
external.SignalStore.refresh, external.SignalStore.refresh_market = orig_ref, orig_rm2
check("holdings fetched but not candidates of the comparison book",
      "MSN" in refreshed and seen_syms and "MSN" not in seen_syms, (refreshed, seen_syms))
# "Bot MUA nhưng trang đang BÁN": only for stocks fetched for this session, never from an old history
from tradingagents.trading.ledger import Signal  # noqa: E402

external.SignalStore.refresh = lambda self, symbols, **kw: (0, "")
external.SignalStore.refresh_market = lambda self, day, fetch=None: ""
bot2._saved_signals = lambda target: ([Signal("HPG", "BUY", 0.8, 21_000)], None, None)
st2 = external.SignalStore(D2)
data2 = st2._read()
data2["symbols"]["HPG"]["fetched"] = "2026-10-01T16:00"                 # an old fetch: no longer followed
st2._write(data2)
old_txt = bot2._stv_run(["2026-10-20", "2026-10-21"], FakeMarket("2026-10-21"), "2026-10-21", bot2.stv_book())
data2["symbols"]["HPG"]["fetched"] = "2026-10-21T16:00"                 # fetched for this session
st2._write(data2)
new_txt = bot2._stv_run(["2026-10-20", "2026-10-21"], FakeMarket("2026-10-21"), "2026-10-21", bot2.stv_book())
external.SignalStore.refresh, external.SignalStore.refresh_market = orig_ref, orig_rm2
check("bot BUY vs site SELL: not from an old history", "trang đang BÁN" not in old_txt, old_txt)
check("bot BUY vs site SELL: flagged when fetched this session", "⚠️ Bot MUA nhưng trang đang BÁN: HPG (từ 21/10)" in new_txt, new_txt)
check("candidates = its own watchlist, not the bot's (VNM, FPT, SSB of the bot's list left out)",
      set(seen_syms) == {"GEX", "HPG"}, seen_syms)

tg._handle(msg("/stvquet"))
check("/stvquet routes", calls_t[-1] == ("quet", True), calls_t[-1:])

# ───────────── 5c. the scan: holdings + watchlist (a stock in both asked once) + the live view ─────────────
_orig_refresh = external.SignalStore.refresh
asked = []


def _refresh_spy(self, symbols, **kw):
    asked.append(list(symbols))
    return _orig_refresh(self, symbols, fetch=fake_fetch, **{k: v for k, v in kw.items() if k != "fetch"})


external.SignalStore.refresh = _refresh_spy
LIVE = {"BUY": [], "SELL": []}
live_calls = []
_orig_live = stv.fetch_live_changes


def _live_spy(side):
    live_calls.append(side)
    return {"changes": list(LIVE[side]), "total": 50, "locked": 48, "during": True}


stv.fetch_live_changes = _live_spy
T._now_vn = lambda: datetime(2026, 10, 22, 10, 0, tzinfo=vn_rules.VN_TZ)
intraday_module.live_price = lambda code, day: {"HPG": 20_900}.get(code)
scan1 = bot.stv_scan()
print(scan1)
check("scan in the session: the holdings only (the watchlist's own signals come after the close)",
      asked[-1] == ["FPT", "HPG", "SSB"], asked[-1:])
check("scan: the held stock the site says SELL (🔴 Mạnh), with the price, the P&L and what to do",
      scan1 and scan1.startswith("📡 SIÊU TÍN HIỆU · 10:00 22/10\n\n🔻 TÀI SẢN CỦA ANH · tín hiệu BÁN")
      and "🔴 Mạnh · HPG 2.000 cp · trang BÁN 21/10 @ 21.000 · mạnh 15%" in scan1
      and f"Giá 20.900 (khớp 10:00) · lãi/lỗ theo giá vốn: +200.000 ({external.pct(20_900 / 20_800 * 100 - 100)})" in scan1
      and "→ BÁN 2.000 cp" in scan1 and "FPT" not in scan1 and "SSB" not in scan1, scan1)
check("scan: the alert kept for its shadow result",
      any(r["symbol"] == "HPG" and r["level"] == "sell" for r in bot.taisan.state()["shadow"]))
check("scan: the live view asked in the session", live_calls[-2:] == ["BUY", "SELL"], live_calls)
check("scan: announced once", bot.stv_scan() is None)
rep_q = bot.stv_scan(report=True)
check("/stvquet: holdings + watchlist, each stock asked once", asked[-1] == ["FPT", "HPG", "SSB", "GEX", "VNM"], asked[-1])
check("/stvquet: a status when nothing is new",
      rep_q.startswith("✅ Đã quét 5 mã lúc 10:00 (danh mục + watchlist, mã trùng hỏi 1 lần): không có tín hiệu mới chưa báo.")
      and "• HPG: BÁN từ 21/10 (đã báo)" in rep_q and "• FPT: MUA từ 09/10" in rep_q
      and "tín hiệu từng mã có sau giờ đóng cửa" in rep_q, rep_q)

# the site's live view during the session (two stocks a side for free): followed stocks only
LIVE["SELL"] = [{"symbol": "VNM", "side": "SELL", "day": "2026-10-22", "at": "2026-10-22T10:20", "price": 60_000,
                 "now": 59_800, "from": "BUY", "stage": "intraday"},
                {"symbol": "FPT", "side": "SELL", "day": "2026-10-22", "at": "2026-10-22T10:25", "price": 93_000,
                 "now": 92_500, "from": "BUY", "stage": "intraday"},
                {"symbol": "ZZZ", "side": "SELL", "day": "2026-10-22", "at": "2026-10-22T10:00", "price": 9_000,
                 "now": 9_000, "from": "BUY", "stage": "intraday"}]
LIVE["BUY"] = [{"symbol": "GEX", "side": "BUY", "day": "2026-10-22", "at": "2026-10-22T10:10", "price": 24_000,
                "now": 24_100, "from": "SELL", "stage": "intraday"}]
T._now_vn = lambda: datetime(2026, 10, 22, 10, 30, tzinfo=vn_rules.VN_TZ)
scan2 = bot.stv_scan()
print(scan2)
# 🟠 (a held stock's SELL seen in the session, not final) is not pushed since v1.5.0 (selling on it
# was no better than waiting for the close): kept for its shadow result and the 15:30 report.
check("live view: a held stock's early SELL (🟠) is kept, not pushed (INTRADAY_EARLY=0)",
      scan2 and "TÀI SẢN CỦA ANH" not in scan2 and "FPT" not in scan2
      and any(r["symbol"] == "FPT" and r["level"] == "early" for r in bot.taisan.state()["shadow"]), scan2)
check("live view: the watchlist's changes, not the unfollowed stock",
      "👀 Đổi chiều trong phiên (chưa chốt, có thể đổi khi đóng cửa)" in scan2
      and "🔴 VNM\n   MUA → BÁN lúc 10:20 · giá 59.800" in scan2 and "🟢 GEX\n   BÁN → MUA lúc 10:10 · giá 24.100" in scan2
      and "ZZZ" not in scan2 and "HPG" not in scan2 and "Premium" in scan2, scan2)
_sent = external.SellAlerts(DATA)._read()
_sent.pop("FPT", None)                       # forget it, to see it with INTRADAY_EARLY=1
external.SellAlerts(DATA).path.write_text(__import__("json").dumps(_sent), encoding="utf-8")
_settings = bot.settings
bot.settings = dataclasses.replace(bot.settings, intraday_early=True)
early_on = bot.stv_scan()
bot.settings = _settings
check("INTRADAY_EARLY=1: it goes to the holdings block as before, said not final",
      early_on and "🟠 Sớm · FPT 1.150 cp · trang BÁN trong phiên lúc 10:25" in early_on
      and "(tín hiệu trong phiên, có thể đổi khi đóng cửa)" in early_on, early_on)
check("a change says the company and the price against the reference when they are known",
      T._stock_head("🟢", "VJC", {"VJC": "Vietjet Air"}) == "🟢 VJC · Vietjet Air" and T._stock_head("🟢", "VJC", {}) == "🟢 VJC"
      and external.change(130_000, 128_400) == "+1,25%" and external.change(9_000, 10_000) == "-10,00%"
      and external.change(130_000, None) == "" and T._paren("") == "" and T._paren("+1,25%") == " (+1,25%)")
_listing = vci_module.fetch_listing
vci_module.fetch_listing = lambda: [{"symbol": "VJC", "organShortName": "Vietjet Air", "organName": "CTCP Hàng không VietJet"},
                                    {"symbol": "FPT", "organName": "CTCP FPT"}, "noise"]
check("company names from the listing: the short name, else the full one, unknown codes left out",
      external.company_names(["vjc", "FPT", "ZZZ"]) == {"VJC": "Vietjet Air", "FPT": "CTCP FPT"})
vci_module.fetch_listing = lambda: (_ for _ in ()).throw(RuntimeError("down"))
check("listing down and no copy on disk: no names, no error", isinstance(external.company_names(["VJC"]), dict))
vci_module.fetch_listing = _listing
from vnbot import trend_estimate as te_module  # noqa: E402

_lv = te_module.levels([20_000 + (i % 7) * 150 - i * 20 for i in range(80)])
_rows, _ = te_module.assess(["AAA"], {"AAA": _lv["rule"]}, {"AAA": _lv}, {"AAA": _lv["prev"] * 1.01})
_board = te_module.board_prices
te_module.board_prices = lambda syms: {"VJC": {"price": 131_000, "ref": 128_400, "exchange": "HOSE"}}
_names, _quotes = bot._stv_quotes(["VJC"], {"rows": [{"symbol": "VJC", "ref": 128_000}]})
check("the message's quotes: the board asked as it goes out (price and reference now)",
      _quotes["VJC"] == {"price": 131_000, "ref": 128_400}, _quotes)
te_module.board_prices = lambda syms: (_ for _ in ()).throw(RuntimeError("board down"))
_names, _quotes = bot._stv_quotes(["VJC"], {"rows": [{"symbol": "VJC", "ref": 128_000}]})
check("board down: the reference from the scan, the price left to the scan's", _quotes["VJC"] == {"price": None, "ref": 128_000}, _quotes)
te_module.board_prices = _board
check("each estimate row carries its reference price (the previous close)",
      _rows and _rows[0]["ref"] == _lv["prev"] and external.change(_rows[0]["price"], _rows[0]["ref"]) == "+1,00%", _rows)
check("live view: announced once", bot.stv_scan() is None)

# after the close: the day's final signals of the watchlist (and the holdings), each stock asked once
HIST["FPT"].append(row("FPT", "2026-10-22", "SELL", 92.0, "2026-10-22T08:08:00Z", 40))   # 15:08: final
HIST["VNM"].append(row("VNM", "2026-10-22", "SELL", 59.8, "2026-10-22T08:08:00Z", 35))
HIST["GEX"].append(row("GEX", "2026-10-22", "BUY", 24.2, "2026-10-22T08:08:00Z", 88))
n_live = len(live_calls)
T._now_vn = lambda: datetime(2026, 10, 22, 15, 20, tzinfo=vn_rules.VN_TZ)
scan3 = bot.stv_scan()
print(scan3)
check("after the close: holdings + watchlist, each stock asked once", asked[-1] == ["FPT", "HPG", "SSB", "GEX", "VNM"], asked[-1])
check("after the close: no live view asked", len(live_calls) == n_live)
check("after the close: the held stock's SELL again, now final (🔴)", "🔴 Mạnh · FPT 1.150 cp · trang BÁN 22/10 @ 92.000 · mạnh 40%" in scan3
      and scan3.count("FPT") == 1, scan3)
check("after the close: the watchlist's new signals with the site's plan, BUY first",
      "👀 WATCHLIST · tín hiệu mới phiên 22/10 (đã chốt sau giờ đóng cửa)" in scan3
      and scan3.index("🟢 GEX 24.200 · mạnh 88%\n   vào ") < scan3.index("🔴 VNM 59.800 · mạnh 35%")
      and "Anh tự quyết định mua" in scan3, scan3)
T._now_vn = lambda: datetime(2026, 10, 22, 15, 35, tzinfo=vn_rules.VN_TZ)
check("after the close: announced once", bot.stv_scan() is None)
T._now_vn = lambda: datetime(2026, 10, 22, 16, 15, tzinfo=vn_rules.VN_TZ)
bot.stv_scan()
check("from 16:00: the holdings only again", asked[-1] == ["FPT", "HPG", "SSB"], asked[-1])
os.environ["STV_WATCH_INTRADAY"] = "1"
T._now_vn = lambda: datetime(2026, 10, 23, 10, 0, tzinfo=vn_rules.VN_TZ)
LIVE["BUY"], LIVE["SELL"] = [], []
bot.stv_scan()
check("STV_WATCH_INTRADAY=1: the whole watchlist in the session too", asked[-1] == ["FPT", "HPG", "SSB", "GEX", "VNM"], asked[-1])
del os.environ["STV_WATCH_INTRADAY"]
alerts = external.SellAlerts(DATA)
alerts.remember([], ["FPT", "SSB"])                          # HPG sold
hpg_sig = external.SignalStore(DATA).state("HPG", "2026-10-22")
check("a stock sold is forgotten: bought again under the same SELL -> announced again",
      alerts.pending([("HPG", hpg_sig)]) == [("HPG", hpg_sig)])
for sym in ("FPT", "VNM", "GEX"):
    HIST[sym].pop()
external.SignalStore.refresh = _orig_refresh
stv.fetch_live_changes = _orig_live
intraday_module.live_price = _live

# the scheduler: every scan_minutes from 09:00 to 17:00 on session days, Zoom first
class ScanBot:
    def __init__(self):
        self.n = 0

    def stv_scan(self):
        self.n += 1
        if self.n == 2:
            raise RuntimeError("site down")
        return "SELL!" if self.n == 1 else None


scan_got = []
clock["t"] = datetime(2026, 10, 22, 8, 59, tzinfo=vn_rules.VN_TZ)
vn_rules.now_vn = lambda: clock["t"]
sched4 = DailyScheduler(SETTINGS, JobQueue(SETTINGS), Watchlist(SETTINGS), bot=ScanBot(),
                        zoom_notify=lambda t: scan_got.append(t))
check("scan: not before 09:00", not sched4._stv_scan_due())
clock["t"] = datetime(2026, 10, 22, 9, 0, tzinfo=vn_rules.VN_TZ)
check("scan: due at 09:00 on a session day", sched4._stv_scan_due())
sched4._stv_scan()
check("scan: the SELL message goes Zoom first", scan_got == ["SELL!"], scan_got)
check("scan: then not again before 15 minutes", not sched4._stv_scan_due())
sched4._stv_scan_next = 0
sched4._stv_scan()
check("scan: an error is logged, the scheduler goes on", scan_got == ["SELL!"])
clock["t"] = datetime(2026, 10, 22, 16, 59, tzinfo=vn_rules.VN_TZ)
sched4._stv_scan_next = 0
check("scan: still at 16:59 (a late close signal)", sched4._stv_scan_due())
clock["t"] = datetime(2026, 10, 22, 17, 0, tzinfo=vn_rules.VN_TZ)
check("scan: not from 17:00", not sched4._stv_scan_due())
clock["t"] = datetime(2026, 10, 24, 10, 0, tzinfo=vn_rules.VN_TZ)
check("scan: not on a weekend", not sched4._stv_scan_due())
os.environ["STV_SCAN_MINUTES"] = "2"
check("STV_SCAN_MINUTES: 5 to 120", external.scan_minutes() == 5)
del os.environ["STV_SCAN_MINUTES"]
vn_rules.now_vn = svc_vn_now

# shares bought before: recorded with their buy date, so they are sellable at once
T._now_vn = lambda: datetime(2026, 10, 22, 10, 0, tzinfo=vn_rules.VN_TZ)
_vn_now_t2 = vn_rules.now_vn
vn_rules.now_vn = T._now_vn                  # Tài sản ages its lots by the same clock (not the real date)
check("a fresh buy is not sellable before T+2", T._pending_lots(bot2.real("stv").positions["MSN"], "2026-10-22"))
vn_rules.now_vn = _vn_now_t2
msg_e = bot2.taisan.handle("/tsmua", "VNM 300 61000 2026-10-01")
pv = bot2.real("stv").positions["VNM"]
check("/tsmua with an old date: sellable now", pv.qty == 300 and pv.avg_price == 61_000 and not T._pending_lots(pv, "2026-10-22"),
      (msg_e, pv))
print("  ", bot2.taisan.handle("/tsban", "VNM 300 62000 2026-10-22"))
check("...and it can be sold at once", "VNM" not in bot2.real("stv").positions)
try:
    bot2.taisan.handle("/tsban", "MSN 250 74000 2026-10-22")
    check("a sale before T+2 refused", False)
except ValueError as exc:
    check("a sale before T+2 refused", "T+2" in str(exc), str(exc))

# T+2 by the time of day; one announcement per SELL even when the site rewrites its history
from tradingagents.trading.ledger import Position  # noqa: E402

lot = Position("XYZ", 500, 20_000, "2026-10-20")
check("bought 20/10: not sellable on 22/10 at 10:00, with when", T._t2_now(lot, datetime(2026, 10, 22, 10, 0, tzinfo=vn_rules.VN_TZ))
      == (0, "500 cp chưa về đủ T+2 (bán được từ chiều 22/10)"), T._t2_now(lot, datetime(2026, 10, 22, 10, 0, tzinfo=vn_rules.VN_TZ)))
check("...sellable on 22/10 at 13:30", T._t2_now(lot, datetime(2026, 10, 22, 13, 30, tzinfo=vn_rules.VN_TZ)) == (500, ""))
check("after the close: judged for the next session's open", T._t2_now(lot, datetime(2026, 10, 21, 15, 30, tzinfo=vn_rules.VN_TZ))
      == (0, "500 cp chưa về đủ T+2 (bán được từ chiều 22/10)"))
lot2 = Position("XYZ", 500, 20_000, "2026-10-21")
check("a weekend: judged for Monday's open (T+2 was Friday afternoon)",
      T._t2_now(lot2, datetime(2026, 10, 24, 10, 0, tzinfo=vn_rules.VN_TZ)) == (500, ""))
from tradingagents.trading.ledger import Ledger as _L  # noqa: E402

book_t2 = _L(cash=1e9, start_cash=1e9)
book_t2.record_fill("BUY", "XYZ", 500, 20_000, "2026-10-21")
book_t2.settle_to("2026-10-22")                   # what the evening run of 22/10 leaves in the saved book
check("T+2 morning (23/10 10:00) from a book settled to the day before: still not sellable",
      T._t2_now(book_t2.positions["XYZ"], datetime(2026, 10, 23, 10, 0, tzinfo=vn_rules.VN_TZ))[0] == 0)
sa = external.SellAlerts(Path(tempfile.mkdtemp(prefix="sa-")))
s_live = {"day": "2026-10-22", "side": "SELL", "created": "2026-10-22T10:30"}
s_final = {"day": "2026-10-22", "side": "SELL", "created": "2026-10-22T15:08"}
s_regen = {"day": "2026-10-22", "side": "SELL", "created": "2026-11-05T02:00"}
sa.remember([("AAA", s_live)], ["AAA"])
check("an in-session SELL confirmed at the close: announced again, as final", sa.pending([("AAA", s_final)]) == [("AAA", s_final)])
sa.remember([("AAA", s_final)], ["AAA"])
check("the same SELL regenerated later by the site: not announced again", sa.pending([("AAA", s_regen)]) == [])
check("a cannot-fetch notice once a day", sa.warn_once("2026-10-22") and not sa.warn_once("2026-10-22")
      and sa.pending([("AAA", s_regen)]) == [])

# ───────────── 6. intraday watches the separate account ─────────────
from vnbot.intraday import IntradayMonitor  # noqa: E402
mon = IntradayMonitor(SETTINGS, bot, lambda t: None, price=lambda c, d: None, index_change=lambda d: None,
                      exchange_of=lambda c: "HOSE", closes=lambda c, on, d: None)
labels = [label for label, _ in mon._books("2026-10-22")]
check("intraday includes the Tài sản portfolio", "💼 Tài sản" in labels, labels)

print(f"\n{PASSES} passed, {len(FAILS)} failed")
sys.exit(1 if FAILS else 0)
