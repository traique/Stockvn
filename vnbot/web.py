"""The web page: ask for an analysis, follow the queue, read the reports.

Plain server-rendered HTML (no build step, no JavaScript framework) so the
upload stays small. Every page but /health asks for WEB_PASSWORD (HTTP Basic,
any user name), since each analysis spends LLM quota; without it set the pages
stay locked. A form posted from another site (the browser would send the saved
password with it) is refused. Repeated wrong passwords from one client lock it
out for a few minutes.
"""

from __future__ import annotations

import html
import json
import logging
import os
import time
from datetime import datetime
from typing import Annotated
from urllib.parse import quote
from zoneinfo import ZoneInfo

from starlette.concurrency import run_in_threadpool
from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from vnbot import ui, webguard
from vnbot.jobs import Job, JobQueue
from vnbot.service import Watchlist, check_request, quick_text, today_vn
from vnbot.settings import Settings
from vnbot.webguard import LoginThrottle

logger = logging.getLogger(__name__)

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
MAX_WATCHLIST = 30

_STATUS_VI = {
    "queued": "đang xếp hàng",
    "quick": "đang lập quyết định định lượng",
    "running": "các agent AI đang phân tích",
    "done": "xong",
    "failed": "lỗi",
}
_SOURCE_VI = {"web": "Web", "telegram": "Telegram", "daily": "Tự động"}
_PHASE_VI = {"closed": "Nghỉ cuối tuần", "pre_open": "Trước giờ mở cửa", "ato": "Phiên mở cửa (ATO)", "continuous": "Khớp lệnh liên tục",
             "lunch": "Nghỉ trưa", "atc": "Phiên đóng cửa (ATC)", "put_through": "Giao dịch thoả thuận", "after_close": "Đã đóng cửa"}
_MODE_VI = {"critic": "AI phản biện", "full": "phân tích chuyên sâu", "off": "không dùng AI"}
_T2_BADGE = '<span class="badge b-none">chờ T+2</span>'
_TUNED_BADGE = '<span class="badge b-ok">đã hiệu chỉnh</span>'
_CHANGED_BADGE = '<span class="badge b-ok">thay đổi</span> '
_KEPT_BADGE = '<span class="badge b-none">giữ nguyên</span>'
_HAND_BADGE = '<span class="badge b-none">đổi tay</span>'
_UNSET_BADGE = '<span class="badge b-none">chưa cấu hình</span>'


# Largest request body accepted. The forms are a few hundred bytes and a Zoom event a few
# KB; without a cap one unauthenticated POST (the webhook, or a form read before the
# password check) is held whole in memory or spooled to disk, enough to exhaust a 2 GB host.
MAX_BODY_BYTES = 64 * 1024


class _BodyTooLarge(Exception):
    pass


_GLYPH_VI = {"⛔": "Cắt lỗ (⛔)", "🔴": "Trang BÁN (🔴)", "🟠": "Dấu hiệu sớm (🟠)", "🎯": "Mục tiêu (🎯)"}


def _named(note: str) -> str:
    """A release note for the page: opened with the alert's name, not its glyph, and a run
    joined by middle dots ("HOME · DANH MỤC") joined by commas."""
    note = note.replace(" · ", ", ")
    head = note[:1]
    return f"{_GLYPH_VI[head]}{note[1:]}" if head in _GLYPH_VI else note


def _too_large(exc: BaseException, depth: int = 0) -> bool:
    """Whether ``exc`` is, or wraps (exception groups, causes), a ``_BodyTooLarge``."""
    if exc is None or depth > 10:
        return False
    if isinstance(exc, _BodyTooLarge):
        return True
    if any(_too_large(e, depth + 1) for e in getattr(exc, "exceptions", None) or ()):
        return True
    return _too_large(exc.__cause__, depth + 1)


class BodyLimit:
    """ASGI middleware: refuse a request body over ``limit`` bytes with 413, from the
    Content-Length header when there is one, else while it streams in (chunked).

    ``wide``: {path: (limit, allowed)} - a POST to that path may carry up to its own limit
    when ``allowed(scope)`` (the right password in the request), the Excel upload."""

    def __init__(self, app, limit: int = MAX_BODY_BYTES, wide: dict | None = None):
        self.app, self.limit, self.wide = app, limit, wide or {}

    def _limit(self, scope) -> int:
        rule = self.wide.get(scope.get("path", ""))
        if rule and scope.get("method") == "POST":
            try:
                if rule[1](scope):
                    return rule[0]
            except Exception:  # noqa: BLE001 - a malformed header keeps the small limit
                pass
        return self.limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self._limit(scope)
        for name, value in scope.get("headers") or []:
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = limit + 1
                if declared > limit:
                    await self._refuse(send)
                    return
        received, started = 0, False

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body") or b"")
                if received > limit:
                    raise _BodyTooLarge
            return message

        async def tracked_send(message):
            nonlocal started
            if message.get("type") == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except Exception as exc:
            # Inner middleware (BaseHTTPMiddleware, anyio task groups) may wrap it in an ExceptionGroup.
            if not _too_large(exc):
                raise
            if not started:
                await self._refuse(send)

    async def _refuse(self, send) -> None:
        body = "Yêu cầu quá lớn.".encode()
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                                (b"content-length", str(len(body)).encode()), (b"connection", b"close")]})
        await send({"type": "http.response.body", "body": body})


def _switch_form(key: str, on: bool) -> str:
    """An on/off switch that posts to /channels/<key> (a form: works without JavaScript)."""
    label = "Đang bật · chạm để tắt" if on else "Đang tắt · chạm để bật"
    return (f'<form method="post" action="/channels/{key}" style="margin:0;display:inline-flex">'
            f'<input type="hidden" name="on" value="{0 if on else 1}">'
            f'<button class="switch{" on" if on else ""}" type="submit" role="switch" aria-checked="{"true" if on else "false"}" '
            f'aria-label="{label}" title="{label}"><span class="knob"></span>'
            f'<span class="st">{"Bật" if on else "Tắt"}</span></button></form>')


def _page(title: str, body: str, refresh: int | None = None, *, active: str = "", status_code: int = 200,
          show_bot: bool = True) -> HTMLResponse:
    # Error pages (status >= 400) can be seen without the password: no version on them.
    return HTMLResponse(ui.page(title, body, active=active, refresh=refresh, show_bot=show_bot,
                                show_version=status_code < 400), status_code=status_code)


def _when(ts: float | None) -> str:
    """An epoch time in Vietnam time, whatever the server's own zone (often UTC)."""
    return datetime.fromtimestamp(ts, VN_TZ).strftime("%d/%m %H:%M") if ts else ""


def _client(request: Request) -> str:
    # The socket peer. Uvicorn already replaces it with X-Forwarded-For when the
    # request comes from a proxy it trusts (FORWARDED_ALLOW_IPS, 127.0.0.1 by
    # default); the header itself is never trusted here, a client could forge it.
    return request.client.host if request.client else "?"


def _job_row(job: Job) -> str:
    return (f'<tr><td><a class="sym" href="/jobs/{job.id}">{html.escape(job.ticker)}</a></td>'
            f'<td class="num">{html.escape(job.date)}</td><td>{ui.action_badge(job.action)}</td>'
            f"<td>{ui.status_badge(job.status)}</td>"
            f'<td class="muted small">{_SOURCE_VI.get(job.source, html.escape(job.source))}</td>'
            f'<td class="muted small num">{_when(job.created)}</td></tr>')


