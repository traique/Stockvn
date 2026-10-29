"""A stock's valuation against its own history, point-in-time (informational only).

The multiple of every session since 2018 - the first year VCI has quarterly
statements for - and where the run date's multiple sits among them. Market
value is the session's UNADJUSTED close × the shares outstanding that same
session (VCI price-history, ``flows.fetch_stock_flows``): both change on a
bonus-share ex-date, so the multiple stays continuous. The adjusted bars of
``vci.fetch_ohlcv`` are not usable here: an old adjusted price over the book
of its day gives a wrong multiple before every bonus issue. VCI posts a
session's row hours after the close (sometimes the next morning), so the
sessions it has not posted yet come from the engine's bars (``_top_up``).

- P/B: market value / equity of the parent's shareholders (``bsa78`` total
  equity minus ``bsa210`` minority interest) of the newest quarter published
  (``publicDate``) by that session.
- P/E: market value / parent shareholders' profit (``isa22``) of the four
  newest quarters published by that session, consecutive. VCI's quarterly EPS
  (``isa23``) is not used: it is 0 for banks and for some quarters of others.

The valuation card of a bank (``card``): Bear / eab / Base / Bull from the book
per share of the run date and a justified P/B, the margin of safety and the zone
(Rẻ / Hợp lý / Đắt), every line labelled [FACT dd/mm] or [THỐNG KÊ dd/mm]. The
assumptions (Ke, λ, g, margin) are set by hand in ``valuation_config``.

Nothing in the Quant Engine (engine, policy, simulator, calibration) reads
this module: it only writes text, for the analysts, after the engine's report
(the ``quant_engine`` graph node) and in /nhanh.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from tradingagents.dataflows import money_flows, ohlcv, valuation_config
from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.errors import VendorError
from tradingagents.dataflows.files import locked, replace_file
from tradingagents.dataflows.symbols import normalize_symbol, safe_ticker_component
from tradingagents.dataflows.vendors import flows, vci
from tradingagents.dataflows.vn_fundamentals import (
    _as_of_day, _num, _parse_day, _period_end, published_annual_ratio_rows, published_on,
)
from tradingagents.market import vn_rules
from tradingagents.quant import fundamental_profiles, report_format as rfmt, sector

logger = logging.getLogger(__name__)

HISTORY_START = "2018-01-01"   # VCI's quarterly statements start with Q1/2018
MIN_SESSIONS = 120             # fewer sessions with a multiple: no percentile
METRIC_LABELS = {"pb": "P/B", "pe": "P/E"}
_PRICE_COLUMNS = ["Date", "Close", "TotalShares"]


# ─── Unadjusted prices, cached ────────────────────────────────────────────────


def _dir() -> str:
    path = os.path.join(get_config()["data_cache_dir"], "valuation")
    os.makedirs(path, exist_ok=True)
    return path


def _load(path: str) -> tuple[str | None, float, pd.DataFrame]:
    """(the day it was last asked, when exactly, the cached rows) of one stock's file."""
    try:
        with open(path, encoding="utf-8") as handle:
            saved = json.load(handle)
        frame = pd.DataFrame({c: saved[c] for c in _PRICE_COLUMNS})
        frame["Date"] = pd.to_datetime(frame["Date"])
        return saved.get("fetched"), float(saved.get("asked_at") or 0), frame
    except (OSError, ValueError, KeyError, TypeError):
        return None, 0.0, pd.DataFrame({"Date": pd.to_datetime([]), "Close": [], "TotalShares": []})


def _due_session() -> pd.Timestamp:
    """The newest session VCI may have posted by now: today's once the day's flows
    are out (``money_flows.published_through``, 15:30), else the session before."""
    day = money_flows.published_through()
    while not vn_rules.is_session_day(day.strftime("%Y-%m-%d")):
        day -= pd.Timedelta(days=1)
    return day


