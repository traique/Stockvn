"""Giá realtime khi gõ mã, không gọi AI: cổ phiếu và chỉ số (DNSE), coin (OKX).

Cổ phiếu, trên Telegram và Zoom (vnbot.telegram_bot): một tin chỉ gồm mã - "FPT", "fpt hpg",
"giá FPT", "VNINDEX", "/FPT", Zoom "FPT" - được trả giá của từng mã (tối đa ``MAX_SYMBOLS``) thay
cho bảng lệnh. Tin có chữ khác ("FPT có nên mua", "hello") vẫn nhận bảng lệnh như cũ.

- Mã: 3 chữ/số (FPT, HPG...) hoặc chỉ số VNINDEX (VNI, VN-Index), VN30, HNX, HNX30, UPCOM.
- Giá: tick khớp gần nhất của hôm nay (DNSE price-api) khi có, không thì giá đóng cửa của nến
  ngày cuối (DNSE chart-api, trong phiên là nến đang chạy); so với giá đóng cửa phiên trước
  (tham chiếu: DNSE đã điều chỉnh cổ tức/thưởng nên ngày GDKHQ vẫn đúng). Kèm mở, cao, thấp và
  khối lượng của phiên.
- Nhiều phiên: thêm một kỳ vào tin - "FPT 7d", "fpt hpg 14d", "FPT 1w", "VNINDEX 1m", "giá FPT 7 ngày" - là
  giá đóng cửa từng phiên của N phiên giao dịch gần nhất (d/ngày/phiên: 1 phiên, w/tuần: 5, m/tháng: 22;
  tối đa ``MAX_SESSIONS``), mới nhất trước, kèm +/- % mỗi phiên và khối lượng, tổng thay đổi cả kỳ so với
  giá đóng cửa của phiên trước kỳ, cao/thấp nhất và khối lượng trung bình. Trong giờ giao dịch phiên hôm
  nay lấy giá khớp gần nhất. Giá DNSE đã điều chỉnh cổ tức/thưởng.
- Thời điểm: "realtime lúc HH:MM" chỉ trong giờ giao dịch (9:00-15:00 ngày thường); ngoài giờ là
  giá đóng cửa phiên gần nhất (tick cuối phiên còn đó cả đêm, không gọi là realtime). Mã có
  phiên cuối khác hôm nay (đang nghỉ, bị ngừng giao dịch) ghi rõ ngày phiên đó.

Coin ("alt btc", "/alt btc", "alt eth sol", on Telegram and Zoom; vnbot.altcoin_bridge): giá khớp gần nhất trên
OKX - hợp đồng USDT-SWAP mà bot Altcoin giao dịch, không có thì cặp spot USDT -, % thay đổi, cao,
thấp và khối lượng 24h. API công khai, không cần khoá, không qua bot Altcoin (chạy cả khi Altcoin
tắt). "BTCUSDT", "BTC-USDT", "BTC/USDT:USDT" đều hiểu là BTC.

Coin nhiều ngày ("alt btc 7d", "alt btc eth 14d", "alt sol 1m", "alt btc 7 ngày"): nến ngày OKX theo UTC (mở
07:00 giờ Việt Nam; crypto giao dịch cả tuần nên d là ngày lịch, w: 7 ngày, m: 30 ngày; tối đa
``MAX_COIN_DAYS``), mới nhất trước: giá đóng cửa, % so với ngày trước, khối lượng USDT; nến hôm nay còn
chạy ghi "(đang chạy)". Đầu khối: thay đổi cả kỳ so với giá đóng cửa của ngày trước kỳ, cao/thấp nhất,
khối lượng trung bình.
"""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests

logger = logging.getLogger(__name__)

MAX_SYMBOLS = 5
LOOKBACK_DAYS = 20               # nến ngày đủ để có phiên trước, cả sau kỳ nghỉ Tết
MAX_SESSIONS = 60                # "FPT 7d": tối đa bấy nhiêu phiên
PERIOD_UNITS = {"d": 1, "ngay": 1, "ngày": 1, "phien": 1, "phiên": 1, "w": 5, "tuan": 5, "tuần": 5,
                "m": 22, "thang": 22, "tháng": 22}
PRICE_WORDS = ("giá", "gia", "price")
OKX_TICKER = "https://www.okx.com/api/v5/market/ticker"
OKX_CANDLES = "https://www.okx.com/api/v5/market/candles"
MAX_COIN_DAYS = 90                 # "alt btc 7d": tối đa bấy nhiêu ngày (OKX trả tới 300 nến một lần)
COIN_PERIOD_UNITS = {"d": 1, "ngay": 1, "ngày": 1, "w": 7, "tuan": 7, "tuần": 7, "m": 30, "thang": 30, "tháng": 30}
OKX_TIMEOUT = 8
OKX_UNKNOWN = "51001"            # OKX: mã hợp đồng không tồn tại

