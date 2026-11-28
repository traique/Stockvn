"""Bộ kiểm định point-in-time (research/pit), offline trên dữ liệu giả:

- sàn theo biên độ trần, đổi mốc 15/01/2013 (HOSE 5%→7%, HNX 7%→10%);
- ảnh chụp từng mã tính dạng mảng = ``factor.snapshot`` của bot (khoảng trống, giá trống, tạm ngừng, cửa sổ 60 phiên);
- thành phần HOSE theo phiên: mã đã rời HOSE có mặt trước ngày rời, không có sau; mã lên HOSE chỉ có từ ngày lên;
  phiên VNDirect trống dùng phiên trước ≤ 5 phiên; trước 2013 theo biên độ; rổ "hôm nay" là danh sách phiên cuối;
- không nhìn trước: đổi dữ liệu sau phiên D không đổi rổ của D;
- chỉ số CAGR / sụt tối đa / Sharpe, VNINDEX lấy gốc phiên trước;
- trọn vòng: tệp theo định dạng của fetch.py → dataset → factor.run và sổ Động lượng có lệnh, báo cáo markdown.
"""
import csv
import gzip
import json
import math
import random
from datetime import date, timedelta
from pathlib import Path

import numpy as np

import _common  # noqa: F401 - clean env, no .env, offline, temp folder, repo on sys.path

from research.pit import dataset, evaluate, fetch
from research.pit import universe as U
from tradingagents.quant.params import DEFAULT
from tradingagents.trading import factor

t = _common.Checks()

# ── sàn theo biên độ ──────────────────────────────────────────────────────────
t.ok("trước 15/01/2013: 5% là HOSE, 7% là HNX, 10% là UPCoM",
     U.band_exchange("2012-06-01", 10000, 10500) == "HOSE" and U.band_exchange("2012-06-01", 10000, 10700) == "HNX"
     and U.band_exchange("2012-06-01", 10000, 11000) == "UPCOM")
t.ok("từ 15/01/2013: 7% là HOSE, 10% là HNX, 15% là UPCoM; bước giá làm tròn xuống vẫn đúng",
     U.band_exchange("2013-01-15", 97200, 104000) == "HOSE" and U.band_exchange("2020-01-02", 10000, 11000) == "HNX"
     and U.band_exchange("2020-01-02", 10000, 11500) == "UPCOM" and U.band_exchange("2020-01-02", 1530, 1630) == "HOSE")
t.ok("biên độ lạ (ngày đầu niêm yết 20%, dữ liệu trống) → không rõ",
     U.band_exchange("2020-01-02", 10000, 12000) is None and U.band_exchange("2020-01-02", None, 1) is None
     and U.band_exchange("2020-01-02", 0, 1) is None)

# ── ảnh chụp dạng mảng = factor.snapshot ──────────────────────────────────────
rng = random.Random(11)
N = 140
sessions = [(date(2020, 1, 1) + timedelta(days=i)).isoformat() for i in range(N)]


def fake_rows(seed, start=0, gap_every=0, nan_every=0, suspend=()):
    r = random.Random(seed)
    rows, px = [], 20000.0
    for i in range(start, N):
        if gap_every and i % gap_every == 0:
            continue                                          # không có dòng VCI
        px *= 1 + r.uniform(-0.03, 0.03)
        vol = 0.0 if i in suspend else r.uniform(1e4, 1e6)
        rows.append({"date": sessions[i], "ref": px, "ceiling": px * 1.069, "floor": px * 0.93, "close_raw": px * 1.3,
                     "open": px, "high": px * 1.01, "low": px * 0.99, "close": px, "volume": vol,
                     "value": (math.nan if nan_every and i % nan_every == 0 else px * vol * 1.3),
                     "shares": (math.nan if nan_every and i % (nan_every + 1) == 0 else 1e8)})
    return rows


def factor_inputs(rows, upto):
    bars, hist = {}, {}
    for r in rows:
        if r["date"] > sessions[upto]:
            continue
        hist[r["date"]] = (r["value"], r["close_raw"], r["shares"])
        if r["close"] > 0 and r["volume"] > 0:
            bars[r["date"]] = (r["close"], r["volume"])
    return bars, hist


same = True
cases = [fake_rows(1), fake_rows(2, start=50), fake_rows(3, gap_every=7), fake_rows(4, nan_every=5),
         fake_rows(5, suspend=set(range(60, 75)) | {120, 121, 122, 123, 124, 125, 126})]
