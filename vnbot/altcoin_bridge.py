"""The altcoin bot through the stock's one Telegram bot and one Zoom app (the child of
vnbot.altcoin_host never holds a channel token).

Commands, "/alt <lệnh>" (alias "/altcoin"), from a sender the stock already allows:

- only the commands of the allowlist reach the child: POST http://127.0.0.1:<port>/_hub/command
  with X-Hub-Cmd-Token (httpx, no proxy from the environment), 15 s; "/alt" alone, "help" or an
  unknown word get the altcoin's command table, built here;
- close, apply and resume need a second message, "/alt <lệnh> xacnhan", from the same sender
  in the same chat within 120 s, used once; only the command's name is sent;
- close (60 s) and apply (30 s) run on an "alt-cmd" thread, one at a time; a write command is
  never sent again after an error or a timeout (the child holds a lock until it is done, so a
  second close is refused there, not run twice);
- the child off, starting, waiting, not the leader, busy, slow or failing: a plain answer
  (ALTCOIN_ENABLED not 1: every /alt, zoomtest and close included, gets "Altcoin đang tắt").

Notifications: the supervisor's pipe reader hands each one to ``put`` (never blocks: a queue of
200, a full queue drops and counts). The "altcoin-notify" thread masks secrets, drops the
leader takeover notice (one instance only) and keeps one start notice per 30 minutes, turns a
bare /resume, /apply... into "/alt ...", sends the same text once per 120 s, at most 15 per
minute and 5 per 20 s, 1 s apart (🛑/⚠️ ones pass the limit; what is dropped is logged whole,
with a summary a minute after the first one), then sends "🪙 Altcoin › <text>" where the
stock's own notifications go (TelegramBot.alt_broadcast: its switches, plus the "altcoin"
switch). A long notification or answer goes as parts that each carry the header, "(2/3)",
short enough that no channel cuts them again. Before the stock is up, the last 50 wait.
Counters: the supervisor's ``bridge`` (/altcoin/hub.json).
"""

from __future__ import annotations

import collections
import logging
import queue
import re
import threading
import time

from vnbot.eventlog import redact
from vnbot.zoom import split

logger = logging.getLogger(__name__)

PREFIX = "🪙 Altcoin › "
READ = ("status", "trades", "signals", "ai", "learn", "lessons", "btc", "phienban", "version")
WRITE = ("pause", "resume", "close", "apply")
CONFIRM = ("resume", "close", "apply")
YES = ("xacnhan", "xácnhận")
SLOW = {"close": 60, "apply": 30}           # seconds; on the alt-cmd thread
TIMEOUT, CONNECT_TIMEOUT = 15, 2
CONFIRM_SECONDS = 120
# The altcoin's commands typed without /alt: never sent to it (a bare /close must not close
# anything), answered with the hint. None of them is a stock command.
BARE = ("/status", "/pause", "/resume", "/trades", "/close", "/signals", "/ai", "/learn", "/apply", "/lessons",
        "/btc", "/zoomtest")
# Rate of the notifications; module values, so the tests can shorten them.
QUEUE_SIZE, EARLY_KEPT = 200, 50
DEDUPE_SECONDS, START_EVERY = 120, 1800
PER_MINUTE, BURST, BURST_SECONDS, SPACING = 15, 5, 20, 1.0
SUMMARY_EVERY = 60
PART = 3500              # characters of one part, under every channel's own cut (Telegram 3,900, Zoom 3,800)

HELP = ("🪙 Altcoin (bot OKX) – gõ /alt <lệnh> (Zoom: alt <lệnh>):\n"
        "/alt status – vốn, lệnh mở, quét gần nhất\n"
        "/alt trades – 10 lệnh gần nhất\n"
        "/alt signals – tín hiệu và quyết định AI\n"
        "/alt ai – lượt gọi từng model\n"
        "/alt learn – trạng thái tự học · /alt lessons – bài học AI\n"
        "/alt btc – bối cảnh BTC\n"
        "/alt phienban – phiên bản Altcoin\n"
        "/alt pause – tạm dừng mở lệnh mới\n"
        "/alt resume – giao dịch lại, xoá trạng thái dừng (cần xác nhận)\n"
        "/alt apply – áp dụng mọi đề xuất tự học đang chờ (cần xác nhận)\n"
        "/alt close – đóng mọi lệnh đang mở (cần xác nhận)\n"
        "/alt zoomtest – gửi thử một thông báo Altcoin qua các kênh\n"
        "Ghép đôi Zoom dùng chung với Cổ phiếu: /zoompair, /zoomxoa, /zoomstatus.")
