"""The bot's own market-wide scan of in-session trend changes, compared with sieutinhieu.vn.

The site's full list of the session's trend changes is a paid product. This scans the
``external.market_size()`` (300) most traded stocks of the market with the rule of
``vnbot.trend_estimate`` from public prices only, and never asks the site about them: the
starting state (BUY or SELL) of each stock is the rule's own too.

- The closes: one request a session for the whole market (VNDirect), kept in
  ``data/external/market_closes.json`` (``HISTORY_SESSIONS`` sessions; the first fill takes
  a few scans, then about two requests a day). VNDirect adjusts its closes for dividends
  and bonus issues as of the day it is asked: each day the last saved session is asked
  again, and a stock whose close moved has its saved closes re-based.
- The last prices: one request for all of them (the price board, VCI), with the session's
  reference price, which also re-bases the levels on an ex-date (``trend_estimate.rebase``).

Compared with the site only where it can be for free: its counts of the day's changes
(market-wide, indices included; the rows themselves are locked), the two rows a side it
shows, and the stocks the owner follows, whose signals the bot fetches every day anyway.
One record a session (``data/external/market_compare.json``) keeps how often the scan
matched the site on those.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from vnbot import trend_estimate as te
from vnbot.jobs import write_text_atomic

logger = logging.getLogger(__name__)

HISTORY_SESSIONS = 130         # levels from 120 closes are within 0,02% of a 4-year history
KEEP_EXTRA = 5                 # sessions kept beyond, so the scan the day after still has its 130
MIN_CLOSES = 120
VALUE_SESSIONS = 20            # "most traded": the average matched value of these sessions
WINDOW = ("09:15", "15:15")    # the scan: from the first match to after the close
SUMMARY_AFTER = "14:46"        # the first scan from then pushes the day's list (ATC matched)
COMPARE_AFTER = "15:15"        # the site's per-stock signals of the day are out (~15:08)
COMPARE_GIVE_UP = "16:30"      # from then compared with what is there
FETCH_PAUSE = 0.3
BUDGET_IN_SESSION = 60.0       # seconds of history download a scan may spend in the session
BUDGET_OFF = 150.0             # ... outside it
COMPARE_KEEP = 60              # sessions of records kept
LIST_CAP = 40                  # codes a side in the pushed summary

_lock = threading.Lock()


def _now():
    from tradingagents.market import vn_rules

    return vn_rules.now_vn()


def sessions(through: str, n: int = HISTORY_SESSIONS) -> list[str]:
    """The ``n`` session days up to ``through`` (included when it is one), oldest first."""
    from tradingagents.market import vn_rules

    day = through if vn_rules.is_session_day(through) else vn_rules.prev_session(through)
    out = []
    while len(out) < n:
        out.append(day)
        day = vn_rules.prev_session(day)
    return out[::-1]


def fetch_day(day: str) -> list[dict]:
    from tradingagents.dataflows.vendors import flows

    return flows.fetch_market_day(day)


class MarketCloses:
    """``{"basis": the day the closes are adjusted to, "floors": {code: exchange},
    "days": {day: {code: [close, value]}}, "empty": [sessions the source has nothing for],
    "pre": {"day": the newest session fetched, "factor": {code: raw close / adjusted close
    on that session}}}``. The factor undoes VNDirect's adjustment for an announced ex-date
    that has not come yet, so ``series`` is on the price board's basis (``te.levels`` is
    compared with board prices); it is 1 once the ex-date has passed."""

    def __init__(self, directory: Path):
        self.path = Path(directory) / "external" / "market_closes.json"

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict) or not isinstance(data.get("days"), dict):
            data = {}
        data.setdefault("days", {})
        data.setdefault("floors", {})
        data.setdefault("empty", [])
        if not isinstance(data.get("pre"), dict) or not isinstance(data["pre"].get("factor"), dict):
            data["pre"] = {"day": "", "factor": {}}
        return data

    @staticmethod
    def _note_pre(data: dict, day: str, rows: list[dict]) -> None:
        """Keep ``raw / close`` of the newest session fetched (see the class docstring)."""
        if day < (data["pre"].get("day") or ""):
            return
        factor = {}
        for r in rows:
            raw, close = r.get("raw"), r.get("close")
            if raw and close and abs(raw / close - 1) > te.REBASE_PCT / 100:
                factor[r["code"]] = round(raw / close, 6)
        data["pre"] = {"day": day, "factor": factor}

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(self.path, json.dumps(data, separators=(",", ":")))

    def days(self) -> list[str]:
        return sorted(self._read()["days"])

    def codes_on(self, day: str) -> set[str]:
        """The stocks with a close saved for session ``day``."""
        return set(self._read()["days"].get(day) or {})

    def progress(self, through: str) -> tuple[int, int]:
        """(sessions saved, sessions wanted) of the history up to ``through``."""
        data = self._read()
        wanted = sessions(through)
        return sum(1 for d in wanted if d in data["days"] or d in data["empty"]), len(wanted)

    def update(self, through: str, *, fetch=None, budget: float = BUDGET_OFF, today: str | None = None) -> tuple[int, str]:
        """Download the missing sessions up to ``through`` (newest first) for ``budget``
        seconds at most; first, once a day, the last saved session again, to re-base the
        stocks VNDirect has adjusted since. Returns (sessions fetched, error or "")."""
        fetch = fetch or fetch_day
        today = today or _now().strftime("%Y-%m-%d")
        started = time.monotonic()
        with _lock:
            data = self._read()
        error, fetched = "", 0
        if data["days"] and data.get("basis") != today:
            last = max(data["days"])
            try:
                rows = fetch(last)
            except Exception as exc:  # noqa: BLE001 - nothing is mixed: retried next time
                return 0, f"giá phiên {last[8:10]}/{last[5:7]}: {exc}"
            fresh = {r["code"]: r["close"] for r in rows}
            if not fresh:
                return 0, f"nguồn giá chưa trả phiên {last[8:10]}/{last[5:7]}"
            self._note_pre(data, last, rows)
            for code, (old, _value) in list(data["days"][last].items()):
                new = fresh.get(code)
                if new and old and abs(new / old - 1) > te.REBASE_PCT / 100:
                    ratio = new / old
                    for day_rows in data["days"].values():
                        if code in day_rows:
                            day_rows[code][0] = round(day_rows[code][0] * ratio, 1)
            data["basis"] = today
            time.sleep(FETCH_PAUSE)
        data["basis"] = today
        wanted = sessions(through, HISTORY_SESSIONS + KEEP_EXTRA)
        missing = [d for d in reversed(wanted) if d not in data["days"] and d not in data["empty"]]
        for day in missing:
            if time.monotonic() - started > budget:
                break
            try:
                rows = fetch(day)
            except Exception as exc:  # noqa: BLE001 - what came is kept, the rest next time
                error = f"giá phiên {day[8:10]}/{day[5:7]}: {exc}"
                break
            fetched += 1
            if not rows:
                if day < today:
                    data["empty"].append(day)        # a past session without data: not asked again
                continue
            data["days"][day] = {r["code"]: [r["close"], r["value"]] for r in rows}
            data["floors"].update({r["code"]: r["floor"] for r in rows if r.get("floor")})
            self._note_pre(data, day, rows)
            time.sleep(FETCH_PAUSE)
        keep = set(wanted)
        data["days"] = {d: v for d, v in data["days"].items() if d in keep}
        data["empty"] = sorted(d for d in set(data["empty"]) if d in keep)
        with _lock:
            self._write(data)
        return fetched, error

    def universe(self, size: int, as_of: str) -> list[str]:
        """The ``size`` stocks with the highest average matched value over the last
        ``VALUE_SESSIONS`` sessions up to ``as_of``, among those with ``MIN_CLOSES`` closes."""
        data = self._read()
        days = sorted(d for d in data["days"] if d <= as_of)
        recent = days[-VALUE_SESSIONS:]
        count: dict[str, int] = {}
        for d in days:
            for code in data["days"][d]:
                count[code] = count.get(code, 0) + 1
        value: dict[str, float] = {}
        for d in recent:
            for code, (_close, val) in data["days"][d].items():
                value[code] = value.get(code, 0.0) + (val or 0.0) / max(len(recent), 1)
        ranked = sorted((c for c in value if count.get(c, 0) >= MIN_CLOSES and value[c] > 0), key=lambda c: -value[c])
        return ranked[:size]

    def series(self, codes, as_of: str) -> tuple[dict[str, list[float]], dict[str, str], dict[str, float]]:
        """({code: closes up to ``as_of``, oldest first}, {code: exchange}, {code: average value})."""
        data = self._read()
        days = sorted(d for d in data["days"] if d <= as_of)
        wanted = set(codes)
        pre = data["pre"]["factor"] if data["pre"].get("day", "") <= as_of or not days else {}
        closes = {c: [] for c in wanted}
        for d in days:
            for code, (close, _val) in data["days"][d].items():
                if code in wanted and close:
                    closes[code].append(round(close * pre.get(code, 1.0), 1) if code in pre else close)
        recent = days[-VALUE_SESSIONS:]
        value = {c: sum((data["days"][d].get(c) or [0, 0])[1] or 0 for d in recent) / max(len(recent), 1) for c in wanted}
        return closes, {c: data["floors"].get(c) for c in wanted}, value


def scan(closes: MarketCloses, size: int, now, *, board=None, levels_cache: dict | None = None) -> dict:
    """The scan at ``now`` (a session day, from 09:15; after the close the board's price is
    the close): ``{at, basis, size, rows, skipped}`` (rows: every stock with a price,
    ``{symbol, state, price, level, dist, flip, value}``, most traded first), or ``{error}``
    while the history is not there."""
    from tradingagents.market import vn_rules

    board = board or te.board_prices
    today = now.strftime("%Y-%m-%d")
    if not vn_rules.is_session_day(today) or now.strftime("%H:%M") < WINDOW[0]:
        return {"error": "ngoài giờ giao dịch"}      # the board has no price of today yet
    basis = vn_rules.prev_session(today)            # after the close the board's price is the close
    cache = levels_cache if levels_cache is not None else {}
    if cache.get("basis") != basis or cache.get("size") != size:     # once a session (the file is ~4 MB)
        have, want = closes.progress(basis)
        if have < want:
            return {"error": f"đang tải lịch sử giá toàn thị trường: {have}/{want} phiên"}
        codes = closes.universe(size, basis)
        if len(codes) < min(size, 50):
            return {"error": f"lịch sử giá chưa đủ ({len(codes)} mã)"}
        series, floors, value = closes.series(codes, basis)
        lv = {}
        for code in codes:
            got = te.levels(series[code][-HISTORY_SESSIONS:])
            if got:
                lv[code] = got
        cache.clear()
        cache.update({"basis": basis, "size": size, "codes": codes, "lv": lv, "floors": floors, "value": value})
    codes, lv, floors, value = cache["codes"], cache["lv"], cache["floors"], cache["value"]
    prices_board = board(codes)
    prices = {c: b["price"] for c, b in prices_board.items() if b.get("price")}
    refs = {c: (b.get("ref"), b.get("exchange") or floors.get(c)) for c, b in prices_board.items()}
    states = {c: v["rule"] for c, v in lv.items()}
    rows, skipped = te.assess(codes, states, lv, prices, refs=refs,
                              exchange_of=lambda c: refs.get(c, (None, None))[1] or floors.get(c))
    for r in rows:
        r["value"] = round(value.get(r["symbol"], 0.0))
    rows.sort(key=lambda r: -r["value"])
    return {"at": now.strftime("%Y-%m-%dT%H:%M"), "basis": basis, "size": len(codes), "rows": rows,
            "skipped": len(skipped)}


def site_view(live: dict) -> dict:
    """The site's free view at the scan: ``{side: {total, locked, shown: [{symbol, at, from,
    met}]}}`` (``live``: ``{side: fetch_live_changes(side)}``)."""
    out = {}
    for side, data in (live or {}).items():
        if not isinstance(data, dict):
            continue
        out[side] = {"total": data.get("total"), "locked": data.get("locked"),
                     "shown": [{"symbol": c["symbol"], "at": c["at"], "from": c.get("from"), "met": c.get("met"),
                                "stage": c.get("stage")} for c in data.get("changes") or []]}
    return out


class Snapshot:
    """``data/external/market_now.json``: ``{"last": the last scan (with the site's view
    then), "final": the scan at the close of the last compared session, "summary_day": the
    day the list was pushed}``."""

    def __init__(self, directory: Path):
        self.path = Path(directory) / "external" / "market_now.json"

    def read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(self.path, json.dumps(data, ensure_ascii=False))

    def update(self, **fields) -> dict:
        with _lock:
            data = self.read()
            data.update(fields)
            self.write(data)
        return data


class CompareLog:
    """One record a session: the bot's scan against what the site shows
    (``data/external/market_compare.json``, the last ``COMPARE_KEEP`` sessions)."""

    def __init__(self, directory: Path):
        self.path = Path(directory) / "external" / "market_compare.json"

    def read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def get(self, day: str) -> dict | None:
        return self.read().get(day)

    def add(self, day: str, record: dict) -> None:
        with _lock:
            data = self.read()
            data[day] = record
            data = {d: data[d] for d in sorted(data)[-COMPARE_KEEP:]}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            write_text_atomic(self.path, json.dumps(data, ensure_ascii=False))

    def totals(self, last: int = 20) -> dict:
        """Summed over the last ``last`` records: sessions, matched, extra, missed, states."""
        data = self.read()
        days = sorted(data)[-last:]
        out = {"sessions": len(days), "matched": 0, "extra": 0, "missed": 0, "agree": 0, "states": 0}
        for d in days:
            f = data[d].get("followed") or {}
            out["matched"] += len(f.get("matched") or [])
            out["extra"] += len(f.get("extra") or [])
            out["missed"] += len(f.get("missed") or [])
            out["agree"] += int(f.get("agree") or 0)
            out["states"] += int(f.get("states") or 0)
        return out


def compare(snapshot: dict, followed, site_final: dict, site_states: dict, site_counts: dict) -> dict:
    """The record of a session: the bot's final scan (``snapshot``, at the close price)
    against the site, on the followed stocks the scan covers.

    ``site_final``: {symbol: side} of the site's new signals of the day (per stock);
    ``site_states``: {symbol: side} the site's state after the day; ``site_counts``:
    {side: total} of its market-wide list (None when not known)."""
    rows = {r["symbol"]: r for r in snapshot.get("rows", [])}
    covered = [s for s in followed if s in rows and s in site_states]
    bot = {s: rows[s]["flip"] for s in covered if rows[s].get("flip")}
    site = {s: v for s, v in site_final.items() if s in covered}
    matched = sorted(s for s in bot if site.get(s) == bot[s])
    extra = sorted(s for s in bot if site.get(s) != bot[s])
    missed = sorted(s for s in site if bot.get(s) != site[s])
    agree = sum(1 for s in covered if (bot.get(s) or rows[s]["state"]) == site_states[s])
    counts = {"BUY": 0, "SELL": 0}
    for r in snapshot.get("rows", []):
        if r.get("flip"):
            counts[r["flip"]] += 1
    return {"at": snapshot.get("at"), "size": snapshot.get("size"), "bot": counts,
            "site": {k: site_counts.get(k) for k in ("BUY", "SELL")},
            "followed": {"n": len(covered), "site": site, "matched": matched, "extra": extra, "missed": missed,
                         "agree": agree, "states": len(covered)}}


# ── texts ──
def _codes(rows, followed, cap=LIST_CAP) -> str:
    names = [r["symbol"] + ("*" if r["symbol"] in followed else "") for r in rows]
    return ", ".join(names[:cap]) + (f" … +{len(names) - cap}" if len(names) > cap else "")


def _ddmm(day: str) -> str:
    return f"{day[8:10]}/{day[5:7]}" if len(day) >= 10 else day


def _fmt(value) -> str:
    return f"{value:,.0f}".replace(",", ".") if value is not None else "-"


def _word(side: str) -> str:
    return "MUA" if side == "BUY" else "BÁN"


def site_lines(snap: dict) -> list[str]:
    """What the site shows for free against the bot's scan."""
    site = snap.get("site") or {}
    if not site:
        return []
    rows = {r["symbol"]: r for r in snap.get("rows", [])}
    total = {s: (site.get(s) or {}).get("total") for s in ("BUY", "SELL")}
    lines = ["So với Siêu tín hiệu (bản miễn phí):"]
    if any(v is not None for v in total.values()):
        lines.append(f"• Trang đếm {total.get('BUY') or 0} lần đổi sang MUA, {total.get('SELL') or 0} sang BÁN trên toàn "
                     "thị trường (~1.500 mã và chỉ số, tính cả mã đổi chiều rồi quay lại); chỉ cho xem 2 mã mỗi bên")
    for side in ("BUY", "SELL"):
        for c in (site.get(side) or {}).get("shown") or []:
            sym = c["symbol"]
            r = rows.get(sym)
            if r is None:
                bot = "ngoài các mã bot quét"
            elif r.get("flip") == side:
                bot = "bot cũng báo ✓"
            elif r.get("flip"):
                bot = f"bot báo ngược ({_word(r['flip'])})"
            else:
                bot = f"bot: chưa qua mức (cách {r['dist']:.1f}%)".replace(".", ",")
            stage = c.get("stage") or ""
            if stage == "confirmed":
                when = " (đã chốt)"
            elif stage == "retracted":
                when = f" lúc {c['at'][11:16]}, đóng cửa không giữ"
            else:
                when = f" lúc {c['at'][11:16]}" + (" (trang: giá đã quay lại)" if c.get("met") is False else "")
            lines.append(f"• Trang: {sym} → {_word(side)}{when} · {bot}")
    return lines


