"""Tạo gói upload cho Botkeep (một server, một file zip) và kiểm tra nó trước khi upload.

    python scripts/build_botkeep_bundle.py                # dist/botkeep/, dist/stockvn-botkeep-<bản>.zip, dist/botkeep.json
    python scripts/build_botkeep_bundle.py --check        # chỉ kiểm tra, không ghi gì
    python scripts/build_botkeep_bundle.py --dir-entries  # zip có cả mục thư mục (khi Botkeep giải nén thiếu thư mục lồng)

Gói chỉ gồm danh sách cho phép (allowed()): main_botkeep.py, requirements.txt, .env.example,
README.md, LICENSE, mọi .py của tradingagents/ và vnbot/, 3 file ảnh; khi đã có altcoin/bot/ thì thêm
altcoin/bot/**/*.py, altcoin/run.py, altcoin/hosted.py, altcoin/requirements.txt. Danh sách file lấy
từ git (file đã theo dõi và file mới không bị .gitignore), không có git thì duyệt thư mục.

Thoát 1, kèm danh sách vi phạm, và không ghi gì khi: gói có file bị cấm (.env, *.db, data/, tests/,
__pycache__/...), thiếu file (kể cả .py git theo dõi mà đã mất trên đĩa), vượt giới hạn upload của
Botkeep (250 file, tính cả thư mục; 4.000.000 B giải nén và zip), có .py không biên dịch được, mã bản
dựng của bản dàn dựng khác RELEASE_BUILD (quên --stamp), hoặc file được đóng gói có thay đổi chưa commit
(--allow-dirty để bỏ qua kiểm tra này). Chạy bằng Python mới hơn Botkeep (3.13) cũng bị từ chối
(--allow-python để bỏ qua): compile() của bản mới nhận cú pháp mà 3.13 báo lỗi.

Mọi mục của zip mang một thời điểm: của commit mới nhất (cùng commit, cùng zip), hoặc mtime mới nhất
của file chưa commit / khi không có git. Không dùng hằng số: Botkeep giữ __pycache__/ qua các lần
upload và Python chỉ so mtime + kích thước của file nguồn, nên sửa không đổi kích thước mà giữ thời
điểm cũ thì server vẫn chạy bytecode cũ.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from fnmatch import fnmatchcase
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_FILES = 250                   # Botkeep: files per upload (files + folders kept under it too)
MAX_BYTES = 4_000_000             # Botkeep: upload size, unpacked and zipped
WARN_SHARE = 0.85                 # warn above this share of any limit
MIN_PYTHON = (3, 12)              # vnbot uses PEP 701 f-strings: a SyntaxError on 3.11
BOTKEEP_PYTHON = (3, 13)          # a newer compile() accepts syntax Botkeep rejects (PEP 758, PEP 750)
ZIP_EPOCH = 315_532_800           # 1980-01-01 UTC, the earliest time a zip entry can hold

FILES = ("main_botkeep.py", "requirements.txt", ".env.example", "README.md", "LICENSE",
         "tradingagents/assets/tauric-logo.svg",       # read by report_html.py on import
         "vnbot/static/favicon.png", "vnbot/static/logo.png",
         "altcoin/run.py", "altcoin/hosted.py", "altcoin/requirements.txt")
PY_TREES = ("tradingagents/", "vnbot/", "altcoin/bot/")     # every .py under these
REQUIRED = ("main_botkeep.py", "requirements.txt", "tradingagents/assets/tauric-logo.svg",
            "vnbot/static/favicon.png", "vnbot/static/logo.png")
ALT_REQUIRED = ("altcoin/run.py",)  # hashed with bot/ by bot.version: the altcoin build needs it
FORBIDDEN_NAMES = (".env", "*.env", "*.db*", "*.pyc", ".git*", "render.yaml")
FORBIDDEN_DIRS = ("__pycache__", "data", "tests", "docs", "dist", ".git*")
FORBIDDEN_PATHS = ("altcoin/test_*", "altcoin/tools/*", "altcoin/dist/*")

# Run in the staged copy: (VERSION, build(), RELEASE_BUILD) of one version module.
_PROBE = ("import json, sys; sys.path.insert(0, sys.argv[1]); import {} as v; "
          "print(json.dumps([v.VERSION, v.build(), v.RELEASE_BUILD]))")


def allowed(path: str) -> bool:
    """In the bundle: the allow list, nothing else."""
    return path in FILES or (path.endswith(".py") and path.startswith(PY_TREES))


def forbidden(path: str) -> bool:
    """Never in the bundle: secrets, databases, data, tests, docs, caches, git, Render, altcoin tools."""
    path = path.lower()
    parts = path.split("/")
    return (any(fnmatchcase(parts[-1], p) for p in FORBIDDEN_NAMES)
            or any(fnmatchcase(d, p) for d in parts[:-1] for p in FORBIDDEN_DIRS)
            or any(fnmatchcase(path, p) for p in FORBIDDEN_PATHS))


def folders(paths) -> set[str]:
    """Every folder the files need (``a/b/c.py`` -> ``a``, ``a/b``)."""
    return {"/".join(p.split("/")[:i]) for p in paths for i in range(1, p.count("/") + 1)}


def _git(root: Path, *args: str) -> list[str]:
    done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True, timeout=60)
    return [p for p in done.stdout.decode("utf-8", "surrogateescape").split("\0") if p]


def source_files(root: Path) -> tuple[list[str], bool]:
    """The tree's files (relative, posix) and whether git listed them: git's tracked (even when
    deleted on disk, so a lost file is reported) and new not-ignored files; without git, a walk
    that skips forbidden folders and files."""
    if (root / ".git").exists():
        try:
            names = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
            return sorted(set(names)), True
        except (OSError, subprocess.SubprocessError):
            print("⚠️  Không chạy được git: duyệt thư mục thay cho danh sách của git.")
    found = []
    for folder, dirs, names in os.walk(root):
        rel = Path(folder).relative_to(root).as_posix()
        prefix = "" if rel == "." else rel + "/"
        dirs[:] = [d for d in dirs if not forbidden(f"{prefix}{d}/-")]
        found += [prefix + n for n in names if not forbidden(prefix + n)]
    return sorted(found), False


def uncommitted(root: Path) -> list[str]:
    """Bundled paths that git sees changed, new or deleted."""
    entries, paths = iter(_git(root, "status", "--porcelain", "-z", "--untracked-files=all")), []
    for entry in entries:
        paths.append(entry[3:])
        if "R" in entry[:2] or "C" in entry[:2]:
            paths.append(next(entries, ""))    # the old name of a rename or copy
    return sorted(p for p in paths if allowed(p))


def zip_time(root: Path, paths: list[str], from_git: bool, dirty: list[str]) -> tuple[int, ...]:
    """The one time of every zip entry (UTC, even seconds as a zip stores them): the newest
    commit's, or newer mtime of a file not committed; without git or a commit, the newest mtime."""
    newest = 0
    if from_git:
        try:
            newest = int(_git(root, "log", "-1", "--format=%ct")[0])
        except (OSError, subprocess.SubprocessError, IndexError, ValueError):
            pass
    for p in (dirty if newest else paths):
        if (root / p).is_file():
            newest = max(newest, int((root / p).stat().st_mtime))
    when = time.gmtime(max(newest, ZIP_EPOCH))
    return (*when[:5], when[5] - when[5] % 2)


