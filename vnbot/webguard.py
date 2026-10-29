"""The web password and the cross-site check, for the stock's pages (vnbot.web) and the
altcoin's under /altcoin/ (vnbot.hub): one WEB_PASSWORD, one lockout counter, one answer.

Pure functions on a header value or a header mapping, standard library only (the Hub
imports this before any framework is loaded).
"""

from __future__ import annotations

import base64
import logging
import secrets
import threading
import time
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

LOCKED_MESSAGE = (
    "Trang web đang được khoá do chưa thiết lập WEB_PASSWORD. Vui lòng bổ sung WEB_PASSWORD trong cấu hình "
    "rồi khởi động lại máy chủ; bot Telegram vẫn hoạt động bình thường."
)
CROSS_SITE_MESSAGE = "Yêu cầu gửi từ trang khác đã bị từ chối."
ASK = {"WWW-Authenticate": "Basic"}


class LoginThrottle:
    """Wrong-password counter per client: after ``max_failures`` within ``window``
    seconds that client is refused for ``lockout`` seconds, right password or not.

    In memory only (one process); the table is bounded so a scan of many
    addresses cannot grow it without limit.
    """

    def __init__(self, max_failures: int = 8, window: float = 600.0, lockout: float = 300.0,
                 max_clients: int = 10_000, clock=time.monotonic):
        self.max_failures, self.window, self.lockout = max_failures, window, lockout
        self.max_clients, self.clock = max_clients, clock
        self._failures: dict[str, list[float]] = {}
        self._locked: dict[str, float] = {}
        self._lock = threading.Lock()

    def retry_after(self, client: str) -> int:
        """Seconds left on ``client``'s lockout, 0 when it may try."""
        with self._lock:
            until = self._locked.get(client)
            if until is None:
                return 0
            left = until - self.clock()
            if left <= 0:
                del self._locked[client]
                return 0
            return int(left) + 1

    def failure(self, client: str) -> None:
        with self._lock:
            now = self.clock()
            recent = [t for t in self._failures.get(client, []) if now - t < self.window] + [now]
            if len(recent) >= self.max_failures:
                self._failures.pop(client, None)
                self._locked[client] = now + self.lockout
            else:
                self._failures[client] = recent
            if len(self._failures) + len(self._locked) > self.max_clients:
                self._prune(now)

    def success(self, client: str) -> None:
        with self._lock:
            self._failures.pop(client, None)

    def _prune(self, now: float) -> None:
        self._locked = {c: u for c, u in self._locked.items() if u > now}
        self._failures = {c: ts for c, ts in self._failures.items() if ts and now - ts[-1] < self.window}
        while self._failures and len(self._failures) + len(self._locked) > self.max_clients:
            self._failures.pop(next(iter(self._failures)))   # the oldest first seen


def basic_password(header: str | None) -> str | None:
    """The password of an HTTP Basic ``Authorization`` value (any user name); None when it
    is not Basic or cannot be read."""
    kind, _, token = (header or "").partition(" ")
    if kind.lower() != "basic":
        return None
    try:
        return base64.b64decode(token.strip(), validate=True).decode("utf-8").partition(":")[2]
    except ValueError:                  # binascii.Error, UnicodeDecodeError
        return None


def password_ok(header: str | None, expected: str) -> bool:
    """``header`` carries ``expected`` (never true while WEB_PASSWORD is unset)."""
    password = basic_password(header)
    return bool(expected) and password is not None and secrets.compare_digest(password.encode("utf-8"),
                                                                             expected.encode("utf-8"))


def check_basic(header: str | None, expected: str, throttle: LoginThrottle,
                client: str) -> tuple[int, str | None, dict | None] | None:
    """None when ``header`` carries WEB_PASSWORD ``expected``; else (status, message, headers):
    503 while it is unset, 429 while ``client`` is locked out, 401 without it (a browser's
    first request: not counted) or with a wrong one (counted)."""
    if not expected:
        return 503, LOCKED_MESSAGE, None
    wait = throttle.retry_after(client)
    if wait:
        return 429, f"Nhập sai mật khẩu quá nhiều lần. Vui lòng thử lại sau {wait // 60 + 1} phút.", \
            {"Retry-After": str(wait)}
    if (header or "").partition(" ")[0].lower() != "basic":
        return 401, None, ASK
    if not password_ok(header, expected):
        throttle.failure(client)
        logger.warning("Web: wrong password from %s", client)
        return 401, None, ASK
    throttle.success(client)
    return None


def host_of(value: str) -> str:
    return (urlsplit(value).netloc if "//" in value else value).strip().lower()


def same_origin_ok(headers, public_url: str = "") -> bool:
    """False for a request sent from another site (CSRF: Basic auth rides along with it):
    Origin, else Sec-Fetch-Site, else Referer, against the Host, the first X-Forwarded-Host
    and the host of PUBLIC_URL. ``headers``: lower-case names (or a case-insensitive mapping)."""
    allowed = {host_of(h) for h in (headers.get("host", ""), headers.get("x-forwarded-host", "").split(",")[0],
                                    public_url) if h}
    origin = headers.get("origin")
    if origin is not None:
        return origin != "null" and host_of(origin) in allowed
    if headers.get("sec-fetch-site") == "cross-site":
        return False
    referer = headers.get("referer")
    return not referer or host_of(referer) in allowed
