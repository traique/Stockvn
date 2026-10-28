"""The history store and "Vì sao chưa MUA" (vnbot.store, vnbot.chandoan, vnbot.eventlog): the
SQLite store, the gate the engine records, the shadow results, the statistics, the masked
activity log, the pages /tinhieu and /dulieu, the /vikhongmua command (offline)."""
import io
import json
import logging
import math
import os
import sqlite3
import types
from dataclasses import replace

import _common  # noqa: F401 - clean env, no .env, offline, temp folder, repo on sys.path

import pandas as pd  # noqa: E402

t = _common.Checks()

from vnbot import store as st  # noqa: E402

DATA = _common.ROOT / "store-data"

# ───────────── 1. the store (SQLite) ─────────────
s = st.Store(DATA, url="")
t.ok("a new store: every table, SQLite under the data folder", not s.pg and s.path == DATA / "bot.db"
     and set(s.counts()) == set(st.SCHEMA) and all(n == 0 for n in s.counts().values()))
t.ok("opening again changes nothing (idempotent schema)", st.Store(DATA, url="").counts() == s.counts())

old = DATA / "old"
old.mkdir()
con = sqlite3.connect(old / "bot.db")
con.execute("CREATE TABLE signals (id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, symbol TEXT, source TEXT, "
            "action TEXT, UNIQUE (day, symbol, source))")
con.commit()
con.close()
migrated = st.Store(old, url="")
t.ok("an older table gets the columns added since (lightweight migration)",
     {"gate", "near", "ret_10", "plan_r"} <= migrated._columns("signals"))
migrated.close()

s.add_signals([{"day": "2026-10-27", "symbol": "FPT", "source": "screen", "action": "WATCH", "confidence": 0.7,
                "holding": False, "gates": "confidence,market"}])
row = s.signals(day="2026-10-27")[0]
s.update("signals", row["id"], {"ret_10": 2.5, "status": "DONE"})
s.add_signals([{"day": "2026-10-27", "symbol": "FPT", "source": "screen", "action": "BUY", "confidence": float("nan"),
                "holding": True}])
again = s.signals(day="2026-10-27")
t.ok("one row per (day, symbol, source): a new decision replaces it, to be resolved again",
     len(again) == 1 and again[0]["action"] == "BUY" and again[0]["status"] == "PENDING" and again[0]["ret_10"] is None)
t.ok("NaN is stored as NULL, a bool as 0/1", again[0]["confidence"] is None and again[0]["holding"] == 1)
try:
    s.upsert("signals", [{"day": "d", "symbol": "x", "source": "y", "x; DROP TABLE kv": 1}])
    refused = False
except ValueError:
    refused = True
t.ok("an unknown column is refused (never formatted into the SQL)", refused and "kv" in s.counts())

s.add_signals([
    {"day": "2026-10-20", "symbol": "HPG", "source": "trade", "action": "WATCH", "holding": False},
    {"day": "2026-10-20", "symbol": "HPG", "source": "screen", "action": "NO_TRADE", "holding": False},
    {"day": "2026-10-20", "symbol": "HPG", "source": "replay", "action": "WATCH", "holding": False},
    {"day": "2026-10-20", "symbol": "VCB", "source": "trade", "action": "HOLD", "holding": True},
    {"day": "2026-10-20", "symbol": "SSI", "source": "replay", "action": "WATCH", "holding": False},
])
rows = {(r["day"], r["symbol"]): r for r in s.outcome_rows("2026-01-01")}
t.ok("statistics rows: one per (day, symbol), the screen's first, then a replay's; held stocks left out",
     rows[("2026-10-20", "HPG")]["source"] == "screen" and rows[("2026-10-20", "SSI")]["source"] == "replay"
     and ("2026-10-20", "VCB") not in rows, sorted(rows))

s.add_scan({"day": "2026-10-27", "kind": "screen", "scanned": 3, "gates": {"bearish": 2}, "near_list": ["FPT (x)"]})
s.add_scan({"day": "2026-10-27", "kind": "screen", "scanned": 4, "gates": {"bearish": 3}})
scans = s.scans()
t.ok("a scan per (day, kind), its counts as JSON both ways", len(scans) == 1 and scans[0]["scanned"] == 4
     and scans[0]["gates"] == {"bearish": 3})
