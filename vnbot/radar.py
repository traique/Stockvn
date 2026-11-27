"""Radar sáng: the morning report of the stocks at a technical inflection, every session day.

At ``RADAR_TIME`` (09:30, Vietnam) on a session day the scheduler scans VN100 plus the
watchlist on the previous session's closed candle - the signal is scored on yesterday's bar,
this morning's prices are only the context - and sends one report to the report chats and
Zoom (``DailyScheduler._radar``); at ``RADAR_NOON_TIME`` (11:30) the same again with the
morning session's trading as the context, the AI's lines of the morning reused
(``IntelCache``); ``/radar`` sends it now. Code only, except one block.

The score of a stock (0-100, every point explained; ``SCORE_MIN`` to be named):

- RSI(14) at or under ``RSI_OVERSOLD``: 20 (under ``RSI_LOW``: 10);
- a bullish divergence (a lower low in price, a higher RSI, ``divergence``): 20;
- stopping volume on the signal candle: it closed in the upper part of its range
  (``CLOSE_POS_MIN`` of the way from the low) on volume ``STOP_VOL_RATIO`` x its 20-session
  average: 20 (``SOFT_VOL_RATIO`` x: 10);
- it fell less (or rose more) than VN-Index that day: 10;
- average matched value over 20 sessions at least ``LIQUIDITY_MIN`` (checklist C): 10;
- close at or above EMA20: 10; at or above MA50: 10 (checklist G needs both).

"Tai mắt" (what the market knows about the company) is the one block the AI writes - level
2: the newest quarter's figures from VCI and the headlines of the last ``NEWS_DAYS`` days go
to one AI request per named stock, which answers three lines with their sources and whether
the news supports a buy (checklist E). When no provider answers, the budget is spent or the
answer is not readable, the block falls back to level 1: the same figures from VCI, read by
code (profit from other income flagged when it is over half of the net profit), and E is
left unscored, said so.

"Quân lệnh" (the orders) are rules: a probe of 5% NAV between 2% and 3% over the 30-session
support only if the close holds the support on volume ``STOP_VOL_RATIO`` x MA20, the stop 2%
under the support, the target EMA20 (the resistance when EMA20 is under the entry), R:R from
those; VN-Index selling hard (``VNI_HARD_PCT``) means no buy. An ex-date inside the window
(VCI events) is named with the amount, so a price gap is not read as a fall.

Nothing here is a trading decision of the bot: the Quant Engine and the paper books never
read this module.
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

TIME_DEFAULT = "09:30"         # RADAR_TIME
WINDOW_END = "11:30"           # a report not sent by then (the bot was down) is skipped for the day
SCORE_MIN = 70
RSI_OVERSOLD, RSI_LOW = 30.0, 35.0
STOP_VOL_RATIO, SOFT_VOL_RATIO = 1.5, 1.2
CLOSE_POS_MIN = 0.6            # the candle closed at least this far up its range (a "rút chân")
LIQUIDITY_MIN = 5e9            # VND matched a session, 20-session average (checklist C)
LEVEL_SESSIONS = 30            # support = the lowest low, resistance = the highest high, of these
DIVERGENCE_RECENT, DIVERGENCE_BACK = 5, 40
VNI_HARD_PCT = -1.5            # VN-Index down this much this morning: no buy, whatever the signal
ENTRY_LOW, ENTRY_HIGH, STOP_UNDER = 1.02, 1.03, 0.98
PROBE_PCT = 5                  # % of NAV of the probe
NEWS_DAYS = 14
NEWS_LIMIT = 12
NEWS_SHOWN = 5                 # headlines printed in the block
NOON_DEFAULT = "11:30"         # RADAR_NOON_TIME: the second send, at the end of the morning session
NOON_WINDOW_END = "13:00"
OTHER_INCOME_SHARE = 0.5       # other income over this share of the net profit: "not from the core business"
MAX_NAMED = 5                  # stocks written up in one report
NEAR_MISSES = 3                # the best under the threshold, named with their score only
WORKERS = 4

SYSTEM = (
    "Bạn là trợ lý đọc tin cho một nhà đầu tư cổ phiếu Việt Nam. Dưới đây là số liệu quý mới nhất của một doanh "
    "nghiệp (từ báo cáo tài chính) và các tiêu đề tin trong hai tuần. Hãy viết tối đa 3 dòng tiếng Việt, mỗi dòng "
    "một ý, nói lợi nhuận đến từ đâu (kinh doanh cốt lõi hay khoản khác, một lần), và có tin nào ủng hộ việc mua "
    "ngay không. Mỗi ý phải nêu nguồn và ngày trong ngoặc, chỉ từ số liệu và tin được đưa, không bịa. "
    "Trả lời DUY NHẤT một object JSON, không thêm chữ nào khác:\n"
    '{"lines": ["dòng 1", "dòng 2", "dòng 3"], "supportive": true hoặc false (tin có ủng hộ mua ngay không), '
    '"one_off": true hoặc false (lợi nhuận quý chủ yếu từ khoản một lần)}'
)
_JSON = re.compile(r"\{.*\}", re.S)


# ─── indicators ──────────────────────────────────────────────────────────────


def rsi_series(closes: list[float], period: int = 14) -> list[float | None]:
    """Wilder's RSI at every bar (None before ``period`` bars), the same arithmetic as
    ``features.calc_rsi`` on the full series."""
    out: list[float | None] = [None] * len(closes)
    if len(closes) < period + 1:
        return out
    gain = loss = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gain += max(d, 0)
        loss += max(-d, 0)
    gain /= period
    loss /= period
    out[period] = 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        gain = (gain * (period - 1) + max(d, 0)) / period
        loss = (loss * (period - 1) + max(-d, 0)) / period
        out[i] = 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
    return out


def divergence(closes: list[float], rsis: list[float | None]) -> bool:
    """A bullish divergence: the low of the last ``DIVERGENCE_RECENT`` bars is under the low of the
    ``DIVERGENCE_BACK`` bars before it, while the RSI at the new low is higher than at the old."""
    if len(closes) < DIVERGENCE_RECENT + 10:
        return False
    recent = range(len(closes) - DIVERGENCE_RECENT, len(closes))
    back = range(max(0, len(closes) - DIVERGENCE_RECENT - DIVERGENCE_BACK), len(closes) - DIVERGENCE_RECENT)
    a = min(recent, key=lambda i: closes[i])
    b = min(back, key=lambda i: closes[i])
    if rsis[a] is None or rsis[b] is None:
        return False
    return closes[a] < closes[b] * 0.995 and rsis[a] > rsis[b] + 1.0


def analyze(code: str, bars: list[dict], vni_pct: float | None) -> dict | None:
    """The signal of ``code`` on its last closed bar: every figure, the checks and the score.
    None when the history is too short. ``bars``: Date/Open/High/Low/Close/Volume, oldest first."""
    from tradingagents.quant import features as f

    if len(bars) < 60:
        return None
    closes = [float(b["Close"]) for b in bars]
    highs = [float(b["High"]) for b in bars]
    lows = [float(b["Low"]) for b in bars]
    volumes = [float(b["Volume"] or 0) for b in bars]
    last = bars[-1]
    rsis = rsi_series(closes)
    rsi = rsis[-1]
    ema20, ma50, ma100 = f.calc_ema(closes, 20), f.calc_sma(closes, 50), f.calc_sma(closes, 100)
    close = closes[-1]
    rng = highs[-1] - lows[-1]
    close_pos = (close - lows[-1]) / rng if rng > 0 else 0.5
    prior = volumes[-21:-1]
    vol_avg = sum(prior) / len(prior) if prior else 0.0
    vol_ratio = volumes[-1] / vol_avg if vol_avg > 0 else None
    values = [c * v for c, v in zip(closes[-20:], volumes[-20:], strict=True)]
    liquidity = sum(values) / len(values)
    support, resistance = min(lows[-LEVEL_SESSIONS:]), max(highs[-LEVEL_SESSIONS:])
    pct = (close / closes[-2] - 1) * 100 if closes[-2] else None
    div = divergence(closes, rsis)
    stopping = close_pos >= CLOSE_POS_MIN and vol_ratio is not None and vol_ratio >= STOP_VOL_RATIO
    soft_stop = close_pos >= CLOSE_POS_MIN and vol_ratio is not None and vol_ratio >= SOFT_VOL_RATIO
    above20 = ema20 is not None and close >= ema20
    above50 = ma50 is not None and close >= ma50
    better = pct is not None and vni_pct is not None and pct > vni_pct
    points = {
        "rsi": 20 if rsi is not None and rsi <= RSI_OVERSOLD else 10 if rsi is not None and rsi <= RSI_LOW else 0,
        "divergence": 20 if div else 0,
        "stopping": 20 if stopping else 10 if soft_stop else 0,
        "market": 10 if better else 0,
        "liquidity": 10 if liquidity >= LIQUIDITY_MIN else 0,
        "ema20": 10 if above20 else 0,
        "ma50": 10 if above50 else 0,
    }
    return {
        "code": code, "day": str(last["Date"])[:10], "close": close, "pct": pct, "vni_pct": vni_pct,
        "rsi": rsi, "divergence": div, "ema20": ema20, "ma50": ma50, "ma100": ma100,
        "gap20": (close / ema20 - 1) * 100 if ema20 else None,
        "support": support, "resistance": resistance, "close_pos": close_pos, "vol_ratio": vol_ratio,
        "vol_avg": vol_avg, "liquidity": liquidity, "stopping": stopping or soft_stop,
        "checks": {"A": stopping or soft_stop, "B": div, "C": liquidity >= LIQUIDITY_MIN, "G": above20 and above50},
        "points": points, "score": sum(points.values()),
    }


# ─── data ────────────────────────────────────────────────────────────────────


def _bars(code: str, day: str) -> list[dict]:
    from tradingagents.dataflows.ohlcv import load_ohlcv

    frame = load_ohlcv(code, day)
    out = frame.to_dict("records")
    for r in out:
        r["Date"] = r["Date"].strftime("%Y-%m-%d") if hasattr(r["Date"], "strftime") else str(r["Date"])[:10]
    return out


def universe(watchlist) -> tuple[list[str], str]:
    """VN100 (VN30 when VCI has no VN100) plus the watchlist, and the scope's name."""
    from tradingagents.dataflows import money_flows as mf

    members, scope = mf.index_members("VN100"), "VN100"
    if not members:
        members, scope = mf.index_members("VN30"), "VN30"
    codes = list(members)
    for c in watchlist or ():
        if c not in codes:
            codes.append(c)
    return codes, scope + " + watchlist"


