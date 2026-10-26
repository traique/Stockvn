"""Kỷ luật: the discipline layer of Tài sản - every rule is measured, nothing is an order.

The owner's measured losses (24/10/2026) came from buying against the site's SELL or far
above its signal price, holding through the site's SELL, and one stock weighing too much;
not from the choice of stocks (the closed trades won 8 of 12). This module closes those
leaks around the real-money journal of ``vnbot.taisan``:

- **cut-loss integrity** (``refresh_levels``): a holding without a cut-loss takes the site's
  plan level; a saved level is never lowered silently; an owner's lowering is recorded
  (``flag_lower``) and scored later;
- **open risk** (``risk_summary``): what every cut-loss would cost if hit, in VND and % of the
  assets, a holding without a cut-loss named "unlimited", and an asset floor the owner may
  set (``/tssan``) that only warns and logs - no rule is locked;
- **the pre-trade check** (``check_buy``, ``/tskiemtra``): a buy against the rules (averaging
  down into a loser, the site saying SELL, no cut-loss) is ⛔, a chase or a far stop ⚠️, and
  the size that fits a 2% risk budget is computed; every recorded buy gets the same tags so
  the groups can be compared later (the journal is never refused);
- **exit tickets** (``update_exits``, ``/tsgiu``): each ⛔/🔴 opens a dated ticket with the
  price of hesitation in VND (the report never says "fee" or "tax": prices stay as entered);
  it closes when the owner sells, records a reasoned hold with a
  mandatory cut-loss, or lapses when the site turns BUY again; one reminder at 14:20 on the
  deadline day;
- **the discipline meter** (``rule_replay``, ``discipline``): the assets had every ⛔/🔴 been
  sold the session after, the counts of the last four weeks, early sells, the win rate with
  its 90% confidence interval; computed by code, commented on by the AI, never predicted;
- **the recovery goal** (``goal``, ``/tsmuctieu``): the arithmetic of the owner's target next
  to the measured figures (``MEASURED``), shown weekly, never as a forecast.

Prices stay as the owner entered them (fees in the buy price; no fee or tax deducted).
Comparisons that need "the next session's price" use its close (the bot keeps closes, not
opens) and say so. Everything here reads the journal and the saved closes; the price
source is asked only through ``TaiSan.market`` (fakes in the tests).
"""

from __future__ import annotations

import logging
import math
from datetime import date as _date, timedelta

logger = logging.getLogger(__name__)

# ─── measured figures (one place; change them when a backtest is re-run) ──────
MEASURED = {
    # docs/backtest_2026-10-23/thoat_tai_san.md and docs/backtest_2026-10-24 (39 stocks, 08/2024-10/2026)
    "rule_per_trade": 1.14,      # % per trade, buy at the site's BUY, exit by ⛔/🔴 (R4), 582 trades
    "rule_win": 32,              # % of trades that won under the rule
    "rule_avg_win": 12.7,        # % average winning trade
    "rule_avg_loss": -4.3,       # % average losing trade
    "hold_median": 13,           # sessions, median holding under the rule
    "worst_rule_loss": -16.4,    # % the worst single trade under the rule
    "sim_hit": "2/20",           # start months in which a small account following every BUY reached +38%
    "sim_worst": -50,            # % worst case of the concentrated variant
    "thrust_n": 12, "thrust_up": 7, "thrust_median": 2.4, "thrust_range": (-10.8, 13.5),
    "per_trade_noise": 9,        # ± points one trade deviates from its day's mean (rule exit)
    # VNINDEX 2022-10/2026, every window of closes (no survivorship bias): the share of windows
    # that gained the owner's required +38%, and the best window (docs/backtest_2026-10-24/README.md)
    "vni_windows": {"2 tháng": (1152, 0.0, 23.5), "6 tháng": (1068, 1.0, 56.9), "12 tháng": (942, 9.6, 62.3)},
    "ath_vnindex": (1928, "18/05/2026"),
    "early_better": 48,          # % of cases selling on 🟠 beat waiting for the site's SELL (547 cases)
}
MEASURED_DATE = "24/10/2026"

DEFAULT_GOAL_DATE = "2026-12-24"
RISK_PCT = 2.0            # % of the assets one buy may lose at its cut-loss (TAISAN_RISK_PCT)
OPEN_RISK_CAP_PCT = 6.0   # % of the assets all planned losses together (TAISAN_OPEN_RISK_PCT)
RISK_WARN_PCT, RISK_RED_PCT = 5.0, 8.0
MAX_WEIGHT_NEW_PCT = 30.0  # % of the assets one stock after a buy (TAISAN_MAX_WEIGHT_NEW)
CHASE_PCT = 5.0           # buying this far above the site's signal price is a chase (⚠️)
FAR_STOP_PCT = abs(MEASURED["worst_rule_loss"])   # a cut-loss farther than the rule's worst trade (16.4%)
FLOOR_RATIO = 0.60        # the default asset floor: 60% of the capital
FLOOR_RECOVER = 1.03      # back above the floor by this much ends an "under the floor" episode
NO_STOP_WEIGHT_PCT = 5.0  # a holding at least this heavy without a cut-loss is named
HOLD_SESSIONS = 15        # a rule trade's typical life incl. T+2, for the goal arithmetic
TICKET_REMIND_FROM, TICKET_REMIND_UNTIL = "14:20", "14:45"
WEEKS = 4                 # the discipline meter's window
KEEP_LOG = 600
KEEP_CLOSED = 200
MIN_REASON_WORDS = 3


def _env_float(name: str, default: float) -> float:
    from vnbot.settings import _float

    return _float(name, default)


def risk_pct() -> float:
    return max(0.1, min(10.0, _env_float("TAISAN_RISK_PCT", RISK_PCT)))


def open_risk_cap() -> float:
    return max(1.0, min(30.0, _env_float("TAISAN_OPEN_RISK_PCT", OPEN_RISK_CAP_PCT)))


def max_weight_new() -> float:
    return max(5.0, min(100.0, _env_float("TAISAN_MAX_WEIGHT_NEW", MAX_WEIGHT_NEW_PCT)))


# ─── formatting (the same conventions as taisan.py) ──────────────────────────

def vnd(value) -> str:
    return "-" if value is None else f"{value:,.0f}".replace(",", ".")


def signed(value) -> str:
    return "-" if value is None else f"{value:+,.0f}".replace(",", ".")


def pct(value, digits: int = 1) -> str:
    return "-" if value is None else f"{value:+.{digits}f}%".replace(".", ",")


def pct0(value, digits: int = 1) -> str:
    return "-" if value is None else f"{value:.{digits}f}%".replace(".", ",")


def ddmm(day: str) -> str:
    return f"{day[8:10]}/{day[5:7]}" if len(day) >= 10 else day


def _week(day: str) -> str:
    y, w, _ = _date.fromisoformat(day).isocalendar()
    return f"{y}-W{w:02d}"


def _sessions_until(first: str, last: str) -> int:
    """Session days from ``first`` to ``last`` inclusive (0 when last < first)."""
    from tradingagents.market import vn_rules

    n, day = 0, first
    while day <= last:
        if vn_rules.is_session_day(day):
            n += 1
        day = (_date.fromisoformat(day) + timedelta(days=1)).isoformat()
    return n


def _store(account):
    from vnbot import external

    return external.SignalStore(account.dir.parent)


def _closes_for(account, symbol: str, through: str) -> dict[str, float]:
    """The closes of ``symbol`` as traded (``prices.json`` keeps unadjusted closes, like the
    owner's prices and the site's levels), completed from the price source's unadjusted
    history when the last saved day is before ``through``. Never a mix of the two bases: when
    only the adjusted series can be had, it is used alone (consistent within itself)."""
    saved = dict(account.closes().get(symbol) or {})
    if saved and max(saved) >= through:
        return saved
    start = min(saved) if saved else (_date.fromisoformat(through) - timedelta(days=400)).isoformat()
    try:
        saved.update(account.market.unadjusted_closes(symbol, start, through))
        return saved
    except Exception as exc:  # noqa: BLE001 - the adjusted series alone, then
        logger.warning("Kỷ luật: unadjusted closes of %s: %s", symbol, exc)
    if saved:
        return saved          # incomplete but on the right basis: the rows settle on a later day
    try:
        return account.market.daily_closes(symbol, through)      # nothing saved: the adjusted series alone
    except Exception as exc:  # noqa: BLE001 - then nothing
        logger.warning("Kỷ luật: closes of %s: %s", symbol, exc)
        return {}


def _close_after(series: dict[str, float], day: str, n: int) -> tuple[str, float] | None:
    """(day, close) ``n`` sessions after ``day`` in ``series`` (closed sessions only)."""
    later = sorted(d for d in series if d > day)
    if len(later) < n:
        return None
    d = later[n - 1]
    return d, series[d]


# ─── 1. cut-loss integrity ───────────────────────────────────────────────────

