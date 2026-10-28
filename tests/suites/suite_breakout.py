"""The Quant Engine's breakout switch (Params.breakout_buy): off, a breakout BUY becomes WATCH with
the gate "breakout" (a holder: HOLD without the plan to add) and every other decision stays as it
was; the shadow results follow the switched-off breakouts as a group of their own; the nightly
calibration never moves the switch and keeps it when it saves; the page /bot turns it (offline)."""
import importlib.util
import math
import os
from dataclasses import replace

import _common  # noqa: F401 - clean env, no .env, offline, temp folder, repo on sys.path

os.environ["TRADINGAGENTS_PARAMS_PATH"] = str(_common.ROOT / "quant_params.json")

import pandas as pd  # noqa: E402

from tradingagents.quant import engine, params as qp, policy  # noqa: E402
from tradingagents.trading import calibrate as cal  # noqa: E402
from vnbot import chandoan as cd  # noqa: E402

t = _common.Checks()
ON, OFF = qp.DEFAULT, replace(qp.DEFAULT, breakout_buy=0)

# ───────────── 1. the decision ─────────────
days = pd.bdate_range("2025-06-02", periods=300)
AS_OF = days[-1].strftime("%Y-%m-%d")


def frame(closes, vols=None):
    closes = [float(c) for c in closes]
    return pd.DataFrame({"Date": days, "Open": [closes[0]] + closes[:-1], "High": [c * 1.01 for c in closes],
                         "Low": [c * 0.99 for c in closes], "Close": closes,
                         "Volume": vols or [1_000_000 + 1000 * (i % 7) for i in range(len(closes))]})


VNI_ON = frame([1500 * 1.001 ** i for i in range(300)], [100.0 + (i % 2) for i in range(300)])
vni_c, vni_v = [1500.0], [100.0]
for i in range(1, 300):            # 25 sessions of distribution days at the end: risk-off
    up = not (i >= 275 and i % 2)
    vni_c.append(vni_c[-1] * (1.0035 if up else 0.997))
    vni_v.append(100.0 if up else vni_v[-1] * 1.3)
VNI_OFF = frame(vni_c, vni_v)
VOLS = [1_000_000 + 1000 * (i % 7) for i in range(299)] + [1_800_000]
# A climb, a spike to 29.000, a month around 25.000, then a close at 25.700 on 1.8x volume: above
# the 20-session channel and under the spike's highs, a resistance to aim at.
BREAKOUT = ([16000 + 9000 * i / 265 + 250 * math.sin(i / 2) for i in range(266)]
            + [25000 + 4000 * (1 - abs(i - 269) / 4) for i in range(266, 273)]
            + [25000 + 150 * math.sin(i) for i in range(273, 299)] + [25700])
# The same kind of breakout with the old high long gone: nothing above it to measure R:R on.
NEW_HIGH = ([18000 + 12000 * i / 120 for i in range(121)] + [30000 - 9000 * (i - 120) / 50 for i in range(121, 171)]
            + [21000 + 3500 * (i - 170) / 100 for i in range(171, 271)]
            + [24500 + 300 * math.sin(i) for i in range(271, 299)] + [25600])


def inputs(closes, vni=VNI_ON, params=ON):
    return engine.build_inputs("BRK", AS_OF, frame(closes, VOLS), vni, exchange="HOSE", params=params).inputs


p_on = inputs(BREAKOUT)
p_off = replace(p_on, params=OFF)
d_on, d_off = policy.evaluate_policy(p_on), policy.evaluate_policy(p_off)
t.ok("switch on (the default): the breakout is a BUY with its plan, as before",
     ON.breakout_buy == 1 and d_on.action == "BUY" and d_on.setup_type == "breakout" and d_on.gate == ""
     and d_on.trade_plan is not None, (d_on.action, d_on.setup_type, d_on.gate, d_on.rr_ratio))
