"""The altcoin bot (``altcoin/``, its ``bot/`` untouched) as a supervised child of the stock
process: one Botkeep server, one zip, one ``python main_botkeep.py``.

Off unless ALTCOIN_ENABLED=1: then nothing starts and nothing changes but one log line. On:

- Waits for the stock to be up (at most 60 s), then checks once: ccxt and httpx installed,
  a data folder of its own (ALT_DATA_DIR, default <DATA_DIR>/altcoin), and no altcoin
  table (trades, locks, learn_log) in the stock's database. A failed check leaves it off.
- Starts ``altcoin/hosted.py`` with an environment built from an allowlist: ALT_<NAME>
  becomes <NAME>; the stock's variables (TELEGRAM_TOKEN, ZOOM_*, WEB_PASSWORD,
  DATABASE_URL...) never pass. The ALT_ keys of what the altcoin no longer has (its own
  Telegram, Zoom, Render peer, login) are only reported (``ignored_keys``); Botkeep's
  masked values are dropped (``dropped_secrets``). With no key of its own, the altcoin
  shares the stock's OpenRouter and 9Router keys.
- Live only when ALT_MODE=live, ALT_I_UNDERSTAND_THE_RISK=yes, ALT_LIVE_ACK equal to the
  instance name and the three ALT_OKX_* keys are all set; else paper, with an error.
- A fresh localhost port each start; running once its /health answers (within 120 s,
  else stopped and started again on another port); watched every 30 s (4 misses:
  restarted). Exit 78: refused, stays off; 75: another instance is alive, tried again after
  LEADER_TTL + 10 s; anything else: started again after 20 s, doubling up to 10 minutes
  (back to 20 s after 15 minutes of good run).
- Its output is printed as "[altcoin] ..." with every known secret masked, and the last 200
  lines kept. Its state and its notifications come through a pipe (one JSON line each);
  the notifications are printed as "[altcoin-notify] ..." for now.
- ``stop()``: SIGTERM, 15 s to release the leader lock, SIGKILL. The child also dies with
  the stock process and is the first the kernel kills when memory runs out.

``state()`` is a snapshot replaced whole, read without a lock; /health shows its "state" only.
Standard library only (the stock's dotenv for the altcoin's files).
"""

from __future__ import annotations

import collections
import hashlib
import http.client
import importlib.util
import json
import logging
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
HOSTED = ROOT / "altcoin" / "hosted.py"
logger = logging.getLogger(__name__)

# Timings in seconds; module values, so the tests can shorten them.
WAIT_STOCK = 60                 # for the stock to be up before the first start
READY_TIMEOUT, READY_POLL = 120, 2
WATCH_EVERY, WATCH_TIMEOUT, WATCH_MISSES = 30, 10, 4
STOP_WAIT = 15                  # SIGTERM to SIGKILL: time to release the leader lock
BACKOFF_START, BACKOFF_MAX, HEALTHY_RESET = 20, 600, 900
WAITING_EXTRA = 10              # exit 75: tried again after LEADER_TTL + this
MAX_LINE = 64 * 1024            # a longer line on the pipe is dropped
LOG_LINES = 200

