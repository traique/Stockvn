"""AI providers shared by the bots of one server: the stock bot and the altcoin bot it runs.

The same file is tradingagents/llm_clients/ai_share.py (the stock) and altcoin/bot/ai_share.py
(the altcoin), byte for byte (tests/suites/suite_ai_share.py checks it): the two bots run in two
processes and neither imports the other's package. Standard library only.

Each bot writes its own file, <AI_SHARE_DIR>/<who>.json (replaced whole, atomically), and only
reads the others'. Nothing is shared when AI_SHARE_DIR is not set (a bot running alone).

A provider is shared with another bot when both reach the same endpoint with the same key: the
file keeps a fingerprint of the two (a hash, never the key). For a shared provider:
- the requests of the day (Vietnam day) of both bots count against the strictest daily cap
  either sets (OpenRouter's free tier counts per key, not per bot);
- a rest one bot puts on it (rate limited, key refused, endpoint down) holds for the other
  until it ends, so the other does not hit a provider that has just refused.

``extra`` carries what a bot wants the others to show (its version, its AI calls of the day,
the model that answered last...): display only.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

VERSION = 1
FRESH = 3 * 86400                 # seconds: an older file (a bot gone) is ignored
_VN = timezone(timedelta(hours=7))


def fingerprint(base_url, key) -> str:
    """The endpoint and key as 16 hex characters: equal for the same pair, never the key."""
    raw = f"{str(base_url or '').strip().rstrip('/').lower()}\n{str(key or '').strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def today(now: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if now is None else now, _VN).strftime("%Y-%m-%d")


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("v") != VERSION or not isinstance(data.get("providers"), dict):
        return None
    return data


class Ledger:
    """This bot's file, and what the others' say. Every method is safe from several threads."""

    def __init__(self, who: str, directory: str | os.PathLike | None = None):
        where = os.environ.get("AI_SHARE_DIR", "") if directory is None else directory
        self.who = who
        self.dir = Path(where) if str(where).strip() else None
        self._lock = threading.Lock()
        self._data = {"v": VERSION, "who": who, "ts": 0.0, "day": today(), "providers": {}, "extra": {}}
        mine = _read(self.path) if self.dir else None
        if mine:                                    # the day's counts and the rests survive a restart
            self._data["day"] = str(mine.get("day") or today())
            self._data["providers"] = {str(k): dict(v) for k, v in mine["providers"].items() if isinstance(v, dict)}
            self._roll()

    @property
    def enabled(self) -> bool:
        return self.dir is not None

    @property
    def path(self) -> Path | None:
        return self.dir / f"{self.who}.json" if self.dir else None

    # ── the others ──
    def peers(self) -> list[dict]:
        """The other bots' files, fresh ones only (``who`` set from the file name if missing)."""
        if not self.dir:
            return []
        try:
            files = sorted(self.dir.glob("*.json"))
        except OSError:
            return []
        out, now = [], time.time()
        for f in files:
            if f.stem == self.who:
                continue
            data = _read(f)
            try:
                fresh = data is not None and now - float(data.get("ts") or 0) < FRESH
            except (TypeError, ValueError):
                fresh = False
            if fresh:
                data["who"] = str(data.get("who") or f.stem)
                out.append(data)
        return out

    def shared(self, name: str, fp: str) -> list[tuple[str, dict, bool]]:
        """(who, its entry, its day is today) for each other bot on the same endpoint and key."""
        if not self.dir or not fp:
            return []
        day = today()
        return [(p["who"], e, p.get("day") == day) for p in self.peers()
                for e in [p["providers"].get(name)] if isinstance(e, dict) and e.get("key") == fp]

    def check(self, name: str, fp: str, cap: int = 0) -> str | None:
        """None: this bot may send to ``name`` now; else why not (``peer_rest``, ``cap_reached``)."""
        return self.peer_rest(name, fp) or self.cap_reached(name, fp, cap)

    def peer_rest(self, name: str, fp: str) -> str | None:
        """Why another bot rests the shared provider now, or None."""
        now = time.time()
        for who, entry, _ in self.shared(name, fp):
            until = _float(entry.get("rest_until"))
            if until > now:
                return f"{who} đang cho nghỉ {int(until - now)} giây ({str(entry.get('reason') or '?')[:80]})"
        return None

    def cap_reached(self, name: str, fp: str, cap: int = 0) -> str | None:
        """Why the shared cap of the day is reached, or None. The cap: the strictest of this
        bot's (``cap``) and the others' on the same endpoint and key; 0 = none."""
        shared = self.shared(name, fp)
        if not shared:
            return None
        caps = [c for c in [_int(cap)] + [_int(e.get("cap")) for _, e, _ in shared] if c > 0]
        if not caps:
            return None
        used = self.calls(name) + sum(_int(e.get("calls")) for _, e, same_day in shared if same_day)
        if used < min(caps):
            return None
        return (f"hết hạn mức chung {used}/{min(caps)} lượt hôm nay "
                f"(cùng khoá với {', '.join(sorted({w for w, _, _ in shared}))})")

    def shared_cap(self, name: str, fp: str, cap: int = 0) -> int:
        """The cap that holds for ``name`` (0 = none): this bot's, or the strictest shared one."""
        caps = [c for c in [_int(cap)] + [_int(e.get("cap")) for _, e, _ in self.shared(name, fp)] if c > 0]
        return min(caps) if caps else 0

    def peer_calls(self, name: str, fp: str) -> int:
        """Today's requests of the other bots to the shared provider."""
        return sum(_int(e.get("calls")) for _, e, same_day in self.shared(name, fp) if same_day)

    # ── this bot ──
    def calls(self, name: str) -> int:
        with self._lock:
            self._roll()
            return _int((self._data["providers"].get(name) or {}).get("calls"))

    def register(self, name: str, fp: str, cap: int = 0) -> None:
        """This bot uses ``name`` with that endpoint and key (and its own daily cap): written, so
        the others see it before its first request. Another key: the count starts again."""
        with self._lock:
            self._roll()
            entry = self._data["providers"].setdefault(name, {"calls": 0, "rest_until": 0, "reason": ""})
            if entry.get("key") != fp:
                entry.update(calls=0, rest_until=0, reason="")
            entry.update(key=fp, cap=_int(cap))
            self._save()

    def count(self, name: str, fp: str, n: int = 1) -> None:
        """``n`` requests sent (-1: one given back, it never reached the provider)."""
        with self._lock:
            self._roll()
            entry = self._data["providers"].setdefault(name, {"calls": 0, "rest_until": 0, "reason": "", "cap": 0})
            entry["key"] = fp
            entry["calls"] = max(0, _int(entry.get("calls")) + n)
            self._save()

    def rest(self, name: str, fp: str, seconds: float, reason: str) -> None:
        """This bot saw ``name`` refuse (provider-wide): the others leave it alone as long."""
        if seconds <= 0:
            return
        with self._lock:
            entry = self._data["providers"].setdefault(name, {"calls": 0, "rest_until": 0, "reason": "", "cap": 0})
            entry["key"] = fp
            entry["rest_until"] = max(_float(entry.get("rest_until")), time.time() + seconds)
            entry["reason"] = str(reason)[:120]
            self._save()

    def clear_rest(self, name: str) -> None:
        """``name`` answered this bot again: its rest is over."""
        with self._lock:
            entry = self._data["providers"].get(name)
            if entry and _float(entry.get("rest_until")) > 0:
                entry.update(rest_until=0, reason="")
                self._save()

    def set_extra(self, **values) -> None:
        with self._lock:
            self._data["extra"].update(values)
            self._save()

    def _roll(self) -> None:
        day = today()
        if self._data["day"] != day:
            self._data["day"] = day
            for entry in self._data["providers"].values():
                entry["calls"] = 0

    def _save(self) -> None:
        if not self.dir:
            return
        self._data["ts"] = time.time()
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.dir / f".{self.who}.{os.getpid()}.{threading.get_ident()}.tmp"
            tmp.write_text(json.dumps(self._data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass                                    # sharing is a help, never a reason to fail a request


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
