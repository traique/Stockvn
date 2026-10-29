"""Altcoin inside Stockvn: the child process that ``vnbot/altcoin_host.py`` (the stock
process) starts and watches. Outside the altcoin fingerprint (``bot/**`` + ``run.py``):
``bot/`` stays byte for byte build 380dfca, everything the merge needs is done here.

    python -u altcoin/hosted.py --port N --parent-pid P --public-url URL --data-dir DIR \\
        --mode paper|live --instance NAME [--deny-db FP] [--allow-empty-db] [--check]

1. Dies with the stock process (PR_SET_PDEATHSIG, and a parent check every 5 s) and before
   it when memory runs out (oom_score_adj 800). HUB_FD, HUB_TOKEN and HUB_CMD_TOKEN leave
   the environment at once, and the pipe is not inherited by any process started here.
2. Reads only altcoin/.env and altcoin/bot.env (the working folder is this one, whatever
   the caller's), then checks the real timezone of the process: the strategy's session
   hours follow ``time.localtime()`` (tzdata's zone file as a fallback, else exit 78).
3. Seals the configuration after ``bot.config`` has read the files:
   - port, data folder, public URL and instance name come from argv;
   - the Telegram, Zoom, Render (PEER_URL, SELF_URL) and own-login (DASHBOARD_*) keys are
     always empty, whatever their source: the stock process owns the one bot and the one
     Zoom app, there is no Render, and the web login is the stock's WEB_PASSWORD;
   - the panel's DATABASE_URL and AI keys win over the files; Botkeep's masked values
     (``__BOTKEEP_KEEP_SECRET_<NAME>__``) are dropped.
4. The mode is the supervisor's alone: paper drops the OKX keys; live takes them from the
   panel and passes the two checks of ``bot.main.main()`` here (exit 78), never there.
5. Read only, before the bot opens its store: the stock's database (same URL, or its tables
   market/fills), an empty database (--allow-empty-db: paper only, and never in a data
   folder that has run on a database with data: <DATA_DIR>/hosted.funded) and open trades
   of the other mode (paper would close live trades on paper, live would manage paper
   trades as real ones) exit 78; another child on this host (flock on
   <DATA_DIR>/hosted.lock) or another instance alive in ``heartbeats`` exit 75 (the
   supervisor retries later).
6. ``bot.main.Telegram`` is HubChannel: every notification goes to the stock process,
   which sends it with its own Telegram/Zoom and switches. One thread ("hub-notify") writes
   JSON lines on HUB_FD: {"t": "state", ...} (newest first) and {"t": "notify", ...}.
   While the bot runs, it also reads ``heartbeats``: this instance name alive on another
   host (the old server started again: same name, same leader lock, both would trade)
   stops this child with exit 75.

Exit codes: 78 = configuration refused (not restarted), 75 = wait (retried).
"""

from __future__ import annotations

import argparse
import ctypes
import fcntl
import hashlib
import json
import logging
import os
import queue
import re
import signal
import sqlite3
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
EXIT_CONFIG, EXIT_WAIT = 78, 75
HUB_ENV = ("HUB_FD", "HUB_TOKEN", "HUB_CMD_TOKEN")
KEEP_SECRET = "__BOTKEEP_KEEP_SECRET_"
# Emptied in cfg (bot/config.py), and their environment names removed: 10 cfg.ZOOM_*, 11
# ZOOM_* names (ZOOM_BOT_SECRET_TOKEN is only an alias folded into cfg.ZOOM_SECRET_TOKEN).
CHANNEL_ATTRS = ("TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID", "ZOOM_WEBHOOK_URL", "ZOOM_WEBHOOK_TOKEN", "ZOOM_CLIENT_ID",
                 "ZOOM_CLIENT_SECRET", "ZOOM_BOT_JID", "ZOOM_SECRET_TOKEN", "ZOOM_VERIFICATION_TOKEN",
                 "ZOOM_ACCOUNT_ID", "ZOOM_TO_JID", "ZOOM_ALLOWED_USERS", "PEER_URL", "SELF_URL",
                 "DASHBOARD_USER", "DASHBOARD_PASS")