s.add_equity([{"day": "2026-10-27", "book": "engine", "nav": 1e9}])
s.add_equity([{"day": "2026-10-27", "book": "engine", "nav": 1.1e9}])
s.add_fills([{"book": "engine", "day": "2026-10-27", "symbol": "FPT", "side": "BUY", "qty": 100, "price": 55000.0}] * 2)
t.ok("NAV upserted per (day, book); a fill stored once", s.counts()["equity"] == 1 and s.counts()["fills"] == 1
     and s.select("SELECT nav FROM equity")[0]["nav"] == 1.1e9)
s.add_events([(1.0, "INFO", "a", "old line"), (4e9, "WARNING", "b", "50% done ?")])
t.ok("events: filtered by level and text (a % in the text is plain text)",
     [e["msg"] for e in s.events(query="50%")] == ["50% done ?"] and len(s.events(level="INFO")) == 1)
s.set("k", {"a": [1, 2]})
t.ok("kv: JSON values", s.get("k") == {"a": [1, 2]} and s.get("nope", 5) == 5)
s.add_signals([{"day": "2020-01-02", "symbol": "OLD", "source": "screen", "action": "WATCH"}])
before = s.counts()["signals"]
done = s.prune("2026-10-28")
t.ok("prune: old events and history beyond STORE_KEEP_DAYS go, the rest stays",
     done["events"] == 1 and done["signals"] == 1 and s.counts()["signals"] == before - 1 and s.counts()["events"] == 1, done)
os.environ["STORE_KEEP_DAYS"] = "5"          # below the floor: clamped to 60
t.ok("keep days are bounded", st.keep_days() == 60 and st.events_days() == 14)
os.environ.pop("STORE_KEEP_DAYS")

pg = st._DB.__new__(st._DB)
pg.pg = True
t.ok("the same SQL on Postgres: ? becomes %s", pg.sql("SELECT a FROM t WHERE b = ? AND c = ?") == "SELECT a FROM t WHERE b = %s AND c = %s")
t.ok("the password of a postgres URL is never shown", st._masked_url("postgresql://u:secret@h:5432/db") == "postgresql://u:***@h:5432/db")
fallback = st.Store(DATA / "fb", url="postgresql://u:secret@127.0.0.1:1/db?connect_timeout=1")
t.ok("DATABASE_URL that does not connect: SQLite instead, said, no password in it",
     not fallback.pg and fallback.fallback and "SQLite" in fallback.describe() and "secret" not in fallback.describe(),
     fallback.describe())
fallback.close()
t.ok("safe(): a failing store call is logged, never raised", st.safe("x", lambda: 1 / 0, "d") == "d")
os.environ["STORE"] = "0"
t.ok("STORE=0: no store at all", st.open_store(DATA) is None)
os.environ.pop("STORE")
s.close()

# ───────────── 2. the gate the engine records ─────────────
from tradingagents.quant import engine, policy  # noqa: E402

days = pd.bdate_range("2025-06-02", periods=300)


def frame(closes, vols=None):
    closes = [float(c) for c in closes]
    return pd.DataFrame({"Date": days[:len(closes)], "Open": [closes[0]] + closes[:-1],
                         "High": [c * 1.01 for c in closes], "Low": [c * 0.99 for c in closes], "Close": closes,
                         "Volume": vols or [1_000_000 + 1000 * (i % 7) for i in range(len(closes))]})


up = frame([20000 * 1.003 ** i * (1 + 0.004 * math.sin(i / 3)) for i in range(300)])
down = frame([40000 * 0.997 ** i for i in range(300)])
vni_c, vni_v = [1500.0], [100.0]
for i in range(1, 300):            # flat, then 25 sessions of distribution days (down 0.3% on higher volume)
    if i >= 275 and i % 2:
        vni_c.append(vni_c[-1] * 0.997)
        vni_v.append(vni_v[-1] * 1.3)
    else:
        vni_c.append(vni_c[-1] * 1.0035)
        vni_v.append(100.0)
