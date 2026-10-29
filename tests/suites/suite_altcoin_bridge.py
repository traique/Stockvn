"""The altcoin through the stock's one Telegram bot and one Zoom app (vnbot/altcoin_bridge.py),
offline: the real TelegramBot with a fake Bot API, the real Zoom webhook path, a fake child
(a localhost HTTP server) and the real supervisor's pipe reader.

- /alt, /alt help, /ALT STATUS, /alt@Bot, /altcoin, Zoom "alt status"; an unknown word and the
  altcoin's bare commands (/close, /status...) never reach the child; with no bridge (the stock
  alone) or the altcoin off, HELP, /docs and every stock command are unchanged (off: every /alt,
  zoomtest and close included, says so and sends nothing);
- the stock's own permissions (a stranger runs nothing, a group gets its answer, Zoom commands
  switched off run nothing); close/apply/resume need "/alt <lệnh> xacnhan" from the same
  sender in the same chat within 120 s, once (Zoom: the real JID, not the shared "zoom" id);
- the answers when the child is off, starting, waiting, unreachable, not the leader, busy,
  slow or failing; a write is never sent twice, a timed-out close then a second one is "busy";
- notifications: prefix (on every part of a long one, answers too), every report chat once and
  Zoom, takeover dropped, start notice once per 30 minutes, /resume -> /alt resume, dedupe (a
  dropped one may come again), 15 a minute with a summary a minute after the first dropped
  (🛑 always), a blocked channel never blocks the pipe reader, failures counted, the "altcoin"
  switch, the 50 kept before the stock is up, secrets masked, /alt zoomtest per channel; at
  shutdown flush() sends what is queued (Zoom's queue too), bounded, at once when off.
"""
import contextlib
import io
import json
import logging
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import _common  # noqa: F401 - clean env, no .env, offline, temp folder, repo on sys.path

_common.need("httpx")
TOKEN = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawQ"
SECRET = "okx-panel-secret-0123456789"
os.environ.update({"TELEGRAM_TOKEN": TOKEN, "TELEGRAM_ALLOWED_IDS": "111,333",
                   "TELEGRAM_REPORT_CHAT_IDS": "-100500,-100600", "ALT_OKX_API_SECRET": SECRET,
                   "ZOOM_ALLOWED_USERS": "b@xmpp.zoom.us"})
import _web_harness as wh  # noqa: E402 - ZOOM_*, WEB_PASSWORD, its data folder
from fastapi.testclient import TestClient  # noqa: E402

from vnbot import altcoin_bridge as ab, altcoin_host as ah, eventlog, pages  # noqa: E402
from vnbot import telegram_bot as tb  # noqa: E402
from vnbot.channels import Switches  # noqa: E402

eventlog.refresh_secrets()
logging.getLogger("vnbot").addHandler(logging.NullHandler())     # the warnings checked here: not on the console
t = _common.Checks()


def wait_for(cond, seconds=10.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return bool(cond())


# ── the fake child: POST /_hub/command, answered by CHILD["fn"] ──
SEEN: list[dict] = []
CHILD = {"fn": lambda body: (200, {"ok": True, "reply": f"trả lời {body['cmd']}"})}


class Child(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        SEEN.append({"path": self.path, "token": self.headers.get("x-hub-cmd-token"), "body": body})
        status, answer = CHILD["fn"](body)
        data = json.dumps(answer, ensure_ascii=False).encode()
        with contextlib.suppress(OSError):          # the bridge may have given up waiting
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), Child)
server.daemon_threads = True
threading.Thread(target=server.serve_forever, daemon=True).start()
PORT = server.server_address[1]


def supervisor(state="running", port=PORT, reason=None, enabled=True):
    sup = ah.Supervisor(_common.ROOT / "sup", environ={"ALTCOIN_ENABLED": "1"} if enabled else {})
    sup._snap = {**sup._snap, "state": state, "port": port, "reason": reason}
    sup._tokens = ("hub-token", "cmd-token")
    return sup


def quiet(sup):
    """A bridge without its altcoin-notify thread: the checks call handle() themselves."""
    enabled, sup.enabled = sup.enabled, False
    bridge = ab.Bridge(sup)
    sup.enabled = enabled
    return bridge


class Clock:
    def __init__(self):
        self.t = 10_000.0

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


# ── the stock's bot, its Bot API faked ──
CALLS: list[tuple] = []
FAIL = {"on": False}


def fake_call(method, http_timeout=30, **params):
    if FAIL["on"]:
        raise RuntimeError(f"Telegram {method}: Too Many Requests: retry later")
    CALLS.append((method, params))
    return {}


def texts(chat):
    return [p["text"] for m, p in CALLS if m == "sendMessage" and p["chat_id"] == chat]


def last(chat):
    found = texts(chat)
    return found[-1] if found else ""


switches = Switches(wh.SETTINGS.data_dir)
bot = tb.TelegramBot(wh.SETTINGS, wh.JobQueue(wh.SETTINGS), wh.Watchlist(wh.SETTINGS))
bot._call = fake_call
bot.switches = switches


def m(user, chat, text):
    return {"chat": {"id": chat}, "from": {"id": user}, "text": text}


