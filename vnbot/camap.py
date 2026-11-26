"""Dòng tiền cá mập: who hit the order book during a session, by order size, from the ticks.

``/camap MÃ`` (Telegram and Zoom), and a block of ``/soi MÃ``. Every matched tick of the
session comes from DNSE (``dnse.fetch_ticks``: price, shares, the side that hit the book -
the buyer, the seller, or an ATO/ATC match with no side). Ticks matched in the same
millisecond at the same price on the same side are one order (a big order fills against
several resting ones, as several ticks), and each order is sized by its value:

- cá mập (shark): ``SHARK_VALUE`` and up a order (1 tỷ);
- sói (wolf): ``WOLF_VALUE`` to ``SHARK_VALUE`` (200 triệu - 1 tỷ);
- cừu (sheep): under ``WOLF_VALUE``.

"Dòng tiền lớn" is the sharks and the wolves together. Buyer-initiated value minus
seller-initiated value is the net active flow of each class. This is the public order book
only: nobody can tell a fund from a rich individual, a sliced order from several small
ones, and ATO/ATC matches (``auction``) and put-through deals have no side. It confirms a
move (big money bought into a rise, sold into a fall), it never names the buyer.
"""

from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

SHARK_VALUE = 1e9             # VND an order: cá mập
WOLF_VALUE = 2e8              # VND an order: sói (below: cừu)
CONFIRM_PCT = 20.0            # |net| of the big money's turnover from which the flow is one-sided
MIN_BIG_SHARE_PCT = 5.0       # big orders under this share of the day's value: too few to read
TOP_ORDERS = 5
CLASSES = (("shark", "Cá mập", f"≥ {SHARK_VALUE / 1e9:.0f} tỷ/lệnh"),
           ("wolf", "Sói", f"{WOLF_VALUE / 1e6:.0f} triệu – {SHARK_VALUE / 1e9:.0f} tỷ"),
           ("sheep", "Cừu", f"< {WOLF_VALUE / 1e6:.0f} triệu"))


def _ticks(symbol: str, day: str) -> list[dict]:
    from tradingagents.dataflows.vendors import dnse

    return dnse.fetch_ticks(symbol, day)


def orders(ticks: list[dict]) -> list[dict]:
    """Ticks grouped into orders: consecutive ticks with the same time (ms), side and price.
    ``{time, price, shares, value, side}``, oldest first."""
    out: list[dict] = []
    for tick in ticks:
        last = out[-1] if out else None
        if last and (last["time"], last["side"], last["price"]) == (tick["time"], tick["side"], tick["price"]):
            last["shares"] += tick["shares"]
            last["value"] += tick["shares"] * tick["price"]
        else:
            out.append({"time": tick["time"], "price": tick["price"], "shares": tick["shares"],
                        "value": tick["shares"] * tick["price"], "side": tick["side"]})
    return out


def classify(value: float) -> str:
    if value >= SHARK_VALUE:
        return "shark"
    if value >= WOLF_VALUE:
        return "wolf"
    return "sheep"


def summarize(ticks: list[dict]) -> dict:
    """``{ticks, orders, total, auction, classes: {shark|wolf|sheep: {buy, sell, n_buy, n_sell, shares_buy,
    shares_sell}}, big: {buy, sell, net}, top: [the biggest orders]}`` - values in VND."""
    grouped = orders(ticks)
    classes = {k: {"buy": 0.0, "sell": 0.0, "n_buy": 0, "n_sell": 0, "shares_buy": 0.0, "shares_sell": 0.0}
               for k, _, _ in CLASSES}
    auction = 0.0
    for o in grouped:
        if o["side"] == 1:
            side = "buy"
        elif o["side"] == 2:
            side = "sell"
        else:
            auction += o["value"]
            continue
        c = classes[classify(o["value"])]
        c[side] += o["value"]
        c["n_" + side] += 1
        c["shares_" + side] += o["shares"]
    big_buy = classes["shark"]["buy"] + classes["wolf"]["buy"]
    big_sell = classes["shark"]["sell"] + classes["wolf"]["sell"]
    top = sorted((o for o in grouped if o["side"] in (1, 2)), key=lambda o: -o["value"])[:TOP_ORDERS]
    return {"ticks": len(ticks), "orders": len(grouped), "total": sum(o["value"] for o in grouped),
            "auction": auction, "classes": classes,
            "big": {"buy": big_buy, "sell": big_sell, "net": big_buy - big_sell}, "top": top}