def fingerprint(base: Path, module: str) -> tuple[str, str, str]:
    """(VERSION, build(), RELEASE_BUILD) of the staged copy, in a clean interpreter
    (-I: no cwd, no PYTHON* variables, no user site; -B: no __pycache__ in the stage)."""
    done = subprocess.run([sys.executable, "-I", "-B", "-c", _PROBE.format(module), str(base)], cwd=base,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                          env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    if done.returncode != 0:
        raise RuntimeError((done.stderr.strip().splitlines() or ["?"])[-1])
    version, build, release = json.loads(done.stdout.strip().splitlines()[-1])
    return version, build, release


def make_zip(stage: Path, paths: list[str], when: tuple[int, ...], dir_entries: bool = False) -> bytes:
    """A flat zip that is the same every time: sorted names, one time (``when``), fixed modes,
    deflate 9; no folder entries unless asked (each entry counts against Botkeep's 250)."""
    names = sorted(paths + ([d + "/" for d in folders(paths)] if dir_entries else []))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names:
            info = zipfile.ZipInfo(name, when)
            info.create_system = 3                     # unix modes, whatever builds it
            if name.endswith("/"):
                info.external_attr = (0o40755 << 16) | 0x10
                archive.writestr(info, b"")
            else:
                info.external_attr = 0o100644 << 16
                archive.writestr(info, (stage / name).read_bytes(), zipfile.ZIP_DEFLATED, 9)
    return buffer.getvalue()


def _n(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def check(sizes: dict[str, int], zip_bytes: int = 0, max_files: int = MAX_FILES,
          max_bytes: int = MAX_BYTES) -> tuple[list[str], list[str]]:
    """(errors, warnings) of a bundle ``{path: bytes}``: forbidden files and Botkeep's limits."""
    errors = [f"{p}: file bị cấm" for p in sorted(sizes) if forbidden(p)]
    files, total = len(sizes), sum(sizes.values())
    warnings = []
    for what, value, limit in (("file", files, max_files), ("file + thư mục", files + len(folders(sizes)), max_files),
                               ("B giải nén", total, max_bytes), ("B zip", zip_bytes, max_bytes)):
        if value > limit:
            errors.append(f"{_n(value)} {what} > giới hạn {_n(limit)}")
        elif value > WARN_SHARE * limit:
            warnings.append(f"{_n(value)} {what} = {value / limit:.0%} giới hạn {_n(limit)}")
    return errors, warnings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Tạo và kiểm tra gói upload cho Botkeep.")
    ap.add_argument("--check", action="store_true", help="chỉ kiểm tra, không ghi gì")
    ap.add_argument("--out", type=Path, default=ROOT / "dist", help="thư mục ghi kết quả (mặc định dist/)")
    ap.add_argument("--max-files", type=int, default=MAX_FILES, help=f"số file tối đa, tính cả thư mục ({MAX_FILES})")
    ap.add_argument("--max-bytes", type=int, default=MAX_BYTES, help=f"số byte tối đa, giải nén và zip ({MAX_BYTES})")
    ap.add_argument("--dir-entries", action="store_true", help="ghi cả mục thư mục vào zip")
    ap.add_argument("--allow-dirty", action="store_true", help="cho đóng gói file có thay đổi chưa commit")
    ap.add_argument("--allow-python", action="store_true", help="cho chạy bằng Python mới hơn Botkeep (3.13)")
    args = ap.parse_args(argv)
    running, botkeep = platform.python_version(), "{}.{}".format(*BOTKEEP_PYTHON)
    if sys.version_info < MIN_PYTHON:
        print(f"❌ Cần Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} trở lên (đang chạy {running}; "
              f"Botkeep chạy {botkeep}): bản cũ hơn báo lỗi cú pháp ở code của bot.")
        return 1
    if sys.version_info[:2] > BOTKEEP_PYTHON:
        newer = (f"Python {running} mới hơn Botkeep ({botkeep}): kiểm tra biên dịch không thấy cú pháp "
                 "chỉ bản mới có, gói qua kiểm tra vẫn có thể lỗi trên Botkeep")
        if not args.allow_python:
            print(f"❌ {newer}. Chạy bằng Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} hoặc {botkeep} "
                  "(hoặc thêm --allow-python).")
            return 1
        print(f"⚠️  {newer}.")

    sources, from_git = source_files(ROOT)
    paths = [p for p in sources if allowed(p) and (ROOT / p).is_file()]
    alt = any(p.startswith("altcoin/bot/") and p.endswith(".py") for p in sources)
    dirty = uncommitted(ROOT) if from_git else []
    errors = []
    if not args.allow_dirty:
        errors += [f"{p}: có thay đổi chưa commit (commit trước, hoặc thêm --allow-dirty)" for p in dirty]
    needed = [*REQUIRED, *(ALT_REQUIRED if alt else ()),
              *(p for p in sources if p.endswith(".py") and p.startswith(PY_TREES))]
    bundled = set(paths)
    errors += [f"{p}: thiếu trong gói" for p in needed if p not in bundled]

    with tempfile.TemporaryDirectory(prefix="botkeep-stage-") as tmp:
        stage = Path(tmp) / "botkeep"
        for p in paths:
            (stage / p).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / p, stage / p)
        for p in paths:
            if p.endswith(".py"):
                try:
                    compile((stage / p).read_bytes(), p, "exec", dont_inherit=True)
                except (SyntaxError, ValueError) as exc:
                    errors.append(f"{p}: không biên dịch được ({exc})")

        builds = {"stock": None, "altcoin": None}
        probes = [("stock", stage, "vnbot.version", "python -m vnbot.version --stamp")]
        if alt:
            probes.append(("altcoin", stage / "altcoin", "bot.version", "cd altcoin && python -m bot.version --stamp"))
        for key, base, module, stamp in probes:
            try:
                version, build, release = fingerprint(base, module)
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
                errors.append(f"{module}: không tính được bản dựng ({exc})")
                continue
            builds[key] = {"version": version, "build": build, "release_build": release}
            if build != release:
                errors.append(f"{module}: bản dựng {build} khác RELEASE_BUILD {release} "
                              f"(sửa code mà quên `{stamp}`, hoặc thiếu file)")

        stock_version = (builds["stock"] or {}).get("version", "unknown")
        name = f"stockvn-botkeep-{stock_version}" + (f"-alt{builds['altcoin']['version']}" if builds["altcoin"] else "") + ".zip"
        data = make_zip(stage, paths, zip_time(ROOT, paths, from_git, dirty), args.dir_entries)
        sizes = {p: (stage / p).stat().st_size for p in paths}
        problems, warnings = check(sizes, len(data), args.max_files, args.max_bytes)
        errors += problems
        files, dirs, total = len(paths), len(folders(paths)), sum(sizes.values())
        report = {"zip": name, "source": "git" if from_git else "walk", "files": files, "dirs": dirs,
                  "zip_entries": files + (dirs if args.dir_entries else 0), "bytes": total, "zip_bytes": len(data),
                  "limits": {"files": args.max_files, "bytes": args.max_bytes},
                  "free": {"files": args.max_files - files, "files_and_dirs": args.max_files - files - dirs,
                           "bytes": args.max_bytes - total, "zip_bytes": args.max_bytes - len(data)},
                  "builds": builds, "python": platform.python_version()}

        print(f"Gói Botkeep ({'danh sách từ git' if from_git else 'duyệt thư mục'}): {name}")
        print(f"  {files} file, {dirs} thư mục = {files + dirs}/{args.max_files} mục"
              f"; zip ghi {report['zip_entries']} mục{' (có mục thư mục)' if args.dir_entries else ''}")
        print(f"  Giải nén {_n(total)} B, zip {_n(len(data))} B (giới hạn {_n(args.max_bytes)} B mỗi loại)")
        free = report["free"]
        print(f"  Còn trống: {free['files']} file ({free['files_and_dirs']} nếu tính thư mục), "
              f"{_n(free['bytes'])} B giải nén, {_n(free['zip_bytes'])} B zip")
        for key, label in (("stock", "Stock"), ("altcoin", "Altcoin")):
            if builds[key]:
                same = builds[key]["build"] == builds[key]["release_build"]
                print(f"  {label} {builds[key]['version']}: bản dựng {builds[key]['build']}"
                      + (" = bản phát hành" if same else f" ≠ RELEASE_BUILD {builds[key]['release_build']}"))
        if not alt:
            print("  Altcoin: chưa có altcoin/bot/ (bỏ qua)")
        for line in warnings:
            print(f"⚠️  Gần giới hạn: {line}")
        if errors:
            print(f"\n❌ Gói KHÔNG đạt ({len(errors)} lỗi), không ghi gì:")
            print("\n".join(f"  - {e}" for e in errors))
            return 1
        if args.check:
            print("\n✅ Gói đạt mọi kiểm tra (--check: không ghi gì).")
            return 0
        out = args.out.resolve()
        out.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(out / "botkeep", ignore_errors=True)
        shutil.copytree(stage, out / "botkeep")
    for old in out.glob("stockvn-botkeep-*.zip"):
        old.unlink()
    (out / name).write_bytes(data)
    (out / "botkeep.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n✅ Đã ghi {out / 'botkeep'}{os.sep}, {out / name}, {out / 'botkeep.json'}")
    print("   Upload file zip rồi giải nén vào thư mục gốc của server Botkeep.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
