"""The /dinhgia pages (vnbot.dinhgia): the bot's own valuation cards.

- ``/dinhgia``: the tracked stocks' newest cards (the evening job's), the cheapest against Base
  first, and a box to look at any stock.
- ``/dinhgia/<MÃ>``: one stock's card laid out as the CTG profile PDF: the freshness of the data,
  "30 giây" (the zone, MOS, the multiple against the stock's own history, eab, Bear), the zone
  table, what Base rests on, the number line Sổ sách - eab - Bear - Base - Bull, the sensitivity,
  "Có gì đổi so với lần xem trước", the KPIs (a bank's against every listed bank's median), the
  owner's own card apart from the bot's, the disclaimer. Every figure carries its label and date.

The card is built off the request (``WAIT_SECONDS`` waited for, then "đang tính" and a reload): a
stock not kept yet is a dozen VCI calls. Nothing here is a decision of the bot.
"""

from __future__ import annotations

import html
import logging
import threading
import time
from urllib.parse import quote

from fastapi.responses import HTMLResponse, RedirectResponse

from vnbot import dinhgia as dg, ui

logger = logging.getLogger(__name__)

WAIT_SECONDS = 8          # a page waits this long for a card being built, then says "đang tính" and reloads
MAX_LOOKS = 3             # cards built at once for the pages (VCI is slow, the host has 2 GB)
_KEPT = 200
_looks: dict[str, threading.Thread] = {}
_done: dict[str, tuple[float, tuple | None, dict | None, str]] = {}   # code -> (at, view, kpis, error)
_lock = threading.Lock()
_e = html.escape
BOARDS = {"HSX": "HOSE", "HOSE": "HOSE", "HNX": "HNX", "UPCOM": "UPCoM"}
ZONE_KIND = {"Rẻ": "pos", "Đắt": "neg"}
RELIABILITY_KIND = {"cao": "pos", "thấp": "neg"}


def _vv():
    from tradingagents.dataflows import vn_valuation

    return vn_valuation


def _label(kind: str, day, as_of=None) -> str:
    """The FACT / THỐNG KÊ tag of a figure, with its date (and year when not the run date's)."""
    if day is None:
        return f'<span class="badge b-none">{kind}</span>'
    text = f"{day:%d/%m}" if as_of is not None and day.year == as_of.year else f"{day:%d/%m/%Y}"
    return f'<span class="badge b-none" title="{_e(kind)} - ngày của số liệu">{_e(kind)} {text}</span>'


def _pct(value, decimals: int = 2) -> str:
    return ui.NONE if value is None else f"{value * 100:.{decimals}f}%".replace(".", ",")


def _num(value, decimals: int = 2) -> str:
    return ui.NONE if value is None else f"{value:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ─── building a card off the request ─────────────────────────────────────────

def _compute(code: str, data_dir) -> None:
    try:
        seen = dg.view(code, data_dir)
        try:
            kpis = dg.kpi_table(code, seen[0].profile)
        except Exception as exc:  # noqa: BLE001 - the card without its KPIs
            logger.info("Định giá web: KPIs of %s: %s", code, exc)
            kpis = None
        done = (time.time(), seen, kpis, "")
    except Exception as exc:  # noqa: BLE001 - said on the page
        logger.warning("Định giá web: card of %s: %s: %s", code, type(exc).__name__, exc)
        done = (time.time(), None, None, f"{type(exc).__name__}: {str(exc)[:200]}")
    with _lock:
        _done[code] = done
        if len(_done) > _KEPT:
            for old in sorted(_done, key=lambda k: _done[k][0])[: len(_done) - _KEPT]:
                _done.pop(old, None)


def look(code: str, data_dir, wait: float = WAIT_SECONDS):
    """(view, kpis, error) of ``code`` once built within ``wait`` seconds; "busy" when ``MAX_LOOKS``
    other cards are being built; None while it is still being built."""
    with _lock:
        thread = _looks.get(code)
        if thread is None or not thread.is_alive():
            if sum(t.is_alive() for t in _looks.values()) >= MAX_LOOKS:
                return "busy"
            thread = threading.Thread(target=_compute, args=(code, data_dir), name=f"dinhgia-{code}", daemon=True)
            _looks[code] = thread
            for gone in [k for k, t in _looks.items() if not t.is_alive() and k != code]:
                _looks.pop(gone, None)
            thread.start()
    thread.join(wait)
    if thread.is_alive():
        return None
    with _lock:
        _, seen, kpis, error = _done.get(code, (0.0, None, None, "không rõ lỗi"))
    return seen, kpis, error