CHANNEL_ENV = CHANNEL_ATTRS + ("ZOOM_BOT_SECRET_TOKEN",)
PANEL_WINS = ("DATABASE_URL", "OPENROUTER_API_KEY", "NINEROUTER_API_KEY", "CUSTOM_API_KEY")
OKX_KEYS = ("OKX_API_KEY", "OKX_API_SECRET", "OKX_PASSPHRASE")
FRAME_LIMIT = 60 * 1024        # bytes of one JSON line on the pipe, newline included
STATE_EVERY = 5.0              # seconds between two looks at the bot's state
RIVAL_EVERY = 15.0             # seconds between two reads of heartbeats while the bot runs
FUNDED = "hosted.funded"       # in DATA_DIR: written once the bot has run on a database with data
OPEN_OTHER = {"paper": "DB có lệnh live đang mở – chạy paper sẽ đóng ảo chúng, lệnh thật trên OKX bị bỏ mặc: "
                       "bật lại live hoặc đóng lệnh trước",
              "live": "DB có lệnh paper đang mở – chạy live sẽ quản lý chúng như lệnh thật: đóng chúng ở paper trước"}
TOKENS: dict[str, str] = {}    # HUB_TOKEN / HUB_CMD_TOKEN, for the web proxy and the /alt commands
HUB: Hub | None = None
_LOCK = None                   # <DATA_DIR>/hosted.lock, held (flock) for the life of the process

log = logging.getLogger("hosted")


def parse(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Altcoin chạy trong Stockvn (tiến trình con của main_botkeep.py).")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--parent-pid", type=int, default=0)
    ap.add_argument("--public-url", default="")
    ap.add_argument("--data-dir", default=str(HERE / "data"))
    ap.add_argument("--mode", choices=("paper", "live"), default="paper")
    ap.add_argument("--instance", default="botkeep")
    ap.add_argument("--deny-db", default="", help="db_fingerprint() of the stock's DATABASE_URL")
    ap.add_argument("--allow-empty-db", action="store_true", help="ALTCOIN_FRESH_START=1: start on an empty database")
    ap.add_argument("--check", action="store_true", help="print the sealed configuration (JSON) and exit")
    return ap.parse_args(argv)


def db_fingerprint(url: str) -> str:
    """The same database whatever the password, the query (sslmode...) or the scheme's
    spelling: a hash of user@host:port/db, so the stock's URL never shows in argv (ps)."""
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


def follow_parent(parent_pid: int) -> None:
    """Die with the stock process: the kernel's SIGTERM when the thread that started this
    one ends, and a check every 5 s (that signal is lost if the parent died before prctl)."""
    try:
        ctypes.CDLL(None, use_errno=True).prctl(1, int(signal.SIGTERM))     # PR_SET_PDEATHSIG
    except (OSError, AttributeError):
        pass
    if os.getppid() != parent_pid:
        sys.exit(0)

    def watch() -> None:
        while os.getppid() == parent_pid:
            time.sleep(5)
        os.kill(os.getpid(), signal.SIGTERM)    # bot.main: KeyboardInterrupt, the leader lock released
        time.sleep(20)
        os._exit(0)

    threading.Thread(target=watch, name="parent-watch", daemon=True).start()


def lower_oom_priority() -> None:
    """The kernel kills this process before the stock one when the server runs out of memory."""
    path = Path("/proc/self/oom_score_adj")
    try:
        path.write_text("800")
        print(f"oom_score_adj={path.read_text().strip()}", flush=True)
    except OSError as exc:
        print(f"oom_score_adj: không đặt được ({exc.strerror})", flush=True)


def take_hub() -> int | None:
    """HUB_FD (the pipe to the stock process) and the two tokens, out of the environment at
    once: a process started from here (os.system, close_fds=False) gets none of them."""
    fd = os.environ.pop("HUB_FD", "")
    TOKENS.update(hub=os.environ.pop("HUB_TOKEN", ""), cmd=os.environ.pop("HUB_CMD_TOKEN", ""))
    try:
        fd = int(fd)
        os.set_inheritable(fd, False)
    except (ValueError, OSError):
        return None
    return fd


def fix_timezone(name: str) -> str | None:
    """'ok', 'tzdata' (the zone file of the tzdata package), or None: the offset of
    time.localtime() is not the one of TIMEZONE (no zoneinfo on the host)."""
    try:
        want = datetime.now(ZoneInfo(name)).utcoffset().total_seconds()
    except Exception:  # noqa: BLE001 - an unknown zone name
        return None
    if time.localtime().tm_gmtoff == want:
        return "ok"
    try:
        from importlib.resources import files

        path = files("tzdata").joinpath("zoneinfo", *name.split("/"))
        if not path.is_file():
            return None
        os.environ["TZ"] = f":{path}"
        time.tzset()
    except Exception:  # noqa: BLE001 - no tzdata package
        return None
    return "tzdata" if time.localtime().tm_gmtoff == want else None


