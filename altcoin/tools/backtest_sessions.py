"""Backtest 3 ý tưởng trên nến lịch sử OKX (không cần API key, không gọi AI):

  A. orb   – bắt phá giả biên độ đầu phiên (kiểu "H4R"): lấy biên 4h đầu từ một mốc giờ UTC, chờ nến 5m ĐÓNG ngoài
             biên rồi một nến 5m ĐÓNG quay lại trong biên → vào ngược hướng cú phá, SL ở cực trị cú phá.
  B. bot   – chạy lại đúng `strategy.diagnose` của bot trên từng nến 15m đã đóng, mô phỏng thoát lệnh bằng `exits.simulate`
             (fixed / hybrid / trail), rồi chia nhóm theo giờ VN (cách bộ tự học đang dùng) và theo phiên Á / London / NY.
  C. vp    – Volume Profile 24h trước tín hiệu (POC / VAH / VAL): vị trí giá so với vùng giá trị, khoảng trống tới POC,
             và thử TP tại mức VP kế tiếp thay cho TP theo ATR.

Giới hạn (khác bot thật): không có AI duyệt, không có OI / funding / long-short (dữ liệu lịch sử không đủ) → điểm KT
không cộng/trừ phần dòng tiền; không có luật chặn của bộ tự học. Kết quả là của TÍN HIỆU KỸ THUẬT, trước AI.

Thống kê: R đã trừ phí + trượt giá. Ngoài t thường còn t theo ngày (gộp R trung bình mỗi ngày rồi mới kiểm định) vì các
tín hiệu cùng ngày thắng/thua cùng nhau – t thường sẽ quá lạc quan. Chia đôi thời gian (nửa đầu / nửa sau) để xem có ổn định.

Chạy (cần mạng tới www.okx.com, chỉ thư viện chuẩn):
    python tools/backtest_sessions.py --days 90 --top 20 --cache .bt_cache --out bt_result.json
    python tools/backtest_sessions.py --report bt_result.json      # in lại báo cáo
"""
import argparse
import json
import math
import os
import sys
import time
import urllib.request
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("MODE", "paper")
os.environ.setdefault("TIMEZONE", "Asia/Ho_Chi_Minh")

from bot.config import cfg  # noqa: E402
from bot import strategy, exits  # noqa: E402
from bot.indicators import to_frame, enrich, atr as atr_fn, Frame  # noqa: E402

API = "https://www.okx.com/api/v5"
M5 = 300_000
VN = 7 * 3600
COST = 2 * cfg.TAKER_FEE + cfg.SLIPPAGE


# ============================================================ dữ liệu
def _get(path):
    for i in range(6):
        try:
            req = urllib.request.Request(API + path, headers={"User-Agent": "bt/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                d = json.loads(r.read())
            if d.get("code") == "0":
                return d["data"]
            if d.get("code") == "50011":          # quá nhanh
                time.sleep(1 + i)
                continue
            raise RuntimeError(d)
        except Exception as e:
            if i == 5:
                raise
            time.sleep(1 + 2 * i)
            last = e  # noqa: F841


def top_symbols(n):
    rows = _get("/market/tickers?instType=SWAP")
    rows = [r for r in rows if r["instId"].endswith("-USDT-SWAP") and not r["instId"].startswith(("BTC-", "USDC-"))]
    rows.sort(key=lambda r: float(r.get("volCcy24h") or 0) * float(r.get("last") or 0), reverse=True)
    return [r["instId"] for r in rows[:n]]


def candles_5m(inst, start_ms, end_ms, cache):
    """Nến 5m đã đóng [ts, o, h, l, c, vol_quote] trong [start, end), cache theo file."""
    fn = os.path.join(cache, f"{inst}_5m.json")
    have = []
    if os.path.exists(fn):
        with open(fn) as f:
            have = json.load(f)
    if have and have[0][0] <= start_ms and have[-1][0] >= end_ms - 2 * M5:
        return [c for c in have if start_ms <= c[0] < end_ms]
    out, after = {}, end_ms
    while after > start_ms:
        rows = _get(f"/market/history-candles?instId={inst}&bar=5m&limit=100&after={after}")
        if not rows:
            break
        for r in rows:
            if r[8] == "1":
                out[int(r[0])] = [int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[7])]
        after = min(int(r[0]) for r in rows)
        time.sleep(0.11)
    data = sorted(out.values())
    os.makedirs(cache, exist_ok=True)
    with open(fn, "w") as f:
        json.dump(data, f)
    return [c for c in data if start_ms <= c[0] < end_ms]


def resample(c5, minutes):
    step = minutes * 60_000
    out, cur = [], None
    for c in c5:
        b = c[0] - c[0] % step
        if cur is None or cur[0] != b:
            if cur is not None and cur[6] == minutes // 5:
                out.append(cur[:6])
            cur = [b, c[1], c[2], c[3], c[4], c[5], 1]
        else:
            cur[2] = max(cur[2], c[2]); cur[3] = min(cur[3], c[3]); cur[4] = c[4]; cur[5] += c[5]; cur[6] += 1
    if cur is not None and cur[6] == minutes // 5:
        out.append(cur[:6])
    return out


# ============================================================ phân nhóm & thống kê
def vn_bucket(ts):
    h = datetime.fromtimestamp(ts + VN, timezone.utc).hour
    return "0h–7h" if h < 7 else "7h–13h" if h < 13 else "13h–19h" if h < 19 else "19h–24h"


def session(ts):
    """Phiên theo giờ UTC: Á 0–7, London 7–12, giao London–NY 12–16, NY chiều 16–21, nghỉ 21–24."""
    h = datetime.fromtimestamp(ts, timezone.utc).hour
    return ("1 Á (0–7 UTC)" if h < 7 else "2 London (7–12)" if h < 12 else "3 London+NY (12–16)" if h < 16
            else "4 NY chiều (16–21)" if h < 21 else "5 Nghỉ (21–24)")


def day_of(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def _t(vals):
    n = len(vals)
    if n < 2:
        return 0.0
    m = sum(vals) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in vals) / (n - 1))
    return m / (sd / math.sqrt(n)) if sd > 1e-12 else 0.0