ASK = {"close": "Lệnh này đóng mọi vị thế Altcoin đang mở (lệnh thật khi chạy live).",
       "apply": "Lệnh này áp dụng mọi đề xuất tự học đang chờ của Altcoin.",
       "resume": "Lệnh này cho Altcoin giao dịch lại và xoá trạng thái dừng của bộ ngắt rủi ro."}
ZOOM_SHARED = "Zoom dùng chung với Cổ phiếu: dùng /zoompair, /zoomxoa, /zoomstatus."
STARTING = "🪙 Altcoin đang khởi động, thử lại sau ít giây."
NOT_LEADER = "🪙 Altcoin vừa khởi động, chưa nhận quyền giao dịch. Thử lại sau khoảng 1 phút."
BUSY = "⏳ Đang có lệnh Altcoin chạy (lệnh trước chưa xong). Đừng gửi lại."
_HINT = re.compile(r"(?<![\w/.:-])/(resume|apply|pause|close|status)(?![\w/])")


def rewrite(text: str) -> str:
    """The altcoin's own command hints ("gõ /resume", "/apply") as the stock's: "/alt resume"."""
    return _HINT.sub(r"/alt \1", text)


def parts(text: str) -> list[str]:
    """A message that starts with PREFIX, as messages that each start with it ("(2/3)" after
    it when there are several); any other message as it is."""
    if not text.startswith(PREFIX) or len(text) <= PART:
        return [text]
    pieces = split(text[len(PREFIX):], PART)
    return [f"{PREFIX}({i}/{len(pieces)}) {piece.lstrip()}" for i, piece in enumerate(pieces, 1)]


