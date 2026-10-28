"""The bot's history store: SQLite on the host (``data/bot.db``), or a shared PostgreSQL
when ``DATABASE_URL`` is set (Supabase / Neon free tier), as the OKX bot does
(altcoin_bot ``bot/storage.py``): one thin adapter runs the same ``?`` SQL on both.

It does not replace the JSON state files: the paper books, the Tài sản journal and the
settings stay where they are, the source of truth. It keeps what nothing kept before
from one day to the next:

- ``signals``: the Quant Engine's decision on EVERY stock of every screen, the gate that
  kept it from BUY, its decision were the market not risk-off, then its shadow result
  (the price 5/10/20 sessions later; whether the plan would have hit its stop or target),
  which tells whether the gates are right (vnbot.chandoan).
- ``scans``: one row a screen: stocks seen, actions, gates, the stocks close to a BUY.
- ``market``: VNINDEX each session: the parts of the engine's regime, and the MA filters
  of the defensive and momentum books.
- ``equity``, ``fills``: each book's NAV per session and its fills (a book file keeps
  only its last 500 fills).
- ``events``: the activity log (INFO and up), secrets masked (vnbot.eventlog).
- ``kv``: loose values.

The store never stops the bot: the recording paths call it through ``safe`` and a
failure only logs a warning. Columns are added by ``_migrate`` from ``SCHEMA`` at start,
so a later version adds a column by adding it here.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

DB_FILE = "bot.db"
KEEP_DAYS = 730            # signals, scans, market, equity: two years (STORE_KEEP_DAYS)
EVENTS_DAYS = 14           # the activity log (EVENTS_KEEP_DAYS)

# table -> (columns as (name, kind), unique key). kind: id | text | real | int.
SCHEMA: dict[str, tuple[tuple[tuple[str, str], ...], tuple[str, ...]]] = {
    "kv": ((("k", "text"), ("v", "text"), ("ts", "real")), ("k",)),
    "signals": ((
        ("id", "id"), ("day", "text"), ("symbol", "text"), ("source", "text"), ("ts", "real"),
        ("action", "text"), ("confidence", "real"), ("setup", "text"), ("bias", "text"), ("regime", "text"),
        ("risk", "text"), ("quality", "text"), ("gate", "text"), ("gates", "text"), ("near", "text"),
        ("price", "real"), ("entry_low", "real"), ("entry_high", "real"), ("stop", "real"), ("target", "real"),
        ("target2", "real"), ("rr", "real"), ("holding", "int"),
        ("alt_action", "text"), ("alt_confidence", "real"), ("alt_gate", "text"), ("alt_stop", "real"),
        ("alt_target", "real"), ("alt_entry_high", "real"), ("alt_rr", "real"),
        ("reasons", "text"),
        # the shadow result, filled by vnbot.chandoan.resolve
        ("status", "text"), ("entry_px", "real"), ("ret_5", "real"), ("ret_10", "real"), ("ret_20", "real"),
        ("vni_5", "real"), ("vni_10", "real"), ("vni_20", "real"),
        ("plan", "text"), ("plan_outcome", "text"), ("plan_r", "real"), ("plan_days", "int"), ("resolved_ts", "real"),
    ), ("day", "symbol", "source")),
    "scans": ((
        ("id", "id"), ("day", "text"), ("kind", "text"), ("ts", "real"), ("scanned", "int"), ("buy", "int"),
        ("near", "int"), ("regime", "text"), ("actions", "text"), ("gates", "text"), ("near_list", "text"),
        ("buys", "text"), ("errors", "text"), ("note", "text"),
    ), ("day", "kind")),
    "market": ((
        ("day", "text"), ("ts", "real"), ("close", "real"), ("sma20", "real"), ("sma50", "real"), ("sma100", "real"),
        ("sma200", "real"), ("alignment", "text"), ("adx", "real"), ("di_plus", "real"), ("di_minus", "real"),
        ("adx_down", "int"), ("dist_days", "int"), ("dist_threshold", "int"), ("regime", "text"), ("why", "text"),
        ("factor_on", "int"), ("momentum_on", "int"),
    ), ("day",)),
    "equity": ((
        ("day", "text"), ("book", "text"), ("nav", "real"), ("cash", "real"), ("invested_pct", "real"),
        ("positions", "int"), ("benchmark", "real"), ("ts", "real"),
    ), ("day", "book")),
    "fills": ((
        ("id", "id"), ("book", "text"), ("day", "text"), ("symbol", "text"), ("side", "text"), ("qty", "real"),
        ("price", "real"), ("cost", "real"), ("pnl", "real"), ("reason", "text"), ("ts", "real"),
    ), ("book", "day", "symbol", "side", "qty", "price")),
    "events": ((("id", "id"), ("ts", "real"), ("level", "text"), ("src", "text"), ("msg", "text")), ()),
}
INDEXES = (
    ("ix_signals_day", "signals", "day"), ("ix_signals_status", "signals", "status"),
    ("ix_signals_symbol", "signals", "symbol"), ("ix_events_ts", "events", "ts"), ("ix_scans_day", "scans", "day"),
)
# What the web page offers as CSV / JSON (vnbot.chandoan_web), oldest first.
EXPORTS = {
    "signals": "SELECT * FROM signals ORDER BY day, symbol, source",
    "scans": "SELECT * FROM scans ORDER BY day, kind",
    "market": "SELECT * FROM market ORDER BY day",
    "equity": "SELECT * FROM equity ORDER BY day, book",
    "fills": "SELECT * FROM fills ORDER BY day, id",
    "events": "SELECT * FROM events ORDER BY ts",
}
_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")


def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(os.environ.get(name) or default)
    except ValueError:
        value = default
    return max(lo, min(hi, value))


def keep_days() -> int:
    return _int_env("STORE_KEEP_DAYS", KEEP_DAYS, 60, 3650)


def events_days() -> int:
    return _int_env("EVENTS_KEEP_DAYS", EVENTS_DAYS, 1, 90)


def _masked_url(url: str) -> str:
    """``postgresql://user:***@host:port/db`` - the password never leaves this module."""
    return re.sub(r"(://[^:/@\s]+:)[^@\s]+@", r"\1***@", url)