# ── 1. syntax, and what never reaches the child ──
SUP = supervisor()
bot.alt = quiet(SUP)
bot._handle(m(111, 111, "/alt"))
bot._handle(m(111, 111, "/alt help"))
t.ok("/alt and /alt help: the altcoin's command table, built by the stock (child not called)",
     texts(111)[-2:] == [ab.HELP, ab.HELP] and "/alt close" in ab.HELP and not SEEN)
bot._handle(m(111, 111, "/ALT STATUS"))
t.ok("/ALT STATUS: POST /_hub/command with X-Hub-Cmd-Token, only the command's name and the source",
     SEEN and SEEN[-1] == {"path": "/_hub/command", "token": "cmd-token", "body": {"cmd": "status", "source": "Telegram"}},
     SEEN[-1:])
t.ok("...the answer: '🪙 Altcoin › status' then the child's text", last(111) == "🪙 Altcoin › status\ntrả lời status",
     last(111))
for text in ("/alt@VnBot trades", "/altcoin signals", "/Altcoin@VnBot btc extra words"):
    bot._handle(m(111, 111, text))
t.ok("/alt@Bot, /altcoin, /Altcoin@Bot: the same (extra words never sent)",
     [s["body"]["cmd"] for s in SEEN[-3:]] == ["trades", "signals", "btc"] and len(SEEN) == 4, SEEN[-3:])
n = len(SEEN)
for text in ("/alt foo", "/alt zoompairx", "/alt close_all", "/alt /close"):
    bot._handle(m(111, 111, text))
t.ok("an unknown word: the command table, never sent", len(SEEN) == n and texts(111)[-4:] == [ab.HELP] * 4)
bot._handle(m(111, 111, "/alt zoompair a@b"))
t.ok("/alt zoompair: points to the stock's commands", last(111) == ab.ZOOM_SHARED and len(SEEN) == n)
for bare in ("/close", "/status", "/apply", "/resume", "/pause", "/ai", "/close@VnBot"):
    bot._handle(m(111, 111, bare))
    t.ok(f"bare {bare}: the /alt hint, never sent", last(111).startswith("Lệnh Altcoin giờ gõ /alt <lệnh>")
         and f"/alt {bare[1:].split('@')[0]}" in last(111) and len(SEEN) == n, last(111))
stock_commands = {"/phienban", "/trangthai", "/id", "/help", "/von", "/khop", "/danhmuc", "/zoompair"}
t.ok("no bare altcoin command is a stock command", not set(ab.BARE) & stock_commands
     and all(c not in tb.TAISAN_COMMANDS for c in ab.BARE))
bot._handle(m(111, 111, "/phienban"))
t.ok("/phienban stays the stock's", last(111).startswith("🏷️ TradingAgents VN") and len(SEEN) == n, last(111)[:80])

# ── 2. the stock alone (no bridge), or the altcoin off: nothing changes ──
bot.alt = None
plain_help = bot._help()
bot._handle(m(111, 111, "/alt status"))
t.ok("no bridge (the stock's test harness): /alt gets the stock's help", last(111) == plain_help and len(SEEN) == n)
bot._handle(m(111, 111, "/close"))
t.ok("no bridge: a bare /close gets the stock's help", last(111) == plain_help)
bot.alt = quiet(supervisor("off", enabled=False))
t.ok("altcoin off: HELP unchanged", bot._help() == plain_help)
bot._handle(m(111, 111, "/close"))
t.ok("altcoin off: a bare /close gets the stock's help, no hint", last(111) == plain_help)
bot._handle(m(111, 111, "/alt status"))
t.ok("altcoin off: /alt status says so", last(111) == "🪙 Altcoin đang tắt (ALTCOIN_ENABLED khác 1)." and len(SEEN) == n,
     last(111))
for text in ("/alt zoomtest", "/alt close", "/alt close xacnhan", "/alt", "/alt pause"):
    bot._handle(m(111, 111, text))
    t.ok(f"altcoin off: {text} says so, nothing to the report chats, nothing asked",
         last(111) == "🪙 Altcoin đang tắt (ALTCOIN_ENABLED khác 1)." and not texts(-100500) and not texts(-100600)
         and len(SEEN) == n, last(111))
bot.alt = quiet(SUP)
line = "/alt <lệnh> - bot Altcoin (OKX): gõ /alt để xem lệnh\n"
t.ok("altcoin on: HELP gains the /alt line only", line in bot._help() and bot._help().replace(line, "") == plain_help)
docs_off = pages.docs_body()
os.environ["ALTCOIN_ENABLED"] = "1"
docs_on = pages.docs_body()
del os.environ["ALTCOIN_ENABLED"]
t.ok("/docs: the 'alt' row only with ALTCOIN_ENABLED=1, before trogiup", "<code>alt status</code>" not in docs_off
     and "<code>alt status</code>" in docs_on and docs_on.index("alt status") < docs_on.index("<code>trogiup</code>")
     and docs_on.replace(docs_on[docs_on.index("<tr><td class='cmd'><code>alt status"):
                                 docs_on.index("<tr><td class='cmd'><code>trogiup")], "") == docs_off)