def scan(codes: list[str], day: str, *, bars=None) -> tuple[list[dict], float | None, list[str]]:
    """Every code analysed on its last closed bar up to ``day``: (the signals, highest score first;
    VN-Index's % that day; the codes without data)."""
    from tradingagents.market import vn_rules

    bars = bars or _bars
    vni_pct = None
    try:
        vni = bars(vn_rules.BENCHMARK, day)
        if len(vni) >= 2 and vni[-2]["Close"]:
            vni_pct = (float(vni[-1]["Close"]) / float(vni[-2]["Close"]) - 1) * 100
    except Exception as exc:  # noqa: BLE001 - the market comparison is one of seven points
        logger.warning("Radar: VNINDEX không đọc được: %s", exc)

    def one(code: str):
        try:
            return analyze(code, bars(code, day), vni_pct)
        except Exception as exc:  # noqa: BLE001 - one code's data must not stop the scan
            logger.warning("Radar %s: %s", code, exc)
            return exc

    with ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="radar") as pool:
        results = list(pool.map(one, codes))
    signals = [r for r in results if isinstance(r, dict)]
    errors = [c for c, r in zip(codes, results, strict=True) if isinstance(r, Exception)]
    signals.sort(key=lambda s: (-s["score"], s["code"]))
    return signals, vni_pct, errors


