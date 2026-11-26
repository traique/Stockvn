"""Đột biến khối lượng: the stocks whose matched volume is far above their 20-session average.

``/dotbien`` (Telegram and Zoom; ``/dotbien 200`` for another threshold): every listed stock
(HOSE, HNX, UPCoM) whose matched volume of the session is at least ``THRESHOLD_PCT`` % of the
average matched volume of the 20 sessions before it - the "VolRelative >= 150% MA20" screen -,
the most extreme first. Put-through deals are not counted (they are not a crowd's volume).
Only stocks that trade at least ``MIN_AVG_VALUE`` a session on average are listed: a 300%
spike on a stock that trades 20 million a day says nothing.

Data: the market history ``vnbot.market_scan.MarketCloses`` keeps (one VNDirect request a
session for the whole market, with volumes since v1.20.0; ``fill_history`` tops it up every
day after the close, and a command fills what is missing for ``FILL_BUDGET`` seconds, the
rest on a background thread). After the close (from ``POSTED_AT``, VNDirect posts the day
~15:11) the list is the session's; in the session it is the volume matched so far against
the full-session average, said so, when the price board gives volumes.

The same history gives the RS score of ``vnbot.soi`` (``MarketCloses.rs_scores``).
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from pathlib import Path

from vnbot import market_scan as ms

logger = logging.getLogger(__name__)

THRESHOLD_PCT = 150            # the screen's default: volume >= 150% of the 20-session average
MIN_THRESHOLD_PCT, MAX_THRESHOLD_PCT = 110, 1000
MIN_AVG_VALUE = 1e9            # VND matched a session on average: below, the stock is not listed
MIN_SESSIONS = 15              # of the last 20 the stock must have traded to have an average
LIST_CAP = 40                  # codes on the headline line
DETAIL_ROWS = 15               # rows with the numbers
FILL_BUDGET = 60.0             # seconds a command spends downloading history before answering
POSTED_AT = "15:15"            # VNDirect has the session's rows from here (~15:11)
SESSION_FROM = "09:15"         # the price board has volumes from the first match
TOPUP_AT = "15:30"             # the daily top-up (service.DailyScheduler)
BAR = "━━━━━━━━━━━━━━━━━━━━━━"

_fill_lock = threading.Lock()
_filling: set[str] = set()


def _now():
    from tradingagents.market import vn_rules

    return vn_rules.now_vn()


def history(data_dir) -> ms.MarketCloses:
    return ms.MarketCloses(Path(data_dir))


def through_day(now: datetime) -> str:
    """The last session whose rows VNDirect may have: today from ``POSTED_AT`` on a session day,
    else the session before."""
    from tradingagents.market import vn_rules

    today = now.strftime("%Y-%m-%d")
    if vn_rules.is_session_day(today) and now.strftime("%H:%M") >= POSTED_AT:
        return today
    return vn_rules.prev_session(today)


def fill_history(data_dir, now: datetime | None = None, *, budget: float = ms.BUDGET_OFF) -> tuple[int, int, str]:
    """Download what the history lacks up to the last posted session, ``budget`` seconds at most:
    (sessions saved, sessions wanted, error or "") - the daily top-up, and a command's first fill."""
    now = now or _now()
    through = through_day(now)
    closes = history(data_dir)
    _, error = closes.update(through, budget=budget, volumes=True)
    have, want = closes.progress(through)
    return have, want, error


def fill_in_background(data_dir, now: datetime | None = None) -> bool:
    """Keep filling the history on a daemon thread until it is complete (one thread per data
    folder at a time); False when one is already at it."""
    key = str(Path(data_dir))
    with _fill_lock:
        if key in _filling:
            return False
        _filling.add(key)

    def work():
        try:
            for _ in range(20):                       # 20 x 150 s: far more than a full history needs
                have, want, error = fill_history(data_dir, now)
                if have >= want or error:
                    break
        except Exception:  # noqa: BLE001 - logged; the next command fills on
            logger.exception("Đột biến khối lượng: tải lịch sử nền lỗi")
        finally:
            with _fill_lock:
                _filling.discard(key)

    threading.Thread(target=work, name="market-history-fill", daemon=True).start()
    return True


def _live_volumes(codes: list[str], board=None) -> dict[str, float]:
    from vnbot import trend_estimate as te

    rows = (board or te.board_prices)(codes)
    return {c: float(b["volume"]) for c, b in rows.items() if b.get("volume")}


