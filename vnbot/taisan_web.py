"""The Tài sản tab (/taisan): the owner's real-money portfolio on the web.

The same journal as the /ts commands (``vnbot.taisan``): the headline figures, the
allocation alerts, the holdings with their levels and the site's signal, the asset chart
against VNINDEX, the risk figures, the rights, how the sell alerts turned out, the forms
that record, the journal with undo, and the Excel import (preview, then confirm) and
export. The page reads the saved closes and state; only the live prices of the holdings
during the session are fetched (cached a minute).
"""

from __future__ import annotations

import html
import logging
from urllib.parse import quote

from fastapi import File, Form, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from vnbot import taisan as ts
from vnbot import ui

logger = logging.getLogger(__name__)

IMPORT_PATH = "/taisan/nhap"
MAX_UPLOAD_BYTES = 1024 * 1024          # the import route's request body (the other routes: 64 KB)
JOURNAL_ROWS = 60
_LEVEL_BADGE = {"danger": ' <span class="badge b-err">nguy hiểm</span>',
                "watch": ' <span class="badge b-watch">theo dõi</span>', "ok": ""}
_RISK_VI = {"high": "cao", "moderate": "vừa", "low": "thấp"}
_SOURCE_VI = {"telegram": "Telegram", "zoom": "Zoom", "web": "Web", "import": "Excel"}


def e(text) -> str:
    return html.escape(str(text if text is not None else ""))


def _signed(value) -> str:
    return "—" if value is None else ts.signed(value)


def _p1(value) -> str:
    """12.34 -> "12,3%"."""
    return "—" if value is None else f"{value:.1f}%".replace(".", ",")


def _p0(value) -> str:
    return "—" if value is None else f"{value:.0f}%"


def _number(text: str, what: str, *, allow_negative: bool = False) -> float:
    """A number typed the Vietnamese way (56.300 or 56300); LedgerError otherwise."""
    from vnbot.telegram_bot import _amount

    raw = (text or "").strip()
    sign = 1.0
    if allow_negative and raw[:1] in ("-", "+") and len(raw) > 1:
        sign, raw = (-1.0 if raw[0] == "-" else 1.0), raw[1:].strip()
    try:
        return sign * _amount(raw)
    except ValueError:
        raise ts.LedgerError(f"{what} không hợp lệ: {text!r}. Nhập số đầy đủ, ví dụ 56300 hoặc 56.300.") from None


def _optional(text: str, what: str) -> float | None:
    return _number(text, what) if (text or "").strip() else None


def _code(text: str) -> str:
    from vnbot.service import check_request

    return check_request(text)[0]


def _back(msg: str, anchor: str = "") -> RedirectResponse:
    return RedirectResponse(f"/taisan?msg={quote(msg)}" + (f"#{anchor}" if anchor else ""), status_code=303)


def _alert(msg: str) -> str:
    if not msg:
        return ""
    kind = "err" if msg[:1] in ("⚠", "❌") else "ok" if msg[:1] in ("✅", "↩", "📥", "🗑") else "info"
    return ui.alert(msg, kind)


# ─── page pieces ─────────────────────────────────────────────────────────────

def _day_change(summary: dict, hist: list[dict], label: str) -> tuple[str, float | None, float | None]:
    """(title, change in VND, change in %) of the last session, deposits and withdrawals
    left out: against the last saved close while the session's prices are live."""
    if not hist:
        return "Phiên gần nhất", None, None
    last = hist[-1]
    live = label and label != "giá đóng cửa gần nhất"
    if live and last["day"] < ts.today_vn():
        base_total, base_capital, title = last["total"], last["capital"], "Hôm nay"
        now_total, now_capital = summary["total"], summary["capital"]
    elif len(hist) >= 2:
        prev = hist[-2]
        base_total, base_capital, title = prev["total"], prev["capital"], f"Phiên {ts.ddmm(last['day'])}"
        now_total, now_capital = last["total"], last["capital"]
    else:
        return "Phiên gần nhất", None, None
    change = (now_total - base_total) - (now_capital - base_capital)
    return title, change, (change / base_total * 100 if base_total > 0 else None)


def _vnindex(closes: dict) -> tuple[str, str]:
    series = closes.get("VNINDEX") or {}
    days = sorted(series)
    if not days:
        return "—", "chưa có giá"
    last = series[days[-1]]
    if len(days) < 2:
        return ui.money(last), ts.ddmmyyyy(days[-1])
    change = (last / series[days[-2]] - 1) * 100
    value = f"{last:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return value, f'<span class="{ui.tone(change)}">{ui.pct(change)}</span> · {ts.ddmm(days[-1])}'


