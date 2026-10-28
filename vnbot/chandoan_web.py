"""Two pages over the history store (vnbot.store, vnbot.chandoan):

- ``/tinhieu`` "Vì sao chưa MUA": the newest screen's funnel (the market, the gates, the
  stocks close to a BUY), every stock's decision, the shadow results by group and gate,
  the screens before.
- ``/dulieu`` "Dữ liệu & nhật ký": what the store holds, CSV / JSON downloads of every
  table (as the OKX bot's "Tải dữ liệu"), rebuilding past sessions, the activity log.
"""

from __future__ import annotations

import csv
import html
import io
import json
import logging
import time
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from vnbot import chandoan as cd, store as store_mod, ui

logger = logging.getLogger(__name__)

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
TABLE_NAMES = {"signals": "Quyết định từng mã", "scans": "Lượt quét", "market": "Thị trường",
               "equity": "NAV các sổ", "fills": "Lệnh khớp các sổ", "events": "Nhật ký hoạt động"}
LEVELS = ("", "INFO", "WARNING", "ERROR")
_e = html.escape


def _when(ts) -> str:
    return datetime.fromtimestamp(float(ts), VN_TZ).strftime("%d/%m %H:%M:%S") if ts else ""


def _off() -> str:
    return ui.alert("Kho dữ liệu đang tắt (STORE=0) hoặc không mở được: xem dòng 'Store:' trong log khởi động.", "err")


def _td(content: str, label: str = "", cls: str = "") -> str:
    """A cell; ``label`` names it when a phone shows the row as a block (``ui.table(stack=True)``)."""
    attrs = (f' class="{cls}"' if cls else "") + (f' data-label="{_e(label)}"' if label else "")
    return f"<td{attrs}>{content}</td>"


def _num_cell(value, digits: int = 2, pct: bool = False, label: str = "") -> str:
    if value is None:
        return _td(ui.NONE, label, "num muted")
    text = cd._pct(value, digits=digits - 1 if pct else digits) if pct else cd._num(value, digits)
    tone = ui.tone(value) if pct else ""
    return _td(_e(text), label, f"num {tone}".strip())


def _stats_table(st: dict) -> str:
    h = st["horizon"]
    rows = []
    for name, _ in cd.GROUPS:
        g = st["groups"][name]
        ret, ex, plan = g["ret"], g["excess"], g["plan"]
        plan_txt = (f'{plan["n"]} lệnh, {cd._num(plan["mean"])}R' if plan["n"] else ui.NONE) if name in cd.PLAN_GROUPS else ""
        if name in cd.PLAN_GROUPS and g["nofill"]:
            plan_txt += f' · {g["nofill"]} không khớp'
        verdict = "mốc so sánh" if name in cd.BASELINES else (cd.verdict(ex) if ret["n"] else ui.NONE)
        rows.append(f'<tr><td>{_e(g["label"])}</td>' + _td(str(ret["n"]), "Mẫu", "num")
                    + _num_cell(ret["mean"], 2, pct=True, label=f"TB {h} phiên") + _num_cell(ret["win"], 1, label="Thắng %")
                    + _num_cell(ex["mean"], 2, pct=True, label="So VNINDEX")
                    + _num_cell(ex["t"] if ret["n"] else None, 2, label="t")
                    + (_td(_e(plan_txt), "Theo kế hoạch", "small") if plan_txt else _td("", "", "small"))
                    + _td(_e(verdict), "Kết luận", "small muted") + "</tr>")
    return ui.table([("Nhóm", ""), ("Mẫu", "num"), (f"TB {h} phiên", "num"), ("Thắng %", "num"),
                     ("So VNINDEX", "num"), ("t", "num"), ("Theo kế hoạch", ""), ("Kết luận", "")],
                    rows, "Chưa có kết quả.", stack=True)


def _gates_table(st: dict) -> str:
    rows = []
    for gate, g in sorted(st["gates"].items(), key=lambda x: -x[1]["ret"]["n"]):
        ret, ex = g["ret"], g["excess"]
        rows.append(f'<tr><td>{_e(g["label"])}</td>' + _td(str(ret["n"]), "Mẫu", "num")
                    + _num_cell(ret["mean"], 2, pct=True, label=f"TB {st['horizon']} phiên")
                    + _num_cell(ret["win"], 1, label="Thắng %") + _num_cell(ex["mean"], 2, pct=True, label="So VNINDEX")
                    + _num_cell(ex["t"], 2, label="t") + "</tr>")
    return ui.table([("Cổng chặn đầu tiên", ""), ("Mẫu", "num"), (f"TB {st['horizon']} phiên", "num"),
                     ("Thắng %", "num"), ("So VNINDEX", "num"), ("t", "num")], rows, "Chưa có kết quả.", stack=True)


