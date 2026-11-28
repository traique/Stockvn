"""Bộ kiểm định point-in-time: chạy Phòng thủ (``factor.run``) và Động lượng trên rổ point-in-time, so với
VNINDEX và với rổ "danh sách HOSE hôm nay" (thiên lệch sống sót). Chạy từ gốc repo sau ``fetch.py``:

    python -m research.pit.evaluate                 # bảng kết quả → màn hình + research/out/ket_qua_<ngày>.md/.json
    python -m research.pit.evaluate --variants      # thêm các phương án còn treo của Phòng thủ (báo cáo 23/10)
    python -m research.pit.evaluate --write research/KET_QUA.md     # ghi bảng vào tệp có trong repo

Giai đoạn (cố định từ kiểm định 15/10/2026, mỗi giai đoạn chạy riêng với 1 tỉ đồng tiền mặt; ALL là một lần chạy):
TRAIN 2013–2020, VALID 2021–2023, TEST 2024 → ngày dữ liệu. Chi phí như sổ ảo của bot (ledger: phí mua 0,15%,
phí bán 0,15% + thuế 0,1%, trượt giá mỗi chiều, khớp ở giá mở cửa phiên sau, T+2, lô 100, bước giá, phiên trắng
bên không khớp); tiền mặt không lãi. CAGR theo ngày lịch; sụt tối đa trên NAV cuối phiên; Sharpe = trung bình /
độ lệch chuẩn lợi nhuận ngày × √252 (lãi suất phi rủi ro 0). VNINDEX là chỉ số GIÁ (không cổ tức) trong khi chiến
lược tính trên giá đã điều chỉnh cổ tức: so sánh có lợi cho chiến lược khoảng 1,5–2,5 điểm %/năm.

Động lượng không có tham số rổ trong bot (``momentum.run`` xếp hạng mọi mã của Dataset), nên ở đây vòng lặp của
``momentum.run`` được chạy lại với ``momentum.decide`` / ``momentum.plan`` của bot trên rổ VN30 giả lập: 30 (hoặc
50) mã vốn hoá lớn nhất trong 100 mã HOSE thanh khoản nhất, dựng lại ở phiên cuối mỗi tháng và dùng cho tháng sau
(như kỳ xem xét rổ VN30), không nhìn trước.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import sys
import time
from dataclasses import replace
from datetime import date
from pathlib import Path

from tradingagents.quant.params import DEFAULT, Params
from tradingagents.trading import factor, momentum
from tradingagents.trading.ledger import Ledger

from research.pit import dataset, fetch
from research.pit.universe import Universe

START_CASH = 1_000_000_000.0
SEGMENTS = (("TRAIN", "2013-01-01", "2020-12-31"), ("VALID", "2021-01-01", "2023-12-31"),
            ("TEST", "2024-01-01", None), ("ALL", "2013-01-01", None))
# Phương án còn treo của Phòng thủ (docs/BAO_CAO_RA_SOAT_2026-10-23.md, mục E: "để cho lần kiểm định điểm-theo-thời-gian")
VARIANTS = (("lọc MA200 hằng ngày, đệm 2%", {"fb_market_daily": 1, "fb_market_buffer_pct": 2.0}),
            ("thêm động lượng 6 tháng ×0,5", {"fb_momentum_weight": 0.5}))


# ─── chỉ số ─────────────────────────────────────────────────────────────────

def metrics(history: list[dict], start_cash: float = START_CASH) -> dict:
    """CAGR %, sụt tối đa %, Sharpe, lợi nhuận % của một lịch sử NAV [{date, nav}] (tiền đầu kỳ = start_cash)."""
    if not history:
        return {"cagr": None, "mdd": None, "sharpe": None, "ret": None, "days": 0}
    navs = [float(start_cash)] + [float(h["nav"]) for h in history]
    first, last = date.fromisoformat(history[0]["date"]), date.fromisoformat(history[-1]["date"])
    years = max((last - first).days, 1) / 365.25
    ret = navs[-1] / navs[0] - 1
    cagr = (navs[-1] / navs[0]) ** (1 / years) - 1 if navs[-1] > 0 else -1.0
    peak, mdd = navs[0], 0.0
    for v in navs:
        peak = max(peak, v)
        mdd = max(mdd, (peak - v) / peak if peak else 0.0)
    rets = [b / a - 1 for a, b in zip(navs, navs[1:]) if a > 0]
    sharpe = None
    if len(rets) > 2:
        mean = sum(rets) / len(rets)
        sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1))
        sharpe = mean / sd * math.sqrt(252) if sd > 0 else None
    return {"cagr": round(cagr * 100, 1), "mdd": round(-mdd * 100, 1), "sharpe": round(sharpe, 2) if sharpe is not None else None,
            "ret": round(ret * 100, 1), "days": len(history), "first": history[0]["date"], "last": history[-1]["date"]}


def index_metrics(benchmark: dict[str, float], start: str, end: str | None) -> dict:
    """VNINDEX cùng kỳ: gốc là giá đóng phiên trước ngày đầu (chiến lược cầm tiền mặt lúc bắt đầu)."""
    days = sorted(benchmark)
    i = bisect.bisect_left(days, start)
    j = bisect.bisect_right(days, end) if end else len(days)
    if i >= j:
        return metrics([])
    base = benchmark[days[i - 1]] if i > 0 else benchmark[days[i]]
    hist = [{"date": d, "nav": START_CASH * benchmark[d] / base} for d in days[i:j]]
    return metrics(hist)


# ─── chạy ───────────────────────────────────────────────────────────────────

def run_defensive(data, universe: Universe, params: Params, start: str, end: str | None) -> Ledger:
    return factor.run(data, params, start=start, end=end, start_cash=START_CASH,
                      universe=lambda day: universe.cap(day, params.fb_universe))


def _month_ends(sessions: list[str]) -> list[str]:
    return [d for d, n in zip(sessions, sessions[1:]) if n[:7] != d[:7]]


def run_momentum(data, universe: Universe, params: Params, start: str, end: str | None, size: int = 30) -> Ledger:
    """Vòng lặp của ``momentum.run`` (decide / plan của bot) trên rổ ``size`` mã vốn hoá lớn, dựng lại mỗi cuối tháng."""
    ledger = Ledger(cash=START_CASH, start_cash=START_CASH)
    days = [d for d in data.dates if d >= start and (end is None or d <= end)]
    vni_days = sorted(data.benchmark)
    vni_values = [data.benchmark[d] for d in vni_days]
    ends = _month_ends(vni_days)
    series = {s: (sorted(bars), bars) for s, bars in data.bars.items()}
    need = params.mom_lookback + 1

    def basket_for(day: str) -> list[str]:
        k = bisect.bisect_left(ends, day)                  # cuối tháng gần nhất TRƯỚC phiên này
        return universe.cap(ends[k - 1] if k else day, size)

    for day in days:
        active = {o.symbol for o in ledger.orders} | set(ledger.positions)
        bars = {s: data.bars[s][day] for s in active if day in data.bars.get(s, {})}
        ledger.process_session(bars, rules=momentum.RULES)
        ledger.end_of_session(day, data.benchmark.get(day))
        closes = {}
        for symbol in basket_for(day):
            got = series.get(symbol)
            if not got or day not in got[1]:
                continue
            dates, by_day = got
            i = bisect.bisect_right(dates, day)
            closes[symbol] = [by_day[d].close for d in dates[max(0, i - need):i]]
        vni = vni_values[: bisect.bisect_right(vni_days, day)][-max(params.mom_market_ma, 1):]
        decision = momentum.decide(ledger, closes, vni, params)
        last = {s: c[-1] for s, c in closes.items()}
        for s in ledger.positions:                         # mã đang giữ đã rời rổ: vẫn cần giá để bán
            if s not in last and day in data.bars.get(s, {}):
                last[s] = data.bars[s][day].close
        momentum.plan(ledger, day, decision, last, params)
    return ledger


def evaluate(data, pit: Universe, today: Universe, *, variants: bool = False, log=print) -> dict:
    as_of = data.dates[-1]
    rows: list[dict] = []
    books = [("Phòng thủ", "point-in-time", lambda s, e: run_defensive(data, pit, DEFAULT, s, e)),
             ("Phòng thủ", "danh sách HOSE hôm nay (thiên lệch)", lambda s, e: run_defensive(data, today, DEFAULT, s, e)),
             ("Động lượng", "VN30 giả lập (30 vốn hoá lớn) point-in-time", lambda s, e: run_momentum(data, pit, DEFAULT, s, e, 30)),
             ("Động lượng", "50 vốn hoá lớn point-in-time", lambda s, e: run_momentum(data, pit, DEFAULT, s, e, 50))]
    if variants:
        for name, change in VARIANTS:
            p = replace(DEFAULT, **change)
            books.append(("Phòng thủ", f"point-in-time, {name}", lambda s, e, p=p: run_defensive(data, pit, p, s, e)))
    for seg, start, end in SEGMENTS:
        rows.append({"book": "VNINDEX", "universe": "chỉ số giá", "segment": seg, **index_metrics(data.benchmark, start, end)})
    for book, uni, fn in books:
        for seg, start, end in SEGMENTS:
            t0 = time.time()
            ledger = fn(start, end)
            m = metrics(ledger.history)
            m.update(trades=sum(1 for f in ledger.fills if f.side == "BUY"),
                     fees=round(sum(float(f.cost or 0) for f in ledger.fills) / START_CASH * 100, 2))
            rows.append({"book": book, "universe": uni, "segment": seg, **m})
            log(f"{book} | {uni} | {seg}: CAGR {m['cagr']}% · DD {m['mdd']}% · Sharpe {m['sharpe']} "
                f"({m['trades']} lệnh mua, {time.time() - t0:.0f}s)")
    entered = sorted({c for d in _month_ends(data.dates) if "2013-01-01" <= d for c in pit.cap(d, DEFAULT.fb_universe)})
    gone = [c for c in entered if c not in set(today.last_members)]
    return {"as_of": as_of, "rows": rows, "universe_codes": len(entered), "left_hose": gone,
            "hose_codes": len({c for v in pit.members.values() for c in v}), "vci_missing": sorted(data.skipped)}


# ─── báo cáo ────────────────────────────────────────────────────────────────

def _fmt(v, suffix=""):
    return "—" if v is None else f"{v:.1f}{suffix}".replace(".", ",") if isinstance(v, float) else f"{v}{suffix}"


def markdown(res: dict) -> str:
    segs = [s for s, _, _ in SEGMENTS]
    out = [f"Dữ liệu tới **{res['as_of']}**. Mỗi ô: CAGR / sụt tối đa / Sharpe (sau phí, thuế, trượt giá).", "",
           "| Sổ | Rổ | " + " | ".join(segs) + " |", "|---|---|" + "---|" * len(segs)]
    keys: list[tuple] = []
    for r in res["rows"]:
        k = (r["book"], r["universe"])
        if k not in keys:
            keys.append(k)
    for k in keys:
        cells = []
        for seg in segs:
            r = next((x for x in res["rows"] if (x["book"], x["universe"]) == k and x["segment"] == seg), None)
            if not r or r.get("cagr") is None:
                cells.append("—")
                continue
            sh = "—" if r.get("sharpe") is None else f"{r['sharpe']:.2f}".replace(".", ",")
            cells.append(f"{_fmt(r['cagr'], '%')} / {_fmt(r['mdd'], '%')} / {sh}")
        out.append(f"| {k[0]} | {k[1]} | " + " | ".join(cells) + " |")
    first = {s: next((r for r in res["rows"] if r["segment"] == s and r.get("first")), {}) for s in segs}
    out += ["", "Giai đoạn: " + "; ".join(f"{s} {first[s].get('first', '—')} → {first[s].get('last', '—')}" for s in segs) + ".",
            f"Mã từng có mặt trên HOSE từ 2013: {res['hose_codes']}; VCI không có dữ liệu: {len(res['vci_missing'])}"
            + (f" ({', '.join(res['vci_missing'][:15])}{'…' if len(res['vci_missing']) > 15 else ''})" if res["vci_missing"] else "") + ".",
            f"Mã từng vào rổ Phòng thủ point-in-time (cuối mỗi tháng): {res['universe_codes']}, "
            f"trong đó {len(res['left_hose'])} mã nay không còn trên HOSE"
            + (f" ({', '.join(res['left_hose'])})" if res["left_hose"] else "") + "."]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Bộ kiểm định point-in-time (Phòng thủ, Động lượng).")
    ap.add_argument("--out", default="", help="thư mục dữ liệu (mặc định research/out)")
    ap.add_argument("--variants", action="store_true", help="thêm các phương án còn treo của Phòng thủ")
    ap.add_argument("--write", default="", help="ghi bảng markdown vào tệp này (ví dụ research/KET_QUA.md)")
    args = ap.parse_args(argv)
    base = Path(args.out) if args.out else fetch.OUT
    t0 = time.time()
    data, series, membership = dataset.load(base)
    pit, today = dataset.universes(data.dates, series, membership)
    print(f"Đã nạp {len(series)} mã, {len(data.dates)} phiên ({time.time() - t0:.0f}s)")
    res = evaluate(data, pit, today, variants=args.variants)
    md = markdown(res)
    print("\n" + md)
    (base / f"ket_qua_{res['as_of']}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    (base / f"ket_qua_{res['as_of']}.md").write_text(md + "\n", encoding="utf-8")
    if args.write:
        Path(args.write).write_text(md + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
