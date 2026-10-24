"""Backtest of three Wyckoff/VSA ideas (wyckoff.py in this folder, causal readings) on the
owner's 13 stocks and VN30 (39 stocks, daily bars 10/2021 - 23/10/2026).

1. Channel position as a BUY filter: are BUYs taken high in the range (70-100%, under the
   resistance) worse than the others? On two sets of BUYs:
   - the bot's daily-trend rule (TB20 + MACD, the estimate of sieutinhieu.vn), every
     SELL->BUY flip 2022-2026: entry next open, exit at the next open after the SELL flip;
     train before 2024-07-01, test after;
   - sieutinhieu.vn's own BUYs (08/2024 - 10/2026): exit rule R4 (cut-loss of the plan or
     the site's SELL, whichever first) - the rule the bot's alerts follow.
2. Spring / no-supply test as an entry: entry next open, stop under the spring's low (or
   2% under the support for a test), exit after 20 sessions; against buying any session
   low in a range (position <= 30%) with the same stop and exit. And as an earlier entry
   than the site's BUY: an event in the 15 sessions before it, held to the same R4 exit.
3. SOW as a sell warning: the move after it (5/10/20 sessions) against all sessions; and
   as an extra exit on the trades of 1 (sell the next open after a SOW).

T+2 throughout (nothing sold before the afternoon of the second session after the buy),
fees 0.15% buy, 0.15% + 0.1% tax sell. Run from the repo root after build_data.py.
"""
import json
import pickle
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import wyckoff as wy  # noqa: E402
from tradingagents.market import vn_rules  # noqa: E402
from vnbot import trend_estimate as te  # noqa: E402

OUT = Path("docs/backtest_2026-10-24/out")
OWNER = ["POW", "HDG", "VRE", "CTG", "TPB", "CII", "DIG", "CTS", "SHS", "FPT", "NVB", "SBS", "GEX"]
SPLIT = "2024-07-01"
BUY_COST = 1 + vn_rules.BUY_FEE_PCT / 100
SELL_KEEP = 1 - (vn_rules.SELL_FEE_PCT + vn_rules.SELL_TAX_PCT) / 100
HORIZON = 20
COOLDOWN = 10
RNG = random.Random(7)


# ─── helpers ─────────────────────────────────────────────────────────────────

def ret(entry, exit_):
    return exit_ * SELL_KEEP / (entry * BUY_COST) - 1


def stats(rs):
    if not rs:
        return {"n": 0}
    return {"n": len(rs), "avg": statistics.mean(rs) * 100, "med": statistics.median(rs) * 100,
            "win": sum(1 for r in rs if r > 0) / len(rs) * 100,
            "big_loss": sum(1 for r in rs if r < -0.10)}


def boot_diff(a, b, n=2000):
    """90% interval of mean(a) - mean(b), in % points (bootstrap)."""
    if len(a) < 5 or len(b) < 5:
        return None
    diffs = []
    for _ in range(n):
        sa = [a[RNG.randrange(len(a))] for _ in a]
        sb = [b[RNG.randrange(len(b))] for _ in b]
        diffs.append(statistics.mean(sa) - statistics.mean(sb))
    diffs.sort()
    return diffs[int(n * 0.05)] * 100, diffs[int(n * 0.95)] * 100


def line(name, s, extra=""):
    if not s or not s.get("n"):
        return f"  {name:<44} n=0"
    return (f"  {name:<44} n={s['n']:4d}  TB {s['avg']:+6.2f}%  trung vị {s['med']:+6.2f}%  thắng {s['win']:5.1f}%  "
            f"lỗ>10% {s['big_loss']:3d}{extra}")


def ci(a, b):
    c = boot_diff(a, b)
    return "" if c is None else f"  · chênh TB, khoảng 90%: [{c[0]:+.2f}, {c[1]:+.2f}] điểm %"


