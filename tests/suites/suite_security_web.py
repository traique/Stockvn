"""Web app security: password on every private page and action, cross-site POSTs refused,
oversized bodies refused before they are read, password lockout, escaped output (offline)."""

import _common  # noqa: F401 - clean env, no .env, offline, temp folder, repo on sys.path
from _web_harness import *  # noqa: E402,F401,F403
from fastapi.testclient import TestClient  # noqa: E402
from starlette.datastructures import UploadFile  # noqa: E402

t = _common.Checks()
AUTH = ("x", "s3cret-pass")
SAME = {"origin": "http://testserver"}

app, zoom, handled, jobs, wl, sw = build(trader=True)
c = TestClient(app, raise_server_exceptions=False)

# ── 1. what needs the password ──
private_get = ["/", "/ai", "/bot", "/zoom", "/jobs/abc", "/reports/abc"]
for path in private_get:
    t.ok(f"GET {path} without password -> 401", c.get(path, follow_redirects=False).status_code == 401)
public_get = {"/health": 200, "/privacy": 200, "/terms": 200, "/support": 200, "/data-deletion": 200, "/docs": 200}
for path, code in public_get.items():
    t.ok(f"GET {path} is public", c.get(path, follow_redirects=False).status_code == code)
for path in ("/openapi.json", "/redoc"):
    t.ok(f"{path} not exposed", c.get(path).status_code == 404)
form = {"ticker": "FPT", "codes": "FPT", "capital": "20000000", "confirm": "1", "books": "factor"}
for path in ("/analyze", "/quick", "/critic", "/watchlist", "/bot/run", "/bot/capital", "/channels/telegram",
             "/zoom/pair", "/zoom/unpair"):
    t.ok(f"POST {path} without password -> 401", c.post(path, data=form).status_code == 401)
t.ok("wrong password -> 401", c.get("/", auth=("x", "wrong")).status_code == 401)
t.ok("watchlist untouched by the refused posts", wl.get() == [], wl.get())

# ── 2. cross-site requests with the password (a browser that remembers it) ──
FakeTrader.calls.clear()
for name, headers in (("cross origin", {"origin": "https://evil.example"}), ("null origin", {"origin": "null"}),
                      ("sec-fetch-site cross-site", {"sec-fetch-site": "cross-site"}),
                      ("evil referer", {"referer": "https://evil.example/x"}),
                      ("sibling subdomain", {"origin": "http://evil.testserver"})):
    r = c.post("/bot/run", auth=AUTH, headers=headers, follow_redirects=False)
    t.ok(f"{name} POST refused (403)", r.status_code == 403, r.status_code)
t.ok("no run started by them", FakeTrader.calls == [], FakeTrader.calls)
r = c.post("/bot/run", auth=AUTH, headers=SAME, follow_redirects=False)
t.ok("same-origin POST works", r.status_code == 303 and FakeTrader.calls == ["start_run"], (r.status_code, FakeTrader.calls))
r = c.post("/watchlist", auth=AUTH, data={"codes": "FPT,<x>"}, headers=SAME, follow_redirects=False)
t.ok("bad stock code refused, watchlist unchanged", r.status_code == 303 and "error=" in r.headers.get("location", "")
     and wl.get() == [], (r.status_code, r.headers.get("location"), wl.get()))
t.ok("unknown channel name -> 404", c.post("/channels/../../etc", auth=AUTH, data={"on": "1"}, headers=SAME).status_code == 404)

# ── 3. big bodies refused before being read ──
spooled = [0]
_write = UploadFile.write


async def _spy(self, data):
    spooled[0] += len(data)
    return await _write(self, data)


UploadFile.write = _spy
r = c.post("/analyze", files={"f": ("a.bin", b"A" * 5_000_000)}, data={"ticker": "FPT"})
t.ok("5 MB multipart without password -> 413, nothing spooled", r.status_code == 413 and spooled[0] == 0, (r.status_code, spooled))
UploadFile.write = _write
r = c.post("/analyze", content="ticker=" + "A" * 5_000_000, headers={"content-type": "application/x-www-form-urlencoded"})
t.ok("5 MB form without password -> 413", r.status_code == 413, r.status_code)
body = b'{"event":"x","pad":"' + b"A" * 20_000_000 + b'"}'
r = c.post("/webhook/zoom", content=body, headers={"content-type": "application/json"})
t.ok("20 MB webhook body -> 413", r.status_code == 413, r.status_code)

# ── 4. password lockout per client ──
app2, *_ = build()
c2 = TestClient(app2, raise_server_exceptions=False)
codes = [c2.get("/ai", auth=("x", f"bad{i}")).status_code for i in range(8)]
t.ok("8 wrong passwords -> 401s", codes == [401] * 8, codes)
t.ok("then even the right password waits (429)", c2.get("/ai", auth=AUTH).status_code == 429)
other = TestClient(app2, raise_server_exceptions=False, client=("10.0.0.9", 1234))
t.ok("another client is not locked out", other.get("/", auth=AUTH).status_code == 200)

# ── 5. escaped output (the real pages, with the real trading bot) ──
from vnbot.trader import TradingBot  # noqa: E402
from vnbot.web import create_app  # noqa: E402

trader = TradingBot(SETTINGS, wl, notify=lambda text: None)
page_app = create_app(SETTINGS, JobQueue(SETTINGS), wl, trader=trader,
                      zoom=ZoomChannel(state_path=SETTINGS.data_dir / "zoom_state2.json", start=False))
c3 = TestClient(page_app, raise_server_exceptions=True)
r = c3.get("/?error=<script>alert(1)</script>", auth=AUTH)
t.ok("index: error message escaped", r.status_code == 200 and "<script>alert(1)</script>" not in r.text, r.status_code)
r = c3.get("/bot?msg=<img src=x onerror=alert(1)>", auth=AUTH)
t.ok("bot page: message escaped", r.status_code == 200 and "<img src=x" not in r.text, r.status_code)
r = c3.get("/zoom", auth=AUTH)
t.ok("zoom page: secrets not shown", r.status_code == 200 and "csecret" not in r.text and "zsecret" not in r.text)
r = c.get("/zoom/oauth/callback?error=<b>x</b>&error_description=<script>1</script>")
t.ok("OAuth error page escaped", r.status_code == 200 and "<script>1</script>" not in r.text, r.status_code)

# ── 6. owner's capital form: odd digits do not crash the page ──
r = c3.post("/bot/capital", auth=AUTH, headers=SAME, data={"capital": "1²", "confirm": "1", "books": "factor"},
            follow_redirects=False)
t.ok("capital '1²' -> a message, not a server error", r.status_code == 303, r.status_code)

t.done()
