"""A stock's KPIs, and a bank's against every listed bank's, point-in-time (informational only).

VCI's TTM ratio rows of the newest quarter published by the run date (``publicDate`` of its
income statement, ``vn_fundamentals.dated_ratio_rows``), with VCI's stray minus signs dropped
(``vn_fundamentals.ratio_value``), and the same quarter a year before. A bank's are set against
the median of every listed bank (ICB 8300 in VCI's listing) that had published the SAME quarter by
the run date - on 30/10/2026 some banks have Q3/2026 out and the others only Q2, and a median of
mixed quarters would compare a quarter with another - with its rank among them, and its 5-year
median ROE (the card's sustainable ROE) ranked among every bank's.

The banks' figures are read once a day by the evening job (``bank_peers(day, compute=True)``, two
VCI calls a bank) and kept on disk with each quarter's and year's publication day, so any run date
up to that read is answered point-in-time from the file; nothing else reads VCI for them (a page,
/dinhgia, the fundamentals tool). VCI's 0 is its "no figure" for a bank KPI (CAR in most quarters,
checked live 29/10/2026), left out like a missing one; CAR itself is not shown for that reason.

Nothing in the Quant Engine reads this module: it only writes text, for /dinhgia and the analysts.
"""

from __future__ import annotations

import json
import logging
import os
import statistics
import threading
import time
from dataclasses import dataclass, replace
from datetime import date

from tradingagents.dataflows import money_flows, vn_valuation
from tradingagents.dataflows import vn_fundamentals as vf
from tradingagents.dataflows.errors import NoMarketDataError
from tradingagents.dataflows.files import replace_file
from tradingagents.dataflows.symbols import normalize_symbol

logger = logging.getLogger(__name__)

BANK_ICB = "8300"
FORMAT = 2                   # the file's layout: quarters and years with their publication day
PEER_QUARTERS = 12           # each bank's newest quarters kept: three years of run dates answered
ROE_YEARS, MIN_ROE_YEARS = 5, 3   # the 5-year median ROE, as the card's (vn_valuation.ROE_YEARS)
MIN_PEERS = 5                # fewer banks (the stock included) with its quarter out by the run date: no median
# (VCI ratio field, label, "pct" or "x", which way is better: "high", "low" or None - no rank)
BANK_KPIS = (
    ("roe", "ROE (4 quý)", "pct", "high"),
    ("netInterestMargin", "NIM (4 quý, theo VCI)", "pct", "high"),
    ("cir", "Chi phí/thu nhập (CIR)", "pct", "low"),
    ("npl", "Nợ xấu (NPL)", "pct", "low"),
    ("loansLossReservesToNPLs", "Bao phủ nợ xấu", "pct", "high"),
    ("provisionToOutstandingLoans", "Chi phí tín dụng (dự phòng/dư nợ, 4 quý)", "pct", "low"),
    ("casaRatio", "CASA", "pct", "high"),
    ("ldrLoanDepositRatio", "Cho vay/huy động (LDR)", "pct", None),
)
# VCI's debtToEquity is total liabilities (bsa54) / equity (bsa78), not borrowings: HPG Q2/2026 0,971 =
# 137.414 / 141.516 tỷ, borrowings 0,70 (checked live 30/10/2026); an insurer's is mostly reserves (BVH 10,8).
OTHER_KPIS = (
    ("roe", "ROE (4 quý)", "pct", None), ("roa", "ROA (4 quý)", "pct", None),
    ("grossMargin", "Biên lãi gộp", "pct", None), ("afterTaxProfitMargin", "Biên lãi ròng", "pct", None),
    ("debtToEquity", "Nợ phải trả/VCSH", "x", None), ("currentRatio", "Thanh toán hiện hành", "x", None),
)
NIM_NOTE = "NIM của VCI là 4 quý gần nhất; công ty và nhiều báo cáo dùng quý quy năm, nên số có thể lệch"
_parsed: dict[str, tuple[float, dict]] = {}       # path -> (mtime, the file's data)
_parsed_lock = threading.Lock()


def fields(profile: str) -> tuple:
    return BANK_KPIS if profile == "banking" else OTHER_KPIS


def _informational(call):
    """``call`` behind the breaker of the informational VCI calls (``money_flows.VCI_INFO``); a stock
    without the data (``NoMarketDataError``) is None, not an outage that trips the breaker."""
    def guarded():
        try:
            return call()
        except NoMarketDataError:
            return None

    return money_flows._guarded(money_flows.VCI_INFO, guarded)