def trade(b, entry_i, *, stop=None, sells=(), sow=None, horizon=None):
    """Enter at the open of ``entry_i``; out at the first of: an open in ``sells`` (a SELL
    signal the session before), the stop (min(open, stop)), the open after a SOW reading
    (``sow``: the readings), ``horizon`` sessions (close). Returns (return, exit index, why)."""
    d, o, lo, c = b["d"], b["o"], b["l"], b["c"]
    n = len(d)
    if entry_i >= n:
        return None
    price_in = o[entry_i]
    first_ok = entry_i + 2                                   # the afternoon of T+2
    sells = set(sells)
    for k in range(entry_i, n):
        if k in sells and k > entry_i:
            if k >= entry_i + 3:
                return ret(price_in, o[k]), k, "sell"
            j = min(first_ok, n - 1)
            return ret(price_in, c[j]), j, "sell"
        if k < first_ok:
            continue
        if stop and lo[k] <= stop:
            return ret(price_in, min(o[k], stop)), k, "stop"
        if horizon is not None and k - entry_i >= horizon:
            return ret(price_in, c[k]), k, "time"
        if sow is not None and sow[k] is not None and sow[k].sow:
            if k + 1 < n:
                sells.add(k + 1)
    return ret(price_in, c[-1]), n - 1, "open"


def rule_flips(closes):
    """(BUY flip indexes, SELL flip indexes) of the daily-trend rule (TB20 + MACD 12/26/9)."""
    e1, e2 = te._ema(closes, te.FAST), te._ema(closes, te.SLOW)
    macd = [a - b for a, b in zip(e1, e2, strict=True)]
    sig = te._ema(macd, te.SIGNAL)
    state, buys, sells = None, [], []
    for i in range(max(te.WARMUP, te.SMA_N - 1), len(closes)):
        c = closes[i]
        sma = sum(closes[i - te.SMA_N + 1:i + 1]) / te.SMA_N
        hist = macd[i] - sig[i]
        if state is None:
            state = "BUY" if c > sma else "SELL"
        elif state == "BUY" and c < sma and hist < 0:
            state = "SELL"
            sells.append(i)
        elif state == "SELL" and c > sma and hist > 0 and c > closes[i - 1]:
            state = "BUY"
            buys.append(i)
    return buys, sells


def bucket(pos):
    if pos is None:
        return None
    return "< 35%" if pos < 35 else "35-70%" if pos < 70 else "70-100%" if pos <= 100 else "> 100% (vượt trần)"


BUCKETS = ("< 35%", "35-70%", "70-100%", "> 100% (vượt trần)")


# ─── data ────────────────────────────────────────────────────────────────────

bars = pickle.load(open(OUT / "bars.pkl", "rb"))
signals = json.load(open(OUT / "signals.json", encoding="utf-8"))
readings = {L: {s: wy.series(b["o"], b["h"], b["l"], b["c"], b["v"], lookback=L) for s, b in bars.items()} for L in (60, 120)}
R = readings[60]
idx = {s: {d: i for i, d in enumerate(b["d"])} for s, b in bars.items()}
out_lines = []


def say(text=""):
    print(text, flush=True)
    out_lines.append(text)


def after(sym, day):
    """The first bar index after ``day``."""
    for i, d in enumerate(bars[sym]["d"]):
        if d > day:
            return i
    return None


# ─── the BUYs of 1 and their trades ──────────────────────────────────────────

rule_trades = []          # {sym, i (signal), ret, ret_sow, pos60, pos120, period}
for s, b in bars.items():
    buys, sells = rule_flips(b["c"])
    sell_opens = [k + 1 for k in sells]
    for i in buys:
        if i + 1 >= len(b["d"]) or b["d"][i] < "2022-01-01":
            continue
        nxt = [k for k in sell_opens if k > i + 1]
        t1 = trade(b, i + 1, sells=nxt[:1])
        t2 = trade(b, i + 1, sells=nxt[:1], sow=R[s])
        rule_trades.append({"sym": s, "day": b["d"][i], "ret": t1[0], "ret_sow": t2[0], "why_sow": t2[2],
                            "open": t1[2] == "open",
                            "pos60": R[s][i].position_pct if R[s][i] else None,
                            "pos120": readings[120][s][i].position_pct if readings[120][s][i] else None,
                            "period": "train" if b["d"][i] < SPLIT else "test"})