for k, rows in enumerate(cases):
    s = U.build_series(f"S{k}", rows, sessions)
    for tt in range(0, N, 3):
        bars, hist = factor_inputs(rows, tt)
        want = factor.snapshot(f"S{k}", sessions[: tt + 1], bars, hist, hose=True)
        got = U.snapshot(s, tt, hose=True)
        for f in ("bars", "raw_close", "recent", "median_value", "market_cap", "has_bar", "eligible"):
            a, b = getattr(want, f), getattr(got, f)
            if not ((a is None and b is None) or (a is not None and b is not None and math.isclose(float(a), float(b), rel_tol=1e-12))):
                same = False
                print("  khác:", k, tt, f, a, b)
t.ok("ảnh chụp dạng mảng = factor.snapshot của bot (5 mã giả: niêm yết muộn, thiếu dòng, giá trị/số cp trống, tạm ngừng)", same)


# ── thành phần HOSE point-in-time ─────────────────────────────────────────────
def flat_series(code, n, *, cap=1e12, value=1e10, start=0, end=None, band=None):
    end = n if end is None else end
    bar = np.array([start <= i < end for i in range(n)])
    arr = lambda v: np.where(bar, v, np.nan)  # noqa: E731
    return U.Series(code, bar, arr(10000.0), arr(1e6), bar.copy(), arr(value), arr(10000.0), arr(cap / 10000.0),
                    [band if start <= i < end else None for i in range(n)])


n = 400
days = [(date(2012, 1, 2) + timedelta(days=i)).isoformat() for i in range(n)]
first_member = days[300]
series = {"OLD": flat_series("OLD", n, cap=5e12, band="HOSE"), "NEW": flat_series("NEW", n, cap=9e12, band="HNX"),
          "MID": flat_series("MID", n, cap=1e12, band="HOSE")}
membership = {}
for i in range(300, n):
    codes = ["MID"] + (["OLD"] if i < 350 else []) + (["NEW"] if i >= 360 else [])
    membership[days[i]] = codes
membership[days[370]] = []                                     # phiên VNDirect trống
uni = U.Universe(days, series, membership)
today = U.Universe(days, series, membership, today=True)
t.ok("mã đã rời HOSE có trong rổ trước ngày rời, không có sau",
     "OLD" in uni.cap(days[340], 50) and "OLD" not in uni.cap(days[355], 50))
t.ok("mã lên HOSE chỉ có từ ngày lên; xếp theo vốn hoá lớn trước",
     "NEW" not in uni.cap(days[355], 50) and uni.cap(days[365], 50)[0] == "NEW")
t.ok("phiên VNDirect trống dùng danh sách phiên trước (≤ 5 phiên)", uni.hose(days[370]) == membership[days[369]])
gap_members = dict(membership)
for i in range(375, 385):
    gap_members[days[i]] = []
t.ok("trống quá 5 phiên → không đoán", U.Universe(days, series, gap_members).hose(days[383]) == [])
t.ok("trước ngày đầu của VNDirect: theo biên độ (HOSE), mã HNX không vào",
     uni.hose(days[290]) == ["MID", "OLD"])
t.ok("rổ 'hôm nay' = danh sách phiên cuối cho mọi phiên (có NEW từ đầu, không có OLD)",
     today.hose(days[305]) == sorted(membership[days[-1]]) and "OLD" not in today.cap(days[340], 50))

# không nhìn trước: dữ liệu sau D đổi → rổ của D không đổi
before = uni.cap(days[340], 2)
series2 = dict(series)
s_mid = flat_series("MID", n, cap=1e12, band="HOSE")
s_mid.shares[341:] = 1e15                                      # sau D: MID thành lớn nhất
s_mid = U.Series(*[getattr(s_mid, f) for f in ("code", "bar", "close", "volume", "hist", "matched", "raw", "shares", "band_ex")])
series2["MID"] = s_mid
t.ok("không nhìn trước: dữ liệu sau phiên D đổi không làm đổi rổ của D",
     U.Universe(days, series2, membership).cap(days[340], 2) == before and
     U.Universe(days, series2, membership).cap(days[345], 2)[0] == "MID")

# ── chỉ số ───────────────────────────────────────────────────────────────────
hist = [{"date": "2020-01-01", "nav": 1.0e9}, {"date": "2020-07-01", "nav": 0.8e9}, {"date": "2021-01-01", "nav": 2.0e9}]
m = evaluate.metrics(hist)
t.ok("CAGR theo ngày lịch, sụt tối đa trên NAV, lợi nhuận", m["ret"] == 100.0 and abs(m["cagr"] - 99.8) < 0.5
     and m["mdd"] == -20.0, m)