# ─── the list ─────────────────────────────────────────────────────────────────

def _search(value: str = "") -> str:
    return (f'<form method="get" action="/dinhgia" class="row" style="align-items:flex-end">'
            f'<div class="fld"><label class="f" for="dg-ma">Xem mã bất kỳ</label>'
            f'<input id="dg-ma" class="ticker" name="ma" maxlength="10" autocomplete="off" placeholder="FPT" '
            f'value="{_e(value)}" required></div><button class="btn btn-primary" type="submit">Xem định giá</button></form>')


def list_body(symbols: list[str], data_dir, *, account=None, msg: str = "") -> str:
    latest = dg.Cards(data_dir).latest()
    manual = set()
    if account is not None:
        try:
            manual = set(account.valuations())
        except Exception:  # noqa: BLE001 - listed without the marks
            manual = set()
    rows = {s: latest[s] for s in symbols if s in latest}
    cards = dg.list_order(rows.values())
    body = []
    for r in cards:
        sym = r["symbol"]
        mine = ' <span class="badge b-info" title="Có thẻ anh nhập (/tsdinhgia)">thẻ anh nhập</span>' if sym in manual else ""
        body.append(
            f'<tr><td data-label="Mã"><a href="/dinhgia/{quote(sym)}"><b>{_e(sym)}</b></a>{mine}</td>'
            f'<td data-label="Vùng">{ui.chip(_e(r["zone"]), ZONE_KIND.get(r["zone"], ""))}</td>'
            f'<td data-label="MOS" class="num {ui.tone(r["mos"])}">{_e(dg.signed_pct(r["mos"]))}</td>'
            f'<td data-label="Giá" class="num">{ui.money(r["close"])}<div class="muted small">phiên {_e(dg.ddmm(r["price_day"]))}'
            f'</div></td><td data-label="Bear" class="num">{ui.money(r["bear"])}</td>'
            f'<td data-label="Base" class="num"><b>{ui.money(r["base"])}</b></td><td data-label="Bull" class="num">{ui.money(r["bull"])}</td>'
            f'<td data-label="Tin cậy">{ui.chip(_e(r.get("reliability") or ui.NONE), RELIABILITY_KIND.get(r.get("reliability"), ""))}'
            f'<div class="muted small">{_e(dg.profile_label(r.get("profile")))}</div></td></tr>')
    for sym, r in rows.items():
        if r.get("base") is None:
            body.append(f'<tr><td data-label="Mã"><a href="/dinhgia/{quote(sym)}"><b>{_e(sym)}</b></a></td>'
                        f'<td colspan="7" data-label="Không lập thẻ" class="muted small">không lập thẻ: {_e(r.get("note") or "")}</td></tr>')
    for sym in symbols:
        if sym not in rows:
            body.append(f'<tr><td data-label="Mã"><a href="/dinhgia/{quote(sym)}"><b>{_e(sym)}</b></a></td>'
                        f'<td colspan="7" data-label="Trạng thái" class="muted small">chưa tính: bot tính sau {dg.JOB_TIME} mỗi phiên, '
                        'hoặc bấm vào mã để tính ngay</td></tr>')
    table = ui.table([("Mã", ""), ("Vùng", ""), ("MOS", "num"), ("Giá", "num"), ("Bear", "num"), ("Base", "num"),
                      ("Bull", "num"), ("Độ tin cậy", "")], body,
                     "Chưa theo dõi mã nào: đặt watchlist ở Tổng quan, hoặc gõ một mã ở trên.", stack=True)
    others = sorted(s for s in latest if s not in rows)[:40]
    seen = ("<p class=\"muted small\" style=\"margin:12px 0 0\">Mã khác đã xem: "
            + " · ".join(f'<a href="/dinhgia/{quote(s)}">{_e(s)}</a>' for s in others) + "</p>") if others else ""
    lead = (f"Bot tự tính thẻ định giá (Bear · eab · Base · Bull, vùng Rẻ / Hợp lý / Đắt) cho watchlist, mã đang giữ trong Tài sản "
            f"và mã có thẻ anh nhập, sau {dg.JOB_TIME} mỗi phiên. Cùng một mô hình P/B hợp lý cho mọi mã; độ tin cậy cho biết sổ "
            "sách neo giá trị tới đâu. Chỉ để tham khảo, không vào quyết định của bot.")
    return (ui.title("Định giá", _e(lead)) + ui.alert(msg, "err") + ui.card("", _search())
            + '<div class="mt">' + ui.card("Các mã theo dõi", table + seen,
                                           sub=_e(f"{dg.ZONE_RULE} · xếp theo MOS, {dg.LIST_ORDER}")) + "</div>"
            + f'<p class="muted small mt">{_e(dg.DISCLAIMER)}</p>')


