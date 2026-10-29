"""Open the web port at once, before the app is ready (as the OKX bot does).

A host such as Botkeep reports "This application is not responding" while
nothing listens on the port it assigned, and may restart a process that is
only slow to import its libraries. ``LateApp`` is served from the first
second: ``/health`` answers 200 and the other pages say the bot is starting
(503 with ``Retry-After``, refreshed every few seconds), until the real app is
handed over with ``ready``. If the start fails, every page says so and points
to the log, and ``/health`` answers 500, instead of the port going quiet.

``health_extra`` (set by main_botkeep when the altcoin is on): more fields for every
/health answer, this page's and the app's; fast, never raising, never a secret.
"""

from __future__ import annotations

import html
import json
import logging

logger = logging.getLogger(__name__)
health_extra = None     # a callable returning a dict (vnbot.altcoin_host.Supervisor.health)


def health_fields() -> dict:
    """The extra /health fields; none when unset or failing (/health must always answer)."""
    try:
        return dict(health_extra()) if health_extra is not None else {}
    except Exception:  # noqa: BLE001
        return {}

_PAGE = """<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">{refresh}<title>{title} · TradingAgents VN</title>
<style>:root{{--paper:oklch(97% .006 55);--ink:oklch(21% .012 38);--muted:oklch(47% .013 45);--cotton:oklch(98.8% .0045 80);--shade:32% .03 50;color-scheme:light}}
@media (prefers-color-scheme:dark){{:root{{--paper:oklch(16% .01 55);--ink:oklch(94% .008 70);--muted:oklch(72% .012 55);--cotton:oklch(19% .011 55);color-scheme:dark}}}}
html,body{{overflow-x:clip}}
body{{margin:0;min-height:100svh;display:grid;place-items:center;background:var(--paper);color:var(--ink);
font:16px/1.6 "Be Vietnam Pro",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;padding:16px;box-sizing:border-box}}
.c{{background:var(--cotton);border-radius:8px;padding:40px 24px;max-width:440px;box-shadow:0 0 0 .5px oklch(var(--shade) / .22),6px 5px 10px -6px oklch(var(--shade) / .3)}}
.m{{display:block;font-weight:600;font-size:17px;margin:0 0 16px}}
h1{{font-weight:600;font-size:24px;line-height:1.2;letter-spacing:-.02em;margin:0 0 8px;text-wrap:balance}}p{{color:var(--muted);margin:0}}</style></head>
<body><div class="c"><div class="m" aria-hidden="true">LCTA</div><h1>{title}</h1><p>{text}</p></div></body></html>"""


class LateApp:
    """An ASGI app that answers by itself until ``ready(app)`` hands it the real one."""

    def __init__(self, retry_seconds: int = 5):
        self.app = None
        self.error = ""
        self.retry = retry_seconds

    def ready(self, app) -> None:
        self.app = app
        logger.info("Web: the app is ready")

    def failed(self, error: str) -> None:
        self.error = error or "lỗi chưa xác định"

    async def __call__(self, scope, receive, send):
        if self.app is not None:
            await self.app(scope, receive, send)
            return
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] != "http":
            return
        path = scope.get("path", "")
        if path.startswith("/static/"):
            from vnbot import ui  # light: no framework import

            name = path.rsplit("/", 1)[-1]
            if name in ui.STATIC_FILES:
                await _respond(send, 200, (ui.STATIC_DIR / name).read_bytes(), "image/png")
                return
        from vnbot.version import VERSION  # light: standard library only

        if path == "/health":
            status = 500 if self.error else 200
            body = json.dumps({"ok": not self.error, "booting": not self.error, "version": VERSION,
                               **({"error": self.error} if self.error else {}), **health_fields()}).encode()
            await _respond(send, status, body, "application/json")
            return
        if self.error:
            page = _PAGE.format(refresh="", title="Khởi động không thành công",
                                text=f"Chi tiết: {html.escape(self.error)}. Vui lòng xem nhật ký (log) trên Botkeep. "
                                     f"(Phiên bản {VERSION})")
            await _respond(send, 500, page.encode(), "text/html; charset=utf-8")
            return
        page = _PAGE.format(refresh=f'<meta http-equiv="refresh" content="{self.retry}">', title="Đang khởi động",
                            text=f"Hệ thống đang khởi động phiên bản {VERSION}, trang sẽ tự tải lại sau giây lát.")
        await _respond(send, 503, page.encode(), "text/html; charset=utf-8",
                       [(b"retry-after", str(self.retry).encode())])


async def _respond(send, status: int, body: bytes, content_type: str, extra=()) -> None:
    headers = [(b"content-type", content_type.encode()), (b"content-length", str(len(body)).encode()),
               (b"cache-control", b"no-store"), *extra]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})