def _num(value) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def fundamentals(code: str) -> dict | None:
    """The newest quarter published: revenue, gross margin, operating profit (isa11), other income net
    (isa14), net profit (isa20) and its change on the year; None for a company without these lines
    (a bank, an insurer) or when VCI has nothing."""
    from tradingagents.dataflows import vn_fundamentals as vf

    rows = vf.published_statement_rows(code, "INCOME_STATEMENT", None)
    if not rows or _num(rows[0].get("isa3")) is None:
        return None
    q = rows[0]
    revenue, gross = _num(q.get("isa3")), _num(q.get("isa5"))
    net, other, core = _num(q.get("isa20")), _num(q.get("isa14")), _num(q.get("isa11"))
    yoy = None
    if len(rows) > 4 and net is not None and _num(rows[4].get("isa20")):
        then = _num(rows[4].get("isa20"))
        yoy = (net - then) / abs(then) * 100
    core_yoy = None
    if len(rows) > 4 and core is not None and _num(rows[4].get("isa11")):
        then = _num(rows[4].get("isa11"))
        core_yoy = (core - then) / abs(then) * 100
    share = other / net if other is not None and net else None
    return {"period": f"Q{q.get('lengthReport')}/{q.get('yearReport')}", "revenue": revenue,
            "gross_margin": gross / revenue * 100 if gross is not None and revenue else None,
            "core": core, "core_yoy": core_yoy, "other": other, "net": net, "yoy": yoy,
            "other_share": share, "one_off": share is not None and share >= OTHER_INCOME_SHARE}


def ex_dates(code: str, start: str, end: str) -> list[dict]:
    """Dividends and share issues whose ex-date is in [start, end]: ``{day, kind, value, title}``."""
    from tradingagents.dataflows.vendors import vci

    out = []
    for e in vci.fetch_events(code, start, end, codes="DIV,ISS"):
        day = str(e.get("exrightDate") or "")[:10]
        if not day or not start <= day <= end:
            continue
        out.append({"day": day, "kind": e.get("eventCode"), "value": _num(e.get("valuePerShare")),
                    "title": e.get("eventTitleVi") or e.get("eventTitleEn") or ""})
    return sorted(out, key=lambda x: x["day"])


