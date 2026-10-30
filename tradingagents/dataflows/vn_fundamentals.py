"""Company fundamentals from VCI, served point-in-time.

Every statement row from VCI carries the date it was published
(``publicDate``). A run dated D therefore reads only the periods published on
or before D. A row with no publication date is treated as published at the
regulatory deadline instead: 45 days after quarter end for quarterly reports
(the longest deadline, for parent companies), 90 days after year end for the
audited annual report. TTM ratios follow the quarter they belong to.

Live-only data (company profile, foreign ownership, today's price and the
analyst target) is withheld from a past run, and so are VCI's own multiples
and market value of a period (P/E, P/B, ...): VCI prices them on the day it
last updated them, weeks after the publication (checked live 29/10/2026: CTG
Q2/2026, published 19/08, at the close of 14/10).
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

from tradingagents.dataflows.date_window import is_historical
from tradingagents.dataflows.errors import NoMarketDataError
from tradingagents.dataflows.symbols import normalize_symbol
from tradingagents.dataflows.vendors import vci
from tradingagents.market import vn_rules
from tradingagents.quant import fundamental_profiles, report_format as rfmt, sector

logger = logging.getLogger(__name__)

QUARTER_REPORT_LAG_DAYS = 45
ANNUAL_REPORT_LAG_DAYS = 90
_SECTION_TITLES = {
    "INCOME_STATEMENT": "Kết quả kinh doanh",
    "BALANCE_SHEET": "Bảng cân đối kế toán",
    "CASH_FLOW": "Lưu chuyển tiền tệ",
}


def _parse_day(raw) -> date | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw)[:19]).date()
    except ValueError:
        return None


def _period_end(year: int, quarter: int | None) -> date:
    if not quarter or quarter >= 5:
        return date(year, 12, 31)
    month = quarter * 3
    next_month = date(year + (month == 12), month % 12 + 1, 1)
    return next_month - timedelta(days=1)


def published_on(row: dict, *, annual: bool = False) -> date:
    """When a statement row became public: its ``publicDate``, else the filing deadline."""
    published = _parse_day(row.get("publicDate"))
    if published is not None:
        return published
    year = int(row.get("yearReport") or 0)
    quarter = None if annual else int(row.get("lengthReport") or row.get("quarter") or 0)
    lag = ANNUAL_REPORT_LAG_DAYS if annual or not quarter or quarter >= 5 else QUARTER_REPORT_LAG_DAYS
    return _period_end(year, quarter) + timedelta(days=lag)


def _as_of_day(as_of_date: str | None) -> date:
    return datetime.strptime(as_of_date, "%Y-%m-%d").date() if as_of_date else vn_rules.now_vn().date()


def _period_label(row: dict, annual: bool) -> str:
    year = row.get("yearReport")
    if annual:
        return f"Năm {year}"
    return f"Q{row.get('lengthReport') or row.get('quarter')}/{year}"


def published_statement_rows(symbol: str, section: str, as_of_date: str | None, annual: bool = False) -> list[dict]:
    """Statement rows published on or before the run date, newest first."""
    data = vci.fetch_statement(symbol, section)
    rows = data["years"] if annual else data["quarters"]
    if not annual:
        rows = [r for r in rows if 1 <= int(r.get("lengthReport") or 0) <= 4]
    cutoff = _as_of_day(as_of_date)
    visible = [r for r in rows if published_on(r, annual=annual) <= cutoff]
    return sorted(
        visible,
        key=lambda r: (int(r.get("yearReport") or 0), int(r.get("lengthReport") or 0)),
        reverse=True,
    )


def _statement_quarters(symbol: str) -> dict[tuple[int, int], date]:
    """Publication date of each quarter's income statement, by (year, quarter)."""
    try:
        rows = vci.fetch_statement(symbol, "INCOME_STATEMENT")["quarters"]
    except NoMarketDataError:
        return {}
    return {
        (int(r.get("yearReport") or 0), int(r.get("lengthReport") or 0)): published_on(r)
        for r in rows
        if 1 <= int(r.get("lengthReport") or 0) <= 4
    }


