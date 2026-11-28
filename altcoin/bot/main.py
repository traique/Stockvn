"""Vòng lặp chính của bot."""
import logging
import os
import signal
import sys
import threading
import time
import datetime as dt
import traceback

from .config import cfg, ENV_FILE_INFO
from .version import VERSION, BUILD
from . import version as ver
from .channels import Switches
from .learner import current_veto_conf
from .exchange import OKX
from .storage import Store
from .ai import AIFilter
from .engine import PaperEngine, LiveEngine, critical, stamp
from .risk import CircuitBreaker
from .strategy import diagnose
from collections import Counter
from .telegram import Telegram
from .notify import Notifier, Zoom
from .dashboard import start_dashboard
from .learner import Learner
from . import eventlog
from . import market as mkt
from . import strategy as strat
from . import books
from .grid import GridRunner
from .sheets import SheetsExporter

logging.basicConfig(
    level=getattr(logging, cfg.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("main")


def learned_changes(L):
    """Tham số tự học đang khác mặc định, dạng "k=v, ..." (hoặc "chưa có") cho log khởi động. Chỉ các tham số
    (Learner.DEFAULTS) – "schema" là phiên bản định dạng, không phải điều bot học được (bản cũ in "schema=3" trên DB mới
    như thể đã học)."""
    return ", ".join(f"{k}={L.get(k)}" for k in Learner.DEFAULTS if L.get(k) not in (None, [], {})) or "chưa có"


class Bot:
    noai = None          # sổ không AI (v3.5, books.py) – None khi NOAI_ENABLED=false (test dựng Bot bằng __new__ cũng không có)
    period = None        # đợt đo (kv "period")
    grid = None          # sổ Grid spot (v3.5, grid.py) – luồng riêng, chỉ API công khai
    sheets = None        # bản sao lên Google Sheets (v3.5, sheets.py) – luồng riêng, tắt khi thiếu biến
    _noai_map = {}

    def __init__(self):
        self.started_at = time.time()
        self.store = Store()
        self.switches = Switches(self.store)
        eventlog.install(self.store)   # mọi log INFO trở lên được ghi vào DB → xem trên web, tải về
        self.learner = None
        self.is_leader = False
        self.instance = cfg.INSTANCE_NAME
        self.last_prices = {}
        self.ai = None
        start_dashboard(self)          # mở cổng web ngay lập tức để host không báo "not responding"
        self.okx = OKX()
        self.tg = Telegram(self)
        self.zoom = Zoom(self)
        self.notify = Notifier(self)
        self.notify.add(self.tg)
        self.notify.add(self.zoom)
        self.ai = AIFilter(self.store)
        engine_cls = LiveEngine if cfg.MODE == "live" else PaperEngine
        self.engine = engine_cls(self.okx, self.store, self.notify.send)
        self.engine.on_close = self._on_trade_closed
        self.learner = Learner(self.store, self.okx, self.ai, self.notify.send)
        self.shadow_seen = {}   # (symbol, side) -> ts: tránh ghi trùng tín hiệu bóng
        self.last_btc = {}
        self.breaker = CircuitBreaker(self.store)
        self.paused = bool(self.store.get("paused", False))
        self.last_scan = 0
        self.last_scan_summary = self.store.get("last_scan")
        self.last_prices = {}
        self.cooldown = {}   # symbol -> time cho phép vào lại
        self.start_equity = self.store.get("start_equity")
        if self.start_equity is None:
            self.start_equity = self.engine.equity()
            self.store.set("start_equity", self.start_equity)
            self.store.set("peak_equity", self.start_equity)
        # v3.5: sổ "không AI" chạy ảo song song (books.py) – luôn PaperEngine, không gửi tin từng lệnh
        self.noai = None
        if cfg.NOAI_ENABLED:
            self.noai = PaperEngine(self.okx, self.store, self.notify.send, book=books.NOAI, quiet=True,
                                    capital=cfg.NOAI_CAPITAL)
        self._noai_map = {}      # id(sig) -> id lệnh noai vừa mở trong lượt quét này (để gắn sid & phán quyết AI)
        self.grid = GridRunner(self.okx, self.store, self.notify.send, active=lambda: self.is_leader)
        self.sheets = SheetsExporter(self)
        # v3.5: đợt đo – ghi lúc bắt đầu (lần khởi động đầu tiên của bản có sổ noai); mốc đỉnh cầu dao của sổ chính đặt lại
        # về vốn lúc này để hai sổ cùng điều kiện (peak_equity cũ là đỉnh trước đợt sụt 19,3% – README 3.4.0)
        self.period = self.store.get("period")
        if self.period is None:
            eq = self.engine.equity()
            self.period = {"ts": time.time(), "equity": eq, "version": VERSION,
                           "noai_equity": self.noai.equity() if self.noai else None}
            self.store.set("period", self.period)
            self.store.set("peak_equity", eq)
            if self.noai:
                self.store.set("noai_peak_equity", self.period["noai_equity"])
            log.info("Bắt đầu đợt đo (v%s): vốn sổ chính %.4f USDT, mốc đỉnh cầu dao đặt lại; sổ noai %s", VERSION, eq,
                     f"{self.period['noai_equity']:.4f} USDT" if self.noai else "tắt")

    # ------------------------------------------------------------ AI tổng kết sau khi đóng lệnh (lần gọi thứ 2)
    def _on_trade_closed(self, t, exit_, pnl, pnl_pct, reason):
        def _run():
            rev = self.ai.review(t, exit_, pnl, pnl_pct, reason)
            if rev:
                self.store.add_review(t, pnl, rev)
                self.notify.send(f"🧠 TỔNG KẾT {t['side'].upper()} {t['symbol']} [{rev['verdict']}]\n"
                             f"{rev['lesson']}\n#" + " #".join(rev.get("tags") or [])
                             + (f"\n⚠️ {rev['note']}" if rev.get("note") else ""))
        threading.Thread(target=_run, daemon=True, name="ai-review").start()

    # ------------------------------------------------------------ text cho Telegram
    def status_text(self):
        eq = self.engine.equity(self.last_prices)
        s = self.store.stats()
        opens = self.store.open_trades()
        peers = ", ".join(f"{i['instance']}={i['role']}" + ("" if time.time() - i['ts'] < cfg.LEADER_TTL else "(mất liên lạc)")
                          for i in self.store.instances())
        lines = [f"📊 v{VERSION} ({ver.build()}) · {cfg.MODE.upper()} | {'TẠM DỪNG' if self.paused else 'ĐANG CHẠY'} | leader: {self.store.lock_owner('trader') or '-'}",
                 f"Instances: {peers}",
                 f"Vốn: {eq:.4f} USDT (bắt đầu {self.start_equity:.2f}, {100*(eq-self.start_equity)/self.start_equity:+.1f}%)",
                 f"Lệnh: {s['trades']} | Win {s['winrate']}% | PF {s['profit_factor']}",
                 f"AI: {self.ai.last_model or '-'} | hôm nay {self.ai.calls_today()}/{cfg.AI_MAX_CALLS_PER_DAY} lần | lỗi {self.ai.stats['fails']}"]
        ls = self.last_scan_summary
        if self.noai:
            neq = self.noai.equity(self.last_prices)
            n_open = len(self.store.open_trades(books.NOAI))
            lines.append(f"Sổ không AI: {neq:.4f} USDT, {n_open} lệnh mở"
                         + (" – ĐÃ DỪNG (cầu dao)" if self.store.get("noai_halted") else "") + " · /compare để so sánh")
        if getattr(self, "last_btc", None):
            lines.append("₿ " + mkt.describe(self.last_btc))
        if ls:
            lines.append(f"Quét cuối {dt.datetime.fromtimestamp(ls['ts']).strftime('%H:%M')}: {ls['scanned']} coin, {ls['passed']} đạt"
                         + (f" – đủ {ls['n_open']} lệnh, chỉ ghi bóng" if ls.get("n_open") else "") + " | "
                         + ", ".join(f"{k} {v}" for k, v in list(ls['reasons'].items())[:4]))
        for t in opens:
            p = self.last_prices.get(t["symbol"])
            upnl = self.engine._pnl(t["side"], t["entry"], p, t["notional"])[0] if p else 0
            lines.append(f"• {t['side'].upper()} {t['symbol']} @ {t['entry']:.6g} SL {t['sl']:.6g} TP {t['tp']:.6g} uPnL {upnl:+.4f}")
        return "\n".join(lines)

    def trades_text(self):
        rows = [t for t in self.store.trades(10) if t["status"] == "closed"]
        if not rows:
            return "Chưa có lệnh nào đóng."
        return "\n".join(f"{'✅' if t['pnl']>0 else '❌'} {t['side'].upper()} {t['symbol']} {t['pnl']:+.4f} ({t['pnl_pct']:+.0f}%) – {t['reason_close']}" for t in rows)

    def signals_text(self):
        rows = self.store.recent_signals(10)
        if not rows:
            return "Chưa có tín hiệu."
        return "\n".join(f"{dt.datetime.fromtimestamp(r['ts']).strftime('%d/%m %H:%M')} {r['symbol']} {r['side']} {r['score']} → AI {r['ai_decision']} {r['ai_conf'] or ''}% {'✅' if r['taken'] else ''}" for r in rows)

    def ai_text(self):
        st = self.ai.public_stats()
        lines = [f"🤖 AI: {'bật' if st['enabled'] else 'tắt'} | thứ tự {', '.join(st['order'])} | hôm nay {st['calls_today']}/{st['budget']}"]
        for x in st.get("shared") or []:
            lines.append(f"↔ {x['provider']} dùng chung khoá với {', '.join(x['with'])}: {x['calls']}"
                         + (f"/{x['cap']}" if x['cap'] else "") + " lượt hôm nay (cả hai bot)"
                         + (f" – {x['rest']}" if x['rest'] else ""))
        for m in st["models"][:10]:
            real = m.get("last_served")
            real = f" → {real}" if real and real != m["model"].split("/", 1)[-1] else ""
            lines.append(f"• {m['model']}{real}: {m['calls']} gọi, {m['ok']} ok, {m['fail']} lỗi"
                         + (f", ~{m['avg_ms']/1000:.1f}s" if m['avg_ms'] else "") + (" (đang nghỉ)" if m['cooldown'] else ""))
        if st.get("last_result"):
            r = st["last_result"]
            real = r.get("served")
            real = f" → {real}" if real and real != r["model"].split("/", 1)[-1] else ""
            lines.append(f"Gần nhất: {r['side'].upper()} {r['symbol']} → {r['decision']} {r['confidence']}% ({r['model']}{real})\n{r['reason']}")
        return "\n".join(lines)

    def lessons_text(self):
        rows = self.store.reviews(8)
        if not rows:
            return "Chưa có tổng kết nào."
        return "\n\n".join(f"{'✅' if r['pnl']>0 else '❌'} {r['side'].upper()} {r['symbol']}"
                           + (f" {r['r']:+.2f}R" if r.get("r") is not None else "") + f" [{r['verdict']}]"
                           + ("" if r.get("consistent", True) else " ⚠️ không khớp kết quả – không dùng làm bài học")
                           + f"\n{r['lesson']}" for r in rows)

    HELP = ("Lệnh (Telegram gõ /lenh, Zoom gõ /okxbot lenh):\n"
            "status – vốn, lệnh mở, quét gần nhất\ntrades – 10 lệnh gần nhất\nsignals – tín hiệu & quyết định AI\n"
            "ai – lượt gọi từng model\nlearn – trạng thái tự học\napply – duyệt đề xuất tự học\n"
            "compare – so sánh sổ chính (có AI) với sổ không AI\ngrid – sổ Grid spot chạy ảo (chỉ đọc)\n"
            "sheets – trạng thái ghi Google Sheets\n"
            "lessons – bài học AI\nbtc – bối cảnh BTC\npause / resume – tạm dừng / tiếp tục\nclose – đóng tất cả lệnh\n"
            "phienban – phiên bản & lịch sử cập nhật\nzoomtest – gửi thử thông báo Zoom\n"
            "zoompair <jid> / zoomxoa / zoomstatus – ghép đôi Zoom (chỉ trên Telegram)")

    def handle_command(self, text, source="Telegram"):
        """Xử lý lệnh chung cho mọi kênh. Trả về chuỗi trả lời."""
        cmd = (text.strip().split() or [""])[0].lower().lstrip("/").split("@")[0]
        if cmd == "status":
            return self.status_text()
        if cmd == "pause":
            self.paused = True
            self.store.set("paused", True)
            return f"⏸ Bot tạm dừng mở lệnh mới (lệnh đang mở vẫn được quản lý). – qua {source}"
        if cmd == "resume":
            self.paused = False
            self.store.set("paused", False)
            self.store.set("halted", False)
            self.store.set("noai_halted", False)
            return f"▶️ Bot tiếp tục giao dịch. – qua {source}"
        if cmd == "compare":
            return books.compare_text(self.compare())
        if cmd == "sheets":
            if not self.sheets or not self.sheets.enabled:
                return "Ghi Google Sheets đang tắt (thiếu SHEETS_ID / SHEETS_SA_JSON" + (f": {self.sheets.last_error}" if self.sheets and self.sheets.last_error else "") + ")."
            st = self.sheets.state()
            return ("📄 Google Sheets: " + ("ghi thành công lần cuối " + dt.datetime.fromtimestamp(st["last_ok"]).strftime("%d/%m %H:%M") if st["last_ok"] else "chưa ghi lần nào")
                    + (f"\n⚠️ lỗi gần nhất: {st['last_error']}" if st["last_error"] else "")
                    + ("\nĐã ghi: " + ", ".join(f"{k} {v}" for k, v in st["written"].items()) if st["written"] else ""))
        if cmd == "grid":
            if not cfg.GRID_ENABLED:
                return "Sổ Grid đang tắt (GRID_ENABLED=false; trong Stockvn: ALT_GRID_ENABLED=true rồi khởi động lại)."
            return self.grid.text() if self.grid else "Grid chưa khởi động."
        if cmd == "trades":
            return self.trades_text()
        if cmd == "close":
            return f"Đã đóng {self.close_all(f'Đóng thủ công qua {source}')} lệnh."
        if cmd == "signals":
            return self.signals_text()
        if cmd == "ai":
            return self.ai_text()
        if cmd == "learn":
            return self.learn_text()
        if cmd == "apply":
            n = self.learner.apply_pending()
            return f"Đã áp dụng {n} đề xuất tự học." if n else "Không có đề xuất nào đang chờ."
        if cmd == "lessons":
            return self.lessons_text()
        if cmd in ("zoompair", "zoomxoa", "zoomstatus"):
            if source == "Zoom":
                return "Lệnh ghép đôi Zoom chỉ dùng được từ Telegram."
            z = self.zoom
            if not z.chatbot_on:
                return "Chưa cấu hình Zoom chatbot (ZOOM_CLIENT_ID / ZOOM_CLIENT_SECRET / ZOOM_BOT_JID)."
            if cmd == "zoomstatus":
                p = z.pairing()
                return f"Zoom đang ghép đôi: {p.get('name') or ''} {p['jid']}" if p.get("jid") else "Chưa ghép đôi JID Zoom nào."
            if cmd == "zoomxoa":
                z.unpair()
                return "Đã gỡ ghép đôi Zoom."
            parts = text.strip().split(maxsplit=2)
            if len(parts) < 2:
                return "Cú pháp: /zoompair <jid> [tên]. JID lấy từ tin báo khi anh nhắn bot trên Zoom lần đầu."
            p = z.pair(parts[1], parts[2] if len(parts) > 2 else "")
            return f"✅ Đã ghép đôi Zoom: {p['jid']}. Từ giờ anh gõ lệnh và nhận thông báo trên Zoom."
        if cmd in ("phienban", "version"):
            return ver.text()
        if cmd == "zoomtest":
            ok, tried = self.zoom.test_send()
            return ("✅ Zoom nhận thông báo thử. " if ok else "❌ Zoom chưa nhận thông báo. ") + "Đã thử: " + "; ".join(tried)
        if cmd == "btc":
            return "₿ " + (mkt.describe(self.last_btc) or "Chưa có dữ liệu BTC (chờ lượt quét đầu).")
        return self.HELP

    def compare(self):
        """So sánh sổ chính và sổ noai trong đợt đo (books.compare) – trang web, /compare, báo cáo."""
        eqs = {books.MAIN: self.engine.equity(self.last_prices)}
        starts = {books.MAIN: (self.period or {}).get("equity") or self.start_equity}
        halted = {books.MAIN: bool(self.store.get("halted"))}
        if self.noai:
            eqs[books.NOAI] = self.noai.equity(self.last_prices)
            starts[books.NOAI] = (self.period or {}).get("noai_equity") or cfg.NOAI_CAPITAL
            halted[books.NOAI] = bool(self.store.get("noai_halted"))
        return books.compare(self.store, self.period, eqs, starts, halted)

    def close_all(self, reason):
        n = 0
        with critical():        # cả lượt là một vùng: tắt bot thì chờ đóng hết, không chỉ lệnh đang đóng
            for t in self.store.open_trades():
                try:
                    if self.engine.close(t, self.okx.price(t["symbol"]), reason) is not False:
                        n += 1      # live: lệnh đóng lỗi → lệnh vẫn mở, bot thử lại ở lượt kiểm tra sau và đã báo
                except Exception as e:
                    log.error("close_all %s: %s", t["symbol"], e)
        return n

    def snapshot(self):
        if self.ai is None:
            return {"mode": cfg.MODE, "paused": False, "halted": False, "instance": self.instance, "is_leader": False,
                    "leader": None, "instances": [], "shared_db": self.store.pg, "equity": 0, "start": 0,
                    "stats": self.store.stats(), "curve": [], "open": [], "trades": [], "signals": [],
                    "ai_model": None, "ai_calls_today": 0, "ai_budget": cfg.AI_MAX_CALLS_PER_DAY,
                    "ai": {"enabled": False}, "reviews": [], "tz": cfg.TIMEZONE, "ts": time.time(), "booting": True,
                    "learn": None, "last_scan": None, "version": VERSION, "build": BUILD}
        opens = self.store.open_trades()
        for t in opens:
            p = self.last_prices.get(t["symbol"])
            t["price"] = p
            t["upnl"] = self.engine._pnl(t["side"], t["entry"], p, t["notional"])[0] if p else None
        return {
            "mode": cfg.MODE, "paused": self.paused, "halted": bool(self.store.get("halted")),
            "instance": self.instance, "is_leader": self.is_leader, "leader": self.store.lock_owner("trader"),
            "instances": self.store.instances(), "shared_db": self.store.pg,
            "equity": self.engine.equity(self.last_prices), "start": self.start_equity,
            "stats": self.store.stats(), "curve": self.store.equity_curve(),
            "open": opens, "trades": self.store.trades(100), "signals": self.store.recent_signals(40),
            "ai_model": self.ai.last_model, "ai_calls_today": self.ai.calls_today(),
            "ai_budget": cfg.AI_MAX_CALLS_PER_DAY, "ai": self.ai.public_stats(),
            "reviews": self.store.reviews(30), "tz": cfg.TIMEZONE, "ts": time.time(),
            "last_scan": self.last_scan_summary,
            "learn": self.learner.public_state() if self.learner else None,
            "btc": getattr(self, "last_btc", {}),
            "version": VERSION, "build": BUILD, "started": self.started_at, "ver": ver.info(),
            "switches": self.switches.all(),
            "books": self._safe(self.compare), "period": self.period,
            "grid": {"enabled": cfg.GRID_ENABLED, "pairs": cfg.GRID_PAIRS, "round": cfg.GRID_ROUND,
                     "runs": self._safe(self.grid.snapshot, []) if (self.grid and cfg.GRID_ENABLED) else []},
            "sheets": self.sheets.state() if self.sheets else {"enabled": False},
            "noai": {"enabled": bool(self.noai), "open": self.store.open_trades(books.NOAI) if self.noai else [],
                     "trades": self.store.trades(50, books.NOAI) if self.noai else [],
                     "curve": self.store.equity_curve(book=books.NOAI) if self.noai else []},
        }

    @staticmethod
    def _safe(fn, default=None):
        try:
            return fn()
        except Exception as e:
            log.debug("%s: %s", getattr(fn, "__name__", fn), e)
            return default

    # ------------------------------------------------------------ logic
    def _day_key(self):
        return dt.datetime.now().strftime("%Y-%m-%d")

    def _risk_ok(self, equity):
        day = self._day_key()
        if self.store.get("day_key") != day:
            self.store.set("day_key", day)
            self.store.set("day_start_equity", equity)
            self.store.set("halted", False)
            self._daily_report()
        peak = max(float(self.store.get("peak_equity") or equity), equity)
        self.store.set("peak_equity", peak)
        ok, why = self.breaker.check(equity, float(self.store.get("day_start_equity") or equity), peak)
        if not ok and not self.store.get("halted"):
            self.store.set("halted", True)
            self.notify.send(f"🛑 DỪNG GIAO DỊCH: {why}. Gõ /resume để chạy lại.")
        return ok and not self.store.get("halted")

    def _noai_risk_ok(self):
        """Cầu dao riêng của sổ noai: sụt MAX_DRAWDOWN từ đỉnh (đỉnh đo từ đầu đợt) → ngừng mở lệnh mới, ghi sự kiện,
        báo MỘT lần; lệnh đang mở vẫn được quản lý; sổ chính không ảnh hưởng. /resume mở lại."""
        eq = self.noai.equity(self.last_prices)
        peak = max(float(self.store.get("noai_peak_equity") or eq), eq)
        self.store.set("noai_peak_equity", peak)
        if self.store.get("noai_halted"):
            return False
        if peak > 0 and (peak - eq) / peak >= cfg.MAX_DRAWDOWN:
            self.store.set("noai_halted", True)
            log.warning("[noai] cầu dao: vốn %.4f sụt %.1f%% từ đỉnh %.4f – sổ noai ngừng mở lệnh mới", eq,
                        100 * (peak - eq) / peak, peak)
            self.notify.send(f"🛑 Sổ không AI dừng mở lệnh mới: sụt {100 * (peak - eq) / peak:.1f}% từ đỉnh "
                             f"({eq:.4f} / {peak:.4f} USDT). Sổ chính không ảnh hưởng. /resume mở lại.")
            return False
        return True

    def _noai_pass(self, candidates):
        """Sổ noai: vào ứng viên điểm cao nhất chưa có vị thế cùng mã, không hỏi AI, tối đa 1 lệnh mỗi lượt quét (như sổ
        chính), thử tối đa AI_CANDIDATES_PER_SCAN ứng viên (như sổ chính hỏi AI lần lượt) khi ứng viên trước không vào
        được (giá đã vượt SL, ký quỹ, lot). Chạy TRƯỚC khi sổ chính hỏi AI; giá last lúc quyết định ghi một lần trên tín
        hiệu (engine.stamp) nên hai sổ vào cùng giá. Trả {id(sig): id lệnh} để gắn sid & phán quyết AI sau."""
        out = {}
        if not self.noai or not candidates or self.paused:
            return out
        try:
            if not self._noai_risk_ok():
                return out
            opens = self.store.open_trades(books.NOAI)
            if len(opens) >= cfg.MAX_OPEN_POSITIONS:
                return out
            held = {t["symbol"] for t in opens}
        except Exception as e:
            log.warning("[noai] không đọc được trạng thái sổ: %s", str(e)[:160])
            return out
        tried = 0
        for sig in candidates:
            if sig.symbol in held:
                continue
            if tried >= max(1, cfg.AI_CANDIDATES_PER_SCAN):
                break
            tried += 1
            skip0, ref0 = sig.skip, (sig.ref_price, sig.ref_ts)
            try:
                t = self.noai.open(sig, books.noai_ai(sig))
            except Exception as e:
                log.warning("[noai] mở %s lỗi: %s", sig.symbol, str(e)[:160])
                t = None
            # lý do bỏ và giá last của sổ noai không lẫn vào sổ chính: sổ chính lấy giá MỚI sau khi AI trả lời (như trước
            # 3.5 – độ trễ của AI là một phần chi phí của AI), dòng tín hiệu cũng ghi giá đó
            sig.skip, (sig.ref_price, sig.ref_ts) = skip0, ref0
            if t:
                log.info("[noai] vào %s %s điểm %s: %.2f USDT @ %.6g SL %.6g", sig.symbol, sig.side, sig.score,
                         t["notional"], t["entry"], t["sl"])
                out[id(sig)] = t["id"]
                break
        return out

    def _link_books(self, sig, sid, ai, taken):
        """Gắn sid của dòng tín hiệu vào lệnh của hai sổ; lệnh noai ghi thêm phán quyết AI của sổ chính (chỉ để phân tích).
        sid trống (tín hiệu đã có dòng từ lượt quét trước trong cùng nến – _shadow / log_signal không ghi lại) → dùng sid của
        dòng đã có (storage.signal_sid); lượt sau sổ chính hỏi AI về đúng tín hiệu đó thì log_signal chuyển sid của lệnh
        sang dòng mới (dòng bóng cũ thành DUP) – cặp noai / main vẫn khớp nhau."""
        if not sid:
            try:
                sid = self.store.signal_sid(sig)
            except Exception as e:
                log.debug("signal_sid: %s", e)
        if not sid:
            return
        try:
            if taken is not None:
                self.store.update_trade(taken["id"], sid=sid)
            tid = self._noai_map.get(id(sig))
            if tid:
                if ai is None:
                    verdict = "AI không trả lời"
                elif ai.get("fallback"):
                    verdict = f"AI lỗi – vào theo điểm ({ai.get('confidence')}%)"
                else:
                    verdict = f"{ai.get('decision')} {ai.get('confidence')}%"
                self.store.update_trade(tid, sid=sid, ai_verdict=verdict)
        except Exception as e:
            log.debug("link books: %s", e)

    def _daily_report(self):
        since = time.time() - 86400
        rows = self.store.closed_trades_since(since)
        if not rows:
            return
        pnl = sum(r["pnl"] for r in rows)
        wins = sum(1 for r in rows if r["pnl"] > 0)
        extra = ""
        if self.noai:
            nrows = self.store.closed_trades_since(since, books.NOAI)
            extra = (f"\nSổ không AI: {len(nrows)} lệnh, PnL {sum(r['pnl'] for r in nrows):+.4f} USDT, "
                     f"vốn {self.noai.equity(self.last_prices):.4f} USDT")
        self.notify.send(f"📅 BÁO CÁO NGÀY\nLệnh: {len(rows)} | Thắng: {wins} | PnL: {pnl:+.4f} USDT\n"
                     f"Vốn: {self.engine.equity(self.last_prices):.4f} USDT" + extra)

    def scan(self, main_active=True):
        """Quét coin, chấm tín hiệu, hỏi AI tối đa AI_CANDIDATES_PER_SCAN ứng viên (vào được 1 lệnh thì dừng).
        v3.5: main_active=False – sổ chính đã dừng (cầu dao) nhưng sổ noai còn chạy: quét chỉ để sổ noai nhận ứng viên; sổ
        chính không hỏi AI, không vào lệnh và không ghi tín hiệu bóng (như trước: bot dừng thì không có dòng nào).
        v3.3: đủ MAX_OPEN_POSITIONS lệnh mở vẫn quét (bản cũ ngừng im lặng – mất log quét và dữ liệu bóng ~9% thời gian):
        không hỏi AI, không vào lệnh, ứng viên đạt được ghi bóng nguồn "max_positions". Ứng viên xếp sau ứng viên vừa vào
        lệnh (bản cũ bỏ mất, không hỏi AI cũng không ghi) được ghi bóng nguồn "slot_taken". Lượt quét sau hỏi AI về đúng
        tín hiệu đó thì dòng bóng cũ được đánh dấu trùng (storage.log_signal) – bộ tự học đếm mỗi tín hiệu một lần.
        Đang có lệnh mở: lượt quét (~30 giây, 50 coin) xen engine.monitor() mỗi MONITOR_INTERVAL – SL/TP1/trailing không
        bị trễ trong lúc quét (bản cũ đủ lệnh thì không quét nên không trễ)."""
        opens = self.store.open_trades()
        full = len(opens) >= cfg.MAX_OPEN_POSITIONS
        open_syms = {t["symbol"] for t in opens}
        last_mon = [time.time()]
        self._noai_map = {}
        noai_open = self._noai_open

        def keep_monitoring():
            if (opens or noai_open) and time.time() - last_mon[0] >= cfg.MONITOR_INTERVAL:
                last_mon[0] = time.time()
                try:
                    self.engine.monitor()
                except Exception as e:
                    log.warning("quản lý lệnh mở giữa lượt quét lỗi: %s", str(e)[:160])
                self._monitor_noai()
        coins = self.okx.top_altcoins(cfg.TOP_N_COINS)
        btc = mkt.btc_context(self.okx)
        self.last_btc = btc
        log.info("Quét %d coin... %s%s", len(coins), mkt.describe(btc),
                 f" | đủ {len(opens)} lệnh mở – chỉ ghi tín hiệu bóng, không hỏi AI" if full else "")
        candidates, reasons, near = [], Counter(), []
        for sym, vol, chg in coins:
            keep_monitoring()
            if sym in open_syms or self.cooldown.get(sym, 0) > time.time():
                continue
            try:
                ltf = self.okx.ohlcv(sym, cfg.TIMEFRAME, 220)
                htf = self.okx.ohlcv(sym, cfg.HTF_TIMEFRAME, 120)
                sig, why, info = diagnose(sym, ltf, htf, funding=0.0, change24h=float(chg or 0), market=btc)
                min_score = strat.LEARNED.get("min_score") or cfg.MIN_SCORE
                if why == strat.BREAKOUT_OFF:
                    # v3.3: chỉ có trigger breakout, cờ tắt – không hỏi AI, không vào lệnh. Khi bật sẽ là ứng viên (qua
                    # ngưỡng điểm sau dòng tiền) → ghi bóng nguồn riêng để đo ngoài mẫu (bộ tự học không dùng nguồn này)
                    reasons[why.split("(")[0].strip()] += 1
                    near.append(f"{sym.split('/')[0]} ({why[:30]})")
                    if sig is not None and sig.score >= min_score - 1.0:
                        sig.context.update(mkt.symbol_context(self.okx, sym))
                        if not strat.apply_flow(sig):
                            sig.rejected = strat.BREAKOUT_OFF
                            self._shadow(sig, strat.BREAKOUT_OFF_SOURCE)
                    continue
                if sig is not None and (not why or why.startswith("điểm thấp")) and sig.score >= min_score - 1.0:
                    # qua cổng + có trigger → lấy dòng tiền (OI / long-short / funding) và cộng/trừ điểm
                    # TRƯỚC khi xét ngưỡng & xếp hạng → ứng viên gửi AI tốt cả kỹ thuật lẫn dòng tiền
                    sig.context.update(mkt.symbol_context(self.okx, sym))
                    why = strat.apply_flow(sig)
                    info["score"] = sig.score
                if sig and not why:
                    rule = strat.blocking_rule(sig)
                    if rule:
                        # v3.3: ghi đúng nguồn gốc luật – bản cũ luôn ghi "luật tự học" dù hầu hết là luật chặn sẵn
                        origin = strat.block_origin(rule)
                        label = strat.BLOCK_LABELS[origin]
                        sig.rejected = f"{label}: {strat.RULES[rule][0]}"
                        sig.context.update(blocked_rule=rule, block_origin=origin)
                        reasons[f"{label} chặn"] += 1
                        log.info("%s %s bị %s chặn: %s", sym, sig.side, label, strat.RULES[rule][0])
                        self._shadow(sig, "blocked")
                        continue
                    candidates.append(sig)
                else:
                    if sig is not None:   # suýt đạt (điểm thấp) → ghi lại để theo dõi kết quả bóng
                        self._shadow(sig, "below_score")
                    reasons[why.split("(")[0].strip()] += 1
                    if "trigger" in why or "điểm thấp" in why:
                        near.append(f"{sym.split('/')[0]} ({why[:30]})")
            except Exception as e:
                log.warning("phân tích %s lỗi: %s", sym, e)
        candidates.sort(key=lambda s: s.score, reverse=True)
        summary = ", ".join(f"{k} {v}" for k, v in reasons.most_common(6))
        log.info("Kết quả quét: %d đạt | loại vì: %s%s", len(candidates), summary,
                 (" | suýt đạt: " + ", ".join(near[:4])) if near else "")
        if candidates:
            log.info("Tín hiệu: %s", ", ".join(f"{s.symbol} {s.side} {s.score}" for s in candidates[:5]))
        self.last_scan_summary = {"ts": time.time(), "scanned": len(coins), "passed": len(candidates),
                                  "reasons": dict(reasons.most_common(8)), "near": near[:6],
                                  "top": [f"{s.symbol.split('/')[0]} {s.side} {s.score}" for s in candidates[:3]],
                                  "n_open": len(opens) if full else 0}
        self.store.set("last_scan", self.last_scan_summary)
        try:
            self.store.add_scan(self.last_scan_summary)
        except Exception as e:
            log.debug("add_scan: %s", e)
        # v3.5: sổ noai nhận đúng danh sách ứng viên này TRƯỚC khi sổ chính hỏi AI (books.py)
        self._noai_map = self._noai_pass(candidates)
        if not main_active:
            for sig in candidates:
                self._link_books(sig, None, None, None)      # sid của dòng đã có (nếu có) cho lệnh noai
            return
        if full:                            # đủ lệnh mở: không hỏi AI, không vào lệnh – chỉ theo dõi kết quả bóng
            for sig in candidates:
                self._link_books(sig, self._shadow(sig, "max_positions"), None, None)
            return
        k = max(1, cfg.AI_CANDIDATES_PER_SCAN)
        for sig in candidates[k:]:          # đạt nhưng xếp sau → không hỏi AI, vẫn theo dõi kết quả
            self._link_books(sig, self._shadow(sig, "not_sent"), None, None)

        for i, sig in enumerate(candidates[:k]):   # hỏi AI lần lượt tối đa k ứng viên tốt nhất (điểm cao trước); vào được 1 lệnh thì dừng
            keep_monitoring()
            log.info("Gửi AI: %s %s điểm %s | %s", sig.symbol, sig.side, sig.score, mkt.describe(sig.context))
            ai = self.ai.analyze(sig)
            taken = False
            if ai is None:
                policy = cfg.AI_FAIL_POLICY or ("score" if cfg.MODE == "paper" else "skip")
                if policy == "score" and sig.score >= cfg.AI_FAIL_MIN_SCORE:
                    log.warning("AI không trả lời (%s) – điểm KT %s ≥ %s nên vẫn vào lệnh %s",
                                self.ai.last_error, sig.score, cfg.AI_FAIL_MIN_SCORE, sig.symbol)
                    ai = {"decision": sig.side.upper(), "confidence": 0, "model": "fallback-kt",
                          "reason": f"AI lỗi – vào theo điểm kỹ thuật {sig.score}", "fallback": True}
                else:
                    log.warning("AI không trả lời (%s) – bỏ qua tín hiệu %s (điểm %s, cần ≥ %s để vào khi AI lỗi%s)",
                                self.ai.last_error, sig.symbol, sig.score, cfg.AI_FAIL_MIN_SCORE,
                                "" if policy == "score" else ", AI_FAIL_POLICY=skip")
            if ai is None:
                pass
            elif ai.get("fallback"):
                if strat.rr_ok(sig):
                    t = self.engine.open(sig, ai)
                    taken = t is not None
            elif ai["decision"] == sig.side.upper() and ai["confidence"] >= cfg.AI_MIN_CONFIDENCE:
                # SL/TP giữ nguyên theo cấu trúc ATR của strategy – AI không được chỉnh
                if strat.rr_ok(sig):
                    t = self.engine.open(sig, ai)
                    taken = t is not None
            elif ai["decision"] == sig.side.upper():
                # AI cùng hướng nhưng chưa chắc: ≥ AI_WEAK_CONFIDENCE → vẫn vào với rủi ro giảm (risk.quality_mult),
                # thấp hơn nữa mới bỏ. (v3.1: trên dữ liệu đã có, AI chọn lọc không tốt hơn ngẫu nhiên → bỏ lệnh = bỏ lãi)
                if ai["confidence"] >= cfg.AI_WEAK_CONFIDENCE and strat.rr_ok(sig):
                    log.info("AI đồng ý nhưng chưa chắc %s: %s%% < %s%% [%s] – vào với rủi ro giảm. %s", sig.symbol,
                             ai["confidence"], cfg.AI_MIN_CONFIDENCE, ai.get("model"), ai.get("reason"))
                    ai = dict(ai, reason=f"[AI chưa chắc {ai['confidence']}%<{cfg.AI_MIN_CONFIDENCE}%] " + str(ai.get("reason", "")),
                              weak=True)
                    t = self.engine.open(sig, ai)
                    taken = t is not None
                else:
                    log.info("AI chưa đủ chắc %s: %s %s%% < %s%% [%s] – bỏ. %s", sig.symbol, ai["decision"], ai["confidence"],
                             cfg.AI_WEAK_CONFIDENCE, ai.get("model"), ai.get("reason"))
            else:
                veto = current_veto_conf()
                if ai["confidence"] < veto and sig.score >= cfg.AI_OVERRIDE_MIN_SCORE and strat.rr_ok(sig):
                    # AI phản đối nhưng không đủ chắc → tín hiệu kỹ thuật mạnh vẫn được vào (bộ tự học theo dõi kết quả)
                    log.info("AI phản đối yếu %s: %s (%s%% < ngưỡng %s%%) [%s] – điểm KT %s ≥ %s nên vẫn vào. Lý do AI: %s",
                             sig.symbol, ai["decision"], ai["confidence"], veto, ai.get("model"), sig.score,
                             cfg.AI_OVERRIDE_MIN_SCORE, ai.get("reason"))
                    ai = dict(ai, reason=f"[Vượt phủ quyết yếu {ai['confidence']}%<{veto}%] " + str(ai.get("reason", "")),
                              override=True)
                    t = self.engine.open(sig, ai)
                    taken = t is not None
                else:
                    why = (f"điểm {sig.score} < {cfg.AI_OVERRIDE_MIN_SCORE}" if ai["confidence"] < veto
                           else f"nguy hiểm {ai['confidence']}% ≥ ngưỡng {veto}%")
                    log.info("AI từ chối %s: %s (%s%%) [%s] (%s) – %s", sig.symbol, ai["decision"], ai["confidence"],
                             ai.get("model"), why, ai.get("reason"))
            if taken and ai and ai.get("override"):
                src = "ai_override"
            elif taken and ai and ai.get("weak"):
                src = "ai_weak"
            elif not taken and sig.skip:
                src = "skipped"         # AI duyệt nhưng engine không vào được (giá đã chạy, vốn, lot) – lý do ở features.skip
            else:
                src = "taken" if taken else ("ai_error" if ai is None or ai.get("fallback") else "ai_rejected")
            stamp(self.okx, sig)        # giá last lúc quyết định – lệnh vừa vào đã có (cùng giá với lệnh)
            sid = self.store.log_signal(sig, ai, taken, source=src)
            self._link_books(sig, sid, ai, t if taken else None)
            self.shadow_seen[(sig.symbol, sig.side)] = time.time()
            self.cooldown[sig.symbol] = time.time() + (3600 if not taken else 7200)
            if taken:
                for rest in candidates[i + 1:k]:     # đã vào 1 lệnh: các ứng viên còn lại không hỏi AI, vẫn theo dõi bóng
                    self._link_books(rest, self._shadow(rest, "slot_taken"), None, None)
                break

    def _shadow(self, sig, source):
        key = (sig.symbol, sig.side)
        if time.time() - self.shadow_seen.get(key, 0) < 3600:
            return
        self.shadow_seen[key] = time.time()
        if "oi_chg_1h" not in sig.context and "ls_ratio" not in sig.context:
            try:
                sig.context.update(mkt.symbol_context(self.okx, sig.symbol))
            except Exception:
                pass
        stamp(self.okx, sig)            # v3.3: kết quả bóng tính từ giá last lúc này, như lệnh paper, không từ giá đóng nến cũ
        try:
            return self.store.log_signal(sig, None, False, source=source)
        except Exception as e:
            log.debug("shadow log: %s", e)
            return None

    def _noai_active(self):
        """Sổ noai còn được mở lệnh mới (bật và chưa dừng vì cầu dao riêng)."""
        return bool(self.noai) and not self.store.get("noai_halted")

    @property
    def _noai_open(self):
        try:
            return bool(self.noai) and bool(self.store.open_trades(books.NOAI))
        except Exception:
            return False

    def _monitor_noai(self):
        """Quản lý lệnh mở của sổ noai (SL/TP1/trailing như sổ chính); lỗi không làm dừng vòng lặp chính."""
        if not self.noai:
            return
        try:
            self.noai.monitor()
        except Exception as e:
            log.warning("[noai] quản lý lệnh mở lỗi: %s", str(e)[:160])

    def learn_text(self):
        st = self.learner.public_state()
        p, s = st["params"], st.get("stats") or {}
        lines = [f"🧬 TỰ HỌC | chế độ {st['mode']} | cần ≥{st['min_samples']} mẫu trong ≥{st.get('min_days', 7)} ngày",
                 f"Ngưỡng điểm: {p['min_score']} (mặc định {p['min_score_default']})",
                 "Luật đang chặn: " + (", ".join(st['rules'][b] for b in p['blocks']) or "không"),
                 "Trọng số trigger: " + (", ".join(f"{st['trigger_names'].get(k,k)} {v}" for k, v in p['trigger_w'].items()) or "mặc định")]
        if s:
            t = s.get("tech", {})
            lines.append(f"Đã có kết quả {s['total']['n']} tín hiệu, đang chờ {s.get('pending', 0)} | tín hiệu kỹ thuật: kỳ vọng {t.get('exp_r', 0):+.2f}R, thắng {t.get('winrate', 0)}%")
            av = s.get("ai_veto") or {}
            if av.get("veto_accuracy") is not None:
                lines.append(f"AI chặn đúng {av['veto_accuracy']}% ({av['rejected']['n']} lần)")
            alt = (s.get("exit_compare") or {}).get("alt") or {}
            if alt.get("n"):
                lines.append(f"Thử nghiệm {alt['name']}: {alt['exp_r']:+.2f}R so với {alt['cur_exp_r']:+.2f}R của cách đang dùng "
                             f"(chênh {alt['diff']:+.2f} ± {alt['diff_se']:.2f}R, {alt['n']} tín hiệu / {alt['diff_days']} ngày)")
        pend = [r for r in st["log"] if r["status"] == "pending"]
        if pend:
            lines.append(f"⏳ {len(pend)} đề xuất chờ duyệt – gõ /apply để áp dụng tất cả")
        return "\n".join(lines)

    # ------------------------------------------------------------ nhiều instance
    def _keepalive(self):
        """Ping chéo để Render free không bị ngủ (và để biết máy kia còn sống)."""
        urls = [u for u in (cfg.SELF_URL, cfg.PEER_URL) if u]
        if not urls:
            return
        import requests
        while True:
            for u in urls:
                try:
                    requests.get(u.rstrip("/") + "/health", timeout=20)
                except Exception as e:
                    log.debug("keepalive %s: %s", u, e)
            time.sleep(cfg.KEEPALIVE_INTERVAL)

    def _elect(self):
        """Giành/gia hạn khoá leader. Trả về True nếu instance này được phép giao dịch."""
        was = self.is_leader
        self.is_leader = self.store.try_lock("trader", self.instance, cfg.LEADER_TTL)
        self.store.heartbeat(self.instance, "leader" if self.is_leader else "standby",
                             f"v{VERSION} b{ver.build()} paper={cfg.MODE=='paper'} host={os.uname().nodename}")
        if self.is_leader and not was:
            log.info("[%s] trở thành LEADER", self.instance)
            if self.learner:   # nạp lại tham số tự học mới nhất (có thể do instance khác vừa cập nhật)
                from . import strategy
                strategy.LEARNED.update(self.store.get("learned") or {})
            self.notify.send(f"👑 {self.instance} tiếp quản vai trò giao dịch (chế độ {cfg.MODE.upper()}).")
        elif was and not self.is_leader:
            log.warning("[%s] mất khoá leader -> chuyển sang standby", self.instance)
        return self.is_leader

    def run(self):
        self.tg.start()
        threading.Thread(target=self._keepalive, daemon=True, name="keepalive").start()
        if self.grid:
            self.grid.start()
        if self.sheets:
            self.sheets.start(active=lambda: self.is_leader)
        fi = ENV_FILE_INFO
        if fi.get("path"):
            log.info("Đã nạp %d biến từ file %s (bỏ qua %d dòng trống/chưa điền) – ưu tiên hơn biến trên panel",
                     fi["loaded"], fi["path"], fi["skipped"])
        if fi.get("error"):
            log.warning("File cấu hình lỗi, không nạp được: %s", fi["error"])
        L = strat.LEARNED
        from . import exits as _ex
        # giá trị THỰC đang dùng (tự học ghi đè mặc định) – bản cũ in mặc định gây hiểu nhầm
        log.info("Thông số đang dùng: MIN_SCORE=%s MIN_ADX=%s HTF_MIN_ADX=%s MAX_EXT_ATR=%s BTC_GATE=%s%% BTC_NGÀY=%s CHASE_1H=%s%% "
                 "CLIMAX_VOL=%s BREAKOUT=%s LATE_ADX=%s SL=%s–%sATR TP1_R=%s "
                 "RISK=%.1f%% (×%.2f–%.2f theo AI, trần tổng %.0f%%, tối đa %d lệnh) TOP_N=%s AI_MIN_CONF=%s/%s "
                 "AI_VETO=%s%% HỌC≥%s mẫu,t≥%s CHẶN=%s", L.get("min_score") or cfg.MIN_SCORE, cfg.MIN_ADX, cfg.HTF_MIN_ADX,
                 cfg.MAX_EXT_ATR, cfg.BTC_GATE_CHG, "bật" if cfg.BTC_DAILY_GATE else "tắt (BTC_DAILY_GATE=false)",
                 cfg.CHASE_1H_PCT, cfg.CLIMAX_VOL,
                 "bật" if cfg.BREAKOUT_ENABLED else "tắt (BREAKOUT_ENABLED=false)", cfg.LATE_TREND_ADX,
                 cfg.ATR_SL_MULT, cfg.MAX_SL_ATR, _ex.current_tp1_r(), cfg.RISK_PER_TRADE * 100,
                 cfg.RISK_MIN_MULT, cfg.RISK_MAX_MULT, cfg.MAX_TOTAL_RISK * 100, cfg.MAX_OPEN_POSITIONS, cfg.TOP_N_COINS,
                 cfg.AI_MIN_CONFIDENCE, cfg.AI_WEAK_CONFIDENCE, current_veto_conf(), cfg.LEARN_MIN_SAMPLES, cfg.LEARN_MIN_T,
                 ",".join(sorted(strat.active_blocks())) or "-")
        log.info("Tự học đã đổi so với mặc định: %s", learned_changes(L))
        log.info("Phiên bản %s · bản dựng %s · %s | Instance %s | storage %s | leader TTL %ss", VERSION, ver.build(),
                 ver.status_line(), self.instance, "Postgres" if self.store.pg else "SQLite", cfg.LEADER_TTL)
        last_eq_log = 0
        announced = False
        while True:
            try:
                if not self._elect():
                    time.sleep(cfg.MONITOR_INTERVAL)
                    continue
                if not announced:
                    announced = True
                    try:
                        cur = f"{VERSION} {ver.build()}"
                        msg = ver.update_notice(self.store.get("last_version") or "")
                        if msg:
                            self.notify.send(msg)
                        self.store.set("last_version", cur)
                    except Exception as e:
                        log.debug("update notice: %s", e)
                    self.notify.send(f"🚀 Bot v{VERSION} khởi động – chế độ {cfg.MODE.upper()} | vốn {self.engine.equity():.2f} USDT | x{cfg.LEVERAGE} | "
                                     f"rủi ro {cfg.RISK_PER_TRADE*100:.1f}%/lệnh (co giãn theo chất lượng), tối đa {cfg.MAX_TOTAL_RISK*100:.0f}% tổng")
                self.paused = bool(self.store.get("paused", False))
                # 1. quản lý lệnh đang mở
                self.engine.monitor()
                self._monitor_noai()
                opens = self.store.open_trades()
                syms = {t["symbol"] for t in opens}
                if self.noai:
                    syms |= {t["symbol"] for t in self.store.open_trades(books.NOAI)}
                if syms:
                    self.last_prices.update(self.okx.prices(sorted(syms)))
                equity = self.engine.equity(self.last_prices)
                self.store.set("last_equity", equity)
                if time.time() - last_eq_log > 300:
                    self.store.log_equity(equity)
                    if self.noai:
                        neq = self.noai.equity(self.last_prices)
                        self.store.set("noai_last_equity", neq)
                        self.store.log_equity(neq, books.NOAI)
                    last_eq_log = time.time()
                # 2. kiểm tra rủi ro + quét tín hiệu (v3.5: sổ chính dừng vì cầu dao thì vẫn quét cho sổ noai)
                main_ok = self._risk_ok(equity)
                if not self.paused and time.time() - self.last_scan > cfg.SCAN_INTERVAL and (main_ok or self._noai_active()):
                    self.last_scan = time.time()
                    self.scan(main_active=main_ok)
                # 3. tự học: cập nhật kết quả bóng, điều chỉnh tham số, AI tổng kết ngày
                self.learner.tick()
            except (KeyboardInterrupt, SystemExit):
                break
            except Exception as e:
                if _is_network_error(e):
                    log.warning("Lỗi mạng tạm thời (%s) – thử lại ở vòng sau", str(e)[:160])
                else:
                    log.error("Lỗi vòng lặp:\n%s", traceback.format_exc())
                time.sleep(10)
            try:
                time.sleep(cfg.MONITOR_INTERVAL)
            except (KeyboardInterrupt, SystemExit):
                break
        # dọn dẹp: bỏ qua tín hiệu dừng lặp lại để không in traceback khi Botkeep restart
        try:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            signal.signal(signal.SIGINT, signal.SIG_IGN)
        except Exception:
            pass
        try:
            if self.is_leader:
                self.store.release_lock("trader", self.instance)
            log.info("Bot dừng (instance %s).", self.instance)
        except BaseException:
            pass


def _is_network_error(e):
    try:
        import ccxt
        if isinstance(e, (ccxt.NetworkError,)):
            return True
    except Exception:
        pass
    import requests
    return isinstance(e, (requests.RequestException, ConnectionError, TimeoutError)) or "mạng" in str(e)


def main():
    if cfg.MODE == "live" and not cfg.OKX_API_KEY:
        log.error("MODE=live nhưng thiếu OKX_API_KEY")
        sys.exit(1)
    if cfg.MODE == "live" and os.getenv("I_UNDERSTAND_THE_RISK", "").lower() != "yes":
        log.error("MODE=live cần đặt I_UNDERSTAND_THE_RISK=yes trong .env để xác nhận")
        sys.exit(1)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    Bot().run()


if __name__ == "__main__":
    main()
