"""Các trang web công khai phục vụ đăng ký app Zoom (Local Test / Marketplace) – theo cách repo lananh làm
(/privacy, /terms, /data-deletion) và bổ sung những trang Zoom yêu cầu riêng:

  /zoom/oauth/callback   OAuth Redirect URL – Zoom chuyển về đây sau khi bấm "Add App Now" / "Allow"
  /zoom/install          Nút "Thêm vào Zoom" – chuyển sang trang cấp quyền của Zoom
  /privacy               Privacy Policy URL
  /terms                 Terms of Use URL
  /support               Support URL
  /docs                  Documentation URL (hướng dẫn sử dụng)
  /data-deletion         Hướng dẫn xoá dữ liệu
  /zoom                  Trang cấu hình (cần đăng nhập dashboard): các URL cần dán vào Marketplace + kiểm tra cấu hình

Mọi trang song ngữ Việt – Anh (người duyệt Zoom đọc tiếng Anh).
"""
import html
import logging
import secrets
import time
from urllib.parse import urlencode
import requests
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from .config import cfg

log = logging.getLogger("pages")

APP_NAME = "OKX AI Trading Bot"
UPDATED = "19/10/2026"

CSS_EXTRA = """
.page{max-width:860px;margin:0 auto;padding:0 16px 48px}
article{background:var(--surface);border:1px solid var(--line);border-radius:18px;padding:26px 28px;box-shadow:var(--shadow);position:relative}
article::before{content:"";position:absolute;left:24px;right:24px;top:0;height:1px;background:var(--hair)}
@media(max-width:560px){article{padding:20px 18px}}
article h1{font-size:30px;margin:0 0 .3rem}article h2{margin-top:1.7rem;font:600 19px/1.3 "Playfair Display",Georgia,serif}
article p,article li{color:var(--ink-2)}
.lang{border-top:1px solid var(--line);margin-top:34px;padding-top:24px}
.box{background:var(--surface-2);border:1px solid var(--line);border-radius:12px;padding:12px 16px;margin:14px 0;font-size:14px}
.btn.ghost{background:transparent;color:var(--ink);border-color:var(--line-strong)}
a.btn{text-decoration:none}a.btn:not(.ghost){background:linear-gradient(135deg,var(--gold),var(--gold-2));color:var(--gold-ink)}
button.btn:not(.ghost){background:linear-gradient(135deg,var(--gold),var(--gold-2));color:var(--gold-ink)}
code,.url{font:13px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:var(--surface-2);border:1px solid var(--line);border-radius:8px;padding:2px 6px;word-break:break-all}
article .row{padding:10px 0}.row b{display:block;font-size:13px}.row .url{display:block;margin-top:4px}
.ok{color:var(--up);font-weight:700}.no{color:var(--down);font-weight:700}
.pubnav{display:flex;gap:2px;flex-wrap:wrap;margin-left:auto}
.pubnav a{color:var(--ink-2);font-size:13.5px;font-weight:500;padding:8px 11px;border-radius:10px}
.pubnav a:hover{background:var(--surface-2);text-decoration:none;color:var(--ink)}
.pubnav a[aria-current=page]{color:var(--ink);background:var(--surface);border:1px solid var(--line)}
.pubfoot{text-align:center;color:var(--muted);font-size:12px;margin-top:22px}
"""


NAV = [("/docs", "Hướng dẫn"), ("/support", "Hỗ trợ"), ("/privacy", "Quyền riêng tư"),
       ("/terms", "Điều khoản"), ("/data-deletion", "Xoá dữ liệu")]


def base_url(request: Request):
    if cfg.PUBLIC_URL:
        return cfg.PUBLIC_URL.rstrip("/")
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


def contact_vi():
    return (f'email <a href="mailto:{html.escape(cfg.SUPPORT_EMAIL)}">{html.escape(cfg.SUPPORT_EMAIL)}</a>'
            if cfg.SUPPORT_EMAIL else "kênh liên hệ do chủ ứng dụng công bố trên trang Zoom App Marketplace của ứng dụng")


def contact_en():
    return (f'email <a href="mailto:{html.escape(cfg.SUPPORT_EMAIL)}">{html.escape(cfg.SUPPORT_EMAIL)}</a>'
            if cfg.SUPPORT_EMAIL else "the contact details published on this app's Zoom App Marketplace listing")


