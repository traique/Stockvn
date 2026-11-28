"""Chẩn đoán 28/11/2026 – đọc bản "sao lưu toàn bộ JSON" của bot Altcoin (thẻ Tải dữ liệu trên dashboard) và in các bảng
trong README.md cùng thư mục: lệnh chạy ảo, tín hiệu bóng theo phía / bối cảnh BTC / nguồn / giờ, AI đoán chuẩn không (Brier),
các biến thể thoát lệnh trên cùng tín hiệu. Sai số chuẩn gom cụm theo ngày (giờ Việt Nam) như bộ tự học.

    python docs/chandoan_2026-11-28/chandoan_paper.py <okxbot-all-...json>

Chỉ thư viện chuẩn; không cần mạng, không đụng DB. File sao lưu không nằm trong repo (dữ liệu riêng của người chạy bot)."""
import json
import math
import statistics as st
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

VN = timezone(timedelta(hours=7))


def day_of(ts):
    return datetime.fromtimestamp(ts, VN).date()


def clustered(rows, key):
    """(trung bình, sai số chuẩn gom cụm theo ngày, n, số ngày) của rows[key]."""
    by = defaultdict(list)
    for r in rows:
        by[r["day"]].append(r[key])
    vals = [v for vs in by.values() for v in vs]
    n = len(vals)
    if n < 2:
        return (vals[0] if vals else float("nan")), 0.0, n, len(by)
    m = st.mean(vals)
    se = math.sqrt(sum(sum(v - m for v in vs) ** 2 for vs in by.values())) / n
    return m, se, n, len(by)


def show(label, rows, key="r"):
    m, se, n, k = clustered(rows, key)
    if not n:
        print(f"  {label:44s} n=0")
        return
    win = 100 * sum(1 for r in rows if r[key] > 0) / n
    print(f"  {label:44s} n={n:4d} ngày={k:2d} R={m:+.2f}±{se:.2f} t={m / se if se else 0:+.1f} thắng={win:.0f}%")


