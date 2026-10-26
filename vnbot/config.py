"""The settings on the web (/caidat): the bot's parameters, their meaning and their bounds.

The bot reads its parameters from the environment (Botkeep's variables or a ``.env``).
This module lets the owner change the non-secret ones from the web page: a value saved
there is kept in ``settings.json`` under the data directory and written into the
environment on every start (``apply``, called by ``settings.load`` before anything reads
the variables) and again when saved, so the modules that read the environment at call
time (Siêu tín hiệu, Tài sản, AI chọn bóng, the AI budget) follow it at once. The
values frozen into ``Settings`` at start (the schedule, the trading bot, the daily
screen) take effect after a restart; ``Field.restart`` says which.

Secrets (tokens, keys, the password) are never shown, stored or editable here: the page
only says whether each is set. A saved value wins over the environment variable; the
page shows where every value comes from and "Về mặc định" removes the saved one.
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

FILE_NAME = "settings.json"
MAX_LEN = 400          # the longest value the form accepts (a watchlist of 60 codes)


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    help: str
    kind: str                        # int | float | hhmm | flag | choice | text | list | ids
    default: str
    group: str
    restart: bool = False            # frozen into Settings at start: a change needs a restart
    lo: float | None = None
    hi: float | None = None
    options: tuple[tuple[str, str], ...] = ()   # (value, label) for a choice
    unit: str = ""
    max_items: int | None = None


@dataclass(frozen=True)
class Group:
    key: str
    title: str
    help: str
    fields: tuple[Field, ...] = field(default_factory=tuple)


_F = Field

FIELDS: tuple[Field, ...] = (
    # Lịch chạy
    _F("DAILY_REPORT_TIME", "Giờ sàng lọc 15:30", "Giờ Việt Nam các ngày giao dịch: Quant Engine sàng lọc rổ mã và watchlist, "
       "AI phản biện các mã nổi bật, gửi Telegram/Zoom.", "hhmm", "15:30", "lich", restart=True),
    _F("TRADE_TIME", "Giờ bot giao dịch ảo", "Bỏ trống: theo giờ sàng lọc nếu sau 15:05, ngược lại 15:30. Không bao giờ chạy "
       "trước 15:05 (nến ngày chưa chốt).", "hhmm", "", "lich", restart=True),
    _F("STV_MORNING_TIME", "Giờ bản tin sáng", "Bản tin Siêu tín hiệu vào Zoom (hoặc Telegram) các ngày giao dịch; phải trước 11:30.",
       "hhmm", "08:30", "lich"),
    _F("CLAUDE_PICK_TIME", "Giờ AI chọn bóng", "Sau khi trang chốt tín hiệu; không sớm hơn 15:10.", "hhmm", "15:50", "lich"),
    # Sàng lọc & AI
    _F("WATCHLIST", "Watchlist mặc định", "Các mã theo dõi khi chưa lưu danh sách trên trang Tổng quan (danh sách lưu ở đó được ưu tiên).",
       "list", "", "sangloc", restart=True, max_items=30),
    _F("SCAN_UNIVERSE", "Rổ mã sàng lọc", "Lượt 15:30 xét rổ này cộng thêm watchlist.", "choice", "vn100", "sangloc", restart=True,
       options=(("vn100", "VN100"), ("vn30", "VN30"), ("watchlist", "Chỉ watchlist"))),
    _F("DAILY_AI_MODE", "AI ở lượt tự động", "critic: mỗi mã một lượt phản biện; full: chuỗi agent, khoảng 25 lượt mỗi mã; off: không gọi AI.",
       "choice", "critic", "sangloc", restart=True,
       options=(("critic", "AI phản biện (1 lượt/mã)"), ("full", "Phân tích chuyên sâu (~25 lượt/mã)"), ("off", "Không dùng AI"))),
    _F("DAILY_AI_MAX", "Số mã gửi AI mỗi ngày", "Những mã mạnh nhất có hành động trong danh sách dưới; 0 = không gọi AI.",
       "int", "5", "sangloc", restart=True, lo=0, hi=30, unit="mã"),
    _F("DAILY_AI_ACTIONS", "Hành động đáng hỏi AI", "Các hành động của Quant Engine được gửi AI phản biện (BUY, SELL, HOLD, WATCH).",
       "list", "BUY,SELL", "sangloc", restart=True, max_items=4),
    _F("LLM_MAX_CALLS_PER_DAY", "Hạn mức lượt AI mỗi ngày", "Tổng số lượt gọi AI mỗi ngày, đếm theo giờ Việt Nam; 0 = không giới hạn.",
       "int", "60", "sangloc", lo=0, hi=5000, unit="lượt"),
    _F("MAX_DEBATE_ROUNDS", "Vòng tranh luận", "Số vòng tranh luận bull/bear trong phân tích chuyên sâu.", "int", "1", "sangloc",
       restart=True, lo=1, hi=3),
    _F("MAX_RISK_ROUNDS", "Vòng thảo luận rủi ro", "Số vòng của ba agent rủi ro.", "int", "1", "sangloc", restart=True, lo=1, hi=3),
    _F("MAX_TOOL_ROUNDS", "Lượt gọi công cụ mỗi agent", "Mỗi agent phân tích được gọi dữ liệu tối đa chừng này lần.", "int", "8",
       "sangloc", restart=True, lo=1, hi=20),
    _F("KEEP_REPORTS", "Số báo cáo giữ lại", "Báo cáo phân tích chuyên sâu cũ hơn bị xoá.", "int", "60", "sangloc", restart=True,
       lo=5, hi=500, unit="báo cáo"),
    _F("CACHE_MAX_AGE_DAYS", "Tuổi bộ nhớ đệm dữ liệu", "Dữ liệu giá tải về được dùng lại trong chừng này ngày.", "int", "3", "sangloc",
       restart=True, lo=1, hi=30, unit="ngày"),
    _F("MAX_PENDING", "Yêu cầu chờ tối đa", "Số lượt phân tích từ web và Telegram được xếp hàng cùng lúc.", "int", "10", "sangloc",
       restart=True, lo=1, hi=50),
    _F("QUICK_MODEL", "Model tầng nhanh", "Model trên 9Router cho các agent; bỏ trống = ROUTER9_MODEL.", "text", "", "sangloc", restart=True),
    _F("DEEP_MODEL", "Model tầng sâu", "Model cho Research Manager và Portfolio Manager; bỏ trống = ROUTER9_MODEL.", "text", "", "sangloc",
       restart=True),
    # Cảnh báo trong phiên
    _F("INTRADAY_ALERTS", "Cảnh báo trong phiên", "Trong giờ khớp lệnh, đọc giá khớp và báo cắt lỗ, mục tiêu, vùng mua, VNINDEX giảm mạnh; "
       "không dùng AI. Có thể tắt tạm ở trang AI & cảnh báo.", "flag", "1", "canhbao", restart=True),
    _F("INTRADAY_INTERVAL_MIN", "Chu kỳ kiểm tra", "Bao nhiêu phút một lần trong giờ khớp lệnh liên tục.", "int", "15", "canhbao",
       restart=True, lo=5, hi=60, unit="phút"),
    _F("INTRADAY_VNI_DROP_PCT", "Ngưỡng VNINDEX giảm", "Báo khi VNINDEX giảm chừng này % trong phiên (và khi giảm gấp đôi).",
       "float", "2", "canhbao", restart=True, lo=0.5, hi=10, unit="%"),
    _F("INTRADAY_EARLY", "Đẩy dấu hiệu bán sớm", "Nhắn khi một mã có dấu hiệu BÁN sớm giữa phiên. Đo trên dữ liệu: bán sớm không hơn "
       "chờ tín hiệu chốt, nên mặc định tắt.", "flag", "0", "canhbao", restart=True),
    # Bot giao dịch ảo
    _F("TRADE_MODE", "Chế độ bot", "paper: ba tài khoản ảo; signal: như paper và gửi lệnh đề xuất cho tài khoản thật; off: tắt bot. "
       "Bot không bao giờ đặt lệnh tới công ty chứng khoán.", "choice", "paper", "bot", restart=True,
       options=(("paper", "Tài khoản ảo"), ("signal", "Tài khoản ảo kèm tín hiệu thật"), ("off", "Tắt"))),
    _F("PAPER_CAPITAL", "Vốn tài khoản ảo", "Vốn khởi điểm cho tài khoản ảo mới; đổi vốn của tài khoản đang chạy ở trang Bot giao dịch.",
       "int", "1000000000", "bot", restart=True, lo=10_000_000, hi=1_000_000_000_000, unit="đ"),
    _F("UNIVERSE_VN30", "Giao dịch rổ VN30", "Bật: VN30 cộng watchlist; tắt: chỉ watchlist.", "flag", "1", "bot", restart=True),
    _F("AUTO_CALIBRATE", "Tự hiệu chỉnh hằng đêm", "Quant Engine và Động lượng: tối đa một điều chỉnh nhỏ mỗi đêm; Phòng thủ giữ bộ đã kiểm định.",
       "flag", "1", "bot", restart=True),
    _F("SIGNAL_STRATEGY", "Chiến lược cho tín hiệu thật", "Ở chế độ signal, lệnh đề xuất theo chiến lược này.", "choice", "factor", "bot",
       restart=True, options=(("factor", "Phòng thủ"), ("momentum", "Động lượng VN30"), ("engine", "Quant Engine"))),
    # Siêu tín hiệu
    _F("SIEUTINHIEU", "Theo dõi sieutinhieu.vn", "Mỗi tối lấy tín hiệu của trang cho các mã đang giữ và watchlist riêng, chạy một sổ ảo theo "
       "tín hiệu đó. AI chọn bóng cần mục này bật.", "flag", "0", "stv"),
    _F("STV_WATCHLIST", "Watchlist riêng của Siêu tín hiệu", "Tối đa 60 mã; bỏ trống = 60 mã mặc định. Danh sách sửa bằng /stvtheodoi được ưu tiên.",
       "list", "", "stv", max_items=60),
    _F("STV_SCAN_MINUTES", "Chu kỳ hỏi trang trong phiên", "Bao nhiêu phút một lần bot hỏi tín hiệu các mã đang giữ, 09:00-17:00 ngày giao dịch.",
       "int", "15", "stv", lo=5, hi=120, unit="phút"),
    _F("STV_WATCH_INTRADAY", "Quét watchlist trong phiên", "Tắt: watchlist quét sau giờ đóng cửa (tín hiệu từng mã của trang chỉ có sau 15:08).",
       "flag", "0", "stv"),
    _F("STV_ESTIMATE", "Bot ước tính đổi chiều", "Trong phiên, so giá khớp với mức đổi chiều tính từ giá đóng cửa, ghi rõ là ước tính của bot.",
       "flag", "1", "stv"),
    _F("STV_MARKET", "Quét toàn thị trường", "Các mã thanh khoản cao nhất, bằng cùng cách tính; danh sách lúc 14:46 và bản đối chiếu sau 15:15.",
       "flag", "1", "stv"),
    _F("STV_MARKET_SIZE", "Số mã quét toàn thị trường", "", "int", "300", "stv", lo=50, hi=500, unit="mã"),
    # Danh mục Tài sản
    _F("TAISAN_RISK_PCT", "Ngân sách rủi ro một lệnh", "Một lệnh mua được phép mất chừng này % tài sản nếu chạm cắt lỗ (kiểm tra lệnh mua).",
       "float", "2", "taisan", lo=0.1, hi=10, unit="%"),
    _F("TAISAN_OPEN_RISK_PCT", "Trần rủi ro đang mở", "Tổng mọi khoản lỗ dự kiến tới cắt lỗ không quá chừng này % tài sản.", "float", "6",
       "taisan", lo=1, hi=30, unit="%"),
    _F("TAISAN_MAX_WEIGHT_NEW", "Tỷ trọng tối đa sau khi mua", "Một mã không quá chừng này % tài sản sau lệnh mua.", "float", "30", "taisan",
       lo=5, hi=100, unit="%"),
    # AI chọn bóng
    _F("CLAUDE_PICK", "AI chọn bóng", "Mỗi phiên AI (qua 9Router) đọc các lệnh MUA mới và danh mục, tìm tin trên web, chọn một mã để đo. "
       "Cần ROUTER9_BASE_URL, ROUTER9_API_KEY và Siêu tín hiệu bật.", "flag", "1", "claude"),
    _F("CLAUDE_PICK_MODEL", "Model trên 9Router", "Model có tìm web, ví dụ gmw/gemini-pro hoặc gmw/gemini-flash (Gemini web của "
       "UniRouter). Tên model Claude cũ được bỏ qua.", "text", "gmw/gemini-pro", "claude"),
    _F("CLAUDE_PICK_BOT", "Thêm mã bot ước tính", "Thêm vào ứng viên các mã bot ước tính vừa đổi sang MUA (cần quét toàn thị trường).",
       "flag", "1", "claude"),
    _F("CLAUDE_PICK_ALERTS", "Xem tin khi có cảnh báo", "Số lần tối đa mỗi ngày AI tìm tin khi một mã đang giữ có cảnh báo; 0 = tắt.",
       "int", "5", "claude", lo=0, hi=20, unit="lần"),
    _F("CLAUDE_PICK_WEEKLY", "Tổng kết tuần", "Sau phiên cuối tuần, AI nhận xét kỷ luật so với quy tắc.", "flag", "1", "claude"),
    # Kết nối & trang web
    _F("PUBLIC_URL", "Địa chỉ công khai", "Để bot gửi kèm link báo cáo và khai báo với Zoom, ví dụ https://abc.botkeep.cloud.", "text", "",
       "ketnoi", restart=True),
    _F("SUPPORT_EMAIL", "Email hỗ trợ", "Hiện trên các trang công khai mà Zoom yêu cầu.", "text", "", "ketnoi", restart=True),
    _F("TELEGRAM_ALLOWED_IDS", "Telegram id được phép", "Các id được dùng bot, cách nhau dấu phẩy (gửi /id cho bot để biết id).", "ids", "",
       "ketnoi", restart=True, max_items=20),
    _F("TELEGRAM_REPORT_CHAT_IDS", "Nơi nhận báo cáo", "Bỏ trống = gửi cho các id được phép.", "ids", "", "ketnoi", restart=True, max_items=20),
)

GROUPS: tuple[Group, ...] = tuple(
    Group(k, t, h, tuple(f for f in FIELDS if f.group == k)) for k, t, h in (
        ("lich", "Lịch chạy", "Giờ Việt Nam, các ngày giao dịch; ngày lễ tự bỏ qua."),
        ("sangloc", "Sàng lọc & AI", "Lượt tự động 15:30 và hạn mức AI. AI chỉ phản biện, không đổi hành động hay mức giá."),
        ("canhbao", "Cảnh báo trong phiên", "Không dùng AI: cắt lỗ, mục tiêu, vùng mua và các nhịp giảm mạnh của VNINDEX."),
        ("bot", "Bot giao dịch ảo", "Ba tài khoản ảo. Bot không bao giờ đặt lệnh tới công ty chứng khoán."),
        ("stv", "Siêu tín hiệu", "So sánh với sieutinhieu.vn, dùng cá nhân."),
        ("taisan", "Danh mục Tài sản", "Ngân sách rủi ro cho sổ tiền thật; chỉ để kiểm tra, không khuyên mua."),
        ("claude", "AI chọn bóng", "Chọn bóng để đo, không phải lệnh. AI là model trên 9Router (UniRouter), mặc định Gemini có tìm web."),
        ("ketnoi", "Kết nối & trang web", "Khoá và token chỉ đặt được trên Botkeep; trang này chỉ cho biết đã có hay chưa."),
    ))

BY_KEY: dict[str, Field] = {f.key: f for f in FIELDS}

# Shown as set / not set only: never their values, never editable here.
SECRETS: tuple[tuple[str, str], ...] = (
    ("WEB_PASSWORD", "Mật khẩu trang web"), ("TELEGRAM_TOKEN", "Telegram token"),
    ("ROUTER9_API_KEY", "Khoá 9Router"), ("GROQ_API_KEY", "Khoá Groq"), ("OPENROUTER_API_KEY", "Khoá OpenRouter"),
    ("XAI_API_KEY", "Khoá xAI"), ("ZOOM_CLIENT_ID", "Zoom client id"), ("ZOOM_CLIENT_SECRET", "Zoom client secret"),
    ("ZOOM_SECRET_TOKEN", "Zoom secret token"), ("ZOOM_BOT_JID", "Zoom bot JID"), ("ZOOM_ACCOUNT_ID", "Zoom account id"),
)

_FLAG_ON = ("1", "true", "yes", "on", "co", "có")
_CODE = re.compile(r"^[A-Z0-9]{2,12}$")
_TEXT = re.compile(r"^[^\x00-\x1f]*$")

_lock = threading.Lock()
_env_before: dict[str, str | None] = {}     # the environment's own value before an override, per key


def normalize(f: Field, raw: str) -> str:
    """The canonical text of ``raw`` for ``f`` (what is stored and put in the environment),
    or ``ValueError`` with a message in Vietnamese naming the field."""
    raw = (raw or "").strip()
    if len(raw) > MAX_LEN:
        raise ValueError(f"{f.label}: giá trị quá dài.")
    if f.kind == "flag":
        return "1" if raw.lower() in _FLAG_ON else "0"
    if f.kind == "choice":
        value = raw.lower()
        if value not in {v for v, _ in f.options}:
            raise ValueError(f"{f.label}: chọn một trong các giá trị có sẵn.")
        return value
    if f.kind == "hhmm":
        if not raw:
            if f.default:
                raise ValueError(f"{f.label}: cần giờ dạng HH:MM.")
            return ""
        try:
            hour, minute = (int(x) for x in raw.replace("h", ":").split(":", 1))
        except ValueError:
            raise ValueError(f"{f.label}: cần giờ dạng HH:MM.") from None
        if not (0 <= hour < 24 and 0 <= minute < 60):
            raise ValueError(f"{f.label}: giờ không hợp lệ.")
        return f"{hour:02d}:{minute:02d}"
    if f.kind in ("int", "float"):
        text = raw.replace(".", "").replace(" ", "") if f.kind == "int" else raw.replace(",", ".")
        try:
            value = int(text) if f.kind == "int" else float(text)
        except ValueError:
            raise ValueError(f"{f.label}: cần một số.") from None
        if (f.lo is not None and value < f.lo) or (f.hi is not None and value > f.hi):
            lo = f"{f.lo:g}".replace(".", ",") if f.lo is not None else ""
            hi = f"{f.hi:g}".replace(".", ",") if f.hi is not None else ""
            raise ValueError(f"{f.label}: trong khoảng {lo} đến {hi}{(' ' + f.unit) if f.unit else ''}.")
        return str(value) if f.kind == "int" else f"{value:g}"
    if f.kind == "list":
        items = [x.strip().upper() for x in raw.replace(";", ",").replace(" ", ",").split(",") if x.strip()]
        bad = [x for x in items if not _CODE.match(x)]
        if bad:
            raise ValueError(f"{f.label}: mã không hợp lệ: {', '.join(bad[:3])}.")
        if f.max_items and len(items) > f.max_items:
            raise ValueError(f"{f.label}: tối đa {f.max_items} mục.")
        return ",".join(dict.fromkeys(items))
    if f.kind == "ids":
        items = [x.strip() for x in raw.replace(";", ",").replace(" ", ",").split(",") if x.strip()]
        bad = [x for x in items if not x.lstrip("-").isdigit()]
        if bad:
            raise ValueError(f"{f.label}: id phải là số: {', '.join(bad[:3])}.")
        if f.max_items and len(items) > f.max_items:
            raise ValueError(f"{f.label}: tối đa {f.max_items} id.")
        return ",".join(dict.fromkeys(items))
    if not _TEXT.match(raw):
        raise ValueError(f"{f.label}: có ký tự không hợp lệ.")
    if f.key == "PUBLIC_URL" and raw and not raw.lower().startswith(("http://", "https://")):
        raise ValueError(f"{f.label}: bắt đầu bằng https://.")
    if f.key == "SUPPORT_EMAIL" and raw and ("@" not in raw or " " in raw):
        raise ValueError(f"{f.label}: không phải địa chỉ email.")
    return raw


def path_of(data_dir: Path | str) -> Path:
    return Path(data_dir) / FILE_NAME


def read(data_dir: Path | str) -> dict[str, str]:
    """The saved overrides {KEY: value}, only known keys with text values."""
    try:
        data = json.loads(path_of(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: str(v) for k, v in data.items() if k in BY_KEY and isinstance(v, (str, int, float)) and v is not None}


def _write(data_dir: Path | str, data: dict[str, str]) -> None:
    from vnbot.jobs import write_text_atomic

    write_text_atomic(path_of(data_dir), json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True))


def _put_env(key: str, value: str | None) -> None:
    """Set ``key`` in the environment to ``value``, remembering the environment's own
    value the first time; ``None`` restores that value."""
    if key not in _env_before:
        _env_before[key] = os.environ.get(key)
    if value is None:
        before = _env_before[key]
        if before is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = before
    else:
        os.environ[key] = value


def apply(data_dir: Path | str) -> dict[str, str]:
    """Put the saved overrides into the environment (at start, before the settings are read)."""
    with _lock:
        saved = read(data_dir)
        for key, value in saved.items():
            _put_env(key, value)
    return saved


def env_value(key: str) -> str | None:
    """The environment's own value of ``key`` (Botkeep or .env), ignoring a saved override."""
    if key in _env_before:
        return _env_before[key]
    return os.environ.get(key)