def headlines(code: str, end: str) -> list:
    """The stock's news of the last ``NEWS_DAYS`` days: the bot's feeds (VCI disclosures, CafeF research
    and company news, Google News) and the papers' own items (``vn_news.press_news``: CafeF, Vietstock,
    VnEconomy), the ones naming the code first, newest first, no repeat."""
    from tradingagents.dataflows.news import collect_company_news
    from tradingagents.dataflows.vendors import vn_news
    from tradingagents.quant import report_format as rfmt

    start = (datetime.strptime(end, "%Y-%m-%d") - timedelta(days=NEWS_DAYS)).strftime("%Y-%m-%d")
    items: list = []
    try:
        items += collect_company_news(code, start, end, NEWS_LIMIT)[0]
    except Exception as exc:  # noqa: BLE001 - the papers alone then
        logger.warning("Radar %s: tin của bot lỗi: %s", code, exc)
    try:
        items += vn_news.press_news(code, start, end, limit=NEWS_LIMIT)
    except Exception as exc:  # noqa: BLE001 - the bot's feeds alone then
        logger.warning("Radar %s: báo lỗi: %s", code, exc)
    seen: set[str] = set()
    out = []
    for h in items:
        key = (h.title or "").lower().strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(h)
    out.sort(key=lambda h: (rfmt.is_news_relevant(h.title, code, h.confirmed), h.published or datetime.min.replace(tzinfo=None)
                            if h.published is None else h.published), reverse=True)
    return out[:NEWS_LIMIT]


def news_lines(items: list, code: str) -> list[str]:
    """The "Tin mới liên quan" block: up to ``NEWS_SHOWN`` items naming the code, newest first, source and day."""
    from tradingagents.quant import report_format as rfmt

    named = [h for h in items if rfmt.is_news_relevant(h.title, code, h.confirmed)]
    named.sort(key=lambda h: h.published or datetime.min.replace(tzinfo=None) if h.published is None else h.published, reverse=True)
    if not named:
        return [f"Tin mới liên quan: không có tin nào nêu {code} trong {NEWS_DAYS} ngày qua (CafeF, Vietstock, VnEconomy, VCI)."]
    out = ["Tin mới liên quan:"]
    for h in named[:NEWS_SHOWN]:
        when = h.published.strftime("%d/%m") if h.published else "?"
        out.append(f"• {when} {h.source}: {h.title}")
    return out


# ─── "Tai mắt": level 2 (the AI) over level 1 (the figures) ───────────────────


def _bn(value: float | None) -> str:
    from vnbot.quote import _vn

    return "N/A" if value is None else f"{_vn(value / 1e9, 0)} tỷ"


def _pct(value: float | None, decimals: int = 1) -> str:
    from vnbot.quote import _vn

    if value is None:
        return "N/A"
    return ("+" if value > 0 else "") + _vn(value, decimals) + "%"


def figures_lines(fund: dict | None) -> list[str]:
    """Level 1: the quarter's figures, read by code."""
    if fund is None:
        return ["Báo cáo tài chính: chưa đọc được từ VCI (hoặc mã không có dòng doanh thu/lợi nhuận hoạt động)."]
    lines = [f"LNST {fund['period']} {_bn(fund['net'])} ({_pct(fund['yoy'])} so với cùng kỳ); lợi nhuận hoạt động "
             f"kinh doanh {_bn(fund['core'])} ({_pct(fund['core_yoy'])}); thu nhập khác ròng {_bn(fund['other'])}; "
             f"biên gộp {_pct(fund['gross_margin'], 1).lstrip('+')} (VCI)."]
    if fund["one_off"]:
        lines.append(f"Thu nhập khác chiếm {fund['other_share'] * 100:.0f}% LNST: lãi quý này chủ yếu không đến từ "
                     "kinh doanh cốt lõi, chưa có lý do cơ bản để mua.")
    elif fund["core"] is not None and fund["net"] and fund["core"] > 0:
        lines.append("Lãi đến từ hoạt động kinh doanh cốt lõi.")
    return lines


def _prompt(code: str, fund: dict | None, items: list, end: str) -> str:
    parts = [f"Mã: {code}. Ngày: {end}.", "Số liệu quý mới nhất (VCI):"] + figures_lines(fund)
    parts.append(f"Tiêu đề tin {NEWS_DAYS} ngày qua (nguồn, ngày; CafeF, Vietstock, VnEconomy, VCI, Google News):")
    if not items:
        parts.append("(không có tin nào)")
    for h in items[:NEWS_LIMIT]:
        when = h.published.strftime("%d/%m") if getattr(h, "published", None) else "?"
        parts.append(f"- {h.title} ({h.source}, {when})")
    return "\n".join(parts)


