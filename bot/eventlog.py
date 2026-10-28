"""Ghi log hoạt động của bot vào DB (bảng events) để xem trực tiếp trên web và tải về.

Ghi qua hàng đợi + luồng nền, gom mỗi 2 giây → không làm chậm vòng lặp giao dịch,
không đệ quy khi chính DB báo lỗi.
"""
import logging
import queue
import re
import threading
import time

SKIP_PREFIX = ("uvicorn", "storage", "eventlog", "urllib3", "ccxt", "asyncio", "httpx")

# v3.2: che bí mật trong MỌI dòng log (console + DB + file export). Lỗi mạng của requests in cả URL,
# vd. api.telegram.org/bot<token>/getUpdates → token Telegram từng lọt vào file "sao lưu toàn bộ".
_SECRET_RE = [re.compile(r"(api\.telegram\.org/bot)[^/\s\"']+", re.I),
              re.compile(r"(postgres(?:ql)?://[^:/\s]+:)[^@\s]+(@)", re.I),
              re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]{8,}", re.I),
              re.compile(r"(sk-[A-Za-z0-9_\-]{4})[A-Za-z0-9_\-]{8,}")]


def _secret_values():
    try:
        from .config import cfg
    except Exception:
        return []
    keys = ("TELEGRAM_TOKEN", "OPENROUTER_API_KEY", "NINEROUTER_API_KEY", "CUSTOM_API_KEY", "OKX_API_SECRET",
            "OKX_PASSPHRASE", "OKX_API_KEY", "ZOOM_CLIENT_SECRET", "ZOOM_SECRET_TOKEN", "ZOOM_WEBHOOK_TOKEN",
            "ZOOM_VERIFICATION_TOKEN", "DASHBOARD_PASS")
    out = []
    for k in keys:
        v = str(getattr(cfg, k, "") or "")
        if len(v) >= 6 and v not in ("9router",):
            out.append(v)
    return sorted(set(out), key=len, reverse=True)


_SECRETS = None


def redact(text):
    """Thay bí mật trong chuỗi bằng ***."""
    global _SECRETS
    if not text:
        return text
    if _SECRETS is None:
        _SECRETS = _secret_values()
    for v in _SECRETS:
        text = text.replace(v, "***")
    for rx in _SECRET_RE:
        text = rx.sub(lambda m: m.group(1) + "***" + (m.group(2) if m.lastindex and m.lastindex >= 2 else ""), text)
    return text


class RedactFilter(logging.Filter):
    """Gắn vào root logger: sửa record trước khi mọi handler (console, DB) ghi."""

    def filter(self, record):
        try:
            msg = record.getMessage()
            red = redact(msg)
            if red != msg:
                record.msg, record.args = red, ()
        except Exception:
            pass
        return True


class DBLogHandler(logging.Handler):
    def __init__(self, store, level=logging.INFO):
        super().__init__(level)
        self.store = store
        self.q = queue.Queue(maxsize=5000)
        threading.Thread(target=self._writer, daemon=True, name="eventlog").start()

    def emit(self, record):
        if record.name.startswith(SKIP_PREFIX):
            return
        try:
            msg = record.getMessage()
            if record.exc_info and record.levelno >= logging.ERROR:
                msg += "\n" + logging.Formatter().formatException(record.exc_info)
            self.q.put_nowait((record.created, record.levelname, record.name, msg[:2000]))
        except Exception:
            pass

    def _writer(self):
        last_prune = 0
        while True:
            batch = []
            try:
                batch.append(self.q.get(timeout=2))
                while len(batch) < 200:
                    batch.append(self.q.get_nowait())
            except queue.Empty:
                pass
            if batch:
                try:
                    self.store.add_events(batch)
                except Exception:
                    pass
            if time.time() - last_prune > 3600:
                last_prune = time.time()
                try:
                    self.store.prune()
                except Exception:
                    pass


def install(store):
    root = logging.getLogger()
    if not any(isinstance(f, RedactFilter) for f in root.filters):
        root.addFilter(RedactFilter())
        for hd in root.handlers:                 # filter của logger chỉ áp cho log gọi trực tiếp trên root → gắn cả handler
            hd.addFilter(RedactFilter())
    h = DBLogHandler(store)
    h.addFilter(RedactFilter())
    root.addHandler(h)
    return h
