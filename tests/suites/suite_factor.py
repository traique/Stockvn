"""The defensive (factor) strategy on synthetic data: orders of day D fill on D+1, equal weights,
cash never negative, and real-account orders sized on the marked NAV (offline)."""
import math
import random
from datetime import date, timedelta

import _common  # noqa: F401 - clean env, no .env, offline, temp folder, repo on sys.path
from tradingagents.quant.params import DEFAULT  # noqa: E402
from tradingagents.trading import factor, simulator as sim  # noqa: E402
from tradingagents.trading.ledger import Bar, Ledger  # noqa: E402

t = _common.Checks()
rng = random.Random(7)
days, d = [], date(2021, 1, 4)
while len(days) < 900:
    if d.weekday() < 5:
        days.append(d.isoformat())
    d += timedelta(days=1)


def walk(start, vol, drift):
    p, out = start, []
    for _ in days:
        p *= math.exp(rng.gauss(drift, vol))
        out.append(p)
    return out


bench = walk(1000, 0.01, 0.0004)
bars = {}
for i in range(40):
    closes = walk(15000 + 700 * i, 0.012 + 0.0005 * i, 0.0004)
    b, prev = {}, None
    for k, day in enumerate(days):
        c = round(closes[k], -1)
        o = round((prev or c) * math.exp(rng.gauss(0, 0.004)), -1)
        b[day] = Bar(day, o, round(max(o, c) * 1.006, -1), round(min(o, c) * 0.994, -1), c, prev, "HOSE", 1e6)
        prev = c
    bars[f"S{i:02d}"] = b
data = sim.Dataset(dates=days[300:], bars=bars, benchmark=dict(zip(days, bench, strict=True)), symbols=list(bars))
L = factor.run(data, DEFAULT)
buys = [f for f in L.fills if f.side == "BUY"]
st = L.stats()
t.ok("the strategy trades", st["trades"] > 0 and buys, st)
# A golden number: if the strategy is changed ON PURPOSE, check the new result and update it here.
t.ok("same result as recorded on this seeded data", st["trades"] == 28 and st["nav"] == 1_270_652_448, st)
first = sorted({f.date for f in buys})[0]
t.ok("first buy is after the first decision day (orders fill the next session)", first > data.dates[0], first)
basket = [f for f in buys if f.date == first]
weights = [f.qty * f.price / 1e9 * 100 for f in basket]
t.ok("first basket: 10 stocks near 9.8% each", len(basket) == 10 and all(9.0 <= w <= 9.8 for w in weights), weights)
t.ok("cash never negative", L.cash >= 0 and min(h["nav"] for h in L.history) > 0)

# real account: sized on the NAV at market prices (the book marked to the last close)
real = Ledger(cash=200e6, start_cash=200e6)
real.record_fill("BUY", "S00", 10000, 10000.0, "2026-01-05")
real.cash = 100e6
real.positions["S00"].last_price = 20000.0              # what the evening run's mark does
t.ok("marked NAV = cash + shares x last close", round(real.nav()) == 300_000_000, real.nav())
orders = factor.plan(real, "2026-06-01", factor.Decision(True, True, ["S00", "S01"]),
                     {"S00": 20000.0, "S01": 20000.0}, DEFAULT)
new = [o for o in orders if o.symbol == "S01" and o.side == "BUY"]
pct = new[0].qty * 20000 / 300e6 * 100 if new else 0
t.ok("new position ~9.8% of the marked NAV", new and 9.0 <= pct <= 9.8, (orders, pct))

t.done()
