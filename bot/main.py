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
from .engine import PaperEngine, LiveEngine
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

logging.basicConfig(
    level=getattr(logging, cfg.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("main")


class Bot:
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

    # ------------------------------------------------------------ AI tổng kết sau khi đóng lệnh (lần gọi thứ 2)
    def _on_trade_closed(self, t, exit_, pnl, pnl_pct, reason):
        def _run():
            rev = self.ai.review(t, exit_, pnl, pnl_pct, reason)
            if rev:
                self.store.add_review(t, pnl, rev)
                self.notify.send(f"🧠 TỔNG KẾT {t['side'].upper()} {t['symbol']} [{rev['verdict']}]\n"
                             f"{rev['lesson']}\n#" + " #".join(rev.get("tags") or []))
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
        if getattr(self, "last_btc", None):
            lines.append("₿ " + mkt.describe(self.last_btc))
        if ls:
            lines.append(f"Quét cuối {dt.datetime.fromtimestamp(ls['ts']).strftime('%H:%M')}: {ls['scanned']} coin, {ls['passed']} đạt | "
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
        for m in st["models"][:10]:
            lines.append(f"• {m['model']}: {m['calls']} gọi, {m['ok']} ok, {m['fail']} lỗi"
                         + (f", ~{m['avg_ms']/1000:.1f}s" if m['avg_ms'] else "") + (" (đang nghỉ)" if m['cooldown'] else ""))
        if st.get("last_result"):
            r = st["last_result"]
            lines.append(f"Gần nhất: {r['side'].upper()} {r['symbol']} → {r['decision']} {r['confidence']}% ({r['model']})\n{r['reason']}")
        return "\n".join(lines)

    def lessons_text(self):
        rows = self.store.reviews(8)
        if not rows:
            return "Chưa có tổng kết nào."
        return "\n\n".join(f"{'✅' if r['pnl']>0 else '❌'} {r['side'].upper()} {r['symbol']} [{r['verdict']}]\n{r['lesson']}" for r in rows)

    HELP = ("Lệnh (Telegram gõ /lenh, Zoom gõ /okxbot lenh):\n"
            "status – vốn, lệnh mở, quét gần nhất\ntrades – 10 lệnh gần nhất\nsignals – tín hiệu & quyết định AI\n"
            "ai – lượt gọi từng model\nlearn – trạng thái tự học\napply – duyệt đề xuất tự học\n"
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
            return f"▶️ Bot tiếp tục giao dịch. – qua {source}"
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

    def close_all(self, reason):
        n = 0
        for t in self.store.open_trades():
            try:
                self.engine.close(t, self.okx.price(t["symbol"]), reason)
                n += 1
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
        }

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

    def _daily_report(self):
        since = time.time() - 86400
        rows = self.store.closed_trades_since(since)
        if not rows:
            return
        pnl = sum(r["pnl"] for r in rows)
        wins = sum(1 for r in rows if r["pnl"] > 0)
        self.notify.send(f"📅 BÁO CÁO NGÀY\nLệnh: {len(rows)} | Thắng: {wins} | PnL: {pnl:+.4f} USDT\n"
                     f"Vốn: {self.engine.equity(self.last_prices):.4f} USDT")

    def scan(self):
        opens = self.store.open_trades()
        if len(opens) >= cfg.MAX_OPEN_POSITIONS:
            return
        open_syms = {t["symbol"] for t in opens}
        coins = self.okx.top_altcoins(cfg.TOP_N_COINS)
        btc = mkt.btc_context(self.okx)
        self.last_btc = btc
        log.info("Quét %d coin... %s", len(coins), mkt.describe(btc))
        candidates, reasons, near = [], Counter(), []
        for sym, vol, chg in coins:
            if sym in open_syms or self.cooldown.get(sym, 0) > time.time():
                continue
            try:
                ltf = self.okx.ohlcv(sym, cfg.TIMEFRAME, 220)
                htf = self.okx.ohlcv(sym, cfg.HTF_TIMEFRAME, 120)
                sig, why, info = diagnose(sym, ltf, htf, funding=0.0, change24h=float(chg or 0), market=btc)
                min_score = strat.LEARNED.get("min_score") or cfg.MIN_SCORE
                if sig is not None and (not why or why.startswith("điểm thấp")) and sig.score >= min_score - 1.0:
                    # qua cổng + có trigger → lấy dòng tiền (OI / long-short / funding) và cộng/trừ điểm
                    # TRƯỚC khi xét ngưỡng & xếp hạng → ứng viên gửi AI tốt cả kỹ thuật lẫn dòng tiền
                    sig.context.update(mkt.symbol_context(self.okx, sym))
                    why = strat.apply_flow(sig)
                    info["score"] = sig.score
                if sig and not why:
                    blk = strat.blocked_by(sig)
                    if blk:
                        sig.rejected = f"luật tự học: {blk}"
                        reasons["luật tự học chặn"] += 1
                        log.info("%s %s bị luật tự học chặn: %s", sym, sig.side, blk)
                        self._shadow(sig, "blocked")
                        continue
                    candidates.append(sig)
                else:
                    if sig is not None:   # suýt đạt (điểm thấp) → ghi lại để theo dõi kết quả bóng
                        self._shadow(sig, "below_score")
                    reasons[why.split("(")[0].strip()] += 1
                    if "trigger" in why or "điểm thấp" in why or "RR thấp" in why:
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
                                  "top": [f"{s.symbol.split('/')[0]} {s.side} {s.score}" for s in candidates[:3]]}
        self.store.set("last_scan", self.last_scan_summary)
        try:
            self.store.add_scan(self.last_scan_summary)
        except Exception as e:
            log.debug("add_scan: %s", e)
        k = max(1, cfg.AI_CANDIDATES_PER_SCAN)
        for sig in candidates[k:]:          # đạt nhưng xếp sau → không hỏi AI, vẫn theo dõi kết quả
            self._shadow(sig, "not_sent")

        for sig in candidates[:k]:          # hỏi AI lần lượt tối đa k ứng viên tốt nhất; vào được 1 lệnh thì dừng
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
                if sig.rr >= strat.min_rr_for(sig):
                    t = self.engine.open(sig, ai)
                    taken = t is not None
            elif ai["decision"] == sig.side.upper() and ai["confidence"] >= cfg.AI_MIN_CONFIDENCE:
                # SL/TP giữ nguyên theo cấu trúc ATR của strategy – AI không được chỉnh
                if sig.rr >= strat.min_rr_for(sig):
                    t = self.engine.open(sig, ai)
                    taken = t is not None
            elif ai["decision"] == sig.side.upper():
                # AI cùng hướng nhưng chưa chắc: ≥ AI_WEAK_CONFIDENCE → vẫn vào với rủi ro giảm (risk.quality_mult),
                # thấp hơn nữa mới bỏ. (v3.1: trên dữ liệu đã có, AI chọn lọc không tốt hơn ngẫu nhiên → bỏ lệnh = bỏ lãi)
                if ai["confidence"] >= cfg.AI_WEAK_CONFIDENCE and sig.rr >= strat.min_rr_for(sig):
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
                if ai["confidence"] < veto and sig.score >= cfg.AI_OVERRIDE_MIN_SCORE and sig.rr >= strat.min_rr_for(sig):
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
            else:
                src = "taken" if taken else ("ai_error" if ai is None or ai.get("fallback") else "ai_rejected")
            self.store.log_signal(sig, ai, taken, source=src)
            self.shadow_seen[(sig.symbol, sig.side)] = time.time()
            self.cooldown[sig.symbol] = time.time() + (3600 if not taken else 7200)
            if taken:
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
        try:
            self.store.log_signal(sig, None, False, source=source)
        except Exception as e:
            log.debug("shadow log: %s", e)

    def learn_text(self):
        st = self.learner.public_state()
        p, s = st["params"], st.get("stats") or {}
        lines = [f"🧬 TỰ HỌC | chế độ {st['mode']} | cần ≥{st['min_samples']} mẫu",
                 f"Ngưỡng điểm: {p['min_score']} (mặc định {p['min_score_default']})",
                 "Luật đang chặn: " + (", ".join(st['rules'][b] for b in p['blocks']) or "không"),
                 "Trọng số trigger: " + (", ".join(f"{st['trigger_names'].get(k,k)} {v}" for k, v in p['trigger_w'].items()) or "mặc định")]
        if s:
            t = s.get("tech", {})
            lines.append(f"Đã có kết quả {s['total']['n']} tín hiệu, đang chờ {s.get('pending', 0)} | tín hiệu kỹ thuật: kỳ vọng {t.get('exp_r', 0):+.2f}R, thắng {t.get('winrate', 0)}%")
            av = s.get("ai_veto") or {}
            if av.get("veto_accuracy") is not None:
                lines.append(f"AI chặn đúng {av['veto_accuracy']}% ({av['rejected']['n']} lần)")
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
        fi = ENV_FILE_INFO
        if fi.get("path"):
            log.info("Đã nạp %d biến từ file %s (bỏ qua %d dòng trống/chưa điền) – ưu tiên hơn biến trên panel",
                     fi["loaded"], fi["path"], fi["skipped"])
        if fi.get("error"):
            log.warning("File cấu hình lỗi, không nạp được: %s", fi["error"])
        L = strat.LEARNED
        from . import exits as _ex
        # giá trị THỰC đang dùng (tự học ghi đè mặc định) – bản cũ in mặc định gây hiểu nhầm
        log.info("Thông số đang dùng: MIN_SCORE=%s MIN_ADX=%s HTF_MIN_ADX=%s MAX_EXT_ATR=%s SL=%s–%sATR TP1_R=%s "
                 "RISK=%.1f%% (×%.2f–%.2f theo chất lượng, trần tổng %.0f%%, tối đa %d lệnh) TOP_N=%s AI_MIN_CONF=%s/%s "
                 "AI_VETO=%s%% HỌC≥%s mẫu,t≥%s CHẶN=%s", L.get("min_score") or cfg.MIN_SCORE, cfg.MIN_ADX, cfg.HTF_MIN_ADX,
                 cfg.MAX_EXT_ATR, cfg.ATR_SL_MULT, cfg.MAX_SL_ATR, _ex.current_tp1_r(), cfg.RISK_PER_TRADE * 100,
                 cfg.RISK_MIN_MULT, cfg.RISK_MAX_MULT, cfg.MAX_TOTAL_RISK * 100, cfg.MAX_OPEN_POSITIONS, cfg.TOP_N_COINS,
                 cfg.AI_MIN_CONFIDENCE, cfg.AI_WEAK_CONFIDENCE, current_veto_conf(), cfg.LEARN_MIN_SAMPLES, cfg.LEARN_MIN_T,
                 ",".join(sorted(strat.active_blocks())) or "-")
        log.info("Tự học đã đổi so với mặc định: %s",
                 ", ".join(f"{k}={v}" for k, v in L.items() if v not in (None, [], {})) or "chưa có")
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
                opens = self.store.open_trades()
                if opens:
                    self.last_prices.update(self.okx.prices([t["symbol"] for t in opens]))
                equity = self.engine.equity(self.last_prices)
                self.store.set("last_equity", equity)
                if time.time() - last_eq_log > 300:
                    self.store.log_equity(equity)
                    last_eq_log = time.time()
                # 2. kiểm tra rủi ro + quét tín hiệu
                if self._risk_ok(equity) and not self.paused and time.time() - self.last_scan > cfg.SCAN_INTERVAL:
                    self.last_scan = time.time()
                    self.scan()
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