# ── 3. permissions: the stock's ──
bot._handle(m(222, -100500, "/alt close xacnhan"))
bot._handle(m(222, 222, "/alt status"))
t.ok("a stranger: the stock's refusal, nothing sent to the child", len(SEEN) == n
     and last(-100500).startswith("Bot chưa cho phép") and last(222).startswith("Bot chưa cho phép"))
bot._handle(m(111, -100500, "/alt status"))
t.ok("an allowed user in a report group: the answer goes to the group", last(-100500).startswith("🪙 Altcoin › status")
     and len(SEEN) == n + 1)
bot._handle(m(333, 333, "/alt pause"))
t.ok("a write command: any allowed user (no list of its own, one user), sent at once (pause needs no confirmation)",
     SEEN[-1]["body"]["cmd"] == "pause" and last(333) == "🪙 Altcoin › pause\ntrả lời pause")

# ── 4. the two-step commands ──
clock = Clock()
bot.alt._now = clock.now
n = len(SEEN)
for cmd in ("close", "apply", "resume"):
    bot._handle(m(111, 111, f"/alt {cmd}"))
    t.ok(f"/alt {cmd}: asks, sends nothing", last(111).startswith("⚠️ ") and f"/alt {cmd} xacnhan" in last(111)
         and "120 giây" in last(111) and len(SEEN) == n, last(111))
clock.t += 121
bot._handle(m(111, 111, "/alt close xacnhan"))
t.ok("xacnhan after 120 s: refused, nothing sent", len(SEEN) == n and "đã quá 120 giây" in last(111), last(111))
bot._handle(m(111, 111, "/alt close"))
bot._handle(m(333, 111, "/alt close xacnhan"))
t.ok("xacnhan by another user in the same chat: refused", len(SEEN) == n and "Không có yêu cầu /alt close" in last(111))
bot._handle(m(111, -100500, "/alt close xacnhan"))
t.ok("xacnhan by the same user in another chat: refused", len(SEEN) == n and "Không có yêu cầu" in last(-100500))
bot._handle(m(111, 111, "/alt apply xacnhan"))
t.ok("xacnhan of another command: refused", len(SEEN) == n)
bot._handle(m(111, 111, "/alt close XACNHAN"))
t.ok("the right one: close sent once, its name only, on the alt-cmd thread ('⏳' first)",
     wait_for(lambda: len(SEEN) == n + 1 and last(111).startswith("🪙 Altcoin › close"))
     and SEEN[-1]["body"] == {"cmd": "close", "source": "Telegram"} and "⏳ Đang gửi lệnh tới Altcoin…" in texts(111)[-2:])
bot._handle(m(111, 111, "/alt close xacnhan"))
t.ok("a second xacnhan: refused (used once)", len(SEEN) == n + 1 and "Không có yêu cầu" in last(111))
bot._handle(m(111, 111, "/alt resume"))
bot._handle(m(111, 111, "/alt resume xácnhận"))
t.ok("resume: on the chat's lane, 'xácnhận' with accents accepted", wait_for(lambda: len(SEEN) == n + 2)
     and SEEN[-1]["body"]["cmd"] == "resume")
bot.alt._now = time.monotonic

# Zoom: through the real webhook (signed), ZoomChannel.handle and zoom_command
app, zoom, handled, _, _, sw = wh.build()
zoom.handler = bot.zoom_command
bot.zoom = zoom
zoom.pair("a@xmpp.zoom.us", "A")
zc = TestClient(app, raise_server_exceptions=False)


def zoom_says(jid, text, channel=""):
    p = {"userJid": jid, "cmd": text, "userName": jid[0].upper(), "accountId": "acc",
         "toJid": "chan@conference.xmpp.zoom.us" if channel else "bot@xmpp.zoom.us", "messageId": f"{jid}{time.time()}"}
    if channel:
        p["channelName"] = channel
    body = json.dumps({"event": "bot_notification", "payload": p}).encode()
    return zc.post("/webhook/zoom", content=body, headers=wh.sign("zsecret", body))


def zoom_replies():
    out = []
    while not zoom.queue.empty():
        kind, text, where = zoom.queue.get_nowait()
        out.append((text, where["user_jid"], where["to_jid"]))
    return out


n = len(SEEN)
zoom_says("a@xmpp.zoom.us", "alt status")
t.ok("Zoom 'alt status' (no slash): '/' added, sent with source Zoom, the answer back to Zoom",
     wait_for(lambda: len(SEEN) == n + 1) and SEEN[-1]["body"] == {"cmd": "status", "source": "Zoom"}
     and wait_for(lambda: not zoom.queue.empty()) and zoom_replies()[0][0].startswith("🪙 Altcoin › status"))
sw.set("zoom_commands", False)
zoom_says("a@xmpp.zoom.us", "alt status")
time.sleep(0.3)
t.ok("Zoom commands switched off: nothing runs", len(SEEN) == n + 1 and zoom.queue.empty())
sw.set("zoom_commands", True)
ROOM = "Phòng trade"
zoom_says("a@xmpp.zoom.us", "alt close", channel=ROOM)
t.ok("Zoom A asks to close in a channel", wait_for(lambda: not zoom.queue.empty())
     and "/alt close xacnhan" in zoom_replies()[0][0])