t.ok("switch off: the same breakout is WATCH, gate 'breakout' alone (it passed every other gate), no plan",
     d_off.action == "WATCH" and d_off.gate == "breakout" and d_off.gates == ("breakout",) and d_off.trade_plan is None
     and d_off.setup_type == "breakout", (d_off.action, d_off.gate, d_off.gates))
t.ok("...the levels and the confidence stay on record, the reason says why",
     (d_off.stop_price, d_off.target_price, d_off.rr_ratio, d_off.confidence)
     == (d_on.stop_price, d_on.target_price, d_on.rr_ratio, d_on.confidence)
     and d_off.reasons[0].startswith("MUA breakout đang tắt") and d_off.invalidation_reason is None, d_off.reasons)
summary_text = engine.render_summary(engine.QuantResult(
    symbol="BRK", as_of_date=AS_OF, last_bar_date=AS_OF, price=p_off.price, exchange="HOSE", action=d_off.action,
    confidence=d_off.confidence, setup_type=d_off.setup_type, market_regime=d_off.market_regime, risk_level=d_off.risk_level,
    data_quality=d_off.data_quality, reasons=d_off.reasons, stop_price=d_off.stop_price, target_price=d_off.target_price,
    rr_ratio=d_off.rr_ratio, invalidation_reason=d_off.invalidation_reason, gate=d_off.gate))
t.ok("the summary says the switch is off, not 'no clear trend', and gives no price zone",
     "MUA breakout đang tắt (đổi ở trang Bot giao dịch)" in summary_text and "Chưa đủ rõ xu hướng" not in summary_text
     and "Vùng mua" not in summary_text, summary_text)
h_on, h_off = policy.evaluate_policy(replace(p_on, is_holding=True)), policy.evaluate_policy(replace(p_off, is_holding=True))
t.ok("a holder: HOLD either way; switch off drops the plan to add, keeps the stop to watch",
     h_on.action == h_off.action == "HOLD" and h_on.trade_plan is not None and h_off.trade_plan is None
     and h_off.gate == "breakout" and h_off.invalidation_reason == h_on.invalidation_reason, (h_on.action, h_off.action))
inside = replace(p_on.enhanced, donchian=replace(p_on.enhanced.donchian, state="inside"))
pb_on = policy.evaluate_policy(replace(p_on, enhanced=inside))
pb_off = policy.evaluate_policy(replace(p_off, enhanced=inside))
t.ok("a pullback BUY of the same stock: unchanged by the switch",
     pb_on.action == pb_off.action == "BUY" and pb_on.setup_type == "pullback"
     and (pb_on.confidence, pb_on.stop_price, pb_on.target_price, pb_on.trade_plan)
     == (pb_off.confidence, pb_off.stop_price, pb_off.target_price, pb_off.trade_plan), (pb_on.action, pb_off.action))
ph = inputs(NEW_HIGH)
projected = replace(ON, breakout_projected_ok=1)
pr_on = policy.evaluate_policy(replace(ph, params=projected))
pr_off = policy.evaluate_policy(replace(ph, params=replace(projected, breakout_buy=0)))
t.ok("a breakout to new highs (breakout_projected_ok = 1): BUY on its 2R target, switched off too",
     policy.evaluate_policy(ph).gate == "resistance" and pr_on.action == "BUY" and pr_off.action == "WATCH"
     and pr_off.gate == "breakout", (pr_on.action, pr_off.action, pr_off.gate))
t.ok("the gate has a label and is the last one checked",
     list(policy.BUY_GATES)[-1] == "breakout" and policy.BUY_GATES["breakout"] == "MUA breakout đang tắt")

# ───────────── 2. what the shadow results follow ─────────────
alt = engine.lifted(p_off, d_off)
t.ok("lifted: a breakout switched off -> the same decision with the switch on (a BUY and its plan)",
     alt is not None and alt["action"] == "BUY" and alt["entry_high"] == d_on.trade_plan.entry_high
     and (alt["stop"], alt["target"], alt["rr"]) == (d_on.stop_price, d_on.target_price, d_on.rr_ratio), alt)
