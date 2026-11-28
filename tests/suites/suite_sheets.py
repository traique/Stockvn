"""The copy on Google Sheets (vnbot.sheets, offline with a fake HTTP session): off without SHEETS_ID /
SHEETS_SA_JSON (raw JSON or base64), the RS256 assertion of the service account checked with its
public key, the token kept, the tabs added once with their header, one append per tab per cycle,
a rerun writes no row twice, a failed tab keeps its cursor and is written next cycle, every text
cell redacted, the rows of each tab (books, ETF, shadow results, AI pick, summary), /bangtinh."""
import base64
import json
import os
import time

import _common  # noqa: F401 - clean env, no .env, offline, temp folder, repo on sys.path

os.environ["TRADINGAGENTS_PARAMS_PATH"] = str(_common.ROOT / "quant_params.json")

import pandas as pd  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import padding, rsa  # noqa: E402

from tradingagents.trading.ledger import Fill, Ledger  # noqa: E402
from vnbot import ai_pick as cp, etf_bench, sheets as sh, store as st  # noqa: E402

t = _common.Checks()
DATA = _common.ROOT / "sheets-data"
(DATA / "results").mkdir(parents=True)

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
ACCOUNT = {"type": "service_account", "client_email": "botlab@proj.iam.gserviceaccount.com", "private_key": PEM,
           "token_uri": "https://oauth2.googleapis.com/token", "private_key_id": "k1"}


def env(**values):
    for key in ("SHEETS_ID", "SHEETS_SA_JSON"):
        os.environ.pop(key, None)
    os.environ.update(values)


# ───────────── 1. on or off ─────────────
env()
t.ok("nothing set: off, and why", not sh.enabled() and sh.why_off() == "thiếu SHEETS_ID")
env(SHEETS_ID="1abc")
t.ok("only the id: off", sh.why_off() == "thiếu SHEETS_SA_JSON")
env(SHEETS_ID="1abc", SHEETS_SA_JSON="not json")
t.ok("a key that is not the account's JSON: off, said", "service account" in sh.why_off() and sh.account_json() is None)
env(SHEETS_ID="1abc", SHEETS_SA_JSON=json.dumps(ACCOUNT))
t.ok("the JSON itself: on", sh.enabled() and sh.account_json()["client_email"] == ACCOUNT["client_email"])
env(SHEETS_ID="1abc", SHEETS_SA_JSON=base64.b64encode(json.dumps(ACCOUNT).encode()).decode())
t.ok("the JSON in base64 (one line on the panel): on", sh.enabled() and sh.account_json()["private_key"] == PEM)
t.ok("the status text while off / on", "tắt (thiếu SHEETS_ID)" in sh.status_text(DATA) if not sh.sheet_id() else
     "bật, ghi lúc 18:05" in sh.status_text(DATA))

# ───────────── 2. the assertion and the token ─────────────
jwt = sh.sign_jwt(ACCOUNT, now=1_700_000_000)
head, claims, sig = jwt.split(".")


def unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


