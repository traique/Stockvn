"""A copy of the stock's figures on a Google Sheets spreadsheet (service account), for the
weekly review on a phone: tabs ``ck_nav``, ``ck_trades``, ``ck_shadow``, ``ck_ai_pick``,
``ck_summary``. The altcoin writes its own tabs of the same spreadsheet (``altcoin/bot/sheets.py``,
the same design), so one side failing never stops the other.

- ``SHEETS_ID`` (the spreadsheet's id, from its URL) and ``SHEETS_SA_JSON`` (the service
  account's key file: its JSON, or that JSON in base64). Either missing: the feature is off,
  said once at start, the bot runs as before. Share the spreadsheet with the account's
  ``client_email`` (editor).
- Auth: an RS256 JWT signed with ``cryptography`` (a dependency of the bot already; no Google
  library, the disk of a small host is short), exchanged at oauth2.googleapis.com for a token
  kept ~50 minutes. Writes: the Sheets REST API with ``requests``; a missing tab is added
  (``batchUpdate`` addSheet) with its header row.
- Appends only, one call per tab per cycle (many rows). Every row has a key; the keys
  written per tab are kept (``data/results/sheets_cursor.json``) so a rerun never writes a
  row twice. A failed tab keeps its cursor and is tried again next cycle; nothing here raises
  into the scheduler. The spreadsheet is never read but for its list of tabs: no tab
  (``proposals`` included) is read back into the bot.
- Every text cell goes through ``vnbot.eventlog.redact``.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

SCOPE = "https://www.googleapis.com/auth/spreadsheets"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://sheets.googleapis.com/v4/spreadsheets/"
TIMEOUT = 30
TOKEN_SECONDS = 50 * 60
CURSOR_FILE = "sheets_cursor.json"
MAX_ROWS_PER_CALL = 2000          # one append per tab per cycle; the rest waits for the next
KEYS_KEPT = 4000                  # keys remembered per tab (the sheet itself holds every row)
EXPORT_TIME = "18:05"             # session days, after the late bars (18:00) and the AI pick's retries
NAME = "Bảng tính BotLab"

# tab -> header row (the first column is the row's key)
TABS: dict[str, tuple[str, ...]] = {
    "ck_nav": ("key", "day", "book", "nav", "return_pct", "vnindex", "etf_nav", "etf_nav_net", "cash", "positions"),
    "ck_trades": ("key", "day", "book", "symbol", "side", "qty", "price", "cost", "pnl", "pnl_pct", "reason"),
    "ck_shadow": ("key", "day", "symbol", "group", "action", "gate", "near", "source", "ret_10", "vni_10", "excess_10",
                  "plan", "plan_outcome", "plan_r", "resolved"),
    "ck_ai_pick": ("key", "day", "pick", "confidence", "r10_pick", "r10_mean", "excess", "n_candidates", "beat",
                   "ranking", "avoid", "model"),
    "ck_summary": ("key", "day", "written_at", "version", "build", "params_fingerprint", "snapshot_day",
                   "params_changed", "auto_calibrate", "breakout_buy", "factor_nav", "factor_ret", "engine_nav",
                   "engine_ret", "momentum_nav", "momentum_ret", "vnindex_ret", "etf_nav", "etf_ret", "etf_ret_net",
                   "closed_trades", "shadow_resolved", "ai_pick_sessions", "warnings"),
}


# ─── settings ────────────────────────────────────────────────────────────────

def sheet_id() -> str:
    return (os.environ.get("SHEETS_ID") or "").strip()


def account_json() -> dict | None:
    """The service account's key, from ``SHEETS_SA_JSON`` (JSON or base64 of it); None when unusable."""
    raw = (os.environ.get("SHEETS_SA_JSON") or "").strip()
    if not raw:
        return None
    text = raw
    if not raw.startswith("{"):
        try:
            text = base64.b64decode(raw, validate=False).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict) or not data.get("client_email") or not data.get("private_key"):
        return None
    return data


def why_off() -> str:
    """Why the export is off ("" when on)."""
    if not sheet_id():
        return "thiếu SHEETS_ID"
    if not (os.environ.get("SHEETS_SA_JSON") or "").strip():
        return "thiếu SHEETS_SA_JSON"
    if account_json() is None:
        return "SHEETS_SA_JSON không phải JSON của service account (cần client_email và private_key)"
    return ""


def enabled() -> bool:
    return not why_off()


