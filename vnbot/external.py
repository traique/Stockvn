"""sieutinhieu.vn beside the bot: their signals kept, shown and traded on a paper book.

For the owner's personal comparison only (``SIEUTINHIEU=1``; off by default).
The signals never change a decision, an order or a level of the bot: they are
shown next to its own (``/nhanh``, the evening report) and followed by one more
paper book under the bot's honest rules, so the two can be compared on equal
terms:

- a signal is the close of its session, known only after it: the book buys at
  the NEXT session's open (and sells at the next open after a SELL), with the
  fees, the 0.1% sell tax, slippage, T+2 and the floor-lock rule of every
  paper book (``tradingagents.trading.ledger``);
- it only follows signals published live (``live``): the site regenerates its
  history from time to time, and a regenerated past is a backtest nobody could
  have traded;
- it buys only on a BUY of the session it plans for (a fresh signal), equal
  weight over ``SLOTS`` slots, within ITS OWN watchlist (``Watchlist``: separate
  from the bot's /theodoi, at most ``WATCHLIST_MAX`` stocks; holdings first, at
  most ``max_symbols()`` lookups a run: the site's per-stock lookup, never a scan
  of the market, whose list is a paid product).

The per-stock history is fetched once a day after the close (and on demand for
``/nhanh`` and ``/stv MÃ``, at most every ``MAX_AGE_HOURS``), one request per
stock, slowly; see ``tradingagents.dataflows.vendors.sieutinhieu``.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from vnbot.jobs import write_text_atomic

logger = logging.getLogger(__name__)

NAME = "Siêu tín hiệu"
SLOTS = 10
WATCHLIST_MAX = 60
MAX_AGE_HOURS = 6.0
LISTED_ROWS = 5            # rows of the free market lists kept per stock (their plans)
LISTED_SYMBOLS = 300
MAX_FAILURES = 3            # failed stocks in one refresh: the site is down, stop for this run
REFRESH_BUDGET_SECONDS = 240    # one refresh never takes longer (the rest waits for the next one)
BOOK_FILE = "paper_stv.json"
ACTION_VI = {"BUY": "MUA", "SELL": "BÁN", "HOLD": "GIỮ", "WATCH": "THEO DÕI", "NO_TRADE": "ĐỨNG NGOÀI"}

_store_lock = threading.Lock()   # read-merge-write of the store file (the evening run, /nhanh, /stv)
_watch_lock = threading.RLock()     # a change reads, then writes, under one lock

# The default watchlist (built 21/10/2026, change it with /stvtheodoi): the whole VN30 - the bot's
# own basket, so both are compared on the same stocks - then the 30 most traded other stocks
# (median value traded over the last 20 sessions, among VN100, HNX30 and the large UPCoM names):
# liquid enough to buy and sell a real order at the price the site shows.
DEFAULT_WATCHLIST = (
    # VN30
    "VIC", "TCB", "VHM", "SHB", "VPB", "HPG", "BSR", "SSI", "MSN", "HDB",
    "FPT", "CTG", "MWG", "MBB", "STB", "VCB", "ACB", "VJC", "BID", "VNM",
    "GVR", "MCH", "LPB", "VRE", "GAS", "VIB", "TCX", "VPL", "SSB", "SAB",
    # the 30 most traded others
    "VIX", "NVL", "PNJ", "PVT", "MSB", "VCI", "GEX", "VND", "PLX", "PVS",
    "SHS", "TPB", "VPI", "GEE", "FRT", "VSC", "CII", "DXG", "DCM", "GMD",
    "CEO", "DGW", "POW", "BVH", "PVD", "HCM", "EIB", "BAF", "DPM", "PDR",
)


def enabled() -> bool:
    from vnbot.settings import _flag

    return _flag("SIEUTINHIEU", False)


def morning_time() -> str:
    """When the morning brief goes to Zoom (``STV_MORNING_TIME``, Vietnam time, weekdays)."""
    from vnbot.settings import _hhmm

    return _hhmm("STV_MORNING_TIME", "08:30")


def scan_minutes() -> int:
    """How often the owner's holdings are checked for a SELL during the session
    (``STV_SCAN_MINUTES``, default 15, 5 to 120)."""
    from vnbot.settings import _int

    return max(5, min(_int("STV_SCAN_MINUTES", 15), 120))


def watch_intraday() -> bool:
    """Scan the whole watchlist every ``scan_minutes()`` during the session too
    (``STV_WATCH_INTRADAY=1``). Off by default: the site's per-stock signals only change
    after the close (~15:08), so the watchlist is scanned from ``WATCH_FROM`` to ``WATCH_UNTIL``."""
    from vnbot.settings import _flag

    return _flag("STV_WATCH_INTRADAY", False)


def market_on() -> bool:
    """The bot's own market-wide scan (``vnbot.market_scan``), compared with the site: on by
    default, ``STV_MARKET=0`` turns it off."""
    from vnbot.settings import _flag

    return _flag("STV_MARKET", True)


def market_size() -> int:
    """How many of the most traded stocks the market scan covers (``STV_MARKET_SIZE``, 50-500,
    default 300)."""
    from vnbot.settings import _int

    return max(50, min(_int("STV_MARKET_SIZE", 300), 500))


def estimate_on() -> bool:
    """During the session, the bot's own estimate of the site's trend changes for the followed
    stocks (``vnbot.trend_estimate``: public prices, no request to the site): on by default,
    ``STV_ESTIMATE=0`` turns it off."""
    from vnbot.settings import _flag

    return _flag("STV_ESTIMATE", True)


WATCH_FROM, WATCH_UNTIL = "15:15", "16:00"     # the watchlist scans after the close (the day's signals ~15:08)
SITE_FINAL_TIME = "15:10"                      # from here the site's per-stock state is the day's, not yesterday's
HOLDINGS_OUTSIDE_WATCHLIST = 20     # the owner's stocks bought outside the watchlist, fetched too


def max_symbols() -> int:
    """Lookups in one refresh: the whole watchlist and every holding outside it (the owner's
    and the comparison book's)."""
    return WATCHLIST_MAX + HOLDINGS_OUTSIDE_WATCHLIST + SLOTS


def data_dir() -> Path:
    """The app's data directory, as ``vnbot.settings.load`` resolves it."""
    return Path(os.environ.get("DATA_DIR") or "data").resolve()


def _now():
    from tradingagents.market import vn_rules

    return vn_rules.now_vn()


def _fmt(value) -> str:
    return f"{value:,.0f}".replace(",", ".") if value is not None else "-"


def _ddmm(day: str) -> str:
    """dd/mm, with the year when it is not the current one (a year-old signal must not read as recent)."""
    if len(day) < 10:
        return day
    return f"{day[8:10]}/{day[5:7]}" + ("" if day[:4] == _now().strftime("%Y") else f"/{day[2:4]}")


def next_open_cutoff(day: str) -> str:
    """09:00 of the session after ``day`` (Vietnam ``YYYY-MM-DDTHH:MM``): a signal created
    later cannot have been acted on at that open."""
    from tradingagents.market import vn_rules

    return f"{vn_rules.add_sessions(day, 1)}T09:00"


class Watchlist:
    """The stocks followed on sieutinhieu.vn: separate from the bot's watchlist (/theodoi).
    Saved in ``data/external/watchlist.json`` once changed (/stvtheodoi); until then
    ``STV_WATCHLIST`` (codes separated by commas or spaces) when set and valid, else
    ``DEFAULT_WATCHLIST``. Every change keeps the list before it (``undo``)."""

    def __init__(self, directory: Path | None = None):
        self.path = Path(directory or data_dir()) / "external" / "watchlist.json"
        self.backup = self.path.with_name("watchlist.prev.json")

    @staticmethod
    def _split(text: str) -> list[str]:
        return [c for c in re.split(r"[,;\s]+", text or "") if c]

    @staticmethod
    def _clean(codes) -> list[str]:
        """Valid stock codes, upper case, no duplicates. ValueError (Vietnamese) on a bad one."""
        from vnbot.service import check_request

        out = []
        for raw in codes:
            raw = str(raw).strip()
            if not raw:
                continue
            code, _ = check_request(raw)
            if code not in out:
                out.append(code)
        return out

    @staticmethod
    def _listed(codes: list[str]) -> None:
        """ValueError for a code the exchange listing does not know (when it can be read)."""
        unknown = _not_listed(codes) if codes else []
        if unknown:
            raise ValueError(f"Không có trên sàn: {', '.join(unknown[:10])}. Kiểm tra lại mã.")

    def _from_env(self) -> list[str] | None:
        raw = self._split(os.environ.get("STV_WATCHLIST") or "")
        if not raw:
            return None
        try:
            codes = self._clean(raw)[:WATCHLIST_MAX]
        except ValueError as exc:
            logger.warning("STV_WATCHLIST ignored (%s); using the default list", exc)
            return None
        return codes or None

    def default(self) -> list[str]:
        return self._from_env() or list(DEFAULT_WATCHLIST)

    def source(self) -> str:
        """Where ``get()`` takes the list from: "đã chỉnh", "từ STV_WATCHLIST" or "mặc định"."""
        if self._read() is not None:
            return "đã chỉnh"
        return "từ STV_WATCHLIST" if self._from_env() else "mặc định"

    def is_custom(self) -> bool:
        return self._read() is not None

    def _read(self) -> list[str] | None:
        """The saved list, or None (never changed, or unreadable: then the default is used)."""
        with _watch_lock:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return None
            except (OSError, ValueError) as exc:
                logger.warning("Siêu tín hiệu watchlist unreadable (%s); using the default list", exc)
                return None
        if not isinstance(data, list):
            return None
        return [str(c).upper() for c in data if isinstance(c, str) and c.strip()][:WATCHLIST_MAX]

    def get(self) -> list[str]:
        saved = self._read()
        return saved if saved is not None else self.default()

    def _save(self, codes: list[str], before: list[str]) -> list[str]:
        """Write ``codes`` (caller holds ``_watch_lock``), keeping ``before`` for ``undo``."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(self.backup, json.dumps(before))
        write_text_atomic(self.path, json.dumps(codes))
        return codes

    def set(self, codes) -> list[str]:
        new = self._clean(codes)
        if not new:
            raise ValueError("Danh sách trống.")
        if len(new) > WATCHLIST_MAX:
            raise ValueError(f"Watchlist Siêu tín hiệu tối đa {WATCHLIST_MAX} mã (gửi {len(new)} mã).")
        self._listed(new)
        with _watch_lock:
            current = self.get()
            return current if new == current else self._save(new, current)

    def add(self, codes) -> tuple[list[str], list[str]]:
        """(the new list, the codes actually added)."""
        new = self._clean(codes)
        current = self.get()
        added = [c for c in new if c not in current]
        if not added:
            return current, []
        if len(current) + len(added) > WATCHLIST_MAX:
            room = WATCHLIST_MAX - len(current)
            raise ValueError(f"Watchlist đã có {len(current)}/{WATCHLIST_MAX} mã"
                             + (f", chỉ thêm được {room} mã" if room > 0 else "")
                             + f". Bỏ bớt trước: /stvtheodoi bo MÃ (rồi thêm {', '.join(added)}).")
        self._listed(added)                       # may read the listing: not under the lock
        with _watch_lock:
            current = self.get()                  # again: another command may have changed it
            added = [c for c in new if c not in current]
            if len(current) + len(added) > WATCHLIST_MAX:
                raise ValueError(f"Watchlist đã có {len(current)}/{WATCHLIST_MAX} mã. Bỏ bớt trước: /stvtheodoi bo MÃ.")
            if not added:
                return current, []
            return self._save(current + added, current), added

    def remove(self, codes) -> tuple[list[str], list[str]]:
        """(the new list, the codes actually removed)."""
        gone = self._clean(codes)
        with _watch_lock:
            current = self.get()
            removed = [c for c in gone if c in current]
            if not removed:
                return current, []
            return self._save([c for c in current if c not in removed], current), removed

    def reset(self) -> list[str]:
        """Back to ``default()`` (the list before is kept for ``undo``)."""
        with _watch_lock:
            if self._read() is None:
                return self.default()             # already the default: keep the backup as it is
            write_text_atomic(self.backup, json.dumps(self.get()))
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
        return self.default()

    def undo(self) -> list[str]:
        """The list before the last change, back (ValueError when there is none)."""
        with _watch_lock:
            try:
                before = json.loads(self.backup.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raise ValueError("Không có thay đổi nào để hoàn tác.") from None
            if not isinstance(before, list) or not before:
                raise ValueError("Không có thay đổi nào để hoàn tác.")
            return self._save([str(c).upper() for c in before][:WATCHLIST_MAX], self.get())


def _not_listed(codes: list[str]) -> list[str]:
    """The codes the exchange listing does not know; [] when the listing cannot be read."""
    try:
        from tradingagents.dataflows.vendors import vci

        listed = {str(row.get("symbol", "")).upper() for row in vci.fetch_listing()}
    except Exception as exc:  # noqa: BLE001 - the shape check is done; the listing is a bonus
        logger.info("Listing unavailable for the watchlist check: %s", exc)
        return []
    return [c for c in codes if c not in listed] if listed else []


_alerts_lock = threading.Lock()


class SellAlerts:
    """The SELL signals already announced for the owner's holdings (``stv_scan``), one per
    stock (``data/external/sell_alerts.json``): a signal is announced once; a stock no longer
    held is forgotten (bought again while the site still says SELL: announced again)."""

    def __init__(self, directory: Path | None = None):
        self.path = Path(directory or data_dir()) / "external" / "sell_alerts.json"

    @staticmethod
    def final(signal: dict) -> bool:
        """The signal of a closed session: published from 15:00 of its day, or later (a signal
        published during the session may still change at the close)."""
        created, day = str(signal.get("created") or ""), str(signal.get("day") or "")
        return not (created[:10] == day and created[11:16] < "15:00")

    @classmethod
    def key(cls, signal: dict) -> str:
        """Its session and whether final - not its publication time, which the site rewrites
        when it regenerates its history (the same SELL must not be announced again); an
        in-session SELL confirmed at the close is announced once more, as final."""
        return f"{signal.get('day', '')}|{'final' if cls.final(signal) else 'session'}"

    def warn_once(self, day: str) -> bool:
        """True the first time it is asked on ``day`` (one 'cannot fetch' notice a day)."""
        with _alerts_lock:
            data = self._read()
            if data.get("_warned") == day:
                return False
            data["_warned"] = day
            self.path.parent.mkdir(parents=True, exist_ok=True)
            write_text_atomic(self.path, json.dumps(data))
            return True

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def pending(self, sells) -> list:
        """The (symbol, signal) of ``sells`` not announced yet."""
        with _alerts_lock:
            sent = self._read()
        return [(sym, sig) for sym, sig in sells if sent.get(sym) != self.key(sig)]

    # the "_warned" entry is not a stock: kept by remember()

    def remember(self, announced, held) -> None:
        with _alerts_lock:
            before = self._read()
            sent = {sym: k for sym, k in before.items() if sym in set(held) or sym.startswith("_")}
            sent.update({sym: self.key(sig) for sym, sig in announced})
            if sent != before:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                write_text_atomic(self.path, json.dumps(sent))


class WatchAlerts:
    """The signals of the day already announced for the followed stocks (``stv_scan``): per
    stock, "SIDE:intraday" (a change in the session - shown by the site, or estimated by the
    bot -, not final), "SIDE:final" (the signal of the closed session) and "SIDE:reverted"
    (an in-session change the close did not keep); reset every day."""

    def __init__(self, directory: Path | None = None):
        self.path = Path(directory or data_dir()) / "external" / "watch_alerts.json"

    def _read(self, day: str) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data.get("keys", {}) if isinstance(data, dict) and data.get("day") == day else {}

    def keys(self, day: str) -> dict:
        """{symbol: [keys announced on ``day``]}."""
        with _alerts_lock:
            return self._read(day)

    def pending(self, day: str, items) -> list:
        """The (symbol, key, payload) of ``items`` not announced on ``day`` (a key given twice
        in ``items`` counts once: the first one)."""
        with _alerts_lock:
            sent = self._read(day)
        out, seen = [], set()
        for sym, key, x in items:
            if key in sent.get(sym, []) or (sym, key) in seen:
                continue
            seen.add((sym, key))
            out.append((sym, key, x))
        return out

    def remember(self, day: str, announced) -> None:
        if not announced:
            return
        with _alerts_lock:
            sent = self._read(day)
            for sym, key, _ in announced:
                sent.setdefault(sym, [])
                if key not in sent[sym]:
                    sent[sym].append(key)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            write_text_atomic(self.path, json.dumps({"day": day, "keys": sent}))


class SignalStore:
    """The fetched signal histories, one entry per stock, saved under ``data/external``."""

    def __init__(self, directory: Path | None = None):
        self.path = Path(directory or data_dir()) / "external" / "sieutinhieu.json"

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"symbols": {}}
        if not isinstance(data, dict) or not isinstance(data.get("symbols"), dict):
            return {"symbols": {}}
        return data

    def _write(self, data: dict) -> None:
        write_text_atomic(self.path, json.dumps(data, ensure_ascii=False))

    def signals(self, symbol: str) -> list[dict]:
        entry = self._read()["symbols"].get(symbol.upper()) or {}
        return [s for s in entry.get("signals") or [] if isinstance(s, dict)]

    def fetched_at(self, symbol: str) -> str:
        return str((self._read()["symbols"].get(symbol.upper()) or {}).get("fetched") or "")

    def last_error(self) -> str:
        return str(self._read().get("error") or "")

    def refresh(self, symbols, *, fresh_after: str | None = None, fetch=None,
                max_age_hours: float = MAX_AGE_HOURS) -> tuple[int, str]:
        """Fetch the stocks whose saved history is older than ``max_age_hours`` or than
        ``fresh_after`` (Vietnam ``YYYY-MM-DDTHH:MM``). Returns (stocks fetched, error or "").

        Bounded: stops at a refusal (kept in the file for 24 h, across restarts), after
        ``MAX_FAILURES`` failures, or after ``REFRESH_BUDGET_SECONDS``. An empty answer never
        replaces a history already saved. The file is re-read and merged under a lock when
        written, so two refreshes at once cannot put back older data."""
        from tradingagents.dataflows.vendors import sieutinhieu as stv

        fetch = fetch or stv.fetch_signals
        data = self._read()
        try:
            stv.refuse_until(float(data.get("refused_until") or 0))
        except (TypeError, ValueError):
            pass
        now = _now()
        stamp = now.strftime("%Y-%m-%dT%H:%M")
        started = time.monotonic()
        got: dict[str, dict] = {}
        failures, error, refused = 0, "", False
        wanted = list(dict.fromkeys(s.upper() for s in symbols))
        cap, cut = max_symbols(), ""
        if len(wanted) > cap:
            cut = f"quá {cap} mã, bỏ qua {len(wanted) - cap} mã cuối ({', '.join(wanted[cap:][:5])}...)"
            logger.warning("Siêu tín hiệu: %s", cut)
        for symbol in wanted[:cap]:
            entry = data["symbols"].get(symbol) or {}
            fetched = str(entry.get("fetched") or "")
            if fetched and (fresh_after is None or fetched >= fresh_after) and _age_hours(fetched, now) < max_age_hours:
                continue
            if time.monotonic() - started > REFRESH_BUDGET_SECONDS:
                error = error or "hết thời gian lấy dữ liệu lượt này"
                break
            try:
                signals = fetch(symbol)
            except stv.Refused as exc:
                error, refused = str(exc), True
                break
            except Exception as exc:  # noqa: BLE001 - one stock must not stop the others
                failures += 1
                error = f"{symbol}: {exc}"
                logger.warning("Siêu tín hiệu: %s", error)
                if failures >= MAX_FAILURES:
                    break
                continue
            if not signals and entry.get("signals"):
                error = f"{symbol}: trang trả về rỗng, giữ lịch sử đã lưu"
                continue
            got[symbol] = {"fetched": stamp, "signals": signals}
        error = "; ".join(e for e in (cut, error) if e)      # the cap is never hidden by a later error
        with _store_lock:
            current = self._read()              # merge into what is on disk now
            current["symbols"].update(got)
            current["error"], current["error_at"] = (error, stamp) if error else ("", "")
            if refused:
                current["refused_until"] = time.time() + stv.REFUSED_COOLDOWN_SECONDS
            if got or error:
                self._write(current)
        return len(got), error

    def refresh_market(self, day: str, *, fetch=None) -> str:
        """Save the free view of the market-wide new signals of session ``day`` (BUY and SELL).
        The site's list is "today's", so it is only read on ``day`` itself after 15:10 (a
        morning read would be the new day's, still empty), and saved only once the site
        says the session is confirmed and the rows it shows are of ``day`` (never the list
        of the session before, still on the site, under ``day``). Returns an error or ""
        (the scheduler asks again while it is missing: ``market_missing``)."""
        from tradingagents.dataflows.vendors import sieutinhieu as stv

        now = _now()
        if now.strftime("%Y-%m-%d") != day or now.strftime("%H:%M") < SITE_FINAL_TIME:
            return ""
        try:
            stv.refuse_until(float(self._read().get("refused_until") or 0))
        except (TypeError, ValueError):
            pass
        fetch = fetch or stv.fetch_market_changes
        stamp = now.strftime("%Y-%m-%dT%H:%M")
        got, error, refused = {}, "", False
        for side in ("BUY", "SELL"):
            if self.market(side, day) is not None:
                continue
            try:
                data = fetch(side)
            except stv.Refused as exc:
                error, refused = f"danh sách {side}: {exc}", True
                break
            except Exception as exc:  # noqa: BLE001 - the brief says it is missing
                error = f"danh sách {side}: {exc}"
                break
            rows = [x for x in data["signals"] if x.get("day") == day]
            # Confirmed, and either rows of ``day`` or a session without any change at all:
            # an empty view with a count could still be the session before's list.
            if not data.get("confirmed") or (not rows and (data["signals"] or data["total"])):
                error = f"danh sách {side} phiên {_ddmm(day)} chưa chốt trên trang"
                break
            got[side] = {"day": day, "fetched": stamp, "total": data["total"], "locked": data["locked"],
                         "signals": rows}
        if got or refused:
            with _store_lock:
                current = self._read()
                current.setdefault("market", {}).update(got)
                listed = current.get("listed") if isinstance(current.get("listed"), dict) else {}
                for entry in got.values():
                    for row in entry["signals"]:
                        sym = str(row.get("symbol") or "").upper()
                        if not sym:
                            continue
                        old = [r for r in listed.get(sym) or [] if isinstance(r, dict)
                               and (r.get("day"), r.get("side")) != (row.get("day"), row.get("side"))]
                        listed[sym] = sorted(old + [row], key=lambda r: str(r.get("day")), reverse=True)[:LISTED_ROWS]
                if len(listed) > LISTED_SYMBOLS:          # keep the stocks listed most recently
                    keep = sorted(listed, key=lambda k: str(listed[k][0].get("day")), reverse=True)[:LISTED_SYMBOLS]
                    listed = {k: listed[k] for k in keep}
                current["listed"] = listed
                if refused:
                    current["refused_until"] = time.time() + stv.REFUSED_COOLDOWN_SECONDS
                self._write(current)
        return error

    def market(self, side: str, day: str) -> dict | None:
        """The saved free market list of ``side`` for session ``day``, or None."""
        saved = (self._read().get("market") or {}).get(side.upper()) or {}
        return saved if saved.get("day") == day else None

    def market_missing(self, day: str) -> bool:
        return any(self.market(side, day) is None for side in ("BUY", "SELL"))

    def listed_state(self, symbol: str, as_of: str) -> dict | None:
        """The latest row of ``symbol`` shown in a saved free market list, dated on or
        before ``as_of`` (the plan of a stock bought from the list but not followed)."""
        for row in (self._read().get("listed") or {}).get(symbol.upper()) or []:
            if isinstance(row, dict) and str(row.get("day") or "") <= as_of:
                return row
        return None

    def state(self, symbol: str, as_of: str, cutoff: str | None = None) -> dict | None:
        """The latest signal of ``symbol`` dated on or before ``as_of`` (no later signal is seen);
        with ``cutoff`` (Vietnam ``YYYY-MM-DDTHH:MM``), only signals created before it."""
        for signal in self.signals(symbol):           # newest first
            if signal.get("day", "") > as_of:
                continue
            if cutoff is not None and str(signal.get("created") or "") >= cutoff:
                continue
            return signal
        return None


def _age_hours(stamp: str, now) -> float:
    try:
        then = datetime.strptime(stamp, "%Y-%m-%dT%H:%M").replace(tzinfo=now.tzinfo)
    except ValueError:
        return float("inf")
    return (now - then).total_seconds() / 3600


def plan(book, day: str, store: SignalStore, symbols, last_close: dict, *, slots: int = SLOTS) -> list:
    """Orders at the open of the session after ``day``, from the signals known by then (dated
    on or before ``day`` AND created before that open): sell every holding whose latest
    signal is SELL; buy, equal weight, the stocks with a live BUY of ``day`` itself, best
    strength first, while free slots and cash last. A slot is freed only once a sale has
    filled (a sale may wait for T+2 or a locked floor)."""
    from tradingagents.market import vn_rules
    from tradingagents.trading.ledger import Order

    orders = []
    cutoff = next_open_cutoff(day)
    open_sells = {o.symbol for o in book.orders if o.side == "SELL"}
    open_buys = {o.symbol for o in book.orders if o.side == "BUY"}
    proceeds = 0.0
    for symbol, pos in book.positions.items():
        signal = store.state(symbol, day, cutoff)
        if signal and signal["side"] == "SELL" and symbol not in open_sells:
            orders.append(Order(symbol, "SELL", pos.qty, None, day, f"trang BÁN {_ddmm(signal['day'])}"))
            proceeds += pos.qty * (last_close.get(symbol) or pos.last_price)
    free = slots - len(book.positions) - len(open_buys)
    candidates = []
    for symbol in dict.fromkeys(s.upper() for s in symbols):
        signal = store.state(symbol, day, cutoff)
        if (signal and signal["side"] == "BUY" and signal["day"] == day and signal.get("live")
                and symbol not in book.positions and symbol not in open_buys and last_close.get(symbol)):
            candidates.append((signal.get("strength_pct") or 0, symbol, signal))
    candidates.sort(key=lambda c: (-c[0], c[1]))
    nav = book.nav()
    available = book.cash - book.reserved() + proceeds * 0.997
    for _, symbol, signal in candidates:
        if free <= 0:
            break
        price = float(last_close[symbol])
        budget = min(nav * 0.98 / max(slots, 1), available)
        qty = vn_rules.round_lot(budget / (price * (1 + vn_rules.BUY_FEE_PCT / 100) * 1.02))   # room for the open
        if qty <= 0:
            continue
        orders.append(Order(symbol, "BUY", qty, None, day, f"tín hiệu {_ddmm(signal['day'])} @ {_fmt(signal['price'])}",
                            ref=price))
        available -= qty * price * 1.02
        free -= 1
    book.orders.extend(orders)
    return orders


def note(symbol: str, day: str, engine_action: str | None = None, store: SignalStore | None = None,
         *, fetch_if_stale: bool = True) -> str:
    """One line for ``/nhanh``: the site's latest signal for ``symbol`` (fetched when the saved
    one is stale) and whether it agrees with the bot. "" when the feature is off or unknown."""
    if not enabled():
        return ""
    store = store or SignalStore()
    if fetch_if_stale:
        try:
            store.refresh([symbol])
        except Exception as exc:  # noqa: BLE001 - only the extra line is lost
            logger.warning("Siêu tín hiệu %s: %s", symbol, exc)
    signal = store.state(symbol, day)
    if signal is None:
        error = store.last_error()
        return f"📡 {NAME}: chưa có tín hiệu cho mã này" + (f" ({error})" if error else "") + "."
    word = "MUA" if signal["side"] == "BUY" else "BÁN"
    extra = f", sức mạnh {signal['strength_pct']}%" if signal.get("strength_pct") is not None else ""
    agree = ""
    if engine_action:
        vi = ACTION_VI.get(engine_action, engine_action)
        long_side = engine_action in ("BUY", "HOLD")
        if signal["side"] == "BUY":
            agree = f" · hệ thống {vi}: cùng chiều" if long_side else f" · hệ thống {vi}: KHÁC chiều"
        else:
            agree = f" · hệ thống {vi}: cùng chiều" if not long_side else f" · hệ thống {vi}: KHÁC chiều"
    return (f"📡 {NAME} (tham khảo, không đổi quyết định của bot): {word} từ {_ddmm(signal['day'])} "
            f"@ {_fmt(signal['price'])}{extra}{agree}")


def replay(signals: list[dict], frame) -> dict:
    """The stock's BUY→SELL trades as the site counts them (the signal closes, no fees) and
    as they could have been traded (next session's open both ways, fees and tax).
    ``frame``: daily bars (Date/Open/Close). Returns {trades, site, real, live, regenerated}."""
    from tradingagents.market import vn_rules

    buy_cost = 1 + vn_rules.BUY_FEE_PCT / 100
    sell_keep = 1 - (vn_rules.SELL_FEE_PCT + vn_rules.SELL_TAX_PCT) / 100
    dates = [d.strftime("%Y-%m-%d") for d in frame["Date"]]
    opens = [float(v) for v in frame["Open"]]
    closes = [float(v) for v in frame["Close"]]

    def next_open(day):
        for i, d in enumerate(dates):
            if d > day and math.isfinite(opens[i]) and opens[i] > 0:
                return d, opens[i]
        return None, None

    ordered = sorted(signals, key=lambda s: (s["day"], s.get("created", "")))
    trades, entry = [], None
    for s in ordered:
        if s["side"] == "BUY" and entry is None:
            entry = s
        elif s["side"] == "SELL" and entry is not None:
            d_in, p_in = next_open(entry["day"])
            d_out, p_out = next_open(s["day"])
            real = (p_out * sell_keep) / (p_in * buy_cost) - 1 if p_in and p_out else None
            trades.append({"buy": entry["day"], "sell": s["day"], "site": s["price"] / entry["price"] - 1,
                           "real": real, "in": p_in, "out": p_out, "live": bool(entry.get("live"))})
            entry = None
    open_trade = None
    if entry is not None:
        d_in, p_in = next_open(entry["day"])
        open_trade = {"buy": entry["day"], "price": entry["price"], "in": p_in,
                      "now": closes[-1] if closes else None}
    site = real = 1.0
    for t in trades:
        if t["real"] is None:
            continue                        # not tradable yet (no session after the signal): left out of both
        site *= 1 + t["site"]
        real *= 1 + t["real"]
    created = sorted({s.get("created", "")[:10] for s in signals if s.get("created") and not s.get("live")})
    return {"trades": trades, "open": open_trade, "site": site - 1, "real": real - 1,
            "live": sum(1 for s in signals if s.get("live")), "regenerated": created[-1] if created else ""}


def symbol_report(symbol: str, store: SignalStore | None = None) -> str:
    """``/stv MÃ``: the latest signal and the site's history replayed honestly."""
    from tradingagents.dataflows.ohlcv import load_ohlcv
    from tradingagents.market import vn_rules

    if not enabled():
        return f"{NAME} đang tắt: đặt SIEUTINHIEU=1 trong .env rồi khởi động lại."
    store = store or SignalStore()
    _, error = store.refresh([symbol])
    signals = store.signals(symbol)
    if not signals:
        return f"📡 {NAME}: không lấy được tín hiệu {symbol}" + (f" ({error})." if error else ".")
    today = vn_rules.now_vn().strftime("%Y-%m-%d")
    frame = load_ohlcv(symbol, today, fill_gaps=False)
    r = replay(signals, frame)
    last = signals[0]
    lines = [f"📡 SIÊU TÍN HIỆU · {symbol} (tham khảo)",
             f"Tín hiệu gần nhất: {icon(last['side'])} {word(last['side'])} {_ddmm(last['day'])} @ {_fmt(last['price'])}"
             + (f" · mạnh {last['strength_pct']}%" if last.get("strength_pct") is not None else "")]
    if last["side"] == "BUY" and plan_text(last):
        lines.append(f"{INDENT}{plan_text(last)}")
    if not last.get("live"):
        lines.append(f"{INDENT}(từ lịch sử trang tạo lại, không phát trực tiếp)")
    if r["open"]:
        o = r["open"]
        if o["in"] and o["now"]:
            lines.append(f"Đang mở từ {_ddmm(o['buy'])}: vào thật {_fmt(o['in'])} → {_fmt(o['now'])} "
                         f"({pct((o['now'] / o['in'] - 1) * 100)})")
        else:
            lines.append(f"Đang mở từ {_ddmm(o['buy'])}: mua được ở giá mở cửa phiên tới.")
    done = [t for t in r["trades"] if t["real"] is not None]
    if done:
        n = len(done)
        site_wins = sum(1 for t in done if t["site"] > 0)
        real_wins = sum(1 for t in done if t["real"] > 0)
        lines += ["", f"Lịch sử trang trả về: {n} lệnh đã đóng",
                  f"• Theo giá tín hiệu: {pct(r['site'] * 100)} · thắng {site_wins}/{n}",
                  f"{INDENT}(đóng cửa ngày phát, chưa phí; số trang hiển thị còn cao hơn)",
                  f"• Làm được thật: {pct(r['real'] * 100)} · thắng {real_wins}/{n}",
                  f"{INDENT}(mở cửa phiên sau, có phí và thuế)",
                  f"{min(n, 5)} lệnh gần nhất (trang → thật):"]
        lines += [f"{INDENT}{_ddmm(t['buy'])}→{_ddmm(t['sell'])}: {pct(t['site'] * 100)} → {pct(t['real'] * 100)}"
                  for t in done[-5:]]
    if r["regenerated"]:
        lines += [f"⚠️ Lịch sử tới {_ddmm(r['regenerated'])} do trang tạo lại (backtest);",
                  f"{INDENT}chỉ {r['live']} tín hiệu phát đúng ngày. So sánh công bằng: /stv"]
    base = store.path.parent.parent
    lines.append("👀 Có trong watchlist Siêu tín hiệu." if symbol.upper() in Watchlist(base).get()
                 else f"👀 Chưa có trong watchlist Siêu tín hiệu: /stvtheodoi them {symbol.upper()}")
    return "\n".join(lines)


# ── message pieces: short lines that read well on a phone (Telegram and Zoom) ──
INDENT = "   "


def pct(value: float, digits: int = 1) -> str:
    return f"{value:+.{digits}f}%"


def icon(side: str) -> str:
    return "🟢" if side == "BUY" else "🔴"


def word(side: str) -> str:
    return "MUA" if side == "BUY" else "BÁN"


def plan_text(signal: dict) -> str:
    """The site's plan of a BUY: "vào 23.600–24.100 · mục tiêu 26.000 · cắt lỗ 22.400" (VND)."""
    parts = []
    if signal.get("entry_low") and signal.get("entry_high"):
        parts.append(f"vào {_fmt(signal['entry_low'])}–{_fmt(signal['entry_high'])}")
    if signal.get("tp1"):
        parts.append(f"mục tiêu {_fmt(signal['tp1'])}")
    if signal.get("sl"):
        parts.append(f"cắt lỗ {_fmt(signal['sl'])}")
    return " · ".join(parts)


def signal_line(symbol: str, signal: dict, *, plan: bool = False) -> str:
    """"🟢 GEX 23.850 · mạnh 90%" (+ the site's plan on a second line with ``plan``)."""
    head = f"{icon(signal['side'])} {symbol} {_fmt(signal['price'])}"
    if signal.get("strength_pct") is not None:
        head += f" · mạnh {signal['strength_pct']}%"
    body = plan_text(signal) if plan and signal["side"] == "BUY" else ""
    return head + (f"\n{INDENT}{body}" if body else "")


def rows(codes, per_row: int = 10) -> list[str]:
    """Stock codes, ``per_row`` a line."""
    codes = list(codes)
    return [", ".join(codes[i:i + per_row]) for i in range(0, len(codes), per_row)]


def watchlist_text(watchlist: Watchlist) -> str:
    """``/stvtheodoi``: the list, ten codes a line, and how to change it."""
    codes = watchlist.get()
    lines = [f"👀 WATCHLIST SIÊU TÍN HIỆU · {len(codes)}/{WATCHLIST_MAX} mã ({watchlist.source()})",
             "Riêng cho Siêu tín hiệu, không đổi watchlist /theodoi của bot.", ""]
    lines += rows(codes) or ["(trống)"]
    lines += ["", "Bỏ: /stvtheodoi bo NVL,PDR", f"Thêm (khi còn chỗ, tối đa {WATCHLIST_MAX}): /stvtheodoi them HAG",
              "Đặt cả danh sách: /stvtheodoi dat MÃ1,MÃ2,...", "Danh sách mặc định: /stvtheodoi macdinh",
              "Hoàn tác thay đổi vừa rồi: /stvtheodoi hoantac"]
    return "\n".join(lines)
