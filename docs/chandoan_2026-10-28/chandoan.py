"""Chạy lại chẩn đoán 28/10/2026 "vì sao chưa có MUA" (cần mạng; chạy từ gốc repo):

    python docs/chandoan_2026-10-28/chandoan.py [số phiên=250] [vn30|vn100]

1. Regime VNINDEX của Quant Engine từng phiên và lý do risk-off; VNINDEX so với MA200/MA100.
2. Dựng lại quyết định của engine cho rổ mã (vnbot.chandoan.replay, vào một kho tạm), rồi in phễu
   cổng chặn và kết quả bóng (như /vikhongmua). Kho tạm bị xoá khi xong.
"""
import logging
import sys
import tempfile
from collections import Counter

sys.path.insert(0, ".")
logging.basicConfig(level=logging.WARNING)

from tradingagents.dataflows.ohlcv import load_ohlcv  # noqa: E402
from tradingagents.market import vn_rules  # noqa: E402
from tradingagents.quant import features as feat, policy  # noqa: E402
from vnbot import chandoan as cd, store as st  # noqa: E402
from vnbot.trader import final_day, today_vn  # noqa: E402

SESSIONS = int(sys.argv[1]) if len(sys.argv) > 1 else 250
UNIVERSE = sys.argv[2] if len(sys.argv) > 2 else "vn100"
AS_OF = final_day()

vni = load_ohlcv(vn_rules.BENCHMARK, AS_OF)
C, H, L, V = (list(map(float, vni[c])) for c in ("Close", "High", "Low", "Volume"))
D = [d.strftime("%Y-%m-%d") for d in vni["Date"]]
regimes, why, above200, above100 = Counter(), Counter(), 0, 0
for i in range(len(C) - SESSIONS, len(C)):
    lo = max(0, i - 259)
    mtf = feat.calc_multi_timeframe(C[lo:i + 1])
    adx = feat.calc_adx(C[lo:i + 1], H[lo:i + 1], L[lo:i + 1])
    dist = feat.calc_distribution_days(C[lo:i + 1], V[lo:i + 1])
    regime = policy.classify_market_regime(mtf, adx, dist)
    regimes[regime] += 1
    if regime == "risk_off":
        why[" + ".join(x for x, on in (("ngày phân phối >= 4", dist >= 4), ("3 khung cùng giảm", mtf.alignment == "bearish"),
                                       ("ADX xác nhận giảm", adx.available and adx.trending and adx.di_minus > adx.di_plus))
                       if on)] += 1
    above200 += C[i] > sum(C[i - 199:i + 1]) / 200
    above100 += C[i] > sum(C[i - 99:i + 1]) / 100
print(f"VNINDEX {D[-SESSIONS]} - {D[-1]} ({SESSIONS} phiên): regime {dict(regimes)}")
print(f"  risk-off vì: {dict(why.most_common())}")
print(f"  trên MA200 {above200}/{SESSIONS} phiên, trên MA100 {above100}/{SESSIONS} phiên; phiên cuối {C[-1]:.2f}, "
      f"MA200 {sum(C[-200:]) / 200:.2f}, MA100 {sum(C[-100:]) / 100:.2f}")

with tempfile.TemporaryDirectory() as tmp:
    store = st.Store(tmp, url="")
    out = cd.replay(cd.replay_symbols(UNIVERSE, []), SESSIONS, as_of=AS_OF, store=store, today=today_vn())
    print(f"\nDựng lại {out['days']} phiên x {out['symbols']} mã = {out['rows']} quyết định ({out['from']} - {out['to']})")
    rows = store.select("SELECT action, gate, near FROM signals")
    print("  hành động:", dict(Counter(r["action"] for r in rows).most_common()))
    print("  cổng chặn đầu tiên:", dict(Counter(r["gate"] or "BUY" for r in rows).most_common()))
    print("  suýt MUA:", dict(Counter(r["near"] for r in rows if r["near"]).most_common()))
    print()
    print(cd.why_text(store).split("📏", 1)[1])
    store.close()