def _market_card(store) -> str:
    rows = store.market(limit=8)
    if not rows:
        return ui.card("Thị trường chung", '<p class="muted">Chưa có số liệu VNINDEX nào được ghi.</p>')
    head = "<ul>" + "".join(f"<li>{_e(x.strip(' •'))}</li>" for x in cd._market_lines(rows[0], glyph=False)) + "</ul>"
    body = []
    for r in rows:
        dist = r.get("dist_days")
        body.append(
            f'<tr><td class="num">{_e(r["day"])}</td>' + _num_cell(r.get("close"), label="VNINDEX")
            + _td(f'{dist if dist is not None else ui.NONE}/{r.get("dist_threshold") or 4}', "Ngày phân phối / ngưỡng", "num")
            + _td(_e(cd.ALIGN_VI.get(r.get("alignment"), r.get("alignment") or "")), "Xu hướng 3 khung")
            + _td(_e(cd.REGIME_VI.get(r.get("regime"), r.get("regime") or "")), "Regime")
            + _td("trên MA200" if r.get("factor_on") else "dưới MA200", "Phòng thủ")
            + _td("trên MA100" if r.get("momentum_on") else "dưới MA100", "Động lượng") + "</tr>")
    table = ui.table([("Phiên", "num"), ("VNINDEX", "num"), ("Ngày phân phối", "num"), ("Xu hướng 3 khung", ""),
                      ("Regime", ""), ("Phòng thủ", ""), ("Động lượng", "")], body, "", stack=True)
    return ui.card("Thị trường chung", head + table,
                   sub="Regime risk-off chặn mọi lệnh MUA mới của Quant Engine; Phòng thủ chỉ đầu tư khi VNINDEX trên MA200 "
                       "(kiểm tra cuối tuần), Động lượng khi trên MA100")


def _decisions_table(rows: list[dict]) -> str:
    order = {"BUY": 0}
    near_rank = {"market": 0, "confidence": 1, "rr": 2, "resistance": 3}
    rows = sorted(rows, key=lambda r: (order.get(r["action"], 1), near_rank.get(r.get("near") or "", 9),
                                       -(r.get("alt_confidence") or r.get("confidence") or 0)))
    body = []
    for r in rows[:200]:
        near = cd.NEAR_VI.get(r.get("near") or "", "")
        gate = cd._gate_vi(r.get("gate") or "") if r["action"] != "BUY" else "đạt mọi cổng"
        trend = cd.BIAS_VI.get(r.get("bias") or "", r.get("bias") or "") + (" · đang giữ" if r.get("holding") else "")
        body.append(
            f'<tr><td class="sym">{_e(r["symbol"])}</td>' + _td(ui.action_badge(r["action"]), "Hành động")
            + _td(_e(gate), "Cổng chặn đầu tiên", "small") + _td(_e(near) or ui.NONE, "Suýt MUA", "small" if near else "small muted")
            + _num_cell(r.get("confidence"), label="Tin cậy") + _num_cell(r.get("rr"), label="R:R")
            + _td(_e(trend), "Xu hướng", "small muted") + "</tr>")
    return ui.table([("Mã", ""), ("Hành động", ""), ("Cổng chặn đầu tiên", ""), ("Suýt MUA", ""), ("Tin cậy", "num"),
                     ("R:R", "num"), ("Xu hướng", "")], body, "Chưa có quyết định nào.", stack=True)


