"""Bộ tự học – học từ kết quả THẬT của mọi tín hiệu, kể cả tín hiệu không vào lệnh.

1) Theo dõi kết quả bóng (shadow): mỗi tín hiệu đã qua bộ lọc kỹ thuật (vào lệnh, AI từ chối,
   không được gửi AI, bị luật chặn, hoặc "suýt đạt" vì điểm thấp) đều được ghi lại kèm entry/SL/TP.
   Bot dùng nến 5m sau đó để xác định: chạm TP trước, chạm SL trước, hay hết giờ → kết quả tính bằng R
   (1R = khoảng cách tới SL), đã trừ phí + trượt giá.

2) Tự điều chỉnh có giới hạn (chỉ những tham số trong danh mục cho phép, không bao giờ tự sửa code):
   - Bật/tắt các luật lọc trong strategy.RULES khi nhóm tín hiệu đó lỗ/lãi ổn định.
   - Trọng số từng loại trigger (0.6 – 1.4).
   - Ngưỡng điểm tối thiểu MIN_SCORE (4.0 – 7.0, mỗi lần ±0.5).
   Mỗi thay đổi cần đủ mẫu (LEARN_MIN_SAMPLES) trải trên ≥ LEARN_MIN_DAYS ngày khác nhau VÀ khác 0 có ý nghĩa thống kê
   (|kỳ vọng|/sai số chuẩn ≥ LEARN_MIN_T, sai số chuẩn gom cụm theo ngày – v3.3: tín hiệu cùng ngày là cùng một nhịp thị
   trường, không độc lập), mỗi tham số chỉ đổi tối đa 1 lần/LEARN_COOLDOWN_H giờ, có lưu lịch sử, khôi phục mặc định được.
   Trọng số trigger và ngưỡng điểm chỉ học từ tín hiệu KHÔNG bị luật đang chặn (v3.3: tín hiệu luật chặn không vào lệnh
   được dù điểm thế nào). LEARN_MODE=auto (tự áp dụng) | suggest (chờ duyệt trên web/Telegram) | off.

3) AI tổng kết chiến lược 1 lần/ngày: đọc số liệu thống kê, viết nhận xét + gợi ý (không tự áp dụng).
"""
import json
import logging
import math
import time
import datetime as dt
from collections import defaultdict
from .config import cfg
from . import strategy
from . import exits
from . import costs

log = logging.getLogger("learner")

TECH_SOURCES = ("taken", "ai_weak", "ai_override", "ai_rejected", "ai_error", "skipped", "not_sent", "slot_taken",
                "max_positions", "blocked")
EXIT_NAMES = {"fixed": "TP cố định", "hybrid": "Ăn sóng (chốt 1 phần + bám sóng)", "trail": "Bám sóng toàn bộ"}
SOURCE_NAMES = {"taken": "Đã vào lệnh", "ai_weak": "Vào (AI đồng ý nhưng chưa chắc, rủi ro giảm)", "ai_override": "Vào dù AI phản đối yếu", "ai_rejected": "AI từ chối", "ai_error": "AI lỗi / hết lượt",
                "skipped": "AI duyệt nhưng không vào được (giá đã chạy / vốn / lot)",
                "not_sent": "Không gửi AI (xếp sau)",
                "slot_taken": "Không gửi AI (lượt quét đã vào lệnh khác)",
                "max_positions": "Không gửi AI (đang đủ lệnh mở)",
                "blocked": "Bị luật chặn (chặn sẵn hoặc tự học)", "below_score": "Suýt đạt (điểm thấp)"}

SYSTEM_DAILY = """Bạn là quản lý rủi ro của một bot giao dịch futures altcoin. Bạn nhận thống kê kết quả THẬT
(đo bằng R, 1R = khoảng cách tới stop-loss, đã trừ phí) của mọi tín hiệu bot tạo ra, chia theo nhóm.
Hãy viết đánh giá ngắn gọn, thẳng thắn bằng tiếng Việt. Cảnh giác với mẫu nhỏ (<15 tín hiệu không đủ kết luận).
TRẢ LỜI DUY NHẤT bằng JSON:
{"summary": "2-3 câu", "observations": ["tối đa 4 ý"],
 "suggestions": [{"param": "min_score" | "block" | "unblock", "value": số hoặc mã luật, "why": "1 câu"}]}
Mã luật hợp lệ cho block/unblock: %s. min_score trong khoảng 4.0-7.0. Không có gợi ý nào thì để mảng rỗng."""


def _day(ts):
    """Ngày (theo TIMEZONE của bot) của một tín hiệu – đơn vị gom cụm của sai số chuẩn."""
    return time.strftime("%Y-%m-%d", time.localtime(float(ts or 0)))


def _tstat(vals, days=None):
    """(sai số chuẩn, t = trung bình / sai số chuẩn) của trung bình `vals`. t kẹp ±99 (mẫu đồng nhất → se = 0).
    days: ngày của từng giá trị → sai số chuẩn GOM CỤM theo ngày (CR1: G/(G-1) × Σ_ngày (Σ phần dư)² / n²). Tín hiệu cùng
    ngày tương quan (cùng nhịp BTC, cùng phiên) nên coi chúng độc lập làm sai số chuẩn quá nhỏ, t quá lớn. Mỗi giá trị một
    ngày thì bằng đúng sai số chuẩn thường. Cần ≥ 2 ngày."""
    n = len(vals)
    if n < 2:
        return 0.0, 0.0
    m = sum(vals) / n
    if days is None:
        var = sum((x - m) ** 2 for x in vals) / (n - 1) / n
    else:
        sums = defaultdict(float)
        for x, d in zip(vals, days):
            sums[d] += x - m
        g = len(sums)
        if g < 2:
            return 0.0, 0.0
        var = g / (g - 1) * sum(v * v for v in sums.values()) / (n * n)
    se = math.sqrt(max(var, 0.0))
    if se <= 1e-12:
        return 0.0, (99.0 if m > 0 else -99.0 if m < 0 else 0.0)
    return se, max(-99.0, min(99.0, m / se))


