"""The Danh mục tab (/taisan): the owner's real-money portfolio on the web.

The same journal as the /ts commands (``vnbot.taisan``), laid out like the owner's
portfolio app: a greeting with today's change, the allocation alerts, the total and its
parts (with the open risk of the cut-losses), the recovery goal next to the measured
figures, the work for the next session (the exit tickets), the discipline meter, the
position weights, the risk and a risk-parity split, the performance chart against
VNINDEX, one card per stock (company, three months of prices, price and change, P&L,
cost, levels, planned loss, ticket, the site's signal, news), then sections that open: the
details, the forms that record, the journal (filtered), the cash, the cut-loss levels,
the assistant, the evening report, the Excel import and the thresholds; last, the reset.

The page reads the saved closes and state; only the live prices of the holdings during
the session are fetched (cached a minute). Every form posts and comes back here.
"""

from __future__ import annotations

import html
import logging
import threading
from urllib.parse import quote

from fastapi import File, Form, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from vnbot import kyluat
from vnbot import taisan as ts
from vnbot import ui

logger = logging.getLogger(__name__)

IMPORT_PATH = "/taisan/nhap"
MAX_UPLOAD_BYTES = 1024 * 1024          # the import route's request body (the other routes: 64 KB)
JOURNAL_ROWS = 60
_RISK_VI = {"high": "cao", "moderate": "vừa", "low": "thấp"}
_RISK_BADGE = {"high": "b-err", "moderate": "b-watch", "low": "b-ok"}
_SOURCE_VI = {"telegram": "Telegram", "zoom": "Zoom", "web": "Web", "import": "Excel"}
_KIND_BADGE = {"BUY": "b-buy", "SELL": "b-sell", "BONUS": "b-info", "DEPOSIT": "b-ok", "WITHDRAW": "b-watch",
               "DIVIDEND": "b-ok", "ADJUST": "b-none"}
_FILTERS = (("", "Tất cả"), ("mua", "Mua"), ("ban", "Bán"), ("thuong", "Thưởng CP"), ("tien", "Tiền"))
_FILTER_KINDS = {"mua": ("BUY",), "ban": ("SELL",), "thuong": ("BONUS",), "tien": ts.CASH_KINDS}

CSS = """
.ts-top{display:grid;gap:var(--gap)}
.ts-top>*{min-width:0}
@media (min-width:1100px){.ts-top.two{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}}
.greet{padding:var(--pad)}
.greet .gh{font-size:var(--fs-section);font-weight:600;margin:0}
.greet .gs{color:var(--muted);margin:4px 0 10px}
.greet .chip{white-space:normal}
.warn-card .wh{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:var(--pad) var(--pad) 8px}
.warn-card .wh h2{display:flex;align-items:center;gap:8px}
.warn-card .wh h2 svg{width:16px;height:16px;color:var(--negative)}
.ar{display:grid;grid-template-columns:auto minmax(0,1fr) auto auto;align-items:center;gap:8px 12px;padding:10px 0;margin:0 var(--pad);
  border-top:1px solid var(--rule)}
.ar .s{display:flex;align-items:center;gap:6px;font-weight:500}
.ar .s svg{width:14px;height:14px;color:var(--negative)}
.ar .p{text-align:right;font-weight:500;color:var(--negative);font-variant-numeric:tabular-nums}
.ar .p small{display:block;color:var(--muted);font-weight:400;font-size:var(--fs-small)}
.ar .x{width:32px;height:32px;border:0;background:none;color:var(--muted);cursor:pointer;border-radius:var(--radius-control);display:grid;place-items:center}
.ar .x:hover{background:var(--raised);color:var(--text)}
.ar .x svg{width:16px;height:16px}
.ar .track{grid-column:1/-1}
.ar .chip{justify-self:start}
@media (min-width:620px){.ar{grid-template-columns:auto minmax(80px,1fr) auto auto auto}.ar .track{grid-column:auto}}
.warn-card .wf{padding:8px var(--pad) var(--pad);color:var(--muted);font-size:var(--fs-small);line-height:16px}
.ts-export{display:flex;justify-content:flex-end;gap:8px}
.total{font-size:32px;line-height:1.15;font-weight:500;letter-spacing:-.01em;margin:2px 0 10px;overflow-wrap:anywhere}
.vni-row{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:8px;margin-top:var(--gap);padding-top:var(--gap);
  border-top:1px solid var(--rule)}
.vni-row .v{font-weight:500}
.todo li{align-items:flex-start}
.stats2{display:flex;flex-wrap:wrap;gap:12px 32px;margin-bottom:var(--gap)}
.stats2 .l{color:var(--muted);font-size:var(--fs-small)}
.stats2 .v{font-size:var(--fs-kpi);line-height:1.15;font-weight:500;margin-top:4px;font-variant-numeric:tabular-nums}
.stocks{display:grid;gap:var(--gap)}
@media (min-width:900px){.stocks{grid-template-columns:repeat(2,minmax(0,1fr))}}
.stock{border:1px solid var(--border);border-radius:var(--radius);padding:16px;display:flex;flex-direction:column;gap:12px;min-width:0}
.sk-top{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}
.sk-top>div{min-width:0}.sk-top .btn{flex:none}
.sk-sym{font-size:20px;line-height:1.2;font-weight:600;margin:0}
.sk-name{color:var(--muted);font-size:var(--fs-small);line-height:16px;margin-top:2px}
.sk-meta{color:var(--muted);font-size:var(--fs-small);line-height:16px;margin-top:4px}
.sk-spark{display:flex;align-items:center;gap:12px;color:var(--muted);font-size:var(--fs-small)}
.sk-mid{display:flex;justify-content:space-between;align-items:flex-end;gap:12px;flex-wrap:wrap}
.sk-price{font-size:var(--fs-kpi);line-height:1.15;font-weight:500;margin-top:2px;font-variant-numeric:tabular-nums}
.sk-chg{font-size:var(--fs-small);margin-top:2px;font-variant-numeric:tabular-nums}
.pnl{min-width:120px;text-align:right}
.pnl .l{color:var(--muted);font-size:var(--fs-small)}
.pnl .v{font-size:20px;line-height:1.2;font-weight:500;margin-top:2px;white-space:nowrap;font-variant-numeric:tabular-nums}
.pnl .pc{font-size:var(--fs-small);font-variant-numeric:tabular-nums}
.jfilter{display:flex;flex-wrap:wrap;gap:var(--gap);align-items:flex-end;margin-bottom:var(--gap)}
.jfilter .fld{flex:1 1 140px}
.jl{list-style:none;margin:0;padding:0}
.jr{display:flex;justify-content:space-between;gap:12px;padding:10px 0;border-top:1px solid var(--rule)}
.jr:first-child{border-top:0;padding-top:0}
.jr.void{opacity:.5}.jr.void .jr-t{text-decoration:line-through}
.jr-t{display:flex;flex-wrap:wrap;align-items:center;gap:8px}
.jr-s{font-size:var(--fs-small);line-height:16px;color:var(--muted);margin-top:2px}
.jr-r{text-align:right;flex:none;font-variant-numeric:tabular-nums}
.sig-row{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:8px 12px;padding:10px 0;border-top:1px solid var(--rule)}
.sig-row:first-child{border-top:0;padding-top:0}
.sig-row .acts{display:flex;flex-wrap:wrap;gap:8px}
.goal-line{margin:0 0 4px}
.danger-card .card-b p{color:var(--muted);margin:0 0 12px}
"""

_DISMISS_JS = """<script>
(function(){
  var card=document.querySelector('.warn-card');if(!card)return;var key='ts-hide-'+card.getAttribute('data-day');
  function hidden(){try{return JSON.parse(sessionStorage.getItem(key)||'[]');}catch(e){return [];}}
  function save(list){try{sessionStorage.setItem(key,JSON.stringify(list));}catch(e){}}
  function apply(){var h=hidden(),left=0;card.querySelectorAll('.ar').forEach(function(r){var gone=h.indexOf(r.getAttribute('data-sym'))>=0;
    r.hidden=gone;if(!gone)left++;});card.hidden=left===0;}
  card.querySelectorAll('.ar .x').forEach(function(b){b.addEventListener('click',function(){var h=hidden();
    h.push(b.closest('.ar').getAttribute('data-sym'));save(h);apply();});});
  apply();
})();
</script>"""