def _scans_table(scans: list[dict]) -> str:
    body = []
    for s in scans:
        gates = s.get("gates") or {}
        top = ", ".join(f"{cd._gate_vi(g) if g != 'BUY' else 'MUA'} {n}" for g, n in list(gates.items())[:3])
        body.append(f'<tr><td class="num">{_e(s["day"])}</td>' + _td(_e(cd.KIND_VI.get(s["kind"], s["kind"])), "Lượt")
                    + _td(str(s.get("scanned") or 0), "Mã", "num") + _td(str(s.get("buy") or 0), "MUA", "num")
                    + _td(str(s.get("near") or 0), "Suýt MUA", "num")
                    + _td(_e(cd.REGIME_VI.get(s.get("regime"), s.get("regime") or "")), "Regime")
                    + _td(_e(top), "Cổng chặn nhiều nhất", "small muted") + "</tr>")
    return ui.table([("Phiên", "num"), ("Lượt", ""), ("Mã", "num"), ("MUA", "num"), ("Suýt MUA", "num"), ("Regime", ""),
                     ("Cổng chặn nhiều nhất", "")], body, "Chưa có lượt quét nào.", stack=True)


def tinhieu_body(store) -> str:
    title = ui.title("Vì sao chưa MUA", "Mỗi lượt quét, bot ghi quyết định của Quant Engine cho từng mã, cổng nào chặn MUA, "
                     "và theo dõi giá các phiên sau (kết quả bóng) để đo các cổng có đúng không. Không cổng nào được nới tự động.")
    if store is None:
        return title + _off()
    scan = cd.latest_scan(store)
    if scan is None:
        return title + ui.alert("Chưa có lượt quét nào được ghi. Bot ghi từ lượt sàng lọc kế tiếp hoặc lượt bot giao dịch; "
                                 "muốn có ngay lịch sử thì dựng lại ở trang Dữ liệu & nhật ký.", "info") + (
            '<p class="mt"><a class="btn btn-primary" href="/dulieu#dunglai">Dựng lại lịch sử</a></p>')
    st = cd.stats(store.outcome_rows(cd.stats_since()))
    regime = scan.get("regime") or "unknown"
    strip = ui.strip([
        ("Phiên", ui.words(scan["day"]), f"{_e(cd.KIND_VI.get(scan['kind'], scan['kind']))}, {scan.get('scanned') or 0} mã"),
        ("Thị trường", ui.words(cd.REGIME_VI.get(regime, regime)), "chặn MUA mới" if regime in ("risk_off", "unknown") else "cho phép MUA"),
        ("MUA · suýt MUA", f"{scan.get('buy') or 0} · {scan.get('near') or 0}", "lệnh của Quant Engine · mã sát ngưỡng"),
        ("Kết quả bóng", f"{st['resolved']:,}".replace(",", "."), f"mã-phiên đủ {st['horizon']} phiên · {st['pending']} đang chờ"),
    ])
    gates = scan.get("gates") or {}
    gate_rows = [f'<tr><td>{_e(cd._gate_vi(g) if g != "BUY" else "đạt mọi cổng (MUA)")}</td>' + _td(str(n), "Số mã", "num")
                 + "</tr>" for g, n in gates.items()]
    funnel = ui.card("Cổng chặn MUA", ui.table([("Cổng (mỗi mã tính ở cổng đầu tiên nó trượt)", ""), ("Số mã", "num")],
                                               gate_rows, "Không có dữ liệu.", stack=True),
                     sub="Thứ tự kiểm tra: dữ liệu → xu hướng → độ tin cậy → thị trường → phiên rủi ro → stop/mục tiêu → R:R", flush=True)
    near = scan.get("near_list") or []
    near_html = ("<ul>" + "".join(f"<li>{_e(x)}</li>" for x in near) + "</ul>") if near else (
        '<p class="muted">Không có mã nào sát ngưỡng MUA trong lượt này.</p>')
    near_card = ui.card("Suýt MUA", near_html, sub="Chỉ vướng thị trường; hoặc độ tin cậy/R:R sát ngưỡng; hoặc phá đỉnh chưa đo được R:R")
    buys = scan.get("buys") or []
    buy_html = "".join(f'<li><b>{_e(b["symbol"])}</b>: vùng mua tới {_e(cd._price(b.get("entry_high")))}, cắt lỗ '
                       f'{_e(cd._price(b.get("stop")))}, mục tiêu {_e(cd._price(b.get("target")))}</li>' for b in buys)
    parts = [title, strip]
    if buy_html:
        parts.append(ui.card("Lệnh MUA của lượt này", f"<ul>{buy_html}</ul>"))
    parts.append('<div class="grid g2 mt">' + funnel + near_card + "</div>")
    parts.append('<div class="mt">' + _market_card(store) + "</div>")
    lead = (f"Mua giá mở cửa phiên sau, giá {st['horizon']} phiên sau; 'Theo kế hoạch': vùng mua, cắt lỗ, mục tiêu của lệnh như sổ ảo "
            f"(T+2, sau phí), tính bằng R. Một nhóm chỉ nói lên điều gì khi có ≥ {cd.MIN_SAMPLES} mẫu và |t| ≥ {cd._num(cd.MIN_T, 0)}.")
    span = f"từ {st['from']} tới {st['to']}" if st["resolved"] else "chưa có"
    parts.append('<div class="mt">' + ui.card("Kết quả bóng theo nhóm", _stats_table(st), sub=f"{span}. {lead}", flush=True) + "</div>")
    parts.append('<div class="mt">' + ui.card("Kết quả bóng theo cổng chặn", _gates_table(st), flush=True) + "</div>")
    rows = store.signals(day=scan["day"], source=scan["kind"], limit=500)
    parts.append('<div class="mt">' + ui.card(f"Quyết định từng mã phiên {_e(scan['day'])}", _decisions_table(rows),
                                              sub="MUA trước, rồi các mã suýt MUA, rồi theo độ tin cậy", flush=True) + "</div>")
    parts.append('<div class="mt">' + ui.card("Các lượt quét gần đây", _scans_table(store.scans(limit=15)), flush=True) + "</div>")
    return "".join(parts)


