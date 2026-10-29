"""Khởi động cho Botkeep.cloud / Render (runtime Python, không Docker).
Start command: python run.py
- Tự cài thư viện còn thiếu (không cache, tiết kiệm ổ đĩa), dọn cache pip.
- Chạy bot, tự khởi động lại nếu bot crash; dừng êm khi host tắt server."""
import importlib
import importlib.util
import os
import shutil
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REQUIRED = {"ccxt": "ccxt", "requests": "requests", "fastapi": "fastapi",
            "uvicorn": "uvicorn", "dotenv": "python-dotenv", "psycopg": "psycopg[binary]"}


def clean_cache():
    for d in (os.path.expanduser("~/.cache/pip"), os.path.join(HERE, "__pycache__")):
        shutil.rmtree(d, ignore_errors=True)


def disk_free_mb(path=HERE):
    try:
        st = os.statvfs(path)
        return st.f_bavail * st.f_frsize / 1e6
    except Exception:
        return -1


def ensure_deps():
    missing = [pkg for mod, pkg in REQUIRED.items() if importlib.util.find_spec(mod) is None]
    if not missing:
        return
    print(f"Cài thư viện: {missing} (ổ đĩa trống ~{disk_free_mb():.0f} MB)", flush=True)
    base = [sys.executable, "-m", "pip", "install", "--no-cache-dir", "--disable-pip-warnings", "-q"]
    req = ["-r", os.path.join(HERE, "requirements.txt")]
    for extra in (["--user"], [], ["--break-system-packages"]):
        if subprocess.call(base + extra + req, env={**os.environ, "PIP_NO_WARN_SCRIPT_LOCATION": "1"}) == 0:
            break
    clean_cache()


def main():
    os.chdir(HERE)
    try:
        ensure_deps()
    except Exception as e:
        print("Cài thư viện lỗi:", e, flush=True)
    child = None

    def stop(*_):
        if child and child.poll() is None:
            child.terminate()
        sys.exit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while True:
        child = subprocess.Popen([sys.executable, "-m", "bot.main"])
        code = child.wait()
        if code == 0:
            break
        clean_cache()
        print(f"Bot thoát với mã {code} (ổ đĩa trống ~{disk_free_mb():.0f} MB), khởi động lại sau 20s...", flush=True)
        time.sleep(20)


if __name__ == "__main__":
    main()
