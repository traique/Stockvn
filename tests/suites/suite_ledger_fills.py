"""The paper fill model: gaps through the stop, locked floor/ceiling days, T+2, odd bars,
the ex-date rebase, and liquidity measured in money (offline, synthetic bars)."""
import _common  # noqa: F401 - clean env, no .env, offline, temp folder, repo on sys.path
from tradingagents.quant import features as feat  # noqa: E402
from tradingagents.trading.ledger import Bar, Ledger, Order, Position, _ExitRules  # noqa: E402

t = _common.Checks()
R = _ExitRules(max_hold_sessions=30, t1_sell_fraction=0.5, use_t2=True)


def book_with_position(stop=9000.0, t1=11000.0, t2=12000.0):
    L = Ledger(cash=1e9, start_cash=1e9)
    L.orders.append(Order("AAA", "BUY", 10000, 10050, "2026-01-01", stop=stop, target1=t1, target2=t2))
    L.process_session({"AAA": Bar("2026-01-02", 10000, 10100, 9950, 10000, 10000, "HOSE", 1e6)}, rules=R)
    L.process_session({"AAA": Bar("2026-01-05", 10000, 10050, 9950, 10000, 10000, "HOSE", 1e6)}, rules=R)
    L.process_session({"AAA": Bar("2026-01-06", 10000, 10050, 9950, 10000, 10000, "HOSE", 1e6)}, rules=R)
    return L


def fills(L, bar):
    return [(f.side, f.qty, f.price, f.reason) for f in L.process_session({"AAA": bar}, rules=R)]


t.ok("limit buy fills at the open under the limit", [(f.side, f.qty, f.price) for f in book_with_position().fills]
     == [("BUY", 10000, 10050.0)])
t.ok("gap below the stop: sold at the open minus slippage, not at the stop",
     fills(book_with_position(), Bar("2026-01-07", 8500, 8700, 8400, 8600, 10000, "HOSE", 1e6))
     == [("SELL", 10000, 8490.0, "chạm stop")])
t.ok("stop and T1 in the same bar: the stop is assumed first (conservative)",
     fills(book_with_position(), Bar("2026-01-07", 10000, 11200, 8900, 10500, 10000, "HOSE", 1e6))
     == [("SELL", 10000, 8990.0, "chạm stop")])
t.ok("floor-locked one-price day: no sale",
     fills(book_with_position(), Bar("2026-01-07", 9300, 9300, 9300, 9300, 10000, "HOSE", 1e6)) == [])
t.ok("touched the floor but traded: sold at the stop",
     fills(book_with_position(stop=9400), Bar("2026-01-07", 9500, 9500, 9300, 9300, 10000, "HOSE", 1e6))
     == [("SELL", 10000, 9390.0, "chạm stop")])

L = Ledger(cash=1e9, start_cash=1e9)
L.orders.append(Order("BBB", "BUY", 1000, 10000, "2026-01-01", stop=9000))
t.ok("low == limit: no fill (queue not reached)",
     L.process_session({"BBB": Bar("2026-01-02", 10200, 10300, 10000, 10100, 10100, "HOSE", 1e6)}) == [])

L = Ledger(cash=1e9, start_cash=1e9)
L.orders.append(Order("CCC", "BUY", 1000, 10050, "2026-01-01", stop=9500))
L.process_session({"CCC": Bar("2026-01-02", 10000, 10100, 9950, 10000, 10000, "HOSE", 1e6)}, rules=R)
f1 = L.process_session({"CCC": Bar("2026-01-05", 9400, 9500, 9300, 9350, 10000, "HOSE", 1e6)}, rules=R)
f2 = L.process_session({"CCC": Bar("2026-01-06", 9000, 9100, 8700, 8800, 9350, "HOSE", 1e6)}, rules=R)
t.ok("T+1: under the stop but not sellable yet", f1 == [])
t.ok("T+2: sold from the afternoon, at the close", [(f.price, f.reason) for f in f2] == [(8790.0, "chạm stop")])

