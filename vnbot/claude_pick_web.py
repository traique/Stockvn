"""The AI chọn bóng page (/claude): the latest session (the pick, the avoided, the views
on the holdings), the running comparisons, the AI's notes on the rule alerts, the weekly
review, the history and the month's spending (``vnbot.claude_pick``).

The page only reads the saved files; the button runs the evening answer in the background
(one to three minutes with Gemini's web search on 9Router) and the page refreshes itself while it runs.
"""

from __future__ import annotations

import html
import logging
import threading
import time
from urllib.parse import quote

from fastapi.responses import HTMLResponse, RedirectResponse

from vnbot import claude_pick as cp
from vnbot import external, ui

logger = logging.getLogger(__name__)

HISTORY_ROWS = 40
NOTE_ROWS = 10
_LEVEL_VI = {"stop": "thủng cắt lỗ", "sell": "trang BÁN", "early": "dấu hiệu sớm"}
_LAST = {"error": ""}          # the last run started from this page that failed outside the log (shown once)


def _e(text) -> str:
    return html.escape(str(text or ""))


def _say(word: str | None) -> str:
    """A view or a rule ("CÂN NHẮC BÁN") in sentence case for the page."""
    return str(word or "").capitalize()


def _value(pct, *, note: str = "") -> str:
    out = f'<span class="num fig {ui.tone(pct)}" style="white-space:nowrap">{ui.pct(pct)}</span>'
    return out + (f'<div class="muted small" style="white-space:nowrap">{_e(note)}</div>' if note else "")


def _quick(res: dict | None) -> str:
    """The quick score (the close ``H10`` sessions after the buy)."""
    if not res:
        return '<span class="muted small">chờ phiên sau</span>'
    return _value(res["pct"], note="" if res["status"] == "closed" else f"{res['sessions']}/{cp.H10} phiên")


def _rule(res: dict | None, source: str) -> str:
    """The rule's result (the site's cut-loss, its SELL, at most ``MAX_HOLD`` sessions)."""
    if source == "bot":
        return '<span class="muted small">không áp dụng</span>'
    if not res:
        return '<span class="muted small">chờ phiên sau</span>'
    return _value(res["pct"], note="đang giữ" if res["status"] != "closed" else res["why"])


def _none() -> str:
    return f'<span class="muted none">{ui.NONE}</span>'


def _links(urls: list[str]) -> str:
    urls = [u for u in urls if str(u).startswith(("https://", "http://"))]   # never a javascript: link
    if not urls:
        return ""
    items = ", ".join(f'<a href="{_e(u)}" rel="noopener noreferrer" target="_blank">{_e(_host(u))}</a>' for u in urls)
    return f'<p class="small muted" style="margin-top:8px">Nguồn: {items}</p>'


def _host(url: str) -> str:
    return cp._norm_url(url).split("/", 1)[0]


def _web(line: str) -> str:
    """A line shared with Telegram ("A · B · C") as one sentence for the page."""
    return line.lstrip("• ").replace(" · ", "; ")


def _who(entry: dict) -> str:
    """The model that answered a record, on 9Router."""
    return f'{_e(entry.get("served_by") or entry.get("model"))} qua 9Router' if entry.get("model") else "Chưa gọi AI"


def _route() -> str:
    """The model that answers now, under the title ("" when 9Router is not set)."""
    return f'<br><span class="small">Trả lời: {_e(cp.model())} qua 9Router</span>' if cp.ready() else ""


