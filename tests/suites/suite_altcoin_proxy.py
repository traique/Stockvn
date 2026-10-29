"""The Hub (vnbot/hub.py) in front of the stock's real app and a fake altcoin child, each on a
real localhost port (offline):

- the raw path checked before anything (400 for "..", "//", "\\", %2E/%2F/%5C/%5F...), then
  exact matches on the decoded path: 308 /altcoin -> /altcoin/, 404 for /altcoin/_hub(/...) and
  the altcoin's old Zoom and channel routes (with the password too), 308 of its public pages to
  the stock's; the child never sees any of them;
- the stock's WEB_PASSWORD (401, 503 unset, 429 with the lockout counter shared both ways) and
  the same-origin check, the same answers as the stock's own app for the same requests;
- /altcoin/hub.json (the state, no token) and /altcoin/health behind the password;
- what the child gets: no Authorization, no client X-Hub-*/X-Forwarded-*, one X-Hub-Token, the
  prefix; a body up to 64 KB, at most 8 at a time; Location rewritten (any scheme, the child's
  own address or this site, a bare "/x"); a 5 MB answer streamed; security headers;
- a 503 page while the child is not running; the stock's pages served meanwhile, and while
  the stock is still starting; the server's shutdown stops the child.
"""
import base64
import dataclasses
import http.client
import json
import logging
import socket
import threading
import time
from collections import deque

import _common  # noqa: F401 - clean env, no .env, offline, temp folder, repo on sys.path
from _web_harness import SETTINGS, JobQueue, Switches, Watchlist, ZoomChannel  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse  # noqa: E402

from vnbot import boot, hub, webguard  # noqa: E402
from vnbot.altcoin_host import Supervisor  # noqa: E402
from vnbot.web import create_app  # noqa: E402

t = _common.Checks()
logging.basicConfig(level=logging.CRITICAL)      # the wrong-password warnings are expected here
PASSWORD = SETTINGS.web_password
TOKEN = "hub-token-for-the-child-0123456789"
HOST = "public.example"
SAME = {"Origin": f"http://{HOST}"}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def serve(app) -> tuple[int, uvicorn.Server]:
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="critical", lifespan="on"))
    threading.Thread(target=server.run, daemon=True).start()
    end = time.monotonic() + 10
    while not server.started and time.monotonic() < end:
        time.sleep(0.02)
    return port, server


# ── the fake child: what it was asked, a few redirects, a big and a slow answer ──
SEEN = []
SLOW = threading.Event()
child = FastAPI()


@child.middleware("http")
async def record(request: Request, call_next):
    SEEN.append((request.method, request.scope["path"]))
    return await call_next(request)


@child.api_route("/echo", methods=["GET", "HEAD", "POST"])
async def echo(request: Request):
    body = await request.body()
    return JSONResponse({"path": request.scope["path"], "raw_path": request.scope["raw_path"].decode(),
                         "query": request.scope["query_string"].decode(), "client": request.client.host,
                         "headers": [[k.decode(), v.decode()] for k, v in request.scope["headers"]],
                         "body": len(body), "scheme": request.url.scheme})


@child.get("/")
def home():
    return HTMLResponse("<p>child home</p>")


@child.get("/health")
def child_health():
    return {"ok": True, "build": "380dfca"}


@child.get("/caidat")
def caidat():
    return HTMLResponse("caidat")


REDIRECTS = {}


@child.get("/redir/{kind}")
def redir(kind: str):
    return RedirectResponse(REDIRECTS[kind], status_code=303)


@child.get("/big")
def big():
    return StreamingResponse((bytes([65 + i % 26]) * 100_000 for i in range(50)), media_type="application/octet-stream")


@child.get("/slow")
async def slow():
    import asyncio

    await asyncio.to_thread(SLOW.wait, 20)
    return {"slow": True}


CHILD_PORT, child_server = serve(child)
REDIRECTS.update({"internal-http": f"http://127.0.0.1:{CHILD_PORT}/caidat",
                  "internal-https": f"https://127.0.0.1:{CHILD_PORT}/x?y=1#z", "public": f"https://{HOST}/caidat",
                  "bare": "/caidat?msg=ok", "relative": "caidat", "other": "https://other.example/x",
                  "prefixed": "/altcoin/ai", "scheme-relative": f"//127.0.0.1:{CHILD_PORT}/ai"})