def e(text) -> str:
    return html.escape(str(text if text is not None else ""))


def _signed(value) -> str:
    return "—" if value is None else ts.signed(value)


def _p1(value) -> str:
    """12.34 -> "12,3%"."""
    return "—" if value is None else f"{value:.1f}%".replace(".", ",")


def _price_label(label: str) -> str:
    """"giá đóng cửa gần nhất" / "khớp 10:30" -> "Giá đóng cửa gần nhất" / "Giá khớp 10:30"."""
    label = (label or "gần nhất").strip()
    return "Giá " + (label[4:] if label.lower().startswith("giá ") else label)


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
    kind = "err" if msg[:1] in ("⚠", "❌") else "ok" if msg[:1] in ("✅", "↩", "📥", "🗑", "📤", "🔄") else "info"
    role = "alert" if kind == "err" else "status"
    return f'<div class="alert alert-{kind}" role="{role}">{e(msg).replace(chr(10), "<br>")}</div>'


def _vnd(value) -> str:
    return "—" if value is None else f"{ui.money(value)} đ"


def _svnd(value) -> str:
    return "—" if value is None else f"{_signed(value)} đ"


# ─── what the numbers say ────────────────────────────────────────────────────

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


def _streak(hist: list[dict], today_change: float | None) -> tuple[int, int]:
    """(sessions in a row, +1 up / -1 down) ending with the latest change."""
    changes = [(b["total"] - a["total"]) - (b["capital"] - a["capital"]) for a, b in zip(hist, hist[1:])]
    if today_change is not None:
        changes.append(today_change)
    n, sign = 0, 0
    for c in reversed(changes):
        s = 1 if c > 0.5 else -1 if c < -0.5 else 0
        if s == 0 or (sign and s != sign):
            break
        sign, n = s, n + 1
    return n, sign


def _vnindex(closes: dict) -> tuple[float | None, float | None, str]:
    series = closes.get("VNINDEX") or {}
    days = sorted(series)
    if not days:
        return None, None, ""
    last = series[days[-1]]
    change = (last / series[days[-2]] - 1) * 100 if len(days) > 1 else None
    return last, change, days[-1]


# ─── the page, top to bottom ─────────────────────────────────────────────────

def _greeting(s: dict, hist: list[dict], todo: list, nxt: str) -> str:
    hour = ts.now_vn().hour
    hi, ic = (("Chào buổi sáng", "sunrise") if hour < 11 else ("Chào buổi trưa", "sun") if hour < 13
              else ("Chào buổi chiều", "sun") if hour < 18 else ("Chào buổi tối", "moon"))
    title, change, change_pct = _day_change(s, hist, s["label"])
    n, sign = _streak(hist, change if title == "Hôm nay" else None)
    what = (f"có {len(todo)} việc cần làm phiên {ts.ddmm(nxt)}, xem bên dưới" if todo
            else "không có việc cần làm, giữ nguyên danh mục")
    if change is None:
        line = f"Danh mục chưa có hai phiên giá đóng cửa · {what}."
    else:
        line = f"{title} danh mục {_svnd(change)} ({ui.pct(change_pct)}) · {what}."
    chips = []
    if change is not None:
        chips.append(ui.chip(f"{_svnd(change)} {title.lower()}", "pos" if change >= 0 else "neg"))
    if n >= 2:
        chips.append(ui.chip(f"{n} phiên {'tăng' if sign > 0 else 'giảm'} liên tiếp"))
    return (f'<section class="card greet"><p class="gh">{hi}</p>'
            f'<p class="gs">{e(line)}</p><div class="chips">{"".join(chips)}</div></section>')


def _alloc_alert(s: dict, alloc: dict) -> str:
    hot = [a for a in alloc["symbols"] if a["level"] != "ok"]
    if not hot:
        return ""
    rows = []
    for a in hot:
        value = next((p["value"] or p["cost"] for p in s["positions"] if p["symbol"] == a["symbol"]), None)
        limit = alloc["danger"] if a["level"] == "danger" else alloc["warn"]
        rows.append(
            f'<div class="ar {a["level"]}" data-sym="{e(a["symbol"])}"><span class="s">{ui.icon("up")}{e(a["symbol"])}</span>'
            f'<div class="track" role="img" aria-label="{_p1(a["pct"])} tổng tài sản"><span style="width:{min(100, a["pct"]):.1f}%"></span></div>'
            f'<span class="p">{_p1(a["pct"])}<small>{ui.money_short(value)}</small></span>'
            f'<span class="chip chip-neg">từ {_p1(limit)}</span>'
            f'<button class="x" type="button" aria-label="Ẩn cảnh báo {e(a["symbol"])} trong phiên này">{ui.icon("x")}</button></div>')
    head = (f'<div class="wh"><h2>{ui.icon("warn")}Cảnh báo tỷ trọng · {len(hot)} mã</h2>'
            f'<a class="btn" href="#nguong">Đổi ngưỡng</a></div>')
    foot = (f'<p class="wf">Tỷ trọng tính trên tổng tài sản {ui.money_short(s["total"])} · theo dõi từ {_p1(alloc["warn"])}, '
            f'nguy hiểm từ {_p1(alloc["danger"])} · bấm × để ẩn trong phiên này</p>')
    return (f'<section class="card warn-card" data-day="{e(ts.today_vn())}" aria-label="Cảnh báo tỷ trọng">'
            f'{head}{"".join(rows)}{foot}</section>{_DISMISS_JS}')


def _overview(account: ts.TaiSan, s: dict, book: ts.Book, hist: list[dict]) -> str:
    title, change, _ = _day_change(s, hist, s["label"])
    rights = account.pending_rights()
    sells = book.sells
    chips = [ui.chip(f"PnL {_svnd(s['pnl'])}", "pos" if s["pnl"] >= 0 else "neg"),
             ui.chip(ui.pct(s["pnl_pct"]), "pos" if (s["pnl_pct"] or 0) >= 0 else "neg")]
    if change is not None:
        chips.append(ui.chip(f"{title} {_svnd(change)}", "pos" if change >= 0 else "neg"))
    tiles = "".join([
        ui.tile("Tiền mặt", _vnd(s["cash"]), "âm: kiểm tra lại sổ" if s["cash"] < 0 else "Sức mua hiện có",
                cls="neg" if s["cash"] < 0 else ""),
        ui.tile("Giá trị thị trường", _vnd(s["market"]), f'{len(s["positions"])} mã đang nắm · {e(_price_label(s["label"]).lower())}'),
        ui.tile("Lãi/lỗ đã chốt", _svnd(s["realized"]), f"{sells} lệnh bán · thắng {book.wins}/{sells}" if sells
                else "chưa bán lệnh nào", cls=ui.tone(s["realized"])),
        ui.tile("Lãi/lỗ đang mở", _svnd(s["unrealized"]), "Theo giá vốn (đã gồm phí mua)", cls=ui.tone(s["unrealized"])),
        ui.tile("Cổ tức đã nhận", _vnd(s["dividends"]), "Cổ tức tiền mặt"),
        ui.tile("Quyền chờ về", _vnd(s["pending"]), f"{len(rights)} quyền đã qua GDKHQ" if rights else "Không có quyền đang chờ"),
        _risk_tile(account, s),
    ])
    vni, vni_chg, vni_day = _vnindex(account.closes())
    vni_row = ""
    if vni:
        value = f"{vni:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        vni_row = (f'<div class="vni-row"><span class="muted">VN-Index · {ts.ddmm(vni_day)}</span>'
                   f'<span><span class="v num">{value}</span> '
                   + (ui.chip(ui.pct(vni_chg), "pos" if vni_chg >= 0 else "neg") if vni_chg is not None else "")
                   + "</span></div>")
    warnings = "".join(ui.alert(w, "err") for w in book.warnings)
    return (f'<section class="card" id="tongquan"><div class="card-b">'
            f'<h2 class="lbl" style="font-weight:400">Tổng tài sản</h2><div class="total num">{_vnd(s["total"])}</div>'
            f'<div class="chips" style="margin-bottom:12px">{"".join(chips)}</div>{warnings}'
            f'<div class="tiles t4">{tiles}</div>{vni_row}</div></section>')