def _stats_strip(st: dict) -> str:
    def value(key):
        v = st.get(key)
        return f'<span class="{ui.tone(v)}">{ui.pct(v)}</span>' if v is not None else '<span class="muted">chưa có</span>'

    goal = next((c for c in cp.CHECKPOINTS if st["n"] < c), cp.CHECKPOINTS[-1])
    return ui.strip([
        ("Lệnh AI chọn", value("pick_mean"), f'sau {cp.H10} phiên, thắng {st["pick_win"]:.0f}%' if st["n"]
         else f"giá sau {cp.H10} phiên"),
        ("TB lệnh cùng ngày", value("mean"), "mọi lệnh MUA mới cùng phiên"),
        ("Nửa trên trừ nửa dưới", value("spread"), "theo bảng xếp hạng của AI"),
        ("Đã chấm", f'{st["n"]}<span class="muted" style="font-size:.6em"> / {goal}</span>',
         f"phiên có lựa chọn, kết luận ở mốc {goal}"),
    ])


def _views(entry: dict) -> str:
    who = "AI"
    rows = []
    for h in entry.get("holdings") or []:
        view = h.get("view")
        badge = (f'<span class="badge {"b-ok" if view == "GIỮ" else "b-watch"}">{_e(_say(view))}</span>' if view
                 else '<span class="badge b-none">chưa có ý kiến</span>')
        rule = f'<span class="badge {"b-err" if h["rule"] == "BÁN" else "b-ok"}">{_e(_say(h["rule"]))}</span>'
        later = (h.get("result") or {}).get("r10")
        rows.append(f'<tr><td><span class="sym">{_e(h["symbol"])}</span><div class="muted small"><span class="fig">{ui.pct(h.get("pnl_pct"))}</span> '
                    f'theo giá vốn</div></td><td data-label="Theo quy tắc">{rule}<div class="muted small">{_e(h["rule_why"])}</div></td>'
                    f'<td data-label="{who}">{badge}<div class="small" style="margin-top:4px">{_e(h.get("reason"))}</div>'
                    f'{_links(h.get("sources") or [])}</td>'
                    f'<td data-label="Giá sau 10 phiên">{_value(later) if later is not None else _none()}</td></tr>')
    if not rows:
        return ""
    return ui.card("Danh mục của anh", ui.table([("Mã", ""), ("Theo quy tắc", ""), (who, ""), ("Giá sau 10 phiên", "")],
                                               rows, "", stack=True),
                   sub=f"Phiên {_e(cp._ddmm(entry['day']))}. Quy tắc cắt lỗ và trang BÁN vẫn là quy tắc chính; ý kiến của "
                       f"{who} được đo bằng giá 10 phiên sau.", flush=True)


def _figs(cand: dict) -> str:
    """The pick's figures as the site gave them (none invented for the bot's own)."""
    items = [("Giá tín hiệu", external._fmt(cand.get("price")))]
    if cand.get("entry_low") and cand.get("entry_high"):
        items.append(("Vùng mua", f'{external._fmt(cand["entry_low"])}<br>{external._fmt(cand["entry_high"])}'))
    if cand.get("sl"):
        items.append(("Cắt lỗ", external._fmt(cand["sl"])))
    if cand.get("tp1"):
        items.append(("Mục tiêu", external._fmt(cand["tp1"])))
    return (f'<dl class="sheet-figs" data-n="{len(items)}" style="--n:{len(items)}">'
            + "".join(f"<div><dt>{k}</dt><dd>{v}</dd></div>" for k, v in items) + "</dl>")


# The settled state is pressed into the stock: a noise mask leaves the coral ink uneven.
_INK = ('<svg width="0" height="0" aria-hidden="true" focusable="false" style="position:absolute">'
        '<filter id="ink"><feTurbulence type="fractalNoise" baseFrequency=".9" numOctaves="2" seed="7"/>'
        '<feColorMatrix values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 -.8 1.25"/>'
        '<feComposite in="SourceGraphic" operator="in"/></filter></svg>')


