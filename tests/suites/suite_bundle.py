"""The Botkeep bundle (scripts/build_botkeep_bundle.py): only the allow list goes in, never a
secret, a database, the tests or a cache; Botkeep's limits (250 files counting the folders,
4,000,000 B unpacked and zipped) hold; the zip is flat and the same every time; the staged
copy's build fingerprints are the released ones (offline, on copies of the repository)."""
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import _common  # noqa: F401 - clean env, no .env, offline, temp folder, repo on sys.path

t = _common.Checks()
REPO = _common.REPO
SCRIPT = "scripts/build_botkeep_bundle.py"
_spec = importlib.util.spec_from_file_location("build_botkeep_bundle", REPO / SCRIPT)
bb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bb)
from vnbot import version as ver  # noqa: E402


def build(root: Path, *args: str) -> tuple[int, str]:
    done = subprocess.run([sys.executable, str(root / SCRIPT), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300)
    return done.returncode, done.stdout + done.stderr


def names_of(zip_path: Path) -> list[str]:
    with zipfile.ZipFile(zip_path) as archive:
        return archive.namelist()


def write(root: Path, rel: str, data: str | bytes = "decoy\n") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode() if isinstance(data, str) else data)


def copy_repo(dest: Path) -> Path:
    for rel in bb.source_files(REPO)[0]:
        write(dest, rel, (REPO / rel).read_bytes())
    return dest


# ── 1. the limits ──
t.ok("Botkeep's limits: 250 files, 4,000,000 B", bb.MAX_FILES == 250 and bb.MAX_BYTES == 4_000_000)

# ── 2. the repository's bundle ──
out = _common.ROOT / "out"
code, log = build(REPO, "--out", str(out), "--allow-dirty")
t.ok("the repository builds (exit 0)", code == 0, log[-3000:])
report = json.loads((out / "botkeep.json").read_text(encoding="utf-8"))
zip_path = out / report["zip"]
names = names_of(zip_path)
staged = sorted(p.relative_to(out / "botkeep").as_posix() for p in (out / "botkeep").rglob("*") if p.is_file())
t.ok("botkeep.json is written beside the stage, not in it", "botkeep.json" not in staged and zip_path.parent == out)
t.ok("zip = the staged folder, sorted, no folder entry", names == staged == sorted(names)
     and not any(n.endswith("/") for n in names), names[:5])
t.ok("flat zip: the files at its root", "main_botkeep.py" in names and "vnbot/web.py" in names)
t.ok("limits hold: files, files + folders, unpacked, zip",
     report["files"] == len(names) <= 250 and report["files"] + report["dirs"] <= 250
     and report["dirs"] == len(bb.folders(names)) and report["bytes"] <= 4_000_000
     and report["zip_bytes"] == zip_path.stat().st_size <= 4_000_000, report)
t.ok("only the allow list, nothing forbidden", all(bb.allowed(n) and not bb.forbidden(n) for n in names),
     [n for n in names if not bb.allowed(n) or bb.forbidden(n)])
t.ok("required files in (start file, requirements, logo svg, favicon, logo)", set(bb.REQUIRED) <= set(names))
packages_py = {p for p in bb.source_files(REPO)[0] if p.endswith(".py") and p.startswith(bb.PY_TREES)}
t.ok("every .py of tradingagents/, vnbot/ (and altcoin/bot/) in", packages_py <= set(names),
     sorted(packages_py - set(names))[:5])
with zipfile.ZipFile(zip_path) as archive:
    infos = archive.infolist()
t.ok("fixed time, unix file mode, deflate", all(i.date_time == bb.ZIP_TIME and i.compress_type == zipfile.ZIP_DEFLATED
                                                and i.external_attr >> 16 == 0o100644 for i in infos))
t.ok("stock fingerprint of the staged copy = RELEASE_BUILD", report["builds"]["stock"]
     == {"version": ver.VERSION, "build": ver.RELEASE_BUILD, "release_build": ver.RELEASE_BUILD}, report["builds"])
alt = (REPO / "altcoin" / "bot").is_dir()
t.ok("altcoin fingerprint checked only when altcoin/bot exists", (report["builds"]["altcoin"] is not None) == alt
     and (not alt or report["builds"]["altcoin"]["build"] == report["builds"]["altcoin"]["release_build"]))
free = report["free"]
print(f"     còn trống: {free['files']} file ({free['files_and_dirs']} nếu tính thư mục), "
      f"{bb._n(free['bytes'])} B giải nén, {bb._n(free['zip_bytes'])} B zip")

code, _ = build(REPO, "--out", str(_common.ROOT / "again"), "--allow-dirty")
again = _common.ROOT / "again" / report["zip"]
t.ok("reproducible: a second build gives the same zip, byte for byte", code == 0 and again.exists()
     and hashlib.sha256(again.read_bytes()).digest() == hashlib.sha256(zip_path.read_bytes()).digest())
