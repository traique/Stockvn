"""Định giá bot tự tính (/dinhgia, the /dinhgia pages).

The bot's own valuation card of any stock (``vn_valuation.assess``: Bear / eab / Base / Bull, the
zone, MOS, the reliability of its profile, or why there is none), kept one row a (day, stock) in the
history store (``valuations``; ``data/valuation_cards.json`` when the store is off). A view compares
the day's card with the newest row of an earlier day ("Có gì đổi so với lần xem trước"): a level
moved 0,3% or more, the zone, a new statement, the assumptions - "không đổi" otherwise.

After the close (``service.DailyScheduler``, a thread of its own) ``run_job`` computes and keeps the
cards of the tracked stocks - the watchlist, the Tài sản holdings, the stocks with a card the owner
entered - two at a time, behind VCI's breaker, a stock already done with the session's price (or
refused on the session's data) skipped. With a bank among them it also reads every listed bank's KPIs
once a day (``bank_peers``), for the page's "so với trung vị ngân hàng".

Informational only: nothing of the Quant Engine reads it. The owner's card (/tsdinhgia) is shown
beside the bot's, named as his, never as the bot's.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from vnbot import store as store_mod
from vnbot.jobs import write_text_atomic

logger = logging.getLogger(__name__)

STATE_FILE = "valuation_cards.json"   # under data/ while the history store is off: each stock's two newest days
JOB_TIME = "16:00"                    # the evening job, Vietnam time: the session's bars are out (15:05)
JOB_WORKERS = 2                       # cards built at once (VCI is slow, the host has 2 GB)
MARK_CHANGE = 0.003                   # a level moved this much (0,3%) is a change worth saying
LEVELS = (("bear", "Bear"), ("eab", "eab"), ("base", "Base"), ("bull", "Bull"))
COLUMNS = tuple(name for name, _ in store_mod.SCHEMA["valuations"][0])
USAGE = "Dùng: /dinhgia FPT - định giá bot tự tính của một mã · /dinhgia - bảng các mã đang theo dõi (gõ tắt /dg)"
DISCLAIMER = ("Ước tính của một mô hình từ số liệu công bố, không phải khuyến nghị mua bán hay giá mục tiêu; "
              "không vào quyết định, cắt lỗ hay mục tiêu của bot.")
ZONE_RULE = "Rẻ: giá ≤ Base · Đắt: giá ≥ Bull · MOS = (Base − giá)/Base"
_file_lock = threading.Lock()


def _vv():
    from tradingagents.dataflows import vn_valuation

    return vn_valuation


def today_vn() -> str:
    from tradingagents.market import vn_rules

    return vn_rules.now_vn().date().isoformat()


def _now() -> float:
    from tradingagents.market import vn_rules

    return vn_rules.now_vn().timestamp()


def _job_time(day: str) -> float:
    """``JOB_TIME`` of ``day``, Vietnam time, as a timestamp: a row kept since was built on that
    session's data (its close is out, and every statement published that day)."""
    from datetime import datetime

    from tradingagents.market import vn_rules

    return datetime.fromisoformat(f"{day}T{JOB_TIME}").replace(tzinfo=vn_rules.VN_TZ).timestamp()


def ddmm(day: str) -> str:
    return f"{day[8:10]}/{day[5:7]}" if day else ""


def price(value) -> str:
    return "—" if value is None else f"{value:,.0f}".replace(",", ".")


def signed_pct(value: float, decimals: int = 1) -> str:
    """+1,2% from a ratio (0,012)."""
    return f"{value * 100:+.{decimals}f}%".replace(".", ",")


def rate(value: float) -> str:
    """An assumption in %, without trailing zeros: 12%, 12,5%."""
    return f"{value * 100:.2f}".rstrip("0").rstrip(".").replace(".", ",") + "%"


def profile_label(key: str | None) -> str:
    from tradingagents.dataflows.valuation_config import PROFILE_LABELS

    return PROFILE_LABELS.get(key or "", "doanh nghiệp")


# ─── the rows ─────────────────────────────────────────────────────────────────

