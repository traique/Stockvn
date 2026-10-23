"""Regression tests for the review of 2026-10-23 (offline): Output Guard exemptions, the
third-party exemption, double guarding, settlement entry at the next session, matched-only
foreign flows, the price-cache fallback, the LLM budget refund, unsigned Zoom requests,
state-log pruning, critic row dating and the whole-lot T1 sale."""
import os
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import _common  # noqa: F401 - clean env, no .env, offline, temp folder, repo on sys.path

os.environ["LLM_USAGE_PATH"] = str(_common.ROOT / "llm_usage.json")

import pandas as pd  # noqa: E402

from tradingagents.agents import output_guard  # noqa: E402
from tradingagents.quant import flow_features as ff, report_format as rfmt  # noqa: E402
from tradingagents.dataflows import ohlcv  # noqa: E402
from tradingagents.dataflows.errors import NoMarketDataError  # noqa: E402
from tradingagents.dataflows.vendors import flows  # noqa: E402
from tradingagents.market import vn_rules  # noqa: E402
from tradingagents.memory import settlement  # noqa: E402

t = _common.Checks()

# ── Output Guard: only the renderer's exact lines are exempt ──
state = {"quant_decision": {
    "action": "BUY", "price": 50000.0, "stop_price": 47000.0, "target_price": 55000.0, "entry_low": 49500.0,
    "entry_high": 50200.0, "rr_ratio": 1.6, "position_size_pct": 10.0, "deterministic_summary": "TÓM TẮT HỆ THỐNG | Stop 47.000 | T1 55.000"}}
text = ("**Rating**: BUY\n\n**Executive Summary**: Mua theo kế hoạch.\n"
        "**Recommendation**: SELL - bán ra toàn bộ\n**Stop Loss**: 40.000 VND\n\n**Price Target**: 55.000 VND")
out, note = output_guard.guard_text(text, state, label="Portfolio Manager")
t.ok("a model line starting with a renderer prefix is still checked for a contradicting action",
     note is not None and "SELL" not in out and "40.000" not in out and out.startswith("**Rating**: BUY"), (note, out))
clean = "**Rating**: BUY\n\n**Executive Summary**: Mua theo kế hoạch, stop 47.000 VND.\n\n**Price Target**: 55.000 VND"
out, note = output_guard.guard_text(clean, state, label="Portfolio Manager")
t.ok("the renderer's own lines and the engine's prices pass", note is None and out == clean, note)

# ── third-party exemption: "tín hiệu/lệnh BÁN" is advice, "lực BÁN" is description ──
t.ok("'đặt lệnh BÁN' under a BUY is a conflict",
     rfmt.find_action_conflicts("Tín hiệu BÁN rất rõ, đặt lệnh BÁN ngay hôm nay.", {"BUY"}) != [])
t.ok("'lực BÁN áp đảo bên MUA' is market description",
     rfmt.find_action_conflicts("Khối ngoại bán ròng mạnh, lực BÁN áp đảo bên MUA.", {"BUY"}) == [])

# ── the final guard does not repeat a note guard_early already wrote ──
node = output_guard.create_output_guard()
early_state = {**state, "guard_notes": "Trader: có mốc giá ngoài dữ liệu (48.500)",
               "trader_investment_plan": "**Action**: BUY\n\nMua quanh 48.500 VND.\n\nFINAL TRANSACTION PROPOSAL: **BUY**",
               "final_trade_decision": clean}
updates = node(early_state)
t.ok("Trader checked once: one note, text untouched",
     updates["guard_notes"].count("Trader:") == 1 and "trader_investment_plan" not in updates, updates["guard_notes"])

# ── settlement: entry at the NEXT session's close, not the decision day's ──
days = pd.to_datetime(["2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05", "2026-03-06", "2026-03-09", "2026-03-10"])
stock = pd.Series([100.0, 110.0, 111.0, 112.0, 113.0, 114.0, 121.0], index=days)
bench = pd.Series([1000.0] * 7, index=days)
settlement.get_closes = lambda sym, start, end: (bench if sym == "VNINDEX" else stock)[lambda s: s.index >= pd.Timestamp(start)]
raw, alpha, held, resolved = settlement.fetch_returns("AAA", "2026-03-02", holding_days=5)
t.ok("return measured from the next close (110) to five sessions later (121): +10%, resolved on the exit day",
     raw is not None and abs(raw - 0.10) < 1e-9 and resolved == "2026-03-10", (raw, resolved))
