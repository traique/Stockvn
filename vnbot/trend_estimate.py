"""The bot's estimate of sieutinhieu.vn's in-session trend changes ("đổi chiều trong phiên").

During the session the site lists the stocks whose daily trend would flip if the session
closed at the current price (e.g. HDB BUY -> SELL at 09:54); without an account it shows two
a side, the rest is a paid product, left alone. This module makes the same comparison
itself, for the stocks the owner follows only, from public prices: no request to the site.

The site's daily trend, measured against its published per-stock histories (12 stocks, 360
signals), follows this rule on the daily closes:

- BUY -> SELL when the close is under its 20-session average (SMA20) and MACD(12, 26, 9)
  is under its signal line;
- SELL -> BUY when the close is over SMA20, MACD over its signal line, and the close over
  the previous close.

Same session as the site for 88% of its signals, within two sessions for 97% (the rest
mostly a session or two early). The levels the site showed in its free rows on 22/10/2026
(HDB 22.000, MSR 61.880) are within 0,1% of the levels computed here. It is an estimate:
the site may weigh more than this, and an ex-dividend day (old closes not adjusted yet) can
fool it. The site's own state (its latest signal) is always the starting point.

From the closes up to the last closed session (the "basis"), today's close flips the trend:

- to SELL when it is under ``sell_below`` = min(mean of the last 19 closes - the close at
  which close = SMA20 -, the close at which MACD = its signal line);
- to BUY when it is over ``buy_above`` = max(those two, the previous close).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from vnbot.jobs import write_text_atomic

logger = logging.getLogger(__name__)

SMA_N = 20
FAST, SLOW, SIGNAL = 12, 26, 9
MIN_BARS = 60                  # MACD needs its averages warmed up
WARMUP = 35                    # the rule's state is read from this bar on
NEAR_PCT = 2.0                 # "sát mức" in /stvdoichieu
LEVELS_BUDGET_SECONDS = 150    # computing the levels of one scan (the price sources slow or down)
PRICES_BUDGET_SECONDS = 90     # asking the last prices of one scan
MATCH_NOTE = "khớp ~88% ngày đổi chiều của trang, 97% trong vòng 2 phiên"
METHOD = "TB20 + MACD(12,26,9) theo giá đóng cửa"

_lock = threading.Lock()


def _ema(values: list[float], span: int) -> list[float]:
    alpha = 2 / (span + 1)
    out, e = [], values[0]
    for v in values:
        e = alpha * v + (1 - alpha) * e
        out.append(e)
    return out


def levels(closes: list[float]) -> dict | None:
    """The flip levels for the session after the last of ``closes`` (oldest first, closed
    sessions), and the rule's own state then; None with too few closes."""
    closes = [float(c) for c in closes if c and c > 0]
    if len(closes) < MIN_BARS:
        return None
    e1, e2 = _ema(closes, FAST), _ema(closes, SLOW)
    macd = [a - b for a, b in zip(e1, e2)]
    sig = _ema(macd, SIGNAL)
    a1, a2 = 2 / (FAST + 1), 2 / (SLOW + 1)
    sma_level = sum(closes[-(SMA_N - 1):]) / (SMA_N - 1)
    macd_level = (sig[-1] - (1 - a1) * e1[-1] + (1 - a2) * e2[-1]) / (a1 - a2)
    state = None
    for i in range(max(WARMUP, SMA_N - 1), len(closes)):
        c = closes[i]
        sma = sum(closes[i - SMA_N + 1:i + 1]) / SMA_N
        hist = macd[i] - sig[i]
        if state is None:
            state = "BUY" if c > sma else "SELL"
        elif state == "BUY" and c < sma and hist < 0:
            state = "SELL"
        elif state == "SELL" and c > sma and hist > 0 and c > closes[i - 1]:
            state = "BUY"
    prev = closes[-1]
    return {"sma": round(sma_level, 1), "macd": round(macd_level, 1), "prev": prev,
            "sell_below": round(min(sma_level, macd_level), 1),
            "buy_above": round(max(sma_level, macd_level, prev), 1), "rule": state}


def flip(state: str, price: float, lv: dict) -> str | None:
    """The side the trend flips to if the session closes at ``price``, else None."""
    if state == "BUY" and price < lv["sell_below"]:
        return "SELL"
    if state == "SELL" and price > lv["buy_above"]:
        return "BUY"
    return None


def level_for(state: str, lv: dict) -> float:
    """The level that matters from ``state``: under it a BUY flips, over it a SELL."""
    return lv["sell_below"] if state == "BUY" else lv["buy_above"]


def distance_pct(state: str, price: float, lv: dict) -> float:
    """How far ``price`` is from flipping, in % of the price (<= 0: flipped or on it)."""
    level = level_for(state, lv)
    return ((price - level) if state == "BUY" else (level - price)) / price * 100


def history_closes(symbol: str, basis: str) -> list[float]:
    """The closes up to session ``basis`` (the bot's price sources, cached by the loader);
    ValueError when the source does not have ``basis`` yet."""
    from tradingagents.dataflows.ohlcv import load_ohlcv

    frame = load_ohlcv(symbol, basis, fill_gaps=False)
    if frame.empty or frame["Date"].iloc[-1].strftime("%Y-%m-%d") != basis:
        raise ValueError(f"nguồn giá chưa có phiên {basis[8:10]}/{basis[5:7]}")
    return [float(c) for c in frame["Close"]]