def page(title, body, current=""):
    """Trang công khai (Zoom yêu cầu) – cùng hệ thống thiết kế với dashboard."""
    from . import ui
    nav = "".join(f'<a href="{h}"{" aria-current=page" if h == current else ""}>{t}</a>' for h, t in NAV)
    return HTMLResponse(
        '<!doctype html><html lang="vi"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{html.escape(title)} · {APP_NAME}</title>{ui.THEME_BOOT}{ui.FONTS}'
        f'<style>{ui.STYLE}{CSS_EXTRA}</style></head><body>'
        '<header class="top"><div class="top-in"><a class="brand" href="/docs"><span class="mark" aria-hidden="true">OKX</span>'
        f'<span class="brand-name">{APP_NAME}<small>Zoom Team Chat</small></span></a>'
        f'<nav class="pubnav" aria-label="Trang">{nav}</nav>'
        f'<button class="icon-btn" id="theme-toggle" type="button" aria-label="Đổi giao diện sáng/tối">{ui.MOON}{ui.SUN}</button>'
        f'</div></header><main id="main" style="padding-top:26px"><div class="page"><article>{body}</article>'
        f'<p class="pubfoot">{APP_NAME} · Trang này không đặt cookie theo dõi và không thu thập dữ liệu người xem.</p>'
        f'</div></main>{ui.THEME_TOGGLE}</body></html>')


# ---------------------------------------------------------------- nội dung các trang
def privacy_body():
    return f"""
<h1>Chính sách quyền riêng tư</h1><p class="muted">Cập nhật lần cuối: {UPDATED}</p>
<p>{APP_NAME} là công cụ cá nhân do chủ ứng dụng tự vận hành để theo dõi và điều khiển một bot giao dịch tiền mã hoá.
Ứng dụng Zoom (Team Chat chatbot) chỉ dùng để chủ ứng dụng nhận thông báo và gửi lệnh điều khiển bot.</p>
<h2>1. Dữ liệu được xử lý</h2>
<ul><li>Định danh Zoom của người nhắn cho bot: JID người dùng, tên hiển thị, Account ID, JID kênh (nếu nhắn trong kênh).</li>
<li>Nội dung các tin nhắn/lệnh gửi <b>trực tiếp cho bot</b> (ví dụ <code>status</code>, <code>pause</code>). Bot không đọc cuộc họp, ghi âm, danh bạ hay tin nhắn không gửi cho bot.</li>
<li>Thông tin kỹ thuật: thời điểm nhận sự kiện, mã sự kiện để chống xử lý trùng.</li></ul>
<h2>2. Mục đích sử dụng</h2>
<p>Xác thực người được phép điều khiển bot (ghép đôi đúng 1 JID), thực thi lệnh và gửi lại kết quả, gửi thông báo giao dịch tới người đã ghép đôi.</p>
<h2>3. Chia sẻ</h2>
<p>Không bán, không chia sẻ dữ liệu cho bên thứ ba để quảng cáo. Dữ liệu chỉ được gửi tới Zoom (để trả lời tin nhắn qua Zoom API) và nhà cung cấp hạ tầng lưu trữ ứng dụng.</p>
<h2>4. Lưu trữ và bảo mật</h2>
<p>Lưu: thông tin ghép đôi (JID, tên, Account ID) cho tới khi gỡ ghép đôi hoặc gỡ ứng dụng; nhật ký hoạt động (có thể chứa nội dung lệnh) tự xoá sau 14 ngày.
Mọi yêu cầu từ Zoom được xác thực chữ ký HMAC; mã truy cập Zoom chỉ giữ trong bộ nhớ, không ghi ra đĩa.</p>
<h2>5. Xoá dữ liệu</h2>
<p>Gỡ ứng dụng khỏi Zoom sẽ tự động xoá thông tin ghép đôi. Xem chi tiết tại <a href="/data-deletion">/data-deletion</a>.</p>
<h2>6. Liên hệ</h2><p>Mọi câu hỏi về quyền riêng tư: {contact_vi()}.</p>
<div class="lang">
<h1>Privacy Policy</h1><p class="muted">Last updated: October 19, 2026</p>
<p>{APP_NAME} is a personal tool operated by its owner to monitor and control a cryptocurrency trading bot.
The Zoom app (Team Chat chatbot) is used only so the owner can receive notifications and send control commands to the bot.</p>
<h2>Data processed</h2>
<ul><li>Zoom identifiers of whoever messages the bot: user JID, display name, account ID, channel JID (when used in a channel).</li>
<li>The text of messages/commands sent <b>directly to the bot</b>. The app does not access meetings, recordings, contacts or any message not addressed to the bot.</li>
<li>Technical metadata: event time and event ID (for de-duplication).</li></ul>
<h2>Purpose</h2><p>To authorize the single paired user, execute their commands, reply with results and deliver trading notifications to that user.</p>
<h2>Sharing</h2><p>No data is sold or shared for advertising. Data is sent only to Zoom (to reply via the Zoom API) and to the hosting provider that runs the app.</p>
<h2>Retention and security</h2><p>Pairing data (JID, name, account ID) is kept until unpaired or the app is uninstalled; activity logs (which may contain command text) are deleted automatically after 14 days.
All Zoom requests are verified with HMAC signatures; Zoom access tokens are kept in memory only.</p>
<h2>Deletion</h2><p>Uninstalling the app from Zoom automatically deletes the pairing data. See <a href="/data-deletion">/data-deletion</a>.</p>
<h2>Contact</h2><p>{contact_en()}.</p></div>"""