t.ok("one bar short of the window: pending", settlement.fetch_returns("AAA", "2026-03-03", holding_days=5)[0] is None)

# ── foreign flows: matched-only net is mapped and used ──
item = {"tradingDate": "2025-10-23T00:00:00", "foreignBuyValueTotal": 302e9, "foreignSellValueTotal": 76e9,
        "foreignNetValueMatched": 43.55e9, "totalMatchValue": 638e9, "closePrice": 95000.0, "totalShares": 1e9,
        "foreignAvailablePercentage": 0.12}
t.ok("ForeignNetMatched column", "ForeignNetMatched" in flows.STOCK_FLOW_COLUMNS and flows._matched_net(item) == 43.55e9)
t.ok("buy-sell matched fallback", flows._matched_net({"foreignBuyValueMatched": 5.0, "foreignSellValueMatched": 2.0}) == 3.0)
frame = pd.DataFrame({"ForeignNet": [226e9, 10e9], "ForeignNetMatched": [43.55e9, float("nan")]})
t.ok("matched net used, total as the row-wise fallback", list(ff._foreign_net_matched(frame)) == [43.55e9, 10e9])

# ── price cache: served when the vendor fails and no newer final bar can exist ──
cache_dir = _common.ROOT / "cache"
os.environ["TRADINGAGENTS_CACHE_DIR"] = str(cache_dir)
ohlcv._cache_file = lambda canonical, source: str(cache_dir / f"{canonical}-{source}-ohlcv.csv")
cache_dir.mkdir(exist_ok=True)
path = Path(ohlcv._cache_file("AAA", "dnse"))
pd.DataFrame({"Date": ["2026-10-22", "2026-10-23"], "Open": [1, 1], "High": [1, 1], "Low": [1, 1], "Close": [1, 1],
              "Volume": [1, 1]}).to_csv(path, index=False)
written = datetime(2026, 10, 23, 16, 0, tzinfo=vn_rules.VN_TZ).timestamp()        # Friday after the close
os.utime(path, (written, written))


def _down(*_a, **_k):
    raise NoMarketDataError("AAA", "AAA", "vendor said 400")


ohlcv._download = _down
vn_rules.now_vn = lambda: datetime(2026, 10, 24, 10, 0, tzinfo=vn_rules.VN_TZ)    # Saturday morning
served = ohlcv._read_source("AAA", "dnse", pd.Timestamp("2026-10-24"))
t.ok("Saturday run, vendor 'no data': Friday's complete file is served", served is not None and len(served) == 2)
vn_rules.now_vn = lambda: datetime(2026, 10, 26, 10, 0, tzinfo=vn_rules.VN_TZ)    # Monday before the close
served = ohlcv._read_source("AAA", "dnse", pd.Timestamp("2026-10-26"))
t.ok("Monday 10:00 run: Friday's file is still complete (no Monday bar is final yet)", served is not None)
vn_rules.now_vn = lambda: datetime(2026, 10, 26, 16, 0, tzinfo=vn_rules.VN_TZ)    # Monday after the close
try:
    ohlcv._read_source("AAA", "dnse", pd.Timestamp("2026-10-26"))
    t.ok("Monday 16:00 run: a Friday file is stale, the vendor error is raised", False)
except NoMarketDataError:
    t.ok("Monday 16:00 run: a Friday file is stale, the vendor error is raised", True)

# ── LLM budget: a request that never reached the provider is refunded ──
from tradingagents.llm_clients import fallback_client as fc  # noqa: E402
import openai  # noqa: E402

fc._spend("groq")
before = fc.usage_today()["total"]
fc._refund("groq")
t.ok("refund takes one back", fc.usage_today()["total"] == before - 1 and fc.usage_today()["by"].get("groq", 0) == 0)
t.ok("connection error is a transport error", fc._transport_error(openai.APIConnectionError(request=None)))
t.ok("HTTP 500 is not refunded", not fc._transport_error(RuntimeError("x")))

# ── Zoom: an unsigned request writes nothing to disk ──
from vnbot.zoom import ZoomChannel, ZoomConfig  # noqa: E402