def _risk_tile(account: ts.TaiSan, s: dict) -> str:
    """The open risk: what every cut-loss would cost if hit (``kyluat.risk_summary``)."""
    try:
        r = kyluat.risk_summary(account, s)
    except Exception as exc:  # noqa: BLE001 - the tile says so
        logger.warning("Tài sản web: risk tile: %s", exc)
        return ui.tile("Rủi ro đang mở", "—", "chưa tính được")
    share = min(1.0, r["pct"] / r["cap"]) if r["cap"] else 0.0
    meter = (f'<div class="meter{" hot" if share >= 0.85 else ""}" role="img" '
             f'aria-label="Rủi ro đang mở {_p1(r["pct"])} trên trần {r["cap"]:g}%"><span style="width:{share * 100:.1f}%"></span></div>')
    sub = f'{_p1(r["pct"])} tài sản · trần {r["cap"]:g}%'
    if r["unlimited"]:
        sub += f' · {e(", ".join(r["unlimited"]))} chưa có cắt lỗ: không giới hạn'
    word = {"🔴": "vượt trần", "🟡": "gần trần"}.get(r["level"], "trong trần")
    return ui.tile("Rủi ro đang mở", _vnd(r["planned"]), f"{word} · {sub}" + meter, cls="neg" if r["level"] == "🔴" else "")


def _todo(todo: list, nxt: str, tickets: list[str] | None = None) -> str:
    if not todo and not tickets:
        return ""
    items = "".join(f"<li>{e(line)}</li>" for line in (tickets or [])) + "".join(f"<li>{e(line)}</li>" for _, line in todo)
    return ui.card(f"Việc cần làm phiên {ts.ddmm(nxt)}", f'<ul class="feed todo">{items}</ul>', kicker="Hành động",
                   sub="Như trong tin nhắn của bot: thủng cắt lỗ, Siêu tín hiệu BÁN, việc treo có hạn, thiếu cắt lỗ, chạm mục tiêu, "
                       "tỷ trọng quá ngưỡng", id="viec")


def _discipline(state: dict) -> str:
    """The discipline meter of the last evening report (``kyluat.discipline``, kept in the state)."""
    d = state.get("discipline") or {}
    if not d.get("day"):
        body = ('<p class="muted" style="margin:0">Chưa có số đo: thước kỷ luật được tính ở báo cáo 15:30 đầu tiên '
                "(hoặc /tsbaocao).</p>")
        return ui.card("Thước kỷ luật", body, kicker="Đo bằng code", id="kyluat")
    try:
        lines = kyluat.discipline_lines(d)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Tài sản web: discipline lines: %s", exc)
        lines = ["Chưa hiển thị được số đo."]
    feed = '<ul class="feed">' + "".join(f"<li>{e(line)}</li>" for line in lines[1:]) + "</ul>"
    groups = d.get("groups") or {}
    names = (("buy_ok", "Lệnh mua trong ngân sách", 20), ("buy_warn", "Lệnh mua có lưu ý", 20),
             ("buy_block", "Lệnh mua ngoài quy tắc", 20), ("override", "Ghi đè /tsgiu", 10),
             ("early", "Bán trước tín hiệu", 10))
    rows = []
    for key, name, need in names:
        g = groups.get(key) or {}
        note = "đang đo" if (g.get("settled") or 0) < need else ""
        rows.append(f'<tr><td>{e(name)}</td><td class="num">{g.get("n", 0)}</td><td class="num">{g.get("settled", 0)}</td>'
                    f'<td class="num {ui.tone(g.get("r10"))}">{ui.pct(g.get("r10"))}</td><td class="muted">{note}</td></tr>')
    table = ui.table([("Nhóm", ""), ("Số lệnh", "num"), ("Đã chấm", "num"), ("Giá 10 phiên sau TB", "num"), ("", "")], rows, "")
    body = (feed + "<h3>Kết quả theo nhóm (giá 10 phiên sau so với giá anh ghi)</h3>" + table
            + '<p class="muted small" style="margin:12px 0 0">Dưới 20 lệnh mua / 10 lần ghi đè hay bán sớm: chưa đủ để nói gì. '
              "Số đo để học, không để tiếc.</p>")
    return ui.card("Thước kỷ luật", body, kicker="Đo bằng code", sub=e(lines[0].replace("📏 KỶ LUẬT ", "")), id="kyluat")


def _goal(account: ts.TaiSan, day: str, s: dict) -> str:
    """The recovery goal next to the measured figures (``kyluat.goal_text``), and the form for its date."""
    try:
        text = kyluat.goal_text(account, day, s)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Tài sản web: goal: %s", exc)
        text = "🎯 Chưa tính được mục tiêu."
    lines = text.split("\n")
    head = lines[0].replace("🎯 MỤC TIÊU HOÀN VỐN · ", "").replace("🎯 ", "")
    body = "".join(f'<p class="goal-line">{e(line)}</p>' for line in lines[1:])
    g = account.state().get("goal") or {}
    form = (f'<form method="post" action="/taisan/muctieu" class="row mt" style="align-items:flex-end">'
            f'<div class="fld"><label class="f" for="ts-goal">Mốc hoàn vốn</label>'
            f'<input id="ts-goal" type="date" name="date" value="{e(g.get("date") or kyluat.DEFAULT_GOAL_DATE)}" required></div>'
            '<button class="btn btn-ghost" type="submit">Đặt mốc</button>'
            '<button class="btn btn-ghost" type="submit" name="action" value="clear">Về mặc định</button></form>')
    return ui.card("Mục tiêu hoàn vốn", body + form, kicker="Số học, không dự báo", sub=e(head), id="muctieu")


def _bars(rows: list[tuple[str, float, str, str, float | None, str]]) -> str:
    """(name, pct, right text, level, marker pct, note)."""
    out = []
    for name, pct, right, level, marker, note in rows:
        mark = f'<i style="left:calc({min(100, max(0, marker)):.1f}% - 1px)" aria-hidden="true"></i>' if marker is not None else ""
        out.append(f'<div class="{level}"><div class="h"><b>{name}</b><span class="num">{right}</span></div>'
                   f'<div class="track" role="img" aria-label="{e(name)} {_p1(pct)}"><span style="width:{min(100, max(1.5, pct)):.1f}%"></span>{mark}</div>'
                   + (f'<div class="note">{note}</div>' if note else "") + "</div>")
    return '<div class="bars">' + "".join(out) + "</div>"


def _weights(s: dict) -> str:
    market = sum((p["value"] or p["cost"]) for p in s["positions"]) or 1.0
    rows = []
    for p in sorted(s["positions"], key=lambda p: -(p["value"] or p["cost"])):
        value = p["value"] or p["cost"]
        share = value / market * 100
        rows.append((e(p["symbol"]), share, f"{_vnd(value)} · {_p1(share)}", "ok", None, ""))
    return ui.card("Tỷ trọng vị thế", _bars(rows) + '<p class="muted small" style="margin:16px 0 0">Tính trên giá trị cổ phiếu '
                   f'({ui.money_short(market)}); cảnh báo tỷ trọng tính trên tổng tài sản.</p>',
                   kicker="Cơ cấu danh mục", action=ui.chip(f"{len(s['positions'])} mã"), id="tytrong")


