"""altcoin's own offline test scripts (altcoin/test_*.py, not test_smoke.py: it needs OKX), each on
its own temporary copy of altcoin/bot + run.py, in its own process with a timeout: no .env or bot.env
is read, no real database is touched, the network is blocked (_common in the child). And
altcoin/check_db.py reads the .env/bot.env beside it (ALT_DATABASE_URL first), never the stock .env
above it."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import _common  # noqa: F401 - clean env, no .env, offline, temp folder, repo on sys.path

t = _common.Checks()
ALT = _common.REPO / "altcoin"
SUITES = Path(__file__).resolve().parent
SCRIPTS = ("test_engine", "test_learner", "test_risk", "test_strategy", "test_web")
TIMEOUT = 300
# The child: _common first (clean env, offline, temp cwd), then no env file at all for bot/config.py.
RUN = ("import sys, runpy; sys.path[:0] = [sys.argv[1], sys.argv[2]]; import _common; import dotenv; "
       "dotenv.dotenv_values = lambda *a, **k: {}; runpy.run_path(sys.argv[3], run_name='__main__')")


def run(args: list[str], cwd: Path) -> tuple[int, str]:
    env = {**os.environ, "TMPDIR": str(_common.ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        done = subprocess.run([sys.executable, *args], cwd=cwd, env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=TIMEOUT)
        return done.returncode, done.stdout + done.stderr
    except subprocess.TimeoutExpired as exc:
        return 124, f"{exc.stdout or ''}{exc.stderr or ''}\nHẾT GIỜ sau {TIMEOUT} giây"


for name in SCRIPTS:
    copy = _common.ROOT / name
    shutil.copytree(ALT / "bot", copy / "bot", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copyfile(ALT / "run.py", copy / "run.py")
    # the scripts keep their data in fixed /tmp folders: moved into this suite's temp folder
    source = (ALT / f"{name}.py").read_text(encoding="utf-8").replace('"/tmp/', f'"{copy.as_posix()}/')
    (copy / f"{name}.py").write_text(source, encoding="utf-8")
    code, out = run(["-c", RUN, str(SUITES), str(copy), str(copy / f"{name}.py")], copy)
    t.ok(f"altcoin/{name}.py: exit 0", code == 0, "\n" + out[-2500:])

# check_db.py: inside the merged tree the stock .env sits one folder up
tree = _common.ROOT / "check_db"
(tree / "altcoin").mkdir(parents=True)
shutil.copyfile(ALT / "check_db.py", tree / "altcoin" / "check_db.py")
(tree / ".env").write_text("DATABASE_URL=postgresql://stock:stock@127.0.0.1:9/stock\n", encoding="utf-8")
code, out = run([str(tree / "altcoin" / "check_db.py")], tree)
t.ok("check_db.py: the stock .env above it is not read", code != 0 and "Chưa có ALT_DATABASE_URL" in out
     and "Kiểm tra" not in out, out[-800:])
(tree / "altcoin" / ".env").write_text("DATABASE_URL=postgresql://alt:alt@127.0.0.1:9/env\n", encoding="utf-8")
(tree / "altcoin" / "bot.env").write_text("ALT_DATABASE_URL=postgresql://alt:alt@127.0.0.1:9/botenv\n"
                                          "DATABASE_URL=<chưa điền>\n", encoding="utf-8")
code, out = run([str(tree / "altcoin" / "check_db.py")], tree)
t.ok("check_db.py: ALT_DATABASE_URL of the bot.env beside it first (local address only)",
     "Kiểm tra ALT_DATABASE_URL (từ bot.env)" in out and "Kết nối OK" not in out, out[-800:])
(tree / "altcoin" / "bot.env").unlink()
code, out = run([str(tree / "altcoin" / "check_db.py")], tree)
t.ok("check_db.py: else DATABASE_URL of the .env beside it", "Kiểm tra DATABASE_URL (từ .env)" in out, out[-800:])

t.done()