def _sheet(entry: dict | None, button: str) -> str:
    """The session's pick sheet: the code, pressed as chosen, its figures, why, what to avoid,
    then every candidate in the AI's order; or what happened instead."""
    if entry is None:
        return ui.card("Chưa có phiên nào", f'<p class="muted">Bot chọn mỗi phiên lúc {_e(cp.pick_time())}, '
                       "sau khi trang chốt tín hiệu.</p>" + (f'<div class="mt">{button}</div>' if button else ""))
    day = entry["day"]
    cands = entry.get("candidates") or []
    meta = (f'<div><dt>Chạy lúc</dt><dd>{_e(entry.get("at", "")[11:16])} {_e(cp._ddmm(day))}</dd></div>'
            f'<div><dt>Trả lời</dt><dd>{_who(entry)}</dd></div>')
    if cands:
        site = sum(1 for c in cands if c.get("source") != "bot")
        meta += f'<div><dt>Ứng viên</dt><dd>{len(cands)} lệnh MUA mới: {site} của trang, {len(cands) - site} bot ước tính</dd></div>'
    foot = f'<dl class="sheet-foot">{meta}</dl>'
    if entry.get("error"):
        return ui.card(f"Phiên {_e(cp._ddmm(day))}: không có lựa chọn", ui.alert(entry["error"], "err") + foot)
    market = (f'<h3>Thị trường</h3><p>{_e(entry["market_note"])}</p>' if entry.get("market_note") else "")
    if not cands:
        text = entry.get("note") or "Phiên này không có lệnh MUA mới; chỉ xem danh mục."
        return ui.card(f"Phiên {_e(cp._ddmm(day))}", f'<p class="muted">{_e(text)}</p>{market}{foot}')
    pick = entry.get("pick") or {}
    avoid = {a["symbol"]: a for a in entry.get("avoid") or []}
    rank = {s: i for i, s in enumerate(entry.get("ranking") or [], 1)}
    if pick.get("symbol"):
        cand = next(c for c in cands if c["symbol"] == pick["symbol"])
        source = "bot ước tính đổi sang MUA, trang chưa báo" if cand.get("source") == "bot" else "lệnh MUA của trang"
        head = (f'<div class="sheet-head"><p class="small muted">Phiên {_e(cp._ddmm(day))}, {source}, '
                f'độ tin {pick["confidence"]}/100</p>'
                f'<div class="sheet-title"><h2 class="sheet-code" id="pick">{_e(pick["symbol"])}</h2>'
                f'<span class="stamp">Đã chọn</span></div>{_figs(cand)}<dl class="sheet-meta">{meta}</dl></div>')
        why = f'<p class="sheet-why">{_e(pick["reason"])}</p>' + _links(pick.get("sources") or [])
    else:
        head = (f'<div class="sheet-head"><p class="small muted">Phiên {_e(cp._ddmm(day))}</p>'
                '<div class="sheet-title"><h2 class="sheet-code is-none" id="pick">Không chọn mã nào</h2></div>'
                f'<dl class="sheet-meta">{meta}</dl></div>')
        why = f'<p class="sheet-why">{_e(pick.get("reason"))}</p>' if pick.get("reason") else ""
    if avoid:
        why += ('<h3>Nên tránh</h3><ul class="sheet-list">'
                + "".join(f'<li><b>{_e(s)}</b>: {_e(a["reason"])}{_links(a.get("sources") or [])}</li>'
                          for s, a in avoid.items()) + "</ul>")
    rows = []
    for c in sorted(cands, key=lambda c: rank.get(c["symbol"], 99)):
        tag = ('<span class="badge b-ok">chọn</span>' if c["symbol"] == pick.get("symbol")
               else '<span class="badge b-err">tránh</span>' if c["symbol"] in avoid else "")
        source = "bot ước tính" if c.get("source") == "bot" else (
            f"trang, mạnh {c['strength']}%" if c.get("strength") is not None else "trang")
        rows.append(f'<tr><td><span class="rank">{rank.get(c["symbol"], "")}</span><span class="sym">{_e(c["symbol"])}</span> {tag}'
                    f'<div class="muted small">{_e(source)}</div></td>'
                    f'<td data-label="Sau {cp.H10} phiên">{_quick(c.get("r10"))}</td>'
                    f'<td data-label="Theo quy tắc">{_rule(c.get("result"), c.get("source", "site"))}</td>'
                    f'<td class="num" data-label="Giá">{external._fmt(c.get("price"))}</td>'
                    f'<td class="num" data-label="Cắt lỗ">{external._fmt(c.get("sl"))}</td></tr>')
    table = ui.table([("Mã theo thứ hạng", ""), (f"Sau {cp.H10} phiên", ""), ("Theo quy tắc", ""), ("Giá", "num"),
                      ("Cắt lỗ", "num")], rows, "", stack=True)
    return (f'<section class="card sheet" aria-labelledby="pick">{_INK}<div class="sheet-grid">{head}'
            f'<div class="sheet-body">{why}{market}</div></div></section>'
            + '<div class="mt">' + ui.card("Xếp hạng mọi ứng viên", table, sub="Thứ tự của AI, chấm như nhau cho mọi lệnh", flush=True) + "</div>")


