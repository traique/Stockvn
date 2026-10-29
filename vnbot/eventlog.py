"""The activity log in the history store (table ``events``), to read and download on the
page /dulieu, as the OKX bot does (altcoin_bot ``bot/eventlog.py``).

- Every record of INFO and up goes through a queue to one writer thread that saves a
  batch every 2 seconds: logging never waits on the database, and a database error
  (which is logged too) cannot loop back into itself.
- Secrets are masked in EVERY log line, the console's included, before any handler
  sees it: the OKX bot found its Telegram token in a "full backup" file because a
  network error printed the request URL. Masked: the values of the secret variables
  (tokens, keys, the web password, DATABASE_URL, the altcoin's ALT_* ones), the values
  added by ``add_secret_values`` (the altcoin's files, its child's tokens), a Telegram bot
  token, the password of a postgres URL, a Bearer token, an ``sk-`` key.
"""

from __future__ import annotations

import logging
import os
import queue
import re
import threading
import time

# Loggers whose lines are not stored: request noise, and the store's own (no loop).
SKIP = ("uvicorn", "vnbot.store", "vnbot.eventlog", "urllib3", "httpx", "httpcore", "asyncio", "openai",
        "langchain", "langgraph", "multipart", "fastapi", "starlette", "psycopg")
SECRET_KEYS = ("TELEGRAM_TOKEN", "ROUTER9_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY", "XAI_API_KEY",
               "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "AZURE_OPENAI_API_KEY",
               "ZOOM_CLIENT_SECRET", "ZOOM_SECRET_TOKEN", "ZOOM_VERIFICATION_TOKEN", "ZOOM_WEBHOOK_TOKEN",
               "WEB_PASSWORD", "DATABASE_URL",
               # the altcoin's (vnbot.altcoin_host), the ignored ones too: they may stay on the panel
               "ALT_DATABASE_URL", "ALT_OKX_API_KEY", "ALT_OKX_API_SECRET", "ALT_OKX_PASSPHRASE",
               "ALT_OPENROUTER_API_KEY", "ALT_NINEROUTER_API_KEY", "ALT_CUSTOM_API_KEY", "ALT_TELEGRAM_TOKEN",
               "ALT_ZOOM_CLIENT_SECRET", "ALT_ZOOM_SECRET_TOKEN", "ALT_ZOOM_BOT_SECRET_TOKEN", "ALT_ZOOM_WEBHOOK_TOKEN",
               "ALT_ZOOM_VERIFICATION_TOKEN", "ALT_DASHBOARD_PASS")
_PATTERNS = (
    re.compile(r"(api\.telegram\.org/(?:file/)?bot)[^/\s\"']+", re.I),
    re.compile(r"(?<![A-Za-z0-9])()\d{5,}:[A-Za-z0-9_-]{30,}"),            # a Telegram bot token anywhere
    re.compile(r"(postgres(?:ql)?://[^:/\s@]+:)[^@\s]+(@)", re.I),
    re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]{8,}", re.I),
    re.compile(r"(sk-[A-Za-z0-9_\-]{4})[A-Za-z0-9_\-]{8,}"),
)
_secrets: list[str] | None = None
_extra: dict[str, set[str]] = {}       # slot -> values: "" only grows, any other slot is replaced whole


def _with_password(value: str) -> set[str]:
    """The value, and the password of a database URL alone: a driver error may print it without the URL."""
    from urllib.parse import unquote, urlsplit

    values = {value}
    try:
        password = urlsplit(value).password if "://" in value else None
    except ValueError:
        password = None
    if password:
        values |= {password, unquote(password)}
    return values


def _secret_values() -> list[str]:
    values = set().union(*_extra.values())
    for key in SECRET_KEYS:
        values |= _with_password((os.environ.get(key) or "").strip())
    return sorted((v for v in values if len(v) >= 6), key=len, reverse=True)


