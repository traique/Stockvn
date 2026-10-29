"""The Hub: the server's one public port, in front of the stock's app (vnbot.boot.LateApp).
Under /altcoin it serves the altcoin's pages (the child of vnbot.altcoin_host, on a localhost
port); every other path, and the lifespan, goes to the stock as before. Installed by
main_botkeep only when ALTCOIN_ENABLED=1.

Under /altcoin, in this order:

1. The raw path, before it is decoded: a dot segment, "//", a backslash, a byte that is not
   ASCII, a stray "%", or an escape of "/", "\\" or of a character that never needs one (%2E,
   %5F...) is a 400. httpx would resolve "/altcoin/health/../api/export/all.json" to another
   page than the one checked below; "/altcoin/%5Fhub/command" would slip past a check of "_hub".
2. Exact matches on the decoded path, before the password: GET /altcoin -> 308 /altcoin/;
   /altcoin/_hub(/...) (the child's command endpoint of the /alt bridge) and the altcoin's
   own Zoom and channel routes -> 404, never to the child; GET of its Zoom and public pages
   -> 308 to the stock's (one Zoom app: the stock's).
3. Every other path: the stock's WEB_PASSWORD with the same lockout counter (vnbot.webguard),
   and the same-origin check for every method but GET/HEAD.
4. /altcoin/hub.json: the supervisor's whole state (no token), answered here even while the
   child or the stock's start is down.
5. The rest goes to the child: a body up to 64 KB, at most 8 requests at a time; the client's
   Authorization, hop-by-hop, X-Forwarded-* and X-Hub-* headers dropped; X-Forwarded-For/
   -Host/-Proto/-Prefix and the child's X-Hub-Token set (never X-Hub-Cmd-Token); a Location
   it sends back made to point under /altcoin/. While the child is not running: a 503 page
   saying why, with its last log lines (/altcoin/health: 503 JSON).

Standard library only when imported (the port opens in the first second); httpx on the first
request that goes to the child.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import string
from urllib.parse import urlsplit, urlunsplit

from vnbot import webguard

logger = logging.getLogger(__name__)

PREFIX = "/altcoin"
MAX_BODY = 64 * 1024
MAX_ACTIVE = 8
CONNECT_TIMEOUT, READ_TIMEOUT = 2.0, 120.0
RETRY = 5
LOG_LINES = 50                  # of the child's output on the 503 page
# 404 before the password, never to the child (decoded path, one trailing "/" ignored)
BLOCKED = {f"{PREFIX}/_hub", f"{PREFIX}/webhook/zoom", f"{PREFIX}/zoom/bot", f"{PREFIX}/zoom/install",
           f"{PREFIX}/zoom/oauth/callback", f"{PREFIX}/zoom/pair", f"{PREFIX}/zoom/unpair", f"{PREFIX}/caidat/zoomtest"}
BLOCKED_UNDER = (f"{PREFIX}/_hub/", f"{PREFIX}/caidat/kenh/")
TO_STOCK = {f"{PREFIX}/{name}" for name in ("zoom", "docs", "privacy", "terms", "support", "data-deletion")}
NEVER_ESCAPED = set(string.ascii_letters + string.digits + "-._~/\\")
_ESCAPE = re.compile(r"%([0-9A-Fa-f]{2})")
HOP = {"connection", "keep-alive", "proxy-connection", "te", "trailer", "transfer-encoding", "upgrade"}
DROP = HOP | {"authorization", "proxy-authorization", "host", "content-length", "expect", "forwarded"}
REPLY_DROP = {name.encode() for name in HOP | {"date", "server"}}     # this server sets its own Date and Server
SECURITY = ((b"x-frame-options", b"DENY"), (b"x-content-type-options", b"nosniff"),
            (b"referrer-policy", b"same-origin"))
NOT_FOUND = "Không tìm thấy trang."

_PAGE = """<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">{refresh}<title>{title} · Altcoin</title>
<style>:root{{color-scheme:light dark}}body{{margin:0;padding:24px 16px;font:16px/1.6 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}}
main{{max-width:720px;margin:0 auto}}h1{{font-size:24px;line-height:1.2;margin:0 0 8px}}p{{margin:0 0 12px}}
pre{{font-size:12px;white-space:pre-wrap;word-break:break-word;opacity:.8}}</style></head>
<body><main><h1>{title}</h1><p>{text}</p>{more}<p><a href="/">Cổ phiếu</a> · <a href="{prefix}/">Altcoin</a></p></main></body></html>"""


def raw_path_ok(raw: bytes) -> bool:
    """Step 1 of the docstring, on the path as it came (without its query)."""
    text = raw.decode("latin-1")
    if not text.isascii() or "\\" in text or "//" in text or any(s in (".", "..") for s in text.split("/")):
        return False
    escapes = _ESCAPE.findall(text)
    return text.count("%") == len(escapes) and not any(chr(int(x, 16)) in NEVER_ESCAPED for x in escapes)


def rewrite_location(value: str, port: int, hosts: set[str]) -> str:
    """A Location from the child, made to point under /altcoin/ of this site: an absolute URL
    of any scheme to the child's own address or to this site, and a bare "/x", become
    "/altcoin/x"; a relative one ("caidat", "./") already resolves under /altcoin/."""
    try:
        parts = urlsplit(value)
    except ValueError:                  # a broken URL: the section's start instead
        return PREFIX + "/"
    if parts.netloc:
        if parts.netloc.lower() not in hosts | {f"127.0.0.1:{port}", f"localhost:{port}"}:
            return value
    elif not value.startswith("/"):
        return value
    path = parts.path or "/"
    if not (path == PREFIX or path.startswith(PREFIX + "/")):
        path = PREFIX + path
    return urlunsplit(("", "", path, parts.query, parts.fragment))


def _headers(scope) -> dict[str, str]:
    """Lower-case names, the first value of each (as the stock's app reads them)."""
    out: dict[str, str] = {}
    for name, value in scope.get("headers") or []:
        out.setdefault(name.decode("latin-1").lower(), value.decode("latin-1"))
    return out


def _client(scope) -> str:
    # The socket peer, as the stock's guard counts it (uvicorn has applied a trusted X-Forwarded-For)
    return (scope.get("client") or ("?",))[0]


async def _respond(send, status: int, body: bytes, content_type: str, extra=None) -> None:
    headers = [(b"content-type", content_type.encode()), (b"content-length", str(len(body)).encode()),
               (b"cache-control", b"no-store"), *SECURITY]
    headers += [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in (extra or {}).items()]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


async def _page(send, status: int, title: str, text: str, extra=None, more: str = "", refresh: bool = False) -> None:
    page = _PAGE.format(title=html.escape(title), text=html.escape(text), more=more, prefix=PREFIX,
                        refresh=f'<meta http-equiv="refresh" content="{RETRY}">' if refresh else "")
    await _respond(send, status, page.encode(), "text/html; charset=utf-8", extra)


async def _redirect(send, location: str, query: bytes) -> None:
    location += "?" + query.decode("latin-1") if query else ""
    await _respond(send, 308, b"", "text/plain; charset=utf-8", {"Location": location})


async def _read_body(receive, headers: dict) -> bytes | None:
    """The request body, or None over MAX_BODY (declared or streamed) or when the client left."""
    try:
        if int(headers.get("content-length") or 0) > MAX_BODY:
            return None
    except ValueError:
        return None
    body = b""
    while True:
        message = await receive()
        if message["type"] != "http.request":
            return None
        body += message.get("body") or b""
        if len(body) > MAX_BODY:
            return None
        if not message.get("more_body"):
            return body


class Hub:
    """``app``: the stock's (LateApp); ``supervisor``: vnbot.altcoin_host.Supervisor;
    ``settings``: the stock's (WEB_PASSWORD, PUBLIC_URL); ``throttle``: the stock's too."""

    def __init__(self, app, supervisor, settings, throttle: webguard.LoginThrottle):
        self.app, self.sup, self.settings, self.throttle = app, supervisor, settings, throttle
        self.active = 0                 # requests at the child now (one event loop: no lock)
        self._http = None               # httpx.AsyncClient, made on first use

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            await self.app(scope, self._lifespan(receive), send)
            return
        path = scope.get("path", "")
        if scope["type"] != "http" or not (path == PREFIX or path.startswith(PREFIX + "/")):
            await self.app(scope, receive, send)
            return
        await self._altcoin(scope, receive, send)

    def _lifespan(self, receive):
        """The server's shutdown stops the child first (SIGTERM, time to release its lock)."""
        async def wrapped():
            message = await receive()
            if message["type"] == "lifespan.shutdown":
                await asyncio.to_thread(self.sup.stop)
            return message
        return wrapped

    async def _altcoin(self, scope, receive, send) -> None:
        method, path, query = scope["method"], scope["path"], scope.get("query_string") or b""
        raw = (scope.get("raw_path") or path.encode()).partition(b"?")[0]
        if not raw_path_ok(raw):
            await _page(send, 400, "Đường dẫn không hợp lệ", "Đường dẫn có ký tự hoặc đoạn không được phép.")
            return
        get = method in ("GET", "HEAD")
        bare = path.rstrip("/") or "/"
        if path == PREFIX and get:
            await _redirect(send, PREFIX + "/", query)
        elif path == PREFIX or bare in BLOCKED or path.startswith(BLOCKED_UNDER) or (path in TO_STOCK and not get):
            await _page(send, 404, "Không tìm thấy", NOT_FOUND)
        elif path in TO_STOCK:
            await _redirect(send, path[len(PREFIX):], query)
        else:
            headers = _headers(scope)
            refused = webguard.check_basic(headers.get("authorization"), self.settings.web_password, self.throttle,
                                           _client(scope))
            if refused:
                status, message, extra = refused
                if status == 401:
                    await _respond(send, 401, "Vui lòng đăng nhập".encode(), "text/html; charset=utf-8", extra)
                else:
                    await _page(send, status, "Trang web đang khoá" if status == 503 else "Tạm khoá đăng nhập",
                                message or "", extra)
            elif not get and not webguard.same_origin_ok(headers, self.settings.public_url):
                await _page(send, 403, "Đã từ chối", webguard.CROSS_SITE_MESSAGE)
            elif path == f"{PREFIX}/hub.json" and get:
                body = json.dumps(self.sup.state(), ensure_ascii=False, default=str).encode()
                await _respond(send, 200, body, "application/json")
            else:
                await self._proxy(scope, receive, send, headers, raw[len(PREFIX):], query)

    async def _proxy(self, scope, receive, send, headers: dict, path: bytes, query: bytes) -> None:
        upstream = self.sup.upstream()
        if upstream is None:
            await self._down(scope, send)
            return
        if self.active >= MAX_ACTIVE:
            await _page(send, 503, "Altcoin đang bận", "Quá nhiều yêu cầu cùng lúc, thử lại sau giây lát.",
                        {"Retry-After": "2"})
            return
        self.active += 1
        try:
            body = await _read_body(receive, headers)
            if body is None:
                await _page(send, 413, "Yêu cầu quá lớn", "Yêu cầu quá lớn.", {"Connection": "close"})
                return
            await self._forward(scope, send, headers, upstream, path, query, body)
        finally:
            self.active -= 1

    async def _forward(self, scope, send, headers: dict, upstream, path: bytes, query: bytes, body: bytes) -> None:
        import httpx

        port, token = upstream
        if self._http is None:
            self._http = httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT))
        listed = {t.strip().lower() for t in headers.get("connection", "").split(",")}     # hop-by-hop too
        out = [(k, v) for k, v in scope.get("headers") or []
               if (name := k.decode("latin-1").lower()) not in DROP and name not in listed
               and not name.startswith(("x-hub-", "x-forwarded-"))]
        host = headers.get("host", "")
        out += [(b"x-forwarded-for", _client(scope).encode("latin-1")), (b"x-forwarded-host", host.encode("latin-1")),
                (b"x-forwarded-proto", scope.get("scheme", "http").encode()), (b"x-forwarded-prefix", PREFIX.encode()),
                (b"x-hub-token", token.encode())]
        url = f"http://127.0.0.1:{port}{path.decode('latin-1')}" + (f"?{query.decode('latin-1')}" if query else "")
        try:
            response = await self._http.send(self._http.build_request(scope["method"], url, headers=out,
                                                                      content=body or None), stream=True)
        except httpx.HTTPError:         # it has just died, or does not answer
            await self._down(scope, send)
            return
        hosts = {webguard.host_of(h) for h in (host, headers.get("x-forwarded-host", "").split(",")[0],
                                                self.settings.public_url) if h}
        try:
            reply = [(name.lower(), rewrite_location(value.decode("latin-1"), port, hosts).encode("latin-1")
                      if name.lower() == b"location" else value)
                     for name, value in response.headers.raw if name.lower() not in REPLY_DROP]
            names = {name for name, _ in reply}
            reply += [h for h in SECURITY if h[0] not in names]
            await send({"type": "http.response.start", "status": response.status_code, "headers": reply})
            async for chunk in response.aiter_raw():
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b""})
        except httpx.HTTPError as exc:  # cut in the middle: the connection is closed, nothing more to say
            logger.warning("Altcoin: phản hồi bị cắt giữa chừng (%s)", type(exc).__name__)
        finally:
            await response.aclose()

    async def _down(self, scope, send) -> None:
        """The child is not running (or not answering): 503, why, and its last lines."""
        snap = self.sup.state()
        state, reason = snap.get("state"), snap.get("reason") or ""
        retry = {"Retry-After": str(RETRY)}
        if scope["path"] == f"{PREFIX}/health":
            await _respond(send, 503, json.dumps({"ok": False, "state": state}).encode(), "application/json", retry)
            return
        coming = state in ("starting", "running", "backoff", "waiting")
        title = "Altcoin đang khởi động" if coming else "Altcoin đang tắt"
        text = reason or ("Trang sẽ tự tải lại sau giây lát." if coming else "Altcoin không chạy.")
        lines = list(self.sup.lines)[-LOG_LINES:]
        more = f"<pre>{html.escape(chr(10).join(lines))}</pre>" if lines else ""
        await _page(send, 503, title, text, retry, more, refresh=coming)
