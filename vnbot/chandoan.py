"""Vì sao chưa có lệnh MUA: record every Quant Engine decision, the gate that kept each
stock from a BUY, and measure afterwards whether those gates were right.

The OKX bot (altcoin_bot) keeps every signal that reached its filters, the rejected
ones too, then follows its "shadow" result on the candles that came after, and only
lets the numbers move a rule. This does the same for a daily VN stock bot:

- ``record_screen`` (the 15:30 screen; the trading run's decisions with ``kind="trade"``):
  one row per stock and session in the store's ``signals`` (vnbot.store): action,
  confidence, setup, the first gate it failed
  (policy.BUY_GATES), every gate it failed, and the decision it would get without the
  gate that can be lifted: VNINDEX not risk-off, or the breakout switch on (engine.lifted);
  one ``scans`` row with the funnel; one ``market`` row with VNINDEX's regime and the MA
  filters of the defensive and momentum books.
- ``resolve``: once later sessions exist, each row gets its shadow result: the return
  5/10/20 sessions on from the next session's open (VNINDEX beside it), and, for a BUY
  (or a BUY blocked only by the market, or only by the breakout switch), whether its plan
  would have hit the stop or the target first, in R after costs, with the T+2 settlement
  and the next-session limit order of the paper book.
- ``stats``: the groups compared (the real BUYs, the BUYs blocked by the market, the
  breakouts switched off, the other near misses, by gate), with the t statistic: a group
  says something only with enough rows and |t| >= 2 (the OKX bot learned that the hard way).

Nothing here changes a decision, an order or a level: these are measurements for the
owner to decide on. ``/vikhongmua`` (Telegram, Zoom) and the page ``/tinhieu`` show them.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from collections import Counter
from datetime import date, timedelta

from vnbot import store as store_mod

logger = logging.getLogger(__name__)

HORIZONS = (5, 10, 20)          # sessions after the decision day
NEAR_CONFIDENCE = 0.05          # confidence this close under the BUY threshold is a near miss
NEAR_RR = 0.3                   # R:R this close under the minimum is a near miss
MIN_SAMPLES = 30                # rows a group needs before its result is read as a result
MIN_T = 2.0                     # |t| a group needs to differ from zero
GIVE_UP_DAYS = 60               # calendar days after which a row without bars is NODATA
STATS_DAYS = 400                # the statistics read this many calendar days back
REPLAY_MAX = 250                # sessions a replay may rebuild
SOURCE_RANK = {"screen": 0, "replay": 1, "trade": 2}   # which row of a (day, symbol) the stats keep

ACTION_VI = {"BUY": "MUA", "HOLD": "GIỮ", "WATCH": "THEO DÕI", "SELL": "BÁN", "NO_TRADE": "ĐỨNG NGOÀI"}
NEAR_VI = {
    "market": "chỉ vướng thị trường",
    "breakout": "MUA breakout đang tắt",
    "confidence": "độ tin cậy sát ngưỡng",
    "rr": "R:R sát ngưỡng",
    "resistance": "phá đỉnh, chưa đo được R:R",
}
REGIME_VI = {"risk_on": "RISK-ON (thuận lợi)", "neutral": "trung tính", "risk_off": "RISK-OFF (xấu)",
             "unknown": "chưa xác định"}
ALIGN_VI = {"bullish": "cùng tăng", "bearish": "cùng giảm", "mixed": "lẫn lộn"}
BIAS_VI = {"bullish": "tăng", "bearish": "giảm", "conflict": "mâu thuẫn", "flat": "chưa rõ"}
KIND_VI = {"screen": "sàng lọc 15:30", "trade": "bot giao dịch", "replay": "dựng lại"}
GROUPS = (
    ("buy", "MUA thật của Quant Engine"),
    ("market", "MUA bị chặn chỉ vì thị trường"),
    ("breakout", "MUA breakout bị tắt (breakout_buy = 0)"),
    ("near", "Suýt MUA khác (tin cậy/R:R sát ngưỡng, phá đỉnh)"),
    ("bullish", "Mọi mã xu hướng tăng"),
    ("bearish", "Mọi mã xu hướng giảm"),
    ("all", "Mọi mã đã xét"),
)


def _gate_vi(gate: str) -> str:
    from tradingagents.quant.policy import BUY_GATES

    return BUY_GATES.get(gate, gate or "đạt mọi cổng")


def _pct(value, signed: bool = True, digits: int = 1) -> str:
    if value is None:
        return "chưa có"
    text = f"{value:+.{digits}f}%" if signed else f"{value:.{digits}f}%"
    return text.replace(".", ",")


def _num(value, digits: int = 2) -> str:
    """1761.87 -> "1.761,87" (Vietnamese separators)."""
    if value is None:
        return "chưa có"
    return f"{value:,.{digits}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _price(value) -> str:
    return "chưa có" if not value else f"{value:,.0f}".replace(",", ".")


# ── one decision -> one row ──
def _lifted_kind(row: dict) -> str:
    """Which gate the row's alternative decision (alt_*) lifted: the breakout switch is
    checked last, so a row stopped by it passed the market gate."""
    return "breakout" if row.get("gate") == "breakout" else "market"


def near_kind(row: dict, params=None) -> str:
    """Why a stock that is not a BUY is close to one ("" = not close): ``market`` when only
    the market gate stopped it, ``breakout`` when only the breakout switch did, else the
    gate it failed with the market lifted, when that gate is a threshold it nearly met
    (or a breakout with nothing to measure R:R on)."""
    if row.get("action") == "BUY":
        return ""
    if row.get("alt_action") == "BUY":
        return _lifted_kind(row)
    if params is None:
        from tradingagents.quant.params import active

        params = active()
    lifted = row.get("alt_action") is not None
    gate = row.get("alt_gate") if lifted else row.get("gate")
    confidence = row.get("alt_confidence") if lifted else row.get("confidence")
    rr = row.get("alt_rr") if lifted else row.get("rr")
    if gate == "confidence" and confidence is not None and confidence >= params.confidence_buy_min - NEAR_CONFIDENCE:
        return "confidence"
    if gate == "rr" and rr is not None and rr >= params.min_rr_ratio - NEAR_RR:
        return "rr"
    if gate == "resistance":
        return "resistance"
    return ""


def _row(day: str, symbol: str, source: str, alt: dict | None, reasons, params, **fields) -> dict:
    alt = alt or {}
    row = {"day": str(day)[:10], "symbol": symbol, "source": source, **fields,
           "alt_action": alt.get("action"), "alt_confidence": alt.get("confidence"), "alt_gate": alt.get("gate"),
           "alt_stop": alt.get("stop"), "alt_target": alt.get("target"), "alt_entry_high": alt.get("entry_high"),
           "alt_rr": alt.get("rr"), "reasons": json.dumps(list(reasons or [])[:8], ensure_ascii=False)}
    row["near"] = near_kind(row, params)
    return row


def signal_row(result, source: str, params=None) -> dict:
    """The ``signals`` row of an engine.QuantResult (keyed by the session it decided on)."""
    return _row(
        result.last_bar_date or result.as_of_date, result.symbol, source, getattr(result, "if_lifted", None),
        result.reasons, params,
        action=result.action, confidence=result.confidence, setup=result.setup_type,
        bias=getattr(result, "bias", "") or "", regime=result.market_regime, risk=result.risk_level,
        quality=result.data_quality, gate=getattr(result, "gate", "") or "",
        gates=",".join(getattr(result, "gates", None) or []), price=result.price, entry_low=result.entry_low,
        entry_high=result.entry_high, stop=result.stop_price, target=result.target_price, target2=result.target2,
        rr=result.rr_ratio, holding=bool(result.is_holding))


def decision_row(symbol: str, day: str, inputs, decision, alt: dict | None, source: str, params=None) -> dict:
    """The same row from the policy's own objects (the replay of past sessions)."""
    plan = decision.trade_plan
    return _row(
        day, symbol, source, alt, decision.reasons, params,
        action=decision.action, confidence=decision.confidence, setup=decision.setup_type, bias=decision.bias,
        regime=decision.market_regime, risk=decision.risk_level, quality=decision.data_quality, gate=decision.gate,
        gates=",".join(decision.gates), price=inputs.price, entry_low=plan.entry_low if plan else None,
        entry_high=plan.entry_high if plan else None, stop=decision.stop_price, target=decision.target_price,
        target2=plan.target2 if plan else None, rr=decision.rr_ratio, holding=bool(inputs.is_holding))