def _optimize(state: dict, alloc: dict) -> str:
    a = state.get("analytics") or {}
    level = {x["symbol"]: x["level"] for x in alloc["symbols"]}
    head = ('<div class="stats2"><div><div class="l">Đa dạng hoá</div><div class="v" style="color:var(--warn)">'
            + (f'{a["diversification"]}<span class="muted" style="font-size:16px">/100</span>' if a.get("diversification") is not None else "—")
            + '</div></div><div><div class="l">Biến động/năm</div><div class="v">'
            + (_p1(a["vol_pct"]) if a.get("vol_pct") is not None else "—") + "</div></div></div>")
    if a.get("weights"):
        rows = []
        for w in sorted(a["weights"], key=lambda w: -w["current"]):
            sug = w["suggested"]
            arrow = ("" if sug is None else f' <span class="neg">↓ {_p1(sug)}</span>' if sug < w["current"] - 2
                     else f' <span class="pos">↑ {_p1(sug)}</span>' if sug > w["current"] + 2 else ' <span class="pos">✓</span>')
            lv = level.get(w["symbol"], "ok")
            badge = (' <span class="badge b-err">Tập trung cao</span>' if lv == "danger"
                     else ' <span class="badge b-watch">Theo dõi</span>' if lv == "watch" else "")
            rows.append((e(w["symbol"]) + badge, w["current"], _p1(w["current"]) + arrow, lv, sug,
                         f"Biến động {_p1(w['vol_pct'])}/năm · cân bằng rủi ro {_p1(sug)}"))
        weights = (_bars(rows) + '<p class="muted small" style="margin:16px 0 0">Vạch đứng = tỷ trọng cân bằng rủi ro '
                   "(mã ít biến động được tỷ trọng cao hơn); chỉ để tham khảo.</p>")
    else:
        weights = '<p class="muted" style="margin:0">Bot tính sau báo cáo 15:30 hoặc khi bấm “Làm mới giá” (cần từ 20 phiên giá).</p>'
    sectors = _bars([(e(x["sector"]) + (' <span class="badge b-err">Nặng</span>' if x["level"] == "danger"
                                        else ' <span class="badge b-watch">Theo dõi</span>' if x["level"] == "watch" else ""),
                      x["pct"], _p1(x["pct"]) + " tài sản", x["level"], None, "") for x in alloc["sectors"]]) \
        if alloc["sectors"] else '<p class="muted" style="margin:0">Chưa có mã nào.</p>'
    pairs = a.get("pairs") or []
    corr = ('<ul class="feed">' + "".join(
        f'<li><span style="flex:1"><b>{e(p["a"])}</b> – <b>{e(p["b"])}</b></span>'
        f'<span class="num">{p["corr"]:.2f}'.replace(".", ",") + f'</span> <span class="badge {_RISK_BADGE.get(p["risk"], "b-none")}">'
        f'{_RISK_VI.get(p["risk"], p["risk"])}</span></li>' for p in pairs) + "</ul>"
        + '<p class="muted small" style="margin:12px 0 0">Tương quan 120 phiên: gần 1 là hai mã lên xuống cùng nhau.</p>') if pairs \
        else '<p class="muted" style="margin:0">Cần từ hai mã có đủ 20 phiên giá.</p>'
    when = f"Tính ngày {ts.ddmmyyyy(a['day'])} từ 120 phiên" if a.get("day") else ""
    return ui.card("Rủi ro &amp; phân bổ", head + ui.tabs("opt", [("Tỷ trọng", weights), ("Ngành", sectors), ("Tương quan", corr)]),
                   kicker="Tối ưu danh mục", sub=when, id="toiuu")


def _change_today(spark: dict, price: float | None, live: bool) -> tuple[float | None, float | None]:
    days, closes = spark.get("days") or [], spark.get("c") or []
    if not price or not closes:
        return None, None
    today = ts.today_vn()
    if live:
        ref = next((c for d, c in zip(reversed(days), reversed(closes)) if d < today), None)
    else:
        ref = closes[-2] if len(closes) > 1 and abs(closes[-1] - price) < 1e-6 else closes[-1]
    if not ref:
        return None, None
    return price - ref, (price / ref - 1) * 100


def _stock_card(p: dict, h: ts.Holding, name: str, spark: dict, level: dict, sig: dict | None, live: bool,
                extra: dict | None = None) -> str:
    sym = e(p["symbol"])
    price = p["price"]
    chg, chg_pct = _change_today(spark, price, live)
    pnl_cls = "neg" if (p["pnl"] or 0) < 0 else "pos"
    chips = [ui.chip(f"SL {ui.money(p['qty'])}"), ui.chip(f"Vốn TB {ui.money(p['avg'])} đ")]
    stop, target = float(level.get("stop") or 0), level.get("target")
    who = "của anh" if level.get("source") == "owner" else "Siêu tín hiệu"
    if stop:
        hit = price and price <= stop
        chips.append(ui.chip(f"{'Thủng cắt lỗ' if hit else 'Cắt lỗ'} {ui.money(stop)}", "neg" if hit else "")
                     .replace("<span ", f'<span title="Mức {who}" ', 1))
    if target:
        hit = price and price >= float(target)
        chips.append(ui.chip(f"{'Chạm mục tiêu' if hit else 'Mục tiêu'} {ui.money(target)}", "pos" if hit else "")
                     .replace("<span ", f'<span title="Mức {who}" ', 1))
    today = ts.today_vn()
    for day, qty in h.pending_lots(today):
        chips.append(f'<span class="badge b-none" title="Bán được từ chiều {ts.ddmmyyyy(day)}">{ui.money(qty)} cp về {ts.ddmm(day)}</span>')
    if sig:
        chips.append(f'<span class="badge {"b-buy" if sig.get("side") == "BUY" else "b-sell"}">Siêu tín hiệu '
                     f'{"MUA" if sig.get("side") == "BUY" else "BÁN"} {ts.ddmm(sig.get("day", ""))}</span>')
    chips += _discipline_chips(extra or {})
    spark_label = f"Giá {sym} khoảng 3 tháng"
    news = f"https://news.google.com/search?q={quote(p['symbol'] + ' cổ phiếu')}&hl=vi&gl=VN&ceid=VN:vi"
    change = (f'<div class="sk-chg {ui.tone(chg)}">{_signed(chg)} · {ui.pct(chg_pct)}</div>' if chg is not None
              else '<div class="sk-chg muted">—</div>')
    closes = [c for c in (spark.get("c") or []) if c]
    three = f"3 tháng {ui.pct((closes[-1] / closes[0] - 1) * 100)}" if len(closes) > 1 and closes[0] else "3 tháng · chưa đủ giá"
    return (
        f'<article class="stock" aria-labelledby="sk-{sym}"><div class="sk-top"><div>'
        f'<h3 class="sk-sym" id="sk-{sym}">{sym}</h3>' + (f'<div class="sk-name">{e(name)}</div>' if name else "")
        + f'<div class="sk-meta">{len(h.lots)} lot · SL {ui.money(p["qty"])} · mua từ {ts.ddmm(p["first_day"])}</div></div>'
        f'<a class="btn btn-ghost" href="/taisan?ma={sym}#nhatky">Xem lệnh</a></div>'
        f'<div class="sk-spark">{ui.sparkline(closes, label=spark_label)}<span>{three}</span></div>'
        f'<div class="sk-mid"><div><div class="muted small">Giá hiện tại</div>'
        f'<div class="sk-price num">{ui.money(price)}</div>{change}</div>'
        f'<div class="pnl"><div class="l">Lãi/lỗ</div><div class="v {pnl_cls} num">{_svnd(p["pnl"])}</div>'
        f'<div class="pc {pnl_cls}">{ui.pct(p["pnl_pct"])}</div></div></div>'
        f'<div class="chips">{"".join(chips)}</div>'
        f'<div class="tiles">{ui.tile("Tổng mua", _vnd(p["cost"]))}{ui.tile("Hiện tại", _vnd(p["value"]))}</div>'
        f'<a class="btn btn-ghost btn-block" href="{e(news)}" target="_blank" rel="noopener noreferrer">Đọc tin tức'
        f'<span class="sr-only"> về {sym} (mở tab mới)</span></a></article>')