def row_of(result, day: str) -> dict:
    """The stored row of a card (or of why there is none) seen on ``day``."""
    vv = _vv()
    row = dict.fromkeys(COLUMNS)
    row.update(day=day, symbol=result.symbol, ts=_now(), profile=result.profile)
    if not isinstance(result, vv.ValuationCard):
        row["note"] = result.reason
        return row
    c, a = result, result.assumptions
    row.update(price_day=c.day.isoformat(), close=c.close, period=c.period, bvps=c.book, roe=c.roe_base, ke=a.ke,
               lam=a.lam, bear=c.bear, eab=c.eab, base=c.base, bull=c.bull, zone=c.zone, mos=c.mos,
               pb_pct=c.pb_rank.higher_pct if c.pb_rank else None, expires=c.next_ex.isoformat() if c.next_ex else "",
               reliability=c.reliability, note="")
    return row


class Cards:
    """The rows: the history store when it is open, else ``STATE_FILE`` (each stock's two newest days)."""

    def __init__(self, data_dir):
        self.path = Path(data_dir) / STATE_FILE

    @staticmethod
    def _db():
        return store_mod.current()

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def rows(self, symbol: str) -> list[dict]:
        """The stock's rows, newest day first (two at most)."""
        db = self._db()
        if db is not None:
            return store_mod.safe("valuation rows", lambda: db.valuation_rows(symbol), []) or []
        with _file_lock:
            return [r for r in self._read().get(symbol) or [] if isinstance(r, dict)][:2]

    def latest(self) -> dict[str, dict]:
        """Every stock's newest row."""
        db = self._db()
        if db is not None:
            return {r["symbol"]: r for r in store_mod.safe("latest valuations", db.latest_valuations, []) or []}
        with _file_lock:
            return {s: rows[0] for s, rows in self._read().items() if isinstance(rows, list) and rows}

    def save(self, row: dict) -> None:
        """The stock's row of its day, replacing that day's earlier one."""
        db = self._db()
        if db is not None:
            store_mod.safe("save valuation", lambda: db.upsert("valuations", [row]))
            return
        with _file_lock:
            data = self._read()
            kept = [r for r in data.get(row["symbol"]) or [] if isinstance(r, dict) and r.get("day") != row["day"]]
            data[row["symbol"]] = sorted([row, *kept], key=lambda r: r["day"], reverse=True)[:2]
            write_text_atomic(self.path, json.dumps(data, ensure_ascii=False))

    def record(self, result, day: str | None = None) -> tuple[dict, dict | None]:
        """Keep ``result`` as the stock's row of ``day`` (today): (that row, the newest row of an
        earlier day - the last time before today the card was seen)."""
        day = day or today_vn()
        row = row_of(result, day)
        previous = next((r for r in self.rows(row["symbol"]) if r.get("day", "") < day), None)
        self.save(row)
        return row, previous


def changes(row: dict, prev: dict | None) -> list[str]:
    """What moved from ``prev`` to ``row``: a level by ``MARK_CHANGE`` or more, the zone, a new statement,
    the assumptions, the reliability; [] when nothing did (or there is nothing to compare with)."""
    if prev is None:
        return []
    if row.get("base") is None or prev.get("base") is None:
        if row.get("base") is not None:
            return [f"có thẻ (lần trước không: {prev.get('note') or 'chưa đủ dữ liệu'})"]
        if prev.get("base") is not None:
            return [f"không còn thẻ: {row.get('note')}"]
        return [] if row.get("note") == prev.get("note") else [f"lý do không có thẻ: {row.get('note')}"]
    out = []
    if row.get("period") != prev.get("period"):
        out.append(f"BCTC mới: {prev.get('period')} → {row.get('period')}")
    if prev.get("ke") is not None and abs(row["ke"] - prev["ke"]) > 1e-9:
        out.append(f"Ke {rate(prev['ke'])} → {rate(row['ke'])}")
    if prev.get("lam") is not None and abs(row["lam"] - prev["lam"]) > 1e-9:
        out.append(f"λ {prev['lam']:g} → {row['lam']:g}".replace(".", ","))
    for key, name in LEVELS:
        old, new = prev.get(key), row.get(key)
        if old and new and abs(new / old - 1) >= MARK_CHANGE:
            out.append(f"{name}: {price(old)} → {price(new)} ({signed_pct(new / old - 1)})")
    if row.get("zone") != prev.get("zone"):
        out.append(f"vùng: {prev.get('zone')} → {row.get('zone')}")
    if prev.get("reliability") and row.get("reliability") != prev.get("reliability"):
        out.append(f"độ tin cậy: {prev['reliability']} → {row.get('reliability')}")
    return out