vni_off = frame(vni_c, vni_v)
vni_on = frame([1500 * 1.001 ** i for i in range(300)], [100.0 + (i % 2) for i in range(300)])
as_of = days[-1].strftime("%Y-%m-%d")
p_up = engine.build_inputs("AAA", as_of, up, vni_off, exchange="HOSE", params=policy.DEFAULT_PARAMS).inputs
d_up = policy.evaluate_policy(p_up)
t.ok("a risk-off market: the regime is risk_off and the market is one of the stock's failed gates",
     d_up.market_regime == "risk_off" and "market" in d_up.gates and d_up.bias == "bullish", (d_up.market_regime, d_up.gates, d_up.bias))
same = policy.evaluate_policy(p_up, regime_override=None)
t.ok("no override = the bot's decision, unchanged", (same.action, same.confidence, same.gates) == (d_up.action, d_up.confidence, d_up.gates))
lifted = engine.lifted(p_up, d_up)
cf = policy.evaluate_policy(replace(p_up, is_holding=False), regime_override="neutral")
t.ok("lifting the market: the same stock decided with a neutral regime (its own gates still apply)",
     lifted is not None and lifted["action"] == cf.action and cf.market_regime == "neutral"
     and "market" not in cf.gates and cf.confidence > d_up.confidence, (lifted, cf.confidence, d_up.confidence))
t.ok("...and the decision says nothing about distribution days it did not use", not any("ngày phân phối" in r for r in cf.reasons))
p_down = engine.build_inputs("BBB", as_of, down, vni_on, exchange="HOSE", params=policy.DEFAULT_PARAMS).inputs
d_down = policy.evaluate_policy(p_down)
t.ok("a falling stock: the first gate is its own trend, nothing to lift",
     d_down.gate == "bearish" and engine.lifted(p_down, d_down) is None, (d_down.gate, d_down.gates))
t.ok("gate '' only for a BUY (or a holder's qualified HOLD); every code has a label",
     (d_up.gate == "") == (d_up.action == "BUY") and all(g in policy.BUY_GATES for g in d_up.gates + d_down.gates))
bad = replace(p_up, quality=replace(p_up.quality, status="bad"))
t.ok("unusable data: gate 'data'", policy.evaluate_policy(bad).gate == "data")

# ───────────── 3. near misses, the plan, the shadow results ─────────────
from vnbot import chandoan as cd  # noqa: E402

P = policy.DEFAULT_PARAMS
t.ok("near: blocked only by the market", cd.near_kind({"action": "WATCH", "alt_action": "BUY"}, P) == "market")
t.ok("near: confidence 0.71 (lifted) is close to 0.75, 0.60 is not",
     cd.near_kind({"action": "WATCH", "alt_action": "WATCH", "alt_gate": "confidence", "alt_confidence": 0.71}, P) == "confidence"
     and cd.near_kind({"action": "WATCH", "alt_action": "WATCH", "alt_gate": "confidence", "alt_confidence": 0.60}, P) == "")
t.ok("near: R:R 1.3 of 1.5; a breakout without resistance; a BUY is not a near miss",
     cd.near_kind({"action": "WATCH", "gate": "rr", "rr": 1.3}, P) == "rr"
     and cd.near_kind({"action": "WATCH", "gate": "resistance"}, P) == "resistance"
     and cd.near_kind({"action": "BUY", "alt_action": "BUY"}, P) == "")

o = [100.0] * 40
h, lo, c = [101.0] * 40, [99.0] * 40, [100.0] * 40
t.ok("plan: the limit is not reached on the next session -> NOFILL",
     cd.simulate_plan(o, h, [100.5] + lo[1:], c, 0, 100.0, 95.0, 110.0)[0] == "NOFILL")
hh = list(h)
hh[3] = 112.0
out = cd.simulate_plan(o, hh, lo, c, 0, 100.5, 95.0, 110.0)
t.ok("plan: filled at the open under the limit, target hit on T+3 -> TP, in R of the planned risk after costs",
     out[0] == "TP" and out[2] == 3 and abs(out[1] - (110.0 - 100.0 - 0.6) / 5.5) < 1e-3, out)