def ask_ai(code: str, fund: dict | None, items: list, end: str, llm) -> dict:
    """Level 2: one request. Raises when no provider answers or the answer is not the JSON asked."""
    from langchain_core.messages import HumanMessage, SystemMessage

    reply = llm.invoke([SystemMessage(content=SYSTEM), HumanMessage(content=_prompt(code, fund, items, end))])
    content = reply.content if isinstance(reply.content, str) else json.dumps(reply.content, ensure_ascii=False)
    found = _JSON.search(content or "")
    data = json.loads(found.group(0)) if found else None
    if not isinstance(data, dict) or not isinstance(data.get("lines"), list) or not data["lines"]:
        raise ValueError(f"AI trả lời không đúng định dạng: {(content or '')[:160]!r}")
    lines = [str(x).strip() for x in data["lines"] if str(x).strip()][:3]
    return {"lines": lines, "supportive": bool(data.get("supportive")), "one_off": bool(data.get("one_off"))}


def intel(code: str, end: str, llm=None, *, news=None, items: list | None = None) -> dict:
    """The "Tai mắt" block: ``{lines, level (2 or 1), supportive (True/False, None unscored), source}``.
    ``items``: the headlines already fetched (else ``news``/``headlines`` fetches them)."""
    try:
        fund = fundamentals(code)
    except Exception as exc:  # noqa: BLE001 - the block without the figures
        logger.warning("Radar %s: VCI lỗi: %s", code, exc)
        fund = None
    if llm is not None:
        if items is None:
            try:
                items = (news or headlines)(code, end)
            except Exception as exc:  # noqa: BLE001 - the AI reads the figures alone
                logger.warning("Radar %s: tin lỗi: %s", code, exc)
                items = []
        try:
            got = ask_ai(code, fund, items, end, llm)
            return {"lines": got["lines"], "level": 2, "supportive": got["supportive"], "one_off": got["one_off"],
                    "source": "AI đọc số liệu VCI và tin 2 tuần", "items": items}
        except Exception as exc:  # noqa: BLE001 - level 1 instead, said so
            logger.warning("Radar %s: AI lỗi, dùng số liệu: %s", code, exc)
            why = str(exc)[:80]
    else:
        why = "chưa cấu hình AI"
        if items is None:
            try:
                items = (news or headlines)(code, end)
            except Exception as exc:  # noqa: BLE001 - the block without the headlines
                logger.warning("Radar %s: tin lỗi: %s", code, exc)
                items = []
    return {"lines": figures_lines(fund), "level": 1, "supportive": None,
            "one_off": bool(fund and fund["one_off"]), "source": f"số liệu VCI, AI không trả lời ({why})", "items": items}


class IntelCache:
    """The day's "Tai mắt" answers on disk (``radar_intel.json``): the noon send reuses the morning's AI
    lines instead of asking again; a level-1 answer is not kept, so the next run tries the AI again."""

    def __init__(self, data_dir):
        from pathlib import Path

        self.path = Path(data_dir) / "radar_intel.json" if data_dir else None

    def _read(self) -> dict:
        if self.path is None:
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def get(self, day: str, code: str) -> dict | None:
        got = (self._read().get(day) or {}).get(code)
        return got if isinstance(got, dict) and got.get("level") == 2 else None

    def put(self, day: str, code: str, value: dict) -> None:
        if self.path is None or value.get("level") != 2:
            return
        from vnbot.jobs import write_text_atomic

        data = {d: v for d, v in self._read().items() if d == day}      # one day kept
        data.setdefault(day, {})[code] = {k: value[k] for k in ("lines", "level", "supportive", "one_off", "source")}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(self.path, json.dumps(data, ensure_ascii=False))


# ─── the report ──────────────────────────────────────────────────────────────


def _price(v: float | None) -> str:
    from vnbot.quote import _vn

    return "N/A" if v is None else _vn(v)


def _tick(price: float, exchange: str | None) -> float:
    from tradingagents.market import vn_rules

    return vn_rules.round_to_tick(price, exchange)


def orders(s: dict, exchange: str | None = None) -> dict:
    """The probe's levels: entry zone, stop, target, R:R."""
    lo, hi = _tick(s["support"] * ENTRY_LOW, exchange), _tick(s["support"] * ENTRY_HIGH, exchange)
    stop = _tick(s["support"] * STOP_UNDER, exchange)
    target = s["ema20"] if s["ema20"] and s["ema20"] > hi else s["resistance"]
    target = _tick(target, exchange)
    mid = (lo + hi) / 2
    rr = (target - mid) / (mid - stop) if mid > stop else None
    return {"entry_low": lo, "entry_high": hi, "stop": stop, "target": target, "rr": rr,
            "target_is_ema": bool(s["ema20"] and s["ema20"] > hi)}