def refresh_levels(account, day: str, site: dict, closes: dict[str, float]) -> list[str]:
    """Give every holding without a cut-loss the site's plan level (when the site says BUY
    and the level is under the close); note the site's current level next to a saved site
    level without ever lowering or raising it. Returns the lines for the report."""
    book = account.book()
    levels = account.levels()
    lines, changes, flags = [], {}, []
    for symbol in sorted(book.holdings):
        lv = dict(levels.get(symbol) or {})
        sig = site.get(symbol)
        close = closes.get(symbol)
        if not sig or sig.get("side") != "BUY":
            continue
        sl, tp1 = sig.get("sl"), sig.get("tp1")
        if not (isinstance(sl, (int, float)) and math.isfinite(sl) and sl > 0):
            continue
        if not lv.get("stop"):
            if close and sl >= close:
                continue
            level = {"stop": float(sl), "target": float(tp1) if tp1 and close and tp1 > close else None,
                     "source": "site", "day": day, "plan_day": sig.get("day", "")}
            changes[symbol] = level
            lines.append(f"📌 {symbol}: lấy mức cắt lỗ {vnd(sl)} theo kế hoạch MUA {ddmm(sig.get('day', ''))} của trang "
                         f"(anh đổi: /tscatlo {symbol} GIÁ)")
            continue
        if lv.get("source") != "site" or float(sl) == float(lv["stop"]):
            if lv.get("site_now") is not None and float(sl) == float(lv["stop"]):
                lv.pop("site_now", None)
                changes[symbol] = lv
            continue
        lv["site_now"] = float(sl)                     # shown beside the saved level; never adopted
        changes[symbol] = lv
        if float(sl) > float(lv["stop"]):
            flags.append({"kind": "site_higher", "day": day, "symbol": symbol, "old": float(lv["stop"]), "new": float(sl),
                          "price": close})
    if changes or flags:
        def change(s):
            s["levels"].update(changes)
            seen = {(f.get("kind"), f.get("symbol"), f.get("new")) for f in s["flags"]}
            s["flags"].extend(f for f in flags if (f["kind"], f["symbol"], f["new"]) not in seen)
        account._update_state(change)
    return lines


def flag_lower(account, symbol: str, old: float, new: float, qty: int, price: float | None) -> str:
    """Record an owner's lowering of a cut-loss and say what it costs (scored later)."""
    account._update_state(lambda s: s["flags"].append(
        {"kind": "lower", "day": account.today(), "symbol": symbol, "old": float(old), "new": float(new), "price": price}))
    if not price:
        return f"⚠️ Anh hạ cắt lỗ {symbol} từ {vnd(old)} xuống {vnd(new)}. Bot ghi lại và chấm sau 5/10/20 phiên."
    total = account.summary()["total"] or 1
    before, after = qty * max(price - old, 0), qty * max(price - new, 0)
    text = (f"⚠️ Anh hạ cắt lỗ {symbol} từ {vnd(old)} xuống {vnd(new)}: lỗ nếu chạm tăng từ {vnd(before)} "
            f"({pct0(before / total * 100)} tài sản) lên {vnd(after)} ({pct0(after / total * 100)}). "
            "Bot ghi lại và chấm sau 5/10/20 phiên.")
    if (price - new) / price * 100 > FAR_STOP_PCT:
        text += f" · xa hơn lệnh lỗ nặng nhất của quy tắc ({pct(MEASURED['worst_rule_loss'])})"
    return text


# ─── 2. open risk and the asset floor ────────────────────────────────────────

def risk_summary(account, summary: dict, levels: dict | None = None) -> dict:
    """What every cut-loss would cost if hit: per holding, in total, in % of the assets; the
    holdings without a cut-loss ("unlimited"); the level 🟢/🟡/🔴; the floor and the distance
    to it. Only arithmetic on the saved levels and the summary's prices."""
    levels = account.levels() if levels is None else levels
    total = summary["total"] or 0.0
    capital = summary["capital"] or 0.0
    guard = account.state().get("guard") or {}
    rows, planned, unlimited = [], 0.0, []
    for p in summary["positions"]:
        lv = levels.get(p["symbol"]) or {}
        stop = float(lv.get("stop") or 0) or None
        price = p["price"] or p["avg"]
        if stop:
            loss = p["qty"] * max(price - stop, 0)
            planned += loss
            rows.append({"symbol": p["symbol"], "stop": stop, "source": lv.get("source"), "loss": loss,
                         "loss_pct": loss / total * 100 if total else 0.0, "waiting": bool(price <= stop),
                         "weight": p["weight"], "value": p["value"] or p["cost"]})
        else:
            unlimited.append(p["symbol"])
            rows.append({"symbol": p["symbol"], "stop": None, "source": None, "loss": None, "loss_pct": None,
                         "waiting": False, "weight": p["weight"], "value": p["value"] or p["cost"]})
    pct_total = planned / total * 100 if total else 0.0
    heavy_unlimited = [r["symbol"] for r in rows if r["stop"] is None and r["weight"] >= NO_STOP_WEIGHT_PCT]
    level = "🔴" if pct_total > RISK_RED_PCT or heavy_unlimited else "🟡" if pct_total > RISK_WARN_PCT else "🟢"
    floor_ratio = float(guard.get("floor_ratio") or FLOOR_RATIO)
    floor = floor_ratio * capital
    nav_after = total - planned
    return {"rows": rows, "planned": planned, "pct": pct_total, "level": level, "unlimited": unlimited,
            "heavy_unlimited": heavy_unlimited, "nav_after": nav_after,
            "req_after": (capital / nav_after - 1) * 100 if nav_after > 0 and capital else None,
            "floor": floor, "floor_ratio": floor_ratio, "floor_reason": guard.get("reason") or "",
            "floor_day": guard.get("day") or "", "distance": (total / floor - 1) * 100 if floor > 0 else None,
            "below": bool(floor and total < floor), "cap": open_risk_cap(), "total": total, "capital": capital}


def risk_line(r: dict) -> str:
    text = f"Rủi ro đang mở: {vnd(r['planned'])} ({pct0(r['pct'])} tài sản) {r['level']} · trần {r['cap']:g}%"
    if r["unlimited"]:
        text += " · " + ", ".join(r["unlimited"]) + " chưa có cắt lỗ → không giới hạn"
    return text


def risk_lines(r: dict) -> list[str]:
    """The 🛡️ block of the evening report."""
    if not r["rows"]:
        return []
    lines = ["", "🛡️ RỦI RO KẾ HOẠCH"]
    for row in r["rows"]:
        if row["stop"] is None:
            lines.append(f"{row['symbol']}: 🔴 chưa có cắt lỗ → rủi ro không giới hạn ({vnd(row['value'])} = "
                         f"{pct0(row['weight'])} tài sản) · đặt: /tscatlo {row['symbol']} GIÁ")
        elif row["waiting"]:
            lines.append(f"{row['symbol']}: đóng cửa ≤ cắt lỗ {vnd(row['stop'])}, đang chờ bán")
        else:
            who = "anh đặt" if row["source"] == "owner" else "trang"
            lines.append(f"{row['symbol']}: cắt lỗ {vnd(row['stop'])} ({who}) · nếu chạm: −{vnd(row['loss'])} "
                         f"({pct0(row['loss_pct'])} tài sản)")
    tail = f"Tổng nếu mọi cắt lỗ chạm: −{vnd(r['planned'])} ({pct0(r['pct'])}) → tài sản còn {vnd(r['nav_after'])}"
    if r["req_after"] is not None and r["req_after"] > 0:
        tail += f", khi đó cần {pct(r['req_after'])} để hoàn vốn"
    lines.append(tail)
    floor_who = (f"anh đặt {ddmm(r['floor_day'])}: {r['floor_reason']}" if r["floor_reason"]
                 else f"{r['floor_ratio'] * 100:.0f}% vốn gốc")
    lines.append(f"Sàn: {vnd(r['floor'])} ({floor_who}) · cách sàn {pct(r['distance'])} · đổi: /tssan SỐ lý do")
    return lines


def set_floor(account, amount: float, reason: str) -> str:
    """``/tssan SỐ lý do``: the floor the owner wants to be told about (ratio of the capital)."""
    s = account.summary()
    capital, total = s["capital"], s["total"]
    if not capital:
        raise ValueError("Chưa có vốn gốc trong sổ (nạp tiền bằng /tsnap trước).")
    if not (0.3 * capital <= amount < total):
        raise ValueError(f"Sàn phải từ 30% vốn gốc ({vnd(0.3 * capital)}) đến dưới tài sản hiện tại ({vnd(total)}).")
    ratio = amount / capital
    account._update_state(lambda st: st["guard"].update(
        {"floor_ratio": ratio, "reason": reason.strip()[:120], "day": account.today(), "below_since": None}))
    why = f", lý do: '{reason.strip()[:120]}'" if reason.strip() else ""
    return (f"✅ Sàn {vnd(amount)} ({ratio * 100:.0f}% vốn gốc){why}. Bot chỉ ghi và nhắc khi tài sản 15:30 xuống dưới sàn; "
            "không khoá lệnh nào (quy tắc sàn chưa được đo).")