def value(row: dict, field: str, bank: bool = True) -> float | None:
    """A KPI of a ratio row, VCI's minus sign dropped; a bank's 0 (VCI's "no figure") is None."""
    v = vf.ratio_value(row, field)
    return None if v is None or (bank and v == 0) else v


def _key(row: dict) -> str:
    return f"{int(row.get('yearReport') or 0)}-{int(row.get('quarter') or 0)}"


def period(key: str) -> str:
    year, quarter = key.split("-")
    return f"Q{quarter}/{year}"


# ─── every listed bank, read once a day ──────────────────────────────────────

def _path(day: str) -> str:
    return os.path.join(vn_valuation._dir(), f"peers-{BANK_ICB}-{day}.json")


def _bank(code: str) -> dict | None:
    """One bank's entry: {"q": {"2026-2": {"pub": day, field: value}}, "y": {"2025": {"pub": day,
    "roe": x}}}; None without ratio rows (PCB, checked live 29/10/2026)."""
    quarters = _informational(lambda: vf.dated_ratio_rows(code))
    if not quarters:
        return None
    annual = _informational(lambda: vf.dated_annual_ratio_rows(code)) or []
    return {"q": {_key(row): {"pub": public.isoformat(), **{f: value(row, f) for f, *_ in BANK_KPIS}}
                  for row, public in quarters[:PEER_QUARTERS]},
            "y": {str(int(row["yearReport"])): {"pub": public.isoformat(), "roe": roe}
                  for row, public in annual if (roe := vf._num(row.get("roe"))) is not None}}


def _read(path: str) -> dict | None:
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    with _parsed_lock:
        hit = _parsed.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if not (isinstance(data, dict) and data.get("format") == FORMAT and isinstance(data.get("banks"), dict)):
        return None                   # v1.17.0 phase A's layout (no publication days): read again
    with _parsed_lock:
        _parsed[path] = (mtime, data)
    return data


def bank_peers(day: str, *, compute: bool = False) -> dict | None:
    """Every listed bank's (ICB 8300 in VCI's listing) KPIs of its ``PEER_QUARTERS`` newest quarters and
    its annual ROEs, each with its publication day, as VCI has them on ``day``: {"day", "format", "banks":
    {code: ...}} (``_bank``), kept on disk for the day. None when not read yet; ``compute`` reads it now
    (the evening job: two VCI calls a bank, ~27 banks). VCI down, or no bank read (a listing without
    banks, no bank's ratios), raises and nothing is kept: the job tries again, readers use an older day."""
    from tradingagents.dataflows.vendors import vci

    path = _path(day)
    data = _read(path)
    if data is not None or not compute:
        return data
    started = time.monotonic()
    listing = _informational(vci.fetch_listing) or []
    codes = sorted({str(r.get("symbol") or "").upper() for r in listing
                    if str(r.get("icbCode2")) == BANK_ICB and r.get("type") == "STOCK"} - {""})
    banks = {code: entry for code in codes if (entry := _bank(code))}
    if not banks:
        raise NoMarketDataError(f"ICB {BANK_ICB}", None, f"no bank KPIs read ({len(codes)} banks in VCI's listing)")
    data = {"day": day, "format": FORMAT, "banks": banks}

    def write(temp: str) -> None:
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(data, handle)

    replace_file(path, write)
    logger.info("Bank KPIs of %d banks read in %.0f s", len(banks), time.monotonic() - started)
    return data


def latest_bank_peers(day: str | None = None) -> dict | None:
    """The newest banks' read kept, of ``day`` or before (any day when None). A read of a later day than
    a run date still answers it point-in-time: every figure carries its publication day."""
    folder = vn_valuation._dir()
    prefix = f"peers-{BANK_ICB}-"
    days = sorted((n[len(prefix):-5] for n in os.listdir(folder) if n.startswith(prefix) and n.endswith(".json")),
                  reverse=True)
    for found in days:
        if (day is None or found <= day) and (data := _read(_path(found))) and data["banks"]:
            return data
    return None


# ─── the comparison ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Kpi:
    field: str
    label: str
    kind: str                       # "pct" or "x"
    better: str | None              # "high", "low" or None (not ranked)
    value: float | None
    prior: float | None             # the same quarter a year before
    median: float | None = None     # of the banks with the same quarter published by the run date
    n: int = 0                      # how many, the stock included
    rank: int | None = None         # 1 = the best of those n