def _money(value) -> str:
    return ui.money(value)


def _book_card(title: str, book, *, accent: str, sub: str = "") -> str:
    st = book.stats()
    nav_sub = f"từ {html.escape(book.start_date)}" if book.start_date else "chưa có phiên giao dịch"
    kpis = "".join([
        ui.kpi("NAV", ui.money_short(st["nav"]), nav_sub),
        ui.kpi("Lợi nhuận", ui.pct(st["return_pct"]),
               f"VNINDEX {ui.pct(st['benchmark_pct'])}" if st["benchmark_pct"] is not None else "", ui.tone(st["return_pct"])),
        ui.kpi("Sụt giảm tối đa", ui.pct(st["max_drawdown_pct"], signed=False), "tính từ đỉnh NAV"),
        ui.kpi("Số lệnh mua", str(st["trades"]),
               f"thắng {st['win_rate_pct']:.0f}%".replace(".", ",") if st["win_rate_pct"] is not None else "chưa có lệnh đóng"),
    ])
    positions = [
        f'<tr><td class="sym">{html.escape(p.symbol)}</td><td class="num">{ui.money(p.qty)}</td>'
        f'<td class="num">{ui.money(p.avg_price)}</td><td class="num">{ui.money(p.last_price)}</td>'
        f'<td class="num {ui.tone(p.last_price - p.avg_price if p.avg_price else None)}">'
        f'{ui.pct((p.last_price / p.avg_price - 1) * 100) if p.avg_price else ui.NONE}</td>'
        f'<td class="num">{ui.money(p.stop) if p.stop else ui.NONE}</td>'
        f'<td>{"" if p.sellable else _T2_BADGE}</td></tr>'
        for p in book.positions.values()]
    orders = [
        f'<tr><td>{ui.action_badge("BUY" if o.side == "BUY" else "SELL")}</td><td class="sym">{html.escape(o.symbol)}</td>'
        f'<td class="num">{ui.money(o.qty)}</td><td class="num">{ui.money(o.limit) if o.limit else "giá mở cửa (ATO)"}</td>'
        f'<td class="muted small">{html.escape(o.reason)}</td></tr>'
        for o in book.orders]
    fills = [
        f'<tr><td class="num muted">{html.escape(f.date)}</td><td>{ui.action_badge("BUY" if f.side == "BUY" else "SELL")}</td>'
        f'<td class="sym">{html.escape(f.symbol)}</td><td class="num">{ui.money(f.qty)}</td><td class="num">{ui.money(f.price)}</td>'
        f'<td class="num {ui.tone(f.pnl)}">{ui.money(f.pnl)}</td><td class="muted small">{html.escape(f.reason)}</td></tr>'
        for f in reversed(book.fills[-12:])]
    body = (
        f'<div class="kpis">{kpis}</div>'
        '<h3>Danh mục nắm giữ</h3>'
        + ui.table([("Mã", ""), ("KL", "num"), ("Giá vốn", "num"), ("Thị giá", "num"), ("Lãi/lỗ", "num"),
                    ("Cắt lỗ", "num"), ("", "")], positions, "Chưa nắm giữ cổ phiếu nào.")
        + '<h3>Lệnh cho phiên kế tiếp</h3>'
        + ui.table([("Chiều", ""), ("Mã", ""), ("KL", "num"), ("Giá", "num"), ("Lý do", "")], orders, "Không có lệnh chờ khớp.")
        + '<h3>Giao dịch gần đây</h3>'
        + ui.table([("Ngày", ""), ("Chiều", ""), ("Mã", ""), ("KL", "num"), ("Giá", "num"), ("Lãi/lỗ", "num"),
                    ("Lý do", "")], fills, "Chưa phát sinh giao dịch.")
    )
    return ui.card(title, body, sub=sub, cls=accent)


def _common_start(*books) -> str | None:
    """The first session every book with a history has: the date the chart indexes on."""
    starts = [min(h["date"] for h in b.history) for b in books if b.history]
    return max(starts) if starts else None


def _chart(trader) -> str:
    paper, mom, fb = trader.paper(), trader.momentum_book(), trader.factor_book()
    dates = sorted({h["date"] for h in paper.history} | {h["date"] for h in mom.history}
                   | {h["date"] for h in fb.history})
    # Every line is 100 on the same session (the books started on different days):
    # the gaps between the lines after it are returns over the same period.
    base = _common_start(paper, mom, fb) or ""

    def indexed(history, key):
        by_day = {h["date"]: h.get(key) for h in history}
        first = next((by_day[d] for d in dates if d >= base and by_day.get(d)), None)
        return [round(by_day[d] / first * 100, 3) if first and by_day.get(d) else None for d in dates]

    bench_hist = max((paper.history, mom.history, fb.history), key=len)
    series = [
        {"name": "Phòng thủ", "color": "var(--c-factor)", "values": indexed(fb.history, "nav")},
        {"name": "Quant Engine", "color": "var(--c-engine)", "values": indexed(paper.history, "nav")},
        {"name": "Động lượng", "color": "var(--c-mom)", "values": indexed(mom.history, "nav")},
        {"name": "VNINDEX", "color": "var(--c-bench)", "values": indexed(bench_hist, "benchmark"), "dashed": True},
    ]
    return ui.nav_chart(dates, series, run_time=_run_time(trader))


def _run_time(trader) -> str:
    """When the scheduler really runs the bot: ``trade_time``, never before the bar is final."""
    from vnbot.settings import MIN_TRADE_TIME

    return max(trader.settings.trade_time, MIN_TRADE_TIME)


def _capital_card(trader) -> str:
    """The form that sets the paper books' capital (each chosen book starts over)."""
    current = trader.paper_capital()
    boxes = "".join(
        f'<label class="check"><input type="checkbox" name="books" value="{key}" checked> {html.escape(name)}</label>'
        for key, name in trader.BOOK_NAMES.items())
    form = f"""
<form method="post" action="/bot/capital">
  <div class="row">
    <div class="fld" style="flex:1 1 220px"><label class="f" for="capital">Vốn khởi điểm mỗi tài khoản (VNĐ)</label>
      <input id="capital" name="capital" inputmode="numeric" value="{ui.money(current)}" required></div>
    <button class="btn btn-primary" type="submit">Áp dụng vốn mới</button>
  </div>
  <div class="checks">{boxes}</div>
  <label class="check warn"><input type="checkbox" name="confirm" value="1" required> Tôi xác nhận các tài khoản được chọn sẽ
  khởi động lại với vốn mới; sổ giao dịch cũ được lưu trữ trong data/trading.</label>
</form>"""
    return ui.card("Vốn tài khoản ảo", form,
                   sub=f"Vốn khởi điểm hiện hành {ui.money(current)} đồng · tối thiểu 10.000.000 đồng", cls="accent-gold")