def dated_ratio_rows(symbol: str) -> list[tuple[dict, date]]:
    """Every TTM ratio row of a quarter with the day it became public, newest quarter first.

    A quarter is public when its income statement was; when VCI has no
    statement for it, the quarterly filing deadline decides.
    """
    rows = [r for r in vci.fetch_ratios(symbol) if 1 <= int(r.get("quarter") or 0) <= 4]
    statements = _statement_quarters(symbol)
    dated = []
    for row in rows:
        key = (int(row.get("yearReport") or 0), int(row.get("quarter") or 0))
        dated.append((row, statements.get(key) or published_on({**row, "lengthReport": key[1]})))
    return sorted(dated, key=lambda rp: (rp[0].get("yearReport") or 0, rp[0].get("quarter") or 0), reverse=True)


def published_ratio_rows(symbol: str, as_of_date: str | None) -> list[dict]:
    """TTM ratio rows of quarters public by the run date, newest first (``dated_ratio_rows``)."""
    cutoff = _as_of_day(as_of_date)
    return [row for row, public in dated_ratio_rows(symbol) if public <= cutoff]


def dated_annual_ratio_rows(symbol: str) -> list[tuple[dict, date]]:
    """Every annual ratio row (``quarter`` 5, which ``published_ratio_rows`` leaves out) with the day
    its annual report became public, newest year first.

    The annual income statement's ``publicDate`` decides (CTG 2025: 31/03/2026); when VCI has no
    statement for the year, the annual filing deadline.
    """
    try:
        years = {int(r.get("yearReport") or 0): published_on(r, annual=True)
                 for r in vci.fetch_statement(symbol, "INCOME_STATEMENT")["years"]}
    except NoMarketDataError:
        years = {}
    dated = [(row, years.get(int(row.get("yearReport") or 0)) or published_on(row, annual=True))
             for row in vci.fetch_ratios(symbol) if int(row.get("quarter") or 0) == 5]
    return sorted(dated, key=lambda rp: int(rp[0].get("yearReport") or 0), reverse=True)


def published_annual_ratio_rows(symbol: str, as_of_date: str | None) -> list[tuple[dict, date]]:
    """``dated_annual_ratio_rows`` of the years whose annual report was public by the run date."""
    cutoff = _as_of_day(as_of_date)
    return [(row, public) for row, public in dated_annual_ratio_rows(symbol) if public <= cutoff]


def _num(value) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v == v else None  # drop NaN


# Bank ratios VCI serves with a minus sign though they are costs or cover (checked live
# 29/10/2026: negative in every quarter of CTG, VCB, ACB, MBB); printed as positive.
NEGATIVE_SIGN_RATIOS = frozenset({
    "cir", "costToIncome", "loansLossReservesToNPLs", "provisionToOutstandingLoans", "averageCostOfFinancing",
})


def ratio_value(row: dict, field: str) -> float | None:
    """A ratio-row field as a number, with VCI's stray minus sign dropped (``NEGATIVE_SIGN_RATIOS``)."""
    v = _num(row.get(field))
    return abs(v) if v is not None and field in NEGATIVE_SIGN_RATIOS else v


def _pct(value) -> str:
    v = _num(value)
    return "N/A" if v is None else rfmt.fmt_pct(v * 100)


def _ratio(value, decimals: int = 2) -> str:
    v = _num(value)
    return "N/A" if v is None else rfmt.fmt_number(v, decimals)


def _billion(value) -> str:
    v = _num(value)
    return "N/A" if v is None else f"{rfmt.fmt_number(v / 1e9, 1)} tỷ"


