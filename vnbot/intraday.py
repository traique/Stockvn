"""Alerts during the session: code only, no AI request.

The books still trade on the day's closed bar after 15:05 (as the backtests
did); this only tells the owner, while the market is open, what deserves a look
now rather than at the evening report. Every ``intraday_interval_min`` minutes
of continuous trading (9:15-11:30, 13:00-14:30) it reads the last matched price
of:

- the real book's holdings (signal mode): price at or under the stop (with how
  many shares T+2 lets you sell at that time of day: shares bought on T only
  from the afternoon of T+2, sessions counted by the exchange calendar), at T1
  or T2, at the floor of yesterday's close;
- the Quant Engine paper book: its holdings (same checks) and its limit buy
  orders for today (the price is in the buy zone, or already under the planned stop);
- VNINDEX down ``intraday_vni_drop_pct`` % (and twice that) from the last close;
- the valuation cards: the price entering the Rẻ zone (≤ Base) or the Đắt zone (≥ Bull) of
  the bot's own card (``vnbot.dinhgia``, kept by the evening job) of every tracked stock -
  watchlist, Tài sản holdings - or, for a stock the owner entered a card of (``/tsdinhgia``,
  held or not), of his card instead: it overrides the bot's while it is valid; the alert names
  the card it followed. Once per entry, not once a day: the last zone of each card is kept in
  ``valuation_zones.json`` (across days and restarts) and is left only
  ``taisan.ZONE_BUFFER_PCT`` back past its level; at most one Rẻ and one Đắt per card a session.
  The owner's card past its date, or past an ex-date since it was entered (its levels are on
  the old basis), is silenced, said once, and the bot's card takes over. The bot's card is used
  only when built on the previous session's data or later (the 16:00 job, or a view today) and
  before its next ex-date; the job builds it again each evening (a new statement, an ex-date).
  No VCI call for the bot's cards here: the stored card and the live price. The "zones_bot"
  switch on /ai turns the bot's cards' alerts off. These go out after the other alerts, in a
  message of their own: an owner's card's ex-dates are read from VCI once a day and a price
  per card from DNSE, which must not hold back a stop.

On an ex-date (cash or stock dividend, bonus shares) the stop and targets of a
book are on yesterday's price basis: when the source's history already shows
the new basis, the levels are divided by the ratio before the checks; when it
does not yet, a price further under yesterday's close than the band allows
(HOSE/HNX; it cannot trade) is reported as a probable ex-date instead of a
stop hit, and on the next sessions the evening run's estimate of the ratio
(``Position.pending_ratio``) is used until the source re-adjusts.

Each alert goes out once a day (kept in ``intraday_alerts.json``, so a restart
does not repeat them). A price checked every few minutes can miss a brief dip
through a stop between two reads.

The one reminder of the discipline layer (``vnbot.kyluat``) also goes out from here:
on the day a ⛔/🔴 exit ticket of the Tài sản portfolio falls due, between 14:20 and
14:45, when no sale is in the journal yet - once a ticket, whatever the interval, and
whether or not the price alerts are switched on (it is not a price alert).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

from vnbot.jobs import write_text_atomic

logger = logging.getLogger(__name__)

ACTIVE_PHASES = ("continuous",)


def _fmt(value) -> str:
    return f"{value:,.0f}".replace(",", ".") if value is not None else "-"


def live_price(code: str, day: str) -> float | None:
    from tradingagents.dataflows.news import get_live_match_price

    return get_live_match_price(code, day)


def vnindex_change(day: str) -> float | None:
    """VNINDEX now against the last close, in percent; None when today's bar is not out.

    Read straight from the price source, whose last bar during the session is
    today's forming one (its close = the index now). The cached loader drops that
    bar until 15:05 by design, so through it this alert could never fire."""
    from datetime import date, timedelta

    from tradingagents.dataflows.vendors import dnse
    from tradingagents.market import vn_rules

    start = (date.fromisoformat(day) - timedelta(days=20)).isoformat()
    frame = dnse.fetch_ohlcv(vn_rules.BENCHMARK, start, day)
    if len(frame) < 2 or str(frame["Date"].iloc[-1])[:10] != day:
        return None
    last, prev = float(frame["Close"].iloc[-1]), float(frame["Close"].iloc[-2])
    if not (last > 0 and prev > 0) or str(frame["Date"].iloc[-2])[:10] != vn_rules.prev_session(day):
        return None              # a hole in the feed: the bar before is not the previous session's close
    return (last / prev - 1) * 100


def history_close(code: str, on: str, day: str) -> float | None:
    """The close of session ``on`` as the price source gives it now, during the session of
    ``day`` (re-adjusted for an ex-date today when the source already did it); None when
    unknown. The loader caches each download for a few minutes."""
    from tradingagents.dataflows.ohlcv import load_ohlcv

    frame = load_ohlcv(code, day, fill_gaps=False)
    rows = frame[frame["Date"].dt.strftime("%Y-%m-%d") == on]
    if rows.empty:
        return None
    value = float(rows["Close"].iloc[-1])
    return value if value > 0 else None


def exchange(code: str) -> str | None:
    from tradingagents.dataflows.symbols import exchange_of

    try:
        return exchange_of(code)
    except Exception:  # noqa: BLE001 - HOSE bands are assumed
        return None


def read_alerts(path: Path, day: str) -> list[dict]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return list(data.get("alerts", [])) if data.get("day") == day else []


TAISAN_LABEL = "💼 Tài sản"
FOOTER = "Bot im lặng = không có việc cần làm. Bot không tự đặt lệnh; các tài khoản ảo vẫn khớp theo nến ngày sau 15:05."


class IntradayMonitor:
    def __init__(self, settings, trader, notify: Callable[[str], None], *,
                 price=live_price, index_change=vnindex_change, exchange_of=exchange, switches=None,
                 closes=history_close, watchlist=list):
        self.settings, self.trader, self.notify = settings, trader, notify
        self.switches = switches            # vnbot.channels.Switches: "intraday", "zones_bot" on the web page
        self.watchlist = watchlist          # () -> the watchlist: with the holdings, the bot's cards alerted on
        self.price, self.index_change, self.exchange_of = price, index_change, exchange_of
        self.closes = closes                # (code, session, day) -> that session's close as the source gives it now
        self.path = Path(settings.data_dir) / "intraday_alerts.json"
        self.zones_path = Path(settings.data_dir) / "valuation_zones.json"     # the last zone of each card
        self._last_check = 0.0
        self._thread = threading.Thread(target=self._loop, name="intraday", daemon=True)

    def start(self) -> None:
        self._thread.start()

    # ── once-a-day memory ──
    def _sent(self, day: str) -> set[str]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return set()
        return set(data.get("keys", [])) if data.get("day") == day else set()

    def _remember(self, day: str, keys: set[str], new: list[str], at: str) -> None:
        log = self.today(day) + [{"at": at, "text": text} for text in new]
        write_text_atomic(self.path, json.dumps({"day": day, "keys": sorted(keys), "alerts": log}, ensure_ascii=False))

    def today(self, day: str) -> list[dict]:
        """Today's alerts as {at: "HH:MM", text}, oldest first (for the web page)."""
        return read_alerts(self.path, day)

    # ── what to watch ──
    def _books(self, day: str):
        """(label, book) of the books whose holdings are watched."""
        out = []
        if self.settings.trade_mode == "signal":
            try:
                real = self.trader.real()    # T+2 is judged per lot and time of day (Position.sellable_on)
                if real.meta.get("close_marks") != 1:
                    # Saved before the evening run marked closes: its marks are fill prices.
                    for pos in real.positions.values():
                        pos.last_date = ""
                out.append(("💼 Thật", real))
            except Exception as exc:  # noqa: BLE001 - a damaged real book is reported by /danhmuc
                logger.warning("Intraday: real book unavailable: %s", exc)
        try:
            mine = self.trader.real("stv")       # the owner's Tài sản portfolio (vnbot.taisan), read only
            if mine.positions:
                out.append((TAISAN_LABEL, mine))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Intraday: Tài sản portfolio unavailable: %s", exc)
        out.append(("🤖 Ảo (Quant Engine)", self.trader.paper()))
        return out

    def _basis_today(self, code: str, marked: float | None, marked_on: str | None, day: str,
                     cache: dict) -> float | None:
        """old basis / today's basis when the source's history, read now, re-prices the
        session a book marked at ``marked`` (an ex-date today: dividend, bonus shares);
        None when nothing changed or it cannot be told."""
        from tradingagents.trading.ledger import BASIS_MAX_RATIO, BASIS_MIN_RATIO

        if not marked_on or not marked or marked <= 0:
            return None
        key = (code, marked_on)
        if key not in cache:
            try:
                cache[key] = self.closes(code, marked_on, day)
            except Exception as exc:  # noqa: BLE001 - the alert falls back to the price-band check
                logger.warning("Intraday history %s: %s", code, exc)
                cache[key] = None
        close = cache[key]
        if not close or close <= 0:
            return None
        ratio = float(marked) / float(close)
        return ratio if BASIS_MIN_RATIO <= ratio <= BASIS_MAX_RATIO else None

    def check(self, day: str, now=None) -> list[str]:
        """One pass: the alerts not sent yet today, sent and returned."""
        from tradingagents.market import vn_rules
        from tradingagents.trading.ledger import GAP_TOLERANCE_PCT, _rebased

        now = now or vn_rules.now_vn()
        afternoon = now.astimezone(vn_rules.VN_TZ).time() >= vn_rules.AFTERNOON_START
        prev = vn_rules.prev_session(day)
        sent = self._sent(day)
        alerts: list[tuple[str, str]] = []
        prices: dict[str, float | None] = {}
        history: dict = {}

        def price_of(code):
            if code not in prices:
                try:
                    prices[code] = self.price(code, day)
                except Exception as exc:  # noqa: BLE001 - one quote must not stop the pass
                    logger.warning("Intraday price %s: %s", code, exc)
                    prices[code] = None
            return prices[code]

        for label, book in self._books(day):
            for pos in book.positions.values():
                p = price_of(pos.symbol)
                if not p:
                    continue
                ex = self.exchange_of(pos.symbol)
                where = f"{label} {pos.symbol}: giá {_fmt(p)}"
                # Yesterday's close, the reference of today's band (a mark from another day is not).
                ref = float(pos.last_price) if pos.last_date == prev and pos.last_price else None
                stop, t1, t2 = pos.stop, pos.target1, pos.target2
                ratio = self._basis_today(pos.symbol, pos.last_price, pos.last_date, day, history)
                pending = float(getattr(pos, "pending_ratio", 0) or 0)
                if ratio or pending > 1:
                    # An ex-date the source already re-priced (measured), or one it has not yet
                    # (the evening run's estimate from the gap): compare on today's basis.
                    measured = bool(ratio)
                    ratio = ratio or pending
                    stop, t1, t2 = (_rebased(x, ratio, ex) for x in (stop, t1, t2))
                    ref = ref / ratio if ref and measured else None
                    how = "giá đã điều chỉnh" if measured else f"tỷ lệ ước tính (GDKHQ {pos.pending_since}, nguồn chưa điều chỉnh)"
                    alerts.append((f"basis:{label}:{pos.symbol}",
                                   f"⚖️ {label} {pos.symbol}: giá điều chỉnh quyền x{ratio:.3f} (cổ tức/thưởng) - "
                                   f"stop/T1/T2 so theo {how} (stop {_fmt(stop) if stop else '-'})"))
                elif ref and vn_rules.normalize_exchange(ex) in ("HOSE", "HNX"):
                    # Further under yesterday's close than the band allows cannot trade: today's
                    # reference was cut (an ex-date the source has not re-priced yet) or the quote
                    # is bad, and the old stop would read as a fake hit. Not on UPCoM (its
                    # reference is the previous average, not the close) or an unknown board.
                    band = vn_rules.PRICE_BAND_PCT[vn_rules.normalize_exchange(ex)]
                    lowest = ref * (1 - (band + GAP_TOLERANCE_PCT) / 100)
                    if p < lowest:
                        alerts.append((f"basis:{label}:{pos.symbol}",
                                       f"⚖️ {where} thấp hơn giá sàn tính từ giá đóng cửa {_fmt(ref)}: nhiều khả năng "
                                       "hôm nay là ngày GDKHQ (cổ tức/thưởng) - stop/T1 cũ chưa điều chỉnh nên bot không "
                                       "báo chạm stop cho mã này hôm nay; kiểm tra trên app CTCK."))
                        continue
                if stop and p <= stop:
                    sellable = pos.sellable_on(day, afternoon=afternoon)
                    t2_txt = (f"bán được {_fmt(sellable)}/{_fmt(pos.qty)} cp (T+2"
                              + ("" if afternoon else ", phần mới về bán được từ phiên chiều") + ")"
                              if sellable < pos.qty else f"{_fmt(pos.qty)} cp bán được")
                    alerts.append((f"stop:{label}:{pos.symbol}", f"⛔ {where} ≤ stop {_fmt(stop)}, {t2_txt}"))
                if t1 and not pos.t1_done and p >= t1:
                    alerts.append((f"t1:{label}:{pos.symbol}", f"🎯 {where} chạm T1 {_fmt(t1)}"))
                if t2 and p >= t2:
                    alerts.append((f"t2:{label}:{pos.symbol}", f"🏁 {where} chạm T2 {_fmt(t2)}"))
                if ref:
                    _, floor = vn_rules.ceiling_floor(ref, ex)
                    if p <= floor:
                        alerts.append((f"floor:{label}:{pos.symbol}",
                                       f"🔒 {where} = giá sàn {_fmt(floor)}, có thể không bán được"))
        for order in self.trader.paper().orders:
            if order.side != "BUY" or not order.limit:
                continue
            p = price_of(order.symbol)
            if not p:
                continue
            limit, stop = order.limit, order.stop
            ratio = self._basis_today(order.symbol, order.ref, order.created, day, history)
            if ratio:
                limit, stop = (_rebased(x, ratio, self.exchange_of(order.symbol)) for x in (limit, stop))
            where = f"🤖 Lệnh mua {order.symbol} giá ≤ {_fmt(limit)}"
            if stop and p <= stop:
                alerts.append((f"order-stop:{order.symbol}",
                               f"⚠️ {where}: giá {_fmt(p)} đã dưới stop dự kiến {_fmt(stop)}, nên bỏ lệnh"))
            elif p <= limit:
                alerts.append((f"order-zone:{order.symbol}", f"🛒 {where}: giá {_fmt(p)} đang trong vùng mua"))
        try:
            change = self.index_change(day)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Intraday VNINDEX: %s", exc)
            change = None
        drop = self.settings.intraday_vni_drop_pct
        if change is not None and drop > 0:
            for level in (drop, 2 * drop):
                if change <= -level:
                    alerts.append((f"vni:{level:g}", f"📉 VNINDEX {change:+.2f}% so với phiên trước (ngưỡng −{level:g}%)"))
        alerts += self._ticket_reminders(day, now)
        new = [(k, text) for k, text in alerts if k not in sent]
        self._keep_taisan_alerts(new, prices, day)
        sent |= {k for k, _ in new}
        texts = [text for _, text in new]
        if texts:                   # sent before the valuation cards are looked at (VCI, a price per card)
            self._send(day, sent, texts)
        try:
            zones = self._zone_alerts(day, price_of)     # their own memory, not the day's keys
        except Exception as exc:  # noqa: BLE001 - the other alerts went out
            logger.warning("Intraday: valuation card alerts failed: %s", exc)
            zones = []
        if zones:
            self._send(day, sent, zones)
        return texts + zones

    def _send(self, day: str, keys: set[str], texts: list[str]) -> None:
        from tradingagents.market import vn_rules

        at = vn_rules.now_vn().strftime("%H:%M")
        self._remember(day, keys, texts, at)
        self.notify(f"🔔 Cảnh báo trong phiên {at} (không gọi AI)\n" + "\n".join(texts) + "\n" + FOOTER)

    def _bot_cards(self, day: str, tracked: list[str]) -> dict[str, dict]:
        """The bot's cards of the tracked stocks usable today: built on the previous session's data or
        later, before their next ex-date (the evening job builds them again). The stored rows only, no
        VCI call."""
        from tradingagents.market import vn_rules
        from vnbot import dinhgia

        built_after = dinhgia._job_time(vn_rules.prev_session(day))
        latest = dinhgia.Cards(self.settings.data_dir).latest()
        out = {}
        for symbol in tracked:
            row = latest.get(symbol)
            if not row or row.get("base") is None or row.get("bull") is None:
                continue                      # not built yet, or no card (the row says why)
            if row.get("day") != day and float(row.get("ts") or 0) < built_after:
                logger.debug("Intraday: the bot's card of %s is from %s, not rebuilt since: no zone alert", symbol,
                            row.get("day"))
                continue
            if row.get("expires") and row["expires"] <= day:
                continue                      # an ex-date: its per-share levels are on the old basis until tonight
            out[symbol] = row
        return out

    def _zone_alerts(self, day: str, price_of) -> list[str]:
        """🟢 a card's Rẻ zone entered (price ≤ Base), 🔺 its Đắt zone (price ≥ Bull): the owner's card
        (``vnbot.taisan``) of a stock he entered one for, else the bot's (``_bot_cards``; "zones_bot" on).
        Kept in the Tài sản shadow results ("re", "dat", with the card's source; no AI call), and said
        only when kept there first: at most one of each per card a session. The memory is the last zone
        per card (the owner's under the stock's code, the bot's under "<code>@bot"); a card entered or
        rebuilt with other levels starts from the zone it showed then. An owner's card expired or stale
        is said once, then left out - the bot's card takes over; one whose ex-dates could not be read
        today is skipped for now (a price on a new basis must not read as Rẻ)."""
        from vnbot import dinhgia

        account = getattr(self.trader, "taisan", None)
        manual = account.valuations() if account is not None else {}
        bot_on = self.switches is None or self.switches.on("zones_bot")
        tracked = dinhgia.tracked(self.watchlist(), account) if bot_on else []
        bot = self._bot_cards(day, tracked) if bot_on else {}
        if not manual and not bot and not self.zones_path.exists():
            return []
        try:
            memory = json.loads(self.zones_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            memory = {}
        memory = memory if isinstance(memory, dict) else {}
        out, kept = [], {}

        def remembered(key: str, ident: str, zone) -> dict:
            seen = memory.get(key) if isinstance(memory.get(key), dict) else {}
            kept[key] = dict(seen) if seen.get("card") == ident else {"card": ident, "zone": zone}
            return kept[key]

        for symbol in sorted(set(manual) | set(bot)):
            row = bot.get(symbol)
            card = manual.get(symbol)
            try:
                if card is not None:
                    seen = remembered(symbol, "|".join(str(card.get(k)) for k in ("at", "bear", "base", "bull")),
                                      card.get("zone"))
                    if not seen.get("muted"):
                        line = self._zone_line(account, symbol, card, seen, day, price_of, bot_next=row is not None)
                        if line:
                            out.append(line)
                    if not seen.get("muted"):
                        continue              # the owner's card stands: the bot's is not looked at
                if row is not None:
                    ident = "bot|" + "|".join([str(row.get("period"))] + [f"{float(row[k]):.0f}" for k in ("bear", "base", "bull")])
                    line = self._bot_zone_line(account, symbol, row, remembered(f"{symbol}@bot", ident, row.get("zone")),
                                               day, price_of)
                    if line:
                        out.append(line)
            except Exception as exc:  # noqa: BLE001 - judged at the next check; the other cards go on
                logger.warning("Intraday: valuation card of %s: %s", symbol, exc)
        # the bot's memory of a stock not looked at now - the switch off, the owner's card standing, its card not
        # rebuilt last night or past an ex-date today - is kept while the stock is tracked
        for key, seen in memory.items():
            if key.endswith("@bot") and key not in kept and (not bot_on or key[:-4] in manual or key[:-4] in tracked):
                kept[key] = seen
        if kept != memory:
            write_text_atomic(self.zones_path, json.dumps(kept, ensure_ascii=False))
        return out

    @staticmethod
    def _entered(account, symbol: str, card: dict, seen: dict, day: str, p: float, src: str) -> str | None:
        """The zone ``p`` enters on ``card`` (``seen``, its memory, updated in place), when it enters Rẻ or
        Đắt and is kept in the shadow results first (once per session and card source)."""
        from vnbot import taisan as ts

        zone = ts.card_zone(card, p, seen.get("zone"))
        entered = zone != seen.get("zone") and zone in ("Rẻ", "Đắt")
        seen["zone"] = zone
        if not entered:
            return None
        if account is not None:
            try:
                if not account.record_alert("re" if zone == "Rẻ" else "dat", symbol, p, day, hook=False, src=src):
                    return None                  # said already this session
            except Exception as exc:  # noqa: BLE001 - the alert is sent anyway
                logger.warning("Tài sản: zone alert of %s not kept: %s", symbol, exc)
        return zone

    @staticmethod
    def _what(symbol: str, zone: str, card: dict, p: float) -> str:
        return (f"🟢 {symbol} vào vùng Rẻ: giá {_fmt(p)} ≤ Base {_fmt(card['base'])}" if zone == "Rẻ"
                else f"🔺 {symbol} vào vùng Đắt: giá {_fmt(p)} ≥ Bull {_fmt(card['bull'])}")

    @classmethod
    def _zone_line(cls, account, symbol: str, card: dict, seen: dict, day: str, price_of, *, bot_next: bool = False) -> str | None:
        """The alert of one card the owner entered, if any; ``seen`` (its memory) updated in place.
        ``bot_next``: the bot has a card of the stock to take over once this one is silenced."""
        from vnbot import taisan as ts

        status = account.valuation_status(symbol, card, day)
        again = f"nhập lại: /tsdinhgia {symbol} BEAR BASE BULL"
        after = ("bot báo vùng Rẻ/Đắt theo thẻ của bot; muốn dùng thẻ của anh thì " + again if bot_next
                 else "bot ngừng báo vùng Rẻ/Đắt; " + again)
        if status["expired"]:
            seen["muted"] = "expired"
            return f"⌛ {symbol}: thẻ định giá anh nhập hết hạn {ts.ddmmyyyy(card['expires'])} → {after}"
        if status["stale"]:
            seen["muted"] = "stale"
            return (f"⚠️ {symbol}: thẻ định giá anh nhập cần cập nhật - GDKHQ {ts.ddmmyyyy(status['stale']['ex'])} "
                    f"({status['stale']['what']}) sau ngày anh nhập {ts.ddmmyyyy(card['day'])}, các mốc còn theo giá "
                    f"cũ → {after}")
        if not status["checked"]:
            return None
        p = price_of(symbol)
        if not p:
            return None
        zone = cls._entered(account, symbol, card, seen, day, p, "tay")
        if zone is None:
            return None
        return (f"{cls._what(symbol, zone, card, p)}, {ts.mos_text(card, p)} (thẻ anh nhập {ts.ddmm(card['day'])}: "
                f"{ts.card_levels(card)}; tham khảo, không phải lệnh)")

    @classmethod
    def _bot_zone_line(cls, account, symbol: str, row: dict, seen: dict, day: str, price_of) -> str | None:
        """The alert of the bot's card of one stock (a ``dinhgia`` row), if any; ``seen`` updated in place."""
        from vnbot import dinhgia
        from vnbot import taisan as ts

        p = price_of(symbol)
        if not p:
            return None
        zone = cls._entered(account, symbol, row, seen, day, p, "bot")
        if zone is None:
            return None
        weak = ", tin cậy THẤP" if row.get("reliability") == "thấp" else ""
        return (f"{cls._what(symbol, zone, row, p)}, {ts.mos_text(row, p)} (thẻ của bot{weak}, BCTC {row.get('period')}, "
                f"tính sau phiên {dinhgia.ddmm(row.get('price_day') or '')}: {ts.card_levels(row)}; tham khảo, không phải "
                f"lệnh; /dinhgia {symbol})")

    def _ticket_reminders(self, day: str, now) -> list[tuple[str, str]]:
        """The reminder of an exit ticket due today (``kyluat.ticket_reminders``), [] outside
        its window or when the portfolio is not there."""
        account = getattr(self.trader, "taisan", None)
        if account is None:
            return []
        from vnbot import kyluat

        try:
            return kyluat.ticket_reminders(account, day, now)
        except Exception as exc:  # noqa: BLE001 - the price alerts still go out
            logger.warning("Tài sản: ticket reminders: %s", exc)
            return []

    def remind(self, day: str, now=None) -> list[str]:
        """The ticket reminders alone (no price read), sent and returned."""
        from tradingagents.market import vn_rules

        now = now or vn_rules.now_vn()
        sent = self._sent(day)
        new = [(k, text) for k, text in self._ticket_reminders(day, now) if k not in sent]
        if new:
            at = now.strftime("%H:%M")
            self._remember(day, sent | {k for k, _ in new}, [text for _, text in new], at)
            self.notify("\n".join(text for _, text in new))
        return [text for _, text in new]

    def _keep_taisan_alerts(self, new, prices, day) -> None:
        """The Tài sản cut-loss and target alerts go to its shadow results (judged later)."""
        account = getattr(self.trader, "taisan", None)
        if account is None:
            return
        for key, _ in new:
            kind, _, rest = key.partition(":")
            label, _, symbol = rest.rpartition(":")
            if label == TAISAN_LABEL and kind in ("stop", "t1", "t2"):
                try:
                    account.record_alert("stop" if kind == "stop" else "target", symbol, prices.get(symbol), day)
                except Exception as exc:  # noqa: BLE001 - the alert is sent anyway
                    logger.warning("Tài sản: alert of %s not kept: %s", symbol, exc)

    def tick(self) -> None:
        from tradingagents.market import vn_rules
        from vnbot import kyluat

        now = vn_rules.now_vn()
        phase = vn_rules.session_phase(now)
        if phase in (*ACTIVE_PHASES, "atc") and kyluat.TICKET_REMIND_FROM <= now.strftime("%H:%M") < kyluat.TICKET_REMIND_UNTIL:
            self.remind(now.strftime("%Y-%m-%d"), now)      # once a ticket; not bound to the interval
        if phase not in ACTIVE_PHASES:
            return
        on = self.switches.on("intraday") if self.switches is not None else self.settings.intraday_alerts
        if not on:
            return
        if time.time() - self._last_check < self.settings.intraday_interval_min * 60:
            return
        self._last_check = time.time()
        self.check(now.strftime("%Y-%m-%d"))

    def _loop(self) -> None:
        while True:
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - the monitor must keep ticking
                logger.exception("Intraday check failed")
            time.sleep(30)
