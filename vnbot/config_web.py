"""The Cài đặt page (/caidat): the bot's parameters in sections, each a label column
beside a panel with its own Lưu; the secrets as set / not set (``vnbot.config``)."""

from __future__ import annotations

import html
import logging
from urllib.parse import quote

from fastapi import Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from vnbot import config, ui

logger = logging.getLogger(__name__)

_SOURCE_VI = {"web": "đặt trên web", "env": "biến môi trường", "default": "mặc định"}


def _e(text) -> str:
    return html.escape(str(text or ""))


def _hint(f: config.Field, saved: dict[str, str]) -> str:
    where = _SOURCE_VI[config.source(f, saved)]
    bits = [f.help] if f.help else []
    default = f.default if f.default else "trống"
    if f.kind == "flag":
        default = "bật" if f.default == "1" else "tắt"
    elif f.kind == "choice":
        default = dict(f.options).get(f.default, f.default)
    tail = f"Mặc định {default}" + (f" {f.unit}" if f.unit and f.kind in ("int", "float") else "") + f" · {where}"
    if f.restart:
        tail += " · có hiệu lực sau khi khởi động lại"
    bits.append(tail)
    return " ".join(bits)


def _field(f: config.Field, saved: dict[str, str]) -> str:
    ident = f"set-{f.key.lower()}"
    value = config.effective(f)
    tag = f'<span class="tag">{_e(f.key)}</span>'
    if f.kind == "flag":
        return (f'<label class="sw-row" for="{ident}"><span><b>{_e(f.label)} {tag}</b><small>{_e(_hint(f, saved))}</small></span>'
                f'<input class="switch" type="checkbox" role="switch" id="{ident}" name="{_e(f.key)}" value="1"'
                f'{" checked" if value == "1" else ""}></label>')
    if f.kind == "choice":
        options = "".join(f'<option value="{_e(v)}"{" selected" if v == value else ""}>{_e(label)}</option>' for v, label in f.options)
        control = f'<select id="{ident}" name="{_e(f.key)}">{options}</select>'
    else:
        mode = {"int": ' inputmode="numeric"', "float": ' inputmode="decimal"', "ids": ' inputmode="numeric"'}.get(f.kind, "")
        shown = ui.money(int(value)) if f.kind == "int" and f.key == "PAPER_CAPITAL" and value else value
        control = (f'<input id="{ident}" name="{_e(f.key)}" value="{_e(shown)}"{mode} maxlength="{config.MAX_LEN}" autocomplete="off"'
                   + (f' placeholder="{_e(f.default) if f.default else "trống"}"' if not value else "") + ">")
    return (f'<div class="fld"><label class="f" for="{ident}">{_e(f.label)} {tag}</label>{control}'
            f'<div class="hint">{_e(_hint(f, saved))}</div></div>')


def _section(g: config.Group, saved: dict[str, str]) -> str:
    fields = "".join(_field(f, saved) for f in g.fields)
    overridden = [f for f in g.fields if f.key in saved]
    now = ""
    if overridden:
        rows = "".join(f"<div>{_e(f.label)}: <b>{_e(config.effective(f) or 'trống')}</b> "
                       f'<form method="post" action="/caidat/xoa" class="inline"><input type="hidden" name="key" value="{_e(f.key)}">'
                       f'<button class="linkbtn" type="submit" style="color:var(--muted)">về mặc định</button></form></div>'
                       for f in overridden)
        now = f'<div class="now">Đang đặt trên web:{rows}</div>'
    restart = any(f.restart for f in g.fields)
    note = ("Các mục ghi “sau khi khởi động lại” có hiệu lực ở lần chạy sau; phần còn lại có hiệu lực ngay." if restart
            else "Có hiệu lực ngay sau khi lưu.")
    return (f'<section class="set" id="{_e(g.key)}"><div class="set-l"><h2>{_e(g.title)}</h2><p>{_e(g.help)}</p>{now}</div>'
            f'<form class="set-p" method="post" action="/caidat"><input type="hidden" name="group" value="{_e(g.key)}">{fields}'
            f'<div class="set-f"><span class="note">{_e(note)}</span><button class="btn btn-primary" type="submit">Lưu</button></div>'
            "</form></section>")


