"""Round-1 fixes: real-book corporate actions and marks, T+2 on holidays, calibration holdout,
Zoom pairing, request body limit (offline)."""
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import _common  # noqa: E402,F401 - clean env, no .env, offline, temp folder, repo on sys.path
DATA = Path(tempfile.mkdtemp(prefix="fixtest-"))
os.environ.update({"DATA_DIR": str(DATA), "TRADE_MODE": "signal", "WEB_PASSWORD": "pw-123456",
                   "TRADINGAGENTS_PARAMS_PATH": str(DATA / "quant_params.json")})

from vnbot import settings as settings_module  # noqa: E402

SETTINGS = settings_module.load()

from tradingagents.market import vn_rules  # noqa: E402
from tradingagents.trading.ledger import Ledger, _sessions_between  # noqa: E402

FAILS, PASSES = [], 0


def check(name, cond, info=""):
    global PASSES
    if cond:
        PASSES += 1
    else:
        FAILS.append(f"{name} {info}")
        print("FAIL:", name, info)


# ───────────── T+2 by the exchange calendar ─────────────
check("sessions 04-29..05-04 = 1 (30/4, 1/5 holidays)", _sessions_between("2026-04-29", "2026-05-04") == 1,
      _sessions_between("2026-04-29", "2026-05-04"))
check("sessions 04-29..05-05 = 2", _sessions_between("2026-04-29", "2026-05-05") == 2)
check("add_sessions T+2 of 04-29 = 05-05", vn_rules.add_sessions("2026-04-29", 2) == "2026-05-05")
check("prev_session of 05-04 = 04-29", vn_rules.prev_session("2026-05-04") == "2026-04-29")
check("prev_session of Mon 10-19 = Fri 10-16", vn_rules.prev_session("2026-10-19") == "2026-10-16")
b = Ledger(cash=1e9, start_cash=1e9)
b.record_fill("BUY", "CCC", 1000, 20_000, "2026-04-29")
try:
    b.record_fill("SELL", "CCC", 1000, 21_000, "2026-05-04")
    check("sell on real T+1 (after holidays) refused", False)
except ValueError:
    check("sell on real T+1 (after holidays) refused", True)
b.record_fill("SELL", "CCC", 1000, 21_000, "2026-05-05")
check("sell on T+2 accepted", "CCC" not in b.positions)

b = Ledger(cash=1e9, start_cash=1e9)
b.record_fill("BUY", "DDD", 1000, 20_000, "2026-10-19")      # Monday
p = b.positions["DDD"]
check("T+1 afternoon: 0 sellable", p.sellable_on("2026-10-20", afternoon=True) == 0)
check("T+2 morning: 0 sellable", p.sellable_on("2026-10-21", afternoon=False) == 0)
check("T+2 afternoon: all sellable", p.sellable_on("2026-10-21", afternoon=True) == 1000)
check("T+3 morning: all sellable", p.sellable_on("2026-10-22", afternoon=False) == 1000)
check("real fill has no close mark", p.last_date == "" and p.last_price == 20_000)

# ───────────── real book: mark to market, corporate actions ─────────────
def hist(pairs):
    return [d for d, _ in pairs], [c for _, c in pairs]


r = Ledger(cash=500_000_000, start_cash=1e9)
r.record_fill("BUY", "BBB", 1000, 60_000, "2026-09-01", stop=55_000, target1=68_000, target2=72_000)
base = [("2026-08-28", 59_000), ("2026-08-31", 59_500), ("2026-09-01", 60_500), ("2026-09-02", 61_000)]
notes = r.mark_to_history({"BBB": hist(base)}, {"BBB": "HOSE"})
pb = r.positions["BBB"]
check("first mark: no note (fill price never compared)", notes == [], notes)
check("first mark = last close", pb.last_price == 61_000 and pb.last_date == "2026-09-02")
check("NAV at market", abs(r.nav() - (r.cash + 1000 * 61_000)) < 1)
# Re-adjusted history after a 30% bonus issue (ex-date 2026-09-03).
adj = [(d, round(c / 1.3)) for d, c in base] + [("2026-09-03", 47_000), ("2026-09-04", 47_500)]
notes = r.mark_to_history({"BBB": hist(adj)}, {"BBB": "HOSE"})
check("basis note", len(notes) == 1 and notes[0]["kind"] == "basis" and abs(notes[0]["ratio"] - 1.3) < 0.001, notes)
check("stop rebased", pb.stop == vn_rules.round_to_tick(55_000 / 1.3, "HOSE"), pb.stop)
check("T1/T2 rebased", pb.target1 == vn_rules.round_to_tick(68_000 / 1.3, "HOSE")
      and pb.target2 == vn_rules.round_to_tick(72_000 / 1.3, "HOSE"))
