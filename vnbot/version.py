"""The bot's version: one place, shown on the web pages (header, footer, /phienban), in
Telegram and Zoom (/phienban, /trogiup, /trangthai), on /health, and announced when it
changes.

- ``VERSION`` / ``RELEASED``: the release, with ``NOTES`` (newest first).
- ``build()``: a short fingerprint of the code that is running (every ``.py`` file of
  ``tradingagents/``, ``vnbot/`` and ``main_botkeep.py``, this file excepted). The same
  files give the same fingerprint on any machine, so it tells whether the server runs
  exactly the released code (``RELEASE_BUILD``) or a partial upload / a local edit.
- After changing the code on purpose: raise ``VERSION``, add its ``NOTES``, then
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

VERSION = "v14"
RELEASED = "2026-10-23"
RELEASE_BUILD = "b8749e5"          # written by `python -m vnbot.version --stamp`

# Newest first: (version, release day, what changed - short lines for a phone).
NOTES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("v14", "2026-10-23", (
        "Rà soát toàn bộ mã nguồn: sửa 14 lỗi, thêm 20 kiểm tra (xem BAO_CAO_RA_SOAT_2026-10-23.md)",
        "Sổ ảo: vị thế 1 lô chạm T1 nay bán toàn bộ (trước không bán mà vẫn dời stop về hoà vốn)",
        "Khối ngoại tính theo khớp lệnh, không gồm thoả thuận; Output Guard hết bỏ sót dòng AI giả nhãn hệ thống",
        "Cache giá dùng được ngày nghỉ khi nguồn lỗi; Zoom webhook không chặn web; hoàn hạn mức AI khi lỗi mạng",
        "Stop không sát hơn 3% giá (stop_min_pct); 6 tham số mới cho hiệu chỉnh, 3 cho Phòng thủ (chỉ đổi tay)",
        "Backtest 2022-2025 các phương án: docs/backtest_2026-10-23; quét 300 mã về đúng bảng giá tới ngày GDKHQ",
    )),
    ("v13", "2026-10-22", (
        "Bot quét 300 mã thanh khoản cao: nhắn danh sách đổi chiều lúc 14:46, /stvthitruong",
        "Đối chiếu với Siêu tín hiệu sau 15:15, cộng dồn độ chính xác qua các phiên",
        "Giá trong phiên lấy từ bảng giá một lần cho cả danh sách; tự điều chỉnh ngày GDKHQ",
    )),
    ("v12", "2026-10-22", (
        "/stvxoa danhmuc: xoá ngay toàn bộ danh mục Siêu tín hiệu (mã, tiền mặt, lệnh đã ghi), không hoàn tác",
    )),
    ("v11", "2026-10-22", (
        "Đổi chiều trong phiên cho mọi mã đang theo dõi: bot tự ước tính, nhắn khi giá qua mức",
        "/stvdoichieu: mã đã qua/sát mức đổi chiều; ngoài giờ: mức đổi chiều phiên tới",
        "Sau đóng cửa báo mã đổi chiều trong phiên nhưng trang không chốt",
    )),
    ("v10", "2026-10-22", (
        "Quét cả 60 mã watchlist cùng danh mục (mã trùng hỏi 1 lần): báo tín hiệu MUA/BÁN mới của ngày",
        "Báo đổi chiều trong phiên mà trang cho xem miễn phí (chưa chốt) nếu là mã đang theo dõi",
        "/stvquet quét cả danh mục lẫn watchlist",
    )),
    ("v9", "2026-10-22", (
        "Ghi được lệnh mua lô lẻ (dưới 100 cp, không cần bội số 100): /stvkhop và /khop",
    )),
    ("v8", "2026-10-22", (
        "Siêu tín hiệu: bỏ đề xuất mua cho danh mục của anh - anh tự quyết định mua",
        "Quét 15 phút/lần các mã đang giữ, nhắn ngay khi trang có tín hiệu BÁN (/stvquet: quét ngay)",
        "Lãi/lỗ thực tế: giá khớp hiện tại, đã trừ phí bán và thuế; /stvcosan cho cổ phiếu có sẵn",
    )),
    ("v7", "2026-10-22", (
        "Số phiên bản trên web (đầu trang, cuối trang, trang /phienban) và Telegram/Zoom (/phienban)",
        "Mã bản dựng: biết server có chạy đúng bản phát hành không",
        "Bot tự báo khi được cập nhật lên phiên bản mới",
    )),
    ("v6", "2026-10-21", (
        "Siêu tín hiệu: watchlist riêng 60 mã (/stvtheodoi) và danh mục riêng (/stvdanhmuc)",
        "Tin nhắn Siêu tín hiệu chia mục, mỗi mã một dòng, dễ đọc trên điện thoại",
    )),
    ("v5", "2026-10-21", (
        "Bộ kiểm thử offline trong tests/ (python tests/run_all.py)",
        "Sửa giá trần UPCoM sai một bước giá; chặn timestamp Zoom lạ gây lỗi 500",
    )),
    ("v4", "2026-10-21", (
        "Bản tin sáng Siêu tín hiệu gửi Zoom (Telegram dự phòng)",
        "Tài khoản thật riêng theo Siêu tín hiệu: /stvvon, /stvkhop, /stvthuong, /stvsang",
    )),
    ("v3", "2026-10-21", (
        "So sánh với sieutinhieu.vn: sổ ảo, /stv, dòng 📡 trong /nhanh",
    )),
    ("v2", "2026-10-21", (
        "Cảnh báo VNINDEX trong phiên, chờ nến trễ, thanh khoản tính theo tiền",
    )),
    ("v1", "2026-10-21", (
        "Sổ thật: cổ tức/thưởng, NAV theo giá thị trường, T+2 theo lịch nghỉ",
        "Hiệu chỉnh tham số không học thuộc dữ liệu kiểm tra; ghép đôi Zoom an toàn",
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
    """"v7 · 22/10/2026 · bản dựng 1a2b3c4" (``short``: "v7")."""
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
