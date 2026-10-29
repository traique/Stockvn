"""The altcoin's pages as the Hub serves them, and the section switch of both sections (offline):
the real bot/dashboard.build_app with a stub bot, after altcoin/hosted.py adapt().

- every page left (/, /ai, /caidat, /phienban): links and forms under /altcoin/, but for exactly
  the three that go to the stock (/ from the switch, /ai#kenh and /zoom from the channel card);
  never /altcoin/altcoin/ nor "@stock/";
- /caidat: one "Kênh do Stock quản lý" card instead of the two channel cards; with the card
  seam gone, the same text as a banner; adapt() names a seam that is gone (ERROR); the AI's
  text escaped in the dashboard's JS; a red banner on every page for seam_ok false or a
  dropped masked value;
- the child lets in only the Hub: X-Hub-Token, X-Hub-Cmd-Token for /_hub/*, GET /health alone
  without; a "/x" redirect gets the prefix;
- POST /_hub/command (the /alt bridge): 503 before the bot is built, 400 off the allowlist, 409
  for a write when not the leader or while another write runs (the lock held until the command
  is done, however long the caller waits), reads never locked; end to end with the stock's
  vnbot/altcoin_bridge.py over a real localhost port: a close that timed out, then a second one,
  is refused as busy and close_all runs once;
- the stock's switch (vnbot/ui.py sections()): only when SECTIONS is set, the altcoin's state
  beside its name, one <h1> and the main navigation unchanged, none on the error pages; the
  same markup on both sides.
"""
import logging
import re
import shutil
import subprocess
import sys
import types
from pathlib import Path

import _common  # noqa: F401 - clean env, no .env, offline, temp folder, repo on sys.path
_common.need("httpx")   # starlette's TestClient
from fastapi.responses import RedirectResponse  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

t = _common.Checks()
SUITES = Path(__file__).resolve().parent
ALT = _common.ROOT / "alt"          # a copy: no altcoin/.env or bot.env of this machine is ever read
shutil.copytree(_common.REPO / "altcoin" / "bot", ALT / "bot", ignore=shutil.ignore_patterns("__pycache__"))
for name in ("run.py", "hosted.py"):
    shutil.copyfile(_common.REPO / "altcoin" / name, ALT / name)
sys.path.insert(0, str(ALT))
import hosted  # noqa: E402
from bot import dashboard, ui as aui  # noqa: E402

HUB, CMD = "hub-token-0123456789abcdef", "cmd-token-0123456789abcdef"
hosted.TOKENS.update(hub=HUB, cmd=CMD)
plain_card = aui.card
missing = hosted.adapt()
t.ok("adapt(): every seam found (ui.page, ui.card, dashboard.build_app)", missing == [], missing)


class Store:
    def get(self, key):
        return None

    def instances(self):
        return []


bot = types.SimpleNamespace(switches=types.SimpleNamespace(all=lambda: {}), tg=types.SimpleNamespace(enabled=True),
                            zoom=types.SimpleNamespace(chatbot_on=False, webhook_on=False), store=Store(),
                            instance="botkeep", is_leader=True, learner=None, snapshot=lambda: {"ok": 1})
app = dashboard.build_app(bot)
app.add_api_route("/to-caidat", lambda: RedirectResponse("/caidat?msg=x", status_code=303))
app.add_api_route("/to-here", lambda: RedirectResponse("caidat", status_code=303))
c = TestClient(app, headers={"x-hub-token": HUB})
bare = TestClient(app)

# ── 1. the pages: links under /altcoin/, but the three to the stock ──
LINK = re.compile(r'\b(?:href|action|formaction)="(/[^"]*)"')
pages = {}
for path in ("/", "/ai", "/caidat", "/phienban"):
    r = c.get(path)
    pages[path] = r.text
    t.ok(f"{path}: 200", r.status_code == 200, r.status_code)