def add_secret_values(values, slot: str = "") -> None:
    """Mask these values too from now on (the altcoin's bot.env, its child's tokens); not
    9Router's well-known default key "9router", a word of many log lines. A named ``slot``
    holds only its latest values (the running child's tokens, not those of every start)."""
    found: set[str] = set()
    for value in values:
        found |= _with_password((value or "").strip()) - {"9router"}
    _extra[slot] = found if slot else _extra.get("", set()) | found
    refresh_secrets()


def refresh_secrets() -> None:
    """Read the secret values again (they are read once, at the first line logged)."""
    global _secrets
    _secrets = _secret_values()


def redact(text) -> str:
    """``text`` with every known secret replaced by ***."""
    global _secrets
    text = str(text)
    if not text:
        return text
    if _secrets is None:
        _secrets = _secret_values()
    for value in _secrets:
        text = text.replace(value, "***")
    for rx in _PATTERNS:
        text = rx.sub(lambda m: m.group(1) + "***" + (m.group(2) if (m.lastindex or 0) >= 2 else ""), text)
    return text


class RedactFilter(logging.Filter):
    """Masks a record's message and traceback in place, before the handlers format it."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
            masked = redact(message)
            if masked != message:
                record.msg, record.args = masked, ()
        except Exception:  # noqa: BLE001 - a bad format string still gets logged
            pass
        if record.exc_info and not record.exc_text:
            try:
                record.exc_text = logging.Formatter().formatException(record.exc_info)
            except Exception:  # noqa: BLE001
                pass
        if record.exc_text:
            masked = redact(record.exc_text)
            if masked != record.exc_text:
                record.exc_text = masked
        return True


class StoreHandler(logging.Handler):
    """Queues INFO+ records; one thread writes them to the store in batches."""

    def __init__(self, store, level: int = logging.INFO, interval: float = 2.0):
        super().__init__(level)
        self.store = store
        self.interval = interval
        self.queue: queue.Queue = queue.Queue(maxsize=5000)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._writer, name="eventlog", daemon=True)
        self._thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith(SKIP):
            return
        try:
            message = record.getMessage()
            if record.exc_text and record.levelno >= logging.ERROR:
                message += "\n" + record.exc_text
            self.queue.put_nowait((record.created, record.levelname, record.name, redact(message)[:2000]))
        except Exception:  # noqa: BLE001 - a full queue drops the line, never blocks the bot
            pass

    def flush_now(self) -> int:
        """Write what is queued (tests, shutdown); returns how many lines."""
        batch = []
        while len(batch) < 500:
            try:
                batch.append(self.queue.get_nowait())
            except queue.Empty:
                break
        if batch:
            try:
                self.store.add_events(batch)
            except Exception:  # noqa: BLE001 - the database is down: those lines are lost, not the bot
                return 0
        return len(batch)

    def _writer(self) -> None:
        last_prune = time.monotonic()
        while not self._stop.is_set():
            time.sleep(self.interval)
            while self.flush_now():
                pass
            if time.monotonic() - last_prune > 3600:
                last_prune = time.monotonic()
                try:
                    self.store.db.run("DELETE FROM events WHERE ts < ?", (time.time() - _keep_seconds(),))
                except Exception:  # noqa: BLE001
                    pass

    def close(self) -> None:
        self._stop.set()
        self.flush_now()
        super().close()


def _keep_seconds() -> float:
    from vnbot.store import events_days

    return events_days() * 86400.0


_FILTER = RedactFilter()


def install_redaction() -> None:
    """Mask secrets in every line: a filter on the root logger's handlers (a logger's own
    filter only sees the lines logged on it, not the ones its children pass up)."""
    root = logging.getLogger()
    if _FILTER not in root.filters:
        root.addFilter(_FILTER)
    for handler in root.handlers:
        if _FILTER not in handler.filters:
            handler.addFilter(_FILTER)


def install(store) -> StoreHandler | None:
    """Redaction everywhere, and the store handler on the root logger (once); None without a store."""
    install_redaction()
    if store is None:
        return None
    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler, StoreHandler):
            return handler
    handler = StoreHandler(store)
    handler.addFilter(_FILTER)
    root.addHandler(handler)
    return handler