def _ref_r(row):
    """R dùng để so sánh các NHÓM tín hiệu: luôn theo cách thoát hybrid (mô phỏng song song trên mọi tín hiệu).
    v3.2: outcome_r được gán theo cách thoát ĐANG DÙNG lúc có kết quả, nên khi bộ tự học đổi cách thoát giữa chừng
    thì thống kê theo luật/trigger/điểm trộn hai cách thoát. Một mốc cố định mới so được."""
    v = row.get("r_hybrid")
    return v if v is not None else (row.get("outcome_r") or 0)


def _agg(rows, value=None):
    """Thống kê một nhóm tín hiệu: n, thắng, kỳ vọng (R), sai số chuẩn & t gom cụm theo ngày, số ngày khác nhau."""
    n = len(rows)
    if not n:
        return {"n": 0, "win": 0, "winrate": 0, "exp_r": 0, "sum_r": 0, "se": 0, "t": 0, "days": 0}
    rs = [(value or _ref_r)(r) for r in rows]
    days = [_day(r.get("ts")) for r in rows]
    wins = sum(1 for x in rs if x > 0)
    se, t = _tstat(rs, days)
    return {"n": n, "win": wins, "winrate": round(100 * wins / n, 1), "exp_r": round(sum(rs) / n, 3),
            "sum_r": round(sum(rs), 2), "se": round(se, 3), "t": round(t, 2), "days": len(set(days))}


def _confident(a, thr, n_min):
    """Nhóm `a` đủ mẫu trải trên ≥ LEARN_MIN_DAYS ngày, kỳ vọng vượt ngưỡng `thr` (thr > 0: tốt hơn; thr < 0: tệ hơn)
    VÀ khác 0 có ý nghĩa thống kê (|t| ≥ LEARN_MIN_T, t gom cụm theo ngày). Mẫu nhỏ / dồn vài ngày → không hành động."""
    if a["n"] < n_min or a.get("days", 0) < cfg.LEARN_MIN_DAYS:
        return False
    if thr >= 0 and a["exp_r"] <= thr:
        return False
    if thr < 0 and a["exp_r"] >= thr:
        return False
    return abs(a.get("t", 0)) >= cfg.LEARN_MIN_T


def _paired(pairs, thr, n_min):
    """So sánh 2 cách trên CÙNG tập tín hiệu: pairs = [(ts, hiệu của cặp)]. Đạt khi đủ mẫu trải trên ≥ LEARN_MIN_DAYS
    ngày, trung bình hiệu ≥ thr và t (gom cụm theo ngày) ≥ LEARN_MIN_T. Trả về (đạt, trung bình hiệu, se, t, số ngày)."""
    n = len(pairs)
    if not n:
        return False, 0.0, 0.0, 0.0, 0
    diffs = [d for _, d in pairs]
    days = [_day(ts) for ts, _ in pairs]
    m = sum(diffs) / n
    se, t = _tstat(diffs, days)
    g = len(set(days))
    ok = n >= n_min and g >= cfg.LEARN_MIN_DAYS and m >= thr and t >= cfg.LEARN_MIN_T
    return ok, round(m, 3), round(se, 3), round(t, 2), g


def _feat(row):
    try:
        return json.loads(row.get("features") or "{}")
    except Exception:
        return {}


def _describe_param(k, v):
    """Mô tả ngắn một tham số tự học đang khác mặc định (cho log / thông báo)."""
    if k == "trigger_w":
        return "trọng số " + ", ".join(f"{strategy.TRIGGER_NAMES.get(t, t)} {w:g} → 1" for t, w in v.items())
    if k == "blocks":
        return "chặn " + ", ".join(v)
    if k == "unblocked":
        return "gỡ chặn sẵn " + ", ".join(v)
    if k == "min_score":
        return f"ngưỡng điểm {v:g} → {cfg.MIN_SCORE:g}"
    if k == "exit_mode":
        return f"cách thoát {v} → mặc định"
    if k == "tp1_r":
        return f"TP1 {v:g}R → {cfg.TP1_R:g}R"
    if k == "ai_veto_conf":
        return f"ngưỡng phủ quyết AI {v} → mặc định"
    return f"{k}={v}"


def current_veto_conf():
    """Độ chắc chắn tối thiểu để lời phản đối của AI có hiệu lực (0 = AI luôn được chặn)."""
    v = strategy.LEARNED.get("ai_veto_conf")
    if v is not None:
        return int(v)
    if cfg.AI_VETO_CONF:
        return int(float(cfg.AI_VETO_CONF))
    return 65 if cfg.MODE == "paper" else 0