ll = list(lo)
ll[1] = 90.0                            # T+1: under the stop, not sellable yet
t.ok("plan: a stop touched before T+2 is not a sale (the shares have not arrived)",
     cd.simulate_plan(o, h, ll, c, 0, 100.5, 95.0, 110.0, max_hold=10)[0] == "TIMEOUT")
oo = list(o)
oo[2], ll2 = 90.0, list(lo)
ll2[2] = 89.0
gap = cd.simulate_plan(oo, h, ll2, c, 0, 100.5, 95.0, 110.0)
t.ok("plan: a gap under the stop sells at the open, below -1R", gap[0] == "SL" and abs(gap[1] - (90.0 - 100.0 - 0.6) / 5.5) < 1e-3, gap)
t.ok("plan: not enough sessions yet -> None (still pending)", cd.simulate_plan(o[:5], h[:5], lo[:5], c[:5], 0, 100.5, 95.0, 110.0) is None)

dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2026-09-01", periods=40)]
stock = (dates, [10.0] * 40, [10.5] * 40, [9.8] * 40, [10.0 + 0.1 * i for i in range(40)])
vni = (dates, [1000.0] * 40, [1001.0] * 40, [999.0] * 40, [1000.0 + i for i in range(40)])
res = cd.outcome({"day": dates[0], "action": "WATCH"}, stock, vni, today=dates[-1])
t.ok("shadow: returns 5/10/20 sessions from the next open, VNINDEX beside them; DONE without a plan",
     res["status"] == "DONE" and res["entry_px"] == 10.0 and abs(res["ret_10"] - 10.0) < 1e-6
     and abs(res["vni_10"] - 1.0) < 1e-6 and abs(res["ret_20"] - 20.0) < 1e-6, res)
part = cd.outcome({"day": dates[25], "action": "WATCH"}, stock, vni, today=dates[-1])
t.ok("shadow: too recent for 20 sessions -> the known horizons, still PENDING",
     part["status"] == "PENDING" and "ret_10" in part and "ret_20" not in part, part)
t.ok("shadow: no session after the day yet -> nothing to say; after 60 days without bars -> NODATA",
     cd.outcome({"day": dates[-1], "action": "WATCH"}, stock, vni, today=dates[-1]) is None
     and cd.outcome({"day": dates[-1], "action": "WATCH"}, stock, vni, today="2027-01-30") == {"status": "NODATA"})
closes = [10.0 + 0.1 * i for i in range(40)]
rising = (dates, [x - 0.05 for x in closes], [x + 0.2 for x in closes], [x - 0.3 for x in closes], closes)
planned = cd.outcome({"day": dates[0], "action": "WATCH", "alt_action": "BUY", "alt_entry_high": 10.2, "alt_stop": 9.5,
                      "alt_target": 11.0}, rising, vni, today=dates[-1], params=P)
t.ok("shadow: a BUY blocked by the market follows its own plan (filled at the open, target on session 8)",
     planned.get("plan") == "market" and planned.get("plan_outcome") == "TP" and planned.get("plan_days") == 7
     and abs(planned["plan_r"] - (11.0 - 10.05 - 10.05 * 0.6 / 100) / (10.2 - 9.5)) < 1e-3, planned)

se, tv = cd.t_stat([1.0, 2.0, 3.0, 4.0])
t.ok("t statistic: mean / standard error", abs(tv - 2.5 / (math.sqrt(5 / 3) / 2)) < 1e-9)
rows = ([{"day": f"2026-01-{i % 28 + 1:02d}", "symbol": f"S{i}", "source": "screen", "action": "WATCH", "bias": "bullish",
          "gate": "market", "near": "market", "ret_10": 3.0 + (i % 3), "vni_10": 1.0, "plan_outcome": "SL", "plan_r": -1.0}
         for i in range(40)]
        + [{"day": "2026-01-05", "symbol": "Z", "source": "screen", "action": "BUY", "bias": "bullish", "gate": "",
            "near": "", "ret_10": -2.0, "vni_10": 0.0}])