# ─── one stock ────────────────────────────────────────────────────────────────

def number_line(points: list[tuple[str, float, str]], close: float) -> str:
    """The levels on one axis (label, value, "up"/"down" side of the axis) and the price as a mark:
    the PDF's Sổ sách - eab - Bear - Base - Bull line. Labels too close to each other step out a row;
    a price far outside the levels sits at the axis' end with its figure, not squeezing them."""
    levels = [v for _, v, _ in points]
    lo, hi = min(levels), max(levels)
    span = hi - lo or hi * 0.1
    lo, hi = min(lo, max(close, lo - span * 0.4)), max(hi, min(close, hi + span * 0.4))
    pad = (hi - lo) * 0.06
    lo, hi = lo - pad, hi + pad
    w, left, right, mid = 520, 36, 36, 82

    def x(v: float) -> float:
        return left + (min(max(v, lo), hi) - lo) / (hi - lo) * (w - left - right)

    marks, last = [], {"up": [], "down": []}
    for name, value, side in sorted(points, key=lambda p: p[1]):
        px = x(value)
        level = 0 if not last[side] or px - last[side][-1][0] >= 76 or last[side][-1][1] == 1 else 1
        last[side].append((px, level))
        sign = -1 if side == "up" else 1
        ty = mid + sign * (22 + 26 * level)
        marks.append(f'<line x1="{px:.1f}" x2="{px:.1f}" y1="{mid}" y2="{mid + sign * (12 + 26 * level):.1f}" '
                     'style="stroke:var(--muted);stroke-width:1"/>'
                     f'<text x="{px:.1f}" y="{ty - (8 if side == "up" else -6):.1f}" text-anchor="middle" '
                     f'style="fill:var(--muted);font-size:12px">{_e(name)}</text>'
                     f'<text x="{px:.1f}" y="{ty + (4 if side == "up" else 18):.1f}" text-anchor="middle" '
                     f'style="fill:var(--text);font-size:14px;font-weight:600">{ui.money(value)}</text>')
    px = x(close)
    outside = "" if lo <= close <= hi else (f" {ui.money(close)} →" if close > hi else f"← {ui.money(close)} ")
    anchor = "end" if close > hi else "start" if close < lo else "middle"
    marks.append(f'<path d="M{px - 6:.1f},{mid - 9} L{px + 6:.1f},{mid - 9} L{px:.1f},{mid - 1} z" style="fill:var(--accent)"/>'
                 f'<text x="{px + (6 if anchor == "end" else -6 if anchor == "start" else 0):.1f}" y="{mid - 13}" '
                 f'text-anchor="{anchor}" style="fill:var(--accent);font-size:12px">'
                 f'{"giá" + outside if close > hi else outside + "giá" if close < lo else "giá"}</text>')
    label = "; ".join(f"{n} {ui.money(v)}" for n, v, _ in points) + f"; giá {ui.money(close)}"
    return (f'<svg viewBox="0 0 {w} 168" width="100%" role="img" aria-label="{_e(label)}" style="max-width:680px">'
            f'<line x1="{left}" x2="{w - right}" y1="{mid}" y2="{mid}" style="stroke:var(--line-strong);stroke-width:1.5"/>'
            + "".join(marks) + "</svg>")