def changes_line(row: dict, prev: dict | None) -> str:
    if prev is None:
        return "Có gì đổi so với lần xem trước: lần đầu bot tính mã này, chưa có lần trước để so."
    return f"Có gì đổi so với lần xem trước ({ddmm(prev['day'])}): " + ("; ".join(changes(row, prev)) or "không đổi")


# ─── the tracked stocks and the evening job ──────────────────────────────────

def tracked(watchlist, account=None) -> list[str]:
    """The stocks the evening job keeps a card of: the watchlist, the Tài sản holdings, the stocks
    with a card the owner entered (/tsdinhgia); in that order, once each."""
    from tradingagents.dataflows.symbols import is_valid_code, normalize_symbol
    from tradingagents.market import vn_rules

    codes = list(watchlist or [])
    if account is not None:
        try:
            codes += sorted(account.book().holdings) + sorted(account.valuations())
        except Exception as exc:  # noqa: BLE001 - the watchlist alone then
            logger.warning("Valuation cards: Tài sản holdings and cards unreadable: %s", exc)
    out: list[str] = []
    for raw in codes:
        code = normalize_symbol(str(raw or "").strip())
        if code and is_valid_code(code) and not vn_rules.is_index(code) and code not in out:
            out.append(code)
    return out


def run_job(symbols: list[str], data_dir, *, session: str, today: str | None = None,
            workers: int = JOB_WORKERS) -> dict[str, list[str]]:
    """Build and keep today's card of each of ``symbols``, but those kept earlier today with the price
    of ``session``, or refused after its ``JOB_TIME`` (a refusal from a morning view is built again: a
    statement may have come out since). The stocks by outcome: done (the price of ``session``), behind
    (an older price: the session's is not out yet), none (no card; the row says why), kept (done earlier
    today), failed (VCI down - its breaker then fails the rest at once - or a fault). A bank among them
    brings the day's medians of every listed bank (``bank_peers``); "peers" is ["failed"] when they could
    not be read (retried like a failed stock)."""
    vv = _vv()
    today = today or today_vn()
    cards = Cards(data_dir)
    started = time.monotonic()
    out: dict[str, list[str]] = {k: [] for k in ("done", "behind", "none", "kept", "failed", "peers")}
    refused_after = _job_time(session)

    def one(symbol: str) -> str:
        try:
            rows = cards.rows(symbol)
            last = rows[0] if rows and rows[0].get("day") == today else {}
            if last and (last.get("price_day") == session
                         or last.get("note") and float(last.get("ts") or 0) >= refused_after):
                return "kept"
            result = vv.assess(symbol, None)
            cards.record(result, today)
        except Exception as exc:  # noqa: BLE001 - one stock never stops the others
            logger.warning("Valuation card of %s: %s: %s", symbol, type(exc).__name__, str(exc)[:200])
            return "failed"
        if not result:
            return "none"
        return "done" if result.day.isoformat() >= session else "behind"

    with ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="dinhgia") as pool:
        for symbol, outcome in zip(symbols, pool.map(one, symbols)):
            out[outcome].append(symbol)
    if any(vv.card_profile(s) == "banking" for s in symbols if s not in out["failed"]):
        try:
            bank_peers(today, compute=True)
        except Exception as exc:  # noqa: BLE001 - the page says the medians are missing; retried
            logger.warning("Valuation cards: bank medians of %s not read: %s: %s", today, type(exc).__name__, exc)
            out["peers"].append("failed")
    logger.info("Valuation cards %s: %d stocks in %.0f s - %d new, %d with an older price, %d without a card, "
                "%d kept from earlier today, %d failed%s%s", session, len(symbols), time.monotonic() - started,
                len(out["done"]), len(out["behind"]), len(out["none"]), len(out["kept"]), len(out["failed"]),
                f" ({', '.join(out['failed'][:10])})" if out["failed"] else "",
                "; bank medians not read" if out["peers"] else "")
    return out