@dataclass(frozen=True)
class KpiTable:
    symbol: str
    profile: str
    as_of: date
    key: str                        # "2026-2"
    period: str                     # "Q2/2026"
    published: date                 # when that quarter was published
    rows: tuple[Kpi, ...]
    peers_read: date | None = None  # the day the banks' figures were read (None: not a bank, or none read)
    banks: int = 0                  # banks of that read with a quarter published by the run date
    with_period: int = 0            # of them, those with the stock's quarter
    newer: int = 0                  # of them, those with a later quarter out already
    roe5: float | None = None       # the stock's median of its newest annual ROEs (up to 5) published
    roe5_years: tuple[int, ...] = ()
    roe5_published: date | None = None
    roe5_median: float | None = None
    roe5_rank: int | None = None
    roe5_of: int = 0


def _rank(own: float, values: list[float], better: str | None) -> int | None:
    if better is None:
        return None
    return 1 + sum(1 for v in values if (v > own if better == "high" else v < own))


def _roe5(years: dict, cutoff: date) -> float | None:
    """The median of a bank's newest ``ROE_YEARS`` annual ROEs published by ``cutoff`` (at least
    ``MIN_ROE_YEARS``), from its entry's "y"."""
    seen = sorted(((int(y), e["roe"]) for y, e in years.items()
                   if e.get("roe") is not None and date.fromisoformat(e["pub"]) <= cutoff), reverse=True)[:ROE_YEARS]
    return statistics.median(v for _, v in seen) if len(seen) >= MIN_ROE_YEARS else None


def compare(table: KpiTable, peers: dict | None) -> KpiTable:
    """``table`` (a bank's own KPIs) with the banks' medians and ranks of ``peers`` (a ``bank_peers``
    read): each KPI among the banks whose ``table.key`` quarter was published by the run date, the
    stock itself included with its own figures (no median under ``MIN_PEERS`` banks: early in a reporting
    season, or a run date before the read's quarters); the 5-year median ROE among every bank with three
    annual ROEs or more published by then."""
    if not peers or not peers.get("banks"):
        return table
    cutoff = table.as_of
    others = {c: b for c, b in peers["banks"].items() if c != table.symbol}
    visible = {c: b for c, b in others.items()
               if any(date.fromisoformat(q["pub"]) <= cutoff for q in b.get("q", {}).values())}
    same = {c: q for c, b in visible.items()
            if (q := b.get("q", {}).get(table.key)) and date.fromisoformat(q["pub"]) <= cutoff}
    rows = list(table.rows)
    if len(same) + 1 >= MIN_PEERS:
        rows = []
        for k in table.rows:
            values = [q[k.field] for q in same.values() if q.get(k.field) is not None]
            if k.value is None:
                rows.append(replace(k, median=statistics.median(values) if values else None, n=len(values)))
                continue
            rows.append(replace(k, median=statistics.median([*values, k.value]), n=len(values) + 1,
                                rank=_rank(k.value, values, k.better)))
    roe5s = [v for b in others.values() if (v := _roe5(b.get("y", {}), cutoff)) is not None]
    order = tuple(int(x) for x in table.key.split("-"))
    newer = sum(1 for b in visible.values() if any(tuple(int(x) for x in k.split("-")) > order
                                                   and date.fromisoformat(q["pub"]) <= cutoff for k, q in b["q"].items()))
    out = replace(table, rows=tuple(rows), peers_read=date.fromisoformat(peers["day"]), banks=len(visible) + 1,
                  with_period=len(same) + 1, newer=newer)
    if table.roe5 is None:
        return replace(out, roe5_median=statistics.median(roe5s) if roe5s else None, roe5_of=len(roe5s))
    return replace(out, roe5_median=statistics.median([*roe5s, table.roe5]), roe5_rank=_rank(table.roe5, roe5s, "high"),
                   roe5_of=len(roe5s) + 1)