state_path = _common.ROOT / "zoom_state.json"
zoom = ZoomChannel(ZoomConfig(), state_path=state_path, start=False)
zoom.record_refused("bot_notification", "sai chữ ký")
t.ok("refused request kept in memory only", not state_path.exists() or "sai chữ ký" not in state_path.read_text(encoding="utf-8"))
t.ok("...but listed for the /zoom page", any(r["reason"] == "sai chữ ký" and r["ok"] is False for r in zoom.requests_log()))

# ── jobs: the per-ticker full state logs are pruned too ──
from vnbot.jobs import JobQueue  # noqa: E402

results = _common.ROOT / "data" / "results"
logs = results / "FPT" / "TradingAgentsStrategy_logs"
logs.mkdir(parents=True)
for i in range(5):
    f = logs / f"full_states_log_2026-10-{10 + i:02d}.json"
    f.write_text("{}")
    os.utime(f, (time.time() - 1000 + i, time.time() - 1000 + i))
JobQueue._prune_reports(SimpleNamespace(settings=SimpleNamespace(data_dir=_common.ROOT / "data", keep_reports=2)))
t.ok("only the newest state logs stay", sorted(p.name for p in logs.iterdir()) ==
     ["full_states_log_2026-10-13.json", "full_states_log_2026-10-14.json"], sorted(p.name for p in logs.iterdir()))

# ── critic: a row is dated by the close it was decided on ──
from vnbot.critic import CriticLog  # noqa: E402

log = CriticLog(_common.ROOT / "critic.json")
log.add("2026-10-23", SimpleNamespace(symbol="FPT", action="BUY", price=95000.0, stop_price=90000.0, target_price=100000.0,
                                      last_bar_date="2026-10-22"), {"verdict": "AGREE", "confidence": 70, "reason": "x"})
t.ok("morning run dated 23/10 on the 22/10 close is scored from 22/10", log.rows()[-1]["day"] == "2026-10-22")

# ═══ Round 2 (same day): part B leftovers and the part C parameters ═══
from dataclasses import replace  # noqa: E402

from tradingagents.quant import params as qp, policy  # noqa: E402
from tradingagents.trading import factor  # noqa: E402
from tradingagents.trading.ledger import Bar, Ledger, Order, Signal, _ExitRules  # noqa: E402

RULES = _ExitRules(max_hold_sessions=30, t1_sell_fraction=0.5, use_t2=True)
P = qp.DEFAULT

# ── a BUY order that lives several sessions ──
def book(ttl):
    L = Ledger(cash=1e9, start_cash=1e9)
    sig = Signal("AAA", "BUY", 0.8, 10000.0, 9900.0, 10050.0, 9000.0, 11000.0, 12000.0, 10.0)
    L.plan_orders("2026-01-05", [sig], max_positions=5, buy_sessions=ttl)
    return L, sig

L, sig = book(1)
L.process_session({"AAA": Bar("2026-01-06", 10200, 10300, 10100, 10200, 10000, "HOSE", 1e6)}, rules=RULES)
L.end_of_session("2026-01-06")
t.ok("one-session order (default): not filled, gone after its session", not L.orders and not L.positions)
L, sig = book(2)
L.process_session({"AAA": Bar("2026-01-06", 10200, 10300, 10100, 10200, 10000, "HOSE", 1e6)}, rules=RULES)
L.end_of_session("2026-01-06")
t.ok("two-session order survives an unfilled session", len(L.orders) == 1 and L.orders[0].sessions_left == 1)
L.plan_orders("2026-01-06", [replace(sig, entry_high=10150.0, stop=9100.0)], max_positions=5, buy_sessions=2)
t.ok("still BUY: the open order follows the newest limit and stop, no second order",
     len(L.orders) == 1 and L.orders[0].limit == 10150.0 and L.orders[0].stop == 9100.0)
f = L.process_session({"AAA": Bar("2026-01-07", 10100, 10200, 10000, 10100, 10200, "HOSE", 1e6)}, rules=RULES)
t.ok("fills on its second session at the limit", [(x.side, x.price) for x in f] == [("BUY", 10150.0)] and not L.orders)
L, sig = book(3)
L.process_session({"AAA": Bar("2026-01-06", 10200, 10300, 10100, 10200, 10000, "HOSE", 1e6)}, rules=RULES)
L.end_of_session("2026-01-06")
L.plan_orders("2026-01-06", [replace(sig, action="WATCH")], max_positions=5, buy_sessions=3)
t.ok("engine no longer says BUY: the open order is cancelled", not L.orders)
L = Ledger(cash=1e9, start_cash=1e9)
L.orders.append(Order("BBB", "BUY", 1000, 10000.0, "2026-01-01", stop=9000.0))
touch = _ExitRules(limit_fill_touch=True)
f = L.process_session({"BBB": Bar("2026-01-02", 10200, 10300, 10000, 10100, 10100, "HOSE", 1e6)}, rules=touch)
t.ok("limit_fill_touch: low == limit fills at the limit", [(x.side, x.price) for x in f] == [("BUY", 10000.0)])