def _breakout_card(current) -> str:
    """The hand switch of the Quant Engine's breakout BUYs (Params.breakout_buy), with why."""
    on = bool(current.breakout_buy)
    state = '<span class="badge b-ok">đang bật</span>' if on else '<span class="badge b-none">đang tắt</span>'
    button = ('<form method="post" action="/bot/breakout" style="margin:0">'
              f'<input type="hidden" name="on" value="{0 if on else 1}">'
              f'<button class="btn {"btn-ghost" if on else "btn-primary"}" type="submit">'
              f'{"Tắt MUA breakout" if on else "Bật lại MUA breakout"}</button></form>')
    body = (f"<p>MUA breakout (giá đóng cửa vượt kênh 20 phiên) {state}</p>"
            '<p class="muted small">Dựng lại 03/2023 - 10/2026 trên VN100, đi theo kế hoạch như sổ ảo: 46 lệnh breakout, 5 chốt lời, '
            "38 cắt lỗ, trung bình -0,68R mỗi lệnh; pullback hoà vốn (docs/chandoan_2026-10-28). Tắt: Quant Engine (sổ ảo, sàng lọc "
            "15:30, tín hiệu thật nếu chọn Quant Engine) không mở MUA mới ở mã breakout, chỉ theo dõi; kết quả bóng của chúng ở trang "
            "Vì sao chưa MUA, nhóm \"MUA breakout bị tắt\". Hiệu chỉnh đêm không bao giờ đổi công tắc này.</p>" + button)
    return ui.card("Công tắc Quant Engine", body, cls="accent-peach")


def _bot_page(trader, msg: str = "") -> str:
    from tradingagents.quant import params as qp
    from tradingagents.trading import calibrate as cal

    mom, fb = trader.momentum_book(), trader.factor_book()
    basket = fb.meta.get("fb_basket") or []
    last = trader.last_report()
    running = trader.running
    run_btn = (
        '<form method="post" action="/bot/run" style="margin:0">'
        f'<button class="btn btn-primary" type="submit"{" disabled" if running else ""}>'
        f'{"Đang xử lý phiên…" if running else "Chạy phiên ngay"}</button></form>'
    )
    mode = {"paper": "Tài khoản ảo", "signal": "Tài khoản ảo kèm tín hiệu thật", "off": "Tắt"}.get(trader.settings.trade_mode, "")
    lead = (f'Chế độ <b>{mode}</b> · tín hiệu thật theo chiến lược <b>{html.escape(cal.STRATEGIES.get(trader.settings.signal_strategy, ""))}</b>'
            f' · tự động chạy lúc {html.escape(_run_time(trader))} mỗi ngày và bù các phiên bị lỡ'
            + (f' · phiên gần nhất {html.escape(last["day"])}' if last.get("day") else ""))
    parts = [ui.title("Bot giao dịch", lead, run_btn), ui.alert(msg, "info")]
    base = _common_start(trader.paper(), mom, fb)
    parts.append(ui.card("Hiệu quả ba chiến lược", _chart(trader),
                         sub="NAV quy về 100 tại phiên " + html.escape(base or "đầu tiên")
                             + ", phiên đầu tiên chung của ba tài khoản, đối chiếu với VNINDEX"))
    parts.append('<div class="mt">' + _capital_card(trader) + "</div>")
    basket_html = ('<div class="chips">' + "".join(f'<span class="chip">{html.escape(s)}</span>' for s in basket) + "</div>"
                   if basket else "tiền mặt")
    current = qp.active()
    parts.append('<div class="stack" style="margin-top:16px">'
                 + _book_card("Phòng thủ (mặc định)", fb, accent="accent-factor",
                              sub=f"{current.fb_top_k} mã ít biến động và gần đỉnh 52 tuần nhất trong {current.fb_universe} mã vốn hoá lớn nhất; "
                                  f"chuyển sang tiền mặt khi VNINDEX dưới MA{current.fb_market_ma}")
                 + _book_card("Quant Engine", trader.paper(), accent="accent-peach",
                              sub="Thực hiện kế hoạch MUA của Quant Engine: vùng mua, điểm cắt lỗ, mục tiêu T1/T2")
                 + _book_card("Động lượng VN30", mom, accent="accent-sa",
                              sub="5 mã VN30 tăng mạnh nhất; chuyển sang tiền mặt khi VNINDEX dưới MA100 (chiến lược đối chứng)")
                 + "</div>")
    parts.append('<div class="mt"></div>')
    parts.append(ui.card("Danh mục Phòng thủ hiện tại", basket_html if basket else
                         '<span class="muted">Đang nắm giữ tiền mặt (VNINDEX dưới đường trung bình) hoặc chưa đến kỳ xếp hạng.</span>'))
    parts.append('<div class="mt">' + _breakout_card(current) + "</div>")
    prow = []
    groups = [(qp.FACTOR_FIXED, "Phòng thủ (cố định)"), (qp.MOMENTUM_TUNABLE, "Động lượng"), (qp.TUNABLE, "Engine"),
              (qp.MANUAL, "Engine (đổi tay)")]
    for k in list(qp.TUNABLE) + list(qp.MANUAL) + list(qp.MOMENTUM_TUNABLE) + list(qp.FACTOR_FIXED):
        default, now = getattr(qp.DEFAULT, k), getattr(current, k)
        changed = default != now
        group = next(name for keys, name in groups if k in keys)
        prow.append(f'<tr class="{"changed" if changed else ""}"><td><code>{html.escape(k)}</code></td>'
                    f'<td class="muted small">{group}</td><td class="num muted">{default}</td>'
                    f'<td class="num"><b>{now}</b></td>'
                    f'<td>{(_HAND_BADGE if k in qp.MANUAL else _TUNED_BADGE) if changed else ""}</td></tr>')
    reports = cal.last_reports(14)
    crow = [
        f'<tr><td class="num">{html.escape(r["as_of"])}</td>'
        f'<td>{html.escape(cal.STRATEGIES.get(r.get("strategy", "engine"), ""))}</td>'
        f'<td>{(_CHANGED_BADGE + html.escape(r["accepted"]["name"])) if r.get("accepted") else _KEPT_BADGE}</td>'
        f'<td class="muted small">{html.escape(r.get("note") or "")}</td></tr>'
        for r in reversed(reports)]
    parts.append('<div class="stack" style="margin-top:16px">'
                 + ui.card("Tự hiệu chỉnh hằng đêm",
                           ui.table([("Ngày", ""), ("Chiến lược", ""), ("Kết quả", ""), ("Ghi chú", "")], crow,
                                    f"Chưa có lần hiệu chỉnh nào; lần đầu chạy sau phiên giao dịch đầu tiên, lúc {html.escape(_run_time(trader))}."),
                           sub="Quant Engine và Động lượng: tối đa một điều chỉnh nhỏ mỗi đêm; Phòng thủ giữ nguyên bộ tham số đã kiểm định", flush=True)
                 + ui.card("Bộ ngưỡng hiện hành",
                           ui.table([("Ngưỡng", ""), ("Nhóm", ""), ("Mặc định", "num"), ("Hiện hành", "num"), ("", "")],
                                    prow, ""),
                           sub="Dòng được tô màu là ngưỡng bot đã tự hiệu chỉnh, hoặc công tắc đã đổi tay", flush=True)
                 + "</div>")
    if last.get("text"):
        parts.append('<div style="margin-top:16px">'
                     + ui.card(f"Báo cáo phiên {html.escape(last['day'])}", f'<pre class="report">{html.escape(last["text"])}</pre>')
                     + "</div>")
    return "".join(parts)