zoom_says("b@xmpp.zoom.us", "alt close xacnhan", channel=ROOM)     # also allowed (ZOOM_ALLOWED_USERS), same channel
t.ok("Zoom B (allowed too) confirms A's request in the same channel: refused, nothing sent (keyed by the real JID)",
     wait_for(lambda: not zoom.queue.empty()) and "Không có yêu cầu" in zoom_replies()[0][0] and len(SEEN) == n + 1)
zoom_says("a@xmpp.zoom.us", "alt close xacnhan")
t.ok("Zoom A confirms in the 1:1 chat, not in the channel it asked in: refused",
     wait_for(lambda: not zoom.queue.empty()) and "Không có yêu cầu" in zoom_replies()[0][0] and len(SEEN) == n + 1)
zoom_says("a@xmpp.zoom.us", "alt close xacnhan", channel=ROOM)
t.ok("Zoom A confirms its own request where it asked: close sent once, source Zoom",
     wait_for(lambda: len(SEEN) == n + 2) and SEEN[-1]["body"] == {"cmd": "close", "source": "Zoom"})
wait_for(lambda: zoom.queue.qsize() >= 2)
zoom_replies()
bot.zoom = None

# ── 5. when the child cannot answer ──
cases = [("off", supervisor("off", enabled=False), "🪙 Altcoin đang tắt (ALTCOIN_ENABLED khác 1)."),
         ("disabled", supervisor("disabled", reason="thiếu thư viện"), "🪙 Altcoin đang tắt (thiếu thư viện)."),
         ("starting", supervisor("starting"), ab.STARTING),
         ("backoff", supervisor("backoff", reason="thoát mã 1"), ab.STARTING),
         ("waiting", supervisor("waiting", reason="instance khác đang chạy: crypto@old-server – dừng server altcoin cũ"),
          "🪙 Altcoin đang chờ: instance khác đang chạy: crypto@old-server – dừng server altcoin cũ. "
          "Altcoin tự chạy khi instance đó dừng.")]
closed = ThreadingHTTPServer(("127.0.0.1", 0), Child)
dead_port = closed.server_address[1]
closed.server_close()
cases.append(("refused", supervisor(port=dead_port), ab.STARTING))
n = len(SEEN)
for name, sup, want in cases:
    got = quiet(sup).call("status", "Telegram")
    t.ok(f"child {name}: {want[:60]}", got == want, got)
answers = {"503": (503, {"ok": False, "error": "đang khởi động"}), "leader": (409, {"ok": False, "leader": False}),
           "busy": (409, {"ok": False, "busy": True}), "error": (200, {"ok": False, "error": f"KeyError: {SECRET}"}),
           "http500": (500, {"detail": "x"})}
want = {"503": ab.STARTING, "leader": ab.NOT_LEADER, "busy": ab.BUSY, "error": "⚠️ Altcoin lỗi: KeyError: ***",
        "http500": "⚠️ Altcoin lỗi: HTTP 500"}
bridge = quiet(SUP)
for name, answer in answers.items():
    CHILD["fn"] = lambda body, a=answer: a
    got = bridge.call("pause", "Telegram")
    t.ok(f"child answers {name}: {want[name]}", got == want[name], got)
t.ok("...each sent once (a write is never sent again)", len(SEEN) == n + len(answers))
ab.TIMEOUT = 1
CHILD["fn"] = lambda body: (time.sleep(2.5), (200, {"ok": True, "reply": "late"}))[1]
n = len(SEEN)
got = bridge.call("status", "Telegram")
t.ok("timeout, read command: try again later", got == "⏱ Altcoin chưa trả lời sau 1 giây, thử lại sau.", got)
got = bridge.call("pause", "Telegram")
t.ok("timeout, write command: still running in the altcoin, do not send again", got.startswith(
    "⏱ Altcoin chưa trả lời sau 1 giây. Lệnh vẫn đang chạy trong Altcoin") and "Đừng gửi lại" in got, got)
time.sleep(0.2)
t.ok("...sent once each, never again", len(SEEN) == n + 2, len(SEEN) - n)
ab.TIMEOUT = 15

# the stock's alt-cmd lock, then the child's: a close that timed out, then a second one
running = threading.Event()
release = threading.Event()
closes = []


def child_close(body):
    if body["cmd"] != "close":
        return 200, {"ok": True, "reply": "ok"}
    if running.is_set():                   # the child's lock (hosted.py WRITE_LOCK) is still held
        return 409, {"ok": False, "busy": True}
    running.set()
    closes.append(1)
    release.wait(10)
    running.clear()
    return 200, {"ok": True, "reply": "Đã đóng 2 lệnh."}


CHILD["fn"] = child_close
ab.SLOW["close"] = 1
bot.alt = quiet(SUP)
bot._handle(m(111, 111, "/alt close"))
bot._handle(m(111, 111, "/alt close xacnhan"))
wait_for(running.is_set)
bot._handle(m(111, 111, "/alt close"))
bot._handle(m(111, 111, "/alt close xacnhan"))
t.ok("a second close while the first runs: 'đang có lệnh' (the stock's alt-cmd thread)", last(111) == ab.BUSY, last(111))
t.ok("the first close times out after 1 s: still running, do not send again",
     wait_for(lambda: "Lệnh vẫn đang chạy" in last(111), 5), last(111))
