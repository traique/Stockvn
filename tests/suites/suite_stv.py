"""sieutinhieu.vn comparison feature, offline (fake HTTP / fake fetch)."""
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import _common  # noqa: E402,F401 - clean env, no .env, offline, temp folder, repo on sys.path
DATA = Path(tempfile.mkdtemp(prefix="stv-"))
os.environ.update({"DATA_DIR": str(DATA), "TRADE_MODE": "paper", "SIEUTINHIEU": "1",
                   "TRADINGAGENTS_PARAMS_PATH": str(DATA / "quant_params.json")})
from vnbot import settings as settings_module  # noqa: E402

SETTINGS = settings_module.load()
import pandas as pd  # noqa: E402
from tradingagents.market import vn_rules  # noqa: E402
from tradingagents.dataflows.vendors import sieutinhieu as stv  # noqa: E402
from tradingagents.dataflows.vendors import http  # noqa: E402

FAILS, PASSES = [], 0


def check(name, cond, info=""):
    global PASSES
    if cond:
        PASSES += 1
    else:
        FAILS.append(name)
        print("FAIL:", name, info)


def row(day, side, price, created, strength=80):
    return {"symbol": "X", "timestamp": f"{day}T00:00:00Z", "signal_type": side, "price": price,
            "price_basis": "bar_close", "created_at": created, "strength_pct": strength,
            "plan": {"tp1": round(price * 1.09, 2), "sl": round(price * 0.94, 2)} if side == "BUY" else None}


# ───────────── 1. normalize ─────────────
n = stv.normalize(row("2026-10-21", "BUY", 23.85, "2026-10-21T08:08:38.160600Z"))
check("price in VND", n["price"] == 23850 and n["tp1"] == 26000, n)
check("published that evening = live", n["live"] is True)
old = stv.normalize(row("2025-01-15", "BUY", 12.03, "2026-10-03T02:32:00Z"))
check("regenerated history is not live", old["live"] is False)
nxt = stv.normalize(row("2026-10-16", "SELL", 10.0, "2026-10-19T01:00:00Z"))       # Fri signal, Mon 08:00 VN
check("published before the next session = live", nxt["live"] is True)
check("bad rows dropped", stv.normalize({"signal_type": "HOLD", "timestamp": "2026-10-21T00:00:00Z", "price": 1}) is None
      and stv.normalize({"signal_type": "BUY", "timestamp": "x", "price": 1}) is None
      and stv.normalize(row("2026-10-21", "BUY", 0, "2026-10-21T08:00:00Z")) is None)

# ───────────── 2. HTTP: honest headers, refusal respected ─────────────
calls = []


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


def fake_request(method, url, **kw):
    calls.append((url, kw.get("headers"), kw.get("params")))
    return RESP["r"]


RESP = {"r": Resp(200, [row("2026-10-21", "BUY", 23.85, "2026-10-21T08:08:38Z"),
                        row("2026-09-08", "SELL", 24.55, "2026-10-03T02:32:00Z")])}
http.request = fake_request
stv.MIN_INTERVAL_SECONDS = 0
out = stv.fetch_signals("gex")
check("fetch parses newest first", [s["day"] for s in out] == ["2026-10-21", "2026-09-08"], out)
url, headers, params = calls[-1]
check("only the per-stock endpoint", url.endswith("/api/v1/signals/latest") and params["symbol"] == "GEX", calls[-1])
check("honest User-Agent, no borrowed Origin/Referer",
      "TradingAgentsVN" in headers["User-Agent"] and "Origin" not in headers and "Referer" not in headers, headers)
RESP["r"] = Resp(403, {})
try:
    stv.fetch_signals("GEX")
    check("403 -> Refused", False)
except stv.Refused:
    check("403 -> Refused", True)
n_calls = len(calls)
try:
    stv.fetch_signals("FPT")
    check("cooldown: no request after a refusal", False)
except stv.Refused:
    check("cooldown: no request after a refusal", len(calls) == n_calls)
stv._refused_until = 0.0
RESP["r"] = Resp(200, [])

