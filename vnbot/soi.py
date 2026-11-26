"""Soi nhanh một mã (``/soi MÃ``, Telegram and Zoom): the card of numbers a trader checks before
anything else, code only, no AI - a few seconds.

- The session: last price, change, reference, ceiling, floor, matched volume against the
  average of the 20 sessions before (MA20), foreign buy/sell/net in shares, the 20-session
  average matched value and volume. After the close from VNDirect (the session's rows,
  ~15:11); in the session the last match of DNSE (``vnbot.quote``) with the day's forming
  bar, the reference being the previous close and the ceiling/floor computed from it.
- P/B at the price shown: price x shares / parent shareholders' equity of the newest quarter
  published (``vn_valuation``'s history, the same book the valuation uses).
- RS score 1-99 (``market_scan.MarketCloses.rs_scores``): where the stock's six-month
  price performance (the newest quarter counting double) ranks among every listed stock.
- SEPA (Minervini) compulsory criteria from the statements published to date (VCI):
  quarterly profit against the same quarter a year before and against the quarter before,
  cash at the last year end, annual profit against the year before, how many of the last 5
  quarters grew on the year, and the price against MA50, MA150, MA200 (DNSE's adjusted
  bars, the live price as the last close in the session). The CANSLIM line passes only when
  every growth criterion and every average does.
- The shark flow of the session (``vnbot.camap``).
- "Nhận định nhanh": a reading written from the numbers (MA20/MA50, RSI 14, MACD, Bollinger,
  volume, the 5-session range) and what it means for a holder and for a new buyer. The
  trading decision of the bot stays ``/nhanh`` (Quant Engine); this card is information.

Each source answers on its own: one down leaves its lines saying so and the rest intact.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from vnbot import camap, dotbien
from vnbot.quote import _icon, _vn, _vn_volume

logger = logging.getLogger(__name__)

BAR = "━━━━━━━━━━━━━━━━━━━━━━"
AVG_SESSIONS = 20
DAYS_BACK = 60                 # calendar days of VNDirect rows asked: > 20 sessions + today, Tết included
RANGE_SESSIONS = 5
RS_LABELS = ((80, "mạnh, nhóm dẫn dắt"), (60, "khá"), (40, "trung bình"), (20, "yếu"),
             (0, "tiêu cực, hạn chế tham gia"))
QUARTERS_POSITIVE = 3          # of the last 5 quarters, how many must grow on the year
PROFIT_FIELDS = ("isa20", "isa22")   # net profit; the parent's when VCI has no total
_CASH_LABEL = re.compile(r"tien va (cac khoan )?tuong duong tien")
_BANK_CASH_LABEL = re.compile(r"^tien mat")
RSI_OVERSOLD, RSI_OVERBOUGHT = 30, 70
SPIKE_RATIO, DRY_RATIO = 1.5, 0.7
NARROW_RANGE_PCT = 8.0


# ─── data ───────────────────────────────────────────────────────────────────


def _now():
    from tradingagents.market import vn_rules

    return vn_rules.now_vn()


def _stock_days(code: str, today: str):
    from tradingagents.dataflows.vendors import flows

    start = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=DAYS_BACK)).strftime("%Y-%m-%d")
    return flows.fetch_stock_days(code, start, today)


def _foreign(code: str):
    from tradingagents.dataflows.vendors import flows

    return flows.fetch_foreign_days(code, 6)


def _bars(code: str, today: str):
    from tradingagents.dataflows.ohlcv import load_ohlcv

    return load_ohlcv(code, today)


def _quote(code: str, now: datetime) -> dict:
    from vnbot import quote

    return quote.stock_quote(code, now)


def _pb_basis(code: str) -> dict | None:
    """{shares, equity, period, published} of the newest quarter published (the valuation's history)."""
    from tradingagents.dataflows import vn_valuation

    history = vn_valuation.multiple_history(code, "pb", None)
    if history.empty:
        return None
    last = history.iloc[-1]
    base = float(last["Base"]) if last["Base"] == last["Base"] else None
    if not base or base <= 0 or not last["TotalShares"]:
        return None
    return {"shares": float(last["TotalShares"]), "equity": base, "period": str(last["Period"]),
            "published": last["PeriodPublished"]}


def _rs(data_dir, now: datetime) -> dict:
    """{score} or {progress: (have, want)}: the market history fills on a thread when short."""
    closes = dotbien.history(data_dir)
    through = dotbien.through_day(now)
    scores = closes.rs_scores(through)
    if scores:
        return {"scores": scores, "day": through}
    have, want = closes.progress(through)
    dotbien.fill_in_background(data_dir, now)
    return {"progress": (have, min(want, dotbien.ms.RS_SESSIONS + 1))}


def _strip(text: str) -> str:
    plain = unicodedata.normalize("NFD", text or "")
    return "".join(c for c in plain if unicodedata.category(c) != "Mn").replace("đ", "d").replace("Đ", "D").lower().strip()


def _cash_field(code: str) -> str | None:
    """The balance-sheet field of cash and equivalents (banks: cash), by VCI's own labels."""
    from tradingagents.dataflows.vendors import vci

    try:
        meta = vci.fetch_metric_labels(code).get("BALANCE_SHEET") or []
    except Exception as exc:  # noqa: BLE001 - no label, no cash line
        logger.info("Soi %s: không có nhãn bảng cân đối: %s", code, exc)
        return None
    names = [(m.get("field"), _strip(m.get("titleVi") or m.get("titleEn") or "")) for m in meta if m.get("field")]
    for field, name in names:
        if _CASH_LABEL.search(name) and "luu chuyen" not in name:
            return field
    for field, name in names:
        if _BANK_CASH_LABEL.search(name):
            return field
    return None


def _num(value) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def _profit(rows: list[dict]) -> list[float | None]:
    field = next((f for f in PROFIT_FIELDS if any(_num(r.get(f)) for r in rows[:5])), None)
    return [_num(r.get(field)) for r in rows] if field else []


def _growth(now_v: float | None, then: float | None) -> float | None:
    if now_v is None or not then:
        return None
    return (now_v - then) / abs(then) * 100


def fundamentals(code: str) -> dict:
    """The SEPA figures from the statements published to date: ``{yoy, yoy_label, qoq, qoq_label,
    cash, cash_year, annual, annual_label, positive (of 5), quarters}`` (None where VCI has no figure)."""
    from tradingagents.dataflows import vn_fundamentals as vf

    quarters = vf.published_statement_rows(code, "INCOME_STATEMENT", None)
    profits = _profit(quarters)
    out: dict = {"yoy": None, "qoq": None, "annual": None, "positive": None, "cash": None, "cash_year": None}
    if profits:
        q = quarters[0]
        label = f"Q{q.get('lengthReport')}/{q.get('yearReport')}"
        out["yoy"] = _growth(profits[0], profits[4]) if len(profits) > 4 else None
        out["yoy_label"] = f"{label} so với Q{q.get('lengthReport')}/{int(q.get('yearReport') or 0) - 1}"
        out["qoq"] = _growth(profits[0], profits[1]) if len(profits) > 1 else None
        if len(quarters) > 1:
            out["qoq_label"] = f"{label} so với Q{quarters[1].get('lengthReport')}/{quarters[1].get('yearReport')}"
        if len(profits) >= 9:
            growths = [_growth(profits[i], profits[i + 4]) for i in range(5)]
            known = [g for g in growths if g is not None]
            out["positive"] = sum(1 for g in known if g > 0) if len(known) == 5 else None
    years = vf.published_statement_rows(code, "INCOME_STATEMENT", None, annual=True)
    annual = _profit(years)
    if len(annual) >= 2 and annual[0] is not None:
        out["annual"] = _growth(annual[0], annual[1])
        out["annual_label"] = f"năm {years[0].get('yearReport')} so với {years[1].get('yearReport')}"
    field = _cash_field(code)
    if field:
        balance = vf.published_statement_rows(code, "BALANCE_SHEET", None, annual=True)
        for row in balance:
            cash = _num(row.get(field))
            if cash is not None:
                out["cash"], out["cash_year"] = cash, row.get("yearReport")
                break
    return out


# ─── indicators ─────────────────────────────────────────────────────────────


def indicators(closes: list[float], highs: list[float], lows: list[float], price: float) -> dict:
    """MA20/50/150/200, RSI 14, MACD, Bollinger, the 5-session range, on ``closes`` whose last is the
    price shown (None where the history is short)."""
    from tradingagents.quant import features as f

    macd = f.calc_macd(closes)
    bb = f.calc_bollinger(closes, price)
    rng_lo = min(lows[-RANGE_SESSIONS:]) if lows else None
    rng_hi = max(highs[-RANGE_SESSIONS:]) if highs else None
    return {"ma20": f.calc_sma(closes, 20), "ma50": f.calc_sma(closes, 50), "ma150": f.calc_sma(closes, 150),
            "ma200": f.calc_sma(closes, 200), "rsi": f.calc_rsi(closes, 14),
            "macd": macd if len(closes) >= 35 else None, "bb": bb if bb.available else None,
            "range_lo": rng_lo, "range_hi": rng_hi}


# ─── the card ───────────────────────────────────────────────────────────────


def rs_label(score: int) -> str:
    return next(label for floor, label in RS_LABELS if score >= floor)


def _pct(value: float | None, decimals: int = 2, signed: bool = True) -> str:
    if value is None:
        return "chưa có số"
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{_vn(value, decimals)}%"


def _bn(value: float) -> str:
    return f"{_vn(value / 1e9, 1)} tỷ"


def _check(ok: bool | None) -> str:
    return "✅" if ok else "❌" if ok is False else "⚪"


def build(code: str, data_dir, now: datetime | None = None) -> dict:
    """Everything the card shows, each source on its own thread; errors kept as text per block."""
    from tradingagents.market import vn_rules

    now = (now or _now()).astimezone(vn_rules.VN_TZ)
    today = now.strftime("%Y-%m-%d")
    session_day = vn_rules.is_session_day(today)
    want_live = session_day and now.strftime("%H:%M") >= "09:00"
    with ThreadPoolExecutor(max_workers=6, thread_name_prefix="soi") as pool:
        futures = {
            "days": pool.submit(_stock_days, code, today),
            "foreign": pool.submit(_foreign, code),
            "bars": pool.submit(_bars, code, today),
            "fund": pool.submit(fundamentals, code),
            "pb": pool.submit(_pb_basis, code),
            "rs": pool.submit(_rs, data_dir, now),
        }
        if want_live:
            futures["quote"] = pool.submit(_quote, code, now)
        got, errors = {}, {}
        for key, fut in futures.items():
            try:
                got[key] = fut.result()
            except Exception as exc:  # noqa: BLE001 - one source down, the others shown
                logger.warning("Soi %s: %s lỗi: %s", code, key, exc)
                got[key], errors[key] = None, str(exc)
    days = got.get("days")
    bars = got.get("bars")
    if (days is None or days.empty) and (bars is None or bars.empty):
        from vnbot.quote import Unknown

        if isinstance(errors.get("bars"), str) and "No market data" in errors["bars"]:
            raise Unknown(code)
        raise ValueError(f"Chưa lấy được giá của {code} (VNDirect: {errors.get('days', 'không có phiên')}; "
                         f"DNSE: {errors.get('bars', 'không có nến')}). Thử lại sau ít phút.")
    session = _session(code, days, got.get("quote"), now, want_live)
    tech = _technicals(bars, session, now)
    ticks_day = session["day"]
    try:
        shark = camap.flow(code, ticks_day)
    except Exception as exc:  # noqa: BLE001 - the shark block says so
        logger.warning("Soi %s: tick DNSE lỗi: %s", code, exc)
        shark = None
    return {"code": code, "now": now, "session": session, "tech": tech, "foreign": got.get("foreign"),
            "fund": got.get("fund"), "pb": got.get("pb"), "rs": got.get("rs"), "shark": shark, "errors": errors}


def _session(code: str, days, q: dict | None, now: datetime, want_live: bool) -> dict:
    """The session shown: ``{day, live, price, ref, ceiling, floor, change, change_pct, volume, value,
    avg_volume, avg_value, high, low, exchange, source}``."""
    from tradingagents.market import vn_rules

    today = now.strftime("%Y-%m-%d")
    rows = days.to_dict("records") if days is not None and not days.empty else []
    exchange = rows[-1]["Exchange"] if rows else None
    posted = rows[-1] if rows and rows[-1]["Date"] == today else None
    out: dict = {"exchange": exchange, "avg_volume": None, "avg_value": None}
    if posted is not None:
        out.update(day=today, live=False, price=posted["Close"], ref=posted["Ref"], ceiling=posted["Ceiling"],
                   floor=posted["Floor"], volume=posted["Volume"], value=posted["Value"], high=posted["High"],
                   low=posted["Low"], source="VNDirect")
    elif want_live and q is not None:
        ref = q["ref"] or (rows[-1]["Close"] if rows else None)
        ceiling, floor = vn_rules.ceiling_floor(ref, exchange) if ref else (None, None)
        volume = q["volume"] if q["day"] == today else None
        out.update(day=q["day"], live=q["day"] == today and vn_rules.is_market_hours(now), price=q["price"], ref=ref,
                   ceiling=ceiling, floor=floor, volume=volume, value=(volume or 0) * q["price"] if volume else None,
                   high=q["high"] if q["day"] == today else None, low=q["low"] if q["day"] == today else None,
                   source="DNSE")
        if q["day"] != today and rows:                 # no match today yet: the last session as posted
            posted = rows[-1]
            out.update(day=posted["Date"], live=False, price=posted["Close"], ref=posted["Ref"], ceiling=posted["Ceiling"],
                       floor=posted["Floor"], volume=posted["Volume"], value=posted["Value"], high=posted["High"],
                       low=posted["Low"], source="VNDirect")
    elif rows:
        posted = rows[-1]
        out.update(day=posted["Date"], live=False, price=posted["Close"], ref=posted["Ref"], ceiling=posted["Ceiling"],
                   floor=posted["Floor"], volume=posted["Volume"], value=posted["Value"], high=posted["High"],
                   low=posted["Low"], source="VNDirect")
    else:
        raise ValueError(f"Chưa lấy được giá của {code}. Thử lại sau ít phút.")
    prior = [r for r in rows if r["Date"] < out["day"]][-AVG_SESSIONS:]
    if prior:
        out["avg_volume"] = sum(r["Volume"] for r in prior) / len(prior)
        out["avg_value"] = sum(r["Value"] for r in prior) / len(prior)
        out["avg_sessions"] = len(prior)
    change = (out["price"] - out["ref"]) if out.get("ref") else None
    out["change"] = change
    out["change_pct"] = change / out["ref"] * 100 if change is not None and out["ref"] else None
    out["volume_ratio"] = out["volume"] / out["avg_volume"] if out.get("volume") and out.get("avg_volume") else None
    return out


def _technicals(bars, session: dict, now: datetime) -> dict | None:
    if bars is None or bars.empty:
        return None
    frame = bars[bars["Date"] <= session["day"]]
    closes = [float(v) for v in frame["Close"]]
    highs = [float(v) for v in frame["High"]]
    lows = [float(v) for v in frame["Low"]]
    last_day = frame["Date"].iloc[-1].strftime("%Y-%m-%d") if len(frame) else ""
    if last_day != session["day"]:                 # the session's bar not final yet: the price shown closes it
        closes.append(float(session["price"]))
        highs.append(float(session["high"] or session["price"]))
        lows.append(float(session["low"] or session["price"]))
    return indicators(closes, highs, lows, float(session["price"]))


def _rs_line(rs: dict | None, code: str) -> str:
    if not rs:
        return "• Điểm RS: chưa tính được (lịch sử giá toàn thị trường chưa tải được)"
    if rs.get("scores"):
        score = rs["scores"].get(code)
        if score is None:
            return "• Điểm RS: chưa có (mã chưa đủ 6 tháng giao dịch)"
        return f"• Điểm RS: {score} (xếp hạng {rs_label(score)}; 1-99 so với toàn thị trường, 6 tháng, quý gần nhất tính gấp đôi)"
    have, want = rs["progress"]
    return f"• Điểm RS: đang tải lịch sử toàn thị trường ({have}/{want} phiên), hỏi lại sau ít phút"


def _foreign_line(foreign, day: str) -> str:
    if foreign is None or foreign.empty:
        return "• Khối ngoại: chưa lấy được (VNDirect)"
    row = foreign[foreign["Date"] == day]
    if row.empty:
        last = foreign.iloc[-1]
        return (f"• Khối ngoại (phiên {last['Date'][8:10]}/{last['Date'][5:7]}, hôm nay chưa đăng): "
                f"Mua {_vn(last['ForeignBuyVol'])} | Bán {_vn(last['ForeignSellVol'])} (Ròng: {_signed(last['ForeignNetVol'])} CP)")
    r = row.iloc[-1]
    return (f"• Khối ngoại: Mua {_vn(r['ForeignBuyVol'])} | Bán {_vn(r['ForeignSellVol'])} "
            f"(Ròng: {_signed(r['ForeignNetVol'])} CP)")


def _signed(value: float) -> str:
    return ("+" if value > 0 else "") + _vn(value)


def _pb_line(pb: dict | None, price: float) -> str:
    if not pb:
        return "• Định giá P/B hiện tại: chưa tính được (VCI chưa có sổ sách)"
    value = price * pb["shares"] / pb["equity"]
    return f"• Định giá P/B hiện tại: {_vn(value, 2)} (sổ sách {pb['period']}, giá {_vn(price)})"


def _sepa_lines(fund: dict | None, tech: dict | None, price: float) -> tuple[list[str], bool | None]:
    """The SEPA block and the CANSLIM verdict (None: a criterion could not be read)."""
    lines = ["SEPA bắt buộc:"]
    checks: list[bool | None] = []
    if fund is None:
        lines.append("• Báo cáo tài chính: chưa lấy được từ VCI")
        checks.append(None)
    else:
        yoy, qoq, annual, pos = fund.get("yoy"), fund.get("qoq"), fund.get("annual"), fund.get("positive")
        checks += [None if yoy is None else yoy > 0, None if qoq is None else qoq > 0,
                   None if annual is None else annual > 0, None if pos is None else pos >= QUARTERS_POSITIVE]
        lines.append(f"• Lợi nhuận sau thuế quý hiện tại so với quý cùng kỳ năm trước: {_pct(yoy)}"
                     + (f" ({fund.get('yoy_label')})" if yoy is not None else "") + f" {_check(checks[0])}")
        lines.append(f"• Lợi nhuận sau thuế quý hiện tại so với quý trước: {_pct(qoq)}"
                     + (f" ({fund.get('qoq_label')})" if qoq is not None else "") + f" {_check(checks[1])}")
        if fund.get("cash") is not None:
            lines.append(f"• Tiền và tương đương tiền cuối kỳ năm gần nhất: {_vn(fund['cash'] / 1e9, 1)} tỷ đồng (năm {fund['cash_year']})")
        else:
            lines.append("• Tiền và tương đương tiền cuối kỳ năm gần nhất: chưa có số")
        lines.append(f"• Lợi nhuận sau thuế năm gần nhất so với năm trước đó: {_pct(annual)}"
                     + (f" ({fund.get('annual_label')})" if annual is not None else "") + f" {_check(checks[2])}")
        lines.append("• Tăng trưởng lợi nhuận sau thuế 5 quý gần nhất so với cùng kỳ: "
                     + (f"{'Dương' if pos >= QUARTERS_POSITIVE else 'Chưa ổn định'} ({pos}/5 quý dương, cần từ {QUARTERS_POSITIVE}/5)"
                        if pos is not None else "chưa đủ 9 quý") + f" {_check(checks[3])}")
    lines.append("• Xu hướng kỹ thuật:")
    for name in ("ma50", "ma150", "ma200"):
        ma = tech.get(name) if tech else None
        ok = None if ma is None else price >= ma
        checks.append(ok)
        if ma is None:
            lines.append(f"  - Giá ≥ {name.upper()}: ⚪ chưa đủ phiên")
        else:
            lines.append(f"  - Giá ≥ {name.upper()}: {'✅ Đạt' if ok else '❌ Không đạt'} (Giá: {_vn(price)} | {name.upper()}: {_vn(ma)})")
    verdict = None if any(c is None for c in checks) else all(checks)
    return lines, verdict


def narrative(code: str, s: dict, tech: dict | None, shark: dict | None) -> list[str]:
    """The reading and what it means for a holder and a buyer, from the numbers alone."""
    price = s["price"]
    if tech is None or tech.get("ma20") is None:
        return [f"Nhận định nhanh: {code} chưa đủ lịch sử giá để đọc xu hướng (cần từ 20 phiên)."]
    ma20, ma50 = tech["ma20"], tech.get("ma50")
    move = ("giảm" if s["change_pct"] < 0 else "tăng" if s["change_pct"] > 0 else "đứng giá") if s.get("change_pct") is not None else ""
    move_text = f", {move} {_vn(abs(s['change_pct']), 2)}%" if move and move != "đứng giá" else (", đứng giá" if move else "")
    above20 = price >= ma20
    above50 = price >= ma50 if ma50 is not None else None
    if above50 is None:
        trend = f"{'nằm trên' if above20 else 'nằm dưới'} MA20 tại {_vn(ma20)} (chưa đủ 50 phiên cho MA50)"
        state = "up" if above20 else "down"
    elif above20 and above50:
        trend = f"nằm trên MA20 tại {_vn(ma20)} và MA50 tại {_vn(ma50)}"
        state = "up"
    elif not above20 and not above50:
        trend = f"nằm dưới MA20 tại {_vn(ma20)} và đã thủng hỗ trợ MA50 quanh {_vn(ma50)}"
        state = "down"
    elif above50:
        trend = f"nằm dưới MA20 tại {_vn(ma20)} nhưng còn giữ trên MA50 quanh {_vn(ma50)}"
        state = "pull"
    else:
        trend = f"đã lấy lại MA20 tại {_vn(ma20)} nhưng còn dưới MA50 quanh {_vn(ma50)}"
        state = "recover"
    parts = [f"{code} đang ở mức {_vn(price)}{move_text}, {trend}."]
    rsi, macd, bb = tech.get("rsi"), tech.get("macd"), tech.get("bb")
    bits = []
    if rsi is not None:
        zone = "ở vùng quá bán" if rsi < RSI_OVERSOLD else "ở vùng quá mua" if rsi > RSI_OVERBOUGHT else "ở vùng trung tính"
        bits.append(f"RSI {rsi:.0f} {zone}")
    if macd is not None:
        if macd.crossover == "bullish":
            bits.append("MACD vừa cắt lên đường tín hiệu")
        elif macd.crossover == "bearish":
            bits.append("MACD vừa cắt xuống đường tín hiệu")
        else:
            soft = " nhẹ" if abs(macd.histogram) < price * 0.005 else ""
            bits.append(f"MACD còn {'bearish' if macd.histogram < 0 else 'bullish'}{soft}")
    if bits:
        parts.append("; ".join(bits) + ".")
    if bb is not None:
        shape = "đang có xu hướng thắt hẹp" if bb.squeeze else "đang mở rộng" if bb.width > 15 else "ở độ rộng bình thường"
        half = "nửa dưới" if bb.pct_b < 50 else "nửa trên"
        parts.append(f"Dải Bollinger {shape} với BB% quanh {_vn(bb.pct_b / 100, 2)}, giá vận động chủ yếu ở {half} dải.")
    if s.get("volume") and s.get("volume_ratio"):
        ratio = s["volume_ratio"]
        read = ("đột biến, có dòng tiền vào/ra mạnh" if ratio >= SPIKE_RATIO else "cạn kiệt, bên mua lẫn bên bán đều thờ ơ"
                if ratio <= DRY_RATIO else "thanh khoản duy trì ở mức cân bằng")
        parts.append(f"Khối lượng đạt khoảng {_vn_volume(s['volume'])} cổ phiếu, bằng {_vn(ratio, 2)} lần trung bình 20 phiên"
                     f" cho thấy {read}.")
    lo, hi = tech.get("range_lo"), tech.get("range_hi")
    if lo and hi:
        width = (hi - lo) / price * 100
        parts.append(f"Biên độ {RANGE_SESSIONS} phiên gần nhất dao động {'tích lũy hẹp' if width < NARROW_RANGE_PCT else 'rộng'} "
                     f"từ {_vn(lo)} đến {_vn(hi)}.")
    if shark and shark.get("ticks"):
        icon, say = camap.verdict(shark, s.get("change_pct"))
        parts.append(f"Dòng tiền cá mập: {say}")
    lines = ["Nhận định nhanh: " + " ".join(parts), ""]
    lines += _advice(state, s, tech)
    return lines


def _advice(state: str, s: dict, tech: dict) -> list[str]:
    price, ma20, ma50 = s["price"], tech["ma20"], tech.get("ma50")
    lo = tech.get("range_lo") or price
    rsi = tech.get("rsi")
    if state == "down":
        hold = (f"Nếu đang giữ, do giá đã đánh mất mốc hỗ trợ MA50 ({_vn(ma50)}) và nằm dưới MA20 ({_vn(ma20)}), "
                "em nghiêng về ưu tiên quản trị rủi ro; trường hợp có nhịp hồi kỹ thuật kiểm định lại vùng "
                f"{_vn(ma20)} – {_vn(ma50)} mà thanh khoản không đột biến thì nên chủ động cơ cấu, hạ tỷ trọng bảo toàn vốn. "
                f"Kịch bản tích cực là cần tích lũy lại tạo nền đáy vững chắc quanh {_vn(lo)} trước khi có tín hiệu dòng tiền quay lại."
                if ma50 is not None else
                f"Nếu đang giữ, giá đang dưới MA20 ({_vn(ma20)}): ưu tiên quản trị rủi ro, hạ tỷ trọng khi nhịp hồi lên MA20 "
                "không kèm thanh khoản.")
        buy = ("Mua mới thì nên kiên nhẫn đứng ngoài quan sát, chưa vội bắt đáy khi xu hướng ngắn hạn chưa xác nhận đảo chiều"
               + (" (RSI quá bán chỉ nói giá đã rơi nhanh, không nói đáy)" if rsi is not None and rsi < RSI_OVERSOLD else "") + ".")
    elif state == "up":
        hot = rsi is not None and rsi > RSI_OVERBOUGHT
        hold = (f"Nếu đang giữ, xu hướng còn thuận: tiếp tục nắm giữ, dời điểm dừng lỗ lên dưới MA20 ({_vn(ma20)})"
                + ("; RSI quá mua nên cân nhắc chốt một phần khi khối lượng đột biến ở vùng đỉnh." if hot else "."))
        buy = (f"Mua mới chỉ ở nhịp điều chỉnh về gần MA20 ({_vn(ma20)}), không mua đuổi khi giá đã cách MA20 "
               f"{_vn((price / ma20 - 1) * 100, 1)}%.")
    elif state == "pull":
        hold = (f"Nếu đang giữ, đây là nhịp điều chỉnh trong xu hướng còn giữ MA50 ({_vn(ma50)}): giữ với điểm dừng lỗ dưới MA50; "
                f"giá lấy lại MA20 ({_vn(ma20)}) kèm khối lượng là tín hiệu tích cực, thủng MA50 thì hạ tỷ trọng.")
        buy = f"Mua mới nên chờ giá lấy lại MA20 ({_vn(ma20)}) với khối lượng trên trung bình, hoặc mua thăm dò sát MA50 với điểm dừng lỗ ngắn."
    else:   # recover
        hold = (f"Nếu đang giữ, giá đã hồi qua MA20 ({_vn(ma20)}) nhưng còn dưới MA50 ({_vn(ma50)}): giữ với điểm dừng lỗ dưới đáy "
                f"{RANGE_SESSIONS} phiên ({_vn(lo)}); mốc cần vượt để xác nhận đảo chiều là MA50.")
        buy = f"Mua mới có thể thăm dò tỷ trọng nhỏ khi giá giữ trên MA20; chỉ tăng tỷ trọng khi vượt MA50 ({_vn(ma50)}) kèm khối lượng."
    return [hold, "", buy]


def text(card: dict) -> str:
    code, s, tech = card["code"], card["session"], card["tech"]
    when = (f"realtime lúc {card['now']:%H:%M %d/%m}" if s["live"]
            else f"phiên {s['day'][8:10]}/{s['day'][5:7]}" + ("" if s["day"] == card["now"].strftime("%Y-%m-%d") else " (phiên gần nhất)"))
    head = f"📊 PHÂN TÍCH NHANH: {code} · {when}"
    change = ""
    if s.get("change_pct") is not None:
        sign = "+" if s["change"] > 0 else ""
        change = f" ({sign}{_vn(s['change_pct'], 2)}% | {sign}{_vn(s['change'])})"
    lines = [head, BAR, f"{_icon(s.get('change'))} Giá khớp: {_vn(s['price'])}{change}"]
    if s.get("ref"):
        lines.append(f"• Tham chiếu: {_vn(s['ref'])} | Trần: {_vn(s['ceiling'])} | Sàn: {_vn(s['floor'])}")
    if s.get("volume"):
        ratio = f" ({_vn(s['volume_ratio'] * 100, 1)}% so với MA20)" if s.get("volume_ratio") else ""
        lines.append(f"• Khối lượng khớp: {_vn(s['volume'])} CP{ratio}" + (" · tới lúc này" if s["live"] else ""))
    else:
        lines.append("• Khối lượng khớp: chưa có")
    lines.append(_foreign_line(card.get("foreign"), s["day"]))
    if s.get("avg_value"):
        n = s.get("avg_sessions", AVG_SESSIONS)
        lines.append(f"• Giá trị trung bình {n} phiên: {_bn(s['avg_value'])}")
        lines.append(f"• Khối lượng trung bình {n} phiên: {_vn(s['avg_volume'])} CP")
    else:
        lines.append("• Trung bình 20 phiên: chưa lấy được (VNDirect)")
    lines.append(_pb_line(card.get("pb"), s["price"]))
    lines.append(_rs_line(card.get("rs"), code))
    lines.append(BAR)
    sepa, verdict = _sepa_lines(card.get("fund"), tech, s["price"])
    lines += sepa
    lines.append("")
    lines.append("Bổ sung: Tiêu chí CANSLIM: " + ("✅ Đạt" if verdict else "❌ Không Đạt" if verdict is False else "⚪ chưa đủ số")
                 + " (đạt khi thoả mãn tất cả tiêu chí bên trên)")
    lines.append(BAR)
    shark = card.get("shark")
    if shark is not None:
        lines.append(f"🦈 Dòng tiền cá mập phiên {s['day'][8:10]}/{s['day'][5:7]} (lệnh khớp chủ động, DNSE):")
        lines += camap.lines(shark, s.get("change_pct"))
    else:
        lines.append("🦈 Dòng tiền cá mập: chưa lấy được tick của DNSE (/camap " + code + " để thử lại)")
    lines.append(BAR)
    lines += narrative(code, s, tech, shark)
    lines += ["", "Tổng hợp từ số liệu, không phải khuyến nghị. Quyết định của hệ thống định lượng: /nhanh " + code]
    return "\n".join(lines)


def command(args: str, data_dir, now: datetime | None = None, progress=None) -> str:
    """/soi MÃ on Telegram and Zoom. ``progress``: told first (the sources take a few seconds)."""
    from vnbot.service import check_request

    words = (args or "").split()
    if not words:
        raise ValueError("Nhập mã cổ phiếu, ví dụ: /soi FPT")
    if len(words) > 1:
        raise ValueError("Mỗi lần soi một mã: /soi FPT")
    code, _ = check_request(words[0])
    if progress is not None:
        progress(f"⏳ Đang soi {code} (giá, khối ngoại, báo cáo tài chính, tick khớp lệnh: 5-20 giây)...")
    from vnbot.quote import Unknown

    try:
        card = build(code, data_dir, now)
    except Unknown:
        return f"❔ {code}: không có mã này trên HOSE/HNX/UPCoM."
    return text(card)