# ── stop distance floor and the SELL threshold ──
stats = SimpleNamespace(volatility=5.0, rsi14=50.0, trend_3m=5.0)
enh = SimpleNamespace(atr14=150.0)        # 1.5% ATR at 10.000: 1.5 x ATR = 2.25%
sr = SimpleNamespace(support=None, resistance=None, support_prior=None, resistance_prior=None)
stop_old = policy._compute_stop_target(10000.0, enh, stats, 0.0, "buy", sr, "HOSE", None, replace(P, stop_min_pct=0.0))[0]
stop_default = policy._compute_stop_target(10000.0, enh, stats, 0.0, "buy", sr, "HOSE", None, P)[0]
stop_floor = policy._compute_stop_target(10000.0, enh, stats, 0.0, "buy", sr, "HOSE", None,
                                         replace(P, stop_min_pct=4.0))[0]
t.ok("stop_min_pct widens a too-tight ATR stop (old 2.25%, default 3%, 4%)",
     stop_old == 9780.0 and stop_default == 9700.0 and stop_floor == 9600.0, (stop_old, stop_default, stop_floor))
t.ok("new tunables are in TUNABLE with bounds", all(k in qp.TUNABLE for k in
     ("breakout_projected_ok", "confidence_sell_min", "stop_atr_mult", "stop_min_pct", "buy_order_sessions", "limit_fill_touch")))
t.ok("coerce clamps them", qp.coerce({"buy_order_sessions": 9}).buy_order_sessions == 3)

# ── factor: hysteresis band and the momentum term ──
closes = [100.0] * 199 + [103.0]
t.ok("band: 3% above MA200 turns on", factor.market_ok_band(closes, 200, 2.0, False))
t.ok("band: 1% above MA keeps the previous state", factor.market_ok_band([100.0] * 199 + [101.0], 200, 2.0, False) is False
     and factor.market_ok_band([100.0] * 199 + [101.0], 200, 2.0, True) is True)
t.ok("band: 3% below turns off", factor.market_ok_band([100.0] * 199 + [97.0], 200, 2.0, True) is False)
import numpy as np  # noqa: E402

rng = np.random.default_rng(1)
flat = list(100 + np.cumsum(rng.normal(0, 0.2, 252)))
trend = list(np.linspace(80, 100, 252) + rng.normal(0, 0.2, 252))
base = factor.rank({"FLAT": flat, "TREND": trend})
mom = factor.rank({"FLAT": flat, "TREND": trend}, momentum_weight=1.0)
t.ok("momentum weight lifts the trending name", mom[0].symbol == "TREND" and mom[0].score > base[0].score, (base, mom))

# ── market scan: VNDirect's pre-adjustment for a coming ex-date is undone for the board ──
from vnbot.market_scan import MarketCloses  # noqa: E402

mc = MarketCloses(_common.ROOT / "mc")
rows_by_day = {
    "2026-03-02": [{"code": "GVR", "floor": "HOSE", "close": 25700, "raw": 26100, "value": 1e9}],
    "2026-03-03": [{"code": "GVR", "floor": "HOSE", "close": 25732, "raw": 26100, "value": 1e9}],
}
mc.update("2026-03-03", fetch=lambda d: rows_by_day.get(d, []), budget=100, today="2026-03-03")
series, _floors, _value = mc.series(["GVR"], "2026-03-03")
t.ok("closes on the board's basis until the ex-date (x raw/adClose of the newest session)",
     series["GVR"] == [round(25700 * 26100 / 25732, 1), 26100.0], series)

# ── critic settle: no lock held while bars are fetched; outcomes merged ──
from vnbot import critic as cr  # noqa: E402

