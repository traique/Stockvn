"""Cấu hình bot – đọc từ biến môi trường (.env)."""
import os
import time
from dotenv import dotenv_values

# Nạp file .env / bot.env ở thư mục gốc bot (cùng chỗ run.py). Giá trị trong file ĐƯỢC ƯU TIÊN hơn biến khai báo trên panel,
# để chỉ cần dán nguyên danh sách vào 1 file. Dòng để trống hoặc còn dạng <...> (chưa điền) được bỏ qua
# → bí mật đã khai trên panel Botkeep vẫn giữ nguyên.
ENV_FILE_INFO = {"path": None, "loaded": 0, "skipped": 0}
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# bot.env: tên file KHÔNG có dấu chấm đầu → hiện trong File Manager, host không tự ghi đè (khác .env)
_files = []
for _name in (".env", "bot.env"):
    for _dir in (_ROOT, os.getcwd()):
        _p = os.path.join(_dir, _name)
        if os.path.isfile(_p) and _p not in _files:
            _files.append(_p)
            break
for _cand in _files:                                 # bot.env nạp sau → thắng .env
    try:
        for _k, _v in dotenv_values(_cand).items():
            _v = (_v or "").strip()
            if not _k or not _v or (_v.startswith("<") and _v.endswith(">")):
                ENV_FILE_INFO["skipped"] += 1
                continue
            os.environ[_k] = _v
            ENV_FILE_INFO["loaded"] += 1
        ENV_FILE_INFO["path"] = ", ".join(os.path.basename(x) for x in _files)
    except Exception as _e:                          # file hỏng không được làm sập bot
        ENV_FILE_INFO["error"] = f"{os.path.basename(_cand)}: {_e}"

# Múi giờ cho log, báo cáo ngày, Telegram (mặc định Việt Nam)
TIMEZONE = os.getenv("TIMEZONE", "Asia/Ho_Chi_Minh")
os.environ["TZ"] = TIMEZONE
try:
    time.tzset()
except AttributeError:
    pass


def _raw(key, default):
    """Giá trị biến; để trống / sai định dạng → dùng mặc định trong code (không làm sập bot)."""
    v = (os.getenv(key) or "").strip()
    return v if v else str(default)


def _f(key, default):
    try:
        return float(_raw(key, default).replace(",", "."))
    except ValueError:
        return float(default)


def _i(key, default):
    try:
        return int(float(_raw(key, default)))
    except ValueError:
        return int(default)


def _b(key, default="false"):
    return _raw(key, default).lower() in ("1", "true", "yes", "on")


