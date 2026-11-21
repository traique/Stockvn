"""Giá realtime khi gõ mã, không gọi AI: cổ phiếu và chỉ số (DNSE), coin (OKX).

Cổ phiếu, trên Telegram và Zoom (vnbot.telegram_bot): một tin chỉ gồm mã - "FPT", "fpt hpg",
"giá FPT", "VNINDEX", "/FPT", Zoom "FPT" - được trả giá của từng mã (tối đa ``MAX_SYMBOLS``) thay
cho bảng lệnh. Tin có chữ khác ("FPT có nên mua", "hello") vẫn nhận bảng lệnh như cũ.

- Mã: 3 chữ/số (FPT, HPG...) hoặc chỉ số VNINDEX (VNI, VN-Index), VN30, HNX, HNX30, UPCOM.
- Giá: tick khớp gần nhất của hôm nay (DNSE price-api) khi có, không thì giá đóng cửa của nến
  ngày cuối (DNSE chart-api, trong phiên là nến đang chạy); so với giá đóng cửa phiên trước
  (tham chiếu: DNSE đã điều chỉnh cổ tức/thưởng nên ngày GDKHQ vẫn đúng). Kèm mở, cao, thấp và
  khối lượng của phiên.
- Thời điểm: "realtime lúc HH:MM" chỉ trong giờ giao dịch (9:00-15:00 ngày thường); ngoài giờ là
  giá đóng cửa phiên gần nhất (tick cuối phiên còn đó cả đêm, không gọi là realtime). Mã có
  phiên cuối khác hôm nay (đang nghỉ, bị ngừng giao dịch) ghi rõ ngày phiên đó.

Coin ("alt btc", "/alt btc", "alt eth sol", on Telegram and Zoom; vnbot.altcoin_bridge): giá khớp gần nhất trên
OKX - hợp đồng USDT-SWAP mà bot Altcoin giao dịch, không có thì cặp spot USDT -, % thay đổi, cao,
thấp và khối lượng 24h. API công khai, không cần khoá, không qua bot Altcoin (chạy cả khi Altcoin
tắt). "BTCUSDT", "BTC-USDT", "BTC/USDT:USDT" đều hiểu là BTC.
"""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import requests

logger = logging.getLogger(__name__)

MAX_SYMBOLS = 5
LOOKBACK_DAYS = 20               # nến ngày đủ để có phiên trước, cả sau kỳ nghỉ Tết
PRICE_WORDS = ("giá", "gia", "price")
OKX_TICKER = "https://www.okx.com/api/v5/market/ticker"
OKX_TIMEOUT = 8
OKX_UNKNOWN = "51001"            # OKX: mã hợp đồng không tồn tại

_SPLIT = re.compile(r"[\s,;+&]+")
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
    """Các mã của một tin chỉ gồm mã (và chữ "giá"), theo thứ tự gõ, không trùng; None khi tin có chữ
    khác. "/FPT", "/FPT@TenBot" và chữ "/" Zoom thêm vào đầu tin đều được bỏ."""
    from tradingagents.dataflows.symbols import normalize_symbol
    from tradingagents.market import vn_rules

    words = [w for w in _SPLIT.split((text or "").strip()) if w]
    if words:
        words[0] = words[0].lstrip("/").split("@")[0]
    codes: list[str] = []
    for word in words:
        word = word.strip("?!.:")
        if not word or word.lower() in PRICE_WORDS:
            continue
        code = normalize_symbol(word)
        if not (vn_rules.is_index(code) or _STOCK_CODE.fullmatch(code)):
            return None
        if code not in codes:
            codes.append(code)
    return codes or None


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

    codes = stock_codes(text)
    if not codes:
        return None
    now = (now or vn_rules.now_vn()).astimezone(vn_rules.VN_TZ)
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


# ─── coin (OKX) ──────────────────────────────────────────────────────────────


def coin_symbols(words: list[str]) -> list[str] | None:
    """Các coin của "/alt btc eth" (theo thứ tự, không trùng): BTCUSDT, BTC-USDT-SWAP, BTC/USDT:USDT là
    BTC. None khi có một chữ không phải dạng mã coin (khi đó "/alt" trả bảng lệnh như cũ)."""
    coins: list[str] = []
    for word in words:
        word = word.upper().strip("?!.,")
        base = word if word == "USDT" else _COIN_SUFFIX.sub("", word)
        if not _COIN.fullmatch(base):
            return None
        if base not in coins:
            coins.append(base)
    return coins or None


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