stx = cd.stats(rows)
g = stx["groups"]
expected = sum(3.0 + (i % 3) - 1.0 for i in range(40)) / 40
t.ok("stats: the groups, the excess over VNINDEX, the plan R",
     g["market"]["ret"]["n"] == 40 and abs(g["market"]["excess"]["mean"] - expected) < 0.01 and g["market"]["plan"]["mean"] == -1.0
     and g["buy"]["ret"]["n"] == 1 and g["all"]["ret"]["n"] == 41 and stx["gates"]["market"]["ret"]["n"] == 40)
t.ok("verdict: too few rows say so; enough with |t| >= 2 says better/worse",
     "cần ≥ 30" in cd.verdict(g["buy"]["excess"]) and "tốt hơn rõ rệt" in cd.verdict(g["market"]["excess"]))

# ───────────── 4. recording a screen, the texts, the command ─────────────
s = st.open_store(DATA / "live")
vni_frame = frame([1800 - i for i in range(300)], [100.0 + (i % 2) * 50 for i in range(300)])
cd._vnindex = lambda day: vni_frame


def result(sym, action, gate="", bias="bullish", alt=None, conf=0.6, holding=False):
    return types.SimpleNamespace(
        symbol=sym, last_bar_date="2026-10-27", as_of_date="2026-10-27", action=action, confidence=conf,
        setup_type="pullback", market_regime="risk_off", risk_level="medium", data_quality="ok",
        reasons=["VNINDEX có 9 ngày phân phối", "khối ngoại bán ròng toàn thị trường 12/15 phiên - hạ regime một bậc"],
        price=10000.0, entry_low=None, entry_high=None, stop_price=None, target_price=None, target2=None, rr_ratio=None,
        is_holding=holding, gate=gate, gates=[gate] if gate else [], bias=bias, if_lifted=alt)


results = {"AAA": result("AAA", "WATCH", "market", alt={"action": "BUY", "confidence": 0.8, "gate": "", "stop": 9500.0,
                                                        "target": 11500.0, "rr": 2.0, "entry_high": 10050.0}),
           "BBB": result("BBB", "NO_TRADE", "bearish", bias="bearish", conf=0.2),
           "CCC": result("CCC", "WATCH", "confidence", alt={"action": "WATCH", "confidence": 0.72, "gate": "confidence"})}
scan = cd.record_screen(results, ["DDD: lỗi"])
t.ok("a screen recorded: every stock, the funnel, the near misses (market first)",
     scan["scanned"] == 3 and scan["gates"] == {"market": 1, "bearish": 1, "confidence": 1}
     and scan["near"] == 2 and scan["near_list"][0].startswith("AAA (chỉ vướng thị trường") and scan["errors"] == ["DDD: lỗi"], scan)
mrow = s.market(1)[0]
t.ok("...and VNINDEX's row, with the engine's own regime and its money-flow reason",
     mrow["day"] == "2026-10-27" and mrow["regime"] == "risk_off" and mrow["factor_on"] == 0, mrow)
line = cd.funnel_line(scan, mrow)
t.ok("the paragraph for the 15:30 message", line.startswith("🔎 Vì sao chưa có MUA") and "Suýt MUA: AAA" in line
     and "/vikhongmua" in line, line)
text = cd.why_text(s)
t.ok("/vikhongmua: the funnel, the market, the MA filters, the near misses, the shadow results",
     all(x in text for x in ("phiên 2026-10-27", "Phòng thủ: VNINDEX dưới MA200", "Cổng chặn MUA", "Suýt MUA (2)",
                             "Kết quả bóng", "không tự nới")), text)
t.ok("/vikhongmua AAA: the stock's recorded sessions", "AAA: 1 phiên" in cd.command_text("aaa", watchlist=[]) )
calls = []
cd.replay = lambda symbols, sessions, **kw: (calls.append((len(symbols), sessions, kw["as_of"])) or
                                            {"rows": 3, "days": 2, "symbols": 1, "from": "a", "to": "b", "resolved": 1})
import tradingagents.dataflows.money_flows as mf  # noqa: E402
mf.index_members = lambda group="VN30": ["FPT", "HPG"] if group == "VN30" else ["FPT", "VNM"]
sent = []
msg = cd.command_text("dunglai 400 vn100", watchlist=["SSI"], notify=sent.append)
import time as _time  # noqa: E402
for _ in range(50):
    if sent:
        break
    _time.sleep(0.05)
