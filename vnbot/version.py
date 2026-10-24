"""The bot's version: one place, shown on the web pages (header, footer, /phienban), in
Telegram and Zoom (/phienban, /trogiup, /trangthai), on /health, and announced when it
changes.

- ``VERSION`` / ``RELEASED``: the release, with ``NOTES`` (newest first).
- ``build()``: a short fingerprint of the code that is running (every ``.py`` file of
  ``tradingagents/``, ``vnbot/`` and ``main_botkeep.py``, this file excepted). The same
  files give the same fingerprint on any machine, so it tells whether the server runs
  exactly the released code (``RELEASE_BUILD``) or a partial upload / a local edit.
- After changing the code on purpose: raise ``VERSION`` (vMAJOR.MINOR.PATCH), add its ``NOTES``, then
  ``python -m vnbot.version --stamp`` writes the new ``RELEASE_BUILD`` here.

Light: standard library only (the boot page imports it before the app is up).
"""

from __future__ import annotations

import hashlib
import platform
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

VERSION = "v1.3.0"
RELEASED = "2026-10-24"
RELEASE_BUILD = "af2cb04"          # written by `python -m vnbot.version --stamp`

# Newest first: (version, release day, what changed - short lines for a phone).
# Versions are vMAJOR.MINOR.PATCH; the history before 1.0.0 was dropped on purpose.
NOTES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("v1.3.0", "2026-10-24", (
        "Claude chọn bóng: mỗi phiên Claude đọc các lệnh MUA mới của trang, tìm tin, chọn 1 mã, nêu mã nên tránh",
        "Chỉ ghi để đo, không phải lệnh: mọi mã chấm theo ⛔/🔴, so với TB các lệnh cùng ngày (cần 50 phiên)",
        "Lệnh /claude, /claude chay, /claude lichsu (Telegram và Zoom); trang web Claude chọn bóng trong Tiện ích",
        "Cần ANTHROPIC_API_KEY và SIEUTINHIEU=1; trần chi phí CLAUDE_PICK_MONTHLY_USD (mặc định 10 USD)",
    )),
    ("v1.2.0", "2026-10-24", (
        "Giao diện mới cho toàn bộ web theo app LCTA: nền lavender, thẻ bo tròn, sáng/tối, gọn trên điện thoại",
        "Đầu trang: ngày âm lịch, trạng thái thị trường; menu HOME · DANH MỤC · TIỆN ÍCH",
        "Danh mục: lời chào, cảnh báo tỷ trọng, thẻ từng mã (tên công ty, giá 3 tháng, PnL, tin tức)",
        "Biểu đồ hiệu suất vẽ theo bề rộng màn hình, so sánh VN-Index, xem từng ngày bằng phím mũi tên",
        "Nhật ký lọc theo loại và mã; báo cáo cuối ngày trên web, gửi ngay qua Telegram và Zoom",
        "Đạt kiểm tra truy cập WCAG 2 A/AA (axe-core) ở cả giao diện sáng và tối",
    )),
    ("v1.1.0", "2026-10-23", (
        "Tài sản: danh mục tiền thật riêng (lệnh /ts, tab web /taisan), thay sổ /stvkhop",
        "Nhập Excel của app cũ (xem trước, đối chiếu từng số), xuất Excel 4 sheet và CSV",
        "Biểu đồ tài sản 7N đến Tất cả, so với VNINDEX theo đơn vị quỹ; quyền chờ về tính vào tài sản",
        "Báo cáo Tài sản 15:30 qua Telegram và Zoom; cảnh báo bán ⛔ 🔴 🟠 🎯 kèm giá sau 5 và 10 phiên",
        "Backtest quy tắc bán: giữ ⛔ hoặc 🔴; 🟠 chỉ để theo dõi (docs/backtest_2026-10-23/thoat_tai_san.md)",
    )),
    ("v1.0.0", "2026-10-23", (
        "Bản phát hành đầu tiên: web, Telegram, Zoom, ba sổ ảo (Phòng thủ, Quant Engine, Động lượng), tín hiệu thật",
        "Rà soát toàn bộ mã nguồn: 22 lỗi đã sửa, 64 kiểm tra hồi quy mới (BAO_CAO_RA_SOAT_2026-10-23.md)",
        "Stop không sát hơn 3% giá; 6 tham số mới cho hiệu chỉnh đêm, 3 cho Phòng thủ (chỉ đổi tay)",
        "Backtest 2022-2025 các phương án: docs/backtest_2026-10-23",
    )),
)

ROOT = Path(__file__).resolve().parents[1]
STARTED = time.time()              # this process (the module is imported at start)
_VN = timezone(timedelta(hours=7))


def _code_files() -> list[Path]:
    files = sorted(p for d in ("tradingagents", "vnbot") for p in (ROOT / d).rglob("*.py")
                   if "__pycache__" not in p.parts)
    main = ROOT / "main_botkeep.py"
    return [p for p in files + ([main] if main.exists() else []) if p.resolve() != Path(__file__).resolve()]