def _overview(account: ts.TaiSan, s: dict, book: ts.Book, hist: list[dict], state: dict) -> str:
    title, change, change_pct = _day_change(s, hist, s["label"])
    vni_value, vni_sub = _vnindex(account.closes())
    strip = ui.strip([
        ("Tổng tài sản", ui.money(s["total"]),
         f'<span class="{ui.tone(s["pnl"])}">{_signed(s["pnl"])} ({ui.pct(s["pnl_pct"])})</span> so với vốn gốc'),
        (title, f'<span class="{ui.tone(change)}">{_signed(change)}</span>',
         ui.pct(change_pct) + " · không tính nạp/rút" if change is not None else "chưa có hai phiên giá đóng cửa"),
        ("Vốn gốc", ui.money(s["capital"]), "tiền nạp trừ tiền rút"),
        ("VNINDEX", vni_value, vni_sub),
    ])
    rights = account.pending_rights()
    analytics = state.get("analytics") or {}
    sells = book.sells
    kpis = "".join([
        ui.kpi("Tiền mặt", ui.money(s["cash"]), "âm: kiểm tra lại sổ" if s["cash"] < 0 else "sức mua hiện có",
               "neg" if s["cash"] < 0 else ""),
        ui.kpi("Giá trị cổ phiếu", ui.money(s["market"]), f'{len(s["positions"])} mã · {e(s["label"] or "giá gần nhất")}'),
        ui.kpi("Lãi/lỗ đã chốt", _signed(s["realized"]),
               f"{sells} lệnh bán · thắng {book.wins}/{sells}" if sells else "chưa bán lệnh nào", ui.tone(s["realized"])),
        ui.kpi("Lãi/lỗ đang mở", _signed(s["unrealized"]), "theo giá vốn (đã gồm phí mua)", ui.tone(s["unrealized"])),
        ui.kpi("Cổ tức đã nhận", ui.money(s["dividends"]), "tiền mặt, đã về tài khoản"),
        ui.kpi("Quyền chờ về", ui.money(s["pending"]),
               f"{len(rights)} quyền đã qua ngày GDKHQ" if rights else "không có quyền đang chờ"),
        ui.kpi("Biến động năm", f'{analytics["vol_pct"]:.1f}%'.replace(".", ",") if analytics.get("vol_pct") is not None else "—",
               "ước tính từ 120 phiên" if analytics else "tính sau báo cáo 15:30"),
        ui.kpi("Đa dạng hoá", f'{analytics["diversification"]}/100' if analytics.get("diversification") is not None else "—",
               "100 = tỷ trọng chia đều" if analytics else "tính sau báo cáo 15:30"),
    ])
    warnings = "".join(ui.alert(w, "err") for w in book.warnings)
    return strip + f'<div class="grid g4 mt">{kpis}</div>' + warnings


def _actions(account: ts.TaiSan, s: dict, site: dict) -> str:
    from tradingagents.market import vn_rules

    day = account.report_day()
    prices = {p["symbol"]: p["price"] for p in s["positions"] if p["price"]}
    todo = account.actions(day, s, prices, site)
    nxt = vn_rules.add_sessions(day, 1)
    items = "".join(f"<li>{e(line)}</li>" for _, line in todo)
    body = (f'<ul class="feed">{items}</ul>' if items
            else '<p class="muted" style="margin:0">Không có việc cần làm: giữ nguyên danh mục.</p>')
    return ui.card(f"Việc cần làm phiên {ts.ddmm(nxt)}", body,
                   sub="⛔ thủng cắt lỗ · 🔴 Siêu tín hiệu BÁN · 🎯 chạm mục tiêu · ⚠️ tỷ trọng quá ngưỡng", cls="accent-gold")


def _bars(rows: list[dict], key: str) -> str:
    items = []
    for r in rows:
        width = min(100.0, max(2.0, r["pct"]))
        items.append(f'<div class="{r["level"]}"><div class="h"><b>{e(r[key])}</b>'
                     f'<span class="num">{_p1(r["pct"])}{_LEVEL_BADGE[r["level"]]}</span></div>'
                     f'<div class="track"><span style="width:{width:.1f}%"></span></div></div>')
    return '<div class="bars">' + "".join(items) + "</div>"


def _allocation(account: ts.TaiSan, s: dict) -> str:
    alloc = account.allocation(s)
    if not alloc["symbols"]:
        return ui.card("Tỷ trọng", '<p class="muted" style="margin:0">Chưa nắm giữ cổ phiếu nào.</p>')
    hot = [a for a in alloc["symbols"] if a["level"] != "ok"]
    listed = ", ".join(f'{a["symbol"]} {_p1(a["pct"])}' for a in hot)
    head = (ui.alert("Vượt ngưỡng: " + listed, "err") if any(a["level"] == "danger" for a in hot)
            else ui.alert("Cần theo dõi: " + listed, "info") if hot else "")
    warn, danger = alloc["warn"], alloc["danger"]
    form = f"""
<details style="margin-top:14px"><summary class="muted small">Đổi ngưỡng cảnh báo tỷ trọng</summary>
<form method="post" action="/taisan/nguong" style="margin-top:10px"><div class="row">
  <div class="fld"><label class="f" for="ts-warn">Theo dõi từ (%)</label><input id="ts-warn" name="warn" inputmode="decimal" value="{warn:g}"></div>
  <div class="fld"><label class="f" for="ts-danger">Nguy hiểm từ (%)</label><input id="ts-danger" name="danger" inputmode="decimal" value="{danger:g}"></div>
  <button class="btn btn-ghost" type="submit">Lưu ngưỡng</button></div></form></details>"""
    sectors = ('<h3>Theo ngành</h3>' + _bars(alloc["sectors"], "sector")) if alloc["sectors"] else ""
    return ui.card("Tỷ trọng và cảnh báo phân bổ", head + _bars(alloc["symbols"], "symbol") + sectors + form,
                   sub=f"Một mã: theo dõi từ {warn:g}%, nguy hiểm từ {danger:g}% tổng tài sản; một ngành: gấp 1,5 lần")