def _near_label(row: dict, params=None) -> str:
    if params is None:
        from tradingagents.quant.params import active

        params = active()
    kind = row.get("near") or ""
    lifted = row.get("alt_action") is not None
    if kind in ("market", "breakout"):
        bits = [f"tin cậy {_num(row.get('alt_confidence'))}"]
        if row.get("alt_rr") is not None:
            bits.append(f"R:R {_num(row.get('alt_rr'))}")
    elif kind == "rr":
        bits = [f"R:R {_num(row.get('alt_rr') if lifted else row.get('rr'))}, cần {_num(params.min_rr_ratio, 1)}"]
    else:
        bits = [f"tin cậy {_num(row.get('alt_confidence') if lifted else row.get('confidence'))}, "
                f"cần {_num(params.confidence_buy_min)}"]
    return f"{row['symbol']} ({NEAR_VI.get(kind, kind)}; {', '.join(bits)})"


def scan_summary(rows: list[dict], *, day: str, kind: str, errors: list[str] | None = None, params=None) -> dict:
    """The ``scans`` row of one screen: how many stocks, actions, first gates, near misses."""
    actions = Counter(r["action"] for r in rows)
    gates = Counter(r["gate"] or "BUY" for r in rows if not r.get("holding"))
    regimes = Counter(r["regime"] for r in rows if r.get("regime") and r["regime"] != "unknown")
    order = {"market": 0, "breakout": 1, "confidence": 2, "rr": 3, "resistance": 4}
    near = sorted((r for r in rows if r.get("near")),
                  key=lambda r: (order.get(r["near"], 9), -(r.get("alt_confidence") or r.get("confidence") or 0)))
    buys = [{"symbol": r["symbol"], "entry_high": r.get("entry_high"), "stop": r.get("stop"),
             "target": r.get("target"), "confidence": r.get("confidence")} for r in rows if r["action"] == "BUY"]
    return {
        "day": day, "kind": kind, "scanned": len(rows), "buy": actions.get("BUY", 0), "near": len(near),
        "regime": regimes.most_common(1)[0][0] if regimes else "unknown",
        "actions": dict(actions.most_common()), "gates": dict(gates.most_common()),
        "near_list": [_near_label(r, params) for r in near[:15]], "buys": buys, "errors": list(errors or [])[:40],
    }


