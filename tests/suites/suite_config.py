"""Settings on the web (/caidat): the schema, the store, the environment, the page and its guards."""
import json
import os

import _common  # noqa: F401 - clean env, no .env, offline, temp folder, repo on sys.path
import _web_harness as h  # noqa: E402 - the real app with stubs

from vnbot import config  # noqa: E402

t = _common.Checks()


def raises(fn) -> bool:
    try:
        fn()
    except ValueError:
        return True
    return False


t.raises = raises
DATA = h.SETTINGS.data_dir
app, zoom, handled, jobs, wl, sw = h.build(trader=False)
from fastapi.testclient import TestClient  # noqa: E402

web = TestClient(app)
AUTH = ("x", h.SETTINGS.web_password)

# ───────────── 1. the schema ─────────────
keys = [f.key for f in config.FIELDS]
t.ok("every field has a group and the groups cover every field", len(keys) == len(set(keys))
     and sum(len(g.fields) for g in config.GROUPS) == len(config.FIELDS) and all(g.fields for g in config.GROUPS))
t.ok("no secret is an editable field", not set(k for k, _ in config.SECRETS) & set(keys))
t.ok("the frozen Settings keys are marked restart", all(config.BY_KEY[k].restart for k in
     ("DAILY_REPORT_TIME", "TRADE_MODE", "SCAN_UNIVERSE", "INTRADAY_INTERVAL_MIN", "PUBLIC_URL")))
t.ok("the live keys are not", not any(config.BY_KEY[k].restart for k in ("TAISAN_RISK_PCT", "CLAUDE_PICK_MODEL", "SIEUTINHIEU", "STV_SCAN_MINUTES")))

# ───────────── 2. normalize ─────────────
F = config.BY_KEY
t.ok("int with dots and bounds", config.normalize(F["PAPER_CAPITAL"], "1.000.000.000") == "1000000000"
     and t.raises(lambda: config.normalize(F["PAPER_CAPITAL"], "5000000")))
t.ok("float with a comma", config.normalize(F["TAISAN_RISK_PCT"], "2,5") == "2.5"
     and t.raises(lambda: config.normalize(F["TAISAN_RISK_PCT"], "11")))
t.ok("hhmm", config.normalize(F["DAILY_REPORT_TIME"], "9:5") == "09:05" and config.normalize(F["TRADE_TIME"], "") == ""
     and t.raises(lambda: config.normalize(F["DAILY_REPORT_TIME"], "")) and t.raises(lambda: config.normalize(F["DAILY_REPORT_TIME"], "25:00")))
t.ok("flag", config.normalize(F["SIEUTINHIEU"], "on") == "1" and config.normalize(F["SIEUTINHIEU"], "") == "0")
t.ok("choice", config.normalize(F["TRADE_MODE"], "SIGNAL") == "signal" and t.raises(lambda: config.normalize(F["TRADE_MODE"], "live")))
t.ok("list: upper, unique, bounded, validated", config.normalize(F["WATCHLIST"], "fpt, hpg;fpt") == "FPT,HPG"
     and t.raises(lambda: config.normalize(F["WATCHLIST"], "<script>")) and t.raises(lambda: config.normalize(F["WATCHLIST"], ",".join(f"A{i:02d}" for i in range(31)))))
t.ok("ids", config.normalize(F["TELEGRAM_ALLOWED_IDS"], "123, -45") == "123,-45" and t.raises(lambda: config.normalize(F["TELEGRAM_ALLOWED_IDS"], "abc")))
t.ok("text: control characters and bad urls refused", t.raises(lambda: config.normalize(F["QUICK_MODEL"], "a\x00b"))
     and t.raises(lambda: config.normalize(F["PUBLIC_URL"], "ftp://x")) and config.normalize(F["PUBLIC_URL"], "https://a.b/") == "https://a.b/"
     and t.raises(lambda: config.normalize(F["SUPPORT_EMAIL"], "nope")))
t.ok("too long refused", t.raises(lambda: config.normalize(F["QUICK_MODEL"], "x" * 500)))