class FakeSup:
    """The supervisor as the Hub sees it."""

    def __init__(self):
        self.snap = {"state": "running", "reason": None, "port": CHILD_PORT, "pid": 4242, "mode": "paper",
                     "seam_ok": True, "bridge": {"notified": 3}}
        self.port = CHILD_PORT
        self.lines = deque(["[boot] Dashboard sẵn sàng", "dòng <b>cuối</b>"], maxlen=200)
        self.stops = 0

    def state(self):
        return dict(self.snap)

    def upstream(self):
        return (self.port, TOKEN) if self.snap["state"] == "running" else None

    def stop(self):
        self.stops += 1


# ── the stock behind the Hub: the real app, sharing the Hub's lockout counter ──
throttle = webguard.LoginThrottle()
stock_app = create_app(SETTINGS, JobQueue(SETTINGS), Watchlist(SETTINGS), switches=Switches(SETTINGS.data_dir, {}),
                       zoom=ZoomChannel(state_path=SETTINGS.data_dir / "zoom_state.json", start=False), throttle=throttle)
t.ok("create_app keeps the counter it is given (app.state.login_throttle)", stock_app.state.login_throttle is throttle)
late = boot.LateApp()
late.ready(stock_app)
sup = FakeSup()
the_hub = hub.Hub(late, sup, SETTINGS, throttle)
HUB_PORT, hub_server = serve(the_hub)


def call(method, path, headers=None, body=None, auth=PASSWORD, port=None, client=None, host=HOST):
    """(status, headers, body) over a real socket: the path goes out exactly as written."""
    conn = http.client.HTTPConnection("127.0.0.1", port or HUB_PORT, timeout=30)
    sent = {"Host": host}
    if auth is not None:
        sent["Authorization"] = "Basic " + base64.b64encode(f"u:{auth}".encode()).decode()
    if client:
        sent["X-Forwarded-For"] = client     # as Botkeep's proxy says (uvicorn trusts 127.0.0.1)
    sent.update(headers or {})
    conn.request(method, path, body=body, headers=sent)
    response = conn.getresponse()
    data = response.read()
    conn.close()
    return response.status, {k.lower(): v for k, v in response.getheaders()}, data


def header_names(path) -> list[str]:
    conn = http.client.HTTPConnection("127.0.0.1", HUB_PORT, timeout=30)
    conn.request("GET", path, headers={"Host": HOST, "Authorization": "Basic " + base64.b64encode(
        f"u:{PASSWORD}".encode()).decode()})
    response = conn.getresponse()
    response.read()
    conn.close()
    return [k.lower() for k, _ in response.getheaders()]


def code(*args, **kwargs) -> int:
    return call(*args, **kwargs)[0]


# ── 1. the raw path, before anything else ──
t.ok("raw_path_ok: a plain path, %20 and %3F pass", hub.raw_path_ok(b"/altcoin/api/export/a%20b.csv")
     and hub.raw_path_ok(b"/altcoin/x%3Fy") and hub.raw_path_ok(b"/altcoin/"))
seen = len(SEEN)
bad = ["/altcoin/health/../api/export/all.json", "/altcoin/./api/state", "/altcoin/%2e%2e/api/state",
       "/altcoin/%2E%2E/api/state", "/altcoin/api%2Fstate", "/altcoin/api%2fstate", "/altcoin//api/state",
       "/altcoin/a\\b", "/altcoin/a%5cb", "/altcoin/%5Fhub/command", "/altcoin/%5fhub/command",
       "/altcoin/_h%75b/command", "/altcoin/_hub/../_hub/command", "/%61ltcoin/_hub/command", "/altcoin/%zz",
       "/altcoin/a%2", "/altcoin/%41pi/state"]
codes = {p: code("GET", p) for p in bad}
t.ok("400 for every odd raw path (dot segments, //, \\, %2E/%2F/%5C, %5F, %75, a stray %)",
     set(codes.values()) == {400}, codes)
t.ok("POST /altcoin/webhook/zoom/../../api/learn/reset -> 400",
     code("POST", "/altcoin/webhook/zoom/../../api/learn/reset", headers=SAME) == 400)
t.ok("...and the child got none of them", len(SEEN) == seen, SEEN[seen:])