def terms_body():
    return f"""
<h1>Điều khoản sử dụng</h1><p class="muted">Cập nhật lần cuối: {UPDATED}</p>
<h2>1. Phạm vi</h2><p>{APP_NAME} là công cụ cá nhân để chủ ứng dụng giám sát và điều khiển bot giao dịch của chính mình qua web, Telegram và Zoom Team Chat. Chỉ người đã được chủ ứng dụng ghép đôi mới điều khiển được bot.</p>
<h2>2. Không phải tư vấn tài chính</h2><p>Mọi tín hiệu, phân tích AI và thông báo chỉ mang tính kỹ thuật/tham khảo, không phải lời khuyên đầu tư. Giao dịch hợp đồng tương lai có đòn bẩy có rủi ro mất toàn bộ vốn; người dùng tự chịu trách nhiệm về quyết định của mình.</p>
<h2>3. Giới hạn dịch vụ</h2><p>Dịch vụ phụ thuộc vào sàn giao dịch, Zoom, Telegram, nhà cung cấp AI và hạ tầng bên thứ ba; không đảm bảo hoạt động liên tục hoặc không có lỗi.</p>
<h2>4. Bảo mật</h2><p>Không chia sẻ mật khẩu dashboard, API key sàn hoặc thông tin xác thực Zoom. Chủ ứng dụng có thể gỡ ghép đôi/thu hồi quyền bất cứ lúc nào.</p>
<h2>5. Quyền riêng tư</h2><p>Xem <a href="/privacy">Chính sách quyền riêng tư</a> và <a href="/data-deletion">Hướng dẫn xoá dữ liệu</a>.</p>
<div class="lang"><h1>Terms of Use</h1><p class="muted">Last updated: October 19, 2026</p>
<h2>Scope</h2><p>{APP_NAME} is a personal tool that lets its owner monitor and control their own trading bot via web, Telegram and Zoom Team Chat. Only the user paired by the owner can control the bot.</p>
<h2>No financial advice</h2><p>All signals, AI analysis and notifications are technical information only, not investment advice. Leveraged futures trading can result in total loss of capital; users are solely responsible for their decisions.</p>
<h2>Service limitations</h2><p>The service depends on the exchange, Zoom, Telegram, AI providers and third-party hosting; continuous or error-free operation is not guaranteed.</p>
<h2>Security</h2><p>Do not share dashboard passwords, exchange API keys or Zoom credentials. The owner may unpair or revoke access at any time.</p>
<h2>Privacy</h2><p>See the <a href="/privacy">Privacy Policy</a> and <a href="/data-deletion">Data Deletion</a> instructions.</p></div>"""


