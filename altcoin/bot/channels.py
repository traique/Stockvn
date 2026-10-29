"""Công tắc bật/tắt kênh, chỉnh trên web (trang Cài đặt). Lưu trong DB (kv "channels") nên restart vẫn giữ.

Công tắc chỉ tắt/bật thứ đã cấu hình, không thay cấu hình (Zoom chưa có khoá app vẫn là tắt).
- telegram_notify: thông báo chủ động tới Telegram (mở/đóng lệnh, báo cáo, tự học…)
- telegram_cmd:    trả lời lệnh gõ trong Telegram
- zoom_notify:     thông báo chủ động tới Zoom
- zoom_cmd:        trả lời lệnh gõ trong Zoom (xác thực endpoint & gỡ app vẫn được xử lý)
"""
import threading

NAMES = {
    "telegram_notify": ("Telegram", "Thông báo", "Mở/đóng lệnh, báo cáo ngày, tự học, cảnh báo"),
    "telegram_cmd": ("Telegram", "Trả lời lệnh", "status, trades, signals, learn… gõ trong Telegram"),
    "zoom_notify": ("Zoom", "Thông báo", "Mở/đóng lệnh, báo cáo ngày, tự học, cảnh báo"),
    "zoom_cmd": ("Zoom", "Trả lời lệnh", "status, trades, signals, learn… gõ trong Zoom"),
}


class Switches:
    def __init__(self, store):
        self.store = store
        self._lock = threading.Lock()

    def _read(self):
        try:
            d = self.store.get("channels") or {}
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def on(self, name):
        v = self._read().get(name)
        return True if v is None else bool(v)

    def set(self, name, on):
        if name not in NAMES:
            raise ValueError(f"Công tắc không hợp lệ: {name}")
        with self._lock:
            d = self._read()
            d[name] = bool(on)
            self.store.set("channels", d)

    def all(self):
        d = self._read()
        return {k: (True if d.get(k) is None else bool(d.get(k))) for k in NAMES}
