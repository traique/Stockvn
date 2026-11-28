"""Tải dữ liệu cho bộ kiểm định point-in-time (chạy từ gốc repo, cần mạng; ghi vào ``research/out/``).

    python -m research.pit.fetch                    # tới phiên gần nhất
    python -m research.pit.fetch --as-of 2026-11-27 --workers 3

Ba nguồn công khai, không khoá (đều là nguồn bot đã dùng):

1. **VNINDEX** – DNSE, nến ngày từ ``START`` (06/2011: đủ 200 phiên cho MA200 và 252 phiên cho điểm
   xếp hạng trước 01/2013). Lịch phiên của bộ kiểm định là lịch phiên VNINDEX.
2. **Thành phần HOSE theo từng phiên** – VNDirect ``stock_prices?q=floor:HOSE~date:D~type:STOCK``: mọi mã
   có phiên D trên HOSE, theo sàn CỦA NGÀY ĐÓ (đã kiểm: 15/03/2013 có ITA, KSA, HVG, PVF – nay đã rời HOSE –
   và không có FLC, SHB – lúc đó ở HNX). VNDirect có dữ liệu từ 02/01/2013, đúng đầu giai đoạn kiểm định.
   Một tệp ``hose/YYYY-MM.json`` mỗi tháng: {ngày: [mã]}.
3. **Giá & lịch sử từng mã** – VCI ``price-history`` (nguồn ``history_of`` của Phòng thủ khi chạy thật):
   giá đã điều chỉnh (mở/cao/thấp/đóng), giá đóng chưa điều chỉnh, giá trị và khối lượng khớp lệnh, số cổ
   phiếu lưu hành, trần/sàn/tham chiếu. Chỉ tải các mã từng có mặt trên HOSE (mục 2). ``vci/MÃ.csv.gz``.

Chạy lại được giữa chừng: tháng / mã đã có thì bỏ qua (``--refresh`` tải lại). ``manifest.json`` ghi ngày dữ
liệu, số mã, các mã HOSE mà VCI không có dữ liệu (thường là mã sáp nhập 2012–2013, ví dụ PVF) – các mã đó
không vào được rổ (không có số cổ phiếu để xếp vốn hoá), như bot thật.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger("research.pit.fetch")

START = "2011-06-01"              # nến cho MA200 / 252 phiên trước giai đoạn kiểm định
MEMBERSHIP_START = "2013-01-01"   # VNDirect có dữ liệu từ 02/01/2013
OUT = Path(os.environ.get("PIT_OUT") or Path(__file__).resolve().parents[1] / "out")
VCI_PAGE = 1000                   # VCI trả tối đa 1000 phiên mỗi trang, không cắt theo năm
PAUSE = 0.3                       # giây giữa hai lần gọi của cùng một luồng (nguồn reset khi gọi dồn)
PRICE_COLUMNS = ("date", "ref", "ceiling", "floor", "close_raw", "open", "high", "low", "close", "volume", "value",
                 "shares")


def out_dir(base: Path | None = None) -> Path:
    path = Path(base or OUT)
    (path / "hose").mkdir(parents=True, exist_ok=True)
    (path / "vci").mkdir(parents=True, exist_ok=True)
    return path


# ─── VNINDEX ────────────────────────────────────────────────────────────────

def fetch_vnindex(base: Path, as_of: str) -> list[tuple[str, float]]:
    from tradingagents.dataflows.vendors import dnse
    from tradingagents.market import vn_rules

    frame = dnse.fetch_ohlcv(vn_rules.BENCHMARK, START, as_of)
    rows = [(str(d)[:10], float(c)) for d, c in zip(frame["Date"], frame["Close"], strict=True) if c and c > 0]
    with open(base / "vnindex.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(("date", "close"))
        w.writerows(rows)
    return rows


def read_vnindex(base: Path) -> list[tuple[str, float]]:
    with open(base / "vnindex.csv", encoding="utf-8") as fh:
        return [(r["date"], float(r["close"])) for r in csv.DictReader(fh)]


# ─── thành phần HOSE theo phiên ─────────────────────────────────────────────

def _hose_day(day: str) -> list[str]:
    from tradingagents.dataflows.vendors import flows

    frame = flows.fetch_hose_day(day)
    if frame is None or frame.empty or "Code" not in frame:
        return []
    return sorted({str(c).upper() for c in frame["Code"] if c})


def fetch_month(base: Path, month: str, days: list[str], refresh: bool = False) -> dict[str, list[str]]:
    """{phiên: [mã HOSE]} của một tháng; giữ tệp tháng đã đủ (mọi phiên có danh sách)."""
    path = base / "hose" / f"{month}.json"
    have: dict[str, list[str]] = {}
    if path.exists() and not refresh:
        try:
            have = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            have = {}
    for day in days:
        if have.get(day):
            continue
        codes: list[str] = []
        for attempt in range(3):
            try:
                codes = _hose_day(day)
                if codes:
                    break
            except Exception as exc:  # noqa: BLE001 - thử lại, rồi ghi rỗng (universe giữ phiên trước)
                logger.warning("HOSE %s lần %d: %s", day, attempt + 1, exc)
            time.sleep(PAUSE * (attempt + 2))
        have[day] = codes
        time.sleep(PAUSE)
    path.write_text(json.dumps(dict(sorted(have.items())), ensure_ascii=False), encoding="utf-8")
    return have


def read_membership(base: Path) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for path in sorted((base / "hose").glob("*.json")):
        out.update(json.loads(path.read_text(encoding="utf-8")))
    return dict(sorted(out.items()))


# ─── giá VCI từng mã ────────────────────────────────────────────────────────

def _num(value) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ""
    return "" if v != v else repr(v)


def fetch_prices(base: Path, code: str, as_of: str) -> int:
    """Mọi phiên của ``code`` từ START tới ``as_of`` vào ``vci/CODE.csv.gz``; trả số phiên (0 = VCI không có)."""
    from tradingagents.dataflows.vendors import flows, http

    rows: list[dict] = []
    page = 0
    while True:
        body = http.get_json(
            f"{flows.VCI_COMPANY}/{code}/price-history", vendor="VCI", headers=flows.VCI_HEADERS, timeout=60,
            params={"timeFrame": "ONE_DAY", "page": page, "size": VCI_PAGE,
                    "fromDate": START.replace("-", ""), "toDate": as_of.replace("-", "")},
        )
        content = ((body or {}).get("data") or {}).get("content") or []
        rows.extend(content)
        time.sleep(PAUSE)
        if len(content) < VCI_PAGE:
            break
        page += 1
    seen: dict[str, list[str]] = {}
    for r in rows:
        day = str(r.get("tradingDate") or "")[:10]
        if not day:
            continue
        seen[day] = [day, _num(r.get("referencePrice")), _num(r.get("ceilingPrice")), _num(r.get("floorPrice")),
                     _num(r.get("closePrice")), _num(r.get("openPriceAdjusted")), _num(r.get("highestPriceAdjusted")),
                     _num(r.get("lowestPriceAdjusted")), _num(r.get("closePriceAdjusted")),
                     _num(r.get("totalMatchVolume")), _num(r.get("totalMatchValue")), _num(r.get("totalShares"))]
    path = base / "vci" / f"{code}.csv.gz"
    tmp = path.with_suffix(".tmp")
    with gzip.open(tmp, "wt", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(PRICE_COLUMNS)
        w.writerows(seen[d] for d in sorted(seen))
    tmp.replace(path)
    return len(seen)


def read_prices(base: Path, code: str) -> list[dict]:
    """Các phiên của một mã (cũ → mới), số thực (NaN khi trống)."""
    path = base / "vci" / f"{code}.csv.gz"
    if not path.exists():
        return []
    out = []
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            row = {"date": r["date"]}
            for k in PRICE_COLUMNS[1:]:
                row[k] = float(r[k]) if r[k] else float("nan")
            out.append(row)
    return out


# ─── chạy ───────────────────────────────────────────────────────────────────

def _months(sessions: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for d in sessions:
        if d >= MEMBERSHIP_START:
            out.setdefault(d[:7], []).append(d)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Tải dữ liệu cho bộ kiểm định point-in-time.")
    ap.add_argument("--as-of", default="", help="ngày cuối (YYYY-MM-DD); trống = phiên VNINDEX gần nhất")
    ap.add_argument("--workers", type=int, default=3, help="số luồng tải (mặc định 3; đừng tăng nhiều)")
    ap.add_argument("--refresh", action="store_true", help="tải lại cả tháng / mã đã có")
    ap.add_argument("--out", default="", help="thư mục dữ liệu (mặc định research/out)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    base = out_dir(Path(args.out) if args.out else None)
    as_of = args.as_of or date.today().isoformat()

    vni = fetch_vnindex(base, as_of)
    as_of = vni[-1][0]
    sessions = [d for d, _ in vni]
    logger.info("VNINDEX: %d phiên %s → %s", len(sessions), sessions[0], as_of)

    months = _months(sessions)
    done_months = set()
    lock = threading.Lock()
    current = max(months)                                   # tháng đang chạy luôn tải lại (có phiên mới)
    with ThreadPoolExecutor(max(1, min(args.workers, 2))) as pool:
        futures = {pool.submit(fetch_month, base, m, days, args.refresh or m == current): m for m, days in months.items()}
        for i, fut in enumerate(as_completed(futures), 1):
            m = futures[fut]
            got = fut.result()
            with lock:
                done_months.add(m)
            empty = [d for d, c in got.items() if not c]
            if i % 12 == 0 or empty:
                logger.info("HOSE %s: %d/%d tháng%s", m, i, len(months), f" (phiên trống: {', '.join(empty)})" if empty else "")
    membership = read_membership(base)
    codes = sorted({c for v in membership.values() for c in v})
    logger.info("Thành phần HOSE: %d phiên, %d mã từng có mặt", len(membership), len(codes))

    index_path = base / "vci" / "_index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
    todo = [c for c in codes if args.refresh or index.get(c, {}).get("as_of") != as_of]
    logger.info("VCI price-history: %d mã cần tải (%d đã có)", len(todo), len(codes) - len(todo))
    with ThreadPoolExecutor(max(1, args.workers)) as pool:
        futures = {pool.submit(fetch_prices, base, c, as_of): c for c in todo}
        for i, fut in enumerate(as_completed(futures), 1):
            c = futures[fut]
            try:
                n = fut.result()
                index[c] = {"as_of": as_of, "rows": n}
            except Exception as exc:  # noqa: BLE001 - mã này chạy lại lần sau
                logger.warning("VCI %s: %s", c, exc)
                continue
            if i % 25 == 0:
                index_path.write_text(json.dumps(index, sort_keys=True), encoding="utf-8")
                logger.info("VCI: %d/%d mã", i, len(todo))
    index_path.write_text(json.dumps(index, sort_keys=True), encoding="utf-8")
    missing = sorted(c for c in codes if index.get(c, {}).get("rows", 0) == 0)
    failed = sorted(c for c in codes if c not in index)
    manifest = {"as_of": as_of, "fetched_at": datetime.now().isoformat(timespec="seconds"), "start": START,
                "sessions": len(sessions), "membership_sessions": len(membership), "hose_codes": len(codes),
                "vci_missing": missing, "vci_failed": failed,
                "empty_membership_days": [d for d, c in membership.items() if not c]}
    (base / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    logger.info("Xong: %d mã HOSE, VCI không có dữ liệu %d (%s), lỗi %d", len(codes), len(missing),
                ", ".join(missing[:20]), len(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