check("qty unchanged until reported", pb.qty == 1000)
check("free share estimate ~300", notes[0]["free_estimate"] == 300, notes[0])
check("mark advanced", pb.last_price == 47_500 and pb.last_date == "2026-09-04")
check("no fake stop: bar low 46,500 > new stop", 46_500 > pb.stop)
again = r.mark_to_history({"BBB": hist(adj)}, {"BBB": "HOSE"})
check("idempotent: no second note", again == [], again)
# Bonus shares arrive, then an odd-lot sale of everything.
cost = pb.avg_price * pb.qty
r.add_free_shares("BBB", 300, "2026-09-25")
check("free shares added", pb.qty == 1300 and abs(pb.avg_price * 1300 - cost) < 1)
r.record_fill("SELL", "BBB", 1300, 48_000, "2026-09-28")
check("odd total sold", "BBB" not in r.positions)
r.record_fill("BUY", "BBB", 150, 48_000, "2026-09-28")        # odd-lot board (v9: the owner buys odd lots)
check("odd-lot buy recorded", r.positions["BBB"].qty == 150)

# Source NOT re-adjusted on the ex-date: a gap.
g = Ledger(cash=1e9, start_cash=1e9)
g.record_fill("BUY", "GGG", 1000, 60_000, "2026-09-01", stop=55_000, target1=68_000)
g.mark_to_history({"GGG": hist(base)}, {"GGG": "HOSE"})
raw = base + [("2026-09-03", 46_500)]
notes = g.mark_to_history({"GGG": hist(raw)}, {"GGG": "HOSE"})
pg = g.positions["GGG"]
check("gap note", [n["kind"] for n in notes] == ["gap"], notes)
check("gap: mark held before the gap", pg.last_date == "2026-09-02" and pg.last_price == 61_000)
check("gap: levels untouched", pg.stop == 55_000)
check("gap: pending estimate kept", abs(pg.pending_ratio - 61_000 / 46_500) < 1e-4 and pg.pending_since == "2026-09-03",
      (pg.pending_ratio, pg.pending_since))
# Next day the source re-adjusts: the basis change is measured.
adj2 = [(d, round(c / 1.3)) for d, c in base] + [("2026-09-03", 46_500), ("2026-09-04", 47_000)]
notes = g.mark_to_history({"GGG": hist(adj2)}, {"GGG": "HOSE"})
check("after re-adjust: basis note", [n["kind"] for n in notes] == ["basis"], notes)
check("after re-adjust: stop rebased", pg.stop == vn_rules.round_to_tick(55_000 / 1.3, "HOSE"), pg.stop)
check("after re-adjust: marked", pg.last_date == "2026-09-04")
check("after re-adjust: pending cleared", pg.pending_ratio == 0 and pg.pending_since == "")
# Never re-adjusted: estimate after GAP_GRACE_SESSIONS sessions.
e = Ledger(cash=1e9, start_cash=1e9)
e.record_fill("BUY", "EEE", 1000, 60_000, "2026-09-01", stop=55_000, target1=68_000)
e.mark_to_history({"EEE": hist(base)}, {"EEE": "HOSE"})
raw = base + [("2026-09-03", 46_500), ("2026-09-04", 46_800), ("2026-09-07", 47_000)]
notes = e.mark_to_history({"EEE": hist(raw)}, {"EEE": "HOSE"})
check("still within grace: gap", [n["kind"] for n in notes] == ["gap"], notes)
raw += [("2026-09-08", 47_100)]
notes = e.mark_to_history({"EEE": hist(raw)}, {"EEE": "HOSE"})
pe = e.positions["EEE"]
check("after grace: gap_estimate", [n["kind"] for n in notes] == ["gap_estimate"], notes)
check("estimate ratio 61000/46500", abs(notes[0]["ratio"] - 61_000 / 46_500) < 1e-4)
check("estimate: marked to latest", pe.last_date == "2026-09-08")
check("estimate: no repeat", e.mark_to_history({"EEE": hist(raw)}, {"EEE": "HOSE"}) == [])

