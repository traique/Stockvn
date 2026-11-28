"""Chẩn đoán 28/11/2026 – đọc kết quả backtest của `altcoin/tools/backtest_sessions.py --parts bot` (từ v3.4 mỗi tín hiệu ghi
regime BTC khung ngày `btc_trend_1d`, `btc_chg_7d` và danh sách luật khớp `blocked`) và trả lời: cổng "long khi BTC ngày đang
giảm / short khi BTC ngày đang tăng" có đáng bật không. Chỉ xét tín hiệu bot THẬT SỰ vào được: đạt điểm (passed) và không dính
luật chặn sẵn (DEFAULT_BLOCKS); R theo cách thoát hybrid (mặc định của bot), đã trừ phí. Sai số chuẩn gom cụm theo ngày.
Chia 60% đầu (trong mẫu) / 40% cuối (ngoài mẫu) theo thời gian như các đợt chẩn đoán trước.

    cd altcoin && python tools/backtest_sessions.py --parts bot --days 88 --symbols <50 mã crypto> --cache .bt_cache --out bt88.json
    python docs/chandoan_2026-11-28/regime_backtest.py altcoin/bt88.json

Chỉ thư viện chuẩn."""
import datetime as dt
import json
import math
import statistics as st
import sys
from collections import defaultdict

DEFAULT_BLOCKS = ("setup_range", "night_session", "afternoon_session", "climax_volume")
KEY = "r_hybrid"


def clustered(rows):
    by = defaultdict(list)
    for r in rows:
        by[r["day"]].append(r[KEY])
    vals = [v for vs in by.values() for v in vs]
    n = len(vals)
    if n < 2:
        return (vals[0] if vals else float("nan")), 0.0, n, len(by)
    m = st.mean(vals)
    se = math.sqrt(sum(sum(v - m for v in vs) ** 2 for vs in by.values())) / n
    return m, se, n, len(by)


def fmt(rows):
    m, se, n, k = clustered(rows)
    if not n:
        return "n=0"
    win = 100 * sum(1 for r in rows if r[KEY] > 0) / n
    return f"n={n:4d} ngày={k:2d} R={m:+.3f}±{se:.3f} t={m / se if se else 0:+.1f} thắng={win:.0f}% ΣR={sum(r[KEY] for r in rows):+.1f}"


def show(label, rows):
    print(f"  {label:46s} {fmt(rows)}")


def main(path):
    d = json.load(open(path, encoding="utf-8"))
    rows = [r for r in d["bot"] if r.get(KEY) is not None]
    print(f"# Backtest {d['meta']['from']} → {d['meta']['to']}, {len(d['meta']['symbols'])} coin, {len(rows)} tín hiệu kỹ thuật")
    ent = [r for r in rows if r["passed"] and not (set(r["blocked"]) & set(DEFAULT_BLOCKS))]
    print(f"  vào được (đạt điểm, không luật chặn sẵn): {len(ent)}; thiếu regime ngày: {sum(1 for r in ent if r.get('btc_trend_1d') is None)}")
    ts = sorted(r["ts"] for r in ent)
    cut = ts[int(len(ts) * 0.6)] if ts else 0
    IN = [r for r in ent if r["ts"] < cut]
    OUT = [r for r in ent if r["ts"] >= cut]
    print(f"  trong mẫu tới {dt.datetime.fromtimestamp(cut, dt.timezone.utc):%Y-%m-%d}, ngoài mẫu sau đó")

    def regime(r):
        return {1: "BTC ngày tăng", 0: "BTC ngày chưa rõ", -1: "BTC ngày giảm"}.get(r.get("btc_trend_1d"), "không có dữ liệu")

    for name, sub in (("Cả kỳ", ent), ("Trong mẫu", IN), ("Ngoài mẫu", OUT)):
        print(f"\n## {name}")
        show("mọi lệnh", sub)
        for side in ("long", "short"):
            S = [r for r in sub if r["side"] == side]
            show(side, S)
            for rg in ("BTC ngày tăng", "BTC ngày chưa rõ", "BTC ngày giảm"):
                show(f"    {side} khi {rg}", [r for r in S if regime(r) == rg])
        gated = [r for r in sub if not ((r["side"] == "long" and r.get("btc_trend_1d") == -1) or
                                        (r["side"] == "short" and r.get("btc_trend_1d") == 1))]
        cut_ = [r for r in sub if r not in gated]
        show("bị cổng ngày loại (long/bear, short/bull)", cut_)
        show("còn lại sau cổng ngày", gated)
        print(f"    ngày có tín hiệu bị loại: {len(set(r['day'] for r in cut_))}; số ngày BTC giảm/tăng/chưa rõ: "
              f"{len(set(r['day'] for r in sub if r.get('btc_trend_1d') == -1))}/{len(set(r['day'] for r in sub if r.get('btc_trend_1d') == 1))}/"
              f"{len(set(r['day'] for r in sub if r.get('btc_trend_1d') == 0))}")

    print("\n## Tham khảo: theo % đổi 7 ngày của BTC (cả kỳ, mọi lệnh vào được)")
    for lo, hi in ((-99, -5), (-5, 0), (0, 5), (5, 99)):
        for side in ("long", "short"):
            show(f"  {side}, BTC 7 ngày {lo:+d}..{hi:+d}%", [r for r in ent if r["side"] == side and r.get("btc_chg_7d") is not None
                                                             and lo <= r["btc_chg_7d"] < hi])
    print("\n## Tham khảo: cổng 1h hiện có đã làm gì (cả kỳ)")
    for side in ("long", "short"):
        for tr in (1, 0, -1):
            show(f"  {side} khi btc_trend_1h = {tr:+d}", [r for r in ent if r["side"] == side and r.get("btc_trend_1h") == tr])


if __name__ == "__main__":
    main(sys.argv[1])