t.ok("lifted: nothing to lift on a BUY", engine.lifted(p_on, d_on) is None)
m_off = replace(inputs(BREAKOUT, VNI_OFF), params=OFF)
dm = policy.evaluate_policy(m_off)
alt_m = engine.lifted(m_off, dm)
t.ok("risk-off market AND switch off: the market is lifted, the switch still holds (not a 'BUY but for the market')",
     "market" in dm.gates and alt_m is not None and alt_m["action"] == "WATCH" and alt_m["gate"] == "breakout", (dm.gates, alt_m))
row = cd.decision_row("BRK", AS_OF, p_off, d_off, alt, "replay", OFF)
row_m = cd.decision_row("BRK", AS_OF, m_off, dm, alt_m, "replay", OFF)
t.ok("near miss: 'breakout' (blocked only by the switch); risk-off + switch off is no near miss",
     row["near"] == "breakout" and row_m["near"] == "" and cd.NEAR_VI["breakout"] == "MUA breakout đang tắt", (row["near"], row_m["near"]))
t.ok("its plan to follow is labelled 'breakout'", cd._plan_of(row) == ("breakout", alt["entry_high"], alt["stop"], alt["target"]))
market_row = {"action": "WATCH", "gate": "market", "alt_action": "BUY", "alt_entry_high": 10.2, "alt_stop": 9.5, "alt_target": 11.0}
t.ok("...a BUY blocked by the market keeps its label 'market' (rows stored before the switch too)",
     cd._plan_of(market_row)[0] == "market" and cd.near_kind(market_row, ON) == "market")
mkt = {**cd.decision_row("MKT", AS_OF, p_on, d_on, None, "replay", OFF), **market_row, "near": "market", "alt_confidence": 0.8}
summary = cd.scan_summary([row, mkt], day=AS_OF, kind="replay", params=OFF)
t.ok("the near list: the market first, then the switched-off breakouts",
     summary["gates"] == {"breakout": 1, "market": 1} and len(summary["near_list"]) == 2
     and summary["near_list"][0].startswith("MKT (chỉ vướng thị trường") and "BRK (MUA breakout đang tắt" in summary["near_list"][1],
     summary)
dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2026-09-01", periods=40)]
closes = [10.0 + 0.1 * i for i in range(40)]
rising = (dates, [x - 0.05 for x in closes], [x + 0.2 for x in closes], [x - 0.3 for x in closes], closes)
vni = (dates, [1000.0] * 40, [1001.0] * 40, [999.0] * 40, [1000.0 + i for i in range(40)])
shadow = cd.outcome({"day": dates[0], "action": "WATCH", "gate": "breakout", "alt_action": "BUY", "alt_entry_high": 10.2,
                     "alt_stop": 9.5, "alt_target": 11.0}, rising, vni, today=dates[-1], params=ON)
t.ok("shadow: a switched-off breakout follows its plan like the paper book would have",
     shadow.get("plan") == "breakout" and shadow.get("plan_outcome") == "TP", shadow)
rows = ([{"day": f"2026-01-{i % 28 + 1:02d}", "symbol": f"B{i}", "source": "screen", "action": "WATCH", "bias": "bullish",
          "gate": "breakout", "near": "breakout", "ret_10": -2.0, "vni_10": 0.0, "plan_outcome": "SL", "plan_r": -1.0}
         for i in range(5)]
        + [{"day": "2026-01-05", "symbol": "M", "source": "screen", "action": "WATCH", "bias": "bullish", "gate": "market",
            "near": "market", "ret_10": 1.0, "vni_10": 0.0, "plan_outcome": "TP", "plan_r": 1.5}])