# A real.json saved by the OLD code: mark = fill price/date, no close_marks flag.
leg = Ledger.from_dict({"cash": 1e9, "start_cash": 1e9, "positions": {"FPT": {
    "symbol": "FPT", "qty": 1000, "avg_price": 61_592, "buy_date": "2026-09-08", "stop": 57_000, "target1": 68_000,
    "last_price": 61_500, "last_date": "2026-09-08", "peak": 61_500,
    "unsettled": [{"date": "2026-09-08", "qty": 1000, "sessions": 0}]}}, "meta": {"last_day": "2026-09-08"}})
n = leg.mark_to_history({"FPT": hist([("2026-09-07", 59_800), ("2026-09-08", 60_000), ("2026-09-09", 60_200)])},
                        {"FPT": "HOSE"})
check("legacy: fill mark not taken for a basis change", n == [] and leg.positions["FPT"].stop == 57_000, n)
check("legacy: flagged and marked", leg.meta.get("close_marks") == 1 and leg.positions["FPT"].last_price == 60_200)

# Legit moves are not gaps.
def no_gap(name, prev, cur, exchange, d2="2026-09-03"):
    L = Ledger(cash=1e9, start_cash=1e9)
    L.record_fill("BUY", "XXX", 100, prev, "2026-09-01", stop=prev * 0.5)
    L.mark_to_history({"XXX": hist([("2026-09-01", prev), ("2026-09-02", prev)])}, {"XXX": exchange})
    n = L.mark_to_history({"XXX": hist([("2026-09-01", prev), ("2026-09-02", prev), (d2, cur)])}, {"XXX": exchange})
    check(name, n == [] and L.positions["XXX"].last_date == d2, n)


no_gap("HOSE floor (-7%) is no gap", 60_000, vn_rules.ceiling_floor(60_000, "HOSE")[1], "HOSE")
no_gap("HNX -9.9% is no gap", 60_000, 54_100, "HNX")
no_gap("unknown exchange -25% is no gap (detection off)", 60_000, 45_000, None)
no_gap("UPCoM -24% is no gap (reference is the average)", 60_000, 45_600, "UPCOM")
no_gap("after a suspension HOSE -15% is no gap", 60_000, 51_000, "HOSE", d2="2026-10-20")
L = Ledger(cash=1e9, start_cash=1e9)
L.record_fill("BUY", "BAD", 100, 60_000, "2026-09-01", stop=50_000)
L.mark_to_history({"BAD": hist(base)}, {"BAD": "HOSE"})
n = L.mark_to_history({"BAD": hist(base + [("2026-09-03", 30_000), ("2026-09-04", 61_200)])}, {"BAD": "HOSE"})
check("bad print undone is no gap", n == [] and L.positions["BAD"].last_price == 61_200, n)

# Paper book unchanged: a buy fill marks with the bar's close and date.
from tradingagents.trading.ledger import Bar, Order  # noqa: E402
P = Ledger(cash=1e9, start_cash=1e9)
P.orders.append(Order("AAA", "BUY", 1000, None, "2026-10-19", ref=10_000))
P.process_session({"AAA": Bar("2026-10-20", 10_000, 10_100, 9_900, 10_050, 10_000, "HOSE", 1e6)})
check("paper buy marked with close/date", P.positions["AAA"].last_date == "2026-10-20"
      and P.positions["AAA"].last_price == 10_050)

# ───────────── trader ─────────────
from vnbot.trader import TradingBot, _pending_lots, _real_adjustment_lines  # noqa: E402


class WL:
    def get(self):
        return []


bot = TradingBot(SETTINGS, WL())
try:
    bot._fill_day("2026-09-02", "")
    check("holiday fill date refused", False)
except ValueError as exc:
    check("holiday fill date refused", "lễ" in str(exc), exc)
check("ordinary day accepted", bot._fill_day("2026-10-20", "") == "2026-10-20")
lot_pos = Ledger(cash=1e9, start_cash=1e9)
lot_pos.record_fill("BUY", "HHH", 100, 10_000, "2026-04-29")
check("pending T+2 date skips holidays", _pending_lots(lot_pos.positions["HHH"], "2026-04-29") == [("2026-05-05", 100)],
      _pending_lots(lot_pos.positions["HHH"], "2026-04-29"))

# /khop with the engine's levels: a SELL signal's levels are not taken.
object.__setattr__(SETTINGS, "signal_strategy", "engine")
bot.set_capital(1_000_000_000)
bot._write_json("signals.json", {"day": "2026-10-16", "signals": {
    "SSS": {"symbol": "SSS", "action": "SELL", "confidence": 0.7, "price": 50_000, "entry_low": None,
            "entry_high": None, "stop": 53_000, "target1": 46_000, "target2": None, "position_size_pct": None},
    "BBY": {"symbol": "BBY", "action": "BUY", "confidence": 0.7, "price": 50_000, "entry_low": 49_000,
            "entry_high": 50_000, "stop": 46_000, "target1": 56_000, "target2": 60_000, "position_size_pct": 8}}})