def _holdings(account: ts.TaiSan, s: dict, book: ts.Book, site: dict) -> str:
    today = ts.today_vn()
    levels = account.levels()
    rows = []
    for p in s["positions"]:
        h = book.holdings[p["symbol"]]
        lv = levels.get(p["symbol"]) or {}
        stop, target = float(lv.get("stop") or 0), lv.get("target")
        mine = lv.get("source") == "owner"
        price = p["price"]
        stop_cls = "neg" if stop and price and price <= stop else ""
        target_cls = "pos" if target and price and price >= float(target) else ""
        t2 = "".join(f'<div><span class="badge b-none" title="Bán được từ chiều {ts.ddmmyyyy(day)}">'
                     f"{ui.money(qty)} cp về {ts.ddmm(day)}</span></div>" for day, qty in h.pending_lots(today))
        sig = site.get(p["symbol"])
        sig_html = (f'{ui.action_badge("BUY" if sig.get("side") == "BUY" else "SELL")} '
                    f'<span class="muted small">{ts.ddmm(sig.get("day", ""))}</span>') if sig else '<span class="muted">—</span>'

        def level(value, cls):
            if not value:
                return '<td class="num muted">—</td>'
            text = ui.money(value)
            return f'<td class="num {cls}" title="{"anh đặt" if mine else "theo Siêu tín hiệu"}">{f"<b>{text}</b>" if mine else text}</td>'
        rows.append(
            f'<tr><td class="sym">{e(p["symbol"])}</td><td class="num">{ui.money(p["qty"])}{t2}</td>'
            f'<td class="num">{ui.money(p["avg"])}</td><td class="num">{ui.money(price)}</td>'
            f'<td class="num">{ui.money(p["value"])}</td>'
            f'<td class="num {ui.tone(p["pnl"])}">{_signed(p["pnl"])}<div class="small">{ui.pct(p["pnl_pct"])}</div></td>'
            f'<td class="num">{_p1(p["weight"])}</td>'
            + level(stop, stop_cls) + level(target, target_cls)
            + f'<td>{sig_html}</td><td class="num muted small">{ts.ddmmyyyy(p["first_day"])}</td></tr>')
    table = ui.table([("Mã", ""), ("KL", "num"), ("Giá vốn BQ", "num"), ("Thị giá", "num"), ("Giá trị", "num"),
                      ("Lãi/lỗ", "num"), ("Tỷ trọng", "num"), ("Cắt lỗ", "num"), ("Mục tiêu", "num"),
                      ("Siêu tín hiệu", ""), ("Mua từ", "num")], rows,
                     "Chưa nắm giữ cổ phiếu nào.")
    return ui.card("Danh mục đang nắm giữ", table, flush=True,
                   sub=f"Giá {e(s['label'] or 'gần nhất')} · giá vốn đã gồm phí mua · cắt lỗ/mục tiêu in đậm: anh đặt, "
                       "thường: theo Siêu tín hiệu · “về dd/mm”: cổ phiếu chờ T+2, bán được từ chiều ngày đó")


def _analytics(state: dict) -> str:
    a = state.get("analytics") or {}
    if not a.get("weights"):
        return ui.card("Rủi ro và tối ưu", '<p class="muted" style="margin:0">Bot tính biến động, tương quan và tỷ trọng '
                       "cân bằng rủi ro sau báo cáo 15:30 (cần từ 20 phiên giá).</p>")
    rows = [f'<tr><td class="sym">{e(w["symbol"])}</td><td class="num">{_p1(w["current"])}</td>'
            f'<td class="num">{_p1(w["suggested"])}</td><td class="num">{_p1(w["vol_pct"])}</td></tr>'
            for w in a["weights"]]
    pairs = "".join(f'<li>{e(p["a"])} – {e(p["b"])}: tương quan {p["corr"]:.2f} '.replace(".", ",")
                    + f'({_RISK_VI.get(p["risk"], p["risk"])})</li>' for p in a.get("pairs") or [])
    body = (ui.table([("Mã", ""), ("Hiện tại", "num"), ("Cân bằng rủi ro", "num"), ("Biến động năm", "num")], rows, "")
            + ('<h3>Cặp mã đi cùng chiều nhất</h3><ul class="feed">' + pairs + "</ul>" if pairs else ""))
    return ui.card("Rủi ro và tối ưu", body,
                   sub=f"Tính ngày {ts.ddmmyyyy(a.get('day', ''))} từ 120 phiên · cân bằng rủi ro: mã biến động ít "
                       "được tỷ trọng lớn hơn (tham khảo)")


