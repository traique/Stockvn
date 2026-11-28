"""Rổ point-in-time của Phòng thủ (và rổ VN30 giả lập cho Động lượng) trên dữ liệu đã tải (``fetch.py``).

Luật chọn rổ là luật của bot (``tradingagents.trading.factor``): ``liquid_pool`` (100 mã HOSE thanh khoản nhất
theo trung vị giá trị khớp 60 phiên, ≥ 250 phiên, giá chưa điều chỉnh ≥ 5.000đ, có phiên trong 5 phiên gần
nhất) rồi ``cap_universe`` (``fb_universe`` mã vốn hoá lớn nhất = số cổ phiếu × giá chưa điều chỉnh). Chỉ phần
"ảnh chụp" từng mã trên phiên D (``factor.snapshot``) được tính lại ở đây dạng mảng theo lịch phiên cho nhanh
(gọi ``factor.snapshot`` cho ~400 mã × ~170 lần dựng rổ đọc lại cả lịch sử mỗi lần); ``tests/suites/
suite_research_pit.py`` kiểm hai cách cho cùng kết quả trên dữ liệu giả có khoảng trống, giá trống, tạm ngừng.

Point-in-time:
* Mã HOSE của phiên D: danh sách VNDirect của chính phiên D (``membership``), không phải danh sách hôm nay –
  mã đã huỷ niêm yết hay bị chuyển sàn vẫn có mặt trong những phiên chúng còn ở HOSE; mã chuyển lên HOSE
  chỉ có mặt từ phiên lên sàn. Phiên VNDirect trống (hiếm) dùng danh sách phiên gần nhất trước đó (≤ 5 phiên).
  Trước 02/01/2013 (VNDirect chưa có) dùng biên độ giá trần của VCI (``band_exchange``).
* Mọi số liệu của phiên D chỉ đọc dữ liệu tới D (không nhìn trước). Giá đã điều chỉnh tới ngày tải (như mọi
  backtest trên nguồn công khai) – không đổi thứ hạng biến động / gần đỉnh trong cùng một cửa sổ.

``today_universe``: cùng luật nhưng danh sách mã HOSE là danh sách của phiên cuối, dùng cho mọi phiên – chính
là thiên lệch sống sót của các backtest cũ; chạy song song để đo độ lệch.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

import numpy as np

from tradingagents.trading import factor

BAND_CUTOVER = "2013-01-15"       # Thông tư 74/2012: HOSE 5% → 7%, HNX 7% → 10% (kiểm trên VCI: HVG, ITA, OGC, FLC)
CARRY_SESSIONS = 5                # phiên VNDirect trống: dùng danh sách phiên gần nhất trước đó, tối đa chừng này phiên


def band_exchange(day: str, ref: float, ceiling: float) -> str | None:
    """Sàn suy ra từ biên độ trần của phiên (None khi không rõ: ngày đầu niêm yết, GDKHQ, dữ liệu thiếu)."""
    try:
        pct = (float(ceiling) - float(ref)) / float(ref) * 100
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if not math.isfinite(pct) or pct <= 0:
        return None
    if day < BAND_CUTOVER:
        table = ((4.0, 5.6, "HOSE"), (6.0, 7.6, "HNX"), (9.0, 10.6, "UPCOM"))
    else:
        table = ((6.0, 7.6, "HOSE"), (9.0, 10.6, "HNX"), (13.5, 15.6, "UPCOM"))
    for lo, hi, ex in table:
        if lo <= pct < hi:
            return ex
    return None


@dataclass
class Series:
    """Một mã trên lịch phiên (mảng dài bằng số phiên; NaN / False ở phiên không có dữ liệu)."""

    code: str
    bar: np.ndarray            # có giao dịch (giá đóng > 0 và khối lượng khớp > 0)
    close: np.ndarray          # giá đóng đã điều chỉnh
    volume: np.ndarray         # khối lượng khớp
    hist: np.ndarray           # có dòng price-history của VCI
    matched: np.ndarray        # giá trị khớp (VND)
    raw: np.ndarray            # giá đóng chưa điều chỉnh
    shares: np.ndarray         # số cổ phiếu lưu hành
    band_ex: list              # sàn theo biên độ từng phiên (None khi không rõ)
    open_: np.ndarray | None = None    # mở / cao / thấp đã điều chỉnh (nến khớp lệnh – dataset.bars_of)
    high: np.ndarray | None = None
    low: np.ndarray | None = None

    def __post_init__(self):
        idx = np.where(self.bar, np.arange(len(self.bar)), -1)
        self.last_bar = np.maximum.accumulate(idx) if len(idx) else idx
        self.cum_bars = np.cumsum(self.bar)
        hits = np.flatnonzero(self.bar)
        self.first_bar = int(hits[0]) if len(hits) else None


def build_series(code: str, rows: Iterable[Mapping], sessions: list[str]) -> Series:
    """``rows``: các phiên của ``fetch.read_prices`` (cũ → mới). Phiên ngoài lịch VNINDEX bị bỏ."""
    pos = {d: i for i, d in enumerate(sessions)}
    n = len(sessions)
    nan = np.full(n, np.nan)
    close, volume, matched, raw, shares = nan.copy(), nan.copy(), nan.copy(), nan.copy(), nan.copy()
    open_, high, low = nan.copy(), nan.copy(), nan.copy()
    bar, hist = np.zeros(n, bool), np.zeros(n, bool)
    band: list = [None] * n
    for r in rows:
        i = pos.get(r["date"])
        if i is None:
            continue
        hist[i] = True
        matched[i], raw[i], shares[i] = r.get("value", np.nan), r.get("close_raw", np.nan), r.get("shares", np.nan)
        c, v = r.get("close", np.nan), r.get("volume", np.nan)
        close[i], volume[i] = c, v
        open_[i], high[i], low[i] = r.get("open", np.nan), r.get("high", np.nan), r.get("low", np.nan)
        bar[i] = bool(np.isfinite(c) and c > 0 and np.isfinite(v) and v > 0)
        band[i] = band_exchange(r["date"], r.get("ref"), r.get("ceiling"))
    return Series(code, bar, close, volume, hist, matched, raw, shares, band, open_, high, low)


def _finite(x) -> bool:
    return x is not None and math.isfinite(float(x))


def snapshot(s: Series, t: int, *, hose: bool = True) -> factor.Snapshot:
    """``factor.snapshot`` của mã ``s`` trên phiên thứ ``t`` – cùng định nghĩa, tính trên mảng."""
    has_bar = bool(s.bar[t])
    bars = int(s.cum_bars[t])
    last = int(s.last_bar[t])
    raw_close = None
    if last >= 0:
        r = s.raw[last] if s.hist[last] else math.nan
        raw_close = float(r) if _finite(r) and r > 0 else float(s.close[last])
    recent = bool(s.bar[max(0, t - factor.RECENT_SESSIONS + 1):t + 1].any())
    values = []
    first = s.first_bar if s.first_bar is not None and s.first_bar <= t else None
    for d in range(max(0, t - factor.LIQ_WINDOW + 1), t + 1):
        if first is None or d < first:
            continue
        if s.bar[d]:
            m = s.matched[d] if s.hist[d] else math.nan
            if _finite(m) and m >= 0:
                value = float(m)
            else:
                c, v = s.close[d], s.volume[d]
                value = float(c) * float(v) if _finite(c) and _finite(v) else math.nan
            if math.isnan(value):
                continue
            values.append(value)
        else:
            values.append(0.0)
    median = float(np.median(values)) if len(values) >= factor.LIQ_MIN_PERIODS else None
    cap = None
    for d in range(t, max(-1, t - factor.CAP_FFILL - 1), -1):
        if s.hist[d] and _finite(s.raw[d]) and _finite(s.shares[d]) and s.raw[d] > 0 and s.shares[d] > 0:
            cap = float(s.shares[d]) * float(s.raw[d])
            break
    return factor.Snapshot(s.code, hose, bars, raw_close, recent, median, cap, has_bar)


class Universe:
    """Rổ theo phiên: ``hose(day)`` (mã HOSE point-in-time) → ``cap(day, size)`` (luật của bot)."""

    def __init__(self, sessions: list[str], series: Mapping[str, Series], membership: Mapping[str, list[str]],
                 *, today: bool = False):
        self.sessions, self.series = list(sessions), dict(series)
        self.pos = {d: i for i, d in enumerate(self.sessions)}
        self.members = {d: list(c) for d, c in membership.items() if c}
        self.member_days = sorted(self.members)
        self.today = today
        self.last_members = self.members[self.member_days[-1]] if self.member_days else []
        self._cache: dict[tuple, list[str]] = {}

    def hose(self, day: str) -> list[str]:
        if self.today:
            return list(self.last_members)
        if day in self.members:
            return list(self.members[day])
        i = bisect.bisect_right(self.member_days, day)
        if i and self.member_days and day >= self.member_days[0]:
            prev = self.member_days[i - 1]
            if self.pos.get(day, 0) - self.pos.get(prev, 0) <= CARRY_SESSIONS:
                return list(self.members[prev])
            return []
        t = self.pos.get(day)                                   # trước VNDirect: theo biên độ của phiên
        if t is None:
            return []
        return sorted(c for c, s in self.series.items() if s.band_ex[t] == "HOSE")

    def snapshots(self, day: str) -> list[factor.Snapshot]:
        t = self.pos[day]
        return [snapshot(self.series[c], t, hose=True) for c in self.hose(day) if c in self.series]

    def cap(self, day: str, size: int, pool: int = factor.LIQUID_POOL) -> list[str]:
        key = (day, int(size), int(pool))
        if key not in self._cache:
            self._cache[key] = factor.cap_universe(self.snapshots(day), size, pool)
        return list(self._cache[key])