def price_history(symbol: str) -> pd.DataFrame:
    """Unadjusted close and shares outstanding of every session since ``HISTORY_START``.

    Kept in ``<data_cache_dir>/valuation``. The day's first call asks VCI from
    the last cached session on (that session again, in case it was saved before
    its close was final); later calls that day ask again only while the file
    lacks the last session due (``_due_session``), at most once an hour
    (``money_flows.RECHECK_SECONDS``). A failed fetch leaves the cached rows
    (possibly none) and trips the shared VCI breaker (``money_flows._guarded``):
    the calls of the next minutes get the cached rows at once.
    """
    code = safe_ticker_component(symbol.upper())
    path = os.path.join(_dir(), f"{code}-prices.json")
    today = vn_rules.now_vn().date().isoformat()
    with locked(path):
        fetched, asked_at, cached = _load(path)
        if fetched == today and (
            (not cached.empty and cached["Date"].iloc[-1] >= _due_session())
            or time.time() - asked_at < money_flows.RECHECK_SECONDS
        ):
            return cached
        start = cached["Date"].iloc[-1].strftime("%Y-%m-%d") if not cached.empty else HISTORY_START
        try:
            fresh = money_flows._guarded("VCI", lambda: flows.fetch_stock_flows(code, start, today))[_PRICE_COLUMNS]
        except VendorError as exc:
            logger.warning("Price history of %s for the valuation unavailable: %s", code, exc)
            return cached
        frames = [f for f in (cached, fresh) if not f.empty]
        frame = (pd.concat(frames).drop_duplicates("Date", keep="last").sort_values("Date").reset_index(drop=True)
                 if frames else cached)
        saved = {"fetched": today, "asked_at": time.time(), "Date": frame["Date"].dt.strftime("%Y-%m-%d").tolist(),
                 "Close": frame["Close"].astype(float).tolist(), "TotalShares": frame["TotalShares"].astype(float).tolist()}

        def write(temp: str) -> None:
            with open(temp, "w", encoding="utf-8") as handle:
                json.dump(saved, handle)

        replace_file(path, write)
        return frame


# ─── The multiple of each session ────────────────────────────────────────────


def parent_equity(row: dict) -> float | None:
    """Equity of the parent's shareholders: total equity (bsa78) minus minority interest (bsa210)."""
    total = _num(row.get("bsa78"))
    return None if total is None else total - (_num(row.get("bsa210")) or 0.0)


def _index(row: dict) -> int:
    return int(row.get("yearReport") or 0) * 4 + int(row.get("lengthReport") or 0)


def _base_steps(symbol: str, metric: str) -> pd.DataFrame:
    """The denominator after each publication day: Published, Base, Period, PeriodPublished."""
    section = "BALANCE_SHEET" if metric == "pb" else "INCOME_STATEMENT"
    rows = [r for r in vci.fetch_statement(symbol, section)["quarters"] if 1 <= int(r.get("lengthReport") or 0) <= 4]
    steps = []
    for day in sorted({published_on(r) for r in rows}):
        visible = sorted((r for r in rows if published_on(r) <= day), key=_index, reverse=True)
        newest = visible[0]
        if metric == "pb":
            base = parent_equity(newest)
        else:
            last4 = visible[:4]
            profits = [_num(r.get("isa22")) for r in last4]
            consecutive = len(last4) == 4 and _index(last4[0]) - _index(last4[3]) == 3
            base = sum(profits) if consecutive and None not in profits else None
        steps.append({"Published": pd.Timestamp(day), "Base": np.nan if base is None else base,
                      "Period": f"Q{newest.get('lengthReport')}/{newest.get('yearReport')}",
                      "PeriodPublished": published_on(newest)})
    return pd.DataFrame(steps, columns=["Published", "Base", "Period", "PeriodPublished"])