t.ok("/vikhongmua dunglai: a background rebuild (capped at 250 sessions, VN100 + VN30 + watchlist), status kept, owner told",
     "Đang dựng lại 250 phiên cho 4 mã" in msg and calls and calls[0][:2] == (4, 250) and sent
     and sent[0].startswith("✅ Đã dựng lại") and cd.replay_status(s)["state"] == "done", (msg, calls, sent))

from tradingagents.trading.ledger import Fill, Ledger  # noqa: E402
book = Ledger(cash=5e8, start_cash=1e9)
book.history = [{"date": "2026-10-24", "nav": 1e9, "benchmark": 1774.0}, {"date": "2026-10-27", "nav": 1.01e9, "benchmark": 1761.0}]
book.fills = [Fill("2026-10-27", "FPT", "BUY", 100, 55000.0, 8.3, "BUY conf 0.80")]
from vnbot.trader import TradingBot  # noqa: E402
TradingBot._record_history("2026-10-27", {"engine": book}, types.SimpleNamespace(market_ok=True), None)
eq = s.select("SELECT day, nav, positions FROM equity ORDER BY day")
t.ok("the trading run: each book's NAV history and fills; the books' own filter state on the market row",
     [r["day"] for r in eq] == ["2026-10-24", "2026-10-27"] and s.counts()["fills"] == 1
     and s.market(1)[0]["factor_on"] == 1 and s.market(1)[0]["regime"] == "risk_off", (eq, s.market(1)[0]))

# ───────────── 5. the activity log, secrets masked ─────────────
os.environ.update({"TELEGRAM_TOKEN": "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawQ", "ROUTER9_API_KEY": "sk-uni-verysecretkey123",
                   "DATABASE_URL": "postgresql://postgres.ref:Sup3rS3cret@aws-0.pooler.supabase.com:5432/postgres"})
from vnbot import eventlog  # noqa: E402
eventlog.refresh_secrets()
raw = ("url https://api.telegram.org/bot123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawQ/getUpdates key sk-uni-verysecretkey123 "
       "db postgresql://postgres.ref:Sup3rS3cret@aws-0.pooler.supabase.com:5432/postgres auth Bearer abcdefghijkl12345")
masked = eventlog.redact(raw)
t.ok("redact: token, key, database password, bearer token", all(x not in masked for x in
     ("AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawQ", "verysecretkey", "Sup3rS3cret", "abcdefghijkl12345")) and "***" in masked, masked)
buf = io.StringIO()
console = logging.StreamHandler(buf)
root = logging.getLogger()
root.addHandler(console)
root.setLevel(logging.INFO)
handler = eventlog.install(s)
logging.getLogger("vnbot.some").warning("calling %s", raw)
try:
    raise RuntimeError("boom Sup3rS3cret")
except RuntimeError:
    logging.getLogger("vnbot.some").exception("failed")
logging.getLogger("uvicorn.access").info("GET /health")
handler.flush_now()
stored = s.events(limit=10)
t.ok("every handler sees the line masked: the console and the store", "Sup3rS3cret" not in buf.getvalue()
     and "AAHdqTcv" not in buf.getvalue() and stored and all("Sup3rS3cret" not in e["msg"] for e in stored), buf.getvalue()[-300:])
t.ok("the store keeps INFO+ with the traceback of an error, not request noise",
     any(e["level"] == "ERROR" and "RuntimeError" in e["msg"] for e in stored) and not any(e["src"].startswith("uvicorn") for e in stored))
t.ok("installed once", eventlog.install(s) is handler)
root.removeHandler(console)
root.removeHandler(handler)
handler.close()

# ───────────── 6. the pages and the command ─────────────
st.reset()
_common.need("httpx")
os.environ.pop("DATABASE_URL")
import _web_harness as h  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

app, *_ = h.build(trader=False)
web = TestClient(app)
AUTH = ("x", h.SETTINGS.web_password)
t.ok("the pages need the password", web.get("/tinhieu").status_code == 401 and web.get("/dulieu").status_code == 401
     and web.get("/dulieu/xuat/signals.csv").status_code == 401)