def _rights(account: ts.TaiSan) -> str:
    today = ts.today_vn()
    pending, upcoming = account.pending_rights(), account.upcoming_rights()
    parts = []
    for r in pending:
        sym = e(r["symbol"])
        if r["kind"] == "STOCK":
            what = f'{ui.money(r.get("shares"))} cp thưởng (tỷ lệ {r["ratio"] * 100:g}%)'
            form = (f'<form method="post" action="/taisan/them" class="row" style="margin-top:8px">'
                    f'<input type="hidden" name="kind" value="BONUS"><input type="hidden" name="symbol" value="{sym}">'
                    f'<div class="fld"><input name="qty" inputmode="numeric" value="{int(r.get("shares") or 0)}" aria-label="Số cổ phiếu đã về"></div>'
                    f'<input type="hidden" name="day" value="{today}">'
                    '<button class="btn btn-ghost" type="submit">Ghi đã về</button></form>')
        else:
            what = f'cổ tức tiền ≈ {ui.money(r["value"])} đ sau thuế ({ui.money(r.get("eligible"))} cp)'
            form = (f'<form method="post" action="/taisan/tien" class="row" style="margin-top:8px">'
                    f'<input type="hidden" name="kind" value="DIVIDEND"><input type="hidden" name="symbol" value="{sym}">'
                    f'<div class="fld"><input name="amount" inputmode="numeric" value="{ui.money(r["value"])}" aria-label="Số tiền thực nhận"></div>'
                    f'<input type="hidden" name="day" value="{today}"><input type="hidden" name="note" value="quyền GDKHQ {ts.ddmm(r["ex"])}">'
                    '<button class="btn btn-ghost" type="submit">Ghi đã về</button></form>')
        parts.append(f'<li><b>⏳ {sym}</b>: {what}, GDKHQ {ts.ddmmyyyy(r["ex"])} · đang tính vào tổng tài sản{form}</li>')
    for r in upcoming:
        parts.append(f"<li>{e(account._right_line(r, pending=False))}</li>")
    body = (f'<ul class="feed">{"".join(parts)}</ul>' if parts
            else '<p class="muted" style="margin:0">Không có quyền chờ về hay sắp tới (cập nhật lúc 15:30 từ lịch sự kiện).</p>')
    return ui.card("Quyền: chờ về và sắp tới", body,
                   sub="Đã qua ngày GDKHQ mà chưa ghi nhận thì tính vào tổng tài sản; số tiền/cổ phiếu thật về "
                       "có thể lệch, sửa trước khi ghi")


def _shadow(account: ts.TaiSan, state: dict) -> str:
    stats = account.shadow_stats()
    rows = [f'<tr><td>{s["icon"]} {e(s["name"])}</td><td class="num">{s["alerts"]}</td><td class="num">{s["settled"]}</td>'
            f'<td class="num {ui.tone(s["r5"])}">{ui.pct(s["r5"])}</td><td class="num {ui.tone(s["r10"])}">{ui.pct(s["r10"])}</td>'
            f'<td class="num">{_p0(s["right"])}</td></tr>' for s in stats]
    recent = list(reversed(state.get("shadow") or []))[:8]
    icons = {k: v[0] for k, v in ts.ALERT_LEVELS.items()}
    feed = "".join(
        f'<li>{icons.get(r.get("level"), "")} {e(r.get("symbol"))} · {ts.ddmm(r.get("day", ""))} {e(r.get("at", ""))}'
        + (f' @ {ui.money(r.get("price"))}' if r.get("price") else "")
        + (f' · 5 phiên {ui.pct(r["r5"])}' if r.get("r5") is not None else "")
        + (f' · 10 phiên {ui.pct(r["r10"])}' if r.get("r10") is not None else "") + "</li>" for r in recent)
    body = (ui.table([("Mức", ""), ("Số lần", "num"), ("Có kết quả", "num"), ("Sau 5 phiên", "num"), ("Sau 10 phiên", "num"),
                      ("Giá thấp hơn", "num")], rows, "")
            + ('<h3>Cảnh báo gần đây</h3><ul class="feed">' + feed + "</ul>" if feed else ""))
    return ui.card("Kết quả cảnh báo bán", body,
                   sub="Giá 5 và 10 phiên sau cảnh báo so với đóng cửa ngày cảnh báo: giá thấp hơn = bán theo cảnh báo là đúng")