# ─── auth ────────────────────────────────────────────────────────────────────

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def sign_jwt(account: dict, *, now: float | None = None, scope: str = SCOPE) -> str:
    """The RS256 assertion of a service account (RFC 7523), signed with ``cryptography``."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    issued = int(now if now is not None else time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {"iss": account["client_email"], "scope": scope, "aud": account.get("token_uri") or TOKEN_URL,
              "iat": issued, "exp": issued + 3600}
    body = _b64url(json.dumps(header, separators=(",", ":")).encode()) + "." + \
        _b64url(json.dumps(claims, separators=(",", ":")).encode())
    key = serialization.load_pem_private_key(account["private_key"].encode("utf-8"), password=None)
    signature = key.sign(body.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    return body + "." + _b64url(signature)


class Token:
    """The access token of a service account, renewed before it expires."""

    def __init__(self, account: dict, session=None):
        self.account, self.session = account, session or requests
        self.value, self.expires = "", 0.0

    def get(self) -> str:
        if self.value and time.time() < self.expires:
            return self.value
        assertion = sign_jwt(self.account)
        response = self.session.post(self.account.get("token_uri") or TOKEN_URL, timeout=TIMEOUT,
                                     data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion})
        if response.status_code != 200:
            raise RuntimeError(f"token: HTTP {response.status_code} {_short(response.text)}")
        data = response.json()
        self.value = str(data.get("access_token") or "")
        if not self.value:
            raise RuntimeError("token: không có access_token trong câu trả lời")
        self.expires = time.time() + min(TOKEN_SECONDS, int(data.get("expires_in") or TOKEN_SECONDS) - 60)
        return self.value


def _short(text: str, n: int = 160) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n] + "…"


# ─── the spreadsheet ─────────────────────────────────────────────────────────

class Spreadsheet:
    """Appends to one spreadsheet; adds a missing tab with its header. Reads only the list of tabs."""

    def __init__(self, sheet: str, account: dict, session=None):
        self.id, self.session = sheet, session or requests
        self.token = Token(account, self.session)
        self._tabs: set[str] | None = None

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token.get()}", "Content-Type": "application/json"}

    def _call(self, method: str, url: str, body: dict | None = None, params: dict | None = None):
        response = self.session.request(method, url, headers=self._headers(), params=params, timeout=TIMEOUT,
                                        data=json.dumps(body) if body is not None else None)
        if response.status_code >= 300:
            raise RuntimeError(f"{method} {url.split(self.id)[-1][:60]}: HTTP {response.status_code} {_short(response.text)}")
        return response.json() if response.text else {}

    def tabs(self) -> set[str]:
        if self._tabs is None:
            data = self._call("GET", API + self.id, params={"fields": "sheets.properties.title"})
            self._tabs = {str(s.get("properties", {}).get("title") or "") for s in data.get("sheets") or []}
        return self._tabs

    def ensure_tab(self, tab: str, header: tuple[str, ...]) -> bool:
        """True when the tab was added now (with its header row)."""
        if tab in self.tabs():
            return False
        self._call("POST", API + self.id + ":batchUpdate", {"requests": [{"addSheet": {"properties": {"title": tab}}}]})
        self._tabs.add(tab)
        self._call("PUT", API + self.id + "/values/" + quote(f"{tab}!A1", safe=""), {"values": [list(header)]},
                   params={"valueInputOption": "RAW"})
        return True

    def append(self, tab: str, rows: list[list]) -> int:
        if not rows:
            return 0
        self._call("POST", API + self.id + "/values/" + quote(f"{tab}!A1", safe="") + ":append", {"values": rows},
                   params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"})
        return len(rows)


# ─── the cursor ──────────────────────────────────────────────────────────────

class Cursor:
    """Per tab: the keys written (bounded) and the last write; ``data/results/sheets_cursor.json``."""

    def __init__(self, data_dir: Path | str):
        self.path = Path(data_dir) / "results" / CURSOR_FILE
        self.data = self._read()

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def save(self) -> None:
        from vnbot.jobs import write_text_atomic

        write_text_atomic(self.path, json.dumps(self.data, ensure_ascii=False))

    def tab(self, name: str) -> dict:
        return self.data.setdefault(name, {"keys": [], "written": 0, "at": "", "error": ""})

    def keys(self, name: str) -> set[str]:
        return set(self.tab(name).get("keys") or [])

    def wrote(self, name: str, keys: list[str]) -> None:
        t = self.tab(name)
        t["keys"] = (list(t.get("keys") or []) + keys)[-KEYS_KEPT:]
        t["written"] = int(t.get("written") or 0) + len(keys)
        t["at"] = datetime.now().isoformat(timespec="seconds")
        t["error"] = ""

    def failed(self, name: str, error: str) -> None:
        self.tab(name)["error"] = f"{datetime.now().isoformat(timespec='minutes')} {error}"[:300]


# ─── the rows ────────────────────────────────────────────────────────────────

def _num(value, digits: int = 4):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ""
    if v != v or v in (float("inf"), float("-inf")):
        return ""
    return int(v) if v.is_integer() and abs(v) < 1e15 else round(v, digits)


def _ret(nav, capital) -> float | str:
    return _num((nav / capital - 1) * 100, 2) if nav and capital else ""


def nav_rows(trader, etf_state: dict | None) -> list[dict]:
    """``ck_nav``: each paper book's NAV per session, with VNINDEX and the ETF benchmark."""
    from vnbot import etf_bench

    etf = {h["date"]: h for h in (etf_state or {}).get("history") or []}
    rows = []
    for name, book in _books(trader):
        start = float(getattr(book, "start_cash", 0) or 0)
        for h in book.history:
            day = h["date"]
            rows.append({"key": f"{day}|{name}", "day": day, "book": name, "nav": _num(h.get("nav")),
                         "return_pct": _ret(h.get("nav"), start), "vnindex": _num(h.get("benchmark")),
                         "etf_nav": _num(etf.get(day, {}).get("nav")), "etf_nav_net": _num(etf.get(day, {}).get("nav_net")),
                         "cash": "", "positions": ""})
        if book.history:
            last = rows[-1]
            last.update(cash=_num(book.cash), positions=len(book.positions))
    for h in etf_bench.series(etf_state):
        full = etf[h["date"]]
        rows.append({"key": f"{h['date']}|etf", "day": h["date"], "book": "etf_" + (etf_state or {}).get("symbol", ""),
                     "nav": _num(full["nav"]), "return_pct": _ret(full["nav"], float((etf_state or {}).get("capital") or 0)),
                     "vnindex": "", "etf_nav": _num(full["nav"]), "etf_nav_net": _num(full["nav_net"]), "cash": "",
                     "positions": ""})
    return sorted(rows, key=lambda r: r["key"])


