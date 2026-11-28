"""The ETF buy-and-hold benchmark: the whole paper capital put into one ETF on the first
session of the measurement and kept (``BENCH_ETF``, default E1VFVN30; ``BENCH_ETF_START``
an optional YYYY-MM-DD, else the first session the trading run processes after the deploy).

- Bought at the START session's CLOSE (the run happens after the close, so the benchmark
  starts the same evening the books' NAV of that session is written), in lots of 100, with the
  buy fee of ``vn_rules`` (the books' fee); the cash that did not fit a lot stays cash.
- NAV each session = cash + the stake x close / close at the start, on ONE adjustment basis:
  the bars are dividend-adjusted by the source, so the ratio is the total return with the
  dividends; a re-adjustment of the whole series (an ex-date since the last refresh) moves
  nothing. ``nav_net``: the same if sold at that close (sell fee + the 0.1% tax).
- Refreshed by the trading run; a source failure keeps the previous state (``error``), never
  stops the run. State: ``data/results/etf_bench.json``.
"""

from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

FILE = "etf_bench.json"
DEFAULT_ETF = "E1VFVN30"
KEEP_HISTORY = 1500


def symbol() -> str:
    return (os.environ.get("BENCH_ETF") or DEFAULT_ETF).strip().upper() or DEFAULT_ETF


def start_env() -> str:
    raw = (os.environ.get("BENCH_ETF_START") or "").strip()
    try:
        return datetime.strptime(raw, "%Y-%m-%d").strftime("%Y-%m-%d") if raw else ""
    except ValueError:
        logger.warning("BENCH_ETF_START=%r is not YYYY-MM-DD; the first run's session starts the benchmark", raw)
        return ""


def path(data_dir: Path | str) -> Path:
    return Path(data_dir) / "results" / FILE


def load(data_dir: Path | str) -> dict | None:
    try:
        data = json.loads(path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("symbol") else None


def _save(data_dir: Path | str, state: dict) -> None:
    from vnbot.jobs import write_text_atomic

    state = dict(state, history=state.get("history", [])[-KEEP_HISTORY:])
    write_text_atomic(path(data_dir), json.dumps(state, ensure_ascii=False))


def _finite(value) -> bool:
    try:
        return value is not None and math.isfinite(float(value)) and float(value) > 0
    except (TypeError, ValueError):
        return False


def _closes(frame) -> list[tuple[str, float]]:
    """(day, close) of the frame's sessions with a usable close, oldest first."""
    out = []
    for day, close in zip(frame["Date"], frame["Close"], strict=True):
        if _finite(close):
            out.append((day.strftime("%Y-%m-%d") if hasattr(day, "strftime") else str(day)[:10], float(close)))
    return out


def _buy(capital: float, close: float) -> tuple[int, float, float]:
    """(shares, stake, cash) of the whole ``capital`` put into the ETF at ``close``: lots of
    100 with the buy fee of vn_rules; the stake is the shares' value at the start."""
    from tradingagents.market import vn_rules

    fee = vn_rules.BUY_FEE_PCT / 100
    shares = vn_rules.round_lot(capital / (close * (1 + fee)))
    stake = shares * close
    cash = capital - stake * (1 + fee)
    return shares, stake, cash


def compute(state: dict, closes: list[tuple[str, float]]) -> dict:
    """The state with its history rebuilt from ``closes`` (one adjustment basis). Buys on the
    first session on or after ``start`` when the stake is not set yet."""
    from tradingagents.market import vn_rules

    net = 1 - (vn_rules.SELL_FEE_PCT + vn_rules.SELL_TAX_PCT) / 100
    rows = [(d, c) for d, c in closes if d >= state["start"]]
    if not rows:
        return state
    if not state.get("bought"):
        day, close = rows[0]
        shares, stake, cash = _buy(float(state["capital"]), close)
        state.update(bought=day, shares=shares, stake=round(stake), cash=round(cash), entry=close)
    rows = [(d, c) for d, c in rows if d >= state["bought"]]
    base = rows[0][1]
    stake, cash = float(state["stake"]), float(state["cash"])
    history = []
    for day, close in rows:
        value = stake * close / base
        history.append({"date": day, "close": close, "nav": round(cash + value), "nav_net": round(cash + value * net)})
    state["history"] = history
    state["basis"] = base          # the start close on the basis of the latest refresh (informational)
    return state


def refresh(data_dir: Path | str, capital: float, day: str, *, frame=None) -> dict | None:
    """Bring the benchmark up to ``day`` (a final session): starts it when there is none
    (``BENCH_ETF_START`` or ``day``), reads the ETF's bars (``frame``: given, for the tests),
    keeps the previous state on a failure. Returns the state, None when nothing could start."""
    state = load(data_dir)
    code = symbol()
    if state is None or state.get("symbol") != code:
        if state is not None:
            logger.info("ETF benchmark: BENCH_ETF changed %s -> %s; a new benchmark starts", state.get("symbol"), code)
        state = {"symbol": code, "start": start_env() or day, "capital": float(capital), "history": []}
    try:
        if frame is None:
            from tradingagents.dataflows import ohlcv

            frame = ohlcv.load_ohlcv(code, day, fill_gaps=False)
        closes = _closes(frame)
        if not closes:
            raise ValueError("không có giá đóng cửa nào")
        state = compute(state, closes)
        state.update(updated=day, error="")
    except Exception as exc:  # noqa: BLE001 - the benchmark never stops the run
        logger.warning("ETF benchmark %s up to %s failed: %s", code, day, exc)
        state["error"] = f"{type(exc).__name__}: {exc}"[:200]
    _save(data_dir, state)
    return state


def stats(state: dict | None) -> dict | None:
    """NAV, returns (gross and if sold today), max drawdown, dates; None before the first session."""
    if not state or not state.get("history"):
        return None
    history = state["history"]
    capital = float(state["capital"])
    navs = [h["nav"] for h in history]
    peak, max_dd = navs[0], 0.0
    for v in navs:
        peak = max(peak, v)
        max_dd = max(max_dd, (peak - v) / peak * 100 if peak else 0.0)
    last = history[-1]
    return {"symbol": state["symbol"], "start": state.get("bought") or state["start"], "last": last["date"],
            "close": last["close"], "shares": state.get("shares"), "cash": state.get("cash"),
            "nav": last["nav"], "nav_net": last["nav_net"],
            "return_pct": round((last["nav"] / capital - 1) * 100, 2),
            "return_net_pct": round((last["nav_net"] / capital - 1) * 100, 2),
            "max_drawdown_pct": round(max_dd, 2), "sessions": len(history), "error": state.get("error") or ""}


def series(state: dict | None) -> list[dict]:
    """{date, nav} per session, for the NAV chart (like a book's history)."""
    return [{"date": h["date"], "nav": h["nav"]} for h in (state or {}).get("history") or []]


def line(state: dict | None, *, fmt=None) -> str:
    """One line for /danhmuc and the reports."""
    st = stats(state)
    code = (state or {}).get("symbol") or symbol()
    if st is None:
        why = (state or {}).get("error")
        return f"📈 ETF {code} mua-giữ: chưa có phiên nào" + (f" ({why})" if why else "") + "."
    money = fmt or (lambda v: f"{v:,.0f}".replace(",", "."))
    return (f"📈 ETF {code} mua-giữ: NAV {money(st['nav'])} ({st['return_pct']:+.2f}% từ {st['start']}; "
            f"bán hôm nay {st['return_net_pct']:+.2f}% sau phí, thuế), DD {st['max_drawdown_pct']:.1f}%"
            + (f" · ⚠️ {st['error']}" if st["error"] else ""))