def _forms(account: ts.TaiSan, book: ts.Book) -> str:
    today = ts.today_vn()
    held = sorted(book.holdings)
    datalist = '<datalist id="ts-held">' + "".join(f'<option value="{e(s)}">' for s in held) + "</datalist>"
    trade = f"""
<form method="post" action="/taisan/them">{datalist}
  <div class="row">
    <div class="fld"><label class="f" for="ts-kind">Loại</label><select id="ts-kind" name="kind">
      <option value="BUY">Mua</option><option value="SELL">Bán</option>
      <option value="BONUS">Cổ phiếu thưởng / cổ tức bằng cổ phiếu</option></select></div>
    <div class="fld"><label class="f" for="ts-sym">Mã</label>
      <input id="ts-sym" class="ticker" name="symbol" list="ts-held" maxlength="10" autocomplete="off" required></div>
  </div>
  <div class="row mt">
    <div class="fld"><label class="f" for="ts-qty">Số cổ phiếu</label><input id="ts-qty" name="qty" inputmode="numeric" required></div>
    <div class="fld"><label class="f" for="ts-price">Giá (mua: đã gồm phí)</label>
      <input id="ts-price" name="price" inputmode="numeric" placeholder="bỏ trống với cổ phiếu thưởng"></div>
    <div class="fld"><label class="f" for="ts-day">Ngày</label><input id="ts-day" type="date" name="day" value="{today}" max="{today}"></div>
  </div>
  <div class="row mt">
    <div class="fld" style="flex:3 1 220px"><label class="f" for="ts-note">Ghi chú</label><input id="ts-note" name="note" maxlength="200"></div>
    <button class="btn btn-primary" type="submit">Ghi vào sổ</button>
  </div>
</form>"""
    cash = f"""
<form method="post" action="/taisan/tien">
  <div class="row">
    <div class="fld"><label class="f" for="ts-ckind">Loại</label><select id="ts-ckind" name="kind">
      <option value="DEPOSIT">Nạp tiền</option><option value="WITHDRAW">Rút tiền</option>
      <option value="DIVIDEND">Cổ tức tiền đã về</option><option value="ADJUST">Điều chỉnh (+/-)</option></select></div>
    <div class="fld"><label class="f" for="ts-amount">Số tiền</label>
      <input id="ts-amount" name="amount" inputmode="numeric" placeholder="ví dụ 50.000.000; điều chỉnh: -120.000" required></div>
  </div>
  <div class="row mt">
    <div class="fld"><label class="f" for="ts-csym">Mã (cổ tức)</label><input id="ts-csym" class="ticker" name="symbol" list="ts-held" maxlength="10"></div>
    <div class="fld"><label class="f" for="ts-cday">Ngày</label><input id="ts-cday" type="date" name="day" value="{today}" max="{today}"></div>
  </div>
  <div class="row mt">
    <div class="fld" style="flex:3 1 220px"><label class="f" for="ts-cnote">Ghi chú</label><input id="ts-cnote" name="note" maxlength="200"></div>
    <button class="btn btn-primary" type="submit">Ghi vào sổ</button>
  </div>
</form>
<form method="post" action="/taisan/khoptien" style="margin-top:18px">
  <div class="row">
    <div class="fld" style="flex:2 1 220px"><label class="f" for="ts-actual">Số dư tiền trên app CTCK</label>
      <input id="ts-actual" name="actual" inputmode="numeric" placeholder="tiền mặt hiện tại {ui.money(book.cash)}" required></div>
    <button class="btn btn-ghost" type="submit">Khớp tiền mặt</button>
  </div>
  <p class="muted small" style="margin:8px 0 0">Ghi một dòng điều chỉnh bằng phần chênh (phí, thuế, lãi chưa ghi); tính vào lãi/lỗ, không vào vốn gốc.</p>
</form>"""
    options = "".join(f'<option value="{e(s)}">{e(s)}</option>' for s in held)
    levels = (f"""
<form method="post" action="/taisan/catlo">
  <div class="row">
    <div class="fld"><label class="f" for="ts-lsym">Mã</label><select id="ts-lsym" name="symbol">{options}</select></div>
    <div class="fld"><label class="f" for="ts-stop">Cắt lỗ</label><input id="ts-stop" name="stop" inputmode="numeric"></div>
    <div class="fld"><label class="f" for="ts-target">Mục tiêu</label><input id="ts-target" name="target" inputmode="numeric"></div>
  </div>
  <div class="row mt">
    <button class="btn btn-primary" type="submit" name="action" value="save">Lưu mức của anh</button>
    <button class="btn btn-ghost" type="submit" name="action" value="clear">Xoá mức của mã</button>
  </div>
  <p class="muted small" style="margin:8px 0 0">Bot báo ⛔ khi giá ≤ cắt lỗ và 🎯 khi chạm mục tiêu. Mã mua mới tự lấy mức của Siêu tín hiệu nếu anh chưa đặt.</p>
</form>""" if held else '<p class="muted" style="margin:0">Chưa nắm giữ mã nào.</p>')
    return ('<div class="grid g2 mt" id="ghi">'
            + ui.card("Ghi giao dịch cổ phiếu", trade, sub="Mua, bán, cổ phiếu thưởng về tài khoản · giá bán ghi đúng giá khớp")
            + ui.card("Tiền", cash, sub="Nạp, rút, cổ tức tiền, điều chỉnh; hoặc khớp theo số dư thật")
            + "</div>"
            + '<div class="mt" id="catlo">' + ui.card("Cắt lỗ và mục tiêu của anh", levels) + "</div>")


