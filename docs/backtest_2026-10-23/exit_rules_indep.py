"""Every site BUY as its own trade (no 'one position at a time'), so the rules exit the
same entries; R5 = half at R4 (cut-loss or site SELL), half held to the cut-loss (R3)."""
import json
import statistics
import sys

sys.path.insert(0, "./docs/backtest_2026-10-23")
import exit_rules as er  # noqa: E402

data = json.load(open(f"{er.OUT}/stv_history.json", encoding="utf-8"))


def trades(sym, rule):
    item = data[sym]
    out = []
    buys = [s for s in item["signals"] if s["side"] == "BUY"]
    for b in buys:
        one = {"signals": [b] + [s for s in item["signals"] if s["side"] == "SELL"], "bars": item["bars"]}
        got = er.simulate(sym, one, rule)
        out += got
    return out


def stats(rets, sessions):
    loss = [r for r in rets if r < 0]
    return {"n": len(rets), "avg": statistics.mean(rets) * 100, "median": statistics.median(rets) * 100,
            "win": sum(1 for r in rets if r > 0) / len(rets) * 100, "avg_loss": statistics.mean(loss) * 100,
            "under10": sum(1 for r in rets if r < -0.10), "worst": min(rets) * 100, "sessions": statistics.mean(sessions)}


res = {}
for group, syms in (("owner", [s for s in er.OWNER if s in data]), ("all", list(data))):
    per = {r: {} for r in er.RULES}
    for r in er.RULES:
        for s in syms:
            for t in trades(s, r):
                per[r][(s, t["buy"])] = t
    keys = sorted(set.intersection(*(set(v) for v in per.values())))
    res[group] = {}
    for r in er.RULES:
        res[group][r] = stats([per[r][k]["ret"] for k in keys], [per[r][k]["sessions"] for k in keys])
    mix = [(per["R4"][k]["ret"] + per["R3"][k]["ret"]) / 2 for k in keys]
    res[group]["R5"] = stats(mix, [(per["R4"][k]["sessions"] + per["R3"][k]["sessions"]) / 2 for k in keys])
    live = [k for k in keys if per["R1"][k]["live"]]
    res[group]["live_n"] = len(live)
    for r in er.RULES:
        if live:
            res[group][r + "_live"] = stats([per[r][k]["ret"] for k in live], [per[r][k]["sessions"] for k in live])
    for r in list(er.RULES) + ["R5"]:
        x = res[group][r]
        print(group, r, " ".join(f"{k}={v:.2f}" if isinstance(v, float) else f"{k}={v}" for k, v in x.items()))
    for r in er.RULES:
        x = res[group].get(r + "_live")
        if x:
            print("  live", r, " ".join(f"{k}={v:.2f}" if isinstance(v, float) else f"{k}={v}" for k, v in x.items()))
json.dump(res, open(f"{er.OUT}/exit_rules_indep.json", "w", encoding="utf-8"), indent=1)