def deletion_body():
    return f"""
<h1>Hướng dẫn xoá dữ liệu</h1><p class="muted">Cập nhật lần cuối: {UPDATED}</p>
<h2>Cách 1 – Gỡ ứng dụng (tự động)</h2><p>Trong Zoom: <b>Zoom App Marketplace → Manage → Added Apps → {APP_NAME} → Remove</b>. Zoom gửi sự kiện huỷ cấp quyền, ứng dụng xoá ngay thông tin ghép đôi (JID, tên, Account ID).</p>
<h2>Cách 2 – Gỡ ghép đôi</h2><p>Chủ ứng dụng gõ <code>/zoomxoa</code> trên Telegram để xoá thông tin ghép đôi Zoom.</p>
<h2>Cách 3 – Yêu cầu qua liên hệ</h2><p>Liên hệ {contact_vi()}, ghi rõ JID hoặc email Zoom. Không gửi mật khẩu hay mã OTP. Yêu cầu hợp lệ được xử lý trong tối đa 7 ngày.</p>
<h2>Dữ liệu còn lại</h2><p>Nhật ký hoạt động (có thể chứa nội dung lệnh) tự xoá sau 14 ngày. Tin nhắn đã gửi trong Zoom nằm trên hệ thống của Zoom và cần xoá trực tiếp trong Zoom.</p>
<div class="lang"><h1>Data Deletion</h1><p class="muted">Last updated: October 19, 2026</p>
<h2>Option 1 – Uninstall (automatic)</h2><p>In Zoom: <b>Zoom App Marketplace → Manage → Added Apps → {APP_NAME} → Remove</b>. Zoom sends a deauthorization event and the app immediately deletes the pairing data (JID, name, account ID).</p>
<h2>Option 2 – Unpair</h2><p>The owner sends <code>/zoomxoa</code> on Telegram to delete the Zoom pairing.</p>
<h2>Option 3 – Request</h2><p>Contact {contact_en()} with your Zoom JID or email. Never send passwords or OTP codes. Valid requests are handled within 7 days.</p>
<h2>Remaining data</h2><p>Activity logs (which may contain command text) are deleted automatically after 14 days. Messages already sent in Zoom are stored by Zoom and must be deleted in Zoom.</p></div>"""


COMMANDS = [("status", "Vốn, lệnh đang mở, lượt quét gần nhất", "Balance, open positions, last scan"),
            ("trades", "10 lệnh gần nhất", "Last 10 trades"),
            ("signals", "Tín hiệu & quyết định AI", "Signals and AI decisions"),
            ("ai", "Lượt gọi từng model AI", "AI model usage"),
            ("learn", "Trạng thái tự học", "Self-learning status"),
            ("btc", "Bối cảnh BTC", "BTC context"),
            ("pause / resume", "Tạm dừng / tiếp tục mở lệnh mới", "Pause / resume new entries"),
            ("close", "Đóng tất cả lệnh đang mở", "Close all open positions")]


def docs_body():
    rows = "".join(f"<tr><td><code>{c}</code></td><td>{vi}</td><td class=muted>{en}</td></tr>" for c, vi, en in COMMANDS)
    return f"""
<h1>Hướng dẫn sử dụng</h1>
<p>{APP_NAME} gửi thông báo giao dịch và nhận lệnh điều khiển bot qua Zoom Team Chat.</p>
<h2>1. Cài ứng dụng</h2><p>Bấm <a class="btn" href="/zoom/install" style="padding:6px 12px;font-size:13px">Thêm vào Zoom</a> và cho phép quyền. Sau đó mở Zoom → Team Chat → mục <b>Apps</b> → chọn <b>{APP_NAME}</b>.</p>
<h2>2. Ghép đôi</h2><p>Nhắn bất kỳ lệnh nào cho bot (vd <code>status</code>). Lần đầu bot báo cho chủ ứng dụng qua Telegram kèm JID của bạn; chủ ứng dụng gõ <code>/zoompair &lt;JID&gt;</code> để cấp quyền. Chỉ 1 tài khoản Zoom được điều khiển bot.</p>
<h2>3. Lệnh</h2><p>Gõ trực tiếp trong chat với bot, hoặc trong kênh: <code>/okxbot status</code>.</p>
<table style="width:100%;border-collapse:collapse;font-size:14px">{rows}</table>
<div class="lang"><h1>Documentation</h1>
<p><b>Install:</b> click <a href="/zoom/install">Add to Zoom</a> and authorize, then open Zoom → Team Chat → Apps → {APP_NAME}.<br>
<b>Pairing:</b> send any command (e.g. <code>status</code>); the owner receives your JID on Telegram and pairs it with <code>/zoompair &lt;JID&gt;</code>. Only one Zoom user can control the bot.<br>
<b>Commands:</b> type them directly in the bot chat, or <code>/okxbot &lt;command&gt;</code> in a channel (see table above).<br>
<b>Uninstall:</b> Zoom App Marketplace → Manage → Added Apps → Remove. Pairing data is deleted automatically.</p></div>"""