def _altcoin_off_body() -> str:
    """/altcoin/ while the altcoin is uploaded but off (ALTCOIN_ENABLED not 1): how to turn it on."""
    steps = (
        "<ol><li>Trên panel Botkeep của server này (khung <code>.env</code>), đặt <code>ALTCOIN_ENABLED=1</code>.</li>"
        "<li>Dữ liệu Altcoin (DB riêng, không bao giờ dùng DB của Cổ phiếu):<ul>"
        "<li>Chạy mới, ảo (paper), DB trống: thêm <code>ALTCOIN_FRESH_START=1</code> cho lần chạy đầu, "
        "rồi xoá dòng này.</li>"
        "<li>Đã có bot Altcoin cũ: chuyển DB cũ sang trước (<code>ALT_DATABASE_URL</code> là chuỗi Supabase cũ, hoặc "
        "file SQLite cũ thành <code>data/altcoin/bot.db</code>) theo mục 9 của <code>docs/ALTCOIN_TRONG_STOCKVN.md</code> "
        "(trong repo, không có trong gói upload); "
        "không đặt <code>ALTCOIN_FRESH_START</code>.</li></ul></li>"
        "<li>Khởi động lại server.</li>"
        '<li>Mở lại <a href="/altcoin/">/altcoin/</a> với cùng mật khẩu trang web. Thanh Cổ phiếu / Altcoin báo '
        "“(đang khởi động)” rồi “Altcoin” khi đã chạy; nếu không chạy được, trang này nói lý do và các dòng log cuối.</li>"
        "</ol>")
    notes = ("<ul><li>Mặc định chạy ảo (paper). Chạy live cần <code>ALT_MODE=live</code> và đủ điều kiện ở mục 10 "
             "của tài liệu.</li><li>Lệnh: <code>/alt status</code> trên Telegram, <code>alt status</code> trên Zoom "
             "(kênh: <code>/vnbot alt status</code>).</li>"
             "<li>Các biến <code>ALT_…</code> khác (khoá AI riêng, thư mục dữ liệu…): mục 4 của tài liệu.</li></ul>")
    return (ui.title("Altcoin đang tắt", "Bot Altcoin (OKX) có trong gói upload nhưng chưa chạy: "
                     "ALTCOIN_ENABLED trên panel Botkeep khác 1. Bot Cổ phiếu chạy bình thường, không phụ thuộc Altcoin.")
            + '<div class="grid g2">' + ui.card("Cách bật", steps) + ui.card("Cần biết", notes) + "</div>")