# ── VNINDEX ──
def _sma(values: list[float], n: int) -> float | None:
    return sum(values[-n:]) / n if len(values) >= n else None


def market_row(day: str, frame, params=None) -> dict:
    """VNINDEX on ``day`` as the engine reads it (price only: the money-flow notch the engine
    may add is in the screen's reasons), and the MA filters of Phòng thủ / Động lượng."""
    from tradingagents.quant import features as feat, policy

    if params is None:
        from tradingagents.quant.params import active

        params = active()
    frame = frame[frame["Date"] <= day].tail(260)
    closes = [float(c) for c in frame["Close"]]
    highs = [float(c) for c in frame["High"]]
    lows = [float(c) for c in frame["Low"]]
    volumes = [float(c) for c in frame["Volume"]]
    mtf = feat.calc_multi_timeframe(closes)
    adx = feat.calc_adx(closes, highs, lows)
    events = feat.distribution_events(closes, volumes)
    dist = sum(1 for change, rally in events if change <= -params.dist_min_drop_pct
               and not (params.dist_expiry_rally_pct and rally >= params.dist_expiry_rally_pct))
    regime = policy.classify_market_regime(mtf, adx, dist, params.distribution_day_threshold)
    adx_down = bool(adx.available and adx.trending and adx.di_minus > adx.di_plus)
    why = []
    if dist >= params.distribution_day_threshold:
        why.append(f"{dist} ngày phân phối trong 25 phiên (ngưỡng {params.distribution_day_threshold})")
    if mtf.alignment == "bearish":
        why.append("xu hướng 1 tuần, 1 tháng, 3 tháng đều giảm")
    if adx_down:
        why.append(f"ADX {adx.adx:.1f} xác nhận xu hướng giảm (DI- {adx.di_minus:.1f} > DI+ {adx.di_plus:.1f})".replace(".", ","))
    close = closes[-1] if closes else None
    sma = {n: _sma(closes, n) for n in (20, 50, 100, 200)}
    fb_ma, mom_ma = _sma(closes, int(params.fb_market_ma)), _sma(closes, int(params.mom_market_ma))
    return {
        "day": day, "close": close, "sma20": sma[20], "sma50": sma[50], "sma100": sma[100], "sma200": sma[200],
        "alignment": mtf.alignment, "adx": adx.adx if adx.available else None,
        "di_plus": adx.di_plus if adx.available else None, "di_minus": adx.di_minus if adx.available else None,
        "adx_down": adx_down, "dist_days": dist, "dist_threshold": int(params.distribution_day_threshold),
        "regime": regime, "why": "; ".join(why),
        "factor_on": bool(close and fb_ma and close > fb_ma), "momentum_on": bool(close and mom_ma and close > mom_ma),
    }


def _vnindex(day: str):
    from tradingagents.dataflows.ohlcv import load_ohlcv
    from tradingagents.market import vn_rules

    return load_ohlcv(vn_rules.BENCHMARK, day)


def record_market(day: str, *, store=None, regime: str | None = None, flow_note: str = "",
                  factor_on: bool | None = None, momentum_on: bool | None = None, frame=None) -> dict | None:
    """Write the ``market`` row of ``day``. ``regime``: the engine's own (money flows
    included) when known; ``factor_on``/``momentum_on``: the books' own filter state
    (Phòng thủ checks it at the week's end) when known."""
    store = store or store_mod.current()
    if store is None:
        return None
    row = market_row(day, frame if frame is not None else _vnindex(day))
    # What a run knows better than the closes is kept until a run that knows it again:
    # the engine's regime (money flows included) and the books' own filter state.
    stored = next(iter(store.select("SELECT regime, why, factor_on, momentum_on FROM market WHERE day = ?", (day,))), {})
    if regime and regime != row["regime"]:
        row["why"] = "; ".join(x for x in (row["why"], flow_note or "dòng tiền khối ngoại/độ rộng hạ một bậc") if x)
        row["regime"] = regime
    elif not regime and stored.get("regime"):
        row["regime"], row["why"] = stored["regime"], stored.get("why") or row["why"]
    for key, value in (("factor_on", factor_on), ("momentum_on", momentum_on)):
        if value is not None:
            row[key] = bool(value)
        elif stored.get(key) is not None:
            row[key] = bool(stored[key])
    store.add_market(row)
    return row


def _flow_note(results) -> str:
    """The engine's money-flow reasons for lowering the regime (the same for every stock)."""
    for result in results:
        notes = [r for r in (result.reasons or []) if "hạ regime" in r]
        if notes:
            return "; ".join(notes)
    return ""


# ── recording ──
def record_screen(results: dict, errors: list[str] | None = None, *, kind: str = "screen", store=None) -> dict | None:
    """Store the decisions of one screen (``{code: QuantResult}``) and its funnel; returns
    the ``scans`` row (None when there is no store or nothing to store)."""
    store = store or store_mod.current()
    if store is None or not results:
        return None
    rows = [signal_row(r, kind) for r in results.values()]
    store.add_signals(rows)
    day = Counter(r["day"] for r in rows).most_common(1)[0][0]
    scan = scan_summary([r for r in rows if r["day"] == day], day=day, kind=kind, errors=errors)
    store.add_scan(scan)
    regime = scan["regime"] if scan["regime"] != "unknown" else None
    store_mod.safe("market row", lambda: record_market(day, store=store, regime=regime,
                                                       flow_note=_flow_note(results.values())))
    return scan