bot._handle(m(111, 111, "/alt close"))
bot._handle(m(111, 111, "/alt close xacnhan"))
t.ok("then close + xacnhan again: the child is still busy -> 'đang có lệnh', no second close_all",
     wait_for(lambda: last(111) == ab.BUSY, 5) and closes == [1], (last(111), closes))
release.set()
time.sleep(0.3)
ab.SLOW["close"] = 60
bot._handle(m(111, 111, "/alt close"))
bot._handle(m(111, 111, "/alt close xacnhan"))
t.ok("once it is done, close runs again", wait_for(lambda: last(111) == "🪙 Altcoin › close\nĐã đóng 2 lệnh.", 5)
     and closes == [1, 1], last(111))

# ── 6. the answers ──
CHILD["fn"] = lambda body: (200, {"ok": True, "reply": {
    "learn": "Tự học: 2 đề xuất\n⏳ 2 đề xuất chờ duyệt – gõ /apply để áp dụng tất cả",
    "status": "x" * 9000, "lessons": f"khoá {SECRET} lộ"}[body["cmd"]]})
n = len(CALLS)
bot._handle(m(111, 111, "/alt status"))
t.ok("an answer of 9,000 characters: 3 Telegram messages, each with the header and its number",
     len(CALLS) == n + 3 and [x[:len(ab.PREFIX) + 6] for x in texts(111)[-3:]] == [
         f"{ab.PREFIX}({i}/3) " for i in (1, 2, 3)] and texts(111)[-3].endswith(") status\n" + "x" * (ab.PART - 7)),
     [x[:30] for x in texts(111)[-3:]])
bot._handle(m(111, 111, "/alt learn"))
t.ok("'gõ /apply' in an answer becomes 'gõ /alt apply'", "gõ /alt apply để áp dụng" in last(111) and "gõ /apply" not in last(111))
bot._handle(m(111, 111, "/alt lessons"))
t.ok("a secret in an answer is masked", SECRET not in last(111) and "khoá *** lộ" in last(111), last(111))
replies = []
bot.zoom = type("Z", (), {"reply": lambda self, target, text: replies.append((target, text)), "notify_on": False,
                          "sender": lambda self, target: ("a@x", "a@x")})()
bot._handle({"chat": {"id": "zoom:7"}, "from": {"id": tb.ZOOM_USER, "jid": "a@x", "chat": "a@x"}, "text": "/alt learn"})
t.ok("an answer to a Zoom command goes to zoom.reply", replies and replies[-1][0] == "zoom:7"
     and replies[-1][1].startswith("🪙 Altcoin › learn"))
n = len(replies)
bot._handle({"chat": {"id": "zoom:7"}, "from": {"id": tb.ZOOM_USER, "jid": "a@x", "chat": "a@x"}, "text": "/alt status"})
t.ok("...a long one: 3 Zoom replies, each with the header, none cut again by Zoom (3,800)",
     len(replies) == n + 3 and all(x[1].startswith(ab.PREFIX) and len(x[1]) <= 3800 for x in replies[-3:]),
     [x[1][:30] for x in replies])
bot.zoom = None

# ── 7. notifications ──


class Zoom:
    """Takes every notification (or none: take=False calls on_fail, as the real worker does)."""

    def __init__(self):
        self.sent, self.take, self.notify_on = [], True, True

    def send(self, text, on_fail=None):
        self.sent.append(text)
        if not self.take and on_fail is not None:
            on_fail()

    def deliver(self, item):
        self.sent.append(item[1])
        return self.take


fz = Zoom()
bot.zoom = fz
sup = supervisor()
bridge = quiet(sup)
clock = Clock()
bridge._now, bridge._sleep = clock.now, clock.sleep
bridge.attach(bot)
CALLS.clear()


def push(text):
    bridge.handle(text)
    clock.t += 2


def sent_texts():
    return [p["text"] for meth, p in CALLS if meth == "sendMessage" and p["chat_id"] == -100500]


push("📈 MỞ LONG BTC/USDT @ 65000")
t.ok("one notification: each report chat once, and Zoom, with the prefix",
     texts(-100500) == ["🪙 Altcoin › 📈 MỞ LONG BTC/USDT @ 65000"] and texts(-100600) == texts(-100500)
     and fz.sent == ["🪙 Altcoin › 📈 MỞ LONG BTC/USDT @ 65000"] and texts(111) == [])
push("👑 crypto tiếp quản vai trò giao dịch (chế độ PAPER).")
t.ok("the leader takeover notice is dropped (one instance)", len(sent_texts()) == 1)
push("🚀 Bot v3.2.0 khởi động – chế độ PAPER | vốn 100.00 USDT")
push("🚀 Bot v3.2.0 khởi động – chế độ PAPER | vốn 100.00 USDT (lần 2)")
clock.t += 1800
push("🚀 Bot v3.2.0 khởi động – chế độ PAPER | vốn 101.00 USDT")
t.ok("the start notice: at most one per 30 minutes", [x for x in sent_texts() if "khởi động" in x] == [
    "🪙 Altcoin › 🚀 Bot v3.2.0 khởi động – chế độ PAPER | vốn 100.00 USDT",
    "🪙 Altcoin › 🚀 Bot v3.2.0 khởi động – chế độ PAPER | vốn 101.00 USDT"], sent_texts())
