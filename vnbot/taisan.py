"""Tài sản: the owner's real-money portfolio, kept apart from every book of the bot.

Nothing here trades or suggests a buy: the owner records what happened at the broker
(``/ts...`` in Telegram and Zoom, the ``/taisan`` web tab, an Excel import) and the bot
values it, charts it, reports it every session and warns when to sell.

- ``data/taisan/journal.jsonl``: every operation, appended, never edited. A mistake is
  undone by a ``VOID`` line that names the entry; the portfolio is always derived again
  from the journal (``derive``), so an undo or a back-dated entry can never leave the
  numbers half-updated.
- Prices are as the owner entered them: a buy's price already holds the broker's fee
  (the owner adds it), a sell's price is the matched price (no fee or tax taken off).
- Costs are average cost per symbol (as the owner's old app and brokers show them);
  each buy is a lot with its own date for T+2 (shares bought on T are sold from the
  afternoon of T+2, sessions counted by the exchange calendar), bonus shares are
  sellable at once.
- ``state.json``: the cut-loss/target of each holding (the site's plan, or set by the
  owner), the marks used to see an ex-date (``Ledger.mark_to_history``), the alerts sent
  and their shadow results, the allocation thresholds, the corporate events, and the
  valuation cards the owner enters (``/tsdinhgia``: Bear/Base/Bull of any stock, held or
  not; only for the zone alerts in the session, never a level of the portfolio).
- ``prices.json``: unadjusted closes per symbol and day (and VNINDEX), which the asset
  chart is computed from. ``pending.json``: the value of the rights waiting to arrive.
"""

from __future__ import annotations

import bisect
import json
import logging
import math
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import date as _date, timedelta
from pathlib import Path

from vnbot import kyluat

logger = logging.getLogger(__name__)

DIR = "taisan"
SETTLEMENT_SESSIONS = 2
DIVIDEND_TAX = 0.05          # personal income tax withheld on a cash dividend (the pending estimate only)
PENDING_DAYS = 120           # a right not recorded this long after its ex-date is dropped from the total
WARN_PCT, DANGER_PCT = 25.0, 40.0
ALERT_LEVELS = {
    "stop": ("⛔", "Khẩn", "giá thủng mức cắt lỗ"),
    "sell": ("🔴", "Mạnh", "trang đã chốt BÁN"),
    "early": ("🟠", "Sớm", "đổi chiều sang BÁN trong phiên (chưa chốt)"),
    "target": ("🎯", "Chốt lời", "chạm mục tiêu"),
    "re": ("🟢", "Vào vùng Rẻ", "giá ≤ Base của thẻ định giá anh nhập"),
    "dat": ("🔺", "Vào vùng Đắt", "giá ≥ Bull của thẻ định giá anh nhập"),
}
UP_IS_RIGHT = ("re",)        # right when the price is higher 10 sessions later; every other level when lower
SHADOW_SESSIONS = (5, 10)
MAX_VALUATIONS = 30          # valuation cards kept: each one is a price read at every intraday check
ZONE_BUFFER_PCT = 0.5        # a zone alerted is left only this far back past its level: no alert ping-pong
EX_LOOKBACK_DAYS = 60        # VCI's event filter is not the ex-date: ask from this long before the card
SPARK_SESSIONS = 62          # about three months of closes on the page's stock cards

KINDS = ("BUY", "SELL", "BONUS", "DEPOSIT", "WITHDRAW", "DIVIDEND", "ADJUST", "VOID")
KIND_VI = {"BUY": "Mua", "SELL": "Bán", "BONUS": "Thưởng CP", "DEPOSIT": "Nạp tiền", "WITHDRAW": "Rút tiền",
           "DIVIDEND": "Cổ tức tiền", "ADJUST": "Điều chỉnh tiền", "VOID": "Huỷ"}
STOCK_KINDS = ("BUY", "SELL", "BONUS")
CASH_KINDS = ("DEPOSIT", "WITHDRAW", "DIVIDEND", "ADJUST")
# Order inside one day: money in and rights first, then sales, then buys, then withdrawals.
_RANK = {"DEPOSIT": 0, "ADJUST": 0, "DIVIDEND": 0, "BONUS": 0, "SELL": 1, "BUY": 2, "WITHDRAW": 3}


# ─── formatting ───────────────────────────────────────────────────────────────

def vnd(value) -> str:
    if value is None:
        return "-"
    return f"{value:,.0f}".replace(",", ".")


def signed(value) -> str:
    return ("+" if value >= 0 else "-") + vnd(abs(round(value)))


def pct(value) -> str:
    return "-" if value is None else f"{value:+.2f}%".replace(".", ",")


def ddmm(day: str) -> str:
    return f"{day[8:10]}/{day[5:7]}" if day and len(day) >= 10 else (day or "")


def ddmmyyyy(day: str) -> str:
    return f"{day[8:10]}/{day[5:7]}/{day[:4]}" if day and len(day) >= 10 else (day or "")


def now_vn():
    from tradingagents.market import vn_rules

    return vn_rules.now_vn()


def today_vn() -> str:
    return now_vn().strftime("%Y-%m-%d")