def record_books(day: str, books: dict, *, store=None, factor_on=None, momentum_on=None, regime=None) -> None:
    """NAV per session and fills of each book (``{name: Ledger}``); the market row of ``day``
    with the books' own filter state."""
    store = store or store_mod.current()
    if store is None:
        return
    first = not store.select("SELECT 1 AS x FROM equity LIMIT 1")
    for name, book in books.items():
        if book is None:
            continue
        history = list(book.history if first else book.history[-25:])
        rows = [{"day": h["date"], "book": name, "nav": h.get("nav"), "benchmark": h.get("benchmark")} for h in history]
        if rows and rows[-1]["day"] == day:
            nav = book.nav()
            rows[-1].update(cash=book.cash, positions=len(book.positions),
                            invested_pct=round((nav - book.cash) / nav * 100, 2) if nav else None)
        store.add_equity(rows)
        fills = list(book.fills if first else book.fills[-60:])
        store.add_fills([{"book": name, "day": f.date, "symbol": f.symbol, "side": f.side, "qty": f.qty,
                          "price": f.price, "cost": f.cost, "pnl": f.pnl, "reason": f.reason} for f in fills])
    store_mod.safe("market row", lambda: record_market(day, store=store, regime=regime, factor_on=factor_on,
                                                       momentum_on=momentum_on))


# ── shadow results ──
def _bars(frame):
    dates = [d.strftime("%Y-%m-%d") for d in frame["Date"]]
    cols = [[float(v) for v in frame[c]] for c in ("Open", "High", "Low", "Close")]
    return dates, *cols


def _ok(value) -> bool:
    return value is not None and math.isfinite(value) and value > 0


def simulate_plan(opens, highs, lows, closes, first: int, entry_high: float, stop: float, target: float,
                  *, max_hold: int = 30, touch: bool = False) -> tuple[str, float | None, int] | None:
    """The plan of a BUY decided the session before ``first`` (index of the next session),
    traded like the paper book: a limit at ``entry_high`` for that session only (filled when
    the low trades through it, at the open if lower), sellable from T+2, then the first of
    stop (a gap under it sells at the open) or target (stop first when one session spans
    both), else the close after ``max_hold`` sessions. R is the planned risk (``entry_high`` -
    ``stop``, what the paper book sizes a position on), costs taken off. Returns (TP | SL |
    TIMEOUT | NOFILL, R, sessions held), or None while the sessions it needs are not there yet."""
    from tradingagents.market import vn_rules

    if first >= len(closes):
        return None
    if not (_ok(entry_high) and _ok(stop) and _ok(target)) or stop >= entry_high:
        return "NODATA", None, 0
    low, open_ = lows[first], opens[first]
    if not (low < entry_high or (touch and low <= entry_high)):
        return "NOFILL", None, 0
    fill = open_ if _ok(open_) and open_ < entry_high else entry_high
    risk = entry_high - stop
    cost = fill * vn_rules.round_trip_cost_pct() / 100

    def r_of(exit_price: float) -> float:
        return round((exit_price - fill - cost) / risk, 3)

    last = first + max_hold
    for j in range(first + 2, min(last, len(closes) - 1) + 1):
        o, h, lo = opens[j], highs[j], lows[j]
        if _ok(o) and o <= stop:
            return "SL", r_of(o), j - first
        if _ok(lo) and lo <= stop:
            return "SL", r_of(stop), j - first
        if _ok(o) and o >= target:
            return "TP", r_of(o), j - first
        if _ok(h) and h >= target:
            return "TP", r_of(target), j - first
        if j == last:
            return "TIMEOUT", r_of(closes[j]), j - first
    return None


def _plan_of(row: dict) -> tuple[str, float, float, float] | None:
    if row.get("action") == "BUY" and row.get("entry_high") and row.get("stop") and row.get("target"):
        return "engine", row["entry_high"], row["stop"], row["target"]
    if row.get("alt_action") == "BUY" and row.get("alt_entry_high") and row.get("alt_stop") and row.get("alt_target"):
        return _lifted_kind(row), row["alt_entry_high"], row["alt_stop"], row["alt_target"]
    return None


def outcome(row: dict, stock, vni, *, today: str, params=None) -> dict | None:
    """The shadow-result fields of ``row`` from the stock's and VNINDEX's bars after its day
    (``(dates, opens, highs, lows, closes)`` each); None when nothing new can be said yet."""
    dates, opens, highs, lows, closes = stock
    day = row["day"]
    first = next((i for i, d in enumerate(dates) if d > day), None)
    if first is None:
        gone = date.fromisoformat(today) - date.fromisoformat(day) > timedelta(days=GIVE_UP_DAYS)
        return {"status": "NODATA"} if gone else None
    entry = opens[first] if _ok(opens[first]) else closes[first]
    if not _ok(entry):
        return {"status": "NODATA"}
    out: dict = {"entry_px": entry}
    v_dates, v_opens, _, _, v_closes = vni
    v_first = next((i for i, d in enumerate(v_dates) if d > day), None)
    v_entry = None
    if v_first is not None:
        v_entry = v_opens[v_first] if _ok(v_opens[v_first]) else (v_closes[v_first - 1] if v_first else None)
    for k in HORIZONS:
        i = first + k - 1
        if i < len(closes) and _ok(closes[i]):
            out[f"ret_{k}"] = round((closes[i] / entry - 1) * 100, 3)
        if v_first is not None and _ok(v_entry) and v_first + k - 1 < len(v_closes):
            out[f"vni_{k}"] = round((v_closes[v_first + k - 1] / v_entry - 1) * 100, 3)
    plan = _plan_of(row)
    plan_done = True
    if plan is not None:
        if params is None:
            from tradingagents.quant.params import active

            params = active()
        kind, entry_high, stop, target = plan
        sim = simulate_plan(opens, highs, lows, closes, first, entry_high, stop, target,
                            max_hold=int(params.max_hold_sessions), touch=bool(params.limit_fill_touch))
        out["plan"] = kind
        if sim is None:
            plan_done = False
        else:
            out["plan_outcome"], out["plan_r"], out["plan_days"] = sim
    done = plan_done and f"ret_{HORIZONS[-1]}" in out
    out["status"] = "DONE" if done else "PENDING"
    return out