_SPLIT = re.compile(r"[\s,;+&]+")
_PERIOD = re.compile(r"(\d{1,3})(d|ngay|ngày|phien|phiên|w|tuan|tuần|m|thang|tháng)")
_STOCK_CODE = re.compile(r"[A-Z0-9]{3}")
_COIN = re.compile(r"[A-Z0-9]{2,15}")
_COIN_SUFFIX = re.compile(r"[-/]?USDT(?:[-:](?:SWAP|USDT))?$")
_PHASES = {"ato": "phiên ATO", "continuous": "đang khớp lệnh", "lunch": "nghỉ trưa", "atc": "phiên ATC",
           "put_through": "sau ATC, giao dịch thoả thuận"}


class Unknown(LookupError):
    """Không có mã này (cổ phiếu: DNSE không biết; coin: OKX không có cặp USDT)."""


# ─── số ──────────────────────────────────────────────────────────────────────


def _vn(value: float, decimals: int = 0) -> str:
    """Số kiểu Việt Nam: 45.300 · 1.688,79."""
    text = f"{value:,.{decimals}f}"
    return text.replace(",", "\0").replace(".", ",").replace("\0", ".")


def _vn_volume(value: float) -> str:
    if value >= 1e9:
        return f"{_vn(value / 1e9, 2)} tỷ"
    if value >= 1e6:
        return f"{_vn(value / 1e6, 2)} triệu"
    return _vn(value)


def _okx_number(raw) -> str:
    """Giá OKX đúng như sàn trả (đủ số lẻ của bước giá), thêm dấu phẩy hàng nghìn: 58,931.8 · 0.0000034418."""
    text = str(raw).strip()
    whole, _, frac = text.partition(".")
    try:
        whole = f"{int(whole):,}"
    except ValueError:
        return text
    return f"{whole}.{frac}" if frac else whole


def _okx_volume(usdt: float) -> str:
    if usdt >= 1e9:
        return f"{usdt / 1e9:.2f} tỷ USDT"
    if usdt >= 1e6:
        return f"{usdt / 1e6:.1f} triệu USDT"
    return f"{usdt:,.0f} USDT"


def _icon(change: float | None) -> str:
    if change is None:
        return "⚪"
    return "🟢" if change > 0 else "🔴" if change < 0 else "🟡"


# ─── cổ phiếu ────────────────────────────────────────────────────────────────


def stock_codes(text: str) -> list[str] | None:
    """Các mã của một tin chỉ gồm mã (và chữ "giá", và một kỳ như "7d"), theo thứ tự gõ, không trùng;
    None khi tin có chữ khác. "/FPT", "/FPT@TenBot" và chữ "/" Zoom thêm vào đầu tin đều được bỏ."""
    parsed = parse_lookup(text)
    return parsed[0] if parsed else None


def _period(word: str, following: str = "") -> tuple[int, bool] | None:
    """(số phiên, dùng cả chữ kế tiếp) của một kỳ: "7d", "14D", "2w", "1m", "7ngày"; hoặc "7" rồi "ngày"."""
    low = word.lower()
    found = _PERIOD.fullmatch(low)
    if found:
        return int(found.group(1)) * PERIOD_UNITS[found.group(2)], False
    if low.isdigit() and len(low) <= 3 and following.lower() in PERIOD_UNITS and len(following) > 1:
        return int(low) * PERIOD_UNITS[following.lower()], True
    return None


def parse_lookup(text: str) -> tuple[list[str], int | None] | None:
    """(các mã, số phiên của kỳ hoặc None) của một tin tra giá; None khi tin có chữ khác (bảng lệnh)."""
    from tradingagents.dataflows.symbols import normalize_symbol
    from tradingagents.market import vn_rules

    words = [w for w in _SPLIT.split((text or "").strip()) if w]
    if words:
        words[0] = words[0].lstrip("/").split("@")[0]
    words = [w.strip("?!.:") for w in words]
    codes: list[str] = []
    sessions = None
    i = 0
    while i < len(words):
        word = words[i]
        i += 1
        if not word or word.lower() in PRICE_WORDS:
            continue
        period = _period(word, words[i] if i < len(words) else "")
        if period is not None:                   # checked before the codes: "14D" is 3 characters
            sessions, used_next = period
            i += used_next
            continue
        code = normalize_symbol(word)
        if not (vn_rules.is_index(code) or _STOCK_CODE.fullmatch(code)):
            return None
        if code not in codes:
            codes.append(code)
    return (codes, sessions) if codes else None