def _notes(notes: list[dict]) -> str:
    alerts = [n for n in notes if n.get("kind") == "alert"][-NOTE_ROWS:]
    if not alerts:
        return ""
    rows = []
    for n in reversed(alerts):
        later = (n.get("result") or {}).get("r10")
        body = (_e(n.get("error")) if n.get("error") else
                f'<b>{_e(_say(n.get("view")))}</b>, {_e(n.get("cause"))}<div class="small">{_e(n.get("news"))}</div>'
                f'<div class="small muted">{_e(n.get("reason"))}</div>{_links(n.get("sources") or [])}'
                )
        rows.append(f'<tr><td class="num muted">{_e(cp._ddmm(n["day"]))} {_e(n.get("at", "")[11:16])}</td>'
                    f'<td data-label="Mã"><span class="sym">{_e(n["symbol"])}</span><div class="muted small">{_LEVEL_VI.get(n.get("level"), "")}</div></td>'
                    f'<td data-label="Nhận định">{body}</td>'
                    f'<td data-label="Giá sau 10 phiên">{_value(later) if later is not None else _none()}</td></tr>')
    return ui.card("AI xem cảnh báo", ui.table([("Lúc", "num"), ("Mã", ""), ("Nhận định", ""), ("Giá sau 10 phiên", "")],
                                                  rows, "", stack=True),
                   sub=f"Khi có cảnh báo bán cho mã anh đang giữ, AI tìm tin của mã đó (tối đa {cp.alerts_per_day()} lần/ngày)",
                   flush=True)


def _week(notes: list[dict]) -> str:
    last = next((n for n in reversed(notes) if n.get("kind") == "week"), None)
    if last is None:
        return ""
    facts = "".join(f"<li>{_e(f.lstrip('• '))}</li>" for f in last.get("facts") or [])
    text = (ui.alert(last["error"], "err") if last.get("error") else
            f'<p style="margin-top:12px;white-space:pre-line">{_e(last.get("text"))}</p>')
    who = "AI"
    return ui.card(f"Tổng kết tuần {_e(last['week'])}", f'<ul style="margin:0;padding-left:20px">{facts}</ul>' + text,
                   sub=f"Tới phiên {_e(cp._ddmm(last['day']))}. Dữ kiện do bot tính, nhận xét của {who}.")