class _DB:
    """One connection, one lock: the same ``?`` SQL on SQLite and Postgres."""

    def __init__(self, url: str, path: Path):
        self.url, self.path = url, path
        self.pg = bool(url)
        self.conn = None
        self.lock = threading.RLock()
        self._connect()

    def _connect(self) -> None:
        if self.pg:
            import psycopg
            from psycopg.rows import dict_row

            url = self.url
            if "sslmode=" not in url and "host=/" not in url:
                url += ("&" if "?" in url else "?") + "sslmode=require"
            # prepare_threshold=None: Supabase's pooler (PgBouncer) has no server-side prepared statements.
            self.conn = psycopg.connect(url, autocommit=True, row_factory=dict_row, connect_timeout=20,
                                        prepare_threshold=None, application_name="tradingagents-vn")
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=10, isolation_level=None)
            self.conn.row_factory = sqlite3.Row
            for pragma in ("PRAGMA journal_mode=WAL", "PRAGMA synchronous=NORMAL", "PRAGMA busy_timeout=5000"):
                try:
                    self.conn.execute(pragma)
                except sqlite3.Error:
                    pass

    def sql(self, query: str) -> str:
        return query.replace("?", "%s") if self.pg else query

    def _lost(self, exc: BaseException) -> bool:
        """A dropped connection (worth one reconnect), not a bad query."""
        if self.pg:
            try:
                import psycopg

                return isinstance(exc, (psycopg.OperationalError, psycopg.InterfaceError))
            except ImportError:      # pragma: no cover - pg without psycopg never connects
                return False
        return isinstance(exc, sqlite3.ProgrammingError) and "closed" in str(exc).lower()

    def _retry(self, work):
        with self.lock:
            for attempt in (1, 2):
                try:
                    return work()
                except Exception as exc:
                    if attempt == 2 or not self._lost(exc):
                        raise
                    logger.warning("Store: connection lost (%s); reconnecting", type(exc).__name__)
                    try:
                        self.conn.close()
                    except Exception:  # noqa: BLE001 - it is gone anyway
                        pass
                    self._connect()

    def run(self, query: str, params=(), *, fetch: bool = False):
        def work():
            cur = self.conn.execute(self.sql(query), tuple(params))
            return [dict(r) for r in cur.fetchall()] if fetch else cur.rowcount
        return self._retry(work)

    def many(self, query: str, rows: list[tuple]) -> None:
        """One statement for many rows, in one transaction."""
        if not rows:
            return

        def work():
            if self.pg:
                with self.conn.transaction(), self.conn.cursor() as cur:
                    cur.executemany(self.sql(query), rows)
            elif self.conn.in_transaction:
                self.conn.executemany(query, rows)
            else:
                self.conn.execute("BEGIN")
                try:
                    self.conn.executemany(query, rows)
                except Exception:
                    self.conn.execute("ROLLBACK")
                    raise
                self.conn.execute("COMMIT")
        self._retry(work)

    def close(self) -> None:
        with self.lock:
            try:
                self.conn.close()
            except Exception:  # noqa: BLE001
                pass