site_trades = []
for s, sig in signals.items():
    if s not in bars:
        continue
    b = bars[s]
    sig = sorted(sig, key=lambda x: (x["day"], x.get("created", "")))
    sells = [x for x in sig if x["side"] == "SELL"]
    for x in sig:
        if x["side"] != "BUY":
            continue
        e = after(s, x["day"])
        if e is None:
            continue
        i = e - 1
        nxt = next((y for y in sells if y["day"] > x["day"]), None)
        sell_open = [after(s, nxt["day"])] if nxt and after(s, nxt["day"]) is not None else []
        stop = x.get("sl") or None
        t1 = trade(b, e, stop=stop, sells=sell_open)
        t2 = trade(b, e, stop=stop, sells=sell_open, sow=R[s])
        site_trades.append({"sym": s, "day": x["day"], "i": i, "entry": e, "ret": t1[0], "exit_i": t1[1],
                            "ret_sow": t2[0], "why_sow": t2[2], "stop": stop, "sell_open": sell_open,
                            "pos60": R[s][i].position_pct if R[s][i] else None,
                            "pos120": readings[120][s][i].position_pct if readings[120][s][i] else None,
                            "live": bool(x.get("live"))})


def by_bucket(trades, key, period=None, owner=False):
    rows = {}
    for t in trades:
        if period and t.get("period") != period:
            continue
        if owner and t["sym"] not in OWNER:
            continue
        b_ = bucket(t[key])
        if b_:
            rows.setdefault(b_, []).append(t["ret"])
    return rows


def idea1(title, trades, periods):
    say(f"\n{title}")
    for key in ("pos60", "pos120"):
        for period in periods:
            rows = by_bucket(trades, key, period)
            say(f" vị trí trong kênh {key[3:]} phiên{'' if not period else ' · ' + ('học 2022-06/2024' if period == 'train' else 'kiểm tra 07/2024-10/2026')}:")
            for name in BUCKETS:
                say(line(name, stats(rows.get(name, []))))
            near = rows.get("70-100%", [])
            rest = [r for k_, v in rows.items() if k_ != "70-100%" for r in v]
            say(line("  bỏ MUA khi 70-100% (giữ phần còn lại)", stats(rest), ci(rest, near)))
            say(line("  tất cả", stats(rest + near)))


say("═══ 1. VỊ TRÍ TRONG KÊNH LÀM BỘ LỌC MUA ═══")
idea1("Quy tắc TB20+MACD (ước tính của bot), 39 mã, mỗi lần đổi sang MUA:", rule_trades, ("train", "test"))
idea1("Tín hiệu MUA thật của Siêu tín hiệu (08/2024-10/2026), thoát R4:", site_trades, (None,))
rows = by_bucket(site_trades, "pos60", owner=True)
say(" 13 mã của anh, Siêu tín hiệu, 60 phiên:")
for name in BUCKETS:
    say(line(name, stats(rows.get(name, []))))


# ─── 2. spring / no-supply ───────────────────────────────────────────────────

def events(kind, vol=None, period=None):
    """(sym, i) of the event sessions, at most one per stock per COOLDOWN sessions."""
    out = []
    for s, rs in R.items():
        last = -10**9
        for i, r in enumerate(rs):
            if r is None or i - last < COOLDOWN or i + 1 >= len(rs):
                continue
            d = bars[s]["d"][i]
            if period == "train" and d >= SPLIT or period == "test" and d < SPLIT:
                continue
            ok = r.spring if kind == "spring" else r.no_supply
            if ok and vol == "low":
                ok = r.volume_ratio is not None and r.volume_ratio <= 1.0
            if ok and vol == "high":
                ok = r.volume_ratio is not None and r.volume_ratio >= wy.HIGH_VOLUME
            if ok:
                out.append((s, i))
                last = i
    return out


def event_trades(evts, kind):
    rs = []
    for s, i in evts:
        b, r = bars[s], R[s][i]
        stop = b["l"][i] * 0.99 if kind == "spring" else r.support * 0.98
        t = trade(b, i + 1, stop=stop, horizon=HORIZON)
        if t:
            rs.append(t[0])
    return rs