def _freshness(c, now_text: str, tracked: bool) -> str:
    vv = _vv()
    expiry = _e(vv._expiry(c))
    years = [y for y, _ in c.roes]
    when = (f"Bot tự tính lại sau {dg.JOB_TIME} mỗi phiên (mã trong watchlist, Tài sản hoặc có thẻ anh nhập)" if tracked
            else "Mã này không trong watchlist, Tài sản hay thẻ anh nhập: bot tính khi có người xem, không tự tính sau phiên")
    return ui.card("Độ mới của dữ liệu", (
        f'<p style="margin:0"><b>Dữ liệu dùng trên trang</b> — trang sinh {_e(now_text)} · giá phiên {c.day:%d/%m/%Y} · '
        f'BCTC {_e(c.period)} công bố {c.published:%d/%m/%Y} · ROE năm tới {years[-1]} (báo cáo năm công bố '
        f'{c.annual_published:%d/%m/%Y})</p><p class="muted small" style="margin:8px 0 0">{expiry}. {_e(when)}; '
        'sổ sách/cp đổi khi có BCTC quý mới, cổ tức tiền hoặc thay đổi số cổ phiếu.</p>'))


def _thirty_seconds(c) -> str:
    from tradingagents.dataflows import valuation_config as cfg

    vv = _vv()
    model = _label("THỐNG KÊ", c.day, c.as_of)
    tiles = [ui.tile("Biên an toàn (MOS)", f'<span class="{ui.tone(c.mos)}">{_e(dg.signed_pct(c.mos))}</span>',
                     f"= (Base {ui.money(c.base)} − giá) ÷ Base")]
    for rank in (c.pb_rank, c.pe_rank):
        if rank:
            name = vv.METRIC_LABELS[rank.metric]
            tiles.append(ui.tile(f"{name} hôm nay", f"{_num(rank.value)} lần",
                                 f"rẻ hơn {vv.cheaper_pct(rank)}% số phiên từ {rank.since:%m/%Y} ({ui.money(rank.sessions)} "
                                 f"phiên; thấp nhất {_num(rank.low)}, cao nhất {_num(rank.high)}) · theo {_e(rank.period)}"))
    gap = (c.close / c.eab - 1) * 100
    tiles.append(ui.tile("eab", ui.money(c.eab), f"giá {'cao' if gap > 0 else 'thấp'} hơn eab {_num(abs(gap), 1)}%"))
    tiles.append(ui.tile("Bear", f'<span class="neg">{ui.money(c.bear)}</span>',
                         f"nếu giá về Bear: {_num((c.bear / c.close - 1) * 100, 1)}% so giá hôm nay"))
    zone = ui.chip(_e(c.zone), ZONE_KIND.get(c.zone, ""))
    rows = []
    for name, cond, why in (
            ("Rẻ", f"giá ≤ Base <b>{ui.money(c.base)}</b>",
             f"Base = giá trị hợp lý mỗi cổ phiếu theo kịch bản cơ sở, neo chính của mọi so sánh; mức rẻ đặt ở Base. eab "
             f"{ui.money(c.eab)} là Base đã trừ biên an toàn {_num(c.assumptions.margin * 100, 1)}%"),
            ("Hợp lý", "giữa mức Rẻ và mức Đắt", "trên Base, dưới Bull"),
            ("Đắt", f"giá ≥ Bull <b>{ui.money(c.bull)}</b>",
             f"Bull = trần định giá mỗi cổ phiếu, {_e(vv.bull_basis(c))} của chính mã; trên mức này coi là đắt"
             + (f". {_e(note)}" if (note := dg.band_note(c.base, c.bull)) else ""))):
        now = " (hôm nay)" if name == c.zone else ""
        rows.append(f'<tr><td data-label="Vùng">{ui.chip(name, ZONE_KIND.get(name, ""))}{_e(now)}</td>'
                    f'<td data-label="Giá mỗi cổ phiếu">{cond}</td><td data-label="Dựa trên" class="small">{why}</td></tr>')
    table = ui.table([("Vùng", ""), ("Giá mỗi cổ phiếu", ""), ("Dựa trên", "")], rows, "", stack=True)
    a = c.assumptions
    years = [y for y, _ in c.roes[-vv.ROE_YEARS:]]
    implied = a.ke + (c.close / c.book - 1) * (1 + a.ke - (1 - a.lam) * (1 + a.g))
    rests = (f'<p><b>Base đứng trên:</b> sổ sách {ui.money(c.book)} đ/cp {_label("FACT", c.day, c.as_of)} · ROE bền vững '
             f'{_e(vv._pct(c.roe_base))} (trung vị {years[0]}-{years[-1]}) {_label("THỐNG KÊ", c.annual_published, c.as_of)} · '
             f'chi phí vốn chủ (Ke) {_e(vv._rate(a.ke))} đặt tay · lợi thế lợi nhuận phai dần λ {_num(a.lam, 1)} · không tăng '
             f'trưởng dài hạn (g = {_e(vv._rate(a.g))}) ⟹ Base = <b>{_num(c.base / c.book)} lần sổ sách</b>. {model}</p>'
             f'<p class="muted small">P/B hợp lý = 1 + (ROE − Ke) / (1 + Ke − (1 − λ)(1 + g)). Bear theo ROE p20 '
             f'{_e(vv._pct(c.roe_bear))}, Bull {_e(vv.bull_basis(c))}, ROE của {len(c.roes)} năm '
             f'{c.roes[0][0]}-{c.roes[-1][0]}'
             + (f'; mã không phải ngân hàng: Bull không thấp hơn Base với Ke trừ {cfg.SENSITIVITY_STEP * 100:g} điểm, để '
                'vùng Hợp lý không biến mất' if c.bull_ke is not None else '')
             + f'. Giá {ui.money(c.close)} ngầm định ROE bền vững {_e(vv._pct(implied))}.</p>')
    line = number_line([("Sổ sách/cp", c.book, "up"), ("Bear", c.bear, "up"), ("Bull", c.bull, "up"),
                        ("eab", c.eab, "down"), ("Base", c.base, "down")], c.close)
    note = ('<p class="muted small">Số trên trang là ước tính của một mô hình, dựng từ số liệu công bố, không phải khuyến '
            'nghị mua bán và không phải giá mục tiêu. Người đọc tự quyết định và tự chịu trách nhiệm.</p>')
    weak = (' <b>Độ tin cậy THẤP</b> với mã này (xem «Độ tin cậy của thẻ»): vùng và MOS chỉ là mốc tham khảo yếu.'
            if c.reliability == "thấp" else "")
    head = (f'<p style="margin:0 0 12px"><b>Vùng giá — rẻ · hợp lý · đắt.</b> Giá hôm nay <b>{ui.money(c.close)}</b> '
            f'(phiên {c.day:%d/%m/%Y}) đang ở vùng {zone}. {model}{weak}</p>')
    return ui.card("30 giây", head + f'<div class="tiles t4">{"".join(tiles)}</div>' + '<div class="mt">' + table + "</div>"
                   + rests + line + note)