class LevelCache:
    """The levels of each followed stock for one basis (``data/external/trend_levels.json``):
    computed once a session, then every scan only asks for the last price."""

    def __init__(self, directory: Path):
        self.path = Path(directory) / "external" / "trend_levels.json"

    def _read(self, basis: str) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data.get("levels", {}) if isinstance(data, dict) and data.get("basis") == basis else {}

    def get(self, symbols, basis: str, closes=None, *, budget: float = LEVELS_BUDGET_SECONDS) -> tuple[dict, dict]:
        """({symbol: levels}, {symbol: why not}) for ``basis``, computing the missing ones
        (``closes(symbol, basis)``: ``history_closes`` by default) for ``budget`` seconds at most:
        the rest waits for the next scan."""
        closes = closes or history_closes
        with _lock:
            have = self._read(basis)
        out, errors, new = {}, {}, {}
        started = time.monotonic()
        for sym in symbols:
            if sym in have:
                out[sym] = have[sym]
                continue
            if time.monotonic() - started > budget:
                errors[sym] = "nguồn giá chậm, tính lượt sau"
                continue
            try:
                lv = levels(closes(sym, basis))
            except Exception as exc:  # noqa: BLE001 - one stock must not stop the others
                errors[sym] = str(exc)[:80] or type(exc).__name__
                continue
            if lv is None:
                errors[sym] = "lịch sử giá quá ngắn"
                continue
            out[sym] = new[sym] = lv
        if new:
            with _lock:
                merged = self._read(basis)
                merged.update(new)
                self.path.parent.mkdir(parents=True, exist_ok=True)
                write_text_atomic(self.path, json.dumps({"basis": basis, "levels": merged}))
        return out, errors


REBASE_PCT = 0.1               # the reference price this far from the last close: an ex-date


def rebase(lv: dict, ref: float | None, exchange: str | None) -> dict:
    """The levels on today's price basis. On HOSE and HNX the session's reference price is
    the last close, except on an ex-date (dividend, bonus shares) where the exchange lowers
    it: then the closes the levels come from are not adjusted yet, and every level (SMA and
    EMAs are linear in the closes) is scaled by reference / last close. UPCoM's reference is
    the last session's average price, so it says nothing there."""
    if not ref or exchange not in ("HOSE", "HNX") or not lv.get("prev"):
        return lv
    ratio = ref / lv["prev"]
    if abs(ratio - 1) * 100 <= REBASE_PCT:
        return lv
    out = {k: (round(v * ratio, 1) if isinstance(v, (int, float)) and not isinstance(v, bool) else v) for k, v in lv.items()}
    out["rebased"] = round(ratio, 4)
    return out


def board_prices(symbols) -> dict[str, dict]:
    """{symbol: {price, ref, exchange}} from the price board, all stocks in one request."""
    from tradingagents.dataflows.vendors import vci

    return vci.fetch_board(symbols)


def assess(symbols, states: dict, lv: dict, prices: dict, *, exchange_of=None, refs=None) -> tuple[list[dict], dict]:
    """One row per stock with a state, levels and a price: ``{symbol, state, price, level,
    dist, flip}``; and {symbol: why not} for the others. ``refs`` ({symbol: (reference price,
    exchange)}, from the price board) puts the levels on an ex-date's basis (``rebase``). A
    price still outside the exchange's band around the previous close is not a real move
    of the trend (an ex-date not adjusted, a bad tick): left out."""
    from tradingagents.market import vn_rules

    rows, skipped = [], {}
    for sym in symbols:
        state, levels_, price = states.get(sym), lv.get(sym), prices.get(sym)
        if state not in ("BUY", "SELL"):
            skipped[sym] = "chưa có tín hiệu của trang"
            continue
        if not levels_:
            continue                             # the reason is in the cache's errors
        if not price:
            skipped[sym] = "chưa có giá"
            continue
        if refs and sym in refs:
            levels_ = rebase(levels_, *refs[sym])
        exchange = exchange_of(sym) if exchange_of else None
        band = vn_rules.band_pct(exchange) if exchange else max(vn_rules.PRICE_BAND_PCT.values())
        if abs(price / levels_["prev"] - 1) * 100 > band + 0.5:
            skipped[sym] = "giá ngoài biên độ so với phiên trước, nghi ngày GDKHQ"
            continue
        row = {"symbol": sym, "state": state, "price": price, "level": level_for(state, levels_),
               "dist": round(distance_pct(state, price, levels_), 2), "flip": flip(state, price, levels_)}
        if levels_.get("rebased"):
            row["rebased"] = levels_["rebased"]
        rows.append(row)
    return rows, skipped


class Snapshot:
    """The last estimate (``data/external/trend_now.json``), for /stvdoichieu and /stvquet."""

    def __init__(self, directory: Path):
        self.path = Path(directory) / "external" / "trend_now.json"

    def read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(self.path, json.dumps(data, ensure_ascii=False))