def _finite(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _sessions_between(first: str, last: str) -> int:
    from tradingagents.trading.ledger import _sessions_between as between

    return between(first, last)


# ─── the journal ─────────────────────────────────────────────────────────────

@dataclass
class Entry:
    id: int
    kind: str
    day: str
    symbol: str = ""
    qty: int = 0
    price: float = 0.0      # BUY: cost per share as entered (fee in); SELL: matched price
    amount: float = 0.0     # cash kinds, VND (WITHDRAW/negative ADJUST: the size, the sign is the kind's)
    note: str = ""
    ref: int = 0            # VOID: the id undone
    at: str = ""            # when it was recorded (Vietnam time)
    source: str = ""        # telegram, zoom, web, import

    @classmethod
    def from_dict(cls, raw: dict) -> Entry:
        names = set(cls.__dataclass_fields__)
        data = {k: v for k, v in raw.items() if k in names}
        entry = cls(**data)
        entry.id, entry.qty, entry.ref = int(entry.id), int(entry.qty or 0), int(entry.ref or 0)
        entry.price, entry.amount = float(entry.price or 0), float(entry.amount or 0)
        if entry.kind not in KINDS:
            raise ValueError(f"unknown kind {entry.kind!r}")
        return entry


@dataclass
class Lot:
    day: str
    qty: int
    free: bool = False          # bonus shares: sellable at once


@dataclass
class Holding:
    symbol: str
    qty: int = 0
    cost: float = 0.0           # total cost (average-cost method)
    lots: list[Lot] = field(default_factory=list)

    @property
    def avg(self) -> float:
        return self.cost / self.qty if self.qty else 0.0

    @property
    def first_day(self) -> str:
        return min((lot.day for lot in self.lots), default="")

    def sellable(self, day: str, *, afternoon: bool = True) -> int:
        """Shares that can be sold during ``day`` (from its afternoon when ``afternoon``)."""
        need = SETTLEMENT_SESSIONS if afternoon else SETTLEMENT_SESSIONS + 1
        return sum(lot.qty for lot in self.lots if lot.free or _sessions_between(lot.day, day) >= need)

    def pending_lots(self, day: str) -> list[tuple[str, int]]:
        """(the T+2 day, shares) of the lots not sellable during the whole of ``day``."""
        from tradingagents.market import vn_rules

        out: dict[str, int] = {}
        for lot in self.lots:
            if not lot.free and _sessions_between(lot.day, day) < SETTLEMENT_SESSIONS + 1:
                t2 = vn_rules.add_sessions(lot.day, SETTLEMENT_SESSIONS)
                out[t2] = out.get(t2, 0) + lot.qty
        return sorted(out.items())


@dataclass
class Row:
    """One effective entry in date order, with what it did (the history table)."""

    entry: Entry
    avg_cost: float | None = None       # SELL: the average cost the sale was booked at
    pnl: float | None = None            # SELL: realised P&L


@dataclass
class Book:
    holdings: dict[str, Holding] = field(default_factory=dict)
    cash: float = 0.0
    deposits: float = 0.0
    withdrawals: float = 0.0
    dividends: float = 0.0
    adjustments: float = 0.0
    realized: float = 0.0
    sells: int = 0
    wins: int = 0
    rows: list[Row] = field(default_factory=list)
    flows: dict[str, float] = field(default_factory=dict)    # deposits - withdrawals by day
    warnings: list[str] = field(default_factory=list)
    first_day: str = ""
    last_day: str = ""

    @property
    def capital(self) -> float:
        return self.deposits - self.withdrawals

    @property
    def losses(self) -> int:
        return self.sells - self.wins


class LedgerError(ValueError):
    """An entry that cannot have happened (Vietnamese message)."""


def effective(entries: list[Entry]) -> list[Entry]:
    """The entries that count: VOID lines and what they undid left out; date order."""
    voided = {e.ref for e in entries if e.kind == "VOID"}
    keep = [e for e in entries if e.kind != "VOID" and e.id not in voided]
    return sorted(keep, key=lambda e: (e.day, _RANK.get(e.kind, 9), e.id))


def derive(entries: list[Entry], *, upto: str | None = None, on_day=None) -> Book:
    """The portfolio after every effective entry (up to ``upto`` included). Raises
    LedgerError at the first entry that cannot have happened (a sale of shares not held or
    not settled). ``on_day(day, book)`` is called after the last entry of each day."""
    book = Book()
    ordered = effective(entries)
    if ordered:
        book.first_day = ordered[0].day
    current = None
    for entry in ordered:
        if upto is not None and entry.day > upto:
            break
        if current is not None and entry.day != current:
            _end_of_day(book, current, on_day)
        current = entry.day
        _apply(book, entry)
    if current is not None:
        _end_of_day(book, current, on_day)
    return book


def _end_of_day(book: Book, day: str, on_day) -> None:
    book.last_day = day
    if book.cash < -0.5:
        note = f"Tiền mặt âm {vnd(book.cash)} sau ngày {ddmmyyyy(day)}: thiếu một lần nạp tiền? (/tsnap, /tstien)"
        if note not in book.warnings:
            book.warnings.append(note)
    if on_day is not None:
        on_day(day, book)


def _apply(book: Book, e: Entry) -> None:
    row = Row(e)
    if e.kind == "DEPOSIT":
        book.cash += e.amount
        book.deposits += e.amount
        book.flows[e.day] = book.flows.get(e.day, 0.0) + e.amount
    elif e.kind == "WITHDRAW":
        book.cash -= e.amount
        book.withdrawals += e.amount
        book.flows[e.day] = book.flows.get(e.day, 0.0) - e.amount
    elif e.kind == "DIVIDEND":
        book.cash += e.amount
        book.dividends += e.amount
    elif e.kind == "ADJUST":
        book.cash += e.amount
        book.adjustments += e.amount
    elif e.kind == "BUY":
        h = book.holdings.setdefault(e.symbol, Holding(e.symbol))
        h.qty += e.qty
        h.cost += e.qty * e.price
        h.lots.append(Lot(e.day, e.qty))
        book.cash -= e.qty * e.price
    elif e.kind == "BONUS":
        h = book.holdings.setdefault(e.symbol, Holding(e.symbol))
        h.qty += e.qty
        h.lots.append(Lot(e.day, e.qty, free=True))
    elif e.kind == "SELL":
        h = book.holdings.get(e.symbol)
        held = h.qty if h else 0
        if held < e.qty:
            raise LedgerError(f"{ddmmyyyy(e.day)}: bán {vnd(e.qty)} cp {e.symbol} nhưng lúc đó chỉ có {vnd(held)} cp.")
        free = h.sellable(e.day)
        if free < e.qty:
            raise LedgerError(f"{ddmmyyyy(e.day)}: bán {vnd(e.qty)} cp {e.symbol} nhưng mới có {vnd(free)} cp về tài khoản "
                              "(cổ phiếu mua phiên T bán được từ chiều T+2).")
        avg = h.avg
        pnl = (e.price - avg) * e.qty
        row.avg_cost, row.pnl = avg, pnl
        book.realized += pnl
        book.sells += 1
        book.wins += 1 if pnl > 0 else 0
        book.cash += e.qty * e.price
        h.cost -= avg * e.qty
        h.qty -= e.qty
        left = e.qty                              # settled lots, oldest first
        for lot in sorted(h.lots, key=lambda lot: lot.day):
            if left <= 0:
                break
            if lot.free or _sessions_between(lot.day, e.day) >= SETTLEMENT_SESSIONS:
                cut = min(left, lot.qty)
                lot.qty -= cut
                left -= cut
        h.lots = [lot for lot in h.lots if lot.qty > 0]
        if h.qty <= 0:
            book.holdings.pop(e.symbol, None)
    book.rows.append(row)


# ─── small JSON files ─────────────────────────────────────────────────────────

def _read_json(path: Path, default):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
    return data if isinstance(data, type(default)) else default


def _write_json(path: Path, data) -> None:
    from vnbot.jobs import write_text_atomic

    write_text_atomic(path, json.dumps(data, ensure_ascii=False, separators=(",", ":")))


# ─── the market data the portfolio needs ─────────────────────────────────────

class VendorMarket:
    """Prices and corporate events from the bot's sources. Tests pass their own object
    with the same methods."""

    def unadjusted_closes(self, symbol: str, start: str, end: str) -> dict[str, float]:
        """{day: close as traded} (VCI price-history: closes are not re-adjusted for later
        dividends or bonus issues, unlike the chart bars)."""
        from tradingagents.dataflows import money_flows as mf

        frame = mf.load_stock_flows(symbol, end, backfill=True)
        out = {}
        for day, close in zip(frame["Date"], frame["Close"], strict=False):
            key = day.strftime("%Y-%m-%d")
            if start <= key <= end and _finite(close) and float(close) > 0:
                out[key] = float(close)
        return out

    def daily_closes(self, symbol: str, end: str) -> dict[str, float]:
        """{day: close} of the price source up to ``end`` (adjusted for past corporate actions;
        on its latest day this is the close as traded)."""
        from tradingagents.dataflows.ohlcv import load_ohlcv

        frame = load_ohlcv(symbol, end, fill_gaps=False)
        return {d.strftime("%Y-%m-%d"): float(c) for d, c in zip(frame["Date"], frame["Close"], strict=True)
                if _finite(c) and float(c) > 0}

    def live(self, symbols: list[str], day: str) -> dict[str, float]:
        from vnbot.intraday import live_price

        out = {}
        for symbol in symbols:
            try:
                price = live_price(symbol, day)
            except Exception:  # noqa: BLE001 - that holding shows its last close
                price = None
            if price and _finite(price) and price > 0:
                out[symbol] = float(price)
        return out

    def events(self, symbol: str, start: str, end: str) -> list[dict]:
        from tradingagents.dataflows.vendors import vci

        return vci.fetch_events(symbol, start, end, codes="DIV,ISS")

    def exchange(self, symbol: str) -> str | None:
        from vnbot.intraday import exchange

        return exchange(symbol)


# ─── corporate events ─────────────────────────────────────────────────────────

_FREE_SHARES = re.compile(r"cổ tức bằng cổ phiếu|cổ phiếu thưởng|co tuc bang co phieu|co phieu thuong", re.IGNORECASE)


def parse_event(raw: dict) -> dict | None:
    """A cash dividend or free shares from a VCI event: {kind CASH/STOCK, ex, value or
    ratio, title}; None for anything else (ESOP, rights to buy, meetings)."""
    ex = str(raw.get("exrightDate") or "")[:10]
    if len(ex) != 10:
        return None
    title = str(raw.get("eventTitleVi") or raw.get("eventNameVi") or "")
    code = raw.get("eventCode")
    if code == "DIV" and _finite(raw.get("valuePerShare")) and float(raw["valuePerShare"]) > 0:
        return {"kind": "CASH", "ex": ex, "value": float(raw["valuePerShare"]), "title": title,
                "record": str(raw.get("recordDate") or "")[:10]}
    if code == "ISS" and _FREE_SHARES.search(title) and _finite(raw.get("exerciseRatio")) \
            and 0 < float(raw["exerciseRatio"]) < 10:
        return {"kind": "STOCK", "ex": ex, "ratio": float(raw["exerciseRatio"]), "title": title,
                "record": str(raw.get("recordDate") or "")[:10]}
    return None


def ex_event(raw: dict) -> dict | None:
    """Any ex-date of a VCI DIV/ISS event, the card's "cần cập nhật": {ex, what} for a cash or
    stock dividend, bonus shares, rights (ESOP and private placements have no ex-date)."""
    ev = parse_event(raw)
    if ev:
        what = (f"cổ tức tiền {vnd(ev['value'])} đ/cp" if ev["kind"] == "CASH"
                else f"cổ phiếu thưởng/cổ tức bằng cổ phiếu {_ratio(ev['ratio'])}")
        return {"ex": ev["ex"], "what": what}
    ex = str(raw.get("exrightDate") or "")[:10]
    if raw.get("eventCode") not in ("DIV", "ISS") or len(ex) != 10:
        return None
    ratio = float(raw["exerciseRatio"]) if _finite(raw.get("exerciseRatio")) and float(raw["exerciseRatio"]) > 0 else 0
    return {"ex": ex, "what": ("cổ tức" if raw["eventCode"] == "DIV" else "phát hành thêm")
            + (f" {_ratio(ratio)}" if ratio else "")}


def _ratio(value: float) -> str:
    """0.44636584 -> "44,6%"."""
    return f"{value * 100:.1f}".rstrip("0").rstrip(".").replace(".", ",") + "%"


# ─── valuation cards entered by hand ──────────────────────────────────────────

VALUATION_USAGE = ("Dùng: /tsdinhgia CTG 27904 30164 33309 [eab 26039] [han 30/10/2026] [nguon ...] - Bear Base Bull "
                   "theo đồng · /tsdinhgia CTG xoa · /tsdinhgia - xem các thẻ")


def card_zone(card: dict, price: float, last: str | None = None) -> str:
    """Rẻ (price ≤ Base) / Hợp lý / Đắt (price ≥ Bull). ``last``: the zone alerted before, kept until
    the price is back ``ZONE_BUFFER_PCT`` past its level (a price wavering around Base alerts once)."""
    base, bull, buffer = float(card["base"]), float(card["bull"]), ZONE_BUFFER_PCT / 100
    if price <= base or (last == "Rẻ" and price <= base * (1 + buffer)):
        return "Rẻ"
    if price >= bull or (last == "Đắt" and price >= bull * (1 - buffer)):
        return "Đắt"
    return "Hợp lý"


def card_levels(card: dict) -> str:
    return (f"Bear {vnd(card['bear'])}" + (f" · eab {vnd(card['eab'])}" if card.get("eab") else "")
            + f" · Base {vnd(card['base'])} · Bull {vnd(card['bull'])}")


def card_mos(card: dict, price: float) -> float:
    """Margin of safety, %: (Base − price) / Base."""
    return (float(card["base"]) - price) / float(card["base"]) * 100


def mos_text(card: dict, price: float) -> str:
    return f"MOS {card_mos(card, price):+.1f}%".replace(".", ",")


def _card_price(word: str, name: str) -> float:
    from vnbot.telegram_bot import _amount

    try:
        return _amount(word)
    except ValueError:
        raise LedgerError(f"{name} không hợp lệ: {word!r}. Nhập giá đầy đủ theo đồng: 30164, 30.164 hoặc 30,164 "
                          "(dấu chấm hay phẩy chỉ ngăn hàng nghìn; không nhận số lẻ như 30,1).") from None


def card_day(word: str) -> str:
    """dd/mm/yyyy (or YYYY-MM-DD) -> YYYY-MM-DD; LedgerError otherwise."""
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", word.strip())
    try:
        return (_date(int(m[3]), int(m[2]), int(m[1])) if m else _date.fromisoformat(word.strip())).isoformat()
    except ValueError:
        raise LedgerError(f"Hạn dùng không hợp lệ: {word!r}. Dùng dạng 30/10/2026.") from None


def parse_valuation(words: list[str]) -> dict:
    """``BEAR BASE BULL [eab X] [han dd/mm/yyyy] [nguon text...]`` -> the fields of ``set_valuation``.
    Prices are whole đồng: "30.164", "30,164" and "30164" are all 30.164 đ - a dot or a comma must
    split groups of three digits (``telegram_bot._amount``), so "30,1" (thousands, or a decimal) is
    refused rather than read as 301 đ."""
    if len(words) < 3:
        raise LedgerError(VALUATION_USAGE)
    out = {"bear": _card_price(words[0], "Bear"), "base": _card_price(words[1], "Base"),
           "bull": _card_price(words[2], "Bull"), "eab": None, "expires": "", "note": ""}
    i = 3
    while i < len(words):
        key = words[i].lower()
        if key in ("nguon", "nguồn"):
            out["note"] = " ".join(words[i + 1:])
            break
        if key not in ("eab", "han", "hạn"):
            raise LedgerError(f"Không hiểu {words[i]!r}. {VALUATION_USAGE}")
        if i + 1 >= len(words):
            raise LedgerError(f"Thiếu giá trị sau {words[i]!r}. {VALUATION_USAGE}")
        if key == "eab":
            out["eab"] = _card_price(words[i + 1], "eab")
        else:
            out["expires"] = card_day(words[i + 1])
        i += 2
    return out


# ─── the portfolio ────────────────────────────────────────────────────────────

class TaiSan:
    def __init__(self, data_dir: Path, *, market=None, site=None):
        self.dir = Path(data_dir) / DIR
        self.dir.mkdir(parents=True, exist_ok=True)
        self.journal_path = self.dir / "journal.jsonl"
        self.state_path = self.dir / "state.json"
        self.prices_path = self.dir / "prices.json"
        self.pending_path = self.dir / "pending.json"
        self.import_path = self.dir / "import_pending.json"
        self.market = market or VendorMarket()
        # (symbol, day, price) -> (stop, target): the site's plan for a new buy (sieutinhieu.vn)
        self.site = site if site is not None else self.site_plan
        self._lock = threading.RLock()
        self._live: tuple[float, dict] = (0.0, {})
        self.rebuilding = False
        self._bad_lines: set[str] = set()
        # (level, symbol, price, day) on a new alert: the AI's look at the news (vnbot.ai_pick), set by main
        self.on_alert = None

    # ── the journal ──
    def entries(self) -> list[Entry]:
        try:
            text = self.journal_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        out = []
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                out.append(Entry.from_dict(json.loads(line)))
            except (ValueError, TypeError, KeyError) as exc:
                if line not in self._bad_lines:          # said once, not at every read
                    self._bad_lines.add(line)
                    logger.warning("Tài sản: unreadable journal line skipped (%s)", exc)
        return out

    def book(self, *, upto: str | None = None) -> Book:
        return derive(self.entries(), upto=upto)

    def _append(self, entries: list[Entry]) -> None:
        """Lines on disk before this returns; a line cut by an earlier crash is closed first."""
        from tradingagents.dataflows.files import locked

        text = "".join(json.dumps(asdict(e), ensure_ascii=False) + "\n" for e in entries)
        with locked(self.journal_path), open(self.journal_path, "a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() > 0:
                handle.seek(-1, os.SEEK_END)
                if handle.read(1) != b"\n":
                    text = "\n" + text
            handle.write(text.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())

    def backup_journal(self) -> Path:
        """Move the journal aside as ``journal.jsonl.bak-<time>`` (never over an earlier copy)."""
        stamp = now_vn().strftime("%Y%m%d-%H%M%S")
        backup, n = self.journal_path.with_name(f"journal.jsonl.bak-{stamp}"), 1
        while backup.exists():
            n += 1
            backup = self.journal_path.with_name(f"journal.jsonl.bak-{stamp}-{n}")
        self.journal_path.rename(backup)
        return backup

    def _next_id(self, entries: list[Entry]) -> int:
        return max((e.id for e in entries), default=0) + 1

    def add(self, kind: str, day: str, *, symbol: str = "", qty: int = 0, price: float = 0.0, amount: float = 0.0,
            note: str = "", source: str = "") -> tuple[Entry, Book]:
        """Record one operation after checking it against the whole journal. LedgerError
        (Vietnamese) when it cannot have happened; nothing is written then."""
        kind = kind.upper()
        if kind not in KINDS or kind == "VOID":
            raise LedgerError(f"Loại giao dịch không hợp lệ: {kind}.")
        day = self._check_day(day, trade=kind in ("BUY", "SELL"))
        symbol = (symbol or "").strip().upper()
        if kind in STOCK_KINDS or kind == "DIVIDEND":
            if not re.fullmatch(r"[A-Z0-9]{1,10}", symbol):
                raise LedgerError(f"Mã không hợp lệ: {symbol!r}.")
        if kind in STOCK_KINDS:
            if isinstance(qty, bool) or not _finite(qty) or float(qty) != int(float(qty)) or int(float(qty)) <= 0:
                raise LedgerError("Số cổ phiếu phải là số nguyên dương.")
            qty = int(float(qty))
        if kind in ("BUY", "SELL"):
            if not _finite(price) or float(price) < 100:
                raise LedgerError("Giá không hợp lệ: nhập theo đồng, ví dụ 56300 hoặc 56.300.")
            price = float(price)
        if kind in CASH_KINDS:
            if not _finite(amount) or (kind != "ADJUST" and float(amount) <= 0) or float(amount) == 0:
                raise LedgerError("Số tiền không hợp lệ.")
            amount = float(amount)
        with self._lock:
            entries = self.entries()
            entry = Entry(self._next_id(entries), kind, day, symbol, int(qty or 0), float(price or 0),
                          float(amount or 0), (note or "").strip()[:200], at=now_vn().isoformat(timespec="seconds"),
                          source=source)
            book = derive(entries + [entry])            # raises when it cannot have happened
            self._append([entry])
        self._forget_live()
        return entry, book

    def void(self, entry_id: int | None = None, *, source: str = "") -> Entry:
        """Undo ``entry_id`` (or the last effective entry): a VOID line, after checking the
        journal still holds together without it."""
        with self._lock:
            entries = self.entries()
            done = effective(entries)
            if not done:
                raise LedgerError("Chưa có giao dịch nào để huỷ.")
            if entry_id is None:
                target = max(done, key=lambda e: e.id)
            else:
                target = next((e for e in done if e.id == int(entry_id)), None)
                if target is None:
                    raise LedgerError(f"Không có giao dịch số {entry_id} (hoặc đã huỷ rồi).")
            undo = Entry(self._next_id(entries), "VOID", today_vn(), ref=target.id,
                         at=now_vn().isoformat(timespec="seconds"), source=source)
            try:
                derive(entries + [undo])
            except LedgerError as exc:
                raise LedgerError(f"Không huỷ được giao dịch số {target.id}: {exc}") from None
            self._append([undo])
        self._forget_live()
        return target

    @staticmethod
    def _check_day(day: str | None, *, trade: bool) -> str:
        from tradingagents.market import vn_rules

        raw = (day or "").strip() or today_vn()
        try:
            parsed = _date.fromisoformat(raw[:10]).isoformat() if len(raw) == 10 else None
        except ValueError:
            parsed = None
        if parsed is None:
            raise LedgerError(f"Ngày không hợp lệ: {raw!r}. Dùng dạng YYYY-MM-DD.")
        if parsed > today_vn():
            raise LedgerError(f"Ngày {ddmmyyyy(parsed)} ở tương lai.")
        if trade and not vn_rules.is_session_day(parsed):
            raise LedgerError(f"{ddmmyyyy(parsed)} không có phiên giao dịch (cuối tuần hoặc ngày lễ).")
        return parsed

    # ── state: levels, marks, settings, alerts ──
    def state(self) -> dict:
        data = _read_json(self.state_path, {})
        # "valuations" is never pruned (``_prune_state``): a card is kept for a stock not held
        for key in ("levels", "marks", "alerts", "shadow", "events", "notes", "names", "spark", "valuations"):
            data.setdefault(key, {} if key not in ("shadow",) else [])
        data.setdefault("thresholds", {"warn": WARN_PCT, "danger": DANGER_PCT})
        # the discipline layer (vnbot.kyluat): cut-loss flags, exit tickets, overrides, checks, buy/sell tags, the floor, the goal
        for key in ("flags", "exits_log", "overrides", "checks"):
            data.setdefault(key, [])
        for key in ("exits", "tags", "guard", "diet", "discipline"):
            data.setdefault(key, {})
        data.setdefault("goal", {"date": kyluat.DEFAULT_GOAL_DATE, "set_day": "", "source": "default"})
        return data

    @staticmethod
    def today() -> str:
        return today_vn()

    @staticmethod
    def now():
        return now_vn()

    def _update_state(self, change) -> dict:
        with self._lock:
            data = self.state()
            change(data)
            _write_json(self.state_path, data)
            return data

    def thresholds(self) -> tuple[float, float]:
        t = self.state()["thresholds"]
        try:
            return float(t.get("warn", WARN_PCT)), float(t.get("danger", DANGER_PCT))
        except (TypeError, ValueError):
            return WARN_PCT, DANGER_PCT

    def set_thresholds(self, warn: float, danger: float) -> None:
        if not (0 < warn < danger <= 100):
            raise LedgerError("Ngưỡng cảnh báo phải nhỏ hơn ngưỡng nguy hiểm, trong khoảng 1-100%.")
        self._update_state(lambda s: s.__setitem__("thresholds", {"warn": float(warn), "danger": float(danger)}))

    def levels(self) -> dict[str, dict]:
        return self.state()["levels"]

    def set_levels(self, symbol: str, stop: float | None, target: float | None, *, source: str = "owner",
                   record_lower: bool = True) -> dict:
        """Save the levels. ``record_lower``: an owner's lowering is flagged and scored (off for
        /tsgiu, whose override row records that one decision)."""
        symbol = symbol.upper()
        book = self.book()
        if symbol not in book.holdings:
            raise LedgerError(f"Danh mục không có {symbol}.")
        avg = book.holdings[symbol].avg
        if stop is not None and not (_finite(stop) and stop > 0):
            raise LedgerError("Mức cắt lỗ không hợp lệ.")
        if target is not None and not (_finite(target) and target > 0):
            raise LedgerError("Mức mục tiêu không hợp lệ.")
        if stop and target and target <= stop:
            raise LedgerError("Mục tiêu phải cao hơn mức cắt lỗ.")
        old = float((self.levels().get(symbol) or {}).get("stop") or 0)
        level = {"stop": float(stop) if stop else 0.0, "target": float(target) if target else None,
                 "source": source, "day": today_vn(), "avg": round(avg, 2)}
        self._update_state(lambda s: s["levels"].__setitem__(symbol, level))
        if record_lower and source == "owner" and old and stop and float(stop) < old:
            # never silent: a lowered cut-loss is recorded and scored (vnbot.kyluat)
            prices, _ = self.current_prices([symbol])
            level = {**level, "lower_text": kyluat.flag_lower(self, symbol, old, float(stop), book.holdings[symbol].qty,
                                                             prices.get(symbol))}
        return level

    def _site_levels_for(self, symbol: str, day: str, price: float) -> None:
        """A new buy gets the site's cut-loss/target unless the owner set them."""
        if self.site is None:
            return
        if (self.levels().get(symbol) or {}).get("source") == "owner":
            return
        try:
            stop, target = self.site(symbol, day, price)
        except Exception as exc:  # noqa: BLE001 - the buy is recorded anyway
            logger.warning("Tài sản: site levels for %s: %s", symbol, exc)
            return
        if stop or target:
            self._update_state(lambda s: s["levels"].__setitem__(
                symbol, {"stop": float(stop or 0), "target": float(target) if target else None,
                         "source": "site", "day": day}))

    def _prune_state(self, book: Book) -> None:
        held = set(book.holdings)

        def change(s):
            for key in ("levels", "marks", "exits"):      # an exit ticket of a stock no longer held says nothing
                for sym in [k for k in s[key] if k not in held]:
                    s[key].pop(sym, None)
        self._update_state(change)

    # ── the bot's Ledger shape (the scans, the morning brief, the intraday alerts read it) ──
    def ledger_view(self):
        """The portfolio as a ``Ledger`` (read only for the callers): holdings with their
        average cost, the lots still within T+2, the levels and the close marks."""
        from tradingagents.trading.ledger import Ledger, Position

        book = self.book()
        state = self.state()
        today = today_vn()
        view = Ledger(cash=book.cash, start_cash=book.capital)
        view.meta.update({"close_marks": 1, "taisan": True, "notes": state.get("notes") or {}})
        for symbol, h in sorted(book.holdings.items()):
            lv, mk = state["levels"].get(symbol) or {}, state["marks"].get(symbol) or {}
            # The recent lots, aged by date by every reader (sellable_on, the T+2 notes); a few
            # sessions of margin so a reader judging an earlier day still sees them.
            lots = [{"date": lot.day, "qty": lot.qty, "sessions": 0} for lot in h.lots
                    if not lot.free and _sessions_between(lot.day, today) < SETTLEMENT_SESSIONS + 3]
            view.positions[symbol] = Position(
                symbol=symbol, qty=h.qty, avg_price=h.avg, buy_date=h.first_day or today,
                stop=float(lv.get("stop") or 0.0), target1=lv.get("target"), target2=lv.get("target2"),
                last_price=float(mk.get("last_price") or 0.0), peak=float(mk.get("peak") or 0.0),
                unsettled=lots, last_date=str(mk.get("last_date") or ""),
                pending_ratio=float(mk.get("pending_ratio") or 0.0), pending_since=str(mk.get("pending_since") or ""))
        return view

    def save_view(self, view) -> None:
        """Keep what a run changed on the view: the close marks, and the levels moved by an
        ex-date (``Ledger.mark_to_history`` divides them by the ratio)."""
        def change(s):
            for symbol, pos in view.positions.items():
                s["marks"][symbol] = {"last_price": pos.last_price, "last_date": pos.last_date,
                                      "pending_ratio": pos.pending_ratio, "pending_since": pos.pending_since,
                                      "peak": pos.peak}
                old = s["levels"].get(symbol)
                if old is not None or pos.stop or pos.target1:
                    level = dict(old or {"source": "site", "day": today_vn()})
                    level.update(stop=float(pos.stop or 0.0), target=pos.target1, target2=pos.target2)
                    s["levels"][symbol] = level
            notes = view.meta.get("notes")
            if isinstance(notes, dict) and notes:
                s["notes"] = notes
        self._update_state(change)

    # ── prices ──
    def _forget_live(self) -> None:
        self._live = (0.0, {})

    def closes(self) -> dict[str, dict[str, float]]:
        return _read_json(self.prices_path, {})

    def save_closes(self, closes: dict[str, dict[str, float]]) -> None:
        with self._lock:
            data = self.closes()
            for symbol, days in closes.items():
                data.setdefault(symbol, {}).update({d: float(v) for d, v in days.items() if _finite(v) and v > 0})
            _write_json(self.prices_path, data)

    @staticmethod
    def close_on(series: dict[str, float], day: str) -> float | None:
        """The last close on or before ``day``."""
        if not series:
            return None
        if day in series:
            return series[day]
        days = sorted(series)
        i = bisect.bisect_right(days, day)
        return series[days[i - 1]] if i else None

    def current_prices(self, symbols: list[str]) -> tuple[dict[str, float], str]:
        """({symbol: price}, label): today's last match in the session (cached a minute), else
        the last close saved, else the price source's last close."""
        from tradingagents.market import vn_rules

        now = now_vn()
        day = now.strftime("%Y-%m-%d")
        phase = vn_rules.session_phase(now)
        out, label = {}, ""
        if vn_rules.is_session_day(day) and phase not in ("closed", "pre_open"):
            stamp, cached = self._live
            if time.time() - stamp > 60 or not set(symbols) <= set(cached):
                cached = self.market.live(symbols, day)
                self._live = (time.time(), cached)
            out = {s: cached[s] for s in symbols if s in cached}
            label = "đóng cửa hôm nay" if phase == "after_close" else f"khớp {now.strftime('%H:%M')}"
        saved = self.closes()
        marks = self.state()["marks"]
        for symbol in symbols:
            if symbol in out:
                continue
            series = saved.get(symbol) or {}
            last_saved = max((d for d in series if d <= day), default="")
            mark = marks.get(symbol) or {}
            if mark.get("last_date") and mark.get("last_price") and mark["last_date"] >= last_saved:
                price = float(mark["last_price"])          # the evening run's mark (the close it read)
            else:
                price = series.get(last_saved) if last_saved else None
            if price is None:
                try:
                    series = self.market.daily_closes(symbol, day)
                    price = series[max(series)] if series else None
                except Exception as exc:  # noqa: BLE001 - shown without a price
                    logger.warning("Tài sản: no price for %s: %s", symbol, exc)
            if price:
                out[symbol] = price
        return out, label or "giá đóng cửa gần nhất"

    # ── valuation ──
    def pending_rights(self) -> list[dict]:
        """The rights past their ex-date that the owner has not recorded yet (``refresh_events``)."""
        return [p for p in _read_json(self.pending_path, {}).get("items", []) if isinstance(p, dict)]

    def summary(self, prices: dict[str, float] | None = None, book: Book | None = None) -> dict:
        book = book or self.book()
        if prices is None:
            prices, label = self.current_prices(list(book.holdings))
        else:
            label = ""
        positions = []
        market = cost = 0.0
        for symbol, h in sorted(book.holdings.items()):
            price = prices.get(symbol)
            value = h.qty * price if price else None
            market += value or h.cost
            cost += h.cost
            positions.append({"symbol": symbol, "qty": h.qty, "avg": h.avg, "cost": h.cost, "price": price,
                              "value": value, "pnl": (value - h.cost) if value is not None else None,
                              "pnl_pct": ((value / h.cost - 1) * 100) if value is not None and h.cost else None,
                              "first_day": h.first_day})
        pending = sum(float(p.get("value") or 0) for p in self.pending_rights())
        total = book.cash + market + pending
        for p in positions:
            p["weight"] = (p["value"] or p["cost"]) / total * 100 if total > 0 else 0.0
        capital = book.capital
        return {"total": total, "market": market, "cash": book.cash, "pending": pending, "capital": capital,
                "pnl": total - capital, "pnl_pct": (total - capital) / capital * 100 if capital else None,
                "unrealized": market - cost, "realized": book.realized, "dividends": book.dividends,
                "sells": book.sells, "wins": book.wins, "losses": book.losses, "positions": positions,
                "label": label, "warnings": list(book.warnings)}

    # ── the asset history (chart) ──
    def history(self) -> list[dict]:
        """One point per session from the first entry: total assets, cash, stock value,
        capital, P&L, VNINDEX, and both indexed to 100 (the portfolio as fund units, so a
        deposit or a withdrawal is not a gain or a loss). Built from the journal and the
        saved closes, so an undo or a back-dated entry is reflected at once."""
        entries = self.entries()
        closes = self.closes()
        vni = closes.get("VNINDEX") or {}
        pending = _read_json(self.pending_path, {}).get("by_day", {})
        rebuilt_until = str(self.state().get("rebuilt_until") or "")
        days = sorted(d for d in vni if d >= (min((e.day for e in effective(entries)), default="9999")))
        if not days:
            return []
        points: list[dict] = []
        books: dict[str, tuple] = {}

        def snap(day, book):
            books[day] = (book.cash, {s: (h.qty, h.cost) for s, h in book.holdings.items()}, book.capital,
                          dict(book.flows))
        try:
            derive(entries, on_day=snap)
        except LedgerError:
            return []
        entry_days = sorted(books)
        units = 0.0
        unit_value = 100.0
        prev_capital = 0.0
        for day in days:
            i = bisect.bisect_right(entry_days, day)
            if not i:
                continue
            cash, held, capital, _ = books[entry_days[i - 1]]
            market = 0.0
            for symbol, (qty, cost) in held.items():
                price = self.close_on(closes.get(symbol) or {}, day)
                market += qty * price if price else cost
            total = cash + market + float(pending.get(day) or 0)
            flow = capital - prev_capital            # deposits less withdrawals since the last point
            prev_capital = capital
            if units <= 0:
                unit_value = 100.0
                units = total / unit_value if total > 0 else 0.0
            else:
                unit_value = (total - flow) / units if units else 100.0
                units += flow / unit_value if unit_value > 0 else 0.0
            points.append({"day": day, "total": round(total), "cash": round(cash), "market": round(market),
                           "capital": round(capital), "pnl": round(total - capital), "held": len(held),
                           "nav": round(unit_value, 4), "vnindex": vni.get(day), "rebuilt": day <= rebuilt_until})
        if points and points[0]["vnindex"]:
            base = points[0]["vnindex"]
            for p in points:
                p["vni_index"] = round(p["vnindex"] / base * 100, 4) if p["vnindex"] else None
        return points

    # ── daily upkeep: closes, rights, the history rebuild ──
    def symbols_held_since(self, start: str) -> dict[str, tuple[str, str]]:
        """{symbol: (first day, last day)} of every holding from ``start``: a stock held
        before ``start`` from ``start``; a stock sold until its sale day (held at the close
        of every session before it); a stock still held until today."""
        spans: dict[str, list[str]] = {}
        prev: set[str] = set()

        def seen(day, book):
            nonlocal prev
            now = set(book.holdings)
            if day >= start:
                for symbol in now | prev:
                    # not seen since ``start`` but held before: held from ``start``
                    span = spans.setdefault(symbol, [start if symbol in prev else day, day])
                    span[1] = day
            prev = now
        derive(self.entries(), on_day=seen)
        today = today_vn()
        for symbol in prev:
            spans.setdefault(symbol, [start, today])[1] = today
        return {s: (a, b) for s, (a, b) in spans.items()}

    def rebuild(self, *, through: str | None = None) -> int:
        """Fill the saved closes from the first entry to ``through`` (default yesterday's
        session): VNINDEX and every symbol over the days it was held, as traded
        (unadjusted). Returns the closes added."""
        from tradingagents.market import vn_rules

        ordered = effective(self.entries())
        if not ordered:
            return 0
        start = ordered[0].day
        end = through or vn_rules.prev_session(today_vn())
        added = {}
        vni = self.market.daily_closes("VNINDEX", end)
        added["VNINDEX"] = {d: c for d, c in vni.items() if start <= d <= end}
        for symbol, (first, last) in self.symbols_held_since(start).items():
            try:
                series = self.market.unadjusted_closes(symbol, first, min(last, end))
            except Exception as exc:  # noqa: BLE001 - that symbol is valued at cost on those days
                logger.warning("Tài sản: closes of %s unavailable: %s", symbol, exc)
                continue
            added[symbol] = series
        self.save_closes(added)
        self._update_state(lambda s: s.__setitem__("rebuilt_until", end))
        return sum(len(v) for v in added.values())

    def rebuild_in_background(self, on_done=None) -> bool:
        """``rebuild`` and ``refresh_events`` in a thread (after an import); False when one is
        already running. ``on_done(added, error)`` afterwards: closes added, or the exception."""
        with self._lock:
            if self.rebuilding:
                return False
            self.rebuilding = True

        def run():
            added, error = 0, None
            try:
                added = self.rebuild()
                self.refresh_events()
                self.refresh_analytics()
            except Exception as exc:  # noqa: BLE001 - the 15:30 run tries again
                logger.exception("Tài sản: rebuild failed")
                error = exc
            finally:
                self.rebuilding = False
            if on_done is not None:
                try:
                    on_done(added, error)
                except Exception:  # noqa: BLE001
                    logger.exception("Tài sản: rebuild callback failed")
        threading.Thread(target=run, name="taisan-rebuild", daemon=True).start()
        return True

    def record_day(self, day: str) -> dict:
        """The closes of session ``day`` for the holdings and VNINDEX (the price source's
        last bar is the close as traded on that day)."""
        book = self.book()
        out = {}
        for symbol in ["VNINDEX", *book.holdings]:
            try:
                series = self.market.daily_closes(symbol, day)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Tài sản: close of %s on %s unavailable: %s", symbol, day, exc)
                continue
            if day in series:
                out[symbol] = {day: series[day]}
        self.save_closes(out)
        return out

    def refresh_events(self, day: str | None = None) -> list[dict]:
        """The rights waiting to arrive (ex-date passed, not recorded with /tsthuong or
        /tscotuc) and those coming within two weeks, from VCI; saved for the pages."""
        from tradingagents.market import vn_rules

        day = day or today_vn()
        entries = self.entries()
        ordered = effective(entries)
        if not ordered:
            _write_json(self.pending_path, {"items": [], "upcoming": [], "by_day": {}})
            return []
        start = (_date.fromisoformat(day) - timedelta(days=PENDING_DAYS)).isoformat()
        horizon = (_date.fromisoformat(day) + timedelta(days=21)).isoformat()
        spans = self.symbols_held_since(max(start, ordered[0].day))
        items, upcoming = [], []
        prices, _ = self.current_prices(list(spans)) if spans else ({}, "")
        old = _read_json(self.pending_path, {})
        by_day = old.get("by_day") if isinstance(old.get("by_day"), dict) else {}
        for symbol in spans:
            try:
                events = [ev for raw in self.market.events(symbol, start, horizon) if (ev := parse_event(raw))]
            except Exception as exc:  # noqa: BLE001 - kept from the last refresh
                logger.warning("Tài sản: events of %s unavailable: %s", symbol, exc)
                items += [p for p in old.get("items", []) if p.get("symbol") == symbol]
                upcoming += [p for p in old.get("upcoming", []) if p.get("symbol") == symbol]
                continue
            for ev in events:
                before = vn_rules.prev_session(ev["ex"])
                eligible = 0
                try:
                    held = derive(entries, upto=before).holdings.get(symbol)
                    eligible = held.qty if held else 0
                except LedgerError:
                    eligible = 0
                if ev["ex"] > day:
                    current = self.book().holdings.get(symbol)
                    if current:
                        upcoming.append({**ev, "symbol": symbol, "qty": current.qty})
                    continue
                if eligible <= 0:
                    continue
                kind = "BONUS" if ev["kind"] == "STOCK" else "DIVIDEND"
                if any(e.kind == kind and e.symbol == symbol and e.day >= ev["ex"] for e in ordered):
                    continue                                  # recorded by the owner
                if ev["kind"] == "STOCK":
                    shares = int(eligible * ev["ratio"] + 1e-9)
                    price = prices.get(symbol) or 0.0
                    item = {**ev, "symbol": symbol, "eligible": eligible, "shares": shares,
                            "value": round(shares * price)}
                else:
                    item = {**ev, "symbol": symbol, "eligible": eligible,
                            "value": round(eligible * ev["value"] * (1 - DIVIDEND_TAX))}
                if item["value"] > 0 or item.get("shares"):
                    items.append(item)
        by_day[day] = round(sum(float(p.get("value") or 0) for p in items))
        by_day = {d: v for d, v in sorted(by_day.items())[-400:]}
        _write_json(self.pending_path, {"items": items, "upcoming": sorted(upcoming, key=lambda p: p["ex"]),
                                        "by_day": by_day, "at": now_vn().isoformat(timespec="minutes")})
        return items

    def upcoming_rights(self) -> list[dict]:
        return [p for p in _read_json(self.pending_path, {}).get("upcoming", []) if isinstance(p, dict)]

    # ── the site's view of the holdings (sieutinhieu.vn, SIEUTINHIEU=1) ──
    def site_states(self, symbols: list[str], day: str, *, refresh: bool = False) -> dict[str, dict]:
        """{symbol: latest signal on or before ``day``} of the site; {} when it is off.
        ``refresh``: ask the site for the holdings first (the evening report, after 15:10)."""
        from vnbot import external

        if not external.enabled() or not symbols:
            return {}
        store = external.SignalStore(self.dir.parent)
        if refresh:
            try:
                store.refresh(symbols, fresh_after=f"{day}T{external.SITE_FINAL_TIME}")
            except Exception as exc:  # noqa: BLE001 - the saved signals are used
                logger.warning("Tài sản: site signals not refreshed: %s", exc)
        return {s: sig for s in symbols if (sig := store.state(s, day))}

    # ── alerts and their shadow results ──
    def record_alert(self, level: str, symbol: str, price: float | None, day: str, at: str = "", *, hook: bool = True) -> bool:
        """Keep an alert sent for a holding (or a valuation card's zone), once per (day, symbol,
        level), to judge later how the price went after it (``settle_shadow``). True when it is new.
        ``hook``: also tell ``on_alert`` (the AI's look at the news); off for an alert the evening
        report repeats and for the zone alerts."""
        if level not in ALERT_LEVELS:
            return False
        added = []

        def change(s):
            rows = s["shadow"]
            if any(r.get("day") == day and r.get("symbol") == symbol and r.get("level") == level for r in rows):
                return
            rows.append({"day": day, "at": at or now_vn().strftime("%H:%M"), "symbol": symbol, "level": level,
                         "price": float(price) if price else None})
            del rows[:-500]
            added.append(True)
        self._update_state(change)
        if added and hook and self.on_alert is not None:
            try:
                self.on_alert(level, symbol, price, day)
            except Exception as exc:  # noqa: BLE001 - the alert itself is kept and sent anyway
                logger.warning("Tài sản: alert hook for %s failed: %s", symbol, exc)
        return bool(added)

    def settle_shadow(self, day: str | None = None) -> int:
        """The close 5 and 10 sessions after each alert against the close of its day (the
        price source's adjusted closes, so a dividend in between is not a fall)."""
        day = day or today_vn()
        rows = self.state()["shadow"]
        todo = [r for r in rows if r.get("r10") is None and r.get("day", "") < day]
        if not todo:
            return 0
        results = {}
        for symbol in {r["symbol"] for r in todo}:
            try:
                series = self.market.daily_closes(symbol, day)
            except Exception as exc:  # noqa: BLE001 - settled on a later day
                logger.warning("Tài sản: shadow closes of %s: %s", symbol, exc)
                continue
            days = sorted(series)
            for r in [r for r in todo if r["symbol"] == symbol]:
                if r["day"] not in series:
                    continue
                i = days.index(r["day"])
                base = series[r["day"]]
                out = {}
                for n in SHADOW_SESSIONS:
                    if i + n < len(days):
                        out[f"r{n}"] = round((series[days[i + n]] / base - 1) * 100, 2)
                if out:
                    results[(r["day"], r["symbol"], r["level"])] = out
        if not results:
            return 0

        def change(s):
            for r in s["shadow"]:
                got = results.get((r.get("day"), r.get("symbol"), r.get("level")))
                if got:
                    r.update(got)
        self._update_state(change)
        return len(results)

    def settle_discipline(self, day: str | None = None) -> int:
        """The discipline layer's rows (vnbot.kyluat): the overrides and the lowered cut-losses
        at 5/10/20 sessions from their day's close, the buy and sell tags at 10/20 from the
        owner's own price. Downloads outside the lock; merged under it."""
        day = day or today_vn()
        st = self.state()
        # rows are matched back by their own keys, never by position: a /tsgiu or a /tsmua recorded while
        # the closes download would otherwise shift every result onto the wrong row
        groups = [("overrides", {self._override_key(r): dict(r) for r in st["overrides"]}, (5, 10, 20), None),
                  ("flags", {self._flag_key(r): dict(r) for r in st["flags"] if r.get("kind") == "lower"}, (5, 10, 20), None),
                  ("tags", {k: dict(r) for k, r in st["tags"].items() if r.get("kind") in ("buy", "sell") and r.get("day")},
                   (10, 20), "price")]
        done = 0
        filled = {}
        for name, rows, sessions, base in groups:
            try:
                done += kyluat.settle_rows(self, list(rows.values()), sessions, through=day, base_key=base)
            except Exception as exc:  # noqa: BLE001 - settled on a later day
                logger.warning("Tài sản: discipline rows (%s) not settled: %s", name, exc)
            filled[name] = rows
        if not done:
            return 0
        results = ("r5", "r10", "r20")

        def change(s):
            for row in s["overrides"]:
                new = filled["overrides"].get(self._override_key(row))
                if new:
                    row.update({k: v for k, v in new.items() if k in results})
            for row in s["flags"]:
                new = filled["flags"].get(self._flag_key(row)) if row.get("kind") == "lower" else None
                if new:
                    row.update({k: v for k, v in new.items() if k in results})
            for key, row in s["tags"].items():
                new = filled["tags"].get(key)
                if new:
                    row.update({k: v for k, v in new.items() if k in ("r10", "r20")})
        self._update_state(change)
        return done

    @staticmethod
    def _override_key(r: dict) -> tuple:
        return (r.get("day"), r.get("symbol"), r.get("ticket_opened"), r.get("stop"))

    @staticmethod
    def _flag_key(r: dict) -> tuple:
        return (r.get("day"), r.get("symbol"), r.get("old"), r.get("new"))

    def shadow_stats(self) -> list[dict]:
        """Per alert level: how many settled, the mean move 5 and 10 sessions later, and how
        often the alert was right 10 sessions later: the price lower (selling on it was right),
        higher for "re" (``UP_IS_RIGHT``: the Rẻ zone said the price was low)."""
        out = []
        rows = self.state()["shadow"]
        for level, (icon, name, _) in ALERT_LEVELS.items():
            done = [r for r in rows if r.get("level") == level and r.get("r5") is not None]
            ten = [r for r in done if r.get("r10") is not None]
            up = level in UP_IS_RIGHT
            out.append({"level": level, "icon": icon, "name": name, "alerts": len([r for r in rows if r.get("level") == level]),
                        "settled": len(done), "up": up,
                        "r5": sum(r["r5"] for r in done) / len(done) if done else None,
                        "r10": sum(r["r10"] for r in ten) / len(ten) if ten else None,
                        "right": sum(1 for r in ten if (r["r10"] > 0 if up else r["r10"] < 0)) / len(ten) * 100 if ten else None})
        return out

    # ── valuation cards entered by hand (/tsdinhgia, the page): informational, no level of the portfolio ──
    def valuations(self) -> dict[str, dict]:
        return self.state()["valuations"]

    def set_valuation(self, symbol: str, bear, base, bull, *, eab=None, expires: str = "", note: str = "",
                      source: str = "") -> dict:
        """Save the owner's card of ``symbol`` (held or not), replacing its earlier one."""
        symbol = (symbol or "").strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{1,10}", symbol):
            raise LedgerError(f"Mã không hợp lệ: {symbol!r}.")
        levels = {"Bear": bear, "Base": base, "Bull": bull, **({"eab": eab} if eab is not None else {})}
        for name, value in levels.items():
            if not _finite(value) or float(value) < 100:
                raise LedgerError(f"{name} không hợp lệ: nhập giá theo đồng, ví dụ 30164 hoặc 30.164.")
        bear, base, bull = float(bear), float(base), float(bull)
        if not bear < base < bull:
            raise LedgerError(f"Cần Bear < Base < Bull (anh nhập Bear {vnd(bear)}, Base {vnd(base)}, Bull {vnd(bull)}).")
        if eab is not None and not float(eab) < base:
            raise LedgerError(f"eab phải thấp hơn Base {vnd(base)} (eab = Base trừ biên an toàn).")
        today = today_vn()
        if expires and expires < today:
            raise LedgerError(f"Hạn dùng {ddmmyyyy(expires)} đã qua.")
        card = {"bear": bear, "base": base, "bull": bull, "eab": float(eab) if eab is not None else None,
                "expires": expires or "", "note": (note or "").strip()[:200], "day": today,
                "at": now_vn().isoformat(timespec="seconds"), "source": source}

        def change(s):
            if symbol not in s["valuations"] and len(s["valuations"]) >= MAX_VALUATIONS:
                raise LedgerError(f"Đã có {MAX_VALUATIONS} thẻ định giá (tối đa): xoá bớt bằng /tsdinhgia MÃ xoa.")
            s["valuations"][symbol] = card
        self._update_state(change)
        return card

    def clear_valuation(self, symbol: str) -> str:
        symbol = (symbol or "").strip().upper()
        if symbol not in self.valuations():
            raise LedgerError(f"Không có thẻ định giá {symbol}.")
        self._update_state(lambda s: s["valuations"].pop(symbol, None))
        return f"✅ Đã xoá thẻ định giá {symbol}."

    def valuation_status(self, symbol: str, card: dict, day: str | None = None) -> dict:
        """{"expired": past the card's own date, "stale": the first ex-date ({ex, what}) after the day
        it was entered, up to ``day`` - its per-share levels are then on the old basis}. The events
        are asked once a day per card and kept in it (asked again at the next look after a failure)."""
        day = day or today_vn()
        if card.get("checked") != day:
            start = (_date.fromisoformat(card["day"]) - timedelta(days=EX_LOOKBACK_DAYS)).isoformat()
            try:
                found = sorted({(ev["ex"], ev["what"]) for raw in self.market.events(symbol, start, day)
                                if (ev := ex_event(raw)) and card["day"] < ev["ex"] <= day})
            except Exception as exc:  # noqa: BLE001 - the last list kept; asked again at the next look
                logger.warning("Tài sản: events of %s for its valuation card unavailable: %s", symbol, exc)
            else:
                ex = [{"ex": d, "what": w} for d, w in found]

                def change(s):
                    kept = s["valuations"].get(symbol)
                    if kept and kept.get("at") == card.get("at"):       # not a card entered meanwhile
                        kept.update(checked=day, ex=ex)
                self._update_state(change)
                card = {**card, "checked": day, "ex": ex}
        return {"expired": bool(card.get("expires")) and card["expires"] < day,
                "stale": (card.get("ex") or [None])[0]}

    def valuation_line(self, symbol: str, card: dict, price: float | None, status: dict) -> str:
        """One card: its levels, today's zone and MOS, its date, and HẾT HẠN / cần cập nhật."""
        now = f" → giá {vnd(price)}: {card_zone(card, price)}, {mos_text(card, price)}" if price else " → chưa có giá"
        flags = []
        if status.get("stale"):
            flags.append(f"⚠️ cần cập nhật: GDKHQ {ddmmyyyy(status['stale']['ex'])} ({status['stale']['what']})")
        if status.get("expired"):
            flags.append(f"⌛ HẾT HẠN {ddmmyyyy(card['expires'])}")
        elif card.get("expires"):
            flags.append(f"hạn {ddmmyyyy(card['expires'])}")
        return (f"• {symbol} {card_levels(card)}{now} · nhập {ddmmyyyy(card['day'])}" + "".join(f" · {f}" for f in flags)
                + (f" · nguồn: {card['note']}" if card.get("note") else ""))

    def valuations_text(self, symbols: list[str] | None = None) -> str:
        """``/tsdinhgia``: every card (or those of ``symbols``) with today's price."""
        cards = self.valuations()
        if symbols:
            missing = [s for s in symbols if s not in cards]
            if missing:
                return f"Chưa có thẻ định giá {', '.join(missing)}. {VALUATION_USAGE}"
            cards = {s: cards[s] for s in symbols}
        if not cards:
            return f"Chưa có thẻ định giá nào. {VALUATION_USAGE}"
        prices, label = self.current_prices(sorted(cards))
        day = today_vn()
        lines = [f"📐 THẺ ĐỊNH GIÁ ANH NHẬP · {label} (tham khảo; không đổi cắt lỗ, mục tiêu hay quyết định của bot)"]
        lines += [self.valuation_line(s, c, prices.get(s), self.valuation_status(s, c, day)) for s, c in sorted(cards.items())]
        lines.append("Rẻ: giá ≤ Base · Đắt: giá ≥ Bull · MOS = (Base − giá)/Base. Bot báo trong phiên khi giá vào Rẻ "
                     "hoặc Đắt; thẻ hết hạn hoặc qua ngày GDKHQ thì ngừng báo. " + VALUATION_USAGE)
        return "\n".join(lines)

    def valuation_set_text(self, symbol: str, card: dict) -> str:
        prices, label = self.current_prices([symbol])
        price = prices.get(symbol)
        now = (f"Giá {vnd(price)} ({label}) → {card_zone(card, price)}, {mos_text(card, price)} (Rẻ: giá ≤ Base · "
               "Đắt: giá ≥ Bull)" if price else "Chưa có giá của mã này.")
        return (f"✅ Thẻ định giá {symbol} (anh nhập, chỉ để tham khảo; không đổi cắt lỗ, mục tiêu hay quyết định của bot):\n"
                + card_levels(card) + (f" · hạn {ddmmyyyy(card['expires'])}" if card.get("expires") else "")
                + (f" · nguồn: {card['note']}" if card.get("note") else "") + f"\n{now}\n"
                "Bot báo trong phiên khi giá vào vùng Rẻ hoặc Đắt (một lần mỗi lần vào vùng); thẻ ngừng báo khi hết hạn "
                "hoặc qua ngày GDKHQ (cổ tức, thưởng, quyền mua): khi đó nhập lại thẻ.")

    # ── allocation and risk ──
    def allocation(self, summary: dict) -> dict:
        """Weights by symbol and by sector with their level (ok / watch / danger)."""
        from tradingagents.quant.sector import get_primary_sector_label

        warn, danger = self.thresholds()
        by_symbol, by_sector = [], {}
        for p in summary["positions"]:
            w = p["weight"]
            by_symbol.append({"symbol": p["symbol"], "pct": w,
                              "level": "danger" if w >= danger else "watch" if w >= warn else "ok"})
            label = get_primary_sector_label(p["symbol"])
            by_sector[label] = by_sector.get(label, 0.0) + w
        sectors = [{"sector": k, "pct": v, "level": "danger" if v >= danger * 1.5 else "watch" if v >= danger else "ok"}
                   for k, v in sorted(by_sector.items(), key=lambda kv: -kv[1])]
        return {"symbols": sorted(by_symbol, key=lambda r: -r["pct"]), "sectors": sectors, "warn": warn, "danger": danger}

    def refresh_analytics(self, day: str | None = None, sessions: int = 120) -> dict:
        """Volatility, correlations, a risk-parity split and a diversification score of the
        holdings, from the last ``sessions`` adjusted closes; saved for the page and report."""
        day = day or today_vn()
        book = self.book()
        symbols = sorted(book.holdings)
        returns, spark = {}, {}
        for symbol in symbols:
            try:
                series = self.market.daily_closes(symbol, day)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Tài sản: analytics closes of %s: %s", symbol, exc)
                continue
            days = sorted(series)[-(sessions + 1):]
            returns[symbol] = {days[i]: series[days[i]] / series[days[i - 1]] - 1 for i in range(1, len(days))}
            spark[symbol] = {"days": days[-SPARK_SESSIONS:], "c": [round(series[d], 2) for d in days[-SPARK_SESSIONS:]]}
        summary = self.summary(book=book)
        weights = {p["symbol"]: (p["value"] or p["cost"]) for p in summary["positions"]}
        total_w = sum(weights.values()) or 1.0
        vol = {}
        for s, r in returns.items():
            vals = list(r.values())
            if len(vals) >= 20:
                mean = sum(vals) / len(vals)
                vol[s] = math.sqrt(sum((v - mean) ** 2 for v in vals) / (len(vals) - 1))
        pairs = []
        corr: dict[tuple[str, str], float] = {}
        names = [s for s in symbols if s in vol]
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                common = sorted(set(returns[a]) & set(returns[b]))
                if len(common) < 20:
                    continue
                xa, xb = [returns[a][d] for d in common], [returns[b][d] for d in common]
                ma, mb = sum(xa) / len(xa), sum(xb) / len(xb)
                cov = sum((p - ma) * (q - mb) for p, q in zip(xa, xb, strict=True)) / (len(common) - 1)
                da = math.sqrt(sum((p - ma) ** 2 for p in xa) / (len(common) - 1))
                db = math.sqrt(sum((q - mb) ** 2 for q in xb) / (len(common) - 1))
                c = cov / (da * db) if da and db else 0.0
                corr[(a, b)] = c
                pairs.append({"a": a, "b": b, "corr": round(c, 2),
                              "risk": "high" if abs(c) > 0.7 else "moderate" if abs(c) > 0.4 else "low"})
        inv = {s: 1 / vol[s] for s in names if vol[s] > 0}
        inv_total = sum(inv.values()) or 1.0
        stock_w = {s: weights.get(s, 0.0) / total_w for s in names}
        port_var = 0.0
        for a in names:
            for b in names:
                c = 1.0 if a == b else corr.get((a, b), corr.get((b, a), 0.0))
                port_var += stock_w[a] * stock_w[b] * vol[a] * vol[b] * c
        hhi = sum(w * w for w in stock_w.values())
        n = len(names)
        result = {
            "day": day,
            "vol_pct": round(math.sqrt(max(port_var, 0.0)) * math.sqrt(252) * 100, 1) if names else None,
            "diversification": round((1 / hhi - 1) / (n - 1) * 100) if n > 1 and hhi else (0 if n == 1 else None),
            "pairs": sorted(pairs, key=lambda p: -abs(p["corr"]))[:10],
            "weights": [{"symbol": s, "current": round(stock_w[s] * 100, 1),
                         "suggested": round(inv[s] / inv_total * 100, 1) if s in inv else None,
                         "vol_pct": round(vol[s] * math.sqrt(252) * 100, 1)} for s in names],
        }
        def change(s):
            s["analytics"] = result
            s["spark"] = spark
        self._update_state(change)
        self.refresh_names(symbols)
        return result

    # ── company names (the page's stock cards) ──
    def refresh_names(self, symbols: list[str]) -> dict[str, str]:
        """Keep the short company name of ``symbols`` (VCI listing, cached a day)."""
        from tradingagents.dataflows.symbols import listing_entry

        found = {}
        for symbol in symbols:
            try:
                row = listing_entry(symbol) or {}
            except Exception as exc:  # noqa: BLE001 - a card without a name
                logger.info("Tài sản: no listing row for %s: %s", symbol, exc)
                continue
            name = str(row.get("organShortName") or row.get("organName") or "").strip()
            if name:
                found[symbol] = name[:80]
        if found:
            self._update_state(lambda s: s.setdefault("names", {}).update(found))
        return found

    def names(self, symbols: list[str]) -> dict[str, str]:
        """The saved company names; the listing kept on disk for the others (no request)."""
        saved = dict(self.state().get("names") or {})
        missing = [s for s in symbols if s not in saved]
        if missing:
            try:
                from tradingagents.dataflows.symbols import _saved_listing

                rows = {str(r.get("symbol", "")).upper(): r for r in (_saved_listing() or [])}
            except Exception:  # noqa: BLE001
                rows = {}
            for symbol in missing:
                row = rows.get(symbol) or {}
                name = str(row.get("organShortName") or row.get("organName") or "").strip()
                if name:
                    saved[symbol] = name[:80]
        return {s: saved[s] for s in symbols if s in saved}

    # ── the evening report, kept for the page ──
    def save_report(self, day: str, text: str) -> None:
        _write_json(self.dir / "last_report.json", {"day": day, "at": now_vn().isoformat(timespec="minutes"), "text": text})

    def last_report(self) -> dict:
        data = _read_json(self.dir / "last_report.json", {})
        return data if isinstance(data, dict) and data.get("text") else {}

    # ── what to do at the next session ──
    def actions(self, day: str, summary: dict, closes: dict[str, float], site: dict[str, dict]) -> list[tuple[int, str]]:
        """(rank, line) of what the owner should look at for the session after ``day``:
        ⛔ the close under the cut-loss, 🔴 the site turned SELL, 🎯 the target reached,
        ⚠️ a holding over the danger weight. Shares sellable at that session's open are said."""
        from tradingagents.market import vn_rules

        nxt = vn_rules.add_sessions(day, 1)
        book = self.book()
        levels = self.levels()
        _, danger = self.thresholds()
        out = []
        for p in summary["positions"]:
            symbol = p["symbol"]
            h = book.holdings.get(symbol)
            if h is None:
                continue
            morning, afternoon = h.sellable(nxt, afternoon=False), h.sellable(nxt, afternoon=True)
            can = (f"bán được {vnd(morning)} cp" if morning >= h.qty
                   else f"bán được {vnd(morning)} cp từ sáng, {vnd(afternoon)} cp từ chiều" if afternoon > morning
                   else f"mới bán được {vnd(morning)}/{vnd(h.qty)} cp")
            close = closes.get(symbol)
            lv = levels.get(symbol) or {}
            stop, target = float(lv.get("stop") or 0), lv.get("target")
            sig = site.get(symbol)
            if close and stop and close <= stop:
                who = "anh đặt" if lv.get("source") == "owner" else f"theo trang {ddmm(lv.get('plan_day') or lv.get('day') or '')}"
                out.append((0, f"⛔ {symbol}: đóng cửa {vnd(close)} ≤ cắt lỗ {vnd(stop)} ({who}) → BÁN phiên {ddmm(nxt)} ({can})"))
            if not stop and p["weight"] >= kyluat.NO_STOP_WEIGHT_PCT:
                series = self.closes().get(symbol) or {}
                recent = sorted(series)[-10:]
                low = min((series[d] for d in recent), default=None)
                out.append((2, f"❗ {symbol} chưa có cắt lỗ → rủi ro không giới hạn · đặt: /tscatlo {symbol} GIÁ"
                               + (f" · tham khảo: thấp nhất 10 phiên {vnd(low)} (chưa đo, chỉ để anh có một mốc)" if low else "")))
            if sig and sig.get("side") == "SELL":
                from vnbot import external

                when = "hôm nay" if sig.get("day") == day else f"từ {ddmm(sig.get('day', ''))}"
                if external.SellAlerts.final(sig):
                    out.append((1, f"🔴 {symbol}: Siêu tín hiệu BÁN {when} @ {vnd(sig.get('price'))} → BÁN phiên "
                                   f"{ddmm(nxt)}, ưu tiên ATO/đầu phiên ({can})"))
                else:          # published in the session, not confirmed at the close: watch, no order
                    out.append((2, f"🟠 {symbol}: trang đổi sang BÁN trong phiên lúc {str(sig.get('created', ''))[11:16]}, "
                                   "chưa chốt → giữ theo quy tắc, theo dõi (chỉ 🔴 đã chốt mới là việc bán)"))
            if close and target and close >= float(target):
                out.append((3, f"🎯 {symbol}: đóng cửa {vnd(close)} ≥ mục tiêu {vnd(target)} → cân nhắc chốt một phần, "
                               "dời cắt lỗ lên giá vốn"))
            if p["weight"] >= danger:
                out.append((4, f"⚠️ {symbol} chiếm {p['weight']:.1f}% tài sản (ngưỡng nguy hiểm {danger:g}%)".replace(".", ",")))
        return sorted(out, key=lambda r: r[0])

    # ── texts ──
    def overview_text(self) -> str:
        """``/ts``: the portfolio now."""
        book = self.book()
        if not effective(self.entries()):
            return ("💼 TÀI SẢN · chưa có dữ liệu\n"
                    "Nhập file Excel từ app cũ: gửi file .xlsx vào Telegram (hoặc trang /taisan), rồi /tsnhap xacnhan.\n"
                    "Hoặc bắt đầu tay: /tsnap 50000000 rồi /tsmua MÃ KL GIÁ.")
        s = self.summary(book=book)
        lines = [f"💼 TÀI SẢN · {s['label']}" if s["label"] else "💼 TÀI SẢN"]
        lines += self._summary_lines(s)
        if s["positions"]:
            lines.append("")
            lines += self._position_lines(s, book)
        lines += ["", "Lệnh: /tsmua · /tsban · /tskiemtra · /tsnhatky · /tshuy · /tsbaocao · /tsxuat · trang web /taisan"]
        return "\n".join(lines)

    def _summary_lines(self, s: dict) -> list[str]:
        lines = [f"Tổng tài sản {vnd(s['total'])}",
                 f"Vốn gốc {vnd(s['capital'])} · lãi/lỗ {signed(s['pnl'])}" + (f" ({pct(s['pnl_pct'])})" if s["pnl_pct"] is not None else ""),
                 f"Tiền mặt {vnd(s['cash'])} · cổ phiếu {vnd(s['market'])}"
                 + (f" · quyền chờ về {vnd(s['pending'])}" if s["pending"] else ""),
                 (f"Đã chốt {signed(s['realized'])} ({s['sells']} lệnh bán, thắng {s['wins']}/{s['sells']})"
                  if s["sells"] else "Chưa bán lệnh nào")
                 + (f" · cổ tức {vnd(s['dividends'])}" if s["dividends"] else "")]
        if s["positions"]:
            lines.append(kyluat.risk_line(kyluat.risk_summary(self, s)))
        lines += [f"⚠️ {w}" for w in s["warnings"]]
        return lines

    def _position_lines(self, s: dict, book: Book, site: dict | None = None) -> list[str]:
        levels = self.levels()
        lines = []
        for p in sorted(s["positions"], key=lambda p: -p["weight"]):
            price = f"{vnd(p['price'])}" if p["price"] else "chưa có giá"
            pnl = f" · {signed(p['pnl'])} ({pct(p['pnl_pct'])})" if p["pnl"] is not None else ""
            weight = f"{p['weight']:.1f}%".replace(".", ",")
            lines.append(f"• {p['symbol']} {vnd(p['qty'])} cp · vốn {vnd(p['avg'])} → {price}{pnl} · {weight}")
            lv = levels.get(p["symbol"]) or {}
            parts = []
            if lv.get("stop"):
                parts.append(f"cắt lỗ {vnd(lv['stop'])}" + (" (anh đặt)" if lv.get("source") == "owner" else " (trang)"))
                if lv.get("site_now") is not None and float(lv["site_now"]) != float(lv["stop"]):
                    parts.append(f"trang mới: {vnd(lv['site_now'])} ({'thấp hơn' if lv['site_now'] < lv['stop'] else 'cao hơn'}; "
                                 "bot giữ mức cũ)")
            if lv.get("target"):
                parts.append(f"mục tiêu {vnd(lv['target'])}")
            sig = (site or {}).get(p["symbol"])
            if sig:
                parts.append(f"Siêu tín hiệu {'MUA' if sig.get('side') == 'BUY' else 'BÁN'} từ {ddmm(sig.get('day', ''))}")
            h = book.holdings.get(p["symbol"])
            pend = h.pending_lots(today_vn()) if h else []
            if pend:
                parts.append("chưa về: " + ", ".join(f"{vnd(q)} cp bán từ chiều {ddmm(d)}" for d, q in pend))
            if parts:
                lines.append("   " + " · ".join(parts))
        return lines

    def report_text(self, day: str, *, refresh_site: bool = True, settle: bool = False) -> str:
        """The evening report of session ``day`` (Telegram and Zoom, 15:30): the figures, the
        work for the next session (every ⛔/🔴 as a dated ticket, ``vnbot.kyluat``), each
        holding, the allocation, the planned risk and the floor, the rights; on the week's
        last session also the alert results, the discipline meter and the recovery goal.
        ``settle``: the steps that change the state (levels adopted from the site, tickets
        opened, the floor log, the discipline meter) run - only the scheduled evening run
        passes it; a report asked during the session (/tsbaocao, the web) reads the state as
        it is, so a live price never opens a ticket dated the session before.
        A fault in the discipline layer costs its block, never the report."""
        from tradingagents.market import vn_rules

        book = self.book()
        closes_saved = self.closes()
        prices = {s: self.close_on(closes_saved.get(s) or {}, day) for s in book.holdings}
        missing = [s for s, v in prices.items() if not v]
        if missing:
            live, _ = self.current_prices(missing)
            prices.update(live)
        known = {k: v for k, v in prices.items() if v}
        site = self.site_states(list(book.holdings), day, refresh=refresh_site)
        pins = self._guarded("refresh_levels", lambda: kyluat.refresh_levels(self, day, site, known), []) if settle else []
        if settle:
            self._guarded("diet", lambda: self._mark_diet(day))
        s = self.summary(known, book=book)
        lines = [f"💼 TÀI SẢN · phiên {ddmmyyyy(day)}", ""]
        hist = self.history()
        today_pt = next((p for p in reversed(hist) if p["day"] == day), None)
        prev_pt = next((p for p in reversed(hist) if p["day"] < day), None)
        lines += self._summary_lines(s)
        if today_pt and prev_pt:
            change = (today_pt["nav"] / prev_pt["nav"] - 1) * 100 if prev_pt["nav"] else None
            vni = (today_pt["vnindex"] / prev_pt["vnindex"] - 1) * 100 if today_pt.get("vnindex") and prev_pt.get("vnindex") else None
            lines.insert(3, f"Hôm nay {pct(change)}" + (f" · VNINDEX {pct(vni)}" if vni is not None else ""))
        if hist and hist[0].get("vni_index") is not None and today_pt:
            lines.insert(4 if today_pt and prev_pt else 3,
                         f"Từ {ddmm(hist[0]['day'])}: danh mục {pct(today_pt['nav'] - 100)} · VNINDEX {pct(today_pt['vni_index'] - 100)}")
        if pins:
            lines += ["", "📌 MỨC CẮT LỖ"] + pins
        nxt = vn_rules.add_sessions(day, 1)
        todo = self.actions(day, s, known, site)
        if settle:
            self._guarded("update_exits", lambda: kyluat.update_exits(self, day, s, known, site))
        tickets, covered = self._guarded("ticket_lines", lambda: kyluat.ticket_lines(self, day, known), ([], set()))
        todo = [(rank, line) for rank, line in todo if not (line[:1] in ("⛔", "🔴") and self._line_symbol(line) in covered)]
        todo += [(2, line) for line in self._guarded("early_lines", lambda: self._early_lines(day, book, site, covered, todo), [])]
        lines += ["", f"📋 VIỆC CẦN LÀM PHIÊN {ddmm(nxt)}"]
        lines += (tickets + [line for _, line in sorted(todo, key=lambda r: r[0])]) or ["Không có việc cần làm: giữ nguyên danh mục."]
        if s["positions"]:
            lines += ["", "📈 TỪNG MÃ"] + self._position_lines(s, book, site)
        alloc = self.allocation(s)
        hot = [a for a in alloc["symbols"] if a["level"] != "ok"]
        if hot:
            lines += ["", "⚖️ PHÂN BỔ"] + [f"{'🔴' if a['level'] == 'danger' else '🟡'} {a['symbol']} {a['pct']:.1f}%".replace(".", ",")
                                         for a in hot]
        risk = self._guarded("risk_summary", lambda: kyluat.risk_summary(self, s), None)
        if risk:
            lines += kyluat.risk_lines(risk)
            if settle:
                lines += self._guarded("floor_check", lambda: kyluat.floor_check(self, day, risk), [])
        rights = self.pending_rights()
        upcoming = [u for u in self.upcoming_rights()
                    if u["ex"] <= (_date.fromisoformat(day) + timedelta(days=14)).isoformat()]
        if rights or upcoming:
            lines += ["", "📅 QUYỀN"]
            lines += [self._right_line(r, pending=True) for r in rights]
            lines += [self._right_line(r, pending=False) for r in upcoming]
        disc = (self._guarded("discipline", lambda: self.refresh_discipline(day, known), None) if settle
                else (self.state().get("discipline") or None))
        if _date.fromisoformat(nxt).isocalendar()[1] != _date.fromisoformat(day).isocalendar()[1]:
            lines += self._shadow_lines()
            if disc:
                lines += [""] + kyluat.discipline_lines(disc)
            last = ({"buys_block": disc["buys_block"], "sold_fast": disc["sold_fast"], "tickets": disc["tickets"],
                     "due": disc.get("due", disc["tickets"]), "open_risk": risk["pct"]} if disc and risk else None)
            goal = self._guarded("goal_text", lambda: kyluat.goal_text(self, day, s, last), "")
            if goal:
                lines += ["", goal]
        lines += ["", "Giá theo đúng giá anh nhập (giá mua đã gồm phí, giá bán không trừ phí/thuế). Bot không đặt lệnh."]
        return "\n".join(lines)

    @staticmethod
    def _guarded(name: str, fn, default=None):
        """``fn()``, or ``default`` with a warning: the discipline layer never costs the report
        or the journal entry it decorates."""
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - the block is left out, the rest goes on
            logger.warning("Tài sản: %s failed: %s: %s", name, type(exc).__name__, exc)
            return default

    @staticmethod
    def _line_symbol(line: str) -> str:
        """The code an action line names: "⛔ FPT: ..." -> FPT."""
        parts = line.split()
        return parts[1].rstrip(":") if len(parts) > 1 else ""

    def _mark_diet(self, day: str) -> None:
        """The first report after the upgrade marks the day the 🟠 pushes stopped (the
        discipline meter compares before and after it)."""
        def change(s):
            if not s["diet"].get("since"):
                s["diet"]["since"] = day
        self._update_state(change)

    def _early_lines(self, day: str, book: Book, site: dict, covered: set, todo: list) -> list[str]:
        """🟠 of ``day`` for the holdings: an early SELL sign in the session (the site's
        in-session view, or the bot's estimate) the site did not confirm at the close. To
        watch, not to act on: selling on it beat waiting for the site's SELL in 48% of the
        measured cases. Not printed when the stock has a 🔴 or a ticket."""
        red = {self._line_symbol(line) for _, line in todo if line[:1] in ("🔴", "🟠")} | set(covered)
        out, seen = [], set()
        for r in self.state()["shadow"]:
            symbol = r.get("symbol")
            if r.get("day") != day or r.get("level") != "early" or symbol not in book.holdings or symbol in red or symbol in seen:
                continue
            sig = site.get(symbol)
            if sig and sig.get("side") == "SELL":
                continue
            seen.add(symbol)
            out.append(f"🟠 {symbol}: trong phiên có dấu hiệu đổi sang BÁN lúc {r.get('at', '')} (trang không chốt BÁN → giữ theo "
                       f"quy tắc; 🟠 chỉ để theo dõi, đo 23/10: đúng {kyluat.MEASURED['early_better']}%)")
        return out

    def refresh_discipline(self, day: str, prices: dict[str, float]) -> dict:
        """The discipline meter of ``day`` (``kyluat.discipline``), kept in the state for the
        web page (the replay reads every closed trade; computed once an evening)."""
        d = kyluat.discipline(self, day, prices)
        saved = {k: (list(v) if isinstance(v, tuple) else v) for k, v in d.items()}
        self._update_state(lambda s: s.__setitem__("discipline", saved))
        return d

    @staticmethod
    def _right_line(r: dict, *, pending: bool) -> str:
        if r["kind"] == "STOCK":
            what = (f"{vnd(r.get('shares'))} cp thưởng (tỷ lệ {r['ratio'] * 100:g}%)" if pending
                    else f"cổ phiếu thưởng tỷ lệ {r['ratio'] * 100:g}%")
            cmd = f"/tsthuong {r['symbol']} {r.get('shares')}" if pending else ""
        else:
            what = (f"cổ tức {vnd(r['value'] / max(r.get('eligible') or 1, 1) / (1 - DIVIDEND_TAX))} đ/cp ≈ {vnd(r['value'])} đ sau thuế"
                    if pending else f"cổ tức tiền {vnd(r['value'])} đ/cp")
            cmd = f"/tscotuc {r['symbol']} {vnd(r['value'] / max(r.get('eligible') or 1, 1))}" if pending else ""
        if pending:
            return f"⏳ {r['symbol']}: {what}, GDKHQ {ddmm(r['ex'])}, chờ về · khi về: {cmd}"
        return f"🗓️ {r['symbol']}: {what}, GDKHQ {ddmm(r['ex'])} (đang giữ {vnd(r.get('qty'))} cp)"

    def _shadow_lines(self) -> list[str]:
        stats = [s for s in self.shadow_stats() if s["settled"]]
        if not stats:
            return []
        lines = ["", "🧪 KẾT QUẢ CẢNH BÁO (giá sau cảnh báo, so với đóng cửa ngày cảnh báo)"]
        for s in stats:
            lines.append(f"{s['icon']} {s['name']}: {s['settled']} lần · sau 5 phiên {pct(s['r5'])}"
                         + (f" · sau 10 phiên {pct(s['r10'])} · giá {'cao' if s['up'] else 'thấp'} hơn {s['right']:.0f}% số lần"
                            if s["r10"] is not None else ""))
        lines.append("(giá giảm sau cảnh báo = bán theo cảnh báo là đúng"
                     + ("; sau 🟢 Rẻ: giá tăng là đúng)" if any(s["up"] for s in stats) else ")"))
        return lines

    def journal_text(self, n: int = 20) -> str:
        entries = self.entries()
        voided = {e.ref for e in entries if e.kind == "VOID"}
        rows = derive(entries).rows
        pnl = {r.entry.id: r.pnl for r in rows}
        shown = [e for e in entries if e.kind != "VOID"][-n:]
        if not shown:
            return "Chưa có giao dịch nào."
        lines = [f"🧾 NHẬT KÝ · {len(shown)} dòng gần nhất (số · ngày · loại)"]
        for e in reversed(shown):
            lines.append(f"#{e.id} {ddmmyyyy(e.day)} {self.entry_text(e)}"
                         + (f" · lãi/lỗ {signed(pnl[e.id])}" if pnl.get(e.id) is not None else "")
                         + (" · ĐÃ HUỶ" if e.id in voided else ""))
        lines.append("Huỷ dòng cuối: /tshuy · huỷ một dòng: /tshuy SỐ")
        return "\n".join(lines)

    @staticmethod
    def entry_text(e: Entry) -> str:
        if e.kind in ("BUY", "SELL"):
            return f"{KIND_VI[e.kind]} {e.symbol} {vnd(e.qty)} cp @ {vnd(e.price)}" + (f" ({e.note})" if e.note else "")
        if e.kind == "BONUS":
            return f"Thưởng {e.symbol} {vnd(e.qty)} cp" + (f" ({e.note})" if e.note else "")
        sign = "-" if e.kind == "WITHDRAW" or (e.kind == "ADJUST" and e.amount < 0) else "+"
        what = KIND_VI[e.kind] + (f" {e.symbol}" if e.symbol else "")
        return f"{what} {sign}{vnd(abs(e.amount))}" + (f" ({e.note})" if e.note else "")

    # ── the site's plan for a new buy ──
    def site_plan(self, symbol: str, day: str, price: float) -> tuple[float | None, float | None]:
        """(cut-loss, target) of the site's plan for its latest BUY of ``symbol`` on or before
        ``day``, when they make sense for the price; (None, None) when the site is off."""
        from vnbot import external

        if not external.enabled():
            return None, None
        store = external.SignalStore(self.dir.parent)
        try:
            store.refresh([symbol])
        except Exception as exc:  # noqa: BLE001 - then no levels
            logger.info("Tài sản: site history of %s not fetched: %s", symbol, exc)
        signal = store.state(symbol, day)
        listed = store.listed_state(symbol, day)
        if listed is not None and (signal is None or str(listed.get("day")) > signal["day"]):
            signal = listed
        if not signal or signal.get("side") != "BUY":
            return None, None
        stop, target = signal.get("sl"), signal.get("tp1")
        if not (_finite(stop) and 0 < float(stop) < price):
            return None, None
        return float(stop), (float(target) if _finite(target) and float(target) > price else None)

    # ── commands (Telegram and Zoom) ──
    COMMANDS = ("/ts", "/taisan", "/tsmua", "/tsban", "/tsthuong", "/tscotuc", "/tsnap", "/tsrut", "/tstien",
                "/tscatlo", "/tsnhatky", "/tshuy", "/tsbaocao", "/tsxuat", "/tsnhap", "/tsnguong",
                "/tskiemtra", "/tsgiu", "/tssan", "/tsmuctieu", "/tsdinhgia")
    HELP = (
        "💼 Tài sản (danh mục tiền thật của anh, tách khỏi bot):\n"
        "/ts - tổng quan · /tsbaocao - báo cáo ngay · trang web /taisan\n"
        "/tsmua FPT 20 73891 [YYYY-MM-DD] - đã mua (giá đã gồm phí) · /tsban FPT 20 75000 [ngày] - đã bán\n"
        "/tsthuong FPT 15 - cổ phiếu thưởng đã về · /tscotuc FPT 2000 [số cp] - cổ tức tiền đã về (đồng/cp)\n"
        "/tsnap 50000000 · /tsrut 10000000 - nạp/rút tiền · /tstien 1268895 - đặt tiền mặt theo số dư CTCK\n"
        "/tscatlo FPT 52000 [60000] - mức cắt lỗ [mục tiêu] của anh · /tscatlo FPT xoa\n"
        "/tskiemtra FPT [GIÁ] [KL] [CẮTLỖ] - kiểm tra một lệnh mua định làm có nằm trong ngân sách rủi ro (bot không khuyên mua)\n"
        "/tsgiu FPT 52000 lý do... - giữ một mã dù bot báo ⛔/🔴: bắt buộc có cắt lỗ và lý do, bot chấm sau 5/10/20 phiên\n"
        "/tssan 6000000 [lý do] - sàn tài sản để bot nhắc (mặc định 60% vốn gốc; không khoá lệnh) · /tssan - xem\n"
        "/tsmuctieu 2026-12-24 · /tsmuctieu xoa · /tsmuctieu - mốc hoàn vốn; bot đặt số đo cạnh mốc, không dự báo\n"
        "/tsnhatky - 20 dòng gần nhất · /tshuy - huỷ dòng cuối · /tshuy 27 - huỷ dòng số 27\n"
        "/tsxuat - file Excel (Zoom: link tải) · /tsnhap - gửi file .xlsx kèm lệnh này, rồi /tsnhap xacnhan\n"
        "/tsnguong 25 40 - ngưỡng cảnh báo tỷ trọng một mã (%)\n"
        "/tsdinhgia CTG 27904 30164 33309 [eab 26039] [han 30/10/2026] [nguon ...] - thẻ định giá anh nhập (Bear Base "
        "Bull, mã nào cũng được): bot báo trong phiên khi giá vào vùng Rẻ/Đắt · /tsdinhgia - xem · /tsdinhgia CTG xoa"
    )

    # ── recording (the commands and the web page share these) ──
    def record_buy(self, symbol: str, qty, price, day: str | None = None, note: str = "", *, source: str = "") -> str:
        """A buy into the journal (never refused for a rule), with the site's levels when the
        owner set none, and its rule tag (``kyluat.check_buy`` against the portfolio before
        it, at today's prices): ⛔ averaging down, buying against the site's SELL, no cut-loss;
        ⚠️ a chase, a far stop, over the risk budget - kept to compare the groups later."""
        from tradingagents.market import vn_rules

        before = self.book()
        entry, book = self.add("BUY", day, symbol=symbol, qty=qty, price=price, note=note, source=source)
        symbol = entry.symbol
        self._site_levels_for(symbol, entry.day, entry.price)
        t2 = vn_rules.add_sessions(entry.day, SETTLEMENT_SESSIONS)
        lv = self.levels().get(symbol) or {}
        check = self._guarded("check_buy", lambda: kyluat.check_buy(
            self, symbol, entry.price, entry.qty, float(lv.get("stop") or 0) or None, day=entry.day, book=before), None)
        self._guarded("tag_buy", lambda: self._tag_buy(entry, check))
        levels = (f" Cắt lỗ theo Siêu tín hiệu {vnd(lv['stop'])}" + (f", mục tiêu {vnd(lv['target'])}" if lv.get("target") else "")
                  + "." if lv.get("stop") else f" Chưa có mức cắt lỗ: đặt bằng /tscatlo {symbol} GIÁ.")
        h = book.holdings[symbol]
        return (f"✅ #{entry.id} MUA {symbol} {vnd(entry.qty)} cp @ {vnd(entry.price)} ngày {ddmmyyyy(entry.day)}. "
                f"Đang giữ {vnd(h.qty)} cp, giá vốn BQ {vnd(h.avg)}. Bán được từ chiều {ddmm(t2)}. "
                f"Tiền mặt còn {vnd(book.cash)}.{levels}"
                + (f"\n{kyluat.rule_tag_line(check)}" if check else "")
                + "".join(f"\n⚠️ {w}" for w in book.warnings))

    def _tag_buy(self, entry: Entry, check: dict | None) -> None:
        tag = {"kind": "buy", "day": entry.day, "symbol": entry.symbol, "price": entry.price, "qty": entry.qty,
               "verdict": check["verdict"] if check else "unknown", "flags": list(check["flags"]) if check else [],
               "checked": kyluat.recently_checked(self, entry.symbol, entry.day), "tags": check["tags"] if check else {}}
        self._update_state(lambda s: s["tags"].__setitem__(str(entry.id), tag))

    def record_sell(self, symbol: str, qty, price, day: str | None = None, note: str = "", *, source: str = "") -> str:
        """A sale into the journal; it closes the stock's exit ticket (``kyluat``) and is tagged
        "early" when the site still said BUY and no ⛔/🔴 came since the last buy."""
        entry, book = self.add("SELL", day, symbol=symbol, qty=qty, price=price, note=note, source=source)
        row = next(r for r in book.rows if r.entry.id == entry.id)
        left = book.holdings.get(entry.symbol)
        self._guarded("close_exits", lambda: kyluat.close_exits_on_sell(self, entry.symbol, entry.day, entry.qty,
                                                                        left.qty if left else 0))
        early = self._guarded("tag_sell", lambda: self._tag_sell(entry), False)
        self._prune_state(book)
        return (f"✅ #{entry.id} BÁN {entry.symbol} {vnd(entry.qty)} cp @ {vnd(entry.price)} ngày {ddmmyyyy(entry.day)}: "
                f"lãi/lỗ {signed(row.pnl)} ({pct((entry.price / row.avg_cost - 1) * 100) if row.avg_cost else '-'}) "
                f"theo giá vốn BQ {vnd(row.avg_cost)}. "
                + (f"Còn {vnd(left.qty)} cp. " if left else "Đã bán hết. ")
                + f"Tiền mặt {vnd(book.cash)}."
                + (f"\n📏 Bán trước tín hiệu: trang vẫn MUA {entry.symbol} và chưa có ⛔/🔴 từ lần mua gần nhất; bot ghi lại "
                   "và chấm sau 10/20 phiên (không phải lỗi, chỉ để đo)." if early else ""))

    def _tag_sell(self, entry: Entry) -> bool:
        """Tag the sale; True when it came before any rule alert while the site said BUY."""
        from vnbot import external

        journal = effective(self.entries())
        last_buy = max((e.day for e in journal if e.kind == "BUY" and e.symbol == entry.symbol and e.day <= entry.day), default="")
        st = self.state()
        alerted = any(r.get("symbol") == entry.symbol and r.get("level") in ("stop", "sell")
                      and last_buy <= str(r.get("day", "")) <= entry.day for r in st["shadow"])
        ticketed = (str((st["exits"].get(entry.symbol) or {}).get("opened") or "") >= last_buy if st["exits"].get(entry.symbol)
                    else False) or any(t.get("symbol") == entry.symbol and str(t.get("opened") or "") >= last_buy
                                       for t in st["exits_log"])
        sig = external.SignalStore(self.dir.parent).state(entry.symbol, entry.day) if external.enabled() else None
        early = bool(sig and sig.get("side") == "BUY" and not alerted and not ticketed)
        tag = {"kind": "sell", "day": entry.day, "symbol": entry.symbol, "price": entry.price, "qty": entry.qty,
               "early": early, "site_state": sig.get("side") if sig else None}
        self._update_state(lambda s: s["tags"].__setitem__(str(entry.id), tag))
        return early

    def record_bonus(self, symbol: str, qty, day: str | None = None, note: str = "", *, source: str = "") -> str:
        entry, book = self.add("BONUS", day, symbol=symbol, qty=qty, note=note, source=source)
        h = book.holdings[entry.symbol]
        return (f"✅ #{entry.id} {entry.symbol} +{vnd(entry.qty)} cp thưởng: đang giữ {vnd(h.qty)} cp, giá vốn BQ "
                f"{vnd(h.avg)} (tổng vốn giữ nguyên). Bán được ngay.")

    def record_dividend(self, symbol: str, amount, day: str | None = None, note: str = "", *, source: str = "") -> str:
        """A cash dividend received: ``amount`` the money that arrived (after the tax withheld)."""
        entry, book = self.add("DIVIDEND", day, symbol=symbol, amount=amount, note=note, source=source)
        return (f"✅ #{entry.id} Cổ tức {entry.symbol} {vnd(entry.amount)} đ"
                + (f" ({entry.note})" if entry.note else "") + f". Tiền mặt {vnd(book.cash)}. Sai thì huỷ rồi ghi lại.")

    def record_cash(self, kind: str, amount, day: str | None = None, note: str = "", *, source: str = "") -> str:
        """Money in (DEPOSIT), out (WITHDRAW), or a correction (ADJUST, signed)."""
        if kind not in ("DEPOSIT", "WITHDRAW", "ADJUST"):
            raise LedgerError(f"Loại giao dịch tiền không hợp lệ: {kind}.")
        entry, book = self.add(kind, day, amount=amount, note=note, source=source)
        if kind == "ADJUST":
            return (f"✅ #{entry.id} Điều chỉnh tiền mặt {signed(entry.amount)}: tiền mặt {vnd(book.cash)}. "
                    "Khoản này tính vào lãi/lỗ, không vào vốn gốc.")
        return (f"✅ #{entry.id} {KIND_VI[kind]} {vnd(entry.amount)} ngày {ddmmyyyy(entry.day)}. "
                f"Tiền mặt {vnd(book.cash)} · vốn gốc {vnd(book.capital)}.")

    def match_cash(self, actual, day: str | None = None, *, source: str = "") -> str:
        """Set the cash to the broker's balance with one ADJUST (fees, taxes, interest not recorded)."""
        if not _finite(actual):
            raise LedgerError("Số dư không hợp lệ.")
        gap = float(actual) - self.book().cash
        if abs(gap) < 1:
            return f"Tiền mặt đã đúng {vnd(actual)}, không cần điều chỉnh."
        entry, book = self.add("ADJUST", day, amount=gap, note="khớp số dư CTCK", source=source)
        return (f"✅ #{entry.id} Điều chỉnh tiền mặt {signed(gap)} (phí, thuế, lãi ký quỹ... chưa ghi): "
                f"tiền mặt {vnd(book.cash)}. Khoản này tính vào lãi/lỗ, không vào vốn gốc.")

    def clear_levels(self, symbol: str) -> str:
        symbol = symbol.upper()
        self._update_state(lambda s: s["levels"].pop(symbol, None))
        return f"✅ Đã xoá mức cắt lỗ/mục tiêu của {symbol}."

    def levels_text(self, symbol: str, stop, target) -> str:
        level = self.set_levels(symbol, stop, target)
        return (f"✅ {symbol.upper()}: cắt lỗ {vnd(stop)}" + (f", mục tiêu {vnd(target)}" if target else "")
                + ". Bot báo ⛔ khi giá ≤ cắt lỗ" + (" và 🎯 khi chạm mục tiêu." if target else ".")
                + (f"\n{level['lower_text']}" if level.get("lower_text") else ""))

    def undo(self, entry_id: int | None = None, *, source: str = "") -> str:
        target = self.void(entry_id, source=source)
        self._update_state(lambda s: s["tags"].pop(str(target.id), None))   # its rule tag goes with it
        self._guarded("reopen_on_void", lambda: kyluat.reopen_on_void(self, target))
        self._prune_state(self.book())
        return f"↩️ Đã huỷ #{target.id}: {ddmmyyyy(target.day)} {self.entry_text(target)}."

    def reset(self) -> str:
        """Start the portfolio over: the journal is kept as a dated copy, the settings and the
        valuation cards stay."""
        with self._lock:
            if not self.journal_path.exists():
                return "Danh mục Tài sản đang trống, không có gì để xoá."
            backup = self.backup_journal()

            def change(s):
                for key in ("levels", "marks", "alerts", "events", "notes", "spark", "exits", "tags", "guard", "diet",
                            "discipline"):
                    s[key] = {}
                for key in ("shadow", "flags", "exits_log", "overrides", "checks"):
                    s[key] = []
                s["goal"] = {"date": kyluat.DEFAULT_GOAL_DATE, "set_day": "", "source": "default"}
                for key in ("rebuilt_until", "analytics", "imported"):
                    s.pop(key, None)
            self._update_state(change)
            self.pending_path.unlink(missing_ok=True)
            (self.dir / "last_report.json").unlink(missing_ok=True)
        self._forget_live()
        return f"🗑️ Đã xoá danh mục Tài sản; sổ cũ lưu tại {backup.name}."

    def report_day(self) -> str:
        """The session a report asked now is about: today after 15:05, else the last one."""
        from tradingagents.market import vn_rules

        day = today_vn()
        if not vn_rules.is_session_day(day) or now_vn().strftime("%H:%M") < "15:05":
            day = vn_rules.prev_session(day)
        return day

    def handle(self, command: str, args: str, *, source: str = "telegram", public_url: str = ""):
        """One ``/ts...`` command: the reply text, or {"file", "caption"} for /tsxuat on
        Telegram. LedgerError/ValueError carry the message for the owner."""
        from vnbot.service import check_request
        from vnbot.telegram_bot import _amount

        words = args.split()
        day_word = next((w for w in words if re.fullmatch(r"\d{4}-\d{2}-\d{2}", w)), None)
        rest = [w for w in words if w != day_word]

        def code(word: str) -> str:
            return check_request(word)[0]

        if command in ("/ts", "/taisan"):
            return self.overview_text()
        if command in ("/tsmua", "/tsban"):
            if len(rest) < 3:
                raise LedgerError(f"Dùng: {command} FPT 20 73891 [YYYY-MM-DD] [ghi chú]")
            record = self.record_buy if command == "/tsmua" else self.record_sell
            return record(code(rest[0]), _amount(rest[1]), _amount(rest[2]), day_word, " ".join(rest[3:]), source=source)
        if command == "/tsthuong":
            if len(rest) != 2:
                raise LedgerError("Dùng: /tsthuong FPT 15 [YYYY-MM-DD] - số cổ phiếu thưởng/cổ tức bằng cổ phiếu đã về")
            return self.record_bonus(code(rest[0]), _amount(rest[1]), day_word, source=source)
        if command == "/tscotuc":
            if len(rest) not in (2, 3):
                raise LedgerError("Dùng: /tscotuc FPT 2000 [số cp được hưởng] [YYYY-MM-DD] - số đồng mỗi cổ phiếu "
                                  "thực nhận (sau thuế nếu CTCK đã trừ)")
            symbol, per_share = code(rest[0]), _amount(rest[1])
            if len(rest) == 3:
                shares = int(_amount(rest[2]))
            else:
                pend = next((p for p in self.pending_rights() if p["symbol"] == symbol and p["kind"] == "CASH"), None)
                held = self.book().holdings.get(symbol)
                shares = int(pend["eligible"]) if pend else (held.qty if held else 0)
            if shares <= 0:
                raise LedgerError(f"Không biết số cổ phiếu {symbol} được hưởng: ghi thêm, ví dụ /tscotuc {symbol} 2000 100")
            return self.record_dividend(symbol, per_share * shares, day_word,
                                        f"{vnd(per_share)} đ/cp x {vnd(shares)} cp", source=source)
        if command in ("/tsnap", "/tsrut"):
            if len(rest) < 1:
                raise LedgerError(f"Dùng: {command} 50000000 [YYYY-MM-DD] [ghi chú]")
            kind = "DEPOSIT" if command == "/tsnap" else "WITHDRAW"
            return self.record_cash(kind, _amount(rest[0]), day_word, " ".join(rest[1:]), source=source)
        if command == "/tstien":
            if len(rest) != 1:
                raise LedgerError("Dùng: /tstien 1268895 - đặt tiền mặt bằng số dư thật trên app CTCK")
            return self.match_cash(_amount(rest[0]), day_word, source=source)
        if command == "/tscatlo":
            if len(rest) < 2:
                raise LedgerError("Dùng: /tscatlo FPT 52000 [60000] - mức cắt lỗ [và mục tiêu]; /tscatlo FPT xoa")
            symbol = code(rest[0])
            if rest[1].lower() in ("xoa", "xoá", "xóa"):
                return self.clear_levels(symbol)
            return self.levels_text(symbol, _amount(rest[1]), _amount(rest[2]) if len(rest) > 2 else None)
        if command == "/tskiemtra":
            if not rest:
                raise LedgerError("Dùng: /tskiemtra FPT [GIÁ] [KL] [CẮTLỖ] - kiểm tra một lệnh mua định làm (bot không khuyên mua)")
            symbol = code(rest[0])
            price = _amount(rest[1]) if len(rest) > 1 else None
            qty = int(_amount(rest[2])) if len(rest) > 2 else None
            stop = _amount(rest[3]) if len(rest) > 3 else None
            check = kyluat.check_buy(self, symbol, price, qty, stop, day=day_word)
            kyluat.record_check(self, check, symbol, qty)
            return check["text"]
        if command == "/tsgiu":
            if len(rest) < 2:
                raise LedgerError("Dùng: /tsgiu FPT 52000 lý do ít nhất ba từ - giữ một mã dù bot báo ⛔/🔴 (bot ghi lại, chấm sau)")
            return kyluat.hold_override(self, code(rest[0]), _amount(rest[1]), " ".join(rest[2:]))
        if command == "/tssan":
            if not rest:
                return kyluat.floor_text(self)
            return kyluat.set_floor(self, _amount(rest[0]), " ".join(rest[1:]))
        if command == "/tsmuctieu":
            word = rest[0] if rest else day_word
            if not word:
                return kyluat.goal_text(self, self.report_day())
            return kyluat.set_goal(self, word)
        if command == "/tsdinhgia":
            words = args.split()                 # its own words: "han 2026-10-30" is not the entry's day
            if not words:
                return self.valuations_text()
            symbol = code(words[0])
            if len(words) == 1:
                return self.valuations_text([symbol])
            if words[1].lower() in ("xoa", "xoá", "xóa"):
                return self.clear_valuation(symbol)
            card = self.set_valuation(symbol, **parse_valuation(words[1:]), source=source)
            return self.valuation_set_text(symbol, card)
        if command == "/tsnhatky":
            n = int(rest[0]) if rest and rest[0].isdigit() else 20
            return self.journal_text(max(1, min(n, 60)))
        if command == "/tshuy":
            return self.undo(int(rest[0]) if rest and rest[0].isdigit() else None, source=source)
        if command == "/tsbaocao":
            return self.report_text(self.report_day())          # reads the state; the 15:30 run settles it
        if command == "/tsnguong":
            if len(rest) != 2:
                warn, danger = self.thresholds()
                return f"Ngưỡng tỷ trọng một mã: cảnh báo {warn:g}%, nguy hiểm {danger:g}%. Đổi: /tsnguong 25 40"
            try:
                warn, danger = float(rest[0].replace(",", ".")), float(rest[1].replace(",", "."))
            except ValueError:
                raise LedgerError("Dùng: /tsnguong 25 40 - hai số phần trăm") from None
            self.set_thresholds(warn, danger)
            return f"✅ Ngưỡng tỷ trọng: cảnh báo {warn:g}%, nguy hiểm {danger:g}%."
        if command == "/tsxuat":
            from vnbot import taisan_excel

            if source == "zoom":
                link = f"{public_url}/taisan/xuat.xlsx" if public_url else "/taisan/xuat.xlsx trên trang web"
                return f"📤 Tải file Excel danh mục Tài sản (cần mật khẩu web): {link}"
            path = taisan_excel.export_xlsx(self, self.dir / "export" / f"tai-san-{today_vn()}.xlsx")
            return {"file": path, "caption": f"Danh mục Tài sản {ddmmyyyy(today_vn())} (4 sheet: vị thế, lịch sử, tổng quan, tiền mặt)"}
        if command == "/tsnhap":
            from vnbot import taisan_excel

            verb = (rest[0].lower() if rest else "")
            if verb in ("xacnhan", "xácnhận", "ok"):
                return taisan_excel.confirm(self, replace=False)
            if verb in ("thaythe", "thaythế"):
                return taisan_excel.confirm(self, replace=True)
            if verb in ("huy", "huỷ", "hủy"):
                return taisan_excel.cancel(self)
            if source == "zoom":
                return "Nhập Excel: gửi file .xlsx vào bot Telegram (kèm /tsnhap) hoặc dùng trang web /taisan."
            return "Gửi file .xlsx (xuất từ app cũ hoặc /tsxuat) vào đây kèm chú thích /tsnhap; bot sẽ cho xem trước rồi mới ghi."
        raise LedgerError(self.HELP)