def _journal(account: ts.TaiSan, entries: list[ts.Entry]) -> str:
    voided = {x.ref for x in entries if x.kind == "VOID"}
    try:
        pnl = {r.entry.id: r.pnl for r in ts.derive(entries).rows}
    except ts.LedgerError:
        pnl = {}
    shown = [x for x in entries if x.kind != "VOID"][-JOURNAL_ROWS:]
    rows = []
    for x in reversed(shown):
        dead = x.id in voided
        amount = (-x.amount if x.kind == "WITHDRAW" else x.amount) if x.kind in ts.CASH_KINDS else (
            x.qty * x.price if x.kind in ("BUY", "SELL") else None)
        undo = ('<span class="muted small">đã huỷ</span>' if dead else
                f'<form method="post" action="/taisan/huy" class="inline" '
                f'onsubmit="return confirm(\'Huỷ dòng #{x.id}?\')"><input type="hidden" name="id" value="{x.id}">'
                '<button class="linkbtn" type="submit">Huỷ</button></form>')
        rows.append(
            f'<tr class="{"void" if dead else ""}"><td class="num muted">#{x.id}</td><td class="num">{ts.ddmmyyyy(x.day)}</td>'
            f'<td>{e(ts.KIND_VI[x.kind])}</td><td class="sym">{e(x.symbol)}</td>'
            f'<td class="num">{ui.money(x.qty) if x.qty else ""}</td><td class="num">{ui.money(x.price) if x.price else ""}</td>'
            f'<td class="num {ui.tone(amount) if x.kind in ts.CASH_KINDS else ""}">{ui.money(amount) if amount is not None else ""}</td>'
            f'<td class="num {ui.tone(pnl.get(x.id))}">{_signed(pnl[x.id]) if pnl.get(x.id) is not None else ""}</td>'
            f'<td class="muted small">{e(x.note)}</td><td class="muted small">{e(_SOURCE_VI.get(x.source, x.source))}</td>'
            f"<td>{undo}</td></tr>")
    table = ui.table([("Số", "num"), ("Ngày", "num"), ("Loại", ""), ("Mã", ""), ("SL", "num"), ("Giá", "num"),
                      ("Số tiền", "num"), ("Lãi/lỗ", "num"), ("Ghi chú", ""), ("Nguồn", ""), ("", "")], rows,
                     "Chưa có giao dịch nào.")
    return ui.card("Nhật ký", table, flush=True,
                   sub=f"{min(len(shown), JOURNAL_ROWS)} dòng gần nhất · huỷ một dòng không xoá nó mà ghi thêm dòng huỷ "
                       "(sổ chỉ ghi thêm, không sửa)")


def _import_card(account: ts.TaiSan, has_entries: bool) -> str:
    from vnbot import taisan_excel

    pending = taisan_excel.pending(account)
    upload = f"""
<form method="post" action="{IMPORT_PATH}" enctype="multipart/form-data">
  <div class="row">
    <div class="fld" style="flex:2 1 240px"><label class="f" for="ts-file">File Excel (.xlsx, tối đa 1 MB)</label>
      <input id="ts-file" type="file" name="file" accept=".xlsx" required></div>
    <button class="btn btn-primary" type="submit">Xem trước</button>
  </div>
  <p class="muted small" style="margin:8px 0 0">File xuất từ app cũ (3 sheet) hoặc từ nút Xuất Excel bên cạnh. Bot cho xem
  trước và đối chiếu số liệu, chỉ ghi khi anh xác nhận.</p>
</form>"""
    if not pending:
        return ui.card("Nhập Excel", upload)
    text = "\n".join(line for line in str(pending.get("text") or "").splitlines() if "/tsnhap" not in line).strip()
    if has_entries:
        go = ("""<form method="post" action="/taisan/nhap/xacnhan" style="margin-top:14px">
  <input type="hidden" name="replace" value="1">
  <label class="check warn"><input type="checkbox" name="confirm" value="1" required> Thay toàn bộ danh mục hiện tại bằng file
  này (sổ cũ được lưu bản sao trong data/taisan).</label>
  <div class="row mt"><button class="btn btn-primary" type="submit">Thay bằng file này</button></div></form>""")
    else:
        go = ('<form method="post" action="/taisan/nhap/xacnhan" style="margin-top:14px">'
              '<input type="hidden" name="replace" value="0"><input type="hidden" name="confirm" value="1">'
              '<button class="btn btn-primary" type="submit">Ghi vào danh mục</button></form>')
    drop = ('<form method="post" action="/taisan/nhap/huy" style="margin-top:10px">'
            '<button class="btn btn-ghost" type="submit">Bỏ file này</button></form>')
    return ui.card("Xem trước nhập Excel", f'<pre class="report">{e(text)}</pre>{go}{drop}',
                   sub=f"{e(pending.get('filename') or 'file')} · tải lên {e(str(pending.get('created', ''))[:16].replace('T', ' '))}",
                   cls="accent-gold")


def _export_card(has_entries: bool) -> str:
    reset = """
<details style="margin-top:16px"><summary class="muted small">Xoá toàn bộ danh mục Tài sản</summary>
<form method="post" action="/taisan/reset" style="margin-top:10px">
  <div class="row"><div class="fld"><label class="f" for="ts-word">Gõ XOA để xác nhận</label>
    <input id="ts-word" name="word" autocomplete="off" required></div>
  <button class="btn btn-ghost" type="submit">Xoá danh mục</button></div>
  <p class="muted small" style="margin:8px 0 0">Sổ cũ được giữ lại thành bản sao trong data/taisan; ngưỡng cảnh báo giữ nguyên.</p>
</form></details>""" if has_entries else ""
    body = ('<div class="row"><a class="btn btn-primary" href="/taisan/xuat.xlsx">⬇ Xuất Excel</a>'
            '<a class="btn btn-ghost" href="/taisan/xuat.csv">⬇ Xuất CSV</a></div>'
            '<p class="muted small" style="margin:10px 0 0">Excel 4 sheet như app cũ: Vị thế hiện tại, Lịch sử giao dịch, '
            "Tổng quan, Tiền mặt (nhập lại được). CSV: toàn bộ nhật ký.</p>" + reset)
    return ui.card("Xuất file", body)