EXIT_CONFIG, EXIT_WAIT = 78, 75
SYSTEM_KEYS = {"PATH", "HOME", "LANG", "TZ", "TMPDIR", "PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE",
               "PYTHONNOUSERSITE", "VIRTUAL_ENV", "LD_LIBRARY_PATH", "HTTP_PROXY", "HTTPS_PROXY", "http_proxy",
               "https_proxy", "NO_PROXY", "no_proxy", "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "SSL_CERT_DIR"}
SUPERVISOR_KEYS = {"ALT_MODE", "ALT_I_UNDERSTAND_THE_RISK", "ALT_LIVE_ACK", "ALT_DATA_DIR", "ALT_PUBLIC_URL",
                   "ALT_INSTANCE_NAME"}      # read here only: the last three go sealed through argv
IGNORED = re.compile(r"ALT_(TELEGRAM_|ZOOM_|PEER_|DASHBOARD_)|ALT_(SELF_URL|KEEPALIVE_INTERVAL|SUPPORT_EMAIL)$")
FALLBACKS = {"OPENROUTER_API_KEY": "OPENROUTER_API_KEY", "NINEROUTER_BASE_URL": "ROUTER9_BASE_URL",
             "NINEROUTER_API_KEY": "ROUTER9_API_KEY"}      # the child's name: the stock's
OKX_KEYS = ("ALT_OKX_API_KEY", "ALT_OKX_API_SECRET", "ALT_OKX_PASSPHRASE")
KEEP_SECRET = "__BOTKEEP_KEEP_SECRET_"
SECRET_NAME = re.compile(r"TOKEN|SECRET|KEY|PASS|DATABASE_URL")
CHILD_FIELDS = ("leader", "mode", "version", "build", "seam_ok")


def _set_value(value) -> str:
    """A real value: not blank, not a <...> placeholder, not Botkeep's mask."""
    value = (value or "").strip()
    if (value.startswith("<") and value.endswith(">")) or value.startswith(KEEP_SECRET):
        return ""
    return value


def file_values(folder: Path = ROOT / "altcoin") -> dict[str, str]:
    """altcoin/.env, then altcoin/bot.env (it wins), as the child reads them."""
    from dotenv import dotenv_values

    values: dict[str, str] = {}
    for name in (".env", "bot.env"):
        try:
            values.update({k: v.strip() for k, v in dotenv_values(folder / name).items() if k and (v or "").strip()})
        except OSError:
            pass
    return values


def live_gate(environ, instance: str) -> tuple[str, str | None]:
    """('live', None) only when every condition holds in the stock's own environment;
    else ('paper', what is missing) - None when paper was asked."""
    asked = (environ.get("ALT_MODE") or "paper").strip().lower()
    if asked == "paper":
        return "paper", None
    if asked != "live":
        return "paper", f"ALT_MODE={asked} không hợp lệ (paper hoặc live): Altcoin chạy paper"
    missing = [] if (environ.get("ALT_I_UNDERSTAND_THE_RISK") or "").strip().lower() == "yes" \
        else ["ALT_I_UNDERSTAND_THE_RISK=yes"]
    if (environ.get("ALT_LIVE_ACK") or "").strip() != instance:
        missing.append(f"ALT_LIVE_ACK={instance}")
    missing += [k for k in OKX_KEYS if not _set_value(environ.get(k))]
    if missing:
        return "paper", f"ALT_MODE=live nhưng thiếu {', '.join(missing)}: Altcoin chạy paper"
    return "live", None


def child_env(environ, files: dict) -> tuple[dict[str, str], dict[str, list[str]]]:
    """The child's environment: an allowlist, never a copy of os.environ (/caidat puts the
    stock's tokens there), plus ALT_<NAME> as <NAME>. ``files``: file_values(), so a shared
    key never hides the altcoin's own. Returns (env, {ignored_keys, dropped_secrets, shared})."""
    env = {k: v for k, v in environ.items() if k in SYSTEM_KEYS or k.startswith("LC_")}
    info: dict[str, list[str]] = {"ignored_keys": [], "dropped_secrets": [], "shared": []}
    for key, value in sorted(environ.items()):
        if not key.startswith("ALT_") or key in SUPERVISOR_KEYS or not value.strip():
            continue
        if IGNORED.match(key):
            info["ignored_keys"].append(key)
        elif value.strip().startswith(KEEP_SECRET):
            info["dropped_secrets"].append(key)
        else:
            env[key[4:]] = value
    for name, stock in FALLBACKS.items():
        if name not in env and not _set_value(files.get(name)) and _set_value(environ.get(stock)):
            env[name] = environ[stock].strip()
            info["shared"].append(stock)
    env.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    return env, info


def db_fingerprint(url) -> str:
    """As altcoin/hosted.py: user@host:port/db hashed, the password and query left out."""
    url = (url or "").strip()
    if not url:
        return ""
    try:
        parts = urlsplit(url)
        key = (f"{unquote(parts.username or '')}@{(parts.hostname or '').lower()}:{parts.port or 5432}/"
               f"{unquote(parts.path.lstrip('/'))}")
    except ValueError:
        return ""
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _probe(port: int, timeout: float) -> bool:
    """The child's /health answers 200 (its web server is up)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("GET", "/health")
        return conn.getresponse().status == 200
    except (OSError, http.client.HTTPException):
        return False
    finally:
        conn.close()


def _rss_mb(pid) -> float | None:
    try:
        pages = int(Path(f"/proc/{pid}/statm").read_text().split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return round(pages * os.sysconf("SC_PAGE_SIZE") / 2**20, 1)


def _stock_altcoin_tables() -> list[str]:
    """Altcoin tables in the stock's own database: the two bots already share it."""
    from vnbot import store

    db = store.current()
    if db is None:
        return []
    sql = ("SELECT table_name AS name FROM information_schema.tables WHERE table_schema = 'public'" if db.pg
           else "SELECT name FROM sqlite_master WHERE type = 'table'")
    rows = store.safe("altcoin tables", lambda: db.db.run(sql, fetch=True), [])
    return sorted({r["name"] for r in rows} & {"trades", "locks", "learn_log"})


class Supervisor:
    def __init__(self, data_dir, public_url: str = "", environ=None, hosted: Path = HOSTED):
        self.environ = os.environ if environ is None else environ
        env = self.environ
        self.enabled = (env.get("ALTCOIN_ENABLED") or "").strip() == "1"
        self.stock_dir = Path(data_dir).resolve()
        self.data_dir = Path((env.get("ALT_DATA_DIR") or "").strip() or self.stock_dir / "altcoin").resolve()
        public_url = (public_url or "").rstrip("/")
        self.public_url = ((env.get("ALT_PUBLIC_URL") or "").strip().rstrip("/")
                           or (f"{public_url}/altcoin" if public_url else ""))
        self.instance = (env.get("ALT_INSTANCE_NAME") or "").strip() or "botkeep"
        self.mode, self.live_error = live_gate(env, self.instance)
        self.fresh = (env.get("ALTCOIN_FRESH_START") or "").strip() == "1"
        try:
            self.leader_ttl = int(env.get("ALT_LEADER_TTL") or 90)
        except ValueError:
            self.leader_ttl = 90
        self.hosted = Path(hosted)
        self.lines: collections.deque[str] = collections.deque(maxlen=LOG_LINES)
        self._lock = threading.RLock()          # spawn vs stop (stop may run in a signal handler)
        self._state_lock = threading.Lock()     # writers of the snapshot; readers take none
        self._stopping = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._readers: list[threading.Thread] = []
        self._tokens = ("", "")                 # HUB_TOKEN, HUB_CMD_TOKEN of the running child
        self._panel = {"ignored_keys": [], "dropped_secrets": []}
        self._child: dict = {}
        self._spawns = 0
        self._snap = {"state": "starting" if self.enabled else "off", "reason": None, "pid": None, "port": None,
                      "started": None, "restarts": 0, "last_exit": None, "version": None, "build": None,
                      "leader": False, "mode": self.mode, "live_error": self.live_error, "seam_ok": None,
                      "bridge": {"notified": 0, "dropped": 0, "last_notify_ts": None},
                      "dropped_secrets": [], "ignored_keys": []}

    # ── what the stock reads ──
    def state(self) -> dict:
        snap = self._snap
        return {**snap, "rss_mb": {"stock": _rss_mb(os.getpid()),
                                   "altcoin": _rss_mb(snap["pid"]) if snap["pid"] else None}}

    def health(self) -> dict:
        """The public /health gets the state alone: no mode, reason, leader or counters."""
        return {"altcoin": {"state": self._snap["state"]}}

    def _set(self, **fields) -> None:
        with self._state_lock:
            self._snap = {**self._snap, **fields}

    def _count(self, name: str, **extra) -> None:
        with self._state_lock:
            bridge = self._snap["bridge"]
            self._snap = {**self._snap, "bridge": {**bridge, name: bridge[name] + 1, **extra}}

    # ── start / stop ──
    def start(self, stock_ready: threading.Event | None = None) -> None:
        if not self.enabled:
            logger.info("Altcoin: tắt (ALTCOIN_ENABLED khác 1)")
            return
        threading.Thread(target=self._run, args=(stock_ready,), name="altcoin-supervisor", daemon=True).start()

    def stop(self) -> None:
        """SIGTERM to the child, STOP_WAIT seconds to release its lock, then SIGKILL; no new
        start after this. Safe twice, and from a signal handler."""
        with self._lock:
            self._stopping.set()
            proc = self._proc
        if proc is not None and proc.poll() is None:
            logger.info("Altcoin: dừng (pid %d)", proc.pid)
            self._kill(proc)
        if self.enabled:
            self._set(state="stopped", pid=None)

    def _wait(self, seconds: float) -> None:
        self._stopping.wait(seconds)

    def _kill(self, proc: subprocess.Popen) -> None:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(STOP_WAIT)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

    # ── the supervisor thread (long-lived: PR_SET_PDEATHSIG follows the thread that spawned) ──
    def _run(self, stock_ready) -> None:
        if stock_ready is not None:
            stock_ready.wait(WAIT_STOCK)
        why = self._precheck()
        if why:
            self._set(state="disabled", reason=why)
            logger.warning("Altcoin: tắt – %s", why)
            return
        if self.live_error:
            logger.error(self.live_error)
        if (self.environ.get("MODE") or "").strip():
            logger.warning("MODE trên panel không có tác dụng với Altcoin: chế độ là ALT_MODE")
        logger.info("Altcoin: bật – chế độ %s, instance %s, dữ liệu %s", self.mode, self.instance, self.data_dir)
        backoff = BACKOFF_START
        while backoff is not None and not self._stopping.is_set():
            try:
                backoff = self._once(backoff)
            except Exception:  # noqa: BLE001 - logged, then tried again
                logger.exception("Altcoin: lỗi supervisor")
                self._wait(BACKOFF_START)

    def _precheck(self) -> str | None:
        missing = [pkg for mod, pkg in (("ccxt", "ccxt==4.5.90"), ("httpx", "httpx>=0.27"))
                   if importlib.util.find_spec(mod) is None]
        if missing:
            return f"thiếu thư viện – cài: pip install --no-cache-dir {' '.join(missing)}"
        if not self.hosted.is_file():
            return f"thiếu {self.hosted.name} (gói upload thiếu file)"
        if self.data_dir == self.stock_dir:
            return "ALT_DATA_DIR trùng DATA_DIR của Cổ phiếu: để trống (mặc định <DATA_DIR>/altcoin)"
        shared = _stock_altcoin_tables()
        if shared:
            return f"DB đang dùng chung: DB của Cổ phiếu có bảng {', '.join(shared)} của Altcoin – tách DB trước"
        return None

    def _once(self, backoff: float) -> float | None:
        """One child, start to exit. Returns the next backoff, or None: stay off."""
        started = time.monotonic()
        proc, port = self._spawn()
        if proc is None:
            return None
        code = self._watch(proc, port)
        for reader in self._readers:        # the last frame (its reason) and lines before deciding
            reader.join(5)
        if self._stopping.is_set():
            return None
        if code is None:                    # not ready in time: again at once, on another port
            return backoff
        reason = self._child.get("reason")
        self._set(pid=None, last_exit=code)
        if code == EXIT_CONFIG:
            reason = reason or "cấu hình bị từ chối (mã 78)"
            self._set(state="disabled", reason=reason)
            logger.error("Altcoin: tắt – %s", reason)
            return None
        if code == EXIT_WAIT:
            reason, wait = reason or "instance khác đang chạy", self.leader_ttl + WAITING_EXTRA
            self._set(state="waiting", reason=reason)
            logger.warning("Altcoin chờ: %s – thử lại sau %d s", reason, wait)
            self._wait(wait)
            return backoff
        if time.monotonic() - started >= HEALTHY_RESET:
            backoff = BACKOFF_START
        self._set(state="backoff", reason=f"thoát mã {code}" + (" – bị kill (OOM?)" if code == -9 else ""))
        logger.warning("Altcoin thoát mã %s%s – chạy lại sau %d s", code, " – bị kill (OOM?)" if code == -9 else "",
                       backoff)
        self._wait(backoff)
        return min(backoff * 2, BACKOFF_MAX)

    def _spawn(self) -> tuple[subprocess.Popen | None, int]:
        from vnbot import eventlog

        port = _free_port()
        files = file_values(self.hosted.parent)
        env, info = child_env(self.environ, files)
        tokens = (secrets.token_urlsafe(32), secrets.token_urlsafe(32))
        eventlog.add_secret_values([*tokens, *(v for k, v in files.items() if SECRET_NAME.search(k))])
        cmd = [sys.executable, "-u", str(self.hosted), "--port", str(port), "--parent-pid", str(os.getpid()),
               "--public-url", self.public_url, "--data-dir", str(self.data_dir), "--mode", self.mode,
               "--instance", self.instance, "--deny-db", db_fingerprint(self.environ.get("DATABASE_URL"))]
        if self.fresh:
            cmd.append("--allow-empty-db")
        read_end, write_end = os.pipe()
        env.update(HUB_FD=str(write_end), HUB_TOKEN=tokens[0], HUB_CMD_TOKEN=tokens[1])
        try:
            with self._lock:
                if self._stopping.is_set():
                    os.close(read_end)
                    return None, port
                try:
                    proc = subprocess.Popen(cmd, cwd=str(self.hosted.parent), env=env, pass_fds=(write_end,),
                                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                            stderr=subprocess.STDOUT, close_fds=True)
                except OSError:
                    os.close(read_end)
                    raise
                self._proc = proc
        finally:
            os.close(write_end)             # the child holds the only copy: EOF when it dies
        self._tokens, self._child, self._spawns = tokens, {}, self._spawns + 1
        self._panel = {k: info[k] for k in ("ignored_keys", "dropped_secrets")}
        if self._spawns == 1:
            if info["ignored_keys"]:
                logger.warning("Altcoin: bỏ qua %s trên panel – Telegram và Zoom dùng chung của Cổ phiếu (lệnh /alt), "
                               "không còn Render, đăng nhập bằng WEB_PASSWORD", ", ".join(info["ignored_keys"]))
            if info["dropped_secrets"]:
                logger.warning("Altcoin: bỏ giá trị bị che (__BOTKEEP_KEEP_SECRET_…) của %s – nhập lại giá trị thật",
                               ", ".join(info["dropped_secrets"]))
            if info["shared"]:
                logger.info("Altcoin dùng %s của Cổ phiếu (chung hạn mức)", ", ".join(info["shared"]))
        self._set(state="starting", reason=None, pid=proc.pid, port=port, started=time.time(),
                  restarts=self._spawns - 1, leader=False, seam_ok=None, **self._panel)
        self._readers = [threading.Thread(target=self._read_log, args=(proc.stdout,), name="altcoin-log", daemon=True),
                         threading.Thread(target=self._read_hub, args=(read_end,), name="altcoin-hub", daemon=True)]
        for reader in self._readers:
            reader.start()
        logger.info("Altcoin: khởi động pid %d, cổng 127.0.0.1:%d (lần %d)", proc.pid, port, self._spawns)
        return proc, port

    def _watch(self, proc: subprocess.Popen, port: int) -> int | None:
        """The child's exit code; None when it was not ready in time (it is stopped)."""
        deadline = time.monotonic() + READY_TIMEOUT
        while not _probe(port, READY_POLL):
            if proc.poll() is not None:
                return proc.returncode
            if self._stopping.is_set():
                return None
            if time.monotonic() > deadline:
                logger.warning("Altcoin chưa sẵn sàng sau %d s: dừng, chạy lại trên cổng khác", READY_TIMEOUT)
                self._kill(proc)
                return None
            self._wait(READY_POLL)
        self._set(state="running", reason=None)
        logger.info("Altcoin: đang chạy (pid %d)", proc.pid)
        misses = 0
        while True:
            try:
                return proc.wait(WATCH_EVERY)
            except subprocess.TimeoutExpired:
                pass
            misses = 0 if _probe(port, WATCH_TIMEOUT) else misses + 1
            if misses >= WATCH_MISSES:
                logger.warning("Altcoin không trả lời /health %d lần liên tiếp: khởi động lại", misses)
                self._kill(proc)
                return proc.returncode

    # ── what the child says ──
    def _read_log(self, stream) -> None:
        """stdout + stderr of the child: printed (not logged: they are in the altcoin's own
        events), every known secret masked, the last lines kept."""
        from vnbot.eventlog import redact

        with stream:
            for raw in stream:
                line = redact(raw.decode("utf-8", "replace").rstrip("\r\n"))
                self.lines.append(line)
                print(f"[altcoin] {line}", flush=True)

    def _read_hub(self, fd: int) -> None:
        """The pipe, to its end (the child's exit): one JSON frame per line. Never waits on
        anything else, so the child never blocks on a full pipe."""
        buf, skip = b"", False
        with open(fd, "rb", buffering=0) as pipe:
            while chunk := pipe.read(65536):
                buf += chunk
                while (end := buf.find(b"\n")) >= 0:
                    line, buf = buf[:end], buf[end + 1:]
                    if skip:                 # the end of a line already dropped
                        skip = False
                    elif len(line) > MAX_LINE:
                        self._count("dropped")
                    else:
                        self._frame(line)
                if len(buf) > MAX_LINE:
                    if not skip:
                        self._count("dropped")
                    buf, skip = b"", True

    def _frame(self, line: bytes) -> None:
        try:
            frame = json.loads(line)
            kind = frame.get("t")
        except (ValueError, AttributeError):
            kind = None
        if kind == "state":
            self._child = {"reason": frame.get("reason")}
            fields = {k: frame.get(k) for k in CHILD_FIELDS}
            if fields["seam_ok"] is False and self._snap["seam_ok"] is not False:
                logger.error("Altcoin: seam kênh hỏng (seam_ok=false) – thông báo Altcoin không tới Cổ phiếu")
            if fields["leader"] and not self._snap["leader"]:
                logger.info("Altcoin: giữ quyền giao dịch – %s, bản %s (%s)", fields["mode"], fields["version"],
                            fields["build"])
            for key in ("ignored_keys", "dropped_secrets"):
                fields[key] = sorted(set(self._panel[key]) | set(frame.get(key) or []))
            self._set(**fields)
        elif kind == "notify":
            from vnbot.eventlog import redact

            print(f"[altcoin-notify] {redact(str(frame.get('text', ''))).replace(chr(10), ' | ')}", flush=True)
            self._count("notified", last_notify_ts=time.time())
        else:
            self._count("dropped")