def _history(days: list[dict]) -> str:
    rows = []
    for entry in reversed([d for d in days if d.get("candidates") or d.get("error")][-HISTORY_ROWS:]):
        cands = entry.get("candidates") or []
        quick = {c["symbol"]: c.get("r10") for c in cands}
        known = [r["pct"] for r in quick.values() if r and r.get("status") == "closed"]   # only full 10-session results
        pick = (entry.get("pick") or {}).get("symbol") or ""
        pick_cand = next((c for c in cands if c["symbol"] == pick), None)
        if entry.get("error"):
            choice = f'<span class="badge b-err" title="{_e(entry["error"])}">lỗi</span>'
        else:
            choice = f'<span class="sym">{_e(pick)}</span>' if pick else '<span class="muted">không chọn</span>'
        mean = sum(known) / len(known) if known else None
        rows.append(f'<tr><td class="muted">Phiên {_e(cp._ddmm(entry["day"]))}</td><td data-label="AI chọn">{choice}</td>'
                    f'<td data-label="Sau {cp.H10} phiên">{_quick(quick.get(pick)) if pick else _none()}</td>'
                    f'<td class="num {ui.tone(mean)}" data-label="TB các lệnh đủ {cp.H10} phiên">{ui.pct(mean) if mean is not None else _none()}</td>'
                    f'<td data-label="Theo quy tắc">{_rule(pick_cand.get("result"), pick_cand.get("source", "site")) if pick_cand else _none()}</td>'
                    f'<td class="num" data-label="Số lệnh">{len(cands)}</td>'
                    f'<td class="num muted" data-label="Tránh">{len(entry.get("avoid") or [])}</td></tr>')
    return ui.card("Lịch sử", ui.table([("Phiên", ""), ("AI chọn", ""), (f"Sau {cp.H10} phiên", ""),
                                        (f"TB các lệnh đủ {cp.H10} phiên", "num"), ("Theo quy tắc", ""), ("Số lệnh", "num"),
                                        ("Tránh", "num")],
                                       rows, "Chưa có phiên nào.", stack=True),
                   sub="Kết quả tính tới hôm nay, chưa trừ phí và thuế (như nhau cho mọi lệnh)", flush=True)


def page_body(pick: cp.ClaudePick, msg: str = "") -> str:
    days = pick.log.days()
    notes = pick.notes.rows()
    st = pick.stats()                       # the checkpoints pinned on their first sessions
    off = cp.why_off()
    running = pick.running
    button = ("" if off else
              '<form method="post" action="/claude/chay" style="margin:0">'
              f'<button class="btn btn-primary" type="submit"{" disabled" if running else ""}>'
              f'{"AI đang xem…" if running else "Chạy ngay phiên gần nhất"}</button></form>')
    notice = ""
    if off:
        notice = ui.alert(f"Tính năng {off}. Thêm biến môi trường trên Botkeep rồi khởi động lại bot.", "info")
    elif running:
        notice = ui.alert("Đang đọc các lệnh và tin (1-3 phút); trang tự tải lại.", "info")
    elif _LAST["error"]:
        notice = ui.alert(f"Lần chạy gần nhất từ trang này lỗi: {_LAST['error']}", "err")
    kind = "err" if msg[:1] in ("⚠", "❌") else "ok" if msg[:1] in ("✅", "▶") else "info"
    lead = "Mỗi chiều AI chọn một lệnh MUA mới để đo, không phải để đặt lệnh."
    verdict_rows = "".join(f"<li>{_e(_web(line))}</li>" for line in cp.stats_lines(st))
    method = (
        "<p>Mỗi chiều sau khi trang sieutinhieu.vn chốt tín hiệu, AI (Gemini qua 9Router) đọc các lệnh MUA mới (của trang và bot ước tính "
        "trên 300 mã thanh khoản cao) cùng danh mục của anh, tìm tin trên các trang tài chính, chọn một mã, nêu mã nên "
        "tránh và cho ý kiến từng mã đang giữ.</p>"
        '<ul style="margin:12px 0 0;padding-left:20px">'
        f"<li>Mọi lệnh MUA: mua ở giá mở cửa phiên sau, so giá đóng cửa sau {cp.H10} phiên (có kết quả sau 2 tuần).</li>"
        "<li>Lệnh của trang còn được chấm theo quy tắc: bán khi chạm cắt lỗ của trang (từ T+2), sau khi trang báo "
        f"BÁN, hoặc sau {cp.MAX_HOLD} phiên.</li>"
        "<li>Chỉ phiên có từ 2 lệnh trở lên mới tính cho phép so sánh: lệnh AI chọn so với trung bình cùng ngày, "
        "nửa trên bảng xếp hạng so với nửa dưới, lệnh bảo tránh so với phần còn lại.</li>"
        f"<li>Kết luận chỉ đưa ra ở mốc {cp.CHECKPOINTS[0]} (sơ bộ) và {cp.CHECKPOINTS[1]} phiên, mỗi mốc tính đúng trên "
        "chừng ấy phiên đầu tiên: xem hằng ngày không làm may mắn thành kết quả.</li>"
        "<li>Ý kiến về danh mục và về cảnh báo được đo bằng giá 10 phiên sau.</li>"
        "<li>Mã chỉ được chọn trong danh sách ứng viên. Nguồn tin là link AI tự nêu: bot chỉ giữ link tới các trang tin tài "
        "chính đã định (CafeF, Vietstock, VnExpress...), không xem được AI đã tìm gì.</li>"
        "<li>Không chạy lại trên dữ liệu quá khứ: AI đã đọc tin của quá khứ nên kết quả sẽ không trung thực.</li></ul>")
    run = (f'<dl class="sheet-foot run"><div><dt>Mô hình</dt><dd>{_e(cp.model())} qua 9Router</dd></div>'
           "<div><dt>Tìm tin</dt><dd>Gemini tự tìm trên web</dd></div>"
           f"<div><dt>Chạy mỗi phiên</dt><dd>lúc {_e(cp.pick_time())}</dd></div></dl>")
    latest = days[-1] if days else None
    return (
        ui.title(cp.NAME, _e(lead) + _route(), button)
        + (ui.alert(msg, kind) if msg else "") + notice
        + _sheet(latest, "")
        + ('<div class="mt">' + _views(latest) + "</div>" if latest and latest.get("holdings") else "")
        + '<h2 class="sect">Kết quả đo tới nay</h2>'
        + _stats_strip(st) + run
        + '<div class="mt">'
        + ui.acc("ketluan", "", "Kết luận tới nay", f'<ul style="margin:0;padding-left:20px">{verdict_rows}</ul>', opened=True)
        + ui.acc("cachcham", "", "Cách chấm điểm", method)
        + "</div>"
        + "".join(f'<div class="mt">{part}</div>' for part in (_notes(notes), _week(notes)) if part)
        + '<div class="mt">' + _history(days) + "</div>"
    )


