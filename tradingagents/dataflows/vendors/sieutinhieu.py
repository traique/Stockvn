"""sieutinhieu.vn: the per-stock signal history, for the owner's personal comparison.

Two endpoints, without login, as the free site shows them:

- ``/api/v1/signals/latest``: ONE stock's signal history (what a search for a
  code shows), only for the stocks the owner follows (the bot's universe, capped);
- ``/api/v1/realtime-signals/live-signals/today-trend-changes``: the market-wide
  list of the session's new signals, which is a paid product: without an
  account it answers a teaser (a few signals, the rest counted as locked). The
  teaser is taken as it is; the locked part is never rebuilt by scanning every
  code.

Polite by construction: an honest User-Agent (no browser disguise, no
borrowed Origin/Referer), at most one request every ``MIN_INTERVAL_SECONDS``,
no retry, a short timeout, nothing asked for ``DOWN_COOLDOWN_SECONDS`` after a
failure, and after a refusal (401/403/429) nothing for
``REFUSED_COOLDOWN_SECONDS`` (``vnbot.external`` keeps that across restarts):
a block is respected, never evaded.

Prices come in thousands of VND (23.85 = 23.850đ) and are scaled to VND here.
A signal's price is the CLOSE of its session (``price_basis`` "bar_close"): it
is known only after that close, so it can be acted on from the next session.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime

from tradingagents.dataflows.errors import VendorUnavailableError
from tradingagents.dataflows.vendors import http
from tradingagents.market import vn_rules

logger = logging.getLogger(__name__)

BASE = "https://sieutinhieu.vn/api/v1"
VENDOR = "sieutinhieu"
USER_AGENT = "TradingAgentsVN/1.0 (personal bot, single user, signal comparison)"
PRICE_SCALE = 1000
MIN_INTERVAL_SECONDS = 1.5
TIMEOUT_SECONDS = 8.0
DOWN_COOLDOWN_SECONDS = 600
REFUSED_COOLDOWN_SECONDS = 24 * 3600
HISTORY_LIMIT = 30

_lock = threading.Lock()
_last_call = 0.0
_refused_until = 0.0
_down_until = 0.0


class Refused(VendorUnavailableError):
    """The site refused the request (401/403/429): stop asking for a while."""


def refused_for() -> float:
    """Seconds left of the cooldown after a refusal (0 when none)."""
    return max(0.0, _refused_until - time.time())


def refuse_until(moment: float) -> None:
    """Restore a refusal cooldown saved before a restart."""
    global _refused_until
    _refused_until = max(_refused_until, float(moment))


def _get(path: str, params: dict) -> object:
    global _last_call, _refused_until, _down_until
    with _lock:
        if time.time() < _refused_until:
            raise Refused(f"{VENDOR} từ chối gần đây; tạm ngừng hỏi thêm {refused_for() / 3600:.1f} giờ")
        if time.time() < _down_until:
            raise VendorUnavailableError(f"{VENDOR} vừa không trả lời; thử lại sau ít phút")
        wait = MIN_INTERVAL_SECONDS - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        try:
            response = http.request("GET", f"{BASE}{path}", vendor=VENDOR, params=params, retries=0,
                                    timeout=TIMEOUT_SECONDS,
                                    headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                                    passthrough=(401, 403, 404, 429))
        except VendorUnavailableError:
            _down_until = time.time() + DOWN_COOLDOWN_SECONDS
            raise
        finally:
            _last_call = time.monotonic()
        if response.status_code in (401, 403, 429):
            _refused_until = time.time() + REFUSED_COOLDOWN_SECONDS
            logger.warning("%s answered HTTP %s: not asked again for 24 h", VENDOR, response.status_code)
            raise Refused(f"{VENDOR} từ chối (HTTP {response.status_code})")
        if response.status_code == 404:
            return []                 # the site knows no such stock
    try:
        return response.json()
    except ValueError as exc:
        raise VendorUnavailableError(f"{VENDOR} returned a non-JSON body") from exc


def _vn_time(raw: str | None) -> str:
    """An ISO timestamp as Vietnam time ``YYYY-MM-DDTHH:MM`` ("" when unreadable)."""
    try:
        moment = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return ""
    if moment.tzinfo is None:
        return ""
    return moment.astimezone(vn_rules.VN_TZ).strftime("%Y-%m-%dT%H:%M")


def _scaled(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number * PRICE_SCALE) if number > 0 else None


def normalize(row: dict) -> dict | None:
    """One signal as ``{day, side, price, created, strength_pct, tp1, sl, live}``, or None.

    ``live``: published before the next session opens (``created`` before 09:00 of
    the next session day, Vietnam time), so it could be acted on at that open. The site
    regenerates its history from time to time (every past signal then carries the
    regeneration date): those are a backtest, not signals anyone could follow then.
    """
    if not isinstance(row, dict):
        return None
    side = str(row.get("signal_type") or "").upper()
    day = str(row.get("timestamp") or "")[:10]
    try:
        date.fromisoformat(day)
    except ValueError:
        return None
    price = _scaled(row.get("price"))
    if side not in ("BUY", "SELL") or price is None:
        return None
    if str(row.get("price_basis") or "bar_close") != "bar_close":
        return None
    plan = row.get("plan") if isinstance(row.get("plan"), dict) else {}
    created = _vn_time(row.get("created_at"))
    live = bool(created) and created < f"{vn_rules.add_sessions(day, 1)}T09:00"    # before the next open
    try:
        strength = int(row.get("strength_pct")) if row.get("strength_pct") is not None else None
    except (TypeError, ValueError):
        strength = None
    return {"symbol": str(row.get("symbol") or "").upper(), "day": day, "side": side, "price": price,
            "created": created, "strength_pct": strength, "tp1": _scaled(plan.get("tp1")), "sl": _scaled(plan.get("sl")),
            "entry_low": _scaled(plan.get("entry_low")), "entry_high": _scaled(plan.get("entry_high")), "live": live}


def fetch_signals(symbol: str, limit: int = HISTORY_LIMIT) -> list[dict]:
    """The stock's latest signals, newest first (normalized). Raises
    ``VendorUnavailableError`` (``Refused`` on a refusal) when the site does not answer."""
    data = _get("/signals/latest", {"symbol": symbol.upper(), "timeframe": "1D", "limit": str(int(limit))})
    rows = data if isinstance(data, list) else (data.get("data") if isinstance(data, dict) else None)
    if not isinstance(rows, list):
        # A maintenance page or a changed format is not "no signals": never let it wipe a history.
        raise VendorUnavailableError(f"{VENDOR} returned an unexpected body ({type(data).__name__})")
    out = [s for s in (normalize(r) for r in rows) if s is not None]
    out.sort(key=lambda s: (s["day"], s["created"]), reverse=True)
    return out


def fetch_live_changes(side: str) -> dict:
    """The free view of today's trend changes of ``side`` as the site detects them during the
    session (``stage`` "intraday": not final until the close): ``{changes, total, locked,
    during}``; each change ``{symbol, side, day, at (Vietnam YYYY-MM-DDTHH:MM), price (VND,
    at detection), now (VND, current), from, stage}``. Only the rows shown without an account
    (two a side); the rest is a paid product, left alone."""
    side = side.upper()
    if side not in ("BUY", "SELL"):
        raise ValueError(side)
    data = _get("/realtime-signals/live-signals/today-trend-changes",
                {"limit": "200", "timeframe": "1D", "signal_type": side, "include_all_today": "false",
                 "sort_by": "trading_value"})
    if not isinstance(data, dict) or not isinstance(data.get("signals"), list):
        raise VendorUnavailableError(f"{VENDOR} returned an unexpected market list ({type(data).__name__})")
    changes = []
    for row in data["signals"]:
        if not isinstance(row, dict) or str(row.get("signal_type") or "").upper() != side:
            continue
        at = _vn_time(row.get("intraday_at") or row.get("timestamp"))
        price = _scaled(row.get("price"))
        if not at or price is None or not row.get("symbol"):
            continue
        changes.append({"symbol": str(row["symbol"]).upper(), "side": side, "day": at[:10], "at": at,
                        "price": price, "now": _scaled(row.get("current_price")),
                        "from": str(row.get("trend_change_from") or "").upper(),
                        "stage": str(row.get("stage") or ""),
                        "met": row.get("condition_met") if isinstance(row.get("condition_met"), bool) else None})

    def count(key, default):
        try:
            return max(0, int(data.get(key)))
        except (TypeError, ValueError):
            return default

    return {"changes": changes, "total": count("total", len(changes)), "locked": count("locked_count", 0),
            "during": bool(data.get("is_during_trading"))}


def fetch_market_changes(side: str) -> dict:
    """The free view of the session's market-wide new signals of ``side`` (BUY/SELL):
    ``{signals, total, locked, teaser, confirmed}``. ``signals`` holds only what the site
    shows without an account; ``locked`` counts the rest (a paid product, left alone)."""
    side = side.upper()
    if side not in ("BUY", "SELL"):
        raise ValueError(side)
    data = _get("/realtime-signals/live-signals/today-trend-changes",
                {"limit": "200", "timeframe": "1D", "signal_type": side, "include_all_today": "false",
                 "sort_by": "trading_value"})
    if not isinstance(data, dict) or not isinstance(data.get("signals"), list):
        raise VendorUnavailableError(f"{VENDOR} returned an unexpected market list ({type(data).__name__})")
    signals = [s for s in (normalize(r) for r in data["signals"]) if s is not None and s["side"] == side]

    def count(key, default):
        try:
            return max(0, int(data.get(key)))
        except (TypeError, ValueError):
            return default

    return {"signals": signals, "total": count("total", len(signals)), "locked": count("locked_count", 0),
            "teaser": bool(data.get("is_teaser")), "confirmed": bool(data.get("session_confirmed"))}