code, log = build(REPO, "--out", str(_common.ROOT / "dirs"), "--allow-dirty", "--dir-entries")
with_dirs = names_of(_common.ROOT / "dirs" / report["zip"]) if code == 0 else []
t.ok("--dir-entries: the same files plus one entry per folder", code == 0
     and sorted(n for n in with_dirs if not n.endswith("/")) == names
     and {n[:-1] for n in with_dirs if n.endswith("/")} == bb.folders(names), log[-1500:])

# ── 3. decoys: a copy without git (the walk), with secrets, data, caches and tests beside the code ──
copy = copy_repo(_common.ROOT / "copy")
if not alt:     # a small altcoin tree (the real one comes with phase 2): bot/ + run.py hashed like bot.version
    fake_version = ('import hashlib\nfrom pathlib import Path\n\nVERSION = "0.0.1"\nRELEASE_BUILD = "{}"\n'
                    'ROOT = Path(__file__).resolve().parents[1]\n\n\ndef build():\n    digest = hashlib.sha256()\n'
                    '    for p in sorted(ROOT.joinpath("bot").rglob("*.py")) + [ROOT / "run.py"]:\n'
                    '        if p.name != "version.py" and "__pycache__" not in p.parts:\n'
                    '            digest.update(p.relative_to(ROOT).as_posix().encode() + p.read_bytes())\n'
                    '    return digest.hexdigest()[:7]\n')
    for rel in ("altcoin/bot/__init__.py", "altcoin/bot/main.py", "altcoin/run.py", "altcoin/hosted.py"):
        write(copy, rel, "x = 1\n")
    write(copy, "altcoin/requirements.txt", "ccxt==4.5.90\n")
    write(copy, "altcoin/bot/version.py", fake_version.format(""))
    _alt_spec = importlib.util.spec_from_file_location("fake_alt_version", copy / "altcoin/bot/version.py")
    _alt = importlib.util.module_from_spec(_alt_spec)
    _alt_spec.loader.exec_module(_alt)
    write(copy, "altcoin/bot/version.py", fake_version.format(_alt.build()))
DECOYS = (".env", "altcoin/bot.env", "altcoin/.env.example", "vnbot/__pycache__/web.cpython-313.pyc",
          "vnbot/__pycache__/stale.py", "data/bot.db", "data/bot.db-wal", "render.yaml", "altcoin/render.yaml",
          "vnbot/local.env", "tests/suites/suite_decoy.py", "tradingagents/tests/test_decoy.py", "docs/decoy.md",
          "altcoin/test_engine.py", "altcoin/tools/kronos_eval.py", "altcoin/dist/okx-ai-bot.zip",
          "altcoin/data/bot.db", "altcoin/bot/__pycache__/main.cpython-313.pyc", "altcoin/botkeep.env",
          "altcoin/README.md", "altcoin/check_db.py", ".github/workflows/ci.yml", "dist/botkeep/vnbot/old.py",
          "vnbot/static/unused.png", "check_db.py")
for rel in DECOYS:
    write(copy, rel, "TELEGRAM_TOKEN=123456:decoy\n")
code, log = build(copy)
copy_report = json.loads((copy / "dist" / "botkeep.json").read_text(encoding="utf-8")) if code == 0 else {}
copy_names = names_of(copy / "dist" / copy_report["zip"]) if code == 0 else []
t.ok("copy without git: built by walking the folder (exit 0)", code == 0 and copy_report["source"] == "walk", log[-3000:])
t.ok("no decoy in the zip (.env, bot.env, .env.example, pyc, db, data/, render.yaml, tests, tools, dist...)",
     copy_names and not set(DECOYS) & set(copy_names), sorted(set(DECOYS) & set(copy_names)))
t.ok("the root .env.example is in, altcoin's is not", ".env.example" in copy_names and "altcoin/.env.example" not in copy_names)
alt_expected = sorted({p.relative_to(copy).as_posix() for p in (copy / "altcoin" / "bot").rglob("*.py")
                       if "__pycache__" not in p.parts} | {p for p in bb.FILES if p.startswith("altcoin/") and (copy / p).is_file()})
t.ok("altcoin runtime in: bot/**/*.py, run.py (+ hosted.py, requirements.txt when present)",
     sorted(n for n in copy_names if n.startswith("altcoin/")) == alt_expected and "altcoin/run.py" in alt_expected,
     sorted(n for n in copy_names if n.startswith("altcoin/")))
t.ok("altcoin fingerprint checked and named in the zip", copy_report.get("builds", {}).get("altcoin")
     and copy_report["zip"].endswith(f"-alt{copy_report['builds']['altcoin']['version']}.zip"), copy_report.get("zip"))
