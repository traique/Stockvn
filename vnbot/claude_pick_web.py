"""The Claude chọn bóng page (/claude): the latest session (the pick, the avoided, the views
on the holdings), the running comparisons, Claude's notes on the rule alerts, the weekly
review, the history and the month's spending (``vnbot.claude_pick``).

The page only reads the saved files; the button runs the evening answer in the background
(one to three minutes with Claude) and the page refreshes itself while it runs.
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


def _value(pct, *, note: str = "") -> str:
    out = f'<span class="num {ui.tone(pct)}" style="white-space:nowrap">{ui.pct(pct)}</span>'
    return out + (f'<div class="muted small" style="white-space:nowrap">{_e(note)}</div>' if note else "")


def _quick(res: dict | None) -> str:
    """The quick score (the close ``H10`` sessions after the buy)."""
    if not res:
        return '<span class="badge b-none">chờ phiên sau</span>'
    return _value(res["pct"], note="" if res["status"] == "closed" else f"{res['sessions']}/{cp.H10} phiên")


def _rule(res: dict | None, source: str) -> str:
    """The rule's result (the site's cut-loss, its SELL, at most ``MAX_HOLD`` sessions)."""
    if source == "bot":
        return '<span class="muted small">—</span>'
    if not res:
        return '<span class="badge b-none">chờ phiên sau</span>'
    return _value(res["pct"], note="đang giữ" if res["status"] != "closed" else res["why"])


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

    goal = next((c for c in cp.CHECKPOINTS if st["n"] < c), cp.CHECKPOINTS[-1])
    return ui.strip([
        ("Lệnh Claude chọn", value("pick_mean"), f'sau {cp.H10} phiên · thắng {st["pick_win"]:.0f}%' if st["n"]
         else f"giá sau {cp.H10} phiên"),
        ("TB lệnh cùng ngày", value("mean"), "mọi lệnh MUA mới cùng phiên"),
        ("Nửa trên − nửa dưới", value("spread"), "theo bảng xếp hạng của Claude"),
        ("Đã chấm", f'{st["n"]}<span class="muted" style="font-size:.6em"> / {goal}</span>',
         "phiên có lựa chọn · mốc kết luận"),
    ])


def _views(entry: dict) -> str:
    rows = []
    for h in entry.get("holdings") or []:
        badge = ('<span class="badge b-ok">GIỮ</span>' if h.get("view") == "GIỮ" else
                 '<span class="badge b-watch">CÂN NHẮC BÁN</span>' if h.get("view") else '<span class="badge b-none">—</span>')
        rule = '<span class="badge b-err">BÁN</span>' if h["rule"] == "BÁN" else '<span class="badge b-ok">GIỮ</span>'
        later = (h.get("result") or {}).get("r10")
        rows.append(f'<tr><td><span class="sym">{_e(h["symbol"])}</span><div class="muted small">{ui.pct(h.get("pnl_pct"))} '
                    f'theo giá vốn</div></td><td>{rule}<div class="muted small">{_e(h["rule_why"])}</div></td>'
                    f'<td>{badge}<div class="small" style="margin-top:4px">{_e(h.get("reason"))}</div>'
                    f'{_links(h.get("sources") or [])}</td>'
                    f'<td>{_value(later) if later is not None else chr(8212)}</td></tr>')
    if not rows:
        return ""
    return ui.card("Danh mục của anh", ui.table([("Mã", ""), ("Theo quy tắc", ""), ("Claude", ""), ("Giá sau 10 phiên", "")],
                                               rows, ""),
                   sub="Quy tắc cắt lỗ và trang BÁN vẫn là quy tắc chính; ý kiến của Claude được đo bằng giá 10 phiên sau", flush=True,
                   kicker=f"Phiên {_e(cp._ddmm(entry['day']))}")


def _latest(entry: dict | None) -> str:
    if entry is None:
        return ui.card("Phiên gần nhất", '<p class="muted" style="margin:0">Chưa có phiên nào.</p>')
    day = entry["day"]
    cands = entry.get("candidates") or []
    sub = f'Chạy lúc {_e(entry.get("at", "")[11:16])} {_e(cp._ddmm(day))}' + (
        f' · {_e(entry.get("served_by") or entry.get("model"))}' if entry.get("model") else "")
    if entry.get("error"):
        return ui.card(f"Phiên {_e(cp._ddmm(day))}", ui.alert(f"Không có lựa chọn: {entry['error']}", "err"), sub=sub)
    if not cands:
        text = entry.get("note") or "Phiên này không có lệnh MUA mới; Claude chỉ xem danh mục."
        return ui.card(f"Phiên {_e(cp._ddmm(day))}", f'<p class="muted" style="margin:0">{_e(text)}</p>'
                       + (f'<p class="small muted mt" style="margin-bottom:0">Thị trường: {_e(entry["market_note"])}</p>'
                          if entry.get("market_note") else ""), sub=sub)
    pick = entry.get("pick") or {}
    avoid = {a["symbol"]: a for a in entry.get("avoid") or []}
    rank = {s: i for i, s in enumerate(entry.get("ranking") or [], 1)}
    if pick.get("symbol"):
        cand = next(c for c in cands if c["symbol"] == pick["symbol"])
        plan = external.plan_text(cand) if cand.get("source") != "bot" else "bot ước tính đổi sang MUA, trang chưa báo"
        head = (f'<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">'
                f'<span class="sym" style="font-size:20px">{_e(pick["symbol"])}</span><span class="badge b-ok">Claude chọn</span>'
                + ui.chip(f"độ tin {pick['confidence']}/100", "pos") + ui.chip(f"giá {external._fmt(cand['price'])}")
                + "</div>"
                + (f'<div class="small muted" style="margin-top:6px">{_e(plan)}</div>' if plan else "")
                + f'<p style="margin:10px 0 0">{_e(pick["reason"])}</p>' + _links(pick.get("sources") or []))
    else:
        head = '<p style="margin:0"><b>Claude không chọn mã nào.</b> ' + _e(pick.get("reason") or "") + "</p>"
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
        source = "bot ước tính" if c.get("source") == "bot" else (
            f"trang · mạnh {c['strength']}%" if c.get("strength") is not None else "trang")
        rows.append(f'<tr><td class="num muted">{rank.get(c["symbol"], "")}</td>'
                    f'<td><span class="sym">{_e(c["symbol"])}</span> {tag}<div class="muted small">{_e(source)}</div></td>'
                    f'<td>{_quick(c.get("r10"))}</td><td>{_rule(c.get("result"), c.get("source", "site"))}</td>'
                    f'<td class="num">{external._fmt(c.get("price"))}</td><td class="num">{external._fmt(c.get("sl"))}</td></tr>')
    table = ui.table([("Hạng", "num"), ("Mã", ""), (f"Sau {cp.H10} phiên", ""), ("Theo quy tắc", ""), ("Giá", "num"),
                      ("Cắt lỗ", "num")], rows, "")
    site = sum(1 for c in cands if c.get("source") != "bot")
    return ui.card(f"Phiên {_e(cp._ddmm(day))} · {len(cands)} lệnh MUA mới", head + f'<div class="mt">{table}</div>',
                   sub=sub + f" · {site} của trang, {len(cands) - site} bot ước tính", kicker="Lựa chọn gần nhất")


def _notes(notes: list[dict]) -> str:
    alerts = [n for n in notes if n.get("kind") == "alert"][-NOTE_ROWS:]
    if not alerts:
        return ""
    rows = []
    for n in reversed(alerts):
        later = (n.get("result") or {}).get("r10")
        body = (_e(n.get("error")) if n.get("error") else
                f'<b>{_e(n.get("view"))}</b> · {_e(n.get("cause"))}<div class="small">{_e(n.get("news"))}</div>'
                f'<div class="small muted">{_e(n.get("reason"))}</div>{_links(n.get("sources") or [])}')
        rows.append(f'<tr><td class="num muted">{_e(cp._ddmm(n["day"]))} {_e(n.get("at", "")[11:16])}</td>'
                    f'<td><span class="sym">{_e(n["symbol"])}</span><div class="muted small">{_LEVEL_VI.get(n.get("level"), "")}</div></td><td>{body}</td>'
                    f'<td>{_value(later) if later is not None else chr(8212)}</td></tr>')
    return ui.card("Claude xem cảnh báo", ui.table([("Lúc", "num"), ("Mã", ""), ("Nhận định", ""), ("Giá sau 10 phiên", "")],
                                                  rows, ""),
                   sub=f"Khi có cảnh báo bán cho mã anh đang giữ, Claude tìm tin của mã đó (tối đa {cp.alerts_per_day()} lần/ngày)",
                   flush=True)


def _week(notes: list[dict]) -> str:
    last = next((n for n in reversed(notes) if n.get("kind") == "week"), None)
    if last is None:
        return ""
    facts = "".join(f"<li>{_e(f.lstrip('• '))}</li>" for f in last.get("facts") or [])
    text = (ui.alert(last["error"], "err") if last.get("error") else
            f'<p style="margin:12px 0 0;white-space:pre-line">{_e(last.get("text"))}</p>')
    return ui.card(f"Tổng kết tuần {_e(last['week'])}", f'<ul style="margin:0;padding-left:20px">{facts}</ul>' + text,
                   sub=f"Tới phiên {_e(cp._ddmm(last['day']))} · dữ kiện do bot tính, nhận xét của Claude")


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
        rows.append(f'<tr><td class="num muted">{_e(cp._ddmm(entry["day"]))}</td><td>{choice}</td>'
                    f'<td>{_quick(quick.get(pick)) if pick else "—"}</td>'
                    f'<td class="num {ui.tone(mean)}">{ui.pct(mean)}</td>'
                    f'<td>{_rule(pick_cand.get("result"), pick_cand.get("source", "site")) if pick_cand else "—"}</td>'
                    f'<td class="num">{len(cands)}</td><td class="num muted">{len(entry.get("avoid") or [])}</td></tr>')
    return ui.card("Lịch sử", ui.table([("Phiên", "num"), ("Claude chọn", ""), (f"Sau {cp.H10} phiên", ""),
                                        (f"TB các lệnh đủ {cp.H10} phiên", "num"), ("Theo quy tắc", ""), ("Số lệnh", "num"),
                                        ("Tránh", "num")],
                                       rows, "Chưa có phiên nào."),
                   sub="Kết quả tính tới hôm nay · chưa trừ phí và thuế (như nhau cho mọi lệnh)", flush=True)


def page_body(pick: cp.ClaudePick, msg: str = "") -> str:
    days = pick.log.days()
    notes = pick.notes.rows()
    st = pick.stats()                       # the checkpoints pinned on their first sessions
    off = cp.why_off()
    running = pick.running
    button = ("" if off else
              '<form method="post" action="/claude/chay" style="margin:0">'
              f'<button class="btn btn-primary" type="submit"{" disabled" if running else ""}>'
              f'{"Claude đang xem…" if running else "Chạy ngay phiên gần nhất"}</button></form>')
    notice = ""
    if off:
        notice = ui.alert(f"Tính năng {off}. Thêm biến môi trường trên Botkeep rồi khởi động lại bot.", "info")
    elif running:
        notice = ui.alert("Claude đang đọc tin (1-3 phút); trang tự tải lại.", "info")
    elif _LAST["error"]:
        notice = ui.alert(f"Lần chạy gần nhất từ trang này lỗi: {_LAST['error']}", "err")
    kind = "err" if msg[:1] in ("⚠", "❌") else "ok" if msg[:1] in ("✅", "▶") else "info"
    lead = ("Mỗi chiều sau khi trang sieutinhieu.vn chốt tín hiệu, Claude đọc các lệnh MUA mới (của trang và bot ước "
            "tính trên 300 mã thanh khoản cao) cùng danh mục của anh, tìm tin trên các trang tài chính, chọn một mã, nêu "
            "mã nên tránh và cho ý kiến từng mã đang giữ. Đây là chọn bóng để đo, không phải lệnh: bot chấm mọi mã theo "
            f"cùng một cách và chỉ kết luận ở các mốc {cp.CHECKPOINTS[0]} và {cp.CHECKPOINTS[1]} phiên có lựa chọn.")
    spent, cap = pick.month_cost(), cp.monthly_cap()
    verdict_rows = "".join(f"<li>{_e(line.lstrip('• '))}</li>" for line in cp.stats_lines(st))
    method = (
        "<ul style=\"margin:0;padding-left:20px\">"
        f"<li>Mọi lệnh MUA: mua ở giá mở cửa phiên sau, so giá đóng cửa sau {cp.H10} phiên (có kết quả sau 2 tuần).</li>"
        "<li>Lệnh của trang còn được chấm theo quy tắc: bán khi chạm cắt lỗ của trang (từ T+2), sau khi trang báo "
        f"BÁN, hoặc sau {cp.MAX_HOLD} phiên.</li>"
        "<li>Chỉ phiên có từ 2 lệnh trở lên mới tính cho phép so sánh: lệnh Claude chọn so với trung bình cùng ngày, "
        "nửa trên bảng xếp hạng so với nửa dưới, lệnh bảo tránh so với phần còn lại.</li>"
        f"<li>Kết luận chỉ đưa ra ở mốc {cp.CHECKPOINTS[0]} (sơ bộ) và {cp.CHECKPOINTS[1]} phiên, mỗi mốc tính đúng trên "
        "chừng ấy phiên đầu tiên: xem hằng ngày không làm may mắn thành kết quả.</li>"
        "<li>Ý kiến về danh mục và về cảnh báo được đo bằng giá 10 phiên sau.</li>"
        "<li>Nguồn tin chỉ được giữ khi đúng là trang Claude tìm thấy; mã chỉ được chọn trong danh sách ứng viên.</li>"
        "<li>Không chạy lại trên dữ liệu quá khứ: Claude đã đọc tin của quá khứ nên kết quả sẽ không trung thực.</li></ul>"
        f'<p class="small muted mt" style="margin-bottom:0">Mô hình {_e(cp.model())} · mức suy nghĩ {_e(cp.effort())} · '
        f"tối đa {cp.max_searches()} lượt tìm mỗi chiều · chi phí tháng này {cp._usd(spent)}/{cp._usd(cap)} USD"
        f" · chạy mỗi phiên lúc {_e(cp.pick_time())}</p>")
    latest = days[-1] if days else None
    return (
        ui.title("Claude chọn bóng", _e(lead), button)
        + (ui.alert(msg, kind) if msg else "") + notice
        + _stats_strip(st)
        + '<div class="grid g2 mt">'
        + ui.card("Kết luận tới nay", f'<ul style="margin:0;padding-left:20px">{verdict_rows}</ul>',
                  sub="Trên các phiên mà mọi lệnh đã đủ 10 phiên")
        + ui.card("Cách chấm điểm", method, sub="Như nhau cho mọi lệnh")
        + "</div>"
        + '<div class="mt">' + _latest(latest) + "</div>"
        + ('<div class="mt">' + _views(latest) + "</div>" if latest and latest.get("holdings") else "")
        + "".join(f'<div class="mt">{part}</div>' for part in (_notes(notes), _week(notes)) if part)
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
            return RedirectResponse(f"/claude?msg={quote('Claude đang chạy, đợi một chút.')}", status_code=303)

        def work():
            try:
                pick.run()
                _LAST["error"] = ""
            except Exception as exc:  # noqa: BLE001 - said on the page (a Retry is not in the log), logged
                logger.exception("Claude pick from the web failed")
                _LAST["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"

        threading.Thread(target=work, name="claude-pick-web", daemon=True).start()
        for _ in range(40):                  # the page that follows shows it running (and refreshes)
            if pick.running:
                break
            time.sleep(0.05)
        return RedirectResponse("/claude", status_code=303)   # no message in the URL: the page refreshes itself