# ─── KPIs, and a bank's against every listed bank ────────────────────────────

BANK_ICB = "8300"
PEER_QUARTERS = 4            # each bank's newest quarters kept: the stock's own may be one or two behind
# (VCI ratio field, label, "pct" or "x"); VCI's TTM figures of the quarter, its stray minus signs dropped.
# VCI's debtToEquity is total liabilities (bsa54) / equity (bsa78), not borrowings: HPG Q2/2026 0,971 =
# 137.414 / 141.516 tỷ, borrowings 0,70 (checked live 30/10/2026); an insurer's is mostly reserves (BVH 10,8).
BANK_KPIS = (("roe", "ROE (4 quý)", "pct"), ("netInterestMargin", "NIM (4 quý, theo VCI)", "pct"),
             ("cir", "Chi phí/thu nhập (CIR)", "pct"), ("npl", "Nợ xấu (NPL)", "pct"),
             ("loansLossReservesToNPLs", "Bao phủ nợ xấu", "pct"), ("provisionToOutstandingLoans", "Dự phòng/dư nợ", "pct"),
             ("casaRatio", "CASA", "pct"), ("ldrLoanDepositRatio", "Cho vay/huy động (LDR)", "pct"))
OTHER_KPIS = (("roe", "ROE (4 quý)", "pct"), ("roa", "ROA (4 quý)", "pct"), ("grossMargin", "Biên lãi gộp", "pct"),
              ("afterTaxProfitMargin", "Biên lãi ròng", "pct"), ("debtToEquity", "Nợ phải trả/VCSH", "x"),
              ("currentRatio", "Thanh toán hiện hành", "x"))


def kpi_fields(profile: str) -> tuple:
    return BANK_KPIS if profile == "banking" else OTHER_KPIS


def own_kpis(symbol: str, profile: str, as_of: str | None = None) -> dict | None:
    """The newest published quarter's KPIs: {"key": "2026-2", "period": "Q2/2026", "published": date,
    "values": {field: value}}; None without one. VCI's ratio rows, cached half an hour (the card read them)."""
    from tradingagents.dataflows import vn_fundamentals as vf

    rows = _informational(lambda: vf.published_ratio_rows(symbol, as_of))
    if not rows:
        return None
    r = rows[0]
    year, quarter = int(r.get("yearReport") or 0), int(r.get("quarter") or 0)
    published = vf._statement_quarters(symbol).get((year, quarter))
    return {"key": f"{year}-{quarter}", "period": f"Q{quarter}/{year}", "published": published,
            "values": {f: vf.ratio_value(r, f) for f, _, _ in kpi_fields(profile)}}


def _informational(call):
    """``call`` behind the breaker of the informational VCI calls (``money_flows.VCI_INFO``); a stock
    without the data (``NoMarketDataError``) is None, not an outage that trips the breaker."""
    from tradingagents.dataflows import money_flows
    from tradingagents.dataflows.errors import NoMarketDataError

    def guarded():
        try:
            return call()
        except NoMarketDataError:
            return None

    return money_flows._guarded(money_flows.VCI_INFO, guarded)


def _peers_path(day: str) -> Path:
    from tradingagents.dataflows.config import get_config

    return Path(get_config()["data_cache_dir"]) / "valuation" / f"peers-{BANK_ICB}-{day}.json"