bot.record_fill("mua", "SSS", 100, 50_000, "2026-10-19")
bot.record_fill("mua", "BBY", 100, 50_000, "2026-10-19")
real = bot.real()
check("SELL-signal levels not taken", real.positions["SSS"].stop == 0 and real.positions["SSS"].target1 is None,
      (real.positions["SSS"].stop, real.positions["SSS"].target1))
check("BUY-signal levels taken", real.positions["BBY"].stop == 46_000 and real.positions["BBY"].target1 == 56_000)
msg = bot.record_free_shares("BBY", 15, "2026-10-20")
check("free shares via trader", bot.real().positions["BBY"].qty == 115, msg)
check("odd-lot sell via trader", "Đã ghi BÁN" in bot.record_fill("ban", "BBY", 115, 51_000, "2026-10-21"))
object.__setattr__(SETTINGS, "signal_strategy", "factor")

# _mark_real + factor sizing on the marked NAV.
import pandas as pd  # noqa: E402


class FakeMarket:
    def __init__(self, series, exchange="HOSE"):
        self.series, self.ex = series, exchange

    def frame(self, symbol, day, required=False):
        rows = [(d, c) for d, c in self.series.get(symbol, []) if d <= day]
        if not rows:
            return None
        return pd.DataFrame({"Date": pd.to_datetime([d for d, _ in rows]), "Close": [c for _, c in rows],
                             "Open": [c for _, c in rows], "High": [c for _, c in rows], "Low": [c for _, c in rows],
                             "Volume": [1e6] * len(rows)})

    def exchange(self, symbol):
        return self.ex

    def close(self, symbol, day):
        f = self.frame(symbol, day)
        return None if f is None else float(f["Close"].iloc[-1])

    def bar(self, symbol, day, required=False):
        from tradingagents.trading.ledger import Bar as B
        f = self.frame(symbol, day)
        if f is None or f["Date"].iloc[-1].strftime("%Y-%m-%d") != day:
            return None
        c = float(f["Close"].iloc[-1])
        prev = float(f["Close"].iloc[-2]) if len(f) > 1 else None
        return B(day, c, c, c, c, prev, exchange="HOSE", volume=1e6)


bot2_dir = DATA / "b2"
os.makedirs(bot2_dir, exist_ok=True)
S2 = settings_module.Settings(**{**SETTINGS.__dict__, "data_dir": bot2_dir})
bot2 = TradingBot(S2, WL())
bot2.set_capital(1_000_000_000)
bot2.record_fill("mua", "AAA", 50_000, 10_000, "2026-09-08")       # ~500m
snap = bot2.real()
market = FakeMarket({"AAA": [("2026-09-08", 10_000), ("2026-10-16", 20_000)], "ZZZ": [("2026-10-16", 50_000)]})
marked, notes = bot2._mark_real(snap, "2026-10-16", market)
check("_mark_real saved the mark", bot2.real().positions["AAA"].last_price == 20_000)
from tradingagents.quant.params import DEFAULT  # noqa: E402
from tradingagents.trading import factor  # noqa: E402
dec = factor.Decision(True, True, ["AAA", "ZZZ"])
text = bot2._real_factor_report("2026-10-16", marked, {}, dec, market, DEFAULT)
true_nav = marked.cash + 50_000 * 20_000
line = [ln for ln in text.splitlines() if "ZZZ" in ln][0]
qty = int(line.split("ZZZ")[1].split("cp")[0].strip().replace(".", ""))
share = qty * 50_000 / true_nav * 100
check("factor buy sized on market NAV (~9.6%)", 9.0 < share < 9.9, f"{share:.2f}% ({line})")
lines = _real_adjustment_lines([{"kind": "basis", "symbol": "BBB", "ratio": 1.3, "qty": 1000, "free_estimate": 300,
                                 "date": "2026-09-04"}, {"kind": "gap", "symbol": "GGG", "ratio": 1.31,
                                                         "date": "2026-09-03"}])
check("adjustment lines", len(lines) == 2 and "/thuong BBB" in lines[0] and "GDKHQ" in lines[1], lines)

# ───────────── intraday ─────────────
from vnbot.intraday import IntradayMonitor  # noqa: E402