def floor_text(account) -> str:
    r = risk_summary(account, account.summary())
    who = (f"anh đặt {ddmm(r['floor_day'])}: {r['floor_reason']}" if r["floor_reason"]
           else f"mặc định {r['floor_ratio'] * 100:.0f}% vốn gốc")
    return (f"Sàn tài sản: {vnd(r['floor'])} ({who}) · tài sản {vnd(r['total'])}, cách sàn {pct(r['distance'])}. "
            "Đổi: /tssan 6000000 lý do. Bot chỉ nhắc, không khoá lệnh.")


def floor_check(account, day: str, r: dict) -> list[str]:
    """Log the day's NAV against the floor; the warning once per episode under the floor."""
    lines = []

    def change(s):
        g = s["guard"]
        log = g.setdefault("log", [])
        if not any(x.get("day") == day for x in log):
            log.append({"day": day, "nav": round(r["total"]), "floor": round(r["floor"]), "open_risk_pct": round(r["pct"], 2),
                        "below": r["below"]})
            del log[:-KEEP_LOG]
        if r["below"] and not g.get("below_since"):
            g["below_since"] = day
            lines.append(f"🛡️ Tài sản {vnd(r['total'])} đã xuống dưới sàn {vnd(r['floor'])} "
                         f"({r['floor_ratio'] * 100:.0f}% vốn gốc). Bot không khoá gì (quy tắc sàn chưa đo), chỉ ghi lại. "
                         "Theo số đo đã có: chỉ bán theo ⛔/🔴, không mua mới, không margin, không bình quân giá xuống. "
                         "Đổi sàn: /tssan SỐ lý do.")
        elif g.get("below_since") and r["floor"] and r["total"] >= r["floor"] * FLOOR_RECOVER:
            g["below_since"] = None
    account._update_state(change)
    return lines


# ─── 3. the pre-trade check ──────────────────────────────────────────────────

def _lot(qty: int) -> tuple[int, str]:
    if qty >= 100:
        return qty // 100 * 100, ""
    return qty, " · lô lẻ: khó khớp, xem phí tối thiểu của CTCK"


def regime(account, day: str) -> str:
    """VNINDEX against its 200-session average from the saved closes ("không rõ" without 200)."""
    series = account.closes().get("VNINDEX") or {}
    days = sorted(d for d in series if d <= day)
    if len(days) < 200:
        return "không rõ"
    ma = sum(series[d] for d in days[-200:]) / 200
    return "trên TB200" if series[days[-1]] > ma else "dưới TB200"


def breadth(account, day: str, store=None) -> float | None:
    """% of the followed stocks (the site's watchlist and the holdings) the site says BUY."""
    from vnbot import external

    store = store or _store(account)
    symbols = list(dict.fromkeys(list(account.book().holdings) + external.Watchlist(account.dir.parent).get()))
    states = [st for s in symbols if (st := store.state(s, day))]
    return round(100 * sum(1 for st in states if st["side"] == "BUY") / len(states), 1) if states else None