# ───────────── 3. store ─────────────
from vnbot import external  # noqa: E402

store = external.SignalStore(DATA)
HIST = {}


def fake_fetch(symbol):
    if symbol == "DOWN":
        raise RuntimeError("timeout")
    if symbol == "BLOCK":
        raise stv.Refused("từ chối (HTTP 403)")
    return [stv.normalize(r) for r in HIST.get(symbol, [])][::-1]


HIST["GEX"] = [row("2026-09-08", "SELL", 24.55, "2026-10-03T02:32:00Z"),
               row("2026-10-21", "BUY", 23.85, "2026-10-21T08:08:38Z")]
done, err = store.refresh(["GEX", "DOWN", "FPT"], fetch=fake_fetch)
check("refresh keeps going past one failure", done == 2 and "DOWN" in err, (done, err))
check("state as of a day sees no later signal", store.state("GEX", "2026-10-20")["side"] == "SELL"
      and store.state("GEX", "2026-10-21")["side"] == "BUY")
done2, _ = store.refresh(["GEX"], fetch=fake_fetch)
check("fresh cache not fetched again", done2 == 0)
done3, err3 = store.refresh(["AAA", "BLOCK", "BBB"], fetch=fake_fetch)
check("refusal stops the run", done3 == 1 and "403" in err3 and not store.signals("BBB"), (done3, err3))

# refusal kept across restarts; empty answer keeps history; non-list body; down cooldown; budget
import json as _json  # noqa: E402
saved = _json.loads(store.path.read_text(encoding="utf-8"))
check("refusal saved in the store file", float(saved.get("refused_until") or 0) > 0, saved.get("refused_until"))
stv._refused_until = 0.0
store.refresh(["AAA"], fetch=fake_fetch, fresh_after="2099-01-01T00:00")     # loads the saved refusal
check("refusal restored into the client after a restart", stv.refused_for() > 3600)
stv._refused_until = 0.0
s_before = store.signals("GEX")
store.refresh(["GEX"], fetch=lambda sym: [], fresh_after="2099-01-01T00:00")
check("an empty answer never wipes a saved history", store.signals("GEX") == s_before and s_before, store.last_error())
RESP["r"] = Resp(200, {"detail": "maintenance"})
try:
    stv.fetch_signals("GEX")
    check("unexpected body raises", False)
except stv.VendorUnavailableError:
    check("unexpected body raises", True)
from tradingagents.dataflows.errors import VendorUnavailableError as VUE  # noqa: E402


def timeout_request(method, url, **kw):
    calls.append(("timeout", url))
    raise VUE("timed out")


http.request = timeout_request
try:
    stv.fetch_signals("GEX")
except VUE:
    pass
n_calls = len(calls)
try:
    stv.fetch_signals("FPT")
except VUE:
    pass
check("after a failure the site is not asked again for a while", len(calls) == n_calls)
stv._down_until = 0.0
http.request = fake_request
RESP["r"] = Resp(200, [])
clock = {"t": 0.0}
import time as _time  # noqa: E402
_real_monotonic = _time.monotonic
external.time.monotonic = lambda: clock.__setitem__("t", clock["t"] + 100) or clock["t"]
done_b, err_b = store.refresh(["B1", "B2", "B3", "B4"], fetch=lambda sym: [stv.normalize(row("2026-10-21", "BUY", 10, "2026-10-21T08:00:00Z"))],
                              fresh_after="2099-01-01T00:00")
check("a refresh stops at its time budget", done_b < 4 and "hết thời gian" in err_b, (done_b, err_b))
external.time.monotonic = _real_monotonic