def opening(vni_now_pct: float | None, named: int) -> str:
    if not named:
        return "Radar im lặng. Không mã nào đạt chuẩn, hôm nay giữ tiền là một vị thế."
    if vni_now_pct is not None and vni_now_pct <= VNI_HARD_PCT:
        return "Địch rũ tới chân thành. Chưa thấy tiền thật, ta chưa rút kiếm."
    if vni_now_pct is not None and vni_now_pct >= 1.0:
        return "Gió thuận, nhưng chỉ xuống thuyền khi có vé: đóng cửa và volume phải xác nhận."
    return "Trận địa yên. Radar có mã, chờ ATC xác nhận rồi mới tính."


def _ddmm(day: str) -> str:
    return f"{day[8:10]}/{day[5:7]}"


def stock_block(s: dict, ctx: dict, tai_mat: dict, exes: list[dict], morning: dict | None) -> list[str]:
    """One named stock: Chiến sự, Tai mắt, Checklist papa, Quân lệnh."""
    from vnbot.quote import _vn

    code = s["code"]
    out = [f"━━ {code} · {s['score']}/100 ━━", "Chiến sự"]
    rsi = f"RSI(14) {_vn(s['rsi'], 1)}" + (" quá bán" if s["rsi"] <= RSI_OVERSOLD else " thấp" if s["rsi"] <= RSI_LOW else "")
    rsi += ", có phân kỳ dương" if s["divergence"] else ", chưa có phân kỳ"
    where = (f"Giá nằm {'dưới' if s['gap20'] < 0 else 'trên'} EMA20 ({_price(s['ema20'])}) khoảng {_vn(abs(s['gap20']), 1)}%"
             if s["gap20"] is not None else "EMA20: chưa đủ phiên")
    out.append(f"Kỹ thuật: {rsi}. {where}. Hỗ trợ {_price(s['support'])}, kháng cự {_price(s['resistance'])} "
               f"({LEVEL_SESSIONS} phiên).")
    if s["vol_ratio"] is not None:
        if s["stopping"]:
            out.append(f"Lực cầu: nến phiên trước rút chân {s['close_pos'] * 100:.0f}% với volume gấp {_vn(s['vol_ratio'], 1)} "
                       "lần MA20, dấu hiệu bên mua chặn đà rơi (stopping volume).")
        else:
            out.append(f"Lực cầu: nến phiên trước đóng ở {s['close_pos'] * 100:.0f}% biên độ, volume {_vn(s['vol_ratio'], 1)} "
                       "lần MA20: chưa thấy bên mua chặn đà rơi.")
    if s["pct"] is not None and s["vni_pct"] is not None:
        verb = lambda p: f"{'mất' if p < 0 else 'tăng'} {_vn(abs(p), 2)}%"  # noqa: E731
        better = s["pct"] > s["vni_pct"]
        out.append(f"So với thị trường: VN-Index {verb(s['vni_pct'])} còn {code} {'chỉ ' if better and s['pct'] < 0 else ''}"
                   f"{verb(s['pct'])}, tức {code} {'chịu đòn tốt hơn' if better else 'yếu hơn'} mặt bằng chung.")
    out += ["", "Tai mắt"] + tai_mat["lines"]
    if tai_mat["level"] == 1:
        out.append(f"(Mức 1: {tai_mat['source']}.)")
    out += news_lines(tai_mat.get("items") or [], code)
    note = f"Tín hiệu được chấm trên nến ngày {_ddmm(s['day'])}."
    if morning:
        vol = morning.get("volume")
        ratio = f" ({vol / s['vol_avg'] * 100:.0f}% MA20)" if vol and s["vol_avg"] else ""
        when = "KL phiên sáng" if morning.get("noon") else f"KL tới {morning['at']}"
        note += f" Hôm nay {when}: {_vn(vol) + ' cp' if vol else 'chưa có'}{ratio}"
        if morning.get("foreign") is not None:
            f = morning["foreign"]
            side = "bán ròng" if f["net"] < 0 else "mua ròng" if f["net"] > 0 else "cân bằng"
            note += f"; khối ngoại phiên {_ddmm(f['day'])} {side} {_vn(abs(f['net']))} cp"
        note += "."
    out.append(note)
    for e in exes:
        amount = f" {_vn(e['value'])} đ/cp" if e["kind"] == "DIV" and e["value"] else ""
        out.append(f"Ngày {_ddmm(e['day'])} là GDKHQ ({e['title']}{amount}): phần giá giảm hôm đó là chia, không phải bán.")
    c = s["checks"]
    mark = lambda ok: "✓" if ok else "✗"  # noqa: E731
    out += ["", "Checklist papa", f"Mẫu hình + phân kỳ (A, B): {mark(c['A'])} {mark(c['B'])}"]
    if tai_mat["supportive"] is None:
        out.append("Tin hỗ trợ (E): chưa chấm (AI không trả lời)")
    else:
        out.append(f"Tin hỗ trợ (E): {mark(tai_mat['supportive'])}")
    ma = f"MA (G): {mark(c['G'])}"
    if not c["G"]:
        ma += (", giá đang dưới cả EMA20, chưa nói đến MA50 và MA100" if s["gap20"] is not None and s["gap20"] < 0
               else ", giá trên EMA20 nhưng dưới MA50")
    out.append(ma)
    out.append(f"Thanh khoản TB20 trên {_vn(LIQUIDITY_MIN / 1e9)} tỷ (C): {mark(c['C'])} ({_vn(s['liquidity'] / 1e9, 1)} tỷ)")
    full = all(c.values()) and tai_mat["supportive"] is True
    out.append("Tổng thể: " + ("đủ chuẩn checklist." if full else
                               "đây là điểm uốn kỹ thuật, chưa phải mã đủ chuẩn để mua theo checklist."))
    o = orders(s, ctx.get("exchange"))
    out += ["", "Quân lệnh hôm nay",
            f"{code} đóng cửa giữ trên {_price(s['support'])} và volume từ {_vn(STOP_VOL_RATIO, 1)}x MA20 trở lên → mua thăm dò "
            f"{PROBE_PCT}% NAV ở {_price(o['entry_low'])}–{_price(o['entry_high'])}, stop {_price(o['stop'])}, mục tiêu "
            f"{_price(o['target'])} ({'EMA20' if o['target_is_ema'] else 'kháng cự'})"
            + (f". R:R khoảng {_vn(o['rr'], 1)}." if o["rr"] else "."),
            f"Thủng {_price(s['support'])} → bỏ, vì nền bị phá.",
            "VN-Index tiếp tục bị bán mạnh → án binh, không mua dù tín hiệu đẹp.",
            f"Không mua đuổi trong phiên. Không bình quân giá xuống dưới {_price(o['stop'])}."]
    if tai_mat["one_off"] or (s["gap20"] is not None and s["gap20"] < 0):
        why = []
        if tai_mat["one_off"]:
            why.append("lãi chủ yếu từ khoản khác")
        if s["gap20"] is not None and s["gap20"] < 0:
            why.append("giá nằm dưới EMA20")
        text = ", ".join(why)
        out.append(text[0].upper() + text[1:] + ": đây là cú bật kỹ thuật, chưa phải đổi xu hướng. "
                   "Chưa có xác nhận lúc ATC thì vào lệnh nghiêng về cờ bạc.")
    return out