def baseline(period=None):
    """Any session low in a range (position <= 30%, width <= 40%): same stop (2% under the
    support) and exit; every 3rd session per stock (less overlap)."""
    rs = []
    for s, rr in R.items():
        for i in range(0, len(rr) - 1, 3):
            r = rr[i]
            if r is None or not (r.width_pct <= wy.MAX_WIDTH_PCT and 0 <= r.position_pct <= 30):
                continue
            d = bars[s]["d"][i]
            if period == "train" and d >= SPLIT or period == "test" and d < SPLIT:
                continue
            t = trade(bars[s], i + 1, stop=r.support * 0.98, horizon=HORIZON)
            if t:
                rs.append(t[0])
    return rs


say("\n═══ 2. SPRING VÀ TEST CẠN CUNG LÀM ĐIỂM MUA (giữ 20 phiên, cắt lỗ dưới đáy) ═══")
for L, period, label in ((60, "train", "học 01/2022-06/2024"), (60, "test", "kiểm tra 07/2024-10/2026"),
                         (120, None, "vùng giá 120 phiên, cả giai đoạn")):
    R = readings[L]
    base = baseline(period)
    say(f" {label}:")
    say(line("mua bất kỳ phiên nào ở đáy kênh (đối chứng)", stats(base)))
    for kind, vol, name in (("spring", None, "Spring (mọi khối lượng)"), ("spring", "low", "Spring khối lượng ≤ TB"),
                            ("spring", "high", "Spring khối lượng ≥ 1,25×TB"), ("nosupply", None, "Test cạn cung")):
        rs = event_trades(events(kind, vol, period), kind)
        say(line(name, stats(rs), ci(rs, base)))
R = readings[60]

say("\n Vào sớm hơn Siêu tín hiệu, chỉ dùng điều biết được lúc đó: mua ở Spring/test cạn cung khi trang đang BÁN;")
say(" trang chuyển MUA trong 15 phiên thì giữ như lệnh của trang (R4), không thì bán sau 15 phiên (hoặc cắt lỗ trước):")


def site_timeline(sym):
    sig = sorted(signals.get(sym, []), key=lambda x: (x["day"], x.get("created", "")))
    return sig


def early_strategy(owner=False):
    """(early trades, the site trades they replace, all site trades) - per-trade returns."""
    early, replaced = [], set()
    for s in bars:
        if owner and s not in OWNER:
            continue
        sig = site_timeline(s)
        if not sig:
            continue
        b, rr = bars[s], R[s]
        first_day = sig[0]["day"]
        evts = sorted({i for s2, i in events("spring") if s2 == s} | {i for s2, i in events("nosupply") if s2 == s})
        busy_until = -1
        for k in evts:
            day_k = b["d"][k]
            if day_k < first_day or k <= busy_until or k + 1 >= len(b["d"]):
                continue
            state = next((x for x in reversed(sig) if x["day"] <= day_k), None)
            if state is None or state["side"] == "BUY":
                continue                                    # already a site BUY: nothing earlier to gain
            r = rr[k]
            stop_e = b["l"][k] * 0.99 if r.spring else r.support * 0.98
            limit = k + 15
            nxt_buy = next((x for x in sig if x["side"] == "BUY" and x["day"] > day_k), None)
            buy_i = idx[s].get(nxt_buy["day"]) if nxt_buy else None
            entry = k + 1
            if buy_i is not None and buy_i <= limit:
                # the site BUYs in time: from its next session the site trade's exits
                st = next((t for t in site_trades if t["sym"] == s and t["day"] == nxt_buy["day"]), None)
                pre = trade(b, entry, stop=stop_e, sells=[buy_i + 1])
                if pre[2] == "stop" and pre[1] <= buy_i:
                    early.append(pre[0])
                    busy_until = pre[1]
                    continue
                if st is None:
                    continue
                exit_px = (st["ret"] + 1) * b["o"][st["entry"]] * BUY_COST / SELL_KEEP
                early.append(ret(b["o"][entry], exit_px))
                replaced.add((s, st["day"]))
                busy_until = st["exit_i"]
            else:
                t = trade(b, entry, stop=stop_e, horizon=15)
                early.append(t[0])
                busy_until = t[1]
    base = [t["ret"] for t in site_trades if not owner or t["sym"] in OWNER]
    kept = [t["ret"] for t in site_trades if (t["sym"], t["day"]) not in replaced and (not owner or t["sym"] in OWNER)]
    return early, kept, base, len(replaced)