def _growth_lines(symbol: str, as_of_date: str | None) -> list[str]:
    """QoQ/YoY growth of revenue and net profit from published quarters."""
    try:
        rows = published_statement_rows(symbol, "INCOME_STATEMENT", as_of_date)
    except NoMarketDataError:
        return []
    if len(rows) < 2:
        return []
    revenue_key = next((k for k in ("isa3", "isb38") if any(_num(r.get(k)) for r in rows[:5])), None)
    lines = []
    for key, label in ((revenue_key, "Doanh thu thuần/Tổng thu nhập hoạt động"), ("isa20", "Lợi nhuận sau thuế")):
        if not key:
            continue
        vals = [_num(r.get(key)) for r in rows]
        parts = []
        if vals[0] is not None and vals[1]:
            parts.append(f"QoQ {rfmt.fmt_signed_pct((vals[0] - vals[1]) / abs(vals[1]) * 100)}")
        if len(vals) >= 5 and vals[0] is not None and vals[4]:
            parts.append(f"YoY {rfmt.fmt_signed_pct((vals[0] - vals[4]) / abs(vals[4]) * 100)}")
        if parts:
            lines.append(f"- {label} {_period_label(rows[0], False)}: {_billion(vals[0])} ({', '.join(parts)})")
    return lines


def _peer_benchmark(symbol: str, metric: str, as_of_date: str | None) -> tuple[float | None, int]:
    """Average ``metric`` (pe/pb) of up to 8 sector peers: VCI's own figure of their newest
    published quarter, priced when VCI updated it (live runs only, see the module docstring)."""
    peers = sector.sector_peers(symbol)

    def value(peer: str) -> float | None:
        try:
            rows = published_ratio_rows(peer, as_of_date)
        except Exception:  # noqa: BLE001 - one peer missing must not sink the benchmark
            return None
        v = _num(rows[0].get(metric)) if rows else None
        return v if v is not None and 0 < v < 500 else None

    with ThreadPoolExecutor(max_workers=4) as pool:
        values = [v for v in pool.map(value, peers) if v is not None]
    if len(values) < 2:
        return None, len(values)
    return round(sum(values) / len(values), 2), len(values)


def _own_history_line(symbol: str, metric: str, as_of_date: str | None) -> str:
    """The run date's multiple from that session's price, against every session since 2018."""
    from tradingagents.dataflows import vn_valuation  # it reads this module's helpers

    try:
        return vn_valuation.multiple_line(symbol, metric, as_of_date)
    except Exception as exc:  # noqa: BLE001 - the history line is enrichment
        logger.warning("Valuation history of %s unavailable: %s", symbol, exc)
        return f"- {vn_valuation.METRIC_LABELS[metric]} hôm nay so với lịch sử của chính mã: chưa đủ dữ liệu"


def _valuation_card_line(symbol: str, as_of_date: str | None) -> str:
    """The system's valuation card of a bank in one line (the full card is in the engine's report)."""
    from tradingagents.dataflows import vn_valuation

    try:
        return vn_valuation.fundamentals_line(vn_valuation.card(symbol, as_of_date))
    except Exception as exc:  # noqa: BLE001 - the card is enrichment
        logger.warning("Valuation card of %s unavailable: %s", symbol, exc)
        return vn_valuation.fundamentals_line(None)


def _bank_peer_lines(symbol: str, as_of_date: str | None) -> list[str]:
    """A bank's KPIs against the median of the banks with the same quarter published by the run date
    (the evening job's read, point-in-time; no VCI call for the other banks)."""
    from tradingagents.dataflows import vn_peers

    try:
        return vn_peers.fundamentals_lines(vn_peers.kpi_table(symbol, as_of_date, "banking"))
    except Exception as exc:  # noqa: BLE001 - the comparison is enrichment
        logger.warning("Bank KPIs of %s against the banks unavailable: %s", symbol, exc)
        return []