def resolve(today: str, *, store=None, frames=None, chunk: int = 5000) -> int:
    """Fill the shadow results of the rows older than ``today``, ``chunk`` rows at a time;
    returns how many rows changed. ``frames(symbol)``: the bars of a symbol up to today
    (default: the price cache)."""
    store = store or store_mod.current()
    if store is None:
        return 0
    changed, after = 0, 0
    while True:
        rows = store.pending_signals(today, chunk, after_id=after)
        if not rows:
            break
        after = rows[-1]["id"]
        changed += _resolve_rows(rows, today, store, frames)
        if len(rows) < chunk:
            break
    if changed:
        logger.info("Shadow results: %d signal rows updated", changed)
    return changed


def _resolve_rows(rows: list[dict], today: str, store, frames) -> int:
    if frames is None:
        from tradingagents.dataflows.ohlcv import load_ohlcv

        def frames(symbol):
            return load_ohlcv(symbol, today)
    from tradingagents.market import vn_rules

    try:
        vni = _bars(frames(vn_rules.BENCHMARK))
    except Exception as exc:  # noqa: BLE001 - tried again on the next run
        logger.warning("Shadow results: VNINDEX unavailable (%s)", exc)
        return 0
    by_symbol: dict[str, list[dict]] = {}
    for row in rows:
        by_symbol.setdefault(row["symbol"], []).append(row)
    updates: list[tuple[int, dict]] = []
    for symbol, items in by_symbol.items():
        try:
            bars = _bars(frames(symbol))
        except Exception as exc:  # noqa: BLE001 - one symbol's source must not stop the others
            logger.info("Shadow results: %s unavailable (%s)", symbol, exc)
            continue
        for row in items:
            out = outcome(row, bars, vni, today=today)
            if out is not None:
                out["resolved_ts"] = time.time()
                updates.append((row["id"], out))
    for start in range(0, len(updates), 1000):
        store.update_many("signals", updates[start:start + 1000])
    return len(updates)


# ── statistics ──
def t_stat(values: list[float]) -> tuple[float, float]:
    """(standard error, mean / standard error); t is clamped to ±99 (a constant sample)."""
    n = len(values)
    if n < 2:
        return 0.0, 0.0
    mean = sum(values) / n
    var = sum((x - mean) ** 2 for x in values) / (n - 1)
    se = math.sqrt(var / n)
    if se <= 1e-12:
        return 0.0, 99.0 if mean > 0 else -99.0 if mean < 0 else 0.0
    return se, max(-99.0, min(99.0, mean / se))


def aggregate(values: list[float]) -> dict:
    values = [v for v in values if v is not None]
    n = len(values)
    if not n:
        return {"n": 0, "mean": None, "win": None, "t": 0.0}
    _, t = t_stat(values)
    return {"n": n, "mean": round(sum(values) / n, 3), "win": round(100 * sum(1 for v in values if v > 0) / n, 1),
            "t": round(t, 2)}


def dedupe(rows: list[dict]) -> list[dict]:
    """One row per (day, symbol): the screen's (a stock judged as a new position) first."""
    best: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (row["day"], row["symbol"])
        if key not in best or SOURCE_RANK.get(row.get("source"), 9) < SOURCE_RANK.get(best[key].get("source"), 9):
            best[key] = row
    return list(best.values())


def _member(group: str, row: dict) -> bool:
    if group == "buy":
        return row.get("action") == "BUY"
    if group in ("market", "breakout"):
        return row.get("near") == group
    if group == "near":
        return row.get("near") in ("confidence", "rr", "resistance")
    if group in ("bullish", "bearish"):
        return row.get("bias") == group
    return True


def stats(rows: list[dict], horizon: int = 10) -> dict:
    """The groups of ``GROUPS`` and every first gate: return after ``horizon`` sessions, the
    same less VNINDEX's, and for the planned ones the plan's R after costs."""
    rows = dedupe([r for r in rows if not r.get("holding")])
    key = f"ret_{horizon}"
    resolved = [r for r in rows if r.get(key) is not None]

    def block(sub: list[dict]) -> dict:
        excess = [r[key] - r[f"vni_{horizon}"] for r in sub if r.get(f"vni_{horizon}") is not None]
        plans = [r for r in sub if r.get("plan_outcome") in ("TP", "SL", "TIMEOUT")]
        return {"ret": aggregate([r[key] for r in sub]), "excess": aggregate(excess),
                "plan": aggregate([r.get("plan_r") for r in plans]),
                "nofill": sum(1 for r in sub if r.get("plan_outcome") == "NOFILL")}

    groups = {name: {"label": label, **block([r for r in resolved if _member(name, r)])} for name, label in GROUPS}
    gates = {}
    for gate in sorted({r.get("gate") or "BUY" for r in resolved}):
        gates[gate] = {"label": "MUA" if gate == "BUY" else _gate_vi(gate),
                       **block([r for r in resolved if (r.get("gate") or "BUY") == gate])}
    days = sorted({r["day"] for r in resolved})
    return {"horizon": horizon, "rows": len(rows), "resolved": len(resolved), "pending": len(rows) - len(resolved),
            "from": days[0] if days else None, "to": days[-1] if days else None, "groups": groups, "gates": gates}