def main(path):
    d = json.load(open(path, encoding="utf-8"))
    trades = [t for t in d["trades"] if t["status"] == "closed"]
    for t in trades:
        risk = t.get("risk_usdt") or abs(t["entry"] - t["sl0"]) / t["entry"] * t["notional0"]
        t["r"] = t["pnl"] / risk if risk else 0.0
        t["day"] = day_of(t["closed_at"])
    sigs = [s for s in d["signals"] if s.get("outcome_r") is not None and s.get("outcome") not in ("DUP", "PENDING", "NODATA")]
    for s in sigs:
        f = s.get("features") or {}
        s["f"] = json.loads(f) if isinstance(f, str) else f
        s["r"] = s["outcome_r"]
        s["day"] = day_of(s["ts"])
    eq = [e for e in d["equity"] if e["mode"] == "paper"]

    print(f"# Lệnh chạy ảo: {len(trades)} lệnh, {day_of(min(t['opened_at'] for t in trades))} → {day_of(max(t['closed_at'] for t in trades))}")
    print(f"  vốn {eq[0]['equity']:.2f} → {eq[-1]['equity']:.2f} USDT; lãi gộp {sum(t['pnl'] for t in trades if t['pnl'] > 0):+.2f}, "
          f"lỗ gộp {sum(t['pnl'] for t in trades if t['pnl'] <= 0):+.2f}, phí {sum(t['fee'] for t in trades):.2f}")
    pk, mdd, pk_ts, worst = eq[0]["equity"], 0.0, eq[0]["ts"], (None, None)
    for e in eq:
        if e["equity"] > pk:
            pk, pk_ts = e["equity"], e["ts"]
        dd = (pk - e["equity"]) / pk
        if dd > mdd:
            mdd, worst = dd, (day_of(pk_ts), day_of(e["ts"]))
    print(f"  sụt giảm lớn nhất từ đỉnh: {100 * mdd:.1f}% ({worst[0]} → {worst[1]})")
    show("mọi lệnh", trades)
    for sd in ("long", "short"):
        show(sd, [t for t in trades if t["side"] == sd])
    for k, _ in Counter(t["reason_close"] for t in trades).most_common():
        show(f"đóng: {k}", [t for t in trades if t["reason_close"] == k])
    by_w = defaultdict(list)
    for t in trades:
        by_w[t["day"].isocalendar()[1]].append(t)
    for w in sorted(by_w):
        show(f"tuần {w}", by_w[w])

    print(f"\n# Tín hiệu bóng có kết quả: {len(sigs)}")
    show("mọi tín hiệu", sigs)
    L = [s for s in sigs if s["side"] == "long"]
    H = [s for s in sigs if s["side"] == "short"]
    show("long", L)
    show("short", H)
    print("  BTC trung vị theo tuần:", {w: round(st.median(v)) for w, v in sorted(
        {w: [s["f"].get("btc_price") or 0 for s in sigs if s["day"].isocalendar()[1] == w] for w in
         set(s["day"].isocalendar()[1] for s in sigs)}.items())})
    for w in sorted(set(s["day"].isocalendar()[1] for s in sigs)):
        show(f"long tuần {w}", [s for s in L if s["day"].isocalendar()[1] == w])
    for w in sorted(set(s["day"].isocalendar()[1] for s in sigs)):
        show(f"short tuần {w}", [s for s in H if s["day"].isocalendar()[1] == w])
    print("  Long theo bối cảnh BTC 1h (cổng hiện có cho qua):")
    for tr in (1, 0, -1):
        show(f"    btc_trend_1h = {tr:+d}", [s for s in L if s["f"].get("btc_trend_1h") == tr])
    show("    btc_chg_4h < 0", [s for s in L if (s["f"].get("btc_chg_4h") or 0) < 0])
    show("    btc_chg_4h ≥ 0", [s for s in L if (s["f"].get("btc_chg_4h") or 0) >= 0])
    print("  Theo nguồn:")
    for k, _ in Counter(s["source"] for s in sigs).most_common():
        show(f"    {k}", [s for s in sigs if s["source"] == k])
    print("  Luật đang chặn (features.blocked_rule):")
    br = defaultdict(list)
    for s in sigs:
        if s["f"].get("blocked_rule"):
            br[s["f"]["blocked_rule"]].append(s)
    for k, v in sorted(br.items(), key=lambda x: -len(x[1])):
        show(f"    {k}", v)
    print("  Theo giờ Việt Nam (mọi tín hiệu / tín hiệu tới được AI):")
    reach = [s for s in sigs if s["source"] not in ("blocked", "below_score", "breakout_off")]
    for h in range(0, 24, 3):
        show(f"    {h:02d}–{h + 3:02d}h", [s for s in sigs if h <= s["f"].get("hour", 0) < h + 3])
        show(f"      tới AI", [s for s in reach if h <= s["f"].get("hour", 0) < h + 3])

    print("\n# AI")
    ai = [s for s in sigs if s.get("ai_decision") and s.get("ai_conf") is not None]
    for dec in ("SKIP", "LONG", "SHORT"):
        show(f"AI nói {dec}", [s for s in ai if s["ai_decision"] == dec])
    show("tới AI, nếu không có AI vào hết", reach and [s for s in sigs if s["source"] in ("taken", "ai_rejected", "ai_override", "ai_error", "ai_weak")])
    show("    long", [s for s in sigs if s["source"] in ("taken", "ai_rejected", "ai_override", "ai_error", "ai_weak") and s["side"] == "long"])
    show("    short", [s for s in sigs if s["source"] in ("taken", "ai_rejected", "ai_override", "ai_error", "ai_weak") and s["side"] == "short"])
    rej = [s for s in sigs if s["source"] == "ai_rejected"]
    for lo, hi in ((0, 5.5), (5.5, 6.5), (6.5, 99)):
        show(f"AI từ chối, điểm {lo}–{hi}", [s for s in rej if lo <= (s["score"] or 0) < hi])
    pts = []
    for s in ai:
        p = max(0.0, min(1.0, s["ai_conf"] / 100))
        p = p if s["ai_decision"] == s["side"].upper() else 1 - p
        pts.append((p, 1 if s["r"] > 0 else 0))
    if pts:
        base = sum(y for _, y in pts) / len(pts)
        brier = sum((p - y) ** 2 for p, y in pts) / len(pts)
        print(f"  Brier {brier:.3f} trên {len(pts)} quyết định; nếu luôn đoán theo tỉ lệ nền {base:.2f}: {base * (1 - base):.3f}; "
              f"skill {1 - brier / (base * (1 - base)):+.2f}")

    print("\n# Biến thể thoát lệnh trên cùng tín hiệu (hiệu so với kết quả chính, sai số theo ngày)")
    def paired(rows, fn, label):
        pr = [{"day": s["day"], "r": fn(s)} for s in rows if fn(s) is not None]
        show(label, pr)
    for grp, rows in (("mọi tín hiệu", sigs), ("short", H), ("long", L)):
        for lvl in ("2.0", "2.5"):
            def tp(s, lvl=lvl):
                v = s.get("r_tp1v")
                v = json.loads(v) if isinstance(v, str) else v
                return (v[lvl] - v["1.0"]) if v and lvl in v and "1.0" in v else None
            paired(rows, tp, f"{grp}: TP1 {lvl}R − TP1 1R")
        paired(rows, lambda s: (s["r_sl25_trail"] - s["r"]) if isinstance(s.get("r_sl25_trail"), (int, float)) else None,
               f"{grp}: SL 2,5 ATR + bám sóng − hybrid")

    print("\n# Tự học đã đổi gì (learn_log)")
    for l in d.get("learn_log", []):
        print(f"  {datetime.fromtimestamp(l['ts'], VN):%d/%m %H:%M} {l['kind']} {l['k']}: {l['old']} → {l['new']} ({l['status']})")
    print("  learned:", json.dumps(d.get("learned"), ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1])