def agg(rows, key="r"):
    vals = [r[key] for r in rows if r.get(key) is not None]
    if not vals:
        return None
    by_day = defaultdict(list)
    for r in rows:
        if r.get(key) is not None:
            by_day[r["day"]].append(r[key])
    dm = [sum(v) / len(v) for v in by_day.values()]
    n = len(vals)
    return {"n": n, "days": len(dm), "exp_r": round(sum(vals) / n, 3), "win": round(100 * sum(v > 0 for v in vals) / n, 1),
            "sum_r": round(sum(vals), 1), "t": round(_t(vals), 2), "t_day": round(_t(dm), 2)}


def halves(rows, key="r"):
    if not rows:
        return None, None
    ts = sorted(r["ts"] for r in rows)
    mid = ts[len(ts) // 2]
    return agg([r for r in rows if r["ts"] < mid], key), agg([r for r in rows if r["ts"] >= mid], key)


# ============================================================ C. volume profile
def volume_profile(c5, bins=48, va=0.70):
    """POC / VAH / VAL từ các nến 5m (volume chia đều theo biên độ mỗi nến)."""
    lo = min(c[3] for c in c5); hi = max(c[2] for c in c5)
    if hi <= lo:
        return None
    w = (hi - lo) / bins
    vol = [0.0] * bins
    for c in c5:
        a = int((c[3] - lo) / w); b = int((c[2] - lo) / w)
        a = min(max(a, 0), bins - 1); b = min(max(b, 0), bins - 1)
        share = c[5] / (b - a + 1)
        for k in range(a, b + 1):
            vol[k] += share
    total = sum(vol)
    if total <= 0:
        return None
    poc = max(range(bins), key=lambda k: vol[k])
    i = j = poc
    acc = vol[poc]
    while acc < va * total and (i > 0 or j < bins - 1):
        up = vol[j + 1] if j < bins - 1 else -1
        dn = vol[i - 1] if i > 0 else -1
        if up >= dn:
            j += 1; acc += vol[j]
        else:
            i -= 1; acc += vol[i]
    mid = lambda k: lo + (k + 0.5) * w  # noqa: E731
    return {"poc": mid(poc), "vah": lo + (j + 1) * w, "val": lo + i * w}


def vp_features(side, entry, risk, a, vp):
    sign = 1 if side == "long" else -1
    pos = "trên VAH" if entry > vp["vah"] else "dưới VAL" if entry < vp["val"] else "trong VA"
    # vị trí theo hướng lệnh: "thuận" = đã thoát khỏi vùng giá trị theo hướng lệnh (breakout), "ngược" = đang ở phía bên kia
    if pos == "trong VA":
        rel = "trong VA"
    elif (pos == "trên VAH") == (side == "long"):
        rel = "ngoài VA, thuận hướng"
    else:
        rel = "ngoài VA, ngược hướng"
    poc_room = sign * (vp["poc"] - entry) / a           # >0: POC nằm phía trước (là mục tiêu / nam châm)
    lv = sorted(x for x in (vp["poc"], vp["vah"], vp["val"]) if sign * (x - entry) >= 1.0 * risk)
    nxt = (lv[0] if side == "long" else lv[-1]) if lv else None
    return {"vp_pos": pos, "vp_rel": rel, "poc_room_atr": round(poc_room, 2), "vp_tp": nxt}


def poc_bucket(x):
    return "POC phía sau lệnh" if x < -0.5 else "POC sát giá (±0.5 ATR)" if x <= 0.5 else \
        "POC phía trước 0.5–2 ATR" if x <= 2 else "POC phía trước >2 ATR"


# ============================================================ mô phỏng thoát lệnh đơn giản (SL/TP cố định)
def sim_fixed(side, entry, sl, tp, t0_ms, c5, max_h=24):
    risk = abs(entry - sl)
    cost_r = COST * entry / risk
    end = t0_ms + max_h * 3600_000
    last = None
    for c in c5:
        if c[0] < t0_ms:
            continue
        hi, lo, cl = c[2], c[3], c[4]
        last = cl
        if (lo <= sl) if side == "long" else (hi >= sl):
            return round(-1 - cost_r, 3)
        if tp is not None and ((hi >= tp) if side == "long" else (lo <= tp)):
            return round(abs(tp - entry) / risk - cost_r, 3)
        if c[0] + M5 >= end:
            break
    if last is None:
        return None
    return round(((last - entry) if side == "long" else (entry - last)) / risk - cost_r, 3)


def sim_mode(side, entry, sl, a, mode, t0_ms, c5, tp_fixed=None):
    sub = [[c[0], c[1], c[2], c[3], c[4], c[5]] for c in c5 if t0_ms - M5 <= c[0] <= t0_ms + 30 * 3600_000]
    o, r = exits.simulate(side, entry, sl, a, mode, t0_ms / 1000, sub, COST, tp_fixed=tp_fixed)
    if o is None and sub:                       # hết dữ liệu → đóng theo giá cuối
        last = sub[-1][4]
        r = round(((last - entry) if side == "long" else (entry - last)) / abs(entry - sl) - COST * entry / abs(entry - sl), 3)
    return r


# ============================================================ A. ORB phá giả
ORB_ANCHORS = (0, 4, 8, 12)   # UTC: đầu ngày UTC, 0h New York (EDT), ~London mở, ~NY mở


def run_orb(inst, c5, btc5):
    out = []
    idx = {c[0]: i for i, c in enumerate(c5)}
    btc_close = {c[0]: c[4] for c in btc5}
    f15 = enrich(to_frame(resample(c5, 15)))
    atr15 = dict(zip([int(x) for x in f15.ts], f15.atr))
    days = sorted({c[0] - c[0] % 86_400_000 for c in c5})
    for d0 in days:
        for h in ORB_ANCHORS:
            r0 = d0 + h * 3600_000
            r1 = r0 + 4 * 3600_000
            if r0 not in idx or r1 not in idx:
                continue
            rng = c5[idx[r0]:idx[r1]]
            if len(rng) < 46:
                continue
            hi = max(c[2] for c in rng); lo = min(c[3] for c in rng)
            a = atr15.get(r1 - 15 * 60_000) or atr15.get(r1 - 30 * 60_000)
            if not a or hi <= lo:
                continue
            state, ext = None, None
            for c in c5[idx[r1]: idx[r1] + 8 * 12]:            # tìm tín hiệu trong 8h sau khi đóng biên
                if state is None:
                    if c[4] > hi:
                        state, ext = "up", c[2]
                    elif c[4] < lo:
                        state, ext = "dn", c[3]
                    continue
                if state == "up":
                    ext = max(ext, c[2])
                    if c[4] < lo:                                 # xuyên luôn sang phía kia → huỷ
                        break
                    if c[4] < hi:
                        side, entry, sl = "short", c[4], ext * (1 + 0.0005)
                    else:
                        continue
                else:
                    ext = min(ext, c[3])
                    if c[4] > hi:
                        break
                    if c[4] > lo:
                        side, entry, sl = "long", c[4], ext * (1 - 0.0005)
                    else:
                        continue
                t0 = c[0] + M5
                risk = abs(entry - sl)
                if risk <= 0:
                    break
                mid = (hi + lo) / 2
                opp = lo if side == "short" else hi
                b_now, b_1h = btc_close.get(c[0]), btc_close.get(c[0] - 3600_000)
                btc_chg = (b_now / b_1h - 1) * 100 if b_now and b_1h else 0
                row = {"inst": inst, "ts": t0 / 1000, "day": day_of(t0 / 1000), "anchor": h, "side": side,
                       "range_atr": round((hi - lo) / a, 2), "risk_pct": round(100 * risk / entry, 3),
                       "depth": round(abs(ext - (hi if side == 'short' else lo)) / (hi - lo), 2),
                       "btc_against": (btc_chg > 0.3 and side == "short") or (btc_chg < -0.3 and side == "long"),
                       "session": session(t0 / 1000)}
                row["r_mid"] = sim_fixed(side, entry, sl, mid if abs(mid - entry) > 0 and (mid - entry) * (1 if side == 'long' else -1) > 0 else None, t0, c5)
                row["r_opp"] = sim_fixed(side, entry, sl, opp, t0, c5)
                row["r_1r"] = sim_fixed(side, entry, sl, entry + (risk if side == "long" else -risk), t0, c5)
                row["r_2r"] = sim_fixed(side, entry, sl, entry + (2 * risk if side == "long" else -2 * risk), t0, c5)
                row["r_trail"] = sim_mode(side, entry, sl, a, "trail", t0, c5)
                row["r_hybrid"] = sim_mode(side, entry, sl, a, "hybrid", t0, c5)
                out.append(row)
                break
    return out


# ============================================================ B + C. chạy lại strategy của bot
class _Clock:
    """Thay module time trong strategy để ctx['hour'] là giờ VN của nến đang xét (không phải giờ máy)."""
    now = 0.0

    def __getattr__(self, k):
        return getattr(time, k)

    def localtime(self, t=None):
        return time.gmtime((self.now if t is None else t) + VN)

    def time(self):
        return self.now


def btc_market(btc5_by_ts, btc1h, t_ms):
    last = btc5_by_ts.get(t_ms - M5)
    p1 = btc5_by_ts.get(t_ms - M5 - 3600_000)
    p4 = btc5_by_ts.get(t_ms - M5 - 4 * 3600_000)
    if not last:
        return {}
    k = btc1h["idx"].get(t_ms - t_ms % 3600_000 - 3600_000)
    trend = 0
    if k is not None:
        f = btc1h["f"]
        e21, e50, cl = f.ema21[k], f.ema50[k], f.close[k]
        trend = 1 if (e21 > e50 and cl > e21) else -1 if (e21 < e50 and cl < e21) else 0
    pct = lambda a, b: round(100 * (a - b) / b, 2) if b else 0.0  # noqa: E731
    return {"btc_chg_1h": pct(last, p1 or last), "btc_chg_4h": pct(last, p4 or last), "btc_trend_1h": trend}


def run_bot(inst, c5, btc5):
    clock = _Clock()
    strategy.time = clock
    strategy.LEARNED.update({"min_score": None, "trigger_w": {}, "blocks": [], "unblocked": []})
    c15 = resample(c5, 15)
    c1h = resample(c5, 60)
    h_idx = {c[0]: i for i, c in enumerate(c1h)}
    btc5_by_ts = {c[0]: c[4] for c in btc5}
    b1 = resample(btc5, 60)
    btc1h = {"f": enrich(to_frame(b1)), "idx": {c[0]: i for i, c in enumerate(b1)}}
    close5 = {c[0]: c[4] for c in c5}
    out, last_sig = [], {}
    for i in range(220, len(c15)):
        t = c15[i][0]                                     # lúc mở nến i: nến 0..i-1 đã đóng
        hk = h_idx.get(t - t % 3600_000 - 3600_000)
        if hk is None or hk < 100:
            continue
        ltf = c15[i - 220:i + 1]
        htf = c1h[hk - 119:hk + 1] + [c1h[hk]]            # phần tử cuối = nến 1h "đang chạy" (diagnose bỏ đi)
        p24 = close5.get(t - M5 - 86_400_000)
        chg24 = 100 * (c15[i - 1][4] / p24 - 1) if p24 else 0.0
        clock.now = t / 1000
        sig, why, _ = strategy.diagnose(inst, ltf, htf, 0.0, chg24, btc_market(btc5_by_ts, btc1h, t))
        if sig is None or (why and not why.startswith("điểm thấp")):
            continue
        key = (sig.side,)
        if t - last_sig.get(key, 0) < 2 * 3600_000:       # cùng hướng trong 2h → coi là 1 tín hiệu
            continue
        last_sig[key] = t
        risk = abs(sig.price - sig.sl)
        is_range = sig.context.get("setup") == "range"
        vp = volume_profile([c for c in c5 if t - 86_400_000 <= c[0] < t])
        row = {"inst": inst, "ts": t / 1000, "day": day_of(t / 1000), "side": sig.side, "score": sig.score,
               "passed": not why, "setup": sig.context.get("setup"), "triggers": sig.triggers,
               "vn": vn_bucket(t / 1000), "session": session(t / 1000),
               "blocked": sorted(strategy.matching_rules(sig.side, sig.context))}
        if is_range:
            row["r_fixed"] = sim_mode(sig.side, sig.price, sig.sl, sig.atr, "fixed", t, c5, tp_fixed=sig.tp)
            row["r_hybrid"] = row["r_trail"] = row["r_fixed"]
        else:
            for m in ("fixed", "hybrid", "trail"):
                row["r_" + m] = sim_mode(sig.side, sig.price, sig.sl, sig.atr, m, t, c5)
        if vp:
            row.update(vp_features(sig.side, sig.price, risk, sig.atr, vp))
            tpv = row.pop("vp_tp")
            row["r_vp_tp"] = sim_mode(sig.side, sig.price, sig.sl, sig.atr, "fixed", t, c5, tp_fixed=tpv) if tpv else None
        out.append(row)
    return out


def work(args):
    part, inst, c5, btc5 = args
    try:
        return run_orb(inst, c5, btc5) if part == "orb" else run_bot(inst, c5, btc5)
    except Exception as e:
        print(f"  ! {part} {inst}: {e}", file=sys.stderr)
        return []


# ============================================================ báo cáo
def fmt(a):
    if not a:
        return "—"
    return f"n={a['n']:4d}  {a['exp_r']:+.3f}R  thắng {a['win']:5.1f}%  ΣR {a['sum_r']:+7.1f}  t={a['t']:+.2f}  t_ngày={a['t_day']:+.2f}"


def group(rows, keyf, key):
    g = defaultdict(list)
    for r in rows:
        k = keyf(r)
        if k is not None:
            g[k].append(r)
    return {k: agg(v, key) for k, v in sorted(g.items(), key=lambda x: str(x[0]))}


def report(res):
    L = []
    p = L.append
    p(f"# Backtest {res['meta']['from']} → {res['meta']['to']} · {len(res['meta']['symbols'])} coin · phí+trượt {COST*100:.2f}%/lệnh\n")
    orb = res.get("orb") or []
    if orb:
        p("## A. Phá giả biên độ đầu phiên (ORB 4h, vào khi nến 5m đóng quay lại trong biên)\n")
        for k in ("r_mid", "r_opp", "r_1r", "r_2r", "r_trail", "r_hybrid"):
            p(f"- Thoát {k[2:]:7s}: {fmt(agg(orb, k))}")
        best = max(("r_mid", "r_opp", "r_1r", "r_2r", "r_trail", "r_hybrid"), key=lambda k: (agg(orb, k) or {}).get("exp_r", -9))
        p(f"\nChia nhóm theo cách thoát tốt nhất (`{best}`):\n")
        for title, f in (("Mốc biên (giờ UTC)", lambda r: f"{r['anchor']:02d}h"), ("Hướng", lambda r: r["side"]),
                         ("Phiên lúc vào", lambda r: r["session"]),
                         ("Độ rộng biên", lambda r: "<2 ATR" if r["range_atr"] < 2 else "2–4 ATR" if r["range_atr"] < 4 else ">4 ATR"),
                         ("SL so với giá", lambda r: "<0.3%" if r["risk_pct"] < 0.3 else "0.3–0.8%" if r["risk_pct"] < 0.8 else ">0.8%"),
                         ("Cú phá sâu (so với biên)", lambda r: "<15%" if r["depth"] < 0.15 else "15–40%" if r["depth"] < 0.4 else ">40%"),
                         ("BTC 1h ngược hướng lệnh", lambda r: "BTC ngược" if r["btc_against"] else "BTC không ngược")):
            p(f"**{title}**")
            for k, a in group(orb, f, best).items():
                p(f"- {k:24s} {fmt(a)}")
            p("")
        h1, h2 = halves(orb, best)
        p(f"Ổn định theo thời gian ({best}): nửa đầu {fmt(h1)}\n                     nửa sau  {fmt(h2)}\n")
    bot = res.get("bot") or []
    if bot:
        passed = [r for r in bot if r["passed"]]
        p("## B. Tín hiệu kỹ thuật của bot (chạy lại strategy.diagnose, chưa qua AI)\n")
        for k in ("r_fixed", "r_hybrid", "r_trail"):
            p(f"- Đạt điểm, thoát {k[2:]:7s}: {fmt(agg(passed, k))}")
        p(f"- Dưới điểm (suýt đạt), trail : {fmt(agg([r for r in bot if not r['passed']], 'r_trail'))}\n")
        key = "r_trail"
        for title, f in (("Giờ VN (nhóm bộ tự học đang dùng)", lambda r: r["vn"]), ("Phiên (UTC)", lambda r: r["session"]),
                         ("Phiên × hướng", lambda r: f"{r['session'][:12]} {r['side']}"), ("Hướng", lambda r: r["side"]),
                         ("Setup", lambda r: r["setup"])):
            p(f"**{title}** (đạt điểm, thoát {key[2:]})")
            for k, a in group(passed, f, key).items():
                p(f"- {k:28s} {fmt(a)}")
            p("")
        p("## C. Volume Profile 24h trước tín hiệu\n")
        for title, f in (("Vị trí so với vùng giá trị", lambda r: r.get("vp_rel")),
                         ("Khoảng trống tới POC", lambda r: poc_bucket(r["poc_room_atr"]) if "poc_room_atr" in r else None)):
            p(f"**{title}** (đạt điểm, thoát {key[2:]})")
            for k, a in group(passed, f, key).items():
                p(f"- {k:28s} {fmt(a)}")
            p("")
        both = [r for r in passed if r.get("r_vp_tp") is not None]
        p(f"**TP tại mức VP kế tiếp (≥1R) so với cách thoát hiện tại – cùng {len(both)} tín hiệu**")
        for k in ("r_vp_tp", "r_fixed", "r_hybrid", "r_trail"):
            p(f"- {k[2:]:8s} {fmt(agg(both, k))}")
        p("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--top", type=int, default=20, help="số coin thanh khoản cao nhất (trừ BTC)")
    ap.add_argument("--symbols", default="", help="danh sách instId, vd ETH-USDT-SWAP,SOL-USDT-SWAP")
    ap.add_argument("--parts", default="orb,bot")
    ap.add_argument("--cache", default=".bt_cache")
    ap.add_argument("--out", default="bt_result.json")
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 2)
    ap.add_argument("--report", default="", help="chỉ in báo cáo từ file kết quả")
    a = ap.parse_args()
    if a.report:
        with open(a.report) as f:
            print(report(json.load(f)))
        return
    end = int(time.time() * 1000)
    end -= end % 86_400_000
    start = end - a.days * 86_400_000
    syms = [s.strip() for s in a.symbols.split(",") if s.strip()] or top_symbols(a.top)
    warm = start - 4 * 86_400_000                       # đủ 220 nến 15m + 120 nến 1h + 24h VP
    print(f"Tải nến 5m {len(syms)} coin + BTC, {a.days} ngày…", file=sys.stderr)
    btc5 = candles_5m("BTC-USDT-SWAP", warm, end, a.cache)
    data = {}
    for s in syms:
        data[s] = candles_5m(s, warm, end, a.cache)
        print(f"  {s}: {len(data[s])} nến", file=sys.stderr)
    res = {"meta": {"from": day_of(start / 1000), "to": day_of(end / 1000), "symbols": syms, "cost": COST}}
    parts = [p.strip() for p in a.parts.split(",")]
    with ProcessPoolExecutor(a.workers) as ex:
        for part in parts:
            t0 = time.time()
            rows = []
            for r in ex.map(work, [(part, s, data[s], btc5) for s in syms]):
                rows.extend(x for x in r if x["ts"] * 1000 >= start)
            res[part] = rows
            print(f"{part}: {len(rows)} tín hiệu ({time.time() - t0:.0f}s)", file=sys.stderr)
    with open(a.out, "w") as f:
        json.dump(res, f, ensure_ascii=False)
    print(report(res))


if __name__ == "__main__":
    main()