# ───────────── 4. plan ─────────────
from tradingagents.trading.ledger import Ledger, Bar  # noqa: E402
book = Ledger(cash=1e9, start_cash=1e9)
HIST.update({
    "AAA": [row("2026-10-21", "BUY", 20.0, "2026-10-21T08:00:00Z", 70)],
    "BBB": [row("2026-10-21", "BUY", 40.0, "2026-10-21T08:00:00Z", 95)],
    "OLD": [row("2026-10-21", "BUY", 30.0, "2026-10-25T02:00:00Z", 99)],           # regenerated later
    "STALE": [row("2026-10-15", "BUY", 10.0, "2026-10-15T08:00:00Z", 99)],         # not today
    "NOTIME": [row("2026-10-21", "BUY", 50.0, None, 99)],                            # publication time unknown
})
store.refresh(["AAA", "BBB", "OLD", "STALE", "NOTIME"], fetch=fake_fetch, fresh_after="2099-01-01T00:00")
check("unknown publication time is not live", store.state("NOTIME", "2026-10-21")["live"] is False)
orders = external.plan(book, "2026-10-21", store, ["AAA", "BBB", "OLD", "STALE", "NOTIME"],
                       {"AAA": 20_000, "BBB": 40_000, "OLD": 30_000, "STALE": 10_000, "NOTIME": 50_000})
check("buys only fresh live BUYs, strongest first", [o.symbol for o in orders] == ["BBB", "AAA"], orders)
check("orders at the next open", all(o.limit is None and o.side == "BUY" and o.created == "2026-10-21" for o in orders))
share = orders[0].qty * 40_000 / 1e9 * 100
check("equal weight ~9.6% (10 slots, room for the open)", 9.0 < share < 9.8, share)

# no look-ahead: a BUY published the next morning after the open is not live; a SELL published
# after the next open is not acted on at that open
late_buy = stv.normalize(row("2026-10-21", "BUY", 20.0, "2026-10-22T03:00:00Z"))          # 10:00 VN next day
check("BUY published after the next open is not live", late_buy["live"] is False)
lk = Ledger(cash=1e9, start_cash=1e9)
lk.record_fill("BUY", "LATE", 1000, 20_000, "2026-10-01")
HIST["LATE"] = [row("2026-10-21", "SELL", 19.0, "2026-10-23T02:32:00Z")]                    # regenerated later
store.refresh(["LATE"], fetch=fake_fetch, fresh_after="2099-01-01T00:00")
o1 = external.plan(lk, "2026-10-21", store, ["LATE"], {"LATE": 19_000})
check("SELL created after the next open is not acted on then", not o1, o1)
lk.orders = []
o2 = external.plan(lk, "2026-10-23", store, ["LATE"], {"LATE": 19_000})
check("...but once known it is", [o.side for o in o2] == ["SELL"], o2)
# slots are freed only by sales that filled
full = Ledger(cash=1e9, start_cash=1e9)
for i in range(10):
    full.record_fill("BUY", f"H{i}", 100, 10_000, "2026-10-01")
HIST["H0"] = [row("2026-10-21", "SELL", 9.0, "2026-10-21T08:00:00Z")]
HIST["NEW"] = [row("2026-10-21", "BUY", 30.0, "2026-10-21T08:00:00Z")]
store.refresh(["H0", "NEW"], fetch=fake_fetch, fresh_after="2099-01-01T00:00")
o3 = external.plan(full, "2026-10-21", store, ["H0", "NEW"] + [f"H{i}" for i in range(1, 10)], {"NEW": 30_000, "H0": 9_000})
check("full book: sells, no buy until a slot is really free", [o.side for o in o3] == ["SELL"], o3)
# replay: NaN open skipped, a SELL on the last bar left out of both totals
fr = pd.DataFrame({"Date": pd.to_datetime(["2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06"]),
                   "Open": [10_000, float("nan"), 11_000, 12_000], "Close": [10_000, 10_500, 11_500, 12_000]})
rp = external.replay([{"day": "2026-10-01", "side": "BUY", "price": 10_000, "created": "2026-10-01T15:00", "live": True},
                      {"day": "2026-10-05", "side": "SELL", "price": 11_500, "created": "2026-10-05T15:00", "live": True},
                      {"day": "2026-10-05", "side": "BUY", "price": 11_500, "created": "2026-10-05T15:01", "live": True},
                      {"day": "2026-10-06", "side": "SELL", "price": 12_000, "created": "2026-10-06T15:00", "live": True}], fr)