def _book_card(c) -> str:
    from tradingagents.dataflows import valuation_config as cfg

    vv = _vv()
    items = [f"<li>Vốn chủ sở hữu công ty mẹ {_e(c.period)} (tới {c.period_end:%d/%m/%Y}): {_num(c.equity / 1e9, 1)} tỷ "
             f"{_label('FACT', c.published, c.as_of)}</li>"]
    if c.dividends:
        paid = " + ".join(f"{ui.money(v)} đ/cp (GDKHQ {ex:%d/%m/%Y})" for ex, v in c.dividends)
        items.append(f"<li>Cổ tức tiền GDKHQ sau ngày khoá sổ quý, đã trừ khỏi sổ sách: {_e(paid)}, tổng "
                     f"{_num(c.paid / 1e9, 1)} tỷ {_label('FACT', c.dividends[-1][0], c.as_of)}</li>")
    items.append(f"<li>Sổ sách/cp {ui.money(c.book)} = vốn chủ{' − cổ tức tiền đã trả' if c.dividends else ''} / "
                 f"{ui.money(c.shares)} cp lưu hành phiên {c.day:%d/%m/%Y} {_label('FACT', c.day, c.as_of)}</li>")
    if c.issues:
        issues = ", ".join(f"{kind}{f' {_num(r * 100, 1)}%' if r > 0 else ''} (GDKHQ {ex:%d/%m/%Y})" for ex, r, kind in c.issues)
        items.append(f"<li>Phát hành thêm sau ngày khoá sổ quý: {_e(issues)}: số cổ phiếu đã tính phần mới, vốn thu về chưa có "
                     f"trong BCTC {_e(c.period)} → sổ sách/cp và các mốc thấp hơn thực tế {_label('FACT', c.issues[-1][0], c.as_of)}</li>")
    if c.profit is not None:
        items.append(f"<li>LNST công ty mẹ 4 quý tới {_e(c.profit_period)}: {_num(c.profit / 1e9, 1)} tỷ "
                     f"{_label('FACT', c.published, c.as_of)}</li>")
    roes = "".join(f'<tr><td class="num">{y}</td><td class="num">{_e(vv._pct(v))}</td></tr>' for y, v in reversed(c.roes))
    table = ui.table([("Năm", "num"), ("ROE", "num")], [roes], "")
    sens = "".join(f'<tr><td class="num">{_e(vv._rate(ke))}{" (đang dùng)" if abs(ke - c.assumptions.ke) < 1e-9 else ""}</td>'
                   f'<td class="num">{ui.money(v)}</td><td class="num {ui.tone((v / c.close - 1) * 100)}">'
                   f'{_e(dg.signed_pct(v / c.close - 1))}</td></tr>' for ke, v in c.sensitivity)
    sens_table = ui.table([("Ke", "num"), ("Base", "num"), ("So giá hôm nay", "num")], [sens], "")
    return ('<div class="grid g2 mt">'
            + ui.card("Sổ sách và ROE", f'<ul class="small">{"".join(items)}</ul>' + table,
                      sub=f"ROE năm của VCI {_label('THỐNG KÊ', c.annual_published, c.as_of)}")
            + ui.card("Độ nhạy theo Ke", sens_table + '<p class="muted small" style="margin:12px 0 0">Ke là giả định đặt tay '
                      f"(ngân hàng {_e(vv._rate(cfg.assumptions('banking').ke))}, mã khác "
                      f"{_e(vv._rate(cfg.assumptions('default').ke))}; đổi bằng VALUATION_KE_BANK / VALUATION_KE_DEFAULT): "
                      "Base đổi nhiều khi Ke đổi 2 điểm.</p>")
            + "</div>")


