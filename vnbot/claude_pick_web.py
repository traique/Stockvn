"""The Claude chọn bóng page (/claude): the latest session's choice, the running comparison
with the site's other BUYs, the history and the month's spending (``vnbot.claude_pick``).

The page only reads the saved log; the button runs the choice in the background (one to
three minutes with Claude) and the page refreshes itself while it runs.
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


def _e(text) -> str:
    return html.escape(str(text or ""))


def _result(res: dict | None) -> str:
    if not res:
        return '<span class="badge b-none">chờ phiên sau</span>'
    value = f'<span class="num {ui.tone(res["pct"])}" style="white-space:nowrap">{ui.pct(res["pct"])}</span>'
    why = "đang giữ" if res["status"] != "closed" else res["why"]
    return value + f'<div class="muted small" style="white-space:nowrap">{_e(why)}</div>'


def _links(urls: list[str]) -> str:
    urls = [u for u in urls if str(u).startswith(("https://", "http://"))]   # never a javascript: link
    if not urls:
        return ""
    items = " · ".join(f'<a href="{_e(u)}" rel="noopener noreferrer" target="_blank">{_e(_host(u))}</a>' for u in urls)
    return f'<div class="small muted" style="margin-top:6px">Nguồn: {items}</div>'


def _host(url: str) -> str:
    return cp._norm_url(url).split("/", 1)[0]


def _stats_strip(st: dict) -> str:
    def value(key):
        v = st.get(key)
        return f'<span class="{ui.tone(v)}">{ui.pct(v)}</span>' if v is not None else "—"

    return ui.strip([
        ("Lệnh Claude chọn", value("pick_mean"), f'thắng {st["pick_win"]:.0f}% · TB mỗi lệnh' if st["n"] else "chưa có kết quả"),
        ("TB lệnh cùng ngày", value("mean"), "mọi lệnh MUA mới của trang"),
        ("Lệnh mạnh nhất", value("strongest"), "độ mạnh cao nhất của trang cùng ngày"),
        ("Đã chấm", f'{st["n"]}<span class="muted" style="font-size:.6em"> / {cp.MIN_SCORED}</span>',
         ui.meter(min(st["n"], cp.MIN_SCORED), cp.MIN_SCORED) + " phiên Claude có chọn"),
    ])


def _latest(entry: dict | None) -> str:
    if entry is None:
        return ui.card("Phiên gần nhất", '<p class="muted" style="margin:0">Chưa có phiên nào.</p>')
    day = entry["day"]
    cands = entry.get("candidates") or []
    sub = f'Chọn lúc {_e(entry.get("at", "")[11:16])} {_e(cp._ddmm(day))}' + (
        f' · {_e(entry.get("served_by") or entry.get("model"))}' if entry.get("model") else "")
    if entry.get("error"):
        return ui.card(f"Phiên {_e(cp._ddmm(day))}", ui.alert(f"Không có lựa chọn: {entry['error']}", "err"), sub=sub)
    if not cands:
        return ui.card(f"Phiên {_e(cp._ddmm(day))}", f'<p class="muted" style="margin:0">{_e(entry.get("note"))}</p>', sub=sub)
    pick = entry.get("pick") or {}
    avoid = {a["symbol"]: a for a in entry.get("avoid") or []}
    rank = {s: i for i, s in enumerate(entry.get("ranking") or [], 1)}
    if pick.get("symbol"):
        cand = next(c for c in cands if c["symbol"] == pick["symbol"])
        plan = external.plan_text(cand)
        head = (f'<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">'
                f'<span class="sym" style="font-size:22px">⭐ {_e(pick["symbol"])}</span>'
                + ui.chip(f"độ tin {pick['confidence']}/100", "pos") + ui.chip(f"tín hiệu {external._fmt(cand['price'])}")
                + "</div>"
                + (f'<div class="small muted" style="margin-top:6px">{_e(plan)}</div>' if plan else "")
                + f'<p style="margin:10px 0 0">{_e(pick["reason"])}</p>' + _links(pick.get("sources") or []))
    else:
        head = ('<p style="margin:0"><b>Claude không chọn mã nào.</b> '
                + _e(pick.get("reason") or "") + "</p>")
    if avoid:
        head += ('<div class="mt"><b>Nên tránh</b><ul style="margin:6px 0 0;padding-left:20px">'
                 + "".join(f'<li><b>{_e(s)}</b>: {_e(a["reason"])}{_links(a.get("sources") or [])}</li>'
                           for s, a in avoid.items()) + "</ul></div>")
    if entry.get("market_note"):
        head += f'<p class="small muted mt" style="margin-bottom:0">Thị trường: {_e(entry["market_note"])}</p>'
    rows = []
    for c in sorted(cands, key=lambda c: rank.get(c["symbol"], 99)):
        tag = ('<span class="badge b-ok">chọn</span>' if c["symbol"] == pick.get("symbol")
               else '<span class="badge b-err">tránh</span>' if c["symbol"] in avoid else "")
        rows.append(f'<tr><td class="num muted">{rank.get(c["symbol"], "")}</td>'
                    f'<td><span class="sym">{_e(c["symbol"])}</span> {tag}</td><td>{_result(c.get("result"))}</td>'
                    f'<td class="num">{external._fmt(c.get("price"))}</td>'
                    f'<td class="num">{_e(c.get("strength") if c.get("strength") is not None else "—")}%</td>'
                    f'<td class="num">{external._fmt(c.get("sl"))}</td></tr>')
    table = ui.table([("Hạng", "num"), ("Mã", ""), ("Kết quả tới nay", ""), ("Giá tín hiệu", "num"), ("Độ mạnh", "num"),
                      ("Cắt lỗ", "num")], rows, "")
    return ui.card(f"Phiên {_e(cp._ddmm(day))} · {len(cands)} lệnh MUA mới", head + f'<div class="mt">{table}</div>',
                   sub=sub, kicker="Lựa chọn gần nhất")


def _history(days: list[dict]) -> str:
    rows = []
    for entry in reversed([d for d in days if d.get("candidates") or d.get("error")][-HISTORY_ROWS:]):
        cands = entry.get("candidates") or []
        by = {c["symbol"]: c.get("result") for c in cands}
        known = [r["pct"] for r in by.values() if r]
        pick = (entry.get("pick") or {}).get("symbol") or ""
        strongest = by.get(cands[0]["symbol"]) if cands else None
        if entry.get("error"):
            choice = f'<span class="badge b-err" title="{_e(entry["error"])}">lỗi</span>'
        else:
            choice = f'<span class="sym">{_e(pick)}</span>' if pick else '<span class="muted">không chọn</span>'
        mean = sum(known) / len(known) if known else None
        rows.append(f'<tr><td class="num muted">{_e(cp._ddmm(entry["day"]))}</td><td>{choice}</td>'
                    f'<td>{_result(by.get(pick)) if pick else "—"}</td>'
                    f'<td class="num {ui.tone(mean)}">{ui.pct(mean)}</td><td>{_result(strongest) if cands else "—"}</td>'
                    f'<td class="num">{len(cands)}</td><td class="num muted">{len(entry.get("avoid") or [])}</td></tr>')
    return ui.card("Lịch sử", ui.table([("Phiên", "num"), ("Claude chọn", ""), ("Kết quả", ""), ("TB các lệnh", "num"),
                                        ("Lệnh mạnh nhất", ""), ("Số lệnh", "num"), ("Tránh", "num")], rows,
                                       "Chưa có phiên nào."),
                   sub="Kết quả tính tới hôm nay · chưa trừ phí và thuế (như nhau cho mọi lệnh)", flush=True)


def page_body(pick: cp.ClaudePick, msg: str = "") -> str:
    days = pick.log.days()
    st = cp.stats(days)
    off = cp.why_off()
    running = pick.running
    button = ("" if off else
              '<form method="post" action="/claude/chay" style="margin:0">'
              f'<button class="btn btn-primary" type="submit"{" disabled" if running else ""}>'
              f'{"Claude đang chọn…" if running else "▶ Chọn ngay phiên gần nhất"}</button></form>')
    notice = ""
    if off:
        notice = ui.alert(f"Tính năng {off}. Thêm biến môi trường trên Botkeep rồi khởi động lại bot.", "info")
    elif running:
        notice = ui.alert("Claude đang đọc tin và chọn (1-3 phút); trang tự tải lại.", "info")
    kind = "err" if msg[:1] in ("⚠", "❌") else "ok" if msg[:1] in ("✅", "▶") else "info"
    lead = ("Mỗi chiều sau khi trang sieutinhieu.vn chốt tín hiệu, Claude đọc các lệnh MUA mới trong danh sách theo "
            "dõi, tìm tin trên các trang tài chính, chọn một mã và nêu mã nên tránh. Bot chấm MỌI mã theo cùng quy tắc "
            "⛔/🔴 rồi so sánh. Đây là chọn bóng để đo, không phải lệnh: chỉ tin khi đủ "
            f"{cp.MIN_SCORED} phiên và kết quả rõ ràng.")
    month = pick.now().strftime("%Y-%m")
    spent, cap = pick.log.month_cost(month), cp.monthly_cap()
    verdict_rows = "".join(f"<li>{_e(line.lstrip('• '))}</li>" for line in cp.stats_lines(st))
    method = (
        "<ul style=\"margin:0;padding-left:20px\">"
        "<li>Mua ở giá mở cửa phiên sau phiên có tín hiệu.</li>"
        "<li>Bán ở giá mở cửa sau khi trang báo BÁN (đợi đủ T+2), khi giá chạm mức cắt lỗ của trang (từ T+2), "
        f"hoặc sau {cp.MAX_HOLD} phiên.</li>"
        "<li>Lệnh Claude chọn được so với trung bình mọi lệnh MUA cùng ngày và với lệnh có độ mạnh cao nhất của trang.</li>"
        "<li>Nguồn tin chỉ được giữ khi đúng là trang Claude tìm thấy; mã chỉ được chọn trong danh sách ứng viên.</li>"
        "<li>Không chạy lại trên dữ liệu quá khứ: Claude đã đọc tin của quá khứ nên kết quả sẽ không trung thực.</li></ul>"
        f'<p class="small muted mt" style="margin-bottom:0">Mô hình {_e(cp.model())} · mức suy nghĩ {_e(cp.effort())} · '
        f"tối đa {cp.max_searches()} lượt tìm mỗi phiên · chi phí tháng này {cp._usd(spent)}/{cp._usd(cap)} USD"
        f" · chạy mỗi phiên lúc {_e(cp.pick_time())}</p>")
    return (
        ui.hero("Claude chọn bóng", _e(lead), button, eyebrow="AI · đo trước khi tin")
        + (ui.alert(msg, kind) if msg else "") + notice
        + _stats_strip(st)
        + '<div class="grid g2 mt">'
        + ui.card("Kết luận tới nay", f'<ul style="margin:0;padding-left:20px">{verdict_rows}</ul>',
                  sub="So sánh trên các phiên mà mọi lệnh đã có kết quả")
        + ui.card("Cách chấm điểm", method, sub="Như nhau cho mọi lệnh")
        + "</div>"
        + '<div class="mt">' + _latest(days[-1] if days else None) + "</div>"
        + '<div class="mt">' + _history(days) + "</div>"
    )


def register(app, data_dir, *, guard, post_guard, show_bot=lambda: True) -> None:
    """The /claude routes on ``app``."""
    from fastapi import Depends

    def render(msg: str = "") -> HTMLResponse:
        pick = cp.ClaudePick(data_dir)
        return HTMLResponse(ui.page("Claude chọn bóng", page_body(pick, msg[:600]), active="claude",
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
            return RedirectResponse(f"/claude?msg={quote('Claude đang chọn, đợi một chút.')}", status_code=303)

        def work():
            try:
                pick.run()
            except Exception:  # noqa: BLE001 - shown on the page as the session's error, or logged
                logger.exception("Claude pick from the web failed")

        threading.Thread(target=work, name="claude-pick-web", daemon=True).start()
        for _ in range(40):                  # the page that follows shows it running (and refreshes)
            if pick.running:
                break
            time.sleep(0.05)
        return RedirectResponse(f"/claude?msg={quote('▶ Đã gửi cho Claude; trang tự tải lại khi xong.')}", status_code=303)
