"""Bổ sung chẩn đoán 28/10/2026: vì sao các lệnh MUA của Quant Engine lỗ (cần mạng; chạy từ gốc repo):

    python docs/chandoan_2026-10-28/vi_sao_lo.py [số phiên=900] [file.pkl]

Dựng lại đầu vào của engine cho VN100 + VN30 ở từng phiên (simulator.build_dataset, không dòng tiền; 900 phiên
khoảng 15 phút; có ``file.pkl`` thì đọc lại, chưa có thì lưu vào đó), rồi so "năm cuối" (250 phiên gần nhất)
với các phiên trước đó:
1. VNINDEX so với cổ phiếu VN100 trong năm cuối: chỉ số do ai kéo.
2. Mỗi lệnh MUA (thật, và bị chặn chỉ vì thị trường) đi theo kế hoạch như sổ ảo (chandoan.simulate_plan): theo
   loại setup; khoảng cách cắt lỗ/mục tiêu; sau khi cắt lỗ giá đi đâu; nếu giữ nguyên không cắt lỗ.
3. Mọi mã xu hướng tăng phá kênh 20 phiên (breakout), theo việc engine có MUA không và vì sao (nếu thị trường trung
   tính): giá 10 và 20 phiên sau.
4. Độ tin cậy của mọi mã xu hướng tăng (nếu thị trường trung tính) so với lợi nhuận 10 phiên sau, vượt VNINDEX.
"""
import logging
import math
import os
import pickle
import statistics
import sys
from collections import Counter
from dataclasses import replace

sys.path.insert(0, ".")
logging.basicConfig(level=logging.ERROR)

from tradingagents.quant import policy  # noqa: E402
from tradingagents.trading import simulator as sim  # noqa: E402
from vnbot import chandoan as cd  # noqa: E402
from vnbot.trader import final_day  # noqa: E402

SESSIONS = int(sys.argv[1]) if len(sys.argv) > 1 else 900
CACHE = sys.argv[2] if len(sys.argv) > 2 else None
YEAR = 250

if CACHE and os.path.exists(CACHE):
    with open(CACHE, "rb") as f:
        ds = pickle.load(f)
else:
    ds = sim.build_dataset(cd.replay_symbols("vn100", []), SESSIONS, final_day(), flows=False, inputs=True)
    if CACHE:
        with open(CACHE, "wb") as f:
            pickle.dump(ds, f)
dates, bench = ds.dates, ds.benchmark
cut = dates[-YEAR] if len(dates) > YEAR else dates[0]
series = {}
for s, bars in ds.bars.items():
    days = sorted(bars)
    series[s] = (days, *([getattr(bars[d], k) for d in days] for k in ("open", "high", "low", "close")),
                 {d: i for i, d in enumerate(days)})
vdays = sorted(bench)
vix = {d: i for i, d in enumerate(vdays)}


def ret(s, day, k):
    """Giá đóng cửa ``k`` phiên sau so với giá mở cửa phiên kế tiếp, %."""
    _, o, _, _, c, ix = series[s]
    i = ix[day]
    return (c[i + k] / o[i + 1] - 1) * 100 if i + k < len(c) and o[i + 1] > 0 and c[i + k] > 0 else None


def vret(day, k):
    i = vix[day]
    return (bench[vdays[i + k]] / bench[day] - 1) * 100 if i + k < len(vdays) else None


plans, bulls, breakouts = [], [], []
for (s, day), inputs in ds.inputs.items():
    inputs = replace(inputs, is_holding=False)
    d = policy.evaluate_policy(inputs)
    lifted = policy.evaluate_policy(inputs, regime_override="neutral") if "market" in d.gates else d
    _, o, h, lo, c, ix = series[s]
    i = ix[day]
    if d.bias == "bullish":
        r10, v10 = ret(s, day, 10), vret(day, 10)
        bulls.append(dict(day=day, conf=lifted.confidence, ex10=r10 - v10 if r10 is not None and v10 is not None else None))
        if d.setup_type == "breakout":
            why = ("engine MUA (còn kháng cự phía trên, R:R đạt)" if lifted.action == "BUY" else
                   "không MUA: phá đỉnh, không còn kháng cự" if "resistance" in lifted.gates else
                   "không MUA: R:R tới kháng cự dưới ngưỡng" if "rr" in lifted.gates else
                   "không MUA: thiếu độ tin cậy" if "confidence" in lifted.gates else "không MUA: cổng khác")
            breakouts.append(dict(day=day, why=why, r10=r10, r20=ret(s, day, 20)))
    if lifted.action != "BUY" or not lifted.trade_plan:
        continue
    eh, stop, target = lifted.trade_plan.entry_high, lifted.stop_price, lifted.target_price
    res = cd.simulate_plan(o, h, lo, c, i + 1, eh, stop, target)
    if not res or res[0] == "NODATA":
        continue
    p = dict(s=s, day=day, kind="MUA thật" if d.action == "BUY" else "bị chặn vì thị trường", setup=d.setup_type,
             res=res[0], R=res[1], stop_pct=(eh - stop) / eh * 100, target_pct=(target - eh) / eh * 100,
             stop_atr=(eh - stop) / inputs.enhanced.atr14 if inputs.enhanced and inputs.enhanced.atr14 else None,
             hold10=ret(s, day, 10), hold20=ret(s, day, 20))
    if res[0] == "SL":
        fill = min(o[i + 1], eh)
        p["after20"] = (c[i + 20] / fill - 1) * 100 if i + 20 < len(c) else None
        p["target_later"] = any(h[j] >= target for j in range(i + 3, min(i + 31, len(h))))
    plans.append(p)


