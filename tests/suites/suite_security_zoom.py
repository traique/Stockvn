"""Zoom webhook security: only requests signed with this app's secrets, fresh timestamps,
strangers never run commands, pairing only of the account the owner saw (offline)."""
import json
import os
import time

import _common  # noqa: F401 - clean env, no .env, offline, temp folder, repo on sys.path
from _web_harness import *  # noqa: E402,F401,F403
from fastapi.testclient import TestClient  # noqa: E402

t = _common.Checks()
AUTH = ("x", "s3cret-pass")
SAME = {"origin": "http://testserver"}

app, zoom, handled, jobs, wl, sw = build()
c = TestClient(app, raise_server_exceptions=False)


def msg(jid, text, name="Mallory", mid=None):
    return json.dumps({"event": "bot_notification", "payload": {"userJid": jid, "cmd": text, "userName": name,
                       "accountId": "acc", "toJid": "bot@xmpp.zoom.us", "messageId": mid or str(time.time())}}).encode()


def commands():
    return [h for h in handled if h[0] != "ALERT"]


t.ok("commands on with a secret token", zoom.commands_on and zoom.chatbot_on)
b = msg("evil@x", "/von 1")
t.ok("unsigned -> 401", c.post("/webhook/zoom", content=b, headers={"content-type": "application/json"}).status_code == 401)
uv = json.dumps({"event": "endpoint.url_validation", "payload": {"plainToken": "v0:123:{}"}}).encode()
r = c.post("/webhook/zoom", content=uv, headers={"content-type": "application/json"})
t.ok("unsigned URL validation -> 401 (no signing oracle)", r.status_code == 401 and "encryptedToken" not in r.text)
r = c.post("/webhook/zoom", content=uv, headers=sign("zsecret", uv))
t.ok("signed URL validation answered", r.status_code == 200 and "encryptedToken" in r.text, r.text[:80])
t.ok("wrong secret -> 401", c.post("/webhook/zoom", content=b, headers=sign("guess", b)).status_code == 401)
old = str(int((time.time() - 1000) * 1000))
t.ok("stale timestamp -> 401", c.post("/webhook/zoom", content=b, headers=sign("zsecret", b, ts=old)).status_code == 401)
weird = {"x-zm-signature": "v0=aa", "x-zm-request-timestamp": "1²".encode("latin-1"),   # raw bytes on the wire
         "content-type": "application/json"}
t.ok("timestamp '1²' -> refused, no server error", c.post("/webhook/zoom", content=b, headers=weird).status_code == 401)
ok, why = zoom.verify_detail(b"{}", {"x-zm-signature": "v0=aa", "x-zm-request-timestamp": "1²"})
t.ok("verify_detail('1²') is a refusal, not an exception", ok is False and "timestamp" in why, why)

handled.clear()
r = c.post("/webhook/zoom", content=b, headers=sign("zsecret", b))
t.ok("signed stranger: accepted, not run, waits for pairing", r.status_code == 200 and not commands()
     and zoom.pending().get("jid") == "evil@x", (commands(), zoom.pending()))
t.ok("the owner is alerted", any(h[0] == "ALERT" for h in handled))

# pairing: only the account the page showed
a = msg("alice@x", "trangthai", name="Alice")
c.post("/webhook/zoom", content=a, headers=sign("zsecret", a))
m = msg("mallory@x", "trangthai", name="Alice")              # same display name, another account
c.post("/webhook/zoom", content=m, headers=sign("zsecret", m))
r = c.post("/zoom/pair", data={"jid": "alice@x"}, auth=AUTH, headers=SAME, follow_redirects=False)
t.ok("stale pairing click pairs nobody", not zoom.pairing().get("jid"), zoom.pairing())
r = c.post("/zoom/pair", data={"jid": "mallory@x"}, auth=AUTH, headers=SAME, follow_redirects=False)
t.ok("click for the shown account pairs it", zoom.pairing().get("jid") == "mallory@x")

handled.clear()
p = msg("mallory@x", "danhmuc", mid="m-1")
c.post("/webhook/zoom", content=p, headers=sign("zsecret", p))
t.ok("paired account's command runs", [x[1] for x in commands()] == ["/danhmuc"], commands())
c.post("/webhook/zoom", content=p, headers=sign("zsecret", p))
t.ok("a replayed message runs once", len(commands()) == 1, commands())
e = msg("evil@x", "danhmuc")
c.post("/webhook/zoom", content=e, headers=sign("zsecret", e))
t.ok("other accounts still do not", len(commands()) == 1, commands())

# a Zoom app without a Secret Token: no commands at all, notifications only
os.environ["ZOOM_SECRET_TOKEN"] = ""
app2, zoom2, handled2, *_ = build()
c2 = TestClient(app2, raise_server_exceptions=False)
t.ok("no secret token -> commands off", zoom2.commands_on is False and zoom2.chatbot_on)
b2 = json.dumps({"event": "bot_notification", "payload": {"userJid": "a@x", "cmd": "/von 1", "messageId": "1"}}).encode()
for name, headers in (("unsigned", {"content-type": "application/json"}),
                      ("empty signature", {"x-zm-signature": "", "x-zm-request-timestamp": str(int(time.time())),
                                           "authorization": ""}),
                      ("signed with an empty key", sign("", b2)),
                      ("blank authorization", {"authorization": " "})):
    t.ok(f"{name} -> 401", c2.post("/webhook/zoom", content=b2, headers=headers).status_code == 401)
r = c2.post("/webhook/zoom", content=b2, headers=sign("csecret", b2))
t.ok("signed with the client secret: still no command (404)", r.status_code == 404 and not handled2, (r.status_code, handled2))
bd = json.dumps({"event": "app_deauthorized", "payload": {}}).encode()
t.ok("unsigned deauthorization -> 401", c2.post("/webhook/zoom", content=bd,
                                                headers={"content-type": "application/json"}).status_code == 401)

t.done()