def verdict(agg: dict) -> str:
    """What a group's numbers allow saying, in words."""
    if agg["n"] < MIN_SAMPLES:
        return f"mới {agg['n']} mẫu, cần ≥ {MIN_SAMPLES} để kết luận"
    if abs(agg["t"]) < MIN_T:
        return f"chưa khác 0 có ý nghĩa (t = {_num(agg['t'])})"
    return ("tốt hơn rõ rệt" if agg["mean"] > 0 else "kém hơn rõ rệt") + f" (t = {_num(agg['t'])})"


# ── text ──
def funnel_line(scan: dict | None, market: dict | None = None) -> str:
    """One paragraph for the daily screen message: why no BUY, the near misses."""
    if not scan:
        return ""
    gates = scan.get("gates") or {}
    order = [g for g, _ in sorted(gates.items(), key=lambda x: -x[1]) if g != "BUY"]
    parts = [f"{_gate_vi(g)} {gates[g]}" for g in order[:5]]
    head = f"🔎 Vì sao {'chỉ có ' + str(scan['buy']) + ' ' if scan.get('buy') else 'chưa có '}MUA: "
    regime = scan.get("regime")
    if regime in ("risk_off", "unknown"):
        why = (market or {}).get("why") or ""
        head += f"thị trường {REGIME_VI.get(regime, regime)}{(' - ' + why) if why else ''}. "
    lines = [head + "Cổng chặn đầu tiên: " + (" · ".join(parts) or "không có") + "."]
    if scan.get("near_list"):
        lines.append("Suýt MUA: " + ", ".join(scan["near_list"][:6]) + ".")
    lines.append("Chi tiết và kết quả bóng: /vikhongmua")
    return "\n".join(lines)


def _market_lines(market: dict | None, glyph: bool = True) -> list[str]:
    mark = "🌡️ " if glyph else ""
    if not market:
        return [f"{mark}Thị trường: chưa có số liệu."]
    close = market.get("close")
    lines = [f"{mark}VNINDEX {_num(close)} phiên {market['day']}: regime {REGIME_VI.get(market.get('regime'), market.get('regime'))}"
             + (" → Quant Engine không mở MUA mới" if market.get("regime") in ("risk_off", "unknown") else "")]
    if market.get("why"):
        lines.append(f"   • {market['why']}")
    for name, ma, on in (("Phòng thủ", "sma200", market.get("factor_on")), ("Động lượng", "sma100", market.get("momentum_on"))):
        level = market.get(ma)
        gap = (close / level - 1) * 100 if close and level else None
        state = "trên" if on else "dưới"
        tail = "được đầu tư" if on else "giữ tiền mặt, không mua mới"
        lines.append(f"   • {name}: VNINDEX {state} MA{ma[3:]} ({_num(level)}, {_pct(gap)}) → {tail}")
    return lines


PLAN_GROUPS = ("buy", "market", "breakout")      # the groups with a plan (stop, target) to follow
BASELINES = ("bullish", "bearish", "all")   # context, not a decision: no verdict


def _group_line(name: str, block: dict, horizon: int) -> str:
    ret, excess, plan = block["ret"], block["excess"], block["plan"]
    label = block["label"]
    if not ret["n"]:
        return f"• {label}: chưa có kết quả"
    text = (f"• {label}: {ret['n']} mã-phiên, sau {horizon} phiên TB {_pct(ret['mean'])}, thắng {_pct(ret['win'], False, 0)}, "
            f"so VNINDEX {_pct(excess['mean'])}")
    text += " (mốc so sánh)" if name in BASELINES else f" → {verdict(excess)}"
    if name in PLAN_GROUPS and (plan["n"] or block["nofill"]):
        text += f"; theo kế hoạch {plan['n']} lệnh khớp, TB {_num(plan['mean'])}R" + (
            f", {block['nofill']} lệnh không khớp" if block["nofill"] else "")
    return text


def breakout_note(params=None) -> str:
    """One line while the breakout switch is off (``Params.breakout_buy`` = 0), else ""."""
    if params is None:
        from tradingagents.quant.params import active

        params = active()
    if params.breakout_buy:
        return ""
    return ("MUA breakout của Quant Engine đang tắt (trang Bot giao dịch): mã breakout chỉ được theo dõi, "
            "kết quả bóng của chúng ở nhóm 'MUA breakout bị tắt'.")


def latest_scan(store) -> dict | None:
    """The newest session's scan: the live screen first, then the trading run, then a replay."""
    scans = store.scans(limit=12)
    if not scans:
        return None
    day = max(s["day"] for s in scans)
    rank = {"screen": 0, "trade": 1, "replay": 2}
    return min((s for s in scans if s["day"] == day), key=lambda s: rank.get(s["kind"], 9))


