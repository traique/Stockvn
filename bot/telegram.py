"""Telegram: gửi thông báo + nhận lệnh /status /pause /resume /trades /close /help (long-polling thread)."""
import logging
import threading
import time
import requests
from .config import cfg

log = logging.getLogger("telegram")


class Telegram:
    switch_key = "telegram_notify"

    def __init__(self, bot_ref):
        self.bot = bot_ref
        self.base = f"https://api.telegram.org/bot{cfg.TELEGRAM_TOKEN}"
        self.enabled = bool(cfg.TELEGRAM_TOKEN and cfg.TELEGRAM_CHAT_ID)
        self.offset = 0

    def send(self, text):
        """Gửi thô (Notifier đã kiểm tra leader)."""
        if not self.enabled:
            return
        try:
            requests.post(f"{self.base}/sendMessage", json={
                "chat_id": cfg.TELEGRAM_CHAT_ID, "text": text[:4000]}, timeout=15)
        except Exception as e:
            log.warning("Telegram send lỗi: %s", e)

    def start(self):
        if not self.enabled:
            log.info("Telegram chưa cấu hình")
            return
        threading.Thread(target=self._poll, daemon=True, name="telegram").start()

    def _poll(self):
        while True:
            if not getattr(self.bot, "is_leader", True):
                # chỉ leader mới nhận lệnh Telegram (Telegram không cho 2 máy cùng getUpdates)
                time.sleep(10)
                continue
            try:
                r = requests.get(f"{self.base}/getUpdates",
                                 params={"offset": self.offset, "timeout": 30}, timeout=40).json()
                for u in r.get("result", []):
                    self.offset = u["update_id"] + 1
                    msg = u.get("message") or {}
                    if str(msg.get("chat", {}).get("id")) != str(cfg.TELEGRAM_CHAT_ID):
                        continue
                    text = msg.get("text", "").strip()
                    if not text:
                        continue
                    sw = getattr(self.bot, "switches", None)
                    if sw is not None and not sw.on("telegram_cmd"):
                        log.info("Telegram: bỏ qua lệnh '%s' (công tắc trả lời lệnh Telegram đang tắt)", text[:40])
                        continue
                    self.send(self.bot.handle_command(text, source="Telegram"))
            except Exception as e:
                log.warning("Telegram poll lỗi: %s", e)
                time.sleep(5)
