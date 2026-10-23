"""Backtest of the Tài sản sell alerts as exit rules, on sieutinhieu.vn's public signal
histories (each stock's last 30 signals) and the daily bars.

Every trade enters at the open of the session after a site BUY (one position per stock).
Exit rules compared:
  R1 🔴 site SELL      : sell at the open after the site's SELL day (ATO).
  R2 🟠 early estimate : the bot's estimate (TB20 + MACD) says the trend flips in the session
                         (the day's low under the level) -> sell at that day's close (ATC);
                         a site SELL first -> as R1.
  R3 ⛔ cut-loss only  : hold until the low touches the site's cut-loss (sl of the BUY plan):
                         sell at min(open, sl); never on a site SELL.
  R4 ⛔ or 🔴 (now)     : whichever comes first (what the alerts do today).
T+2: nothing sold before the afternoon of the 2nd session after the buy (an ATO sale
before that moves to that afternoon's close). Fees 0.15% buy, 0.15% + 0.1% tax sell.
A trade still open at the last bar is valued at the last close (marked "open").
"""
import json
import statistics
import sys
import time

sys.path.insert(0, ".")          # run from the repo root
from tradingagents.dataflows.money_flows import VN30_FALLBACK  # noqa: E402
from tradingagents.dataflows.ohlcv import load_ohlcv  # noqa: E402
from tradingagents.dataflows.vendors import sieutinhieu as stv  # noqa: E402
from tradingagents.market import vn_rules  # noqa: E402
from vnbot import trend_estimate as te  # noqa: E402

OUT = "./docs/backtest_2026-10-23/out"
END = "2026-10-23"
OWNER = ["POW", "HDG", "VRE", "CTG", "TPB", "CII", "DIG", "CTS", "SHS", "FPT", "NVB", "SBS", "GEX"]
UNIVERSE = list(dict.fromkeys(OWNER + list(VN30_FALLBACK)))
BUY_COST = 1 + vn_rules.BUY_FEE_PCT / 100
SELL_KEEP = 1 - (vn_rules.SELL_FEE_PCT + vn_rules.SELL_TAX_PCT) / 100
RULES = ("R1", "R2", "R3", "R4")


def fetch():
    cache = f"{OUT}/stv_history.json"
    try:
        return json.load(open(cache, encoding="utf-8"))
    except (OSError, ValueError):
        pass
    data = {}
    for sym in UNIVERSE:
        try:
            sig = stv.fetch_signals(sym)
        except Exception as exc:  # noqa: BLE001
            print("signals", sym, exc)
            continue
        frame = load_ohlcv(sym, END, fill_gaps=False)
        data[sym] = {"signals": sig, "bars": {
            "d": [d.strftime("%Y-%m-%d") for d in frame["Date"]],
            "o": [float(x) for x in frame["Open"]], "h": [float(x) for x in frame["High"]],
            "l": [float(x) for x in frame["Low"]], "c": [float(x) for x in frame["Close"]]}}
        print("ok", sym, len(sig), len(frame))
        time.sleep(0.2)
    json.dump(data, open(cache, "w", encoding="utf-8"), ensure_ascii=False)
    return data