def _bars(code: str, start: str, end: str):
    from tradingagents.dataflows.vendors import dnse

    return dnse.fetch_ohlcv(code, start, end)


def _tick(code: str, day: str) -> float | None:
    from tradingagents.dataflows.vendors import dnse

    return dnse.fetch_last_match(code, day)


def stock_quote(code: str, now: datetime | None = None) -> dict:
    """{code, price, ref, open, high, low, volume, day, index} của một mã. ``ref``: giá đóng cửa phiên
    trước (None khi mã mới niêm yết); open/high/low/volume None khi hôm nay đã khớp mà nến chưa có.
    Unknown khi DNSE không biết mã; lỗi mạng (VendorUnavailableError...) để người gọi báo."""
    from tradingagents.dataflows.errors import NoMarketDataError
    from tradingagents.market import vn_rules

    now = (now or vn_rules.now_vn()).astimezone(vn_rules.VN_TZ)
    today = now.strftime("%Y-%m-%d")
    try:
        frame = _bars(code, (now - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d"), today)
    except NoMarketDataError as exc:
        raise Unknown(code) from exc
    rows = frame.to_dict("records") if frame is not None else []
    if not rows:
        raise Unknown(code)
    index = vn_rules.is_index(code)
    tick = None
    if not index and vn_rules.session_phase(now) not in ("closed", "pre_open"):
        try:
            tick = _tick(code, today)
        except Exception as exc:  # noqa: BLE001 - không có tick thì dùng giá của nến
            logger.info("Giá khớp %s: %s", code, exc)
    last = rows[-1]
    if last["Date"] != today and tick:           # hôm nay đã khớp, nguồn nến chưa có nến hôm nay
        bar, ref, day = None, last["Close"], today
    else:
        bar, day = last, last["Date"]
        ref = rows[-2]["Close"] if len(rows) >= 2 else None
    price = tick or last["Close"]
    return {"code": code, "price": float(price), "ref": float(ref) if ref else None, "day": day, "index": index,
            "open": bar and bar["Open"], "high": bar and bar["High"], "low": bar and bar["Low"],
            "volume": bar and bar["Volume"]}


def _stock_lines(q: dict, today: str) -> list[str]:
    decimals = 2 if q["index"] else 0
    unit = " điểm" if q["index"] else ""
    head = f"{q['code']}: {_vn(q['price'], decimals)}{unit}"
    change = None
    if q["ref"]:
        change = q["price"] - q["ref"]
        sign = "+" if change > 0 else ""
        head += f" {sign}{_vn(change, decimals)} ({sign}{_vn(change / q['ref'] * 100, 2)}%)"
    if q["day"] != today:
        head += f" · phiên {q['day'][8:10]}/{q['day'][5:7]}"
    detail = [f"TC {_vn(q['ref'], decimals)}"] if q["ref"] else []
    if q["open"] is not None:
        detail += [f"Mở {_vn(q['open'], decimals)}", f"Cao {_vn(q['high'], decimals)}",
                   f"Thấp {_vn(q['low'], decimals)}"]
    if q["volume"]:
        detail.append(f"KL {_vn_volume(q['volume'])}")
    return [f"{_icon(change)} {head}"] + (["   " + " · ".join(detail)] if detail else [])


def _stock_days(code: str, start: str, end: str):
    from tradingagents.dataflows.vendors import flows

    return flows.fetch_stock_days(code, start, end)


def _row(r: dict) -> dict:
    return {k: r.get(k) for k in ("Date", "Close", "Ref", "High", "Low", "Volume")}


def stock_history(code: str, sessions: int, now: datetime | None = None) -> dict:
    """Các phiên gần nhất của một mã: ``{code, index, rows, live_day, raw}`` - ``rows`` tối đa ``sessions``
    phiên cũ nhất trước, mỗi phiên ``{Date, Close, Ref, High, Low, Volume}``.

    Cổ phiếu: giá khớp thực tế của VNDirect (``raw``), mỗi phiên kèm giá tham chiếu của chính nó, nên %
    từng phiên đúng như bảng giá, cả ngày GDKHQ. Phiên hôm nay trong giờ: giá khớp gần nhất của DNSE,
    tham chiếu là giá đóng cửa phiên trước theo DNSE (đã điều chỉnh nếu hôm nay là ngày GDKHQ), cao/thấp/KL
    của nến đang chạy. Chỉ số, hoặc VNDirect lỗi: nến DNSE đã điều chỉnh cổ tức/thưởng, tham chiếu là giá
    đóng cửa phiên trước. ``live_day``: ngày của phiên còn đang chạy (None ngoài giờ). Unknown khi không
    nguồn nào biết mã."""
    from tradingagents.dataflows.errors import NoMarketDataError
    from tradingagents.market import vn_rules

    now = (now or vn_rules.now_vn()).astimezone(vn_rules.VN_TZ)
    today = now.strftime("%Y-%m-%d")
    start = (now - timedelta(days=int(sessions * 1.6) + LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    index = vn_rules.is_index(code)
    in_session = vn_rules.is_session_day(today) and vn_rules.is_market_hours(now)
    rows: list[dict] = []
    raw = False
    if not index:
        try:
            frame = _stock_days(code, start, today)
            rows = [_row(r) for r in frame.to_dict("records")] if frame is not None else []
            raw = bool(rows)
        except Exception as exc:  # noqa: BLE001 - DNSE's adjusted bars instead
            logger.info("Giá nhiều phiên %s từ VNDirect: %s", code, exc)
    trading = vn_rules.is_session_day(today) and vn_rules.session_phase(now) not in ("closed", "pre_open")
    today_missing = not rows or rows[-1]["Date"] != today
    dnse: list[dict] = []
    if not raw or (trading and today_missing):
        try:
            frame = _bars(code, start if not raw else (now - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d"), today)
            dnse = [dict(r) for r in frame.to_dict("records")] if frame is not None else []
        except NoMarketDataError as exc:
            if not raw:
                raise Unknown(code) from exc
        except Exception:
            if not raw:
                raise                            # both sources down: the caller says so
        before = None
        for bar in dnse:                         # DNSE's reference: the close before, on today's basis
            bar["Ref"] = before
            before = bar["Close"]
        if not raw:
            rows = [_row(b) for b in dnse]
    if not rows:
        raise Unknown(code)
    if trading and (not raw or rows[-1]["Date"] != today):
        tick = None
        if not index:
            try:
                tick = _tick(code, today)
            except Exception as exc:  # noqa: BLE001 - không có tick thì dùng giá của nến
                logger.info("Giá khớp %s: %s", code, exc)
        bar = next((b for b in dnse if b["Date"] == today), None)
        if rows[-1]["Date"] == today:            # DNSE's forming bar: the last match closes it
            if tick:
                last = rows[-1]
                last.update(Close=float(tick), High=max(last["High"] or tick, tick), Low=min(last["Low"] or tick, tick))
        elif tick or bar is not None:            # VNDirect has not posted today: the bar and the last match
            price = float(tick or bar["Close"])
            ref = bar["Ref"] if bar is not None else (dnse[-1]["Close"] if dnse else rows[-1]["Close"])
            rows.append({"Date": today, "Close": price, "Ref": ref,
                         "High": max(bar["High"], price) if bar is not None else price,
                         "Low": min(bar["Low"], price) if bar is not None else price,
                         "Volume": bar["Volume"] if bar is not None else None})
    chosen = rows[-sessions:]
    live_day = today if in_session and chosen[-1]["Date"] == today else None
    return {"code": code, "index": index, "rows": chosen, "live_day": live_day, "raw": raw}


def _signed(value: float, decimals: int) -> str:
    return ("+" if value > 0 else "") + _vn(value, decimals)


def _history_lines(h: dict) -> list[str]:
    """Một khối của "FPT 7d": thay đổi cả kỳ, cao/thấp, KL trung bình, rồi từng phiên mới nhất trước."""
    decimals = 2 if h["index"] else 0
    unit = " điểm" if h["index"] else ""
    rows = h["rows"]
    last = rows[-1]["Close"]
    span = f"{rows[0]['Date'][8:10]}/{rows[0]['Date'][5:7]} – {rows[-1]['Date'][8:10]}/{rows[-1]['Date'][5:7]}"
    # A session whose reference is not the close before it: an ex-date (cash or stock dividend, bonus,
    # rights), the reference adjusted by the exchange. Its gap is not a loss.
    ex_days = [r["Date"] for r, before in zip(rows[1:], rows[:-1], strict=False)
               if r["Ref"] and before["Close"] and abs(r["Ref"] - before["Close"]) >= 0.5]
    growth, known = 1.0, 0
    for r in rows:
        if r["Ref"]:
            growth *= r["Close"] / r["Ref"]
            known += 1
    head = f"{h['code']}: {_vn(last, decimals)}{unit}"
    change = None
    if known:
        change = (growth - 1) * 100
        if ex_days:
            head += f" {_signed(change, 2)}% sau {known} phiên"
        else:
            base = rows[0]["Ref"] or rows[0]["Close"]
            head += f" {_signed(last - base, decimals)} ({_signed(change, 2)}%) sau {known} phiên"
    else:
        head += f" · {len(rows)} phiên từ ngày niêm yết"
    out = [f"{_icon(change)} {head} ({span})"]
    if ex_days:
        out.append("   Qua ngày GDKHQ " + ", ".join(f"{d[8:10]}/{d[5:7]}" for d in ex_days)
                   + ": % cả kỳ cộng dồn theo giá tham chiếu từng phiên, không tính phần chia cổ tức/quyền")
    high = max(rows, key=lambda r: r["High"] or r["Close"])
    low = min(rows, key=lambda r: r["Low"] or r["Close"])
    summary = [f"Cao nhất {_vn(high['High'] or high['Close'], decimals)} ({high['Date'][8:10]}/{high['Date'][5:7]})",
               f"Thấp nhất {_vn(low['Low'] or low['Close'], decimals)} ({low['Date'][8:10]}/{low['Date'][5:7]})"]
    volumes = [r["Volume"] for r in rows if r["Volume"] and r["Date"] != h["live_day"]]
    if volumes:
        summary.append(f"KL TB {_vn_volume(sum(volumes) / len(volumes))}")
    out.append("   " + " · ".join(summary))
    for row in reversed(rows):
        line = f"   {row['Date'][8:10]}/{row['Date'][5:7]}: {_vn(row['Close'], decimals)}"
        if row["Ref"]:
            day_change = row["Close"] - row["Ref"]
            line += f" {_icon(day_change)} {_signed(day_change / row['Ref'] * 100, 2)}%"
        if row["Volume"]:
            line += f" · KL {_vn_volume(row['Volume'])}"
        if row["Date"] in ex_days:
            line += " · GDKHQ"
        if row["Date"] == h["live_day"]:
            line += " (đang khớp)"
        out.append(line)
    return out


def _parallel(fn, items: list) -> list:
    """fn(item) cho từng item, cùng lúc (tối đa MAX_SYMBOLS luồng); lỗi trả về đúng chỗ của nó."""
    def safe(item):
        try:
            return fn(item)
        except Exception as exc:  # noqa: BLE001 - mỗi mã một câu trả lời riêng
            return exc

    if len(items) == 1:
        return [safe(items[0])]
    with ThreadPoolExecutor(max_workers=min(len(items), MAX_SYMBOLS), thread_name_prefix="quote") as pool:
        return list(pool.map(safe, items))


def stocks_reply(text: str, now: datetime | None = None) -> str | None:
    """Câu trả lời cho một tin chỉ gồm mã cổ phiếu/chỉ số; None khi tin không phải vậy (bảng lệnh)."""
    from tradingagents.market import vn_rules

    parsed = parse_lookup(text)
    if not parsed:
        return None
    codes, sessions = parsed
    now = (now or vn_rules.now_vn()).astimezone(vn_rules.VN_TZ)
    if sessions is not None:
        return _history_reply(codes, sessions, now)
    today = now.strftime("%Y-%m-%d")
    more = len(codes) > MAX_SYMBOLS
    codes = codes[:MAX_SYMBOLS]
    phase = vn_rules.session_phase(now)
    if vn_rules.is_market_hours(now) and vn_rules.is_session_day(today):
        lines = [f"💹 Giá realtime lúc {now:%H:%M %d/%m} ({_PHASES.get(phase, 'trong phiên')})"]
    else:
        lines = [f"💹 Giá lúc {now:%H:%M %d/%m} (ngoài giờ giao dịch: giá đóng cửa phiên gần nhất)"]
    unknown = []
    for code, result in zip(codes, _parallel(lambda c: stock_quote(c, now), codes)):
        if isinstance(result, Unknown):
            unknown.append(code)
            lines.append(f"❔ {code}: không có mã này trên HOSE/HNX/UPCoM")
        elif isinstance(result, Exception):
            logger.warning("Giá %s: %s", code, result)
            lines.append(f"⚠️ {code}: chưa lấy được giá (nguồn giá DNSE không trả lời), thử lại sau ít phút")
        else:
            lines.extend(_stock_lines(result, today))
    if more:
        lines.append(f"(Mỗi lần tra tối đa {MAX_SYMBOLS} mã.)")
    if unknown:
        lines.append("Giá coin: gõ alt btc. Danh sách lệnh: /trogiup.")
    return "\n".join(lines)


def _history_reply(codes: list[str], sessions: int, now: datetime) -> str:
    """Câu trả lời của "FPT 7d": một khối mỗi mã."""
    from tradingagents.market import vn_rules

    capped = sessions > MAX_SESSIONS
    sessions = max(1, min(sessions, MAX_SESSIONS))
    more = len(codes) > MAX_SYMBOLS
    codes = codes[:MAX_SYMBOLS]
    today = now.strftime("%Y-%m-%d")
    if vn_rules.is_market_hours(now) and vn_rules.is_session_day(today):
        head = f"📅 Giá {sessions} phiên gần nhất · lúc {now:%H:%M %d/%m} (phiên hôm nay theo giá khớp, đang chạy)"
    else:
        head = f"📅 Giá {sessions} phiên gần nhất · lúc {now:%H:%M %d/%m} (giá đóng cửa)"
    lines = [head]
    if capped:
        lines.append(f"(Mỗi lần xem tối đa {MAX_SESSIONS} phiên.)")
    unknown, raw, adjusted = False, False, False
    for code, result in zip(codes, _parallel(lambda c: stock_history(c, sessions, now), codes), strict=True):
        lines.append("")
        if isinstance(result, Unknown):
            unknown = True
            lines.append(f"❔ {code}: không có mã này trên HOSE/HNX/UPCoM")
        elif isinstance(result, Exception):
            logger.warning("Giá nhiều phiên %s: %s", code, result)
            lines.append(f"⚠️ {code}: chưa lấy được giá (nguồn giá không trả lời), thử lại sau ít phút")
        else:
            raw = raw or result["raw"]
            adjusted = adjusted or (not result["raw"] and not result["index"])
            lines.extend(_history_lines(result))
    if more:
        lines.append(f"(Mỗi lần tra tối đa {MAX_SYMBOLS} mã.)")
    lines.append("")
    if raw:
        lines.append("Giá khớp thực tế từng phiên (VNDirect), % so với tham chiếu của phiên như bảng giá.")
    if adjusted:
        lines.append("Mã ghi giá DNSE đã điều chỉnh cổ tức/thưởng (VNDirect chưa trả lời).")
    lines.append("Kỳ khác: FPT 14d · FPT 2w (10 phiên) · FPT 1m (22 phiên), tối đa 60 phiên.")
    if unknown:
        lines.append("Giá coin: gõ alt btc. Danh sách lệnh: /trogiup.")
    return "\n".join(lines)


# ─── coin (OKX) ──────────────────────────────────────────────────────────────


def coin_symbols(words: list[str]) -> list[str] | None:
    """Các coin của "/alt btc eth" (theo thứ tự, không trùng): BTCUSDT, BTC-USDT-SWAP, BTC/USDT:USDT là
    BTC. None khi có một chữ không phải dạng mã coin (khi đó "/alt" trả bảng lệnh như cũ). Một kỳ ("7d")
    được bỏ qua ở đây: ``coin_lookup`` đọc nó."""
    parsed = coin_lookup(words)
    return parsed[0] if parsed else None


_COIN_PERIOD = re.compile(r"(\d{1,3})(d|ngay|ngày|w|tuan|tuần|m|thang|tháng)")


def _coin_period(word: str, following: str = "") -> tuple[int, bool] | None:
    """(số ngày, dùng cả chữ kế tiếp) của một kỳ coin: "7d", "2w", "1m", "7ngày"; hoặc "7" rồi "ngày"."""
    low = word.lower().strip("?!.,")
    found = _COIN_PERIOD.fullmatch(low)
    if found:
        return int(found.group(1)) * COIN_PERIOD_UNITS[found.group(2)], False
    unit = following.lower().strip("?!.,")
    if low.isdigit() and len(low) <= 3 and unit in COIN_PERIOD_UNITS and len(unit) > 1:
        return int(low) * COIN_PERIOD_UNITS[unit], True
    return None


def coin_lookup(words: list[str]) -> tuple[list[str], int | None] | None:
    """(các coin, số ngày của kỳ hoặc None) của "/alt btc eth 7d"; None khi có một chữ không phải mã coin."""
    coins: list[str] = []
    days = None
    i = 0
    while i < len(words):
        raw = words[i]
        i += 1
        period = _coin_period(raw, words[i] if i < len(words) else "")
        if period is not None:                   # checked first: "14D" has the shape of a coin code
            days, used_next = period
            i += used_next
            continue
        word = raw.upper().strip("?!.,")
        base = word if word == "USDT" else _COIN_SUFFIX.sub("", word)
        if not _COIN.fullmatch(base):
            return None
        if base not in coins:
            coins.append(base)
    return (coins, days) if coins else None


def _okx_ticker(inst_id: str) -> dict | None:
    """Ticker OKX của một instId; None khi OKX không có instId đó."""
    r = requests.get(OKX_TICKER, params={"instId": inst_id}, timeout=OKX_TIMEOUT)
    body = r.json()
    if str(body.get("code")) == OKX_UNKNOWN:
        return None
    if str(body.get("code")) != "0":
        raise RuntimeError(f"OKX {body.get('code')}: {body.get('msg') or r.status_code}")
    return (body.get("data") or [None])[0]


def coin_quote(symbol: str) -> dict:
    """{symbol, inst, swap, last, open24h, high24h, low24h, volume_usdt, ts} trên OKX. Unknown khi OKX không có
    cả hợp đồng {symbol}-USDT-SWAP lẫn cặp spot {symbol}-USDT."""
    for inst, swap in ((f"{symbol}-USDT-SWAP", True), (f"{symbol}-USDT", False)):
        row = _okx_ticker(inst)
        if not row or not row.get("last"):
            continue
        last = float(row["last"])
        vol = float(row.get("volCcy24h") or 0)
        # USDT-SWAP: volCcy24h tính bằng coin; spot: bằng USDT
        return {"symbol": symbol, "inst": inst, "swap": swap, "last": row["last"],
                "open24h": float(row.get("open24h") or 0), "high24h": row.get("high24h"),
                "low24h": row.get("low24h"), "volume_usdt": vol * last if swap else vol,
                "ts": int(row.get("ts") or 0)}
    raise Unknown(symbol)


def _okx_candles(inst_id: str, limit: int) -> list[list] | None:
    """Nến ngày UTC của một instId, mới nhất trước (ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm);
    None khi OKX không có instId đó."""
    r = requests.get(OKX_CANDLES, params={"instId": inst_id, "bar": "1Dutc", "limit": limit}, timeout=OKX_TIMEOUT)
    body = r.json()
    if str(body.get("code")) == OKX_UNKNOWN:
        return None
    if str(body.get("code")) != "0":
        raise RuntimeError(f"OKX {body.get('code')}: {body.get('msg') or r.status_code}")
    return body.get("data") or []


def coin_history(symbol: str, days: int) -> dict:
    """``{symbol, inst, swap, rows, base}``: ``rows`` tối đa ``days`` nến ngày UTC cũ nhất trước, mỗi nến
    ``{day (YYYY-MM-DD, UTC), open, high, low, close (chuỗi OKX), volume_usdt, live}``; ``base``: giá đóng cửa
    của ngày ngay trước kỳ (None khi cặp mới niêm yết). Unknown khi OKX không có cả hợp đồng lẫn spot."""
    for inst, swap in ((f"{symbol}-USDT-SWAP", True), (f"{symbol}-USDT", False)):
        data = _okx_candles(inst, days + 1)
        if not data:
            continue
        rows = []
        for c in reversed(data):
            try:
                day = datetime.fromtimestamp(int(c[0]) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
                volume = float(c[7]) if len(c) > 7 and c[7] not in (None, "") else 0.0
                rows.append({"day": day, "open": c[1], "high": c[2], "low": c[3], "close": c[4],
                             "volume_usdt": volume, "live": len(c) > 8 and str(c[8]) == "0"})
            except (IndexError, TypeError, ValueError):
                continue
        if not rows:
            continue
        chosen = rows[-days:]
        base = rows[-days - 1]["close"] if len(rows) > days else None
        return {"symbol": symbol, "inst": inst, "swap": swap, "rows": chosen, "base": base}
    raise Unknown(symbol)


def _coin_history_lines(h: dict) -> list[str]:
    """Một khối của "alt btc 7d": thay đổi cả kỳ, cao/thấp, KL trung bình, rồi từng ngày mới nhất trước."""
    rows = h["rows"]
    last = float(rows[-1]["close"])
    span = f"{rows[0]['day'][8:10]}/{rows[0]['day'][5:7]} – {rows[-1]['day'][8:10]}/{rows[-1]['day'][5:7]}"
    head = f"{h['symbol']}: {_okx_number(rows[-1]['close'])} USDT"
    change = None
    if h["base"] and float(h["base"]):
        change = (last / float(h["base"]) - 1) * 100
        head += f" {change:+.2f}% sau {len(rows)} ngày"
    else:
        head += f" · {len(rows)} ngày từ ngày niêm yết"
    kind = "hợp đồng USDT-SWAP" if h["swap"] else "spot USDT"
    out = [f"{_icon(change)} {head} ({span}, {kind})"]
    high = max(rows, key=lambda r: float(r["high"]))
    low = min(rows, key=lambda r: float(r["low"]))
    summary = [f"Cao nhất {_okx_number(high['high'])} ({high['day'][8:10]}/{high['day'][5:7]})",
               f"Thấp nhất {_okx_number(low['low'])} ({low['day'][8:10]}/{low['day'][5:7]})"]
    volumes = [r["volume_usdt"] for r in rows if r["volume_usdt"] and not r["live"]]
    if volumes:
        summary.append(f"KL TB {_okx_volume(sum(volumes) / len(volumes))}/ngày")
    out.append("   " + " · ".join(summary))
    prev = [h["base"]] + [r["close"] for r in rows[:-1]]
    for row, before in reversed(list(zip(rows, prev, strict=True))):
        line = f"   {row['day'][8:10]}/{row['day'][5:7]}: {_okx_number(row['close'])}"
        if before and float(before):
            day_change = (float(row["close"]) / float(before) - 1) * 100
            line += f" {_icon(day_change)} {day_change:+.2f}%"
        if row["volume_usdt"]:
            line += f" · KL {_okx_volume(row['volume_usdt'])}"
        if row["live"]:
            line += " (đang chạy)"
        out.append(line)
    return out


def coins_history_text(coins: list[str], days: int, now: datetime | None = None) -> str:
    """Giá nhiều ngày trên OKX của các coin (tối đa MAX_SYMBOLS), không có tiêu đề: người gọi thêm "🪙 Altcoin ›"."""
    from tradingagents.market import vn_rules

    now = (now or vn_rules.now_vn()).astimezone(vn_rules.VN_TZ)
    capped = days > MAX_COIN_DAYS
    days = max(1, min(days, MAX_COIN_DAYS))
    more = len(coins) > MAX_SYMBOLS
    coins = coins[:MAX_SYMBOLS]
    lines = [f"📅 {days} ngày gần nhất trên OKX lúc {now:%H:%M %d/%m} (nến ngày UTC, mở 07:00 giờ VN)"]
    if capped:
        lines.append(f"(Mỗi lần xem tối đa {MAX_COIN_DAYS} ngày.)")
    for symbol, result in zip(coins, _parallel(lambda c: coin_history(c, days), coins), strict=True):
        lines.append("")
        if isinstance(result, Unknown):
            lines.append(f"❔ {symbol}: OKX không có cặp {symbol}-USDT (hợp đồng hay spot)")
        elif isinstance(result, Exception):
            logger.warning("Giá nhiều ngày OKX %s: %s", symbol, result)
            lines.append(f"⚠️ {symbol}: chưa lấy được giá OKX, thử lại sau ít phút")
        else:
            lines.extend(_coin_history_lines(result))
    if more:
        lines.append(f"(Mỗi lần tra tối đa {MAX_SYMBOLS} coin.)")
    lines += ["", "Kỳ khác: alt btc 14d · alt btc 2w (14 ngày) · alt btc 1m (30 ngày)."]
    return "\n".join(lines)


def _coin_lines(q: dict) -> list[str]:
    last = float(q["last"])
    change = (last / q["open24h"] - 1) * 100 if q["open24h"] else None
    head = f"{q['symbol']}: {_okx_number(q['last'])} USDT"
    if change is not None:
        head += f" {change:+.2f}% (24h)"
    detail = []
    if q.get("high24h") and q.get("low24h"):
        detail += [f"Cao {_okx_number(q['high24h'])}", f"Thấp {_okx_number(q['low24h'])}"]
    if q["volume_usdt"]:
        detail.append(f"KL {_okx_volume(q['volume_usdt'])}")
    detail.append("hợp đồng USDT-SWAP" if q["swap"] else "spot USDT")
    return [f"{_icon(change)} {head}", "   24h: " + " · ".join(detail)]


def coins_text(coins: list[str], now: datetime | None = None) -> str:
    """Giá OKX của các coin (tối đa MAX_SYMBOLS), không có tiêu đề: người gọi thêm "🪙 Altcoin ›"."""
    from tradingagents.market import vn_rules

    now = (now or vn_rules.now_vn()).astimezone(vn_rules.VN_TZ)
    more = len(coins) > MAX_SYMBOLS
    coins = coins[:MAX_SYMBOLS]
    lines = [f"Realtime trên OKX lúc {now:%H:%M:%S %d/%m}"]
    started = time.monotonic()
    for symbol, result in zip(coins, _parallel(coin_quote, coins)):
        if isinstance(result, Unknown):
            lines.append(f"❔ {symbol}: OKX không có cặp {symbol}-USDT (hợp đồng hay spot)")
        elif isinstance(result, Exception):
            logger.warning("Giá OKX %s: %s", symbol, result)
            lines.append(f"⚠️ {symbol}: chưa lấy được giá OKX, thử lại sau ít phút")
        else:
            lines.extend(_coin_lines(result))
    logger.info("Giá OKX %s: %.1f s", ",".join(coins), time.monotonic() - started)
    if more:
        lines.append(f"(Mỗi lần tra tối đa {MAX_SYMBOLS} coin.)")
    return "\n".join(lines)