def bank_peers(day: str, *, compute: bool = False) -> dict | None:
    """Every listed bank's (ICB 8300 in VCI's listing) KPIs of its ``PEER_QUARTERS`` newest quarters
    published by ``day`` and its 5-year median ROE: {"day", "banks": {code: {"q": {"2026-2": {field:
    value}}, "roe5": x}}}, kept on disk for the day. None when not read yet; ``compute`` reads it now
    (the evening job: two VCI calls a bank, ~27 banks). VCI down, or no bank read (a listing without
    banks, no bank's ratios), raises and nothing is kept: the job tries again, the page shows older ones."""
    import numpy as np

    from tradingagents.dataflows import vn_fundamentals as vf
    from tradingagents.dataflows.errors import NoMarketDataError
    from tradingagents.dataflows.vendors import vci

    path = _peers_path(day)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("banks"), dict):
            return data
    except (OSError, ValueError):
        pass
    if not compute:
        return None
    started = time.monotonic()
    listing = _informational(vci.fetch_listing) or []
    codes = sorted({str(r.get("symbol") or "").upper() for r in listing
                    if str(r.get("icbCode2")) == BANK_ICB and r.get("type") == "STOCK"} - {""})
    banks = {}
    for code in codes:
        rows = _informational(lambda code=code: vf.published_ratio_rows(code, day))
        annual = _informational(lambda code=code: vf.published_annual_ratio_rows(code, day))
        if not rows:                         # PCB: no ratio rows at VCI (checked live 29/10/2026)
            continue
        quarters = {f"{int(r['yearReport'])}-{int(r['quarter'])}": {f: vf.ratio_value(r, f) for f, _, _ in BANK_KPIS}
                    for r in rows[:PEER_QUARTERS]}
        roes = [v for r, _ in annual or [] if (v := vf._num(r.get("roe"))) is not None][:5]
        banks[code] = {"q": quarters, "roe5": float(np.median(roes)) if roes else None}
    if not banks:
        raise NoMarketDataError(f"ICB {BANK_ICB}", None, f"no bank KPIs read ({len(codes)} banks in VCI's listing)")
    data = {"day": day, "banks": banks}
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, json.dumps(data))
    logger.info("Valuation cards: KPIs of %d banks read in %.0f s", len(banks), time.monotonic() - started)
    return data


def latest_bank_peers(day: str) -> dict | None:
    """The newest banks' KPIs read by ``day`` (the evening job's; a page never reads VCI for them)."""
    folder = _peers_path(day).parent
    days = sorted((p.stem.rsplit("-", 3)[-3:] for p in folder.glob(f"peers-{BANK_ICB}-*.json")), reverse=True)
    for parts in days:
        found = "-".join(parts)
        if found <= day and (data := bank_peers(found)) and data["banks"]:
            return data
    return None


def peer_medians(peers: dict, key: str) -> dict:
    """{field: (median, n)} over the banks with quarter ``key`` (a 0 is VCI's "no figure": left out),
    and {"roe5": {code: x}} for the rank."""
    import numpy as np

    out = {}
    for field, _, _ in BANK_KPIS:
        values = [q[field] for b in peers.get("banks", {}).values() if (q := b.get("q", {}).get(key))
                  and q.get(field) not in (None, 0)]
        out[field] = (float(np.median(values)), len(values)) if values else (None, 0)
    out["roe5"] = {c: b["roe5"] for c, b in peers.get("banks", {}).items() if b.get("roe5") is not None}
    return out


# ─── the phone text ───────────────────────────────────────────────────────────

def _manual_line(symbol: str, account, bot_base: float | None) -> str:
    """The owner's own card (/tsdinhgia), named as his, and how far its Base is from the bot's."""
    if account is None:
        return ""
    try:
        card = account.valuations().get(symbol)
    except Exception:  # noqa: BLE001 - shown without it
        return ""
    if not card:
        return ""
    from vnbot import taisan as ts

    flags, stale = "", False
    try:
        status = account.valuation_status(symbol, card, today_vn())
        if status.get("stale"):
            stale = True
            flags += f" · cần cập nhật (GDKHQ {ts.ddmmyyyy(status['stale']['ex'])})"
        if status.get("expired"):
            flags += f" · HẾT HẠN {ts.ddmmyyyy(card['expires'])}"
    except Exception as exc:  # noqa: BLE001 - the levels shown without the flags
        logger.info("Valuation card of %s entered by hand: status unread: %s", symbol, exc)
    # past an ex-date his levels are on the old share basis, the bot's on the new one: no gap (as /taisan)
    gap = (f" → Base của anh {'cao' if card['base'] > bot_base else 'thấp'} hơn bot "
           f"{abs(card['base'] / bot_base - 1) * 100:.1f}%".replace(".", ",") if bot_base and not stale else "")
    return (f"✍️ Thẻ anh nhập (/tsdinhgia, không phải của bot, nhập {ts.ddmm(card['day'])}): {ts.card_levels(card)}"
            f"{flags}{gap}")