def support_body():
    return f"""
<h1>Hỗ trợ</h1>
<p>Liên hệ: {contact_vi()}. Thời gian phản hồi: trong 2 ngày làm việc.</p>
<h2>Câu hỏi thường gặp</h2>
<p><b>Bot không trả lời trên Zoom?</b> Tài khoản Zoom của bạn chưa được ghép đôi – chủ ứng dụng cần gõ <code>/zoompair &lt;JID&gt;</code> trên Telegram.<br>
<b>Muốn ngừng nhận thông báo?</b> Gỡ ứng dụng trong Zoom App Marketplace hoặc nhờ chủ ứng dụng gõ <code>/zoomxoa</code>.<br>
<b>Xoá dữ liệu?</b> Xem <a href="/data-deletion">/data-deletion</a>.</p>
<p>Danh sách lệnh: <a href="/docs">/docs</a>.</p>
<div class="lang"><h1>Support</h1>
<p>Contact: {contact_en()}. Response time: within 2 business days.</p>
<p><b>The bot does not reply?</b> Your Zoom account is not paired yet – the owner must run <code>/zoompair &lt;JID&gt;</code> on Telegram.<br>
<b>Stop notifications?</b> Remove the app in Zoom App Marketplace, or ask the owner to run <code>/zoomxoa</code>.<br>
<b>Delete data?</b> See <a href="/data-deletion">/data-deletion</a>. Commands: <a href="/docs">/docs</a>.</p></div>"""


# ---------------------------------------------------------------- OAuth
def authorize_url(base, state):
    return "https://zoom.us/oauth/authorize?" + urlencode({
        "response_type": "code", "client_id": cfg.ZOOM_CLIENT_ID,
        "redirect_uri": base + "/zoom/oauth/callback", "state": state})


def exchange_code(code, redirect_uri):
    r = requests.post("https://zoom.us/oauth/token", timeout=15,
                      data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
                      auth=(cfg.ZOOM_CLIENT_ID, cfg.ZOOM_CLIENT_SECRET))
    try:
        data = r.json()
    except Exception:
        data = {"error": r.text[:200]}
    return r.status_code, data