def own(symbol: str, as_of_date: str | None = None, profile: str | None = None) -> KpiTable | None:
    """The stock's KPIs of its newest quarter published by the run date and of the same quarter a year
    before, a bank's 5-year median ROE; None without a quarter. VCI's ratio rows (kept half an hour:
    the card read them)."""
    code = normalize_symbol(symbol)
    profile = profile or vn_valuation.card_profile(code)
    cutoff = vf._as_of_day(as_of_date)
    rows = [(r, p) for r, p in _informational(lambda: vf.dated_ratio_rows(code)) or [] if p <= cutoff]
    if not rows:
        return None
    bank = profile == "banking"
    latest, published = rows[0]
    year, quarter = int(latest["yearReport"]), int(latest["quarter"])
    prior = next((r for r, _ in rows if (int(r["yearReport"]), int(r["quarter"])) == (year - 1, quarter)), None)
    kpis = tuple(Kpi(f, label, kind, better, value(latest, f, bank), value(prior, f, bank) if prior else None)
                 for f, label, kind, better in fields(profile))
    table = KpiTable(symbol=code, profile=profile, as_of=cutoff, key=_key(latest), period=period(_key(latest)),
                     published=published, rows=kpis)
    if not bank:
        return table
    annual = [(int(r["yearReport"]), v, p) for r, p in _informational(lambda: vf.dated_annual_ratio_rows(code)) or []
              if p <= cutoff and (v := vf._num(r.get("roe"))) is not None][:ROE_YEARS]
    if len(annual) < MIN_ROE_YEARS:
        return table
    return replace(table, roe5=statistics.median(v for _, v, _ in annual),
                   roe5_years=tuple(sorted(y for y, _, _ in annual)), roe5_published=max(p for _, _, p in annual))


def kpi_table(symbol: str, as_of_date: str | None = None, profile: str | None = None) -> KpiTable | None:
    """``own``, and for a bank ``compare`` with the newest banks' read kept (no VCI call for them)."""
    table = own(symbol, as_of_date, profile)
    if table is None or table.profile != "banking":
        return table
    return compare(table, latest_bank_peers())


# ─── text ─────────────────────────────────────────────────────────────────────

def fmt(v: float | None, kind: str = "pct") -> str:
    """24,1% / 1,20% (two decimals under 10%) / 134,0%; a multiple with two decimals; "—" for none."""
    from tradingagents.quant import report_format as rfmt

    if v is None:
        return "—"
    if kind == "x":
        return rfmt.fmt_number(v, 2)
    return rfmt.fmt_pct(v * 100, 2 if abs(v) < 0.1 else 1)


def rank_text(k: Kpi) -> str:
    """"hạng 3/27 (thấp là tốt)"; "" for a KPI not ranked."""
    if k.rank is None:
        return ""
    return f"hạng {k.rank}/{k.n} ({'cao' if k.better == 'high' else 'thấp'} là tốt)"


LOWER_IS_BETTER = ", ".join(label.split(" (")[0] for _, label, _, better in BANK_KPIS if better == "low")


def fundamentals_lines(t: KpiTable | None) -> list[str]:
    """A bank's KPIs against the banks' medians for the fundamentals report (two lines)."""
    if t is None or t.profile != "banking":
        return []
    if t.peers_read is None:
        return [("- So với các ngân hàng: chưa có số đọc sẵn (bot đọc chỉ số mọi ngân hàng niêm yết mỗi tối khi "
                 "watchlist, Tài sản hoặc thẻ anh nhập có một ngân hàng)")]
    if t.with_period < MIN_PEERS:
        lines = [f"- So với các ngân hàng: mới {t.with_period}/{t.banks} ngân hàng đã công bố {t.period} tới ngày này (số "
                 f"VCI đọc {t.peers_read:%d/%m/%Y}), chưa đủ để lấy trung vị cùng kỳ"]
    else:
        parts = [f"{k.label} {fmt(k.value, k.kind)} (cùng kỳ {fmt(k.prior, k.kind)}; trung vị {fmt(k.median, k.kind)}"
                 + (f", hạng {k.rank}/{k.n}" if k.rank else "") + ")" for k in t.rows if k.value is not None]
        lines = [f"- So với trung vị các ngân hàng cùng kỳ {t.period} ({t.with_period}/{t.banks} ngân hàng đã công bố kỳ "
                 f"này; số VCI đọc {t.peers_read:%d/%m/%Y}) {vn_valuation._label('THỐNG KÊ', t.peers_read, t.as_of)}: "
                 + " | ".join(parts) + f". Hạng 1 là tốt nhất ({LOWER_IS_BETTER}: thấp là tốt). {NIM_NOTE}"]
    if t.roe5 is not None:
        lines.append(f"- ROE trung vị {len(t.roe5_years)} năm {t.roe5_years[0]}-{t.roe5_years[-1]}: {fmt(t.roe5)}, "
                     f"hạng {t.roe5_rank}/{t.roe5_of} ngân hàng (trung vị nhóm {fmt(t.roe5_median)}) "
                     f"{vn_valuation._label('THỐNG KÊ', t.roe5_published, t.as_of)}")
    return lines