# ── 2. exact matches on the decoded path, before the password ──
st, hd, _ = call("GET", "/altcoin?x=1", auth=None)
t.ok("GET /altcoin -> 308 /altcoin/ (relative, query kept), no password", st == 308 and hd["location"] == "/altcoin/?x=1", hd)
t.ok("HEAD /altcoin -> 308", code("HEAD", "/altcoin", auth=None) == 308)
t.ok("POST /altcoin -> 404", code("POST", "/altcoin", headers=SAME) == 404)
for name in ("zoom", "docs", "privacy", "terms", "support", "data-deletion"):
    st, hd, _ = call("GET", f"/altcoin/{name}", auth=None)
    t.ok(f"GET /altcoin/{name} -> 308 /{name} (the stock's page), no password", st == 308 and hd["location"] == f"/{name}",
         (st, hd.get("location")))
t.ok("POST /altcoin/docs -> 404", code("POST", "/altcoin/docs", headers=SAME) == 404)
seen = len(SEEN)
blocked = [("GET", "/altcoin/_hub"), ("GET", "/altcoin/_hub/"), ("POST", "/altcoin/_hub/command"),
           ("GET", "/altcoin/_hub/command"), ("POST", "/altcoin/webhook/zoom"), ("POST", "/altcoin/zoom/bot"),
           ("POST", "/altcoin/caidat/kenh/telegram_notify"), ("POST", "/altcoin/caidat/zoomtest"),
           ("GET", "/altcoin/zoom/install"), ("GET", "/altcoin/zoom/oauth/callback"), ("POST", "/altcoin/zoom/pair"),
           ("POST", "/altcoin/zoom/unpair")]
for auth in (None, PASSWORD):
    codes = {f"{m} {p}": code(m, p, auth=auth, headers=SAME) for m, p in blocked}
    t.ok(f"404 for _hub and the old Zoom/channel routes ({'with' if auth else 'without'} the password)",
         set(codes.values()) == {404}, codes)
t.ok("...the child got none of them", len(SEEN) == seen, SEEN[seen:])
t.ok("/altcoinx is the stock's (its 404), not the Hub's", code("GET", "/altcoinx") == 404 and len(SEEN) == seen)

# ── 3. the password and the cross-site check ──
st, hd, _ = call("GET", "/altcoin/", auth=None)
t.ok("no password -> 401 Basic (the browser asks)", st == 401 and hd.get("www-authenticate") == "Basic", (st, hd))
t.ok("wrong password -> 401", code("GET", "/altcoin/", auth="nope", client="10.9.9.1") == 401)
st, hd, body = call("GET", "/altcoin/")
t.ok("right password -> the child's page", st == 200 and b"child home" in body, (st, body[:80]))
t.ok("security headers on the child's answers", hd.get("x-frame-options") == "DENY"
     and hd.get("x-content-type-options") == "nosniff" and hd.get("referrer-policy") == "same-origin", hd)
names = header_names("/altcoin/")
t.ok("one Date and one Server header (the child's dropped)", names.count("date") == 1 and names.count("server") <= 1,
     names)
t.ok("/altcoin/health needs the password", code("GET", "/altcoin/health", auth=None) == 401)
st, _, body = call("GET", "/altcoin/health")
t.ok("/altcoin/health with it: the child's own", st == 200 and json.loads(body)["build"] == "380dfca", body)
seen = len(SEEN)
t.ok("/altcoin/hub.json needs the password", code("GET", "/altcoin/hub.json", auth=None) == 401)
st, hd, body = call("GET", "/altcoin/hub.json")
state = json.loads(body)
t.ok("/altcoin/hub.json: the supervisor's whole state, answered by the Hub (not the child), no token",
     st == 200 and state["bridge"] == {"notified": 3} and state["mode"] == "paper" and TOKEN.encode() not in body
     and len(SEEN) == seen and hd["content-type"] == "application/json", (st, body[:200]))

for i in range(8):
    call("GET", "/altcoin/", auth=f"bad{i}", client="10.1.1.1")
st, hd, _ = call("GET", "/", client="10.1.1.1")
t.ok("8 wrong passwords at /altcoin/ -> the stock's pages wait too (429, shared counter)", st == 429
     and int(hd.get("retry-after", 0)) > 0, (st, hd))