def why_text(store=None, *, horizon: int = 10) -> str:
    """``/vikhongmua``: the last screen's funnel, the market, the near misses, the shadow results."""
    store = store or store_mod.current()
    if store is None:
        return ("Kho dữ liệu đang tắt (STORE=0) hoặc không mở được: bot chưa ghi lại quyết định từng mã. "
                "Xem log khởi động dòng 'Store:'.")
    scan = latest_scan(store)
    if scan is None:
        return ("Chưa có lượt quét nào được ghi lại. Bot ghi từ lượt sàng lọc kế tiếp (DAILY_REPORT_TIME) "
                "hoặc lượt bot giao dịch; muốn có ngay lịch sử thì dựng lại trên trang Dữ liệu (/dulieu) "
                "hoặc gõ /vikhongmua dunglai.")
    market = next(iter(store.market(limit=1)), None)
    actions = " · ".join(f"{ACTION_VI.get(a, a)} {n}" for a, n in (scan.get("actions") or {}).items())
    lines = [f"🔎 Vì sao chưa có MUA - phiên {scan['day']} ({KIND_VI.get(scan['kind'], scan['kind'])}, {scan['scanned']} mã)",
             f"Kết quả: {actions or 'chưa có'}"]
    if scan.get("buys"):
        lines.append("MUA: " + ", ".join(f"{b['symbol']} (vùng mua tới {_price(b.get('entry_high'))}, cắt lỗ "
                                         f"{_price(b.get('stop'))}, mục tiêu {_price(b.get('target'))})" for b in scan["buys"]))
    lines += [""] + _market_lines(market)
    if breakout_note():
        lines.append("⚙️ " + breakout_note())
    gates = scan.get("gates") or {}
    if gates:
        lines += ["", "🧱 Cổng chặn MUA (mỗi mã tính ở cổng đầu tiên nó trượt):"]
        lines += [f"• {_gate_vi(g) if g != 'BUY' else 'đạt mọi cổng (MUA)'}: {n}" for g, n in gates.items()]
    if scan.get("near_list"):
        lines += ["", f"🎯 Suýt MUA ({scan.get('near', len(scan['near_list']))}):"] + [f"• {x}" for x in scan["near_list"][:10]]
    st = stats(store.outcome_rows(stats_since()), horizon)
    lines += ["", f"📏 Kết quả bóng (mua giá mở cửa phiên sau, giá {horizon} phiên sau; "
                  f"{st['resolved']} mã-phiên có kết quả, {st['pending']} đang chờ):"]
    if st["resolved"]:
        lines.append(f"Từ {st['from']} tới {st['to']}.")
        for name in ("buy", "market", "breakout", "near", "bullish", "all"):
            if name != "breakout" or st["groups"][name]["ret"]["n"]:     # only once the switch was used
                lines.append(_group_line(name, st["groups"][name], horizon))
    else:
        lines.append("Chưa có: cần ít nhất 10 phiên sau ngày ghi đầu tiên (hoặc dựng lại lịch sử trên /dulieu).")
    lines += ["", "Bot không tự nới cổng nào vì các số này; chúng để anh quyết định. Trang web: /tinhieu."]
    return "\n".join(lines)


def stats_since(today: str | None = None) -> str:
    base = date.fromisoformat(today) if today else date.today()
    return (base - timedelta(days=STATS_DAYS)).isoformat()


# ── rebuilding the past (replay) ──
_replay_lock = threading.Lock()


def replay(symbols: list[str], sessions: int, *, as_of: str, store=None, flows: bool = False, params=None,
           today: str | None = None) -> dict:
    """Decide again every (stock, session) of the last ``sessions`` sessions up to ``as_of``
    with today's engine (rows with source ``replay``), then their shadow results: weeks of
    history on the first day instead of waiting for them. Same code as the live decision
    (engine.build_inputs, policy), but on prices adjusted as of today and today's basket
    (survivorship), without news; without ``flows`` the money-flow notch is left out."""
    from collections import defaultdict
    from dataclasses import replace

    from tradingagents.quant import engine, policy
    from tradingagents.trading import simulator as sim

    store = store or store_mod.current()
    if store is None:
        raise RuntimeError("kho dữ liệu đang tắt")
    if params is None:
        from tradingagents.quant.params import active

        params = active()
    sessions = max(5, min(int(sessions), REPLAY_MAX))
    ds = sim.build_dataset(list(dict.fromkeys(symbols)), sessions, as_of, flows=flows, inputs=True)
    rows = []
    for (symbol, day), inputs in ds.inputs.items():
        inputs = replace(inputs, params=params, is_holding=False)
        decision = policy.evaluate_policy(inputs)
        rows.append(decision_row(symbol, day, inputs, decision, engine.lifted(inputs, decision), "replay", params))
    store.add_signals(rows)
    by_day: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_day[row["day"]].append(row)
    for day, day_rows in by_day.items():
        store.add_scan(scan_summary(day_rows, day=day, kind="replay"))
    vni = _vnindex(as_of)
    have = set(store.days("market", limit=100_000))      # a live run's row knows more: kept
    for day in ds.dates:
        if day not in have:
            store_mod.safe("market row", lambda d=day: store.add_market(market_row(d, vni, params)))
    resolved = resolve(today or as_of, store=store) if (today or as_of) else 0
    return {"rows": len(rows), "days": len(ds.dates), "symbols": len(ds.symbols),
            "from": ds.dates[0] if ds.dates else None, "to": ds.dates[-1] if ds.dates else None,
            "skipped": sorted(ds.skipped), "resolved": resolved}