def page_body(account: ts.TaiSan, msg: str = "") -> str:
    entries = account.entries()
    has_entries = bool(ts.effective(entries))
    right = ('<div class="row"><a class="btn btn-ghost" href="#ghi">＋ Ghi giao dịch</a>'
             '<a class="btn btn-ghost" href="/taisan/xuat.xlsx">⬇ Excel</a></div>')
    parts = [ui.hero("Tài sản", "Danh mục tiền thật của anh: sổ riêng, không lẫn với các tài khoản ảo của bot. "
                     "Báo cáo mỗi ngày lúc 15:30 qua Telegram và Zoom; lệnh /ts trong chat.", right,
                     eyebrow="Tiền thật · bot không đặt lệnh"),
             _alert(msg)]
    if account.rebuilding:
        parts.append(ui.alert("Bot đang dựng lại biểu đồ từ ngày đầu tiên trong sổ (vài phút) · trang tự cập nhật.", "info"))
    try:
        book = account.book()
    except ts.LedgerError as exc:
        parts.append(ui.alert(f"Sổ Tài sản có dòng không hợp lệ: {exc}. Huỷ dòng sai trong nhật ký bên dưới.", "err"))
        parts.append('<div class="mt" id="nhatky">' + _journal(account, entries) + "</div>")
        return "".join(parts)
    import_card = _import_card(account, has_entries)
    if account.import_path.exists():
        parts.append('<div class="mt" id="nhap">' + import_card + "</div>")
    if not has_entries:
        parts.append(ui.card("Bắt đầu", "<ol><li>Nhập file Excel của app cũ (khung Nhập Excel bên dưới), hoặc</li>"
                             "<li>ghi khoản nạp vốn đầu tiên (khung Tiền), rồi từng lệnh mua (khung Ghi giao dịch).</li>"
                             "<li>Trong Telegram/Zoom: /tsnap, /tsmua, /tsban; hướng dẫn đủ: /trogiup.</li></ol>",
                             cls="accent-gold"))
        parts.append(_forms(account, book))
        if not account.import_path.exists():
            parts.append('<div class="grid g2 mt" id="nhap">' + import_card + _export_card(False) + "</div>")
        return "".join(parts)
    s = account.summary(book=book)
    state = account.state()
    hist = account.history()
    site = account.site_states(sorted(book.holdings), ts.today_vn())
    parts.append('<div class="mt">' + _overview(account, s, book, hist, state) + "</div>")
    parts.append('<div class="grid g-wide mt">' + _actions(account, s, site) + _allocation(account, s) + "</div>")
    parts.append('<div class="mt" id="danhmuc">' + _holdings(account, s, book, site) + "</div>")
    first = hist[0]["day"] if hist else ""
    parts.append('<div class="mt" id="hieusuat">' + ui.card(
        "Hiệu suất danh mục", ui.asset_chart(hist),
        sub=("Tổng tài sản theo giá đóng cửa mỗi phiên từ " + ts.ddmmyyyy(first) + "; phần trăm tính theo đơn vị quỹ nên "
             "nạp/rút tiền không bị tính là lãi/lỗ") if first else "Tổng tài sản theo giá đóng cửa mỗi phiên") + "</div>")
    parts.append('<div class="grid g2 mt">' + _analytics(state) + _rights(account) + "</div>")
    parts.append('<div class="mt">' + _shadow(account, state) + "</div>")
    parts.append(_forms(account, book))
    parts.append('<div class="mt" id="nhatky">' + _journal(account, entries) + "</div>")
    if not account.import_path.exists():
        parts.append('<div class="grid g2 mt" id="nhap">' + import_card + _export_card(True) + "</div>")
    else:
        parts.append('<div class="mt">' + _export_card(True) + "</div>")
    return "".join(parts)


# ─── routes ──────────────────────────────────────────────────────────────────