check("NaN open skipped (entry at the next valid open)", rp["trades"][0]["in"] == 11_000, rp["trades"][0])
check("trade without a next session left out of both totals", rp["trades"][1]["real"] is None
      and abs(rp["site"] - 0.15) < 1e-9, rp)

# ───────────── 5. the book through sessions (trader._stv_session) ─────────────
import vnbot.trader as T  # noqa: E402

SERIES = {
    "VNINDEX": [("2026-10-21", 1700, 1700), ("2026-10-22", 1705, 1710), ("2026-10-23", 1710, 1712),
                ("2026-10-26", 1712, 1700), ("2026-10-27", 1700, 1690), ("2026-10-28", 1690, 1695)],
    "GEX": [("2026-10-21", 23_100, 23_850), ("2026-10-22", 24_000, 24_200), ("2026-10-23", 24_300, 24_500),
            ("2026-10-26", 24_600, 24_000), ("2026-10-27", 23_900, 23_500), ("2026-10-28", 23_400, 23_600)],
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
                                                   "Close": [r[2] for r in rows]})

    def exchange(self, s):
        return "HOSE"

    def close(self, s, day):
        rows = self._rows(s, day)
        return float(rows[-1][2]) if rows else None

    def bar(self, s, day, required=False):
        rows = self._rows(s, day)
        if not rows or rows[-1][0] != day:
            return None
        _, o, c = rows[-1]
        prev = rows[-2][2] if len(rows) > 1 else None
        return Bar(day, o, max(o, c), min(o, c), c, prev, "HOSE", 1e6)


class WL:
    def get(self):
        return ["GEX"]


bot = T.TradingBot(SETTINGS, WL())
check("comparison book listed for the capital reset", bot.BOOK_NAMES.get("stv") == "Siêu tín hiệu")
HIST["GEX"] = [row("2026-10-21", "BUY", 23.85, "2026-10-21T08:08:38Z", 90),
               row("2026-10-26", "SELL", 24.00, "2026-10-26T08:05:00Z", 10)]
store.refresh(["GEX"], fetch=fake_fetch, fresh_after="2099-01-01T00:00")
market = FakeMarket("2026-10-28")
sessions = market.sessions("VNINDEX")
sb = bot.stv_book()
for d in sessions:
    bot._stv_session(d, sessions, sb, market, store, ["GEX"])
fills = [(f.date, f.side, f.price) for f in sb.fills]
check("bought at the open of the session after the signal (+slippage)",
      fills and fills[0][:2] == ("2026-10-22", "BUY") and fills[0][2] == vn_rules.slipped(24_000, "BUY", "HOSE"), fills)
check("sold at the open after the SELL signal", len(fills) == 2 and fills[1][:2] == ("2026-10-27", "SELL")
      and fills[1][2] == vn_rules.slipped(23_900, "SELL", "HOSE"), fills)
check("fees charged", sb.fills[0].cost > 0 and sb.fills[1].cost > 0)
check("book saved with its day", T._book_day(bot.stv_book()) == "2026-10-28")

# ───────────── 6. replay of a history the site regenerated (synthetic bars and signals) ─────────────
# Sessions from June 2026; closes rise 50đ a session and each open is the previous close + 100đ,
# so the next-open price a trader really gets is always worse than the signal's close.
days6, d6 = [], datetime(2026, 6, 1)
while len(days6) < 100:
    if vn_rules.is_session_day(d6.strftime("%Y-%m-%d")):
        days6.append(d6.strftime("%Y-%m-%d"))
    d6 = d6 + pd.Timedelta(days=1)
closes6 = [20_000 + 50 * i for i in range(len(days6))]
g = pd.DataFrame({"Date": pd.to_datetime(days6), "Open": [closes6[max(i - 1, 0)] + 100 for i in range(len(days6))],
                  "Close": closes6})
hist = []
for k in range(7):                                  # 7 old BUY->SELL pairs, all created on 03/10 (a regenerated history)
    b, sl = days6[5 + 10 * k], days6[10 + 10 * k]
    hist.append(row(b, "BUY", closes6[days6.index(b)] / 1000, "2026-10-03T01:00:00Z"))
    hist.append(row(sl, "SELL", closes6[days6.index(sl)] / 1000, "2026-10-03T01:00:00Z"))