def trade_rows(trader) -> list[dict]:
    """``ck_trades``: every fill of the paper books; a SELL's P&L after fees and tax, in %."""
    rows = []
    for name, book in _books(trader):
        for n, f in enumerate(book.fills):
            basis = f.qty * f.price - (f.cost or 0) - (f.pnl or 0) if f.side == "SELL" and f.pnl is not None else None
            rows.append({"key": f"{f.date}|{name}|{f.symbol}|{f.side}|{_num(f.qty)}|{_num(f.price)}|{n}",
                         "day": f.date, "book": name, "symbol": f.symbol, "side": f.side, "qty": _num(f.qty),
                         "price": _num(f.price), "cost": _num(f.cost), "pnl": _num(f.pnl) if f.pnl is not None else "",
                         "pnl_pct": _num(f.pnl / basis * 100, 2) if basis else "", "reason": f.reason or ""})
    return rows


def shadow_rows(store) -> list[dict]:
    """``ck_shadow``: the resolved shadow results (10 sessions) with their group - the switched-off
    breakouts among them - as ``vnbot.chandoan`` counts them."""
    from vnbot import chandoan

    if store is None:
        return []
    rows = []
    for r in chandoan.dedupe([x for x in store.outcome_rows(chandoan.stats_since()) if not x.get("holding")]):
        if r.get("ret_10") is None:
            continue
        group = next((g for g, _ in chandoan.GROUPS if g not in ("bullish", "bearish", "all") and chandoan._member(g, r)), "")
        excess = r["ret_10"] - r["vni_10"] if r.get("vni_10") is not None else ""
        resolved = r.get("resolved_ts")
        rows.append({"key": f"{r['day']}|{r['symbol']}", "day": r["day"], "symbol": r["symbol"], "group": group,
                     "action": r.get("action") or "", "gate": r.get("gate") or "", "near": r.get("near") or "",
                     "source": r.get("source") or "", "ret_10": _num(r.get("ret_10")), "vni_10": _num(r.get("vni_10")),
                     "excess_10": _num(excess) if excess != "" else "", "plan": r.get("plan") or "",
                     "plan_outcome": r.get("plan_outcome") or "", "plan_r": _num(r.get("plan_r")) if r.get("plan_r") is not None else "",
                     "resolved": datetime.fromtimestamp(float(resolved)).strftime("%Y-%m-%d") if resolved else ""})
    return sorted(rows, key=lambda r: r["key"])