def _discipline_chips(extra: dict) -> list[str]:
    """The planned loss or the missing cut-loss, the exit ticket, today's early sign."""
    chips = []
    row = extra.get("risk")
    if row and row.get("stop") is None:
        chips.append(ui.chip("Chưa cắt lỗ → không giới hạn", "neg"))
    elif row and row.get("loss") is not None and not row.get("waiting"):
        chips.append(ui.chip(f"Nếu chạm cắt lỗ −{ui.money(row['loss'])} đ · {_p1(row['loss_pct'])}"))
    t = extra.get("ticket")
    if t and t.get("status") == "open":
        chips.append(ui.chip(f"{'Trang BÁN' if t.get('level') == 'sell' else 'Thủng cắt lỗ'} · treo {extra.get('hanging', 0)} phiên chưa bán", "neg"))
    elif t and t.get("status") == "held":
        chips.append(f'<span class="badge b-info">Giữ có lý do từ {ts.ddmm(t.get("held_day") or t.get("opened") or "")}</span>')
    if extra.get("early"):
        chips.append(ui.chip(f"Dấu hiệu bán sớm {e(extra['early'])} · theo dõi", "amber"))
    return chips


def _card_extras(account: ts.TaiSan, s: dict, state: dict, day: str) -> dict[str, dict]:
    """Per symbol, what the discipline layer adds to a stock card."""
    try:
        rows = {r["symbol"]: r for r in kyluat.risk_summary(account, s)["rows"]}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Tài sản web: risk rows: %s", exc)
        rows = {}
    exits = state.get("exits") or {}
    today = ts.today_vn()
    early = {r.get("symbol"): r.get("at", "") for r in state.get("shadow") or []
             if r.get("day") == today and r.get("level") == "early"}
    out = {}
    for p in s["positions"]:
        sym = p["symbol"]
        t = exits.get(sym)
        hanging = max(0, kyluat._sessions_until(t["opened"], day) - 1) if t and t.get("opened") else 0
        out[sym] = {"risk": rows.get(sym), "ticket": t, "hanging": hanging, "early": early.get(sym)}
    return out


def _holdings(account: ts.TaiSan, s: dict, book: ts.Book, state: dict, site: dict, day: str | None = None) -> str:
    names = account.names([p["symbol"] for p in s["positions"]])
    levels, spark = state.get("levels") or {}, state.get("spark") or {}
    live = bool(s["label"]) and s["label"] != "giá đóng cửa gần nhất"
    extras = _card_extras(account, s, state, day or account.report_day())
    cards = "".join(_stock_card(p, book.holdings[p["symbol"]], names.get(p["symbol"], ""), spark.get(p["symbol"]) or {},
                                levels.get(p["symbol"]) or {}, site.get(p["symbol"]), live, extras.get(p["symbol"]))
                    for p in sorted(s["positions"], key=lambda p: -(p["value"] or p["cost"])))
    action = (f'<div class="chips">{ui.chip(f"{len(s["positions"])} mã")}'
              f'<form method="post" action="/taisan/lammoi" class="inline"><button class="btn btn-ghost" type="submit">'
              f'Làm mới giá</button></form></div>')
    return ui.card("Mã đang nắm giữ", f'<div class="stocks">{cards}</div>' if cards
                   else '<p class="muted" style="margin:0">Chưa nắm giữ cổ phiếu nào.</p>',
                   kicker="Danh mục hiện tại", sub=f"{_price_label(s['label'])} · giá vốn đã gồm phí mua", action=action, id="danhmuc")


def _rights(account: ts.TaiSan) -> str:
    today = ts.today_vn()
    pending, upcoming = account.pending_rights(), account.upcoming_rights()
    if not pending and not upcoming:
        return ""
    parts = []
    for r in pending:
        sym = e(r["symbol"])
        if r["kind"] == "STOCK":
            what = f'{ui.money(r.get("shares"))} cp thưởng (tỷ lệ {r["ratio"] * 100:g}%)'
            form = (f'<form method="post" action="/taisan/them" class="row" style="margin-top:12px">'
                    f'<input type="hidden" name="kind" value="BONUS"><input type="hidden" name="symbol" value="{sym}">'
                    f'<div class="fld"><label class="f" for="r-{sym}-q">Số cổ phiếu đã về</label>'
                    f'<input id="r-{sym}-q" name="qty" inputmode="numeric" value="{int(r.get("shares") or 0)}"></div>'
                    f'<input type="hidden" name="day" value="{today}"><button class="btn btn-primary" type="submit">Ghi đã về</button></form>')
        else:
            what = f'cổ tức tiền ≈ {ui.money(r["value"])} đ sau thuế ({ui.money(r.get("eligible"))} cp)'
            form = (f'<form method="post" action="/taisan/tien" class="row" style="margin-top:12px">'
                    f'<input type="hidden" name="kind" value="DIVIDEND"><input type="hidden" name="symbol" value="{sym}">'
                    f'<div class="fld"><label class="f" for="r-{sym}-a">Số tiền thực nhận</label>'
                    f'<input id="r-{sym}-a" name="amount" inputmode="numeric" value="{ui.money(r["value"])}"></div>'
                    f'<input type="hidden" name="day" value="{today}"><input type="hidden" name="note" value="quyền GDKHQ {ts.ddmm(r["ex"])}">'
                    '<button class="btn btn-primary" type="submit">Ghi đã về</button></form>')
        parts.append(f'<li style="display:block"><b>{sym}</b>: {what}, GDKHQ {ts.ddmmyyyy(r["ex"])} · đang tính vào tổng tài sản{form}</li>')
    parts += [f"<li>{e(account._right_line(r, pending=False))}</li>" for r in upcoming]
    return ui.card("Chờ về và sắp tới", f'<ul class="feed">{"".join(parts)}</ul>', kicker="Quyền", id="quyen",
                   sub="Đã qua ngày GDKHQ mà chưa ghi thì tính vào tổng tài sản; số thật về có thể lệch, sửa trước khi ghi")


# ─── the sections that open ──────────────────────────────────────────────────

def _details(s: dict, book: ts.Book) -> str:
    tiles = "".join([
        ui.tile("Tổng vốn", _vnd(book.capital), "Nạp trừ rút"),
        ui.tile("Tiền mặt hệ thống", _vnd(book.cash), "Từ dòng tiền và giao dịch"),
        ui.tile("Cổ tức đã nhận", _vnd(book.dividends), "Cổ tức tiền mặt"),
        ui.tile("Điều chỉnh thủ công", _svnd(book.adjustments), "Cân bằng sổ sách"),
        ui.tile("Đã nạp", _vnd(book.deposits), ""), ui.tile("Đã rút", _vnd(book.withdrawals), ""),
        ui.tile("Lệnh bán", str(book.sells), f"thắng {book.wins} · thua {book.losses}"),
        ui.tile("Bắt đầu theo dõi", ts.ddmmyyyy(book.first_day) or "—", f"dòng cuối {ts.ddmmyyyy(book.last_day)}"),
    ])
    return ui.acc("chitiet", "Thống kê", "Chi tiết danh mục", f'<div class="tiles">{tiles}</div>')


def _trade_form(book: ts.Book) -> str:
    today = ts.today_vn()
    held = sorted(book.holdings)
    datalist = '<datalist id="ts-held">' + "".join(f'<option value="{e(s)}">' for s in held) + "</datalist>"
    form = f"""
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
    <div class="fld"><label class="f" for="ts-stop">Cắt lỗ (để kiểm tra)</label>
      <input id="ts-stop" name="stop" inputmode="numeric" placeholder="tuỳ chọn"></div>
  </div>
  <div class="row mt">
    <button class="btn btn-ghost" type="submit" formaction="/taisan/kiemtra" formnovalidate>Kiểm tra lệnh mua</button>
    <button class="btn btn-primary" type="submit">Ghi vào sổ</button>
  </div>
  <p class="muted small" style="margin:12px 0 0">Giá bán ghi đúng giá khớp; bot không trừ phí hay thuế. Mua mới tự lấy cắt lỗ/mục tiêu
  của Siêu tín hiệu nếu anh chưa đặt. «Kiểm tra lệnh mua» chỉ xem lệnh có nằm trong ngân sách rủi ro (2% tài sản) không,
  không ghi gì vào sổ và không khuyên mua.</p>
</form>"""
    return ui.acc("ghi", "Giao dịch", "Thêm giao dịch", form)