def _multiple_text(rank, label: str) -> str:
    from tradingagents.quant import report_format as rfmt

    return (f"{label} {rfmt.fmt_number(rank.value)}: rẻ hơn {_vv().cheaper_pct(rank)}% số phiên từ "
            f"{rank.since:%m/%Y}")


def card_text(result, row: dict, prev: dict | None, *, account=None, public_url: str = "") -> str:
    """/dinhgia MÃ: the card on a phone - zone and MOS first, then the levels, the multiple's
    percentile, what Base rests on, the sensitivity, the reliability, the dates, the changes, the
    owner's card - every figure with its label and date."""
    from tradingagents.quant import report_format as rfmt

    vv = _vv()
    code = result.symbol
    link = f"\nChi tiết: {public_url.rstrip('/')}/dinhgia/{code}" if public_url else ""
    head = f"📐 ĐỊNH GIÁ {code} · {profile_label(result.profile)} · bot tự tính"
    if not isinstance(result, vv.ValuationCard):
        lines = [head, f"Bot không lập thẻ: {result.reason}.", changes_line(row, prev)]
        manual = _manual_line(code, account, None)
        return "\n".join([*lines, *([manual] if manual else [])]) + link
    c, a = result, result.assumptions
    model, as_of = vv._label("THỐNG KÊ", c.day, c.as_of), c.as_of
    years = [y for y, _ in c.roes[-vv.ROE_YEARS:]]
    multiples = [_multiple_text(r, vv.METRIC_LABELS[r.metric]) for r in (c.pe_rank, c.pb_rank) if r]
    (low_ke, high_base), (high_ke, low_base) = c.sensitivity[0], c.sensitivity[-1]
    weak = " (tin cậy THẤP)" if c.reliability == "thấp" else ""
    bull = f" ({vv.bull_basis(c)})" if c.bull_ke is not None else ""
    lines = [
        head,
        f"Giá {price(c.close)} (phiên {vv._day(c.day, as_of)}) → vùng {c.zone.upper()}{weak} · MOS {signed_pct(c.mos)} "
        f"so với Base {price(c.base)}",
        f"Bear {price(c.bear)} · eab {price(c.eab)} · Base {price(c.base)} · Bull {price(c.bull)}{bull} {model}",
        ZONE_RULE,
    ]
    if multiples:
        lines.append(" · ".join(multiples) + f" {model}")
    lines += [
        f"Base = sổ sách {price(c.book)} đ/cp {vv._label('FACT', c.day, as_of)} × {rfmt.fmt_number(c.base / c.book)} lần: "
        f"ROE bền vững {vv._pct(c.roe_base)} (trung vị {years[0]}-{years[-1]}) "
        f"{vv._label('THỐNG KÊ', c.annual_published, as_of)} · Ke {vv._rate(a.ke)} đặt tay · λ {rfmt.fmt_number(a.lam, 1)} · "
        f"g {vv._rate(a.g)} · eab = Base × (1 − {vv._rate(a.margin)})",
        f"Độ nhạy theo Ke: {vv._rate(low_ke)} → {price(high_base)} · {vv._rate(a.ke)} → {price(c.base)} · "
        f"{vv._rate(high_ke)} → {price(low_base)}",
        f"Độ tin cậy: {c.reliability.upper()} - {c.reliability_why}",
        f"BCTC {c.period} công bố {vv._day(c.published, as_of)} · {vv._expiry(c)}",
        changes_line(row, prev),
    ]
    manual = _manual_line(code, account, c.base)
    if manual:
        lines.append(manual)
    return "\n".join(lines) + link + "\n" + DISCLAIMER


