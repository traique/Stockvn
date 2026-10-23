"""Bộ lọc AI – gọi tối đa 2 lần cho mỗi lệnh:
  1. analyze(): xác nhận tín hiệu trước khi vào lệnh
  2. review():  tổng kết sau khi đóng lệnh, rút bài học → được đưa lại vào prompt lần phân tích sau

Hỗ trợ nhiều provider OpenAI-compatible (9Router local, OpenRouter, custom), tự fallback.
Có giới hạn số lần gọi mỗi ngày (AI_MAX_CALLS_PER_DAY).
"""
import json
import logging
import re
import time
import datetime as dt
import requests
from .config import cfg

log = logging.getLogger("ai")

SYSTEM_ANALYZE = ("""Bạn là Giám đốc Quản trị Rủi ro (Chief Risk Officer) cho quỹ giao dịch hợp đồng tương lai Altcoin.
Mục tiêu: LỢI NHUẬN KỲ VỌNG DƯƠNG (tỉ lệ thắng × mức lãi – tỉ lệ thua × mức lỗ). Duyệt lệnh có lợi thế rõ,
từ chối lệnh có rủi ro CỤ THỂ – không từ chối chỉ vì lý do chung chung, vì bỏ lỡ lệnh tốt cũng làm giảm lợi nhuận.
Hệ thống đã lọc kỹ thuật (xu hướng 1h, ADX, volume, cổng BTC, chống râu nến, điểm hội tụ + dòng tiền) và gửi bạn 1 ứng viên tốt nhất.
Chỉ DUYỆT khi thoả cả 3 tiêu chí:
1. Cấu trúc nến sạch: không có nến râu dài từ chối giá ngay sát vùng vào lệnh; không có kháng cự/hỗ trợ mạnh ngay trước mặt
   (xem khoảng cách tới đỉnh/đáy 20 nến tính bằng ATR); giá chưa chạy quá xa.
2. Đồng thuận dòng tiền: ưu tiên oi_chg_1h >= 0 (có tiền mới); ls_ratio không quá lệch về phía đám đông
   (Long cần ls_ratio < 2.2, Short cần ls_ratio > 0.6); funding không cực đoan cùng phía lệnh.
3. Thuận xu hướng BTC (btc_trend_1h, btc_chg_1h, btc_chg_4h).
Nếu thiếu dữ liệu một tiêu chí thì đánh giá dựa trên các tiêu chí còn lại, không tự suy diễn.
Nếu setup = "range" (khung 1h đi ngang, đánh đảo chiều tại biên Bollinger, TP = dải giữa bb_mid): nến râu từ chối TẠI BIÊN
là điểm CỘNG; tiêu chí 2–3 đổi thành: BTC không chạy mạnh ngược hướng lệnh, không có dấu hiệu bứt phá khỏi vùng
(volume bùng nổ theo hướng phá biên, OI tăng mạnh cùng chiều phá biên), và TP dải giữa không bị chặn bởi kháng cự/hỗ trợ.
RSI nằm trong vùng hệ thống cho phép (long """ + f"{cfg.RSI_LONG_MIN:g}–{cfg.RSI_LONG_MAX:g}, short {cfg.RSI_SHORT_MIN:g}–{cfg.RSI_SHORT_MAX:g}" + """) là động lượng bình thường, tự nó không phải lý do từ chối.
Cũng tránh lặp lại các lỗi trong "lessons_from_recent_trades".
Ý nghĩa "confidence" (0-100) – BẮT BUỘC theo đúng định nghĩa:
- Nếu decision trùng proposed_side: confidence = CHẤT LƯỢNG setup so với tín hiệu trung bình đã qua bộ lọc
  (50 = trung bình; 60–70 = tốt hơn rõ, có lý do cụ thể; 80+ = rất hiếm, mọi tiêu chí đều đẹp).
  Hệ thống vào lệnh khi confidence >= """ + str(cfg.AI_MIN_CONFIDENCE) + """.
- Nếu decision == "SKIP": confidence = mức độ NGUY HIỂM của kèo (càng nhiều dấu hiệu bẫy giá, phân kỳ, ngược dòng tiền, ngược BTC thì càng cao).
Dữ liệu: "candles_15m" mô tả 6 nến gần nhất đã chuẩn hoá theo ATR (thân, râu, close_pos 0=đóng đáy…1=đóng đỉnh, vol so trung bình);
oi_chg_1h + px_chg_1h – giá đi cùng OI tăng là tiền mới, OI giảm là chỉ đóng vị thế; ls_ratio – tỉ lệ tài khoản long/short.
TRẢ LỜI DUY NHẤT bằng JSON hợp lệ:
{"decision": "LONG" | "SHORT" | "SKIP", "confidence": 0-100, "reason": "tối đa 2 câu tiếng Việt, nêu tiêu chí nào đạt/không đạt"}""")