def _journal(entries: list[ts.Entry], ma: str, loai: str) -> str:
    voided = {x.ref for x in entries if x.kind == "VOID"}
    try:
        pnl = {r.entry.id: r.pnl for r in ts.derive(entries).rows}
    except ts.LedgerError:
        pnl = {}
    lines = [x for x in entries if x.kind != "VOID"]
    symbols = sorted({x.symbol for x in lines if x.symbol})
    if ma:
        lines = [x for x in lines if x.symbol == ma]
    if loai in _FILTER_KINDS:
        lines = [x for x in lines if x.kind in _FILTER_KINDS[loai]]
    shown = lines[-JOURNAL_ROWS:]
    items = []
    for x in reversed(shown):
        dead = x.id in voided
        if x.kind in ts.CASH_KINDS:
            cash, what = (-x.amount if x.kind == "WITHDRAW" else x.amount), (f"<b>{e(x.symbol)}</b>" if x.symbol else "")
        elif x.kind == "BONUS":
            cash, what = None, f"<b>{e(x.symbol)}</b> +{ui.money(x.qty)} cp"
        else:
            cash = x.qty * x.price * (1 if x.kind == "SELL" else -1)
            what = f"<b>{e(x.symbol)}</b> {ui.money(x.qty)} cp × {ui.money(x.price)}"
        gain = pnl.get(x.id)
        undo = ('<span class="muted small">đã huỷ</span>' if dead else
                f'<form method="post" action="/taisan/huy" class="inline" onsubmit="return confirm(\'Huỷ dòng #{x.id}?\')">'
                f'<input type="hidden" name="id" value="{x.id}"><button class="linkbtn" type="submit" '
                f'aria-label="Huỷ dòng số {x.id}">Huỷ</button></form>')
        meta = " · ".join(v for v in (f"#{x.id}", ts.ddmmyyyy(x.day), _SOURCE_VI.get(x.source, x.source), e(x.note)) if v)
        items.append(
            f'<li class="jr{" void" if dead else ""}"><div><div class="jr-t"><span class="badge {_KIND_BADGE.get(x.kind, "b-none")}">'
            f'{e(ts.KIND_VI[x.kind])}</span>{what}</div><div class="jr-s">{meta}</div></div>'
            f'<div class="jr-r"><div class="num {ui.tone(cash)}">{_signed(cash) if cash is not None else ""}</div>'
            + (f'<div class="small num {ui.tone(gain)}">lãi/lỗ {_signed(gain)}</div>' if gain is not None else "")
            + f"{undo}</div></li>")
    kinds = "".join(f'<option value="{k}"{" selected" if k == loai else ""}>{label}</option>' for k, label in _FILTERS)
    syms = '<option value="">Tất cả mã</option>' + "".join(
        f'<option value="{e(s)}"{" selected" if s == ma else ""}>{e(s)}</option>' for s in symbols)
    filt = (f'<form method="get" action="/taisan" class="jfilter" role="search" aria-label="Lọc nhật ký">'
            f'<div class="fld"><label class="f" for="j-loai">Loại</label><select id="j-loai" name="loai">{kinds}</select></div>'
            f'<div class="fld"><label class="f" for="j-ma">Mã</label><select id="j-ma" name="ma">{syms}</select></div>'
            '<button class="btn btn-ghost" type="submit">Lọc</button>'
            + ('<a class="btn btn-ghost" href="/taisan#nhatky">Bỏ lọc</a>' if ma or loai else "") + "</form>")
    body = (filt + (f'<ul class="jl">{"".join(items)}</ul>' if items else '<p class="muted" style="margin:0">Không có dòng nào.</p>')
            + f'<p class="muted small" style="margin:16px 0 0">{len(shown)} dòng gần nhất · huỷ một dòng là ghi thêm dòng huỷ '
            "(sổ chỉ ghi thêm, không sửa); trong Telegram: /tsnhatky, /tshuy</p>")
    return ui.acc("nhatky", "Giao dịch", "Nhật ký giao dịch", body, opened=bool(ma or loai))


def _cash_form(book: ts.Book) -> str:
    today = ts.today_vn()
    body = f"""
<form method="post" action="/taisan/tien">
  <div class="row">
    <div class="fld"><label class="f" for="ts-ckind">Loại</label><select id="ts-ckind" name="kind">
      <option value="DEPOSIT">Nạp tiền</option><option value="WITHDRAW">Rút tiền</option>
      <option value="DIVIDEND">Cổ tức tiền đã về</option><option value="ADJUST">Điều chỉnh (+/-)</option></select></div>
    <div class="fld"><label class="f" for="ts-amount">Số tiền</label>
      <input id="ts-amount" name="amount" inputmode="numeric" placeholder="50.000.000 · điều chỉnh: -120.000" required></div>
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
<form method="post" action="/taisan/khoptien" style="margin-top:24px">
  <div class="row">
    <div class="fld" style="flex:2 1 220px"><label class="f" for="ts-actual">Số dư tiền trên app CTCK</label>
      <input id="ts-actual" name="actual" inputmode="numeric" placeholder="tiền mặt trong sổ: {ui.money(book.cash)}" required></div>
    <button class="btn btn-ghost" type="submit">Khớp tiền mặt</button>
  </div>
  <p class="muted small" style="margin:12px 0 0">Ghi một dòng điều chỉnh bằng phần chênh (phí, thuế, lãi chưa ghi); tính vào lãi/lỗ,
  không vào vốn gốc.</p>
</form>"""
    return ui.acc("tien", "Tiền mặt", "Nạp / rút / cổ tức / điều chỉnh", body)


def _levels_form(book: ts.Book, levels: dict) -> str:
    held = sorted(book.holdings)
    if not held:
        return ui.acc("catlo", "Kỷ luật", "Cắt lỗ & mục tiêu", '<p class="muted" style="margin:0">Chưa nắm giữ mã nào.</p>')
    options = "".join(f'<option value="{e(s)}">{e(s)}</option>' for s in held)
    current = "".join(
        f'<li><b style="flex:1">{e(s)}</b><span class="num">cắt lỗ {ui.money(lv.get("stop")) if lv.get("stop") else "—"}'
        f' · mục tiêu {ui.money(lv.get("target")) if lv.get("target") else "—"}</span>'
        f' <span class="badge {"b-info" if lv.get("source") == "owner" else "b-none"}">{"anh đặt" if lv.get("source") == "owner" else "Siêu tín hiệu"}</span></li>'
        for s, lv in sorted(levels.items()) if s in book.holdings)
    body = (f"""
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
</form>""" + (f'<ul class="feed" style="margin-top:16px">{current}</ul>' if current else "")
            + '<p class="muted small" style="margin:12px 0 0">Bot báo khi giá ≤ cắt lỗ và khi chạm mục tiêu (trong phiên và báo cáo 15:30).</p>')
    return ui.acc("catlo", "Kỷ luật", "Cắt lỗ & mục tiêu", body)


