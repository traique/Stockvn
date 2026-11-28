"""Lịch sự kiện vĩ mô – v3.5.1: cửa sổ quanh giờ công bố CPI Mỹ và tuyên bố FOMC.

Chỉ để ĐO (luật bóng ``event_window`` trong strategy.RULES, không nằm trong DEFAULT_BLOCKS): tín hiệu rơi vào cửa sổ
EVENT_WINDOW_MIN phút trước / sau một sự kiện được ghi ``event`` vào context (dòng tín hiệu bóng, bộ tự học đếm theo luật)
để sau đợt đo biết nhóm đó có thua thật không, như mọi luật ứng viên khác. Bot không chặn gì theo lịch này.

Giờ công bố (giờ Mỹ, đổi sang UTC bằng zoneinfo – tự theo giờ mùa hè):
* CPI: 08:30 ET ngày công bố (BLS). Lịch 2026 theo BLS; 2027 BLS chưa công bố lúc viết – thêm bằng EVENT_DATES.
* FOMC: 14:00 ET ngày thứ hai của kỳ họp (federalreserve.gov/monetarypolicy/fomccalendars.htm, đọc 28/11/2026;
  "mỗi ngày họp là tạm thời tới khi được xác nhận ở kỳ họp trước đó").
Thêm / sửa sự kiện: EVENT_DATES="CPI@2027-01-13T08:30ET, FOMC@2027-01-27T14:00ET, UNLOCK-ARB@2027-02-16T00:00Z" (ET = giờ
New York, Z = UTC). Lịch có sẵn bị hết hạn (không còn sự kiện nào sau hôm nay) thì log cảnh báo mỗi ngày một lần.
"""
import logging
import os
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger("calendar")
ET = ZoneInfo("America/New_York")
UTC = timezone.utc

# (tên, 'YYYY-MM-DDTHH:MM', múi giờ) – giờ công bố
BUILTIN = [
    # CPI 2026 (BLS, lịch công bố năm 2026)
    *[("CPI", f"2026-{d}T08:30", "ET") for d in ("01-13", "02-11", "03-11", "04-10", "05-12", "06-10", "07-14", "08-12",
                                                "09-11", "10-14", "11-10", "12-10")],
    # FOMC 2026–2027 (ngày thứ hai của kỳ họp; 2027 là lịch tạm thời của Fed)
    *[("FOMC", f"2026-{d}T14:00", "ET") for d in ("01-28", "03-18", "04-29", "06-17", "07-29", "09-16", "10-28", "12-09")],
    *[("FOMC", f"2027-{d}T14:00", "ET") for d in ("01-27", "03-17", "04-28", "06-09", "07-28", "09-15", "10-27", "11-17")],
]


def _parse(name, stamp, tz):
    dt = datetime.strptime(stamp, "%Y-%m-%dT%H:%M")
    dt = dt.replace(tzinfo=ET if tz.upper() == "ET" else UTC)
    return name.strip().upper(), dt.astimezone(UTC).timestamp()


def _extra():
    """EVENT_DATES: "TÊN@YYYY-MM-DDTHH:MMET, TÊN@...Z" – dòng sai bị bỏ qua với cảnh báo."""
    out = []
    for item in (os.getenv("EVENT_DATES") or "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            name, stamp = item.split("@", 1)
            tz = "ET" if stamp.upper().endswith("ET") else "UTC"
            out.append(_parse(name, stamp[:-2] if tz == "ET" else stamp.rstrip("Zz"), tz))
        except Exception as e:
            log.warning("EVENT_DATES bỏ qua '%s': %s", item, e)
    return out


_EVENTS = None


def events():
    """[(tên, ts UTC)] tăng dần, gồm lịch có sẵn và EVENT_DATES."""
    global _EVENTS
    if _EVENTS is None:
        _EVENTS = sorted([_parse(*e) for e in BUILTIN] + _extra(), key=lambda x: x[1])
    return _EVENTS


def window_min():
    try:
        return max(1, int(float(os.getenv("EVENT_WINDOW_MIN") or 30)))
    except ValueError:
        return 30


def active_event(ts=None):
    """Tên sự kiện nếu ``ts`` nằm trong ±EVENT_WINDOW_MIN phút quanh giờ công bố, '' nếu không."""
    ts = time.time() if ts is None else float(ts)
    w = window_min() * 60
    for name, at in events():
        if at - w <= ts <= at + w:
            return name
        if at - w > ts:
            break
    return ""


def upcoming(ts=None, hours=48):
    """[(tên, ts)] các sự kiện trong ``hours`` giờ tới."""
    ts = time.time() if ts is None else float(ts)
    return [(n, a) for n, a in events() if ts <= a <= ts + hours * 3600]


_warned_day = None


def describe(ts=None, tz="Asia/Ho_Chi_Minh"):
    """Một dòng cho /status: sự kiện đang trong cửa sổ hoặc sự kiện gần nhất sắp tới; cảnh báo khi lịch đã hết."""
    global _warned_day
    ts = time.time() if ts is None else float(ts)
    now = active_event(ts)
    if now:
        return f"📅 Đang trong cửa sổ {now} (±{window_min()} phút) – chỉ ghi, không chặn"
    nxt = [(n, a) for n, a in events() if a > ts]
    if not nxt:
        day = time.strftime("%Y-%m-%d", time.localtime(ts))
        if _warned_day != day:
            _warned_day = day
            log.warning("Lịch sự kiện đã hết (không còn CPI/FOMC nào sau hôm nay) – thêm bằng EVENT_DATES")
        return "📅 Lịch sự kiện đã hết – thêm EVENT_DATES"
    n, a = nxt[0]
    local = datetime.fromtimestamp(a, ZoneInfo(tz)).strftime("%d/%m %H:%M")
    return f"📅 Sắp tới: {n} {local} (giờ VN)"