clog = cr.CriticLog(_common.ROOT / "critic2.json")
clog.add("2026-01-05", SimpleNamespace(symbol="AAA", action="BUY", price=10000.0, stop_price=9000.0,
                                       target_price=12000.0, last_bar_date="2026-01-05"),
         {"verdict": "AGREE", "confidence": 70, "reason": "x"})


def bars(code, day):
    assert not clog._lock.locked(), "lock held during the fetch"
    return [{"High": 12500.0, "Low": 11000.0, "Close": 12200.0}]


n = clog.settle(bars)
t.ok("settled outside the lock", n == 1 and clog.rows()[-1]["outcome"] is not None)

# ── memory log: an entry lands whole ──
from tradingagents.memory.log import TradingMemoryLog  # noqa: E402

mlog = TradingMemoryLog({"memory_log_path": str(_common.ROOT / "mem.md")})
mlog.store_decision("FPT", "2026-01-05", "**Rating**: BUY\n\nx", rating="BUY")
mlog.store_decision("HPG", "2026-01-05", "**Rating**: SELL\n\ny", rating="SELL")
text = (_common.ROOT / "mem.md").read_text(encoding="utf-8")
t.ok("two entries, both tagged", text.count("<!-- ENTRY_END -->") == 2 and "| HPG |" in text and "| FPT |" in text)

# ── listing saved on disk survives an outage ──
from tradingagents.dataflows import symbols as sy  # noqa: E402
from tradingagents.dataflows.vendors import vci  # noqa: E402

sy._listing_path = lambda: str(_common.ROOT / "vci_listing.json")
vci.fetch_listing = lambda: [{"symbol": "ABC", "board": "UPCOM"}]
t.ok("listing fetched and saved", sy.exchange_of("ABC") == "UPCOM" and Path(sy._listing_path()).exists())


def boom():
    raise RuntimeError("VCI down")


vci.fetch_listing = boom
t.ok("VCI down: the saved listing answers", sy.exchange_of("ABC") == "UPCOM")

# ── price cache rules around 15:05 (part B.8) ──
cache_dir2 = _common.ROOT / "cache2"
cache_dir2.mkdir()
p2 = cache_dir2 / "x.csv"
p2.write_text("Date,Open,High,Low,Close,Volume\n2026-10-23,1,1,1,1,1\n")
day = pd.Timestamp("2026-10-23")


def at(h, m):
    return datetime(2026, 10, 23, h, m, tzinfo=vn_rules.VN_TZ)


os.utime(p2, (at(14, 50).timestamp(),) * 2)
vn_rules.now_vn = lambda: at(15, 6)
t.ok("a 14:50 download is not fresh for today's run after 15:05", not ohlcv._cache_is_fresh(str(p2), day, day))
t.ok("...but is fresh for a run dated yesterday", ohlcv._cache_is_fresh(str(p2), day - pd.Timedelta(days=1), day))
os.utime(p2, (at(15, 6).timestamp(),) * 2)
vn_rules.now_vn = lambda: at(15, 8)
t.ok("a 15:06 download is fresh for today's run", ohlcv._cache_is_fresh(str(p2), day, day))
t.ok("_final_through: 15:04 -> yesterday, 15:06 -> today",
     ohlcv._final_through(at(15, 4)) == day - pd.Timedelta(days=1) and ohlcv._final_through(at(15, 6)) == day)
frame = pd.DataFrame({"Date": pd.to_datetime(["2026-10-22", "2026-10-23"]), "Close": [1.0, 2.0]})
t.ok("today's bar dropped before 15:05, kept after",
     len(ohlcv.drop_forming_bar(frame, day, now=at(15, 4))) == 1 and len(ohlcv.drop_forming_bar(frame, day, now=at(15, 6))) == 2)
from tradingagents.dataflows import money_flows as mf  # noqa: E402

d1, d2 = pd.Timestamp("2026-03-02"), pd.Timestamp("2026-03-03")
cached = pd.DataFrame({"Date": [d1], "AAA": [20.0]})
refs = pd.DataFrame({"AAA": [10.0]}, index=[d2])          # reference of 03-03 on today's basis = 10 (a 2:1 bonus)
out = mf._rebase(cached, refs, [d1, d2])
t.ok("breadth closes re-based x0.5 at a 2:1 junction", float(out.loc[out["Date"] == d1, "AAA"].iloc[0]) == 10.0, out)

t.done()