for i in range(8):
    call("GET", "/ai", auth=f"bad{i}", client="10.1.1.2")
st, hd, _ = call("GET", "/altcoin/", client="10.1.1.2")
t.ok("8 wrong passwords on the stock -> /altcoin/ waits too (429 + Retry-After)", st == 429 and hd.get("retry-after"),
     (st, hd))
t.ok("another client is not locked out", code("GET", "/altcoin/", client="10.1.1.3") == 200)

# the same requests to a stock page and to an altcoin page get the same answer
CASES = [("no password", "GET", {}, None), ("wrong password", "GET", {}, "wrong"),
         ("Bearer", "GET", {"Authorization": "Bearer x"}, None), ("broken Basic", "GET", {"Authorization": "Basic !!"}, None),
         ("right password", "GET", {}, PASSWORD),
         ("cross origin POST", "POST", {"Origin": "https://evil.example"}, PASSWORD),
         ("null origin POST", "POST", {"Origin": "null"}, PASSWORD),
         ("sec-fetch-site cross-site POST", "POST", {"Sec-Fetch-Site": "cross-site"}, PASSWORD),
         ("evil referer POST", "POST", {"Referer": "https://evil.example/x"}, PASSWORD),
         ("sibling subdomain POST", "POST", {"Origin": f"http://evil.{HOST}"}, PASSWORD),
         ("cross origin POST without password", "POST", {"Origin": "https://evil.example"}, None),
         ("same origin POST", "POST", SAME, PASSWORD), ("POST, no Origin nor Referer", "POST", {}, PASSWORD),
         ("same referer POST", "POST", {"Referer": f"http://{HOST}/x"}, PASSWORD),
         ("X-Forwarded-Host origin POST", "POST", {"Origin": "https://front.example", "X-Forwarded-Host": "front.example"},
          PASSWORD)]


def outcome(status: int) -> str:
    return "ok" if status < 400 else str(status)


def compare(cases, port, tag, prefix=""):
    for n, (name, method, headers, auth) in enumerate(cases):
        client = f"10.2.{tag}.{n}"
        form = {"Content-Type": "application/x-www-form-urlencoded"} if method == "POST" else {}
        stock = call(method, "/watchlist" if method == "POST" else "/ai", headers={**headers, **form}, auth=auth,
                     port=port, client=client + "0", body="codes=FPT" if method == "POST" else None)
        alt = call(method, f"{prefix}/altcoin/echo", headers={**headers, **form}, auth=auth, port=port, client=client + "1",
                   body="codes=FPT" if method == "POST" else None)
        same = (outcome(stock[0]) == outcome(alt[0]) and stock[1].get("www-authenticate") == alt[1].get("www-authenticate"))
        t.ok(f"same answer, stock and altcoin: {name} ({outcome(alt[0])})", same, (stock[0], alt[0]))


compare(CASES, HUB_PORT, 1)
for who in ("0", "1"):
    path = "/ai" if who == "0" else "/altcoin/echo"
    codes = [code("GET", path, auth=f"bad{i}", client=f"10.3.0.{who}") for i in range(8)]
    st, hd, _ = call("GET", path, client=f"10.3.0.{who}")
    t.ok(f"lockout on {path}: 8 x 401, then 429 with Retry-After for the right password", codes == [401] * 8
         and st == 429 and hd.get("retry-after"), (codes, st))

nopass = dataclasses.replace(SETTINGS, web_password="")
late2 = boot.LateApp()
late2.ready(create_app(nopass, JobQueue(nopass), Watchlist(nopass), switches=Switches(nopass.data_dir, {}),
                       zoom=ZoomChannel(state_path=nopass.data_dir / "zoom2.json", start=False)))
NOPASS_PORT, _ = serve(hub.Hub(late2, FakeSup(), nopass, webguard.LoginThrottle()))
t.ok("WEB_PASSWORD unset: 503 at /altcoin/ as at the stock's pages", code("GET", "/altcoin/", port=NOPASS_PORT) == 503
     and code("GET", "/ai", port=NOPASS_PORT) == 503 and code("GET", "/altcoin/hub.json", port=NOPASS_PORT) == 503)