def ai_pick_rows(days: list[dict]) -> list[dict]:
    """``ck_ai_pick``: one row a session once every candidate's 10-session result is in."""
    import statistics

    rows = []
    for entry in sorted(days, key=lambda d: d["day"]):
        cands = entry.get("candidates") or []
        if not cands or entry.get("error"):
            continue
        quick = {c["symbol"]: c["r10"]["pct"] for c in cands if (c.get("r10") or {}).get("status") == "closed"}
        if len(quick) != len(cands):
            continue
        pick = (entry.get("pick") or {}).get("symbol") or ""
        mean = statistics.mean(quick.values())
        r_pick = quick.get(pick)
        rows.append({"key": entry["day"], "day": entry["day"], "pick": pick,
                     "confidence": _num((entry.get("pick") or {}).get("confidence")) if pick else "",
                     "r10_pick": _num(r_pick) if r_pick is not None else "", "r10_mean": _num(mean),
                     "excess": _num(r_pick - mean) if r_pick is not None else "", "n_candidates": len(cands),
                     "beat": (1 if r_pick > mean else 0) if r_pick is not None else "",
                     "ranking": " > ".join(entry.get("ranking") or []), "avoid": ", ".join(a["symbol"] for a in entry.get("avoid") or []),
                     "model": entry.get("served_by") or entry.get("model") or ""})
    return rows


def summary_row(day: str, trader, store, etf_state: dict | None, pick_days: list[dict], *, settings=None,
                shadow_n: int = 0) -> dict:
    """``ck_summary``: the day's figures the weekly review checks its own against."""
    from tradingagents.quant import params as qp
    from vnbot import etf_bench, quant_snapshot, version

    data_dir = trader.settings.data_dir if trader is not None else None
    snap = quant_snapshot.status(data_dir) if data_dir else {}
    etf = etf_bench.stats(etf_state) or {}
    books = dict(_books(trader))
    warnings = []
    if snap.get("changed"):
        warnings.append("tham số đổi: " + ", ".join(f"{c['name']} {c['frozen']}→{c['now']}" for c in snap["changed"]))
    if etf.get("error"):
        warnings.append(f"ETF: {etf['error']}")
    calibrate = getattr(settings, "calibrate", None) if settings is not None else None
    row = {"key": day, "day": day, "written_at": datetime.now().isoformat(timespec="minutes"),
           "version": version.VERSION, "build": version.build(), "params_fingerprint": snap.get("now", ""),
           "snapshot_day": snap.get("day") or snap.get("taken_at", "")[:10], "params_changed": len(snap.get("changed") or []),
           "auto_calibrate": "" if calibrate is None else int(bool(calibrate)), "breakout_buy": qp.active().breakout_buy}
    closed = 0
    for name in ("factor", "engine", "momentum"):
        book = books.get(name)
        st = book.stats() if book is not None else {}
        row[f"{name}_nav"] = _num(st.get("nav")) if st else ""
        row[f"{name}_ret"] = _num(st.get("return_pct"), 2) if st else ""
        closed += int(st.get("closed") or 0) if st else 0
    engine = books.get("engine")
    row["vnindex_ret"] = _num(engine.stats().get("benchmark_pct"), 2) if engine is not None and engine.history else ""
    row.update(etf_nav=_num(etf.get("nav")), etf_ret=_num(etf.get("return_pct"), 2), etf_ret_net=_num(etf.get("return_net_pct"), 2),
               closed_trades=closed, shadow_resolved=shadow_n, ai_pick_sessions=len(ai_pick_rows(pick_days)),
               warnings=" · ".join(warnings))
    return row


def _books(trader) -> list[tuple[str, object]]:
    if trader is None:
        return []
    out = []
    for name, getter in (("factor", "factor_book"), ("engine", "paper"), ("momentum", "momentum_book")):
        try:
            out.append((name, getattr(trader, getter)()))
        except Exception as exc:  # noqa: BLE001 - one unreadable book must not stop the others
            logger.warning("Sheets: book %s unreadable: %s", name, exc)
    return out


# ─── the export ──────────────────────────────────────────────────────────────

def _cells(row: dict, header: tuple[str, ...]) -> list:
    """The row in the tab's column order; text redacted, numbers as numbers."""
    from vnbot.eventlog import redact

    out = []
    for col in header:
        value = row.get(col, "")
        if isinstance(value, bool):
            value = int(value)
        if isinstance(value, (int, float)):
            out.append(value)
        else:
            out.append(redact("" if value is None else str(value)))
    return out