class Store:
    def __init__(self, data_dir: Path | str, url: str | None = None):
        self.data_dir = Path(data_dir)
        url = (os.environ.get("DATABASE_URL", "") if url is None else url).strip()
        self.path = self.data_dir / DB_FILE
        self.fallback = ""
        try:
            self.db = _DB(url, self.path)
        except Exception as exc:  # noqa: BLE001 - no Postgres (driver, network): the host's SQLite instead
            if not url:
                raise
            self.fallback = f"{type(exc).__name__}: {str(exc)[:160]}"
            logger.warning("Store: PostgreSQL unavailable (%s); using SQLite %s", _masked_url(self.fallback), self.path)
            self.db = _DB("", self.path)
        self.pg = self.db.pg
        self.url = _masked_url(url) if self.pg else ""
        self._migrate()
        logger.info("Store: %s", self.describe())

    # ── schema ──
    def _type(self, kind: str) -> str:
        if kind == "id":
            return "BIGSERIAL PRIMARY KEY" if self.pg else "INTEGER PRIMARY KEY AUTOINCREMENT"
        return {"text": "TEXT", "int": "BIGINT" if self.pg else "INTEGER",
                "real": "DOUBLE PRECISION" if self.pg else "REAL"}[kind]

    def _migrate(self) -> None:
        for table, (cols, unique) in SCHEMA.items():
            body = ", ".join(f"{name} {self._type(kind)}" for name, kind in cols)
            if unique:
                has_id = any(kind == "id" for _, kind in cols)
                body += f", {'UNIQUE' if has_id else 'PRIMARY KEY'} ({', '.join(unique)})"
            self.db.run(f"CREATE TABLE IF NOT EXISTS {table} ({body})")
            have = self._columns(table)
            for name, kind in cols:
                if name not in have and kind != "id":
                    self.db.run(f"ALTER TABLE {table} ADD COLUMN {name} {self._type(kind)}")
        for name, table, col in INDEXES:
            self.db.run(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({col})")

    def _columns(self, table: str) -> set[str]:
        if self.pg:
            rows = self.db.run("SELECT column_name AS name FROM information_schema.columns WHERE table_name = ?",
                               (table,), fetch=True)
        else:
            rows = self.db.run(f"PRAGMA table_info({table})", fetch=True)
        return {r["name"] for r in rows}

    @staticmethod
    def _check(table: str, names) -> None:
        known = {n for n, _ in SCHEMA[table][0]}
        bad = [n for n in names if n not in known or not _NAME.match(n)]
        if bad:
            raise ValueError(f"unknown column(s) of {table}: {bad}")

    # ── generic writes ──
    def upsert(self, table: str, rows: list[dict], *, keep: tuple[str, ...] = ()) -> None:
        """Insert ``rows``, or update the existing row with the same unique key; the
        columns in ``keep`` (and every column a row leaves out) keep their stored value."""
        if not rows:
            return
        names = list(dict.fromkeys(k for r in rows for k in r))
        self._check(table, names)
        unique = SCHEMA[table][1]
        update = [n for n in names if n not in unique and n not in keep]
        sql = f"INSERT INTO {table} ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})"
        if unique:
            sql += f" ON CONFLICT ({', '.join(unique)}) DO " + (
                "UPDATE SET " + ", ".join(f"{n} = excluded.{n}" for n in update) if update else "NOTHING")
        self.db.many(sql, [tuple(_plain(r.get(n)) for n in names) for r in rows])

    def insert_new(self, table: str, rows: list[dict]) -> None:
        """Insert ``rows``, skipping any whose unique key is already stored."""
        if not rows:
            return
        names = list(dict.fromkeys(k for r in rows for k in r))
        self._check(table, names)
        sql = f"INSERT INTO {table} ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})"
        if SCHEMA[table][1]:
            sql += " ON CONFLICT DO NOTHING"
        self.db.many(sql, [tuple(_plain(r.get(n)) for n in names) for r in rows])

    def update(self, table: str, row_id: int, fields: dict) -> None:
        self._check(table, fields)
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.db.run(f"UPDATE {table} SET {sets} WHERE id = ?", [_plain(v) for v in fields.values()] + [row_id])

    def update_many(self, table: str, items: list[tuple[int, dict]]) -> None:
        """Many ``update``s in one transaction per set of columns (one round trip to a remote
        Postgres instead of one per row)."""
        groups: dict[tuple[str, ...], list[tuple]] = {}
        for row_id, fields in items:
            keys = tuple(fields)
            groups.setdefault(keys, []).append(tuple(_plain(fields[k]) for k in keys) + (row_id,))
        for keys, rows in groups.items():
            self._check(table, keys)
            self.db.many(f"UPDATE {table} SET {', '.join(f'{k} = ?' for k in keys)} WHERE id = ?", rows)

    def select(self, sql: str, params=()) -> list[dict]:
        return self.db.run(sql, params, fetch=True)

    # ── kv ──
    def get(self, key: str, default=None):
        rows = self.db.run("SELECT v FROM kv WHERE k = ?", (key,), fetch=True)
        if not rows:
            return default
        try:
            return json.loads(rows[0]["v"])
        except (TypeError, ValueError):
            return default

    def set(self, key: str, value) -> None:
        self.upsert("kv", [{"k": key, "v": json.dumps(value, ensure_ascii=False), "ts": time.time()}])

    # ── signals ──
    def add_signals(self, rows: list[dict]) -> None:
        """One row per (day, symbol, source); a new decision for the same key replaces the
        old one and is resolved again (``status`` PENDING)."""
        cleared = {c: None for c in ("entry_px", "ret_5", "ret_10", "ret_20", "vni_5", "vni_10", "vni_20", "plan",
                                     "plan_outcome", "plan_r", "plan_days", "resolved_ts")}
        rows = [{**r, **cleared, "status": "PENDING", "ts": r.get("ts") or time.time()} for r in rows]
        for start in range(0, len(rows), 2000):
            self.upsert("signals", rows[start:start + 2000])

    def signals(self, *, day: str | None = None, symbol: str | None = None, since: str | None = None,
                source: str | None = None, limit: int = 500) -> list[dict]:
        sql, params = "SELECT * FROM signals WHERE 1 = 1", []
        for col, op, value in (("day", "=", day), ("symbol", "=", symbol), ("day", ">=", since), ("source", "=", source)):
            if value:
                sql += f" AND {col} {op} ?"
                params.append(value)
        sql += " ORDER BY day DESC, symbol LIMIT ?"
        params.append(int(limit))
        return self.db.run(sql, params, fetch=True)

    PENDING_COLS = "id, day, symbol, action, entry_high, stop, target, alt_action, alt_entry_high, alt_stop, alt_target"

    def pending_signals(self, before_day: str, limit: int = 5000, after_id: int = 0) -> list[dict]:
        """The rows still waiting for their shadow result, by id (``after_id`` pages through
        them), with only the columns the result needs."""
        return self.db.run(f"SELECT {self.PENDING_COLS} FROM signals WHERE status = ? AND day < ? AND id > ? "
                           "ORDER BY id LIMIT ?", ("PENDING", before_day, int(after_id), int(limit)), fetch=True)

    OUTCOME_COLS = ("day, symbol, source, action, bias, gate, near, ret_5, ret_10, ret_20, vni_5, vni_10, vni_20, "
                    "plan_outcome, plan_r")

    def outcome_rows(self, since: str) -> list[dict]:
        """The statistics' rows since ``since``: new positions only (not a held stock), one
        per (day, symbol) - the screen's first, then a replay's, then the trading run's -
        only the columns the statistics read (a year of VN100 is ~30.000 rows)."""
        cols = self.OUTCOME_COLS
        return self.db.run(
            f"SELECT {cols} FROM (SELECT {cols}, ROW_NUMBER() OVER (PARTITION BY day, symbol ORDER BY "
            "CASE source WHEN 'screen' THEN 0 WHEN 'replay' THEN 1 ELSE 2 END) AS rn FROM signals "
            "WHERE (holding = 0 OR holding IS NULL) AND day >= ?) ranked WHERE rn = 1", (since,), fetch=True)

    def pending_count(self) -> int:
        return int(self.db.run("SELECT COUNT(*) AS n FROM signals WHERE status = ?", ("PENDING",), fetch=True)[0]["n"])

    def days(self, table: str = "signals", limit: int = 60) -> list[str]:
        self._check(table, ["day"])
        rows = self.db.run(f"SELECT DISTINCT day FROM {table} ORDER BY day DESC LIMIT ?", (int(limit),), fetch=True)
        return [r["day"] for r in rows]

    # ── scans, market, equity, fills ──
    def add_scan(self, row: dict) -> None:
        row = dict(row)
        row.setdefault("ts", time.time())
        for key in ("actions", "gates", "near_list", "buys", "errors"):
            if key in row and not isinstance(row[key], str):
                row[key] = json.dumps(row[key], ensure_ascii=False)
        self.upsert("scans", [row])

    def scans(self, *, kind: str | None = None, limit: int = 60) -> list[dict]:
        sql, params = "SELECT * FROM scans", []
        if kind:
            sql += " WHERE kind = ?"
            params.append(kind)
        rows = self.db.run(sql + " ORDER BY day DESC, ts DESC LIMIT ?", params + [int(limit)], fetch=True)
        for r in rows:
            for key in ("actions", "gates", "near_list", "buys", "errors"):
                try:
                    r[key] = json.loads(r.get(key) or "null")
                except (TypeError, ValueError):
                    pass
        return rows

    def add_market(self, row: dict) -> None:
        self.upsert("market", [{**row, "ts": time.time()}])

    def market(self, limit: int = 60) -> list[dict]:
        return self.db.run("SELECT * FROM market ORDER BY day DESC LIMIT ?", (int(limit),), fetch=True)

    def add_equity(self, rows: list[dict]) -> None:
        self.upsert("equity", [{**r, "ts": time.time()} for r in rows])

    def add_fills(self, rows: list[dict]) -> None:
        self.insert_new("fills", [{**r, "ts": time.time()} for r in rows])

    # ── events ──
    def add_events(self, rows: list[tuple]) -> None:
        self.db.many("INSERT INTO events (ts, level, src, msg) VALUES (?, ?, ?, ?)", rows)

    def events(self, *, limit: int = 300, level: str | None = None, query: str | None = None) -> list[dict]:
        sql, params = "SELECT ts, level, src, msg FROM events WHERE 1 = 1", []
        if level:
            sql += " AND level = ?"
            params.append(level)
        if query:
            sql += " AND msg LIKE ?"
            params.append(f"%{query}%")
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(int(limit))
        return self.db.run(sql, params, fetch=True)

    # ── upkeep ──
    def prune(self, today: str | None = None) -> dict[str, int]:
        """Drop events older than ``events_days()`` and history older than ``keep_days()``."""
        from datetime import date, timedelta

        now = time.time()
        cutoff = ((date.fromisoformat(today) if today else date.today()) - timedelta(days=keep_days())).isoformat()
        done = {"events": self.db.run("DELETE FROM events WHERE ts < ?", (now - events_days() * 86400,))}
        for table in ("signals", "scans", "market", "equity"):
            done[table] = self.db.run(f"DELETE FROM {table} WHERE day < ?", (cutoff,))
        return done

    def counts(self) -> dict[str, int]:
        return {t: int(self.db.run(f"SELECT COUNT(*) AS n FROM {t}", fetch=True)[0]["n"]) for t in SCHEMA}

    def export(self, name: str) -> list[dict]:
        return self.db.run(EXPORTS[name], fetch=True)

    def size_bytes(self) -> int | None:
        if self.pg:
            return None
        return sum(p.stat().st_size for p in self.data_dir.glob(DB_FILE + "*") if p.is_file())

    def describe(self) -> str:
        if self.pg:
            return f"PostgreSQL dùng chung ({self.url.split('@')[-1].split('?')[0]})"
        size = self.size_bytes() or 0
        tail = f"; DATABASE_URL lỗi, dùng SQLite: {self.fallback}" if self.fallback else ""
        return f"SQLite {self.path.name} ({size / 1e6:.1f} MB){tail}"

    def close(self) -> None:
        self.db.close()


def _plain(value):
    """A value SQLite and Postgres both take: bools as 0/1, lists/dicts as JSON, numpy
    scalars as floats, NaN/inf as NULL."""
    if isinstance(value, bool):          # before int: Postgres refuses a boolean in a BIGINT column
        return int(value)
    if value is None or isinstance(value, (str, int)):
        return value
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, ensure_ascii=False)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return number if number == number and abs(number) != float("inf") else None