def register(app, bot, authed):
    """Gắn các route vào FastAPI app của dashboard. authed(request) → bool (đăng nhập dashboard)."""

    @app.get("/privacy", response_class=HTMLResponse)
    def privacy():
        return page("Chính sách quyền riêng tư / Privacy Policy", privacy_body(), "/privacy")

    @app.get("/terms", response_class=HTMLResponse)
    def terms():
        return page("Điều khoản sử dụng / Terms of Use", terms_body(), "/terms")

    @app.get("/data-deletion", response_class=HTMLResponse)
    def deletion():
        return page("Xoá dữ liệu / Data Deletion", deletion_body(), "/data-deletion")

    @app.get("/docs", response_class=HTMLResponse)
    def docs():
        return page("Hướng dẫn / Documentation", docs_body(), "/docs")

    @app.get("/support", response_class=HTMLResponse)
    def support():
        return page("Hỗ trợ / Support", support_body(), "/support")

    @app.get("/zoom/install")
    def install(request: Request):
        if not cfg.ZOOM_CLIENT_ID:
            return page("Chưa cấu hình", "<h1>Chưa cấu hình Zoom</h1><p>Thiếu <code>ZOOM_CLIENT_ID</code>.</p>")
        state = secrets.token_urlsafe(16)
        states = {k: v for k, v in (bot.store.get("zoom_oauth_states") or {}).items() if time.time() - v < 900}
        states[state] = time.time()
        bot.store.set("zoom_oauth_states", states)
        return RedirectResponse(authorize_url(base_url(request), state), status_code=302)

    @app.get("/zoom/oauth/callback", response_class=HTMLResponse)
    def callback(request: Request, code: str = "", state: str = "", error: str = "", error_description: str = ""):
        if error:
            log.warning("Zoom OAuth bị từ chối: %s %s", error, error_description)
            return page("Chưa cài được", f"""<h1>Chưa cài được ứng dụng</h1>
<p>Zoom trả về lỗi: <code>{html.escape(error)}</code> {html.escape(error_description)}</p>
<p><a class="btn ghost" href="/zoom/install">Thử lại</a></p>""")
        if not code:
            return page("Zoom OAuth callback", f"""<h1>Zoom OAuth Redirect URL</h1>
<p>Đây là địa chỉ Zoom chuyển về sau khi cài ứng dụng. Hãy cài từ Zoom Marketplace hoặc bấm nút dưới đây.</p>
<p class="muted">This is the OAuth redirect endpoint of {APP_NAME}.</p><p><a class="btn" href="/zoom/install">Thêm vào Zoom / Add to Zoom</a></p>""")
        states = bot.store.get("zoom_oauth_states") or {}
        if state and state not in states:
            log.warning("Zoom OAuth: state không khớp (có thể link cũ) – vẫn thử đổi mã")
        if state in states:
            states.pop(state, None)
            bot.store.set("zoom_oauth_states", states)
        redirect_uri = base_url(request) + "/zoom/oauth/callback"
        inst = bot.store.get("zoom_install") or {}
        if inst.get("code") == code[-12:]:
            # trình duyệt/Zoom gọi callback lần 2 với cùng mã (mã chỉ dùng được 1 lần) → coi như đã cài
            return page("Đã cài thành công", f"""<h1>✅ Đã cài {APP_NAME} vào Zoom</h1>
<p>Mở Zoom → Team Chat → Apps → <b>{APP_NAME}</b>, nhắn <code>status</code>.</p><p><a class="btn" href="/docs">Xem hướng dẫn</a></p>""")
        status, data = (0, {"error": "ZOOM_CLIENT_ID/SECRET chưa cấu hình"})
        if cfg.ZOOM_CLIENT_ID and cfg.ZOOM_CLIENT_SECRET:
            try:
                status, data = exchange_code(code, redirect_uri)
            except Exception as e:
                status, data = 0, {"error": str(e)}
        ok = status == 200 and "access_token" in data
        if ok:
            # Không lưu access/refresh token: chatbot dùng client_credentials riêng.
            bot.store.set("zoom_install", {"ts": time.time(), "scope": data.get("scope", ""), "code": code[-12:]})
            log.info("Zoom: ứng dụng đã được cài (scope: %s)", data.get("scope", ""))
            try:
                bot.notify.send("✅ Zoom: ứng dụng đã được cài vào tài khoản Zoom. Mở Team Chat → Apps → "
                                f"{APP_NAME} và nhắn 'status' để ghép đôi.")
            except Exception:
                pass
            return page("Đã cài thành công", f"""<h1>✅ Đã cài {APP_NAME} vào Zoom</h1>
<p>Mở Zoom → <b>Team Chat</b> → mục <b>Apps</b> → chọn <b>{APP_NAME}</b>, nhắn <code>status</code>.</p>
<p>Lần đầu bot sẽ báo JID của bạn cho chủ ứng dụng qua Telegram; chủ ứng dụng gõ <code>/zoompair &lt;JID&gt;</code> để cấp quyền.</p>
<p><a class="btn" href="zoommtg://zoom.us/">Mở Zoom</a> &nbsp; <a class="btn ghost" href="/docs">Xem hướng dẫn</a></p>
<div class="lang"><p class="muted">The app has been installed. Open Zoom → Team Chat → Apps → {APP_NAME} and send <code>status</code>.</p></div>""")
        if data.get("error") == "invalid_grant" and time.time() - float(inst.get("ts") or 0) < 600:
            log.info("Zoom OAuth: mã đã dùng ở lần gọi trước (đã cài thành công) – bỏ qua")
            return page("Đã cài thành công", f"""<h1>✅ Đã cài {APP_NAME} vào Zoom</h1>
<p>Mở Zoom → Team Chat → Apps → <b>{APP_NAME}</b>, nhắn <code>status</code>.</p><p><a class="btn" href="/docs">Xem hướng dẫn</a></p>""")
        log.error("Zoom OAuth đổi mã thất bại (%s): %s", status, data)
        reason = html.escape(str(data.get("reason") or data.get("error_description") or data.get("error") or data))
        return page("Lỗi cài đặt", f"""<h1>Chưa hoàn tất cài đặt</h1>
<p>Zoom đã chuyển về nhưng đổi mã xác thực thất bại (HTTP {status}): <code>{reason}</code></p>
<div class="box">Kiểm tra: <b>OAuth Redirect URL</b> trên Marketplace phải đúng <code>{html.escape(redirect_uri)}</code>
và nằm trong <b>OAuth Allow List</b>; <code>ZOOM_CLIENT_ID</code>/<code>ZOOM_CLIENT_SECRET</code> phải là cặp của
<b>Development</b> nếu đang Local Test (Production nếu đã publish). Biến <code>PUBLIC_URL</code> nên là <code>{html.escape(base_url(request))}</code>.</div>
<p><a class="btn ghost" href="/zoom/install">Thử lại</a></p>""")

    @app.post("/zoom/pair")
    def zoom_pair(request: Request):
        if not authed(request):
            return RedirectResponse("/", status_code=303)
        pend = bot.store.get("zoom_pending") or {}
        z = getattr(bot, "zoom", None)
        if z and pend.get("jid"):
            z.pair(pend["jid"], pend.get("name", ""), pend.get("account_id", ""), pend.get("reply_jid", ""))
            bot.store.set("zoom_pending", {})
            log.info("Zoom: đã ghép đôi %s qua trang /zoom", pend["jid"])
            try:
                z.q.put_nowait(("reply", "✅ Đã ghép đôi. Gõ status để xem tình trạng bot.",
                                {"to_jid": pend.get("reply_jid") or pend["jid"], "user_jid": pend["jid"],
                                 "account_id": pend.get("account_id")}))
            except Exception:
                pass
        return RedirectResponse("/zoom", status_code=303)

    @app.get("/zoom", response_class=HTMLResponse)
    def zoom_setup(request: Request):
        if not authed(request):
            return RedirectResponse("/", status_code=302)
        b = base_url(request)
        z = getattr(bot, "zoom", None)
        p = z.pairing() if z else {}
        inst = bot.store.get("zoom_install") or {}

        def chk(v):
            return '<span class="ok">✓</span>' if v else '<span class="no">✗</span>'

        urls = [("OAuth Redirect URL", b + "/zoom/oauth/callback"),
                ("OAuth Allow List", b + "/zoom/oauth/callback"),
                ("Bot endpoint URL (Development & Production)", b + "/webhook/zoom"),
                ("Event notification endpoint URL", b + "/webhook/zoom"),
                ("Deauthorization Notification Endpoint", b + "/webhook/zoom"),
                ("Home URL / Direct landing URL", b + "/docs"),
                ("Privacy Policy URL", b + "/privacy"),
                ("Terms of Use URL", b + "/terms"),
                ("Support URL", b + "/support"),
                ("Documentation URL", b + "/docs")]
        rows = "".join(f'<div class="row"><div><b>{n}</b><span class="url">{html.escape(u)}</span></div>'
                       f'<button class="btn ghost" style="padding:6px 10px;font-size:12px" onclick="navigator.clipboard.writeText(\'{html.escape(u)}\');this.textContent=\'Đã chép\'">Chép</button></div>' for n, u in urls)
        checks = [("PUBLIC_URL", bool(cfg.PUBLIC_URL)), ("ZOOM_CLIENT_ID", bool(cfg.ZOOM_CLIENT_ID)),
                  ("ZOOM_CLIENT_SECRET", bool(cfg.ZOOM_CLIENT_SECRET)), ("ZOOM_BOT_JID", bool(cfg.ZOOM_BOT_JID)),
                  ("ZOOM_SECRET_TOKEN", bool(cfg.ZOOM_SECRET_TOKEN)), ("ZOOM_ACCOUNT_ID", bool(cfg.ZOOM_ACCOUNT_ID)),
                  ("SUPPORT_EMAIL", bool(cfg.SUPPORT_EMAIL))]
        crow = "".join(f"<div class='row'><div><b>{k}</b></div><div>{chk(v)}</div></div>" for k, v in checks)
        pend = bot.store.get("zoom_pending") or {}
        pend_html = ""
        if pend.get("jid") and pend.get("jid") != p.get("jid"):
            pend_html = f"""<div class="box"><b>Có người vừa nhắn bot nhưng chưa được ghép đôi</b><br>
{html.escape(pend.get('name') or '')} · <span class="url">{html.escape(pend['jid'])}</span> · {time.strftime('%d/%m %H:%M', time.localtime(pend['ts']))}
<form method="post" action="/zoom/pair" style="margin-top:10px"><button class="btn">Ghép đôi tài khoản này</button></form></div>"""
        reqs = bot.store.get("zoom_requests") or []
        req_html = "".join(f"<div class='row'><div><b>{time.strftime('%d/%m %H:%M:%S', time.localtime(r['ts']))} · {html.escape(r['event'])}</b>"
                           f"<span class='muted'>{html.escape(r.get('user') or '')} {html.escape(r.get('cmd') or '')} — {html.escape(r['reason'])}</span></div>"
                           f"<div>{chk(r['ok'])}</div></div>" for r in reqs) or \
            "<p class='box'>Chưa nhận được request nào từ Zoom. Nghĩa là Zoom chưa gọi tới bot: kiểm tra <b>Bot endpoint URL</b> trong Features → Surface → Team Chat Subscription của đúng app này (bản Development khi Local Test), và anh đang nhắn đúng bot của app này chứ không phải bot lananh.</p>"
        body = f"""<h1>Cấu hình Zoom</h1>
<p class="muted"><a href="/zoom">Tải lại</a> · Chỉ hiện khi đã đăng nhập dashboard. Dán các URL dưới đây vào app trên <a href="https://marketplace.zoom.us/user/build" target="_blank">Zoom Marketplace</a>.</p>
<h2>Trạng thái</h2>
<div class="row"><div><b>Đã cài vào Zoom (OAuth)</b></div><div>{chk(inst)} <span class="muted">{time.strftime('%d/%m %H:%M', time.localtime(inst['ts'])) if inst else ''}</span></div></div>
<div class="row"><div><b>JID đã ghép đôi</b><span class="muted">{html.escape((p.get('name') or '') + ' ' + (p.get('jid') or 'chưa có'))}</span></div><div>{chk(p.get('jid'))}</div></div>
{crow}
{pend_html}
<h2>Request Zoom gần nhất</h2>{req_html}
<h2>URL cần dán vào Marketplace</h2>{rows}
<h2>Các bước Local Test</h2>
<ol><li>Basic Information → dán <b>OAuth Redirect URL</b> và <b>OAuth Allow List</b>.</li>
<li>Features → Surface → bật <b>Team Chat</b> → Team Chat Subscription: dán <b>Bot endpoint URL</b>, slash command <code>okxbot</code>. Chép <b>Bot JID</b> (Development) → <code>ZOOM_BOT_JID</code>.</li>
<li>Features → Access → <b>Secret Token</b> → <code>ZOOM_SECRET_TOKEN</code>. Bật Event Subscription với URL ở trên, thêm sự kiện <i>App Deauthorized</i>, bấm <b>Validate</b>.</li>
<li>Basic Information → App Credentials (Development) → <code>ZOOM_CLIENT_ID</code>, <code>ZOOM_CLIENT_SECRET</code>. <code>ZOOM_ACCOUNT_ID</code>: để trống – bot tự lấy account_id Zoom gửi kèm tin nhắn (không dùng "Account No." ở zoom.us/account).</li>
<li>Scopes: <code>imchat:bot</code> (Zoom tự thêm khi bật chatbot).</li>
<li>Restart bot, rồi Local Test → <b>Add App Now</b> (hoặc nút dưới) → Allow → về trang callback báo thành công.</li>
<li>Zoom → Team Chat → Apps → nhắn <code>status</code> → Telegram báo JID → gõ <code>/zoompair &lt;JID&gt;</code>.</li></ol>
<p><a class="btn" href="/zoom/install">Thêm vào Zoom (Local Test)</a> &nbsp; <a class="btn ghost" href="/">Về dashboard</a></p>"""
        return page("Cấu hình Zoom", body)