def get_fundamentals(ticker: str, as_of_date: str | None = None) -> str:
    """Valuation, profitability, leverage and growth as known on the run date."""
    symbol = normalize_symbol(ticker)
    rows = published_ratio_rows(symbol, as_of_date)
    if not rows:
        raise NoMarketDataError(ticker, symbol, f"no financial ratios published by {as_of_date}")
    latest = rows[0]
    historical = is_historical(as_of_date)
    details: dict = {}
    if not historical:
        try:
            details = vci.fetch_details(symbol)
        except Exception as exc:  # noqa: BLE001 - the profile is enrichment
            logger.warning("VCI details unavailable for %s: %s", symbol, exc)
    profile = fundamental_profiles.get_profile(symbol, details.get("comTypeCode"))
    period = f"Q{latest.get('quarter')}/{latest.get('yearReport')}"
    if historical:
        vci_multiples = (f"- P/E, P/B, P/S, EV/EBITDA, vốn hoá và số cổ phiếu của VCI: bị giữ lại cho lượt phân tích "
                         f"ngày {as_of_date} (VCI tính theo giá ngày VCI cập nhật, có thể sau ngày này)")
    else:
        vci_multiples = (f"- P/E: {_ratio(latest.get('pe'))} | P/B: {_ratio(latest.get('pb'))} | P/S: {_ratio(latest.get('ps'))} | "
                         f"EV/EBITDA: {_ratio(latest.get('evToEbitda'))} (số VCI của kỳ, tính theo giá lúc VCI cập nhật, "
                         "không phải giá hôm nay)")

    lines = [
        f"# Chỉ số cơ bản của {symbol} (nguồn VCI)",
        f"# Tính tới ngày: {_as_of_day(as_of_date):%Y-%m-%d}; kỳ báo cáo mới nhất đã công bố: {period}",
        f"# Hồ sơ định giá: {profile.label} - so sánh chính theo {profile.benchmark_metric.upper()}. {profile.note}",
        "",
        "## Định giá và hiệu quả (TTM theo kỳ báo cáo)",
        vci_multiples,
        f"- ROE: {_pct(latest.get('roe'))} | ROA: {_pct(latest.get('roa'))} | ROIC: {_pct(latest.get('roic'))}",
    ]
    if profile.key == "banking":
        lines.append(
            f"- NIM: {_pct(latest.get('netInterestMargin'))} | CIR: {_pct(ratio_value(latest, 'cir'))} | "
            f"NPL: {_pct(latest.get('npl'))} | Bao phủ nợ xấu: {_pct(ratio_value(latest, 'loansLossReservesToNPLs'))} | "
            f"Chi phí tín dụng: {_pct(ratio_value(latest, 'provisionToOutstandingLoans'))} | "
            f"CASA: {_pct(latest.get('casaRatio'))} | LDR: {_pct(latest.get('ldrLoanDepositRatio'))}"
        )
    else:
        lines.append(
            f"- Biên lãi gộp: {_pct(latest.get('grossMargin'))} | Biên lãi ròng: {_pct(latest.get('afterTaxProfitMargin'))} | "
            f"Nợ phải trả/VCSH: {_ratio(latest.get('debtToEquity'))} | Thanh toán hiện hành: {_ratio(latest.get('currentRatio'))}"
        )
    if not historical:
        lines.append(
            f"- Vốn hoá theo giá VCI cập nhật: {_billion(latest.get('marketCap'))} | "
            f"Số cổ phiếu: {rfmt.fmt_price(_num(latest.get('numberOfSharesMktCap')))}"
        )

    metric = profile.benchmark_metric
    lines.append(_own_history_line(symbol, metric, as_of_date))
    if profile.key == "banking":
        lines.append(_valuation_card_line(symbol, as_of_date))
        lines += _bank_peer_lines(symbol, as_of_date)
    if historical:
        lines.append(f"- So sánh ngành: bị giữ lại cho lượt phân tích ngày {as_of_date} (theo số VCI của kỳ, giá sau ngày này).")
    else:
        current = _num(latest.get(metric))
        peer_avg, peer_n = _peer_benchmark(symbol, metric, as_of_date)
        if peer_avg is not None and current:
            gap = (current - peer_avg) / peer_avg * 100
            lines.append(
                f"- {metric.upper()} trung bình {peer_n} mã cùng ngành ({sector.get_primary_sector_label(symbol)}): "
                f"{rfmt.fmt_number(peer_avg)} → {symbol} {'cao hơn' if gap > 0 else 'thấp hơn'} {rfmt.fmt_pct(abs(gap))} "
                "(cùng theo số VCI của kỳ)"
            )
        else:
            lines.append("- So sánh ngành: chưa đủ dữ liệu mã cùng ngành.")

    growth = _growth_lines(symbol, as_of_date)
    lines += ["", "## Tăng trưởng theo quý (từ báo cáo đã công bố)"] + (growth or ["- Chưa đủ quý để tính tăng trưởng."])

    lines += ["", "## Hồ sơ hiện tại (chỉ khi phân tích ngày hôm nay)"]
    if historical:
        lines.append(
            f"- Bị giữ lại cho lượt phân tích ngày {as_of_date}: hồ sơ, sở hữu nước ngoài, giá mục tiêu của "
            "chuyên viên phân tích chỉ có giá trị hiện tại, không có phiên bản lịch sử."
        )
    elif details:
        foreign = _num(details.get("foreignerPercentage"))
        foreign_max = _num(details.get("maximumForeignPercentage"))
        lines += [
            f"- Tên: {details.get('viOrganName') or details.get('enOrganName')} | Ngành (ICB): {details.get('sectorVn') or details.get('sector')} | Nhóm chỉ số: {details.get('comGroupCode')}",
            f"- Sở hữu nước ngoài: {_pct(foreign)} / room tối đa {_pct(foreign_max)}"
            + (f" (room còn {rfmt.fmt_pct((foreign_max - foreign) * 100)})" if foreign is not None and foreign_max else ""),
            f"- Sở hữu nhà nước: {_pct(details.get('statePercentage'))}",
            f"- Giá cao/thấp nhất 52 tuần: {rfmt.fmt_price(_num(details.get('highestPrice1Year')))} / {rfmt.fmt_price(_num(details.get('lowestPrice1Year')))} VND",
            f"- Thanh khoản TB 1 tháng: {rfmt.fmt_price(_num(details.get('averageMatchVolume1Month')))} cp/phiên",
        ]
        if details.get("targetPrice"):
            lines.append(
                f"- Giá mục tiêu của VCI: {rfmt.fmt_price(_num(details.get('targetPrice')))} VND "
                f"(khuyến nghị {details.get('rating')}, ngày {details.get('ratingAsOf')}) - quan điểm bên ngoài, chỉ tham khảo"
            )
    else:
        lines.append("- Không lấy được hồ sơ hiện tại.")
    return "\n".join(lines)