def register(app, account: ts.TaiSan, *, guard, post_guard, show_bot=lambda: True) -> None:
    """The /taisan routes on ``app`` (``guard``: the password; ``post_guard``: it and same-origin)."""
    from fastapi import Depends

    app.state.taisan = account

    def render(body: str, refresh: int | None = None) -> HTMLResponse:
        return HTMLResponse(ui.page("Tài sản", body, active="taisan", refresh=refresh, show_bot=show_bot()))

    @app.get("/taisan", dependencies=[Depends(guard)])
    def taisan_page(msg: str = "") -> HTMLResponse:
        return render(page_body(account, msg[:2000]), refresh=20 if account.rebuilding else None)

    def run(action, anchor: str = "") -> RedirectResponse:
        try:
            reply = action()
        except ValueError as exc:            # LedgerError, the Excel and the number parsers' errors
            reply = f"⚠️ {exc}"
        return _back(reply, anchor)

    @app.post("/taisan/them", dependencies=post_guard)
    def taisan_trade(kind: str = Form(...), symbol: str = Form(""), qty: str = Form(""), price: str = Form(""),
                     day: str = Form(""), note: str = Form("")):
        def act():
            sym, count = _code(symbol), _number(qty, "Số cổ phiếu")
            when = day.strip() or None
            if kind == "BONUS":
                return account.record_bonus(sym, count, when, note, source="web")
            if kind not in ("BUY", "SELL"):
                raise ts.LedgerError("Loại giao dịch không hợp lệ.")
            record = account.record_buy if kind == "BUY" else account.record_sell
            return record(sym, count, _number(price, "Giá"), when, note, source="web")
        return run(act, "ghi")

    @app.post("/taisan/tien", dependencies=post_guard)
    def taisan_cash(kind: str = Form(...), amount: str = Form(""), symbol: str = Form(""), day: str = Form(""),
                    note: str = Form("")):
        def act():
            when = day.strip() or None
            if kind == "DIVIDEND":
                if not symbol.strip():
                    raise ts.LedgerError("Cổ tức tiền: ghi mã cổ phiếu trả cổ tức.")
                return account.record_dividend(_code(symbol), _number(amount, "Số tiền"), when, note, source="web")
            value = _number(amount, "Số tiền", allow_negative=kind == "ADJUST")
            return account.record_cash(kind, value, when, note, source="web")
        return run(act, "ghi")

    @app.post("/taisan/khoptien", dependencies=post_guard)
    def taisan_match(actual: str = Form(...)):
        return run(lambda: account.match_cash(_number(actual, "Số dư"), source="web"), "ghi")

    @app.post("/taisan/catlo", dependencies=post_guard)
    def taisan_levels(symbol: str = Form(...), stop: str = Form(""), target: str = Form(""), action: str = Form("save")):
        def act():
            sym = _code(symbol)
            if action == "clear":
                return account.clear_levels(sym)
            cut = _optional(stop, "Mức cắt lỗ")
            if cut is None:
                raise ts.LedgerError("Ghi mức cắt lỗ (mục tiêu không bắt buộc).")
            return account.levels_text(sym, cut, _optional(target, "Mục tiêu"))
        return run(act, "catlo")

    @app.post("/taisan/nguong", dependencies=post_guard)
    def taisan_thresholds(warn: str = Form(...), danger: str = Form(...)):
        def act():
            try:
                w, d = float(warn.replace(",", ".")), float(danger.replace(",", "."))
            except ValueError:
                raise ts.LedgerError("Ngưỡng phải là số phần trăm, ví dụ 25 và 40.") from None
            account.set_thresholds(w, d)
            return f"✅ Ngưỡng tỷ trọng: theo dõi từ {w:g}%, nguy hiểm từ {d:g}%."
        return run(act)

    @app.post("/taisan/huy", dependencies=post_guard)
    def taisan_undo(id: str = Form(...)):  # noqa: A002 - the form field's name
        def act():
            if not id.strip().isdigit():
                raise ts.LedgerError("Số dòng không hợp lệ.")
            return account.undo(int(id), source="web")
        return run(act, "nhatky")

    @app.post(IMPORT_PATH, dependencies=post_guard)
    def taisan_import(file: UploadFile = File(...)):
        from vnbot import taisan_excel

        def act():
            name = (file.filename or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1][:120]
            if not name.lower().endswith(".xlsx"):
                raise ts.LedgerError("Chỉ nhận file Excel .xlsx.")
            data = file.file.read(MAX_UPLOAD_BYTES + 1)
            if len(data) > MAX_UPLOAD_BYTES:
                raise ts.LedgerError("File quá lớn (tối đa 1 MB).")
            text = taisan_excel.preview(account, data, filename=name)
            return text.splitlines()[0] if not text.startswith("❌") else text
        return run(act, "nhap")

    @app.post("/taisan/nhap/xacnhan", dependencies=post_guard)
    def taisan_import_confirm(replace: str = Form("0"), confirm: str = Form("")):
        from vnbot import taisan_excel

        def act():
            if confirm != "1":
                raise ts.LedgerError("Đánh dấu ô xác nhận trước khi thay danh mục.")
            text = taisan_excel.confirm(account, replace=replace == "1")
            account.rebuild_in_background()
            return text.replace("Xem: /ts hoặc trang /taisan.", "").strip()
        return run(act)

    @app.post("/taisan/nhap/huy", dependencies=post_guard)
    def taisan_import_cancel():
        from vnbot import taisan_excel

        return run(lambda: taisan_excel.cancel(account))

    @app.post("/taisan/reset", dependencies=post_guard)
    def taisan_reset(word: str = Form("")):
        def act():
            if word.strip().upper() not in ("XOA", "XOÁ", "XÓA"):
                raise ts.LedgerError("Gõ XOA vào ô xác nhận để xoá danh mục.")
            return account.reset()
        return run(act)

    @app.get("/taisan/xuat.xlsx", dependencies=[Depends(guard)])
    def taisan_export_xlsx():
        from vnbot import taisan_excel

        name = f"tai-san-{ts.today_vn()}.xlsx"
        path = taisan_excel.export_xlsx(account, account.dir / "export" / name)
        return FileResponse(path, filename=name,
                            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            headers={"Cache-Control": "no-store"})

    @app.get("/taisan/xuat.csv", dependencies=[Depends(guard)])
    def taisan_export_csv():
        from vnbot import taisan_excel

        name = f"tai-san-{ts.today_vn()}.csv"
        return Response(taisan_excel.export_csv(account).encode("utf-8"), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})
