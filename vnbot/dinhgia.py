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
once a day (``vn_peers.bank_peers``): a bank's KPIs against the median of the banks with the same quarter.

The intraday monitor (``vnbot.intraday``) alerts on the kept cards when the price enters Rẻ or Đắt -
no VCI call there, a card kept after the session before's ``JOB_TIME`` only (``built_for``) - and the
job's zone moves are sent once it is done for the session (``night_block``).

Informational only: nothing of the Quant Engine reads it. The owner's card (/tsdinhgia) is shown
beside the bot's, named as his, never as the bot's; in the alerts it overrides the bot's.
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
ZONES_FILE = "valuation_zone_changes.json"   # under data/: the zones the newest job moved, for its message
MAX_ZONE_LINES = 12                   # zone changes named in the message; the rest counted
JOB_TIME = "16:00"                    # the evening job, Vietnam time: the session's bars are out (15:05)
JOB_WORKERS = 2                       # cards built at once (VCI is slow, the host has 2 GB)
MARK_CHANGE = 0.003                   # a level moved this much (0,3%) is a change worth saying
# The alerts' Đắt on the bot's card at least this far above Base: a bank's Bull by ROE p80 can sit a tick above
# Base (MBB 30/10/2026: +0,37%, CTG 16/12/2025: +0,30%), inside the alerts' 0,5% buffer (taisan.ZONE_BUFFER_PCT),
# and Rẻ then Đắt would both be said on a 0,6% move.
ALERT_BAND = 0.02
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


def built_for(row: dict, session: str) -> bool:
    """Whether ``row`` was kept after ``session``'s ``JOB_TIME``: the evening job keeps such a row of today
    instead of building it again, and the intraday alerts of the next session use a card by the same rule
    (a view between the bars, 15:05, and 16:00 is built again: it would be refused the next day)."""
    return float(row.get("ts") or 0) >= _job_time(session)


def alert_levels(row: dict) -> dict:
    """The bot's card ``row`` as the intraday alerts read it: Đắt from Bull, or from Base + ``ALERT_BAND``
    when Bull is closer to Base."""
    return {**row, "bull": max(float(row["bull"]), float(row["base"]) * (1 + ALERT_BAND))}


def band_note(base: float, bull: float) -> str:
    """The card's note when its Bull is under Base + ``ALERT_BAND`` (the alerts' Đắt is there), else ""."""
    floor = base * (1 + ALERT_BAND)
    return (f"Bull sát Base ({signed_pct(bull / base - 1)}): cảnh báo trong phiên báo Đắt từ Base "
            f"{signed_pct(ALERT_BAND, 0)} ({price(floor)})" if bull < floor else "")


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


def _move(row: dict, prev: dict | None) -> dict | None:
    """The zone ``row`` moved to from ``prev`` (both with a card), None when it did not."""
    if not prev or row.get("base") is None or prev.get("base") is None or row.get("zone") == prev.get("zone"):
        return None
    return {"from": prev["zone"], "to": row["zone"], "close": row["close"], "base": row["base"], "bull": row["bull"]}


def run_job(symbols: list[str], data_dir, *, session: str, today: str | None = None,
            workers: int = JOB_WORKERS) -> dict[str, list[str]]:
    """Build and keep the card of each of ``symbols`` as its row of ``today`` (``session`` unless told: a job
    caught up the day after keeps its rows as that session's), but those kept that day after ``session``'s
    ``JOB_TIME`` (``built_for``) with its price or refused (a view before is built again: a statement may
    have come out since, and the next session's alerts would not use it). The stocks by outcome: done (the
    price of ``session``), behind (an older price: the session's is not out yet), none (no card; the row says
    why), kept (done earlier), failed (VCI down - its breaker then fails the rest at once - or a fault). Each
    stock's zone move against its row of an earlier day is kept for ``night_block``, a kept one's too. A bank
    among them brings the day's read of every listed bank (``vn_peers.bank_peers``); "peers" is ["failed"]
    when they could not be read (retried like a failed stock)."""
    vv = _vv()
    today = today or session
    cards = Cards(data_dir)
    started = time.monotonic()
    out: dict[str, list[str]] = {k: [] for k in ("done", "behind", "none", "kept", "failed", "peers")}
    moved: dict[str, dict] = {}

    def one(symbol: str) -> str:
        try:
            rows = cards.rows(symbol)
            last = rows[0] if rows and rows[0].get("day") == today else {}
            if last and built_for(last, session) and (last.get("price_day") == session or last.get("note")):
                moved[symbol] = _move(last, rows[1] if len(rows) > 1 else None)
                return "kept"
            result = vv.assess(symbol, None)
            row, prev = cards.record(result, today)
            moved[symbol] = _move(row, prev)
        except Exception as exc:  # noqa: BLE001 - one stock never stops the others
            logger.warning("Valuation card of %s: %s: %s", symbol, type(exc).__name__, str(exc)[:200])
            return "failed"
        if not result:
            return "none"
        return "done" if result.day.isoformat() >= session else "behind"

    with ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="dinhgia") as pool:
        for symbol, outcome in zip(symbols, pool.map(one, symbols)):
            out[outcome].append(symbol)
    try:
        _keep_zone_changes(data_dir, session, moved)
    except OSError as exc:  # the cards are kept; only the zones message is missing
        logger.warning("Valuation cards: zone changes not kept: %s", exc)
    if any(vv.card_profile(s) == "banking" for s in symbols if s not in out["failed"]):
        try:
            _peers().bank_peers(today_vn(), compute=True)   # read today, whatever session
        except Exception as exc:  # noqa: BLE001 - the page says the medians are missing; retried
            logger.warning("Valuation cards: bank medians not read: %s: %s", type(exc).__name__, exc)
            out["peers"].append("failed")
    logger.info("Valuation cards %s: %d stocks in %.0f s - %d new, %d with an older price, %d without a card, "
                "%d kept from earlier today, %d failed%s%s", session, len(symbols), time.monotonic() - started,
                len(out["done"]), len(out["behind"]), len(out["none"]), len(out["kept"]), len(out["failed"]),
                f" ({', '.join(out['failed'][:10])})" if out["failed"] else "",
                "; bank medians not read" if out["peers"] else "")
    return out