def _labels(symbol: str, section: str) -> list[tuple[str, str, int]]:
    try:
        meta = vci.fetch_metric_labels(symbol).get(section) or []
    except Exception:  # noqa: BLE001 - labels are cosmetic; fall back to codes
        return []
    return [(m["field"], m.get("titleVi") or m.get("titleEn") or m["field"], int(m.get("level") or 1)) for m in meta]


def get_statement(ticker: str, section: str, freq: str = "quarterly", as_of_date: str | None = None, periods: int = 4) -> str:
    """One financial statement as a table of the latest published periods (tỷ đồng)."""
    symbol = normalize_symbol(ticker)
    annual = str(freq).lower().startswith("a") or str(freq).lower().startswith("y")
    rows = published_statement_rows(symbol, section, as_of_date, annual=annual)[:periods]
    title = _SECTION_TITLES[section]
    if not rows:
        raise NoMarketDataError(ticker, symbol, f"no {title} published by {as_of_date}")
    labels = _labels(symbol, section)
    if not labels:
        codes = sorted({k for r in rows for k in r if k[:3] in {"isa", "isb", "iss", "isi", "bsa", "bsb", "bss", "bsi", "cfa", "cfb", "cfs", "cfi"}})
        labels = [(c, c, 1) for c in codes]
    header = ["Chỉ tiêu"] + [
        f"{_period_label(r, annual)} (công bố {published_on(r, annual=annual):%d/%m/%Y})" for r in rows
    ]
    lines = [
        f"# {title} của {symbol} ({'năm' if annual else 'quý'}, đơn vị: tỷ đồng; EPS: đồng)",
        f"# Chỉ gồm báo cáo đã công bố tới ngày {_as_of_day(as_of_date):%Y-%m-%d}",
        "",
        "| " + " | ".join(header) + " |",
        "|" + "---|" * len(header),
    ]
    for field, label, level in labels:
        values = [_num(r.get(field)) for r in rows]
        if all(v is None or v == 0 for v in values):
            continue
        per_share = "cổ phiếu" in label.lower()
        cells = [
            "" if v is None else (rfmt.fmt_price(v) if per_share else rfmt.fmt_number(v / 1e9, 1))
            for v in values
        ]
        indent = "  " * max(0, level - 1)
        lines.append(f"| {indent}{label} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def get_income_statement(ticker: str, freq: str = "quarterly", as_of_date: str | None = None) -> str:
    return get_statement(ticker, "INCOME_STATEMENT", freq, as_of_date)


def get_balance_sheet(ticker: str, freq: str = "quarterly", as_of_date: str | None = None) -> str:
    return get_statement(ticker, "BALANCE_SHEET", freq, as_of_date)


def get_cashflow(ticker: str, freq: str = "quarterly", as_of_date: str | None = None) -> str:
    return get_statement(ticker, "CASH_FLOW", freq, as_of_date)


_EVENT_GROUPS = (
    ("Cổ tức & phát hành", ("DIV", "ISS")),
    ("Giao dịch nội bộ & cổ đông lớn", ("DDIND", "DDINS", "DDRP")),
    ("Đại hội cổ đông", ("AGME", "AGMR", "EGME")),
)


def get_corporate_events(ticker: str, as_of_date: str | None = None, look_back_days: int = 365) -> str:
    """Dividends, share issues, insider/major-holder deals and AGMs public by the run date."""
    symbol = normalize_symbol(ticker)
    end = _as_of_day(as_of_date)
    start = end - timedelta(days=look_back_days)
    events = vci.fetch_events(symbol, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    visible = []
    for event in events:
        public = _parse_day(event.get("publicDate")) or _parse_day(event.get("displayDate1"))
        if public is None or public > end:
            continue
        visible.append((public, event))
    lines = [
        f"# Sự kiện doanh nghiệp {symbol} từ {start:%d/%m/%Y} đến {end:%d/%m/%Y} (nguồn VCI)",
        "# Chỉ gồm sự kiện đã công bố trước hoặc trong ngày phân tích. Giao dịch nội bộ là ĐĂNG KÝ "
        "hoặc KẾT QUẢ giao dịch đã báo cáo, không phải giao dịch đã chắc chắn khớp.",
    ]
    if not visible:
        lines.append("\nKhông có sự kiện nào trong khoảng này.")
        return "\n".join(lines)
    for title, codes in _EVENT_GROUPS:
        group = sorted(((p, e) for p, e in visible if e.get("eventCode") in codes), key=lambda pe: pe[0])
        if not group:
            continue
        lines += ["", f"## {title}"]
        for public, event in reversed(group[-15:]):
            extra = []
            ex_date = _parse_day(event.get("exrightDate"))
            if ex_date:
                extra.append(f"GDKHQ {ex_date:%d/%m/%Y}")
            if event.get("valuePerShare"):
                extra.append(f"{rfmt.fmt_price(_num(event.get('valuePerShare')))} đ/cp")
            text = event.get("eventTitleVi") or event.get("eventNameVi") or event.get("eventTitleEn") or ""
            lines.append(f"- {public:%d/%m/%Y}: {text}" + (f" ({'; '.join(extra)})" if extra else ""))
    return "\n".join(lines)