push("🛑 DỪNG GIAO DỊCH: lỗ ngày 5%. Gõ /resume để chạy lại.")
t.ok("/resume in a notification becomes /alt resume", sent_texts()[-1] == "🪙 Altcoin › 🛑 DỪNG GIAO DỊCH: lỗ ngày 5%. "
     "Gõ /alt resume để chạy lại.", sent_texts()[-1])
push("📅 BÁO CÁO NGÀY")
push("📅 BÁO CÁO NGÀY")
clock.t += 121
push("📅 BÁO CÁO NGÀY")
t.ok("the same text within 120 s: sent once (counted), again after", sent_texts().count("🪙 Altcoin › 📅 BÁO CÁO NGÀY") == 2
     and sup.state()["bridge"]["deduped"] == 1)
push(f"lỗi khoá {SECRET}")
t.ok("a secret in a notification is masked", sent_texts()[-1] == "🪙 Altcoin › lỗi khoá ***")
long_text = ("Tổng kết ngày: lãi lỗ 🟢🔴 đề xuất tự học ✅ " * 400)[:12000]
frame = (json.dumps({"t": "notify", "n": 1, "ts": 0, "text": long_text}, ensure_ascii=False) + "\n").encode()
read_end, write_end = os.pipe()
os.write(write_end, frame)
os.close(write_end)
CALLS.clear()
fz.sent.clear()
with contextlib.redirect_stdout(io.StringIO()):
    sup._read_hub(read_end)                 # the real reader: the line, then put() of this bridge
queued = []
while not bridge._queue.empty():
    queued.append(bridge._queue.get_nowait())