def _assistant(account: ts.TaiSan, book: ts.Book, site: dict) -> str:
    from vnbot import external

    site_on = external.enabled()
    rows = []
    for sym in sorted(book.holdings):
        sig = site.get(sym)
        if sig:
            side = "MUA" if sig.get("side") == "BUY" else "BÁN"
            plan = (f" · vào {ui.money(sig.get('entry_low'))}-{ui.money(sig.get('entry_high'))}" if sig.get("entry_low") else "") \
                + (f" · mục tiêu {ui.money(sig.get('tp1'))}" if sig.get("tp1") else "") + (f" · cắt lỗ {ui.money(sig.get('sl'))}" if sig.get("sl") else "")
            state = (f'<span class="badge {"b-buy" if side == "MUA" else "b-sell"}">{side}</span> '
                     f'<span class="small">từ {ts.ddmm(sig.get("day", ""))} @ {ui.money(sig.get("price"))}{plan}</span>')
        else:
            state = ('<span class="muted small">chưa có tín hiệu của trang</span>' if site_on
                     else '<span class="muted small">bật SIEUTINHIEU=1 để xem tín hiệu của trang</span>')
        rows.append(f'<div class="sig-row"><div><b>{e(sym)}</b> {state}</div><div class="acts">'
                    f'<form method="post" action="/quick" class="inline"><input type="hidden" name="ticker" value="{e(sym)}">'
                    '<button class="btn btn-ghost" type="submit">Quant Engine</button></form>'
                    f'<form method="post" action="/critic" class="inline"><input type="hidden" name="ticker" value="{e(sym)}">'
                    '<button class="btn btn-ghost" type="submit">AI phản biện · 1 lượt</button></form></div></div>')
    stats = account.shadow_stats()
    srows = [f'<tr><td>{s["icon"]} {e(s["name"])}</td><td class="num">{s["alerts"]}</td><td class="num">{s["settled"]}</td>'
             f'<td class="num {ui.tone(s["r5"])}">{ui.pct(s["r5"])}</td><td class="num {ui.tone(s["r10"])}">{ui.pct(s["r10"])}</td>'
             f'<td class="num">{_right(s["right"])}</td></tr>'
             for s in stats]
    shadow = ui.table([("Cảnh báo", ""), ("Số lần", "num"), ("Có kết quả", "num"), ("Sau 5 phiên", "num"), ("Sau 10 phiên", "num"),
                       ("Giá thấp hơn", "num")], srows, "")
    body = ("<h3 style=\"margin-top:0\">Siêu tín hiệu và ý kiến hệ thống</h3>"
            + ("".join(rows) or '<p class="muted" style="margin:0">Chưa nắm giữ mã nào.</p>')
            + "<h3>Kết quả cảnh báo bán</h3>" + shadow
            + '<p class="muted small" style="margin:12px 0 0">Giá 5 và 10 phiên sau cảnh báo so với đóng cửa ngày cảnh báo: giá thấp hơn = '
            "bán theo cảnh báo là đúng. Backtest: bán khi thủng cắt lỗ hoặc trang BÁN; dấu hiệu sớm chỉ để theo dõi.</p>")
    return ui.acc("nhanxet", "Trợ lý", "Nhận xét & chiến lược", body)


def _right(value) -> str:
    return "—" if value is None else f"{value:.0f}%"


def _report(account: ts.TaiSan, fresh: bool, can_send: bool) -> str:
    last = account.last_report()
    if fresh:
        try:
            text, when = account.report_text(account.report_day(), refresh_site=False), "bản tính lúc này"
        except Exception as exc:  # noqa: BLE001 - shown, not raised
            logger.warning("Tài sản: report preview failed: %s", exc)
            text, when = "", ""
    else:
        text = last.get("text", "")
        at = str(last.get("at", ""))
        when = (f"Báo cáo phiên {ts.ddmmyyyy(last.get('day', ''))} · tạo lúc {at[11:16]} {ts.ddmm(at[:10])}" if last else "")
    body = ((f'<p class="muted small" style="margin:0 0 12px">{e(when)}</p><pre class="report">{e(text)}</pre>') if text
            else '<p class="muted" style="margin:0">Chưa có báo cáo nào: bot gửi lúc 15:30 các ngày giao dịch.</p>')
    acts = '<div class="row mt"><a class="btn btn-ghost" href="/taisan?baocao=1#baocao">Xem bản mới nhất</a>'
    if can_send:
        acts += ('<form method="post" action="/taisan/baocao" class="inline">'
                 '<button class="btn btn-primary" type="submit">Gửi ngay qua Telegram & Zoom</button></form>')
    return ui.acc("baocao", "Telegram", "Báo cáo cuối ngày", body + acts + "</div>", opened=fresh)


def _import(account: ts.TaiSan) -> str:
    body = f"""
<form method="post" action="{IMPORT_PATH}" enctype="multipart/form-data">
  <div class="row">
    <div class="fld" style="flex:2 1 240px"><label class="f" for="ts-file">File Excel (.xlsx, tối đa 1 MB)</label>
      <input id="ts-file" type="file" name="file" accept=".xlsx" required></div>
    <button class="btn btn-primary" type="submit">Xem trước</button>
  </div>
  <p class="muted small" style="margin:12px 0 0">File xuất từ app cũ (3 sheet) hoặc từ nút XLSX. Bot cho xem trước và đối chiếu
  số liệu, chỉ ghi khi anh xác nhận. Trong Telegram: gửi file kèm /tsnhap.</p>
</form>"""
    return ui.acc("nhap", "Excel", "Nhập file Excel", body)


def _thresholds(account: ts.TaiSan) -> str:
    warn, danger = account.thresholds()
    body = f"""
<form method="post" action="/taisan/nguong"><div class="row">
  <div class="fld"><label class="f" for="ts-warn">Theo dõi từ (%)</label><input id="ts-warn" name="warn" inputmode="decimal" value="{warn:g}"></div>
  <div class="fld"><label class="f" for="ts-danger">Nguy hiểm từ (%)</label><input id="ts-danger" name="danger" inputmode="decimal" value="{danger:g}"></div>
  <button class="btn btn-primary" type="submit">Lưu ngưỡng</button></div>
  <p class="muted small" style="margin:12px 0 0">Tỷ trọng một mã trên tổng tài sản; một ngành: gấp 1,5 lần. Trong Telegram: /tsnguong 25 40.</p>
</form>"""
    return ui.acc("nguong", "Cài đặt", "Ngưỡng cảnh báo tỷ trọng", body)


def _preview(account: ts.TaiSan, has_entries: bool) -> str:
    from vnbot import taisan_excel

    pending = taisan_excel.pending(account)
    if not pending:
        return ""
    text = "\n".join(line for line in str(pending.get("text") or "").splitlines() if "/tsnhap" not in line).strip()
    if has_entries:
        go = ("""<form method="post" action="/taisan/nhap/xacnhan" style="margin-top:16px">
  <input type="hidden" name="replace" value="1">
  <label class="check warn"><input type="checkbox" name="confirm" value="1" required> Thay toàn bộ danh mục hiện tại bằng file
  này (sổ cũ được lưu bản sao trong data/taisan).</label>
  <div class="row mt"><button class="btn btn-primary" type="submit">Thay bằng file này</button></div></form>""")
    else:
        go = ('<form method="post" action="/taisan/nhap/xacnhan" style="margin-top:16px">'
              '<input type="hidden" name="replace" value="0"><input type="hidden" name="confirm" value="1">'
              '<button class="btn btn-primary" type="submit">Ghi vào danh mục</button></form>')
    drop = ('<form method="post" action="/taisan/nhap/huy" style="margin-top:12px">'
            '<button class="btn btn-ghost" type="submit">Bỏ file này</button></form>')
    return ui.card("Xem trước nhập Excel", f'<pre class="report">{e(text)}</pre>{go}{drop}', kicker="Excel", id="xemtruoc",
                   sub=f"{e(pending.get('filename') or 'file')} · tải lên {e(str(pending.get('created', ''))[:16].replace('T', ' '))}")


def _reset(has_entries: bool) -> str:
    if not has_entries:
        return ""
    body = """<p>Xoá toàn bộ dữ liệu hiện tại để khởi tạo lại từ đầu. Sổ cũ được giữ lại thành bản sao trong data/taisan;
ngưỡng cảnh báo giữ nguyên.</p>
<form method="post" action="/taisan/reset"><div class="row">
  <div class="fld"><label class="f" for="ts-word">Gõ XOA để xác nhận</label><input id="ts-word" name="word" autocomplete="off" required></div>
  <button class="btn btn-danger" type="submit">Xoá toàn bộ</button></div></form>"""
    return '<div class="mt">' + ui.card("Reset danh mục", body, kicker="Quản trị hệ thống", cls="danger-card", id="reset") + "</div>"