class IT:
    def __init__(self, real, paper=None):
        self._real, self._paper = real, paper or Ledger(cash=1e9, start_cash=1e9)

    def real(self):
        return Ledger.from_dict(json.loads(json.dumps(self._real.to_dict())))

    def paper(self):
        return self._paper


def run_intraday(book, price, closes, at="10:00", day="2026-10-21", exchange="HOSE"):
    sent = []
    s = settings_module.Settings(**{**SETTINGS.__dict__, "data_dir": Path(tempfile.mkdtemp())})
    mon = IntradayMonitor(s, IT(book), sent.append, price=lambda c, d: price, index_change=lambda d: None,
                          exchange_of=lambda c: exchange, closes=closes)
    hh, mm = map(int, at.split(":"))
    return mon.check(day, now=datetime(2026, 10, 21, hh, mm, tzinfo=vn_rules.VN_TZ))


def real_book(buy_day="2026-10-15", stop=55_000, last=(61_000, "2026-10-20"), flagged=True):
    L = Ledger(cash=1e9, start_cash=1e9)
    if flagged:
        L.meta["close_marks"] = 1
    L.record_fill("BUY", "BBB", 1000, 60_000, buy_day, stop=stop, target1=68_000)
    L.positions["BBB"].last_price, L.positions["BBB"].last_date = last
    return L


same = lambda c, on, d: 61_000                      # source history not re-adjusted  # noqa: E731
adjusted = lambda c, on, d: round(61_000 / 1.3)     # source already re-adjusted today  # noqa: E731
near = lambda c, on, d: 57_000                     # yesterday's close 57.000: floor 53.100  # noqa: E731
out = run_intraday(real_book(last=(57_000, "2026-10-20")), 54_000, near)
check("real stop hit alerted", any("≤ stop 55.000" in a for a in out), out)
out = run_intraday(real_book(buy_day="2026-10-19", last=(57_000, "2026-10-20")), 54_000, near, at="10:00")
check("T+2 morning: 0 sellable", any("bán được 0/1.000" in a for a in out), out)
out = run_intraday(real_book(buy_day="2026-10-19", last=(57_000, "2026-10-20")), 54_000, near, at="13:30")
check("T+2 afternoon: all sellable", any("1.000 cp bán được" in a for a in out), out)
out = run_intraday(real_book(), 46_500, adjusted)
check("ex-date adjusted: no fake stop", not any("≤ stop" in a for a in out), out)
check("ex-date adjusted: basis note", any("điều chỉnh quyền x1.300" in a for a in out), out)
out = run_intraday(real_book(), 41_000, adjusted)
check("ex-date adjusted: real stop on new basis still alerted",
      any("≤ stop " + f"{vn_rules.round_to_tick(55_000 / 1.3, 'HOSE'):,.0f}".replace(",", ".") in a for a in out), out)
out = run_intraday(real_book(), 46_500, same)
check("ex-date not adjusted: guard instead of stop", any("GDKHQ" in a for a in out)
      and not any("≤ stop" in a for a in out), out)
out = run_intraday(real_book(stop=0), vn_rules.ceiling_floor(61_000, "HOSE")[1], same)
check("floor from last close", any("giá sàn 56.800" in a for a in out), out)
out = run_intraday(real_book(stop=0, last=(61_000, "2026-10-16")), 56_800, same)
check("stale mark: no floor alert", not any("giá sàn" in a for a in out), out)
out = run_intraday(real_book(stop=0, last=(60_000, "")), 55_800, same)
check("fill price is no floor reference", not any("giá sàn" in a for a in out), out)
out = run_intraday(real_book(last=(57_000, "2026-10-20")), 54_000, lambda c, on, d: (_ for _ in ()).throw(RuntimeError("down")))
check("history down: stop still alerted", any("≤ stop" in a for a in out), out)
# Legacy real.json (fill price as the mark, no flag), before the first patched evening run.
legacy = real_book(last=(61_500, "2026-10-20"), flagged=False)
out = run_intraday(legacy, 56_500, lambda c, on, d: 60_000, )
check("legacy intraday: no fake basis from the fill price", not any("x1.025" in a for a in out), out)
legacy = real_book(stop=57_000, last=(61_500, "2026-10-20"), flagged=False)
out = run_intraday(legacy, 56_500, lambda c, on, d: 60_000)
check("legacy intraday: stop 57.000 alerted, no fake basis", any("≤ stop 57.000" in a for a in out)
      and not any("điều chỉnh quyền" in a for a in out), out)