# ── 4. what the child gets ──
st, _, body = call("POST", "/altcoin/echo?a=1&b=%2F", body=b"x" * 1000, headers={
    **SAME, "Proxy-Authorization": "Basic eDp5", "X-Hub-Token": "forged", "X-Hub-Cmd-Token": "forged-cmd",
    "x-hub-anything": "1", "X-Forwarded-Host": "evil.example", "X-Forwarded-Prefix": "/x", "X-Forwarded-Proto": "https",
    "Forwarded": "for=6.6.6.6", "Connection": "keep-alive, X-Drop-Me", "X-Drop-Me": "1", "X-Custom": "kept",
    "Content-Type": "application/octet-stream"}, client="203.0.113.7")
seen_by = json.loads(body)
names = [k for k, _ in seen_by["headers"]]
got = dict(seen_by["headers"])
t.ok("the child: no Authorization, Proxy-Authorization, Forwarded or Connection-listed header",
     not {"authorization", "proxy-authorization", "forwarded", "x-drop-me"} & set(names), names)
t.ok("the child: exactly one X-Hub-Token, the Hub's; no X-Hub-Cmd-Token, no client X-Hub-*",
     names.count("x-hub-token") == 1 and got["x-hub-token"] == TOKEN and "x-hub-cmd-token" not in names
     and "x-hub-anything" not in names, seen_by["headers"])
t.ok("the child: X-Forwarded-* set by the Hub (client, Host, scheme, /altcoin), the client's gone",
     names.count("x-forwarded-for") == 1 and got["x-forwarded-for"] == "203.0.113.7" and got["x-forwarded-host"] == HOST
     and got["x-forwarded-prefix"] == "/altcoin" and got["x-forwarded-proto"] == "https" and seen_by["client"] == "203.0.113.7",
     got)
t.ok("the child: its path without the prefix, the query as sent, the body, other headers kept",
     seen_by["raw_path"] == "/echo" and seen_by["query"] == "a=1&b=%2F" and seen_by["body"] == 1000
     and got.get("x-custom") == "kept", seen_by)
st, _, body = call("POST", "/altcoin/echo", body=b"x" * (64 * 1024), headers=SAME)
t.ok("a 64 KB body goes through", st == 200 and json.loads(body)["body"] == 64 * 1024, st)
seen = len(SEEN)
t.ok("a 64 KB + 1 body -> 413", code("POST", "/altcoin/echo", body=b"x" * (64 * 1024 + 1), headers=SAME) == 413)
chunked = b"".join(b"9c40\r\n" + b"x" * 40_000 + b"\r\n" for _ in range(2)) + b"0\r\n\r\n"    # one write
t.ok("a chunked body over 64 KB -> 413", code("POST", "/altcoin/echo", body=chunked,
                                              headers={**SAME, "Transfer-Encoding": "chunked"}) == 413)
t.ok("...neither reached the child", len(SEEN) == seen, SEEN[seen:])
st, hd, body = call("GET", "/altcoin/big")
t.ok("a 5 MB answer streamed whole", st == 200 and len(body) == 5_000_000 and body[:1] == b"A" and body[-1:] == b"X",
     (st, len(body)))

# ── 5. Location from the child ──
want = {"internal-http": "/altcoin/caidat", "internal-https": "/altcoin/x?y=1#z", "public": "/altcoin/caidat",
        "bare": "/altcoin/caidat?msg=ok", "relative": "caidat", "other": "https://other.example/x",
        "prefixed": "/altcoin/ai", "scheme-relative": "/altcoin/ai"}
for kind, location in want.items():
    st, hd, _ = call("GET", f"/altcoin/redir/{kind}")
    t.ok(f"Location {REDIRECTS[kind]} -> {location}", st == 303 and hd.get("location") == location, hd.get("location"))
st, hd, _ = call("GET", "/altcoin/caidat/", headers={"X-Forwarded-Proto": "https"})
t.ok("/altcoin/caidat/ behind https: the child's https://127.0.0.1:<port>/caidat -> /altcoin/caidat",
     st in (307, 308) and hd.get("location") == "/altcoin/caidat", (st, hd.get("location")))
t.ok("rewrite_location: the public host of PUBLIC_URL, any case", hub.rewrite_location(
    "HTTP://Public.Example/a", 1, {"public.example"}) == "/altcoin/a" and hub.rewrite_location("/", 1, set()) == "/altcoin/")