queued = [x for x in queued if x]           # attach() left a None (the wake-up) in the queue
bridge.handle(queued[0])
clock.t += 2
parts = sent_texts()
t.ok("12,000 characters of Vietnamese and emoji (one frame of %d KiB): read whole, sent as Telegram parts of at most "
     "3,900 characters" % (len(frame) // 1024), len(frame) < 60 * 1024 and queued == [long_text]
     and len(parts) == 4 and all(len(x) <= 3900 for x in parts)
     and " ".join(re.sub(r"^🪙 Altcoin › \(\d/4\) ", "", x) for x in parts).split() == long_text.split(),
     [len(x) for x in parts])
t.ok("...every part, in each report chat and Zoom, starts with '🪙 Altcoin › (i/4)' (none cut again by a channel)",
     [x[:len(ab.PREFIX) + 6] for x in parts] == [f"{ab.PREFIX}({i}/4) " for i in (1, 2, 3, 4)]
     and texts(-100600) == parts and fz.sent == parts and all(len(x) <= 3800 for x in parts), [x[:20] for x in fz.sent])

# 30 in a minute: at most 15, the 🛑 one always, a summary, 1 s apart
clock.t += 600
CALLS.clear()
fz.sent.clear()
stamps = []
real_broadcast = bot.alt_broadcast
bot.alt_broadcast = lambda text, on_fail=None: (stamps.append(clock.t), real_broadcast(text, on_fail=on_fail))[1]
buf = io.StringIO()
handler = logging.StreamHandler(buf)
logging.getLogger("vnbot.altcoin_bridge").addHandler(handler)
logging.getLogger("vnbot.altcoin_bridge").setLevel(logging.INFO)
dropped0 = sup.state()["bridge"]["dropped"]
start = clock.t
first = None                                 # when the first one was dropped
for i in range(30):                          # as the altcoin-notify thread does: handle, then _summary
    at = clock.t
    bridge.handle("🛑 DỪNG GIAO DỊCH lần 20" if i == 20 else f"📊 tín hiệu số {i:02d}")
    if first is None and sup.state()["bridge"]["dropped"] > dropped0:
        first = at
    bridge._summary()
    clock.t = max(clock.t, start + 2 * (i + 1))
burst = sent_texts()
print(f"     ({len(burst)} of 30 sent)")
t.ok("30 notifications in a minute: at most 15 sent, no summary yet", len(burst) <= 15 and first is not None
     and not any("Bỏ qua" in x for x in burst), burst[-3:])
t.ok("...the 🛑 one among them still sent", "🪙 Altcoin › 🛑 DỪNG GIAO DỊCH lần 20" in burst)
t.ok("...the rest counted as dropped and logged whole", sup.state()["bridge"]["dropped"] - dropped0 == 30 - len(burst)
     and buf.getvalue().count("bỏ thông báo vì quá dày: 📊 tín hiệu số") == 30 - len(burst), buf.getvalue()[-300:])
clock.t = first + ab.SUMMARY_EVERY - 1
bridge._summary()
t.ok("...no summary before a minute after the first dropped", sent_texts() == burst)
clock.t = first + ab.SUMMARY_EVERY
bridge._summary()
bridge._summary()
t.ok("...then one summary of them all: 'Bỏ qua N thông báo vì quá dày'", sent_texts()[len(burst):] == [
     f"🪙 Altcoin › Bỏ qua {30 - len(burst)} thông báo vì quá dày – xem /dulieu"], sent_texts()[len(burst):])
CALLS.clear()
stamps.clear()
clock.t += 600
for i in range(8):                       # all at the same moment
    bridge.handle(f"⚡ cùng lúc {i}")
t.ok("a burst at one moment: 5 sent, each at least 1 s after the one before",
     len(sent_texts()) == 5 and all(b - a >= 1.0 for a, b in zip(stamps, stamps[1:])), stamps)
deduped0 = sup.state()["bridge"]["deduped"]
clock.t += ab.BURST_SECONDS + 5
bridge.handle("⚡ cùng lúc 7")
t.ok("a dropped one sent again 25 s later: sent (not taken for a repeat)", sent_texts()[-1] == "🪙 Altcoin › ⚡ cùng lúc 7"
     and sup.state()["bridge"]["deduped"] == deduped0, sent_texts()[-1:])
bot.alt_broadcast = real_broadcast
logging.getLogger("vnbot.altcoin_bridge").removeHandler(handler)

# failures, switches
clock.t += 600
CALLS.clear()
FAIL["on"] = True
failed0 = sup.state()["bridge"]["send_failed"]
with contextlib.redirect_stderr(io.StringIO()):
    push("❗ không gửi được Telegram")
FAIL["on"] = False
t.ok("Telegram refuses (429 every try): send_failed + 1 per report chat",
     sup.state()["bridge"]["send_failed"] - failed0 == 2, sup.state()["bridge"])
fz.take = False
push("❗ Zoom không nhận")
fz.take = True
t.ok("Zoom does not take it (on_fail): send_failed + 1", sup.state()["bridge"]["send_failed"] - failed0 == 3)
switches.set("altcoin", False)
CALLS.clear()
fz.sent.clear()
muted0 = sup.state()["bridge"]["muted"]
push("🔕 công tắc tắt")
t.ok("the altcoin switch off: nothing sent, muted + 1", not CALLS and not fz.sent
     and sup.state()["bridge"]["muted"] == muted0 + 1)
bot._handle(m(111, 111, "/danhmuc"))
t.ok("...the stock's commands still answered", CALLS)
switches.set("altcoin", True)
switches.set("telegram", False)
CALLS.clear()
push("📨 chỉ Zoom")
t.ok("the telegram switch off: Zoom only", not texts(-100500) and fz.sent[-1] == "🪙 Altcoin › 📨 chỉ Zoom")
switches.set("telegram", True)
switches.set("zoom_notify", False)
fz.sent.clear()
push("📨 chỉ Telegram")
t.ok("the zoom_notify switch off: Telegram only", not fz.sent and texts(-100500) == ["🪙 Altcoin › 📨 chỉ Telegram"])
switches.set("zoom_notify", True)
try:
    switches.set("nope", True)
    unknown_ok = True
except ValueError:
    unknown_ok = False
t.ok("Switches: 'altcoin' is a name, an unknown one still refused", "altcoin" in __import__("vnbot.channels").channels.NAMES
     and not unknown_ok)
t.ok("the stock's own broadcast never depends on the altcoin switch", (switches.set("altcoin", False), CALLS.clear(),
     bot.broadcast("báo cáo cổ phiếu"), texts(-100500))[3] == ["báo cáo cổ phiếu"])
switches.set("altcoin", True)

# /alt zoomtest
CALLS.clear()
fz.sent.clear()
bot._handle(m(111, 111, "/alt zoomtest"))
t.ok("/alt zoomtest: one test message to each report chat and Zoom (sent at once), reported per channel",
     texts(-100500) == texts(-100600) == ["🪙 Altcoin › 🧪 Tin thử kênh chung (thông báo Altcoin qua bot Cổ phiếu)"]
     and fz.sent == texts(-100500) and "Zoom: nhận" in last(111) and "Telegram -100500: OK" in last(111)
     and "Telegram -100600: OK" in last(111), last(111))
fz.take = False
switches.set("telegram", False)
bot._handle(m(111, 111, "/alt zoomtest"))
t.ok("/alt zoomtest: 'Zoom: không nhận', the telegram switch named", "Zoom: không nhận" in last(111)
     and "Telegram: công tắc đang tắt" in last(111), last(111))
switches.set("telegram", True)
switches.set("altcoin", False)
bot._handle(m(111, 111, "/alt zoomtest"))
t.ok("/alt zoomtest with the altcoin switch off: nothing sent, said", "Công tắc Altcoin đang tắt" in last(111))
switches.set("altcoin", True)
fz.take = True

# before the stock is up: the last 50 kept, then sent in order (the real thread, real clock)
ab.SPACING = 0
ab.PER_MINUTE, ab.BURST = 1000, 1000
sup2 = supervisor()
live = ab.Bridge(sup2)
CALLS.clear()
for i in range(55):
    live.put(f"📝 sớm {i:02d}")
t.ok("before the stock is up: nothing sent, the oldest beyond 50 dropped and counted",
     wait_for(lambda: sup2.state()["bridge"]["dropped"] == 5) and not CALLS)
live.attach(bot)
t.ok("...then the 50 sent in order once attached", wait_for(lambda: len(texts(-100500)) == 50)
     and texts(-100500) == [f"🪙 Altcoin › 📝 sớm {i:02d}" for i in range(5, 55)])

# a blocked channel never blocks the pipe reader
gate = threading.Event()
blocked = type("Blocked", (), {"alt_on": lambda self: True,
                               "alt_broadcast": lambda self, text, on_fail=None: gate.wait(120)})()
sup3 = supervisor()
live3 = ab.Bridge(sup3)
live3.attach(blocked)
read_end, write_end = os.pipe()
reader = threading.Thread(target=sup3._read_hub, args=(read_end,), daemon=True)
started = time.monotonic()
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    reader.start()
    writer = threading.Thread(target=lambda: [os.write(write_end, (json.dumps(
        {"t": "notify", "n": i, "ts": 0, "text": f"🔔 số {i} " + "x" * 200}, ensure_ascii=False) + "\n").encode())
        for i in range(500)] + [os.write(write_end, json.dumps({"t": "state", "state": "running", "leader": True,
                                "mode": "paper", "version": "3.2.0", "build": "380dfca", "seam_ok": True}).encode()
                                + b"\n"), os.close(write_end)], daemon=True)
    writer.start()
    writer.join(20)
    reader.join(20)
took = time.monotonic() - started
st = sup3.state()
t.ok("the channel blocked while the child writes 500 frames: the reader is not (all read, pipe closed)",
     not writer.is_alive() and not reader.is_alive() and took < 15, took)
t.ok("...the overflow dropped and counted, the newest state frame still in state()",
     st["bridge"]["dropped"] >= 500 - ab.QUEUE_SIZE - 1 and st["bridge"]["notified"] == 500 and st["leader"] is True
     and st["build"] == "380dfca", st["bridge"])
gate.set()

# shutdown (the supervisor's stop(), the child gone): flush() sends what is queued, Zoom's queue included
import queue  # noqa: E402
import types  # noqa: E402


class SlowSink:
    """Telegram sends from the bridge's thread (0.1 s); Zoom has its own queue and thread (0.3 s)."""

    def __init__(self):
        self.sent, self.zoomed, self.zoom = [], [], types.SimpleNamespace(queue=queue.Queue())
        threading.Thread(target=self._zoom, daemon=True).start()

    def alt_on(self):
        return True

    def alt_broadcast(self, text, on_fail=None):
        time.sleep(0.1)
        self.sent.append(text)
        self.zoom.queue.put(text)
        return True

    def _zoom(self):
        while True:
            text = self.zoom.queue.get()
            time.sleep(0.3)
            self.zoomed.append(text)
            self.zoom.queue.task_done()


sink, sup4 = SlowSink(), supervisor()
live4 = ab.Bridge(sup4)
live4.attach(sink)
for i in range(3):
    live4.put(f"📉 ĐÓNG LỆNH {i}")
started = time.monotonic()
sup4.drain(10)
took = time.monotonic() - started
want = [f"🪙 Altcoin › 📉 ĐÓNG LỆNH {i}" for i in range(3)]
t.ok("drain (Bridge.flush, set on the supervisor): returns once the 3 queued are sent on Telegram and by Zoom's thread",
     sup4.drain == live4.flush and sink.sent == want and sink.zoomed == want and took < 5, (took, sink.zoomed))
gate2 = threading.Event()
stuck = type("Stuck", (), {"alt_on": lambda self: True,
                           "alt_broadcast": lambda self, text, on_fail=None: gate2.wait(120)})()
live5 = ab.Bridge(supervisor())
live5.attach(stuck)
live5.put("📉 kẹt")
started = time.monotonic()
live5.flush(0.5)
took = time.monotonic() - started
gate2.set()
t.ok("...a channel that hangs: flush gives up after its timeout", 0.45 <= took < 3, took)
off = ab.Bridge(supervisor(enabled=False))
off.attach(bot)                                  # its wake-up None stays queued: no thread when off
started = time.monotonic()
off.flush(5)
t.ok("...ALTCOIN_ENABLED off: flush returns at once (every shutdown of the stock alone)", time.monotonic() - started < 0.1)
t.ok("hub.json's bridge: every counter", set(st["bridge"]) == {"notified", "muted", "dropped", "deduped", "send_failed",
                                                               "last_notify_ts", "last_cmd_ts"} and SUP.state()["bridge"]["last_cmd_ts"])

# ── 8. the stock's /ai page: the altcoin switch, id="kenh" ──
AUTH = ("x", "s3cret-pass")
page = zc.get("/ai", auth=AUTH).text
t.ok("/ai: the channel card has id=\"kenh\" (/ai#kenh), the Altcoin row 'chưa cấu hình' while it is off",
     'id="kenh"' in page and "Altcoin · thông báo" in page and 'action="/channels/altcoin"' not in page)
os.environ["ALTCOIN_ENABLED"] = "1"
page = zc.get("/ai", auth=AUTH).text
r = zc.post("/channels/altcoin", auth=AUTH, data={"on": "0"}, headers={"origin": "http://testserver"},
            follow_redirects=False)
del os.environ["ALTCOIN_ENABLED"]
t.ok("/ai with ALTCOIN_ENABLED=1: the switch; POST /channels/altcoin turns it off -> /ai#kenh",
     'action="/channels/altcoin"' in page and r.status_code == 303 and r.headers["location"] == "/ai#kenh"
     and not sw.on("altcoin"))

server.shutdown()
t.done()
