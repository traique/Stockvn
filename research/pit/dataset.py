"""Dữ liệu đã tải (``fetch.py``) → ``simulator.Dataset`` cho ``factor.run`` / sổ Động lượng, và ``Universe``.

Nến khớp lệnh: giá VCI đã điều chỉnh (mở/cao/thấp/đóng) của các phiên có giao dịch (khối lượng khớp > 0); phiên
tạm ngừng không có nến – lệnh chờ phiên sau, như ledger của bot. ``prev_close`` / ``prev_date`` là phiên có nến
trước đó (ledger nhận phiên trắng bên và đổi gốc giá). Sàn của nến: HOSE nếu mã có trong danh sách HOSE của
phiên đó, không thì theo biên độ trần (``universe.band_exchange``) – quyết định bước giá khi trượt giá.
"""

from __future__ import annotations

import math
from pathlib import Path

from tradingagents.trading.ledger import Bar
from tradingagents.trading.simulator import Dataset

from research.pit import fetch
from research.pit.universe import Series, Universe, build_series


def load(base: Path | None = None, *, codes: list[str] | None = None) -> tuple[Dataset, dict[str, Series], dict]:
    """(Dataset, {mã: Series}, membership) từ ``research/out`` (hoặc ``base``)."""
    base = Path(base or fetch.OUT)
    vni = fetch.read_vnindex(base)
    sessions = [d for d, _ in vni]
    membership = fetch.read_membership(base)
    members_by_day = {d: set(c) for d, c in membership.items()}
    if codes is None:
        codes = sorted({c for v in membership.values() for c in v})
    data = Dataset(dates=list(sessions), benchmark={d: c for d, c in vni})
    series: dict[str, Series] = {}
    for code in codes:
        rows = fetch.read_prices(base, code)
        if not rows:
            data.skipped[code] = "VCI không có dữ liệu"
            continue
        s = build_series(code, rows, sessions)
        series[code] = s
        data.bars[code] = bars_of(s, sessions, members_by_day)
    data.symbols = sorted(series)
    return data, series, membership


def bars_of(s: Series, sessions: list[str], members_by_day: dict[str, set]) -> dict[str, Bar]:
    """Nến khớp lệnh của một mã (chỉ phiên có giao dịch, giá đã điều chỉnh)."""
    from research.pit.universe import _finite

    out: dict[str, Bar] = {}
    prev_close, prev_date = None, None
    for i, day in enumerate(sessions):
        if not s.bar[i]:
            continue
        o, h, l_, c = (float(x) for x in (s.open_[i], s.high[i], s.low[i], s.close[i]))
        if not all(_finite(x) and x > 0 for x in (o, h, l_)):
            o = h = l_ = c                    # VCI thiếu mở/cao/thấp đã điều chỉnh: phiên một giá (hiếm)
        ex = "HOSE" if s.code in members_by_day.get(day, ()) else s.band_ex[i]
        out[day] = Bar(day, o, h, l_, c, prev_close, exchange=ex,
                       volume=float(s.volume[i]) if math.isfinite(s.volume[i]) else None, prev_date=prev_date)
        prev_close, prev_date = c, day
    return out


def universes(sessions: list[str], series: dict[str, Series], membership: dict) -> tuple[Universe, Universe]:
    """(rổ point-in-time, rổ "danh sách HOSE hôm nay" – thiên lệch sống sót)."""
    return Universe(sessions, series, membership), Universe(sessions, series, membership, today=True)