class Learner:
    def __init__(self, store, okx, ai, notify):
        self.store, self.okx, self.ai, self.notify = store, okx, ai, notify
        self.mode = cfg.LEARN_MODE or ("auto" if cfg.MODE == "paper" else "suggest")
        saved = store.get("learned")
        if saved:
            strategy.LEARNED.update(saved)
        self._migrate()
        self.last_stats = store.get("learn_stats")
        self.last_resolve = 0
        self.last_learn = 0
        self._first = {}        # sid → nến 1m của khung 5m chứa lúc vào (không đổi nữa sau khi khung đóng)

    LEARNED_SCHEMA = 3
    RESET_FLAG = "learn_reset_3_3"      # kv: đã chạy lần đưa tham số tự học về mặc định của v3.3 (chỉ một lần)
    NOTICE_KEY = "learn_notice"         # kv: thông báo chờ leader gửi (lúc khởi tạo bot chưa là leader → Notifier bỏ tin)
    DEFAULTS = {"min_score": None, "trigger_w": {}, "blocks": [], "unblocked": [], "exit_mode": None,
                "ai_veto_conf": None, "tp1_r": None}

    def _migrate(self):
        """v3.2 (schema < 2): tham số do bản cũ học với ngưỡng t ≥ 1.3 (học nhiễu) được đưa về mặc định một lần:
        bỏ chặn cả phía (short_side/long_side), bỏ chặn luật học được, trả cách thoát / ngưỡng điểm / trọng số trigger
        về mặc định. Giữ ngưỡng phủ quyết AI (được học nhất quán cả 2 lần) và TP1.
        v3.3 (cờ kv RESET_FLAG): mọi tham số tự học trước 3.3 về mặc định – xem _reset_v33."""
        L = strategy.LEARNED
        if int(L.get("schema") or 1) < 2:
            self._migrate_v32(L)
        if not self.store.get(self.RESET_FLAG):
            self._reset_v33(L)

    def _reset_v33(self, L):
        """Một lần (cờ kv RESET_FLAG, dùng chung khi nhiều instance chung DB): đưa MỌI tham số tự học về mặc định, xoá thời
        gian chờ, bỏ các đề xuất đang chờ duyệt. Chúng được học bằng thống kê sai của bản trước 3.3: tín hiệu luật chặn sẵn
        đã chặn vẫn được tính cho trọng số trigger (pullback 0.82: 22/32 tín hiệu đã bị chặn sẵn), sai số chuẩn coi tín hiệu
        cùng ngày là độc lập (32 tín hiệu của 3 ngày, t ≈ -2.04), không cần số ngày tối thiểu, trọng số pullback bị bỏ qua
        ở nửa số tín hiệu, giá vào cũ và phí 0.15%. Ghi log, lịch sử tự học, và thông báo (leader gửi ở tick đầu tiên)."""
        old = {k: L.get(k) for k in self.DEFAULTS}
        changed = {k: v for k, v in old.items() if v not in (None, [], {})}
        L.update({k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v)
                  for k, v in self.DEFAULTS.items()})
        L["schema"] = self.LEARNED_SCHEMA
        self.store.set("learned", L)
        self.store.set("learn_last", {})
        dropped = 0
        for r in self.store.learn_log(500):
            if r["status"] == "pending":
                self.store.set_learn_status(r["ts"], "rejected")
                dropped += 1
        if changed or dropped:
            what = ", ".join(_describe_param(k, v) for k, v in changed.items()) or "không có tham số nào đang khác mặc định"
            reason = (f"v3.3: đưa tham số tự học về mặc định ({what}"
                      + (f"; bỏ {dropped} đề xuất chờ duyệt" if dropped else "") + "). Chúng được học bằng thống kê cũ: "
                      "tính cả tín hiệu luật chặn sẵn đã chặn, coi tín hiệu cùng ngày là độc lập, phí 0,15% và giá vào cũ. "
                      f"Từ nay mỗi thay đổi cần ≥ {cfg.LEARN_MIN_SAMPLES} mẫu trải trên ≥ {cfg.LEARN_MIN_DAYS} ngày, "
                      f"t ≥ {cfg.LEARN_MIN_T:g} tính theo ngày.")
            self.store.add_learn("migrate", "v3.3", old, self.DEFAULTS, reason, 0, 0, "applied")
            self.store.set(self.NOTICE_KEY, "🧬 TỰ HỌC – " + reason)
            log.info("Tự học: %s", reason)
        else:
            log.info("Tự học v3.3: tham số tự học đang ở mặc định – không cần đưa về")
        self.store.set(self.RESET_FLAG, time.time())

    def _flush_notice(self):
        """Gửi thông báo đang chờ (lần đưa về mặc định v3.3) qua kênh thông báo thường – chỉ leader gọi tick()."""
        msg = self.store.get(self.NOTICE_KEY)
        if msg:
            self.store.set(self.NOTICE_KEY, None)
            self.notify(msg)

    def _migrate_v32(self, L):
        old = {k: L.get(k) for k in ("blocks", "unblocked", "exit_mode", "min_score", "trigger_w")}
        changed = any(old[k] for k in old)
        L.update({"blocks": [], "unblocked": [], "exit_mode": None, "min_score": None, "trigger_w": {},
                  "schema": self.LEARNED_SCHEMA})
        self.store.set("learned", L)
        if changed:
            last = self.store.get("learn_last") or {}
            for k in list(last):
                if k.startswith(("rule:", "trig:")) or k in ("exit_mode", "min_score"):
                    last.pop(k)
            self.store.set("learn_last", last)
            self.store.add_learn("migrate", "v3.2", old, {k: L[k] for k in old},
                                 "v3.2: đưa tham số học bằng ngưỡng cũ (t ≥ 1.3) về mặc định; từ nay cần t ≥ 2.0 và "
                                 "không chặn cả một phía", 0, 0, "applied")
            log.info("Tự học: đã đưa tham số bản cũ về mặc định (v3.2): %s", old)

    # ================================================================ 1. kết quả bóng
    def resolve_pending(self):
        rows = self.store.pending_signals(60)
        if not rows:
            return 0
        by_sym = defaultdict(list)
        for r in rows:
            by_sym[r["symbol"]].append(r)
        done = 0
        cost_frac = costs.after_fill_cost()     # v3.3: cùng mô hình chi phí với paper (bản cũ thiếu trượt giá chiều vào)
        for sym, items in by_sym.items():
            since = int(min(i["ts"] for i in items) * 1000)
            try:
                candles = self.okx.ex.fetch_ohlcv(sym, "5m", since=since, limit=300)
            except Exception as e:
                log.debug("shadow %s: %s", sym, e)
                continue
            for it in items:
                first = self._entry_bars(sym, it)
                if first is False:
                    continue            # chưa đọc được nến 1m lúc vào – để lượt sau, không mô phỏng lẫn giá trước lúc vào
                res = self._simulate_all(it, candles, cost_frac, first)
                if res:
                    outcome, r, rf, rh, rt, rv, ra = res
                    self.store.set_outcome(it["sid"], outcome, r, rf, rh, rt, rv, ra)
                    self._first.pop(it["sid"], None)
                    done += 1
        if done:
            log.info("Tự học: cập nhật kết quả %d tín hiệu", done)
        return done

    def _entry_bars(self, sym, it):
        """Nến 1m của khung 5m chứa lúc vào tín hiệu (để mô phỏng bóng bỏ phút chứa lúc vào như lệnh paper – v3.3).
        None: sàn không trả nến 1m nào (dùng cả nến 5m như cũ); False: lỗi mạng, để lượt sau."""
        sid = it["sid"]
        if sid in self._first:
            return self._first[sid]
        b0 = int(float(it["ts"]) // 300) * 300
        try:
            rows = self.okx.ex.fetch_ohlcv(sym, "1m", since=b0 * 1000, limit=6)
        except Exception as e:
            log.debug("shadow 1m %s: %s", sym, e)
            return False
        rows = [r for r in rows or [] if b0 * 1000 <= r[0] < (b0 + 300) * 1000] or None
        self._first[sid] = rows
        return rows

    @staticmethod
    def _simulate_all(it, candles, cost_frac, first=None):
        """Mô phỏng cả 3 cách thoát + hybrid với từng mức TP1 + biến thể A/B "SL 2.5 ATR + bám sóng toàn bộ" (exits.ALT_*).
        Kết quả chính (outcome/R) theo cách bot đang dùng. Tín hiệu setup "range" có TP riêng (dải giữa Bollinger) → chỉ
        thoát kiểu fixed, không có biến thể (SL/TP của range là một setup khác).
        v3.3 – cùng giá & phí với lệnh paper: vào ở it["entry"] (giá last lúc quyết định) trượt SLIPPAGE, 1R/TP1 tính
        từ giá khớp đó, trừ phí hai chiều + trượt giá chiều ra (costs.after_fill_cost); tổng 0.20% như paper. ATR là ATR
        đúng của tín hiệu (features.atr, như lệnh paper); tín hiệu ghi trước khi có trường này dùng atr_pct (đã làm tròn).
        first: nến 1m của khung 5m chứa lúc vào – bỏ phút chứa lúc vào như paper (exits.simulate).
        Trả về (outcome, R, R fixed, R hybrid, R trail, {TP1: R}, R biến thể) hoặc None nếu chưa kết thúc."""
        side, sl = it["side"], it["sl"]
        if not it["entry"] or not sl:
            return "NODATA", 0.0, None, None, None, None, None
        sign = 1 if side == "long" else -1
        entry = costs.entry_fill(side, it["entry"])
        if (entry - sl) * sign <= 0:        # giá khớp đã vượt SL – không vào được (engine.fill_check)
            return "NODATA", 0.0, None, None, None, None, None
        try:
            f = json.loads(it.get("features") or "{}")
        except Exception:
            f = {}
        atr = float(f.get("atr") or 0) or (f.get("atr_pct") or 0) / 100 * entry
        if atr <= 0:
            atr = abs(entry - sl) / cfg.ATR_SL_MULT
        is_range = f.get("setup") == "range"
        tp_fixed = it.get("tp") if is_range else None
        if is_range and tp_fixed and ((tp_fixed - entry) * sign <= 0 or (tp_fixed - it["entry"]) * sign <= 0):
            return "NODATA", 0.0, None, None, None, None, None    # giá đã vượt TP cố định – paper cũng không vào
        jobs = {m: dict(mode=("fixed" if is_range else m), tp_fixed=tp_fixed) for m in exits.MODES}
        if not is_range:
            for v in exits.TP1_CANDIDATES:
                jobs[f"tp1:{v}"] = dict(mode="hybrid", tp1_r=v)
            # biến thể A/B: SL từ giá đóng nến tín hiệu như strategy._levels với 2.5 ATR, vào cùng giá khớp, bám sóng toàn bộ
            jobs["alt"] = dict(mode=exits.ALT_MODE,
                               sl=exits.alt_sl(side, float(f.get("signal_close") or it["entry"]), sl, atr))
        res = {}
        for k, j in jobs.items():
            res[k] = exits.simulate(side, entry, j.get("sl", sl), atr, j["mode"], it["ts"], candles, cost_frac,
                                    tp1_r=j.get("tp1_r"), tp_fixed=j.get("tp_fixed"), first=first)
        # hết dữ liệu mà chưa kết thúc → sau RUNNER_MAX_HOLD_HOURS + 1h thì đóng theo giá cuối
        age = time.time() - it["ts"]
        unfinished = [k for k in res if res[k][0] is None]
        if unfinished:
            if age <= cfg.RUNNER_MAX_HOLD_HOURS * 3600 + 3600 or not candles:
                return None
            last = float(candles[-1][4])
            for k in unfinished:
                risk = abs(entry - jobs[k].get("sl", sl))
                move = ((last - entry) if side == "long" else (entry - last)) / risk - cost_frac * entry / risk
                res[k] = ("TIMEOUT", round(move, 3))
        rv = {k.split(":")[1]: res[k][1] for k in res if k.startswith("tp1:")} or None
        from .engine import current_exit_mode
        main = current_exit_mode()
        o, r = res["fixed"] if is_range else res[main]
        return o, r, res["fixed"][1], res["hybrid"][1], res["trail"][1], rv, (res["alt"][1] if "alt" in res else None)

    # ================================================================ 2. thống kê
    def compute_stats(self):
        """Thống kê (và đề xuất của learn) chỉ trên tín hiệu có kết quả theo mô hình giá & phí hiện hành (v3.3+: features
        có entry_ref). Tín hiệu bản cũ (vào ở giá đóng nến 15m cũ, phí 0.15%) lệch ~-0.065R/tín hiệu so với mô hình mới –
        trộn vào làm sai các ngưỡng R cố định, nên bị bỏ (số lượng ghi ở legacy_excluded). Sau khi nâng cấp, bộ tự học
        chờ tới khi đủ LEARN_MIN_SAMPLES tín hiệu mới mới đề xuất."""
        since = time.time() - cfg.LEARN_WINDOW_DAYS * 86400
        rows = self.store.resolved_signals(since)
        cur = [r for r in rows if "entry_ref" in _feat(r)]
        legacy, rows = len(rows) - len(cur), cur
        tech = [r for r in rows if r["source"] in TECH_SOURCES]
        ab = strategy.active_blocks()
        # v3.3: trọng số trigger và ngưỡng điểm chỉ đổi điểm của tín hiệu KHÔNG bị luật đang chặn – tín hiệu luật chặn (sẵn
        # hoặc tự học) không vào lệnh được dù điểm thế nào, nên không được quyết định chúng (bản cũ: pullback 0.82 từ 32 tín
        # hiệu, 22 trong đó vốn đã bị luật chặn sẵn). Thống kê theo luật (chặn / gỡ chặn) vẫn dùng mọi tín hiệu.
        open_ = [r for r in tech if not (set(strategy.matching_rules(r["side"], _feat(r))) & ab)]
        st = {"ts": time.time(), "window_days": cfg.LEARN_WINDOW_DAYS, "legacy_excluded": legacy,
              "min_days": cfg.LEARN_MIN_DAYS, "total": _agg(rows), "tech": _agg(tech),
              "trigger_blocked_excluded": len(tech) - len(open_),
              "pending": len(self.store.pending_signals(500)), "by_source": {}, "by_side": {},
              "by_trigger": {}, "by_rule": {}, "by_score": {}, "by_session": {}}
        for src in list(TECH_SOURCES) + ["below_score"]:     # mọi nguồn, kể cả slot_taken / max_positions (v3.3)
            sub = [r for r in rows if r["source"] == src]
            if sub:
                st["by_source"][src] = {**_agg(sub), "name": SOURCE_NAMES.get(src, src)}
        for side in ("long", "short"):
            st["by_side"][side] = _agg([r for r in tech if r["side"] == side])
        trig = defaultdict(list)
        rule = defaultdict(list)
        sess = defaultdict(list)
        score = defaultdict(list)
        for r in open_:
            for k in dict.fromkeys(_feat(r).get("triggers") or []):
                trig[k].append(r)
        for r in tech:
            f = _feat(r)
            for k in strategy.matching_rules(r["side"], f):
                rule[k].append(r)
            h = f.get("hour", 12)
            sess["0h–7h" if h < 7 else "7h–13h" if h < 13 else "13h–19h" if h < 19 else "19h–24h"].append(r)
            score[f"{math.floor((r['score'] or 0) * 2) / 2:.1f}"].append(r)
        for r in rows:
            if r["source"] == "below_score":
                score[f"{math.floor((r['score'] or 0) * 2) / 2:.1f}"].append(r)
        st["by_trigger"] = {k: {**_agg(v), "name": strategy.TRIGGER_NAMES.get(k, k),
                                "weight": (strategy.LEARNED.get("trigger_w") or {}).get(k, 1.0)} for k, v in trig.items()}
        st["by_rule"] = {k: {**_agg(v), "name": strategy.RULES[k][0], "blocked": k in ab}
                         for k, v in rule.items()}
        for k in ab:
            st["by_rule"].setdefault(k, {**_agg([]), "name": strategy.RULES[k][0], "blocked": True})
        st["by_session"] = {k: _agg(v) for k, v in sorted(sess.items())}
        st["by_score"] = {k: _agg(v) for k, v in sorted(score.items(), key=lambda x: float(x[0]))}
        # So sánh cách thoát lệnh trên cùng tập tín hiệu kỹ thuật
        both = [r for r in tech if r.get("r_fixed") is not None and r.get("r_hybrid") is not None and r.get("r_trail") is not None]
        from .engine import current_exit_mode
        cur_mode = current_exit_mode()
        st["exit_compare"] = {"n": len(both), "current": cur_mode, "configured": cfg.EXIT_MODE,
                              "modes": {m: {"exp_r": round(sum(r[f"r_{m}"] for r in both) / len(both), 3) if both else 0,
                                            "sum_r": round(sum(r[f"r_{m}"] for r in both), 2),
                                            "winrate": round(100 * sum(1 for r in both if r[f"r_{m}"] > 0) / len(both), 1) if both else 0,
                                            "best": round(max((r[f"r_{m}"] for r in both), default=0), 2)}
                                        for m in exits.MODES}}
        # v3.3: biến thể A/B "SL 2.5 ATR + bám sóng toàn bộ" trên CÙNG tín hiệu (setup xu hướng có kết quả biến thể) –
        # chỉ để so sánh, bộ tự học không chuyển sang. Hiệu từng cặp với cách đang dùng, sai số chuẩn gom cụm theo ngày.
        alt = [r for r in both if r.get("r_sl25_trail") is not None]
        _, d_m, d_se, d_t, d_days = _paired([(r["ts"], r["r_sl25_trail"] - r[f"r_{cur_mode}"]) for r in alt], 0, 1)
        st["exit_compare"]["alt"] = {**_agg(alt, value=lambda r: r["r_sl25_trail"]), "name": exits.ALT_NAME,
                                     "best": round(max((r["r_sl25_trail"] for r in alt), default=0), 2),
                                     "cur_exp_r": round(sum(r[f"r_{cur_mode}"] for r in alt) / len(alt), 3) if alt else 0,
                                     "diff": d_m, "diff_se": d_se, "diff_t": d_t, "diff_days": d_days}
        # So sánh các mức TP1 (hybrid) – chọn theo LỢI NHUẬN KỲ VỌNG, không chỉ tỉ lệ thắng
        tv = [d for _, d in self._tp1_vals(tech)]
        lv = {}
        for x in exits.TP1_CANDIDATES:
            vals = [d[str(x)] for d in tv if d.get(str(x)) is not None]
            if vals:
                lv[f"{x:g}"] = {"n": len(vals), "exp_r": round(sum(vals) / len(vals), 3),
                                "winrate": round(100 * sum(1 for v in vals if v > 0) / len(vals), 1),
                                "sum_r": round(sum(vals), 2)}
        st["tp1_compare"] = {"n": len(tv), "current": exits.current_tp1_r(), "levels": lv}
        # AI có giúp ích không: tín hiệu AI chặn mà hoá ra thua = AI đúng
        rej = [r for r in rows if r["source"] == "ai_rejected"]
        tak = [r for r in rows if r["source"] == "taken"]
        ovr = [r for r in rows if r["source"] == "ai_override"]
        st["ai_veto"] = {"rejected": _agg(rej), "taken": _agg(tak), "override": _agg(ovr),
                         "opposed": _agg(rej + ovr), "veto_conf": current_veto_conf(),
                         "veto_accuracy": round(100 * sum(1 for r in rej if (r["outcome_r"] or 0) <= 0) / len(rej), 1) if rej else None}
        self.last_stats = st
        self.store.set("learn_stats", st)
        return st, rows, tech

    @staticmethod
    def _tp1_vals(tech):
        """[(ts, {mức TP1: R})] của các tín hiệu có kết quả hybrid theo từng mức TP1."""
        out = []
        for r in tech:
            try:
                v = json.loads(r.get("r_tp1v") or "null")
            except Exception:
                v = None
            if v:
                out.append((r["ts"], v))
        return out

    # ================================================================ 3. tự điều chỉnh
    def _cool(self, key):
        last = (self.store.get("learn_last") or {}).get(key, 0)
        return time.time() - last < cfg.LEARN_COOLDOWN_H * 3600

    def _touch(self, key):
        d = self.store.get("learn_last") or {}
        d[key] = time.time()
        self.store.set("learn_last", d)

    def learn(self):
        if self.mode == "off":
            return []
        st, rows, tech = self.compute_stats()
        ab = strategy.active_blocks()

        def unblocked(r):
            return not (set(strategy.matching_rules(r["side"], _feat(r))) & ab)

        N = cfg.LEARN_MIN_SAMPLES
        L = strategy.LEARNED
        proposals = []

        # a) luật lọc
        blocks = strategy.active_blocks()
        for k, v in st["by_rule"].items():
            if k in strategy.NEVER_BLOCK:        # v3.2: không chặn cả phía – đó là regime, không phải luật
                continue
            need = N
            if v["n"] < need or self._cool("rule:" + k):
                continue
            if k not in blocks and _confident(v, -0.25, need):
                proposals.append(dict(kind="block", key=k, old=False, new=True, n=v["n"], exp=v["exp_r"],
                                      reason=f"Nhóm \"{v['name']}\": {v['n']} tín hiệu / {v['days']} ngày, kỳ vọng {v['exp_r']:+.2f}R "
                                             f"(t {v['t']:+.1f}), thắng {v['winrate']}% → chặn"))
            elif k in blocks and _confident(v, 0.1, need):
                proposals.append(dict(kind="unblock", key=k, old=True, new=False, n=v["n"], exp=v["exp_r"],
                                      reason=f"Nhóm \"{v['name']}\" đã cải thiện: {v['n']} tín hiệu / {v['days']} ngày, kỳ vọng "
                                             f"{v['exp_r']:+.2f}R (t {v['t']:+.1f}) → bỏ chặn"))

        # b) trọng số trigger – by_trigger chỉ gồm tín hiệu không bị luật đang chặn (compute_stats); áp dụng theo
        # strategy.trigger_group_weights (trung bình trong nhóm) nên trọng số học được luôn có tác dụng
        tw = dict(L.get("trigger_w") or {})
        for k, v in st["by_trigger"].items():
            if v["n"] < N or self._cool("trig:" + k):
                continue
            cur = tw.get(k, 1.0)
            # vùng chết ±0.1R và phải có ý nghĩa thống kê – trọng số không còn nhảy qua lại theo nhiễu
            if not (_confident(v, 0.1, N) or _confident(v, -0.1, N)):
                continue
            target = max(0.6, min(1.4, 1 + 0.5 * v["exp_r"]))
            new = round(cur + max(-0.2, min(0.2, target - cur)), 2)
            if abs(new - cur) >= 0.1:
                proposals.append(dict(kind="trigger_w", key=k, old=cur, new=new, n=v["n"], exp=v["exp_r"],
                                      reason=f"Trigger \"{v['name']}\": {v['n']} tín hiệu không bị luật chặn / {v['days']} ngày, "
                                             f"kỳ vọng {v['exp_r']:+.2f}R (t {v['t']:+.1f}) → trọng số {cur} → {new}"))

        # c) ngưỡng điểm – chỉ tín hiệu không bị luật đang chặn (ngưỡng không đổi được số phận tín hiệu bị chặn)
        cur = L.get("min_score") or cfg.MIN_SCORE
        if not self._cool("min_score"):
            below = [r for r in rows if r["source"] == "below_score" and (r["score"] or 0) >= cur - 1 and unblocked(r)]
            just = [r for r in tech if cur <= (r["score"] or 0) < cur + 1 and unblocked(r)]
            a, b = _agg(below), _agg(just)
            if _confident(a, 0.15, N) and cur - 0.5 >= 4.0:
                proposals.append(dict(kind="min_score", key="min_score", old=cur, new=cur - 0.5, n=a["n"], exp=a["exp_r"],
                                      reason=f"Tín hiệu điểm {cur-1}–{cur} bị loại nhưng có kỳ vọng {a['exp_r']:+.2f}R ({a['n']} mẫu) → hạ ngưỡng"))
            elif _confident(b, -0.2, N) and cur + 0.5 <= 7.0:
                proposals.append(dict(kind="min_score", key="min_score", old=cur, new=cur + 0.5, n=b["n"], exp=b["exp_r"],
                                      reason=f"Tín hiệu điểm {cur}–{cur+1} có kỳ vọng {b['exp_r']:+.2f}R ({b['n']} mẫu) → nâng ngưỡng"))

        # d) cách thoát lệnh (chỉ khi EXIT_MODE=auto)
        ec = st.get("exit_compare") or {}
        both = [r for r in tech if all(r.get(f"r_{m}") is not None for m in exits.MODES)]
        # v3.2: cần ≥ LEARN_EXIT_MIN_SAMPLES (60) cặp – bản cũ đổi hybrid→trail trên 31 tín hiệu của 2 ngày thuận,
        # trong khi trên 211 tín hiệu hybrid vẫn nhỉnh hơn trail.
        need_exit = max(N, cfg.LEARN_EXIT_MIN_SAMPLES)
        if cfg.EXIT_MODE == "auto" and len(both) >= need_exit and not self._cool("exit_mode"):
            cur = L.get("exit_mode") or "hybrid"
            best = max(exits.MODES, key=lambda m: ec["modes"][m]["exp_r"])
            ok = _paired([(r["ts"], r[f"r_{best}"] - r[f"r_{cur}"]) for r in both], 0.25, need_exit)[0]
            if best != cur and ok:
                proposals.append(dict(kind="exit_mode", key="exit_mode", old=cur, new=best, n=ec["n"], exp=ec["modes"][best]["exp_r"],
                                      reason=f"Cách thoát \"{EXIT_NAMES[best]}\" cho {ec['modes'][best]['exp_r']:+.2f}R/tín hiệu so với \"{EXIT_NAMES[cur]}\" {ec['modes'][cur]['exp_r']:+.2f}R trên {ec['n']} tín hiệu → chuyển"))

        # f) mức TP1 cho lợi nhuận kỳ vọng cao nhất (chỉ khi đang/có thể dùng hybrid)
        tc = st.get("tp1_compare") or {}
        # chọn 1 trong 5 mức trên mẫu nhỏ gần như luôn "thấy" mức tốt hơn (thiên lệch chọn lọc) → cần ≥ 40 mẫu,
        # so sánh theo cặp trên cùng tín hiệu và chênh ≥ 0.15R có ý nghĩa thống kê
        if cfg.EXIT_MODE in ("auto", "hybrid") and tc.get("n", 0) >= max(N, 40) and not self._cool("tp1_r"):
            cur = exits.current_tp1_r()
            lv = tc["levels"]
            if lv:
                best = max(lv, key=lambda k: lv[k]["exp_r"])
                cur_k = f"{cur:g}"
                cur_exp = lv.get(cur_k, {}).get("exp_r", -9)
                pairs = [(ts, d.get(best), d.get(cur_k)) for ts, d in self._tp1_vals(tech)]
                ok = _paired([(ts, a - b) for ts, a, b in pairs if a is not None and b is not None], 0.15, max(N, 40))[0]
                if float(best) != cur and ok:
                    proposals.append(dict(kind="tp1_r", key="tp1_r", old=cur, new=float(best), n=tc["n"], exp=lv[best]["exp_r"],
                                          reason=f"TP1 {best}R cho {lv[best]['exp_r']:+.2f}R/lệnh (thắng {lv[best]['winrate']}%) "
                                                 f"so với TP1 {cur_k}R {cur_exp:+.2f}R trên {tc['n']} tín hiệu → đổi TP1"))

        # e) ngưỡng phủ quyết AI: tín hiệu AI phản đối (chặn + bị vượt quyền) thực tế thắng hay thua?
        opp = st["ai_veto"]["opposed"]
        if opp["n"] >= N and not self._cool("ai_veto_conf"):
            cur = current_veto_conf()
            if _confident(opp, 0.15, N) and cur < 95:
                new = min(95, cur + 5)
                proposals.append(dict(kind="ai_veto_conf", key="ai_veto_conf", old=cur, new=new, n=opp["n"], exp=opp["exp_r"],
                                      reason=f"Tín hiệu AI phản đối vẫn có kỳ vọng {opp['exp_r']:+.2f}R ({opp['n']} mẫu) → AI phải chắc ≥{new}% mới được chặn"))
            elif _confident(opp, -0.2, N) and cur > 0:
                new = max(0, cur - 5)
                proposals.append(dict(kind="ai_veto_conf", key="ai_veto_conf", old=cur, new=new, n=opp["n"], exp=opp["exp_r"],
                                      reason=f"Tín hiệu AI phản đối có kỳ vọng {opp['exp_r']:+.2f}R ({opp['n']} mẫu) – AI chặn đúng → ngưỡng phủ quyết {cur}% → {new}%"))

        pending = {(r["kind"], r["k"]) for r in self.store.learn_log(200) if r["status"] == "pending"}
        for p in proposals:
            if self.mode == "auto":
                self._apply(p)
                self.store.add_learn(p["kind"], p["key"], p["old"], p["new"], p["reason"], p["n"], p["exp"], "applied")
                self.notify("🧬 TỰ HỌC – đã áp dụng\n" + p["reason"])
            elif (p["kind"], p["key"]) not in pending:
                self.store.add_learn(p["kind"], p["key"], p["old"], p["new"], p["reason"], p["n"], p["exp"], "pending")
                self.notify("🧬 TỰ HỌC – đề xuất (chờ duyệt trên web hoặc /apply)\n" + p["reason"])
        if proposals:
            log.info("Tự học: %d thay đổi (%s)", len(proposals), self.mode)
        return proposals

    def _apply(self, p):
        L = strategy.LEARNED
        k = p["kind"]
        if k == "block":
            L["blocks"] = sorted(set(L.get("blocks") or []) | {p["key"]})
            L["unblocked"] = sorted(set(L.get("unblocked") or []) - {p["key"]})
            self._touch("rule:" + p["key"])
        elif k == "unblock":
            L["blocks"] = sorted(set(L.get("blocks") or []) - {p["key"]})
            if p["key"] in cfg.DEFAULT_BLOCKS:          # luật chặn sẵn → ghi nhớ đã gỡ
                L["unblocked"] = sorted(set(L.get("unblocked") or []) | {p["key"]})
            self._touch("rule:" + p["key"])
        elif k == "trigger_w":
            tw = dict(L.get("trigger_w") or {})
            tw[p["key"]] = p["new"]
            L["trigger_w"] = tw
            self._touch("trig:" + p["key"])
        elif k == "min_score":
            L["min_score"] = float(p["new"])
            self._touch("min_score")
        elif k == "exit_mode":
            L["exit_mode"] = p["new"]
            self._touch("exit_mode")
        elif k == "tp1_r":
            L["tp1_r"] = float(p["new"])
            self._touch("tp1_r")
        elif k == "ai_veto_conf":
            L["ai_veto_conf"] = int(p["new"])
            self._touch("ai_veto_conf")
        self.store.set("learned", L)
        log.info("Tự học áp dụng: %s %s → %s", k, p["key"], p["new"])

    # ---- duyệt / khôi phục (web & Telegram)
    def apply_pending(self, ts=None):
        n = 0
        for r in self.store.learn_log(200):
            if r["status"] != "pending" or (ts is not None and abs(r["ts"] - float(ts)) > 1e-3):
                continue
            p = dict(kind=r["kind"], key=r["k"], old=json.loads(r["old"]), new=json.loads(r["new"]))
            self._apply(p)
            self.store.set_learn_status(r["ts"], "applied")
            n += 1
        return n

    def reject_pending(self, ts):
        for r in self.store.learn_log(200):
            if r["status"] == "pending" and abs(r["ts"] - float(ts)) < 1e-3:
                self.store.set_learn_status(r["ts"], "rejected")
                return 1
        return 0

    def apply_suggestion(self, param, value):
        """Áp dụng 1 gợi ý của AI tổng kết ngày (chỉ trong danh mục cho phép)."""
        L = strategy.LEARNED
        if param == "min_score":
            v = max(4.0, min(7.0, round(float(value) * 2) / 2))
            p = dict(kind="min_score", key="min_score", old=L.get("min_score") or cfg.MIN_SCORE, new=v)
        elif param in ("block", "unblock") and value in strategy.RULES:
            p = dict(kind=param, key=value, old=param == "unblock", new=param == "block")
        else:
            return False
        self._apply(p)
        self.store.add_learn(p["kind"], p["key"], p["old"], p["new"], "Áp dụng gợi ý AI tổng kết ngày (duyệt thủ công)", 0, 0, "applied")
        return True

    def reset(self):
        old = dict(strategy.LEARNED)
        strategy.LEARNED.clear()
        strategy.LEARNED.update({"min_score": None, "trigger_w": {}, "blocks": [], "unblocked": [], "exit_mode": None,
                                 "ai_veto_conf": None, "tp1_r": None, "schema": self.LEARNED_SCHEMA})
        self.store.set("learned", strategy.LEARNED)
        self.store.set("learn_last", {})
        self.store.add_learn("reset", "all", old, strategy.LEARNED, "Khôi phục tham số mặc định (thủ công)", 0, 0, "applied")
        log.info("Tự học: đã khôi phục mặc định")

    # ================================================================ 4. AI tổng kết ngày (1 lượt AI/ngày)
    def maybe_daily_ai_review(self):
        if not cfg.AI_DAILY_REVIEW or not cfg.AI_ENABLED:
            return
        now = dt.datetime.now()
        key = now.strftime("%Y-%m-%d")
        if now.hour < cfg.AI_DAILY_REVIEW_HOUR or self.store.get("ai_daily_key") == key:
            return
        st = self.last_stats or self.compute_stats()[0]
        if st["total"]["n"] < 5:
            self.store.set("ai_daily_key", key)
            return
        self.store.set("ai_daily_key", key)
        payload = {
            "stats": {k: st[k] for k in ("total", "tech", "by_source", "by_side", "by_trigger", "by_rule",
                                          "by_score", "by_session", "ai_veto", "exit_compare", "tp1_compare")},
            "current_params": {"min_score": strategy.LEARNED.get("min_score") or cfg.MIN_SCORE,
                               "blocked_rules": strategy.LEARNED.get("blocks"),
                               "trigger_weights": strategy.LEARNED.get("trigger_w"),
                               "ai_min_confidence": cfg.AI_MIN_CONFIDENCE},
            "account": self.store.stats(),
            "recent_lessons": self.store.recent_lessons(8),
        }
        system = SYSTEM_DAILY % ", ".join(strategy.RULES.keys())
        res = self.ai._ask(system, "Thống kê:\n" + json.dumps(payload, ensure_ascii=False), "summary", kind="daily")
        if not res:
            return
        res["ts"] = time.time()
        res["suggestions"] = [s for s in (res.get("suggestions") or [])
                              if s.get("param") in ("min_score", "block", "unblock")][:4]
        self.store.set("ai_daily_review", res)
        self.notify("📈 AI TỔNG KẾT NGÀY\n" + str(res.get("summary", ""))[:600] +
                    ("\nGợi ý: " + "; ".join(f"{s['param']}={s.get('value')}" for s in res["suggestions"]) if res["suggestions"] else ""))

    # ================================================================ 5. dữ liệu cho web
    def public_state(self):
        L = strategy.LEARNED
        return {
            "mode": self.mode, "min_samples": cfg.LEARN_MIN_SAMPLES, "window_days": cfg.LEARN_WINDOW_DAYS,
            "min_days": cfg.LEARN_MIN_DAYS, "min_t": cfg.LEARN_MIN_T,
            "params": {"min_score": L.get("min_score") or cfg.MIN_SCORE, "min_score_default": cfg.MIN_SCORE,
                       "trigger_w": L.get("trigger_w") or {}, "blocks": sorted(strategy.active_blocks()),
                       "exit_mode": L.get("exit_mode"), "ai_veto_conf": current_veto_conf(),
                       "tp1_r": exits.current_tp1_r(),
                       "ai_override_min_score": cfg.AI_OVERRIDE_MIN_SCORE},
            "exit_names": EXIT_NAMES, "exit_configured": cfg.EXIT_MODE,
            "rules": {k: v[0] for k, v in strategy.RULES.items()},
            "trigger_names": strategy.TRIGGER_NAMES,
            "stats": self.last_stats, "log": self.store.learn_log(30),
            "daily": self.store.get("ai_daily_review"),
        }

    # ================================================================ 6. chu kỳ
    def tick(self):
        try:
            self._flush_notice()
        except Exception as e:
            log.warning("learn notice: %s", e)
        now = time.time()
        if now - self.last_resolve > 300:
            self.last_resolve = now
            try:
                self.resolve_pending()
            except Exception as e:
                log.warning("resolve_pending: %s", e)
        if now - self.last_learn > 3600:
            self.last_learn = now
            try:
                self.learn() if self.mode != "off" else self.compute_stats()
            except Exception as e:
                log.warning("learn: %s", e)
        try:
            self.maybe_daily_ai_review()
        except Exception as e:
            log.warning("daily review: %s", e)