L = Ledger(cash=1e9, start_cash=1e9)
L.orders.append(Order("DDD", "BUY", 1000, None, "2026-01-01", ref=10000))
t.ok("ceiling-locked day: market buy not filled",
     L.process_session({"DDD": Bar("2026-01-02", 10700, 10700, 10700, 10700, 10000, "HOSE", 1e6)}) == [])
L = Ledger(cash=1e9, start_cash=1e9)
L.orders.append(Order("EEE", "BUY", 1000, None, "2026-01-01", ref=10000))
t.ok("opened at the ceiling then traded down: filled at the ceiling",
     [f.price for f in L.process_session({"EEE": Bar("2026-01-02", 10700, 10700, 10400, 10500, 10000, "HOSE", 1e6)})] == [10700])

L = book_with_position()
t.ok("NaN bar ignored", L.process_session({"AAA": Bar("2026-01-07", float("nan"), 9000, 8000, 8500, 10000, "HOSE", 1e6)}, rules=R) == [])
t.ok("zero-volume bar ignored", L.process_session({"AAA": Bar("2026-01-08", 8000, 8000, 8000, 8000, 10000, "HOSE", 0)}, rules=R) == [])
t.ok("cash never negative", L.cash >= 0)


# ── ex-date of a 30% stock dividend, the source's history already adjusted ──
def held():
    L = Ledger(cash=1e9, start_cash=1e9)
    L.positions["BBB"] = Position("BBB", 1000, 60090.0, "2026-10-01", stop=55000.0, target1=68000.0,
                                  last_price=60000.0, last_date="2026-10-20", sessions=10, unsettled=[])
    return L


L = held()
out = L.process_session({"BBB": Bar("2026-10-21", 46200, 47000, 45900, 46600, 46150.0, "HOSE", 2e6, "2026-10-20")})
p = L.positions.get("BBB")
t.ok("ex-date: no fake stop, shares x1.3, stop rebased", out == [] and p and p.qty == 1300 and p.stop == 42300,
     (out, p and p.qty, p and p.stop))

# ── T1 on a position too small to sell half of (one lot of 100) ──
L = Ledger(cash=0, start_cash=1e7)
L.positions["SML"] = Position("SML", 100, 20000.0, "2026-10-01", stop=19000.0, target1=22000.0, target2=24000.0,
                              sessions=5, last_price=21000.0, unsettled=[])
out = L.process_session({"SML": Bar("2026-10-10", 22100, 22500, 22000, 22300, 21000, "HOSE", 1e6)}, rules=R)
t.ok("one lot at T1: the whole lot is sold at T1 (half a lot cannot be sold)",
     [(f.side, f.qty, f.price, f.reason) for f in out] == [("SELL", 100, 22050.0, "chốt toàn bộ tại T1")]
     and "SML" not in L.positions,
     (out, L.positions.get("SML")))
L = Ledger(cash=0, start_cash=1e7)
L.positions["SML"] = Position("SML", 100, 20000.0, "2026-10-01", stop=19000.0, target1=22000.0, target2=24000.0,
                              sessions=5, last_price=21000.0, unsettled=[])
R0 = _ExitRules(max_hold_sessions=30, t1_sell_fraction=0.0, use_t2=True)
out = L.process_session({"SML": Bar("2026-10-10", 22100, 22500, 22000, 22300, 21000, "HOSE", 1e6)}, rules=R0)
p = L.positions.get("SML")
t.ok("t1_sell_fraction 0: nothing sold at T1, stop moved to break-even (unchanged rule)",
     out == [] and p is not None and p.t1_done and p.stop == 20000.0, (out, p))

# ── liquidity is money traded, not shares ──
cheap = feat.calc_liquidity([150_000.0] * 21, closes=[4_000.0] * 21)        # ~0,6 tỷ/ngày
dear = feat.calc_liquidity([60_000.0] * 21, closes=[150_000.0] * 21)        # ~9 tỷ/ngày
t.ok("a cheap stock with many shares but little money is thin", cheap.is_thin)
t.ok("an expensive stock with few shares but much money is not thin", not dear.is_thin)

t.done()