for path, html in pages.items():
    outside = sorted({u for u in LINK.findall(html) if not u.startswith("/altcoin/")})
    allowed = ["/", "/ai#kenh", "/zoom"] if path == "/caidat" else ["/"]
    t.ok(f"{path}: links outside /altcoin/ are only the stock's {allowed}", outside == allowed, outside)
    t.ok(f"{path}: no /altcoin/altcoin/, no @stock/, no /altcoin/ai#kenh or /altcoin/zoom link",
         "/altcoin/altcoin/" not in html and "@stock/" not in html and 'href="/altcoin/ai#kenh"' not in html
         and 'href="/altcoin/zoom"' not in html)
    t.ok(f"{path}: the section switch right after the header's start, Altcoin current; one <h1>",
         html.count('<div class="top-in"><nav class="seg sections" aria-label="Khu vực"><a href="/">Cổ phiếu</a>'
                    '<a href="/altcoin/" class="on" aria-current="true">Altcoin</a></nav>') == 1
         and html.count("<h1") == 1 and ".sections{" in html)
made = aui.page("Thử", '<a href="/api/x">a</a><form action="/caidat"><button formaction="/b">b</button></form>'
                      '<a href="//cdn.example/x">c</a><a href="https://okx.com/x">d</a><a href="mailto:a@b">e</a>'
                      '<a href="@stock/zoom">f</a>', active="home")
t.ok("a '/x' link, form or button gets the prefix; '//', 'https:', 'mailto:' are left alone; '@stock/x' is '/x'",
     'href="/altcoin/api/x"' in made and 'action="/altcoin/caidat"' in made and 'formaction="/altcoin/b"' in made
     and 'href="//cdn.example/x"' in made and 'href="https://okx.com/x"' in made and 'href="mailto:a@b"' in made
     and 'href="/zoom"' in made)
settings = pages["/caidat"]
t.ok("/caidat: the two channel cards are gone (no switch form, no Zoom test)",
     "Thông báo &amp; tin nhắn" not in settings and "Thông báo & tin nhắn" not in settings and "<h2>Zoom</h2>" not in settings
     and "caidat/kenh" not in settings and "zoomtest" not in settings)
t.ok("/caidat: one 'Kênh do Stock quản lý' card with /ai#kenh, /zoom and the /alt commands",
     settings.count("<h2>Kênh do Stock quản lý</h2>") == 1 and 'href="/ai#kenh"' in settings and 'href="/zoom"' in settings
     and "/alt status" in settings)
t.ok("/caidat: the rest of the page kept (parameters, downloads under /altcoin/ by relative links)",
     "Thông số đang dùng" in settings and 'href="api/export/all.json"' in settings)
wrapped, aui.card = aui.card, plain_card      # as if the card seam were gone: the old cards stay
lost = c.get("/caidat").text
aui.card = wrapped
t.ok("card seam gone: a banner with the same text and links on /caidat", lost.count('class="alert alert-info"') == 1
     and "Thông báo & tin nhắn</h2>" in lost and 'href="/ai#kenh"' in lost and 'href="/zoom"' in lost
     and "@stock/" not in lost)
hosted_missing = subprocess.run(
    [sys.executable, "-c", "import sys, logging; sys.path[:0] = sys.argv[1:3]; import _common, hosted; "
     "from bot import dashboard, ui; logging.basicConfig(level=logging.ERROR, stream=sys.stdout, format='%(message)s'); "
     "del ui.card; del dashboard.build_app; print('MISSING', hosted.adapt())", str(SUITES), str(ALT)],
    capture_output=True, text=True, timeout=120)
out = hosted_missing.stdout
t.ok("adapt() names the seams that are gone, each with an ERROR line", "MISSING ['ui.card', 'dashboard.build_app']" in out
     and "Seam ui.card không còn" in out and "Seam dashboard.build_app không còn" in out, out + hosted_missing.stderr[-500:])
# The AI's text now runs on the stock's origin (same password): never raw into innerHTML
raw_ai = re.findall(r"\$\{(?:r\.reason|r\.decision|m\.last_error|x\.ai_reason|x\.ai_decision|x\.lesson|x\.verdict"
                    r"|x\.tags)\b(?!\?)", dashboard.APP_JS)     # "${x.tags?`…`:''}": a test, not shown
