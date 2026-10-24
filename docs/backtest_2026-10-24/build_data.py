"""Daily bars with volume of the 39 stocks (the owner's 13 and VN30) and sieutinhieu.vn's
public signal histories, saved to out/ (run from the repo root, needs the network)."""
import json
import pickle
import sys
from pathlib import Path

sys.path.insert(0, ".")
from tradingagents.dataflows.money_flows import VN30_FALLBACK  # noqa: E402
from tradingagents.dataflows.ohlcv import load_ohlcv  # noqa: E402
from tradingagents.dataflows.vendors import sieutinhieu as stv  # noqa: E402

OUT = Path("docs/backtest_2026-10-24/out")
END = "2026-10-23"
OWNER = ["POW", "HDG", "VRE", "CTG", "TPB", "CII", "DIG", "CTS", "SHS", "FPT", "NVB", "SBS", "GEX"]
UNIVERSE = list(dict.fromkeys(OWNER + list(VN30_FALLBACK)))

if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    bars = {}
    for sym in UNIVERSE:
        f = load_ohlcv(sym, END, fill_gaps=False)
        bars[sym] = {"d": [x.strftime("%Y-%m-%d") for x in f["Date"]], "o": [float(x) for x in f["Open"]],
                     "h": [float(x) for x in f["High"]], "l": [float(x) for x in f["Low"]],
                     "c": [float(x) for x in f["Close"]], "v": [float(x) for x in f["Volume"]]}
        print(sym, len(f), bars[sym]["d"][0], bars[sym]["d"][-1], flush=True)
    pickle.dump(bars, open(OUT / "bars.pkl", "wb"))
    old = Path("docs/backtest_2026-10-23/out/stv_history.json")      # fetched on 23/10 for the exit rules
    if old.exists():
        signals = {s: v["signals"] for s, v in json.load(open(old, encoding="utf-8")).items()}
    else:
        signals = {s: stv.fetch_signals(s) for s in UNIVERSE}
    json.dump(signals, open(OUT / "signals.json", "w", encoding="utf-8"), ensure_ascii=False)
    print("symbols", len(bars), "signals", sum(len(v) for v in signals.values()))