LIST_ORDER = "rẻ nhất so với Base trước, tin cậy thấp xếp sau"


def list_order(rows) -> list[dict]:
    """The rows with a card, the cheapest against Base first; a low-reliability card after the others
    (a large MOS from a model weak for that stock must not top the list)."""
    return sorted((r for r in rows if r.get("base") is not None),
                  key=lambda r: (r.get("reliability") == "thấp", -float(r["mos"])))


def _row_line(r: dict, newest: str) -> str:
    when = f" (giá phiên {ddmm(r['price_day'])})" if r.get("price_day") and r["price_day"] != newest else ""
    return (f"• {r['symbol']} {r['zone']} · MOS {signed_pct(r['mos'])} · Base {price(r['base'])} · giá {price(r['close'])}"
            f"{when} · tin cậy {r.get('reliability') or '—'}")


def list_text(symbols: list[str], data_dir, *, account=None) -> str:
    """/dinhgia: the tracked stocks' newest cards, the cheapest against Base first; those without a
    card with why, those not computed yet."""
    latest = Cards(data_dir).latest()
    rows = {s: latest[s] for s in symbols if s in latest}
    cards = list_order(rows.values())
    newest = max((r["price_day"] for r in cards), default="")
    manual = set()
    if account is not None:
        try:
            manual = set(account.valuations())
        except Exception:  # noqa: BLE001 - listed without the marks
            manual = set()
    head = (f"📐 ĐỊNH GIÁ BOT TỰ TÍNH · {len(symbols)} mã theo dõi (watchlist, Tài sản, thẻ anh nhập)"
            + (f" · giá đóng cửa phiên {ddmm(newest)}" if newest else "") + f" · {LIST_ORDER}")
    lines = [head] + [_row_line(r, newest) + (" · ✍️ có thẻ anh nhập" if r["symbol"] in manual else "") for r in cards]
    lines += [f"• {r['symbol']}: không lập thẻ - {r.get('note')}" for s, r in rows.items() if r.get("base") is None]
    missing = [s for s in symbols if s not in rows]
    if missing:
        lines.append(f"Chưa tính: {', '.join(missing)} (bot tính sau {JOB_TIME} mỗi phiên; /dinhgia MÃ để tính ngay)")
    if not symbols:
        lines.append("Chưa theo dõi mã nào: đặt watchlist bằng /theodoi, hoặc xem một mã bằng /dinhgia FPT.")
    lines += [ZONE_RULE + ". /dinhgia MÃ: chi tiết và có gì đổi.", DISCLAIMER]
    return "\n".join(lines)


def view(symbol: str, data_dir):
    """The card of ``symbol`` today (built, or kept from the last hour), kept as today's row:
    (card or why none, the row, the newest row of an earlier day). VCI down raises."""
    result = _vv().assess(symbol, None)
    row, prev = Cards(data_dir).record(result)
    return result, row, prev


def command(args: str, data_dir, *, watchlist=(), account=None, public_url: str = "", progress=None) -> str:
    """/dinhgia [MÃ] (Telegram and Zoom). ``progress``: told first when the card takes a while."""
    from tradingagents.dataflows.errors import VendorError
    from vnbot.service import check_request

    words = (args or "").split()
    if not words:
        return list_text(tracked(watchlist, account), data_dir, account=account)
    if len(words) > 1:
        raise ValueError(USAGE)
    code, _ = check_request(words[0])
    if progress is not None and not _vv().kept(code, None):
        progress(f"⏳ Đang tính định giá {code} (lần đầu một mã khoảng 10-20 giây)...")
    try:
        result, row, prev = view(code, data_dir)
    except VendorError as exc:
        return f"⚠️ VCI đang lỗi hoặc chậm, chưa tính được định giá {code} ({exc}). Thử lại sau ít phút."
    return card_text(result, row, prev, account=account, public_url=public_url)