# Day after an unadjusted gap: the evening run left pending_ratio and the mark at E-1.
pend = real_book(last=(61_000, "2026-10-19"))
pend.positions["BBB"].pending_ratio, pend.positions["BBB"].pending_since = round(61_000 / 46_500, 6), "2026-10-20"
out = run_intraday(pend, 47_000, same)
check("pending: no fake stop on E+1", not any("≤ stop" in a for a in out), out)
check("pending: estimate note", any("tỷ lệ ước tính" in a for a in out), out)
out = run_intraday(pend, 41_000, same)
est = f"{vn_rules.round_to_tick(55_000 / round(61_000 / 46_500, 6), 'HOSE'):,.0f}".replace(",", ".")
check("pending: real fall below estimated stop alerted", any(f"≤ stop {est}" in a for a in out), out)
out = run_intraday(pend, 47_000, adjusted)
check("pending + source re-adjusted: measured ratio wins", any("x1.300" in a and "giá đã điều chỉnh" in a for a in out), out)
# Guard tolerance and boards.
out = run_intraday(real_book(stop=0), 56_500, same)                        # 56.800 floor, 56.500 within 1% tolerance
check("guard: within tolerance no guard", not any("GDKHQ" in a for a in out), out)
out = run_intraday(real_book(), 46_500, same, exchange="UPCOM")
check("guard: off on UPCoM (stop alerted)", any("≤ stop" in a for a in out) and not any("GDKHQ" in a for a in out), out)
out = run_intraday(real_book(), 46_500, same, exchange=None)
check("guard: off on unknown board", not any("GDKHQ" in a for a in out), out)

# ───────────── calibration history ─────────────
from tradingagents.trading import calibrate as cal  # noqa: E402
hp = cal.history_path()
hp.parent.mkdir(parents=True, exist_ok=True)
rows = [
    {"as_of": "2026-06-01", "strategy": "momentum", "tried": [{"name": "x", "holdout": {"value": 1}}],
     "accepted": None, "champion": {"params": {}}},                                    # old row, tested
    {"as_of": "2026-07-01", "strategy": "momentum", "tried": [{"name": "x", "holdout": None}], "accepted": None,
     "champion": {"params": {}}, "holdout_tested": False},                            # new row, not tested
    {"as_of": "2026-07-02", "strategy": "momentum", "tried": [], "accepted": None, "champion": {"params": {}},
     "holdout_tested": True, "dry_run": True},                                        # dry run: ignored
    {"as_of": "2026-07-03", "strategy": "engine", "tried": [], "accepted": None, "champion": {"params": {}},
     "holdout_tested": True},                                                         # other strategy
]
hp.write_text("".join(json.dumps(x) + "\n" for x in rows), encoding="utf-8")
dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2026-05-01", "2026-08-31")]
since = cal._sessions_since_attempt("momentum", "2026-08-31", dates)
check("attempt from old row found", since is not None and since[0] == "2026-06-01", since)
check("engine attempt separate", cal._sessions_since_attempt("engine", "2026-08-31", dates)[0] == "2026-07-03")
check("noise rms", abs(cal.holdout_noise([1.0, -1.0, 3.0], 0.0) - (11 / 3) ** 0.5) < 1e-9)
check("margin: the fixed one when the holdout is quiet", cal.required_margin(1, 0.0) == cal.holdout_margin(1))
check("margin: twice the noise when that is larger", cal.required_margin(1, 50.0) == max(cal.holdout_margin(1), 100.0))
check("margin grows with the number of challengers tested", cal.required_margin(3, 0.0) == 3 * cal.holdout_margin(1))
hp.unlink()



# The evening report's view of a holding in an unadjusted gap: levels divided by the estimate.
from vnbot.trader import _estimated_view  # noqa: E402
gv = Ledger(cash=1e9, start_cash=1e9)
gv.record_fill("BUY", "GGG", 1000, 60_000, "2026-09-08", stop=55_000, target1=68_000)
gv.positions["GGG"].pending_ratio = 1.3
view = _estimated_view(gv, lambda s: "HOSE")
check("view: estimated stop", view.positions["GGG"].stop == vn_rules.round_to_tick(55_000 / 1.3, "HOSE"))
check("view: book untouched", gv.positions["GGG"].stop == 55_000)
check("view: same object when nothing pending", _estimated_view(b, lambda s: "HOSE") is b)
print(f"after view tests: {PASSES} passed, {len(FAILS)} failed")
sys.exit(1 if FAILS else 0)