def _morning(code: str, now: datetime) -> dict | None:
    """This morning's price, volume and the last foreign session of a named stock (None on error)."""
    from tradingagents.dataflows.vendors import flows
    from vnbot import quote

    try:
        q = quote.stock_quote(code, now)
    except Exception as exc:  # noqa: BLE001 - the block without this morning
        logger.info("Radar %s: giá sáng nay: %s", code, exc)
        return None
    out = {"at": now.strftime("%H:%M"), "price": q["price"], "ref": q["ref"],
           "pct": (q["price"] / q["ref"] - 1) * 100 if q["ref"] else None,
           "volume": q["volume"] if q["day"] == now.strftime("%Y-%m-%d") else None, "foreign": None,
           "noon": now.strftime("%H:%M") >= NOON_DEFAULT}
    try:
        frame = flows.fetch_foreign_days(code, 3)
        rows = [r for r in frame.to_dict("records") if r["ForeignBuyVol"] or r["ForeignSellVol"]]
        if rows:
            out["foreign"] = {"day": rows[-1]["Date"], "net": float(rows[-1]["ForeignNetVol"])}
    except Exception as exc:  # noqa: BLE001
        logger.info("Radar %s: khối ngoại: %s", code, exc)
    return out


def _exchange(code: str) -> str | None:
    from tradingagents.dataflows.symbols import listing_entry
    from tradingagents.market import vn_rules

    entry = listing_entry(code)
    return vn_rules.normalize_exchange((entry or {}).get("board")) if entry else None