def seal(cfg, args, panel: dict) -> tuple[list[str], list[str]]:
    """Step 3 of the docstring, on cfg and os.environ (bot.config has already run, file
    values winning). ``panel``: the environment this process got, before the files.
    Returns (ignored_keys, dropped_secrets): names only."""
    dropped = sorted(k for k, v in os.environ.items() if v.startswith(KEEP_SECRET))
    for key in dropped:
        value = panel.get(key, "")
        if value and not value.startswith(KEEP_SECRET):
            os.environ[key] = value
        else:
            del os.environ[key]
        if isinstance(getattr(cfg, key, None), str):
            setattr(cfg, key, os.environ.get(key, "").strip())
    if dropped:
        log.warning("Bỏ giá trị bị che (__BOTKEEP_KEEP_SECRET_…) của %s: nhập lại giá trị thật từ nguồn",
                    ", ".join(dropped))
    for key in PANEL_WINS:
        value = (panel.get(key) or "").strip()
        if value and os.environ.get(key, "").strip() != value:
            log.warning("%s: dùng giá trị trên panel (ALT_%s), bỏ giá trị trong altcoin/bot.env", key, key)
            os.environ[key] = value
            setattr(cfg, key, value)
    ignored = [k for k in CHANNEL_ENV if os.environ.pop(k, "").strip()]
    for attr in CHANNEL_ATTRS:
        setattr(cfg, attr, "")
    if ignored:
        log.warning("Bỏ qua %s trong altcoin/bot.env: Telegram và Zoom dùng chung của Cổ phiếu (lệnh /alt), "
                    "không còn Render, đăng nhập bằng WEB_PASSWORD", ", ".join(ignored))
    sealed = {"DATA_DIR": args.data_dir, "PUBLIC_URL": args.public_url, "INSTANCE_NAME": args.instance,
              "DASHBOARD_HOST": "127.0.0.1", "DASHBOARD_PORT": str(args.port)}
    overridden = [k for k in ("PORT", "SERVER_PORT", "APP_PORT") if os.environ.pop(k, "")]
    overridden += [k for k, v in sealed.items() if os.environ.get(k, v) != v]
    if overridden:
        log.warning("Bỏ qua %s: cổng, thư mục dữ liệu, URL và tên instance do Cổ phiếu đặt", ", ".join(overridden))
    os.environ.update(sealed)
    cfg.DATA_DIR, cfg.PUBLIC_URL, cfg.INSTANCE_NAME = args.data_dir, args.public_url, args.instance
    cfg.DASHBOARD_PORT = args.port
    return ignored, dropped


def set_mode(cfg, args, panel: dict) -> str | None:
    """cfg.MODE = --mode. Paper: no OKX key, no I_UNDERSTAND_THE_RISK. Live: the panel's
    keys only, then the two checks of bot.main.main(). Returns why live is refused."""
    asked = os.environ.get("MODE", "").strip().lower()     # only a file sets it: the supervisor passes no MODE
    if asked and asked != args.mode:
        log.error("MODE=%s trong altcoin/bot.env bị bỏ qua: chế độ do ALT_MODE trên panel quyết định (đang chạy %s)",
                  asked, args.mode)
    cfg.MODE = os.environ["MODE"] = args.mode
    if args.mode == "paper":
        for key in OKX_KEYS:
            os.environ.pop(key, None)
            setattr(cfg, key, "")
        os.environ.pop("I_UNDERSTAND_THE_RISK", None)
        return None
    for key in OKX_KEYS:
        os.environ[key] = (panel.get(key) or "").strip()
        setattr(cfg, key, os.environ[key])
    cfg.OKX_DEMO = (panel.get("OKX_DEMO") or "").strip().lower() in ("1", "true", "yes", "on")
    os.environ["I_UNDERSTAND_THE_RISK"] = "yes"
    if not cfg.OKX_API_KEY:
        return "MODE=live nhưng thiếu ALT_OKX_API_KEY"
    if os.getenv("I_UNDERSTAND_THE_RISK", "").lower() != "yes":
        return "MODE=live cần ALT_I_UNDERSTAND_THE_RISK=yes"
    if not (cfg.OKX_API_SECRET and cfg.OKX_PASSPHRASE):
        return "MODE=live nhưng thiếu ALT_OKX_API_SECRET hoặc ALT_OKX_PASSPHRASE"
    return None


