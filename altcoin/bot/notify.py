"""Kênh thông báo & điều khiển: Telegram và Zoom Team Chat ngang hàng nhau.

Zoom có 2 phần độc lập, bật cái nào cũng được:
  1) Incoming Webhook (dễ, 5 phút): bot GỬI mọi thông báo vào 1 kênh chat Zoom.
     Biến: ZOOM_WEBHOOK_URL, ZOOM_WEBHOOK_TOKEN
  2) Chatbot (nâng cao): anh GÕ LỆNH trong Zoom (/okxbot status, /okxbot pause...) và bot trả lời.
     Cần tạo app "General" trên Zoom App Marketplace, bật Team Chat → chatbot, Bot endpoint URL phải là HTTPS.
     Biến: ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET, ZOOM_BOT_JID, ZOOM_SECRET_TOKEN, ZOOM_ACCOUNT_ID
     Quyền điều khiển: ghép đôi 1 JID bằng /zoompair trên Telegram (hoặc ZOOM_ALLOWED_USERS)
"""
import hashlib
import hmac
import json
import logging
import queue
import re
import threading
import time
import requests
from .config import cfg

log = logging.getLogger("notify")


class Notifier:
    """Gửi 1 thông báo tới tất cả kênh đang bật. Chỉ leader gửi (tránh trùng khi chạy 2 máy)."""

    def __init__(self, bot):
        self.bot = bot
        self.channels = []

    def add(self, ch):
        self.channels.append(ch)

    def send(self, text):
        log.info("Thông báo: %s", text.replace("\n", " | ")[:200])
        if not getattr(self.bot, "is_leader", True) and "tiếp quản" not in text:
            return
        sw = getattr(self.bot, "switches", None)
        for ch in self.channels:
            if not ch.enabled:
                continue
            key = getattr(ch, "switch_key", None)
            if sw is not None and key and not sw.on(key):
                continue                        # công tắc trên web đang tắt kênh này
            ch.send(text)