def summary_text(snap: dict, followed) -> str:
    """The pushed list of the day (the first scan from ``SUMMARY_AFTER``)."""
    flips = [r for r in snap.get("rows", []) if r.get("flip")]
    buy = [r for r in flips if r["flip"] == "BUY"]
    sell = [r for r in flips if r["flip"] == "SELL"]
    lines = [f"🌐 TOÀN THỊ TRƯỜNG · bot quét {snap['size']} mã · {snap['at'][11:16]} {_ddmm(snap['at'][:10])}",
             "Đổi chiều nếu đóng cửa ở giá này (ATC đã khớp):",
             f"🟢 Sang MUA ({len(buy)}): " + (_codes(buy, followed) or "không có"),
             f"🔴 Sang BÁN ({len(sell)}): " + (_codes(sell, followed) or "không có")]
    if any(r["symbol"] in followed for r in flips):
        lines.append("(* mã anh đang giữ hoặc theo dõi)")
    extra = site_lines(snap)
    if extra:
        lines += [""] + extra
    lines += ["", "Ước tính của bot, xếp theo giá trị giao dịch. Chi tiết: /stvthitruong · đối chiếu với trang sau ~15:15."]
    return "\n".join(lines)


def compare_text(day: str, rec: dict, totals: dict) -> str:
    """The pushed comparison of a session, once the site's signals are out."""
    f = rec.get("followed") or {}
    site_counts = rec.get("site") or {}
    site_new = list((f.get("site") or {}).items())
    lines = [f"🌐 ĐỐI CHIẾU VỚI SIÊU TÍN HIỆU · phiên {_ddmm(day)}",
             f"Bot ({rec.get('size')} mã, giá đóng cửa): {rec['bot']['BUY']} MUA · {rec['bot']['SELL']} BÁN"]
    if site_counts.get("BUY") is not None or site_counts.get("SELL") is not None:
        lines.append(f"Trang (toàn thị trường ~1.500 mã và chỉ số, tính cả mã đổi chiều trong phiên rồi quay lại): "
                     f"{site_counts.get('BUY') or 0} MUA · {site_counts.get('SELL') or 0} BÁN - chỉ xem được 2 mã mỗi bên, "
                     "nên số này chỉ để tham khảo")
    if f.get("n"):
        n_buy = sum(1 for _, v in site_new if v == "BUY")
        lines += ["", f"Trên {f['n']} mã anh theo dõi (trang có tín hiệu từng mã):",
                  f"• trang chốt {n_buy} MUA, {len(site_new) - n_buy} BÁN; bot đúng {len(f['matched'])}/{len(site_new)}"
                  + (f", báo thừa {len(f['extra'])} ({', '.join(f['extra'][:8])})" if f["extra"] else ", không báo thừa")
                  + (f", sót {', '.join(f['missed'][:8])}" if f["missed"] else ""),
                  f"• trạng thái MUA/BÁN của bot khớp trang {f['agree']}/{f['states']} mã"]
    lines.append("")
    lines.append(accuracy_line(totals))
    return "\n".join(lines)


def accuracy_line(totals: dict) -> str:
    if not totals.get("sessions"):
        return "Chưa có phiên đối chiếu nào."
    site_total = totals["matched"] + totals["missed"]
    pct = f" ({totals['matched'] / site_total * 100:.0f}%)" if site_total else ""
    states = (f"; trạng thái khớp {totals['agree'] / totals['states'] * 100:.0f}%" if totals["states"] else "")
    return (f"Cộng dồn {totals['sessions']} phiên: bot đúng {totals['matched']}/{site_total} tín hiệu của trang{pct}, "
            f"báo thừa {totals['extra']}{states}.")