g = cd.stats(rows)["groups"]
t.ok("stats: a group of their own, with the plan's R; the market group keeps only its own",
     "breakout" in cd.PLAN_GROUPS and g["breakout"]["ret"]["n"] == 5 and g["breakout"]["plan"]["mean"] == -1.0
     and g["market"]["ret"]["n"] == 1, g["breakout"])
t.ok("the note while the switch is off, none while on",
     cd.breakout_note(ON) == "" and "MUA breakout của Quant Engine đang tắt" in cd.breakout_note(OFF))

# ───────────── 3. the calibration ─────────────
t.ok("a hand switch: bounded 0/1, never among the tunables",
     "breakout_buy" in qp.MANUAL and "breakout_buy" not in qp.TUNABLE and qp.coerce({"breakout_buy": 5}).breakout_buy == 1
     and qp.coerce({"breakout_buy": 0}).breakout_buy == 0 and qp.coerce({}).breakout_buy == 1)
t.ok("no challenger of the night moves it",
     all(trial.params.breakout_buy == 0 for trial in cal.neighbours(OFF)))
qp.save(OFF, note="test")
accepted = replace(ON, confidence_buy_min=0.8)          # a challenger built before the switch was turned
cal._put_in_force(accepted, qp.TUNABLE, "test")
now = qp.active()
t.ok("an accepted change is saved onto the set in force: the switch turned meanwhile stays off",
     now.confidence_buy_min == 0.8 and now.breakout_buy == 0, (now.confidence_buy_min, now.breakout_buy))
qp.save(ON, note="test")

# ───────────── 4. the page /bot ─────────────
if importlib.util.find_spec("httpx") is None:
    print("(bỏ phần trang web: thiếu httpx - pip install -r requirements-dev.txt)")
else:
    from _web_harness import SETTINGS, JobQueue, Watchlist, ZoomChannel  # noqa: E402
    from fastapi.testclient import TestClient  # noqa: E402

    from vnbot.trader import TradingBot  # noqa: E402
    from vnbot.web import create_app  # noqa: E402

    wl = Watchlist(SETTINGS)
    app = create_app(SETTINGS, JobQueue(SETTINGS), wl, trader=TradingBot(SETTINGS, wl, notify=lambda text: None),
                     zoom=ZoomChannel(state_path=SETTINGS.data_dir / "zoom_brk.json", start=False))
    c = TestClient(app, raise_server_exceptions=True)
    AUTH, SAME = ("x", "s3cret-pass"), {"origin": "http://testserver"}
    page = c.get("/bot", auth=AUTH).text
    t.ok("/bot: the switch, on, with the button to turn it off",
         "Công tắc Quant Engine" in page and "đang bật" in page and "Tắt MUA breakout" in page
         and 'action="/bot/breakout"' in page, page[:200])
    t.ok("POST without the password -> 401; cross-site -> 403; the switch untouched",
         c.post("/bot/breakout", data={"on": "0"}).status_code == 401
         and c.post("/bot/breakout", auth=AUTH, data={"on": "0"}, headers={"origin": "https://evil.example"}).status_code == 403
         and qp.active().breakout_buy == 1)
    r = c.post("/bot/breakout", auth=AUTH, data={"on": "0"}, headers=SAME, follow_redirects=False)
    t.ok("turned off: saved for every later run (quant_params.json), the page says so",
         r.status_code == 303 and "/bot?msg=" in r.headers["location"] and qp.active().breakout_buy == 0
         and qp._path().exists(), (r.status_code, r.headers.get("location")))
    page = c.get("/bot", auth=AUTH).text
    t.ok("/bot: off, the button to turn it back on, the row marked as changed by hand",
         "đang tắt" in page and "Bật lại MUA breakout" in page and "<code>breakout_buy</code>" in page and "đổi tay" in page)
    c.post("/bot/breakout", auth=AUTH, data={"on": "1"}, headers=SAME)
    t.ok("turned back on", qp.active().breakout_buy == 1)

t.done()
