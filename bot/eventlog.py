"""Ghi log hoạt động của bot vào DB (bảng events) để xem trực tiếp trên web và tải về.

Ghi qua hàng đợi + luồng nền, gom mỗi 2 giây → không làm chậm vòng lặp giao dịch,
không đệ quy khi chính DB báo lỗi.
"""
import logging
import queue
import threading
import time

SKIP_PREFIX = ("uvicorn", "storage", "eventlog", "urllib3", "ccxt", "asyncio", "httpx")


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
    h = DBLogHandler(store)
    logging.getLogger().addHandler(h)
    return h