# ── 6. at most 8 at a time ──
results = []
threads = [threading.Thread(target=lambda: results.append(code("GET", "/altcoin/slow"))) for _ in range(8)]
for th in threads:
    th.start()
time.sleep(0.2)
end = time.monotonic() + 5
while the_hub.active < 8 and time.monotonic() < end:
    time.sleep(0.02)
st, hd, _ = call("GET", "/altcoin/")
t.ok("a 9th request while 8 are at the child -> 503 (busy) with Retry-After", st == 503 and hd.get("retry-after"), st)
t.ok("...the stock's pages still answer", code("GET", "/ai") == 200)
SLOW.set()
for th in threads:
    th.join(10)


def settled() -> bool:
    """The Hub's count drops a moment after the client has its answer."""
    end = time.monotonic() + 5
    while the_hub.active and time.monotonic() < end:
        time.sleep(0.02)
    return the_hub.active == 0


t.ok("the 8 finish, the count goes back to 0, and the next goes through", results == [200] * 8 and settled()
     and code("GET", "/altcoin/") == 200 and settled(), (results, the_hub.active))

# ── 7. the child not running ──
sup.snap.update(state="backoff", reason="thoát mã 1")
st, hd, body = call("GET", "/altcoin/ai")
page = body.decode()
t.ok("child down: 503 'đang khởi động', Retry-After 5, why, its last lines escaped", st == 503 and hd.get("retry-after") == "5"
     and "Altcoin đang khởi động" in page and "thoát mã 1" in page and "dòng &lt;b&gt;cuối&lt;/b&gt;" in page, page[-400:])
st, _, body = call("GET", "/altcoin/health")
t.ok("child down: /altcoin/health -> 503 JSON", st == 503 and json.loads(body) == {"ok": False, "state": "backoff"}, body)
t.ok("child down: /altcoin/hub.json still answers", json.loads(call("GET", "/altcoin/hub.json")[2])["state"] == "backoff")
t.ok("child down: the stock serves as before", code("GET", "/ai") == 200 and code("GET", "/health", auth=None) == 200)
sup.snap.update(state="disabled", reason="DB rỗng – chép DB cũ hoặc đặt ALTCOIN_FRESH_START=1")
st, hd, body = call("GET", "/altcoin/")
t.ok("disabled: 503 'đang tắt' with the reason, no refresh", st == 503 and "Altcoin đang tắt" in body.decode()
     and "ALTCOIN_FRESH_START" in body.decode() and b"http-equiv" not in body, body[:300])
sup.snap.update(state="running", reason=None)
sup.port = free_port()                   # it died a moment ago: nothing listens there any more
t.ok("'running' but nothing listening (just died) -> the 503 page", code("GET", "/altcoin/") == 503)
sup.port = CHILD_PORT

starting = boot.LateApp()
BOOT_PORT, boot_server = serve(hub.Hub(starting, FakeSup(), SETTINGS, webguard.LoginThrottle()))
st, _, body = call("GET", "/health", auth=None, port=BOOT_PORT)
t.ok("the stock still starting: /health answers (booting), /altcoin/ is served", st == 200 and json.loads(body)["booting"]
     and code("GET", "/altcoin/", port=BOOT_PORT) == 200 and code("GET", "/altcoin/hub.json", port=BOOT_PORT) == 200)
t.ok("...and its other pages say it is starting (503)", code("GET", "/ai", port=BOOT_PORT) == 503)

# ── 8. the real supervisor's upstream() and the server's shutdown ──
real = Supervisor(SETTINGS.data_dir, environ={"ALTCOIN_ENABLED": "1"})
none_before = real.upstream() is None
real._tokens = ("tok", "cmd")
real._snap = {**real._snap, "state": "running", "port": 5555}
t.ok("Supervisor.upstream(): None until running, then (port, HUB_TOKEN) - never the command token",
     none_before and real.upstream() == (5555, "tok"))
boot_server.should_exit = True
end = time.monotonic() + 10
while boot_server.started and not boot_server.should_exit and time.monotonic() < end:
    time.sleep(0.05)
hub_server.should_exit = True
end = time.monotonic() + 10
while sup.stops == 0 and time.monotonic() < end:
    time.sleep(0.05)
t.ok("the server's shutdown (lifespan) stops the child", sup.stops >= 1, sup.stops)

t.done()