def _replay_card(store) -> str:
    status = cd.replay_status(store)
    state = status.get("state")
    if state == "running":
        line = f"Đang dựng lại {status.get('sessions')} phiên cho {status.get('symbols')} mã, bắt đầu {_when(status.get('started'))}."
    elif state == "done":
        line = (f"Lần gần nhất ({_when(status.get('finished'))}): {status.get('days')} phiên {status.get('from')} - {status.get('to')}, "
                f"{status.get('rows')} quyết định, {status.get('resolved')} có kết quả bóng.")
    elif state == "failed":
        line = f"Lần gần nhất lỗi ({_when(status.get('finished'))}): {status.get('error')}"
    else:
        line = "Chưa dựng lại lần nào."
    form = f"""
<form method="post" action="/dulieu/dunglai">
  <div class="row">
    <div class="fld" style="flex:1 1 160px"><label class="f" for="rp-sessions">Số phiên (tối đa {cd.REPLAY_MAX})</label>
      <input id="rp-sessions" name="sessions" inputmode="numeric" value="120" required></div>
    <div class="fld" style="flex:1 1 160px"><label class="f" for="rp-universe">Rổ mã</label>
      <select id="rp-universe" name="universe"><option value="vn30">VN30 + watchlist</option>
      <option value="vn100">VN100 + watchlist (lâu hơn)</option></select></div>
    <button class="btn btn-primary" type="submit"{" disabled" if state == "running" else ""}>Dựng lại lịch sử</button>
  </div>
</form>
<p class="muted small">{_e(line)}</p>
<p class="muted small">Quant Engine quyết định lại từng mã ở từng phiên đã qua (cùng code với quyết định thật, giá đã điều chỉnh
tới hôm nay, rổ mã hôm nay, không có tin tức và dòng tiền), rồi tính kết quả bóng. Ghi với nguồn <code>replay</code>, không ghi đè
quyết định thật. VN30 120 phiên mất khoảng 1-3 phút.</p>"""
    return ui.card("Dựng lại lịch sử", form, id="dunglai", sub="Có ngay số đo của các phiên trước khi bật kho dữ liệu")


def _events_card(store, level: str, query: str, limit: int) -> str:
    rows = store.events(limit=limit, level=level or None, query=query or None)
    opts = "".join(f'<option value="{lv}"{" selected" if lv == level else ""}>{lv or "Mọi mức"}</option>' for lv in LEVELS)
    form = (f'<form method="get" action="/dulieu#nhatky"><div class="row">'
            f'<div class="fld" style="flex:0 1 160px"><label class="f" for="ev-level">Mức</label><select id="ev-level" name="level">{opts}</select></div>'
            f'<div class="fld" style="flex:1 1 220px"><label class="f" for="ev-q">Tìm</label>'
            f'<input id="ev-q" name="q" value="{_e(query)}" maxlength="80" autocomplete="off"></div>'
            f'<div class="fld" style="flex:0 1 120px"><label class="f" for="ev-limit">Số dòng</label>'
            f'<input id="ev-limit" name="limit" inputmode="numeric" value="{limit}"></div>'
            '<button class="btn btn-ghost" type="submit">Lọc</button></div></form>')
    body = [f'<tr><td class="num small">{_e(_when(r["ts"]))}</td>' + _td(_e(r["level"]), "Mức", "small")
            + _td(_e(r["src"]), "Nguồn", "small muted")
            + f'<td class="small" data-label="Nội dung" style="white-space:pre-wrap;word-break:break-word">{_e(r["msg"])}</td></tr>'
            for r in rows]
    table = ui.table([("Lúc", "num"), ("Mức", ""), ("Nguồn", ""), ("Nội dung", "")], body, "Chưa có dòng nào.", stack=True)
    return ui.card("Nhật ký hoạt động", form + table, id="nhatky",
                   sub=f"Log INFO trở lên của bot, đã che khoá và token; giữ {store_mod.events_days()} ngày", flush=False)