class Zoom:
    """Kênh Zoom Team Chat – theo mô hình đã chạy thực tế của repo traique/lananh (channels/zoom.py):
    - Chatbot app (General App + Team Chat Chatbot), xác thực HMAC bằng Secret Token (hoặc Verification Token cũ).
    - Ghép đôi (pairing) đúng 1 JID được điều khiển bot; JID lạ nhắn tới → báo chủ bot qua Telegram 1 lần.
    - Trả lời 1:1 phải gửi về JID người gửi (toJid trong chat 1:1 là JID của chính bot → lỗi 7004).
    - Chuyển markdown sang phương ngữ Zoom, cắt tin > 4096 ký tự, thử lại 3 lần.
    - Thông báo chủ động: gửi tới JID đã ghép đôi; nếu có Incoming Webhook thì gửi vào kênh webhook.
    """
    switch_key = "zoom_notify"
    API = "https://api.zoom.us/v2/im/chat/messages"
    TOKEN_URL = "https://api.zoom.us/oauth/token"
    MAX_CHARS = 4096

    def __init__(self, bot):
        self.bot = bot
        self.webhook_on = bool(cfg.ZOOM_WEBHOOK_URL and cfg.ZOOM_WEBHOOK_TOKEN)
        self.chatbot_on = bool(cfg.ZOOM_CLIENT_ID and cfg.ZOOM_CLIENT_SECRET and cfg.ZOOM_BOT_JID)
        self.enabled = self.webhook_on or self.chatbot_on
        self._token, self._token_exp = None, 0
        self._seen = {}                 # event_id → ts: Zoom có thể gửi lại cùng sự kiện
        self._alerted_at = {}           # JID lạ → lần cuối đã báo chủ bot
        self._notify_fail, self._notify_fail_ts = 0, 0.0   # số lần liên tiếp Zoom từ chối thông báo
        self.q = queue.Queue(maxsize=500)
        if self.enabled:
            threading.Thread(target=self._worker, daemon=True, name="zoom").start()
        if self.chatbot_on and not (cfg.ZOOM_SECRET_TOKEN or cfg.ZOOM_VERIFICATION_TOKEN):
            log.warning("Zoom chatbot thiếu ZOOM_SECRET_TOKEN – sẽ từ chối mọi lệnh gửi tới")
        log.info("Zoom: webhook %s | chatbot %s", "bật" if self.webhook_on else "tắt", "bật" if self.chatbot_on else "tắt")

    # ------------------------------------------------------------ ghép đôi
    def pairing(self):
        return self.bot.store.get("zoom_pairing") or {}

    def pair(self, jid, name="", account_id="", reply_jid=""):
        p = {"jid": jid.strip(), "name": name, "account_id": account_id or self.account_id(),
             "reply_jid": reply_jid or jid.strip(), "ts": time.time()}
        self.bot.store.set("zoom_pairing", p)
        return p

    def unpair(self):
        self.bot.store.set("zoom_pairing", {})
        self.bot.store.set("zoom_notify_target", {})
        self.bot.store.set("zoom_notify_ok", {})
        self.bot.store.set("zoom_account_id", "")

    # ------------------------------------------------------------ gửi
    def send(self, text):
        try:
            self.q.put_nowait(("notify", text, None))
        except queue.Full:
            pass

    def _worker(self):
        while True:
            kind, text, target = self.q.get()
            try:
                if kind == "notify":
                    if self.webhook_on:
                        self._post_webhook(text)
                    elif self.chatbot_on:
                        if self._notify_fail >= 3 and time.time() - self._notify_fail_ts < 1800:
                            continue            # Zoom liên tục từ chối → tạm ngưng 30 phút (nhắn bot 1 lệnh để làm mới)
                        if self.deliver_notify(text)[0]:
                            self._notify_fail = 0
                        elif self.notify_targets():
                            self._notify_fail += 1
                            self._notify_fail_ts = time.time()
                            if self._notify_fail == 3:
                                log.warning("Zoom: thông báo bị từ chối 3 lần liên tiếp – tạm ngưng 30 phút. "
                                            "Nhắn bot 1 lệnh bất kỳ (vd: status) trên Zoom, hoặc bấm 'Gửi thử' ở trang Cài đặt.")
                else:
                    owner = target.pop("owner", False)
                    ok = self.send_message(**target, text=text)
                    if ok:
                        log.info("Zoom: đã trả lời tới %s (account_id=%s)", target.get("to_jid"),
                                 target.get("account_id") or self.account_id() or "-")
                    if ok and owner:
                        # trả lời chủ bot thành công → lưu đúng đích này để gửi thông báo chủ động
                        if (self.bot.store.get("zoom_notify_target") or {}) != target:
                            self.bot.store.set("zoom_notify_target", dict(target))
                            log.info("Zoom: đã lưu đích gửi thông báo theo lần trả lời thành công (user_jid=%s)",
                                     target.get("user_jid"))
                        self._notify_fail = 0
            except Exception as e:
                log.warning("Zoom gửi lỗi: %s", e)

    # ------------------------------------------------------------ đích gửi thông báo chủ động
    def notify_targets(self):
        """Các cách gửi thông báo, thử lần lượt tới khi Zoom nhận. Cách đã thành công lần trước được thử trước."""
        out, seen = [], set()
        robots = [r for r in dict.fromkeys([self.robot_jid(), cfg.ZOOM_BOT_JID]) if r]

        learned_acc = self.account_id()

        def add(name, to_jid, user_jid, account_id):
            if not to_jid:
                return
            for i, robot in enumerate(robots):
                acc = account_id or learned_acc
                key = (robot, to_jid, user_jid or to_jid, acc or "")
                if key in seen:
                    continue
                seen.add(key)
                out.append({"name": name + ("" if i == 0 else " + ZOOM_BOT_JID trên panel"), "robot_jid": robot,
                            "to_jid": to_jid, "user_jid": user_jid or to_jid, "account_id": acc, "omit_user": False})

        good = self.bot.store.get("zoom_notify_ok") or {}
        if good.get("to_jid") and good.get("robot_jid"):
            key = (good["robot_jid"], good["to_jid"], good.get("user_jid"), good.get("account_id") or "")
            seen.add(key)
            out.append(dict(good, omit_user=False))
        if cfg.ZOOM_TO_JID:
            add("ZOOM_TO_JID", cfg.ZOOM_TO_JID, cfg.ZOOM_TO_JID, learned_acc)
        t = self.bot.store.get("zoom_notify_target") or {}
        if t.get("to_jid"):
            add("địa chỉ lần trả lời thành công", t["to_jid"], t.get("user_jid"), t.get("account_id"))
        p = self.pairing()
        if p.get("jid"):
            # account_id Zoom gửi kèm tin nhắn đứng trước: ZOOM_ACCOUNT_ID trên panel hay bị điền nhầm "Account No." (10 chữ số)
            for acc_name, acc in (("", learned_acc), (" + account_id lúc ghép đôi", p.get("account_id")),
                                  (" + ZOOM_ACCOUNT_ID", cfg.ZOOM_ACCOUNT_ID)):
                if acc:
                    add("JID đã ghép đôi" + acc_name, p.get("reply_jid") or p["jid"], p["jid"], acc)
        return out

    def deliver_notify(self, text):
        """Gửi 1 thông báo; trả về (ok, mô tả cách đã thử)."""
        tried = []
        for t in self.notify_targets():
            ok = self.send_message(t["to_jid"], text, t["user_jid"], t["account_id"], robot_jid=t.get("robot_jid"))
            tried.append(f"{t['name']}: {'OK' if ok else 'lỗi'}")
            if ok:
                if (self.bot.store.get("zoom_notify_ok") or {}).get("name") != t["name"]:
                    log.info("Zoom: gửi thông báo thành công bằng cách '%s' – sẽ dùng cách này", t["name"])
                self.bot.store.set("zoom_notify_ok", t)
                return True, tried
        if not tried:
            tried.append("chưa có JID nào để gửi (nhắn bot 1 lệnh trên Zoom hoặc đặt ZOOM_TO_JID)")
        return False, tried

    def test_send(self):
        """Nút 'Gửi thử' (web) và lệnh zoomtest: gửi ngay 1 thông báo, trả về kết quả từng cách."""
        if self.webhook_on:
            ok = self._post_webhook("🔔 Thử thông báo Zoom từ OKX Altcoin Bot")
            return ok, ["Incoming Webhook: " + ("OK" if ok else "lỗi")]
        if not self.chatbot_on:
            return False, ["Zoom chatbot chưa cấu hình (thiếu ZOOM_CLIENT_ID / ZOOM_CLIENT_SECRET / ZOOM_BOT_JID)"]
        self._notify_fail = 0
        ok, tried = self.deliver_notify("🔔 Thử thông báo Zoom từ OKX Altcoin Bot – nếu thấy tin này là thông báo đã hoạt động.")
        log.info("Zoom gửi thử: %s", " | ".join(tried))
        return ok, tried

    def _post_webhook(self, text):
        url = cfg.ZOOM_WEBHOOK_URL
        if "format=" not in url:
            url += ("&" if "?" in url else "?") + "format=message"
        r = requests.post(url, data=json.dumps(text[:3800], ensure_ascii=False).encode("utf-8"),
                          headers={"Authorization": cfg.ZOOM_WEBHOOK_TOKEN, "Content-Type": "application/json"}, timeout=15)
        if r.status_code >= 300:
            log.warning("Zoom webhook %s: %s", r.status_code, r.text[:200])

    def _access_token(self):
        if self._token and time.time() < self._token_exp:
            return self._token
        r = requests.post(self.TOKEN_URL, params={"grant_type": "client_credentials"},
                          auth=(cfg.ZOOM_CLIENT_ID, cfg.ZOOM_CLIENT_SECRET), timeout=15)
        if r.status_code >= 400:
            log.error("Zoom OAuth token lỗi (%s): %s", r.status_code, r.text[:300])
        r.raise_for_status()
        d = r.json()
        self._token = d["access_token"]
        self._token_exp = time.time() + max(60, int(d.get("expires_in", 3600)) - 60)
        return self._token

    @staticmethod
    def to_zoom_markdown(text):
        out = []
        for line in text.split("\n"):
            h = re.match(r"^(#{1,6})\s+(.*)$", line)
            if h:
                out.append(f"*{h.group(2).strip()}*")
                continue
            t = re.match(r"^\s*\|(.+)\|\s*$", line)
            if t:
                cells = [c.strip() for c in t.group(1).split("|")]
                if all(re.match(r"^:?-{2,}:?$", c) for c in cells if c):
                    continue
                out.append("• " + " — ".join(c for c in cells if c))
                continue
            out.append(line)
        s = "\n".join(out)
        s = re.sub(r"\*\*(.+?)\*\*", r"*\1*", s)
        return re.sub(r"~~(.+?)~~", r"~\1~", s)

    @classmethod
    def split(cls, text, limit=None):
        limit = limit or cls.MAX_CHARS
        if len(text) <= limit:
            return [text] if text else []
        chunks, rest = [], text
        while len(rest) > limit:
            w = rest[:limit]
            cut = w.rfind("\n\n")
            if cut < limit // 2:
                cut = w.rfind("\n")
            if cut < limit // 2:
                cut = w.rfind(" ")
            if cut < limit // 2:
                cut = limit
            chunks.append(rest[:cut].rstrip())
            rest = rest[cut:].lstrip("\n")
        if rest:
            chunks.append(rest)
        return chunks

    def robot_jid(self):
        """Bot JID để gửi: ưu tiên JID mà chính Zoom ghi trong sự kiện gần nhất (payload.robotJid) –
        sai Bot JID (vd chép nhầm bản Production/Development) làm Zoom báo 7004 dù JID người nhận đúng."""
        return (self.bot.store.get("zoom_robot_jid") or "").strip() or cfg.ZOOM_BOT_JID

    def account_id(self):
        """account_id để gửi: ưu tiên accountId mà Zoom ghi trong tin nhắn gần nhất. Biến ZOOM_ACCOUNT_ID hay bị điền
        nhầm "Account No." (dãy số ở zoom.us/account) – sai account_id thì Zoom báo 7004 dù to_jid đúng."""
        return (self.bot.store.get("zoom_account_id") or "").strip() or cfg.ZOOM_ACCOUNT_ID

    def send_message(self, to_jid, text, user_jid=None, account_id=None, omit_user=False, robot_jid=None):
        robot = robot_jid or self.robot_jid()
        for chunk in self.split(self.to_zoom_markdown(text)):
            body = {"robot_jid": robot, "to_jid": to_jid, "user_jid": user_jid or to_jid,
                    "account_id": account_id or self.account_id(), "is_markdown_support": True,
                    "content": {"body": [{"type": "message", "text": chunk}]}}
            if omit_user:
                body.pop("user_jid", None)
            sent = False
            for attempt in range(1, 4):
                try:
                    r = requests.post(self.API, json=body, timeout=15,
                                      headers={"Authorization": f"Bearer {self._access_token()}"})
                    if r.status_code < 400:
                        sent = True
                        break
                    log.error("Zoom gửi tin lỗi (%s) robot_jid=%s to_jid=%s user_jid=%s account_id=%s: %s", r.status_code,
                              robot, to_jid, body.get("user_jid", "-"), body["account_id"] or "-", r.text[:300])
                    if r.status_code == 401:
                        self._token = None
                    elif r.status_code != 429:
                        break                   # lỗi cố định (vd 7004 sai JID/account) → thử lại vô ích
                except requests.RequestException as e:
                    log.warning("Zoom gửi tin thử lại %d/3: %s", attempt, e)
                time.sleep(1.5 * attempt)
            if not sent:
                return False
        return True

    # ------------------------------------------------------------ nhận lệnh (endpoint /zoom/bot hoặc /webhook/zoom)
    @staticmethod
    def _fresh(ts):
        if not ts or len(ts) > 16 or not ts.isdigit():
            return False
        t = int(ts)
        t = t / 1000 if t > 10 ** 11 else float(t)
        return abs(time.time() - t) <= 300

    def verify_detail(self, raw_body: bytes, headers):
        """Trả về (ok, lý do). Thử chữ ký HMAC với mọi khoá bí mật của app (Secret Token, Verification Token,
        Client Secret) để tự xử lý trường hợp dán nhầm biến – vẫn an toàn vì cả 3 đều là bí mật của chính app này."""
        sig = headers.get("x-zm-signature", "")
        ts = headers.get("x-zm-request-timestamp", "")
        auth = headers.get("authorization", "")
        names = [n for n in ("x-zm-signature", "x-zm-request-timestamp", "authorization") if headers.get(n)]
        if sig:
            if not self._fresh(ts):
                return False, f"timestamp lệch giờ/không hợp lệ ({ts})"
            msg = b"v0:" + ts.encode("ascii", "ignore") + b":" + raw_body
            for name, key in (("ZOOM_SECRET_TOKEN", cfg.ZOOM_SECRET_TOKEN), ("ZOOM_VERIFICATION_TOKEN", cfg.ZOOM_VERIFICATION_TOKEN),
                              ("ZOOM_CLIENT_SECRET", cfg.ZOOM_CLIENT_SECRET)):
                if not key:
                    continue
                exp = "v0=" + hmac.new(key.encode(), msg, hashlib.sha256).hexdigest()
                if hmac.compare_digest(sig.encode(), exp.encode()):
                    if name != "ZOOM_SECRET_TOKEN":
                        log.warning("Zoom: chữ ký khớp với %s chứ không phải ZOOM_SECRET_TOKEN – nên sửa biến cho đúng", name)
                        return True, f"chữ ký hợp lệ (khớp {name} – ZOOM_SECRET_TOKEN đang sai, nên sửa lại)"
                    return True, "chữ ký hợp lệ"
        if auth:
            for name, key in (("ZOOM_VERIFICATION_TOKEN", cfg.ZOOM_VERIFICATION_TOKEN), ("ZOOM_SECRET_TOKEN", cfg.ZOOM_SECRET_TOKEN)):
                if key and hmac.compare_digest(auth.encode(), key.encode()):
                    return True, f"Authorization khớp {name}"
        if not sig and not auth:
            return False, "request không có chữ ký Zoom"
        return False, (f"chữ ký KHÔNG khớp với ZOOM_SECRET_TOKEN / VERIFICATION_TOKEN / CLIENT_SECRET đang đặt "
                       f"(header có: {', '.join(names)}). Lấy lại Secret Token ở Features → Access của ĐÚNG app này")

    def record(self, event, ok, reason, data=None):
        """Lưu 20 request Zoom gần nhất để xem trên trang /zoom."""
        p = (data or {}).get("payload") or {}
        row = {"ts": time.time(), "event": event or "?", "ok": ok, "reason": reason,
               "user": str(p.get("userName") or p.get("userJid") or p.get("user_jid") or "")[:80],
               "cmd": str(p.get("cmd") or p.get("message") or "")[:60],
               "robot": str(p.get("robotJid") or p.get("robot_jid") or "")[:80]}
        rows = ([row] + (self.bot.store.get("zoom_requests") or []))[:20]
        try:
            self.bot.store.set("zoom_requests", rows)
        except Exception:
            pass

    def verify(self, raw_body: bytes, headers) -> bool:
        if cfg.ZOOM_SECRET_TOKEN:
            sig = headers.get("x-zm-signature", "")
            ts = headers.get("x-zm-request-timestamp", "")
            if not sig or not self._fresh(ts):
                return False
            msg = b"v0:" + ts.encode("ascii") + b":" + raw_body
            exp = "v0=" + hmac.new(cfg.ZOOM_SECRET_TOKEN.encode(), msg, hashlib.sha256).hexdigest()
            return hmac.compare_digest(sig.encode(), exp.encode())
        if cfg.ZOOM_VERIFICATION_TOKEN:          # cơ chế xác thực cũ
            return hmac.compare_digest(headers.get("authorization", ""), cfg.ZOOM_VERIFICATION_TOKEN)
        return False

    @staticmethod
    def url_validation(plain):
        enc = hmac.new((cfg.ZOOM_SECRET_TOKEN or "").encode(), plain.encode(), hashlib.sha256).hexdigest()
        return {"plainToken": plain, "encryptedToken": enc}

    @staticmethod
    def parse_event(data):
        p = data.get("payload")
        if not isinstance(p, dict):
            return None
        text = str(p.get("cmd") or p.get("message") or p.get("content") or "").strip()
        sender = str(p.get("userJid") or p.get("user_jid") or "").strip()
        to_jid = str(p.get("toJid") or p.get("to_jid") or sender).strip()
        channel = str(p.get("channelName") or p.get("channel_name") or "").strip()
        ev = {
            "event_id": str(p.get("messageId") or p.get("message_id") or f"{sender}:{data.get('event_ts', '')}"),
            "sender_jid": sender, "text": text, "to_jid": to_jid, "channel": channel,
            "account_id": str(p.get("accountId") or p.get("account_id") or "").strip(),
            "name": str(p.get("userName") or p.get("user_name") or ""),
            "email": str(p.get("userEmail") or p.get("user_email") or "").lower(),
            "robot_jid": str(p.get("robotJid") or p.get("robot_jid") or "").strip(),
        }
        # Zoom gửi channelName (= tên bot) cả trong chat 1:1, nên phân biệt theo toJid: kênh nhóm có đuôi @conference.
        is_channel = "@conference." in to_jid.lower()
        ev["channel"] = channel if is_channel else ""
        # Trả lời: trong kênh nhóm dùng toJid (JID kênh); chat 1:1 phải trả về JID người gửi
        ev["reply_jid"] = to_jid if is_channel else sender
        if not sender or not text:
            return None
        return ev

    def lifecycle(self, event, payload):
        """Sự kiện vòng đời app: gỡ app → xoá ghép đôi (cam kết trong /privacy)."""
        if event == "app_deauthorized":
            self.unpair()
            self.bot.store.set("zoom_install", {})
            log.warning("Zoom: app bị gỡ khỏi tài khoản %s – đã xoá thông tin ghép đôi", payload.get("account_id") or payload.get("accountId"))
            self.bot.notify.send("⚠️ Zoom: ứng dụng đã bị gỡ khỏi Zoom, thông tin ghép đôi đã được xoá.")
        elif event == "bot_installed":
            log.info("Zoom: bot đã được cài (%s)", payload.get("userName") or payload.get("userJid") or "")

    def handle(self, data):
        ev = self.parse_event(data)
        if ev is None:
            return
        now = time.time()
        if ev["event_id"] in self._seen:
            return                                  # Zoom gửi lại – bỏ qua
        self._seen[ev["event_id"]] = now
        if len(self._seen) > 2000:
            self._seen = {k: v for k, v in self._seen.items() if now - v < 3600}
        if ev.get("robot_jid"):
            if ev["robot_jid"] != (self.bot.store.get("zoom_robot_jid") or ""):
                self.bot.store.set("zoom_robot_jid", ev["robot_jid"])
                if cfg.ZOOM_BOT_JID and ev["robot_jid"] != cfg.ZOOM_BOT_JID:
                    log.warning("Zoom: Bot JID Zoom gửi tới (%s) KHÁC biến ZOOM_BOT_JID (%s) – bot sẽ dùng JID của Zoom. "
                                "Nên sửa ZOOM_BOT_JID cho khớp (thường do chép nhầm bản Production/Development).",
                                ev["robot_jid"], cfg.ZOOM_BOT_JID)
                else:
                    log.info("Zoom: Bot JID từ Zoom khớp ZOOM_BOT_JID")
        if ev.get("account_id") and ev["account_id"] != (self.bot.store.get("zoom_account_id") or ""):
            self.bot.store.set("zoom_account_id", ev["account_id"])
            if cfg.ZOOM_ACCOUNT_ID and ev["account_id"] != cfg.ZOOM_ACCOUNT_ID:
                log.warning("Zoom: account_id Zoom gửi tới (%s) KHÁC biến ZOOM_ACCOUNT_ID (%s) – bot sẽ dùng account_id của "
                            "Zoom. Nên xoá hoặc sửa ZOOM_ACCOUNT_ID (đó không phải 'Account No.' ở zoom.us/account).",
                            ev["account_id"], cfg.ZOOM_ACCOUNT_ID)
            else:
                log.info("Zoom: đã lưu account_id từ Zoom (%s)", ev["account_id"])
            self._notify_fail = 0
        target = {"to_jid": ev["reply_jid"], "user_jid": ev["sender_jid"], "account_id": ev["account_id"],
                  "robot_jid": ev.get("robot_jid") or None}
        p = self.pairing()
        allowed = [u.strip().lower() for u in cfg.ZOOM_ALLOWED_USERS.split(",") if u.strip()]
        ok = (p.get("jid") == ev["sender_jid"]) or (allowed and ({ev["sender_jid"].lower(), ev["email"]} & set(allowed)))
        if not ok:
            log.warning("Zoom: tin từ JID chưa ghép đôi %s (%s) – bỏ qua", ev["sender_jid"], ev["name"])
            self.bot.store.set("zoom_pending", {"jid": ev["sender_jid"], "name": ev["name"], "account_id": ev["account_id"],
                                                "reply_jid": ev["reply_jid"], "ts": time.time()})
            if time.time() - self._alerted_at.get(ev["sender_jid"], 0) > 120:     # báo lại tối đa 2 phút/lần
                self._alerted_at[ev["sender_jid"]] = time.time()
                self.bot.notify.send(f"🔐 Zoom: {ev['name'] or 'ai đó'} vừa nhắn bot nhưng chưa được cấp quyền.\n"
                                     f"JID: {ev['sender_jid']}\nNếu là anh, gõ trên Telegram: /zoompair {ev['sender_jid']}\n"
                                     f"hoặc bấm 'Ghép đôi' trên trang {cfg.PUBLIC_URL or ''}/zoom")
            self.q.put_nowait(("reply", "⛔ Tài khoản Zoom này chưa được ghép đôi với bot. Chủ bot cần gõ /zoompair trên Telegram hoặc bấm Ghép đôi trên trang /zoom.", target))
            return
        if not p.get("jid") or (not ev["channel"] and p.get("jid") != ev["sender_jid"]):
            # chưa ghép đôi, hoặc chủ bot nhắn từ JID khác JID đã lưu (vd được phép qua ZOOM_ALLOWED_USERS) → ghép lại
            self.pair(ev["sender_jid"], ev["name"], ev["account_id"], ev["reply_jid"])
        elif not ev["channel"] and (p.get("reply_jid") != ev["reply_jid"]
                                    or (ev["account_id"] and p.get("account_id") != ev["account_id"])):
            # cập nhật JID trả lời & account_id theo tin nhắn mới nhất (sai account_id → Zoom báo 7004 khi bot tự gửi)
            log.info("Zoom: cập nhật thông tin ghép đôi (account_id %s → %s)", p.get("account_id") or "-", ev["account_id"] or "-")
            self.pair(p["jid"], p.get("name", "") or ev["name"], ev["account_id"] or p.get("account_id", ""), ev["reply_jid"])
            self._notify_fail = 0
        sw = getattr(self.bot, "switches", None)
        if sw is not None and not sw.on("zoom_cmd"):
            log.info("Zoom: bỏ qua lệnh '%s' (công tắc trả lời lệnh Zoom đang tắt)", ev["text"][:40])
            return
        log.info("Zoom lệnh từ %s: %s", ev["name"] or ev["sender_jid"], ev["text"])

        target["owner"] = not ev["channel"]

        def _run():
            try:
                reply = self.bot.handle_command(ev["text"], source="Zoom")
            except Exception as e:
                reply = f"Lỗi khi xử lý lệnh: {e}"
            self.q.put(("reply", reply, target))
        threading.Thread(target=_run, daemon=True).start()