def read_db(url: str, path: Path) -> dict | None:
    """What the guards need, read only: the tables, whether the bot has started on it
    (kv start_equity / paper_balance) and the heartbeats. None: no SQLite file yet."""
    def facts(tables, query, mark) -> dict:
        funded = "kv" in tables and bool(query(f"SELECT k FROM kv WHERE k IN ({mark}, {mark})",
                                               ("start_equity", "paper_balance")))
        beats = query("SELECT instance, ts, info FROM heartbeats") if "heartbeats" in tables else []
        opens = query("SELECT DISTINCT mode FROM trades WHERE status = 'open'") if "trades" in tables else []
        return {"tables": tables, "funded": funded, "heartbeats": [tuple(r) for r in beats],
                "open_modes": {r[0] for r in opens}}

    if url:
        import psycopg

        if "sslmode=" not in url and "host=/" not in url:     # as bot/storage.py connects
            url += ("&" if "?" in url else "?") + "sslmode=require"
        with psycopg.connect(url, autocommit=True, connect_timeout=20, prepare_threshold=None) as conn:
            def query(sql, params=()):
                return conn.execute(sql, params).fetchall()
            tables = {r[0] for r in query("SELECT table_name FROM information_schema.tables "
                                          "WHERE table_schema = 'public'")}
            return facts(tables, query, "%s")
    if not path.is_file():
        return None
    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        def query(sql, params=()):
            return conn.execute(sql, params).fetchall()
        return facts({r[0] for r in query("SELECT name FROM sqlite_master WHERE type = 'table'")}, query, "?")
    finally:
        conn.close()