t.ok("dashboard JS: the AI's reason, decision, error, lesson, verdict and tags go through esc()", not raw_ai and all(
    f"esc({f})" in dashboard.APP_JS for f in ("r.reason", "r.decision", "m.last_error", "x.ai_reason", "x.ai_decision",
                                              "x.lesson", "x.verdict", "x.tags")), raw_ai)
t.ok("no banner while the hub is fine (or absent)", 'class="alert alert-err"' not in pages["/"])
hosted.HUB = hosted.Hub(None, {"dropped_secrets": ["ALT_OKX_API_SECRET", "OPENROUTER_API_KEY"]})
hosted.HUB.sent = {"t": "state", "seam_ok": False}
for path in ("/", "/ai", "/caidat", "/phienban"):
    html = c.get(path).text
    t.ok(f"{path}: seam_ok false and dropped secrets → one red banner right after <main>, both said",
         html.count('class="alert alert-err"') == 1 and '<main id="main"><div class="alert alert-err" role="alert">' in html
         and "lỗi kênh" in html and "ALT_OKX_API_SECRET, OPENROUTER_API_KEY" in html)
hosted.HUB = hosted.Hub(None, {"dropped_secrets": []})
hosted.HUB.sent = {"t": "state", "seam_ok": True}
t.ok("...both fine again: no banner", 'class="alert alert-err"' not in c.get("/").text)
hosted.HUB = None

# ── 2. only the Hub gets in ──
t.ok("no X-Hub-Token -> 403", bare.get("/").status_code == 403 and bare.get("/api/state").status_code == 403)
t.ok("a wrong X-Hub-Token -> 403", bare.get("/", headers={"x-hub-token": HUB + "x"}).status_code == 403)
t.ok("the command token is not the proxy's -> 403", bare.get("/", headers={"x-hub-token": CMD}).status_code == 403)
t.ok("GET /health without a token (the supervisor's probe) -> 200", bare.get("/health").status_code == 200)
t.ok("...any other method on /health needs it (403)", bare.head("/health").status_code == 403
     and bare.post("/health").status_code == 403)
t.ok("POST /api/learn/run without a token -> 403", bare.post("/api/learn/run", json={}).status_code == 403)
t.ok("with X-Hub-Token: the API answers", c.get("/api/state").json() == {"ok": 1})
t.ok("/_hub/command with X-Hub-Token only -> 403", c.post("/_hub/command", json={}).status_code == 403
     and c.get("/_hub").status_code == 403)
t.ok("/_hub/command with the right X-Hub-Cmd-Token passes the gate (the bot not built yet: 503)",
     bare.post("/_hub/command", headers={"x-hub-cmd-token": CMD}, json={"cmd": "status"}).status_code == 503)
t.ok("/_hub/command with a wrong X-Hub-Cmd-Token -> 403",
     bare.post("/_hub/command", headers={"x-hub-cmd-token": HUB}, json={}).status_code == 403)
r = c.get("/to-caidat", follow_redirects=False)
t.ok("a '/x' redirect gets the prefix (/altcoin/x)", r.headers.get("location") == "/altcoin/caidat?msg=x", r.headers)
r = c.get("/to-here", follow_redirects=False)
t.ok("a relative redirect is left alone", r.headers.get("location") == "caidat", r.headers)
hosted.TOKENS.update(hub="", cmd="")
t.ok("no token set at all (HUB_TOKEN missing): everything but /health -> 403, even an empty header",
     bare.get("/", headers={"x-hub-token": ""}).status_code == 403 and bare.get("/health").status_code == 200
     and bare.post("/_hub/command", headers={"x-hub-cmd-token": ""}).status_code == 403)
hosted.TOKENS.update(hub=HUB, cmd=CMD)

# ── 3. the stock's switch ──
from vnbot import ui  # noqa: E402

t.ok("SECTIONS unset (the stock alone, the tests' harness): no switch", ui.SECTIONS is None and ui.sections() == ""
     and "sections" not in ui.page("Tổng quan", ui.title("Tổng quan")).split("<body>", 1)[1])