def report(now: datetime, watchlist, llm=None, *, bars=None, scan_fn=None, morning=_morning, news=None,
           events=ex_dates, exchange=_exchange, score_min: float = SCORE_MIN, noon: bool = False,
           cache: IntelCache | None = None) -> str:
    """The report at ``now``: the morning one, or with ``noon`` the one at the end of the morning session
    (the same signal, this morning's trading as the context, the AI's lines from ``cache`` when the
    morning run kept them). Every data function is replaceable by the tests."""
    from tradingagents.market import vn_rules
    from vnbot import quote
    from vnbot.quote import _vn

    now = now.astimezone(vn_rules.VN_TZ)
    today = now.strftime("%Y-%m-%d")
    signal_day = vn_rules.prev_session(today) if vn_rules.is_session_day(today) and now.strftime("%H:%M") < "15:05" \
        else (today if vn_rules.is_session_day(today) else vn_rules.prev_session(today))
    codes, scope = universe(watchlist)
    signals, vni_pct, errors = (scan_fn or scan)(codes, signal_day, bars=bars)
    named = [s for s in signals if s["score"] >= score_min][:MAX_NAMED]
    near = [s for s in signals if s["score"] < score_min][:NEAR_MISSES]
    vni_now = None
    try:
        q = quote.stock_quote(vn_rules.BENCHMARK, now)
        vni_now = {"price": q["price"], "pct": (q["price"] / q["ref"] - 1) * 100 if q["ref"] else None, "day": q["day"]}
    except Exception as exc:  # noqa: BLE001
        logger.info("Radar: VN-Index sáng nay: %s", exc)
    head = [opening(vni_now["pct"] if vni_now else None, len(named))]
    line = f"{now:%d/%m/%Y, %H:%M}" + (" (kết thúc phiên sáng)" if noon else "")
    if vni_now:
        line += f" | VN-Index {_vn(vni_now['price'], 2)} ({_pct(vni_now['pct'], 2)})"
    blocks = []
    for s in named:
        m = morning(s["code"], now) if morning else None
        if m and noon:
            m["noon"] = True
        if m:
            line += f" | {s['code']} {_price(m['price'])} ({_pct(m['pct'], 2)})" + (f", KL {_vn(m['volume'])} cp" if m.get("volume") else "")
        try:
            exes = events(s["code"], (now - timedelta(days=45)).strftime("%Y-%m-%d"), today)
        except Exception as exc:  # noqa: BLE001
            logger.info("Radar %s: sự kiện VCI: %s", s["code"], exc)
            exes = []
        try:
            ex = exchange(s["code"])
        except Exception:  # noqa: BLE001
            ex = None
        kept = cache.get(today, s["code"]) if cache else None
        if kept:
            try:
                kept["items"] = (news or headlines)(s["code"], today)
            except Exception as exc:  # noqa: BLE001 - the block without the headlines
                logger.warning("Radar %s: tin lỗi: %s", s["code"], exc)
                kept["items"] = []
            got = kept
        else:
            got = intel(s["code"], today, llm, news=news)
            if cache:
                cache.put(today, s["code"], got)
        blocks.append(stock_block(s, {"exchange": ex}, got, exes, m))
    head.append(line)
    out = head + ["", f"Radar quét {scope} ({len(codes)} mã) trên nến ngày {_ddmm(signal_day)}: "
                  + (f"{len(named)} mã đạt chuẩn (từ {score_min:.0f}/100): " + ", ".join(f"{s['code']} {s['score']}" for s in named)
                     if named else f"không mã nào đạt {score_min:.0f}/100")
                  + (f". Gần ngưỡng: {', '.join(f'{s['code']} {s['score']}' for s in near)}." if near else ".")]
    if errors:
        out.append(f"Không đọc được: {', '.join(errors[:8])}" + (f" +{len(errors) - 8}" if len(errors) > 8 else ""))
    for b in blocks:
        out += [""] + b
    out += ["", f"Điểm: RSI ≤ {RSI_OVERSOLD:.0f} 20 · phân kỳ 20 · nến rút chân + volume 20 · hơn VN-Index 10 · thanh khoản 10 · "
            "trên EMA20 10 · trên MA50 10. Không phải khuyến nghị; quyết định của bot: /nhanh MÃ."]
    return "\n".join(out)


def command(args: str, watchlist, llm=None, now: datetime | None = None, data_dir=None) -> str:
    """/radar (Telegram and Zoom): the report now (the noon layout from 11:30 on)."""
    from tradingagents.market import vn_rules

    if (args or "").strip():
        raise ValueError("Dùng: /radar (quét VN100 + watchlist ngay, như bản tin 09:30 và 11:30).")
    now = now or vn_rules.now_vn()
    noon = now.astimezone(vn_rules.VN_TZ).strftime("%H:%M") >= NOON_DEFAULT
    return report(now, watchlist, llm, noon=noon, cache=IntelCache(data_dir) if data_dir else None)