def create_app(settings: Settings, jobs: JobQueue, watchlist: Watchlist, trader=None, zoom=None,
               switches=None, throttle: LoginThrottle | None = None) -> FastAPI:
    """``throttle``: the wrong-password counter, shared with the Hub (/altcoin/) when given."""
    from vnbot.channels import Switches

    app = FastAPI(title="TradingAgents VN", docs_url=None, redoc_url=None, openapi_url=None)
    if switches is None:
        switches = Switches(settings.data_dir, {"intraday": settings.intraday_alerts})

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response

    def password_in(scope) -> bool:
        """The request carries WEB_PASSWORD (HTTP Basic); the guard still checks it, throttled."""
        header = next((v.decode("latin-1") for k, v in scope.get("headers") or [] if k == b"authorization"), None)
        return webguard.password_ok(header, settings.web_password)

    from vnbot import taisan_web

    # outermost: before any body is read; the Excel upload may be larger, with the password
    app.add_middleware(BodyLimit, limit=MAX_BODY_BYTES,
                       wide={taisan_web.IMPORT_PATH: (taisan_web.MAX_UPLOAD_BYTES + 64 * 1024, password_in)})

    throttle = throttle or LoginThrottle()
    app.state.login_throttle = throttle

    def guard(request: Request) -> None:
        refused = webguard.check_basic(request.headers.get("authorization"), settings.web_password, throttle,
                                       _client(request))
        if refused:
            raise HTTPException(*refused)

    def same_origin(request: Request) -> None:
        """Refuse a form posted from another site (CSRF): Basic auth rides along with it."""
        if not webguard.same_origin_ok(request.headers, settings.public_url):
            raise HTTPException(status.HTTP_403_FORBIDDEN, webguard.CROSS_SITE_MESSAGE)

    post_guard = [Depends(guard), Depends(same_origin)]

    @app.get("/health")
    async def health() -> JSONResponse:
        # Async: it must answer even when every threadpool worker is busy (a burst of sync
        # requests), or Botkeep sees "not responding" and restarts the bot.
        from vnbot import boot, version as ver

        return JSONResponse({"ok": True, "time": time.time(), "version": ver.VERSION, **boot.health_fields()})

    @app.get("/phienban", dependencies=[Depends(guard)])
    def version_page() -> HTMLResponse:
        """The running version, the release check and the release history."""
        import platform

        from vnbot import version as ver

        ok = ver.is_release()
        check = ('<span class="badge b-ok">đúng bản phát hành</span>' if ok
                 else '<span class="badge b-err">khác bản phát hành</span>')
        stats = ui.strip([
            ("Phiên bản", html.escape(ver.VERSION), f"phát hành {html.escape(ver._ddmmyyyy(ver.RELEASED))}"),
            ("Bản dựng", f"<code>{html.escape(ver.build())}</code>", check),
            ("Đang chạy từ", html.escape(ver.started_text()), html.escape(ver.uptime())),
            ("Python", html.escape(platform.python_version()), html.escape(platform.system())),
        ])
        warn = "" if ok else ui.alert(
            f"Code đang chạy (bản dựng {ver.build()}) khác bản phát hành {ver.VERSION} ({ver.RELEASE_BUILD}): có thể "
            "upload thiếu file, lẫn file của bản cũ, hoặc code đã được sửa. Hãy upload lại đủ gói rồi khởi động lại.",
            "err")
        history = "".join(
            f'<h3>{html.escape(v)}</h3><p class="muted small">phát hành {html.escape(ver._ddmmyyyy(day))}'
            + (' <span class="badge b-ok">đang chạy</span>' if v == ver.VERSION else "") + "</p>"
            + "<ul>" + "".join(f"<li>{html.escape(_named(n))}</li>" for n in notes) + "</ul>"
            for v, day, notes in ver.NOTES)
        how = ("<ol><li>Upload đủ gói của phiên bản mới lên Botkeep (ghi đè các file cũ).</li>"
               "<li>Khởi động lại bot.</li>"
               "<li>Kiểm tra: góc trên trang web và lệnh <code>/phienban</code> trong Telegram/Zoom hiện số mới; "
               "bot tự nhắn “Bot đã cập nhật” vào Telegram/Zoom.</li>"
               "<li>Bản dựng phải báo <b>đúng bản phát hành</b>; báo khác là thiếu file hoặc lẫn file cũ.</li></ol>")
        body = (ui.title("Phiên bản", "Bản đang chạy trên server, mã bản dựng để kiểm tra đã upload đúng và đủ, "
                        "và những gì thay đổi qua từng phiên bản.")
                + stats + warn
                + '<div class="grid g-wide mt">' + ui.card("Lịch sử phiên bản", history, sub="mới nhất ở trên")
                + ui.card("Cập nhật bot", how) + "</div>")
        return _page("Phiên bản", body, active="phienban", show_bot=trader is not None and settings.trade_mode != "off")

    @app.get("/static/{name}")
    def static_file(name: str):
        """The logo and favicon (public: the Zoom pages show them too)."""
        if name not in ui.STATIC_FILES:
            raise HTTPException(404, "Không tìm thấy trang.")
        return FileResponse(ui.STATIC_DIR / name, media_type="image/png",
                            headers={"Cache-Control": "public, max-age=86400"})

    async def zoom_webhook(request: Request) -> JSONResponse:
        """Zoom chatbot events (Bot endpoint, Event notification and Deauthorization endpoint).
        Signed by Zoom (HMAC), no password; the last requests are kept for the /zoom page."""
        if zoom is None or not zoom.chatbot_on:
            return JSONResponse({"error": "zoom is off"}, status_code=404)
        raw = await request.body()
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            return JSONResponse({"error": "bad json"}, status_code=400)
        if not isinstance(data, dict):
            return JSONResponse({"error": "bad json"}, status_code=400)
        event = data.get("event")
        ok, why = zoom.verify_detail(raw, request.headers)
        # Everything below reads/writes zoom_state.json (fsync) and may send a Telegram
        # alert (a blocking HTTP call with retries): off the event loop, or every other
        # route (/health included) would freeze while it runs.
        if event == "endpoint.url_validation":
            plain = str((data.get("payload") or {}).get("plainToken", ""))
            if not ok or not plain or not zoom.config.secret_token:
                zoom.record_refused(event, why if not ok else "thiếu plainToken hoặc ZOOM_SECRET_TOKEN")
                return JSONResponse({"error": "invalid"}, status_code=401 if not ok else 400)
            await run_in_threadpool(zoom.record, event, True, "đã trả lời Validate")
            return JSONResponse(zoom.url_validation(plain))
        if not ok:
            # An unsigned request writes nothing to disk: anyone can POST here, and 20 junk
            # rows would push the real requests off the /zoom page (and cost an fsync each).
            zoom.record_refused(event, why)
            logger.warning("Zoom webhook from %s refused: %s", _client(request), why)
            return JSONResponse({"error": "invalid signature"}, status_code=401)
        await run_in_threadpool(zoom.record, event, ok, why, data)
        if event in ("app_deauthorized", "bot_installed"):
            await run_in_threadpool(zoom.lifecycle, event, data.get("payload") or {})
            return JSONResponse({"ok": True})
        if not zoom.commands_on:
            return JSONResponse({"error": "zoom commands are off"}, status_code=404)
        if not switches.on("zoom_commands"):
            await run_in_threadpool(zoom.record, event, True, "bỏ qua: nhận lệnh Zoom đang tắt trên trang web", data)
            return JSONResponse({"ok": True, "ignored": "zoom commands switched off"})
        await run_in_threadpool(zoom.handle, data)
        return JSONResponse({"ok": True})

    app.add_api_route("/webhook/zoom", zoom_webhook, methods=["POST"])
    app.add_api_route("/zoom/bot", zoom_webhook, methods=["POST"])     # the OKX bot's other path

    from vnbot import pages

    pages.register(app, settings, zoom, guard=guard, post_guard=post_guard,
                   notify=getattr(zoom, "alert_owner", None))

    from vnbot.taisan import TaiSan

    taisan_web.register(app, getattr(trader, "taisan", None) or TaiSan(settings.data_dir), guard=guard,
                        post_guard=post_guard, show_bot=lambda: trader is not None and settings.trade_mode != "off",
                        notify=getattr(trader, "notify", None))

    from vnbot import ai_pick_web

    ai_pick_web.register(app, settings.data_dir, guard=guard, post_guard=post_guard,
                             show_bot=lambda: trader is not None and settings.trade_mode != "off")

    from vnbot import config_web

    config_web.register(app, settings.data_dir, guard=guard, post_guard=post_guard,
                        show_bot=lambda: trader is not None and settings.trade_mode != "off")

    from vnbot import chandoan_web

    chandoan_web.register(app, settings.data_dir, guard=guard, post_guard=post_guard,
                          show_bot=lambda: trader is not None and settings.trade_mode != "off",
                          watchlist=watchlist.get, notify=getattr(trader, "notify", None))

    from vnbot import dinhgia_web

    dinhgia_web.register(app, settings.data_dir, guard=guard, watchlist=watchlist.get, account=app.state.taisan,
                         show_bot=lambda: trader is not None and settings.trade_mode != "off")

    from vnbot import altcoin_host

    if altcoin_host.off_but_uploaded():     # on, the Hub answers everything under /altcoin before this app
        def altcoin_off() -> HTMLResponse:
            return HTMLResponse(ui.page("Altcoin đang tắt", _altcoin_off_body(), section="altcoin",
                                        show_bot=trader is not None and settings.trade_mode != "off"))

        for path in ("/altcoin", "/altcoin/"):      # nothing else under /altcoin/: the stock's 404
            app.add_api_route(path, altcoin_off, methods=["GET"], dependencies=[Depends(guard)])

    critic_llm: list = []        # the provider chain, built on the first web /critic

    def _ai_today() -> tuple[dict, list[dict]]:
        from tradingagents.llm_clients.fallback_client import provider_status, usage_today

        return usage_today(), provider_status()

    def _ai_stat(usage: dict) -> tuple[str, str, str]:
        cap = usage["cap"]
        value = f'{usage["total"]}<span class="muted" style="font-size:.6em"> / {cap}</span>' if cap else str(usage["total"])
        return ("Lượt AI hôm nay", value, "trong hạn mức ngày" if cap else "không giới hạn")

    def _alerts_today() -> list[dict]:
        from vnbot.intraday import read_alerts

        return read_alerts(settings.data_dir / "intraday_alerts.json", today_vn())

    @app.get("/", dependencies=[Depends(guard)])
    def index(error: str = "") -> HTMLResponse:
        from tradingagents.market import vn_rules

        usage, providers = _ai_today()
        rows = [_job_row(j) for j in jobs.recent(30)]
        codes = watchlist.get()
        today = today_vn()
        phase = vn_rules.session_phase()
        scope = {"vn100": "VN100", "vn30": "VN30"}.get(settings.scan_universe, "Watchlist")
        alerts = _alerts_today()
        ready = sum(1 for p in providers if not p["resting_seconds"])
        stats = ui.strip([
            _ai_stat(usage),
            ("Thị trường", ui.words(_PHASE_VI.get(phase, phase)), f"{html.escape(today)} · giờ Việt Nam"),
            ("Cảnh báo hôm nay", str(len(alerts)),
             "trong phiên · không dùng AI" if switches.on("intraday") else "cảnh báo trong phiên đang tắt"),
            ("Sàng lọc 15:30", ui.words(scope + (" + watchlist" if scope != "Watchlist" else "")),
             f"tối đa {settings.daily_ai_max} mã cho AI · {_MODE_VI.get(settings.daily_ai_mode, '')}"),
        ])
        ask = f"""
<form method="post" action="/analyze">
  <div class="row">
    <div class="fld"><label class="f" for="ticker">Mã cổ phiếu</label>
      <input class="ticker" id="ticker" name="ticker" placeholder="FPT" required maxlength="20" autocomplete="off" autocapitalize="characters"></div>
    <div class="fld"><label class="f" for="date">Ngày phân tích</label>
      <input id="date" name="date" type="date" value="{today}" max="{today}"></div>
  </div>
  <div class="row" style="margin-top:14px">
    <button class="btn btn-primary" type="submit" formaction="/critic">AI phản biện</button>
    <button class="btn btn-ghost" type="submit" formaction="/quick">Quyết định định lượng</button>
    <button class="btn btn-ghost" type="submit">Phân tích chuyên sâu</button>
  </div>
  <p class="muted small" style="margin:12px 0 0">Quyết định định lượng có sau vài giây và không dùng lượt AI.
  AI phản biện gửi một yêu cầu duy nhất (1 lượt): AI đồng thuận hay phản đối, kèm lý do. Phân tích chuyên sâu huy động
  toàn bộ chuỗi agent (khoảng 25 lượt) và mất vài phút.</p>
</form>"""
        chips = ('<div class="chips" style="margin-bottom:14px">'
                 + "".join(f'<span class="chip">{html.escape(c)}</span>' for c in codes) + "</div>") if codes else ""
        watch = f"""{chips}
<form method="post" action="/watchlist" class="row">
  <div class="fld" style="flex:1 1 220px"><label class="f" for="codes">Danh sách mã</label>
    <input id="codes" name="codes" value="{html.escape(",".join(codes))}" placeholder="FPT,HPG,VCB"></div>
  <button class="btn btn-sa" type="submit">Lưu danh sách</button>
</form>
<p class="muted small" style="margin:12px 0 0">Mỗi phiên giao dịch, lúc {html.escape(settings.daily_report_time)}, Quant Engine
sàng lọc {html.escape(scope)} cùng danh sách theo dõi; những mã nổi bật nhất được AI phản biện và gửi qua Telegram/Zoom.</p>"""
        bot_link = ""
        if trader is not None and settings.trade_mode != "off":
            books = [("Phòng thủ", trader.factor_book()), ("Quant Engine", trader.paper()),
                     ("Động lượng", trader.momentum_book())]

            def kpi(title, book):
                st = book.stats()
                since = f' · từ {html.escape(book.start_date)}' if book.start_date else ""
                return ui.kpi(title, ui.pct(st["return_pct"]), f'NAV {ui.money_short(st["nav"])}{since}',
                              ui.tone(st["return_pct"]))

            bot_link = ui.card(
                "Bot giao dịch",
                '<div class="kpis c3">' + "".join(kpi(t, b) for t, b in books) + "</div>",
                sub="Ba tài khoản ảo, lãi/lỗ tính từ ngày khởi tạo của từng tài khoản",
                action='<a class="btn btn-ghost" href="/bot">Chi tiết</a>', cls="accent-gold",
            )
        ai_card = ui.card(
            "Nhà cung cấp AI",
            f'<p class="small" style="margin:0">{ready}/{len(providers)} sẵn sàng · '
            f'hôm nay đã dùng <b>{usage["total"]}</b>' + (f' / {usage["cap"]} lượt' if usage["cap"] else " lượt") + "</p>"
            + ui.meter(usage["total"], usage["cap"]),
            sub="Theo thứ tự dự phòng" if providers else
                "Chưa cấu hình nhà cung cấp AI (ROUTER9_*, GROQ_API_KEY, OPENROUTER_API_KEY).",
            action='<a class="btn btn-ghost" href="/ai">Xem</a>')
        body = (
            ui.title("Tổng quan",
                    "Mọi hành động và mức giá do hệ thống định lượng xác lập; AI chỉ đóng vai trò phản biện và diễn giải.")
            + ui.alert(error, "err")
            + stats
            + '<div class="grid g-wide">'
            + '<div class="stack">' + ui.card("Phân tích một mã", ask, cls="accent-gold")
            + ui.card("Lượt phân tích gần đây",
                      ui.table([("Mã", ""), ("Ngày", "num"), ("Hành động", ""), ("Trạng thái", ""), ("Nguồn", ""),
                                ("Tạo lúc", "num")], rows, "Chưa có lượt phân tích nào."),
                      sub="Trang tự cập nhật khi có lượt đang chạy" if jobs.is_active() else "", flush=True)
            + "</div>"
            + '<div class="stack">' + bot_link + ui.card("Danh sách theo dõi", watch) + ai_card + "</div>"
            + "</div>"
        )
        return _page("Tổng quan", body, refresh=20 if jobs.is_active() else None, active="home",
                     show_bot=trader is not None and settings.trade_mode != "off")

    @app.get("/ai", dependencies=[Depends(guard)])
    def ai_page() -> HTMLResponse:
        from vnbot.critic import CriticLog

        usage, providers = _ai_today()
        log = CriticLog(settings.data_dir)
        st = log.stats()
        alerts = _alerts_today()
        prow = []
        for p in providers:
            state = (f'<span class="badge b-watch">tạm nghỉ {p["resting_seconds"]} giây</span>'
                     if p["resting_seconds"] else '<span class="badge b-ok">sẵn sàng</span>')
            calls = f'{p["calls_today"]} / {p["cap"]}' if p["cap"] else str(p["calls_today"])
            prow.append(f'<tr><td><b>{html.escape(p["label"])}</b><div class="muted small">{html.escape(p["model"])}</div>'
                        + (f'<div class="small neg">{html.escape(p["last_error"])}</div>' if p["last_error"] else "")
                        + f'</td><td>{state}</td><td class="num">{calls}{ui.meter(p["calls_today"], p["cap"])}</td></tr>')
        budget = ui.card(
            "Hạn mức AI hôm nay",
            ui.table([("Nhà cung cấp", ""), ("Trạng thái", ""), ("Lượt", "num")], prow,
                     "Chưa cấu hình nhà cung cấp AI (ROUTER9_*, GROQ_API_KEY, OPENROUTER_API_KEY)."),
            sub=f"Đã dùng {usage['total']}" + (f" / {usage['cap']} lượt (LLM_MAX_CALLS_PER_DAY)" if usage["cap"] else " lượt")
                + " · tính theo ngày giờ Việt Nam", flush=True)
        feed = "".join(f'<li><span class="t">{html.escape(a.get("at", ""))}</span><span>{html.escape(a.get("text", ""))}</span></li>'
                       for a in reversed(alerts))
        if not feed:
            when = (f"Bot kiểm tra mỗi {settings.intraday_interval_min} phút trong giờ khớp lệnh liên tục."
                    if switches.on("intraday") else "Cảnh báo trong phiên đang tắt.")
            feed = f'<li class="empty">Hôm nay chưa có cảnh báo. {html.escape(when)}</li>'
        alert_card = ui.card("Cảnh báo trong phiên", f'<ul class="feed">{feed}</ul>',
                             sub="Cắt lỗ, mục tiêu, vùng mua và các nhịp giảm mạnh của VNINDEX · không dùng AI")
        crow = []
        for r in reversed(log.rows()[-30:]):
            out = r.get("outcome")
            if out is None:
                result = '<span class="badge b-none">chờ kết quả</span>'
            elif r["action"] == "BUY":
                result = f'<span class="num {ui.tone(out["r"])}">{out["r"]:+.2f}R</span> <span class="muted small">{html.escape(out["label"])}</span>'
            else:
                result = f'<span class="num">{ui.pct(out.get("pct"))}</span> <span class="muted small">{html.escape(out["label"])}</span>'
            verdict = ('<span class="badge b-ok">đồng ý</span>' if r["verdict"] == "AGREE"
                       else '<span class="badge b-err">phản đối</span>')
            crow.append(f'<tr><td class="num muted">{html.escape(r["day"])}</td><td class="sym">{html.escape(r["code"])}</td>'
                        f'<td>{ui.action_badge(r["action"])}</td><td>{verdict} <span class="muted small">{r["confidence"]}%</span></td>'
                        f'<td class="small">{html.escape(r.get("reason", ""))}</td><td>{result}</td></tr>')
        agree, reject = st["AGREE"], st["REJECT"]

        def r_of(x):
            return f'{x["avg_r"]:+.2f}R'.replace(".", ",") if x["n"] else ui.NONE

        stats = ui.strip([
            _ai_stat(usage),
            ("AI phản đối đúng", f'{st["veto_right_pct"]}%' if st["veto_right_pct"] is not None else ui.NONE,
             f'trên {reject["n"]} lệnh MUA bị phản đối đã có kết quả'),
            ("Kết quả theo dõi", f'{r_of(agree)} <span class="muted" style="font-size:.6em">/ {r_of(reject)}</span>'
             if agree["n"] or reject["n"] else ui.NONE,
             "R trung bình: AI đồng ý / phản đối"),
            ("Cảnh báo hôm nay", str(len(alerts)), "trong phiên"),
        ])
        channels = [
            ("telegram", "Telegram · thông báo", settings.telegram_token != "",
             "Báo cáo hằng ngày, AI phản biện và cảnh báo; khi tắt, bot vẫn trả lời lệnh"),
            ("zoom_notify", "Zoom · thông báo", bool(zoom and zoom.notify_on),
             "Incoming Webhook hoặc chatbot tới tài khoản đã ghép đôi"),
            ("zoom_commands", "Zoom · nhận lệnh", bool(zoom and zoom.commands_on), "Lệnh gõ trong Zoom (/webhook/zoom)"),
            ("intraday", "Cảnh báo trong phiên", trader is not None and settings.trade_mode != "off",
             f"Mỗi {settings.intraday_interval_min} phút trong giờ khớp lệnh · không dùng AI"),
            ("zones_bot", "Vùng Rẻ/Đắt theo thẻ của bot", trader is not None and settings.trade_mode != "off",
             ("Trong cảnh báo trong phiên: giá vào vùng Rẻ/Đắt theo thẻ định giá bot tự tính (watchlist, mã đang giữ); "
              "mã có thẻ anh nhập (/tsdinhgia) luôn báo theo thẻ của anh")),
            ("altcoin", "Altcoin · thông báo", (os.environ.get("ALTCOIN_ENABLED") or "").strip() == "1",
             "Tin của bot Altcoin, mở đầu “🪙 Altcoin ›”, qua Telegram và Zoom ở trên; lệnh: /alt"),
        ]
        chrow = [f'<tr><td><b>{label}</b><div class="muted small">{html.escape(note)}</div></td>'
                 f"<td class='num'>{_switch_form(key, switches.on(key)) if ready else _UNSET_BADGE}</td></tr>"
                 for key, label, ready, note in channels]
        body = (
            ui.title("AI & cảnh báo",
                    "AI chỉ được hỏi khi thực sự cần: mỗi mã một lượt, trong hạn mức hằng ngày. Mọi ý kiến phản biện "
                    "đều được lưu lại và đối chiếu với diễn biến các phiên sau, giúp bạn đánh giá mức độ đáng tin của AI.")
            + stats
            + '<div class="grid g2">' + budget + alert_card + "</div>"
            + '<div class="mt">'
            + ui.card("AI phản biện gần đây",
                      ui.table([("Ngày", ""), ("Mã", ""), ("Hệ thống", ""), ("AI", ""), ("Lý do", ""), ("Kết quả", "")],
                               crow, "Chưa có lần phản biện nào. Lượt sàng lọc 15:30 hoặc nút “AI phản biện” sẽ tạo dữ liệu đầu tiên."),
                      sub="Lệnh MUA: chạm cắt lỗ (−1R), chạm mục tiêu hoặc sau 20 phiên · Lệnh BÁN: diễn biến giá sau 10 phiên",
                      flush=True)
            + "</div>"
            + '<div class="mt">' + ui.card("Kênh thông báo", ui.table([("Kênh", ""), ("", "")], chrow, ""), flush=True,
                                            action='<a class="btn btn-ghost" href="/zoom">Cấu hình Zoom</a>',
                                            id="kenh") + "</div>"
        )
        return _page("AI & cảnh báo", body, active="ai", show_bot=trader is not None and settings.trade_mode != "off")

    @app.post("/critic", dependencies=post_guard)
    def critic_web(ticker: str = Form(...), date: str = Form("")):
        from tradingagents.quant import engine
        from vnbot.critic import CriticLog, default_llm

        try:
            code, day = check_request(ticker, date)
        except ValueError as exc:
            return RedirectResponse(f"/?error={quote(str(exc))}", status_code=303)
        try:
            result = engine.analyze(code, day)
            if not critic_llm:
                critic_llm.append(default_llm(settings))
            log = CriticLog(settings.data_dir)
            log.settle_now()
            verdict = log.review(day, result, critic_llm[0])
        except Exception:  # noqa: BLE001 - shown to the user, not raised
            logger.exception("Web critic for %r failed", code)
            return RedirectResponse(f"/?error={quote('Chưa thể phản biện (lỗi dữ liệu hoặc AI). Vui lòng thử lại sau ít phút.')}",
                                    status_code=303)
        body = (ui.title(f'<span class="tick">{html.escape(code)}</span> {ui.action_badge(result.action)}',
                        f"Phiên {html.escape(day)} · AI phản biện, một lượt AI",
                        '<a class="btn btn-ghost" href="/">Mã khác</a>')
                + '<div class="stack">'
                + ui.card("Nhận định của AI", f'<pre class="report">{html.escape(verdict)}</pre>',
                          sub="AI không thay đổi hành động hay mức giá; kết quả sẽ được đối chiếu tại trang AI & cảnh báo",
                          cls="accent-gold")
                + ui.card("Quyết định hệ thống định lượng", f'<pre class="report">{html.escape(result.deterministic_summary)}</pre>',
                          sub="Quant Engine · tính bằng code")
                + "</div>")
        return _page(f"{code} - AI phản biện", body, active="home")

    @app.post("/analyze", dependencies=post_guard)
    def analyze(ticker: str = Form(...), date: str = Form("")):
        try:
            code, day = check_request(ticker, date)
            job = jobs.submit(code, day, "web")
        except ValueError as exc:   # a bad request, or QueueFull
            return RedirectResponse(f"/?error={quote(str(exc))}", status_code=303)
        return RedirectResponse(f"/jobs/{job.id}", status_code=303)

    @app.post("/quick", dependencies=post_guard)
    def quick(ticker: str = Form(...), date: str = Form("")):
        try:
            text = quick_text(ticker, date)
        except ValueError as exc:
            return RedirectResponse(f"/?error={quote(str(exc))}", status_code=303)
        except Exception:  # noqa: BLE001 - shown to the user, not raised
            # The details go to the log; the page only says what failed.
            logger.exception("Quick decision for %r failed", ticker)
            text = "Chưa tính được quyết định định lượng (lỗi dữ liệu hoặc tính toán). Vui lòng thử lại sau ít phút."
        code = ticker.strip().upper()[:20]
        body = (ui.title(f'<span class="tick">{html.escape(code)}</span> · quyết định định lượng',
                        "Do Quant Engine tính toán, không dùng AI.",
                        '<a class="btn btn-ghost" href="/">Mã khác</a>')
                + ui.card("", f'<pre class="report">{html.escape(text)}</pre>'))
        return _page(f"{code} - quyết định định lượng", body, active="home")   # page() escapes the title

    @app.post("/watchlist", dependencies=post_guard)
    def save_watchlist(codes: str = Form("")):
        try:
            wanted = [c for c in codes.replace(" ", ",").split(",") if c.strip()]
            if len(wanted) > MAX_WATCHLIST:
                raise ValueError(f"Danh sách theo dõi có tối đa {MAX_WATCHLIST} mã.")
            watchlist.set(wanted)
        except ValueError as exc:
            return RedirectResponse(f"/?error={quote(str(exc))}", status_code=303)
        return RedirectResponse("/", status_code=303)

    @app.get("/bot", dependencies=[Depends(guard)])
    def bot_page(msg: str = "") -> HTMLResponse:
        if trader is None or settings.trade_mode == "off":
            raise HTTPException(404, "Bot giao dịch đang tắt (TRADE_MODE=off).")
        return _page("Bot giao dịch", _bot_page(trader, msg), active="bot", refresh=15 if trader.running else None)

    @app.post("/bot/run", dependencies=post_guard)
    def bot_run():
        if trader is None or settings.trade_mode == "off":
            raise HTTPException(404, "Bot giao dịch đang tắt (TRADE_MODE=off).")
        return RedirectResponse(f"/bot?msg={quote(trader.start_run())}", status_code=303)

    @app.post("/bot/capital", dependencies=post_guard)
    def bot_capital(books: Annotated[list[str] | None, Form()] = None, capital: str = Form(""), confirm: str = Form("")):
        if trader is None or settings.trade_mode == "off":
            raise HTTPException(404, "Bot giao dịch đang tắt (TRADE_MODE=off).")
        try:
            if confirm != "1":
                raise ValueError("Vui lòng đánh dấu ô xác nhận: các tài khoản được chọn sẽ khởi động lại từ đầu.")
            amount = float("".join(ch for ch in capital if ch in "0123456789") or 0)
            msg = trader.reset_paper(amount, books or [])
        except ValueError as exc:
            msg = f"⚠️ {exc}"
        return RedirectResponse(f"/bot?msg={quote(msg)}", status_code=303)

    @app.post("/bot/breakout", dependencies=post_guard)
    def bot_breakout(on: str = Form("1")):
        """The Quant Engine's breakout BUYs on or off (Params.breakout_buy, kept in quant_params.json)."""
        if trader is None or settings.trade_mode == "off":
            raise HTTPException(404, "Bot giao dịch đang tắt (TRADE_MODE=off).")
        from dataclasses import replace

        from tradingagents.quant import params as qp

        value = 1 if on == "1" else 0
        qp.save(replace(qp.active(), breakout_buy=value), note=f"trang Bot giao dịch: MUA breakout {'bật' if value else 'tắt'}")
        logger.info("Web: the Quant Engine's breakout BUYs switched %s", "on" if value else "off")
        msg = ("✅ Đã bật lại MUA breakout của Quant Engine, từ lượt quyết định kế tiếp." if value else
               "✅ Đã tắt MUA breakout: từ lượt quyết định kế tiếp Quant Engine không mở MUA mới ở mã breakout (lệnh mua đã "
               "đặt cho phiên tới vẫn giữ); kết quả bóng của các mã đó ở trang Vì sao chưa MUA.")
        return RedirectResponse(f"/bot?msg={quote(msg)}", status_code=303)

    @app.post("/channels/{name}", dependencies=post_guard)
    def channel_switch(name: str, on: str = Form("1")):
        try:
            switches.set(name, on == "1")
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from None
        logger.info("Web: channel %s switched %s", name, "on" if on == "1" else "off")
        return RedirectResponse("/ai#kenh", status_code=303)

    @app.get("/jobs/{job_id}", dependencies=[Depends(guard)])
    def job_page(job_id: str) -> HTMLResponse:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Không tìm thấy lượt phân tích này.")
        live = job.status in ("queued", "quick", "running")
        right = (f'<a class="btn btn-primary" href="/reports/{job.id}">Báo cáo đầy đủ</a>' if job.html_path
                 else '<a class="btn btn-ghost" href="/">Trang chính</a>')
        parts = [ui.title(f'<span class="tick">{html.escape(job.ticker)}</span> {ui.action_badge(job.action)}',
                         f"Ngày phân tích {html.escape(job.date)} · {_SOURCE_VI.get(job.source, '')}", right),
                 ui.stepper(job.status)]
        if job.status == "queued":
            parts.append(ui.alert(f"Còn {jobs.position(job.id)} lượt chờ phía trước · trang sẽ tự cập nhật.", "info"))
        if job.status == "running":
            parts.append(ui.alert("Các agent AI đang phân tích, thường mất vài phút · trang sẽ tự cập nhật.", "info"))
        if job.error:
            parts.append(ui.alert(job.error, "err"))
        blocks = []
        if job.quick_summary:
            blocks.append(ui.card("Quyết định hệ thống định lượng", f'<pre class="report">{html.escape(job.quick_summary)}</pre>',
                                  sub="Quant Engine · đã chốt, các agent không được thay đổi", cls="accent-peach"))
        if job.final_decision:
            blocks.append(ui.card("Quyết định cuối cùng", f'<pre class="report">{html.escape(job.final_decision)}</pre>',
                                  sub="Portfolio Manager, sau kiểm duyệt của Output Guard", cls="accent-sa"))
        if job.guard_notes:
            blocks.append(ui.card("Output Guard đã chặn", f'<pre class="report">{html.escape(job.guard_notes)}</pre>',
                                  sub="Các đoạn AI nói trái với hệ thống"))
        if blocks:
            parts.append('<div class="stack" style="margin-top:16px">' + "".join(blocks) + "</div>")
        return _page(f"{job.ticker} {job.date}", "".join(parts), refresh=15 if live else None, active="home")

    @app.get("/reports/{job_id}", dependencies=[Depends(guard)])
    def report(job_id: str):
        job = jobs.get(job_id)
        if job is None or job.html_path is None:
            raise HTTPException(404, "Chưa có báo cáo")
        return FileResponse(job.html_path, media_type="text/html; charset=utf-8")

    # Starlette's class: also the 404/405 the router raises for unknown paths/methods.
    @app.exception_handler(StarletteHTTPException)
    def http_error(request: Request, exc: StarletteHTTPException):
        if exc.status_code == 401:
            return HTMLResponse("Vui lòng đăng nhập", status_code=401, headers=exc.headers)
        message = {"Not Found": "Không tìm thấy trang.",
                   "Method Not Allowed": "Phương thức không được hỗ trợ."}.get(exc.detail, str(exc.detail))
        title = {503: "Trang web đang khoá", 429: "Tạm khoá đăng nhập"}.get(exc.status_code, "Đã xảy ra lỗi")
        body = (ui.title(title, "", '<a class="btn btn-ghost" href="/">Trang chính</a>')
                + ui.alert(message, "err"))
        return HTMLResponse(ui.page("Lỗi", body, show_bot=False, show_version=False), status_code=exc.status_code,
                            headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    def bad_form(request: Request, exc: RequestValidationError):
        body = (ui.title("Đã xảy ra lỗi", "", '<a class="btn btn-ghost" href="/">Trang chính</a>')
                + ui.alert("Biểu mẫu thiếu thông tin hoặc chưa hợp lệ.", "err"))
        return _page("Lỗi", body, status_code=422, show_bot=False)

    @app.exception_handler(Exception)
    def server_error(request: Request, exc: Exception):
        # Never a traceback or an exception text on the page; Starlette re-raises
        # the exception after this response, so the server still logs it.
        body = (ui.title("Đã xảy ra lỗi", "", '<a class="btn btn-ghost" href="/">Trang chính</a>')
                + ui.alert("Máy chủ gặp sự cố khi xử lý yêu cầu. Chi tiết đã được ghi vào nhật ký.", "err"))
        return _page("Lỗi", body, status_code=500, show_bot=False)

    return app