def _top_up(symbol: str, prices: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """``prices`` with the sessions up to ``as_of`` VCI has not posted yet, from the engine's
    bars (``ohlcv.load_ohlcv``: closed sessions only): after the last ex-date the adjusted
    close is the unadjusted one, and the shares are carried forward. Left as they are when
    the bars' close of VCI's last session differs from VCI's: a dividend or bonus issue
    went ex since, so the bars are on another basis and the shares may have changed."""
    if prices.empty or prices["Date"].iloc[-1] >= as_of:
        return prices
    try:
        bars = ohlcv.load_ohlcv(symbol, as_of.strftime("%Y-%m-%d"), fill_gaps=False)
    except VendorError as exc:
        logger.info("Bars of %s for the valuation unavailable: %s", symbol, exc)
        return prices
    last = prices.iloc[-1]
    same = bars.loc[bars["Date"] == last["Date"], "Close"]
    newer = bars[bars["Date"] > last["Date"]]
    if newer.empty or same.empty or round(float(same.iloc[-1])) != round(float(last["Close"])):
        return prices
    added = pd.DataFrame({"Date": newer["Date"], "Close": newer["Close"].astype(float), "TotalShares": last["TotalShares"]})
    return pd.concat([prices, added], ignore_index=True)


def multiple_history(symbol: str, metric: str, as_of_date: str | None) -> pd.DataFrame:
    """One row per session up to the run date: Date, Close, TotalShares, Base, Period,
    PeriodPublished, Multiple (NaN before the first statement or when the base is not positive)."""
    as_of = pd.Timestamp(_as_of_day(as_of_date))
    prices = price_history(symbol)
    prices = _top_up(symbol, prices[prices["Date"] <= as_of].reset_index(drop=True), as_of)
    steps = _base_steps(symbol, metric)
    if steps.empty:
        return prices.assign(Base=np.nan, Period=None, PeriodPublished=None, Multiple=np.nan)
    frame = prices.copy()
    at = np.searchsorted(steps["Published"].to_numpy(dtype="datetime64[ns]"),
                         frame["Date"].to_numpy(dtype="datetime64[ns]"), side="right") - 1
    picked = steps.iloc[np.maximum(at, 0)].reset_index(drop=True)
    for column in ("Base", "Period", "PeriodPublished"):
        frame[column] = picked[column].where(at >= 0)
    value = frame["Close"].astype(float) * frame["TotalShares"].astype(float) / frame["Base"]
    frame["Multiple"] = value.where((frame["Base"] > 0) & (value > 0))
    return frame


@dataclass(frozen=True)
class MultipleRank:
    metric: str
    day: date              # the session of the multiple (the last one by the run date)
    close: float
    value: float
    period: str            # the statement quarter it uses, e.g. "Q2/2026"
    published: date        # when that quarter was published
    higher_pct: float      # share of sessions with a higher multiple, %
    sessions: int          # sessions with a multiple, the last one included
    since: date
    low: float
    high: float


def rank_of(history: pd.DataFrame, metric: str) -> MultipleRank | None:
    """Where the last session's multiple sits among all sessions with one (None: too few, or none today)."""
    if history.empty or pd.isna(history["Multiple"].iloc[-1]):
        return None
    valid = history.dropna(subset=["Multiple"])
    if len(valid) < MIN_SESSIONS:
        return None
    today = history.iloc[-1]
    multiples = valid["Multiple"]
    return MultipleRank(
        metric=metric, day=today["Date"].date(), close=float(today["Close"]), value=float(today["Multiple"]),
        period=str(today["Period"]), published=today["PeriodPublished"],
        higher_pct=float((multiples > today["Multiple"]).sum() / len(valid) * 100), sessions=len(valid),
        since=valid["Date"].iloc[0].date(), low=float(multiples.min()), high=float(multiples.max()),
    )


def multiple_line(symbol: str, metric: str, as_of_date: str | None) -> str:
    """One line for the fundamentals report: the run date's multiple, from that session's price,
    and the share of sessions since 2018 that were dearer."""
    label = METRIC_LABELS[metric]
    history = multiple_history(symbol, metric, as_of_date)
    base = "vốn chủ sở hữu công ty mẹ" if metric == "pb" else "LNST công ty mẹ 4 quý"
    if not history.empty and history["Base"].iloc[-1] <= 0:
        return f"- {label} hôm nay: không tính được ({base} tới {history['Period'].iloc[-1]} ≤ 0)"
    rank = rank_of(history, metric)
    if rank is None:
        return f"- {label} hôm nay so với lịch sử của chính mã: chưa đủ dữ liệu"
    # The run date's session not out yet: before the close, a holiday, or VCI and the bars both behind.
    when = "hôm nay" if rank.day == _as_of_day(as_of_date) else "phiên gần nhất có dữ liệu"
    return (
        f"- {label} {when} {rfmt.fmt_number(rank.value)} (giá đóng cửa {rank.day:%d/%m/%Y}: {rfmt.fmt_price(rank.close)}; "
        f"{base} tới {rank.period}, công bố {rank.published:%d/%m/%Y}): rẻ hơn {rfmt.fmt_number(rank.higher_pct, 0)}% "
        f"số phiên từ {rank.since:%m/%Y} ({rfmt.fmt_price(rank.sessions)} phiên; thấp nhất {rfmt.fmt_number(rank.low)}, "
        f"cao nhất {rfmt.fmt_number(rank.high)})"
    )


# ─── The valuation card (banks) ──────────────────────────────────────────────

ROE_YEARS = 5                 # sustainable ROE: median of the newest 5 annual ROEs published
MIN_ROE_YEARS = 3             # fewer annual ROEs published (VCI's start in 2018): no card
EVENT_LOOKAHEAD_DAYS = 45     # how far ahead a scheduled ex-date ends the card's validity
CARD_HEADING = "--- Định giá (tham khảo"
# A paid issue (rights to buy): VCI counts its shares from the ex-date (MBB 11/08/2026: +10% with a 15%
# stock dividend, +25% in all), while the cash comes into a later statement.
_RIGHTS_ISSUE = re.compile(r"quyền mua|rights issue", re.IGNORECASE)
_CARD_CACHE_MAX = 256
_cards: dict[tuple, tuple[float, "ValuationCard | None"]] = {}
_cards_lock = threading.Lock()


def justified_pb(roe: float, ke: float, lam: float, g: float = 0.0) -> float:
    """P/B = 1 + (ROE − Ke) / (1 + Ke − ω(1 + g)), ω = 1 − λ: next year's excess return
    (ROE − Ke) × book, fading by ω a year while the book grows by g, discounted at Ke. With g = 0
    it is the formula of the CTG profile PDF (book 25.166, ROE 17,46%, Ke 7,94%, λ 0,4 → 30.164)."""
    return 1 + (roe - ke) / (1 + ke - (1 - lam) * (1 + g))


def is_bank(symbol: str) -> bool:
    """The card covers banks only: by the sector map, else by VCI's company type (NH)."""
    sectors = sector.get_symbol_sectors(symbol)
    if sectors:
        return "banking" in sectors
    try:
        com_type = vci.fetch_details(symbol).get("comTypeCode")
    except Exception as exc:  # noqa: BLE001 - unknown type: no card
        logger.info("Company type of %s unavailable: %s", symbol, exc)
        return False
    return fundamental_profiles.get_profile(symbol, com_type).key == "banking"


@dataclass(frozen=True)
class ValuationCard:
    symbol: str
    as_of: date
    day: date                     # the session of the price (the last one by the run date)
    close: float
    period: str                   # the book's quarter, e.g. "Q2/2026"
    published: date              # when that quarter was published
    period_end: date
    equity: float                 # parent shareholders' equity of that quarter (bsa78 − bsa210)
    shares: float                 # shares outstanding at ``day``
    dividends: tuple[tuple[date, float], ...]   # cash dividends gone ex after the period end: (ex-date, đ/cp)
    rights: tuple[tuple[date, float], ...]      # rights issues gone ex after it: (ex-date, ratio), cash not in the book
    paid: float                   # what they paid out in all, đồng
    book: float                   # book per share at ``day``
    roes: tuple[tuple[int, float], ...]         # every annual ROE published, oldest first
    annual_published: date        # when the newest annual report was published
    roe_base: float
    roe_bear: float
    roe_bull: float
    assumptions: valuation_config.Assumptions
    bear: float
    eab: float
    base: float
    bull: float
    mos: float                    # (Base − price) / Base
    zone: str                     # Rẻ (price ≤ Base) / Hợp lý / Đắt (price ≥ Bull)
    sensitivity: tuple[tuple[float, float], ...]  # (Ke, Base)
    next_ex: date | None          # the next ex-date already announced, within EVENT_LOOKAHEAD_DAYS


def _shares_on(history: pd.DataFrame, day: date) -> float:
    rows = history[history["Date"] <= pd.Timestamp(day)]
    return float((rows if not rows.empty else history)["TotalShares"].iloc[-1])


def build_card(symbol: str, as_of_date: str | None) -> ValuationCard | None:
    """The card of the run date, from data published by then (None: not enough of it).

    Book per share = (the newest published quarter's parent equity − the cash dividends gone ex
    after that quarter's end, by the price's session) / the shares outstanding of that session.
    The shares change on a bonus issue's ex-date, so a book published before it is restated
    (CTG: Q3/2025 over 7,77 billion shares from 17/12/2025, the 44,6% bonus); each dividend is
    paid on the shares of its own ex-date.
    """
    code = normalize_symbol(symbol)
    as_of = _as_of_day(as_of_date)
    setup = valuation_config.assumptions("banking")
    rows = [(int(r["yearReport"]), v, public) for r, public in published_annual_ratio_rows(code, as_of_date)
            if (v := _num(r.get("roe"))) is not None]
    if len(rows) < MIN_ROE_YEARS:
        return None
    annual = sorted((year, v) for year, v, _ in rows)
    annual_published = max(public for _, _, public in rows)
    history = multiple_history(code, "pb", as_of_date)
    if history.empty or pd.isna(history["Base"].iloc[-1]) or history["Base"].iloc[-1] <= 0:
        return None
    last = history.iloc[-1]
    day = last["Date"].date()
    quarter, year = (int(x) for x in str(last["Period"])[1:].split("/"))
    period_end = _period_end(year, quarter)
    events = vci.fetch_events(code, (period_end + timedelta(days=1)).isoformat(),
                              (as_of + timedelta(days=EVENT_LOOKAHEAD_DAYS)).isoformat(), codes="DIV,ISS")
    dividends, rights, upcoming = [], [], []
    for event in events:
        ex = _parse_day(event.get("exrightDate"))
        public = _parse_day(event.get("publicDate")) or ex
        if ex is None or public > as_of:
            continue
        if ex > as_of:
            upcoming.append(ex)
        elif not period_end < ex <= day:
            continue
        elif event.get("eventCode") == "DIV" and (_num(event.get("valuePerShare")) or 0) > 0:
            dividends.append((ex, _num(event.get("valuePerShare"))))
        elif event.get("eventCode") == "ISS" and _RIGHTS_ISSUE.search(
                f"{event.get('eventTitleVi') or ''} {event.get('eventTitleEn') or ''}"):
            rights.append((ex, _num(event.get("exerciseRatio")) or 0.0))
    dividends.sort()
    rights.sort()
    paid = sum(value * _shares_on(history, ex) for ex, value in dividends)
    shares = float(last["TotalShares"])
    book = (float(last["Base"]) - paid) / shares
    if book <= 0:
        return None

    values = [v for _, v in annual]
    roe_base = float(np.median(values[-ROE_YEARS:]))
    p20, p80 = (float(v) for v in np.percentile(values, [20, 80]))
    roe_bear, roe_bull = min(p20, roe_base), max(p80, roe_base)   # Bear ≤ Base ≤ Bull, whatever the spread

    def value(roe: float, ke: float = setup.ke) -> float:
        return book * justified_pb(roe, ke, setup.lam, setup.g)

    base, close = value(roe_base), float(last["Close"])
    bull = value(roe_bull)
    zone = "Rẻ" if close <= base else "Đắt" if close >= bull else "Hợp lý"
    return ValuationCard(
        symbol=code, as_of=as_of, day=day, close=close, period=str(last["Period"]),
        published=last["PeriodPublished"], period_end=period_end, equity=float(last["Base"]), shares=shares,
        dividends=tuple(dividends), rights=tuple(rights), paid=paid, book=book, roes=tuple(annual), annual_published=annual_published,
        roe_base=roe_base, roe_bear=roe_bear, roe_bull=roe_bull, assumptions=setup,
        bear=value(roe_bear), eab=base * (1 - setup.margin), base=base, bull=bull, mos=(base - close) / base,
        zone=zone, sensitivity=tuple((ke, value(roe_base, ke)) for ke in valuation_config.SENSITIVITY_KE),
        next_ex=min(upcoming) if upcoming else None,
    )


def card(symbol: str, as_of_date: str | None) -> ValuationCard | None:
    """``build_card``, kept per (stock, run date, assumptions): the engine node, the fundamentals
    tool and /nhanh of one run read the same numbers. Today's card is rebuilt after an hour
    (``money_flows.RECHECK_SECONDS``), once the session's close may be out."""
    key = (normalize_symbol(symbol), _as_of_day(as_of_date).isoformat(), valuation_config.assumptions("banking"))
    today = vn_rules.now_vn().date().isoformat()
    with _cards_lock:
        hit = _cards.get(key)
    if hit and (key[1] < today or time.time() - hit[0] < money_flows.RECHECK_SECONDS):
        return hit[1]
    built = build_card(symbol, as_of_date)
    with _cards_lock:
        if len(_cards) >= _CARD_CACHE_MAX:
            _cards.pop(min(_cards, key=lambda k: _cards[k][0]), None)
        _cards[key] = (time.time(), built)
    return built


def _label(kind: str, day: date) -> str:
    return f"[{kind} {day:%d/%m}]"


def _pct(value: float) -> str:
    return rfmt.fmt_pct(value * 100)


def _rate(value: float) -> str:
    """An assumption in %, without trailing zeros: 12%, 7,94%, 0%."""
    return rfmt.fmt_number(value * 100).rstrip("0").rstrip(",") + "%"


def _expiry(c: ValuationCard) -> str:
    when = f"{c.next_ex:%d/%m}" if c.next_ex else "chưa có lịch"
    return f"Hạn dùng: tới khi có BCTC quý mới hoặc tới ngày GDKHQ kế tiếp ({when})"


def render_card(c: ValuationCard) -> str:
    """The card for the engine's report: every price with its distance from the session's close."""
    a = c.assumptions
    model = _label("THỐNG KÊ", c.day)
    years = [y for y, _ in c.roes]
    recent = c.roes[-ROE_YEARS:]
    span = (f"{len(recent)} năm {recent[0][0]}-{recent[-1][0]}" if len(recent) >= ROE_YEARS
            else f"chỉ có {len(recent)} năm {recent[0][0]}-{recent[-1][0]}, chưa đủ {ROE_YEARS}")
    lines = [
        f"{CARD_HEADING}, không vào quyết định; không phải khuyến nghị) - ngân hàng ---",
        f"Giá phiên {c.day:%d/%m} · BCTC {c.period} công bố {c.published:%d/%m} · {_expiry(c)}",
        f"{_label('FACT', c.published)} Vốn chủ sở hữu công ty mẹ {c.period} (tới {c.period_end:%d/%m/%Y}): "
        f"{rfmt.fmt_number(c.equity / 1e9, 1)} tỷ",
    ]
    if c.dividends:
        paid = " + ".join(f"{rfmt.fmt_price(v)} đ/cp (GDKHQ {ex:%d/%m/%Y})" for ex, v in c.dividends)
        lines.append(f"{_label('FACT', c.dividends[-1][0])} Cổ tức tiền GDKHQ sau ngày khoá sổ quý, đã trừ khỏi "
                     f"sổ sách: {paid}, tổng {rfmt.fmt_number(c.paid / 1e9, 1)} tỷ")
    lines += [
        f"{_label('FACT', c.day)} Sổ sách/cp {rfmt.fmt_price(c.book)} = (vốn chủ"
        + (" − cổ tức tiền đã trả" if c.dividends else "")
        + f") / {rfmt.fmt_price(c.shares)} cp lưu hành phiên {c.day:%d/%m/%Y}",
    ]
    if c.rights:
        issues = ", ".join(f"{rfmt.fmt_pct(r * 100, 1)} (GDKHQ {ex:%d/%m/%Y})" for ex, r in c.rights)
        lines.append(f"{_label('FACT', c.rights[-1][0])} Phát hành quyền mua {issues}: số cổ phiếu đã tính phần mới, "
                     f"tiền thu về chưa có trong BCTC {c.period} → sổ sách/cp và các mốc dưới đây thấp hơn thực tế")
    lines += [
        f"{_label('THỐNG KÊ', c.annual_published)} ROE bền vững {_pct(c.roe_base)} = trung vị ROE năm ({span}: "
        + " · ".join(_pct(v) for _, v in recent) + f"); ROE p20 {_pct(c.roe_bear)}, p80 {_pct(c.roe_bull)} "
        f"của {len(years)} năm {years[0]}-{years[-1]}",
        f"{model} Base (giá trị hợp lý) {rfmt.format_level(c.close, c.base)} = {rfmt.fmt_number(c.base / c.book)} "
        f"lần sổ sách; Ke {_rate(a.ke)} (giả định đặt tay), λ {rfmt.fmt_number(a.lam, 1)}, g {_rate(a.g)}: "
        "P/B = 1 + (ROE − Ke)/(1 + Ke − (1 − λ)(1 + g))",
        f"{model} Bear {rfmt.format_level(c.close, c.bear)} theo ROE p20 · Bull {rfmt.format_level(c.close, c.bull)} "
        "theo ROE p80",
        f"{model} eab {rfmt.format_level(c.close, c.eab)} = Base × (1 − {rfmt.fmt_pct(a.margin * 100, 1)})",
        f"{model} Giá {rfmt.fmt_price(c.close)} (phiên {c.day:%d/%m/%Y}): MOS {rfmt.fmt_signed_pct(c.mos * 100)} "
        f"= (Base − giá)/Base → vùng {c.zone} (Rẻ: giá ≤ Base; Đắt: giá ≥ Bull)",
        f"{model} Độ nhạy Base theo Ke: "
        + " · ".join(f"{_rate(ke)} {rfmt.fmt_price(v)}" for ke, v in c.sensitivity),
    ]
    return "\n".join(lines)


def card_text(symbol: str, as_of_date: str | None) -> str:
    """The card of a bank for the engine's report; "" for any other stock or without the data."""
    if not is_bank(normalize_symbol(symbol)):
        return ""
    c = card(symbol, as_of_date)
    return render_card(c) if c else ""


def fundamentals_line(c: ValuationCard | None) -> str:
    """The card in one line of the fundamentals report (banks)."""
    if c is None:
        return "- Định giá tham khảo (thẻ của hệ thống): chưa đủ dữ liệu"
    return (
        f"- Định giá tham khảo (thẻ của hệ thống, không vào quyết định) {_label('THỐNG KÊ', c.day)}: "
        f"Bear {rfmt.fmt_price(c.bear)} · eab {rfmt.fmt_price(c.eab)} · Base {rfmt.fmt_price(c.base)} · "
        f"Bull {rfmt.fmt_price(c.bull)} VND; giá {rfmt.fmt_price(c.close)} → vùng {c.zone} "
        f"(MOS {rfmt.fmt_signed_pct(c.mos * 100)}); sổ sách/cp {rfmt.fmt_price(c.book)}, ROE bền vững "
        f"{_pct(c.roe_base)}, Ke {_rate(c.assumptions.ke)} đặt tay. Giá trị hợp lý chỉ trích đúng các số này, giữ nhãn"
    )


def quick_line(symbol: str, as_of_date: str | None) -> str:
    """One line for /nhanh (banks): the card's levels and zone; "" otherwise."""
    if not is_bank(normalize_symbol(symbol)):
        return ""
    c = card(symbol, as_of_date)
    if c is None:
        return ""
    return (f"📐 Định giá tham khảo (không vào quyết định): Bear {rfmt.fmt_price(c.bear)} · Base {rfmt.fmt_price(c.base)}"
            f" · Bull {rfmt.fmt_price(c.bull)} → {c.zone}, MOS {rfmt.fmt_signed_pct(c.mos * 100, 1)} "
            f"(Ke {_rate(c.assumptions.ke)}, BCTC {c.period})")


def card_prices(report: str) -> set[int]:
    """The prices printed on the card inside an engine report (empty: no card)."""
    start = (report or "").find(CARD_HEADING)
    if start < 0:
        return set()
    return rfmt.price_numbers(report[start:].split("\n\n", 1)[0])
