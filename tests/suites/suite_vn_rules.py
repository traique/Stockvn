"""Vietnamese market rules: tick sizes, price bands (exact, no float edge), session phases,
holidays, T+2 counted in exchange sessions (offline)."""
from datetime import datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

import _common  # noqa: F401 - clean env, no .env, offline, temp folder, repo on sys.path
from tradingagents.market import vn_rules as R  # noqa: E402
from tradingagents.trading.ledger import Ledger, _sessions_between  # noqa: E402

t = _common.Checks()

# ── ticks ──
t.ok("HOSE ticks 10/50/100", [R.tick_size(p, "HOSE") for p in (9_990, 10_000, 49_950, 50_000)] == [10, 50, 50, 100])
t.ok("HNX and UPCoM tick 100", R.tick_size(5_000, "HNX") == 100 and R.tick_size(5_000, "UPCOM") == 100)
t.ok("unknown board: 100 (valid everywhere)", R.tick_size(5_000, None) == 100)


# ── bands: the ceiling rounds down, the floor up, exactly as decimal arithmetic ──
def exact_tick(p, ex):
    return (10 if p < 10_000 else 50 if p < 50_000 else 100) if ex == "HOSE" else 100


def exact_band(ref, ex, pct):
    ref, b = Decimal(ref), Decimal(pct) / 100
    rc, rf = ref * (1 + b), ref * (1 - b)
    tc, tf = exact_tick(rc, ex), exact_tick(rf, ex)
    return int((rc / tc).to_integral_value(ROUND_FLOOR) * tc), int((rf / tf).to_integral_value(ROUND_CEILING) * tf)


for ex, pct in (("HOSE", 7), ("HNX", 10), ("UPCOM", 15)):
    bad = []
    for p in range(1_000, 300_001, 10):
        if p % exact_tick(p, ex):
            continue
        got = tuple(int(x) for x in R.ceiling_floor(float(p), ex))
        want = exact_band(p, ex, pct)
        if got != want and want[0] > p and want[1] < p:      # tiny prices move one tick minimum: skipped
            bad.append((p, got, want))
    t.ok(f"{ex} ±{pct}% bands exact for every price 1.000-300.000", not bad, bad[:5])
t.ok("UPCoM 6.000 -> ceiling 6.900 (6000 x 1.15 is 6899.99... in float)", R.ceiling_floor(6_000.0, "UPCOM") == (6_900.0, 5_100.0))
t.ok("HOSE 10.000 -> 10.700 / 9.300", R.ceiling_floor(10_000.0, "HOSE") == (10_700.0, 9_300.0))

# ── sessions ──
phases = {"09:14": "ato", "09:15": "continuous", "11:29": "continuous", "11:30": "lunch", "12:59": "lunch",
          "13:00": "continuous", "14:29": "continuous", "14:30": "atc", "14:45": "put_through", "15:00": "after_close"}
got = {hm: R.session_phase(datetime(2026, 10, 21, int(hm[:2]), int(hm[3:]), tzinfo=R.VN_TZ)) for hm in phases}
t.ok("session phases", got == phases, got)
t.ok("2/9 holiday, 21/10 a session", not R.is_session_day("2026-09-02") and R.is_session_day("2026-10-21"))
t.ok("weekend not a session", not R.is_session_day("2026-10-24"))
t.ok("30/4 and 1/5 holidays", not R.is_session_day("2026-04-30") and not R.is_session_day("2026-05-01"))
t.ok("prev_session skips the weekend", R.prev_session("2026-10-26") == "2026-10-23")
t.ok("add_sessions skips holidays", R.add_sessions("2026-04-29", 2) == "2026-05-05")

# ── T+2 in exchange sessions ──
L = Ledger(cash=1e9, start_cash=1e9)
L.record_fill("BUY", "FPT", 1000, 100_000.0, "2026-10-19")           # Monday
pos = L.positions["FPT"]
t.ok("Mon buy: nothing sellable Wed morning (T+2)", pos.sellable_on("2026-10-21", afternoon=False) == 0)
t.ok("Mon buy: sellable Wed afternoon", pos.sellable_on("2026-10-21", afternoon=True) == 1000)
t.ok("Mon buy: sellable Thu morning", pos.sellable_on("2026-10-22", afternoon=False) == 1000)

t.ok("sessions between 29/4 and 4/5 = 1 (two holidays)", _sessions_between("2026-04-29", "2026-05-04") == 1)
L2 = Ledger(cash=1e9, start_cash=1e9)
L2.record_fill("BUY", "FPT", 1000, 100_000.0, "2026-04-29")
L2.settle_to("2026-05-04")
t.ok("Wed 29/4 buy not settled on Mon 4/5 (only T+1)", L2.positions["FPT"].settled_qty == 0)
try:
    L2.record_fill("SELL", "FPT", 1000, 101_000.0, "2026-05-04")
    t.ok("selling before T+2 refused", False)
except ValueError as exc:
    t.ok("selling before T+2 refused", "T+2" in str(exc), str(exc))
L2.settle_to("2026-05-05")
t.ok("settled on Tue 5/5 (real T+2)", L2.positions["FPT"].settled_qty == 1000)

from vnbot.trader import _add_sessions  # noqa: E402

t.ok("the report's T+2 date for a 29/4 buy is 5/5", _add_sessions("2026-04-29", 2) == "2026-05-05")

t.done()
