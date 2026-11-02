"""Kiểm tra v3.0: công tắc kênh, thông báo cập nhật phiên bản, khung trang web."""
import os
import shutil
import sys

D = "/tmp/okx_test_web"
shutil.rmtree(D, ignore_errors=True)
os.environ.update(DATA_DIR=D, MODE="paper", DASHBOARD_USER="", DASHBOARD_PASS="", DATABASE_URL="")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from types import SimpleNamespace as NS
from bot.storage import Store
from bot.channels import Switches
from bot.notify import Notifier
from bot import version as ver, ui

st = Store()
sw = Switches(st)
got = []


class Ch:
    def __init__(self, key):
        self.switch_key, self.enabled = key, True

    def send(self, t):
        got.append((self.switch_key, t))


bot = NS(is_leader=True, switches=sw)
n = Notifier(bot)
n.add(Ch("telegram_notify"))
n.add(Ch("zoom_notify"))
n.send("a")
assert [k for k, _ in got] == ["telegram_notify", "zoom_notify"]
sw.set("zoom_notify", False)
got.clear()
n.send("b")
assert [k for k, _ in got] == ["telegram_notify"]
assert sw.all()["zoom_notify"] is False and Switches(st).on("zoom_notify") is False   # lưu bền
try:
    sw.set("khong_co", True)
    raise SystemExit("phải báo lỗi")
except ValueError:
    pass
print("✓ công tắc kênh")

assert ver.update_notice(f"{ver.VERSION} {ver.build()}") is None
assert ver.update_notice("2.9.7 abc1234").startswith("🆕 Bot đã cập nhật: v2.9.7 → v" + ver.VERSION)
assert "vừa đổi" in ver.update_notice(f"{ver.VERSION} 0000000")
assert len(ver.build()) == 7 and ver.NOTES[0][0] == ver.VERSION
# lệnh phienban (/alt phienban qua bot Cổ phiếu) vừa MỘT tin: bot Cổ phiếu (vnbot.altcoin_bridge) gửi
# "🪙 Altcoin › phienban\n" + trả lời và cắt mỗi PART = 3500 ký tự (bản 3.3.0 trước sửa: 3714–3768 ký tự → 2 tin)
try:
    from vnbot.altcoin_bridge import PART, PREFIX, parts, rewrite
except ImportError:                     # chạy thẳng trong altcoin/ của Stockvn: vnbot ở thư mục trên
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        from vnbot.altcoin_bridge import PART, PREFIX, parts, rewrite
    except ImportError:                 # bot chạy độc lập, không có Stockvn
        PART, PREFIX, parts, rewrite = 3500, "🪙 Altcoin › ", None, str
reply = f"{PREFIX}phienban\n{rewrite(ver.text())}"
assert len(reply) <= PART - 60, (len(reply), PART)              # chừa chỗ: thời gian chạy, trạng thái bản dựng dài hơn
assert parts is None or len(parts(reply)) == 1, [len(p) for p in parts(reply)]
assert all(f"• {n}" in ver.text(limit=0) for n in ver.NOTES[0][2])   # bản mới nhất luôn đủ, bản cũ hơn chỉ khi còn chỗ
assert f"v{ver.NOTES[1][0]} (" not in ver.text(limit=0) and f"v{ver.NOTES[1][0]} (" in ver.text(limit=10 ** 6)
print("✓ phiên bản & thông báo cập nhật:", ver.label())

html = ui.page("Thử", ui.hero("Tiêu đề", "mô tả", eyebrow="Nhóm"), active="home")
assert "aria-current=page" in html and "Phiên bản" in html and html.count("<h1>") == 1
assert 'role="switch"' in ui.switch_form("zoom_cmd", True, "Zoom – lệnh")
print("✓ khung trang")
print("TẤT CẢ OK")