def effective(f: Field) -> str:
    """What the bot sees now: the saved override, else the environment, else the default."""
    raw = os.environ.get(f.key)
    if raw is None or raw.strip() == "":
        return f.default
    try:
        return normalize(f, raw)
    except ValueError:
        return raw.strip()


def source(f: Field, saved: dict[str, str]) -> str:
    """Where the current value comes from: web | env | default."""
    if f.key in saved:
        return "web"
    raw = env_value(f.key)
    return "env" if raw is not None and raw.strip() != "" else "default"


def save(data_dir: Path | str, group_key: str, form: dict[str, str]) -> tuple[list[Field], bool]:
    """Save a group's form: every field of the group is normalized (a flag left out of the
    form is off); the override is kept where it differs from what the environment gives,
    removed where it is the same. Returns the fields that changed and whether one of them
    needs a restart. ``ValueError`` (nothing saved) when a value is out of bounds."""
    group = next((g for g in GROUPS if g.key == group_key), None)
    if group is None:
        raise ValueError("Nhóm cài đặt không tồn tại.")
    wanted: dict[str, str] = {}
    for f in group.fields:
        wanted[f.key] = normalize(f, form.get(f.key, "" if f.kind == "flag" else effective(f)))
    changed: list[Field] = []
    with _lock:
        saved = read(data_dir)
        for f in group.fields:
            before = effective(f)
            env_raw = env_value(f.key)
            try:
                env_norm = normalize(f, env_raw) if env_raw is not None and env_raw.strip() != "" else f.default
            except ValueError:
                env_norm = None
            value = wanted[f.key]
            if value == env_norm:
                saved.pop(f.key, None)
                _put_env(f.key, None)
            else:
                saved[f.key] = value
                _put_env(f.key, value)
            if effective(f) != before:
                changed.append(f)
        _write(data_dir, saved)
    return changed, any(f.restart for f in changed)


def clear(data_dir: Path | str, key: str) -> Field:
    """Remove the saved override of ``key`` (back to the environment or the default)."""
    f = BY_KEY.get(key)
    if f is None:
        raise ValueError("Tham số không tồn tại.")
    with _lock:
        saved = read(data_dir)
        saved.pop(key, None)
        _put_env(key, None)
        _write(data_dir, saved)
    return f


def secret_set(key: str) -> bool:
    return bool((os.environ.get(key) or "").strip())