for owner, label in ((False, "39 mã"), (True, "13 mã của anh")):
    early, kept, base, n_rep = early_strategy(owner)
    combined = early + kept
    say(f"  {label}:")
    say(line("chỉ theo Siêu tín hiệu (R4)", stats(base)) + f"  · tổng {sum(base) * 100:+.1f} điểm %")
    say(line("lệnh vào sớm (gồm cả lần trang không MUA)", stats(early)))
    say(line("thêm vào sớm (thay lệnh trang tương ứng)", stats(combined)) + f"  · tổng {sum(combined) * 100:+.1f} điểm %"
        + f"  · {n_rep} lệnh trang được vào sớm, {len(early) - n_rep} lần vào sớm mà trang không MUA/cắt lỗ")


# ─── 3. SOW ──────────────────────────────────────────────────────────────────

def forward(period=None, only_sow=False):
    out = {5: [], 10: [], 20: []}
    for s, rr in R.items():
        b = bars[s]
        last = -10**9
        for i, r in enumerate(rr):
            if r is None or i + 21 >= len(rr):
                continue
            d = b["d"][i]
            if period == "train" and d >= SPLIT or period == "test" and d < SPLIT:
                continue
            if only_sow:
                if not r.sow or i - last < COOLDOWN:
                    continue
                last = i
            elif i % 3:
                continue
            for h in (5, 10, 20):
                out[h].append(b["c"][i + h] / b["o"][i + 1] - 1)
    return out


say("\n═══ 3. SOW LÀM CẢNH BÁO BÁN ═══")
for L, period, label in ((60, "train", "học 01/2022-06/2024"), (60, "test", "kiểm tra 07/2024-10/2026"),
                         (120, None, "vùng giá 120 phiên, cả giai đoạn")):
    R = readings[L]
    allf, sowf = forward(period), forward(period, True)
    say(f" {label}: giá sau SOW (từ giá mở cửa phiên sau) so với mọi phiên")
    R = readings[L]
    for h in (5, 10, 20):
        a, s_ = allf[h], sowf[h]
        say(f"  sau {h:2d} phiên: SOW TB {statistics.mean(s_) * 100:+.2f}% (thấp hơn {sum(1 for x in s_ if x < 0) / len(s_) * 100:.0f}% số lần, n={len(s_)})"
            f" · mọi phiên TB {statistics.mean(a) * 100:+.2f}% (thấp hơn {sum(1 for x in a if x < 0) / len(a) * 100:.0f}%)"
            + ci(s_, a))

R = readings[60]
say("\n Thêm SOW làm điểm bán (bán phiên sau SOW) vào các lệnh đang giữ:")
for name, trades, filt in (("Siêu tín hiệu, thoát R4", site_trades, None),
                           ("Siêu tín hiệu R4, 13 mã của anh", site_trades, OWNER),
                           ("Quy tắc TB20+MACD · học", [t for t in rule_trades if t["period"] == "train"], None),
                           ("Quy tắc TB20+MACD · kiểm tra", [t for t in rule_trades if t["period"] == "test"], None)):
    ts = [t for t in trades if filt is None or t["sym"] in filt]
    a = [t["ret"] for t in ts]
    b_ = [t["ret_sow"] for t in ts]
    hit = sum(1 for t in ts if t["why_sow"] == "sell" and t["ret_sow"] != t["ret"])
    say(f" {name}:")
    say(line("không có SOW", stats(a)))
    say(line("thêm SOW", stats(b_), ci(b_, a) + f"  · SOW đổi kết quả {hit} lệnh"))

json.dump({"lines": out_lines}, open(OUT / "wyckoff_result.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
say("\nDONE")
