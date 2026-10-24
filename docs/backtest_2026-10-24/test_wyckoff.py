"""Checks of wyckoff.py: causal (a later bar never changes an earlier reading), the range and
the position, spring, no-supply test and SOW. Run: python docs/backtest_2026-10-24/test_wyckoff.py"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wyckoff as wy  # noqa: E402


class Checks:
    def __init__(self):
        self.passed, self.failed = 0, []

    def ok(self, name, condition, info=""):
        if condition:
            self.passed += 1
        else:
            self.failed.append(name)
            print(f"FAIL {name}  [{info}]")

    def done(self):
        print(f"{self.passed} passed, {len(self.failed)} failed")
        sys.exit(1 if self.failed else 0)


t = Checks()


def flat(n=80, low=95.0, high=105.0, vol=1000.0):
    """A sideways range: alternate highs and lows, steady volume."""
    o, h, lo, c, v = [], [], [], [], []
    for i in range(n):
        mid = 100.0 + (2.0 if i % 2 else -2.0)
        o.append(mid)
        h.append(high if i % 10 == 5 else mid + 1.5)
        lo.append(low if i % 10 == 0 else mid - 1.5)
        c.append(mid)
        v.append(vol)
    return o, h, lo, c, v


o, h, lo, c, v = flat()
r = wy.read(o, h, lo, c, v, 79)
t.ok("range of the 60 sessions before: support, resistance, width", r.support == 95.0 and r.resistance == 105.0
     and abs(r.width_pct - (105 / 95 - 1) * 100) < 1e-3, r)
t.ok("position in the range", abs(r.position_pct - (c[79] - 95) / 10 * 100) < 1e-6, r.position_pct)
t.ok("too few sessions: no reading", wy.read(o, h, lo, c, v, 30) is None)

# A spring: the low under the support, the close back above it.
o2, h2, lo2, c2, v2 = (list(x) for x in (o, h, lo, c, v))
o2.append(97.0), h2.append(98.0), lo2.append(93.0), c2.append(96.5), v2.append(1500.0)
r = wy.read(o2, h2, lo2, c2, v2, 80)
t.ok("spring: pierced the support, closed back inside", r.spring and not r.sow, r)
lo2[80], c2[80] = 93.0, 94.0
t.ok("a close under the support is not a spring", not wy.read(o2, h2, lo2, c2, v2, 80).spring)
r = wy.read(o2, h2, lo2, c2, v2, 80)
t.ok("SOW: a down bar closing under the support on high volume", r.sow, r)
v2[80] = 1100.0
t.ok("SOW needs high volume", not wy.read(o2, h2, lo2, c2, v2, 80).sow)

# A spring needs a range: a 60% wide window is a trend, not a trading range.
o3, h3, lo3, c3, v3 = (list(x) for x in (o, h, lo, c, v))
h3[40] = 160.0
o3.append(97.0), h3.append(98.0), lo3.append(93.0), c3.append(96.5), v3.append(1500.0)
t.ok("no spring outside a range", not wy.read(o3, h3, lo3, c3, v3, 80).spring)

# No supply: a down close, low volume, narrow spread, low in the range.
o4, h4, lo4, c4, v4 = (list(x) for x in (o, h, lo, c, v))
o4.append(97.5), h4.append(97.8), lo4.append(96.6), c4.append(96.8), v4.append(600.0)
c4[79] = 98.0
r = wy.read(o4, h4, lo4, c4, v4, 80)
t.ok("no-supply test", r.no_supply and r.volume_ratio <= 0.75 and r.spread_ratio <= 1.0 and r.position_pct <= 30, r)
v4[80] = 900.0
t.ok("no-supply needs low volume", not wy.read(o4, h4, lo4, c4, v4, 80).no_supply)

# Causal: the reading of a session is the same with or without the bars after it.
rng = random.Random(3)
price, bars = 100.0, ([], [], [], [], [])
for _ in range(400):
    op = price * (1 + rng.uniform(-0.02, 0.02))
    cl = op * (1 + rng.uniform(-0.04, 0.04))
    hi, lw = max(op, cl) * (1 + rng.uniform(0, 0.02)), min(op, cl) * (1 - rng.uniform(0, 0.02))
    for lst, val in zip(bars, (op, hi, lw, cl, rng.uniform(500, 3000)), strict=True):
        lst.append(val)
    price = cl
full = wy.series(*bars)
same = all(full[i] == wy.read(*(x[:i + 1] for x in bars), i) for i in range(0, 400, 7))
t.ok("causal: later bars never change an earlier reading", same)
t.ok("series: None before the lookback, readings after", full[59] is None and full[60] is not None)

t.done()
