"""The trading bot: three paper accounts traded every session, signals for a real one.

Three strategies run side by side, each on its own paper book:

- ``factor`` (the default for signals): the defensive trend book - low
  volatility and nearness to the 52-week high among the 50 largest liquid HOSE
  stocks, held only while VNINDEX is above its 200-session average
  (trading/factor.py; chosen by the independent evaluation of 2026-10-15).
- ``engine``: the Quant Engine's BUY plans (entry zone, stop, T1/T2).
- ``momentum``: the older momentum rotation over today's VN30
  (trading/momentum.py), kept as a comparison book.

The report compares them every day; ``SIGNAL_STRATEGY`` picks which one the
real-account signals follow. After the close (``run_day`` / ``catch_up``):

1. Every VNINDEX session since a book's last processed day is replayed in
   order, each with the data up to that session only (no look-ahead): the
   orders of the session before fill or lapse, stops and targets are checked
   on the session's bar (ledger rules, T+2, fees, slippage). A session whose
   bar is missing or not final is never processed: it waits for the next run.
2. The engine decides for the basket (VN30 + watchlist + anything held), with
   the paper book as its portfolio so held stocks are judged as holdings.
3. The paper account places the next session's orders; the NAV is recorded
   beside VNINDEX. Each book saves after each session with its own
   ``meta["last_day"]``, so a run that fails half-way is resumed, not replayed.
4. In ``signal`` mode, the real book is first marked to the last close and put
   on today's price basis (``Ledger.mark_to_history``: after a dividend or bonus
   issue its stop/targets are divided by the ratio, as for a paper book), so its
   NAV is at market value; then the decisions of the last session are turned
   into suggested orders sized on it, and that session's stop/target hits on
   real holdings are flagged. Its trades, cash and share count change only when
   its owner reports them (/khop, /von, /thuong); the runs write only the marks
   and the rebased levels.

Then, if enabled, the nightly calibration may move one threshold of the
engine and one of the momentum rotation (see ``tradingagents.trading.calibrate``);
the defensive book keeps the parameters that were evaluated. No order is ever
sent to a broker.
"""

from __future__ import annotations

import bisect
import copy
import json
import logging
import math
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import date as _date, timedelta
from pathlib import Path

from vnbot.jobs import write_text_atomic
from vnbot.settings import MIN_TRADE_TIME, Settings

logger = logging.getLogger(__name__)

# Sessions replayed after downtime. Older missed sessions are skipped (their
# orders lapse) and the report says so: weeks of replay would take long and
# the decisions would be stale anyway.
MAX_CATCH_UP = 20

# After the listing (exchange of each stock) failed to load, runs use the HOSE
# tick grid for this long instead of waiting on the listing again.
LISTING_RETRY_SECONDS = 1800
_listing_down_until = 0.0

# A session is not saved when the price sources did not answer for a held stock or
# for more than this share of the basket: it is retried later (the source breaker
# turns a short outage into minutes of failures for every symbol).
MAX_UNAVAILABLE_SHARE = 0.2

# A held, ordered or ranked stock missing the latest bar is waited for (until the late-bar
# cutoff) only if it traded on each of this many sessions before (``_fresh_gaps``).
LATE_BAR_HISTORY = 5

# The defensive book's universe: one build per session it is needed on (month ends,
# the market filter turning on), cached; after a failed build the last one this many
# days old at most is used, else the VN30 members.
UNIVERSE_FILE = "factor_universe.json"
UNIVERSE_KEEP = 12


def _fmt(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:,.0f}".replace(",", ".")


def _vnd(value: float) -> str:
    return f"{value:,.0f}".replace(",", ".") + " đồng"


def _bn(value: float) -> str:
    return f"{value / 1e6:,.1f} tr".replace(",", "X").replace(".", ",").replace("X", ".")


def _now_vn():
    from tradingagents.market import vn_rules

    return vn_rules.now_vn()


def today_vn() -> str:
    return _now_vn().strftime("%Y-%m-%d")


def final_day(now=None) -> str:
    """The latest calendar day whose session (if there was one) is final: today from
    MIN_TRADE_TIME (Vietnam time), else yesterday."""
    now = now or _now_vn()
    day = now.date()
    if now.strftime("%H:%M") < MIN_TRADE_TIME:
        day -= timedelta(days=1)
    return day.isoformat()


def _book_day(book) -> str:
    """The last session a book has processed ("" for a new book). Books saved before
    ``meta["last_day"]`` existed (the momentum book) fall back to their NAV history."""
    day = book.meta.get("last_day")
    if day:
        return str(day)
    return str(book.history[-1]["date"]) if book.history else ""


def _pending(sessions: list[str], last: str) -> tuple[list[str], int]:
    """The sessions a book at ``last`` still has to process, and how many are skipped (cap)."""
    if not last:
        return sessions[-1:], 0          # a new book starts at the latest session, no backfill
    after = [s for s in sessions if s > last]
    skipped = max(0, len(after) - MAX_CATCH_UP)
    return after[skipped:], skipped


def _expire(book, prev: str | None) -> list:
    """Drop the orders whose session has passed without being processed.

    An order created on D is for the session after D. Before processing session S
    (previous session ``prev``), an order created before ``prev`` was meant for a
    session up to ``prev``: it must lapse rather than fill at S's prices.
    """
    if not prev:
        return []

    def lapsed(o) -> bool:
        # An order still alive for several sessions is fine as long as the session before
        # S was processed with it in the book (``Order.last_session``); otherwise a session
        # it was meant for passed unprocessed.
        return o.created < prev and (getattr(o, "last_session", "") or o.created) < prev

    stale = [o for o in book.orders if lapsed(o)]
    if stale:
        book.orders = [o for o in book.orders if not lapsed(o)]
    return stale


