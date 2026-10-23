"""Backtest the part C variants on the cached datasets (engine: VN30, factor: VN100).

Train = the first ~2/3 of the window, Test = the last ~1/3 (never used to pick a variant).
Score = return % - 0.5 x max drawdown % (the calibration's own score)."""
import json
import pickle
import sys
from dataclasses import replace

sys.path.insert(0, ".")
from tradingagents.quant.params import DEFAULT  # noqa: E402
from tradingagents.trading import factor, simulator as sim  # noqa: E402

OUT = "./docs/backtest_2026-10-23/out"


def split(ds):
    n = len(ds.dates)
    cut = ds.dates[int(n * 2 / 3)]
    return ds.dates[0], cut, ds.dates[-1]


def line(name, s):
    return (f"{name:<42} lãi {s.return_pct:+7.2f}%  VNI {s.benchmark_pct if s.benchmark_pct is not None else float('nan'):+7.2f}%  "
            f"DD {s.max_drawdown_pct:5.2f}%  lệnh {s.trades:3d}  thắng {s.win_rate_pct if s.win_rate_pct is not None else float('nan'):5.1f}%  điểm {s.value:+7.2f}")


def fills_stats(ledger):
    buys = sum(1 for f in ledger.fills if f.side == "BUY")
    return buys


def run_engine(ds, variants):
    start, cut, end = split(ds)
    print(f"\n=== QUANT ENGINE (VN30, {len(ds.symbols)} mã, {len(ds.dates)} phiên {start}..{end}; train ..{cut}, test {cut}..) ===")
    results = {}
    for name, p in variants:
        full = sim.run(ds, p)
        tr = sim.run(ds, p, end=cut)
        te = sim.run(ds, p, start=cut)
        results[name] = {"full": sim.score(full).__dict__, "train": sim.score(tr).__dict__, "test": sim.score(te).__dict__,
                         "orders_planned": sum(1 for f in full.fills if f.side == "BUY")}
        print(line(name + " [toàn bộ]", sim.score(full)))
        print(line(name + " [train]", sim.score(tr)))
        print(line(name + " [test]", sim.score(te)))
    return results


def run_factor(ds, variants):
    start, cut, end = split(ds)
    print(f"\n=== PHÒNG THỦ (VN100 hôm nay - thiên lệch sống sót, {len(ds.symbols)} mã, {len(ds.dates)} phiên; train ..{cut}, test {cut}..) ===")
    results = {}
    for name, p in variants:
        full = factor.run(ds, p)
        tr = factor.run(ds, p, end=cut)
        te = factor.run(ds, p, start=cut)
        results[name] = {"full": sim.score(full).__dict__, "train": sim.score(tr).__dict__, "test": sim.score(te).__dict__}
        print(line(name + " [toàn bộ]", sim.score(full)))
        print(line(name + " [train]", sim.score(tr)))
        print(line(name + " [test]", sim.score(te)))
    return results


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    out = {}
    if which in ("all", "engine"):
        with open(f"{OUT}/ds_engine.pkl", "rb") as f:
            ds = pickle.load(f)
        D = DEFAULT
        variants = [
            ("mặc định", D),
            ("C1 breakout_projected_ok=1", replace(D, breakout_projected_ok=1)),
            ("C1 breakout + projected_rr 2.5", replace(D, breakout_projected_ok=1, projected_rr=2.5)),
            ("C2 buy_order_sessions=2", replace(D, buy_order_sessions=2)),
            ("C2 buy_order_sessions=3", replace(D, buy_order_sessions=3)),
            ("C2 limit_fill_touch=1", replace(D, limit_fill_touch=1)),
            ("C3 trail_pct=8", replace(D, trail_pct=8.0)),
            ("C3 trail_pct=10, use_t2=0", replace(D, trail_pct=10.0, use_t2=0)),
            ("C4 stop_min_pct=3", replace(D, stop_min_pct=3.0)),
            ("C4 stop_atr_mult=2.0", replace(D, stop_atr_mult=2.0)),
            ("C4 stop_atr_mult=2.5, stop_min_pct=3", replace(D, stop_atr_mult=2.5, stop_min_pct=3.0)),
            ("C5 confidence_sell_min=0.65", replace(D, confidence_sell_min=0.65)),
            ("C6 flow_weight=0.10", replace(D, flow_weight=0.10)),
            ("C6 flow_weight=0.15", replace(D, flow_weight=0.15)),
            ("max_hold_sessions=45", replace(D, max_hold_sessions=45)),
            ("confidence_buy_min=0.70", replace(D, confidence_buy_min=0.70)),
        ]
        out["engine"] = run_engine(ds, variants)
        with open(f"{OUT}/engine_results.json", "w") as f:
            json.dump(out["engine"], f, indent=1, ensure_ascii=False)
    if which in ("all", "factor"):
        with open(f"{OUT}/ds_factor.pkl", "rb") as f:
            ds2 = pickle.load(f)
        D = DEFAULT
        variants = [
            ("mặc định (tuần, MA200)", D),
            ("C7a kiểm tra hằng ngày, đệm 2%", replace(D, fb_market_daily=1, fb_market_buffer_pct=2.0)),
            ("C7a kiểm tra hằng ngày, đệm 0%", replace(D, fb_market_daily=1, fb_market_buffer_pct=0.0)),
            ("C7b động lượng 0.5", replace(D, fb_momentum_weight=0.5)),
            ("C7b động lượng 1.0", replace(D, fb_momentum_weight=1.0)),
            ("C7a+b hằng ngày 2% + động lượng 0.5", replace(D, fb_market_daily=1, fb_market_buffer_pct=2.0, fb_momentum_weight=0.5)),
        ]
        out["factor"] = run_factor(ds2, variants)
        with open(f"{OUT}/factor_results.json", "w") as f:
            json.dump(out["factor"], f, indent=1, ensure_ascii=False)
    print("\nDONE")