# ───────────── 3. save, apply, clear ─────────────
os.environ.pop("TAISAN_RISK_PCT", None)
changed, restart = config.save(DATA, "taisan", {"TAISAN_RISK_PCT": "3", "TAISAN_OPEN_RISK_PCT": "6", "TAISAN_MAX_WEIGHT_NEW": "30"})
t.ok("saving a group: only the changed field is stored, the environment follows, no restart for a live key",
     [f.key for f in changed] == ["TAISAN_RISK_PCT"] and restart is False and os.environ["TAISAN_RISK_PCT"] == "3"
     and config.read(DATA) == {"TAISAN_RISK_PCT": "3"})
from vnbot import kyluat  # noqa: E402
t.ok("...and the module that reads the environment sees it at once", kyluat.risk_pct() == 3.0)
t.ok("the page says where the value comes from", config.source(F["TAISAN_RISK_PCT"], config.read(DATA)) == "web"
     and config.source(F["TAISAN_OPEN_RISK_PCT"], config.read(DATA)) == "default")
changed, restart = config.save(DATA, "taisan", {"TAISAN_RISK_PCT": "2", "TAISAN_OPEN_RISK_PCT": "6", "TAISAN_MAX_WEIGHT_NEW": "30"})
t.ok("setting it back to the default removes the override and the environment entry",
     [f.key for f in changed] == ["TAISAN_RISK_PCT"] and config.read(DATA) == {} and os.environ.get("TAISAN_RISK_PCT") is None)
os.environ["STV_SCAN_MINUTES"] = "20"          # the operator's own value on Botkeep
config._env_before.pop("STV_SCAN_MINUTES", None)
changed, _ = config.save(DATA, "stv", {"STV_SCAN_MINUTES": "30", "STV_MARKET_SIZE": "300", "SIEUTINHIEU": "1", "STV_ESTIMATE": "1", "STV_MARKET": "1"})
t.ok("an override on top of the operator's value", os.environ["STV_SCAN_MINUTES"] == "30" and config.env_value("STV_SCAN_MINUTES") == "20"
     and config.source(F["STV_SCAN_MINUTES"], config.read(DATA)) == "env" or config.source(F["STV_SCAN_MINUTES"], config.read(DATA)) == "web")
config.clear(DATA, "STV_SCAN_MINUTES")
t.ok("clearing restores the operator's value", os.environ["STV_SCAN_MINUTES"] == "20" and "STV_SCAN_MINUTES" not in config.read(DATA))
config.clear(DATA, "SIEUTINHIEU")
t.ok("a flag left out of the form is off; a restart key says so",
     config.save(DATA, "canhbao", {"INTRADAY_INTERVAL_MIN": "15", "INTRADAY_VNI_DROP_PCT": "2"})[1] is True
     and os.environ["INTRADAY_ALERTS"] == "0" and "INTRADAY_EARLY" not in config.read(DATA))   # off is its default: not stored
config.clear(DATA, "INTRADAY_ALERTS")
t.ok("out of bounds: nothing saved", t.raises(lambda: config.save(DATA, "taisan", {"TAISAN_RISK_PCT": "50"})) and config.read(DATA) == {})
t.ok("an unknown group or key is refused", t.raises(lambda: config.save(DATA, "nope", {})) and t.raises(lambda: config.clear(DATA, "PATH")))
(DATA / config.FILE_NAME).write_text(json.dumps({"TAISAN_RISK_PCT": "4", "PATH": "/evil", "WEB_PASSWORD": "x", "X": 1}))
applied = config.apply(DATA)
t.ok("apply at start: only known keys reach the environment", applied == {"TAISAN_RISK_PCT": "4"} and os.environ["TAISAN_RISK_PCT"] == "4"
     and os.environ.get("PATH") != "/evil" and os.environ.get("WEB_PASSWORD") != "x")
config.clear(DATA, "TAISAN_RISK_PCT")
(DATA / config.FILE_NAME).write_text("not json")
t.ok("a broken file reads as empty", config.read(DATA) == {})
(DATA / config.FILE_NAME).unlink()