page = web.get("/tinhieu", auth=AUTH)
t.ok("/tinhieu on an empty store says how to start", page.status_code == 200 and "Chưa có lượt quét nào" in page.text
     and 'href="/tinhieu" class="nav-item on" aria-current="page"' in page.text, page.text[-400:])
db = st.current()
cd._vnindex = lambda day: vni_frame
cd.record_screen(results, [])
page = web.get("/tinhieu", auth=AUTH).text
t.ok("/tinhieu: the funnel, the near misses, the market, every stock (labelled for a phone)",
     all(x in page for x in ("Cổng chặn MUA", "AAA (chỉ vướng thị trường", "Thị trường chung", "Quyết định từng mã",
                             'data-label="Cổng chặn đầu tiên"', "Kết quả bóng theo nhóm")), page[-500:])
data = web.get("/dulieu", auth=AUTH)
t.ok("/dulieu: the store, the downloads, the rebuild form, the log", data.status_code == 200
     and all(x in data.text for x in ("SQLite", "/dulieu/xuat/signals.csv", "Dựng lại lịch sử", "Nhật ký hoạt động"))
     and 'href="/dulieu" class="nav-item on"' in data.text)
csv_r = web.get("/dulieu/xuat/signals.csv", auth=AUTH)
t.ok("CSV for Excel: UTF-8 with BOM, a header, a readable time column", csv_r.status_code == 200
     and csv_r.content.startswith("﻿".encode()) and "symbol" in csv_r.text.splitlines()[0]
     and "ts_time" in csv_r.text.splitlines()[0] and "attachment" in csv_r.headers["content-disposition"])
t.ok("JSON of one table and the full backup; an unknown table is 404",
     web.get("/dulieu/xuat/scans.json", auth=AUTH).json()[0]["kind"] == "screen"
     and set(web.get("/dulieu/xuat/all.json", auth=AUTH).json()) == set(st.EXPORTS)
     and web.get("/dulieu/xuat/kv.csv", auth=AUTH).status_code == 404)
t.ok("the rebuild form refuses a post from another site", web.post("/dulieu/dunglai", data={"sessions": "30"}, auth=AUTH,
                                                                   headers={"origin": "https://evil.example"}).status_code == 403)
started = []
cd.start_replay = lambda symbols, n, **kw: started.append((len(symbols), n)) or "Đang dựng lại."
r = web.post("/dulieu/dunglai", data={"sessions": "abc", "universe": "vn30"}, auth=AUTH, follow_redirects=False)
t.ok("the rebuild from the page: started in the background, back to the page with the message",
     r.status_code == 303 and r.headers["location"].startswith("/dulieu?msg=") and started == [(2, 120)], (r.headers, started))
from vnbot import config  # noqa: E402
t.ok("the settings: a 'Lưu trữ dữ liệu' group; DATABASE_URL is a secret (set / not set only)",
     {f.key for f in next(g for g in config.GROUPS if g.key == "luutru").fields} == {"STORE", "STORE_KEEP_DAYS", "EVENTS_KEEP_DAYS"}
     and "DATABASE_URL" in dict(config.SECRETS))

from vnbot import telegram_bot as tb  # noqa: E402
from vnbot.jobs import JobQueue  # noqa: E402
from vnbot.service import Watchlist  # noqa: E402
os.environ["TELEGRAM_ALLOWED_IDS"] = "111"
from vnbot import settings as settings_module  # noqa: E402
S2 = settings_module.load()
bot = tb.TelegramBot(S2, JobQueue(S2), Watchlist(S2))
replies = []
bot.send = lambda chat, text: replies.append(text)
bot._handle({"chat": {"id": 111}, "from": {"id": 111}, "text": "/vikhongmua"})
bot._handle({"chat": {"id": 111}, "from": {"id": 111}, "text": "/chandoan AAA"})
t.ok("Telegram /vikhongmua and /chandoan MÃ", len(replies) == 2 and replies[0].startswith("🔎 Vì sao chưa có MUA")
     and "AAA" in replies[1], replies)
t.ok("the help lists /vikhongmua", "/vikhongmua" in bot._help())

t.done()