class Config:
    # ---- Chế độ ----
    MODE = os.getenv("MODE", "paper").lower()          # paper | live
    PAPER_START_BALANCE = _f("PAPER_START_BALANCE", 10)

    # ---- OKX ----
    OKX_API_KEY = os.getenv("OKX_API_KEY", "")
    OKX_API_SECRET = os.getenv("OKX_API_SECRET", "")
    OKX_PASSPHRASE = os.getenv("OKX_PASSPHRASE", "")
    OKX_DEMO = _b("OKX_DEMO", "false")                 # dùng tài khoản demo của OKX (x-simulated-trading)

    # ---- Rủi ro ----
    LEVERAGE = _i("LEVERAGE", 10)
    # Rủi ro cơ bản 3%/lệnh (v3.1: 10% khiến 5 lệnh thua liên tiếp mất ~41% vốn – chuỗi thua như vậy là bình thường
    # với tỉ lệ thắng 35–45%). Rủi ro thực tế còn được nhân với hệ số chất lượng (điểm KT, AI) và chuỗi thua – xem risk.py.
    RISK_PER_TRADE = _f("RISK_PER_TRADE", 0.03)        # % vốn mất khi chạm SL (lệnh chất lượng chuẩn)
    RISK_MAX_MULT = _f("RISK_MAX_MULT", 1.5)           # lệnh rất đẹp (AI ≥ 80, điểm ≥ 7) được tối đa 1.5× rủi ro cơ bản
    RISK_MIN_MULT = _f("RISK_MIN_MULT", 0.35)          # không bao giờ xuống dưới 35% rủi ro cơ bản
    LOSS_STREAK_CUT = _f("LOSS_STREAK_CUT", 0.7)       # mỗi lệnh thua liên tiếp (từ lệnh thứ LOSS_STREAK_FROM) nhân rủi ro với 0.7
    # v3.2: co từ lệnh thua thứ 3 (trước: thứ 2). 4 lệnh thua liên tiếp là bình thường với tỉ lệ thắng ~40%; co quá sớm
    # làm bot vào rất nhỏ đúng lúc thị trường đổi chiều (5 ngày chạy ảo: 2 lệnh long cuối chỉ còn 0.35× rủi ro).
    LOSS_STREAK_FROM = _i("LOSS_STREAK_FROM", 3)
    MAX_OPEN_POSITIONS = _i("MAX_OPEN_POSITIONS", 2)   # tối đa 2 lệnh, nhưng tổng rủi ro đang mở ≤ MAX_TOTAL_RISK
    MAX_TOTAL_RISK = _f("MAX_TOTAL_RISK", 0.05)        # tổng % vốn có thể mất nếu mọi lệnh đang mở chạm SL
    MIN_LOT_RISK_MULT = _f("MIN_LOT_RISK_MULT", 2.0)   # lot tối thiểu của sàn làm rủi ro vượt 2× dự tính → bỏ tín hiệu
    MAX_DAILY_LOSS = _f("MAX_DAILY_LOSS", 0.12)        # dừng ngày nếu lỗ 12% vốn đầu ngày (~4 lệnh thua)
    MAX_DRAWDOWN = _f("MAX_DRAWDOWN", 0.30)            # dừng hẳn bot nếu DD từ đỉnh > 30%, chờ /resume
    MIN_RR = _f("MIN_RR", 1.5)                         # tỉ lệ lời/lỗ tối thiểu
    TAKER_FEE = _f("TAKER_FEE", 0.0005)                # OKX taker 0.05%
    SLIPPAGE = _f("SLIPPAGE", 0.0005)                  # mô phỏng trượt giá
    ATR_SL_MULT = _f("ATR_SL_MULT", 2.0)                # SL tối thiểu (ATR) – test 10 ngày: 2.0 tốt hơn 1.5
    ATR_TP_MULT = _f("ATR_TP_MULT", 3.0)
    TRAIL_ACTIVATE_R = _f("TRAIL_ACTIVATE_R", 1.2)     # kéo SL về hoà vốn khi lời 1.2R
    MAX_HOLD_HOURS = _f("MAX_HOLD_HOURS", 12)
    # ---- Cách thoát lệnh ("ăn sóng") ----
    EXIT_MODE = os.getenv("EXIT_MODE", "auto").strip().lower()     # auto (bộ tự học chọn, khởi đầu hybrid) | hybrid | fixed | trail
    TP1_R = _f("TP1_R", 1.0)                            # hybrid: chốt 50% khi lời 1R rồi SL về hoà vốn (tự học chọn lại)
    TP1_FRACTION = _f("TP1_FRACTION", 0.5)              # chốt 50%
    TRAIL_ATR_MULT = _f("TRAIL_ATR_MULT", 2.5)          # phần còn lại bám sóng: SL = đỉnh ∓ 2.5 ATR (altcoin nhiễu mạnh)
    TRAIL_TIGHTEN_R = _f("TRAIL_TIGHTEN_R", 4.0)        # lời ≥ 4R thì siết còn ...
    TRAIL_ATR_MULT_TIGHT = _f("TRAIL_ATR_MULT_TIGHT", 1.5)
    STALL_HOURS = _f("STALL_HOURS", 4)                  # đang bám sóng mà 4h không lập đỉnh mới → thoát
    RUNNER_MAX_HOLD_HOURS = _f("RUNNER_MAX_HOLD_HOURS", 24)
    RUNNER_MAX_TP_ATR = _f("RUNNER_MAX_TP_ATR", 10)     # TP an toàn rất xa cho phần bám sóng

    # ---- Quét coin ----
    TOP_N_COINS = _i("TOP_N_COINS", 50)
    # Loại BTC/ETH, stablecoin và các "token hoá" cổ phiếu / hàng hoá / chỉ số mà OKX niêm yết (không phải altcoin)
    _DEFAULT_EXCLUDE = (
        "BTC,ETH,USDC,USDT,DAI,TUSD,FDUSD,USDE,"
        "XAU,XAG,XPT,XPD,WTI,BRENT,NATGAS,"
        "TSLA,NVDA,AAPL,MSFT,AMZN,GOOGL,GOOG,META,COIN,MSTR,HOOD,CRCL,PLTR,AMD,INTC,MU,"
        "SNDK,SOXL,SKHYNIX,SPY,QQQ,SPX,NDX,DJI,TQQQ,SQQQ,NFLX,AVGO,ORCL,TSM,ASML,GME,AMC"
    )
    EXCLUDE = [s.strip().upper() for s in os.getenv("EXCLUDE", _DEFAULT_EXCLUDE).split(",") if s.strip()]
    TIMEFRAME = os.getenv("TIMEFRAME", "15m")
    HTF_TIMEFRAME = os.getenv("HTF_TIMEFRAME", "1h")
    SCAN_INTERVAL = _i("SCAN_INTERVAL", 300)           # giây giữa 2 lần quét tín hiệu
    MONITOR_INTERVAL = _i("MONITOR_INTERVAL", 20)      # giây giữa 2 lần kiểm tra SL/TP
    # v3.1: điểm 5.0 (dữ liệu 80 tín hiệu bóng: điểm 4.x–5.x không thua 6.x; chất lượng được phản ánh vào KHỐI LƯỢNG
    # thay vì chặn cứng – xem risk.quality_mult). Bộ tự học vẫn chỉnh trong 4.0–7.0.
    MIN_SCORE = _f("MIN_SCORE", 5.0)                   # điểm hợp lưu tối thiểu để hỏi AI
    # ---- Cổng bắt buộc của tín hiệu ----
    MIN_ADX = _f("MIN_ADX", 20)                        # ADX 15m tối thiểu (lọc sideway)
    HTF_MIN_ADX = _f("HTF_MIN_ADX", 18)                # ADX 1h tối thiểu để công nhận xu hướng
    MIN_ATR_PCT = _f("MIN_ATR_PCT", 0.35)              # ATR% tối thiểu
    MIN_VOL_RATIO = _f("MIN_VOL_RATIO", 0.7)           # volume nến tín hiệu >= 70% trung bình 20 nến (lý do loại #1 là "volume yếu")
    BREAKOUT_VOL = _f("BREAKOUT_VOL", 1.5)
    MAX_EXT_ATR = _f("MAX_EXT_ATR", 1.0)               # giá cách EMA21 tối đa (ATR) – chống đuổi giá (backtest: >1 ATR lỗ)
    MAX_SL_ATR = _f("MAX_SL_ATR", 3.0)                 # SL theo cấu trúc xa hơn mức này → bỏ tín hiệu
    # v3.2: 0.6 → 0.3. 211 tín hiệu bóng: short khi BTC đang +0.3%/1h trở lên cho -0.60R (n=27, thắng 22%).
    BTC_GATE_CHG = _f("BTC_GATE_CHG", 0.3)             # BTC giảm/tăng quá % này trong 1h → cấm long/short altcoin
    # v3.2: không đánh ngược nhịp 1h của chính coin – short khi coin vừa tăng ≥0.5% trong 1h (hoặc long khi vừa giảm)
    # là "bắt đỉnh của nhịp hồi": 56 tín hiệu bóng cho -0.72R, thắng 18%; nhóm còn lại -0.13R.
    CHASE_1H_PCT = _f("CHASE_1H_PCT", 0.5)
    # v3.2: volume nến tín hiệu ≥ 1.5× trung bình là cao trào (climax), không phải xác nhận: 31 tín hiệu bóng -0.68R,
    # thắng 16% (volume < 1.5×: -0.16R). Trừ điểm thay vì cộng, và luật climax_volume chặn sẵn.
    CLIMAX_VOL = _f("CLIMAX_VOL", 1.5)
    # v3.2: ADX 1h ≥ 35 = xu hướng đã chạy lâu, vào lúc này là vào muộn: 76 short bóng -0.44R (ADX 1h < 35: -0.22R).
    LATE_TREND_ADX = _f("LATE_TREND_ADX", 35)
    WICK_CLOSE_POS = _f("WICK_CLOSE_POS", 0.6)         # long: nến tín hiệu đóng >= 60% biên độ (short: <= 40%)
    # Luật chặn sẵn từ đầu (bộ tự học vẫn theo dõi bóng và tự gỡ nếu thực tế có lãi)
    # v3.1: chặn thêm phiên 0h–7h (giờ VN): 14 tín hiệu bóng chỉ thắng 1, kỳ vọng -0.89R. Bộ tự học sẽ gỡ nếu bóng có lãi.
    # v3.2: chặn thêm 13h–19h VN (06–12 UTC): lỗ ở backtest 10 ngày lẫn 5 ngày chạy ảo (-0.33..-0.76R mỗi khung 2h),
    # và nến volume cao trào (climax_volume). 14h–16h UTC (21h–23h VN, Mỹ mở cửa) là khung duy nhất có lãi rõ (+0.42R, n=27).
    DEFAULT_BLOCKS = [x.strip() for x in os.getenv("DEFAULT_BLOCKS", "setup_range,night_session,afternoon_session,climax_volume").split(",") if x.strip()]
    # Setup RANGE: đảo chiều tại biên Bollinger khi 1h đi ngang (có lệnh cả lúc thị trường sideway)
    RANGE_ENABLED = _b("RANGE_ENABLED", "true")
    RANGE_MAX_HTF_ADX = _f("RANGE_MAX_HTF_ADX", 25)      # ADX 1h phải dưới mức này
    RANGE_MAX_ADX = _f("RANGE_MAX_ADX", 25)              # ADX 15m phải dưới mức này
    RANGE_RSI_LONG = _f("RANGE_RSI_LONG", 35)            # long: RSI 15m <= 35 (short: >= 65) trong 3 nến gần nhất
    RANGE_CLOSE_POS = _f("RANGE_CLOSE_POS", 0.6)         # nến từ chối: long đóng >= 60% biên độ
    RANGE_MAX_SL_ATR = _f("RANGE_MAX_SL_ATR", 2.0)
    RANGE_MIN_RR = _f("RANGE_MIN_RR", 1.2)               # tối thiểu RR từ giá tới dải giữa
    RANGE_MIN_SCORE = _f("RANGE_MIN_SCORE", 4.5)        # điểm tối thiểu riêng cho setup range
    RSI_LONG_MIN = _f("RSI_LONG_MIN", 45); RSI_LONG_MAX = _f("RSI_LONG_MAX", 70)
    RSI_SHORT_MIN = _f("RSI_SHORT_MIN", 30); RSI_SHORT_MAX = _f("RSI_SHORT_MAX", 55)

    # ---- AI: nhiều provider OpenAI-compatible, thử theo thứ tự AI_PROVIDER_ORDER ----
    AI_ENABLED = _b("AI_ENABLED", "true")
    AI_PROVIDER_ORDER = [p.strip().lower() for p in os.getenv("AI_PROVIDER_ORDER", "9router,openrouter").split(",") if p.strip()]
    # 9Router – gateway local gom nhiều provider free (npm i -g 9router → http://localhost:20128/v1)
    NINEROUTER_BASE_URL = os.getenv("NINEROUTER_BASE_URL", "http://127.0.0.1:20128/v1").rstrip("/")
    NINEROUTER_API_KEY = os.getenv("NINEROUTER_API_KEY", "9router")
    NINEROUTER_MODELS = [m.strip() for m in (os.getenv("NINEROUTER_MODELS") or os.getenv("NINEROUTER_MODEL") or "").split(",") if m.strip()]
    TIMEZONE = TIMEZONE
    # OpenRouter – model free trực tiếp
    OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
    # "auto" (mặc định) = tự dò các model ':free' còn sống trên OpenRouter, làm mới mỗi 12h.
    # Có thể ghi thêm model cụ thể, ví dụ: auto,google/gemma-4-31b-it:free  (model ghi tay được ưu tiên)
    OPENROUTER_MODELS = [m.strip() for m in os.getenv("OPENROUTER_MODELS", "auto").split(",") if m.strip()]
    OPENROUTER_MAX_MODELS = _i("OPENROUTER_MAX_MODELS", 8)
    # Provider tuỳ ý khác (Groq, Gemini OpenAI-compatible, Ollama...) – để trống nếu không dùng
    CUSTOM_BASE_URL = os.getenv("CUSTOM_BASE_URL", "").rstrip("/")
    CUSTOM_API_KEY = os.getenv("CUSTOM_API_KEY", "")
    CUSTOM_MODELS = [m.strip() for m in os.getenv("CUSTOM_MODELS", "").split(",") if m.strip()]

    AI_MIN_CONFIDENCE = _i("AI_MIN_CONFIDENCE", 60)   # điểm chất lượng AI (50 = tín hiệu trung bình) để duyệt
    AI_TIMEOUT = _i("AI_TIMEOUT", 60)
    NINEROUTER_TIMEOUT = _i("NINEROUTER_TIMEOUT", 30)       # 9router chậm thì bỏ sớm, chuyển OpenRouter
    AI_MAX_TRIES = _i("AI_MAX_TRIES", 6)
    AI_CANDIDATES_PER_SCAN = _i("AI_CANDIDATES_PER_SCAN", 3)  # AI từ chối ứng viên #1 thì hỏi tiếp #2, #3
    # AI cùng hướng nhưng confidence dưới AI_MIN_CONFIDENCE (nhưng ≥ AI_WEAK_CONFIDENCE) → vẫn vào với rủi ro giảm
    # (v3.1: thay vì bỏ hẳn – AI chọn lọc chưa tốt hơn ngẫu nhiên trên dữ liệu đã có, bỏ lệnh = bỏ lợi nhuận)
    AI_WEAK_CONFIDENCE = _i("AI_WEAK_CONFIDENCE", 50)
    AI_MAX_TOKENS = _i("AI_MAX_TOKENS", 1500)                # đủ chỗ cho model suy luận + JSON                     # số model tối đa thử cho 1 câu hỏi
    # Khi TẤT CẢ model AI lỗi: skip = bỏ tín hiệu | score = vẫn vào nếu điểm KT >= AI_FAIL_MIN_SCORE
    # Để trống: chạy ảo = score (không bỏ lỡ dữ liệu), chạy thật = skip (an toàn)
    AI_FAIL_POLICY = (os.getenv("AI_FAIL_POLICY") or "").strip().lower()
    AI_FAIL_MIN_SCORE = _f("AI_FAIL_MIN_SCORE", 7)
    # Phủ quyết của AI: AI phản đối chỉ có hiệu lực khi độ chắc chắn >= AI_VETO_CONF.
    # Phản đối "yếu" (< ngưỡng) với tín hiệu điểm KT >= AI_OVERRIDE_MIN_SCORE thì vẫn vào lệnh.
    # Để trống: chạy ảo = 65, chạy thật = 0 (AI luôn có quyền chặn). Bộ tự học sẽ chỉnh dần.
    AI_VETO_CONF = os.getenv("AI_VETO_CONF", "").strip()
    AI_OVERRIDE_MIN_SCORE = _f("AI_OVERRIDE_MIN_SCORE", 6.5)
    AI_MAX_CALLS_PER_DAY = _i("AI_MAX_CALLS_PER_DAY", 80)   # tổng cả phân tích + tổng kết (thực tế ~20–40/ngày)
    AI_REVIEW_ENABLED = _b("AI_REVIEW_ENABLED", "true")     # gọi AI tổng kết sau mỗi lệnh đóng
    AI_LESSONS_IN_PROMPT = _i("AI_LESSONS_IN_PROMPT", 5)    # số bài học gần nhất đưa vào prompt phân tích
    AI_DAILY_REVIEW = _b("AI_DAILY_REVIEW", "true")          # AI tổng kết chiến lược 1 lần/ngày
    AI_DAILY_REVIEW_HOUR = _i("AI_DAILY_REVIEW_HOUR", 7)     # giờ (theo TIMEZONE)

    # ---- Tự học ----
    LEARN_MODE = os.getenv("LEARN_MODE", "").strip().lower()  # auto | suggest | off (trống: paper=auto, live=suggest)
    LEARN_MIN_SAMPLES = _i("LEARN_MIN_SAMPLES", 30)           # số tín hiệu tối thiểu trước khi thay đổi gì
    # v3.2: 1.3 → 2.0. Với t ≥ 1.3 (p một phía ≈ 0.10) và ~25 nhóm được so cùng lúc, bộ tự học "học" nhiễu rồi đảo ngược
    # sau 48h (5 ngày chạy ảo: trọng số pullback 1.0→1.2→1.0, vwap 1.0→1.17→0.97, chặn cả phía short sau 2 ngày xấu).
    LEARN_MIN_T = _f("LEARN_MIN_T", 2.0)                      # |kỳ vọng| / sai số chuẩn ≥ 2.0 (≈97.5% một phía) mới coi là khác 0
    LEARN_EXIT_MIN_SAMPLES = _i("LEARN_EXIT_MIN_SAMPLES", 60)  # đổi cách thoát lệnh cần ≥ 60 tín hiệu so theo cặp
    LEARN_COOLDOWN_H = _f("LEARN_COOLDOWN_H", 48)             # mỗi tham số đổi tối đa 1 lần / 48h (chống nhảy qua lại)
    LEARN_WINDOW_DAYS = _i("LEARN_WINDOW_DAYS", 21)           # chỉ học từ dữ liệu N ngày gần nhất

    # ---- Telegram ----
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

    # ---- Nhiều instance (Render + Botkeep chạy song song) ----
    DATABASE_URL = os.getenv("DATABASE_URL", "").strip()          # Postgres dùng chung; trống = SQLite 1 máy
    INSTANCE_NAME = os.getenv("INSTANCE_NAME", "") or os.getenv("RENDER_SERVICE_NAME", "") or os.uname().nodename
    LEADER_TTL = _i("LEADER_TTL", 90)                              # giây; leader mất liên lạc quá lâu → máy kia tiếp quản
    PEER_URL = os.getenv("PEER_URL", "").strip()                   # URL /health của instance kia (giữ Render không ngủ)
    SELF_URL = os.getenv("SELF_URL", "").strip() or os.getenv("RENDER_EXTERNAL_URL", "").strip()
    KEEPALIVE_INTERVAL = _i("KEEPALIVE_INTERVAL", 240)

    # ---- Zoom Team Chat ----
    ZOOM_WEBHOOK_URL = os.getenv("ZOOM_WEBHOOK_URL", "").strip()          # Incoming Webhook (gửi thông báo)
    ZOOM_WEBHOOK_TOKEN = os.getenv("ZOOM_WEBHOOK_TOKEN", "").strip()
    ZOOM_CLIENT_ID = os.getenv("ZOOM_CLIENT_ID", "").strip()              # Chatbot (nhận lệnh) – tuỳ chọn
    ZOOM_CLIENT_SECRET = os.getenv("ZOOM_CLIENT_SECRET", "").strip()
    ZOOM_BOT_JID = os.getenv("ZOOM_BOT_JID", "").strip()
    ZOOM_SECRET_TOKEN = (os.getenv("ZOOM_SECRET_TOKEN") or os.getenv("ZOOM_BOT_SECRET_TOKEN") or "").strip()  # Access > Secret Token
    ZOOM_VERIFICATION_TOKEN = os.getenv("ZOOM_VERIFICATION_TOKEN", "").strip()   # cơ chế xác thực cũ (nếu không có Secret Token)
    ZOOM_ACCOUNT_ID = os.getenv("ZOOM_ACCOUNT_ID", "").strip()
    ZOOM_TO_JID = os.getenv("ZOOM_TO_JID", "").strip()                    # tuỳ chọn: JID nhận thông báo cố định
    ZOOM_ALLOWED_USERS = os.getenv("ZOOM_ALLOWED_USERS", "").strip()      # tuỳ chọn: email/JID tự được cấp quyền

    PUBLIC_URL = os.getenv("PUBLIC_URL", "").strip()                      # vd https://okx.bot-keep.xyz – dùng cho OAuth redirect
    SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", "").strip()                # hiện trên trang /privacy, /support

    # ---- Dashboard ----
    # Render cấp PORT; một số host (Botkeep) cấp SERVER_PORT / APP_PORT – bot nhận cái nào có
    DASHBOARD_PORT = _i("PORT", 0) or _i("SERVER_PORT", 0) or _i("APP_PORT", 0) or _i("DASHBOARD_PORT", 8080)
    DASHBOARD_USER = os.getenv("DASHBOARD_USER", "")
    DASHBOARD_PASS = os.getenv("DASHBOARD_PASS", "")

    DATA_DIR = os.getenv("DATA_DIR", "data")
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")


cfg = Config()