def _reliability(c) -> str:
    return ui.card("Độ tin cậy của thẻ", f'<p style="margin:0">{ui.chip(_e(c.reliability), RELIABILITY_KIND.get(c.reliability, ""))} '
                   f'{_e(dg.profile_label(c.profile))}: {_e(c.reliability_why)}.</p>')


def _changes(row: dict, prev: dict | None) -> str:
    if prev is None:
        body = '<p class="muted" style="margin:0">Lần đầu bot tính mã này, chưa có lần trước để so.</p>'
        title = "Có gì đổi so với lần xem trước"
    else:
        items = dg.changes(row, prev)
        body = ("<ul>" + "".join(f"<li>{_e(x)}</li>" for x in items) + "</ul>") if items else '<p style="margin:0">Không đổi.</p>'
        title = f"Có gì đổi so với lần xem trước ({_e(dg.ddmm(prev['day']))})"
    return ui.card(title, body, sub="Mốc đổi từ 0,3%, đổi vùng, BCTC mới, đổi giả định")


def _kpis(code: str, profile: str, t, as_of) -> str:
    """The KPIs as the PDF's table: chỉ số · the stock · the banks' median of the same quarter · rank and
    note · label and date. A bank's against every listed bank's (``vn_peers``), another stock's alone."""
    from tradingagents.dataflows import vn_peers as vp

    if t is None:
        return ui.card("Chỉ số chính", '<p class="muted" style="margin:0">Chưa đọc được chỉ số của VCI.</p>')
    bank = profile == "banking"
    peers = bank and t.peers_read is not None and t.with_period >= vp.MIN_PEERS
    own_label = _label("FACT", t.published, as_of)
    peer_label = _label("THỐNG KÊ", t.peers_read, as_of) if peers else ""
    prior = f"{t.key.split('-')[1]}/{int(t.key.split('-')[0]) - 1}"
    rows = []
    for k in t.rows:
        notes = [x for x in (vp.rank_text(k) if peers else "",
                             f"cùng kỳ Q{prior}: {vp.fmt(k.prior, k.kind)}" if k.prior is not None else "",
                             "4 quý của VCI; nhiều báo cáo dùng quý quy năm" if k.field == "netInterestMargin" else "") if x]
        median = (f'{vp.fmt(k.median, k.kind)} <span class="muted small">({k.n} NH)</span>' if k.median is not None
                  else ui.NONE)
        rows.append(f'<tr><td data-label="Chỉ số">{_e(k.label)}</td><td data-label="{_e(code)}" class="num">'
                    f'{vp.fmt(k.value, k.kind)}</td>'
                    + (f'<td data-label="Trung vị cùng kỳ" class="num">{median}</td>' if bank else "")
                    + f'<td data-label="Hạng · ghi chú" class="small">{_e(" · ".join(notes)) or ui.NONE}</td>'
                    f'<td data-label="Nhãn · ngày">{own_label}{" " + peer_label if peers and k.median is not None else ""}</td></tr>')
    if bank and t.roe5 is not None:
        median = (f'{vp.fmt(t.roe5_median)} <span class="muted small">({t.roe5_of} NH)</span>'
                  if t.roe5_median is not None else ui.NONE)
        note = (f"hạng {t.roe5_rank}/{t.roe5_of} (cao là tốt) · " if t.roe5_rank else "") + \
            f"ROE năm {t.roe5_years[0]}-{t.roe5_years[-1]}; là ROE bền vững của thẻ"
        rows.append(f'<tr><td data-label="Chỉ số">ROE trung vị {len(t.roe5_years)} năm</td>'
                    f'<td data-label="{_e(code)}" class="num">{vp.fmt(t.roe5)}</td>'
                    f'<td data-label="Trung vị" class="num">{median}</td><td data-label="Hạng · ghi chú" class="small">'
                    f'{_e(note)}</td><td data-label="Nhãn · ngày">{_label("THỐNG KÊ", t.roe5_published, as_of)}</td></tr>')
    head = [("Chỉ số", ""), (f"{_e(code)} {_e(t.period)}", "num")]
    if bank:
        head.append(("Trung vị ngân hàng cùng kỳ", "num"))
    head += [("Hạng · ghi chú", ""), ("Nhãn · ngày", "")]
    sub = f"Số TTM của VCI, kỳ {t.period} công bố {t.published:%d/%m/%Y}"
    if peers:
        sub += (f"; trung vị {t.with_period}/{t.banks} ngân hàng niêm yết (ICB 8300) đã công bố {t.period}"
                + (f", {t.newer} ngân hàng đã có kỳ mới hơn nhưng vẫn so cùng kỳ" if t.newer else "")
                + f"; VCI đọc {t.peers_read:%d/%m/%Y}. Hạng 1 là tốt nhất")
    elif bank and t.peers_read is not None:
        sub += (f"; trung vị nhóm: chưa có, mới {t.with_period}/{t.banks} ngân hàng đã công bố {t.period} "
                f"(VCI đọc {t.peers_read:%d/%m/%Y})")
    elif bank:
        sub += (f"; trung vị nhóm: chưa có (bot đọc sau {dg.JOB_TIME} mỗi phiên khi watchlist, Tài sản hoặc thẻ anh "
                "nhập có một ngân hàng)")
    else:
        sub += "; so với nhóm: bot chỉ tính cho ngân hàng (mọi ngân hàng niêm yết, ICB 8300)"
    return ui.card("Chỉ số chính" + (" so với các ngân hàng" if bank else ""), ui.table(head, rows, "", stack=True),
                   sub=_e(sub))


