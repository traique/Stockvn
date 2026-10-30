"""The bot's version: one place, shown on the web pages (header, footer, /phienban), in
Telegram and Zoom (/phienban, /trogiup, /trangthai), on /health, and announced when it
changes.

- ``VERSION`` / ``RELEASED``: the release, with ``NOTES`` (newest first).
- ``build()``: a short fingerprint of the code that is running (every ``.py`` file of
  ``tradingagents/``, ``vnbot/``, ``main_botkeep.py`` and ``altcoin/hosted.py`` - the altcoin's
  glue, outside its own fingerprint -, this file excepted). The same
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

VERSION = "v1.17.0"
RELEASED = "2026-10-30"
RELEASE_BUILD = "aeea8c5"          # written by `python -m vnbot.version --stamp`

# Newest first: (version, release day, what changed - short lines for a phone).
# Versions are vMAJOR.MINOR.PATCH; the history before 1.0.0 was dropped on purpose.
NOTES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("v1.17.0", "2026-10-30", (
        "Định giá bot tự tính cho mọi mã, không cần nhập: /dinhgia MÃ (gõ tắt /dg); Zoom: dinhgia FPT",
        "Mô hình P/B hợp lý như thẻ ngân hàng; Ke 12% ngân hàng, 13% mã khác (VALUATION_KE_DEFAULT)",
        "Mỗi thẻ ghi độ tin cậy: cao (ngân hàng, bảo hiểm, chứng khoán), thấp (BĐS, công nghệ, ROE rất cao...)",
        "Không lập thẻ khi vốn chủ âm, chưa đủ 3 năm ROE hoặc đang lỗ, và nói rõ lý do",
        "Có gì đổi so với lần xem trước: mốc đổi từ 0,3%, đổi vùng, BCTC mới, đổi giả định",
        "/dinhgia: mã theo dõi xếp theo MOS; trang web /dinhgia/MÃ theo bố cục hồ sơ PDF",
        "Ngân hàng: NIM, CIR, nợ xấu, bao phủ... so với trung vị các ngân hàng cùng quý, kèm hạng",
        "Sau 16:00 bot tự tính thẻ của watchlist, mã đang giữ và mã có thẻ anh nhập; bị lỡ thì chạy bù",
        "Tự báo trong phiên khi giá vào Rẻ/Đắt theo thẻ của bot; công tắc riêng ở /ai (Kênh thông báo)",
        "Bull sát Base: báo Đắt từ Base +2%; việc 16:00 xong nhắn các mã đổi vùng",
        "Thẻ anh nhập (/tsdinhgia) thành tuỳ chọn: thay thẻ của bot khi báo vùng, hết hạn thì về thẻ của bot",
        "Altcoin tắt: thanh ghi “Altcoin (tắt)”, trang /altcoin/ chỉ cách bật (cùng mật khẩu)",
        "README: bảng lệnh Telegram/Zoom, cách xem trang Altcoin; /phienban chỉ in các bản gần nhất",
        "Chỉ là thông tin: không đổi quyết định, stop/target hay cổng chặn của Quant",
    )),
    ("v1.16.0", "2026-10-29", (
        "Định giá tham khảo: P/B (ngân hàng, chứng khoán, bảo hiểm, BĐS) hoặc P/E theo giá đóng cửa gần nhất",
        "So với mọi phiên từ 2018 của chính mã: “rẻ hơn X% số phiên”; sổ sách đúng quý đã công bố tại mỗi phiên",
        "Giá chưa điều chỉnh × số cp của chính phiên đó: không lệch qua ngày chia thưởng",
        "VCI chưa đăng phiên hôm nay: giá từ nến của Quant (trừ ngày GDKHQ chia thưởng); hỏi lại VCI mỗi giờ",
        "Bỏ dòng “P/B hiện tại” cũ (theo giá ngày VCI cập nhật)",
        "P/E từ LNST công ty mẹ 4 quý, không từ EPS quý (VCI có quý để EPS bằng 0)",
        "Phân tích ngày cũ: bỏ P/E, P/B, vốn hoá, so sánh ngành của VCI (tính theo giá sau ngày đó)",
        "CIR nay in số dương (VCI trả số âm)",
        "Thẻ định giá ngân hàng (Bear · eab · Base · Bull, MOS, vùng Rẻ/Hợp lý/Đắt): sau báo cáo Quant, /nhanh",
        "Thẻ: sổ sách/cp đúng ngày, ROE trung vị 5 năm, Ke 12% (VALUATION_KE_BANK) và ±2 điểm, nhãn FACT/THỐNG KÊ",
        "Cổ tức tiền tính trên số cp trước GDKHQ; báo phát hành riêng lẻ/ESOP/quyền mua sau ngày khoá sổ",
        "Output Guard: giá trị hợp lý AI nêu phải khớp thẻ (±0,5%); không bắt nhầm mốc Quant, phe Bull/Bear, VCI",
        "Thẻ anh nhập /tsdinhgia MÃ BEAR BASE BULL (cả /taisan, cạnh thẻ của bot): báo khi vào Rẻ/Đắt, sau ⛔/🎯",
        "Chỉ là thông tin: không đổi quyết định, stop/target hay dòng tiền của Quant",
    )),
    ("v1.15.0", "2026-10-29", (
        "Bot Altcoin (OKX) chạy cùng server, là tiến trình con có giám sát; mặc định tắt, bật bằng ALTCOIN_ENABLED=1",
        "Altcoin dùng DB riêng (data/altcoin), không bao giờ dùng DB, token Telegram hay khoá Zoom của Cổ phiếu",
        "Chạy thật (live) chỉ khi đủ ALT_MODE, ALT_I_UNDERSTAND_THE_RISK, ALT_LIVE_ACK và khoá ALT_OKX_*",
        "ALT_MODE=live mà thiếu điều kiện: Altcoin không chạy (không lùi về paper trên DB live)",
        "Tự chặn DB rỗng, DB của Cổ phiếu và instance Altcoin thứ hai; Altcoin lỗi thì tự chạy lại",
        "Chặn chạy paper khi DB còn lệnh live đang mở (và ngược lại); ALTCOIN_FRESH_START chỉ cho paper",
        "/health thêm trạng thái Altcoin; log Altcoin có tiền tố [altcoin], đã che bí mật",
        "Trang Altcoin ở /altcoin/, cùng mật khẩu WEB_PASSWORD; thanh chuyển Cổ phiếu / Altcoin trên mọi trang",
        "Sai mật khẩu ở Cổ phiếu hay Altcoin đều tính chung để khoá; form gửi từ trang khác bị chặn ở cả hai",
        "/altcoin/hub.json (cần mật khẩu): trạng thái chi tiết của Altcoin; Altcoin tắt hay lỗi thì báo rõ lý do",
        "Cài đặt Altcoin: kênh Telegram/Zoom do Cổ phiếu quản lý; /altcoin/zoom chuyển sang /zoom của Cổ phiếu",
        "Lệnh Altcoin trên bot Telegram và app Zoom của Cổ phiếu: /alt <lệnh> (hoặc /altcoin), Zoom gõ alt <lệnh>",
        "/alt close, apply, resume phải gõ thêm xacnhan trong 120 giây; /close, /status... gõ trần chỉ nhận gợi ý",
        "Tin Altcoin tới cùng chat và Zoom của Cổ phiếu, mọi phần mở đầu “🪙 Altcoin ›”; công tắc Altcoin trên /ai",
        "Thông báo Altcoin chống trùng, tối đa 15 tin/phút (tin 🛑/⚠️ luôn gửi); /alt zoomtest gửi thử từng kênh",
        "Kèm Altcoin 3.2.1: khởi động lại server không còn cắt giữa lúc đặt lệnh live và lúc ghi DB",
        "Khởi động lại server: tin Altcoin của những giây cuối (mở, đóng lệnh) được gửi xong rồi mới tắt",
        "Altcoin bị kill giữa lúc ghi SQLite không còn kẹt khi chạy lại: DB tự phục hồi như bot chạy riêng",
        "Trang /altcoin/ báo đỏ khi tin Altcoin không tới được Cổ phiếu hoặc có khoá bị che đã bị bỏ",
        "Mã bản dựng của Cổ phiếu tính cả altcoin/hosted.py (file nối hai bot)",
        "Cần Python 3.12 trở lên",
    )),
    ("v1.14.0", "2026-10-28", (
        "Công tắc MUA breakout của Quant Engine (trang Bot giao dịch): tắt để thử trên sổ ảo, mặc định bật như cũ",
        "Tắt: mã breakout chỉ được THEO DÕI; kết quả bóng của chúng thành nhóm riêng ở /tinhieu và /vikhongmua",
        "Hiệu chỉnh đêm không đổi công tắc này; khi lưu một thay đổi chỉ ghi tham số của chính chiến lược đó",
    )),
    ("v1.13.0", "2026-10-28", (
        "Kho dữ liệu như bot OKX: SQLite data/bot.db, hoặc PostgreSQL dùng chung khi đặt DATABASE_URL",
        "Ghi quyết định Quant Engine cho mọi mã mỗi lượt quét, cổng chặn MUA và quyết định nếu thị trường tốt",
        "/vikhongmua và trang /tinhieu: vì sao chưa có MUA, mã suýt MUA, kết quả bóng 5/10/20 phiên kèm t",
        "Trang /dulieu: tải CSV/JSON, dựng lại tới 250 phiên đã qua, nhật ký hoạt động; check_db.py",
        "Che token, khoá, mật khẩu và DATABASE_URL trong mọi dòng log; lượt 15:30 thêm đoạn Vì sao chưa có MUA",
    )),
    ("v1.12.1", "2026-10-26", (
        "Đổi chiều trong phiên: giá và % lấy từ bảng giá ngay lúc gửi tin (một lần hỏi cho mọi mã)",
    )),
    ("v1.12.0", "2026-10-26", (
        "Siêu tín hiệu, đổi chiều trong phiên: thêm tên công ty và % so với giá tham chiếu cạnh giá hiện tại",
    )),
    ("v1.11.0", "2026-10-26", (
        "Đổi tên thành AI thẩm định tín hiệu (lệnh và trang /thamdinh): ghi lại để kiểm chứng, không phải lệnh",
        "Lệnh /chonbong và địa chỉ /chonbong cũ vẫn dùng được, tự chuyển sang /thamdinh",
    )),
    ("v1.10.1", "2026-10-26", (
        "AI thẩm định tín hiệu: bản ghi trước v1.8 ghi rõ là bản cũ, không tìm tin, thay lý do cũ dễ hiểu nhầm",
    )),
    ("v1.10.0", "2026-10-26", (
        "Chạy tay AI thẩm định tín hiệu (nút trên web, /thamdinh chay hoặc ngay) luôn hỏi lại với dữ liệu mới nhất",
        "Trong phiên: giá khớp hôm nay, mã bot ước tính vừa qua mức MUA; kết quả tạm, 15:50 hỏi lại",
        "Sau 15:10: tín hiệu đã chốt của hôm nay; trước giờ mở cửa: phiên gần nhất, có ghi rõ",
        "Trang web: dưới nút ghi bấm lúc này dùng dữ liệu nào; bấm xong hiện đang chạy, rồi kết quả hoặc lỗi",
    )),
    ("v1.9.0", "2026-10-26", (
        "Bỏ hẳn tên cũ: lệnh /thamdinh, trang /thamdinh, biến AI_PICK_*, file ai_pick.json",
        "Dữ liệu, cài đặt và biến môi trường theo tên cũ được tự chuyển sang tên mới khi khởi động",
        "Hai model: Pro chọn mã buổi chiều, Flash xem cảnh báo và tổng kết tuần (AI_PICK_MODEL_FAST)",
        "Model này lỗi hoặc hết hạn mức thì tự chuyển sang model kia; tin nhắn ghi rõ model nào trả lời",
    )),
    ("v1.8.0", "2026-10-26", (
        "AI thẩm định tín hiệu: bỏ API trả phí, không còn chi phí USD",
        "AI là Gemini web trên 9Router (UniRouter), tự tìm tin trên web",
        "Hỏi bằng chữ kèm JSON Schema, đọc một khối JSON; không đúng mẫu thì hỏi lại một lần",
        "Nguồn tin chỉ giữ link tới các trang tin tài chính đã định",
        "Tính vào hạn mức AI ngày của 9Router",
    )),
    ("v1.7.0", "2026-10-26", (
        "AI thẩm định tín hiệu: thiếu khoá API trả phí, API lỗi hoặc chạm trần USD thì 9Router trả lời thay",
        "9Router dùng chung hạn mức ngày với các agent; không tìm tin nên không có nguồn, không tính vào trần USD",
        "Mỗi tin nhắn, bản ghi và trang web ghi rõ model nào trả lời, và vì sao",
        "Giao diện Coral: giấy xám ấm, bản ghi như tờ giấy cotton, nút in nổi, chữ Be Vietnam Pro",
        "Trang AI thẩm định tín hiệu mở đầu bằng phiếu chọn của phiên, mã được chọn đóng dấu Đã chọn",
        "Số thiếu ghi chưa có thay gạch ngang; axe 0 lỗi ở mọi trang, sáng và tối, 390 và 1440 px",
    )),
    ("v1.6.0", "2026-10-25", (
        "Giao diện web mới kiểu sổ cái không màu: nền xanh xám, thẻ trắng viền mỏng, chữ Geist, số dạng bảng",
        "Thanh bên trái thay cho đầu trang; trên điện thoại gập thành thanh 48 px và menu toàn màn hình",
        "Màu chỉ ở dữ liệu: đường danh mục xanh, lãi/lỗ xanh lá/đỏ; không bóng đổ, không chuyển màu, không emoji",
        "Trang Cài đặt /caidat: 49 tham số trong 8 nhóm sửa ngay trên web, lưu data/settings.json, ưu tiên hơn env",
        "Mỗi mục ghi rõ lấy từ đâu (web / biến môi trường / mặc định) và có hiệu lực ngay hay sau khởi động lại",
        "Khoá và token chỉ hiện đã đặt / chưa đặt, không sửa trên web; mọi trang axe 0 lỗi ở 390 và 1440 px",
    )),
    ("v1.5.1", "2026-10-24", (
        "Rà soát độc lập lớp kỷ luật: vé giữ có lý do bị thay bằng ⛔ ở mức cắt lỗ đang lưu, kể cả mức nâng sau",
        "/tshuy lệnh bán mở lại việc treo; vé của mã đã bán hết được dọn; hạn bán theo T+2; /tsgiu giữ mục tiêu",
        "Chấm điểm ghép theo khoá, không theo vị trí dòng; giá bổ sung cùng cơ sở chưa điều chỉnh quyền",
        "Chấm lại quy tắc: mua khi trang BÁN = bán ngay phiên sau; cổ phiếu thưởng đi theo lệnh mua",
        "AI thẩm định tín hiệu: phiên lỗi chưa tốn tiền được hỏi lại; lượt đang chạy từ web không bị ghi là xong",
        "Chữ: mốc VNINDEX so theo cửa sổ hợp với mốc (2/6/12 tháng), số thập phân kiểu Việt, đếm vé đến hạn",
        "/tsbaocao và bản web chỉ đọc, chỉ bản 15:30 mới mở việc treo; 🔴 chỉ khi trang đã chốt",
        "Nhắc 14:20 chạy ở mọi TRADE_MODE; trang /taisan hết tràn ngang ở 320px, axe 0 lỗi; số quá dài bị từ chối",
    )),
    ("v1.5.0", "2026-10-24", (
        "Tài sản: mỗi ⛔/🔴 thành việc treo có hạn bán, kèm cái giá của chần chừ; nhắc 14:20 ngày hạn",
        "Giữ có lý do: /tsgiu MÃ CẮTLỖ lý_do (bắt buộc cắt lỗ), bot chấm sau 5/10/20 phiên",
        "Kiểm tra trước khi mua /tskiemtra: ngân sách 2% tài sản, ⛔ bình quân giá xuống, mua khi trang BÁN",
        "Mọi /tsmua gắn thẻ quy tắc; cắt lỗ lấy của trang khi thiếu, không tự hạ, hạ tay thì ghi lại",
        "Rủi ro đang mở và sàn tài sản (/tssan) trong báo cáo và trang web; chỉ thông tin, không khoá lệnh",
        "Thứ Sáu: thước kỷ luật đo bằng code và mục tiêu hoàn vốn trên số đo (/tsmuctieu), không dự báo",
        "🟠 không đẩy trong phiên nữa (INTRADAY_EARLY=1 bật lại); AI thẩm định tín hiệu: sửa 18 lỗi theo rà soát",
    )),
    ("v1.4.0", "2026-10-24", (
        "AI thẩm định tín hiệu đo nhanh hơn: giá sau 10 phiên, chỉ tính phiên có từ 2 lệnh, cả bảng xếp hạng",
        "Thêm ứng viên bot ước tính trong 300 mã; kết luận ở mốc 20 (sơ bộ) và 40 phiên có lựa chọn",
        "AI xem danh mục mỗi chiều: GIỮ / CÂN NHẮC BÁN từng mã, đo bằng giá 10 phiên sau",
        "Có cảnh báo ⛔/🔴/🟠 thì AI tìm tin của mã đó; tổng kết tuần về kỷ luật (/thamdinh tuan)",
    )),
    ("v1.3.0", "2026-10-24", (
        "AI thẩm định tín hiệu: mỗi phiên AI đọc các lệnh MUA mới của trang, tìm tin, chọn 1 mã, nêu mã nên tránh",
        "Chỉ ghi để đo, không phải lệnh: mọi mã chấm theo ⛔/🔴, so với TB các lệnh cùng ngày (cần 50 phiên)",
        "Lệnh /thamdinh, /thamdinh chay, /thamdinh lichsu (Telegram và Zoom) và trang web trong Tiện ích",
        "Cần khoá API trả phí và SIEUTINHIEU=1; trần chi phí mặc định 10 USD mỗi tháng",
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
    extra = [p for p in (ROOT / "main_botkeep.py", ROOT / "altcoin" / "hosted.py") if p.exists()]
    return [p for p in files + extra if p.resolve() != Path(__file__).resolve()]


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


TEXT_LIMIT = 3800       # /phienban stays one Telegram message (3.900 characters): older releases left to the web page


def text(history: int = 3) -> str:
    """``/phienban`` (Telegram, Zoom): the running version and up to ``history`` releases, newest first,
    as many as fit in ``TEXT_LIMIT`` characters (the newest always); the rest are on the web page."""
    lines = [f"🏷️ TradingAgents VN {VERSION} (phát hành {_ddmmyyyy(RELEASED)})",
             f"Bản dựng {build()} · {status_line()}",
             f"Chạy từ {started_text()} ({uptime()}) · Python {platform.python_version()}"]
    tail = ["", "Các bản trước và đủ lịch sử: trang /phienban trên web"]
    for i, (version, day, notes) in enumerate(NOTES[:history]):
        block = ["", f"{'Có gì mới ở ' if version == VERSION else ''}{version} ({_ddmmyyyy(day)}):"] + [f"• {n}" for n in notes]
        if i and len("\n".join(lines + block + tail)) > TEXT_LIMIT:
            break
        lines += block
    return "\n".join(lines + tail)


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