KEY.public_key().verify(unb64(sig), f"{head}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256())
body = json.loads(unb64(claims))
t.ok("RS256 JWT: header, the claims (issuer, scope, audience, one hour), the signature checks with the public key",
     json.loads(unb64(head)) == {"alg": "RS256", "typ": "JWT"} and body["iss"] == ACCOUNT["client_email"]
     and body["scope"] == sh.SCOPE and body["aud"] == sh.TOKEN_URL and body["exp"] - body["iat"] == 3600 and body["iat"] == 1_700_000_000)


class Resp:
    def __init__(self, status, data=None, text=""):
        self.status_code, self._data, self.text = status, data, text or (json.dumps(data) if data is not None else "")

    def json(self):
        return self._data


class Session:
    """Google's two endpoints, as far as the exporter uses them; ``fail`` = tabs whose append fails."""

    def __init__(self):
        self.calls, self.fail, self.tabs, self.values = [], set(), {"Sheet1", "proposals"}, {}
        self.tokens = 0

    def post(self, url, data=None, timeout=None, **kw):
        self.calls.append(("token", url))
        self.tokens += 1
        assert "assertion" in data and data["grant_type"].endswith("jwt-bearer")
        return Resp(200, {"access_token": f"ya29.{self.tokens}", "expires_in": 3599})

    def request(self, method, url, headers=None, params=None, timeout=None, data=None):
        assert headers["Authorization"].startswith("Bearer ya29.")
        body = json.loads(data) if data else None
        self.calls.append((method, url.split("/spreadsheets/")[-1], params, body))
        if url.endswith(":batchUpdate"):
            self.tabs.add(body["requests"][0]["addSheet"]["properties"]["title"])
            return Resp(200, {})
        if "/values/" in url:
            tab = url.split("/values/")[-1].split("%21")[0]
            if url.endswith(":append"):
                if tab in self.fail:
                    return Resp(500, text="backend error")
                self.values.setdefault(tab, []).extend(body["values"])
            else:
                self.values.setdefault(tab, []).insert(0, body["values"][0])
            return Resp(200, {})
        return Resp(200, {"sheets": [{"properties": {"title": name}} for name in sorted(self.tabs)]})


session = Session()
token = sh.Token(ACCOUNT, session)
first = token.get()
t.ok("the token: fetched once, then kept (~50 minutes)", first == "ya29.1" and token.get() == "ya29.1" and session.tokens == 1)
token.expires = time.time() - 1
t.ok("...fetched again when it expired", token.get() == "ya29.2")

# ───────────── 3. the sources ─────────────
engine = Ledger(cash=1_004_000_000.0, start_cash=1e9, start_date="2026-12-01")
engine.history = [{"date": "2026-12-01", "nav": 1_000_000_000, "benchmark": 1250.0},
                  {"date": "2026-12-02", "nav": 1_004_000_000, "benchmark": 1260.0}]
engine.fills = [Fill("2026-12-01", "FPT", "BUY", 1000, 50_000.0, 75_000.0, "mua theo tín hiệu"),
                Fill("2026-12-02", "FPT", "SELL", 1000, 52_000.0, 130_000.0, "chốt T1 Bearer abcdefghijklmnop", pnl=1_795_000.0)]
factor = Ledger(cash=1e9, start_cash=1e9, start_date="2026-12-01")
factor.history = [{"date": "2026-12-01", "nav": 1_000_000_000, "benchmark": 1250.0}]
momentum = Ledger(cash=1e9, start_cash=1e9)


class Trader:
    settings = type("S", (), {"data_dir": DATA, "calibrate": False})()

    def paper(self):
        return engine

    def factor_book(self):
        return factor

    def momentum_book(self):
        return momentum


os.environ["BENCH_ETF_START"] = "2026-12-01"
etf_bench.refresh(DATA, 1e9, "2026-12-02", frame=pd.DataFrame(
    {"Date": pd.to_datetime(["2026-12-01", "2026-12-02"]), "Open": [25000.0, 26000.0], "High": [25000.0, 26000.0],
     "Low": [25000.0, 26000.0], "Close": [25000.0, 26000.0], "Volume": [1e6, 1e6]}))
etf_state = etf_bench.load(DATA)
rows = sh.nav_rows(Trader(), etf_state)
keys = [r["key"] for r in rows]
t.ok("ck_nav: one row per session and book (sorted), VNINDEX and the ETF's NAV beside, the ETF as a book of its own",
     keys == ["2026-12-01|engine", "2026-12-01|etf", "2026-12-01|factor", "2026-12-02|engine", "2026-12-02|etf"]
     and rows[0]["vnindex"] == 1250 and rows[0]["etf_nav"] == etf_state["history"][0]["nav"]
     and rows[3]["return_pct"] == 0.4 and rows[3]["cash"] == 1_004_000_000 and rows[1]["book"] == "etf_E1VFVN30", rows[:2])
trades = sh.trade_rows(Trader())
t.ok("ck_trades: every fill, a SELL with its P&L after fees and tax in % of the cost basis",
     len(trades) == 2 and trades[1]["side"] == "SELL" and trades[1]["pnl"] == 1_795_000
     and trades[1]["pnl_pct"] == round(1_795_000 / (52_000_000 - 130_000 - 1_795_000) * 100, 2) and trades[0]["pnl"] == "", trades[1])

db = st.Store(DATA / "store", url="")
today = time.strftime("%Y-%m-%d")
db.add_signals([{"day": "2026-11-20", "symbol": "BRK", "source": "screen", "action": "WATCH", "gate": "breakout", "near": "breakout",
                 "bias": "bullish", "holding": 0},
                {"day": "2026-11-20", "symbol": "FPT", "source": "screen", "action": "BUY", "gate": "", "near": "", "bias": "bullish",
                 "holding": 0},
                {"day": "2026-11-21", "symbol": "HPG", "source": "screen", "action": "WATCH", "gate": "rr", "near": "", "bias": "bullish",
                 "holding": 0}])
ids = {r["symbol"]: r["id"] for r in db.signals(day="2026-11-20") + db.signals(day="2026-11-21")}
db.update(st.Store.__name__ and "signals", ids["BRK"], {"status": "DONE", "ret_10": -2.5, "vni_10": 1.0, "plan": "breakout",
                                                        "plan_outcome": "SL", "plan_r": -1.0, "resolved_ts": time.time()})
db.update("signals", ids["FPT"], {"status": "DONE", "ret_10": 3.0, "vni_10": 1.0, "plan": "buy", "plan_outcome": "TP", "plan_r": 2.0,
                                  "resolved_ts": time.time()})
shadow = sh.shadow_rows(db)
t.ok("ck_shadow: the resolved rows only, each with its group (the switched-off breakouts as theirs) and the excess over VNINDEX",
     [r["key"] for r in shadow] == ["2026-11-20|BRK", "2026-11-20|FPT"] and shadow[0]["group"] == "breakout"
     and shadow[1]["group"] == "buy" and shadow[0]["excess_10"] == -3.5 and shadow[0]["plan_r"] == -1 and shadow[0]["resolved"] == today,
     shadow)
t.ok("ck_shadow without a store: nothing, no error", sh.shadow_rows(None) == [])

DAYS = [{"day": "2026-11-10", "pick": {"symbol": "AAA", "confidence": 70}, "ranking": ["AAA", "BBB"], "avoid": [{"symbol": "BBB"}],
         "served_by": "gemini-pro",
         "candidates": [{"symbol": "AAA", "r10": {"status": "closed", "pct": 4.0}}, {"symbol": "BBB", "r10": {"status": "closed", "pct": 1.0}}]},
        {"day": "2026-11-11", "pick": {"symbol": "CCC", "confidence": 80}, "model": "gmw/gemini-pro",
         "candidates": [{"symbol": "CCC", "r10": {"status": "closed", "pct": -1.0}}, {"symbol": "DDD", "r10": {"status": "open", "pct": 2.0}}]}]
cp.PickLog(DATA)._write(DAYS)
picks = sh.ai_pick_rows(cp.PickLog(DATA).days())
t.ok("ck_ai_pick: a session once every candidate is scored: pick, confidence, its r10, the mean, the excess, beat, model",
     len(picks) == 1 and picks[0]["key"] == "2026-11-10" and picks[0]["confidence"] == 70 and picks[0]["r10_pick"] == 4
     and picks[0]["r10_mean"] == 2.5 and picks[0]["excess"] == 1.5 and picks[0]["beat"] == 1 and picks[0]["ranking"] == "AAA > BBB"
     and picks[0]["avoid"] == "BBB" and picks[0]["model"] == "gemini-pro", picks)
summary = sh.summary_row("2026-12-02", Trader(), db, etf_state, cp.PickLog(DATA).days(), settings=Trader.settings, shadow_n=2)
t.ok("ck_summary: version and build, the params fingerprint, AUTO_CALIBRATE, breakout_buy, each book, VNINDEX, the ETF, counts",
     summary["key"] == "2026-12-02" and summary["version"].startswith("v") and len(summary["build"]) == 7
     and len(summary["params_fingerprint"]) == 7 and summary["auto_calibrate"] == 0 and summary["breakout_buy"] == 1
     and summary["engine_ret"] == 0.4 and summary["vnindex_ret"] == 0.8 and summary["factor_nav"] == 1_000_000_000
     and summary["momentum_nav"] == 1_000_000_000 and summary["etf_ret"] == etf_bench.stats(etf_state)["return_pct"]
     and summary["closed_trades"] == 1 and summary["shadow_resolved"] == 2 and summary["ai_pick_sessions"] == 1, summary)

# ───────────── 4. a cycle, a rerun, a failure, the redaction ─────────────
session = Session()
sheet = sh.Spreadsheet("1abc", ACCOUNT, session)
ex = sh.Exporter(DATA, trader=Trader(), store=db, settings=Trader.settings, spreadsheet=sheet)
result = ex.run("2026-12-02")
adds = [c for c in session.calls if c[0] == "POST" and c[1].endswith(":batchUpdate")]
appends = [c for c in session.calls if c[0] == "POST" and c[1].endswith(":append")]
t.ok("first cycle: the five tabs added (with their header row), one append per tab, every row written",
     result == {"ck_nav": 5, "ck_trades": 2, "ck_shadow": 2, "ck_ai_pick": 1, "ck_summary": 1} and len(adds) == 5
     and len(appends) == 5 and session.values["ck_nav"][0] == list(sh.TABS["ck_nav"]) and len(session.values["ck_nav"]) == 6
     and session.tokens == 1, (result, len(adds), len(appends)))
t.ok("the spreadsheet was read for its list of tabs only; 'proposals' never read nor written",
     [c for c in session.calls if c[0] == "GET"] == [("GET", "1abc", {"fields": "sheets.properties.title"}, None)]
     and "proposals" not in session.values)
t.ok("append: RAW values, INSERT_ROWS; the keys in the first column; numbers as numbers",
     appends[0][2] == {"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"}
     and session.values["ck_nav"][1][0] == "2026-12-01|engine" and isinstance(session.values["ck_nav"][1][3], int))
reason = session.values["ck_trades"][2][-1]
t.ok("a text cell is redacted (a Bearer token in a fill's reason)", reason == "chốt T1 Bearer ***" , reason)
cursor = sh.Cursor(DATA).data
t.ok("the cursor remembers every key written, per tab", set(cursor["ck_nav"]["keys"]) == set(keys) and cursor["ck_summary"]["keys"] == ["2026-12-02"]
     and cursor["ck_trades"]["written"] == 2 and cursor["ck_nav"]["error"] == "")

before = len(session.calls)
result = ex.run("2026-12-02")
t.ok("a rerun the same day: nothing written twice, no append sent",
     result == {tab: 0 for tab in sh.TABS} and len([c for c in session.calls[before:] if c[0] == "POST"]) == 0
     and len(session.values["ck_nav"]) == 6)
engine.history.append({"date": "2026-12-03", "nav": 1_010_000_000, "benchmark": 1270.0})
result = sh.Exporter(DATA, trader=Trader(), store=db, settings=Trader.settings, spreadsheet=sheet).run("2026-12-03")
t.ok("the next day: only the new rows (a fresh Exporter reads the cursor from disk)",
     result == {"ck_nav": 1, "ck_trades": 0, "ck_shadow": 0, "ck_ai_pick": 0, "ck_summary": 1}
     and session.values["ck_nav"][-1][0] == "2026-12-03|engine")

session.fail.add("ck_trades")
engine.fills.append(Fill("2026-12-03", "HPG", "BUY", 500, 30_000.0, 22_500.0, "mua theo tín hiệu"))
engine.history.append({"date": "2026-12-04", "nav": 1_011_000_000, "benchmark": 1271.0})
result = ex.run("2026-12-04")
t.ok("one tab failing (HTTP 500): -1 for it, the others written, its cursor and error kept, nothing raised",
     result["ck_trades"] == -1 and result["ck_nav"] == 1 and sh.Cursor(DATA).data["ck_trades"]["written"] == 2
     and "HTTP 500" in sh.Cursor(DATA).data["ck_trades"]["error"], (result, sh.Cursor(DATA).data["ck_trades"]))
session.fail.clear()
result = ex.run("2026-12-04")
t.ok("next cycle: the failed tab's rows are written, once; the error cleared",
     result["ck_trades"] == 1 and result["ck_nav"] == 0 and len(session.values["ck_trades"]) == 4
     and sh.Cursor(DATA).data["ck_trades"]["error"] == "")
status = sh.status_text(DATA)
t.ok("/bangtinh: rows per tab, the last write", "• ck_nav: 7 dòng, lần cuối" in status and "• ck_trades: 3 dòng" in status, status)


class Boom:
    def _headers(self):
        raise RuntimeError("token: HTTP 401 invalid_grant")

    def ensure_tab(self, tab, header):
        raise RuntimeError("token: HTTP 401 invalid_grant")


result = sh.Exporter(DATA, trader=Trader(), store=db, settings=Trader.settings, spreadsheet=Boom()).run("2026-12-05")
t.ok("the account refused: every tab -1, the errors recorded, nothing raised into the scheduler",
     set(result.values()) == {-1} and "401" in sh.Cursor(DATA).data["ck_nav"]["error"])

# ───────────── 5. the scheduler's loop ─────────────
from vnbot import settings as settings_module  # noqa: E402
from vnbot.jobs import JobQueue  # noqa: E402
from vnbot.service import DailyScheduler, Watchlist  # noqa: E402

os.environ["DATA_DIR"] = str(DATA)
SETTINGS = settings_module.load()
notes = []
sched = DailyScheduler(SETTINGS, JobQueue(SETTINGS), Watchlist(SETTINGS), bot=None, notify=notes.append)
sh.Exporter.run = lambda self, day: {"ck_nav": -1, "ck_trades": 0, "ck_shadow": 0, "ck_ai_pick": 0, "ck_summary": 0}
sched._sheets_export("2026-12-05")
t.ok("a failed cycle: tried again later (no stamp yet)", sched._sheets_next > time.time() and not sched._sheets_stamp.exists())
for _ in range(sched.SHEETS_MAX_TRIES - 1):
    sched._sheets_export("2026-12-05")
t.ok("after SHEETS_MAX_TRIES: the day is stamped and the owner told which tab waits",
     sched._sheets_stamp.read_text(encoding="utf-8") == "2026-12-05" and len(notes) == 1 and "ck_nav" in notes[0] and "/bangtinh" in notes[0])
sh.Exporter.run = lambda self, day: {tab: 0 for tab in sh.TABS}
notes.clear()
sched._sheets_export("2026-12-08")
t.ok("a good cycle: stamped, silent", sched._sheets_stamp.read_text(encoding="utf-8") == "2026-12-08" and notes == [])
t.ok("the export runs at 18:05 on session days, after the late bars (18:00) and the AI pick", sh.EXPORT_TIME == "18:05")

t.done()