def _manual(code: str, account, bot_base: float | None) -> str:
    if account is None:
        return ""
    try:
        card = account.valuations().get(code)
    except Exception:  # noqa: BLE001 - the page without it
        return ""
    if not card:
        return ""
    from vnbot import taisan as ts

    line = _e(dg._manual_line(code, account, bot_base).removeprefix("✍️ "))
    return ui.card("Thẻ anh nhập (không phải của bot)", f'<p style="margin:0">{line}</p>'
                   f'<p class="muted small" style="margin:8px 0 0">Nhập {_e(ts.ddmmyyyy(card["day"]))}'
                   + (f" · nguồn: {_e(card['note'])}" if card.get("note") else "")
                   + ' · sửa hoặc xoá ở <a href="/taisan#dinhgia">Danh mục → Thẻ định giá anh nhập</a> hoặc /tsdinhgia.</p>')


def profile_body(code: str, seen, kpis, *, account=None, name: str = "", tracked: bool = False) -> str:
    from tradingagents.market import vn_rules

    vv = _vv()
    result, row, prev = seen
    now_text = vn_rules.now_vn().strftime("%d/%m/%Y %H:%M")
    lead = _e(name)
    if not isinstance(result, vv.ValuationCard):
        return (ui.title(_e(code), lead + f" · {_e(dg.profile_label(result.profile))}")
                + ui.alert(f"Bot không lập thẻ định giá cho {code}: {result.reason}.", "info")
                + '<div class="mt">' + _changes(row, prev) + "</div>"
                + '<div class="mt">' + _kpis(code, result.profile, kpis, result.as_of) + "</div>"
                + ('<div class="mt">' + m + "</div>" if (m := _manual(code, account, None)) else "")
                + f'<p class="muted small mt">{_e(dg.DISCLAIMER)} Trang sinh {_e(now_text)}.</p>')
    c = result
    head = ui.title(_e(code), f"{lead} · {_e(dg.profile_label(c.profile))}",
                    right=f'<span class="big num">Giá <b>{ui.money(c.close)}</b></span> <span class="muted">({c.day:%d/%m/%Y})</span>')
    parts = [head, _freshness(c, now_text, tracked), '<div class="mt">' + _thirty_seconds(c) + "</div>",
             '<div class="grid g2 mt">' + _reliability(c) + _changes(row, prev) + "</div>", _book_card(c),
             '<div class="mt">' + _kpis(code, c.profile, kpis, c.as_of) + "</div>"]
    manual = _manual(code, account, c.base)
    if manual:
        parts.append('<div class="mt">' + manual + "</div>")
    parts.append('<p class="muted small mt"><b>Không phải khuyến nghị đầu tư.</b> Mọi con số là ước tính của mô hình tại ngày '
                 'ghi kèm, dựng từ số liệu doanh nghiệp công bố (VCI); người đọc tự quyết định và tự chịu trách nhiệm. Thẻ '
                 'không đổi hành động, cắt lỗ, mục tiêu hay cổng chặn của bot.</p>')
    return "".join(parts)