live_day = days6[-1]                                # one live BUY, published at 15:08 on its own day
hist.append(row(live_day, "BUY", closes6[-1] / 1000, f"{live_day}T08:08:38Z"))
sigs = [s for s in (stv.normalize(r) for r in hist) if s]
r = external.replay(sigs, g)
check("replay pairs BUY->SELL", len(r["trades"]) == 7 and r["open"] and r["open"]["buy"] == live_day, len(r["trades"]))
check("history flagged as regenerated", r["regenerated"] == "2026-10-03" and r["live"] == 1, (r["regenerated"], r["live"]))
check("real < signal-price result", r["real"] < r["site"], (r["real"], r["site"]))
t0 = r["trades"][0]
i_buy, i_sell = days6.index(t0["buy"]), days6.index(t0["sell"])
want = (closes6[i_sell] + 100) * (1 - (vn_rules.SELL_FEE_PCT + vn_rules.SELL_TAX_PCT) / 100) \
    / ((closes6[i_buy] + 100) * (1 + vn_rules.BUY_FEE_PCT / 100)) - 1
check("real result = next session's opens, with fees and tax", abs(t0["real"] - want) < 1e-12
      and abs(t0["site"] - (closes6[i_sell] / closes6[i_buy] - 1)) < 1e-9, (t0, want))

# /stv MÃ: the latest signal with its plan, the history replayed both ways, short lines
import tradingagents.dataflows.ohlcv as ohlcv_module  # noqa: E402

rstore = external.SignalStore(Path(tempfile.mkdtemp(prefix="stvrep-")))
rstore._write({"symbols": {"GEX": {"fetched": "2099-01-01T00:00", "signals": sorted(sigs, key=lambda x: x["day"], reverse=True)}}})
rstore.refresh = lambda *a, **k: (0, "")
_load = ohlcv_module.load_ohlcv
ohlcv_module.load_ohlcv = lambda *a, **k: g
rep = external.symbol_report("GEX", rstore)
ohlcv_module.load_ohlcv = _load
print(rep)
check("/stv MÃ: header and latest signal with the plan below",
      rep.startswith(f"📡 SIÊU TÍN HIỆU · GEX (tham khảo)\nTín hiệu gần nhất: 🟢 MUA {external._ddmm(live_day)} @ ")
      and rep.split("\n")[2].startswith("   mục tiêu "), rep)
check("/stv MÃ: both results, one per bullet", "• Theo giá tín hiệu: " in rep and "• Làm được thật: " in rep
      and "lệnh gần nhất (trang → thật):" in rep, rep)
check("/stv MÃ: regenerated history flagged", "⚠️ Lịch sử tới 03/10 do trang tạo lại" in rep, rep)
check("/stv MÃ: says whether it is in the watchlist", "👀 Có trong watchlist Siêu tín hiệu." in rep, rep)
check("/stv MÃ: short lines", max(len(line) for line in rep.splitlines()) <= 90, max(rep.splitlines(), key=len))

# ───────────── 7. /nhanh note, disabled feature ─────────────
line = external.note("GEX", "2026-10-21", "NO_TRADE", store, fetch_if_stale=False)
check("note shows the signal and the disagreement", "MUA từ 21/10" in line and "ĐỨNG NGOÀI: KHÁC chiều" in line, line)
check("HOLD counts as the same side as a BUY", "cùng chiều" in external.note("GEX", "2026-10-21", "HOLD", store, fetch_if_stale=False))
os.environ["SIEUTINHIEU"] = "0"
check("off: no line", external.note("GEX", "2026-10-21", "BUY", store, fetch_if_stale=False) == "")
check("off: no comparison book option", "stv" not in T.TradingBot(SETTINGS, WL()).BOOK_NAMES)
os.environ["SIEUTINHIEU"] = "1"

print(f"\n{PASSES} passed, {len(FAILS)} failed")
sys.exit(1 if FAILS else 0)