plain = ui.page("Tổng quan", ui.title("Tổng quan"))
state = {"state": "starting"}
ui.SECTIONS = lambda: state
page = ui.page("Tổng quan", ui.title("Tổng quan"))
main_nav = re.compile(r'<nav aria-label="Điều hướng chính">.*?</nav>', re.S)
t.ok("with SECTIONS: one <h1>, the main navigation unchanged", page.count("<h1") == 1
     and main_nav.search(page).group(0) == main_nav.search(plain).group(0))
t.ok("with SECTIONS: the switch under the brand and in the phone bar, Cổ phiếu current",
     page.count('<nav class="seg sections" aria-label="Khu vực"><a href="/" class="on" aria-current="true">Cổ phiếu</a>'
                '<a href="/altcoin/">Altcoin') == 2
     and page.index('class="seg sections"') < page.index('<nav aria-label="Điều hướng chính">')
     and '<span class="tt">Tổng quan</span><nav class="seg sections"' in page)
labels = {}
for snap in ({"state": "running", "seam_ok": True}, {"state": "running", "seam_ok": False}, {"state": "starting"},
             {"state": "waiting"}, {"state": "backoff"}, {"state": "disabled"}, {"state": "stopped"}, {"state": "off"}):
    state = snap
    labels[snap["state"] + ("/seam" if snap.get("seam_ok") is False else "")] = re.search(
        r'<a href="/altcoin/">([^<]*)</a>', ui.sections()).group(1)
t.ok("the Altcoin label shows its state", labels == {
    "running": "Altcoin", "running/seam": "Altcoin (lỗi kênh)", "starting": "Altcoin (đang khởi động)",
    "waiting": "Altcoin (chờ instance khác)", "backoff": "Altcoin (lỗi)", "disabled": "Altcoin (tắt)",
    "stopped": "Altcoin (tắt)", "off": "Altcoin (tắt)"}, labels)
t.ok("the phone bar's switch: names only", ">Altcoin</a>" in ui.sections(compact=True))
ui.SECTIONS = lambda: 1 / 0
t.ok("a failing state never breaks a page", "Altcoin</a>" in ui.page("Tổng quan", ui.title("Tổng quan")))
ui.SECTIONS = lambda: {"state": "running"}
error_page = ui.page("Lỗi", ui.title("Đã xảy ra lỗi"), show_bot=False, show_version=False)
t.ok("no switch (nor the altcoin's state) on the error pages, seen without the password", "sections" not in
     error_page.split("<body>", 1)[1])
stock_nav = re.search(r'<nav class="seg sections".*?</nav>', ui.sections()).group(0)
alt_nav = hosted.SECTIONS
strip = re.compile(r' class="on" aria-current="true"')
t.ok("the same switch on both sides, each marking its own section",
     strip.sub("", stock_nav) == strip.sub("", alt_nav) and 'href="/" class="on"' in stock_nav
     and 'href="/altcoin/" class="on"' in alt_nav, (stock_nav, alt_nav))
ui.SECTIONS = None

# ── 4. POST /_hub/command ──
import threading  # noqa: E402
import time  # noqa: E402

import uvicorn  # noqa: E402

release = threading.Event()


class CmdBot:
    """handle_command as bot.main.Bot's, close_all held until ``release``."""

    def __init__(self):
        self.calls, self.closing, self.is_leader, self.start_equity = [], 0, True, 100.0

    def handle_command(self, text, source="Telegram"):
        self.calls.append((text, source))
        if text == "close":
            self.closing += 1
            release.wait(20)
            self.closing -= 1
            return "Đã đóng 1 lệnh."
        if text == "lessons":
            raise KeyError("lesson")
        return f"{text} qua {source}"


logging.getLogger("hosted").addHandler(logging.NullHandler())     # the ERROR of the failing command, checked below
cmd_bot = CmdBot()
cmd_app = dashboard.build_app(cmd_bot)
cc = TestClient(cmd_app, headers={"x-hub-cmd-token": CMD})
r = cc.post("/_hub/command", json={"cmd": "status", "source": "Zoom"})
t.ok("status: 200 {ok, reply}, the source kept (Zoom)", r.status_code == 200
     and r.json() == {"ok": True, "reply": "status qua Zoom"}, r.text)