def _name(code: str) -> str:
    from tradingagents.dataflows.symbols import listing_entry

    entry = listing_entry(code) or {}
    board = BOARDS.get(str(entry.get("board") or "").upper(), entry.get("board") or "")
    return " · ".join(x for x in (entry.get("organName") or "", board) if x)


def register(app, data_dir, *, guard, show_bot=lambda: True, watchlist=lambda: [], account=None) -> None:
    """The /dinhgia routes on ``app``. Read-only pages: viewing keeps the day's card (the changes'
    baseline), no form posts."""
    from fastapi import Depends

    from vnbot.service import check_request

    def render(title: str, body: str, refresh: int | None = None) -> HTMLResponse:
        return HTMLResponse(ui.page(title, body, active="dinhgia", refresh=refresh, show_bot=show_bot()))

    def tracked() -> list[str]:
        return dg.tracked(watchlist(), account)

    @app.get("/dinhgia", dependencies=[Depends(guard)])
    def dinhgia_list(ma: str = "") -> HTMLResponse:
        msg = ""
        if ma.strip():
            try:
                code, _ = check_request(ma[:20])
                return RedirectResponse(f"/dinhgia/{quote(code)}", status_code=303)
            except ValueError as exc:
                msg = str(exc)
        return render("Định giá", list_body(tracked(), data_dir, account=account, msg=msg))

    @app.get("/dinhgia/{symbol}", dependencies=[Depends(guard)])
    def dinhgia_stock(symbol: str) -> HTMLResponse:
        try:
            code, _ = check_request(symbol[:20])
        except ValueError as exc:
            return render("Định giá", ui.title("Định giá") + ui.alert(str(exc), "err") + ui.card("", _search(symbol[:10])))
        found = look(code, data_dir)
        back = '<p class="small"><a href="/dinhgia">← Các mã theo dõi</a></p>'
        if found == "busy":
            return render(code, back + ui.title(_e(code)) + ui.alert("Bot đang tính định giá vài mã khác; trang tự tải lại sau "
                                                                     "15 giây.", "info"), refresh=15)
        if found is None:
            return render(code, back + ui.title(_e(code)) + ui.alert(f"Đang tính định giá {code} (lần đầu một mã khoảng 10-20 "
                                                                     "giây: giá từ 2018 và BCTC từ VCI); trang tự tải lại.", "info"),
                          refresh=5)
        seen, kpis, error = found
        if seen is None:
            return render(code, back + ui.title(_e(code)) + ui.alert(f"Chưa tính được định giá {code}: {error}. VCI có thể "
                                                                     "đang lỗi hoặc chậm; thử lại sau ít phút.", "err")
                          + ui.card("", _search(code)))
        try:
            name = _name(code)
        except Exception:  # noqa: BLE001 - the page without the company's name
            name = ""
        try:
            followed = code in tracked()
        except Exception:  # noqa: BLE001 - said as not followed
            followed = False
        return render(code, back + profile_body(code, seen, kpis, account=account, name=name, tracked=followed)
                      + ui.card("", _search()))