class Exporter:
    """One cycle: build each tab's rows, drop the keys already written, append, remember."""

    def __init__(self, data_dir: Path | str, *, trader=None, store=None, settings=None, spreadsheet: Spreadsheet | None = None):
        self.data_dir, self.trader, self.store, self.settings = Path(data_dir), trader, store, settings
        self.spreadsheet = spreadsheet
        self.cursor = Cursor(data_dir)

    def _sheet(self) -> Spreadsheet:
        if self.spreadsheet is None:
            account = account_json()
            if not sheet_id() or account is None:
                raise RuntimeError(why_off())
            self.spreadsheet = Spreadsheet(sheet_id(), account)
        return self.spreadsheet

    def rows(self, day: str) -> dict[str, list[dict]]:
        """Every tab's candidate rows (before the cursor), each source on its own."""
        from vnbot import ai_pick, etf_bench

        etf_state = etf_bench.load(self.data_dir)
        pick_days = ai_pick.PickLog(self.data_dir).days()
        out: dict[str, list[dict]] = {}
        sources = {"ck_nav": lambda: nav_rows(self.trader, etf_state), "ck_trades": lambda: trade_rows(self.trader),
                   "ck_shadow": lambda: shadow_rows(self.store), "ck_ai_pick": lambda: ai_pick_rows(pick_days)}
        for tab, build in sources.items():
            try:
                out[tab] = build()
            except Exception as exc:  # noqa: BLE001 - one source failing must not stop the others
                logger.warning("Sheets: rows of %s failed: %s", tab, exc)
                self.cursor.failed(tab, f"dựng dòng: {exc}")
                out[tab] = []
        try:
            out["ck_summary"] = [summary_row(day, self.trader, self.store, etf_state, pick_days, settings=self.settings,
                                             shadow_n=len(out.get("ck_shadow") or []))]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Sheets: summary row failed: %s", exc)
            self.cursor.failed("ck_summary", f"dựng dòng: {exc}")
            out["ck_summary"] = []
        return out

    def run(self, day: str) -> dict:
        """Write what is new; returns {tab: rows written (or -1 on a failure)}. Never raises."""
        result: dict[str, int] = {}
        self.cursor = Cursor(self.data_dir)        # what the last cycle (any Exporter) wrote
        try:
            rows = self.rows(day)
            sheet = self._sheet()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Sheets export %s not started: %s", day, exc)
            for tab in TABS:
                self.cursor.failed(tab, str(exc))
                result[tab] = -1
            self.cursor.save()
            return result
        for tab, header in TABS.items():
            new = [r for r in rows.get(tab) or [] if r["key"] not in self.cursor.keys(tab)][:MAX_ROWS_PER_CALL]
            try:
                sheet.ensure_tab(tab, header)
                sheet.append(tab, [_cells(r, header) for r in new])
                self.cursor.wrote(tab, [r["key"] for r in new])
                result[tab] = len(new)
            except Exception as exc:  # noqa: BLE001 - this tab waits for the next cycle, the others go on
                logger.warning("Sheets export %s failed: %s", tab, exc)
                self.cursor.failed(tab, str(exc))
                result[tab] = -1
        self.cursor.save()
        logger.info("Sheets export %s: %s", day, ", ".join(f"{k} {v}" for k, v in result.items()))
        return result


def status_text(data_dir: Path | str) -> str:
    """``/bangtinh``: on or off, and per tab the rows written, the last write, the last error."""
    off = why_off()
    lines = [f"📋 {NAME}: " + (f"tắt ({off})." if off else f"bật, ghi lúc {EXPORT_TIME} các ngày giao dịch (chỉ ghi thêm, không đọc).")]
    cursor = Cursor(data_dir).data
    for tab in TABS:
        t = cursor.get(tab) or {}
        if not t:
            lines.append(f"• {tab}: chưa ghi")
            continue
        lines.append(f"• {tab}: {t.get('written', 0)} dòng" + (f", lần cuối {t['at'][:16].replace('T', ' ')}" if t.get("at") else "")
                     + (f" · ⚠️ {t['error']}" if t.get("error") else ""))
    if not off:
        lines.append("Bot không bao giờ đọc bảng tính (kể cả tab proposals); lỗi ghi thì thử lại lượt sau.")
    return "\n".join(lines)