SYSTEM_REVIEW = """Bạn là huấn luyện viên giao dịch. Bạn nhận dữ liệu 1 lệnh vừa đóng (tín hiệu lúc vào, lý do AI đồng ý, kết quả).
Hãy tổng kết ngắn gọn, thẳng thắn: lệnh này thắng/thua vì đâu, bài học gì áp dụng cho lệnh sau trên altcoin.
QUAN TRỌNG: mục "facts" là số liệu ĐÃ TÍNH SẴN từ dữ liệu thật (khoảng cách SL theo ATR, kết quả theo R, giá chạy
thuận tối đa MFE, ngược tối đa MAE). Chỉ dùng đúng các con số đó, KHÔNG tự tính lại, KHÔNG bịa số khác.
SL cách ≥ 1.5 ATR là theo đúng quy tắc hệ thống → không được kết luận "SL quá sát"; nếu MFE ≥ 1R rồi mới thua thì
bài học là về quản lý lệnh (chốt/dời SL), không phải điểm vào.
TRẢ LỜI DUY NHẤT bằng JSON hợp lệ:
{"verdict": "GOOD_TRADE" | "BAD_TRADE" | "LUCKY" | "UNLUCKY", "lesson": "1-2 câu tiếng Việt, cụ thể, có thể hành động",
 "tags": ["tối đa 3 từ khoá ngắn"]}"""


# model free không phù hợp để phân tích (an toàn nội dung, âm thanh, quá nhỏ)
_OR_BLOCK = ("guard", "safety", "lyria", "audio", "tts", "whisper", "embed", "-1b", "-2b", "-3b", "e2b", "e4b", "vl-",
             "inkling")   # inkling: chỉ cho agent harness (403)
# ưu tiên model lớn, giỏi suy luận/JSON
# thứ tự: model trả lời nhanh, ra JSON gọn trước; model suy luận dài (chậm, dễ hết token) để sau
_OR_PREFER = ("gemma-4-31b", "nemotron-3-super", "deepseek", "qwen3", "minimax", "llama-4", "gemma-4-26b",
              "glm", "kimi", "gpt-oss", "mistral", "laguna", "nemotron-3-ultra", "nemotron")


_OR_CACHE_V = 2     # tăng khi đổi luật lọc/xếp hạng → bỏ danh sách cũ đã lưu
_JSON_ONLY = ("\n\nQUAN TRỌNG: KHÔNG suy nghĩ thành lời, KHÔNG giải thích ngoài JSON. "
              "Chỉ in ra đúng 1 object JSON trên 1 dòng, bắt đầu bằng { và kết thúc bằng }. "
              "Phần chữ trong JSON viết hoàn toàn bằng tiếng Việt.")


class AIHTTPError(RuntimeError):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status = status