def replay_status(store=None) -> dict:
    store = store or store_mod.current()
    return (store.get("replay_status", {}) if store is not None else {}) or {}


def start_replay(symbols: list[str], sessions: int, *, as_of: str, today: str, store=None, notify=None,
                 flows: bool = False) -> str:
    """Run ``replay`` on a background thread (one at a time); the status is kept in the store."""
    store = store or store_mod.current()
    if store is None:
        return "Kho dữ liệu đang tắt (STORE=0) hoặc không mở được."
    if not _replay_lock.acquire(blocking=False):
        return "Đang dựng lại lịch sử, chờ lượt đang chạy xong."
    sessions = max(5, min(int(sessions), REPLAY_MAX))
    store.set("replay_status", {"state": "running", "started": time.time(), "sessions": sessions,
                                "symbols": len(symbols), "as_of": as_of})

    def work():
        try:
            out = replay(symbols, sessions, as_of=as_of, store=store, flows=flows, today=today)
            store.set("replay_status", {"state": "done", "finished": time.time(), "sessions": sessions, **out})
            msg = (f"✅ Đã dựng lại {out['days']} phiên ({out['from']} - {out['to']}) cho {out['symbols']} mã: "
                   f"{out['rows']} quyết định, {out['resolved']} có kết quả bóng. Xem /vikhongmua hoặc trang /tinhieu.")
        except Exception as exc:  # noqa: BLE001 - reported, the bot carries on
            logger.exception("Replay failed")
            error = f"{type(exc).__name__}: {exc}"[:300]
            store_mod.safe("replay status", lambda: store.set(
                "replay_status", {"state": "failed", "finished": time.time(), "error": error}))
            msg = f"⚠️ Dựng lại lịch sử lỗi: {error[:200]}"
        finally:
            _replay_lock.release()
        if notify:
            try:
                notify(msg)
            except Exception:  # noqa: BLE001
                logger.warning("Replay: the notice could not be sent")

    threading.Thread(target=work, name="chandoan-replay", daemon=True).start()
    return (f"Đang dựng lại {sessions} phiên cho {len(symbols)} mã (vài phút; trang /dulieu ghi trạng thái, "
            "bot nhắn khi xong).")


def replay_symbols(universe: str, watchlist: list[str]) -> list[str]:
    """VN30 (or VN100 + VN30) plus the watchlist."""
    from tradingagents.dataflows import money_flows as mf

    members = list(mf.index_members("VN30"))
    if universe == "vn100":
        members = list(mf.index_members("VN100")) + members
    return list(dict.fromkeys(members + [s.upper() for s in watchlist]))


def symbol_text(symbol: str, store=None, sessions: int = 10) -> str:
    """``/vikhongmua MÃ``: the stock's decisions of its last recorded sessions and their shadow results."""
    store = store or store_mod.current()
    if store is None:
        return why_text(store)
    rows = dedupe(store.signals(symbol=symbol, limit=sessions * 3))
    rows = sorted(rows, key=lambda r: r["day"], reverse=True)[:sessions]
    if not rows:
        return f"Chưa có quyết định nào của {symbol} được ghi lại (mã ngoài rổ quét, hoặc kho dữ liệu mới bật)."
    lines = [f"🔎 {symbol}: {len(rows)} phiên gần nhất bot đã ghi"]
    for r in rows:
        bits = [ACTION_VI.get(r["action"], r["action"])]
        if r["action"] != "BUY":
            bits.append(f"cổng: {_gate_vi(r.get('gate') or '')}")
        bits.append(f"tin cậy {_num(r.get('confidence'))}")
        if r.get("near"):
            bits.append(f"suýt MUA: {NEAR_VI.get(r['near'], r['near'])}")
        if r.get("ret_10") is not None:
            bits.append(f"10 phiên sau {_pct(r['ret_10'])}")
        if r.get("plan_outcome"):
            bits.append(f"kế hoạch {r['plan_outcome']}" + (f" {_num(r.get('plan_r'))}R" if r.get("plan_r") is not None else ""))
        lines.append(f"• {r['day']}: " + " · ".join(bits))
    try:
        reasons = json.loads(rows[0].get("reasons") or "[]")
    except ValueError:
        reasons = []
    if reasons:
        lines += ["", f"Lý do phiên {rows[0]['day']}:"] + [f"- {x}" for x in reasons[:6]]
    lines.append("\nQuyết định đầy đủ hôm nay: /nhanh " + symbol)
    return "\n".join(lines)


def command_text(args: str, *, watchlist: list[str], notify=None, store=None) -> str:
    """``/vikhongmua`` [MÃ | dunglai [số phiên] [vn100]] for Telegram and Zoom."""
    from vnbot.trader import final_day, today_vn

    words = args.split()
    if not words:
        return why_text(store)
    if words[0].lower() in ("dunglai", "dựnglại", "replay"):
        sessions = next((int(w) for w in words[1:] if w.isdigit()), 120)
        universe = "vn100" if any(w.lower() == "vn100" for w in words[1:]) else "vn30"
        return start_replay(replay_symbols(universe, watchlist), sessions, as_of=final_day(), today=today_vn(),
                            store=store, notify=notify)
    from tradingagents.dataflows.symbols import normalize_symbol

    return symbol_text(normalize_symbol(words[0]), store)