def _zones_path(data_dir) -> Path:
    return Path(data_dir) / ZONES_FILE


def _keep_zone_changes(data_dir, session: str, moved: dict) -> None:
    """The zones the job of ``session`` moved against each stock's row of an earlier day, its runs
    together (a stock built again: its newest outcome, None for no move), for ``night_block``."""
    path = _zones_path(data_dir)
    with _file_lock:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict) or data.get("day") != session:
            data = {"day": session, "changes": {}, "reported": ""}
        before = dict(data["changes"])
        for symbol, change in moved.items():
            if change:
                data["changes"][symbol] = change
            else:
                data["changes"].pop(symbol, None)
        if data["changes"] != before:
            write_text_atomic(path, json.dumps(data, ensure_ascii=False))


def night_block(data_dir, session: str) -> str:
    """The message of the zones the job of ``session`` moved (at its close), sent once the job is done for
    the session (``service.DailyScheduler``); "" when none moved, or said already (kept as reported)."""
    path = _zones_path(data_dir)
    with _file_lock:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ""
        if not isinstance(data, dict) or not data.get("changes") or data.get("day") != session or data.get("reported"):
            return ""
        data["reported"] = today_vn()
        write_text_atomic(path, json.dumps(data, ensure_ascii=False))
    items = sorted(data["changes"].items(), key=lambda kv: (kv[1]["to"] != "Rẻ", kv[1]["to"] != "Đắt", kv[0]))
    parts = [f"{code} {c['from']} → {c['to']} (giá {price(c['close'])}, Base {price(c['base'])}, Bull {price(c['bull'])})"
             for code, c in items[:MAX_ZONE_LINES]]
    more = f" · và {len(items) - MAX_ZONE_LINES} mã khác" if len(items) > MAX_ZONE_LINES else ""
    return (f"📐 Thẻ định giá của bot đổi vùng (tính sau phiên {ddmm(data['day'])}, theo giá đóng cửa): "
            + " · ".join(parts) + more + ". Chi tiết: /dinhgia MÃ (tham khảo, không vào quyết định).")


# ─── KPIs, and a bank's against every listed bank (tradingagents.dataflows.vn_peers) ──

def _peers():
    from tradingagents.dataflows import vn_peers

    return vn_peers


def kpi_table(symbol: str, profile: str):
    """The stock's KPI table of today (``vn_peers.kpi_table``: a bank's against the banks' medians of the
    evening job's read), None without one; VCI down raises."""
    return _peers().kpi_table(symbol, None, profile)


SHORT_KPIS = (("netInterestMargin", "NIM"), ("cir", "CIR"), ("npl", "NPL"), ("loansLossReservesToNPLs", "bao phủ nợ xấu"),
              ("provisionToOutstandingLoans", "chi phí tín dụng"))


def bank_line(t) -> str:
    """/dinhgia MÃ of a bank: its main KPIs against the banks with the same quarter, one line."""
    vp = _peers()
    if t is None or t.profile != "banking":
        return ""
    rows = {k.field: k for k in t.rows}
    if t.peers_read is None or not t.covered or t.with_period < vp.MIN_PEERS:
        own = " · ".join(f"{name} {vp.fmt(rows[f].value)}" for f, name in SHORT_KPIS if rows[f].value is not None)
        why = (f"bot đọc sau {JOB_TIME} khi watchlist, Tài sản hoặc thẻ anh nhập có một ngân hàng" if t.peers_read is None
               else "số đọc sẵn không phủ kỳ này" if not t.covered
               else f"mới {t.with_period} ngân hàng đã có {t.period} {vp.until_text(t)}")
        return f"🏦 Chỉ số {t.period}: {own} (chưa có trung vị các ngân hàng: {why})"
    parts = [f"{name} {vp.fmt(k.value)} ({vp.fmt(k.median)}" + (f" · {k.rank}/{k.n}" if k.rank else "") + ")"
             for f, name in SHORT_KPIS if (k := rows[f]).value is not None]
    if t.roe5 is not None:
        parts.append(f"ROE {len(t.roe5_years)} năm {vp.fmt(t.roe5)} ({vp.fmt(t.roe5_median)} · {t.roe5_rank}/{t.roe5_of})")
    newer = f"; {t.newer} NH đã có kỳ mới hơn" if t.newer else ""
    return (f"🏦 So với {t.with_period} ngân hàng đã có {t.period} (trong ngoặc: trung vị nhóm · hạng, 1 là tốt nhất{newer}; "
            f"VCI đọc {ddmm(t.peers_read.isoformat())}) {_vv()._label('THỐNG KÊ', t.peers_read, t.as_of)}: "
            + " · ".join(parts))


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


def card_text(result, row: dict, prev: dict | None, *, account=None, public_url: str = "", kpis=None) -> str:
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
        " · ".join(x for x in (ZONE_RULE, band_note(c.base, c.bull)) if x),
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
        *([line] if (line := bank_line(kpis)) else []),
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
    table = None
    if result.profile == "banking":
        try:
            table = kpi_table(code, result.profile)
        except Exception as exc:  # noqa: BLE001 - the card without the banks' line
            logger.info("/dinhgia %s: KPIs unread: %s", code, exc)
    return card_text(result, row, prev, account=account, public_url=public_url, kpis=table)
