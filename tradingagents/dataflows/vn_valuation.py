"""A stock's valuation against its own history, point-in-time (informational only).

The multiple of every session since 2018 - the first year VCI has quarterly
statements for - and where the run date's multiple sits among them. Market
value is the session's UNADJUSTED close × the shares outstanding that same
session (VCI price-history, ``flows.fetch_stock_flows``): both change on a
bonus-share ex-date, so the multiple stays continuous. The adjusted bars of
``vci.fetch_ohlcv`` are not usable here: an old adjusted price over the book
of its day gives a wrong multiple before every bonus issue.

- P/B: market value / equity of the parent's shareholders (``bsa78`` total
  equity minus ``bsa210`` minority interest) of the newest quarter published
  (``publicDate``) by that session.
- P/E: market value / parent shareholders' profit (``isa22``) of the four
  newest quarters published by that session, consecutive. VCI's quarterly EPS
  (``isa23``) is not used: it is 0 for banks and for some quarters of others.

Nothing in the Quant Engine (engine, policy, simulator, calibration) reads
this module: it only writes text for the analysts.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.errors import VendorError
from tradingagents.dataflows.files import locked, replace_file
from tradingagents.dataflows.symbols import safe_ticker_component
from tradingagents.dataflows.vendors import flows, vci
from tradingagents.dataflows.vn_fundamentals import _as_of_day, _num, published_on
from tradingagents.market import vn_rules
from tradingagents.quant import report_format as rfmt

logger = logging.getLogger(__name__)

HISTORY_START = "2018-01-01"   # VCI's quarterly statements start with Q1/2018
MIN_SESSIONS = 120             # fewer sessions with a multiple: no percentile
METRIC_LABELS = {"pb": "P/B", "pe": "P/E"}
_PRICE_COLUMNS = ["Date", "Close", "TotalShares"]


# ─── Unadjusted prices, cached for the day ────────────────────────────────────


def _dir() -> str:
    path = os.path.join(get_config()["data_cache_dir"], "valuation")
    os.makedirs(path, exist_ok=True)
    return path


def _load(path: str) -> tuple[str | None, pd.DataFrame]:
    """(the day it was fetched, the cached rows) of one stock's file."""
    try:
        with open(path, encoding="utf-8") as handle:
            saved = json.load(handle)
        frame = pd.DataFrame({c: saved[c] for c in _PRICE_COLUMNS})
        frame["Date"] = pd.to_datetime(frame["Date"])
        return saved.get("fetched"), frame
    except (OSError, ValueError, KeyError, TypeError):
        return None, pd.DataFrame({"Date": pd.to_datetime([]), "Close": [], "TotalShares": []})


def price_history(symbol: str) -> pd.DataFrame:
    """Unadjusted close and shares outstanding of every session since ``HISTORY_START``.

    Kept in ``<data_cache_dir>/valuation`` and asked of VCI at most once a day:
    the day's first call fetches from the last cached session on (that session
    again, in case it was saved before its close was final). A failed fetch
    leaves the cached rows (possibly none) and is asked again on the next call.
    """
    code = safe_ticker_component(symbol.upper())
    path = os.path.join(_dir(), f"{code}-prices.json")
    today = vn_rules.now_vn().date().isoformat()
    with locked(path):
        fetched, cached = _load(path)
        if fetched == today:
            return cached
        start = cached["Date"].iloc[-1].strftime("%Y-%m-%d") if not cached.empty else HISTORY_START
        try:
            fresh = flows.fetch_stock_flows(code, start, today)[_PRICE_COLUMNS]
        except VendorError as exc:
            logger.warning("Price history of %s for the valuation unavailable: %s", code, exc)
            return cached
        frames = [f for f in (cached, fresh) if not f.empty]
        frame = (pd.concat(frames).drop_duplicates("Date", keep="last").sort_values("Date").reset_index(drop=True)
                 if frames else cached)
        saved = {"fetched": today, "Date": frame["Date"].dt.strftime("%Y-%m-%d").tolist(),
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


def multiple_history(symbol: str, metric: str, as_of_date: str | None) -> pd.DataFrame:
    """One row per session up to the run date: Date, Close, TotalShares, Base, Period,
    PeriodPublished, Multiple (NaN before the first statement or when the base is not positive)."""
    prices = price_history(symbol)
    prices = prices[prices["Date"] <= pd.Timestamp(_as_of_day(as_of_date))].reset_index(drop=True)
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
    return (
        f"- {label} hôm nay {rfmt.fmt_number(rank.value)} (giá đóng cửa {rank.day:%d/%m/%Y}: {rfmt.fmt_price(rank.close)}; "
        f"{base} tới {rank.period}, công bố {rank.published:%d/%m/%Y}): rẻ hơn {rfmt.fmt_number(rank.higher_pct, 0)}% "
        f"số phiên từ {rank.since:%m/%Y} ({rfmt.fmt_price(rank.sessions)} phiên; thấp nhất {rfmt.fmt_number(rank.low)}, "
        f"cao nhất {rfmt.fmt_number(rank.high)})"
    )
