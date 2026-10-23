"""Web fixes: request body limit and Zoom pairing of the account the page showed (offline)."""
import json
import sys

from _web_harness import *  # noqa: E402,F401,F403
from fastapi.testclient import TestClient  # noqa: E402

fails = []


def check(name, cond, info=""):
    print(("ok  " if cond else "FAIL") + " " + name + (f"  [{info}]" if not cond else ""))
    if not cond:
        fails.append(name)


app, zoom, handled, jobs, wl, sw = build(trader=True)
c = TestClient(app, raise_server_exceptions=False)
AUTH = ("x", "s3cret-pass")
H = {"origin": "http://testserver"}

big = b"{" + b'"a":"' + b"x" * 200_000 + b'"}'
r = c.post("/webhook/zoom", content=big, headers=sign("zsecret", big))
check("big signed webhook body -> 413", r.status_code == 413, r.status_code)
r = c.post("/webhook/zoom", content=big, headers={"content-type": "application/json"})
check("big unsigned webhook body -> 413", r.status_code == 413, r.status_code)
r = c.post("/analyze", files={"f": ("a.bin", b"x" * 300_000)}, data={"ticker": "FPT"})
check("big multipart without auth -> 413", r.status_code == 413, r.status_code)


def chunks():
    for _ in range(40):
        yield b"x" * 4096


r = c.post("/webhook/zoom", content=chunks(), headers={"content-type": "application/json"})
check("chunked oversize body -> 413", r.status_code == 413, r.status_code)
small = json.dumps({"event": "x"}).encode()
r = c.post("/webhook/zoom", content=small, headers={"content-type": "application/json"})
check("small unsigned webhook still 401 (normal path)", r.status_code == 401, r.status_code)
r = c.post("/watchlist", data={"codes": "FPT,HPG"}, auth=AUTH, headers=H, follow_redirects=False)
check("normal form post works", r.status_code == 303 and wl.get() == ["FPT", "HPG"], (r.status_code, wl.get()))

# Zoom pairing: only the JID the page showed.
def stranger(jid, name):
    ev = {"event": "bot_notification", "event_ts": int(time.time() * 1000),
          "payload": {"accountId": "acc", "userJid": jid, "userName": name, "toJid": jid, "cmd": "danhmuc",
                      "robotJid": "bot@xmpp.zoom.us", "userId": jid, "timestamp": int(time.time() * 1000)}}
    body = json.dumps(ev).encode()
    return c.post("/webhook/zoom", content=body, headers=sign("zsecret", body))


stranger("alice@x", "Alice")
page = c.get("/zoom", auth=AUTH).text
check("page form carries the shown JID", 'name="jid" value="alice@x"' in page)
stranger("mallory@x", "Alice")                           # someone else writes before the click
r = c.post("/zoom/pair", data={"jid": "alice@x"}, auth=AUTH, headers=H, follow_redirects=False)
check("stale click pairs nobody", r.status_code == 303 and not zoom.pairing().get("jid"), zoom.pairing())
r = c.post("/zoom/pair", data={"jid": "mallory@x"}, auth=AUTH, headers=H, follow_redirects=False)
check("click for the shown JID pairs it", zoom.pairing().get("jid") == "mallory@x", zoom.pairing())
r = c.post("/zoom/pair", data={"jid": "alice@x"}, auth=("x", "wrong"), headers=H)
check("pair still needs the password", r.status_code == 401, r.status_code)

print("FAILED:", fails if fails else "none")
sys.exit(1 if fails else 0)