def _secrets() -> str:
    rows = "".join(
        f'<tr><td>{_e(label)}<span class="tag" style="display:block">{_e(key)}</span></td>'
        f'<td class="num">{"<span class=\"badge b-ok\">đã đặt</span>" if config.secret_set(key) else "<span class=\"badge b-none\">chưa đặt</span>"}</td></tr>'
        for key, label in config.SECRETS)
    table = ui.table([("Khoá / token", ""), ("", "num")], rows, "")
    return (f'<section class="set" id="khoa"><div class="set-l"><h2>Khoá và token</h2><p>Đặt trên Botkeep (Environment) rồi khởi động '
            "lại bot. Trang này không hiện và không lưu giá trị của chúng.</p></div>"
            f'<div class="set-p" style="padding:0 0 4px">{table}</div></section>')


def page_body(data_dir, msg: str = "") -> str:
    saved = config.read(data_dir)
    kind = "err" if msg[:1] in ("⚠", "❌") else "ok" if msg[:1] == "✅" else "info"
    lead = (f"{len(config.FIELDS)} tham số trong {len(config.GROUPS)} nhóm. Giá trị lưu ở đây được ưu tiên hơn biến môi trường "
            "trên Botkeep và giữ qua các lần khởi động lại.")
    jump = " · ".join(f'<a href="#{_e(g.key)}">{_e(g.title)}</a>' for g in config.GROUPS) + ' · <a href="#khoa">Khoá và token</a>'
    return (ui.title("Cài đặt", lead) + (ui.alert(msg, kind) if msg else "")
            + f'<p class="muted small" style="margin:0 0 20px">{jump}</p>'
            + "".join(_section(g, saved) for g in config.GROUPS) + _secrets())


def register(app, data_dir, *, guard, post_guard, show_bot=lambda: True) -> None:
    from fastapi import Depends

    def render(msg: str = "") -> HTMLResponse:
        return HTMLResponse(ui.page("Cài đặt", page_body(data_dir, msg[:600]), active="caidat", show_bot=show_bot()))

    @app.get("/caidat", dependencies=[Depends(guard)])
    def settings_page(msg: str = "") -> HTMLResponse:
        return render(msg)

    @app.post("/caidat", dependencies=post_guard)
    async def settings_save(request: Request):
        form = await request.form()
        data = {k: str(v) for k, v in form.items() if isinstance(v, str)}
        group = data.pop("group", "")
        try:
            changed, restart = config.save(data_dir, group, data)
        except ValueError as exc:
            return RedirectResponse(f"/caidat?msg={quote('⚠️ ' + str(exc))}#{quote(group)}", status_code=303)
        title = next((g.title for g in config.GROUPS if g.key == group), group)
        if not changed:
            msg = f"✅ {title}: không có gì thay đổi."
        else:
            names = ", ".join(f.key for f in changed)
            msg = f"✅ {title}: đã lưu {len(changed)} thay đổi ({names})."
            if restart:
                msg += " Có hiệu lực sau khi khởi động lại bot trên Botkeep."
        logger.info("Web: settings %s saved: %s", group, [f.key for f in changed])
        return RedirectResponse(f"/caidat?msg={quote(msg)}#{quote(group)}", status_code=303)

    @app.post("/caidat/xoa", dependencies=post_guard)
    def settings_clear(key: str = Form("")):
        try:
            f = config.clear(data_dir, key)
        except ValueError as exc:
            return RedirectResponse(f"/caidat?msg={quote('⚠️ ' + str(exc))}", status_code=303)
        msg = f"✅ {f.label}: đã bỏ giá trị đặt trên web" + (", có hiệu lực sau khi khởi động lại." if f.restart else ".")
        logger.info("Web: settings override %s cleared", key)
        return RedirectResponse(f"/caidat?msg={quote(msg)}#{quote(f.group)}", status_code=303)