# ── the process's store ──
_store: Store | None = None
_store_dir: Path | None = None
_open_lock = threading.Lock()


def enabled() -> bool:
    return (os.environ.get("STORE") or "1").strip().lower() not in ("0", "false", "no", "off")


def open_store(data_dir: Path | str) -> Store | None:
    """The process's store under ``data_dir`` (opened once); None when STORE=0 or it cannot open."""
    global _store, _store_dir
    if not enabled():
        return None
    directory = Path(data_dir)
    with _open_lock:
        if _store is not None and _store_dir == directory:
            return _store
        try:
            store = Store(directory)
        except Exception as exc:  # noqa: BLE001 - no history store: the bot runs as before
            logger.warning("Store: cannot open %s (%s); history is not recorded", directory / DB_FILE, exc)
            return None
        if _store is not None:
            _store.close()
        _store, _store_dir = store, directory
        return store


def current() -> Store | None:
    """The store opened by ``open_store`` (None before, or when off)."""
    return _store


def reset() -> None:
    """Close the process's store (tests)."""
    global _store, _store_dir
    with _open_lock:
        if _store is not None:
            _store.close()
        _store = _store_dir = None


def safe(what: str, work, default=None):
    """Run a store write/read; a failure is logged, never raised (the store never stops the bot)."""
    try:
        return work()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Store: %s failed: %s: %s", what, type(exc).__name__, str(exc)[:200])
        return default