def guard(cfg, args) -> tuple[int, str] | None:
    """Step 5 of the docstring: (exit code, reason), or None to start."""
    global _LOCK
    url = (cfg.DATABASE_URL or "").strip()
    if url and args.deny_db and db_fingerprint(url) == args.deny_db:
        return EXIT_CONFIG, ("DATABASE_URL của Altcoin là DB của Cổ phiếu: đặt ALT_DATABASE_URL riêng "
                             "(hoặc để trống: SQLite trong thư mục dữ liệu của Altcoin)")
    data = Path(cfg.DATA_DIR)
    facts = read_db(url, data / "bot.db")
    stock = sorted((facts or {}).get("tables", set()) & {"market", "fills"})
    if stock:
        return EXIT_CONFIG, f"DB của Altcoin có bảng {', '.join(stock)} của Cổ phiếu: đây là DB của Cổ phiếu"
    funded = bool(facts and facts["funded"])
    if not funded and (data / FUNDED).exists():
        return EXIT_CONFIG, ("DB rỗng, nhưng thư mục dữ liệu này đã chạy trên DB có dữ liệu: DB bị đổi (ALT_DATABASE_URL "
                             f"bị che, mất bot.db)? Sửa DB, hoặc xoá {FUNDED} nếu thật sự muốn bắt đầu mới")
    if not funded and not (args.allow_empty_db and args.mode == "paper"):
        return EXIT_CONFIG, "DB rỗng – chép DB cũ hoặc đặt ALTCOIN_FRESH_START=1" + (
            " (chỉ cho paper)" if args.allow_empty_db else "")
    if funded and args.allow_empty_db:
        log.warning("ALTCOIN_FRESH_START=1 không còn tác dụng (DB đã có dữ liệu): bỏ biến này trên panel")
    if facts and {m for m in facts["open_modes"] if m} - {args.mode}:
        return EXIT_CONFIG, OPEN_OTHER[args.mode]
    data.mkdir(parents=True, exist_ok=True)
    _LOCK = open(data / "hosted.lock", "a")     # noqa: SIM115 - held until the process ends
    try:
        fcntl.flock(_LOCK, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return EXIT_WAIT, "child khác trên cùng host còn chạy (hosted.lock)"
    host = os.uname().nodename
    for name, ts, info in (facts or {}).get("heartbeats", []):
        found = re.search(r"host=(\S+)", info or "")
        other = found.group(1) if found else "?"
        if time.time() - float(ts or 0) < cfg.LEADER_TTL and (name != args.instance or other != host):
            return EXIT_WAIT, f"instance khác đang chạy: {name}@{other} – dừng server altcoin cũ"
    return None


class Hub:
    """The one writer of HUB_FD: JSON lines, the newest state frame always before the
    notifications still waiting. Without HUB_FD it writes nothing (Notifier logs each one)."""

    def __init__(self, fd: int | None, base: dict, mark: Path | None = None, ttl: float = 90):
        self.fd, self.base = fd, base     # base: mode, version, build, instance, ignored_keys, dropped_secrets
        self.mark, self.ttl = mark, ttl   # <DATA_DIR>/hosted.funded, written once running; cfg.LEADER_TTL
        self.queue: queue.Queue = queue.Queue(maxsize=200)
        self.bot = None                   # set by HubChannel (built by Bot.__init__)
        self.seam_ok = None               # False: bot.main.Telegram is gone
        self.stop_reason = None           # set when a rival is seen: this child stops, main() returns 75
        self.n = self.dropped = self.errors = 0
        self.sent = None
        self._seam_logged = False
        self._next_look = 0.0

    def notify(self, text) -> None:
        # A lone surrogate (an AI reply cut in the middle of an emoji) cannot be encoded: "?" instead.
        text = str(text).encode("utf-8", "replace").decode("utf-8")
        try:
            self.queue.put_nowait((time.time(), text))
        except queue.Full:
            self.dropped += 1

    def rival(self) -> str | None:
        """'<name>@<host>' when this instance name is alive in heartbeats on another host;
        read every RIVAL_EVERY s once the bot is built. A database error is no rival."""
        bot = self.bot
        if getattr(bot, "start_equity", None) is None or time.monotonic() < self._next_look:
            return None
        self._next_look = time.monotonic() + RIVAL_EVERY
        name, host = self.base.get("instance"), os.uname().nodename
        try:
            rows = bot.store.instances()
        except Exception:  # noqa: BLE001 - the bot's own loop reports the database
            return None
        for row in rows:
            found = re.search(r"host=(\S+)", row.get("info") or "")
            if (row.get("instance") == name and found and found.group(1) != host
                    and time.time() - float(row.get("ts") or 0) < self.ttl):
                return f"{name}@{found.group(1)}"
        return None

    def state(self, state: str | None = None, reason: str | None = None) -> dict:
        bot = self.bot
        built = getattr(bot, "start_equity", None) is not None
        seam = self.seam_ok
        if seam is None and built:       # in effect, not only installed
            seam = (isinstance(getattr(bot, "tg", None), HubChannel)
                    and bot.tg in getattr(getattr(bot, "notify", None), "channels", ()))
            if not seam and not self._seam_logged:
                log.error("Seam kênh hỏng: bot.tg không phải HubChannel – thông báo Altcoin không tới Cổ phiếu")
            self._seam_logged = not seam
        state = state or ("waiting" if self.stop_reason else "running" if built else "starting")
        return {"t": "state", "state": state, "reason": reason or self.stop_reason,
                "leader": bool(getattr(bot, "is_leader", False)), **self.base, "seam_ok": seam}

    def write(self, frame: dict) -> None:
        if self.fd is None:
            return
        line = json.dumps(frame, ensure_ascii=False)
        over = len(line.encode()) + 1 - FRAME_LIMIT
        while over > 0 and frame.get("t") == "notify":     # cut the text by UTF-8 bytes, keep the line
            raw = frame["text"].encode()
            frame["text"] = raw[:max(0, len(raw) - over - 16)].decode(errors="ignore") + "…"
            line = json.dumps(frame, ensure_ascii=False)
            over = len(line.encode()) + 1 - FRAME_LIMIT
        data = (line + "\n").encode()
        try:
            while data:
                data = data[os.write(self.fd, data):]
        except OSError as exc:            # EPIPE, EBADF: the stock process is gone
            log.warning("Pipe tới Cổ phiếu đã đóng (%s): bỏ thông báo", exc.strerror)
            self.fd = None

    def run(self) -> None:
        item = None
        while True:
            try:
                rival = None if self.stop_reason else self.rival()
                if rival:
                    self.stop_reason = f"instance khác đang chạy: {rival} – dừng server altcoin cũ"
                    log.error("Instance %s cũng chạy trên host khác (cùng khoá leader, cả hai cùng giao dịch): "
                              "Altcoin ở đây dừng, chờ nó tắt", rival)
                frame = self.state()
                if frame != self.sent:
                    self.write(frame)
                    self.sent = frame
                if item:
                    self.n += 1
                    self.write({"t": "notify", "n": self.n, "ts": item[0], "text": item[1]})
                if rival:
                    os.kill(os.getpid(), signal.SIGTERM)    # bot.main: KeyboardInterrupt, the leader lock released
                if frame["state"] == "running" and self.mark is not None:
                    try:
                        self.mark.touch()
                    except OSError:
                        pass
                    self.mark = None
            except Exception:  # noqa: BLE001 - the one writer of HUB_FD: never ends
                self.errors += 1
                if self.errors == 1:
                    log.exception("hub-notify: lỗi, bỏ qua khung này")
            try:
                item = self.queue.get(timeout=STATE_EVERY)
            except queue.Empty:
                item = None


class HubChannel:
    """The bot's "Telegram" channel in Stockvn (bot.main.Telegram): hands every notification
    to the hub and returns. Never blocks, never raises, no switch_key (the old
    telegram_notify switch in the altcoin's kv no longer applies)."""

    enabled = True

    def __init__(self, bot):
        HUB.bot = bot

    def send(self, text) -> None:
        HUB.notify(text)

    def start(self) -> None:
        pass


def install_seams(bm) -> None:
    if hasattr(bm, "Telegram"):
        bm.Telegram = HubChannel
    else:
        HUB.seam_ok = False
        log.error("Seam bot.main.Telegram không còn: thông báo Altcoin không tới được Cổ phiếu")


def launch() -> None:
    import bot.main as bm

    install_seams(bm)
    try:
        bm.main()
    except KeyboardInterrupt:       # SIGTERM outside the loop's own try (the 10 s pause after an error)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        bot = HUB.bot
        if getattr(bot, "is_leader", False):
            bot.store.release_lock("trader", bot.instance)
        log.info("Bot dừng (instance %s).", getattr(bot, "instance", "-"))


def main(argv=None) -> int:
    global HUB
    args = parse(argv)
    args.data_dir = str(Path(args.data_dir).resolve())
    os.chdir(HERE)                      # bot/config.py also looks in the working folder: never the stock's .env
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    if not args.check:
        if args.parent_pid:
            follow_parent(args.parent_pid)
        lower_oom_priority()
    fd = take_hub()
    panel = dict(os.environ)
    from bot.config import ENV_FILE_INFO, cfg

    logging.basicConfig(level=getattr(logging, cfg.LOG_LEVEL, logging.INFO),
                        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout)])     # as bot.main does
    tz = fix_timezone(cfg.TIMEZONE)
    if tz == "tzdata":
        log.warning("Múi giờ %s lấy từ gói tzdata (máy không có zoneinfo)", cfg.TIMEZONE)
    ignored, dropped = seal(cfg, args, panel)
    refused = set_mode(cfg, args, panel)
    from bot import version as ver

    HUB = Hub(fd, {"mode": cfg.MODE, "version": ver.VERSION, "build": ver.build(), "instance": args.instance,
                   "ignored_keys": ignored, "dropped_secrets": dropped}, Path(cfg.DATA_DIR) / FUNDED, cfg.LEADER_TTL)
    if tz is None:
        refused = f"múi giờ {cfg.TIMEZONE}: giờ của máy lệch và không có gói tzdata"
    if args.check:
        print(json.dumps({
            "mode": cfg.MODE, "refused": refused, "instance": cfg.INSTANCE_NAME, "data_dir": cfg.DATA_DIR,
            "public_url": cfg.PUBLIC_URL, "dashboard": f"{os.environ['DASHBOARD_HOST']}:{cfg.DASHBOARD_PORT}",
            "database": "postgres" if cfg.DATABASE_URL else "sqlite", "timezone": cfg.TIMEZONE, "tz": tz,
            "utc_offset": time.localtime().tm_gmtoff, "env_files": ENV_FILE_INFO.get("path"),
            "emptied": {a: getattr(cfg, a) for a in CHANNEL_ATTRS},
            "emptied_env_left": [k for k in CHANNEL_ENV if k in os.environ], "okx_keys": bool(cfg.OKX_API_KEY),
            "ignored_keys": ignored, "dropped_secrets": dropped, "hub_fd": fd is not None,
        }, ensure_ascii=False, indent=1))
        return EXIT_CONFIG if refused else 0
    stop = (EXIT_CONFIG, refused) if refused else guard(cfg, args)
    if stop:
        code, reason = stop
        HUB.write(HUB.state("disabled" if code == EXIT_CONFIG else "waiting", reason))
        (log.error if code == EXIT_CONFIG else log.warning)("Altcoin không chạy: %s", reason)
        return code
    threading.Thread(target=HUB.run, name="hub-notify", daemon=True).start()
    launch()
    return EXIT_WAIT if HUB.stop_reason else 0


if __name__ == "__main__":
    sys.exit(main())