@lru_cache(maxsize=1)
def build() -> str:
    """7 hex characters: the running code (paths and contents, line endings ignored)."""
    digest = hashlib.sha256()
    for path in _code_files():
        try:
            data = path.read_bytes().replace(b"\r\n", b"\n")
        except OSError:
            data = b"<unreadable>"
        digest.update(path.relative_to(ROOT).as_posix().encode() + b"\0" + data + b"\0")
    return digest.hexdigest()[:7]


def is_release() -> bool:
    """The running code is exactly the released ``VERSION``."""
    return build() == RELEASE_BUILD


def _ddmmyyyy(day: str) -> str:
    return f"{day[8:10]}/{day[5:7]}/{day[:4]}" if len(day) >= 10 else day


def label(short: bool = False) -> str:
    """"v1.0.0 · 23/10/2026 · bản dựng 1a2b3c4" (``short``: "v1.0.0")."""
    if short:
        return VERSION
    mark = "" if is_release() else " (đã sửa)"
    return f"{VERSION} · {_ddmmyyyy(RELEASED)} · bản dựng {build()}{mark}"


def uptime(now: float | None = None) -> str:
    seconds = int((now or time.time()) - STARTED)
    days, rest = divmod(max(seconds, 0), 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    return (f"{days} ngày " if days else "") + (f"{hours} giờ " if days or hours else "") + f"{minutes} phút"


def started_text() -> str:
    return datetime.fromtimestamp(STARTED, _VN).strftime("%d/%m %H:%M")


def info() -> dict:
    """For /health and the web page."""
    return {"version": VERSION, "released": RELEASED, "build": build(), "release_build": RELEASE_BUILD,
            "release": is_release(), "started": int(STARTED)}


def status_line() -> str:
    """The release check, after the build: "✅ đúng bản phát hành" or what differs."""
    if is_release():
        return "✅ đúng bản phát hành"
    return f"⚠️ khác bản phát hành {VERSION} ({RELEASE_BUILD}): upload thiếu file hoặc đã sửa code"


def text(history: int = 3) -> str:
    """``/phienban`` (Telegram, Zoom): the running version and the last ``history`` releases."""
    lines = [f"🏷️ TradingAgents VN {VERSION} (phát hành {_ddmmyyyy(RELEASED)})",
             f"Bản dựng {build()} · {status_line()}",
             f"Chạy từ {started_text()} ({uptime()}) · Python {platform.python_version()}"]
    for version, day, notes in NOTES[:history]:
        lines += ["", f"{'Có gì mới ở ' if version == VERSION else ''}{version} ({_ddmmyyyy(day)}):"]
        lines += [f"• {n}" for n in notes]
    lines += ["", "Trên web: trang /phienban"]
    return "\n".join(lines)


def changes_since(previous: str) -> list[tuple[str, str, tuple[str, ...]]]:
    """The releases after ``previous`` up to ``VERSION`` (newest first); all of them when unknown."""
    names = [v for v, _, _ in NOTES]
    if previous in names:
        return list(NOTES[:names.index(previous)])
    return list(NOTES[:1])


def update_notice(last: str) -> str | None:
    """The message to announce at start, from the "version build" saved by the last start
    (``last``; "" on the first start with versions): None when nothing changed."""
    parts = (last or "").split()
    prev_version, prev_build = (parts + ["", ""])[:2]
    if prev_version == VERSION and prev_build == build():
        return None
    if prev_version == VERSION:
        return (f"⚠️ Code của bot vừa đổi nhưng vẫn ghi {VERSION}: bản dựng {prev_build} → {build()}\n"
                f"Bản dựng {build()} · {status_line()}")
    head = (f"🆕 Bot đã cập nhật: {prev_version} → {VERSION}" if prev_version
            else f"🏷️ Bot đang chạy TradingAgents VN {VERSION}")
    lines = [head, f"Bản dựng {build()} · {status_line()}"]
    for version, day, notes in changes_since(prev_version):
        lines += ["", f"{version} ({_ddmmyyyy(day)}):"] + [f"• {n}" for n in notes]
    lines += ["", "Chi tiết: /phienban"]
    return "\n".join(lines)


def stamp() -> str:
    """Write the current ``build()`` as ``RELEASE_BUILD`` in this file; returns it."""
    build.cache_clear()
    value = build()
    path = Path(__file__)
    source = path.read_text(encoding="utf-8")
    new, count = re.subn(r'^RELEASE_BUILD = "[0-9a-f]*"', f'RELEASE_BUILD = "{value}"', source, count=1, flags=re.MULTILINE)
    if count != 1:
        raise RuntimeError("RELEASE_BUILD line not found")
    path.write_text(new, encoding="utf-8")
    return value


if __name__ == "__main__":
    if "--stamp" in sys.argv[1:]:
        print(f"{VERSION}: RELEASE_BUILD = {stamp()}")
    else:
        print(text())
