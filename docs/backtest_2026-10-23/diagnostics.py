"""Why so few trades: action histogram, the gate that blocks, orders planned vs filled,
average exposure; then a second batch of variants aimed at the blocking gate."""
import pickle
import re
import sys
from collections import Counter
from dataclasses import replace

sys.path.insert(0, ".")
from tradingagents.quant import policy  # noqa: E402
from tradingagents.quant.params import DEFAULT  # noqa: E402
from tradingagents.trading import simulator as sim  # noqa: E402
from tradingagents.trading.ledger import Ledger  # noqa: E402

OUT = "./docs/backtest_2026-10-23/out"
with open(f"{OUT}/ds_engine.pkl", "rb") as f:
    ds = pickle.load(f)

REASONS = [
    ("RR dưới ngưỡng", r"risk/reward .* dưới ngưỡng"),
    ("không có kháng cự thật", r"không có kháng cự thật"),
    ("confidence chưa đạt", r"confidence chưa đạt ngưỡng"),
    ("regime risk-off/unknown", r"risk-off - hạn chế|không xác định được regime"),
    ("phiên rủi ro (hard_no_buy)", r"phiên gần nhất có rủi ro"),
    ("không tính được stop", r"không tính được stop"),
    ("mâu thuẫn", r"mâu thuẫn"),
    ("chưa có tín hiệu", r"chưa có tín hiệu rõ ràng"),
]


def histogram(p):
    actions, why, bias_bull = Counter(), Counter(), 0
    for (sym, day), inp in ds.inputs.items():
        d = policy.evaluate_policy(replace(inp, params=p, is_holding=False))
        actions[d.action] += 1
        if d.action in ("WATCH", "NO_TRADE"):
            text = " | ".join(d.reasons)
            for label, rx in REASONS:
                if re.search(rx, text):
                    why[label] += 1
                    break
            else:
                why["khác"] += 1
    return actions, why


def exposure_run(p, **kw):
    planned = Counter()
    orig_plan, orig_end = Ledger.plan_orders, Ledger.end_of_session
    expo = []

    def plan(self, date, signals, **k):
        new = orig_plan(self, date, signals, **k)
        planned["orders"] += sum(1 for o in new if o.side == "BUY")
        planned["buy_signals"] += sum(1 for s in signals if s.action == "BUY")
        return new

    def end(self, date, benchmark=None):
        orig_end(self, date, benchmark)
        nav = self.nav()
        expo.append((nav - self.cash) / nav if nav else 0.0)

    Ledger.plan_orders, Ledger.end_of_session = plan, end
    try:
        book = sim.run(ds, p, **kw)
    finally:
        Ledger.plan_orders, Ledger.end_of_session = orig_plan, orig_end
    fills = sum(1 for f in book.fills if f.side == "BUY")
    return book, planned, fills, sum(expo) / max(len(expo), 1)


def line(name, s):
    return (f"{name:<48} lãi {s.return_pct:+7.2f}%  DD {s.max_drawdown_pct:5.2f}%  lệnh {s.trades:3d}  "
            f"thắng {s.win_rate_pct if s.win_rate_pct is not None else float('nan'):5.1f}%  điểm {s.value:+7.2f}")


if __name__ == "__main__":
    D = DEFAULT
    print("=== Phân bố action của engine (mặc định) trên", len(ds.inputs), "cặp (mã, phiên) ===")
    acts, why = histogram(D)
    print(dict(acts))
    print("Vì sao WATCH/NO_TRADE:", dict(why.most_common()))
    print("\n=== Lệnh đặt / khớp / tỷ trọng đầu tư trung bình ===")
    for name, p in [("mặc định", D), ("buy_order_sessions=3", replace(D, buy_order_sessions=3)),
                    ("limit_fill_touch=1", replace(D, limit_fill_touch=1))]:
        book, planned, fills, expo = exposure_run(p)
        print(f"{name:<28} tín hiệu BUY {planned['buy_signals']:4d}  lệnh đặt {planned['orders']:4d}  khớp {fills:3d}  "
              f"tỷ trọng đầu tư TB {expo * 100:5.1f}%  lãi {book.stats()['return_pct']:+.2f}%")

    cut = ds.dates[int(len(ds.dates) * 2 / 3)]
    print(f"\n=== Đợt 2: nới cổng chặn (train ..{cut}, test {cut}..) ===")
    variants = [
        ("mặc định", D),
        ("skip_near_resistance=1", replace(D, skip_near_resistance=1)),
        ("min_rr_ratio=1.2", replace(D, min_rr_ratio=1.2)),
        ("skip_near + min_rr 1.2", replace(D, skip_near_resistance=1, min_rr_ratio=1.2)),
        ("skip_near + breakout_projected", replace(D, skip_near_resistance=1, breakout_projected_ok=1)),
        ("confidence_buy_min=0.65", replace(D, confidence_buy_min=0.65)),
        ("risk_per_trade_pct=2.0", replace(D, risk_per_trade_pct=2.0)),
        ("risk 2.0 + max_positions 8", replace(D, risk_per_trade_pct=2.0, max_positions=8)),
        ("stop_min_pct=3 + flow 0.15", replace(D, stop_min_pct=3.0, flow_weight=0.15)),
        ("skip_near + stop_min 3 + flow 0.15", replace(D, skip_near_resistance=1, stop_min_pct=3.0, flow_weight=0.15)),
        ("skip_near + risk 2.0 + stop_min 3", replace(D, skip_near_resistance=1, risk_per_trade_pct=2.0, stop_min_pct=3.0)),
        ("dist_min_drop_pct=0.5", replace(D, dist_min_drop_pct=0.5)),
        ("distribution_day_threshold=6", replace(D, distribution_day_threshold=6)),
    ]
    for name, p in variants:
        full, planned, fills, expo = exposure_run(p)
        tr = sim.run(ds, p, end=cut)
        te = sim.run(ds, p, start=cut)
        print(line(name + " [toàn bộ]", sim.score(full)) + f"  đầu tư TB {expo * 100:4.1f}%")
        print(line(name + " [train]", sim.score(tr)))
        print(line(name + " [test]", sim.score(te)))
    print("\nDONE")