def _extract_json(text, required_key=None):
    """Tìm object JSON hợp lệ trong câu trả lời (kể cả khi model 'nghĩ' thành lời trước đó).
    Nếu JSON bị cắt giữa chừng (hết token) thì cứu các trường đơn giản bằng regex."""
    if not text:
        return None
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    text = text.replace("```json", "```")
    dec = json.JSONDecoder()
    best = None
    for m in re.finditer(r"\{", text):
        try:
            obj, _ = dec.raw_decode(text[m.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            if required_key is None or required_key in obj:
                best = obj          # lấy object hợp lệ CUỐI cùng (thường là đáp án)
    if best is not None:
        return best
    # cứu JSON bị cắt: lấy các cặp "key": "chuỗi" | số | [..]
    out = {}
    for k, v in re.findall(r'"(\w+)"\s*:\s*("(?:[^"\\]|\\.)*"|-?\d+(?:\.\d+)?)', text):
        try:
            out[k] = json.loads(v)
        except Exception:
            pass
    if required_key and required_key in out:
        out["_salvaged"] = True
        return out
    return None


class AIFilter:
    def __init__(self, store):
        self.store = store
        self.cooldown = {}        # (provider, model) -> timestamp cho phép dùng lại
        self.last_model = None
        self.stats = {"calls": 0, "fails": 0}
        # thống kê theo từng model (lưu DB để không mất khi khởi động lại)
        self.model_stats = self.store.get("ai_model_stats") or {}
        self.last_result = self.store.get("ai_last_result")
        self.fail_streak = {}      # (provider, model) -> số lần lỗi liên tiếp
        self.dead = set()          # model trả 404 (đã bị gỡ) → bỏ qua tới lần dò lại
        self._last_refresh = time.time()
        self.last_error = None
        self.providers = self._build_providers()
        log.info("AI providers: %s", [(p["name"], len(p["models"])) for p in self.providers])

    # ------------------------------------------------------------ provider
    def _build_providers(self):
        table = {
            "9router": dict(name="9router", base=cfg.NINEROUTER_BASE_URL, key=cfg.NINEROUTER_API_KEY,
                            models=cfg.NINEROUTER_MODELS or ["auto"]),
            "openrouter": dict(name="openrouter", base=cfg.OPENROUTER_BASE_URL, key=cfg.OPENROUTER_API_KEY,
                               models=self._openrouter_models()),
            "custom": dict(name="custom", base=cfg.CUSTOM_BASE_URL, key=cfg.CUSTOM_API_KEY, models=cfg.CUSTOM_MODELS),
        }
        out = []
        for name in cfg.AI_PROVIDER_ORDER:
            p = table.get(name)
            if not p or not p["base"] or not p["models"]:
                continue
            if name == "openrouter" and not p["key"]:
                continue
            out.append(p)
        return out

    # ------------------------------------------------------------ OpenRouter: tự dò model free
    def _discover_openrouter(self):
        """Lấy danh sách model ':free' đang còn sống trên OpenRouter (API công khai, không tốn lượt)."""
        r = requests.get(f"{cfg.OPENROUTER_BASE_URL}/models", timeout=20)
        r.raise_for_status()
        found = []
        for m in r.json().get("data", []):
            mid = m.get("id", "")
            if not (mid.endswith(":free") or mid == "openrouter/free"):
                continue
            if any(b in mid.lower() for b in _OR_BLOCK):
                continue
            arch = m.get("architecture") or {}
            outs = arch.get("output_modalities") or ["text"]
            if "text" not in outs:
                continue
            ctx = int(m.get("context_length") or 0)
            if ctx and ctx < 8000:
                continue
            found.append(mid)

        def rank(mid):
            for i, pref in enumerate(_OR_PREFER):
                if pref in mid:
                    return i
            return len(_OR_PREFER) + (100 if mid == "openrouter/free" else 0)
        found.sort(key=rank)
        has_router = "openrouter/free" in found
        found = [m for m in found if m != "openrouter/free"][:cfg.OPENROUTER_MAX_MODELS]
        if has_router:                          # router tự chọn → để cuối làm lưới an toàn
            found.append("openrouter/free")
        return found

    def _openrouter_models(self, force=False):
        manual = [m for m in cfg.OPENROUTER_MODELS if m.lower() != "auto"]
        auto = not manual or "auto" in [m.lower() for m in cfg.OPENROUTER_MODELS]
        cache = self.store.get("ai_or_models") or {}
        fresh = time.time() - cache.get("ts", 0) < 12 * 3600 and cache.get("v") == _OR_CACHE_V
        models = list(cache.get("models") or [])
        if auto and (force or not fresh or not models):
            try:
                models = self._discover_openrouter()
                self.store.set("ai_or_models", {"ts": time.time(), "models": models, "v": _OR_CACHE_V})
                log.info("OpenRouter: %d model free đang hoạt động: %s", len(models), ", ".join(models[:6]))
            except Exception as e:
                log.warning("OpenRouter: không lấy được danh sách model (%s)", e)
        if not auto:
            return manual
        # model khai báo tay (nếu có) được ưu tiên trước, sau đó là danh sách tự dò
        out = [m for m in manual if m not in self.dead] + [m for m in models if m not in manual and m not in self.dead]
        return out or ["openrouter/free"]

    def refresh_models(self, force=False):
        for p in self.providers:
            if p["name"] == "openrouter":
                p["models"] = self._openrouter_models(force=force)
        self._last_refresh = time.time()

    def _chat(self, provider, model, system, user):
        headers = {"Authorization": f"Bearer {provider['key'] or 'none'}", "Content-Type": "application/json"}
        if provider["name"] == "openrouter":
            headers.update({"HTTP-Referer": "https://github.com/okx-ai-bot", "X-Title": "OKX AI Bot"})
        system = system + _JSON_ONLY
        body = {"model": model, "temperature": 0.2, "max_tokens": cfg.AI_MAX_TOKENS,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if provider["name"] == "openrouter":
            # model suy luận: nghĩ ít, không trả phần suy nghĩ → dành token cho JSON
            body["reasoning"] = {"effort": "low", "exclude": True}
        r = requests.post(f"{provider['base']}/chat/completions", headers=headers, json=body, timeout=(cfg.NINEROUTER_TIMEOUT if provider["name"] == "9router" else cfg.AI_TIMEOUT))
        if r.status_code == 429:
            raise AIHTTPError(429, "rate limited (hết lượt/quá tải)")
        if r.status_code in (400, 401, 402, 403, 404):
            try:
                msg = r.json().get("error", {})
                msg = msg.get("message", "") if isinstance(msg, dict) else str(msg)
            except Exception:
                msg = r.text[:120]
            raise AIHTTPError(r.status_code, f"HTTP {r.status_code}: {msg[:140]}")
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            raise RuntimeError(data["error"].get("message", "error") if isinstance(data["error"], dict) else data["error"])
        msg = (data.get("choices") or [{}])[0].get("message") or {}
        content = msg.get("content") or ""
        if not content.strip():
            # một số model để toàn bộ câu trả lời trong trường reasoning
            content = msg.get("reasoning") or msg.get("reasoning_content") or ""
        return content

    # ------------------------------------------------------------ hạn mức ngày
    def _day(self):
        return dt.datetime.now().strftime("%Y-%m-%d")

    def calls_today(self):
        d = self.store.get("ai_calls") or {}
        return int(d.get(self._day(), 0))

    def _count_call(self):
        d = {self._day(): self.calls_today() + 1}
        self.store.set("ai_calls", d)

    def budget_left(self):
        return cfg.AI_MAX_CALLS_PER_DAY - self.calls_today()

    def _record(self, key, ok, ms, err=None, kind=None):
        m = self.model_stats.setdefault(key, {"calls": 0, "ok": 0, "fail": 0, "ms_total": 0, "last_ts": 0,
                                               "last_error": "", "analyze": 0, "review": 0})
        m["calls"] += 1
        m["ok" if ok else "fail"] += 1
        m["ms_total"] += int(ms)
        m["last_ts"] = time.time()
        if err:
            m["last_error"] = str(err)[:160]
        if ok and kind:
            m[kind] = m.get(kind, 0) + 1
        try:
            self.store.set("ai_model_stats", self.model_stats)
        except Exception:
            pass

    def public_stats(self):
        """Dữ liệu cho dashboard / Telegram."""
        rows = []
        for key, m in self.model_stats.items():
            rows.append({"model": key, "calls": m["calls"], "ok": m["ok"], "fail": m["fail"],
                         "avg_ms": int(m["ms_total"] / m["ok"]) if m["ok"] else None,
                         "analyze": m.get("analyze", 0), "review": m.get("review", 0),
                         "last_ts": m["last_ts"], "last_error": m.get("last_error", ""),
                         "cooldown": self.cooldown.get(tuple(key.split("/", 1)), 0) > time.time()})
        rows.sort(key=lambda r: -r["last_ts"])
        return {
            "enabled": cfg.AI_ENABLED and bool(self.providers),
            "providers": [{"name": p["name"], "base": p["base"], "models": p["models"]} for p in self.providers],
            "order": cfg.AI_PROVIDER_ORDER, "calls_today": self.calls_today(), "budget": cfg.AI_MAX_CALLS_PER_DAY,
            "min_confidence": cfg.AI_MIN_CONFIDENCE, "last_model": self.last_model,
            "last_result": self.last_result, "models": rows, "last_error": self.last_error,
            "fail_policy": cfg.AI_FAIL_POLICY, "dead": sorted(self.dead),
        }

    def _cool(self, p, model, err):
        """Thời gian nghỉ theo loại lỗi."""
        st = getattr(err, "status", None)
        if st == 404 or st == 400:
            return 6 * 3600          # model bị gỡ / không hỗ trợ
        if st in (401, 403):
            return 3600              # sai key / không có quyền
        if st == 402:
            return 6 * 3600          # hết credit
        if st == 429:
            # 9router 'auto' là router tổng → nghỉ ngắn; model OpenRouter free → nghỉ lâu hơn
            return 120 if p["name"] != "openrouter" else 600
        return 180                   # timeout / lỗi mạng / JSON hỏng

    def _ask(self, system, user, required_key, kind="analyze"):
        """Thử lần lượt provider/model. Trả về dict JSON (kèm 'model') hoặc None.
        Chỉ trừ hạn mức ngày khi có model trả lời thành công."""
        if self.budget_left() <= 0:
            log.warning("AI: hết hạn mức %d lần/ngày", cfg.AI_MAX_CALLS_PER_DAY)
            self.last_error = "hết hạn mức ngày"
            return None
        if time.time() - self._last_refresh > 6 * 3600:
            self.refresh_models()
        tried, errors = 0, []
        for attempt in range(2):
            now = time.time()
            for p in self.providers:
                for model in list(p["models"]):
                    if self.cooldown.get((p["name"], model), 0) > now:
                        continue
                    if tried >= cfg.AI_MAX_TRIES:
                        break
                    tried += 1
                    key = f"{p['name']}/{model}"
                    t0 = time.time()
                    try:
                        self.stats["calls"] += 1
                        txt = self._chat(p, model, system, user)
                        res = _extract_json(txt or "", required_key)
                        if not res or required_key not in res:
                            raise RuntimeError(f"JSON không hợp lệ: {(txt or '')[:120]}")
                        res["model"] = key
                        self.last_model = key
                        self.last_error = None
                        self._count_call()
                        self.fail_streak.pop((p["name"], model), None)
                        self._record(key, True, (time.time() - t0) * 1000, kind=kind)
                        return res
                    except Exception as e:
                        self.stats["fails"] += 1
                        self._record(key, False, (time.time() - t0) * 1000, err=e)
                        log.warning("AI %s lỗi: %s", key, e)
                        errors.append(f"{key}: {e}")
                        n = self.fail_streak[(p["name"], model)] = self.fail_streak.get((p["name"], model), 0) + 1
                        # lỗi liên tiếp → nghỉ lâu dần (x2 mỗi lần, tối đa 2 giờ)
                        wait = min(7200, self._cool(p, model, e) * (2 ** min(n - 1, 6)))
                        self.cooldown[(p["name"], model)] = time.time() + wait
                        if getattr(e, "status", None) in (403, 404) and p["name"] == "openrouter":
                            self.dead.add(model)
            # lượt 1 thất bại vì model OpenRouter chết → dò lại danh sách rồi thử thêm 1 lượt
            if attempt == 0 and any(m in self.dead for p in self.providers for m in p["models"]):
                self.refresh_models(force=True)
                continue
            break
        self.last_error = (errors[-1] if errors else "mọi model đang nghỉ (cooldown)")[:200]
        if not errors:
            log.warning("AI: tất cả model đang trong thời gian nghỉ")
        return None

    # ------------------------------------------------------------ 1) phân tích trước khi vào
    def analyze(self, signal):
        if not cfg.AI_ENABLED or not self.providers:
            return {"decision": signal.side.upper(), "confidence": 100, "reason": "AI tắt", "model": "none"}
        lessons = self.store.recent_lessons(cfg.AI_LESSONS_IN_PROMPT)
        payload = {
            "symbol": signal.symbol, "proposed_side": signal.side.upper(),
            "technical_score": signal.score, "reasons": signal.reasons,
            "entry": signal.price, "stop_loss": signal.sl, "take_profit": signal.tp,
            "risk_reward": round(signal.rr, 2), "leverage": cfg.LEVERAGE,
            "indicators_15m_1h": {k: v for k, v in signal.context.items() if k != "last_candles"},
            "candles_15m": signal.context.get("last_candles"),
            "lessons_from_recent_trades": lessons,
        }
        res = self._ask(SYSTEM_ANALYZE, "Dữ liệu tín hiệu:\n" + json.dumps(payload, ensure_ascii=False), "decision")
        if res is None:
            return None
        res["decision"] = str(res["decision"]).upper().strip()
        try:
            res["confidence"] = int(float(res.get("confidence", 0)))
        except (TypeError, ValueError):
            res["confidence"] = 0
        self.last_result = {"ts": time.time(), "symbol": signal.symbol, "side": signal.side, "score": signal.score,
                            "decision": res["decision"], "confidence": res["confidence"],
                            "reason": str(res.get("reason", ""))[:300], "model": res["model"]}
        try:
            self.store.set("ai_last_result", self.last_result)
        except Exception:
            pass
        return res

    # ------------------------------------------------------------ 2) tổng kết sau khi đóng
    def review(self, trade, exit_price, pnl, pnl_pct, reason_close):
        if not cfg.AI_ENABLED or not cfg.AI_REVIEW_ENABLED or not self.providers:
            return None
        entry, side = float(trade["entry"]), trade["side"]
        sl0 = float(trade.get("sl0") or trade["sl"])
        atr = float(trade.get("atr") or 0)
        risk0 = abs(entry - sl0)
        sign = 1 if side == "long" else -1
        notional0 = float(trade.get("notional0") or trade.get("notional") or 0)
        risk_usdt = notional0 * risk0 / entry if entry else 0
        peak = trade.get("peak")
        facts = {
            "sl_distance_atr": round(risk0 / atr, 2) if atr else None,
            "sl_distance_pct": round(100 * risk0 / entry, 2) if entry else None,
            "result_r": round(pnl / risk_usdt, 2) if risk_usdt else None,
            "exit_vs_entry_r": round(sign * (float(exit_price) - entry) / risk0, 2) if risk0 else None,
            "mfe_r": round(sign * (float(peak) - entry) / risk0, 2) if (peak is not None and risk0) else None,
            "tp1_hit": bool(trade.get("tp1_done")),
            "risk_mult": trade.get("risk_mult"),
        }
        payload = {
            "symbol": trade["symbol"], "side": side, "entry": entry, "exit": exit_price,
            "stop_loss_initial": sl0, "stop_loss_final": trade["sl"], "take_profit": trade["tp"], "pnl_usdt": round(pnl, 4),
            "pnl_pct_margin": round(pnl_pct, 1), "close_reason": reason_close,
            "hold_minutes": round((time.time() - trade["opened_at"]) / 60),
            "facts": facts,
            "technical_score": trade.get("score"), "reason_open": trade.get("reason_open"),
            "ai_confidence_at_entry": trade.get("ai_conf"),
        }
        res = self._ask(SYSTEM_REVIEW, "Lệnh vừa đóng:\n" + json.dumps(payload, ensure_ascii=False), "lesson", kind="review")
        if res:
            res["verdict"] = str(res.get("verdict", "")).upper()
            res["lesson"] = str(res.get("lesson", ""))[:400]
            res["tags"] = [str(t)[:20] for t in (res.get("tags") or [])][:3]
        return res