def scan(data_dir, now: datetime | None = None, *, threshold: float = THRESHOLD_PCT, budget: float = FILL_BUDGET,
         board=None) -> dict:
    """The screen at ``now``: ``{day, live, at, threshold, rows, candidates}`` - ``rows``
    ``{symbol, volume, avg, ratio (%), value, change (% against the previous close, None in the
    session), floor}``, the highest ratio first; ``live`` when it is the session's volume so far -,
    or ``{error}`` while the history is not there (with ``progress`` (have, want) while it loads).
    ``board``: the price board (tests)."""
    from tradingagents.market import vn_rules

    now = now or _now()
    today, hhmm = now.strftime("%Y-%m-%d"), now.strftime("%H:%M")
    session = vn_rules.is_session_day(today)
    live = session and SESSION_FROM <= hhmm < POSTED_AT
    through = through_day(now)
    closes = history(data_dir)
    _, error = closes.update(through, budget=budget, volumes=True)
    have, want = closes.volume_progress(through)
    if have < want:
        fill_in_background(data_dir, now)
        return {"error": f"đang tải lịch sử khối lượng toàn thị trường ({have}/{want} phiên)"
                + (f"; nguồn báo: {error}" if error else ""), "progress": (have, want)}
    vols = closes.volumes(through, ms.VOLUME_SESSIONS + 1)
    if not vols:
        return {"error": "chưa có khối lượng nào trong lịch sử (nguồn VNDirect chưa trả khối lượng)"}
    day = max(d for v in vols.values() for d in v["days"][-1:])
    pending = day < through and not live          # the session's rows not posted yet
    today_vol: dict[str, float] = {}
    if live:
        today_vol = _live_volumes(sorted(vols), board)
        if not today_vol:
            return {"error": "bảng giá chưa cho khối lượng trong phiên; danh sách của phiên có sau 15:15"}
    rows, candidates = [], 0
    for code, v in vols.items():
        if live:
            hist_v, hist_val = v["volume"][-ms.VOLUME_SESSIONS:], v["value"][-ms.VOLUME_SESSIONS:]
            volume, change = today_vol.get(code), None
        else:
            if not v["days"] or v["days"][-1] != day:
                continue
            hist_v, hist_val = v["volume"][:-1][-ms.VOLUME_SESSIONS:], v["value"][:-1][-ms.VOLUME_SESSIONS:]
            volume, change = v["volume"][-1], None
        if not volume or len(hist_v) < MIN_SESSIONS:
            continue
        avg, avg_value = sum(hist_v) / len(hist_v), sum(hist_val) / len(hist_val)
        if avg <= 0 or avg_value < MIN_AVG_VALUE:
            continue
        candidates += 1
        ratio = volume / avg * 100
        if ratio < threshold:
            continue
        rows.append({"symbol": code, "volume": volume, "avg": avg, "ratio": round(ratio, 1),
                     "value": (v["value"][-1] if not live else None), "change": change, "floor": v.get("floor")})
    if not live:
        _add_changes(closes, rows, day)
    rows.sort(key=lambda r: -r["ratio"])
    return {"day": today if live else day, "live": live, "at": now.strftime("%H:%M"), "threshold": threshold,
            "rows": rows, "candidates": candidates, "pending": pending}


def _add_changes(closes: ms.MarketCloses, rows: list[dict], day: str) -> None:
    """The session's % change of each listed stock, from the saved closes (both adjusted alike)."""
    if not rows:
        return
    series, _, _ = closes.series([r["symbol"] for r in rows], day)
    for r in rows:
        s = series.get(r["symbol"]) or []
        r["change"] = round((s[-1] / s[-2] - 1) * 100, 2) if len(s) >= 2 and s[-2] else None


# ── text ──
def _ddmmyyyy(day: str) -> str:
    return f"{day[8:10]}/{day[5:7]}/{day[:4]}"


def _vol(value: float) -> str:
    from vnbot.quote import _vn

    if value >= 1e6:
        return f"{_vn(value / 1e6, 2)} triệu"
    if value >= 1e3:
        return f"{_vn(value / 1e3, 0)} nghìn"
    return _vn(value)


