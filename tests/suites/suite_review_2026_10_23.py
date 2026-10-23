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

t.done()