def dulieu_body(store, msg: str = "", level: str = "", query: str = "", limit: int = 300) -> str:
    title = ui.title("Dữ liệu & nhật ký", "Kho lịch sử của bot: SQLite trên máy chủ, hoặc PostgreSQL dùng chung khi đặt "
                     "DATABASE_URL. Sổ ảo, Tài sản và cài đặt vẫn nằm trong các file JSON như trước.")
    if store is None:
        return title + ui.alert(msg, "info") + _off()
    counts = store.counts()
    size = store.size_bytes()
    where = (store.url.split("@")[-1].split("?")[0] if store.pg
             else f"{store.data_dir.name}/{store.path.name}" + (" (DATABASE_URL lỗi)" if store.fallback else ""))
    strip = ui.strip([
        ("Kho", ui.words("PostgreSQL" if store.pg else "SQLite"), _e(where)),
        ("Dung lượng", ui.words(f"{size / 1e6:.1f} MB".replace(".", ",") if size is not None else "trên máy chủ PG"),
         f"giữ {store_mod.keep_days()} ngày lịch sử"),
        ("Quyết định đã ghi", f"{counts['signals']:,}".replace(",", "."),
         f"{store.pending_count():,} chưa đủ phiên để chấm xong".replace(",", ".")),
        ("Lượt quét", f"{counts['scans']:,}".replace(",", "."),
         f"{counts['market']} phiên VNINDEX · {counts['events']:,} dòng nhật ký".replace(",", ".")),
    ])
    links = "".join(f'<li><a href="/dulieu/xuat/{k}.csv">{_e(name)}</a> <span class="muted small">({counts.get(k, 0):,} dòng · '
                    f'<a href="/dulieu/xuat/{k}.json">JSON</a>)</span></li>'.replace(",", ".")
                    for k, name in TABLE_NAMES.items())
    downloads = ui.card("Tải dữ liệu", f"<ul>{links}</ul>"
                        '<p><a class="btn btn-ghost" href="/dulieu/xuat/all.json">Sao lưu toàn bộ (JSON)</a></p>'
                        '<p class="muted small">CSV mở được bằng Excel / Google Sheets (UTF-8, đúng tiếng Việt); cột giờ có thêm '
                        'bản dễ đọc theo giờ Việt Nam.</p>')
    pg = ui.card("PostgreSQL dùng chung (tuỳ chọn)",
                 "<p>Đặt <code>DATABASE_URL</code> trên Botkeep (chuỗi <b>Session pooler</b> của Supabase, dạng "
                 "<code>postgresql://postgres.&lt;ref&gt;:&lt;mật khẩu&gt;@aws-0-&lt;vùng&gt;.pooler.supabase.com:5432/postgres</code>) "
                 "rồi khởi động lại: dữ liệu nằm ngoài ổ đĩa máy chủ, không mất khi máy chủ bị xoá. Kiểm tra trước bằng "
                 "<code>python check_db.py</code>. Không mở được thì bot tự dùng SQLite và báo ở đây.</p>"
                 + (f'<p class="muted small">Đang dùng: {_e(store.describe())}</p>'))
    parts = [title, ui.alert(msg, "info"), strip, '<div class="grid g2 mt">' + downloads + _replay_card(store) + "</div>",
             '<div class="mt">' + _events_card(store, level, query, limit) + "</div>", '<div class="mt">' + pg + "</div>"]
    return "".join(parts)