def verdict(summary: dict, change_pct: float | None = None) -> tuple[str, str]:
    """(icon, one sentence): what the big money did, and whether it confirms the session's move."""
    big, total = summary["big"], summary["total"]
    turnover = big["buy"] + big["sell"]
    if not total or not turnover:
        return "⚪", "Không có lệnh lớn nào (≥ 200 triệu) trong phiên: chưa thấy dấu vết cá mập."
    share = turnover / total * 100
    if share < MIN_BIG_SHARE_PCT:
        return "⚪", (f"Lệnh lớn chỉ chiếm {share:.0f}% giá trị khớp: dòng tiền lớn đứng ngoài, "
                     "chưa xác nhận được cá mập.")
    net_pct = big["net"] / turnover * 100
    if net_pct >= CONFIRM_PCT:
        what = "Dòng tiền lớn MUA ròng chủ động"
        icon = "🟢"
    elif net_pct <= -CONFIRM_PCT:
        what = "Dòng tiền lớn BÁN ròng chủ động"
        icon = "🔴"
    else:
        return "🟡", (f"Dòng tiền lớn cân bằng (mua {_bn(big['buy'])} · bán {_bn(big['sell'])}, chiếm {share:.0f}% "
                     "giá trị khớp): chưa nghiêng hẳn về bên nào.")
    text = f"{what} {_bn(abs(big['net']))} ({abs(net_pct):.0f}% giá trị lệnh lớn, lệnh lớn chiếm {share:.0f}% phiên)"
    if change_pct is not None:
        if (net_pct > 0) == (change_pct >= 0):
            text += ": xác nhận nhịp " + ("tăng" if change_pct >= 0 else "giảm") + " của phiên."
        else:
            text += (": NGƯỢC với giá (giá " + ("tăng" if change_pct > 0 else "giảm")
                     + " mà tiền lớn " + ("bán" if net_pct < 0 else "mua") + "): theo dõi thêm.")
    else:
        text += "."
    return icon, text


def _bn(value: float) -> str:
    from vnbot.quote import _vn

    if not value:
        return "0"
    if abs(value) >= 1e9:
        return f"{_vn(value / 1e9, 1)} tỷ"
    return f"{_vn(value / 1e6, 0)} triệu"


def _signed_bn(value: float) -> str:
    return ("+" if value > 0 else "−" if value < 0 else "") + _bn(abs(value))


def flow(symbol: str, day: str, ticks: list[dict] | None = None) -> dict:
    """The session's summary for ``symbol`` (``ticks``: already fetched)."""
    ticks = _ticks(symbol, day) if ticks is None else ticks
    out = summarize(ticks)
    out.update(symbol=symbol, day=day)
    return out


def lines(summary: dict, change_pct: float | None = None, *, top: bool = False) -> list[str]:
    """The block: one line a class, the active split, the verdict (and the biggest orders)."""
    from vnbot.quote import _vn

    if not summary["ticks"]:
        return ["Chưa có lệnh khớp nào trong phiên này (DNSE chưa có tick)."]
    out = []
    for key, name, band in CLASSES:
        c = summary["classes"][key]
        n = c["n_buy"] + c["n_sell"]
        out.append(f"• {name} ({band}): mua {_bn(c['buy'])} · bán {_bn(c['sell'])} → ròng {_signed_bn(c['buy'] - c['sell'])}"
                   f" ({n} lệnh)")
    if summary["auction"]:
        out.append(f"• ATO/ATC (không rõ chiều): {_bn(summary['auction'])}")
    active = summary["total"] - summary["auction"]
    if active > 0:
        buy = sum(c["buy"] for c in summary["classes"].values())
        out.append(f"Mua chủ động {buy / active * 100:.0f}% · bán chủ động {100 - buy / active * 100:.0f}% giá trị khớp lệnh "
                   f"({_vn(summary['orders'])} lệnh từ {_vn(summary['ticks'])} tick)")
    icon, say = verdict(summary, change_pct)
    out.append(f"{icon} {say}")
    if top and summary["top"]:
        out.append("Lệnh lớn nhất: " + "; ".join(
            f"{o['time'][:8]} {'mua' if o['side'] == 1 else 'bán'} {_bn(o['value'])} @{_vn(o['price'])}" for o in summary["top"]))
    return out


def text(symbol: str, day: str, summary: dict, change_pct: float | None = None, *, live: bool = False) -> str:
    head = f"🦈 DÒNG TIỀN CÁ MẬP: {symbol} · phiên {day[8:10]}/{day[5:7]}" + (" (đang chạy)" if live else "")
    body = ["Theo lệnh khớp chủ động trên DNSE (tick cùng mili-giây, cùng giá, cùng chiều gộp thành một lệnh; "
            "không gồm thoả thuận):"] + lines(summary, change_pct, top=True)
    return "\n".join([head] + body + ["", "Chỉ là sổ lệnh công khai: thấy tiền lớn mua hay bán, không biết là ai. "
                                      "Soi đủ chỉ tiêu: /soi " + symbol])


def command(args: str, now: datetime | None = None) -> str:
    """/camap MÃ: the session of the day (the last session outside it)."""
    from tradingagents.market import vn_rules
    from vnbot.service import check_request

    words = (args or "").split()
    if not words:
        raise ValueError("Nhập mã cổ phiếu, ví dụ: /camap FPT")
    code, _ = check_request(words[0])
    now = (now or _now()).astimezone(vn_rules.VN_TZ)
    today = now.strftime("%Y-%m-%d")
    live = vn_rules.is_session_day(today) and vn_rules.is_market_hours(now)
    day = today if vn_rules.is_session_day(today) and now.strftime("%H:%M") >= "09:15" else vn_rules.prev_session(today)
    summary = flow(code, day)
    if not summary["ticks"] and day == today and now.strftime("%H:%M") < "09:20":
        day = vn_rules.prev_session(today)
        summary, live = flow(code, day), False
    change = None
    try:
        from vnbot import quote

        q = quote.stock_quote(code, now)
        if q["day"] == day and q["ref"]:
            change = (q["price"] / q["ref"] - 1) * 100
    except Exception as exc:  # noqa: BLE001 - the verdict without the price's direction
        logger.info("Cá mập %s: không có giá: %s", code, exc)
    return text(code, day, summary, change, live=live)


def _now():
    from tradingagents.market import vn_rules

    return vn_rules.now_vn()