def _pct(value: float, decimals: int = 0) -> str:
    from vnbot.quote import _vn

    return _vn(value, decimals) + "%"


def text(result: dict) -> str:
    """The pushed/answered list (``scan``'s result)."""
    from vnbot.quote import _vn

    if result.get("error"):
        more = ""
        if result.get("progress"):
            have, want = result["progress"]
            more = f"\nBot đang tải tiếp ({max(want - have, 1)} phiên, khoảng 1-2 giây mỗi phiên): hỏi lại /dotbien sau ít phút."
        return f"⚠️ Đột biến khối lượng: {result['error']}.{more}"
    thr = _pct(result["threshold"])
    head = f"🚀 DANH SÁCH ĐỘT BIẾN KHỐI LƯỢNG ({_ddmmyyyy(result['day'])})"
    if result["live"]:
        head += f" · tới {result['at']}, tạm tính"
    lines = [head, f"(Tiêu chuẩn VolRelative >= {thr} MA20 phiên)", BAR]
    rows = result["rows"]
    if not rows:
        lines.append(f"Không có mã nào đạt {thr} khối lượng trung bình 20 phiên"
                     + (" tới lúc này" if result["live"] else "") + ".")
    else:
        codes = [r["symbol"] for r in rows]
        more = f" … +{len(codes) - LIST_CAP}" if len(codes) > LIST_CAP else ""
        lines.append("🔥 Các mã nổi bật: " + ", ".join(codes[:LIST_CAP]) + more)
    lines.append(f"({len(rows)} mã trong {result['candidates']} mã có thanh khoản TB20 từ {_vn(MIN_AVG_VALUE / 1e9, 0)} tỷ/phiên; "
                 "khối lượng khớp lệnh, không gồm thoả thuận)")
    if result["live"]:
        lines.append("Trong phiên: khối lượng khớp tới lúc này so với trung bình cả phiên của 20 phiên trước; "
                     "danh sách chốt sau 15:15.")
    elif result.get("pending"):
        lines.append("Phiên hôm nay nguồn chưa đăng (thường sau 15:11): đây là phiên gần nhất có số.")
    if rows:
        lines += [BAR, "Chi tiết (KL phiên / TB20 · thay đổi giá):"]
        for r in rows[:DETAIL_ROWS]:
            change = "" if r["change"] is None else f" · {'+' if r['change'] > 0 else ''}{_vn(r['change'], 2)}%"
            lines.append(f"• {r['symbol']} {_pct(r['ratio'])}: {_vol(r['volume'])} / {_vol(r['avg'])}{change}")
        if len(rows) > DETAIL_ROWS:
            lines.append(f"… và {len(rows) - DETAIL_ROWS} mã nữa.")
    lines += ["", "Soi một mã: /soi MÃ · dòng tiền cá mập: /camap MÃ"]
    return "\n".join(lines)


def command(args: str, data_dir, now: datetime | None = None, *, board=None) -> str:
    """/dotbien [NGƯỠNG%] on Telegram and Zoom."""
    words = (args or "").split()
    threshold = THRESHOLD_PCT
    if words:
        raw = words[0].rstrip("%").replace(",", ".")
        try:
            threshold = float(raw)
        except ValueError:
            raise ValueError(f"Ngưỡng không hợp lệ: {words[0]!r}. Dùng: /dotbien hoặc /dotbien 200 (phần trăm của TB20).") from None
        if not MIN_THRESHOLD_PCT <= threshold <= MAX_THRESHOLD_PCT:
            raise ValueError(f"Ngưỡng từ {MIN_THRESHOLD_PCT} đến {MAX_THRESHOLD_PCT} (phần trăm của khối lượng trung bình 20 phiên).")
    return text(scan(data_dir, now, threshold=threshold, board=board))


def topup_due(data_dir, now: datetime | None = None, stamp: Path | None = None) -> str | None:
    """The day of the daily top-up when it is due (from ``TOPUP_AT`` on a session day, once)."""
    from tradingagents.market import vn_rules

    now = now or _now()
    today = now.strftime("%Y-%m-%d")
    if not vn_rules.is_session_day(today) or now.strftime("%H:%M") < TOPUP_AT:
        return None
    stamp = stamp or Path(data_dir) / "last_market_history.txt"
    try:
        if stamp.read_text(encoding="utf-8").strip() == today:
            return None
    except OSError:
        pass
    return today