r = cc.post("/_hub/command", json={"cmd": "STATUS", "source": "evil"})
t.ok("any other source is Telegram; the name in any case", r.json() == {"ok": True, "reply": "status qua Telegram"}, r.text)
n = len(cmd_bot.calls)
codes = [cc.post("/_hub/command", json={"cmd": c}).status_code for c in ("zoompair", "zoomtest", "", "help", "close_all")]
t.ok("off the allowlist (zoompair, zoomtest, empty, help...): 400, handle_command not called",
     codes == [400] * 5 and len(cmd_bot.calls) == n, codes)
r = cc.post("/_hub/command", json={"cmd": "lessons"})
t.ok("handle_command raises: 200 {ok: false, error}", r.status_code == 200 and r.json()["ok"] is False
     and "KeyError" in r.json()["error"], r.text)
t.ok("only with X-Hub-Cmd-Token (X-Hub-Token alone: 403)",
     TestClient(cmd_app, headers={"x-hub-token": HUB}).post("/_hub/command", json={"cmd": "status"}).status_code == 403)
cmd_bot.is_leader = False
r = cc.post("/_hub/command", json={"cmd": "pause"})
t.ok("a write when not the leader: 409 leader:false, not run; a read still runs",
     r.status_code == 409 and r.json() == {"ok": False, "leader": False} and ("pause", "Telegram") not in cmd_bot.calls
     and cc.post("/_hub/command", json={"cmd": "status"}).status_code == 200)
cmd_bot.is_leader = True

# a real server, and the stock's bridge
from vnbot import altcoin_bridge as ab, altcoin_host as ah  # noqa: E402

t.ok("the child's allowlist is the bridge's (READ + WRITE), the same writes", set(hosted.COMMANDS) == set(ab.READ + ab.WRITE)
     and set(hosted.WRITES) == set(ab.WRITE) and set(ab.CONFIRM) <= set(ab.WRITE))

server = uvicorn.Server(uvicorn.Config(cmd_app, host="127.0.0.1", port=0, log_level="error"))
threading.Thread(target=server.run, daemon=True).start()
deadline = time.monotonic() + 20
while not server.started and time.monotonic() < deadline:
    time.sleep(0.05)
port = server.servers[0].sockets[0].getsockname()[1]
sup = ah.Supervisor(_common.ROOT / "sup", environ={"ALTCOIN_ENABLED": "1"})
sup._snap = {**sup._snap, "state": "running", "port": port}
sup._tokens = (HUB, CMD)
sup.enabled = False
bridge = ab.Bridge(sup)                 # no notify thread
ab.SLOW["close"] = 1
first = bridge.call("close", "Telegram")
t.ok("close timed out after 1 s (close_all still running): 'still running, do not send again'",
     first.startswith("⏱ Altcoin chưa trả lời sau 1 giây. Lệnh vẫn đang chạy") and cmd_bot.closing == 1, first)
second = bridge.call("close", "Telegram")
t.ok("a second close meanwhile: 409 busy -> 'đang có lệnh', close_all not run again",
     second == ab.BUSY and cmd_bot.calls.count(("close", "Telegram")) == 1, second)
t.ok("...pause too (one write at a time)", bridge.call("pause", "Telegram") == ab.BUSY)
t.ok("...a read is not locked", bridge.call("status", "Telegram") == "🪙 Altcoin › status\nstatus qua Telegram")
release.set()
deadline = time.monotonic() + 10
while cmd_bot.closing and time.monotonic() < deadline:
    time.sleep(0.05)
time.sleep(0.2)
ab.SLOW["close"] = 60
t.ok("once the first close is done, the next one runs", bridge.call("close", "Telegram") == "🪙 Altcoin › close\nĐã đóng 1 lệnh."
     and cmd_bot.calls.count(("close", "Telegram")) == 2)
server.should_exit = True

t.done()