def check_buy(account, symbol: str, price: float | None = None, qty: int | None = None, stop: float | None = None,
              day: str | None = None, *, site: dict | None = None, prices: dict | None = None, book=None,
              store=None) -> dict:
    """Whether a buy of ``symbol`` at ``price`` fits the rules and the risk budget. Pure
    arithmetic on the journal, the saved closes and the site's saved state (``site``: the
    signal to use, else the store's); no AI, nothing recorded here. ``book``: the portfolio
    the buy is judged against (before the buy)."""
    symbol = symbol.upper()
    day = day or account.today()
    book = book or account.book()
    store = store or _store(account)
    if site is None:
        site = store.state(symbol, day)
        listed = store.listed_state(symbol, day)
        if listed is not None and (site is None or str(listed.get("day")) > site["day"]):
            site = listed
    if prices is None:
        prices, _ = account.current_prices(sorted(set(book.holdings) | {symbol}))
    price = float(price) if price else prices.get(symbol)
    s = account.summary(prices={k: v for k, v in prices.items() if v}, book=book)
    nav, cash = s["total"], book.cash
    levels = account.levels()
    risk = risk_summary(account, s, levels)
    held = book.holdings.get(symbol)
    flags, lines = [], []
    sig_price = site.get("price") if site else None
    if stop is None and site and site.get("side") == "BUY" and site.get("sl") and price and float(site["sl"]) < price:
        stop = float(site["sl"])
    head = f"🔎 KIỂM TRA MUA {symbol}" + (f" @ {vnd(price)}" if price else "") + f" ({ddmm(day)})"
    if site and site.get("side") == "BUY":
        dist = (price / sig_price - 1) * 100 if price and sig_price else None
        state = (f"Trang: MUA từ {ddmm(site.get('day', ''))} @ {vnd(sig_price)}"
                 + (f" (giá anh định mua {pct(dist)} so với tín hiệu)" if dist is not None else "")
                 + (f" · cắt lỗ của trang {vnd(site['sl'])} ({pct((site['sl'] / price - 1) * 100)})"
                    if site.get("sl") and price else ""))
    elif site:
        state = f"Trang: BÁN từ {ddmm(site.get('day', ''))} @ {vnd(sig_price)}"
    else:
        state = "Trang: chưa có tín hiệu cho mã này"
    if not price:
        line = f"⛔ Chưa có giá của mã này: ghi giá, ví dụ /tskiemtra {symbol} 25000 100"
        return {"verdict": "block", "flags": ["no_price"], "lines": [line], "text": "\n".join([head, state, line]),
                "max_qty": 0, "tags": {"verdict": "block", "flags": ["no_price"]}}
    # (1) averaging down
    avg_down = bool(held and held.qty > 0 and price < held.avg)
    if avg_down:
        flags.append("avg_down")
        lines.append(f"⛔ Mua thêm mã đang lỗ ({pct((price / held.avg - 1) * 100)}) = bình quân giá xuống. "
                     "Bot không khuyến nghị.")
    # (2) against the site, no cut-loss
    if site and site.get("side") == "SELL":
        flags.append("site_sell")
        lines.append(f"⛔ Trang đang BÁN {symbol} từ {ddmm(site.get('day', ''))}: mua ngược tín hiệu.")
    if not stop or stop >= price:
        flags.append("no_stop")
        lines.append(f"⛔ Chưa có mức cắt lỗ dưới giá mua: rủi ro không giới hạn. Kiểm tra với mức của anh: "
                     f"/tskiemtra {symbol} {vnd(price).replace('.', '')} {qty or 100} CẮTLỖ")
    # (3) under the floor
    guard = account.state().get("guard") or {}
    if guard.get("below_since"):
        flags.append("below_floor")
        lines.append(f"⚠️ Tài sản đang dưới sàn {vnd(risk['floor'])} từ {ddmm(guard['below_since'])}")
    # (4) size
    budget_pct = risk_pct()
    r_budget = budget_pct / 100 * nav
    headroom = max(0.0, risk["cap"] / 100 * nav - risk["planned"])
    budget = min(r_budget, headroom)
    _, danger = account.thresholds()
    weight_cap = min(max_weight_new(), danger)
    held_value = (held.qty * price) if held else 0.0
    q_risk = int(budget // (price - stop)) if stop and stop < price else None
    q_weight = int(max(0.0, weight_cap / 100 * nav - held_value) // price) if nav > 0 else 0
    q_cash = int(max(0.0, cash) // price)
    max_qty = min(x for x in (q_risk, q_weight, q_cash) if x is not None)
    max_qty, lot_note = _lot(max(max_qty, 0))
    this_risk = (qty or max_qty) * (price - stop) if stop and stop < price else None
    if max_qty <= 0 and "no_stop" not in flags:
        why = ("không đủ tiền mặt" if q_cash <= 0
               else f"rủi ro đang mở {pct0(risk['pct'])}/trần {risk['cap']:g}%" if headroom <= 0
               else f"tỷ trọng tối đa {weight_cap:g}% một mã")
        flags.append("no_budget")
        lines.append(f"⛔ Không còn ngân sách: {why}.")
    elif "no_stop" not in flags:
        value, add = max_qty * price, max_qty * (price - stop)
        share = (risk["planned"] + add) / nav * 100 if nav else 0
        lines.append(f"Ngân sách rủi ro: {budget_pct:g}% tài sản = {vnd(r_budget)} → tối đa {vnd(max_qty)} cp "
                     f"({vnd(value)} = {pct0(value / nav * 100 if nav else 0)} tài sản){lot_note}"
                     f" · rủi ro đang mở {vnd(risk['planned'])} ({pct0(risk['pct'])}) + lệnh này {vnd(add)} = {pct0(share)}"
                     f" / trần {risk['cap']:g}%")
    if risk["unlimited"]:
        lines.append(f"⚠️ {', '.join(risk['unlimited'])} chưa có cắt lỗ: rủi ro đang mở chưa tính đủ")
    # (5) warnings
    dist_signal = (price / sig_price - 1) * 100 if sig_price and site and site.get("side") == "BUY" else None
    if dist_signal is not None and dist_signal > CHASE_PCT:
        flags.append("chase")
        lines.append(f"⚠️ Giá cao hơn giá tín hiệu {pct(dist_signal)} (>{CHASE_PCT:g}%): mua đuổi - chỉ cảnh báo và ghi để đo "
                     "(backtest 24/10: mua cao trong kênh không kém hơn)")
    dist_stop = (price - stop) / price * 100 if stop and stop < price else None
    if dist_stop is not None and dist_stop > FAR_STOP_PCT:
        flags.append("far_stop")
        lines.append(f"⚠️ Cắt lỗ cách giá {pct0(dist_stop)}: xa hơn lệnh lỗ nặng nhất của quy tắc "
                     f"({pct(MEASURED['worst_rule_loss'])})")
    if qty and max_qty and qty > max_qty and stop and stop < price:
        flags.append("over_size")
        lines.append(f"⚠️ KL {vnd(qty)} > tối đa theo ngân sách {vnd(max_qty)}: lỗ nếu chạm cắt lỗ {vnd(qty * (price - stop))} "
                     f"= {pct0(qty * (price - stop) / nav * 100 if nav else 0)} tài sản")
    blocks = [f for f in flags if f in ("avg_down", "site_sell", "no_stop", "no_budget")]
    verdict = "block" if blocks else "warn" if flags else "ok"
    if verdict == "block":
        verdict_line = f"⛔ NGOÀI QUY TẮC ({len(blocks)} lý do)"
    elif verdict == "warn":
        verdict_line = f"⚠️ TRONG NGÂN SÁCH, CÓ LƯU Ý: tối đa {vnd(max_qty)} cp"
    else:
        verdict_line = (f"✅ TRONG NGÂN SÁCH RỦI RO: tối đa {vnd(max_qty)} cp ({vnd(max_qty * price)} = "
                        f"{pct0(max_qty * price / nav * 100 if nav else 0)} tài sản)")
    footer = ("Bot không khuyên mua; chỉ kiểm tra lệnh có nằm trong ngân sách rủi ro không. Quy tắc ⛔/🔴 trên tín hiệu MUA "
              f"của trang đo được {pct(MEASURED['rule_per_trade'], 2)}/lệnh, thắng {MEASURED['rule_win']}%, lỗ TB "
              f"{pct(MEASURED['rule_avg_loss'])} (582 lệnh 2024–26). Bot không đặt lệnh.")
    weight_after = ((held_value + (qty or 0) * price) / (nav + 0) * 100) if nav else None
    tags = {"site_state": site.get("side") if site else None, "signal_day": site.get("day") if site else None,
            "signal_price": sig_price, "dist_signal": round(dist_signal, 2) if dist_signal is not None else None,
            "stop": stop, "dist_stop": round(dist_stop, 2) if dist_stop is not None else None, "avg_down": avg_down,
            "risk_pct": round(this_risk / nav * 100, 2) if this_risk is not None and nav else None,
            "weight_after": round(weight_after, 1) if weight_after is not None else None,
            "open_risk_pct": round(risk["pct"], 2), "verdict": verdict, "flags": flags, "regime": regime(account, day),
            "breadth": breadth(account, day, store)}
    return {"verdict": verdict, "flags": flags, "lines": lines, "max_qty": max_qty, "risk_vnd": this_risk,
            "weight_after": weight_after, "open_risk_pct": risk["pct"], "tags": tags, "price": price, "stop": stop,
            "text": "\n".join([head, state, *lines, verdict_line, footer])}


def rule_tag_line(check: dict) -> str:
    """The one 📏 line a recorded buy gets."""
    names = {"avg_down": "⛔ bình quân giá xuống", "site_sell": "⛔ mua ngược tín hiệu BÁN", "no_stop": "⛔ chưa có cắt lỗ",
             "no_budget": "⛔ hết ngân sách rủi ro", "chase": "⚠️ cao hơn giá tín hiệu", "far_stop": "⚠️ cắt lỗ xa",
             "over_size": "⚠️ quá khối lượng ngân sách", "below_floor": "⚠️ dưới sàn tài sản"}
    parts = [names[f] for f in check["flags"] if f in names]
    if check.get("tags", {}).get("dist_signal") is not None and "chase" in check["flags"]:
        parts = [p + (f" ({pct(check['tags']['dist_signal'])})" if p.startswith("⚠️ cao") else "") for p in parts]
    if check.get("tags", {}).get("signal_day") and "site_sell" in check["flags"]:
        parts = [p + (f" (trang BÁN từ {ddmm(check['tags']['signal_day'])})" if p.startswith("⛔ mua ngược") else "")
                 for p in parts]
    if parts:
        return "📏 Quy tắc: " + " · ".join(parts)
    risk_text = (f"lỗ nếu chạm cắt lỗ {vnd(check['risk_vnd'])} = {pct0(check['tags']['risk_pct'])} tài sản"
                 if check.get("risk_vnd") is not None and check["tags"].get("risk_pct") is not None else "")
    return "📏 Quy tắc: trong ngân sách rủi ro" + (f" ({risk_text})" if risk_text else "")


def record_check(account, check: dict, symbol: str, qty) -> None:
    account._update_state(lambda s: (s["checks"].append(
        {"day": account.today(), "at": account.now().strftime("%H:%M"), "symbol": symbol.upper(), "price": check.get("price"),
         "qty": qty, "verdict": check["verdict"], "flags": check["flags"], "max_qty": check["max_qty"], "tags": check["tags"]}),
        s["checks"].__delitem__(slice(0, -300))))


def recently_checked(account, symbol: str, day: str) -> bool:
    from tradingagents.market import vn_rules

    since = vn_rules.prev_session(vn_rules.prev_session(day)) if vn_rules.is_session_day(day) else day
    return any(c.get("symbol") == symbol.upper() and since <= c.get("day", "") <= day
               for c in account.state().get("checks") or [])


# ─── 4. exit tickets ─────────────────────────────────────────────────────────

def _final(sig: dict) -> bool:
    from vnbot import external

    return external.SellAlerts.final(sig)


def sell_deadline(holding, day: str) -> str:
    """The first session after ``day`` on which some of the shares can be sold (T+2: a lot
    bought on T sells from the afternoon of T+2) - the ticket's deadline."""
    from tradingagents.market import vn_rules

    nxt = vn_rules.add_sessions(day, 1)
    for _ in range(6):
        if holding.sellable(nxt, afternoon=True) > 0:
            return nxt
        nxt = vn_rules.add_sessions(nxt, 1)
    return nxt


def sell_window(holding, deadline: str) -> tuple[str, str]:
    """("BÁN phiên dd/mm, ưu tiên ATO" | "..., từ phiên chiều (T+2)", "N cp bán được từ sáng|chiều")."""
    morning, afternoon = holding.sellable(deadline, afternoon=False), holding.sellable(deadline, afternoon=True)
    if morning:
        return f"BÁN phiên {ddmm(deadline)}, ưu tiên ATO", f"{vnd(morning)} cp bán được từ sáng"
    return f"BÁN phiên {ddmm(deadline)}, từ phiên chiều (T+2)", f"{vnd(afternoon)} cp bán được từ chiều"


def update_exits(account, day: str, summary: dict, closes: dict[str, float], site: dict) -> None:
    """Open a ticket for every ⛔ (close under the cut-loss) and 🔴 (the site's final SELL)
    on a holding; mark lapsed the tickets whose stock the site turned BUY again; fill the
    close of the session after the alert. Called from the evening report only."""
    book = account.book()
    levels = account.levels()
    saved = account.closes()
    opened_alerts = []

    def change(s):
        exits = s["exits"]
        for symbol, h in book.holdings.items():
            lv = levels.get(symbol) or {}
            stop = float(lv.get("stop") or 0) or None
            close = closes.get(symbol)
            sig = site.get(symbol)
            ticket = exits.get(symbol)
            if ticket and ticket.get("status") in ("open", "held"):
                if sig and sig.get("side") == "BUY" and _final(sig) and sig.get("day", "") > ticket["opened"]:
                    ticket.update(status="lapsed", closed_day=day)
                    s["exits_log"].append(ticket)
                    del s["exits_log"][:-KEEP_CLOSED]
                    exits.pop(symbol, None)
                    ticket = None
                elif ticket.get("ref_next") is None:
                    nxt = _close_after(saved.get(symbol) or {}, ticket["opened"], 1)
                    if nxt:
                        ticket["ref_next"], ticket["ref_next_day"] = nxt[1], nxt[0]
            level = None
            if close and stop and close <= stop:
                level = "stop"
            if level is None and sig and sig.get("side") == "SELL" and _final(sig):
                level = "sell"
            if level is None:
                continue
            if ticket and ticket.get("status") == "open":
                continue                                        # one ticket a stock while it is open
            if ticket and ticket.get("status") == "held":
                # a reasoned hold stands until a ⛔ on the cut-loss saved NOW (the one /tsgiu set, or the owner's
                # later /tscatlo); the site's SELL it was held against does not reopen it
                if level != "stop":
                    continue
                ticket.update(status="replaced", closed_day=day)
                s["exits_log"].append(ticket)
                del s["exits_log"][:-KEEP_CLOSED]
            exits[symbol] = {"symbol": symbol, "level": level, "opened": day,
                             "signal_day": sig.get("day") if sig and level == "sell" else None,
                             "signal_price": sig.get("price") if sig and level == "sell" else None, "stop": stop,
                             "ref_price": close, "ref_next": None, "ref_next_day": None,
                             "deadline": sell_deadline(h, day), "status": "open", "reason": "", "owner_stop": None,
                             "reminded": None, "sold_day": None, "closed_day": None, "qty": h.qty, "sold_qty": 0}
            if level == "stop":
                opened_alerts.append((symbol, close))
    account._update_state(change)
    for symbol, close in opened_alerts:
        try:
            account.record_alert("stop", symbol, close, day, at="15:30", hook=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Kỷ luật: alert of %s not kept: %s", symbol, exc)


def close_exits_on_sell(account, symbol: str, day: str, qty: int, left: int) -> None:
    """A recorded sale closes the stock's ticket (fully sold) or notes the part sold."""
    def change(s):
        ticket = s["exits"].get(symbol)
        if not ticket or ticket.get("status") not in ("open", "held"):
            return
        ticket["sold_qty"] = int(ticket.get("sold_qty") or 0) + int(qty)
        if left <= 0:
            ticket.update(status="sold", sold_day=day, closed_day=day)
            s["exits_log"].append(ticket)
            del s["exits_log"][:-KEEP_CLOSED]
            s["exits"].pop(symbol, None)
    account._update_state(change)


def reopen_on_void(account, entry) -> None:
    """``/tshuy`` of a sale: the shares are held again, so the ticket that sale closed (status
    "sold" on that day) returns to the open tickets, or the part it sold is given back."""
    if getattr(entry, "kind", "") != "SELL":
        return

    def change(s):
        t = s["exits"].get(entry.symbol)
        if t and t.get("status") in ("open", "held"):
            t["sold_qty"] = max(0, int(t.get("sold_qty") or 0) - int(entry.qty))
            return
        for i in range(len(s["exits_log"]) - 1, -1, -1):
            x = s["exits_log"][i]
            if x.get("symbol") == entry.symbol and x.get("status") == "sold" and x.get("sold_day") == entry.day:
                x.update(status="held" if x.get("reason") else "open", sold_day=None, closed_day=None,
                         sold_qty=max(0, int(x.get("sold_qty") or 0) - int(entry.qty)))
                s["exits"][entry.symbol] = s["exits_log"].pop(i)
                return
    account._update_state(change)


def hold_override(account, symbol: str, stop: float, reason: str) -> str:
    """``/tsgiu MÃ CẮTLỖ lý do``: the owner keeps a stock the rule says to sell; a cut-loss
    and a reason are mandatory; recorded and scored at 5/10/20 sessions."""
    symbol = symbol.upper()
    ticket = (account.state().get("exits") or {}).get(symbol)
    if not ticket or ticket.get("status") != "open":
        raise ValueError(f"{symbol} không có việc treo ⛔/🔴 nào đang mở.")
    prices, _ = account.current_prices([symbol])
    price = prices.get(symbol)
    if not (isinstance(stop, (int, float)) and math.isfinite(stop) and stop > 0):
        raise ValueError("Cắt lỗ phải là một mức giá (đồng).")
    if price and stop >= price:
        raise ValueError(f"Cắt lỗ phải thấp hơn giá hiện tại {vnd(price)}.")
    words = [w for w in reason.split() if w]
    if len(words) < MIN_REASON_WORDS:
        raise ValueError(f"Cần lý do ít nhất {MIN_REASON_WORDS} từ, ví dụ: /tsgiu {symbol} {vnd(stop).replace('.', '')} "
                         "chờ kết quả quý 3")
    reason = " ".join(words)[:200]
    target = (account.levels().get(symbol) or {}).get("target")
    account.set_levels(symbol, float(stop), float(target) if target and float(target) > stop else None, source="owner",
                       record_lower=False)         # the override row below records and scores this one decision
    day = account.today()

    def change(s):
        t = s["exits"].get(symbol)
        if t:
            t.update(status="held", reason=reason, owner_stop=float(stop), held_day=day)
        s["overrides"].append({"day": day, "symbol": symbol, "level": ticket["level"], "price": price, "stop": float(stop),
                               "reason": reason, "ticket_opened": ticket["opened"]})
        del s["overrides"][:-KEEP_CLOSED]
    account._update_state(change)
    what = (f"BÁN (🔴 từ {ddmm(ticket.get('signal_day') or ticket['opened'])})" if ticket["level"] == "sell"
            else f"BÁN (⛔ thủng cắt lỗ {ddmm(ticket['opened'])})")
    return (f"📝 Đã ghi: anh giữ {symbol} dù quy tắc báo {what}; cắt lỗ anh đặt {vnd(stop)}"
            + (f" ({pct((stop / price - 1) * 100)} so với giá nay)" if price else "")
            + f". Bot sẽ báo ⛔ khi đóng cửa ≤ {vnd(stop)}, chấm kết quả sau 5/10/20 phiên và tính vào tổng kết thứ Sáu; "
            "không nhắc lại hằng ngày. Thước kỷ luật thứ Sáu tính tài sản sẽ ra sao nếu bán theo ⛔/🔴.")


def hesitation_cost(ticket: dict, price_now: float | None) -> float | None:
    """qty × (price now − the close after the alert): negative = selling on the alert was better."""
    if not price_now or ticket.get("ref_next") is None:
        return None
    qty = int(ticket.get("qty") or 0) - int(ticket.get("sold_qty") or 0)
    return qty * (price_now - float(ticket["ref_next"]))


def ticket_lines(account, day: str, prices: dict[str, float], closes_vn: dict | None = None) -> tuple[list[str], set[str]]:
    """The ⏳ block of the evening report and the symbols it covers (their plain ⛔/🔴 line
    is not printed twice)."""
    from tradingagents.market import vn_rules

    exits = account.state().get("exits") or {}
    book = account.book()
    lines, covered = [], set()
    nxt = vn_rules.add_sessions(day, 1)
    for symbol, t in sorted(exits.items()):
        h = book.holdings.get(symbol)
        if not h:
            continue
        if t.get("status") == "held":
            covered.add(symbol)
            lines.append(f"📝 {symbol}: giữ có lý do từ {ddmm(t.get('held_day') or t['opened'])} ('{t.get('reason', '')}'), "
                         f"cắt lỗ anh đặt {vnd(t.get('owner_stop'))}; bot báo ⛔ khi đóng cửa ≤ mức này.")
            continue
        if t.get("status") != "open":
            continue
        covered.add(symbol)
        price = prices.get(symbol)
        n = _sessions_until(t["opened"], day) - 1
        head = (f"🔴 {symbol}: trang BÁN từ {ddmm(t.get('signal_day') or t['opened'])} @ {vnd(t.get('signal_price'))}"
                if t["level"] == "sell" else f"⛔ {symbol}: thủng cắt lỗ {vnd(t.get('stop'))} ngày {ddmm(t['opened'])}")
        head += f" · treo {n} phiên"
        cost = hesitation_cost(t, price)
        if price and t.get("ref_next"):
            qty = int(t.get("qty") or 0) - int(t.get("sold_qty") or 0)
            head += (f" · giá nay {vnd(price)} ({pct((price / t['ref_next'] - 1) * 100)} từ phiên sau cảnh báo = "
                     f"{signed(cost)} trên {vnd(qty)} cp)")
        elif price:
            head += f" · giá nay {vnd(price)}"
        deadline = t.get("deadline") or nxt
        when, can = sell_window(h, max(deadline, nxt))
        late = f"hạn {ddmm(deadline)} đã qua → " if deadline < nxt else ""
        lines += [head, f"→ Theo quy tắc: {late}{when} ({can}).",
                  f"→ Nếu anh cố ý giữ: /tsgiu {symbol} GIÁ_CẮT_LỖ lý do… (bot ghi lại và chấm sau 5/10/20 phiên)."]
    if lines:
        lines.append("(số âm = bán theo cảnh báo thì hơn; giá phiên sau là giá đóng cửa, gần đúng; theo đúng giá anh nhập)")
    return lines, covered


def ticket_reminders(account, day: str, now) -> list[tuple[str, str]]:
    """One reminder a ticket, on its deadline day between 14:20 and 14:45, when no sale of
    the stock is in the journal since the alert: (key, text) for the intraday monitor."""
    from vnbot import taisan as ts

    hhmm = now.strftime("%H:%M")
    if not (TICKET_REMIND_FROM <= hhmm < TICKET_REMIND_UNTIL):
        return []
    exits = account.state().get("exits") or {}
    out, remind = [], []
    journal = ts.effective(account.entries())
    book = account.book()
    for symbol, t in exits.items():
        if t.get("status") != "open" or t.get("deadline") != day or t.get("reminded"):
            continue
        if any(e.kind == "SELL" and e.symbol == symbol and e.day >= t["opened"] for e in journal):
            continue
        h = book.holdings.get(symbol)
        if not h:
            continue
        what = (f"🔴 trang BÁN từ {ddmm(t.get('signal_day') or t['opened'])}" if t["level"] == "sell"
                else f"⛔ thủng cắt lỗ {ddmm(t['opened'])}")
        out.append((f"ticket:{symbol}",
                    f"⏰ {TICKET_REMIND_FROM} · {symbol} {vnd(h.qty)} cp: hạn bán theo quy tắc là hôm nay ({what}), "
                    f"chưa thấy lệnh bán trong sổ; còn 10 phút trước ATC.\n"
                    f"• Đã bán: /tsban {symbol} {vnd(h.qty).replace('.', '')} GIÁ\n"
                    f"• Giữ có lý do: /tsgiu {symbol} CẮTLỖ lý_do (bot ghi lại, chấm sau 5/10/20 phiên)\nBot không đặt lệnh."))
        remind.append(symbol)
    if remind:
        account._update_state(lambda s: [s["exits"][sym].__setitem__("reminded", day) for sym in remind if sym in s["exits"]])
    return out


def deadline_lines(account, day: str) -> list[str]:
    """For the morning brief: the open tickets whose deadline is the session after ``day``."""
    from tradingagents.market import vn_rules

    nxt = vn_rules.add_sessions(day, 1)
    book = account.book()
    out = []
    for symbol, t in sorted((account.state().get("exits") or {}).items()):
        h = book.holdings.get(symbol)
        if not h or t.get("status") != "open":
            continue
        n = _sessions_until(t["opened"], day) - 1
        what = (f"🔴 từ {ddmm(t.get('signal_day') or t['opened'])}" if t["level"] == "sell" else f"⛔ {ddmm(t['opened'])}")
        deadline = t.get("deadline") or nxt
        when = ("HÔM NAY" if deadline == nxt else f"đã qua ({ddmm(deadline)})" if deadline < nxt
                else f"{ddmm(deadline)} (chờ T+2)")
        out.append(f"⏰ HẠN BÁN {when}: {symbol} {vnd(h.qty)} cp ({what}, treo {n} phiên). Backtest: bán ATO/đầu phiên.")
    return out


# ─── 5. the discipline meter ─────────────────────────────────────────────────

def settle_rows(account, rows: list[dict], sessions: tuple[int, ...], *, through: str, base_key: str | None = None) -> int:
    """Fill ``r{n}`` (the close ``n`` sessions after the row's day against the base: the
    row's own price when ``base_key`` names it, else that day's close) in place. Returns how
    many rows got a new value."""
    done = 0
    series_cache: dict[str, dict[str, float]] = {}
    for row in rows:
        symbol, day = row.get("symbol"), row.get("day")
        if not symbol or not day or all(row.get(f"r{n}") is not None for n in sessions):
            continue
        if symbol not in series_cache:
            series_cache[symbol] = _closes_for(account, symbol, through)
        series = series_cache[symbol]
        base = row.get(base_key) if base_key else series.get(day)
        if not base:
            continue
        for n in sessions:
            if row.get(f"r{n}") is None:
                got = _close_after(series, day, n)
                if got:
                    row[f"r{n}"] = round((got[1] / float(base) - 1) * 100, 2)
                    done += 1
    return done


def rule_replay(account, day: str, prices: dict[str, float], store=None) -> dict:
    """The journal replayed under the exit rule: for each buy, the first ⛔ (close under the
    plan's cut-loss, the site's level at the buy) or 🔴 (the site's final SELL after the buy)
    and the close of the session after it; what the owner did with those shares instead
    (sold later at their price, or still held at today's price). The sum is how much better
    (positive) or worse (negative) the rule would have left the assets - before fees and tax,
    like the journal; "the next session's close" stands in for its open."""
    from vnbot import taisan as ts

    store = store or _store(account)
    journal = ts.effective(account.entries())
    held = set(account.book().holdings)
    # a stock sold out needs its closes only up to its last sale (saved while it was held)
    last_sell = {}
    for e in journal:
        if e.kind == "SELL":
            last_sell[e.symbol] = max(last_sell.get(e.symbol, ""), e.day)
    closes = {}
    lots: dict[str, list[dict]] = {}
    per_entry: dict[int, dict] = {}
    gap = 0.0
    per_symbol: dict[str, float] = {}

    def series(symbol):
        if symbol not in closes:
            through = day if symbol in held else min(day, last_sell.get(symbol, day))
            closes[symbol] = _closes_for(account, symbol, through)
        return closes[symbol]

    for e in journal:
        if e.kind == "BUY":
            plan = store.state(e.symbol, e.day, f"{e.day}T09:00")     # what the site said when he bought (the day's own signal comes later)
            planned = plan and plan.get("side") == "BUY" and plan.get("sl") and 0 < float(plan["sl"]) < e.price
            stop = float(plan["sl"]) if planned else None
            sells = sorted((sg for sg in store.signals(e.symbol) if sg.get("side") == "SELL" and sg.get("day", "") > e.day
                            and _final(sg)), key=lambda sg: sg["day"])
            exit_day, level = (sells[0]["day"], "sell") if sells else (None, None)
            if plan and plan.get("side") == "SELL" and _final(plan):
                exit_day, level = e.day, "sell"              # bought against the site's SELL: the rule said sell at once
            if stop:
                hit = next((d for d in sorted(series(e.symbol)) if d > e.day and series(e.symbol)[d] <= stop), None)
                if hit and (exit_day is None or hit < exit_day):
                    exit_day, level = hit, "stop"
            exit_price = None
            if exit_day:
                got = _close_after(series(e.symbol), exit_day, 1)
                exit_price = got[1] if got else None
            lot = {"id": e.id, "qty": e.qty, "price": e.price, "day": e.day, "stop": stop, "exit_day": exit_day,
                   "level": level, "exit_price": exit_price}
            lots.setdefault(e.symbol, []).append(lot)
            per_entry[e.id] = lot
        elif e.kind == "BONUS":
            # bonus shares ride with the stock: received before the rule's exit they leave with it; received after
            # it, the rule-follower never had them, so their value counts against the rule (exit price 0)
            last = next((lot for lot in reversed(lots.get(e.symbol, [])) if lot["price"] > 0), None)
            lot = {"id": e.id, "qty": e.qty, "price": 0.0, "day": e.day, "stop": None, "exit_day": None, "level": None,
                   "exit_price": None}
            if last and last["exit_day"]:
                if last["exit_day"] >= e.day:
                    lot.update(exit_day=last["exit_day"], level=last["level"], exit_price=last["exit_price"])
                elif last["exit_price"] is not None:
                    lot.update(exit_day=e.day, level=last["level"], exit_price=0.0)
            lots.setdefault(e.symbol, []).append(lot)
        elif e.kind == "SELL":
            left = e.qty
            for lot in lots.get(e.symbol, []):
                if left <= 0:
                    break
                take = min(left, lot["qty"])
                if take <= 0:
                    continue
                lot["qty"] -= take
                left -= take
                if lot["exit_day"] and e.day > lot["exit_day"] and lot["exit_price"] is not None:
                    part = take * (lot["exit_price"] - e.price)
                    gap += part
                    per_symbol[e.symbol] = per_symbol.get(e.symbol, 0.0) + part
    for symbol, items in lots.items():
        price_now = prices.get(symbol)
        for lot in items:
            if lot["qty"] > 0 and lot["exit_day"] and lot["exit_price"] is not None and price_now:
                part = lot["qty"] * (lot["exit_price"] - price_now)
                gap += part
                per_symbol[symbol] = per_symbol.get(symbol, 0.0) + part
    return {"gap": gap, "per_symbol": per_symbol, "entries": per_entry, "day": day,
            "note": "giá đóng cửa phiên sau, gần đúng; theo đúng giá anh nhập; số này âm khi giữ là đúng"}


def wilson(wins: int, n: int, z: float = 1.645) -> tuple[float, float] | None:
    if n <= 0:
        return None
    p = wins / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half) * 100, min(1.0, centre + half) * 100


def discipline(account, day: str, prices: dict[str, float], store=None) -> dict:
    """The last four ISO weeks up to ``day``, counted by code: tickets and what became of
    them, buys by their rule verdict, early sells, the override results; plus the whole-
    journal replay under the rule and the owner's closed-trade profile."""
    state = account.state()
    book = account.book()
    first = _date.fromisoformat(day) - timedelta(days=7 * WEEKS - 1)
    since = first.isoformat()
    tickets = [t for t in list((state.get("exits") or {}).values()) + list(state.get("exits_log") or [])
               if t.get("opened", "") >= since]
    sold_fast = [t for t in tickets if t.get("status") == "sold" and t.get("sold_day") and t["sold_day"] <= t.get("deadline", "")]
    held = [t for t in tickets if t.get("status") in ("held", "replaced")]
    lapsed = [t for t in tickets if t.get("status") == "lapsed"]
    ignored = [t for t in tickets if t.get("status") == "open" and t.get("deadline", "") < day]
    # tickets whose deadline has passed, or that were acted on: the denominator of "sold in one session"
    due = [t for t in tickets if t.get("deadline", "") <= day or t.get("status") in ("sold", "held", "replaced", "lapsed")]
    costs = [c for symbol, t in (state.get("exits") or {}).items() if t.get("status") == "open"
             for c in [hesitation_cost(t, prices.get(symbol))] if c is not None]
    waiting = sum(costs) if costs else None            # None: no open ticket has its next-session close yet
    tags = state.get("tags") or {}
    buys = [t for t in tags.values() if t.get("kind") == "buy" and t.get("day", "") >= since]
    by_verdict = {v: [t for t in buys if t.get("verdict") == v] for v in ("ok", "warn", "block")}
    early = [t for t in tags.values() if t.get("kind") == "sell" and t.get("early") and t.get("day", "") >= since]
    early_r10 = [t["r10"] for t in early if t.get("r10") is not None]
    # the whole journal's tagged rows, for the groups' results (more samples than four weeks)
    all_buys = [t for t in tags.values() if t.get("kind") == "buy"]
    groups = {}
    for v in ("ok", "warn", "block"):
        xs = [t["r10"] for t in all_buys if t.get("verdict") == v and t.get("r10") is not None]
        groups[f"buy_{v}"] = {"n": len([t for t in all_buys if t.get("verdict") == v]), "settled": len(xs),
                              "r10": sum(xs) / len(xs) if xs else None}
    early_all = [t for t in tags.values() if t.get("kind") == "sell" and t.get("early")]
    xs = [t["r10"] for t in early_all if t.get("r10") is not None]
    groups["early"] = {"n": len(early_all), "settled": len(xs), "r10": sum(xs) / len(xs) if xs else None}
    overrides = [o for o in state.get("overrides") or [] if o.get("day", "") >= since]
    ov_r10 = [o["r10"] for o in state.get("overrides") or [] if o.get("r10") is not None]
    groups["override"] = {"n": len(state.get("overrides") or []), "settled": len(ov_r10),
                          "r10": sum(ov_r10) / len(ov_r10) if ov_r10 else None}
    lowered = [f for f in state.get("flags") or [] if f.get("kind") == "lower" and f.get("day", "") >= since]
    replay = rule_replay(account, day, prices, store)
    s = account.summary(prices=prices, book=book)
    ci = wilson(book.wins, book.sells)
    return {"since": since, "day": day, "tickets": len(tickets), "due": len(due), "sold_fast": len(sold_fast), "held": len(held),
            "lapsed": len(lapsed), "ignored": len(ignored), "waiting_cost": waiting,
            "buys": len(buys), "buys_ok": len(by_verdict["ok"]), "buys_warn": len(by_verdict["warn"]),
            "buys_block": len(by_verdict["block"]),
            "block_why": [f"{t.get('symbol')}: {', '.join(t.get('flags') or [])}" for t in by_verdict["block"]][:4],
            "early": len(early), "early_r10": sum(early_r10) / len(early_r10) if early_r10 else None,
            "overrides": len(overrides), "override_r10": sum(ov_r10) / len(ov_r10) if ov_r10 else None,
            "override_n": len(ov_r10), "lowered": len(lowered),
            "rule_gap": replay["gap"], "nav": s["total"], "nav_rule": s["total"] + replay["gap"], "pnl": s["pnl"],
            "sells": book.sells, "wins": book.wins, "ci": ci, "realized": s["realized"], "unrealized": s["unrealized"],
            "diet_since": (state.get("diet") or {}).get("since") or "", "groups": groups}


def discipline_lines(d: dict) -> list[str]:
    lines = [f"📏 KỶ LUẬT (đo bằng code, không phải ý kiến) · {WEEKS} tuần đến {ddmm(d['day'])}"]
    gap = d["rule_gap"]
    share = f" = {pct0(abs(gap) / abs(d['pnl']) * 100 if d['pnl'] else 0, 0)} khoản lỗ" if d["pnl"] < 0 and gap > 0 else ""
    lines.append(f"Tài sản thực {vnd(d['nav'])} · nếu bán theo ⛔/🔴 ở phiên sau mỗi cảnh báo từ đầu sổ: ~{vnd(d['nav_rule'])} "
                 f"({signed(gap)}{share}) · giá đóng cửa phiên sau, gần đúng; theo đúng giá anh nhập; số này âm khi giữ là đúng")
    lines.append(f"Cảnh báo ⛔/🔴: {d['tickets']} · bán trong 1 phiên: {d['sold_fast']}/{d.get('due', d['tickets'])} đến hạn"
                 f" · giữ có lý do (/tsgiu): {d['held']} · bỏ qua: {d['ignored']}"
                 + (f" · quy tắc rút lại: {d['lapsed']}" if d["lapsed"] else "")
                 + (f" · cái giá của chần chừ đang mở: {signed(d['waiting_cost'])}" if d["waiting_cost"] is not None
                    else " · cái giá của chần chừ: chưa đo (chưa có phiên sau cảnh báo)"))
    lines.append(f"Lệnh mua: {d['buys']} · trong ngân sách ✅ {d['buys_ok']} · có lưu ý ⚠️ {d['buys_warn']} · ngoài quy tắc ⛔ "
                 f"{d['buys_block']}" + (" (" + "; ".join(d["block_why"]) + ")" if d["block_why"] else ""))
    if d["early"]:
        lines.append(f"Bán trước tín hiệu (trang vẫn MUA, chưa có ⛔/🔴): {d['early']} lần"
                     + (f" · giá 10 phiên sau TB {pct(d['early_r10'])}" if d["early_r10"] is not None else " · đang đo"))
    if d["overrides"] or d["override_n"]:
        lines.append(f"Ghi đè /tsgiu: {d['overrides']} lần trong {WEEKS} tuần"
                     + (f" · giá 10 phiên sau TB {pct(d['override_r10'])} ({d['override_n']} lần đã chấm"
                        + (", chưa đủ mẫu" if d["override_n"] < 10 else "") + ")" if d["override_n"] else ""))
    if d["lowered"]:
        lines.append(f"Anh hạ cắt lỗ: {d['lowered']} lần (bot chấm sau 5/10/20 phiên)")
    if d["sells"]:
        ci = d["ci"]
        verdict = "chưa đủ để nói có lợi thế" if ci and ci[0] < 50 else "có lợi thế trong mẫu này"
        lines.append(f"Hồ sơ: {d['sells']} lệnh đã đóng, thắng {d['wins']}/{d['sells']} ({pct0(d['wins'] / d['sells'] * 100, 0)}"
                     + (f"; khoảng tin cậy 90%: {ci[0]:.0f}–{ci[1]:.0f}% → {verdict}" if ci else "") + ")"
                     f" · lãi đã chốt {signed(d['realized'])} · lỗ đang mở {signed(d['unrealized'])}")
    if d["diet_since"]:
        lines.append(f"(từ {ddmm(d['diet_since'])} bot không đẩy 🟠 trong phiên)")
    lines.append(f"Quy tắc cũng thua {100 - MEASURED['rule_win']}% số lệnh với lỗ nhỏ (TB {pct(MEASURED['rule_avg_loss'])}): "
                 "số đo để học, không để tiếc.")
    return lines


# ─── 6. the recovery goal ────────────────────────────────────────────────────

def set_goal(account, word: str) -> str:
    """``/tsmuctieu NGÀY`` (YYYY-MM-DD or dd/mm/yyyy, after today) or ``xoa``."""
    word = word.strip().lower()
    if word in ("xoa", "xoá", "xóa"):
        account._update_state(lambda s: s.__setitem__("goal", {"date": DEFAULT_GOAL_DATE, "set_day": "",
                                                               "source": "default"}))
        return f"✅ Mốc hoàn vốn về mặc định {ddmm(DEFAULT_GOAL_DATE)}/{DEFAULT_GOAL_DATE[:4]}."
    try:
        if "/" in word:
            d, m, y = (int(x) for x in word.split("/"))
            when = _date(y, m, d)
        else:
            when = _date.fromisoformat(word)
    except (ValueError, TypeError):
        raise ValueError("Dùng: /tsmuctieu 2027-03-31 (hoặc 31/03/2027) · /tsmuctieu xoa") from None
    if when.isoformat() <= account.today():
        raise ValueError("Mốc phải là một ngày sau hôm nay.")
    if (when - _date.fromisoformat(account.today())).days > 5 * 366:
        raise ValueError("Mốc tối đa 5 năm (số đo chỉ có tới cửa sổ 12 tháng).")
    account._update_state(lambda s: s.__setitem__("goal", {"date": when.isoformat(), "set_day": account.today(),
                                                           "source": "owner"}))
    return f"✅ Mốc hoàn vốn: {when.strftime('%d/%m/%Y')}. Bot đặt số đo cạnh mốc này mỗi cuối tuần; không dự báo."


def goal(account, day: str, summary: dict | None = None) -> dict:
    """The arithmetic of the owner's recovery target next to the measured figures."""
    from tradingagents.market import vn_rules

    s = summary or account.summary()
    g = account.state().get("goal") or {}
    target = g.get("date") or DEFAULT_GOAL_DATE
    nav, capital = s["total"], s["capital"]
    req = (capital / nav - 1) * 100 if nav > 0 and capital else None
    n = _sessions_until(vn_rules.add_sessions(day, 1), target) if target > day else 0
    out = {"date": target, "source": g.get("source") or "default", "set_day": g.get("set_day") or "", "nav": nav,
           "capital": capital, "req": req, "sessions": n, "passed": target <= day}
    if req is None:
        return out
    r = req / 100
    per = MEASURED["rule_per_trade"] / 100
    out["rounds"] = n / HOLD_SESSIONS if n else None
    out["r_trade"] = ((1 + r) ** (HOLD_SESSIONS / n) - 1) * 100 if n and r > 0 else None
    out["multiple"] = out["r_trade"] / MEASURED["rule_per_trade"] if out["r_trade"] else None
    out["trades_to_recover"] = math.log(1 + r) / math.log(1 + per) if r > 0 else 0
    out["months"] = out["trades_to_recover"] * HOLD_SESSIONS / 21
    vni = account.closes().get("VNINDEX") or {}
    last = max((d for d in vni if d <= day), default=None)
    out["vnindex"] = vni.get(last) if last else None
    out["vnindex_needed"] = out["vnindex"] * (1 + r) if out["vnindex"] else None
    ath, ath_day = MEASURED["ath_vnindex"]
    out["ath_pct"] = (ath / out["vnindex"] - 1) * 100 if out["vnindex"] else None
    # the index windows were measured at +38%; the one closest to the horizon is the yardstick
    out["window"] = "2 tháng" if n <= 50 else "6 tháng" if n <= 150 else "12 tháng"
    best = MEASURED["vni_windows"][out["window"]][2]
    out["out_of_range"] = req > best                       # more than the best window of that length ever gave
    out["tail"] = req > MEASURED["thrust_range"][1]         # beyond what the 20-session thrusts gave
    return out


def goal_text(account, day: str, summary: dict | None = None, last_week: dict | None = None) -> str:
    g = goal(account, day, summary)
    when = _date.fromisoformat(g["date"]).strftime("%d/%m/%Y")
    if g["req"] is None:
        return "🎯 MỤC TIÊU HOÀN VỐN: chưa có vốn gốc trong sổ."
    if g["req"] <= 0:
        return (f"🎯 MỤC TIÊU HOÀN VỐN · mốc {when}: tài sản {vnd(g['nav'])} đã bằng hoặc hơn vốn gốc {vnd(g['capital'])} "
                f"({pct(-g['req'])}). Giữ quy tắc ⛔/🔴 và ngân sách rủi ro.")
    if g["passed"]:
        lo, hi = MEASURED["thrust_range"]
        inside = lo <= -g["req"] <= hi
        return (f"🎯 Mốc {when} đã qua: tài sản {vnd(g['nav'])} so với vốn {vnd(g['capital'])} ({pct(-g['req'])}); so với số đo: "
                f"{'trong' if inside else 'ngoài'} khoảng {pct(lo)}…{pct(hi)} của {MEASURED['thrust_n']} lần bùng nổ độ rộng. "
                "Đặt mốc mới: /tsmuctieu NGÀY (mốc 12–24 tháng hợp với mức đo được).")
    who = f"anh đặt {ddmm(g['set_day'])}" if g["source"] == "owner" else "mặc định; đổi: /tsmuctieu NGÀY"
    ath, ath_day = MEASURED["ath_vnindex"]
    m = MEASURED
    lines = [f"🎯 MỤC TIÊU HOÀN VỐN · mốc {when} ({who}) · còn {g['sessions']} phiên",
             f"Cần {pct(g['req'])}: từ {vnd(g['nav'])} lên vốn gốc {vnd(g['capital'])} (thiếu {vnd(g['capital'] - g['nav'])}); "
             "nạp thêm tiền không thu hẹp khoản thiếu này, chỉ làm con số % nhỏ đi."]
    if g.get("vnindex_needed"):
        lines.append(f"Nếu danh mục đi cùng thị trường, VNINDEX phải lên ~{vnd(g['vnindex_needed'])} điểm (về đỉnh cũ "
                     f"{vnd(ath)} ngày {ath_day} chỉ là {pct(g['ath_pct'])})"
                     + (f" → hơn mọi cửa sổ {g['window']} đã đo" if g["out_of_range"] else "") + ".")
    lines.append("Số đo đã có (không phải dự báo):")
    rule = (f"• Quy tắc ⛔/🔴, 582 lệnh 2024–26: {pct(m['rule_per_trade'], 2)}/lệnh, thắng {m['rule_win']}%, giữ trung vị "
            f"{m['hold_median']} phiên")
    if g.get("r_trade"):
        rounds = f"{g['rounds']:.1f}".replace(".", ",")
        rule += (f" → ~{rounds} vòng/mã trong {g['sessions']} phiên; mục tiêu đòi ~{pct(g['r_trade'], 1)}/lệnh, "
                 f"gấp ~{g['multiple']:.0f} lần mức đo được;")
    rule += f" với mức đo được, hoàn vốn là việc ~{g['months']:.0f} tháng."
    w2, w6, w12 = m["vni_windows"]["2 tháng"], m["vni_windows"]["6 tháng"], m["vni_windows"]["12 tháng"]
    # the windows were measured at +38% (the owner's figure on 24/10/2026); his figure now is compared with the best
    # window of the length closest to his horizon
    wn = g["window"]
    seen = (f"chưa từng xảy ra trong {wn}" if g["out_of_range"]
            else f"từng xảy ra trong {wn}, ở phần đuôi" if g["tail"] else f"từng xảy ra trong {wn}")
    lines += [rule,
              f"• VNINDEX 2022–10/2026, mọi cửa sổ: 2 tháng tốt nhất {pct(w2[2])} ({vnd(w2[0])} cửa sổ, chưa lần nào +38%); "
              f"6 tháng: {pct0(w6[1])} số cửa sổ đạt +38%, tốt nhất {pct(w6[2])}; 12 tháng: {pct0(w12[1])}, tốt nhất {pct(w12[2])}. "
              f"Mức anh cần ({pct(g['req'])}, mốc {wn}) {seen}.",
              f"• Tài khoản nhỏ mô phỏng theo mọi lệnh MUA của trang: đạt +38% trong kỳ ở {m['sim_hit']} tháng bắt đầu; "
              f"dồn 2 mã không giúp, xấu nhất {m['sim_worst']}%.",
              f"• {m['thrust_n']} lần bùng nổ độ rộng như hiện nay: sau 20 phiên rổ lên {m['thrust_up']}/{m['thrust_n']}, "
              f"trung vị {pct(m['thrust_median'])}, khoảng {pct(m['thrust_range'][0])}…{pct(m['thrust_range'][1])}.",
              (f"Kết luận: mục tiêu {g['sessions']} phiên đòi hơn cả cửa sổ {g['window']} tốt nhất đã đo, chỉ tới nếu gặp may ở phần đuôi."
               if g["out_of_range"] else
               f"Kết luận: mục tiêu {g['sessions']} phiên nằm trong vùng từng xảy ra, nhưng không phải thường lệ; không có "
               "cách nào bảo đảm nó.")
              + f" Việc bot bảo đảm được: mọi ⛔/🔴 có hạn xử lý, mọi lệnh mua được kiểm tra, rủi ro đang mở ≤ "
              f"{open_risk_cap():g}% tài sản; không margin, không bình quân giá xuống."]
    week = ""
    if last_week:
        handled = last_week["sold_fast"] + last_week.get("held", 0)
        week = (f" {WEEKS} tuần qua: {last_week['buys_block']} lệnh mua ⛔ · xử lý trong 1 phiên {handled}/"
                f"{last_week.get('due', last_week['tickets'])} (bán {last_week['sold_fast']}, giữ có lý do {last_week.get('held', 0)}) · "
                f"rủi ro đang mở hôm nay {pct0(last_week['open_risk'])}.")
    lines.append(f"Mục tiêu tuần (đo được): 0 lệnh mua ⛔ · 100% ⛔/🔴 xử lý trong 1 phiên (bán, hoặc /tsgiu có lý do) · "
                 f"rủi ro đang mở ≤ {open_risk_cap():g}%.{week}")
    return "\n".join(lines)