def simulate(sym, item, rule):
    sig = sorted(item["signals"], key=lambda s: (s["day"], s.get("created", "")))
    b = item["bars"]
    d, o, h, lo, c = b["d"], b["o"], b["h"], b["l"], b["c"]
    idx = {day: i for i, day in enumerate(d)}

    def after(day):                       # first bar after ``day``
        for i, x in enumerate(d):
            if x > day:
                return i
        return None

    sells = [s for s in sig if s["side"] == "SELL"]
    trades, busy_until = [], ""
    for s in sig:
        if s["side"] != "BUY" or s["day"] < busy_until:
            continue
        e = after(s["day"])
        if e is None:
            continue
        price_in = o[e]
        stop = s.get("sl") or 0
        site_sell = next((x for x in sells if x["day"] > s["day"]), None)
        j_site = after(site_sell["day"]) if site_sell else None
        exit_i, exit_px, why = None, None, "open"
        first_ok = e + 2                  # afternoon of T+2
        # walk the sessions after the entry
        for i in range(e, len(d)):
            if rule in ("R1", "R2", "R4") and j_site is not None and i == j_site:
                if i >= e + 3:
                    exit_i, exit_px, why = i, o[i], "site"
                else:
                    exit_i, exit_px, why = first_ok, c[first_ok] if first_ok < len(d) else c[-1], "site"
                break
            if i < first_ok:
                continue
            if rule in ("R3", "R4") and stop and lo[i] <= stop:
                exit_i, exit_px, why = i, min(o[i], stop), "stop"
                break
            if rule == "R2":
                lv = te.levels(c[max(0, i - 250):i])
                if lv and lo[i] < lv["sell_below"]:
                    exit_i, exit_px, why = i, c[i], "early"
                    break
        if exit_i is None:
            exit_i, exit_px = len(d) - 1, c[-1]
        ret = exit_px * SELL_KEEP / (price_in * BUY_COST) - 1
        trades.append({"sym": sym, "buy": s["day"], "in": price_in, "out_day": d[exit_i], "out": exit_px,
                       "ret": ret, "why": why, "sessions": exit_i - e, "live": bool(s.get("live"))})
        busy_until = d[exit_i]
    return trades


def summarize(trades):
    rets = [t["ret"] for t in trades]
    closed = [t for t in trades if t["why"] != "open"]
    comp = 1.0
    for r in rets:
        comp *= 1 + r
    return {"trades": len(trades), "closed": len(closed),
            "win": sum(1 for r in rets if r > 0) / len(rets) * 100 if rets else None,
            "avg": statistics.mean(rets) * 100 if rets else None,
            "median": statistics.median(rets) * 100 if rets else None,
            "worst": min(rets) * 100 if rets else None, "best": max(rets) * 100 if rets else None,
            "sessions": statistics.mean(t["sessions"] for t in trades) if trades else None,
            "why": {k: sum(1 for t in trades if t["why"] == k) for k in ("site", "early", "stop", "open")}}


def per_symbol_comp(trades_by_sym):
    out = []
    for sym, trades in trades_by_sym.items():
        comp = 1.0
        for t in trades:
            comp *= 1 + t["ret"]
        out.append(comp - 1)
    return statistics.mean(out) * 100 if out else None, statistics.median(out) * 100 if out else None


if __name__ == "__main__":
    data = fetch()
    print("symbols", len(data), "signals", sum(len(v["signals"]) for v in data.values()))
    result = {}
    for group, syms in (("owner", [s for s in OWNER if s in data]), ("all", list(data))):
        result[group] = {}
        for rule in RULES:
            by = {s: simulate(s, data[s], rule) for s in syms}
            allt = [t for v in by.values() for t in v]
            live = [t for t in allt if t["live"]]
            summ = summarize(allt)
            summ["sym_comp_mean"], summ["sym_comp_median"] = per_symbol_comp(by)
            summ["live"] = summarize(live)
            result[group][rule] = summ
            print(group, rule, json.dumps({k: (round(v, 2) if isinstance(v, float) else v) for k, v in summ.items()
                                           if k != "live"}, ensure_ascii=False))
            print("   live", json.dumps({k: (round(v, 2) if isinstance(v, float) else v)
                                         for k, v in summ["live"].items()}, ensure_ascii=False))
    # Early alerts that were wrong: the site did not turn SELL within 5 sessions of the alert.
    early = [t for s in data for t in simulate(s, data[s], "R2") if t["why"] == "early"]
    r1 = {(t["sym"], t["buy"]): t for s in data for t in simulate(s, data[s], "R1")}
    better = [t for t in early if (t["sym"], t["buy"]) in r1 and t["ret"] > r1[(t["sym"], t["buy"])]["ret"]]
    result["early_vs_site"] = {"early_exits": len(early), "better_than_site": len(better)}
    print("early exits better than waiting for the site:", len(better), "/", len(early))
    json.dump(result, open(f"{OUT}/exit_rules_result.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