def page_body(account: ts.TaiSan, msg: str = "", *, ma: str = "", loai: str = "", fresh_report: bool = False,
              can_send: bool = False) -> str:
    from tradingagents.market import vn_rules

    entries = account.entries()
    has_entries = bool(ts.effective(entries))
    parts = [f"<style>{CSS}</style>", ui.title("Danh mục", "Sổ tiền thật của anh: tổng tài sản, việc cần làm, thước kỷ luật và từng mã.",
                                               '<a class="btn" href="/taisan/xuat.xlsx">Xuất XLSX</a><a class="btn" href="/taisan/xuat.csv">Xuất CSV</a>'),
             _alert(msg)]
    if account.rebuilding:
        parts.append(ui.alert("Bot đang dựng lại biểu đồ từ ngày đầu tiên trong sổ (vài phút) · trang tự cập nhật.", "info"))
    try:
        book = account.book()
    except ts.LedgerError as exc:
        parts.append(ui.alert(f"Sổ Tài sản có dòng không hợp lệ: {exc}. Huỷ dòng sai trong nhật ký bên dưới.", "err"))
        parts.append(_journal(entries, "", ""))
        return "".join(parts)
    parts.append(_preview(account, has_entries))
    if not has_entries:
        start = ('<p style="margin:0 0 16px;color:var(--ink-2)">Sổ tiền thật của anh, tách riêng khỏi các tài khoản ảo của bot. '
                 "Bắt đầu bằng một trong ba cách:</p><div class=\"row\">"
                 '<a class="btn btn-primary" href="#nhap">Nhập file Excel</a><a class="btn btn-ghost" href="#tien">Nạp vốn đầu tiên</a>'
                 '<a class="btn btn-ghost" href="#ghi">Thêm giao dịch</a></div>'
                 '<p class="muted small" style="margin:16px 0 0">Trong Telegram/Zoom: /tsnap, /tsmua, /tsban; hướng dẫn đủ: /trogiup.</p>')
        parts.append(ui.card("Bắt đầu theo dõi tài sản", start, kicker="Danh mục trống"))
        parts.append('<div class="mt">' + _trade_form(book) + _cash_form(book) + _import(account) + "</div>")
        return "".join(parts)
    s = account.summary(book=book)
    state = account.state()
    hist = account.history()
    site = account.site_states(sorted(book.holdings), ts.today_vn())
    day = account.report_day()
    nxt = vn_rules.add_sessions(day, 1)
    prices = {p["symbol"]: p["price"] for p in s["positions"] if p["price"]}
    todo = account.actions(day, s, prices, site)
    try:
        tickets, covered = kyluat.ticket_lines(account, day, prices)
    except Exception as exc:  # noqa: BLE001 - the plain lines stand
        logger.warning("Tài sản web: tickets: %s", exc)
        tickets, covered = [], set()
    todo = [(rank, line) for rank, line in todo if not (line[:1] in ("⛔", "🔴") and ts.TaiSan._line_symbol(line) in covered)]
    alloc = account.allocation(s)
    warn = _alloc_alert(s, alloc)
    parts.append(f'<div class="ts-top{" two" if warn else ""}">' + _greeting(s, hist, todo + [(0, sym) for sym in sorted(covered)], nxt)
                 + warn + "</div>")
    parts.append(_overview(account, s, book, hist))
    parts.append('<div class="mt">' + _goal(account, day, s) + "</div>")
    if todo or tickets:
        parts.append('<div class="mt">' + _todo(todo, nxt, tickets) + "</div>")
    parts.append('<div class="mt">' + _discipline(state) + "</div>")
    parts.append('<div class="grid g2 mt">' + _weights(s) + _optimize(state, alloc) + "</div>")
    parts.append('<div class="mt">' + ui.card("Hiệu suất danh mục", ui.asset_chart(hist, total=s["total"], capital=s["capital"]),
                                              kicker="Lịch sử", id="hieusuat") + "</div>")
    parts.append('<div class="mt">' + _holdings(account, s, book, state, site, day) + "</div>")
    rights = _rights(account)
    if rights:
        parts.append('<div class="mt">' + rights + "</div>")
    parts.append('<div class="mt">' + _details(s, book) + _trade_form(book) + _journal(entries, ma, loai) + _cash_form(book)
                 + _levels_form(book, state.get("levels") or {}) + _assistant(account, book, site)
                 + _report(account, fresh_report, can_send) + _import(account) + _thresholds(account) + "</div>")
    parts.append(_reset(has_entries))
    return "".join(parts)


# ─── routes ──────────────────────────────────────────────────────────────────

def register(app, account: ts.TaiSan, *, guard, post_guard, show_bot=lambda: True, notify=None) -> None:
    """The /taisan routes on ``app`` (``guard``: the password; ``post_guard``: it and same-origin;
    ``notify``: sends a text to Telegram and Zoom, for "Gửi ngay")."""
    from fastapi import Depends

    app.state.taisan = account

    def render(body: str, refresh: int | None = None) -> HTMLResponse:
        return HTMLResponse(ui.page("Danh mục", body, active="taisan", refresh=refresh, show_bot=show_bot()))

    @app.get("/taisan", dependencies=[Depends(guard)])
    def taisan_page(msg: str = "", ma: str = "", loai: str = "", baocao: str = "") -> HTMLResponse:
        symbol = ma.strip().upper()[:10] if ma.strip().isalnum() else ""
        return render(page_body(account, msg[:2000], ma=symbol, loai=loai if loai in _FILTER_KINDS else "",
                                fresh_report=baocao == "1", can_send=notify is not None),
                      refresh=20 if account.rebuilding else None)

    def run(action, anchor: str = "") -> RedirectResponse:
        try:
            reply = action()
        except (ValueError, OverflowError) as exc:   # LedgerError, the Excel and the number parsers' errors
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

    @app.post("/taisan/kiemtra", dependencies=post_guard)
    def taisan_check(symbol: str = Form(""), qty: str = Form(""), price: str = Form(""), stop: str = Form(""),
                     day: str = Form("")):
        def act():
            sym = _code(symbol)
            count = _optional(qty, "Số cổ phiếu")
            n = int(count) if count else None
            check = kyluat.check_buy(account, sym, _optional(price, "Giá"), n, _optional(stop, "Cắt lỗ"),
                                     day=day.strip() or None)
            kyluat.record_check(account, check, sym, n)
            return check["text"]
        return run(act, "ghi")

    @app.post("/taisan/muctieu", dependencies=post_guard)
    def taisan_goal(date: str = Form(""), action: str = Form("")):
        return run(lambda: kyluat.set_goal(account, "xoa" if action == "clear" else date), "muctieu")

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
        return run(act, "tien")

    @app.post("/taisan/khoptien", dependencies=post_guard)
    def taisan_match(actual: str = Form(...)):
        return run(lambda: account.match_cash(_number(actual, "Số dư"), source="web"), "tien")

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
        return run(act, "nguong")

    @app.post("/taisan/huy", dependencies=post_guard)
    def taisan_undo(id: str = Form(...)):  # noqa: A002 - the form field's name
        def act():
            if not id.strip().isdigit():
                raise ts.LedgerError("Số dòng không hợp lệ.")
            return account.undo(int(id), source="web")
        return run(act, "nhatky")

    @app.post("/taisan/lammoi", dependencies=post_guard)
    def taisan_refresh():
        """Live prices asked again; the closes behind the small charts and the risk figures in the background."""
        account._forget_live()

        def work():
            try:
                account.refresh_analytics()
            except Exception:  # noqa: BLE001 - the page shows the last figures
                logger.exception("Tài sản: refresh failed")
        threading.Thread(target=work, name="taisan-refresh", daemon=True).start()
        return _back("🔄 Đã lấy lại giá; biểu đồ nhỏ và chỉ số rủi ro cập nhật sau ít giây (tải lại trang).", "danhmuc")

    @app.post("/taisan/baocao", dependencies=post_guard)
    def taisan_send_report():
        def act():
            if notify is None:
                raise ts.LedgerError("Chưa nối kênh gửi (Telegram/Zoom).")
            text = account.report_text(account.report_day())

            def send():
                try:
                    notify(text)
                except Exception:  # noqa: BLE001
                    logger.exception("Tài sản: report not sent")
            threading.Thread(target=send, name="taisan-send", daemon=True).start()
            return "📤 Đang gửi báo cáo Tài sản qua Telegram và Zoom."
        return run(act, "baocao")

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
        return run(act, "xemtruoc")

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