# ───────────── 4. the page and its guards ─────────────
t.ok("GET /caidat needs the password", web.get("/caidat").status_code == 401)
page = web.get("/caidat", auth=AUTH)
t.ok("the page: every group, every field, the secrets as set / not set, never a value",
     page.status_code == 200 and all(f'id="{g.key}"' in page.text for g in config.GROUPS)
     and all(f'name="{f.key}"' in page.text for f in config.FIELDS) and "đã đặt" in page.text and "chưa đặt" in page.text
     and h.SETTINGS.web_password not in page.text and 'href="/caidat" class="nav-item on" aria-current="page"' in page.text)
t.ok("POST /caidat without password -> 401; cross-site -> 403",
     web.post("/caidat", data={"group": "taisan"}).status_code == 401
     and web.post("/caidat", data={"group": "taisan"}, auth=AUTH, headers={"origin": "https://evil.example"}).status_code == 403
     and web.post("/caidat/xoa", data={"key": "TAISAN_RISK_PCT"}).status_code == 401)
r = web.post("/caidat", data={"group": "claude", "CLAUDE_PICK": "1", "CLAUDE_PICK_MODEL": "claude-opus-5-5", "CLAUDE_PICK_EFFORT": "high",
                              "CLAUDE_PICK_SEARCHES": "10", "CLAUDE_PICK_MONTHLY_USD": "15", "CLAUDE_PICK_BOT": "1", "CLAUDE_PICK_ALERTS": "5",
                              "CLAUDE_PICK_WEEKLY": "1", "CLAUDE_PICK_ROUTER9": "1", "CLAUDE_PICK_ROUTER9_MODEL": ""},
             auth=AUTH, follow_redirects=False)
from urllib.parse import unquote  # noqa: E402
loc = unquote(r.headers.get("location", ""))
t.ok("saving from the form: back to the group with a message, the environment set",
     r.status_code == 303 and "đã lưu 1 thay đổi (CLAUDE_PICK_EFFORT)" in loc and loc.endswith("#claude") and os.environ["CLAUDE_PICK_EFFORT"] == "high", loc)
from vnbot import claude_pick as cp  # noqa: E402
t.ok("...Claude chọn bóng reads it", cp.effort() == "high")
page = web.get("/caidat", auth=AUTH).text
t.ok("the page shows the override and the way back", "Đang đặt trên web:" in page and 'value="CLAUDE_PICK_EFFORT"' in page)
r = web.post("/caidat", data={"group": "claude", "CLAUDE_PICK_SEARCHES": "99"}, auth=AUTH, follow_redirects=False)
t.ok("a bad value: a message, nothing saved", "Lượt tìm tin mỗi phiên: trong khoảng 1 đến 20" in unquote(r.headers["location"])
     and config.read(DATA) == {"CLAUDE_PICK_EFFORT": "high"})
r = web.post("/caidat/xoa", data={"key": "CLAUDE_PICK_EFFORT"}, auth=AUTH, follow_redirects=False)
t.ok("clearing from the form", r.status_code == 303 and config.read(DATA) == {} and cp.effort() == "medium")
r = web.post("/caidat", data={"group": "lich", "DAILY_REPORT_TIME": "16:00", "TRADE_TIME": "", "STV_MORNING_TIME": "08:30", "CLAUDE_PICK_TIME": "15:50"},
             auth=AUTH, follow_redirects=False)
t.ok("a restart key says so in the message", "khởi động lại" in unquote(r.headers["location"]))
config.clear(DATA, "DAILY_REPORT_TIME")
t.ok("nothing changed: said plainly", "không có gì thay đổi" in unquote(web.post(
    "/caidat", data={"group": "taisan", "TAISAN_RISK_PCT": "2", "TAISAN_OPEN_RISK_PCT": "6", "TAISAN_MAX_WEIGHT_NEW": "30"},
    auth=AUTH, follow_redirects=False).headers["location"]))
t.ok("the pages: no gradient, glass, uppercase labels, shadows or emoji in the chrome",
     all(x not in page for x in ("gradient(", "backdrop-filter", "box-shadow", "⭐", "👋"))
     and page.count("text-transform:uppercase") == 1)   # only the stock-code input types in capitals

t.done()