class Bridge:
    def __init__(self, supervisor):
        self.sup = supervisor
        self._pending: dict[tuple, float] = {}       # (channel, sender, chat, command) -> asked at
        self._lock = threading.Lock()
        self._busy = threading.Lock()                # the alt-cmd thread: one close/apply at a time
        self._queue: queue.Queue = queue.Queue(maxsize=QUEUE_SIZE)
        self._early: collections.deque[str] = collections.deque()
        self._sink = None                            # vnbot.telegram_bot.TelegramBot, once the stock is up
        self._seen: dict[str, float] = {}            # text -> sent at (dedupe)
        self._sent: collections.deque[float] = collections.deque()
        self._last = self._started = self._summary_at = -1e9
        self._skipped = 0
        self._now, self._sleep = time.monotonic, time.sleep      # the tests' clock
        supervisor.on_notify = self.put
        if supervisor.enabled:
            threading.Thread(target=self._loop, name="altcoin-notify", daemon=True).start()

    @property
    def enabled(self) -> bool:
        return self.sup.enabled

    # ── commands ──
    def command(self, args: str, key: tuple, source: str, reply) -> None:
        """'/alt <args>' from an allowed sender. ``key``: (channel, real sender, chat) of the
        two-step commands; ``source``: "Telegram" or "Zoom" (the altcoin says "qua Zoom");
        ``reply(text)`` answers in that chat."""
        if not self.enabled:
            reply(self._not_running(self.sup.state()))
            return
        words = args.lower().split()
        cmd = words[0] if words else ""
        if cmd in ("zoompair", "zoomxoa", "zoomstatus"):
            reply(ZOOM_SHARED)
            return
        if cmd not in READ + WRITE:
            reply(HELP)
            return
        if cmd in CONFIRM:
            asked = self._confirm(key, cmd, len(words) > 1 and words[1] in YES)
            if asked:
                reply(asked)
                return
        if cmd not in SLOW:
            for part in parts(self.call(cmd, source, key)):
                reply(part)
            return
        if not self._busy.acquire(blocking=False):
            reply(BUSY)
            return
        reply("⏳ Đang gửi lệnh tới Altcoin…")

        def work() -> None:
            try:
                for part in parts(self.call(cmd, source, key)):
                    reply(part)
            except Exception:  # noqa: BLE001 - logged; the lock is released below
                logger.exception("Altcoin: lệnh /alt %s lỗi", cmd)
            finally:
                self._busy.release()

        try:
            threading.Thread(target=work, name="alt-cmd", daemon=True).start()
        except Exception:
            self._busy.release()
            raise

    def _confirm(self, key: tuple, cmd: str, yes: bool) -> str | None:
        """None: go. Otherwise what to answer: the question (first step), or why not."""
        if not key[1]:
            return "⚠️ Không xác định được người gửi, không chạy lệnh cần xác nhận."
        now = self._now()
        with self._lock:
            self._pending = {k: t for k, t in self._pending.items() if now - t < CONFIRM_SECONDS}
            if not yes:
                self._pending[(*key, cmd)] = now
                return (f"⚠️ {ASK[cmd]} Gõ /alt {cmd} xacnhan trong {CONFIRM_SECONDS} giây để thực hiện.")
            if self._pending.pop((*key, cmd), None) is None:
                return (f"Không có yêu cầu /alt {cmd} nào của bạn ở cuộc trò chuyện này đang chờ xác nhận "
                        f"(hoặc đã quá {CONFIRM_SECONDS} giây). Gõ /alt {cmd} để bắt đầu lại.")
        return None

    def call(self, cmd: str, source: str, key: tuple = ("", "", "")) -> str:
        """The child's answer to one command, as the reply to send (masked, hints rewritten)."""
        target = self.sup.command_target()
        if target is None:
            return redact(self._not_running(self.sup.state()))
        import httpx

        port, token = target
        timeout = SLOW.get(cmd, TIMEOUT)
        self.sup.count(last_cmd_ts=time.time())
        started = time.monotonic()
        outcome = "ok"
        try:
            r = httpx.post(f"http://127.0.0.1:{port}/_hub/command", json={"cmd": cmd, "source": source},
                           headers={"X-Hub-Cmd-Token": token}, trust_env=False,
                           timeout=httpx.Timeout(timeout, connect=CONNECT_TIMEOUT))
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if r.status_code == 200 and body.get("ok"):
                text = f"{PREFIX}{cmd}\n{rewrite(str(body.get('reply', '')))}"
            elif r.status_code == 409 and body.get("busy"):
                text, outcome = BUSY, "busy"
            elif r.status_code == 409:
                text, outcome = NOT_LEADER, "not leader"
            elif r.status_code == 503:
                text, outcome = STARTING, "starting"
            else:
                outcome = f"HTTP {r.status_code}"
                text = f"⚠️ Altcoin lỗi: {body.get('error') or outcome}"
        except (httpx.ConnectError, httpx.ConnectTimeout):
            text, outcome = STARTING, "not reachable"
        except httpx.TimeoutException:
            outcome = "timeout"
            text = f"⏱ Altcoin chưa trả lời sau {timeout} giây, thử lại sau."
            if cmd in WRITE:
                text = (f"⏱ Altcoin chưa trả lời sau {timeout} giây. Lệnh vẫn đang chạy trong Altcoin (với close, kết "
                        "quả từng lệnh đóng sẽ tới qua thông báo). Đừng gửi lại: lệnh ghi mới sẽ bị từ chối cho tới "
                        "khi lệnh này xong.")
        except (httpx.HTTPError, ValueError) as exc:
            outcome = type(exc).__name__
            text = f"⚠️ Altcoin lỗi: {exc}"
        logger.info("Altcoin: /alt %s từ %s %s: %s (%.1f s)", cmd, key[0], key[1], outcome, time.monotonic() - started)
        return redact(text)

    @staticmethod
    def _not_running(snap: dict) -> str:
        state, reason = snap.get("state"), snap.get("reason")
        if state == "off":
            return "🪙 Altcoin đang tắt (ALTCOIN_ENABLED khác 1)."
        if state in ("disabled", "stopped"):
            return f"🪙 Altcoin đang tắt ({reason or 'đã dừng'})."
        if state == "waiting":
            return f"🪙 Altcoin đang chờ: {reason or 'instance khác còn chạy'}. Altcoin tự chạy khi instance đó dừng."
        return STARTING

    # ── notifications ──
    def put(self, text: str) -> None:
        """A notification from the child (the supervisor's pipe reader): queued, never waits."""
        try:
            self._queue.put_nowait(text)
        except queue.Full:
            self.sup.count("dropped")
            logger.warning("Altcoin: bỏ thông báo (hàng đợi đầy): %s", redact(text))

    def attach(self, sink) -> None:
        """The stock is up: notifications go to ``sink`` (TelegramBot: alt_on(), alt_broadcast())."""
        self._sink = sink
        try:
            self._queue.put_nowait(None)         # wake the thread: the ones waiting go now
        except queue.Full:
            pass

    def _loop(self) -> None:
        while True:
            try:
                text = self._queue.get(timeout=5)
            except queue.Empty:
                text = None
            try:
                if self._sink is None:
                    if text is not None:
                        self._hold(text)
                    continue
                while self._early:
                    self.handle(self._early.popleft())
                if text is not None:
                    self.handle(text)
                self._summary()
            except Exception:  # noqa: BLE001 - one bad notification must not stop the thread
                logger.exception("Altcoin: gửi thông báo lỗi")

    def _hold(self, text: str) -> None:
        if len(self._early) >= EARLY_KEPT:
            self.sup.count("dropped")
            logger.warning("Altcoin: bỏ thông báo (Cổ phiếu chưa sẵn sàng): %s", redact(self._early.popleft()))
        self._early.append(text)

    def handle(self, text: str) -> None:
        """One notification, in the order of the module's docstring."""
        text = redact(text).strip()
        now = self._now()
        if text.startswith("👑") and "tiếp quản" in text:
            logger.info("Altcoin: %s", text)
            return
        if text.startswith("🚀") and "khởi động" in text:
            if now - self._started < START_EVERY:
                logger.info("Altcoin: %s", text)
                return
            self._started = now
        text = rewrite(text)
        if not self._sink.alt_on():
            self.sup.count("muted")
            return
        self._seen = {k: t for k, t in self._seen.items() if now - t < DEDUPE_SECONDS}
        if text in self._seen:
            self.sup.count("deduped")
            return
        while self._sent and now - self._sent[0] >= 60:
            self._sent.popleft()
        if not text.startswith(("🛑", "⚠️")) and (
                len(self._sent) >= PER_MINUTE or sum(now - t < BURST_SECONDS for t in self._sent) >= BURST):
            if not self._skipped:
                self._summary_at = now           # the summary: a minute after the first one dropped
            self._skipped += 1
            self.sup.count("dropped")
            logger.warning("Altcoin: bỏ thông báo vì quá dày: %s", text)
            return
        self._seen[text] = now                   # only once sent: a dropped one may come again
        self._send(text)

    def _summary(self) -> None:
        if self._skipped and self._now() - self._summary_at >= SUMMARY_EVERY:
            n, self._skipped = self._skipped, 0
            if self._sink.alt_on():
                self._send(f"Bỏ qua {n} thông báo vì quá dày – xem /dulieu")

    def _send(self, text: str) -> None:
        wait = self._last + SPACING - self._now()
        if wait > 0:
            self._sleep(wait)
        self._last = self._now()
        self._sent.append(self._last)

        def failed() -> None:
            self.sup.count("send_failed")
            logger.warning("Altcoin: một kênh không nhận thông báo: %s", text)

        for part in parts(PREFIX + text):
            if self._sink.alt_broadcast(part, on_fail=failed) is False:
                self.sup.count("muted")
                return
