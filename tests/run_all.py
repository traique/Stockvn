"""Chạy bộ kiểm thử offline của bot (không cần pytest, không cần mạng, không đụng dữ liệu thật).

    python tests/run_all.py              # tất cả
    python tests/run_all.py stv web      # chỉ các bộ có tên chứa "stv" hoặc "web"
    python tests/run_all.py -j 3         # chạy 3 bộ cùng lúc (máy 2 GB: để mặc định 1)
    python tests/run_all.py -v           # in toàn bộ kết quả của từng bộ

Mỗi bộ chạy trong một tiến trình riêng: biến môi trường của bot (token, Zoom, TRADE_MODE...)
và file .env không được đọc, mọi kết nối mạng bị chặn, file tạm nằm trong một thư mục tạm
và bị xoá khi xong. Thoát với mã 0 khi tất cả đạt, 1 khi có bộ lỗi.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SUITES_DIR = Path(__file__).resolve().parent / "suites"
SUITES = sorted(SUITES_DIR.glob("suite_*.py"))
TIMEOUT_SECONDS = 900
SKIPPED = 77          # tests/suites/_common.SKIPPED: a test-only library is missing
_KEEP = ("PATH", "HOME", "LANG", "LANGUAGE", "TZ", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC",
         "PATHEXT", "WINDIR", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "VIRTUAL_ENV", "CONDA_PREFIX")


def _env() -> dict:
    """The child's environment: only what Python needs (the suite also cleans it itself)."""
    env = {k: v for k, v in os.environ.items() if k in _KEEP or k.startswith("LC_")}
    env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    return env


def run_suite(path: Path) -> tuple[int, str, float]:
    """(exit code, output, seconds) of one suite, run in its own process."""
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="vnbot-run-") as cwd:
        try:
            done = subprocess.run([sys.executable, str(path)], cwd=cwd, env=_env(), capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT_SECONDS)
            code, out = done.returncode, done.stdout + done.stderr
        except subprocess.TimeoutExpired as exc:
            code = 124
            out = f"{exc.stdout or ''}{exc.stderr or ''}\nHẾT GIỜ sau {TIMEOUT_SECONDS} giây"
    return code, out, time.monotonic() - started


def _failures(out: str) -> list[str]:
    return [line for line in out.splitlines() if line.startswith(("FAIL", "Traceback", "FAILED: [")) or "Error:" in line[:60]]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Bộ kiểm thử offline của TradingAgents VN.")
    ap.add_argument("names", nargs="*", help="chỉ chạy các bộ có tên chứa một trong các chuỗi này")
    ap.add_argument("-j", "--jobs", type=int, default=1, help="số bộ chạy cùng lúc (mặc định 1)")
    ap.add_argument("-v", "--verbose", action="store_true", help="in toàn bộ kết quả của từng bộ")
    args = ap.parse_args(argv)
    suites = [s for s in SUITES if not args.names or any(n in s.stem for n in args.names)]
    if not suites:
        print("Không có bộ kiểm thử nào khớp:", ", ".join(args.names))
        return 1
    print(f"Chạy {len(suites)} bộ kiểm thử (offline)...\n")
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        results = list(zip(suites, pool.map(run_suite, suites), strict=True))
    failed, skipped = [], []
    for path, (code, out, secs) in results:
        name = path.stem.removeprefix("suite_")
        summary = next((line for line in reversed(out.splitlines())
                        if "passed" in line or line.startswith(("FAILED:", "SKIPPED:"))), "")
        mark = "✅" if code == 0 else "⏭️ " if code == SKIPPED else "❌"
        print(f"{mark} {name:<22} {secs:6.1f}s  {summary}")
        if code == SKIPPED:
            skipped.append(name)
            continue
        if args.verbose:
            print(out)
        elif code != 0:
            lines = _failures(out) or out.splitlines()[-30:]
            print("   " + "\n   ".join(lines[:40]))
            print("   ... chi tiết: python tests/run_all.py -v " + name)
        if code != 0:
            failed.append(name)
    total = time.monotonic() - started
    ran = len(suites) - len(skipped)
    print(f"\n{ran - len(failed)}/{ran} bộ đạt trong {total:.0f}s"
          + (f" - LỖI: {', '.join(failed)}" if failed else " - tất cả đều đạt.")
          + (f"\n⚠️ Bỏ qua {len(skipped)} bộ vì thiếu thư viện kiểm thử ({', '.join(skipped)}): "
             "pip install -r requirements-dev.txt" if skipped else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