def _finite(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _unavailable(exc: BaseException) -> bool:
    """Whether ``exc`` (or what caused it) is a price source that did not answer."""
    from tradingagents.dataflows.errors import VendorUnavailableError

    seen = 0
    while exc is not None and seen < 10:
        if isinstance(exc, VendorUnavailableError):
            return True
        exc, seen = exc.__cause__ or exc.__context__, seen + 1
    return False


def _add_sessions(day: str, n: int) -> str:
    """``n`` sessions after ``day`` by the exchange calendar (weekdays that are not known holidays)."""
    from tradingagents.market import vn_rules

    return vn_rules.add_sessions(day, n)


def _pending_lots(pos, day: str) -> list[tuple[str, int]]:
    """(T+2 day, shares) of a real holding's lots that cannot be sold all through the
    session after ``day``: shares bought on T arrive in the afternoon of T+2."""
    from tradingagents.trading.ledger import SETTLEMENT_SESSIONS, _sessions_between

    due: dict[str, int] = {}
    for lot in pos.unsettled:
        if max(lot["sessions"], _sessions_between(lot["date"], day)) < SETTLEMENT_SESSIONS:
            t2 = _add_sessions(lot["date"], SETTLEMENT_SESSIONS)
            due[t2] = due.get(t2, 0) + lot["qty"]
    return sorted(due.items())


def _t2_note(pending: list[tuple[str, int]], day: str) -> str:
    nxt = _add_sessions(day, 1)
    return "; ".join(f"{_fmt(qty)} cp chưa về đủ T+2: chỉ bán được từ chiều "
                     + (f"phiên tới ({t2[8:10]}/{t2[5:7]})" if t2 <= nxt else f"{t2[8:10]}/{t2[5:7]}")
                     + " - đặt lệnh phiên chiều/ATC" for t2, qty in pending)


def _sell_advice(pos, day: str, verb: str = "BÁN", when: str = "") -> str:
    """What of a real holding can be sold next session, and when the rest can (T+2)."""
    pending = _pending_lots(pos, day)
    if not pending:
        return f"đề xuất {verb} {_fmt(pos.qty)} cp{when}"
    free = max(0, pos.qty - sum(qty for _, qty in pending))
    head = f"đề xuất {verb} {_fmt(free)} cp{when}; " if free else ""
    return head + _t2_note(pending, day)


def _net_pnl(pos, price: float | None) -> tuple[float, float]:
    """(VND, percent) a holding gives if sold now at ``price``: the sell fee and the 0.1% tax
    off the proceeds; the cost already includes the buy fee. (0, 0) without a price."""
    from tradingagents.market import vn_rules

    cost = (pos.avg_price or 0) * pos.qty
    if not cost or not price or not _finite(price):
        return 0.0, 0.0
    proceeds = pos.qty * float(price) * (1 - (vn_rules.SELL_FEE_PCT + vn_rules.SELL_TAX_PCT) / 100)
    return proceeds - cost, (proceeds / cost - 1) * 100


def _money(value: float) -> str:
    """Signed VND: "+1.234.000", "-752.000"."""
    return ("+" if value >= 0 else "-") + _fmt(abs(round(value)))


def _t2_now(pos, now=None) -> tuple[int, str]:
    """(shares sellable now, a note on the rest): a lot bought on T is sold from the
    afternoon of T+2 (13:00), exchange sessions counted."""
    from tradingagents.market import vn_rules

    now = now or _now_vn()
    day, hhmm = now.strftime("%Y-%m-%d"), now.strftime("%H:%M")
    if not vn_rules.is_session_day(day) or hhmm >= "15:00":
        day, afternoon = vn_rules.add_sessions(day, 1), False      # closed now: the next session's open
    else:
        afternoon = hhmm >= "13:00"
    free = pos.sellable_on(day, afternoon=afternoon)
    if free >= pos.qty:
        return pos.qty, ""
    rest = pos.qty - free
    pending = _pending_lots(pos, vn_rules.prev_session(day))     # the lots not sellable all through that session
    when = ", ".join(f"chiều {t2[8:10]}/{t2[5:7]}" for t2, _ in pending) or "chiều T+2"
    return free, f"{_fmt(rest)} cp chưa về đủ T+2 (bán được từ {when})"


def _advice(pos, day: str, when: str = "") -> str:
    """``_sell_advice`` for an arrow line: "BÁN 2.000 cp phiên tới" (no "đề xuất")."""
    text = _sell_advice(pos, day, when=when)
    return text[len("đề xuất "):] if text.startswith("đề xuất ") else text


def _estimated_view(real, exchange_of):
    """``real`` itself, or a copy whose holdings in an unadjusted ex-date gap
    (``Position.pending_ratio``) have their stop/targets divided by the estimate, for the
    report's checks only (the saved book keeps the levels until the ratio is measured)."""
    from tradingagents.trading.ledger import Ledger

    if not any((p.pending_ratio or 0) > 1 for p in real.positions.values()):
        return real
    view = Ledger.from_dict(copy.deepcopy(real.to_dict()))
    for symbol, pos in view.positions.items():
        if (pos.pending_ratio or 0) > 1:
            Ledger._rebase_levels(pos, float(pos.pending_ratio), exchange_of(symbol))
    return view


def _ts_words(text: str) -> str:
    """A note about the Tài sản portfolio: the main account's commands it names become its own."""
    return (re.sub(r"(?<![\w/])/thuong\b", "/tsthuong", text)
            .replace("cập nhật /von khi tiền về", "khi tiền về báo /tscotuc MÃ SỐ_ĐỒNG_MỖI_CP"))


def _real_adjustment_lines(notes: list[dict], cmd: str = "/thuong") -> list[str]:
    """The report lines of the real book's price-basis notes (``Ledger.mark_to_history``);
    ``cmd``: the command that records bonus shares for that book."""
    lines = []
    for n in notes:
        sym, ratio = n["symbol"], float(n.get("ratio") or 1)
        if n.get("kind") == "gap":
            lines.append(f"- ⚠️ {sym}: giá ngày {n['date']} giảm vượt biên độ so với phiên trước - nhiều khả năng là "
                         "ngày GDKHQ (cổ tức/thưởng) mà nguồn dữ liệu chưa điều chỉnh lịch sử giá. Tới khi nguồn điều "
                         f"chỉnh, bot so stop/T1 theo tỷ lệ ước tính x{ratio:.3f} (sai số tối đa bằng biên độ ngày đó); "
                         "kiểm tra trên app CTCK.")
            continue
        how = ("ước tính từ khoảng trống giá vì nguồn không điều chỉnh lịch sử"
               if n.get("kind") == "gap_estimate" else "theo lịch sử giá đã điều chỉnh")
        free = int(n.get("free_estimate") or 0)
        lines.append(f"- ⚖️ {sym}: giá điều chỉnh quyền x{ratio:.3f} ({how}) - stop/T1/T2 đã điều chỉnh theo, "
                     f"số cổ phiếu giữ nguyên {_fmt(n.get('qty'))} cp. Nếu là cổ phiếu thưởng/cổ tức bằng cổ phiếu "
                     f"(khoảng {_fmt(free)} cp), khi về tài khoản hãy báo {cmd} {sym} <số cp nhận được>; "
                     "nếu là cổ tức tiền mặt, cập nhật /von khi tiền về.")
    return lines


def _adjustment_lines(book, day: str) -> list[str]:
    """The day's corporate-action adjustments of a paper book (ledger ``_rebase``)."""
    return [f"- ⚖️ {a['symbol']}: giá điều chỉnh quyền (cổ tức/thưởng) x{a['ratio']:.3f} - "
            f"{_fmt(a['before'])} → {_fmt(a['after'])} cp, stop/T1/T2 điều chỉnh theo"
            for a in book.meta.get("adjustments") or [] if isinstance(a, dict) and a.get("date") == day]


def _benchmark() -> str:
    from tradingagents.market import vn_rules

    return vn_rules.BENCHMARK


def _before_cutoff(day: str) -> bool:
    """``day`` is today (Vietnam time) and its late bars may still be published."""
    from vnbot.service import LATE_BAR_CUTOFF

    now = _now_vn()
    return day == now.strftime("%Y-%m-%d") and now.strftime("%H:%M") < LATE_BAR_CUTOFF


@dataclass
class RunResult:
    day: str | None              # the last session the books are at
    text: str                    # the report (the previous one when nothing was new)
    new: bool = False            # whether this run processed at least one session
    sessions: list[str] = field(default_factory=list)   # processed by this run, in order
    waiting: list[str] = field(default_factory=list)    # stocks whose latest bar is awaited (session not run)
    extra: str = ""              # the comparison book's section, to send on its own when ``new`` is False


class _Market:
    """Daily bars for one run, read once per symbol up to ``as_of`` and sliced per session.

    Bars come unfilled (``fill_gaps=False``): a session with missing prices is a
    session without trading, never a forward-filled copy of the day before.
    """

    def __init__(self, as_of: str):
        self.as_of = as_of
        self._frames: dict[str, tuple | Exception | None] = {}
        self._boards: dict[str, str | None] | None = None

    def _entry(self, symbol: str):
        if symbol not in self._frames:
            from tradingagents.dataflows import ohlcv
            from tradingagents.dataflows.errors import NoMarketDataError, VendorError

            try:
                frame = ohlcv.load_ohlcv(symbol, self.as_of, fill_gaps=False)
                dates = [d.strftime("%Y-%m-%d") for d in frame["Date"]]
                self._frames[symbol] = (frame.reset_index(drop=True), dates)
            except NoMarketDataError as exc:
                logger.warning("Bot: no bars for %s up to %s: %s", symbol, self.as_of, exc)
                self._frames[symbol] = None
            except VendorError as exc:          # the source did not answer: not "no data"
                logger.warning("Bot: bars for %s unavailable: %s", symbol, exc)
                self._frames[symbol] = exc
        return self._frames[symbol]

    def frame(self, symbol: str, day: str, *, required: bool = False):
        """Bars up to and including ``day``, or None. ``required``: a source that did
        not answer raises (the run is retried) instead of passing for "no data"."""
        entry = self._entry(symbol)
        if isinstance(entry, Exception):
            if required:
                raise entry
            return None
        if entry is None:
            return None
        frame, dates = entry
        return frame.iloc[: bisect.bisect_right(dates, day)]

    def sessions(self, symbol: str) -> list[str]:
        entry = self._entry(symbol)
        if isinstance(entry, Exception):
            raise entry
        return list(entry[1]) if entry else []

    def exchange(self, symbol: str) -> str | None:
        """HOSE/HNX/UPCOM (the tick grid of slipped fills); None when unknown (HOSE grid).
        The listing is asked once per run: when it is down, not once per symbol."""
        global _listing_down_until
        if self._boards is None:
            from tradingagents.market import vn_rules

            self._boards = {}
            if time.monotonic() >= _listing_down_until:
                try:
                    from tradingagents.dataflows.vendors import vci

                    self._boards = {str(row.get("symbol", "")).upper(): vn_rules.normalize_exchange(row.get("board"))
                                    for row in vci.fetch_listing() if isinstance(row, dict)}
                except Exception as exc:  # noqa: BLE001 - enrichment only
                    logger.warning("Bot: listing unavailable, HOSE tick grid assumed: %s", exc)
                    _listing_down_until = time.monotonic() + LISTING_RETRY_SECONDS
        return self._boards.get(symbol.upper())

    def bar(self, symbol: str, day: str, *, required: bool = False):
        """The ledger Bar of ``symbol`` on ``day``; None when it did not trade that day."""
        from tradingagents.trading.ledger import Bar

        frame = self.frame(symbol, day, required=required)
        if frame is None or not len(frame) or frame["Date"].iloc[-1].strftime("%Y-%m-%d") != day:
            return None            # suspended (or no data) that session
        row = frame.iloc[-1]
        prev = float(frame["Close"].iloc[-2]) if len(frame) > 1 else None
        prev_date = frame["Date"].iloc[-2].strftime("%Y-%m-%d") if len(frame) > 1 else None
        volume = row.get("Volume")
        volume = float(volume) if _finite(volume) else None
        # NaN prices stay NaN: Bar.tradable then treats the session as one without trading.
        return Bar(day, float(row["Open"]), float(row["High"]), float(row["Low"]), float(row["Close"]), prev,
                   exchange=self.exchange(symbol), volume=volume, prev_date=prev_date)

    def close(self, symbol: str, day: str) -> float | None:
        frame = self.frame(symbol, day)
        if frame is None or not len(frame):
            return None
        value = float(frame["Close"].iloc[-1])
        return value if _finite(value) else None


class TradingBot:
    def __init__(self, settings: Settings, watchlist, notify=None):
        self.settings, self.watchlist = settings, watchlist
        self.notify = notify or (lambda text: None)
        self.dir = settings.data_dir / "trading"
        self.dir.mkdir(parents=True, exist_ok=True)
        # One run at a time over the paper books and their state files (scheduler,
        # /chay and the web button may all ask at once).
        self._lock = threading.RLock()
        # The real book: written only by /khop and /von, read by the runs. Separate,
        # so a report of a fill is not kept waiting for a whole session run.
        self._real_lock = threading.Lock()
        self._start_guard = threading.Lock()
        self._running = threading.Event()
        self._stv_lock = threading.Lock()     # the comparison book (vnbot.external), run outside _lock
        self._stv_scan_lock = threading.Lock()    # one SELL scan at a time (the scheduler and /stvquet)
        self._market_levels: dict = {}            # vnbot.market_scan: the levels of the 300 for one basis
        from vnbot import external
        from vnbot.taisan import TaiSan

        # The owner's real-money portfolio (/ts, the /taisan tab): it replaced the separate
        # Siêu tín hiệu account; ``real("stv")`` is its read-only Ledger view.
        self.taisan = TaiSan(settings.data_dir)

        if external.enabled():          # the paper book that follows sieutinhieu.vn (for comparison)
            self.BOOK_FILES = {**type(self).BOOK_FILES, "stv": external.BOOK_FILE}
            self.BOOK_NAMES = {**type(self).BOOK_NAMES, "stv": external.NAME}

    # ── books ──
    @property
    def paper_path(self) -> Path:
        return self.dir / "paper.json"

    @property
    def real_path(self) -> Path:
        return self.dir / "real.json"

    @property
    def momentum_path(self) -> Path:
        return self.dir / "paper_momentum.json"

    @property
    def factor_path(self) -> Path:
        return self.dir / "paper_factor.json"

    BOOK_FILES = {"factor": "paper_factor.json", "engine": "paper.json", "momentum": "paper_momentum.json"}
    BOOK_NAMES = {"factor": "Phòng thủ", "engine": "Quant Engine", "momentum": "Động lượng"}
    MIN_CAPITAL, MAX_CAPITAL = 10_000_000, 1_000_000_000_000

    def paper_capital(self) -> float:
        """The starting cash of a new paper book: set on the web page, else PAPER_CAPITAL."""
        try:
            value = float(json.loads((self.dir / "paper_capital.json").read_text(encoding="utf-8"))["capital"])
        except (OSError, ValueError, KeyError, TypeError):
            return self.settings.paper_capital
        return value if self.MIN_CAPITAL <= value <= self.MAX_CAPITAL else self.settings.paper_capital

    def reset_paper(self, capital: float, books: list[str]) -> str:
        """Start the chosen paper books again with ``capital`` (the web page's form).

        A book's history cannot be rescaled honestly (lots, ticks and fees depend on the
        cash), so it starts over: its file is kept as ``<file>.reset-<time>`` and a new book
        trades from the next run (the latest session, no backfill). Refused during a run."""
        chosen = [b for b in books if b in self.BOOK_FILES]
        if not chosen:
            raise ValueError("Chọn ít nhất một tài khoản ảo để đổi vốn.")
        if not self.MIN_CAPITAL <= capital <= self.MAX_CAPITAL:
            raise ValueError(f"Vốn phải từ {_vnd(self.MIN_CAPITAL)} đến {_vnd(self.MAX_CAPITAL)}.")
        if not self._lock.acquire(blocking=False):
            raise ValueError("Bot đang chạy phiên, thử lại sau ít phút.")
        if not self._stv_lock.acquire(blocking=False):
            self._lock.release()
            raise ValueError("Bot đang cập nhật sổ Siêu tín hiệu, thử lại sau ít phút.")
        try:
            if self._running.is_set():
                raise ValueError("Bot đang chạy phiên, thử lại sau ít phút.")
            stamp = _now_vn().strftime("%Y%m%d-%H%M%S")
            for book in chosen:
                path = self.dir / self.BOOK_FILES[book]
                if path.exists():
                    path.rename(path.with_name(f"{path.name}.reset-{stamp}"))
            self._write_json("paper_capital.json", {"capital": capital, "at": _now_vn().isoformat(timespec="minutes"),
                                                    "books": chosen})
        finally:
            self._stv_lock.release()
            self._lock.release()
        names = ", ".join(self.BOOK_NAMES[b] for b in chosen)
        logger.info("Paper books %s restarted with %.0f VND", ",".join(chosen), capital)
        return (f"Đã đặt vốn {_vnd(capital)} cho {names}. Tài khoản bắt đầu lại từ phiên chạy tới "
                "(bấm “Chạy phiên ngay” để bắt đầu luôn); sổ cũ được giữ lại trong thư mục data/trading.")

    def factor_book(self):
        from tradingagents.trading.ledger import Ledger

        return Ledger.load(self.factor_path, self.paper_capital(), fresh_on_error=True)

    @property
    def stv_path(self) -> Path:
        from vnbot import external

        return self.dir / external.BOOK_FILE

    def stv_watchlist(self) -> list[str]:
        """The stocks followed on sieutinhieu.vn (/stvtheodoi), separate from /theodoi."""
        from vnbot import external

        return external.Watchlist(self.settings.data_dir).get()

    def stv_book(self):
        """The paper book that follows sieutinhieu.vn's signals (``vnbot.external``); kept out
        of ``books()``: it never holds the other books back."""
        from tradingagents.trading.ledger import Ledger

        return Ledger.load(self.stv_path, self.paper_capital(), fresh_on_error=True)

    def momentum_book(self):
        from tradingagents.trading.ledger import Ledger

        # A damaged paper book is moved aside (kept) and restarted.
        return Ledger.load(self.momentum_path, self.paper_capital(), fresh_on_error=True)

    def paper(self):
        from tradingagents.trading.ledger import Ledger

        return Ledger.load(self.paper_path, self.paper_capital(), fresh_on_error=True)

    # The real books: the owner's account the strategies' signals are for ("real"), and the
    # separate account that follows sieutinhieu.vn ("stv", vnbot.external; SIEUTINHIEU=1).
    REAL_BOOKS = {"real": ("real.json", "tài khoản thật"), "stv": ("real_stv.json", "tài khoản thật Siêu tín hiệu")}

    def _book_path(self, book: str) -> Path:
        return self.dir / self.REAL_BOOKS[book][0]

    def real(self, book: str = "real"):
        """The real book; raises LedgerLoadError when its file is damaged (never emptied).

        The ledger moves a damaged file aside; until its owner restores it (or deletes
        the copy to start over) the book stays refused, rather than coming back empty
        on the next load and being overwritten by the next /von or /khop."""
        from tradingagents.trading.ledger import Ledger, LedgerLoadError

        if book == "stv":
            return self.taisan.ledger_view()
        path = self._book_path(book)
        if not path.exists():
            aside = sorted(path.parent.glob(f"{path.name}.corrupt-*"))
            if aside:
                raise LedgerLoadError(
                    f"Sổ {self.REAL_BOOKS[book][1]} bị hỏng, đã chuyển sang {aside[-1].name} (không xoá). "
                    f"Khôi phục file đó thành {path.name}, hoặc xoá nó để bắt đầu sổ mới.")
        return Ledger.load(path, 0.0)

    def _real_for_update(self, book: str = "real"):
        from tradingagents.trading.ledger import LedgerLoadError

        try:
            return self.real(book)
        except LedgerLoadError as exc:
            raise ValueError(str(exc)) from exc

    def last_signals(self) -> dict:
        try:
            return json.loads((self.dir / "signals.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def last_report(self) -> dict:
        try:
            data = json.loads((self.dir / "last_report.json").read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def books(self) -> list[tuple[str, object]]:
        """(strategy, paper book) of the three strategies, in report order."""
        return [("engine", self.paper()), ("factor", self.factor_book()), ("momentum", self.momentum_book())]

    def last_day(self) -> str:
        """The last session every paper book has processed ("" before the first run)."""
        return min(_book_day(book) for _, book in self.books())

    def _write_json(self, name: str, data) -> None:
        write_text_atomic(self.dir / name, json.dumps(data, ensure_ascii=False))

    # ── the basket ──
    def universe(self) -> list[str]:
        from tradingagents.dataflows import money_flows as mf

        codes: list[str] = []
        if self.settings.universe_vn30:
            codes += mf.index_members("VN30")
        codes += self.watchlist.get()
        out = []
        for code in codes:
            if code not in out:
                out.append(code)
        return out

    # ── the sessions ──
    def run_day(self, day: str) -> str:
        """Bring the books up to ``day`` (every missed session first). Returns the report."""
        return self.catch_up(day).text

    def catch_up(self, day: str) -> RunResult:
        """Process, in order, every final VNINDEX session after each book's last day up to
        ``day``; place the next session's orders; report on the last one."""
        with self._lock:
            self._running.set()
            try:
                result = self._catch_up(day)
            finally:
                self._running.clear()
        return self._with_stv(result)

    def _catch_up(self, day: str) -> RunResult:
        from tradingagents.market import vn_rules
        from tradingagents.quant.params import active

        params = active()
        paper, mom_book, fb_book = self.paper(), self.momentum_book(), self.factor_book()
        real, real_error = self._real_snapshot()
        market = _Market(min(day, final_day()))
        sessions = market.sessions(vn_rules.BENCHMARK)     # VNINDEX unreadable: raise, retried later
        if not sessions:
            raise RuntimeError(f"VNINDEX không có phiên nào tới {market.as_of}")
        # A held or ordered stock without the latest bar although it traded the session before:
        # the source may still be publishing it. Processing now would skip its stop and drop
        # its order for good, so the session waits (until LATE_BAR_CUTOFF on that day); after
        # that the stock is taken as suspended and the report says so.
        books3 = (paper, mom_book, fb_book)
        fresh_gaps = self._fresh_gaps(sessions, market, books3, real)
        # Likewise the names a book ranks today (VN30 for Động lượng, the cap universe for
        # Phòng thủ): a basket ranked without a late name is kept for weeks.
        rank_gaps = [s for s in self._fresh_gaps(sessions, market, books3, None,
                                                 only=self._ranking_names(sessions[-1], mom_book, fb_book, params))
                     if s not in fresh_gaps]
        waiting = fresh_gaps + rank_gaps if (fresh_gaps or rank_gaps) and _before_cutoff(sessions[-1]) else []
        if waiting:
            logger.info("Bot: no %s bar yet for %s; session waits", sessions[-1], ",".join(waiting))
            sessions = sessions[:-1]
            if not sessions:
                raise RuntimeError(f"chưa có nến phiên đầu tiên của {', '.join(waiting)}")
        target = sessions[-1]
        p_todo, p_skipped = _pending(sessions, _book_day(paper))
        m_todo, m_skipped = _pending(sessions, _book_day(mom_book))
        f_todo, f_skipped = _pending(sessions, _book_day(fb_book))
        last = self.last_report()
        if not p_todo and not m_todo and not f_todo:
            if last.get("day") == target and last.get("text"):
                return RunResult(target, last["text"], waiting=waiting)
            days = [_book_day(paper), _book_day(mom_book), _book_day(fb_book)]
            if any(d != target for d in days):
                return RunResult(target, f"✅ Phiên {target} đã xử lý (sổ đang ở phiên {max(days)}).",
                                 waiting=waiting)
            # Every book is at the target but its report was lost (a crash before writing it):
            # rebuild it from the books, without processing anything again.

        universe = self.universe() if p_todo else None
        engine_out: dict = {}
        for s in p_todo:
            engine_out = self._engine_session(s, sessions, paper, real, market, universe, params, final=s == target)
        fb_decision = None
        for s in f_todo:
            fb_decision = self._factor_session(s, sessions, fb_book, market, params)
        mom_decision = None
        for s in m_todo:
            mom_decision = self._momentum_session(s, sessions, mom_book, market, params)

        if engine_out.get("day") == target:
            signals, real_only, errors = engine_out["signals"], engine_out["real_only"], engine_out["errors"]
        else:
            # The engine book was already at the target (a run resumed after a failure):
            # its decisions were saved; analyse again only if they were not.
            signals, real_only, errors = self._saved_signals(target)
            if signals is None:
                signals, real_only, errors = self._signals_for(
                    target, paper, real, self.universe() if universe is None else universe, params)
                # Saved as ``_engine_session`` does: the next retry of this day (late bars, /chay)
                # must not analyse the whole basket again, and /khop must see today's levels.
                self._write_json("signals.json", {"day": target, "signals": {s.symbol: s.__dict__ for s in signals}})
        if mom_decision is None:
            mom_decision = self._decision_from_meta(mom_book, params)
        if fb_decision is None:
            fb_decision = self._factor_decision_from_meta(fb_book, params)

        text = self._factor_text(target, fb_book, fb_decision, params)
        text += "\n\n" + self._paper_report(target, paper, [f for f in paper.fills if f.date == target],
                                            [o for o in paper.orders if o.created == target], signals, errors)
        text += "\n\n" + self._momentum_text(target, mom_book, mom_decision, params)
        text += "\n\n" + self._comparison(paper, mom_book, fb_book)
        if self.settings.trade_mode == "signal":
            if real is None:
                text += f"\n\n⚠️ Không đọc được tài khoản thật: {real_error}"
            else:
                # Market value (order sizes) and today's price basis (stop/T1/T2) first.
                real, real_notes = self._mark_real(real, target, market)
                real.settle_to(target)               # shares bought T+2 ago are sellable (report only)
                # A holding in an ex-date gap the source has not re-adjusted yet is checked on
                # its levels divided by the gap's estimated ratio (a copy: the book keeps them).
                real = _estimated_view(real, market.exchange)
                bars = {s: b for s in real.positions if (b := market.bar(s, target)) is not None}
                text += self._strategy_switch_notice(last, real)
                if self.settings.signal_strategy == "factor":
                    block = self._real_factor_report(target, real, bars, fb_decision, market, params)
                elif self.settings.signal_strategy == "momentum":
                    block = self._real_momentum_report(target, real, bars, mom_decision, market, params)
                else:
                    block = self._real_report(target, real, bars, signals, real_only, params)
                notes = _real_adjustment_lines(real_notes)
                if notes:
                    title, _, rest = block.partition("\n")
                    block = "\n".join([title, *notes] + ([rest] if rest else []))
                text += "\n\n" + block
        done = sorted(set(p_todo) | set(m_todo) | set(f_todo))
        earlier = [s for s in done if s != target]
        head = []
        if earlier:
            head.append(f"⏪ Chạy bù {len(earlier)} phiên bị lỡ: {', '.join(earlier)}"
                        + self._missed_fills(earlier, paper, mom_book, fb_book))
        if p_skipped or m_skipped or f_skipped:
            head.append(f"⚠️ Bỏ qua {max(p_skipped, m_skipped, f_skipped)} phiên cũ hơn (chỉ chạy bù tối đa {MAX_CATCH_UP} "
                        "phiên); lệnh chờ của các phiên đó đã huỷ.")
        if fresh_gaps and not waiting:
            head.append(f"⚠️ Không có nến phiên {target} của {', '.join(fresh_gaps)} (đang giữ hoặc có lệnh) sau khi "
                        "chờ nguồn giá: coi như tạm ngừng giao dịch - phiên này không xét stop/mục tiêu, lệnh chờ của "
                        "mã đó bị huỷ. Kiểm tra trên app CTCK.")
        if rank_gaps and not waiting:
            head.append(f"⚠️ Không có nến phiên {target} của {', '.join(rank_gaps)} (rổ xếp hạng VN30/Phòng thủ) sau "
                        "khi chờ nguồn giá: nếu hôm nay xếp hạng lại thì các mã này không được xét.")
        if head:
            text = "\n".join(head) + "\n\n" + text
        self._write_json("last_report.json", {"day": target, "text": text,
                                              "at": _now_vn().isoformat(timespec="minutes"),
                                              "sessions": done, "signal_strategy": self.settings.signal_strategy})
        # A rebuilt report (books done, report lost) was never sent either: it is new too.
        return RunResult(target, text, bool(done) or last.get("day") != target, done, waiting=waiting)

    @staticmethod
    def _fresh_gaps(sessions, market, books, real, *, only=None) -> list[str]:
        """Stocks with no bar on the latest session although they traded on each of the
        ``LATE_BAR_HISTORY`` sessions before: the source may still be publishing it (or it is
        the first day of a suspension, which only time tells apart). A stock that skips
        sessions (thin trading) or was already suspended is not waited for.

        The stocks: those a paper book holds or has an order for that session, and the real
        book's holdings; or, with ``only``, those names. Empty when every book already
        processed the session."""
        if len(sessions) < 2 or all(_book_day(book) >= sessions[-1] for book in books):
            return []
        target, prev = sessions[-1], sessions[-2]
        recent = sessions[-1 - LATE_BAR_HISTORY:-1]
        if only is not None:
            symbols = set(only)
        else:
            symbols = set(real.positions) if real is not None else set()
            for book in books:      # orders older than the session before lapse unfilled anyway
                symbols |= set(book.positions) | {o.symbol for o in book.orders if o.created >= prev}
        out = []
        for symbol in sorted(symbols):
            try:
                if market.bar(symbol, target) is not None:
                    continue
                frame = market.frame(symbol, prev)
                if frame is None or not len(frame):
                    continue
                traded = {d.strftime("%Y-%m-%d") for d in frame["Date"].tail(len(recent) + 10)}
                if all(s in traded for s in recent):
                    out.append(symbol)
            except Exception:  # noqa: BLE001 - a source that does not answer fails the session itself
                continue
        return out

    def _ranking_names(self, target, mom_book, fb_book, params) -> list[str]:
        """The names a book may rank on ``target`` (as ``momentum.decide`` / ``factor.decide``
        would see it): VN30 when the momentum rotation is due or may turn on, the cached
        cap universe at a rebuild month end or when the defensive book may turn on."""
        from tradingagents.dataflows import money_flows as mf
        from tradingagents.trading import factor

        names: list[str] = []
        meta = mom_book.meta
        if (int(meta.get("mom_since", 10**6)) + 1 >= params.mom_rebalance or not meta.get("mom_market_ok")
                or "mom_basket" not in meta):
            try:
                names += mf.index_members("VN30")
            except Exception as exc:  # noqa: BLE001 - only a wait is lost
                logger.warning("Bot: VN30 members unavailable for the late-bar check: %s", exc)
        meta = fb_book.meta
        nxt = factor.next_session_guess(target)
        month_end = factor._month(nxt) != factor._month(target) and factor._rebuild_month(
            factor._month(target), params.fb_rebalance_months)
        may_turn_on = meta.get("fb_basket") is None and ("fb_on" not in meta or factor._week(nxt) != factor._week(target))
        if month_end or may_turn_on:
            cache = self._read_universes()
            if cache:
                names += [s for s in cache[max(cache)]["universe"] if s not in names]
        return names

    # ── the sieutinhieu.vn comparison book ──
    def _with_stv(self, result: RunResult) -> RunResult:
        """Run the comparison book after the bot's run, OUTSIDE the trading lock (its fetches
        must never hold up /chay, /khop or the capital reset), and attach its section: to the
        report when the run produced one (saved again for the web page), else as ``extra``.
        Nothing here can fail or change the bot's run."""
        from vnbot import external

        if not external.enabled() or not result.day:
            return result
        if not self._stv_lock.acquire(blocking=False):
            return result                     # another run is updating it right now
        try:
            book = self.stv_book()
            if not result.new and _book_day(book) >= result.day:
                return result                 # nothing new for it either
            try:
                market = _Market(result.day)
                sessions = market.sessions(_benchmark())
                section = self._stv_run(sessions, market, result.day, book)
            except Exception as exc:  # noqa: BLE001 - the comparison book never stops the bot's own run
                logger.exception("Siêu tín hiệu book failed")
                section = f"📡 {external.NAME}: chưa cập nhật được ({exc}); các tài khoản của bot không bị ảnh hưởng."
        finally:
            self._stv_lock.release()
        if result.new:
            result.text += "\n\n" + section
            last = self.last_report()
            if last.get("day") == result.day:
                last["text"] = result.text
                self._write_json("last_report.json", last)
        else:
            result.extra = section
        return result

    def _stv_run(self, sessions, market, target, book) -> str:
        """Fetch the site's signals for the universe (and the book's holdings), bring its paper
        book up to ``target`` and report it beside the bot's decisions of ``target``."""
        from vnbot import external

        store = external.SignalStore(self.settings.data_dir)
        saved, _, _ = self._saved_signals(target)
        signals = saved or []
        # Its own holdings, then ITS watchlist (not the bot's): at most external.max_symbols() lookups.
        symbols = list(dict.fromkeys(list(book.positions) + self.stv_watchlist()))
        try:
            stv_real = self.real("stv")
        except Exception:  # noqa: BLE001 - reported by /stv
            stv_real = None
        held = list(stv_real.positions) if stv_real is not None else []
        # The separate account's holdings are fetched too (its exits), but are not candidates
        # of the comparison book: it must not buy a stock only because the owner holds it.
        fetched, error = store.refresh(list(dict.fromkeys(held + symbols)), fresh_after=f"{target}T15:10")
        market_error = store.refresh_market(target)       # the free market-wide list, for the morning brief
        if stv_real is not None and stv_real.positions:
            try:
                self._mark_real(stv_real, target, market, book="stv")
            except Exception as exc:  # noqa: BLE001
                logger.warning("Siêu tín hiệu real book not marked: %s", exc)
        todo, skipped = _pending(sessions, _book_day(book))
        early = []
        if target == today_vn() and _now_vn().strftime("%H:%M") < external.SITE_FINAL_TIME:
            # The site posts the day's signals from ~15:08: a state fetched before that is
            # yesterday's. Processing ``target`` now would stamp the book at it with no BUY
            # of the day and never replay it (``_with_stv`` returns once the book is at the
            # day); leave it for the next run (``is_done`` keeps the scheduler and /chay coming back).
            early = [s for s in todo if s >= target]
            todo = [s for s in todo if s < target]
        for s in todo:
            self._stv_session(s, sessions, book, market, store, symbols)
        st = book.stats()
        x = external
        lines = [f"📡 SỔ ẢO SIÊU TÍN HIỆU · phiên {x._ddmm(target)}",
                 "Tham khảo, không ảnh hưởng lệnh của bot · khớp giá mở cửa phiên sau, có phí",
                 *([f"⏳ Chưa tới {external.SITE_FINAL_TIME}: trang chưa chốt tín hiệu phiên {x._ddmm(target)}, "
                    "sổ ảo xử lý phiên này ở lượt chạy sau."] if early else []),
                 f"NAV {_fmt(st['nav'])} ({st['return_pct']:+.2f}% từ {x._ddmm(book.start_date or target)})"
                 + (f" · VNINDEX {st['benchmark_pct']:+.2f}%" if st["benchmark_pct"] is not None else "")
                 + f" · sụt giảm tối đa {st['max_drawdown_pct']:.2f}%"]
        fills = [f for f in book.fills if f.date == target]
        if fills:
            lines += ["", "Khớp phiên này:"]
            lines += [f"{x.icon(f.side)} {x.word(f.side)} {f.symbol} {_fmt(f.qty)} cp @ {_fmt(f.price)}"
                      + (f" · lãi/lỗ {_bn(f.pnl)}" if f.pnl is not None else "") + (f" · {f.reason}" if f.reason else "")
                      for f in fills]
        orders = [o for o in book.orders if o.created == target]
        if orders:
            lines += ["", "Lệnh phiên tới (giá mở cửa):"]
            lines += [f"{x.icon(o.side)} {x.word(o.side)} {o.symbol} {_fmt(o.qty)} cp · {o.reason}" for o in orders]
        engine = {sig.symbol: sig.action for sig in signals or []}
        today = sorted(((sym, sig) for sym in symbols if (sig := store.state(sym, target)) and sig["day"] == target),
                       key=lambda item: (item[1]["side"] != "BUY", -(item[1].get("strength_pct") or 0)))
        lines += ["", f"Tín hiệu mới trong watchlist Siêu tín hiệu ({len(today)}):" if today
                  else f"Không có tín hiệu mới trong watchlist Siêu tín hiệu phiên {x._ddmm(target)}."]
        lines += [x.signal_line(sym, sig) + (f" · bot: {x.ACTION_VI.get(engine[sym], engine[sym])}" if sym in engine else "")
                  for sym, sig in today]
        # Only the stocks fetched for this session: an older history saved for a stock no longer
        # followed would show a SELL of weeks ago as the site's view of today.
        fresh = f"{target}T15:10"
        clash = [f"{sym} (từ {x._ddmm(sig['day'])})" for sym, action in engine.items()
                 if action == "BUY" and store.fetched_at(sym) >= fresh
                 and (sig := store.state(sym, target)) and sig["side"] == "SELL"]
        if clash:
            lines.append("⚠️ Bot MUA nhưng trang đang BÁN: " + ", ".join(clash))
        if error or market_error:
            lines.append(f"⚠️ Lấy tín hiệu chưa trọn ({error or market_error}); lượt sau sẽ lấy lại.")
        if skipped:
            lines.append(f"⚠️ Bỏ qua {skipped} phiên cũ (chạy bù tối đa {MAX_CATCH_UP}).")
        logger.info("Siêu tín hiệu: %d stocks fetched, book at %s", fetched, _book_day(book))
        return "\n".join(lines)

    def _stv_session(self, day, sessions, book, market, store, symbols) -> None:
        """One session of the comparison book: fills of the orders of the session before, then
        the orders for the next open from the site's signals of ``day`` (``external.plan``)."""
        from tradingagents.market import vn_rules
        from tradingagents.trading.ledger import _ExitRules
        from vnbot import external

        i = sessions.index(day)
        _expire(book, sessions[i - 1] if i else None)
        touched = {o.symbol for o in book.orders} | set(book.positions)
        # Not ``required``: a source that does not answer makes it a session without fills
        # for that stock here, never a failed run of the bot.
        bars = {s: b for s in touched if (b := market.bar(s, day)) is not None}
        book.process_session(bars, rules=_ExitRules(max_hold_sessions=10**6))   # exits only on the site's SELL
        book.end_of_session(day, market.close(vn_rules.BENCHMARK, day))
        last_close = {}
        for symbol in set(symbols) | set(book.positions):
            bar = bars.get(symbol) or market.bar(symbol, day)
            if bar is not None and _finite(bar.close) and bar.close > 0:
                last_close[symbol] = float(bar.close)
        external.plan(book, day, store, symbols, last_close)
        book.meta["last_day"] = day
        book.save(self.stv_path)

    def _stv_real_lines(self, day, store, market) -> list[str]:
        """The owner's Tài sản portfolio in the morning brief: marked to the closes of ``day``
        (an ex-date moves its levels), each holding at the price as entered (no fee or tax
        taken off), and its exits: the site turned SELL, the close under the cut-loss, the
        target reached. No buy is suggested: the owner records the buys (/tsmua)."""
        from vnbot import external as x

        title = "💼 TÀI SẢN CỦA ANH"
        real = self.real("stv")
        notes = []
        if real.positions:
            real, notes = self._mark_real(real, day, market, book="stv")
        kept = real.meta.get("notes") if isinstance(real.meta.get("notes"), dict) else {}
        if kept.get("day") == day:          # marked by the evening run of ``day``
            seen = {(n.get("kind"), n.get("symbol")) for n in notes}
            notes = [n for n in kept.get("notes") or [] if (n.get("kind"), n.get("symbol")) not in seen] + notes
        view = _estimated_view(real, market.exchange)
        if not view.positions:
            return [title, "Chưa giữ mã nào. Mua xong báo /tsmua MÃ KL GIÁ (giá đã gồm phí)."]
        summary = self.taisan.summary({s: p.last_price for s, p in view.positions.items() if p.last_price})
        lines = [title, f"Tổng tài sản {_fmt(summary['total'])} · lãi/lỗ {_money(summary['pnl'])}"
                 + (f" ({x.pct(summary['pnl_pct'])})" if summary["pnl_pct"] is not None else "")
                 + f" · theo giá đóng cửa {x._ddmm(day)}"]
        lines += [_ts_words(line) for line in _real_adjustment_lines(notes, cmd="/tsthuong")]
        cutoff = x.next_open_cutoff(day)
        for symbol, pos in view.positions.items():
            signal = store.state(symbol, day, cutoff)
            bar = market.bar(symbol, day)
            price = pos.last_price or pos.avg_price
            money = (price - pos.avg_price) * pos.qty
            change = (price / pos.avg_price - 1) * 100 if pos.avg_price else 0.0
            lines.append(f"• {symbol} {_fmt(pos.qty)} cp · vốn {_fmt(pos.avg_price)} → {_fmt(price)}"
                         f" · {_money(money)} ({x.pct(change)})")
            levels = " · ".join(([f"cắt lỗ {_fmt(pos.stop)}"] if pos.stop else [])
                                + ([f"mục tiêu {_fmt(pos.target1)}"] if pos.target1 else []))
            if levels:
                lines.append(x.INDENT + levels)
            if signal and signal["side"] == "SELL":
                lines.append(f"{x.INDENT}🔴 Trang BÁN {x._ddmm(signal['day'])} @ {_fmt(signal['price'])} → "
                             + _advice(pos, day, when=" phiên tới"))
            elif bar is not None and bar.tradable and pos.stop and bar.close <= pos.stop:
                lines.append(f"{x.INDENT}⛔ Đóng cửa {_fmt(bar.close)} ≤ cắt lỗ {_fmt(pos.stop)} → " + _advice(pos, day))
            elif bar is not None and bar.tradable and pos.target1 and bar.high >= pos.target1:
                lines.append(f"{x.INDENT}🎯 Chạm mục tiêu {_fmt(pos.target1)} (cao nhất {_fmt(bar.high)}) → cân nhắc chốt lời")
        return lines

    def stv_morning(self, session: str | None = None) -> str:
        """The morning brief (Zoom): the free market-wide list of the last session, the signals
        of the followed stocks (with the site's plan), and the owner's portfolio with its real
        P&L and the site's SELLs."""
        from vnbot import external

        if not external.enabled():
            return f"{external.NAME} đang tắt: đặt SIEUTINHIEU=1 trong .env rồi khởi động lại."
        from tradingagents.market import vn_rules

        session = session or self.session_day()
        expected = vn_rules.prev_session(today_vn())      # at least the session before today
        stale = session < expected
        store = external.SignalStore(self.settings.data_dir)
        held = []
        for loader in (lambda: self.real("stv"), self.stv_book):
            try:
                held += list(loader().positions)
            except Exception:  # noqa: BLE001
                pass
        symbols = list(dict.fromkeys(held + self.stv_watchlist()))
        # The evening run's fetch (after the session's signals) is fresh enough: no second round.
        _, error = store.refresh(symbols, fresh_after=f"{session}T15:10", max_age_hours=24)
        x = external
        dd = x._ddmm(session)
        lines = [f"☀️ SIÊU TÍN HIỆU · sáng {_now_vn().strftime('%d/%m')}",
                 f"Tín hiệu phiên {dd} của sieutinhieu.vn · chỉ tham khảo, không đổi lệnh của bot"]
        if stale:
            lines.append(f"⚠️ Nguồn giá chưa có phiên {x._ddmm(expected)}: các danh sách dưới đây là của phiên {dd}, "
                         "đã cũ.")
        lines += ["", "📊 TOÀN THỊ TRƯỜNG"]
        for side in ("BUY", "SELL"):
            m = store.market(side, session)
            if m is None:
                lines.append(f"{x.word(side)}: chưa có (lấy lúc chạy tối {dd}).")
                continue
            shown = m["signals"]
            locked = f" · khoá {m['locked']} (cần Premium)" if m.get("locked") else ""
            lines.append(f"{x.word(side)}: {m['total']} mã đổi tín hiệu · xem miễn phí {len(shown)}{locked}")
            lines += [x.signal_line(s["symbol"], s, plan=True) for s in shown]
        watch = self.stv_watchlist()
        new = sorted(((sym, sig) for sym in symbols if (sig := store.state(sym, session)) and sig["day"] == session),
                     key=lambda item: (item[1]["side"] != "BUY", -(item[1].get("strength_pct") or 0)))
        known = [sym for sym in symbols if store.state(sym, session)]
        lines += ["", f"👀 WATCHLIST SIÊU TÍN HIỆU · {len(watch)} mã"]
        lines.append(f"Tín hiệu mới phiên {dd} ({len(new)}):" if new else f"Không có tín hiệu mới phiên {dd}.")
        lines += [x.signal_line(sym, sig, plan=True) for sym, sig in new]
        if any(sig["side"] == "BUY" for _, sig in new):
            lines.append("Anh tự quyết định mua; mua xong báo /tsmua MÃ KL GIÁ (bot lấy cắt lỗ, mục tiêu của trang).")
        in_watch = [sym for sym in known if sym in watch]
        buying = [sym for sym in in_watch if (sig := store.state(sym, session)) and sig["side"] == "BUY"]
        if buying:
            lines.append(f"Đang ở trạng thái MUA ({len(buying)}/{len(in_watch)} mã):")
            lines += x.rows(buying, per_row=8)
        if error:
            lines.append(f"⚠️ Lấy tín hiệu chưa trọn ({error}).")
        market = _Market(session)
        lines.append("")
        lines += self._stv_real_lines(session, store, market)
        lines += ["", "ℹ️ Giá tín hiệu là giá đóng cửa phiên phát.",
                  "Vùng vào, mục tiêu, cắt lỗ là của trang. Không phải khuyến nghị."]
        return "\n".join(lines)

    def _live_prices(self, symbols) -> tuple[dict[str, float], str]:
        """Today's last matched prices ({symbol: price}, label) on a session day from the ATO
        to the end of the day ("khớp HH:MM" in the session, "đóng cửa hôm nay" after it:
        the last match is then the close, before the evening run marks it); ({}, "") else."""
        from tradingagents.market import vn_rules
        from vnbot.intraday import live_price

        now = _now_vn()
        day = now.strftime("%Y-%m-%d")
        phase = vn_rules.session_phase(now)
        if not vn_rules.is_session_day(day) or phase in ("closed", "pre_open"):
            return {}, ""
        out = {}
        for symbol in symbols:
            try:
                price = live_price(symbol, day)
            except Exception:  # noqa: BLE001 - that holding shows its last close
                price = None
            if price and _finite(price) and price > 0:
                out[symbol] = float(price)
        return out, ("đóng cửa hôm nay" if phase == "after_close" else f"khớp {now.strftime('%H:%M')}")

    def _current_prices(self, positions) -> tuple[dict[str, tuple[float | None, str]], str]:
        """{symbol: (price, label)} and the label most prices share: today's last match when
        there is one, else the last close the book marked, else the last close of the price
        source (a holding not marked yet), else (None, "chưa có giá")."""
        from vnbot.intraday import history_close

        live, label = self._live_prices([p.symbol for p in positions])
        out = {}
        for p in positions:
            if p.symbol in live:
                out[p.symbol] = (live[p.symbol], label)
            elif p.last_date and p.last_price:
                out[p.symbol] = (p.last_price, f"đóng cửa {p.last_date[8:10]}/{p.last_date[5:7]}")
            else:
                from tradingagents.market import vn_rules

                last = final_day()
                if not vn_rules.is_session_day(last):
                    last = vn_rules.prev_session(last)
                close = None
                try:
                    close = history_close(p.symbol, last, today_vn())
                except Exception:  # noqa: BLE001 - shown as "chưa có giá"
                    close = None
                out[p.symbol] = ((close, f"đóng cửa {last[8:10]}/{last[5:7]}") if close
                                 else (None, "chưa có giá"))
        labels = [lab for _, lab in out.values()]
        if live:
            return out, label                 # today's price leads; the others say which close they show
        return out, (max(set(labels), key=labels.count) if labels else "")

    def _stv_holdings_lines(self, real, today: str) -> list[str]:
        """The owner's holdings with the real P&L (sold now: fee and tax off), at today's last
        match when there is one, else at the last close."""
        from vnbot import external as x

        prices, common = self._current_prices(list(real.positions.values()))
        total = cost = proceeds = 0.0
        rows, unpriced = [], []
        for p in real.positions.values():
            price, label = prices[p.symbol]
            if price is None:
                unpriced.append(p.symbol)
                rows.append(f"• {p.symbol} {_fmt(p.qty)} cp · vốn {_fmt(p.avg_price)} · chưa có giá")
            else:
                money, pct = _net_pnl(p, price)
                total += money
                cost += p.avg_price * p.qty
                proceeds += p.avg_price * p.qty + money
                rows.append(f"• {p.symbol} {_fmt(p.qty)} cp · vốn {_fmt(p.avg_price)} → {_fmt(price)}"
                            + ("" if label == common else f" ({label})"))
                rows.append(f"{x.INDENT}Lãi/lỗ: {_money(money)} ({x.pct(pct)})")
            levels = " · ".join(([f"cắt lỗ {_fmt(p.stop)}"] if p.stop else [])
                                + ([f"mục tiêu {_fmt(p.target1)}"] if p.target1 else []))
            if levels:
                rows.append(x.INDENT + levels)
            note = _t2_now(p)[1]
            if note:
                rows.append(f"{x.INDENT}⏳ {note}")
        head = [f"Lãi/lỗ thực tế: {_money(total)}" + (f" ({x.pct(total / cost * 100)})" if cost else ""),
                (f"(theo giá {common}, đã trừ phí bán và thuế 0,1%)" if common != "chưa có giá"
                 else "(chưa có giá hiện tại của các mã)"),
                f"Tiền {_fmt(real.cash)} · bán hết nhận {_fmt(proceeds)} · tổng {_fmt(real.cash + proceeds)}"]
        if unpriced:
            head.append(f"(chưa có giá, không tính vào tổng: {', '.join(unpriced)})")
        return head + rows

    def stv_text(self) -> str:
        """``/stv``: the short overview - the owner's portfolio, the comparison book against the bot's."""
        from vnbot import external as x

        if not x.enabled():
            return f"{x.NAME} đang tắt: đặt SIEUTINHIEU=1 trong .env rồi khởi động lại."
        lines = ["📡 SIÊU TÍN HIỆU · tổng quan"]
        try:
            s = self.taisan.summary()
            if s["positions"]:
                lines.append(f"💼 Tài sản của anh: {len(s['positions'])} mã · tổng {_fmt(s['total'])} · lãi/lỗ "
                             f"{_money(s['pnl'])}" + (f" ({x.pct(s['pnl_pct'])})" if s["pnl_pct"] is not None else ""))
                lines.append(f"{x.INDENT}tiền {_fmt(s['cash'])} · giá {s['label']} · chi tiết /ts")
            else:
                lines.append("💼 Tài sản của anh: chưa giữ mã nào (/ts)")
        except Exception as exc:  # noqa: BLE001
            lines.append(f"💼 Tài sản của anh: ⚠️ {exc}")
        book = self.stv_book()
        st = book.stats()
        win = f"{st['win_rate_pct']}%" if st["win_rate_pct"] is not None else "-"
        lines += [f"📒 Sổ ảo so sánh: NAV {_fmt(st['nav'])} ({st['return_pct']:+.2f}%)",
                  f"{x.INDENT}{st['trades']} lệnh mua · thắng {win} · sụt giảm tối đa {st['max_drawdown_pct']:.1f}%",
                  "", "📊 Lợi nhuận từ ngày bắt đầu của mỗi sổ:"]
        for title, b in (("Siêu tín hiệu (sổ ảo)", book), ("Phòng thủ", self.factor_book()),
                         ("Quant Engine", self.paper()), ("Động lượng", self.momentum_book())):
            bs = b.stats()
            lines.append(f"• {title} {bs['return_pct']:+.2f}%" + (f" từ {x._ddmm(b.start_date)}" if b.start_date else "")
                         + f" · sụt giảm {bs['max_drawdown_pct']:.1f}%")
        wl = x.Watchlist(self.settings.data_dir)
        lines += ["", f"👀 Watchlist: {len(wl.get())} mã ({wl.source()}) · quét danh mục {x.scan_minutes()} phút/lần",
                  "Sổ ảo khớp giá mở cửa phiên sau tín hiệu, có phí và T+2 - cùng luật với các sổ của bot.",
                  "", "Danh mục: /stvdanhmuc · một mã: /stv MÃ", "Bản tin: /stvsang · quét ngay: /stvquet"]
        return "\n".join(lines)

    def stv_portfolio_text(self) -> str:
        """``/stvdanhmuc``: the comparison book (the owner's real money is the Tài sản portfolio, /ts)."""
        from vnbot import external as x

        if not x.enabled():
            return f"{x.NAME} đang tắt: đặt SIEUTINHIEU=1 trong .env rồi khởi động lại."
        book = self.stv_book()
        st = book.stats()
        lines = ["📒 SỔ ẢO SO SÁNH SIÊU TÍN HIỆU", "Bot tự khớp theo tín hiệu của trang, chỉ để so với các sổ của bot.",
                 f"NAV {_fmt(st['nav'])} ({st['return_pct']:+.2f}%"
                 + (f" từ {x._ddmm(book.start_date)}" if book.start_date else "") + f") · tiền {_fmt(book.cash)}"]
        for p in book.positions.values():
            pnl = (p.last_price / p.avg_price - 1) * 100 if p.avg_price and p.last_price else 0.0
            lines.append(f"• {p.symbol} {_fmt(p.qty)} cp · {_fmt(p.avg_price)} → {_fmt(p.last_price)} ({x.pct(pnl)})")
        if not book.positions:
            lines.append("Chưa giữ mã nào.")
        if book.orders:
            lines.append("Sổ ảo sẽ khớp phiên tới:")
            lines += [f"{x.icon(o.side)} {x.word(o.side)} {o.symbol} {_fmt(o.qty)} cp · {o.reason}" for o in book.orders]
        lines += ["", "Danh mục tiền thật của anh: /ts (trang web /taisan) · tổng quan: /stv · quét bán ngay: /stvquet"]
        return "\n".join(lines)

    def stv_scan(self, *, report: bool = False) -> str | None:
        """Every ``external.scan_minutes()`` from 09:00 to 17:00 on session days (DailyScheduler)
        and on /stvquet. One request per stock, a stock in both lists asked once:

        - the owner's holdings, always: a message for each SELL not announced yet;
        - the watchlist, after the close (``WATCH_FROM``-``WATCH_UNTIL``: the site's per-stock
          signals of the day come ~15:08 and do not change during the session), during the
          session too with ``STV_WATCH_INTRADAY=1``, and always on /stvquet: its new BUY and
          SELL signals of the day, each announced once;
        - during the session, the free view of the site's live trend changes (two stocks a
          side): the rows of followed or held stocks, as "not final";
        - during the session, the bot's own estimate of those changes for every followed stock
          (``vnbot.trend_estimate``: the last price against the level at which the close would
          flip the site's trend; public prices, no request to the site), as "not final";
        - after the close, the in-session changes announced that the close did not keep.

        None when there is nothing new - with ``report`` (/stvquet) a short status instead."""
        from tradingagents.trading.ledger import LedgerLoadError
        from vnbot import external as x

        if not x.enabled():
            return f"{x.NAME} đang tắt." if report else None
        try:
            real = self.real("stv")
        except LedgerLoadError as exc:
            return f"⚠️ {exc}" if report else None
        with self._stv_scan_lock:
            return self._stv_scan(real, report)

    def _stv_scan(self, real, report) -> str | None:
        from tradingagents.dataflows.vendors import sieutinhieu as stv
        from tradingagents.market import vn_rules
        from vnbot import external as x

        now = _now_vn()
        today, hhmm = now.strftime("%Y-%m-%d"), now.strftime("%H:%M")
        held = list(real.positions)
        watch = self.stv_watchlist()
        followed = set(held) | set(watch)
        with_watch = report or x.watch_intraday() or x.WATCH_FROM <= hhmm < x.WATCH_UNTIL
        in_session = vn_rules.is_session_day(today) and "09:00" <= hhmm < "15:00"
        symbols = list(dict.fromkeys(held + (watch if with_watch else [])))       # each stock once
        store = x.SignalStore(self.settings.data_dir)
        alerts = x.SellAlerts(self.settings.data_dir)
        if not held:
            alerts.remember([], [])                 # all sold: forget the announced SELLs
        fresh = (now - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M")
        fetched, error = store.refresh(symbols, fresh_after=fresh) if symbols else (0, "")
        live, site_live = [], {}                     # the site's free intraday view (followed stocks: ``live``)
        if in_session:
            for side in ("BUY", "SELL"):
                try:
                    site_live[side] = stv.fetch_live_changes(side)
                    live += [c for c in site_live[side]["changes"] if c["symbol"] in followed and c["day"] == today]
                except Exception as exc:  # noqa: BLE001 - the next scan asks again
                    error = error or f"danh sách trong phiên: {exc}"

        estimate = {}                                # the bot's estimate of the site's changes, in the session
        if in_session and x.estimate_on():
            try:
                estimate = self._stv_estimate(list(dict.fromkeys(held + watch)), store, now, live=True)
            except Exception as exc:  # noqa: BLE001 - the site's own signals still go out
                logger.warning("Siêu tín hiệu: ước tính đổi chiều lỗi: %s", exc)
        stamp = f"{today}T{hhmm}"
        guessed = [{"symbol": r["symbol"], "side": r["flip"], "day": today, "at": stamp, "price": r["price"],
                    "now": r["price"], "from": r["state"], "stage": "estimate", "level": r["level"]}
                   for r in estimate.get("rows", []) if r.get("flip")]

        # 1. the holdings: a SELL (the stock's history, or a change in the session: the site's, the bot's estimate)
        states = {sym: store.state(sym, today) for sym in held}
        for c in live + guessed:                     # the site's own view first
            if c["symbol"] in states and c["side"] == "SELL":
                sig = states[c["symbol"]]
                if not sig or sig["day"] < today:
                    states[c["symbol"]] = {"day": today, "side": "SELL", "price": c["price"], "created": c["at"],
                                           "live": True, "strength_pct": None, "intraday": True,
                                           "estimate": c["stage"] == "estimate", "level": c.get("level")}
        sells = [(sym, sig) for sym, sig in states.items() if sig and sig["side"] == "SELL"]
        new_sells = alerts.pending(sells)

        # 2. the watchlist: the day's new signals (final), and the changes shown in the session
        items = []
        if with_watch:
            for sym in watch:
                sig = store.state(sym, today)
                if sig and sig["day"] == today and not (sym in held and sig["side"] == "SELL"):
                    items.append((sym, f"{sig['side']}:{'final' if x.SellAlerts.final(sig) else 'intraday'}", sig))
        for c in live + guessed:                     # one alert a stock and side: the site's row when both
            if not (c["symbol"] in held and c["side"] == "SELL"):
                items.append((c["symbol"], f"{c['side']}:intraday", c))
        seen = x.WatchAlerts(self.settings.data_dir)
        new_watch = seen.pending(today, items)
        held_moves = [(sym, "SELL:intraday", sig) for sym, sig in new_sells if sig.get("intraday")]
        if with_watch and hhmm >= x.WATCH_FROM and vn_rules.is_session_day(today):
            new_watch += self._stv_reverted(today, seen, store, followed)
        market_blocks, market_note = [], ""
        if x.market_on():
            try:
                market_blocks, market_note = self._stv_market(now, store, site_live, held, watch, push=not report)
            except Exception as exc:  # noqa: BLE001 - the stocks the owner follows still go out
                logger.warning("Siêu tín hiệu: quét toàn thị trường lỗi: %s", exc)

        if not new_sells and not new_watch:
            alerts.remember([], held)              # forget the stocks sold since
            if market_blocks:
                return "\n\n".join(market_blocks)
            if report:
                return self._stv_scan_status(now, states, symbols, with_watch, store, live, error, estimate, market_note)
            if error and not fetched and symbols and alerts.warn_once(today):
                return (f"⚠️ Siêu tín hiệu: chưa lấy được tín hiệu ({error}). Bot vẫn thử lại "
                        f"{x.scan_minutes()} phút/lần; trong lúc đó tín hiệu có thể đến muộn.")
            return None
        lines = [f"📡 SIÊU TÍN HIỆU · {now.strftime('%H:%M %d/%m')}"]
        if new_sells:
            lines += ["", "🔻 TÀI SẢN CỦA ANH · tín hiệu BÁN"] + self._stv_sell_lines(real, new_sells, today)
            lines.append("Anh tự đặt lệnh; bán xong báo /tsban MÃ KL GIÁ.")
        finals = [(sym, sig) for sym, key, sig in new_watch if "at" not in sig and not key.endswith(":reverted")]
        moves = [(sym, c) for sym, key, c in new_watch if "at" in c and c.get("stage") != "estimate"]   # the site's view
        guesses = [(sym, c) for sym, key, c in new_watch if c.get("stage") == "estimate"]            # the bot's estimate
        undone = [(sym, c) for sym, key, c in new_watch if key.endswith(":reverted")]
        if finals:
            finals.sort(key=lambda it: (it[1]["side"] != "BUY", -(it[1].get("strength_pct") or 0)))
            lines += ["", f"👀 WATCHLIST · tín hiệu mới phiên {x._ddmm(today)} (đã chốt sau giờ đóng cửa)"]
            lines += [x.signal_line(sym, sig, plan=True) + (" · đang giữ" if sym in held else "")
                      + ("" if x.SellAlerts.final(sig) else " · trong phiên, chưa chốt") for sym, sig in finals]
            if any(sig["side"] == "BUY" for _, sig in finals):
                lines.append("Anh tự quyết định mua; mua xong báo /tsmua MÃ KL GIÁ.")
        if moves:
            lines += ["", "👀 Đổi chiều trong phiên (chưa chốt, có thể đổi khi đóng cửa)"]
            lines += [f"{x.icon(c['side'])} {sym} {x.word(c['from']) + ' → ' if c['from'] in ('BUY', 'SELL') else ''}"
                      f"{x.word(c['side'])} lúc {c['at'][11:16]}" + (f" · giá {_fmt(c['now'])}" if c.get("now") else "")
                      + (" · đang giữ" if sym in held else "") for sym, c in moves]
            lines.append("(miễn phí, trang chỉ cho xem 2 mã đổi chiều mỗi bên; danh sách đầy đủ cần Premium)")
        if guesses:
            lines += ["", "🧮 Bot ước tính đổi chiều trong phiên (giá đã qua mức đổi chiều; chưa chốt)"]
            lines += [f"{x.icon(c['side'])} {sym} {x.word(c['from'])} → {x.word(c['side'])} · giá {_fmt(c['price'])} "
                      f"{'<' if c['side'] == 'SELL' else '>'} mức {_fmt(c['level'])}" + (" · đang giữ" if sym in held else "")
                      for sym, c in guesses]
            lines.append("(ước tính của bot, không phải tín hiệu của trang; chi tiết: /stvdoichieu)")
        if undone:
            lines += ["", "↩️ Đóng cửa không giữ đổi chiều trong phiên (trang vẫn như cũ): "
                      + ", ".join(f"{sym} ({x.word(c['side'])})" for sym, c in undone)]
        if error:
            lines.append(f"⚠️ Lấy tín hiệu chưa trọn ({error}).")
        alerts.remember(new_sells, held)          # only once the message is built
        seen.remember(today, new_watch + held_moves)
        return "\n\n".join(["\n".join(lines)] + market_blocks)

    def _stv_market(self, now, store, site_live, held, watch, *, push: bool) -> tuple[list[str], str]:
        """The bot's market-wide scan in a scan (``vnbot.market_scan``): the history kept up
        (one request a session), the scan in the session (one request for the 300), and -
        with ``push`` - the day's list once from 14:46 and the comparison with the site once
        its signals are out. Returns (blocks to send, a status line for /stvquet)."""
        from tradingagents.market import vn_rules
        from vnbot import external as x
        from vnbot import market_scan as ms

        today, hhmm = now.strftime("%Y-%m-%d"), now.strftime("%H:%M")
        session_day = vn_rules.is_session_day(today)
        closes = ms.MarketCloses(self.settings.data_dir)
        snaps = ms.Snapshot(self.settings.data_dir)
        followed = set(held) | set(watch)
        in_window = session_day and ms.WINDOW[0] <= hhmm < ms.WINDOW[1]
        # The history up to the session before (from 15:30: up to today, whose closes are out, ready
        # for tomorrow): nothing to ask once it is there.
        through = today if session_day and hhmm >= "15:30" else vn_rules.prev_session(today)
        if self._market_levels.get("basis") != through:      # else the history up to it is there already
            have, want = closes.progress(through)
            if have < want:
                closes.update(through, budget=ms.BUDGET_IN_SESSION if in_window else ms.BUDGET_OFF)
        blocks, note = [], ""
        if in_window:
            snap = ms.scan(closes, x.market_size(), now, levels_cache=self._market_levels)
            if snap.get("error"):
                return [], f"Toàn thị trường: {snap['error']}"
            snap["site"] = ms.site_view(site_live)
            saved = snaps.update(last=snap)
            flips = [r for r in snap["rows"] if r.get("flip")]
            note = (f"Toàn thị trường ({snap['size']} mã): {sum(r['flip'] == 'BUY' for r in flips)} qua mức MUA, "
                    f"{sum(r['flip'] == 'SELL' for r in flips)} qua mức BÁN · /stvthitruong")
            if push and hhmm >= ms.SUMMARY_AFTER and saved.get("summary_day") != today:
                blocks.append(ms.summary_text(snap, followed))
                snaps.update(summary_day=today)
        if push and session_day and hhmm >= ms.COMPARE_AFTER:
            text = self._stv_market_compare(now, store, closes, snaps, followed)
            if text:
                blocks.append(text)
        return blocks, note

    def _stv_market_compare(self, now, store, closes, snaps, followed) -> str | None:
        """Once a session, when the site's signals of the day are out: the bot's scan at the
        close against the site (``market_scan.compare``), saved and returned as a message."""
        from vnbot import external as x
        from vnbot import market_scan as ms

        today, hhmm = now.strftime("%Y-%m-%d"), now.strftime("%H:%M")
        log = ms.CompareLog(self.settings.data_dir)
        if log.get(today) is not None:
            return None
        give_up = hhmm >= ms.COMPARE_GIVE_UP
        fresh = [s for s in followed if store.fetched_at(s) >= f"{today}T15:10"]
        if len(fresh) < 0.9 * len(followed) and not give_up:
            return None                               # the watchlist's signals of the day not all fetched yet
        if store.market_missing(today):
            store.refresh_market(today)
        counts = {side: (store.market(side, today) or {}).get("total") for side in ("BUY", "SELL")}
        if any(v is None for v in counts.values()) and not give_up:
            return None
        snap = ms.scan(closes, x.market_size(), now, levels_cache=self._market_levels)   # the board's price: the close
        if snap.get("error"):
            return None
        site_final, site_states = {}, {}
        for sym in fresh:
            sig = store.state(sym, today)
            if sig:
                site_states[sym] = sig["side"]
                if sig["day"] == today:
                    site_final[sym] = sig["side"]
        record = ms.compare(snap, sorted(followed), site_final, site_states, counts)
        log.add(today, record)
        snaps.update(final=snap)
        return ms.compare_text(today, record, log.totals())

    def stv_market_text(self) -> str:
        """``/stvthitruong``: the bot's market-wide scan. In the session (from 09:15): the
        stocks past their level now (at most 20 a side, most traded first) and the closest,
        with what the site shows; else the last scan at the close and its comparison."""
        from tradingagents.dataflows.vendors import sieutinhieu as stv
        from tradingagents.market import vn_rules
        from tradingagents.trading.ledger import LedgerLoadError
        from vnbot import external as x
        from vnbot import market_scan as ms

        if not x.enabled():
            return f"{x.NAME} đang tắt."
        if not x.market_on():
            return "Quét toàn thị trường đang tắt (STV_MARKET=0)."
        try:
            held = list(self.real("stv").positions)
        except LedgerLoadError:
            held = []
        followed = set(held) | set(self.stv_watchlist())
        now = _now_vn()
        today, hhmm = now.strftime("%Y-%m-%d"), now.strftime("%H:%M")
        closes = ms.MarketCloses(self.settings.data_dir)
        snaps = ms.Snapshot(self.settings.data_dir)
        session_day = vn_rules.is_session_day(today)
        in_window = session_day and ms.WINDOW[0] <= hhmm < ms.WINDOW[1]
        with self._stv_scan_lock:
            basis = vn_rules.prev_session(today) if in_window else (
                today if session_day and hhmm >= "15:30" else vn_rules.prev_session(today))
            have, want = closes.progress(basis)
            if have < want:
                closes.update(basis, budget=ms.BUDGET_OFF)
                have, want = closes.progress(basis)
                if have < want:
                    return (f"⏳ Đang tải lịch sử giá toàn thị trường: {have}/{want} phiên (mỗi phiên một lần hỏi, "
                            "lần đầu mất vài phút). Gõ lại /stvthitruong sau ít phút; trong phiên bot tự tải tiếp.")
            saved = snaps.read()
            snap = saved.get("last") or {}
            recent = (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M")
            if in_window and not (snap.get("at", "") >= recent and snap.get("at", "")[:10] == today):
                snap = ms.scan(closes, x.market_size(), now, levels_cache=self._market_levels)
                if snap.get("error"):
                    return f"Toàn thị trường: {snap['error']}."
                site = {}
                for side in ("BUY", "SELL"):
                    try:
                        site[side] = stv.fetch_live_changes(side)
                    except Exception:  # noqa: BLE001 - shown without the site's view
                        pass
                snap["site"] = ms.site_view(site)
                snaps.update(last=snap)
            elif not in_window:
                snap = saved.get("final") or snap
        if not snap.get("rows"):
            return "Chưa có lượt quét toàn thị trường nào (bot quét trong phiên, từ 09:15)."
        day = snap["at"][:10]
        flips = [r for r in snap["rows"] if r.get("flip")]
        buy = [r for r in flips if r["flip"] == "BUY"]
        sell = [r for r in flips if r["flip"] == "SELL"]
        live_now = in_window and day == today
        lines = [f"🌐 TOÀN THỊ TRƯỜNG · bot quét {snap['size']} mã · {snap['at'][11:16]} {x._ddmm(day)}"
                 + ("" if live_now else " (giá đóng cửa)"),
                 f"Đã qua mức đổi chiều: {len(buy)} sang MUA · {len(sell)} sang BÁN (xếp theo giá trị giao dịch)"]

        def line(r):
            mark = " *" if r["symbol"] in followed else ""
            return (f"{x.icon(r['flip'])} {r['symbol']} {_fmt(r['price'])} {'>' if r['flip'] == 'BUY' else '<'} "
                    f"{_fmt(r['level'])}{mark}")

        cap = 20
        for side_rows in (buy, sell):
            if side_rows:
                lines += [""] + [line(r) for r in side_rows[:cap]]
                if len(side_rows) > cap:
                    lines.append(f"… và {len(side_rows) - cap} mã nữa")
        near = sorted((r for r in snap["rows"] if not r.get("flip") and r["dist"] <= 1.0), key=lambda r: r["dist"])
        if near and live_now:
            lines += ["", f"Sát mức, cách ≤ 1% ({len(near)}): "
                      + ", ".join(f"{r['symbol']} {x.word('SELL' if r['state'] == 'BUY' else 'BUY')} "
                                  f"{r['dist']:.1f}%".replace(".", ",") for r in near[:cap])]
        if any(r["symbol"] in followed for r in flips):
            lines.append("(* mã anh đang giữ hoặc theo dõi)")
        site = ms.site_lines(snap) if live_now else []
        if site:
            lines += [""] + site
        log = ms.CompareLog(self.settings.data_dir)
        rec_day = max(log.read() or {"": None})
        if rec_day:
            rec = log.get(rec_day)
            f = rec.get("followed") or {}
            lines += ["", f"Đối chiếu phiên {x._ddmm(rec_day)} trên {f.get('n', 0)} mã anh theo dõi: bot đúng "
                      f"{len(f.get('matched') or [])}/{len(f.get('site') or {})} tín hiệu của trang, báo thừa "
                      f"{len(f.get('extra') or [])}; trạng thái khớp {f.get('agree', 0)}/{f.get('states', 0)} mã."]
        lines.append(ms.accuracy_line(log.totals()))
        lines += ["", "Ước tính của bot (TB20 + MACD theo giá đóng cửa, trạng thái cũng do bot tính), không phải danh "
                  "sách của trang. Trang chốt sau giờ đóng cửa (~15:08)."]
        return "\n".join(lines)

    def _stv_reverted(self, today, seen, store, followed) -> list:
        """After the close (the site's signals of the day are out from ~15:08): the changes
        announced in the session ("SIDE:intraday") that the day's final signal does not
        confirm, each once ("SIDE:reverted"). Only for stocks fetched since 15:10."""
        out = []
        for sym, keys in seen.keys(today).items():
            if sym not in followed or store.fetched_at(sym) < f"{today}T15:10":
                continue
            for side in ("BUY", "SELL"):
                if f"{side}:intraday" not in keys or f"{side}:reverted" in keys:
                    continue
                sig = store.state(sym, today)
                if not (sig and sig["day"] == today and sig["side"] == side):
                    out.append((sym, f"{side}:reverted", {"side": side}))
        return out

    def _stv_sell_lines(self, real, new_sells, today) -> list[str]:
        """The SELL of each Tài sản holding: 🔴 the site's SELL after the close, 🟠 a change in
        the session (the site's, or the bot's estimate), the price, the P&L at the price as
        entered, what can be sold now (T+2). Each alert is kept for its shadow result."""
        from tradingagents.market import vn_rules
        from vnbot import external as x

        prices, _ = self._current_prices([real.positions[sym] for sym, _ in new_sells])
        lines = []
        for sym, sig in new_sells:
            pos = real.positions[sym]
            price, label = prices[sym]
            early = bool(sig.get("estimate") or sig.get("intraday") or not x.SellAlerts.final(sig))
            mark = "🟠 Sớm" if early else "🔴 Mạnh"
            head = (f"{mark} · {sym} {_fmt(pos.qty)} cp · bot ước tính đổi sang BÁN lúc {sig['created'][11:16]} "
                    f"(giá {_fmt(sig['price'])} < mức {_fmt(sig.get('level'))})" if sig.get("estimate")
                    else f"{mark} · {sym} {_fmt(pos.qty)} cp · trang BÁN trong phiên lúc {sig['created'][11:16]}" if sig.get("intraday")
                    else f"{mark} · {sym} {_fmt(pos.qty)} cp · trang BÁN {x._ddmm(sig['day'])} @ {_fmt(sig['price'])}"
                    + (f" · mạnh {sig['strength_pct']}%" if sig.get("strength_pct") is not None else ""))
            lines += ["", head]
            if price:
                money = (price - pos.avg_price) * pos.qty
                change = (price / pos.avg_price - 1) * 100 if pos.avg_price else 0.0
                lines.append(f"{x.INDENT}Giá {_fmt(price)} ({label}) · lãi/lỗ theo giá vốn: {_money(money)} ({x.pct(change)})")
            free, note = _t2_now(pos)
            # Backtest 2026-10-23 (docs/backtest_2026-10-23/thoat_tai_san.md): selling at the close on an
            # early change was better than waiting for the site's SELL in 48% of 547 cases, on
            # average no better - so the early alert says to wait for the site unless near the cut-loss.
            lines.append(f"{x.INDENT}→ " + ("theo dõi, chờ trang chốt sau 15:10; chỉ bán sớm khi sát mức cắt lỗ"
                                            if early else f"BÁN {_fmt(free)} cp" if free else "chưa bán được")
                         + (f"; {note}" if note else ""))
            if sig.get("estimate"):
                lines.append(f"{x.INDENT}(ước tính của bot, chưa phải tín hiệu của trang; có thể đổi khi đóng cửa)")
            elif not x.SellAlerts.final(sig):
                lines.append(f"{x.INDENT}(tín hiệu trong phiên, có thể đổi khi đóng cửa)")
            elif not sig.get("live"):
                lines.append(f"{x.INDENT}(lịch sử trang tạo lại, từ {x._ddmm(sig['day'])})")
            elif sig["day"] < vn_rules.prev_session(today):
                lines.append(f"{x.INDENT}(không phải tín hiệu mới: trang đã ở trạng thái BÁN từ {x._ddmm(sig['day'])})")
            try:
                self.taisan.record_alert("early" if early else "sell", sym, price, today)
            except Exception as exc:  # noqa: BLE001 - the alert goes out anyway
                logger.warning("Tài sản: alert of %s not kept: %s", sym, exc)
        lines.append("")
        return lines

    def _stv_scan_status(self, now, states, symbols, with_watch, store, live, error, estimate=None, market_note="") -> str:
        """``/stvquet`` when nothing is new: what was scanned and the state of each holding."""
        from vnbot import external as x

        today = now.strftime("%Y-%m-%d")
        lines = [f"✅ Đã quét {len(symbols)} mã lúc {now.strftime('%H:%M')} (danh mục + watchlist, mã trùng hỏi 1 lần): "
                 "không có tín hiệu mới chưa báo."]
        if states:
            lines += ["", "Danh mục của anh:"]
            lines += [f"• {sym}: " + (f"{x.word(sig['side'])} từ {x._ddmm(sig['day'])}" if sig else "chưa có tín hiệu")
                      + (" (đã báo)" if sig and sig["side"] == "SELL" else "") for sym, sig in states.items()]
        if with_watch:
            today_sigs = [(sym, sig) for sym in self.stv_watchlist() if (sig := store.state(sym, today)) and sig["day"] == today]
            lines.append(f"Watchlist: {len(today_sigs)} mã có tín hiệu mới phiên {x._ddmm(today)}"
                         + (" (đã báo)" if today_sigs else "")
                         + ("" if now.strftime("%H:%M") >= "15:15" else " - tín hiệu từng mã có sau giờ đóng cửa (~15:08)"))
        if live:
            lines.append("Trong phiên: " + ", ".join(f"{c['symbol']} {x.word(c['side'])} {c['at'][11:16]}" for c in live))
        if estimate:
            flips = [r for r in estimate.get("rows", []) if r.get("flip")]
            lines.append(f"Bot ước tính đổi chiều: {len(estimate.get('rows', []))} mã có giá, "
                         + (", ".join(f"{r['symbol']} → {x.word(r['flip'])}" for r in flips) + " (đã báo)" if flips
                            else "chưa mã nào qua mức") + " · /stvdoichieu")
        if market_note:
            lines.append(market_note)
        if error:
            lines.append(f"⚠️ Lấy tín hiệu chưa trọn ({error}).")
        return "\n".join(lines)

    def _stv_estimate(self, symbols, store, now, *, live: bool) -> dict:
        """The bot's estimate of the site's trend changes (``vnbot.trend_estimate``), saved for
        /stvdoichieu: ``{at, basis, live, rows, skipped}``.

        ``live`` (in the session): today's last price against the levels from the closes up to
        the previous session, from the site's state of that session. Else: the levels for the
        next session, from the last close. The site's state comes from its per-stock history;
        a stock not fetched since that session's signals (~15:08) is fetched first (once a
        day, so normally nothing), and a state still not current is used only when the rule
        agrees with it."""
        from tradingagents.market import vn_rules
        from vnbot import trend_estimate as te
        from vnbot.intraday import exchange, live_price

        today = now.strftime("%Y-%m-%d")
        stamp = now.strftime("%Y-%m-%dT%H:%M")
        basis = vn_rules.prev_session(today) if live else final_day(now)
        if not vn_rules.is_session_day(basis):
            basis = vn_rules.prev_session(basis)
        cache = te.LevelCache(self.settings.data_dir)
        lv, errors = cache.get(symbols, basis)
        if not live and not lv and basis == today:            # today's close not in the source yet
            basis = vn_rules.prev_session(today)
            lv, errors = cache.get(symbols, basis)
        cutoff = f"{basis}T15:10"
        stale = [s for s in symbols if store.fetched_at(s) < cutoff]
        if stale and stamp >= cutoff:
            store.refresh(stale, fresh_after=cutoff, max_age_hours=24 * 7)
        states, skipped = {}, dict(errors)
        for sym in symbols:
            sig = store.state(sym, basis)
            if not sig or sym in skipped:
                continue
            if store.fetched_at(sym) < cutoff and (sym not in lv or lv[sym]["rule"] != sig["side"]):
                skipped[sym] = "trạng thái của trang chưa cập nhật"
                continue
            states[sym] = sig["side"]
        refs = {}
        if live:
            wanted = [s for s in symbols if s in states and s in lv]
            try:
                board = te.board_prices(wanted) if wanted else {}
            except Exception as exc:  # noqa: BLE001 - one price a stock from DNSE then
                logger.info("Price board unavailable (%s): last prices one by one", exc)
                board = {}
            prices = {s: b["price"] for s, b in board.items() if b.get("price")}
            refs = {s: (b.get("ref"), b.get("exchange")) for s, b in board.items()}
            started = time.monotonic()
            for sym in wanted:
                if sym not in board:                 # on the board without a price: no match yet today
                    if time.monotonic() - started > te.PRICES_BUDGET_SECONDS:
                        skipped[sym] = "nguồn giá chậm, xem lượt sau"     # DNSE slow or down: the scan goes on
                        continue
                    try:
                        prices[sym] = live_price(sym, today)
                    except Exception:  # noqa: BLE001 - that stock is left out this time
                        prices[sym] = None
        else:
            prices = {sym: v["prev"] for sym, v in lv.items()}

        def exchange_of(sym):
            if refs.get(sym, (None, None))[1]:
                return refs[sym][1]
            try:
                return exchange(sym)
            except Exception:  # noqa: BLE001 - the widest band then
                return None

        rows, why = te.assess([s for s in symbols if s not in skipped], states, lv, prices, exchange_of=exchange_of,
                              refs=refs)
        skipped.update(why)
        result = {"at": stamp, "basis": basis, "live": live, "rows": rows, "skipped": skipped}
        te.Snapshot(self.settings.data_dir).write(result)
        return result

    def stv_trend_text(self) -> str:
        """``/stvdoichieu``: the bot's estimate for the followed stocks (holdings + watchlist).
        In the session: the stocks past their flip level at the last price, then the closest;
        outside it: the levels at which the next close flips each trend, closest first."""
        from tradingagents.market import vn_rules
        from tradingagents.trading.ledger import LedgerLoadError
        from vnbot import external as x
        from vnbot import trend_estimate as te

        if not x.enabled():
            return f"{x.NAME} đang tắt."
        try:
            held = list(self.real("stv").positions)
        except LedgerLoadError as exc:
            return f"⚠️ {exc}"
        symbols = list(dict.fromkeys(held + self.stv_watchlist()))
        now = _now_vn()
        today, hhmm = now.strftime("%Y-%m-%d"), now.strftime("%H:%M")
        live = vn_rules.is_session_day(today) and "09:00" <= hhmm < "15:00"
        snap = te.Snapshot(self.settings.data_dir).read()
        recent = (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M")
        if not (live and snap.get("live") and snap.get("at", "") >= recent and snap.get("at", "")[:10] == today):
            with self._stv_scan_lock:
                snap = self._stv_estimate(symbols, x.SignalStore(self.settings.data_dir), now, live=live)
        rows = [r for r in snap.get("rows", []) if r["symbol"] in symbols]
        skipped = {s: why for s, why in (snap.get("skipped") or {}).items() if s in symbols}

        def tag(sym):
            return " · đang giữ" if sym in held else ""

        def far(r):
            return f"{r['dist']:.1f}%".replace(".", ",")

        cap = 15
        if snap.get("live"):
            lines = [f"🧮 ĐỔI CHIỀU TRONG PHIÊN · {snap['at'][11:16]} {x._ddmm(snap['at'][:10])}",
                     f"Bot ước tính cho {len(rows)} mã (danh mục + watchlist), nếu đóng cửa ở giá hiện tại."]
            flipped = sorted((r for r in rows if r.get("flip")), key=lambda r: r["dist"])
            near = sorted((r for r in rows if not r.get("flip") and r["dist"] <= te.NEAR_PCT), key=lambda r: r["dist"])
            if flipped:
                lines += ["", f"Đã qua mức đổi chiều ({len(flipped)}):"]
                lines += [f"{x.icon(r['flip'])} {r['symbol']} {x.word(r['state'])} → {x.word(r['flip'])} · giá {_fmt(r['price'])} "
                          f"{'<' if r['flip'] == 'SELL' else '>'} {_fmt(r['level'])}{tag(r['symbol'])}" for r in flipped[:cap]]
            if near:
                lines += ["", f"Sát mức, cách ≤ {te.NEAR_PCT:.0f}% ({len(near)}):"]
                lines += [f"• {r['symbol']} {x.word(r['state'])} · {_fmt(r['price'])} · "
                          f"{x.word('SELL' if r['state'] == 'BUY' else 'BUY')} nếu {'<' if r['state'] == 'BUY' else '>'} "
                          f"{_fmt(r['level'])} (cách {far(r)}){tag(r['symbol'])}" for r in near[:cap]]
            if not flipped and not near:
                lines += ["", f"Chưa mã nào qua hoặc sát mức đổi chiều (cách ≤ {te.NEAR_PCT:.0f}%)."]
            shown = flipped[:cap] + near[:cap]
            if len(flipped) + len(near) > len(shown):
                lines.append(f"... và {len(flipped) + len(near) - len(shown)} mã nữa")
            lines.append(f"Bot quét {x.scan_minutes()} phút/lần và nhắn khi một mã qua mức.")
        else:
            basis = snap.get("basis", "")
            lines = [f"🧮 MỨC ĐỔI CHIỀU PHIÊN TỚI (theo đóng cửa {x._ddmm(basis)})",
                     f"{len(rows)} mã (danh mục + watchlist), gần mức nhất trước:"]
            near = sorted(rows, key=lambda r: r["dist"])
            lines += [""] + [f"• {r['symbol']} {x.word(r['state'])} · đóng {_fmt(r['price'])} · "
                             f"{x.word('SELL' if r['state'] == 'BUY' else 'BUY')} nếu đóng cửa {'<' if r['state'] == 'BUY' else '>'} "
                             f"{_fmt(r['level'])} (cách {far(r)}){tag(r['symbol'])}" for r in near[:cap]]
            if len(near) > cap:
                lines.append(f"... và {len(near) - cap} mã xa mức hơn")
            lines.append(f"Trong phiên bot quét {x.scan_minutes()} phút/lần và nhắn khi một mã qua mức.")
        if skipped:
            lines.append(f"Không tính được {len(skipped)} mã: "
                         + ", ".join(f"{s} ({why})" for s, why in list(skipped.items())[:6])
                         + (" ..." if len(skipped) > 6 else ""))
        lines += ["", f"Ước tính của bot ({te.METHOD}), không phải tín hiệu của trang: {te.MATCH_NOTE}. "
                  "Trang chốt tín hiệu sau giờ đóng cửa (~15:08)."]
        return "\n".join(lines)

    def _strategy_switch_notice(self, last: dict, real) -> str:
        """A one-time note when the real signals follow another strategy than in the last
        report (a changed SIGNAL_STRATEGY, or the default that moved from momentum to
        factor on 2026-10-15) while the real account holds stocks."""
        from tradingagents.trading.calibrate import STRATEGIES

        now = self.settings.signal_strategy
        if not last.get("day") or not real.positions:
            return ""
        # Reports written before the key existed followed the old default (momentum),
        # unless SIGNAL_STRATEGY said otherwise; that cannot be known, so say "probably".
        before = last.get("signal_strategy")
        if before == now or (before is None and now == "momentum"):
            return ""
        was = STRATEGIES.get(before, before) if before else "có lẽ Động lượng (mặc định cũ)"
        return (f"\n\nℹ️ Tín hiệu thật đổi chiến lược: {was} → {STRATEGIES.get(now, now)}. "
                "Lệnh dưới đây đưa tài khoản thật về rổ của chiến lược mới, kể cả bán các mã đang giữ "
                "theo chiến lược cũ. Muốn giữ chiến lược cũ: đặt SIGNAL_STRATEGY=momentum (hoặc engine) "
                "trong .env rồi khởi động lại bot.")

    def _saved_signals(self, day: str):
        from tradingagents.trading.ledger import Signal

        saved = self.last_signals()
        if saved.get("day") != day or not isinstance(saved.get("signals"), dict):
            return None, {}, []
        try:
            return [Signal(**v) for v in saved["signals"].values()], {}, []
        except TypeError:
            return None, {}, []

    def _real_snapshot(self):
        from tradingagents.trading.ledger import LedgerLoadError

        with self._real_lock:
            try:
                return self.real(), ""
            except LedgerLoadError as exc:
                logger.error("Real book unreadable: %s", exc)
                return None, str(exc)

    def _mark_real(self, snapshot, day, market, book: str = "real"):
        """The real book marked to the closes up to ``day`` and put on today's price basis
        (``Ledger.mark_to_history``), then saved: its NAV - and so the sizes of the orders
        suggested for it - is at market value, the intraday alerts take the floor from the
        last close, and a dividend or bonus issue moves the stop/targets instead of firing
        a fake stop. Returns (book, notes); the snapshot and no notes when the book cannot
        be read again (it is reported as before)."""
        from tradingagents.trading.ledger import LedgerLoadError

        for symbol in list(snapshot.positions):   # download before the lock: /khop must not wait on a vendor
            try:
                market.frame(symbol, day)
                market.exchange(symbol)
            except Exception:  # noqa: BLE001 - retried (and reported) below
                pass
        with self._real_lock:
            try:
                real = self.real(book)    # read again under the lock: a /khop may have come in since
            except LedgerLoadError as exc:
                logger.error("Real book unreadable, not marked: %s", exc)
                return snapshot, []
            history, exchanges = {}, {}
            for symbol in real.positions:
                try:
                    frame = market.frame(symbol, day)
                    if frame is None or not len(frame):
                        continue
                    history[symbol] = ([d.strftime("%Y-%m-%d") for d in frame["Date"]],
                                       [float(c) for c in frame["Close"]])
                    exchanges[symbol] = market.exchange(symbol)
                except Exception as exc:  # noqa: BLE001 - one holding must not stop the report
                    logger.warning("Real book: %s not marked: %s", symbol, exc)
            if not real.positions:
                return real, []           # nothing to mark (and no file created for an unused book)
            notes = real.mark_to_history(history, exchanges)
            if book == "stv" and notes:
                # Shown once by the morning brief of the session (the evening run marks first).
                kept = real.meta.get("notes") if isinstance(real.meta.get("notes"), dict) else {}
                old = [n for n in kept.get("notes") or [] if isinstance(n, dict)] if kept.get("day") == day else []
                new = [n for n in notes if isinstance(n, dict)]
                fresh = {(n.get("kind"), n.get("symbol")) for n in new}
                real.meta["notes"] = {"day": day, "notes": [n for n in old if (n.get("kind"), n.get("symbol")) not in fresh] + new}
            if book == "stv":
                self.taisan.save_view(real)       # the Tài sản portfolio keeps its marks and levels itself
            else:
                real.save(self._book_path(book))
        return real, notes

    def _engine_session(self, day, sessions, paper, real, market, universe, params, *, final) -> dict:
        """One session of the Quant Engine's book: settle, decide, plan, save."""
        from tradingagents.market import vn_rules

        prev = sessions[sessions.index(day) - 1] if sessions.index(day) else None
        _expire(paper, prev)
        touched = {o.symbol for o in paper.orders} | set(paper.positions)
        # A held stock whose source does not answer stops the run (retried later) rather
        # than pass for suspended: its stop would be skipped and its order lost.
        bars = {s: b for s in touched if (b := market.bar(s, day, required=True)) is not None}
        paper.process_session(bars, params=params)
        paper.end_of_session(day, market.close(vn_rules.BENCHMARK, day))
        signals, real_only, errors = self._signals_for(day, paper, real if final else None, universe, params)
        paper.plan_orders(day, signals, max_positions=params.max_positions,
                          buy_sessions=getattr(params, "buy_order_sessions", 1))
        paper.meta["last_day"] = day
        paper.save(self.paper_path)
        if final:
            self._write_json("signals.json", {"day": day, "signals": {s.symbol: s.__dict__ for s in signals}})
        return {"day": day, "signals": signals, "real_only": real_only, "errors": errors}

    def _signals_for(self, day, paper, real, universe, params):
        """The engine's decisions on ``day`` for the basket, the paper holdings and (when
        ``real`` is given) the real holdings, the latter judged again as holdings."""
        from tradingagents.quant import engine
        from tradingagents.trading.ledger import Signal

        held_real = list(real.positions) if real is not None else []
        symbols = list(dict.fromkeys(list(universe) + list(paper.positions) + held_real))
        signals, real_only, errors, down = [], {}, [], []
        book = paper.portfolio_context()
        for symbol in symbols:
            try:
                result = engine.analyze(symbol, day, portfolio=book, params=params)
                signal = Signal.from_result(result)
                if signal.action == "BUY" and str(getattr(result, "last_bar_date", day))[:10] != day:
                    # Decided on an older close (suspended, or the source is late): its entry zone
                    # is not today's price. No buy; the levels stay for reference.
                    errors.append(f"{symbol}: chưa có giá phiên {day} (dữ liệu tới {result.last_bar_date}) - "
                                  "không đề xuất MUA")
                    signal.action = "WATCH"
                signals.append(signal)
                if symbol in held_real and symbol not in paper.positions:
                    real_only[symbol] = Signal.from_result(
                        engine.analyze(symbol, day, portfolio=real.portfolio_context(), params=params))
            except Exception as exc:  # noqa: BLE001 - one stock must not stop the session
                errors.append(f"{symbol}: {exc}")
                if _unavailable(exc):
                    down.append(symbol)
                logger.warning("Bot: %s on %s failed: %s", symbol, day, exc)
        held_down = [s for s in down if s in paper.positions or s in held_real]
        if held_down or len(down) > MAX_UNAVAILABLE_SHARE * len(symbols):
            # Raised before the session is saved: the scheduler retries it once the
            # sources answer, instead of a session saved with half its decisions.
            raise RuntimeError(f"Nguồn giá không trả lời cho {len(down)}/{len(symbols)} mã "
                               f"({', '.join(down[:5])}{', ...' if len(down) > 5 else ''}) - phiên {day} "
                               "chưa lưu, sẽ chạy lại.")
        return signals, real_only, errors

    # ── on demand (/chay, the web button) ──
    def session_day(self) -> str:
        """The last final session: today from 15:05, else the previous one (holidays skipped)."""
        from tradingagents.dataflows.ohlcv import load_ohlcv
        from tradingagents.market import vn_rules

        frame = load_ohlcv(vn_rules.BENCHMARK, final_day(), fill_gaps=False)
        return frame["Date"].iloc[-1].strftime("%Y-%m-%d")

    @property
    def running(self) -> bool:
        return self._running.is_set()

    def is_done(self, day: str) -> bool:
        from vnbot import external

        done = self.last_day() >= day and self.last_report().get("day") == day
        if done and external.enabled():
            done = _book_day(self.stv_book()) >= day      # a comparison book behind (a failed fetch, a reset)
        return done

    def start_run(self, notify=None) -> str:
        """Run the last closed session in the background; the report goes to ``notify``."""
        notify = notify or self.notify
        with self._start_guard:
            if self._running.is_set():
                return "⏳ Bot đang chạy một phiên, đợi vài phút."
            try:
                day = self.session_day()
            except Exception as exc:  # noqa: BLE001
                return f"⚠️ Không đọc được dữ liệu VNINDEX: {exc}"
            if self.is_done(day):
                return f"✅ Phiên {day} đã chạy rồi - xem /danhmuc, /lenh hoặc trang /bot."
            self._running.set()

        def work():
            from vnbot.service import LATE_BAR_CUTOFF

            try:
                result = self.catch_up(day)
                if result.waiting:
                    notify((result.text + "\n\n" if result.new else "")
                           + f"⏳ Nguồn giá chưa có nến phiên {day} của {', '.join(result.waiting)} (đang giữ, có lệnh "
                           "hoặc trong rổ xếp hạng): bot chưa xử lý phiên này để không bỏ sót stop/lệnh. Lần chạy theo "
                           f"lịch (từ {self.settings.trade_time}) tự thử lại 15 phút một lần, hoặc gửi /chay sau ít phút; "
                           f"sau {LATE_BAR_CUTOFF} mã chưa có nến được coi như tạm ngừng giao dịch.")
                    return
                if result.new:
                    notify(result.text)
                elif result.extra:
                    notify(result.extra)
                else:
                    notify(f"✅ Phiên {day} đã chạy rồi - xem /danhmuc hoặc /lenh.")
            except Exception as exc:  # noqa: BLE001
                logger.exception("On-demand session %s failed", day)
                notify(f"❌ Bot giao dịch lỗi phiên {day}: {exc}")
            finally:
                self._running.clear()

        threading.Thread(target=work, name="bot-run-now", daemon=True).start()
        return f"▶️ Đang chạy phiên {day} cho ba tài khoản ảo (khoảng 1 phút); báo cáo sẽ gửi khi xong."

    # ── the defensive book (the default strategy) ──
    def _factor_session(self, day, sessions, book, market, params):
        """One session of the defensive book: settle, check the market, rebuild when due,
        plan, save. Returns the decision."""
        from tradingagents.market import vn_rules
        from tradingagents.trading import factor

        i = sessions.index(day)
        prev = sessions[i - 1] if i else None
        _expire(book, prev)
        touched = {o.symbol for o in book.orders} | set(book.positions)
        bars = {s: b for s in touched if (b := market.bar(s, day, required=True)) is not None}
        book.process_session(bars, rules=factor.RULES)
        book.end_of_session(day, market.close(vn_rules.BENCHMARK, day))
        # The session after ``day`` is known when it is replayed; the latest one's is guessed
        # (a wrong guess makes the week's check or the month's rebuild run one session late).
        next_day = sessions[i + 1] if i + 1 < len(sessions) else factor.next_session_guess(day)
        vni = market.frame(vn_rules.BENCHMARK, day)
        vni_closes = [float(c) for c in vni["Close"].tail(max(params.fb_market_ma, 1))] if vni is not None else []
        decision = factor.decide(book, day, next_day, vni_closes, params,
                                 lambda: self._factor_ranking(day, sessions[: i + 1], market, params))
        last_close = {}
        for symbol in set(decision.basket) | set(book.positions):
            bar = bars.get(symbol) or market.bar(symbol, day, required=symbol in decision.basket)
            if bar is not None and _finite(bar.close) and bar.close > 0:
                last_close[symbol] = float(bar.close)
        factor.plan(book, day, decision, last_close, params)
        book.meta["last_day"] = day
        book.save(self.factor_path)
        return decision

    def _factor_ranking(self, day, sessions, market, params):
        """The defensive book's ranking on ``day`` (``sessions``: VNINDEX sessions up to it)."""
        from tradingagents.trading import factor

        universe = self._factor_universe(day, sessions, market, params)
        names, late = [], []
        prev = sessions[-2] if len(sessions) > 1 else None
        for symbol in universe:
            bar = market.bar(symbol, day, required=True)     # a source down: the session is retried
            if bar is not None and _finite(bar.close) and bar.close > 0:
                names.append(symbol)
            elif prev and market.bar(symbol, prev) is not None:
                late.append(symbol)                          # traded the session before: late, or just suspended
        if universe and len(late) > MAX_UNAVAILABLE_SHARE * len(universe) and _before_cutoff(day):
            # Too many to be suspensions: the source is still publishing the session. A basket
            # ranked without them would be held for a month.
            raise RuntimeError(f"nguồn giá chưa có nến phiên {day} của {len(late)}/{len(universe)} mã trong rổ "
                               "Phòng thủ - phiên sẽ được thử lại")
        window = sessions[-factor.SCORE_WINDOW:]
        closes = {}
        for symbol in names:
            frame = market.frame(symbol, day)
            series = {d.strftime("%Y-%m-%d"): float(c) for d, c in zip(frame["Date"], frame["Close"], strict=True)}
            closes[symbol] = factor.aligned_closes(series, window)
        return factor.rank(closes, names, momentum_weight=getattr(params, "fb_momentum_weight", 0.0))

    @property
    def universe_path(self) -> Path:
        return self.dir / UNIVERSE_FILE

    def _read_universes(self) -> dict:
        try:
            data = json.loads(self.universe_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        days = data.get("days") if isinstance(data, dict) else None
        return {d: e for d, e in (days or {}).items() if isinstance(e, dict) and isinstance(e.get("universe"), list)}

    def _factor_universe(self, day, sessions, market, params) -> list[str]:
        """The cap universe on ``day``: built from the vendors once per session and cached.

        When it cannot be built the session fails and is retried later, as for a
        missing bar: a basket ranked on another universe (an old build, today's
        VN30) would be held for a month on rules that were never evaluated."""
        cache = self._read_universes()
        entry = cache.get(day)
        if entry and entry.get("size") == params.fb_universe:
            return list(entry["universe"])
        try:
            built = self._build_universe(day, sessions, market, params)
        except Exception as exc:
            logger.warning("Bot: cap universe for %s unavailable: %s", day, exc)
            raise RuntimeError(f"chưa dựng được rổ vốn hoá ngày {day} cho Phòng thủ ({exc}); "
                               "phiên sẽ được thử lại") from exc
        cache[day] = {"size": params.fb_universe, "universe": built["universe"], "pool": built["pool"],
                      "at": _now_vn().isoformat(timespec="minutes")}
        keep = dict(sorted(cache.items())[-UNIVERSE_KEEP:])
        self._write_json(UNIVERSE_FILE, {"days": keep})
        return list(built["universe"])

    def _build_universe(self, day, sessions, market, params) -> dict:
        from tradingagents.dataflows import money_flows as mf
        from tradingagents.dataflows.vendors import vci
        from tradingagents.market import vn_rules
        from tradingagents.trading import factor

        hose = [str(row.get("symbol", "")).upper() for row in vci.fetch_listing()
                if isinstance(row, dict) and str(row.get("type", "")).upper() == "STOCK"
                and vn_rules.normalize_exchange(row.get("board")) == "HOSE"]

        def bars_of(symbol):
            frame = market.frame(symbol, day, required=True)
            if frame is None:
                return None
            volume = frame["Volume"] if "Volume" in frame else [math.nan] * len(frame)
            return {d.strftime("%Y-%m-%d"): (float(c), float(v) if _finite(v) else math.nan)
                    for d, c, v in zip(frame["Date"], frame["Close"], volume, strict=True)}

        def history_of(symbol):
            frame = mf.load_price_history(symbol, day, guarded=False)     # build_universe judges outages
            if frame is None or frame.empty:
                return {}
            cols = [frame[c] if c in frame else [math.nan] * len(frame) for c in ("MatchValue", "Close", "TotalShares")]
            return {d.strftime("%Y-%m-%d"): (float(a), float(b), float(c))
                    for d, a, b, c in zip(frame["Date"], *cols, strict=True)}

        return factor.build_universe(sessions, hose, params.fb_universe, bars_of=bars_of, history_of=history_of)

    @staticmethod
    def _factor_decision_from_meta(book, params):
        """The decision of the book's last session, from the state ``factor.decide`` kept."""
        from tradingagents.trading.factor import Decision

        on = bool(book.meta.get("fb_on", False))
        note = "" if on else f"VNINDEX dưới MA{params.fb_market_ma} - giữ tiền mặt"
        return Decision(on, False, list(book.meta.get("fb_basket") or []), note=note)

    def _factor_text(self, day, book, decision, params) -> str:
        fills = [f for f in book.fills if f.date == day]
        orders = [o for o in book.orders if o.created == day]
        st = book.stats()
        bench_txt = f" | VNINDEX {st['benchmark_pct']:+.2f}%" if st["benchmark_pct"] is not None else ""
        state = "trên" if decision.market_ok else "dưới"
        lines = [f"🛡️ BOT PHÒNG THỦ {day}",
                 f"NAV {_fmt(st['nav'])} ({st['return_pct']:+.2f}% từ {book.start_date or day}){bench_txt} | "
                 f"sụt giảm tối đa {st['max_drawdown_pct']:.2f}%",
                 f"Thị trường: VNINDEX {state} MA{params.fb_market_ma} (xét phiên cuối tuần) | rổ: "
                 + (", ".join(decision.basket) or "tiền mặt")
                 + (f" ({decision.note})" if decision.note and (decision.rebalanced or not decision.market_ok) else "")]
        lines += _adjustment_lines(book, day)
        if fills:
            lines += [f"- Khớp {f.side} {f.symbol} {_fmt(f.qty)} @ {_fmt(f.price)} ({f.reason})"
                      + (f", lãi/lỗ {_bn(f.pnl)}" if f.pnl is not None else "") for f in fills]
        if orders:
            lines += [f"- Lệnh phiên tới: {'MUA' if o.side == 'BUY' else 'BÁN'} {o.symbol} {_fmt(o.qty)} giá mở cửa ({o.reason})"
                      for o in orders]
        return "\n".join(lines)

    def _real_factor_report(self, day, real, bars, decision, market, params) -> str:
        from tradingagents.trading import factor

        return self._real_rotation_report(day, real, bars, decision, market, params, factor.plan,
                                          "📣 TÍN HIỆU THẬT - PHÒNG THỦ (tự đặt lệnh, rồi báo /khop)")

    # ── the momentum book ──
    def _momentum_session(self, day, sessions, book, market, params):
        """One session of the momentum book: settle, rank, plan, save. Returns the decision."""
        from tradingagents.dataflows import money_flows as mf
        from tradingagents.market import vn_rules
        from tradingagents.trading import momentum

        prev = sessions[sessions.index(day) - 1] if sessions.index(day) else None
        _expire(book, prev)
        touched = {o.symbol for o in book.orders} | set(book.positions)
        bars = {s: b for s in touched if (b := market.bar(s, day, required=True)) is not None}
        book.process_session(bars, rules=momentum.RULES)
        book.end_of_session(day, market.close(vn_rules.BENCHMARK, day))
        need = params.mom_lookback + 1
        closes = {}
        for symbol in mf.index_members("VN30"):
            frame = market.frame(symbol, day)
            if frame is None or not len(frame) or frame["Date"].iloc[-1].strftime("%Y-%m-%d") != day:
                continue
            closes[symbol] = [float(c) for c in frame["Close"].tail(need)]
        vni = market.frame(vn_rules.BENCHMARK, day)
        vni_closes = [float(c) for c in vni["Close"].tail(params.mom_market_ma)] if vni is not None else []
        decision = momentum.decide(book, closes, vni_closes, params)
        momentum.plan(book, day, decision, {s: c[-1] for s, c in closes.items()}, params)
        book.meta["last_day"] = day
        book.save(self.momentum_path)
        return decision

    @staticmethod
    def _decision_from_meta(book, params):
        """The decision of the book's last session, from the state ``momentum.decide`` kept."""
        from tradingagents.trading.momentum import Decision

        meta = book.meta
        ok = bool(meta.get("mom_market_ok", False))
        note = "" if ok else f"VNINDEX dưới MA{params.mom_market_ma} - giữ tiền mặt"
        return Decision(ok, int(meta.get("mom_since", 1) or 0) == 0 and ok, list(meta.get("mom_basket") or []),
                        note=note)

    def _momentum_text(self, day, book, decision, params) -> str:
        fills = [f for f in book.fills if f.date == day]
        orders = [o for o in book.orders if o.created == day]
        st = book.stats()
        bench_txt = f" | VNINDEX {st['benchmark_pct']:+.2f}%" if st["benchmark_pct"] is not None else ""
        lines = [f"🚀 BOT ĐỘNG LƯỢNG {day}",
                 f"NAV {_fmt(st['nav'])} ({st['return_pct']:+.2f}% từ {book.start_date or day}){bench_txt} | "
                 f"sụt giảm tối đa {st['max_drawdown_pct']:.2f}%",
                 ("Thị trường: VNINDEX trên MA" if decision.market_ok else "Thị trường: VNINDEX dưới MA")
                 + f"{params.mom_market_ma} | rổ: " + (", ".join(decision.basket) or "tiền mặt")
                 + (f" ({decision.note})" if decision.note and (decision.rebalanced or not decision.market_ok) else "")]
        lines += _adjustment_lines(book, day)
        if fills:
            lines += [f"- Khớp {f.side} {f.symbol} {_fmt(f.qty)} @ {_fmt(f.price)} ({f.reason})"
                      + (f", lãi/lỗ {_bn(f.pnl)}" if f.pnl is not None else "") for f in fills]
        if orders:
            lines += [f"- Lệnh phiên tới: {'MUA' if o.side == 'BUY' else 'BÁN'} {o.symbol} {_fmt(o.qty)} giá mở cửa ({o.reason})"
                      for o in orders]
        return "\n".join(lines)

    @staticmethod
    def _missed_fills(days, paper, mom_book, fb_book=None) -> str:
        wanted = set(days)
        lines = []
        books = [("Phòng thủ", fb_book)] if fb_book is not None else []
        for title, book in books + [("Quant Engine", paper), ("Động lượng", mom_book)]:
            lines += [f"\n- {f.date} {title}: {f.side} {f.symbol} {_fmt(f.qty)} @ {_fmt(f.price)} ({f.reason})"
                      for f in book.fills if f.date in wanted]
        return "".join(lines) if lines else " (không có lệnh khớp)"

    @staticmethod
    def _comparison(engine_book, mom_book, fb_book=None) -> str:
        """Each book's return since its own start (dated: the books may have started on
        different days, and then the figures cover different periods)."""
        books = ([("Phòng thủ", fb_book)] if fb_book is not None else []) + [
            ("Quant Engine", engine_book), ("Động lượng", mom_book)]
        parts, starts = [], set()
        for title, book in books:
            st = book.stats()
            since = f" từ {book.start_date}" if book.start_date else ""
            starts.add(book.start_date or "")
            parts.append(f"{title} {st['return_pct']:+.2f}%{since} (DD {st['max_drawdown_pct']:.1f}%)")
        text = "📊 So sánh: " + " | ".join(parts)
        if len(starts) > 1:
            text += "\n(các tài khoản bắt đầu ở các ngày khác nhau: lợi nhuận không cùng giai đoạn)"
        return text

    def _real_momentum_report(self, day, real, bars, decision, market, params) -> str:
        from tradingagents.trading import momentum

        return self._real_rotation_report(day, real, bars, decision, market, params, momentum.plan,
                                          "📣 TÍN HIỆU THẬT - ĐỘNG LƯỢNG (tự đặt lệnh, rồi báo /khop)")

    def _real_rotation_report(self, day, real, bars, decision, market, params, planner, title) -> str:
        """Orders that move the real book to a rotation book's basket (``planner``: its ``plan``)."""
        from tradingagents.trading.ledger import Ledger

        lines = [title]
        for symbol, pos in real.positions.items():
            bar = bars.get(symbol)
            if bar is not None and bar.tradable and pos.stop and bar.low <= pos.stop:
                lines.append(f"- ⛔ {symbol}: chạm stop {_fmt(pos.stop)} - {_sell_advice(pos, day)}")
        if real.cash <= 0 and not real.positions:
            lines.append("Chưa có vốn tài khoản thật: gửi /von 500000000 để bot tính khối lượng.")
            return "\n".join(lines)
        draft = Ledger.from_dict(copy.deepcopy(real.to_dict()))
        draft.orders = []
        last = {}
        for symbol in set(decision.basket) | set(real.positions):
            # Only names that traded on ``day`` (as the paper books do): a suspended name
            # must not get a BUY sized on an old close.
            bar = market.bar(symbol, day)
            if bar is not None and bar.tradable:
                last[symbol] = bar.close
        orders = planner(draft, day, decision, last, params)
        for o in orders:
            pos = real.positions.get(o.symbol)
            if o.side == "SELL" and pos is not None and _pending_lots(pos, day):
                lines.append(f"- BÁN {o.symbol}: {_sell_advice(pos, day, when=' giá mở cửa')} ({o.reason})")
            else:
                lines.append(f"- {'MUA' if o.side == 'BUY' else 'BÁN'} {o.symbol} {_fmt(o.qty)} cp giá mở cửa ({o.reason})")
        if len(lines) == 1:
            lines.append("Không đổi danh mục phiên tới.")
        return "\n".join(lines)

    def _paper_report(self, day, paper, fills, orders, signals, errors) -> str:
        s = paper.stats()
        bench = f" | VNINDEX {s['benchmark_pct']:+.2f}%" if s["benchmark_pct"] is not None else ""
        lines = [f"🤖 BOT PAPER {day}",
                 f"NAV {_fmt(s['nav'])} ({s['return_pct']:+.2f}% từ {paper.start_date or day}){bench} | "
                 f"sụt giảm tối đa {s['max_drawdown_pct']:.2f}%"]
        if fills:
            lines.append("Khớp hôm nay:")
            lines += [f"- {f.side} {f.symbol} {_fmt(f.qty)} @ {_fmt(f.price)} ({f.reason})"
                      + (f", lãi/lỗ {_bn(f.pnl)}" if f.pnl is not None else "") for f in fills]
        lines += _adjustment_lines(paper, day)
        if paper.positions:
            lines.append("Đang giữ:")
            for p in paper.positions.values():
                pnl = (p.last_price / p.avg_price - 1) * 100 if p.avg_price else 0
                lines.append(f"- {p.symbol} {_fmt(p.qty)} cp, vốn {_fmt(p.avg_price)}, giá {_fmt(p.last_price)} "
                             f"({pnl:+.1f}%), stop {_fmt(p.stop)}, T1 {_fmt(p.target1)}"
                             + ("" if p.sellable else " [chưa về T+2]"))
        if orders:
            lines.append("Lệnh cho phiên tới:")
            lines += [f"- {o.side} {o.symbol} {_fmt(o.qty)}"
                      + (f" giá ≤ {_fmt(o.limit)}, stop {_fmt(o.stop)}, T1 {_fmt(o.target1)}" if o.limit else " giá mở cửa")
                      for o in orders]
        else:
            buys = [s for s in signals if s.action == "BUY"]
            lines.append("Không có lệnh mới." + (f" ({len(buys)} tín hiệu BUY nhưng hết chỗ/tiền)" if buys else
                                                  " Không mã nào đạt điều kiện BUY."))
        if errors:
            lines.append(f"⚠️ {len(errors)} mã lỗi dữ liệu: " + "; ".join(errors[:3]))
        return "\n".join(lines)

    def _real_report(self, day, real, bars, signals, real_only, params) -> str:
        from tradingagents.trading.ledger import Ledger

        lines = ["📣 TÍN HIỆU CHO TÀI KHOẢN THẬT (tự đặt lệnh, rồi báo /khop)"]
        for symbol, pos in real.positions.items():
            bar = bars.get(symbol)
            if bar is not None and not bar.tradable:
                bar = None
            sig = real_only.get(symbol) or next((s for s in signals if s.symbol == symbol), None)
            pending = _pending_lots(pos, day)
            if bar is not None and pos.stop and bar.low <= pos.stop:
                lines.append(f"- ⛔ {symbol}: chạm stop {_fmt(pos.stop)} hôm nay - {_sell_advice(pos, day)}")
            elif bar is not None and pos.target1 and not pos.t1_done and bar.high > pos.target1:
                # Traded THROUGH T1, as the ledger's resting limit needs (``_target_fill``).
                lines.append(f"- 🎯 {symbol}: chạm T1 {_fmt(pos.target1)} - đề xuất bán 1/2, dời stop về hoà vốn"
                             + (f" ({_t2_note(pending, day)})" if pending else ""))
            elif sig is not None and sig.action == "SELL":
                lines.append(f"- 🔻 {symbol}: hệ thống chuyển SELL - {_sell_advice(pos, day, verb='bán', when=' phiên tới')}")
        if real.cash <= 0 and not real.positions:
            lines.append("Chưa có vốn tài khoản thật: gửi /von 500000000 để bot tính khối lượng.")
            return "\n".join(lines)
        draft = Ledger.from_dict(copy.deepcopy(real.to_dict()))
        draft.orders = []
        orders = draft.plan_orders(day, signals, max_positions=params.max_positions)
        buys = [o for o in orders if o.side == "BUY"]
        if buys:
            lines += [f"- MUA {o.symbol} {_fmt(o.qty)} cp, giá ≤ {_fmt(o.limit)}, stop {_fmt(o.stop)}, "
                      f"T1 {_fmt(o.target1)}" + (f", T2 {_fmt(o.target2)}" if o.target2 else "") for o in buys]
        elif len(lines) == 1:
            lines.append("Không có lệnh đề xuất cho phiên tới.")
        return "\n".join(lines)

    # ── the real book, updated by its owner ──
    @staticmethod
    def _fill_day(day: str | None, last: str) -> str:
        """The fill's session: default today (Vietnam time); a weekday, not in the future,
        not before the last fill already recorded (the book is kept in date order)."""
        raw = (day or "").strip() or today_vn()
        try:
            parsed = _date.fromisoformat(raw[:10]) if len(raw) == 10 else None
        except ValueError:
            parsed = None
        if parsed is None:
            raise ValueError(f"Ngày khớp không hợp lệ: {raw!r}. Dùng dạng YYYY-MM-DD.")
        from tradingagents.market import vn_rules

        text = parsed.isoformat()
        if text > today_vn():
            raise ValueError(f"Ngày khớp {text} ở tương lai (hôm nay {today_vn()}).")
        if parsed.weekday() >= 5:
            raise ValueError(f"{text} là cuối tuần, không có phiên giao dịch.")
        if not vn_rules.is_session_day(text):
            raise ValueError(f"{text} là ngày nghỉ lễ, không có phiên giao dịch.")
        if last and text < last:
            raise ValueError(f"Ngày khớp {text} trước lệnh đã ghi gần nhất ({last}); "
                             "báo các lệnh theo đúng thứ tự ngày.")
        return text

    def record_fill(self, side: str, symbol: str, qty: int, price: float, day: str | None = None,
                    book: str = "real") -> str:
        """Book a fill the owner made at the broker (/khop). Raises ValueError with a Vietnamese
        message, leaving the book untouched, when it cannot have happened. (The owner's own
        portfolio is the Tài sản journal: /tsmua, /tsban.)"""
        if book != "real":
            raise ValueError("Danh mục tiền thật của anh ghi bằng /tsmua, /tsban (xem /ts).")
        return self._record_fill(side, symbol, qty, price, day, book)

    def _record_fill(self, side, symbol, qty, price, day, book) -> str:
        from tradingagents.market import vn_rules

        side = {"MUA": "BUY", "BAN": "SELL", "BÁN": "SELL"}.get(str(side).strip().upper(), str(side).strip().upper())
        if side not in ("BUY", "SELL"):
            raise ValueError("Dùng: /khop mua|ban MÃ KHỐI_LƯỢNG GIÁ [YYYY-MM-DD]")
        symbol = str(symbol).strip().upper()
        if isinstance(qty, bool) or not _finite(qty) or float(qty) != int(float(qty)) or int(float(qty)) <= 0:
            raise ValueError("Khối lượng phải là số nguyên dương.")
        qty = int(float(qty))                 # odd lots too (1-99 shares: the odd-lot board)
        if not _finite(price) or float(price) <= 0:
            raise ValueError("Giá khớp không hợp lệ.")
        price = float(price)
        with self._real_lock:
            real = self._real_for_update(book)
            day = self._fill_day(day, str(real.meta.get("last_day") or ""))
            stop = target1 = target2 = None
            if side == "BUY":
                if self.settings.signal_strategy == "factor":
                    pass                        # held until it leaves the basket: no stop, no target
                elif self.settings.signal_strategy == "momentum":
                    from tradingagents.quant.params import active

                    pct = active().mom_stop_pct
                    stop = vn_rules.round_to_tick(price * (1 - pct / 100)) if pct else None
                else:
                    sig = (self.last_signals().get("signals") or {}).get(symbol) or {}
                    stop = float(sig["stop"]) if _finite(sig.get("stop")) and sig["stop"] > 0 else None
                    target1 = float(sig["target1"]) if _finite(sig.get("target1")) else None
                    target2 = float(sig["target2"]) if _finite(sig.get("target2")) else None
                    # Only the levels of a plan to buy or hold: a stop under the fill, targets above
                    # it. A SELL signal's "stop" is its invalidation level ABOVE the price and its
                    # "target" a support below: as a stop and a T1 they would both fire at once.
                    if sig.get("action") not in ("BUY", "HOLD", "WATCH") or stop is None or stop >= price:
                        stop = target1 = target2 = None
                    if target1 is not None and target1 <= price:
                        target1 = target2 = None
                    if target2 is not None and target2 <= (target1 or price):
                        target2 = None
            held = real.positions.get(symbol)
            t1_before = bool(held and held.t1_done)
            fill = real.record_fill(side, symbol, qty, price, day, stop=stop, target1=target1, target2=target2,
                                    reason="báo khớp")
            if side == "BUY":
                self._mark_fill_close(real.positions.get(symbol), day)
            real.meta["last_day"] = max(day, str(real.meta.get("last_day") or ""))
            real.save(self._book_path(book))
        if side == "BUY":
            pos = real.positions[symbol]
            if self.settings.signal_strategy == "factor":
                note = "giữ tới khi rời rổ phòng thủ (hoặc VNINDEX dưới MA)"
            elif self.settings.signal_strategy == "momentum":
                note = f"stop {_fmt(pos.stop)}" if pos.stop else "giữ tới khi rời rổ động lượng"
            else:
                note = (f"stop {_fmt(pos.stop)}, T1 {_fmt(pos.target1)}" if pos.stop
                        else "chưa có stop (không có tín hiệu BUY gần nhất)")
            return (f"✅ Đã ghi MUA {symbol} {_fmt(qty)} @ {_fmt(price)} ngày {day}; {note}. "
                    f"Bán được từ chiều T+2. Tiền còn {_fmt(real.cash)}.")
        text = f"✅ Đã ghi BÁN {symbol} {_fmt(qty)} @ {_fmt(price)} ngày {day}; lãi/lỗ {_bn(fill.pnl)}."
        pos = real.positions.get(symbol)
        if pos is not None and pos.t1_done and not t1_before:
            text += f" Đã chốt T1: stop phần còn lại ({_fmt(pos.qty)} cp) dời về hoà vốn {_fmt(pos.stop)}."
        return text

    @staticmethod
    def _mark_fill_close(pos, day: str) -> None:
        """Mark a position the owner reported to the last CLOSE on or before the fill day, so
        ``mark_to_history`` can see an ex-date between that day and the first evening run
        (a fill price says nothing about the price basis). Best effort: the mark stays
        empty when the price source does not answer."""
        if pos is None:
            return
        try:
            bar = _Market(day).bar(pos.symbol, day)
        except Exception as exc:  # noqa: BLE001 - the mark is a convenience, the fill is booked
            logger.info("No close to mark %s on %s: %s", pos.symbol, day, exc)
            return
        if bar is not None and bar.tradable and bar.date <= day and (not pos.last_date or bar.date >= pos.last_date):
            pos.last_price, pos.last_date = float(bar.close), bar.date

    def record_free_shares(self, symbol: str, qty: int, day: str | None = None, book: str = "real") -> str:
        """Book bonus shares or a stock dividend credited to the real account (/thuong): more
        shares at the same total cost. Raises ValueError (Vietnamese message)."""
        if book != "real":
            raise ValueError("Cổ phiếu thưởng của danh mục tiền thật ghi bằng /tsthuong (xem /ts).")
        return self._record_free_shares(symbol, qty, day, book)

    def _record_free_shares(self, symbol, qty, day, book) -> str:
        from tradingagents.market import vn_rules

        symbol = str(symbol).strip().upper()
        if not (day or "").strip():
            today = today_vn()                  # reported on a weekend or holiday: the last session
            day = today if vn_rules.is_session_day(today) else vn_rules.prev_session(today)
        with self._real_lock:
            real = self._real_for_update(book)
            day = self._fill_day(day, "")       # a valid past session; not ordered with the fills
            pos = real.add_free_shares(symbol, qty, day)
            real.save(self._book_path(book))
        return (f"✅ Đã ghi {_fmt(qty)} cp {symbol} được nhận (thưởng/cổ tức bằng cổ phiếu) ngày {day}: "
                f"đang giữ {_fmt(pos.qty)} cp, giá vốn {_fmt(pos.avg_price)}/cp.")

    # ── the owner's Tài sản portfolio, every session ──
    def taisan_day(self, day: str) -> str | None:
        """The evening upkeep and report of the Tài sản portfolio for session ``day``: the
        closes (and any gap since the last run filled), the ex-date marks of the levels, the
        rights waiting to arrive, the shadow results, the risk figures, then the report.
        None while the day's bar is not out; "" when the portfolio is empty."""
        from tradingagents.market import vn_rules
        from tradingagents.trading.ledger import LedgerLoadError
        from vnbot.taisan import effective

        account = self.taisan
        if not effective(account.entries()):
            return ""
        vni = account.market.daily_closes(vn_rules.BENCHMARK, day)
        if day not in vni:
            return None
        prev = vn_rules.prev_session(day)
        if str(account.state().get("rebuilt_until") or "") < prev:
            account.rebuild(through=prev)
        account.record_day(day)
        view = account.ledger_view()
        if view.positions:
            try:
                self._mark_real(view, day, _Market(day), book="stv")
            except (LedgerLoadError, Exception) as exc:  # noqa: BLE001 - the levels stay as they were
                logger.warning("Tài sản: not marked on %s: %s", day, exc)
        for step in (account.refresh_events, account.settle_shadow, account.refresh_analytics):
            try:
                step(day)
            except Exception as exc:  # noqa: BLE001 - the report goes out without that part
                logger.warning("Tài sản: %s on %s failed: %s", step.__name__, day, exc)
        text = account.report_text(day)
        account.save_report(day, text)
        return text

    def set_capital(self, cash: float, book: str = "real") -> str:
        """Set the real book's cash (/von), as the broker shows it."""
        if book != "real":
            raise ValueError("Tiền mặt của danh mục tiền thật: /tsnap, /tsrut, /tstien (xem /ts).")
        if isinstance(cash, bool) or not _finite(cash) or float(cash) <= 0:
            raise ValueError("Vốn phải là một số lớn hơn 0.")
        if float(cash) > 1e13:
            raise ValueError("Số vốn quá lớn - kiểm tra lại (nhập theo đồng, ví dụ 500000000).")
        cash = float(cash)
        with self._real_lock:
            real = self._real_for_update(book)
            real.cash = cash
            if not real.positions:
                real.start_cash = cash
            real.save(self._book_path(book))
        return f"✅ Tiền mặt {self.REAL_BOOKS[book][1]}: {_fmt(cash)} VND."

    # ── status ──
    def portfolio_text(self) -> str:
        from tradingagents.trading.ledger import LedgerLoadError

        paper = self.paper()
        s = paper.stats()
        lines = [f"🤖 Paper: NAV {_fmt(s['nav'])} ({s['return_pct']:+.2f}%), tiền {_fmt(paper.cash)}, "
                 f"{s['trades']} lệnh mua, thắng {s['win_rate_pct'] if s['win_rate_pct'] is not None else '-'}%"]
        lines += [f"- {p.symbol} {_fmt(p.qty)} @ {_fmt(p.avg_price)} → {_fmt(p.last_price)}, stop {_fmt(p.stop)}"
                  for p in paper.positions.values()] or ["- (không giữ mã nào)"]
        fb = self.factor_book()
        f = fb.stats()
        lines.append(f"🛡️ Phòng thủ: NAV {_fmt(f['nav'])} ({f['return_pct']:+.2f}%), tiền {_fmt(fb.cash)}, "
                     f"rổ {', '.join(fb.meta.get('fb_basket') or []) or 'tiền mặt'}")
        lines += [f"- {p.symbol} {_fmt(p.qty)} @ {_fmt(p.avg_price)} → {_fmt(p.last_price)}"
                  for p in fb.positions.values()] or ["- (không giữ mã nào)"]
        mom = self.momentum_book()
        m = mom.stats()
        lines.append(f"🚀 Động lượng: NAV {_fmt(m['nav'])} ({m['return_pct']:+.2f}%), tiền {_fmt(mom.cash)}, "
                     f"rổ {', '.join(mom.meta.get('mom_basket') or []) or 'tiền mặt'}")
        lines += [f"- {p.symbol} {_fmt(p.qty)} @ {_fmt(p.avg_price)} → {_fmt(p.last_price)}"
                  for p in mom.positions.values()] or ["- (không giữ mã nào)"]
        from vnbot import external

        if external.enabled():
            lines.append("📡 Sổ ảo so sánh Siêu tín hiệu: /stvdanhmuc")
        lines.append("💼 Danh mục tiền thật của anh: /ts (trang web /taisan)")
        if self.settings.trade_mode == "signal":
            try:
                real = self.real()
            except LedgerLoadError as exc:
                lines.append(f"💼 Thật: ⚠️ {exc}")
                return "\n".join(lines)
            lines.append(f"💼 Thật: tiền {_fmt(real.cash)}")
            # Sellable now by the time of day (a lot bought on T sells from 13:00 of T+2), as the
            # intraday alerts count it; settle_to(today) would call it "about" from the morning.
            lines += [f"- {p.symbol} {_fmt(p.qty)} @ {_fmt(p.avg_price)}, stop {_fmt(p.stop)}, T1 {_fmt(p.target1)}"
                      + ("" if _t2_now(p)[0] >= p.qty else f" [{_fmt(_t2_now(p)[0])} cp bán được, {_t2_now(p)[1]}]")
                      for p in real.positions.values()] or ["- (chưa ghi vị thế nào)"]
        return "\n".join(lines)

    def orders_text(self) -> str:
        lines = []
        for title, book in (("Phòng thủ", self.factor_book()), ("Quant Engine", self.paper()),
                            ("Động lượng", self.momentum_book())):
            lines.append(f"{title}:")
            lines += [f"- {o.side} {o.symbol} {_fmt(o.qty)}"
                      + (f" giá ≤ {_fmt(o.limit)}, stop {_fmt(o.stop)}" if o.limit else " giá mở cửa")
                      for o in book.orders] or ["- không có lệnh chờ"]
        return "Lệnh cho phiên tới\n" + "\n".join(lines)

    def calibration_text(self) -> str:
        from tradingagents.quant import params as qp
        from tradingagents.trading import calibrate as cal

        current = qp.active()
        diff = qp.changed(current)
        lines = ["⚙️ Ngưỡng đang dùng: " + (", ".join(f"{k} {a}→{b}" for k, (a, b) in diff.items())
                                         if diff else "mặc định (chưa đổi lần nào)")]
        reports = cal.last_reports(10)
        for row in reversed(reports):
            acc = row.get("accepted")
            who = cal.STRATEGIES.get(row.get("strategy", "engine"), "")
            lines.append(f"- {row['as_of']} {who}: " + (f"ĐỔI {acc['name']}" if acc else f"giữ nguyên. {row.get('note', '')}"))
        if not reports:
            lines.append("Chưa chạy hiệu chỉnh lần nào.")
        return "\n".join(lines)

    # ── nightly ──
    def calibrate(self, day: str) -> str:
        """Calibrate on the sessions up to ``day``, which must be a final one (never past
        the last session the books processed)."""
        from tradingagents.dataflows import money_flows as mf
        from tradingagents.trading import calibrate as cal, simulator as sim

        day = min(day, final_day())
        symbols = list(dict.fromkeys(self.universe() + mf.index_members("VN30")))
        data = sim.build_dataset(symbols, cal.TRAIN_SESSIONS + cal.HOLDOUT_SESSIONS, day)
        out = []
        for strategy in ("engine", "momentum"):
            if strategy == "momentum":
                members = set(mf.index_members("VN30"))
                data.symbols = [s for s in data.symbols if s in members]
                data.bars = {s: b for s, b in data.bars.items() if s in members}
            out.append("⚙️ " + cal.calibrate(symbols, day, apply=True, data=data, strategy=strategy).summary())
        # The defensive book is not calibrated: its parameters stay the ones the
        # evaluation froze and tested (a recalibrating variant was never evaluated, and
        # a dataset of today's names cannot rebuild its point-in-time universe).
        return "\n\n".join(out)