def _csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    cols = list(rows[0].keys()) if rows else []
    for c in ("ts", "resolved_ts"):
        if c in cols:
            cols.insert(cols.index(c) + 1, c + "_time")
    writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        r = dict(r)
        for c in ("ts", "resolved_ts"):
            if c in r:
                r[c + "_time"] = datetime.fromtimestamp(float(r[c]), VN_TZ).strftime("%Y-%m-%d %H:%M:%S") if r[c] else ""
        writer.writerow(r)
    return "﻿" + buf.getvalue()


def register(app, data_dir, *, guard, post_guard, show_bot=lambda: True, watchlist=lambda: [], notify=None) -> None:
    """The /tinhieu and /dulieu routes on ``app``."""
    from fastapi import Depends

    def db():
        return store_mod.current() or store_mod.open_store(data_dir)

    @app.get("/tinhieu", dependencies=[Depends(guard)])
    def tinhieu_page() -> HTMLResponse:
        store = db()
        body = store_mod.safe("page /tinhieu", lambda: tinhieu_body(store), None)
        if body is None:
            body = ui.title("Vì sao chưa MUA") + ui.alert("Không đọc được kho dữ liệu, xem log.", "err")
        return HTMLResponse(ui.page("Vì sao chưa MUA", body, active="tinhieu", show_bot=show_bot()))

    @app.get("/dulieu", dependencies=[Depends(guard)])
    def dulieu_page(msg: str = "", level: str = "", q: str = "", limit: str = "300") -> HTMLResponse:
        store = db()
        level = level if level in LEVELS else ""
        try:
            lines = max(20, min(int(limit), 2000))
        except ValueError:
            lines = 300
        body = store_mod.safe("page /dulieu", lambda: dulieu_body(store, msg[:600], level, q[:80], lines), None)
        if body is None:
            body = ui.title("Dữ liệu & nhật ký") + ui.alert("Không đọc được kho dữ liệu, xem log.", "err")
        running = cd.replay_status(store).get("state") == "running" if store else False
        return HTMLResponse(ui.page("Dữ liệu & nhật ký", body, active="dulieu", show_bot=show_bot(),
                                    refresh=10 if running else None))

    @app.get("/dulieu/xuat/{name}", dependencies=[Depends(guard)])
    def dulieu_export(name: str):
        store = db()
        if store is None:
            return JSONResponse({"error": "kho dữ liệu đang tắt"}, status_code=503)
        table, _, fmt = name.partition(".")
        stamp = time.strftime("%Y%m%d-%H%M")
        headers = {"Cache-Control": "no-store"}
        if table == "all" and fmt == "json":
            data = {k: store.export(k) for k in store_mod.EXPORTS}
            headers["Content-Disposition"] = f'attachment; filename="vnbot-all-{stamp}.json"'
            return Response(json.dumps(data, ensure_ascii=False, default=str), media_type="application/json", headers=headers)
        if table not in store_mod.EXPORTS or fmt not in ("csv", "json"):
            return JSONResponse({"error": "không có bảng này"}, status_code=404)
        rows = store.export(table)
        headers["Content-Disposition"] = f'attachment; filename="{table}-{stamp}.{fmt}"'
        if fmt == "json":
            return Response(json.dumps(rows, ensure_ascii=False, default=str), media_type="application/json", headers=headers)
        return Response(_csv(rows).encode("utf-8"), media_type="text/csv; charset=utf-8", headers=headers)

    @app.post("/dulieu/dunglai", dependencies=post_guard)
    def dulieu_replay(sessions: str = Form("120"), universe: str = Form("vn30")):
        from vnbot.trader import final_day, today_vn

        try:
            n = max(5, min(int(sessions), cd.REPLAY_MAX))
        except ValueError:
            n = 120
        try:
            symbols = cd.replay_symbols("vn100" if universe == "vn100" else "vn30", watchlist())
            msg = cd.start_replay(symbols, n, as_of=final_day(), today=today_vn(), store=db(), notify=notify)
        except Exception as exc:  # noqa: BLE001 - said on the page
            logger.exception("Replay from the web failed")
            msg = f"⚠️ Không dựng lại được: {type(exc).__name__}: {str(exc)[:200]}"
        logger.info("Web: replay %s sessions (%s): %s", n, universe, msg)
        return RedirectResponse(f"/dulieu?msg={quote(msg)}#dunglai", status_code=303)
