"""Shared setup of the offline test suites. Every suite imports it FIRST:

    import _common  # noqa: F401 - clean env, no .env, offline, temp folder, repo on sys.path

so that a suite run on the server never sees the owner's settings or tokens, never
reaches the network (no Telegram/Zoom message, no market data), and leaves nothing
behind (all temporary files live in one folder removed at exit).
"""
from __future__ import annotations

import atexit
import os
import shutil
import socket
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

# 1. Only what a Python process needs from the environment: the bot's variables
#    (TELEGRAM_TOKEN, ZOOM_*, TRADE_MODE, ...) of the shell or the server never reach a test.
_KEEP = {"PATH", "HOME", "LANG", "LANGUAGE", "TZ", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT", "SYSTEMDRIVE",
         "COMSPEC", "PATHEXT", "WINDIR", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "PYTHONIOENCODING",
         "PYTHONUTF8", "VIRTUAL_ENV", "CONDA_PREFIX"}
for _key in list(os.environ):
    if _key not in _KEEP and not _key.startswith("LC_"):
        del os.environ[_key]

# 2. No .env file is read (the bot's modules call load_dotenv() when imported).
try:
    import dotenv
    import dotenv.main

    dotenv.load_dotenv = dotenv.main.load_dotenv = lambda *args, **kwargs: False
except ImportError:  # pragma: no cover - the bot itself needs python-dotenv
    pass

# 3. Offline: any connection outside this machine fails at once.
_LOCAL = {"127.0.0.1", "::1", "localhost"}
_real_connect, _real_connect_ex = socket.socket.connect, socket.socket.connect_ex
_real_getaddrinfo = socket.getaddrinfo


class NetworkBlocked(OSError):
    pass


def _host(address) -> str | None:
    return address[0] if isinstance(address, tuple) and address else None


def _connect(self, address, *args):
    if _host(address) not in _LOCAL and self.family != getattr(socket, "AF_UNIX", None):
        raise NetworkBlocked(f"bộ kiểm thử chạy offline: chặn kết nối tới {address!r}")
    return _real_connect(self, address, *args)


def _connect_ex(self, address, *args):
    if _host(address) not in _LOCAL and self.family != getattr(socket, "AF_UNIX", None):
        raise NetworkBlocked(f"bộ kiểm thử chạy offline: chặn kết nối tới {address!r}")
    return _real_connect_ex(self, address, *args)


def _getaddrinfo(host, *args, **kwargs):
    if host not in _LOCAL and host is not None:
        raise NetworkBlocked(f"bộ kiểm thử chạy offline: chặn tra DNS {host!r}")
    return _real_getaddrinfo(host, *args, **kwargs)


socket.socket.connect = _connect
socket.socket.connect_ex = _connect_ex
socket.getaddrinfo = _getaddrinfo

# 4. One temporary folder for everything (tempfile.mkdtemp() included), removed at exit;
#    the working directory is moved there so a relative "data" folder never lands in the repo.
ROOT = Path(tempfile.mkdtemp(prefix="vnbot-test-"))
tempfile.tempdir = str(ROOT)
atexit.register(shutil.rmtree, ROOT, True)
os.chdir(ROOT)
os.environ["DATA_DIR"] = str(ROOT / "data")
os.environ["PYTHONIOENCODING"] = "utf-8"

# 5. The repository's code, not an installed copy.
sys.path.insert(0, str(REPO))
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


SKIPPED = 77     # exit code of a suite that cannot run here (a missing test-only library)


def need(module: str) -> None:
    """Skip the suite (exit SKIPPED) when a test-only library is not installed."""
    import importlib.util

    if importlib.util.find_spec(module) is None:
        print(f"SKIPPED: thiếu thư viện {module} - cài: pip install -r requirements-dev.txt")
        sys.exit(SKIPPED)


class Checks:
    """A tiny check list for the suites: ``ok(name, condition, info)``, then ``done()``."""

    def __init__(self) -> None:
        self.passed, self.failed = 0, []

    def ok(self, name: str, condition, info="") -> bool:
        if condition:
            self.passed += 1
            print(f"ok   {name}")
        else:
            self.failed.append(name)
            print(f"FAIL {name}" + (f"  [{info}]" if info != "" else ""))
        return bool(condition)

    def done(self) -> None:
        print(f"\n{self.passed} passed, {len(self.failed)} failed")
        sys.exit(1 if self.failed else 0)