def register(app, data_dir, *, guard, post_guard, show_bot=lambda: True) -> None:
    """The /claude routes on ``app``."""
    from fastapi import Depends

    def render(msg: str = "") -> HTMLResponse:
        pick = cp.ClaudePick(data_dir)
        return HTMLResponse(ui.page(cp.NAME, page_body(pick, msg[:600]), active="claude",
                                    refresh=15 if pick.running else None, show_bot=show_bot()))

    @app.get("/claude", dependencies=[Depends(guard)])
    def claude_page(msg: str = "") -> HTMLResponse:
        return render(msg)

    @app.post("/claude/chay", dependencies=post_guard)
    def claude_run():
        pick = cp.ClaudePick(data_dir)
        off = cp.why_off()
        if off:
            return RedirectResponse(f"/claude?msg={quote(f'⚠️ Tính năng {off}.')}", status_code=303)
        if pick.running:
            return RedirectResponse(f"/claude?msg={quote('AI đang chạy, đợi một chút.')}", status_code=303)

        def work():
            try:
                pick.run()
                _LAST["error"] = ""
            except Exception as exc:  # noqa: BLE001 - said on the page (a Retry is not in the log), logged
                logger.exception("AI pick from the web failed")
                _LAST["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"

        threading.Thread(target=work, name="claude-pick-web", daemon=True).start()
        for _ in range(40):                  # the page that follows shows it running (and refreshes)
            if pick.running:
                break
            time.sleep(0.05)
        return RedirectResponse("/claude", status_code=303)   # no message in the URL: the page refreshes itself