t.ok("same stock code as the repository: same files in the bundle", sorted(n for n in copy_names if not n.startswith("altcoin/")) == names)

# ── 4. the limits fail the build ──
code, log = build(copy, "--check", "--max-files", "10")
t.ok("--max-files 10: exit 1", code == 1 and "> giới hạn 10" in log, log[-800:])
code, log = build(copy, "--check", "--max-bytes", "1000")
t.ok("--max-bytes 1000: exit 1", code == 1 and "B giải nén > giới hạn 1.000" in log and "B zip > giới hạn" in log, log[-800:])
t.ok("251 files: error", bb.check({f"f{i}.py": 1 for i in range(251)})[0])
errors, warnings = bb.check({f"f{i}.py": 1 for i in range(250)})
t.ok("250 files at the root: within the limit, with a warning (> 85%)", not errors and warnings, (errors, warnings))
t.ok("240 files in 11 folders (251 entries): error", bb.check({f"d{i % 11}/f{i}.py": 1 for i in range(240)})[0])
t.ok("4,000,001 B unpacked: error; 4,000,000 B: within", bb.check({"a.py": 4_000_001})[0]
     and not bb.check({"a.py": 4_000_000})[0])
t.ok("a 4,000,001 B zip: error", bb.check({"a.py": 1}, zip_bytes=4_000_001)[0])
t.ok("85%: warned above, quiet below", bb.check({"a.py": 3_500_000})[1] and not bb.check({"a.py": 3_000_000})[1])
NOT_LISTED = ("altcoin/.env.example", "altcoin/README.md", "altcoin/check_db.py", "vnbot/static/unused.png", "check_db.py")
t.ok("every other decoy is a forbidden name (refused even if it were listed)",
     all(bb.check({rel: 1})[0] for rel in DECOYS if rel not in NOT_LISTED),
     [rel for rel in DECOYS if rel not in NOT_LISTED and not bb.check({rel: 1})[0]])
t.ok("allow list: altcoin's .env.example, README, check_db.py and stray images stay out",
     not any(bb.allowed(p) for p in NOT_LISTED + ("requirements-dev.txt", "vnbot/notes.txt")))

# ── 5. fingerprints: code changed without --stamp is refused ──
web = copy / "vnbot" / "web.py"
web.write_bytes(web.read_bytes() + b"# edited\n")
code, log = build(copy, "--check")
t.ok("stock code edited, not stamped: exit 1, says --stamp", code == 1 and "python -m vnbot.version --stamp" in log, log[-800:])
web.write_bytes((REPO / "vnbot" / "web.py").read_bytes())
run_py = copy / "altcoin" / "run.py"
run_py.write_bytes(run_py.read_bytes() + b"# edited\n")
code, log = build(copy, "--check")
t.ok("altcoin run.py edited, not stamped: exit 1, says bot.version --stamp", code == 1
     and "python -m bot.version --stamp" in log and "vnbot.version" not in log, log[-800:])
run_py.unlink()
code, log = build(copy, "--check")
t.ok("altcoin/bot without run.py: exit 1 (missing file)", code == 1 and "altcoin/run.py: thiếu" in log, log[-800:])

# ── git: uncommitted changes to bundled files are refused without --allow-dirty ──
if shutil.which("git"):
    repo = copy_repo(_common.ROOT / "gitcopy")
    git = ["git", "-C", str(repo), "-c", "user.name=test", "-c", "user.email=test@example.invalid",
           "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null"]
    subprocess.run(git + ["init", "-q"], check=True, capture_output=True)
    subprocess.run(git + ["add", "-A"], check=True, capture_output=True)
    subprocess.run(git + ["commit", "-qm", "copy", "--no-verify"], check=True, capture_output=True)
    write(repo, "notes.txt")       # an untracked file outside the bundle: not a change that counts
    code, log = build(repo, "--check")
    t.ok("git copy, clean: exit 0", code == 0 and "danh sách từ git" in log, log[-800:])
    web = repo / "vnbot" / "web.py"
    web.write_bytes(web.read_bytes() + b"# edited\n")
    code, log = build(repo, "--check")
    t.ok("bundled file changed, not committed: exit 1", code == 1 and "vnbot/web.py: có thay đổi chưa commit" in log
         and "notes.txt" not in log, log[-800:])
    code, log = build(repo, "--check", "--allow-dirty")
    t.ok("--allow-dirty: the fingerprint still refuses unstamped code", code == 1 and "chưa commit" not in log
         and "--stamp" in log, log[-800:])
    tracked = len(bb._git(REPO, "ls-files", "-z")) if (REPO / ".git").exists() else 0
    if tracked > 240:
        print(f"⚠️  git theo dõi {tracked} file (> 240): không deploy cả repo, chỉ upload gói zip")
else:
    print("     (không có git: bỏ qua phần kiểm tra thay đổi chưa commit)")

t.done()