def r_line(rows):
    rs = [r["R"] for r in rows if r["R"] is not None]
    if not rs:
        return "chưa có lệnh khớp"
    n, mean = len(rs), sum(rs) / len(rs)
    t = mean / (statistics.stdev(rs) / math.sqrt(n)) if n > 2 and statistics.stdev(rs) else 0.0
    k = Counter(r["res"] for r in rows)
    return (f"{n:3d} lệnh khớp ({len({r['day'] for r in rows})} ngày): chốt lời {k['TP']:2d}, cắt lỗ {k['SL']:2d}, "
            f"hết hạn {k['TIMEOUT']:2d}, TB {mean:+.2f}R (t {t:+.1f}), cộng {sum(rs):+.1f}R")


def pct_line(xs):
    xs = [x for x in xs if x is not None]
    if len(xs) < 3:
        return f"n={len(xs)}"
    mean = sum(xs) / len(xs)
    return f"n={len(xs):5d} TB {mean:+.2f}% (t {mean / (statistics.stdev(xs) / math.sqrt(len(xs))):+.1f})"


last = dates[-1]
print(f"Dữ liệu {dates[0]} - {last}, {len(ds.symbols)} mã; năm cuối từ {cut}")
moves = {s: (ds.bars[s][last].close / ds.bars[s][cut].close - 1) * 100
         for s in ds.symbols if cut in ds.bars.get(s, {}) and last in ds.bars[s]}
v_year = (bench[last] / bench[cut] - 1) * 100
print(f"\n1. Năm cuối: VNINDEX {v_year:+.1f}%, cổ phiếu trung vị {statistics.median(moves.values()):+.1f}%; "
      f"{sum(m > 0 for m in moves.values())}/{len(moves)} mã tăng, {sum(m > v_year for m in moves.values())} mã hơn VNINDEX; "
      "mạnh nhất " + ", ".join(f"{s} {m:+.0f}%" for s, m in sorted(moves.items(), key=lambda kv: -kv[1])[:5]))

print("\n2. Lệnh MUA theo kế hoạch như sổ ảo (giới hạn ở cận trên vùng mua, bán từ T+2, cắt lỗ/mục tiêu, tối đa 30 phiên)")
for name, rows in ((f"trước {cut}", [p for p in plans if p["day"] < cut]), ("năm cuối", [p for p in plans if p["day"] >= cut])):
    print(f"  {name}:")
    for label, keep in (("MUA thật", lambda p: p["kind"] == "MUA thật"),
                        ("bị chặn vì thị trường", lambda p: p["kind"] != "MUA thật"),
                        ("setup breakout", lambda p: p["setup"] == "breakout"),
                        ("setup pullback", lambda p: p["setup"] == "pullback")):
        print(f"    {label:22} {r_line([p for p in rows if keep(p)])}")
    for setup in ("breakout", "pullback"):
        sub = [p for p in rows if p["setup"] == setup and p["res"] != "NOFILL"]
        print(f"    {setup} giữ nguyên không cắt lỗ: 10 phiên {pct_line([p['hold10'] for p in sub])}, "
              f"20 phiên {pct_line([p['hold20'] for p in sub])}")
filled = [p for p in plans if p["res"] != "NOFILL"]
stop_pct = statistics.median(p["stop_pct"] for p in filled)
target_pct = statistics.median(p["target_pct"] for p in filled)
print(f"  Trung vị: cắt lỗ -{stop_pct:.1f}% ({statistics.median(p['stop_atr'] for p in filled if p['stop_atr']):.2f} ATR), "
      f"mục tiêu +{target_pct:.1f}%: không có lợi thế gì thì khoảng {target_pct / (target_pct + stop_pct):.0%} lệnh chạm cắt lỗ trước")
for name, rows in ((f"trước {cut}", [p for p in plans if p["day"] < cut and p["res"] == "SL"]),
                   ("năm cuối", [p for p in plans if p["day"] >= cut and p["res"] == "SL"])):
    after = [p["after20"] for p in rows if p.get("after20") is not None]
    if after:
        print(f"  Sau khi cắt lỗ ({name}, {len(rows)} lệnh): 20 phiên sau tín hiệu giá TB {sum(after) / len(after):+.1f}% so với giá mua, "
              f"{sum(a > 0 for a in after)} lệnh cao hơn giá mua, {sum(p['target_later'] for p in rows)} lệnh tới mục tiêu sau đó")

print("\n3. Mã xu hướng tăng phá kênh 20 phiên, theo quyết định của engine nếu thị trường trung tính: giá 10 | 20 phiên sau")
for name, rows in ((f"trước {cut}", [b for b in breakouts if b["day"] < cut]),
                   ("năm cuối", [b for b in breakouts if b["day"] >= cut])):
    print(f"  {name}:")
    for why, _ in Counter(b["why"] for b in rows).most_common():
        sub = [b for b in rows if b["why"] == why]
        print(f"    {why:46} {pct_line([b['r10'] for b in sub])} | {pct_line([b['r20'] for b in sub])}")

print("\n4. Mã xu hướng tăng theo độ tin cậy nếu thị trường trung tính: 10 phiên sau, vượt VNINDEX (cả kỳ)")
for lo_, hi_ in ((0, .55), (.55, .65), (.65, .75), (.75, .85), (.85, 1.01)):
    print(f"  {lo_:.2f} - {min(hi_, 1):.2f}: {pct_line([b['ex10'] for b in bulls if lo_ <= b['conf'] < hi_])}")