idx = evaluate.index_metrics({"2019-12-31": 100.0, "2020-01-02": 110.0, "2020-12-31": 120.0}, "2020-01-01", None)
t.ok("VNINDEX: gốc là phiên trước ngày đầu", idx["ret"] == 20.0, idx)
t.ok("lịch sử rỗng → không có chỉ số", evaluate.metrics([])["cagr"] is None)

# ── trọn vòng trên tệp theo định dạng fetch.py ───────────────────────────────
base = Path(_common.ROOT) / "pit_out"
fetch.out_dir(base)
cal, d0 = [], date(2011, 6, 1)
while len(cal) < 1000:
    if d0.weekday() < 5:
        cal.append(d0.isoformat())
    d0 += timedelta(days=1)
r = random.Random(3)
vni, px = [], 500.0
for i, d in enumerate(cal):
    px *= 1 + r.gauss(0.0006, 0.01)
    vni.append((d, px))
with open(base / "vnindex.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh)
    w.writerow(("date", "close"))
    w.writerows(vni)
codes = [f"A{i:02d}" for i in range(16)]
months: dict = {}
for i, d in enumerate(cal):
    if d < fetch.MEMBERSHIP_START:
        continue
    members = [c for k, c in enumerate(codes) if not (k == 3 and d >= "2013-09-01")]   # A03 rời HOSE 09/2013
    months.setdefault(d[:7], {})[d] = members
for mth, v in months.items():
    (base / "hose" / f"{mth}.json").write_text(json.dumps(v), encoding="utf-8")
for k, c in enumerate(codes):
    rr, p = random.Random(100 + k), 10000.0 + 1000 * k
    drift = 0.0004 + 0.0002 * (k % 5)
    with gzip.open(base / "vci" / f"{c}.csv.gz", "wt", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(fetch.PRICE_COLUMNS)
        for d in cal:
            if k == 3 and d >= "2013-12-01":
                break                                          # huỷ niêm yết hẳn
            p *= 1 + rr.gauss(drift, 0.012 + 0.002 * (k % 4))
            band = 1.069 if d >= U.BAND_CUTOVER else 1.049
            vol = rr.uniform(1e5, 1e6)
            w.writerow([d, p, p * band, p / band, p, p * 0.995, p * 1.01, p * 0.99, p, vol, p * vol, 1e8 * (1 + k)])
rows_got = fetch.read_prices(base, "A01")
t.ok("fetch.read_prices đọc lại đúng định dạng đã ghi", len(rows_got) == len(cal) and rows_got[0]["date"] == cal[0]
     and math.isfinite(rows_got[5]["shares"]))
data, ser, mem = dataset.load(base)
pit, tod = dataset.universes(data.dates, ser, mem)
t.ok("dataset: nến khớp lệnh có prev_close, sàn HOSE theo danh sách của phiên, UPCoM/không rõ khi rời",
     data.bars["A01"][cal[300]].prev_close == data.bars["A01"][cal[299]].close
     and data.bars["A01"][cal[700]].exchange == "HOSE" and data.bars["A03"].get(cal[700]) is None)
led = evaluate.run_defensive(data, pit, DEFAULT, "2013-01-01", None)
buys = [f for f in led.fills if f.side == "BUY"]
t.ok("Phòng thủ chạy trên rổ point-in-time và có lệnh mua; không mua mã đã rời HOSE sau ngày rời",
     len(buys) > 0 and not any(f.symbol == "A03" and f.date >= "2013-09-02" for f in buys), len(buys))
lm = evaluate.run_momentum(data, pit, DEFAULT, "2013-01-01", None, size=10)
t.ok("Động lượng (decide/plan của bot) chạy trên rổ 10 vốn hoá lớn, có lệnh", any(f.side == "BUY" for f in lm.fills))
real_segments = evaluate.SEGMENTS
evaluate.SEGMENTS = (("A", "2013-01-01", "2013-12-31"), ("B", "2014-01-01", None))
try:
    res = evaluate.evaluate(data, pit, tod, log=lambda *a: None)
finally:
    evaluate.SEGMENTS = real_segments
md = evaluate.markdown(res)
t.ok("báo cáo: VNINDEX + 4 sổ × 2 giai đoạn, bảng markdown có số", len(res["rows"]) == 2 + 4 * 2
     and "| Phòng thủ | point-in-time |" in md and "VNINDEX" in md and "A03" in md, md[:400])

t.done()
