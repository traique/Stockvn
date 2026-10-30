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

The valuation card (``card``, ``assess``): Bear / eab / Base / Bull from the book
per share of the run date and a justified P/B, the margin of safety and the zone
(Rẻ / Hợp lý / Đắt), every line labelled [FACT dd/mm] or [THỐNG KÊ dd/mm] (with
the year when it is not the run date's). Every stock gets the same model with the
assumptions (Ke, λ, g, margin) of its profile, set by hand in ``valuation_config``,
and a reliability saying how far its book anchors the value; ``assess`` says why
there is no card (equity ≤ 0, fewer than three annual ROEs, a loss). The engine's
report, the fundamentals tool and /nhanh carry the card of a bank only (there the
other stocks' Bull/Bear are the two sides of the debate); /dinhgia shows any stock's.

Nothing in the Quant Engine (engine, policy, simulator, calibration) reads
this module: it only writes text, for the analysts, after the engine's report
(the ``quant_engine`` graph node), in /nhanh and /dinhgia.
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
from tradingagents.dataflows.errors import NoMarketDataError, VendorError
from tradingagents.dataflows.files import locked, replace_file
from tradingagents.dataflows.symbols import listing_entry, normalize_symbol, safe_ticker_component
from tradingagents.dataflows.vendors import flows, vci
from tradingagents.dataflows.vn_fundamentals import (
    _as_of_day, _num, _parse_day, _period_end, published_annual_ratio_rows, published_on,
)
from tradingagents.market import vn_rules
from tradingagents.quant import report_format as rfmt, sector

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
    (``money_flows.RECHECK_SECONDS``). A failed fetch leaves the cached rows and
    trips the breaker of the informational VCI calls (``money_flows.VCI_INFO``,
    not the engine's): the calls of the next minutes get the cached rows at once.
    With none cached it raises: an outage is not a stock without prices (the
    card would say "no price" for a day, the evening job would not retry it).
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
            fresh = money_flows._guarded(money_flows.VCI_INFO,
                                         lambda: flows.fetch_stock_flows(code, start, today))[_PRICE_COLUMNS]
        except VendorError as exc:
            if cached.empty:
                raise
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
    went ex since, so the bars are on another basis and the shares may have changed.
    Cut before an issue going ex among the added sessions (VCI ISS: bonus, stock dividend,
    rights, ESOP...): the shares change that day, while on its evening the source may not
    have re-adjusted the bars yet (CTG 17/12/2025, 44,6%: 5,37 → 7,77 tỷ cp, 51.000 → 34.700).
    A cash dividend changes no shares. None is added when the events are unavailable."""
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
    first = newer["Date"].iloc[0]
    try:   # VCI returns an event whose publicDate or ex-date is in the window
        events = money_flows._guarded(money_flows.VCI_INFO, lambda: vci.fetch_events(
            normalize_symbol(symbol), first.strftime("%Y-%m-%d"), newer["Date"].iloc[-1].strftime("%Y-%m-%d"),
            codes="ISS"))
    except VendorError as exc:
        logger.info("Issues of %s for the valuation unavailable: %s", symbol, exc)
        return prices
    issued = [ex for e in events if (ex := _parse_day(e.get("exrightDate"))) and ex >= first.date()]
    if issued:
        newer = newer[newer["Date"] < pd.Timestamp(min(issued))]
        if newer.empty:
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


def cheaper_pct(rank: MultipleRank) -> int:
    """The share of sessions with a higher multiple, rounded, but never "100%" while a session was
    cheaper (FPT 29/10/2026: 99,5%, 8 sessions lower), nor "0%" while one was dearer."""
    shown = round(rank.higher_pct)
    if rank.low < rank.value:
        shown = min(shown, 99)
    if rank.higher_pct > 0:
        shown = max(shown, 1)
    return shown


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
        f"{base} tới {rank.period}, công bố {rank.published:%d/%m/%Y}): rẻ hơn {cheaper_pct(rank)}% "
        f"số phiên từ {rank.since:%m/%Y} ({rfmt.fmt_price(rank.sessions)} phiên; thấp nhất {rfmt.fmt_number(rank.low)}, "
        f"cao nhất {rfmt.fmt_number(rank.high)})"
    )


# ─── The valuation card ──────────────────────────────────────────────────────

ROE_YEARS = 5                 # sustainable ROE: median of the newest 5 annual ROEs published
MIN_ROE_YEARS = 3             # fewer annual ROEs published (VCI's start in 2018): no card
EVENT_LOOKAHEAD_DAYS = 45     # how far ahead a scheduled ex-date ends the card's validity
CARD_HEADING = "--- Định giá (tham khảo"
BANK_ICB = "8300"             # VCI listing's icbCode2 of the banks (28 codes, checked live 29/10/2026)
# An issue that brings in capital - rights, a private placement, ESOP, a bond conversion: VCI counts its
# shares from the ex-date (MBB rights 11/08/2026; BID private placement 3,8% 24/03/2026), while the capital
# comes into a later statement. Every bank ISS title on VCI (checked live 29/10/2026) is one of these, a
# bonus issue ("Cổ phiếu thưởng") or a stock dividend ("Trả Cổ tức bằng Cổ phiếu"), which bring none.
_PAID_ISSUE_KIND = {"quyền mua": "quyền mua", "riêng lẻ": "riêng lẻ", "cbcnv": "ESOP",
                    "chuyển đổi": "chuyển đổi trái phiếu"}
_PAID_ISSUE = re.compile("|".join(_PAID_ISSUE_KIND), re.IGNORECASE)
GROWTH_YEARS = 3              # the book growth of the reliability: the parent equity of the same quarter 3 years before
_CARD_CACHE_MAX = 256
_cards: dict[tuple, tuple[float, str, "ValuationCard | NoCard | None"]] = {}   # key -> (built at, built on day, card)
_cards_lock = threading.Lock()


def justified_pb(roe: float, ke: float, lam: float, g: float = 0.0) -> float:
    """P/B = 1 + (ROE − Ke) / (1 + Ke − ω(1 + g)), ω = 1 − λ: next year's excess return
    (ROE − Ke) × book, fading by ω a year while the book grows by g, discounted at Ke. With g = 0
    it is the formula of the CTG profile PDF (book 25.166, ROE 17,46%, Ke 7,94%, λ 0,4 → 30.164)."""
    return 1 + (roe - ke) / (1 + ke - (1 - lam) * (1 + g))


def is_bank(symbol: str) -> bool:
    """A bank, whose card the engine's report carries: by the sector map, else by the VCI listing's ICB
    code (8300). No call of its own: the listing is kept a day and on disk, and the engine has read it
    for the exchange."""
    sectors = sector.get_symbol_sectors(symbol)
    if sectors:
        return "banking" in sectors
    entry = listing_entry(symbol)
    return bool(entry) and str(entry.get("icbCode2")) == BANK_ICB


def card_profile(symbol: str) -> str:
    """The card's profile (``valuation_config.PROFILES``): by the sector map, else by the VCI listing's
    ICB code; "default" for any other stock, and while the listing cannot be read."""
    sectors = sector.get_symbol_sectors(symbol)
    if sectors:
        return next((k for k in valuation_config.PROFILE_ORDER if k in sectors), "default")
    entry = listing_entry(symbol) or {}
    return valuation_config.ICB_PROFILE.get(str(entry.get("icbCode2")), "default")


def _is_tech(symbol: str) -> bool:
    sectors = sector.get_symbol_sectors(symbol)
    if sectors:
        return "technology" in sectors
    return str((listing_entry(symbol) or {}).get("icbCode2")) == valuation_config.TECH_ICB


@dataclass(frozen=True)
class NoCard:
    """Why a stock has no card on the run date (falsy, so ``if card`` reads as before)."""
    symbol: str
    as_of: date
    profile: str
    reason: str                   # Vietnamese, for the owner

    def __bool__(self) -> bool:
        return False


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
    issues: tuple[tuple[date, float, str], ...]  # paid issues gone ex after it: (ex-date, ratio, kind), capital not in the book
    paid: float                   # what they paid out in all, đồng
    book: float                   # book per share at ``day``
    roes: tuple[tuple[int, float], ...]         # every annual ROE published, oldest first
    annual_published: date        # when the newest annual report was published
    roe_base: float
    roe_bear: float
    roe_bull: float               # ROE p80 (Bull's ROE unless ``bull_ke`` is set)
    assumptions: valuation_config.Assumptions
    bear: float
    eab: float
    base: float
    bull: float
    mos: float                    # (Base − price) / Base
    zone: str                     # Rẻ (price ≤ Base) / Hợp lý / Đắt (price ≥ Bull)
    sensitivity: tuple[tuple[float, float], ...]  # (Ke, Base)
    next_ex: date | None          # the next ex-date announced after ``day``, within EVENT_LOOKAHEAD_DAYS
    profile: str = "banking"      # valuation_config.PROFILES key
    reliability: str = ""         # cao / trung bình / thấp: how far the book anchors the value
    reliability_why: str = ""
    profit: float | None = None   # parent shareholders' profit of the four quarters published by ``day``
    profit_period: str = ""
    pb_rank: MultipleRank | None = None   # the session's P/B among every session since 2018
    pe_rank: MultipleRank | None = None   # the same for P/E ("default" profile: P/E is its own multiple)
    bull_ke: float | None = None  # not a bank, ROE p80 too close to Base: Bull is Base at this Ke (Ke − 2 points)


def _shares_before(history: pd.DataFrame, day: date) -> float:
    """Shares outstanding of the last session before ``day``: a cash dividend is paid to the holders of
    record, before a bonus issue going ex the same day (TPB 02/10/2026: 500đ and 15% in shares) adds its."""
    before = history.loc[history["Date"] < pd.Timestamp(day), "TotalShares"]
    return float(before.iloc[-1] if not before.empty else history["TotalShares"].iloc[0])


def _profit_steps(code: str, day: date) -> pd.DataFrame:
    """The four quarters' parent profit after each publication up to ``day`` (``_base_steps`` "pe")."""
    steps = _base_steps(code, "pe")
    return steps[steps["Published"] <= pd.Timestamp(day)].reset_index(drop=True)


def _book_growth(history: pd.DataFrame) -> float | None:
    """The parent equity's growth a year, from the same quarter ``GROWTH_YEARS`` years before the
    last session's (a bonus issue moves none of it; a cyclical profit's trough does not inflate it)."""
    quarter, year = (int(x) for x in str(history["Period"].iloc[-1])[1:].split("/"))
    then = history.loc[history["Period"] == f"Q{quarter}/{year - GROWTH_YEARS}", "Base"]
    now = float(history["Base"].iloc[-1])
    if then.empty or not float(then.iloc[0]) > 0 or not now > 0:
        return None
    return (now / float(then.iloc[0])) ** (1 / GROWTH_YEARS) - 1


def _reliability(profile: str, code: str, roe_base: float, history: pd.DataFrame,
                 roes: tuple = (), ke: float = 0.0) -> tuple[str, str]:
    """(level, why) of the profile (``valuation_config.RELIABILITY``). Not a bank, it drops to "thấp" after a
    year's ROE under −Ke (``roes``: (year, ROE)): a heavy loss makes Base and Bear by the years' ROE say little
    (DLG 30/10/2026: −86,6% in 2023, ROE p20 −58%); the "default" profile also for a technology company, a
    sustained ROE far above Ke, or a book growing fast."""
    level, why = valuation_config.RELIABILITY[profile]
    if profile == "banking":
        return level, why
    reasons = []
    year, worst = min(roes, key=lambda r: r[1], default=(0, 0.0))
    if worst < -ke:
        reasons.append(f"có năm lỗ nặng (ROE {year} {_pct(worst)}, dưới −Ke {_rate(ke)}): Base và Bear theo ROE các "
                       "năm ít ý nghĩa")
    if profile == "default":
        if _is_tech(code):
            reasons.append("công ty công nghệ, ít tài sản hữu hình: giá trị nằm ở con người và hợp đồng, không ở sổ sách")
        if roe_base >= valuation_config.HIGH_ROE:
            reasons.append(f"ROE bền vững {_pct(roe_base)} cao hơn hẳn Ke: lợi thế có thể bền hơn mức phai λ 0,4 nên "
                           "Base dễ thấp hơn thực")
        growth = _book_growth(history)
        if growth is not None and growth >= valuation_config.HIGH_GROWTH:
            reasons.append(f"vốn chủ sở hữu tăng {_pct(growth)}/năm trong {GROWTH_YEARS} năm: g 0 bỏ qua tăng trưởng")
    if not reasons:
        return level, why
    return "thấp", "; ".join(([why] if level == "thấp" else []) + reasons)   # real estate keeps its own why first


def build_card(symbol: str, as_of_date: str | None) -> ValuationCard | NoCard:
    """The card of the run date, from data published by then; a ``NoCard`` saying why there is none.

    Book per share = (the newest published quarter's parent equity − the cash dividends gone ex
    after that quarter's end, by the price's session) / the shares outstanding of that session.
    The shares change on a bonus issue's ex-date, so a book published before it is restated
    (CTG: Q3/2025 over 7,77 billion shares from 17/12/2025, the 44,6% bonus); each dividend is
    paid on the shares of the session before its ex-date. No card for a negative book, fewer than
    ``MIN_ROE_YEARS`` annual ROEs, or a loss (the four quarters', or the sustained ROE): the
    justified P/B needs a positive return on a positive book.
    """
    code = normalize_symbol(symbol)
    as_of = _as_of_day(as_of_date)
    profile = card_profile(code)
    setup = valuation_config.assumptions(profile)

    def none(reason: str) -> NoCard:
        return NoCard(symbol=code, as_of=as_of, profile=profile, reason=reason)

    rows = [(int(r["yearReport"]), v, public) for r, public in published_annual_ratio_rows(code, as_of_date)
            if (v := _num(r.get("roe"))) is not None]
    if len(rows) < MIN_ROE_YEARS:
        return none(f"mới có {len(rows)} năm ROE đã công bố (cần ít nhất {MIN_ROE_YEARS} năm cho ROE bền vững)")
    annual = sorted((year, v) for year, v, _ in rows)
    annual_published = max(public for _, _, public in rows)
    history = multiple_history(code, "pb", as_of_date)
    if history.empty or pd.isna(history["Base"].iloc[-1]):
        return none("chưa có giá phiên hoặc BCTC quý đã công bố tới ngày này")
    last = history.iloc[-1]
    if last["Base"] <= 0:
        return none(f"vốn chủ sở hữu công ty mẹ {last['Period']} âm ({rfmt.fmt_number(last['Base'] / 1e9, 1)} tỷ): "
                    "không có sổ sách để neo giá trị")
    day = last["Date"].date()
    quarter, year = (int(x) for x in str(last["Period"])[1:].split("/"))
    period_end = _period_end(year, quarter)
    events = vci.fetch_events(code, (period_end + timedelta(days=1)).isoformat(),
                              (as_of + timedelta(days=EVENT_LOOKAHEAD_DAYS)).isoformat(), codes="DIV,ISS")
    dividends, issues, upcoming = [], [], []
    for event in events:
        ex = _parse_day(event.get("exrightDate"))
        public = _parse_day(event.get("publicDate")) or ex
        if ex is None or public > as_of:
            continue
        # after the price's session, not the run date: on an ex-date morning the bars end the session
        # before, so the card (old basis) expires that day, not at the next one (CTG 17/12/2025, 44,6%)
        if ex > day:
            upcoming.append(ex)
        elif not period_end < ex <= day:
            continue
        elif event.get("eventCode") == "DIV" and (_num(event.get("valuePerShare")) or 0) > 0:
            dividends.append((ex, _num(event.get("valuePerShare"))))
        elif event.get("eventCode") == "ISS" and (kind := _PAID_ISSUE.search(event.get("eventTitleVi") or "")):
            issues.append((ex, _num(event.get("exerciseRatio")) or 0.0, _PAID_ISSUE_KIND[kind.group(0).lower()]))
    dividends.sort()
    issues.sort()
    paid = sum(value * _shares_before(history, ex) for ex, value in dividends)
    shares = float(last["TotalShares"])
    book = (float(last["Base"]) - paid) / shares
    if book <= 0:
        return none("sổ sách/cp sau cổ tức tiền đã trả ≤ 0: không có sổ sách để neo giá trị")
    profits = _profit_steps(code, day)
    profit = float(profits["Base"].iloc[-1]) if not profits.empty and pd.notna(profits["Base"].iloc[-1]) else None
    profit_period = str(profits["Period"].iloc[-1]) if not profits.empty else ""
    if profit is not None and profit <= 0:
        return none(f"đang lỗ: LNST công ty mẹ 4 quý tới {profit_period} là {rfmt.fmt_number(profit / 1e9, 1)} tỷ; "
                    "P/B hợp lý cần lợi nhuận dương")

    values = [v for _, v in annual]
    roe_base = float(np.median(values[-ROE_YEARS:]))
    if roe_base <= 0:
        return none(f"ROE bền vững (trung vị {min(len(values), ROE_YEARS)} năm) {_pct(roe_base)} ≤ 0: lỗ kéo dài")
    p20, p80 = (float(v) for v in np.percentile(values, [20, 80]))
    roe_bear, roe_bull = min(p20, roe_base), max(p80, roe_base)   # Bear ≤ Base ≤ Bull, whatever the spread

    def value(roe: float, ke: float = setup.ke) -> float:
        return book * justified_pb(roe, ke, setup.lam, setup.g)

    base, close = value(roe_base), float(last["Close"])
    bull, bull_ke = value(roe_bull), None
    # Not a bank (the bank card stays v1.16.0's): a steady or rising ROE puts p80 next to the median
    # (FPT 30/10/2026: Bull 0,17% above Base), so the Hợp lý band would vanish and the zone flip Rẻ ↔ Đắt
    # on a tick. Bull is then at least the sensitivity line's Base at Ke − 2 points.
    floor_ke = round(setup.ke - valuation_config.SENSITIVITY_STEP, 6)
    if profile != "banking" and value(roe_base, floor_ke) > bull:
        bull, bull_ke = value(roe_base, floor_ke), floor_ke
    zone = "Rẻ" if close <= base else "Đắt" if close >= bull else "Hợp lý"
    reliability, why = _reliability(profile, code, roe_base, history, tuple(annual), setup.ke)
    # ROE p20 under Ke − (1 + Ke − (1 − λ)(1 + g)) (−40% at g 0) gives a negative P/B; a share is worth no
    # less than nothing. Only after a heavy loss year, which made the card "thấp" above (not a bank).
    bear_pb = justified_pb(roe_bear, setup.ke, setup.lam, setup.g)
    if bear_pb < 0:
        why += f"; ROE p20 {_pct(roe_bear)} cho P/B âm nên Bear giữ 0"
    pe_rank = rank_of(multiple_history(code, "pe", as_of_date), "pe") if profile == "default" else None
    return ValuationCard(
        symbol=code, as_of=as_of, day=day, close=close, period=str(last["Period"]),
        published=last["PeriodPublished"], period_end=period_end, equity=float(last["Base"]), shares=shares,
        dividends=tuple(dividends), issues=tuple(issues), paid=paid, book=book, roes=tuple(annual), annual_published=annual_published,
        roe_base=roe_base, roe_bear=roe_bear, roe_bull=roe_bull, assumptions=setup,
        bear=book * max(bear_pb, 0.0), eab=base * (1 - setup.margin), base=base, bull=bull, mos=(base - close) / base,
        zone=zone, sensitivity=tuple((ke, value(roe_base, ke)) for ke in valuation_config.sensitivity_ke(setup.ke)),
        next_ex=min(upcoming) if upcoming else None, profile=profile, reliability=reliability, reliability_why=why,
        profit=profit, profit_period=profit_period, pb_rank=rank_of(history, "pb"), pe_rank=pe_rank, bull_ke=bull_ke,
    )


def _key(symbol: str, as_of_date: str | None) -> tuple:
    code = normalize_symbol(symbol)
    return code, _as_of_day(as_of_date).isoformat(), valuation_config.assumptions(card_profile(code))


def _last_session(day: date) -> date:
    while not vn_rules.is_session_day(day.isoformat()):
        day -= timedelta(days=1)
    return day


def _final(key: tuple, built_on: str, c: ValuationCard | NoCard | None) -> bool:
    """A past run date's card that cannot change any more: built on a later day, with that date's
    last session. Any other (today's, one built during the session, none) is rebuilt after an hour."""
    return (isinstance(c, ValuationCard) and built_on > key[1]
            and c.day == _last_session(date.fromisoformat(key[1])))


def _fresh(key: tuple, hit) -> bool:
    """A kept entry ``assess`` answers with: final, or built within the hour."""
    return bool(hit) and (_final(key, hit[1], hit[2]) or time.time() - hit[0] < money_flows.RECHECK_SECONDS)


def _build(symbol: str, as_of_date: str | None) -> ValuationCard | NoCard | None:
    """``build_card`` behind the breaker of the informational VCI calls (``money_flows.VCI_INFO``, not
    the engine's): while VCI is down the card fails at once instead of waiting out each request's
    retries. No data is not an outage."""
    def build():
        try:
            return build_card(symbol, as_of_date)
        except NoMarketDataError as exc:
            logger.info("No valuation card for %s: %s", symbol, exc)
            return None

    return money_flows._guarded(money_flows.VCI_INFO, build)


def assess(symbol: str, as_of_date: str | None) -> ValuationCard | NoCard:
    """``build_card``, kept per (stock, run date, assumptions): the engine node, the fundamentals
    tool, /nhanh and /dinhgia of one day read the same numbers. Rebuilt after an hour
    (``money_flows.RECHECK_SECONDS``) until it is final (``_final``). VCI down raises
    (``VendorError``), a stock without the data is a ``NoCard``."""
    key = _key(symbol, as_of_date)
    with _cards_lock:
        hit = _cards.get(key)
    if _fresh(key, hit):
        built = hit[2]
    else:
        built = _build(symbol, as_of_date)
        with _cards_lock:
            if len(_cards) >= _CARD_CACHE_MAX:
                _cards.pop(min(_cards, key=lambda k: _cards[k][0]), None)
            _cards[key] = (time.time(), vn_rules.now_vn().date().isoformat(), built)
    if built is None:
        return NoCard(symbol=key[0], as_of=date.fromisoformat(key[1]), profile=card_profile(key[0]),
                      reason="VCI chưa có đủ BCTC hoặc giá của mã này")
    return built


def card(symbol: str, as_of_date: str | None) -> ValuationCard | None:
    """``assess``: the card, None when there is none."""
    built = assess(symbol, as_of_date)
    return built if isinstance(built, ValuationCard) else None


def kept(symbol: str, as_of_date: str | None) -> bool:
    """Whether ``assess`` would answer from what it keeps (a card or a NoCard), without VCI."""
    key = _key(symbol, as_of_date)
    with _cards_lock:
        hit = _cards.get(key)
    return _fresh(key, hit)


def cached_card(symbol: str, as_of_date: str | None) -> ValuationCard | None:
    """The card of a BANK ``card`` keeps for the run date, without building one (the Output Guard: the
    fundamentals tool may have built it after the engine's node failed to). Never another stock's,
    though /dinhgia, its evening job and the Tài sản page keep those under the same key: the engine's report
    carries no card for them, and there Bull/Bear are the two sides of the debate."""
    with _cards_lock:
        hit = _cards.get(_key(symbol, as_of_date))
    found = hit[2] if hit else None
    return found if isinstance(found, ValuationCard) and found.profile == "banking" else None


def _day(day: date, as_of: date) -> str:
    """dd/mm, with the year when it is not the run date's: on 30/03/2026 the 2024 annual report
    published 29/03/2025 must not read as yesterday's."""
    return f"{day:%d/%m}" if day.year == as_of.year else f"{day:%d/%m/%Y}"


def _label(kind: str, day: date, as_of: date) -> str:
    return f"[{kind} {_day(day, as_of)}]"


def _pct(value: float) -> str:
    return rfmt.fmt_pct(value * 100)


def _rate(value: float) -> str:
    """An assumption in %, without trailing zeros: 12%, 7,94%, 0%."""
    return rfmt.fmt_number(value * 100).rstrip("0").rstrip(",") + "%"


def bull_basis(c: ValuationCard) -> str:
    """What Bull rests on: ROE p80, or (not a bank, p80 next to Base) the Base at Ke − 2 points."""
    if c.bull_ke is None:
        return f"theo ROE p80 {_pct(c.roe_bull)}"
    return f"= Base với Ke {_rate(c.bull_ke)} (ROE p80 {_pct(c.roe_bull)} sát Base)"


def _expiry(c: ValuationCard) -> str:
    when = _day(c.next_ex, c.as_of) if c.next_ex else "chưa có lịch"
    return f"Hạn dùng: tới khi có BCTC quý mới hoặc tới ngày GDKHQ kế tiếp ({when})"


def render_card(c: ValuationCard) -> str:
    """The card for the engine's report: every price with its distance from the session's close."""
    a = c.assumptions
    model = _label("THỐNG KÊ", c.day, c.as_of)
    years = [y for y, _ in c.roes]
    recent = c.roes[-ROE_YEARS:]
    span = (f"{len(recent)} năm {recent[0][0]}-{recent[-1][0]}" if len(recent) >= ROE_YEARS
            else f"chỉ có {len(recent)} năm {recent[0][0]}-{recent[-1][0]}, chưa đủ {ROE_YEARS}")
    lines = [
        f"{CARD_HEADING}, không vào quyết định; không phải khuyến nghị) - ngân hàng ---",
        f"Giá phiên {_day(c.day, c.as_of)} · BCTC {c.period} công bố {_day(c.published, c.as_of)} · {_expiry(c)}",
        f"{_label('FACT', c.published, c.as_of)} Vốn chủ sở hữu công ty mẹ {c.period} (tới {c.period_end:%d/%m/%Y}): "
        f"{rfmt.fmt_number(c.equity / 1e9, 1)} tỷ",
    ]
    if c.dividends:
        paid = " + ".join(f"{rfmt.fmt_price(v)} đ/cp (GDKHQ {ex:%d/%m/%Y})" for ex, v in c.dividends)
        lines.append(f"{_label('FACT', c.dividends[-1][0], c.as_of)} Cổ tức tiền GDKHQ sau ngày khoá sổ quý, đã trừ khỏi "
                     f"sổ sách: {paid}, tổng {rfmt.fmt_number(c.paid / 1e9, 1)} tỷ")
    lines += [
        f"{_label('FACT', c.day, c.as_of)} Sổ sách/cp {rfmt.fmt_price(c.book)} = (vốn chủ"
        + (" − cổ tức tiền đã trả" if c.dividends else "")
        + f") / {rfmt.fmt_price(c.shares)} cp lưu hành phiên {c.day:%d/%m/%Y}",
    ]
    if c.issues:
        issues = ", ".join(f"{kind}{f' {rfmt.fmt_pct(r * 100, 1)}' if r > 0 else ''} (GDKHQ {ex:%d/%m/%Y})"
                           for ex, r, kind in c.issues)
        lines.append(f"{_label('FACT', c.issues[-1][0], c.as_of)} Phát hành thêm sau ngày khoá sổ quý: {issues}: số cổ "
                     f"phiếu đã tính phần mới, vốn thu về chưa có trong BCTC {c.period} → sổ sách/cp và các mốc dưới "
                     "đây thấp hơn thực tế")
    lines += [
        f"{_label('THỐNG KÊ', c.annual_published, c.as_of)} ROE bền vững {_pct(c.roe_base)} = trung vị ROE năm ({span}: "
        + " · ".join(_pct(v) for _, v in recent) + f"); ROE p20 {_pct(c.roe_bear)}, p80 {_pct(c.roe_bull)} "
        f"của {len(years)} năm {years[0]}-{years[-1]}",
        f"{model} Base (giá trị hợp lý) {rfmt.format_level(c.close, c.base)} = {rfmt.fmt_number(c.base / c.book)} "
        f"lần sổ sách; Ke {_rate(a.ke)} (giả định đặt tay), λ {rfmt.fmt_number(a.lam, 1)}, g {_rate(a.g)}: "
        "P/B = 1 + (ROE − Ke)/(1 + Ke − (1 − λ)(1 + g))",
        f"{model} Bear {rfmt.format_level(c.close, c.bear)} theo ROE p20 · Bull {rfmt.format_level(c.close, c.bull)} "
        + ("theo ROE p80" if c.bull_ke is None else bull_basis(c)),
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
        f"- Định giá tham khảo (thẻ của hệ thống, không vào quyết định) {_label('THỐNG KÊ', c.day, c.as_of)}: "
        f"Bear {rfmt.fmt_price(c.bear)} · eab {rfmt.fmt_price(c.eab)} · Base {rfmt.fmt_price(c.base)} · "
        f"Bull {rfmt.fmt_price(c.bull)} VND; giá {rfmt.fmt_price(c.close)} → vùng {c.zone} "
        f"(MOS {rfmt.fmt_signed_pct(c.mos * 100)}); sổ sách/cp {rfmt.fmt_price(c.book)}, ROE bền vững "
        f"{_pct(c.roe_base)}, Ke {_rate(c.assumptions.ke)} đặt tay. Giá trị hợp lý chỉ trích đúng các số này, giữ nhãn"
    )


def quick_line(symbol: str, as_of_date: str | None) -> str:
    """One line for /nhanh (banks): the card's levels and zone, the price's session, how far it is
    from Base, and that Ke is set by hand with the Base it gives 2 points either side; "" otherwise."""
    if not is_bank(normalize_symbol(symbol)):
        return ""
    c = card(symbol, as_of_date)
    if c is None:
        return ""
    (low_ke, high_base), (high_ke, low_base) = c.sensitivity[0], c.sensitivity[-1]
    gap = (c.close - c.base) / c.base * 100
    return (f"📐 Định giá tham khảo (không vào quyết định): Bear {rfmt.fmt_price(c.bear)} · Base {rfmt.fmt_price(c.base)}"
            f" · Bull {rfmt.fmt_price(c.bull)} → {c.zone}: giá {rfmt.fmt_price(c.close)} (phiên {_day(c.day, c.as_of)}) "
            f"{'trên' if gap > 0 else 'dưới'} Base {rfmt.fmt_pct(abs(gap), 1)}. Ke {_rate(c.assumptions.ke)} đặt tay "
            f"(Ke {_rate(low_ke)}-{_rate(high_ke)}: Base {rfmt.fmt_price(high_base)}-{rfmt.fmt_price(low_base)}), "
            f"BCTC {c.period}")


def split_card(report: str) -> tuple[str, str]:
    """(the card inside an engine report, the rest of the report); ("", report) without a card."""
    report = report or ""
    start = report.find(CARD_HEADING)
    if start < 0:
        return "", report
    card, gap, after = report[start:].partition("\n\n")
    return card, report[:start] + gap + after


def card_prices(report: str) -> set[int]:
    """The prices printed on the card inside an engine report (empty: no card)."""
    return rfmt.price_numbers(split_card(report)[0])
