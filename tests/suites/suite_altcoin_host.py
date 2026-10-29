"""The altcoin as a child of the stock process (offline): vnbot/altcoin_host.py (the supervisor)
and altcoin/hosted.py (the child).

- the child's environment: an allowlist, ALT_<NAME> as <NAME>, the stock's tokens never (even
  after /caidat put them in os.environ), the ignored ALT_ keys reported, Botkeep's masked
  values dropped, the shared AI keys only when the altcoin has none;
- the live gate (supervisor, then the child) up to a stubbed Bot(); a real Bot() whose
  "Telegram" is HubChannel and that reaches no Telegram/Zoom address;
- sealing against a decoy bot.env, precedence, the database guards (the stock's URL or tables,
  an empty database, a hot journal, hosted.lock, heartbeats), the pipe (frames, limits, newest
  state first, the last notifications written before exit, HUB_DROPPED),
  the timezone (tzdata fallback), ``hosted.py --check`` from / with a decoy stock .env;
- the supervisor with a fake child: exit 78/75/-9, backoff, readiness timeout on a new port,
  stop() (SIGKILL fallback, /health fast meanwhile, the pipe read to its end then drain()),
  main_botkeep's SIGTERM handler, masking.
"""
import contextlib
import io
import json
import logging
import os
import re
import select
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import _common  # noqa: F401 - clean env, no .env, offline, temp folder, repo on sys.path

t = _common.Checks()
logging.basicConfig(level=logging.CRITICAL)      # the supervisor's warnings are expected here
ALT = _common.REPO / "altcoin"
SUITES = Path(__file__).resolve().parent
TMP = _common.ROOT
PY = sys.executable
HOST = os.uname().nodename
ALT_BUILD = re.search(r'^RELEASE_BUILD = "(\w+)"', (ALT / "bot" / "version.py").read_text(encoding="utf-8"), re.M)[1]
BASE_ENV = {k: os.environ[k] for k in ("PATH", "HOME", "LANG") if k in os.environ}
BASE_ENV.update(TZ="UTC", PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")

sys.path.insert(0, str(ALT))
import hosted  # noqa: E402 - the module only: its main() is not run here

from vnbot import altcoin_host as ah, boot, eventlog  # noqa: E402


def wait_for(condition, timeout=10.0, step=0.05):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(step)
    return bool(condition())


def tree(name: str, bot_env: str = "", stock_env: str = "") -> Path:
    """A copy of altcoin/ (bot/, run.py: the fingerprint; hosted.py) under <TMP>/<name>/altcoin, its own bot.env."""
    alt = TMP / name / "altcoin"
    shutil.copytree(ALT / "bot", alt / "bot", ignore=shutil.ignore_patterns("__pycache__"))
    for file in ("run.py", "hosted.py"):
        shutil.copyfile(ALT / file, alt / file)
    if bot_env:
        (alt / "bot.env").write_text(bot_env, encoding="utf-8")
    if stock_env:
        (alt.parent / ".env").write_text(stock_env, encoding="utf-8")
    return alt


def alt_db(path: Path, *, funded=True, beats=(), tables=(), trades=()) -> Path:
    """An altcoin SQLite database: kv (start_equity), heartbeats, trades ((mode, status), ...), any other tables."""
    path.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path / "bot.db")
    con.execute("CREATE TABLE kv(k TEXT PRIMARY KEY, v TEXT)")
    con.execute("CREATE TABLE heartbeats(instance TEXT PRIMARY KEY, ts REAL, role TEXT, info TEXT)")
    if trades:
        con.execute("CREATE TABLE trades(id INTEGER PRIMARY KEY, mode TEXT, symbol TEXT, status TEXT)")
        con.executemany("INSERT INTO trades(mode, symbol, status) VALUES(?, 'BTC/USDT:USDT', ?)", trades)
    if funded:
        con.execute("INSERT INTO kv VALUES('start_equity', '10')")
    for name, age, host in beats:
        con.execute("INSERT INTO heartbeats VALUES(?,?,?,?)", (name, time.time() - age, "leader",
                                                              f"v3.2.0 b380dfca paper=True host={host}"))
    for table in tables:
        con.execute(f"CREATE TABLE {table}(x)")
    con.commit()
    con.close()
    return path


# The child under test, in its own process: _common first (offline), the child's environment back,
# then hosted.main(argv) with launch() replaced: "guards" stops before bot.main, "stub" runs
# bot.main.main() with a stub Bot, "rival" too, its heartbeats showing this instance name on
# another host, "real" builds the real Bot() (its run loop not started).
DRIVER = TMP / "driver.py"
DRIVER.write_text(r'''
import json, os, socket, subprocess, sys, time, types
suites, alt, kind = sys.argv[1:4]
saved = dict(os.environ)
sys.path.insert(0, suites)
import _common
os.environ.clear(); os.environ.update(saved)
sys.path.insert(0, alt)
import hosted
hosted.STATE_EVERY = 0.2
out = {"kind": kind}
lookups = []
_real = socket.getaddrinfo
def _lookup(host, *a, **k):
    lookups.append(str(host)); return _real(host, *a, **k)
socket.getaddrinfo = _lookup
fake_pg = json.loads(saved.get("FAKE_PG") or "null")
if fake_pg is not None:
    class Conn:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, params=()):
            out.setdefault("pg_sql", []).append(sql)
            class R:
                def fetchall(self_):
                    if "information_schema" in sql: return [(x,) for x in fake_pg["tables"]]
                    if "FROM kv" in sql: return [("start_equity",)] if fake_pg.get("funded") else []
                    if "FROM trades" in sql: return [(m,) for m in fake_pg.get("open_modes", [])]
                    return [tuple(b) for b in fake_pg.get("beats", [])]
            return R()
    def connect(url, **kw):
        out["pg_url_sslmode"] = "sslmode=require" in url
        out["pg_kw"] = sorted(kw)
        return Conn()
    sys.modules["psycopg"] = types.SimpleNamespace(connect=connect)

def report(cfg):
    keys = ("MODE", "INSTANCE_NAME", "PUBLIC_URL", "DATA_DIR", "DATABASE_URL", "OPENROUTER_API_KEY", "LEVERAGE",
            "OKX_API_KEY", "OKX_API_SECRET", "OKX_PASSPHRASE", "OKX_DEMO", "DASHBOARD_PORT", "TELEGRAM_TOKEN")
    out["cfg"] = {k: getattr(cfg, k) for k in keys}
    out["env"] = {k: os.environ.get(k) for k in ("MODE", "I_UNDERSTAND_THE_RISK", "OKX_API_KEY", "DASHBOARD_HOST",
                                                  "PORT", "DATABASE_URL", "TELEGRAM_TOKEN", "ZOOM_BOT_SECRET_TOKEN")}
    out["hub_env_left"] = [k for k in hosted.HUB_ENV if k in os.environ]
    fd = int(saved.get("HUB_FD") or -1)
    if fd >= 0:
        out["inheritable"] = os.get_inheritable(fd)
        out["grandchild_sees_fd"] = subprocess.run([sys.executable, "-c", f"import os; os.fstat({fd})"],
                                                   close_fds=False, capture_output=True).returncode == 0

def launch():
    from bot.config import cfg
    if kind == "guards":
        report(cfg)
        end = time.monotonic() + 3
        while hosted.HUB.sent is None and time.monotonic() < end:   # the first state frame written
            time.sleep(0.02)
    elif kind == "stub":
        import bot.main as bm
        hosted.install_seams(bm)
        class Bot:
            def __init__(self):
                out["engine"] = (bm.LiveEngine if bm.cfg.MODE == "live" else bm.PaperEngine).__name__
                out["telegram_is_hub"] = bm.Telegram is hosted.HubChannel
                report(cfg)
            def run(self):
                out["ran"] = True
        bm.Bot = Bot
        bm.main()
    elif kind == "lastword":
        import bot.main as bm
        hosted.install_seams(bm)
        write = hosted.Hub.write
        def slow(self, frame):                      # a slow pipe: still queued when main() returns
            if frame.get("t") == "notify":
                time.sleep(0.2)
            write(self, frame)
        hosted.Hub.write = slow
        class Bot:
            def __init__(self):
                self.tg = bm.Telegram(self)
                report(cfg)
            def run(self):
                for i in range(5):
                    self.tg.send(f"📉 ĐÓNG LỆNH {i}")
        bm.Bot = Bot
        bm.main()
    elif kind == "rival":
        import bot.main as bm
        hosted.install_seams(bm)
        hosted.RIVAL_EVERY = 0.1
        class Store:
            def instances(self_):
                out["looked"] = out.get("looked", 0) + 1
                return [{"instance": cfg.INSTANCE_NAME, "ts": time.time(), "role": "leader",
                         "info": "v3.2.0 b380dfca paper=True host=old-server"}]
        class Bot:
            def __init__(self):
                self.store, self.is_leader, self.start_equity = Store(), True, 10.0
                self.tg = bm.Telegram(self)
                self.notify = types.SimpleNamespace(channels=[self.tg])
            def run(self):
                try:
                    time.sleep(30)
                except KeyboardInterrupt:          # bot.main's SIGTERM handler, as its run loop
                    out["stopped_by_sigterm"] = True
        bm.Bot = Bot
        bm.main()
    else:
        import bot.main as bm
        import requests
        calls = []
        def blocked(self, method, url, *a, **k):
            calls.append(str(url)); raise requests.ConnectionError("offline")
        requests.Session.request = blocked
        hosted.install_seams(bm)
        bot = bm.Bot()
        out.update(tg=type(bot.tg).__name__, tg_in_channels=bot.tg in bot.notify.channels, zoom_on=bot.zoom.enabled)
        bot._elect()                              # leader: "👑 ... tiếp quản" goes to the hub
        time.sleep(1.0)
        bot.tg = object()                         # a broken seam: seam_ok false in the next state frame
        time.sleep(0.8)
        out["requests"] = calls
        report(cfg)
    out["lookups"] = lookups
    print("RESULT " + json.dumps(out, ensure_ascii=False), flush=True)

hosted.launch = launch
sys.exit(hosted.main(sys.argv[4:]))
''', encoding="utf-8")


def child(alt: Path, kind: str, env: dict, *args: str, hub: bool = True, cwd=None, timeout=120):
    """(exit code, output, RESULT dict or None, frames read on HUB_FD)."""
    env = {**BASE_ENV, **env}
    fds, frames, chunks = (), [], []
    if hub:
        read_end, write_end = os.pipe()
        env["HUB_FD"], env["HUB_TOKEN"], env["HUB_CMD_TOKEN"] = str(write_end), "hub-token-xyz1", "cmd-token-xyz2"
        fds = (write_end,)

        def drain():
            with open(read_end, "rb") as pipe:
                chunks.append(pipe.read())
        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
    cmd = [PY, str(DRIVER), str(SUITES), str(alt), kind, *args] if kind != "check" \
        else [PY, str(alt / "hosted.py"), "--check", *args]
    try:
        done = subprocess.run(cmd, env=env, cwd=cwd or alt.parent, pass_fds=fds, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
        code, out = done.returncode, done.stdout + done.stderr
    finally:
        if hub:
            os.close(write_end)
            reader.join(10)
    if hub and chunks:
        frames = [json.loads(line) for line in chunks[0].decode().splitlines() if line.strip()]
    found = next((line[7:] for line in out.splitlines() if line.startswith("RESULT ")), None)
    return code, out, json.loads(found) if found else None, frames


def check_json(out: str) -> dict:
    start = out.index("\n{") + 1 if not out.startswith("{") else 0
    return json.loads(out[start:out.rindex("}") + 1])


# ── 1. the child's environment (pure) ──
stock = {"PATH": "/usr/bin", "HOME": "/root", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "UTC",
         "HTTPS_PROXY": "http://proxy:3128", "no_proxy": "localhost",
         # the stock's own, some put there by /caidat (config.apply): never passed
         "TELEGRAM_TOKEN": "111111111:stock-telegram-token-aaaaaaaaaaaaaaaaaaa", "TELEGRAM_ALLOWED_IDS": "1",
         "ZOOM_CLIENT_SECRET": "stock-zoom-secret", "ZOOM_SECRET_TOKEN": "stock-zoom-token",
         "WEB_PASSWORD": "stock-web-pass", "DATABASE_URL": "postgresql://stock:pw@db.stock:5432/postgres",
         "RENDER_SERVICE_NAME": "okx", "RENDER_EXTERNAL_URL": "https://okx.onrender.com", "MODE": "live",
         "DATA_DIR": "/data", "PORT": "8080", "SERVER_PORT": "8080", "OPENROUTER_API_KEY": "sk-or-stock-key",
         "ROUTER9_BASE_URL": "http://127.0.0.1:20128/v1", "ROUTER9_API_KEY": "stock-router9-key",
         # the altcoin's
         "ALT_LEVERAGE": "5", "ALT_DATABASE_URL": "postgresql://alt:pw@db.alt:5432/postgres",
         "ALT_MODE": "live", "ALT_I_UNDERSTAND_THE_RISK": "yes", "ALT_LIVE_ACK": "botkeep", "ALT_DATA_DIR": "/x",
         "ALT_PUBLIC_URL": "https://h/altcoin", "ALT_INSTANCE_NAME": "crypto",
         "ALT_TELEGRAM_TOKEN": "222:old-okx-bot", "ALT_TELEGRAM_CHAT_ID": "-100", "ALT_ZOOM_CLIENT_ID": "zid",
         "ALT_ZOOM_BOT_SECRET_TOKEN": "zbs", "ALT_PEER_URL": "https://peer", "ALT_PEER_MODE": "x",
         "ALT_SELF_URL": "https://self", "ALT_KEEPALIVE_INTERVAL": "240", "ALT_DASHBOARD_USER": "admin",
         "ALT_DASHBOARD_PASS": "pw", "ALT_SUPPORT_EMAIL": "a@b.c", "ALT_EMPTY": "",
         "ALT_OKX_API_SECRET": "__BOTKEEP_KEEP_SECRET_ALT_OKX_API_SECRET__"}
env, info = ah.child_env(stock, {})
t.ok("child env: the system allowlist passes (PATH, HOME, LANG, LC_*, TZ, proxies)",
     all(env.get(k) == stock[k] for k in ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "HTTPS_PROXY", "no_proxy")), env)
t.ok("child env: PYTHONIOENCODING=utf-8, PYTHONUTF8=1", env.get("PYTHONIOENCODING") == "utf-8"
     and env.get("PYTHONUTF8") == "1")
t.ok("child env: no stock variable (TELEGRAM_*, ZOOM_*, WEB_PASSWORD, DATABASE_URL of the stock, RENDER_*, MODE, "
     "DATA_DIR, PORT)", not any(k.startswith(("TELEGRAM_", "ZOOM_", "RENDER_", "WEB_")) for k in env)
     and env.get("DATABASE_URL") == stock["ALT_DATABASE_URL"] and not {"MODE", "DATA_DIR", "PORT", "SERVER_PORT"} & set(env),
     sorted(env))
t.ok("child env: ALT_<NAME> becomes <NAME>", env.get("LEVERAGE") == "5" and "ALT_LEVERAGE" not in env)
t.ok("child env: the supervisor's keys never pass (ALT_MODE, _I_UNDERSTAND_THE_RISK, _LIVE_ACK, _DATA_DIR, "
     "_PUBLIC_URL, _INSTANCE_NAME)", not {"I_UNDERSTAND_THE_RISK", "LIVE_ACK", "PUBLIC_URL", "INSTANCE_NAME"} & set(env)
     and not any(k.startswith("ALT_") for k in env))
ignored = ["ALT_DASHBOARD_PASS", "ALT_DASHBOARD_USER", "ALT_KEEPALIVE_INTERVAL", "ALT_PEER_MODE", "ALT_PEER_URL",
           "ALT_SELF_URL", "ALT_SUPPORT_EMAIL", "ALT_TELEGRAM_CHAT_ID", "ALT_TELEGRAM_TOKEN",
           "ALT_ZOOM_BOT_SECRET_TOKEN", "ALT_ZOOM_CLIENT_ID"]
t.ok("child env: the ignored ALT_ keys (Telegram, Zoom, peer, self URL, keepalive, login, support e-mail) are only "
     "reported", info["ignored_keys"] == ignored and not {"SELF_URL", "PEER_URL", "KEEPALIVE_INTERVAL",
                                                           "SUPPORT_EMAIL", "DASHBOARD_PASS"} & set(env), info)
t.ok("child env: Botkeep's masked value dropped and reported", info["dropped_secrets"] == ["ALT_OKX_API_SECRET"]
     and "OKX_API_SECRET" not in env)
t.ok("child env: the stock's AI keys shared when the altcoin has none", env.get("OPENROUTER_API_KEY") == "sk-or-stock-key"
     and env.get("NINEROUTER_API_KEY") == "stock-router9-key" and env.get("NINEROUTER_BASE_URL") == stock["ROUTER9_BASE_URL"]
     and info["shared"] == ["OPENROUTER_API_KEY", "ROUTER9_BASE_URL", "ROUTER9_API_KEY"], info["shared"])
env2, info2 = ah.child_env({**stock, "ALT_OPENROUTER_API_KEY": "sk-or-alt"},
                           {"NINEROUTER_API_KEY": "botenv-9r", "NINEROUTER_BASE_URL": "<chưa điền>"})
t.ok("...not over the altcoin's own (panel ALT_, or altcoin/bot.env; a <...> placeholder does not count)",
     env2["OPENROUTER_API_KEY"] == "sk-or-alt" and "NINEROUTER_API_KEY" not in env2
     and env2.get("NINEROUTER_BASE_URL") == stock["ROUTER9_BASE_URL"], info2["shared"])
t.ok("child env: an empty ALT_ value is left out", "EMPTY" not in env)
bad = TMP / "badenv"
bad.mkdir()
(bad / ".env").write_text("LEVERAGE=4\n", encoding="utf-8")
(bad / "bot.env").write_bytes(b"LEVERAGE=5\n# ghi ch\xfa\nOKX_API_KEY=x\n")
try:
    got = ah.file_values(bad)
except Exception as exc:  # noqa: BLE001
    got = repr(exc)
t.ok("file_values: a bot.env that is not UTF-8 is skipped, as the child skips it (never a supervisor error forever)",
     got == {"LEVERAGE": "4"}, got)

from vnbot import config as stock_config  # noqa: E402

t.ok("no ALT_/ALTCOIN_ key and no MODE on the web settings page (config.FIELDS)",
     not [f.key for f in stock_config.FIELDS if f.key.startswith(("ALT_", "ALTCOIN_")) or f.key == "MODE"])

# ── 2. the live gate (supervisor) ──
live = {"ALT_MODE": "live", "ALT_I_UNDERSTAND_THE_RISK": "yes", "ALT_LIVE_ACK": "crypto",
        "ALT_OKX_API_KEY": "k-panel", "ALT_OKX_API_SECRET": "s-panel", "ALT_OKX_PASSPHRASE": "p-panel"}
t.ok("live gate: every condition → live", ah.live_gate(live, "crypto") == ("live", None))
cases = {
    "no ALT_MODE": ({k: v for k, v in live.items() if k != "ALT_MODE"}, None),
    "risk not yes": ({**live, "ALT_I_UNDERSTAND_THE_RISK": "no"}, "ALT_I_UNDERSTAND_THE_RISK=yes"),
    "no ALT_LIVE_ACK": ({k: v for k, v in live.items() if k != "ALT_LIVE_ACK"}, "ALT_LIVE_ACK=crypto"),
    "ALT_LIVE_ACK of another instance": ({**live, "ALT_LIVE_ACK": "botkeep"}, "ALT_LIVE_ACK=crypto"),
    "an OKX key missing": ({k: v for k, v in live.items() if k != "ALT_OKX_PASSPHRASE"}, "ALT_OKX_PASSPHRASE"),
    "an OKX key masked": ({**live, "ALT_OKX_API_KEY": "__BOTKEEP_KEEP_SECRET_ALT_OKX_API_KEY__"}, "ALT_OKX_API_KEY"),
    "ALT_MODE not paper/live": ({**live, "ALT_MODE": "real"}, "không hợp lệ"),
}
for name, (environ, why) in cases.items():
    mode, error = ah.live_gate(environ, "crypto")
    t.ok(f"live gate: {name} → paper" + (" with the reason" if why else ""), mode == "paper"
         and (error is None if why is None else why in (error or "")), error)
for name, extra, why in (("ALT_LIVE_ACK of another name", {"ALT_LIVE_ACK": "x"}, "ALT_LIVE_ACK=crypto"),
                         ("ALT_MODE not paper/live", {"ALT_MODE": "real"}, "ALT_MODE=real")):
    sup = ah.Supervisor(TMP / "gate-data", environ={**live, "ALTCOIN_ENABLED": "1", "ALT_INSTANCE_NAME": "crypto", **extra},
                        hosted=TMP / "nonexistent" / "hosted.py")
    sup._run(None)
    st = sup.state()
    t.ok(f"supervisor: a refused live ({name}) starts nothing, not even paper: disabled with the reason",
         st["state"] == "disabled" and sup._spawns == 0 and why in st["reason"] and "Altcoin không chạy" in st["reason"]
         and st["reason"] == st["live_error"], st)

# ── 3. the database fingerprint: the same database whatever the password or the query ──
same = ["postgresql://u:pw1@DB.example.com:5432/postgres?sslmode=require", "postgres://u:other@db.example.com/postgres"]
t.ok("db_fingerprint: password, query, scheme spelling and default port do not matter",
     len({ah.db_fingerprint(u) for u in same}) == 1 and ah.db_fingerprint(same[0]) == hosted.db_fingerprint(same[1]))
t.ok("db_fingerprint: another user, host, port or database differs", len({ah.db_fingerprint(u) for u in (
    same[0], "postgresql://v:pw1@db.example.com:5432/postgres", "postgresql://u:pw1@db2.example.com:5432/postgres",
    "postgresql://u:pw1@db.example.com:6543/postgres", "postgresql://u:pw1@db.example.com:5432/other")}) == 5)
t.ok("db_fingerprint: none for an empty URL; the password never in it", ah.db_fingerprint("") == ""
     and "pw1" not in ah.db_fingerprint(same[0]) and len(ah.db_fingerprint(same[0])) == 16)

# ── 4. --check: sealing against a decoy bot.env, run from / with a decoy stock .env ──
config_src = (ALT / "bot" / "config.py").read_text(encoding="utf-8")
zoom_attrs = set(re.findall(r"^\s+(ZOOM_\w+) = ", config_src, re.M))
zoom_env = set(re.findall(r'os\.getenv\("(ZOOM_\w+)"', config_src))
t.ok("pinned: exactly 10 cfg.ZOOM_* attributes and 11 ZOOM_* names in bot/config.py, all emptied",
     len(zoom_attrs) == 10 and len(zoom_env) == 11 and zoom_attrs == {a for a in hosted.CHANNEL_ATTRS if a.startswith("ZOOM_")}
     and zoom_env == {k for k in hosted.CHANNEL_ENV if k.startswith("ZOOM_")}, (sorted(zoom_attrs), sorted(zoom_env)))
decoy = "\n".join(f"{k}=decoy-{k.lower()}-value" for k in hosted.CHANNEL_ENV) + "\nTELEGRAM_TOKEN=987654321:AAHdqTcvCH1vGWJx" \
    "fSeofSAs0K5PALDsawQ\nLEVERAGE=7\nPORT=9999\nDASHBOARD_HOST=0.0.0.0\nINSTANCE_NAME=crypto\nMODE=live\n"
alt = tree("check", bot_env=decoy, stock_env="LEVERAGE=99\nTELEGRAM_TOKEN=stock-decoy\nINSTANCE_NAME=stockdecoy\n")
code, out, _, _ = child(alt, "check", {}, "--port", "4321", "--data-dir", str(TMP / "check-data"), "--instance", "botkeep",
                        hub=False, cwd="/")
cj = check_json(out)
t.ok("--check from cwd /: exit 0, paper, only altcoin/bot.env read (not the stock's .env)", code == 0
     and cj["mode"] == "paper" and cj["env_files"] == "bot.env" and cj["instance"] == "botkeep", out[-1500:])
t.ok("--check: every Telegram/Zoom/Render/login cfg value empty, none left in the environment",
     set(cj["emptied"]) == set(hosted.CHANNEL_ATTRS) and not any(cj["emptied"].values()) and cj["emptied_env_left"] == [])
t.ok("--check: ignored_keys lists all 17 (ZOOM_BOT_SECRET_TOKEN too), with a WARNING naming them",
     sorted(cj["ignored_keys"]) == sorted(hosted.CHANNEL_ENV) and len(cj["ignored_keys"]) == 17
     and "Bỏ qua TELEGRAM_TOKEN" in out and "ZOOM_BOT_SECRET_TOKEN" in out.split("WARNING")[1], cj["ignored_keys"])
t.ok("--check: port and host sealed (bot.env's PORT, DASHBOARD_HOST, INSTANCE_NAME ignored, WARNING)",
     cj["dashboard"] == "127.0.0.1:4321" and "Bỏ qua PORT, DASHBOARD_HOST: cổng" in out.replace("INSTANCE_NAME, ", ""), out[:1500])
t.ok("--check: MODE=live in bot.env is an ERROR, not live", "ERROR" in out and "MODE=live trong altcoin/bot.env" in out)
t.ok("--check: no secret value printed", "decoy-" not in out and "AAHdq" not in out)
t.ok("--check: Vietnam time in the child while the parent is UTC (tm_gmtoff 25200)", cj["tz"] == "ok"
     and cj["utc_offset"] == 25200 and cj["timezone"] == "Asia/Ho_Chi_Minh")
stock_root = tree("check-root", stock_env="LEVERAGE=99\nTELEGRAM_TOKEN=stock-decoy\nMODE=live\n")
code, out, _, _ = child(stock_root, "check", {}, "--port", "1", hub=False, cwd=stock_root.parent)
cj = check_json(out)
t.ok("--check from the stock folder with its .env: not read (no file at all)", code == 0 and cj["env_files"] is None
     and cj["mode"] == "paper" and cj["ignored_keys"] == [], out[-800:])
code, out, _, _ = child(stock_root, "check", {"TZDIR": "/nonexistent"}, hub=False)
cj = check_json(out)
t.ok("timezone: no system zoneinfo → tzdata's zone file, still +07", code == 0 and cj["tz"] == "tzdata"
     and cj["utc_offset"] == 25200 and "tzdata" in out, out[-600:])
code, out, _, _ = child(stock_root, "check", {"TIMEZONE": "Mars/Base"}, hub=False)
t.ok("timezone: an unknown zone → refused (78)", code == 78 and "múi giờ Mars/Base" in out, out[-400:])
code, out, _, _ = child(stock_root, "check", {"HUB_FD": "notanumber"}, hub=False)
t.ok("--check: a bad HUB_FD is no pipe, not a crash", code == 0 and check_json(out)["hub_fd"] is False)

# ── 5. precedence with bot.env, masked values, and the stub run (guards) ──
botenv = ("INSTANCE_NAME=crypto\nPUBLIC_URL=https://okx.old\nDATA_DIR=/srv/old\nPORT=8080\nDASHBOARD_HOST=0.0.0.0\n"
          "LEVERAGE=7\nDATABASE_URL=postgresql://botenv:pw@db.botenv:5432/postgres\nMODE=live\n"
          "I_UNDERSTAND_THE_RISK=no\nOKX_API_KEY=k-botenv\nOKX_API_SECRET=s-botenv\nOKX_PASSPHRASE=p-botenv\n"
          "OPENROUTER_API_KEY=__BOTKEEP_KEEP_SECRET_OPENROUTER_API_KEY__\nTELEGRAM_TOKEN=x\n")
alt = tree("prec", bot_env=botenv)
panel = {"ALT_LEVERAGE": "5", "ALT_DATABASE_URL": "postgresql://panel:pw@db.panel:5432/postgres",
         "ALT_OPENROUTER_API_KEY": "sk-or-panel-key"}
fake_pg = json.dumps({"tables": ["kv", "trades", "heartbeats"], "funded": True})
code, out, res, frames = child(alt, "guards", {**ah.child_env(panel, ah.file_values(alt))[0], "FAKE_PG": fake_pg},
                               "--port", "5555", "--data-dir", str(TMP / "prec-data"), "--instance", "botkeep",
                               "--public-url", "https://h/altcoin")
c = (res or {}).get("cfg", {})
t.ok("precedence: instance, public URL, data folder and port from argv (bot.env's ignored, WARNING)", code == 0
     and c.get("INSTANCE_NAME") == "botkeep" and c.get("PUBLIC_URL") == "https://h/altcoin"
     and c.get("DATA_DIR") == str((TMP / "prec-data").resolve()) and c.get("DASHBOARD_PORT") == 5555
     and res["env"]["DASHBOARD_HOST"] == "127.0.0.1" and res["env"]["PORT"] is None
     and "INSTANCE_NAME" in out and "PUBLIC_URL" in out, out[-1500:])
t.ok("precedence: the other parameters from bot.env win (LEVERAGE 7 over ALT_LEVERAGE 5)", c.get("LEVERAGE") == 7)
t.ok("precedence: the panel's DATABASE_URL wins over bot.env's (WARNING)", c.get("DATABASE_URL") == panel["ALT_DATABASE_URL"]
     and res["env"]["DATABASE_URL"] == panel["ALT_DATABASE_URL"] and "DATABASE_URL: dùng giá trị trên panel" in out)
t.ok("precedence: MODE=live + keys + I_UNDERSTAND_THE_RISK in bot.env → paper, no key, no risk flag",
     c.get("MODE") == "paper" and not any(c.get(k) for k in ("OKX_API_KEY", "OKX_API_SECRET", "OKX_PASSPHRASE"))
     and res["env"]["I_UNDERSTAND_THE_RISK"] is None and res["env"]["OKX_API_KEY"] is None)
t.ok("masked value in bot.env dropped (panel value kept), reported in dropped_secrets with a WARNING",
     c.get("OPENROUTER_API_KEY") == "sk-or-panel-key" and "OPENROUTER_API_KEY" in out.split("Bỏ giá trị bị che")[1][:200]
     and frames and frames[0]["dropped_secrets"] == ["OPENROUTER_API_KEY"], frames[:1])
t.ok("Postgres guards: read only through information_schema, sslmode=require as the bot connects",
     res.get("pg_url_sslmode") is True and any("information_schema.tables" in s for s in res.get("pg_sql", []))
     and not any(re.match(r"\s*(INSERT|UPDATE|DELETE|CREATE|DROP|ALTER)", s, re.I) for s in res.get("pg_sql", [])))
t.ok("in the child: HUB_FD, HUB_TOKEN, HUB_CMD_TOKEN out of os.environ, the pipe not inheritable",
     res["hub_env_left"] == [] and res.get("inheritable") is False)
t.ok("...and a grandchild process (close_fds=False) does not get the pipe", res.get("grandchild_sees_fd") is False)
t.ok("state frame: the schema of §7.4", frames and set(frames[0]) == {"t", "state", "reason", "leader", "mode", "version",
     "build", "instance", "seam_ok", "ignored_keys", "dropped_secrets"} and frames[0]["build"] == ALT_BUILD
     and frames[0]["ignored_keys"] == ["TELEGRAM_TOKEN"], frames[:1])

# ── 6. the live matrix, run to a stub Bot() ──
def stub_run(name, panel, bot_env="", data=None, beats=(), extra=()):
    alt = tree(f"live-{name}", bot_env=bot_env)
    gate_mode, _ = ah.live_gate(panel, "crypto")
    data = alt_db(TMP / f"live-{name}-data", beats=beats) if data is None else data
    env = ah.child_env(panel, ah.file_values(alt))[0]
    return child(alt, "stub", env, "--mode", gate_mode, "--instance", "crypto", "--data-dir", str(data), *extra)


code, out, res, _ = stub_run("all", live, "MODE=paper\nI_UNDERSTAND_THE_RISK=no\nOKX_API_KEY=k-botenv\n")
t.ok("live matrix: every condition (bot.env says paper and I_UNDERSTAND_THE_RISK=no) → LiveEngine, both checks of "
     "main() pass, the panel's keys", code == 0 and res and res.get("ran") and res["engine"] == "LiveEngine"
     and res["cfg"]["MODE"] == "live" and res["env"]["I_UNDERSTAND_THE_RISK"] == "yes"
     and res["cfg"]["OKX_API_KEY"] == "k-panel" and res["cfg"]["OKX_PASSPHRASE"] == "p-panel", out[-1500:])
t.ok("live matrix: bot.main.Telegram is HubChannel before Bot()", res and res["telegram_is_hub"] is True)
for name, panel_case, bot_env in (
        ("botenv", {}, "MODE=live\nI_UNDERSTAND_THE_RISK=yes\nOKX_API_KEY=k\nOKX_API_SECRET=s\nOKX_PASSPHRASE=p\n"),
        ("norisk", {k: v for k, v in live.items() if k != "ALT_I_UNDERSTAND_THE_RISK"}, ""),
        ("noack", {k: v for k, v in live.items() if k != "ALT_LIVE_ACK"}, ""),
        ("keysinfile", {k: v for k, v in live.items() if not k.startswith("ALT_OKX")},
         "OKX_API_KEY=k\nOKX_API_SECRET=s\nOKX_PASSPHRASE=p\n")):
    refused = ah.live_gate(panel_case, "crypto")[1] is not None     # ALT_MODE=live: the supervisor starts nothing
    code, out, res, _ = stub_run(name, panel_case, bot_env)
    t.ok(f"live matrix: {name} → " + ("not started (supervisor); --mode paper anyway: " if refused else "")
         + "PaperEngine, no OKX key", refused == (name != "botenv") and code == 0 and res
         and res["engine"] == "PaperEngine" and res["cfg"]["MODE"] == "paper" and not res["cfg"]["OKX_API_KEY"]
         and res["env"]["I_UNDERSTAND_THE_RISK"] is None, out[-800:])
# open trades of the other mode: paper would close live trades on paper (the OKX positions left unmanaged),
# live would manage paper trades as real ones
for name, panel_case, trades, want in (
        ("paper-on-live", {}, [("live", "open"), ("paper", "closed")], hosted.OPEN_OTHER["paper"]),
        ("live-on-paper", live, [("paper", "open"), ("live", "open")], hosted.OPEN_OTHER["live"]),
        ("live-on-live", live, [("live", "open"), ("paper", "closed")], None)):
    data = alt_db(TMP / f"live-{name}-data", trades=trades)
    code, out, res, frames = stub_run(name, panel_case, data=data)
    if want:
        t.ok(f"live matrix: {name} (open trades of the other mode) → 78 before Bot(), the reason in the last frame",
             code == 78 and res is None and frames and frames[-1]["state"] == "disabled"
             and frames[-1]["reason"] == want, (code, frames[-1:], out[-600:]))
    else:
        t.ok(f"live matrix: {name} (open trades of this mode) → runs, LiveEngine", code == 0 and res
             and res["engine"] == "LiveEngine", out[-600:])
code, out, res, frames = stub_run("beat", live, beats=[("crypto", 5, "old-server")])
t.ok("live matrix: another instance alive in heartbeats → 75, even with every live condition", code == 75 and res is None
     and frames and frames[-1]["state"] == "waiting" and "crypto@old-server" in frames[-1]["reason"], out[-800:])
alt = tree("rival")
code, out, res, frames = child(alt, "rival", {}, "--instance", "crypto", "--data-dir", str(alt_db(TMP / "rival-data")))
states = [f for f in frames if f["t"] == "state"]
t.ok("while running: 'crypto' alive on another host (the old server started again: same leader lock) → ERROR, a "
     "last frame 'waiting' with crypto@old-server, SIGTERM to itself (bot.main's loop stops), exit 75",
     code == 75 and res and res.get("stopped_by_sigterm") and states and states[-1]["state"] == "waiting"
     and "crypto@old-server" in (states[-1]["reason"] or "") and "cũng chạy trên host khác" in out,
     (code, states[-2:], out[-800:]))
alt = tree("lastword")
code, out, res, frames = child(alt, "lastword", {"HUB_DROPPED": "ALT_OKX_API_SECRET,ALT_NINEROUTER_API_KEY"},
                               "--instance", "crypto", "--data-dir", str(alt_db(TMP / "lastword-data")))
notes = [f["text"] for f in frames if f["t"] == "notify"]
t.ok("the bot's last notifications, still queued when main() returns (a slow pipe): all written before exit",
     code == 0 and notes == [f"📉 ĐÓNG LỆNH {i}" for i in range(5)], (code, notes, out[-600:]))
states = [f for f in frames if f["t"] == "state"]
t.ok("HUB_DROPPED (the ALT_ keys the stock dropped, names only): out of the environment, in dropped_secrets",
     res and res["hub_env_left"] == [] and states and all(
         {"ALT_OKX_API_SECRET", "ALT_NINEROUTER_API_KEY"} <= set(f["dropped_secrets"]) for f in states), states[-1:])
alt = tree("live-nokey")
code, out, res, frames = child(alt, "stub", {}, "--mode", "live", "--instance", "crypto",
                               "--data-dir", str(alt_db(TMP / "live-nokey-data")))
t.ok("child: --mode live without the panel's keys → 78 before main() (never its sys.exit(1))", code == 78
     and res is None and frames[-1]["state"] == "disabled" and "ALT_OKX_API_KEY" in frames[-1]["reason"], out[-600:])

# ── 7. the database guards ──
def guard_run(name, data, *extra, env=None):
    alt = tree(f"g-{name}")
    return child(alt, "guards", env or {}, "--instance", "crypto", "--data-dir", str(data), *extra)


code, out, res, frames = guard_run("empty", TMP / "g-empty-data")
t.ok("empty database (no file) → 78 'DB rỗng – chép DB cũ hoặc đặt ALTCOIN_FRESH_START=1', a last state frame",
     code == 78 and frames and frames[-1]["state"] == "disabled"
     and frames[-1]["reason"] == "DB rỗng – chép DB cũ hoặc đặt ALTCOIN_FRESH_START=1", out[-600:])
code, out, res, frames = guard_run("emptykv", alt_db(TMP / "g-emptykv-data", funded=False))
t.ok("empty database (kv without start_equity/paper_balance) → 78", code == 78)
code, out, res, frames = guard_run("fresh", TMP / "g-fresh-data", "--allow-empty-db")
t.ok("ALTCOIN_FRESH_START (--allow-empty-db) → starts", code == 0 and res is not None, out[-600:])
code, out, res, frames = guard_run("fresh-live", TMP / "g-fresh-live-data", "--allow-empty-db", "--mode", "live",
                                   env={"OKX_API_KEY": "k", "OKX_API_SECRET": "s", "OKX_PASSPHRASE": "p"})
t.ok("ALTCOIN_FRESH_START in live → 78: an empty database is for paper only", code == 78 and res is None and frames
     and frames[-1]["reason"].endswith("(chỉ cho paper)"), out[-600:])
marked = TMP / "g-marked-data"
marked.mkdir()
(marked / hosted.FUNDED).touch()
code, out, res, frames = guard_run("marked", marked, "--allow-empty-db")
t.ok("ALTCOIN_FRESH_START left on, then an empty database where one with data ran (hosted.funded) → 78 'DB bị đổi'",
     code == 78 and res is None and "DB bị đổi" in frames[-1]["reason"], out[-600:])
code, out, res, frames = guard_run("fresh-funded", alt_db(TMP / "g-fresh-funded-data"), "--allow-empty-db")
t.ok("ALTCOIN_FRESH_START on a database with data → starts, WARNING 'không còn tác dụng'", code == 0 and res is not None
     and "không còn tác dụng" in out, out[-600:])
hot = alt_db(TMP / "g-hot-data", tables=("junk",))
with contextlib.closing(sqlite3.connect(hot / "bot.db")) as con:
    con.executemany("INSERT INTO junk VALUES(?)", [("a" * 500,)] * 200)
    con.commit()
subprocess.run([PY, "-c", "import os, signal, sqlite3, sys; con = sqlite3.connect(sys.argv[1], isolation_level=None); "
                "con.execute('PRAGMA cache_size=1'); con.execute('BEGIN'); con.execute(\"UPDATE junk SET x = 'b' || x\"); "
                "os.kill(os.getpid(), signal.SIGKILL)", str(hot / "bot.db")], check=False)
try:                                              # the journal is hot: a read-only connection cannot roll it back
    with contextlib.closing(sqlite3.connect(f"{(hot / 'bot.db').as_uri()}?mode=ro", uri=True)) as con:
        con.execute("SELECT name FROM sqlite_master").fetchall()
    hot_ro = "opened"
except sqlite3.OperationalError as exc:
    hot_ro = str(exc)
code, out, res, frames = guard_run("hot", hot)
with contextlib.closing(sqlite3.connect(hot / "bot.db")) as con:
    changed = con.execute("SELECT count(*) FROM junk WHERE x LIKE 'b%'").fetchone()[0]
t.ok("a hot journal (the bot killed mid-write: OOM, SIGKILL after STOP_WAIT) → rolled back, starts; never a crash loop",
     hot_ro == "attempt to write a readonly database" and code == 0 and res is not None and changed == 0
     and not (hot / "bot.db-journal").exists(), (hot_ro, code, out[-600:]))
code, out, res, frames = guard_run("stocktables", alt_db(TMP / "g-stock-data", tables=("market", "fills")))
t.ok("the stock's tables (market, fills) in the altcoin's SQLite → 78", code == 78
     and "fills, market" in frames[-1]["reason"], frames[-1:])
pg_stock = json.dumps({"tables": ["kv", "market", "events"], "funded": True})
code, out, res, frames = guard_run("pgstock", TMP / "g-pg-data", env={"DATABASE_URL": "postgresql://a:b@pooler:6543/postgres",
                                                                      "FAKE_PG": pg_stock})
t.ok("Postgres with the stock's table market (same database by another endpoint) → 78", code == 78
     and "market" in frames[-1]["reason"], out[-600:])
stock_url = "postgresql://postgres.ref:pw@db.ref.supabase.co:5432/postgres"
code, out, res, frames = guard_run("pgdeny", TMP / "g-deny-data", "--deny-db", ah.db_fingerprint(stock_url.replace("pw", "x")),
                                   env={"DATABASE_URL": stock_url + "?sslmode=require", "FAKE_PG": pg_stock})
t.ok("the stock's DATABASE_URL (another password/query) → 78 before any connection", code == 78
     and "DB của Cổ phiếu" in frames[-1]["reason"] and res is None and "pg_sql" not in out, out[-600:])
pg_ok = json.dumps({"tables": ["kv", "trades", "heartbeats"], "funded": True,
                    "beats": [["render", time.time() - 10, "v3.2.0 paper=True host=srv-render"]]})
code, out, res, frames = guard_run("pgbeat", TMP / "g-pgbeat-data", env={"DATABASE_URL": "postgresql://a:b@h/db",
                                                                        "FAKE_PG": pg_ok})
t.ok("Postgres: Render's heartbeat still alive → 75 'render@srv-render'", code == 75
     and "render@srv-render" in frames[-1]["reason"], frames[-1:])
pg_live = json.dumps({"tables": ["kv", "trades", "heartbeats"], "funded": True, "open_modes": ["live"]})
code, out, res, frames = guard_run("pglive", TMP / "g-pglive-data", env={"DATABASE_URL": "postgresql://a:b@h/db",
                                                                        "FAKE_PG": pg_live})
t.ok("Postgres: an open live trade, paper asked → 78", code == 78 and frames[-1]["reason"] == hosted.OPEN_OTHER["paper"],
     frames[-1:])
for name, beats, want in (("othername", [("render", 10, "srv")], 75), ("otherhost", [("crypto", 10, "old-host")], 75),
                          ("samehost", [("crypto", 10, HOST)], 0), ("stale", [("render", 200, "srv")], 0)):
    code, out, res, frames = guard_run(name, alt_db(TMP / f"g-{name}-data", beats=beats))
    t.ok(f"one instance: heartbeat {name} → {'75' if want else 'starts'}", code == want, out[-500:])
lock_data = alt_db(TMP / "g-lock-data")
holder = subprocess.Popen([PY, "-c", "import fcntl, sys, time; f = open(sys.argv[1], 'a'); "
                                     "fcntl.flock(f, fcntl.LOCK_EX); print('locked', flush=True); time.sleep(600)",
                           str(lock_data / "hosted.lock")], stdout=subprocess.PIPE, text=True)
holder.stdout.readline()
code, out, res, frames = guard_run("lock", lock_data)
t.ok("one instance: hosted.lock held by another process → 75, even with the same name on the same host",
     code == 75 and "hosted.lock" in frames[-1]["reason"], out[-500:])
holder.kill()
holder.wait()
code, out, res, frames = guard_run("lock2", lock_data)
t.ok("...the holder SIGKILLed: the lock is free, the next start runs", code == 0 and res is not None, out[-500:])

# ── 8. a real Bot(): HubChannel, no Telegram/Zoom address, the seam checked in effect ──
alt = tree("real", bot_env=("TELEGRAM_TOKEN=987654321:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawQ\nTELEGRAM_CHAT_ID=-100\n"
                            "ZOOM_WEBHOOK_URL=https://integrations.zoom.us/chat/webhooks/x\nZOOM_WEBHOOK_TOKEN=w\n"
                            "ZOOM_CLIENT_ID=c\nZOOM_CLIENT_SECRET=s\nZOOM_BOT_JID=b@xmpp.zoom.us\n"))
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    free = s.getsockname()[1]
code, out, res, frames = child(alt, "real", {}, "--port", str(free), "--data-dir", str(TMP / "real-data"),
                               "--allow-empty-db", timeout=180)
res = res or {}
t.ok("real Bot(): bot.tg is HubChannel and in notify.channels; Zoom off (its keys emptied)", code == 0
     and res.get("tg") == "HubChannel" and res.get("tg_in_channels") is True and res.get("zoom_on") is False, out[-2000:])
reached = [h for h in res.get("lookups", []) + res.get("requests", []) if "telegram" in h or "zoom" in h]
t.ok("real Bot(): no lookup or request to api.telegram.org / api.zoom.us", "lookups" in res and not reached, reached)
notes = [f for f in frames if f["t"] == "notify"]
states = [f for f in frames if f["t"] == "state"]
t.ok("pipe: the notification '👑 … tiếp quản' arrives as a notify frame (n, ts, text)", notes
     and "tiếp quản" in notes[0]["text"] and set(notes[0]) == {"t", "n", "ts", "text"} and notes[0]["n"] == 1, notes[:1])
t.ok("pipe: state frames say running, leader, seam_ok true", any(f["state"] == "running" and f["leader"]
                                                                  and f["seam_ok"] is True for f in states), states[-3:])
t.ok("seam checked in effect: bot.tg replaced by another class → seam_ok false (ERROR logged)",
     states and states[-1]["seam_ok"] is False and "Seam kênh hỏng" in out, states[-1:])
t.ok("once running: <DATA_DIR>/hosted.funded written (an empty database there is refused from now on)",
     (TMP / "real-data" / hosted.FUNDED).is_file())

# ── 9. the hub in this process: limits, newest state first, a closed pipe ──
class FakeBot:
    is_leader = False


read_end, write_end = os.pipe()
hub = hosted.Hub(write_end, {"mode": "paper", "version": "3.2.0", "build": "380dfca", "instance": "x",
                             "ignored_keys": [], "dropped_secrets": []})
hosted.HUB = hub
hosted.STATE_EVERY = 0.05
bot = FakeBot()
channel = hosted.HubChannel(bot)
for i in range(300):
    channel.send(f"{i:03d} " + "x" * 1000)
t.ok("HubChannel.send never blocks: 200 queued, the rest dropped and counted", hub.queue.qsize() == 200
     and hub.dropped == 100)
threading.Thread(target=hub.run, daemon=True).start()
time.sleep(0.5)                                  # the pipe (64 KiB) is full: the writer waits
bot.is_leader = True
lines, buf = [], b""
deadline = time.monotonic() + 15                 # a broken hub fails here, never hangs the suite
with open(read_end, "rb", buffering=0) as pipe:
    while sum(1 for x in lines if x["t"] == "notify") < 200 and time.monotonic() < deadline:
        if select.select([pipe], [], [], 0.5)[0]:
            buf += pipe.read(65536)
        *done, buf = buf.split(b"\n")
        lines += [json.loads(x) for x in done]
first_leader = next((i for i, x in enumerate(lines) if x["t"] == "state" and x["leader"]), len(lines))
before = sum(1 for x in lines[:first_leader] if x["t"] == "notify")
t.ok("300 notifications while the parent does not read: the newest state still goes before the waiting ones",
     0 < before <= 80 and len(lines) - first_leader > 100, (before, len(lines)))
t.ok("notify frames in order, numbered", [x["text"][:3] for x in lines if x["t"] == "notify"] == [f"{i:03d}" for i in range(200)])

read_end, write_end = os.pipe()
got = []
drain = threading.Thread(target=lambda: got.append(open(read_end, "rb").read()), daemon=True)
drain.start()
hub2 = hosted.Hub(write_end, {})
text = ("ệ🚀" * 6000)[:12000]
hub2.write({"t": "notify", "n": 1, "ts": 0, "text": text})
hub2.write({"t": "notify", "n": 2, "ts": 0, "text": "Đ" * 100000})
os.close(write_end)
drain.join(10)
raw = got[0].split(b"\n")[:-1]
t.ok("a 12,000-character Vietnamese + emoji notification: one frame ≤ 60 KiB, whole (> 64 KiB as ASCII JSON)",
     len(raw[0]) + 1 <= 60 * 1024 and json.loads(raw[0])["text"] == text
     and len(json.dumps({"text": text})) > 64 * 1024, len(raw[0]))
t.ok("a 100,000-character one: cut by UTF-8 bytes to ≤ 60 KiB, still valid JSON, ends with …",
     len(raw[1]) + 1 <= 60 * 1024 and json.loads(raw[1])["text"].endswith("…"), len(raw[1]))

read_end, write_end = os.pipe()
os.close(read_end)
hub3 = hosted.Hub(write_end, {})
hosted.HUB = hub3
try:
    hub3.write({"t": "notify", "n": 1, "ts": 0, "text": "x"})
    hub3.write({"t": "notify", "n": 2, "ts": 0, "text": "y"})
    hosted.HubChannel(FakeBot()).send("z")
    t.ok("the stock gone (pipe closed): no exception, the hub stops writing", hub3.fd is None)
except Exception as exc:  # noqa: BLE001
    t.ok("the stock gone (pipe closed): no exception, the hub stops writing", False, repr(exc))
hosted.HUB = hosted.Hub(None, {})
hosted.HubChannel(FakeBot()).send("no pipe")
hosted.HUB.write({"t": "notify", "n": 1, "ts": 0, "text": "no pipe"})
t.ok("no HUB_FD: HubChannel takes the text, nothing written (Notifier logs it)", hosted.HUB.queue.qsize() == 1)


class FlakyBot:
    """Its first look raises: an error in one turn of the hub."""
    looks = 0

    @property
    def is_leader(self):
        FlakyBot.looks += 1
        if FlakyBot.looks == 1:
            raise RuntimeError("boom")
        return False


read_end, write_end = os.pipe()
hub4 = hosted.HUB = hosted.Hub(write_end, {})
channel = hosted.HubChannel(FlakyBot())
runner = threading.Thread(target=hub4.run, daemon=True)
runner.start()
channel.send("🧠 TỔNG KẾT " + json.loads('"\\ud83d x"'))      # an AI reply cut in the middle of an emoji
channel.send("🛑 DỪNG GIAO DỊCH")
texts, buf = [], b""
deadline = time.monotonic() + 10
with open(read_end, "rb", buffering=0) as pipe:
    while len(texts) < 2 and time.monotonic() < deadline:
        if select.select([pipe], [], [], 0.2)[0]:
            buf += pipe.read(65536)
        *done, buf = buf.split(b"\n")
        texts += [f["text"] for f in map(json.loads, done) if f["t"] == "notify"]
    t.ok("a lone surrogate in a notification, an error in a turn: the hub thread lives on, the error counted, "
         "the next notification arrives", runner.is_alive() and hub4.errors == 1
         and texts == ["🧠 TỔNG KẾT ? x", "🛑 DỪNG GIAO DỊCH"], (texts, hub4.errors))


class BeatStore:
    def __init__(self, rows):
        self.rows = rows

    def instances(self):
        if self.rows is None:
            raise RuntimeError("DB down")
        return self.rows


def beat(name, host, age=5.0):
    return {"instance": name, "ts": time.time() - age, "role": "leader", "info": f"v3.2.0 b380dfca paper=True host={host}"}


hosted.RIVAL_EVERY = 0
for name, rows, built, want in (
        ("this name on another host", [beat("crypto", "old-server")], True, "crypto@old-server"),
        ("this name on this host", [beat("crypto", HOST)], True, None),
        ("another name alive (the leader lock keeps one trader)", [beat("render", "srv"), beat("crypto", HOST)], True, None),
        ("this name on another host, older than LEADER_TTL", [beat("crypto", "old-server", 200)], True, None),
        ("database error", None, True, None),
        ("bot not built yet", [beat("crypto", "old-server")], False, None)):
    hub5 = hosted.Hub(None, {"instance": "crypto"}, ttl=90)
    hub5.bot = type("B", (), {"store": BeatStore(rows), "start_equity": 10.0 if built else None})()
    t.ok(f"rival(): {name} → {want}", hub5.rival() == want)
hosted.RIVAL_EVERY = 60
hub5 = hosted.Hub(None, {"instance": "crypto"})
hub5.bot = type("B", (), {"store": BeatStore([beat("crypto", "old-server")]), "start_equity": 10.0})()
t.ok("rival(): heartbeats read at most every RIVAL_EVERY s", hub5.rival() == "crypto@old-server" and hub5.rival() is None)
hosted.RIVAL_EVERY = 15.0

# the parent's reader: a line > 64 KiB dropped, bad JSON / unknown frames dropped, then still in sync
sup = ah.Supervisor(TMP / "reader", environ={"ALTCOIN_ENABLED": "1"})
read_end, write_end = os.pipe()
reader = threading.Thread(target=sup._read_hub, args=(read_end,), daemon=True)
with contextlib.redirect_stdout(io.StringIO()) as printed:
    reader.start()
    for chunk in (b"x" * (70 * 1024) + b"\n", b"not json\n", b'{"t":"other"}\n', b"[1,2]\n",
                  (json.dumps({"t": "notify", "n": 1, "ts": 0, "text": text}, ensure_ascii=False) + "\n").encode(),
                  json.dumps({"t": "state", "state": "running", "leader": True, "mode": "paper", "version": "3.2.0",
                              "build": "380dfca", "seam_ok": True, "ignored_keys": ["TELEGRAM_TOKEN"],
                              "dropped_secrets": []}).encode() + b"\n"):
        os.write(write_end, chunk)
    os.close(write_end)
    reader.join(10)
st = sup.state()
t.ok("parent: a line > 64 KiB, bad JSON, an unknown frame dropped and counted; the next frames still read",
     st["bridge"]["dropped"] == 4 and st["bridge"]["notified"] == 1 and st["leader"] is True
     and st["build"] == "380dfca", st["bridge"])
t.ok("parent: a notification is printed '[altcoin-notify] …', whole", printed.getvalue().count("[altcoin-notify] ") == 1
     and text in printed.getvalue())

# ── 10. masking on the parent's side ──
os.environ.update(ALT_ZOOM_CLIENT_SECRET="panel-zoom-secret-42", ALT_DATABASE_URL="postgresql://alt:alt-db-pw-77@h/db",
                  ALT_OKX_API_SECRET="panel-okx-secret-43")
eventlog.refresh_secrets()
eventlog.add_secret_values(["file-okx-token-44", "9router", "postgresql://u:file-db-pw-45@h/db"])
masked = eventlog.redact("a panel-zoom-secret-42 b alt-db-pw-77 c panel-okx-secret-43 d file-okx-token-44 "
                         "e file-db-pw-45 f 9router")
t.ok("redact: ALT_ secrets (the ignored ALT_ZOOM_* too), the password of ALT_DATABASE_URL alone, added values",
     masked == "a *** b *** c *** d *** e *** f 9router", masked)
eventlog.add_secret_values(["hub-token-start-1a", "cmd-token-start-1b"], slot="altcoin-hub")
eventlog.add_secret_values(["hub-token-start-2a", "cmd-token-start-2b"], slot="altcoin-hub")
eventlog.add_secret_values(["file-okx-token-47"])
masked = eventlog.redact("hub-token-start-1a cmd-token-start-2b file-okx-token-44 file-okx-token-47 panel-okx-secret-43")
t.ok("add_secret_values: a slot keeps its latest values only (the running child's tokens), the rest still masked",
     masked == "hub-token-start-1a *** *** *** ***", masked)

# ── 11. the supervisor with a fake child ──
FAKE = TMP / "fake" / "hosted.py"
FAKE.parent.mkdir()
(FAKE.parent / "bot.env").write_text("TELEGRAM_TOKEN=987654321:old-okx-bot-token-in-bot-env\n"
                                     "ZOOM_CLIENT_SECRET=old-okxbot-zoom-secret-46\nLEVERAGE=3\n", encoding="utf-8")
FAKE.write_text(r'''
import json, os, signal, sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer
args = sys.argv[1:]
port = int(args[args.index("--port") + 1])
fd = int(os.environ["HUB_FD"])
log = os.environ["FAKE_LOG"]
def note(text):
    try:
        with open(log, "a") as f:
            f.write(text + "\n")
    except OSError:
        pass
note(f"start {os.getpid()} {port} " + json.dumps({"env": sorted(os.environ), "args": args, "cwd": os.getcwd(),
                                                   "dropped": os.environ.get("HUB_DROPPED")}))
behave = os.environ.get("FAKE", "ok")
if behave.startswith("seq:"):
    steps = behave[4:].split(",")
    started = sum(1 for line in open(log) if line.startswith("start "))
    behave = steps[min(started, len(steps)) - 1]
def frame(**f):
    os.write(fd, (json.dumps(f, ensure_ascii=False) + "\n").encode())
base = dict(leader=False, mode="paper", version="9.9.9", build="abcdef0", instance="x", seam_ok=None,
            ignored_keys=["TELEGRAM_TOKEN"], dropped_secrets=[])
frame(t="state", state="starting", reason=None, **base)
print("leak " + os.environ.get("LEAK", "") + " hub " + os.environ["HUB_TOKEN"] + " " + os.environ["HUB_CMD_TOKEN"], flush=True)
if behave == "exit78":
    frame(t="state", state="disabled", reason="DB rỗng – thử", **base); sys.exit(78)
if behave == "exit75":
    frame(t="state", state="waiting", reason="instance khác đang chạy: crypto@old", **base); sys.exit(75)
if behave == "exit1":
    sys.exit(1)
if behave == "hang":
    time.sleep(600)
if behave == "ignore_term":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
elif behave == "lastword":                    # a trade closed as it stops: the notice written, then exit
    signal.signal(signal.SIGTERM, lambda *a: (frame(t="notify", n=2, ts=time.time(), text="📉 ĐÓNG LỆNH cuối"),
                                              os._exit(0)))
else:
    signal.signal(signal.SIGTERM, lambda *a: (note(f"sigterm {os.getpid()}"), os._exit(0)))
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b'{"ok": true}')
    def log_message(self, *a):
        pass
server = HTTPServer(("127.0.0.1", port), H)
frame(t="state", state="running", reason=None, **{**base, "leader": True, "seam_ok": True})
frame(t="notify", n=1, ts=time.time(), text="🚀 khởi động")
server.serve_forever()
''', encoding="utf-8")
ah.READY_TIMEOUT, ah.READY_POLL, ah.WATCH_EVERY, ah.WATCH_TIMEOUT, ah.WATCH_MISSES = 3, 0.1, 0.3, 0.5, 2
ah.STOP_WAIT, ah.BACKOFF_START, ah.BACKOFF_MAX, ah.WAITING_EXTRA = 1.5, 0.3, 1.2, 0
SUPS = []


class Recording(ah.Supervisor):
    """Every wait recorded as (seconds, state, reason); none longer than 0.05 s."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.waits = []

    def _wait(self, seconds):
        self.waits.append((seconds, self._snap["state"], self._snap["reason"]))
        super()._wait(min(seconds, 0.05))


def supervisor(name, fake="ok", cls=ah.Supervisor, **extra):
    log = TMP / f"{name}.log"
    environ = {**stock, "ALTCOIN_ENABLED": "1", "ALT_FAKE": fake, "ALT_FAKE_LOG": str(log), "ALT_LEADER_TTL": "1",
               "ALT_MODE": "", "ALT_DATA_DIR": "", "ALT_INSTANCE_NAME": "", "PUBLIC_URL": "https://h", **extra}
    sup = cls(TMP / f"{name}-data", "https://h", environ=environ, hosted=FAKE)
    SUPS.append(sup)
    return sup, log


def starts(log):
    return [line.split(" ", 3) for line in log.read_text().splitlines() if line.startswith("start ")] \
        if log.exists() else []


def alive(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split(") ")[1][0] != "Z"
    except OSError:
        return False


with contextlib.redirect_stdout(io.StringIO()):
    sup, log = supervisor("ok", ALT_LEAK="panel-zoom-secret-42 old-okxbot-zoom-secret-46")
    sup.start()
    up = wait_for(lambda: sup.state()["state"] == "running" and sup.state()["seam_ok"] is True, 15)
st = sup.state()
first = starts(log)[0]
meta = json.loads(first[3])
t.ok("fake child: running once its /health answers; the state frame merged (version, build, leader, seam_ok)", up
     and st["version"] == "9.9.9" and st["build"] == "abcdef0" and st["leader"] is True and st["pid"] == int(first[1]), st)
t.ok("spawn: absolute hosted.py, cwd = its folder, sealed argv (port, parent pid, data dir, mode, instance, deny-db)",
     meta["cwd"] == str(FAKE.parent) and meta["args"][meta["args"].index("--port") + 1] == first[2]
     and meta["args"][meta["args"].index("--parent-pid") + 1] == str(os.getpid())
     and meta["args"][meta["args"].index("--data-dir") + 1] == str((TMP / "ok-data" / "altcoin").resolve())
     and meta["args"][meta["args"].index("--public-url") + 1] == "https://h/altcoin"
     and meta["args"][meta["args"].index("--instance") + 1] == "botkeep"
     and meta["args"][meta["args"].index("--deny-db") + 1] == ah.db_fingerprint(stock["DATABASE_URL"])
     and "--allow-empty-db" not in meta["args"], meta)
t.ok("spawn: the child's environment has HUB_FD/HUB_TOKEN/HUB_CMD_TOKEN and no stock secret",
     {"HUB_FD", "HUB_TOKEN", "HUB_CMD_TOKEN", "PYTHONIOENCODING"} <= set(meta["env"])
     and not [k for k in meta["env"] if k.startswith(("TELEGRAM_", "ZOOM_", "WEB_", "RENDER_")) or k == "MODE"], meta["env"])
t.ok("state(): ignored_keys = the panel's (ALT_…) and the child's; rss_mb of both processes",
     "ALT_TELEGRAM_TOKEN" in st["ignored_keys"] and "TELEGRAM_TOKEN" in st["ignored_keys"]
     and st["rss_mb"]["stock"] > 0 and (st["rss_mb"]["altcoin"] or 0) > 0, st)
t.ok("the notification printed and counted", st["bridge"]["notified"] == 1 and st["bridge"]["last_notify_ts"])
logged = "\n".join(sup.lines)
tokens = sup._tokens
t.ok("[altcoin] lines: the panel's ignored ALT_ZOOM_* value, bot.env's old secrets and this start's tokens masked",
     "leak *** *** hub *** ***" in logged and tokens[0] not in logged and "old-okxbot" not in logged, logged)
dump = json.dumps(sup.state()) + json.dumps(sup.health())
t.ok("state() and /health hold no token", tokens[0] not in dump and tokens[1] not in dump)
t.ok("health(): the state alone", sup.health() == {"altcoin": {"state": "running"}})
n_secrets = len(eventlog._secret_values())
pid = st["pid"]
with contextlib.redirect_stdout(io.StringIO()):
    os.kill(pid, signal.SIGKILL)
    again = wait_for(lambda: sup.state()["state"] == "running" and sup.state()["pid"] not in (None, pid), 15)
st = sup.state()
t.ok("kill -9 of the child: last_exit -9, started again (restarts 1)", again and st["last_exit"] == -9
     and st["restarts"] == 1 and len(starts(log)) == 2, st)
values = eventlog._secret_values()
t.ok("a new start masks its new tokens and drops the old ones: the list does not grow by start",
     sup._tokens[0] in values and sup._tokens[1] in values and tokens[0] not in values and len(values) == n_secrets,
     (len(values), n_secrets))
new_pid = st["pid"]
with contextlib.redirect_stdout(io.StringIO()):
    sup.stop()
    time.sleep(0.6)
t.ok("stop(): SIGTERM to the child, gone, no new start; state 'stopped'", f"sigterm {new_pid}" in log.read_text()
     and not alive(new_pid) and len(starts(log)) == 2 and sup.state()["state"] == "stopped", log.read_text()[-300:])

order = []
with contextlib.redirect_stdout(io.StringIO()):
    sup, log = supervisor("lastword", "lastword", ALT_OKX_API_SECRET="__BOTKEEP_KEEP_SECRET_ALT_OKX_API_SECRET__")
    sup.on_notify = lambda text: (time.sleep(0.3), order.append(("notify", text)))     # a slow bridge
    sup.drain = lambda timeout: order.append(("drain", timeout))
    sup.start()
    wait_for(lambda: ("notify", "🚀 khởi động") in order, 15)
    sup.stop()
    after = list(order)
t.ok("stop(): the child's last notification (written as it exits) read to the pipe's end, then drain(DRAIN_WAIT)",
     after[-2:] == [("notify", "📉 ĐÓNG LỆNH cuối"), ("drain", ah.DRAIN_WAIT)], after)
t.ok("spawn: HUB_DROPPED names the masked ALT_ keys (names only) for the banner of its pages",
     json.loads(starts(log)[0][3])["dropped"] == "ALT_OKX_API_SECRET")
with contextlib.redirect_stdout(io.StringIO()):
    sup, log = supervisor("e78", "exit78")
    sup.start()
    wait_for(lambda: sup.state()["state"] == "disabled", 10)
    time.sleep(0.8)
st = sup.state()
t.ok("exit 78: disabled with the child's reason, never started again", st["state"] == "disabled"
     and st["reason"] == "DB rỗng – thử" and st["last_exit"] == 78 and len(starts(log)) == 1, st)

ah.WAITING_EXTRA = 3
with contextlib.redirect_stdout(io.StringIO()):
    sup, log = supervisor("e75", "seq:exit75,exit1,ok", cls=Recording, ALT_LEADER_TTL="2")
    sup.start()
    ran = wait_for(lambda: sup.state()["state"] == "running", 15)
    sup.stop()
ah.WAITING_EXTRA = 0
slept = [w for w in sup.waits if w[0] >= ah.BACKOFF_START]
t.ok("exit 75: 'waiting' with <name>@<host> from the last frame, tried again after LEADER_TTL + WAITING_EXTRA (2 + 3 s)",
     ran and slept[:1] == [(5, "waiting", "instance khác đang chạy: crypto@old")] and len(starts(log)) == 3, slept)
t.ok("...and exit 75 leaves the backoff alone: the crash after it waits BACKOFF_START (0.3), not double",
     slept[1:2] == [(0.3, "backoff", "thoát mã 1")], slept)

with contextlib.redirect_stdout(io.StringIO()):
    log = TMP / "e1.log"
    sup = Recording(TMP / "e1-data", environ={**stock, "ALTCOIN_ENABLED": "1", "ALT_FAKE": "exit1", "ALT_FAKE_LOG": str(log),
                                             "ALT_MODE": ""}, hosted=FAKE)
    SUPS.append(sup)
    sup.start()
    wait_for(lambda: len(starts(log)) >= 5, 15)
    st = sup.state()
    sup.stop()
backoffs = [w for w, _, _ in sup.waits if w >= ah.BACKOFF_START][:4]
t.ok("other exit codes: backoff 20 s doubling up to the cap (here 0.3, 0.6, 1.2, 1.2)", backoffs == [0.3, 0.6, 1.2, 1.2]
     and st["last_exit"] == 1, (backoffs, st["state"], st["reason"]))

with contextlib.redirect_stdout(io.StringIO()):
    sup, log = supervisor("hang", "seq:hang,ok")
    sup.start()
    ok = wait_for(lambda: sup.state()["state"] == "running", 20)
    sup.stop()
ports = [s[2] for s in starts(log)]
t.ok("not ready in time: stopped, started again at once on another port", ok and len(ports) == 2 and ports[0] != ports[1]
     and sup.state()["restarts"] == 1, ports)

with contextlib.redirect_stdout(io.StringIO()):
    sup, log = supervisor("term", "ignore_term")
    sup.start()
    wait_for(lambda: sup.state()["state"] == "running", 15)
    pid = sup.state()["pid"]
    boot.health_extra = sup.health
    late = boot.LateApp()

    async def _get_health():
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)
        await late({"type": "http", "path": "/health", "method": "GET", "headers": []}, receive, send)
        return json.loads(sent[1]["body"])

    import asyncio

    stopper = threading.Thread(target=sup.stop)
    started_at = time.monotonic()
    stopper.start()
    time.sleep(0.2)
    slowest, during = 0.0, None
    for _ in range(5):
        t0 = time.monotonic()
        during = asyncio.run(_get_health())
        slowest = max(slowest, time.monotonic() - t0)
    stopper.join(10)
    took = time.monotonic() - started_at
t.ok("/health answers in < 100 ms while stop() waits for the child", slowest < 0.1 and during["ok"] is True, slowest)
t.ok("/health: the altcoin's state only", during.get("altcoin") == {"state": during["altcoin"]["state"]}
     and set(during) == {"ok", "booting", "version", "altcoin"}, during)
t.ok("stop(): a child that ignores SIGTERM is SIGKILLed after STOP_WAIT", not alive(pid) and took >= ah.STOP_WAIT, took)
boot.health_extra = None
t.ok("/health without the hook (ALTCOIN_ENABLED off): unchanged", "altcoin" not in asyncio.run(_get_health()))

# main_botkeep's handler: a real SIGTERM to the process stops the child first, then the process dies of it
HANDLER = TMP / "handler.py"
HANDLER.write_text(r'''
import json, os, sys, time
saved = dict(os.environ)
sys.path.insert(0, sys.argv[1])
import _common
os.environ["DATA_DIR"] = sys.argv[2]
import main_botkeep
from vnbot import altcoin_host as ah
ah.READY_POLL, ah.STOP_WAIT = 0.1, 3
sup = ah.Supervisor(sys.argv[2], environ=json.loads(sys.argv[4]), hosted=sys.argv[3])
main_botkeep._stop_on_signal(sup.stop)
sup.start()
end = time.monotonic() + 30
while sup.state()["state"] != "running" and time.monotonic() < end:
    time.sleep(0.05)
print("READY", sup.state()["pid"] or 0, flush=True)
for _ in range(600):        # short sleeps: a signal taken by another thread still reaches the handler soon
    time.sleep(0.1)
''', encoding="utf-8")
log = TMP / "handler.log"
proc = subprocess.Popen([PY, str(HANDLER), str(SUITES), str(TMP / "handler-data"), str(FAKE),
                         json.dumps({"PATH": os.environ.get("PATH", ""), "ALTCOIN_ENABLED": "1", "ALT_FAKE_LOG": str(log)})],
                        env={**BASE_ENV}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
line = ""
for line in proc.stdout:
    if line.startswith("READY"):
        break
pid = int(line.split()[1]) if line.startswith("READY") else 0
proc.send_signal(signal.SIGTERM)
try:
    code = proc.wait(20)
except subprocess.TimeoutExpired:
    proc.kill()
    code = proc.wait()
t.ok("main_botkeep: SIGTERM → the child is stopped (its SIGTERM), then the process ends by that signal", pid
     and code == -signal.SIGTERM and f"sigterm {pid}" in log.read_text(), (pid, code))
t.ok("...no orphan child left", pid and wait_for(lambda: not alive(pid), 5))

# ── 12. the checks before any start ──
sup = ah.Supervisor(TMP / "off-data", environ={**stock})
buf = io.StringIO()
handler = logging.StreamHandler(buf)
logging.getLogger("vnbot.altcoin_host").addHandler(handler)
logging.getLogger("vnbot.altcoin_host").setLevel(logging.INFO)
threads = threading.active_count()
sup.start()
t.ok("ALTCOIN_ENABLED unset: off, one log line, no thread", not sup.enabled and sup.state()["state"] == "off"
     and "Altcoin: tắt (ALTCOIN_ENABLED khác 1)" in buf.getvalue() and threading.active_count() == threads)
sup = ah.Supervisor(TMP / "same", environ={**stock, "ALTCOIN_ENABLED": "1", "ALT_DATA_DIR": str(TMP / "same")})
t.ok("ALT_DATA_DIR = the stock's DATA_DIR → disabled", "trùng DATA_DIR" in (sup._precheck() or ""))
real_find = ah.importlib.util.find_spec
ah.importlib.util.find_spec = lambda name, *a: None if name == "ccxt" else real_find(name, *a)
sup = ah.Supervisor(TMP / "nolib", environ={**stock, "ALTCOIN_ENABLED": "1", "ALT_MODE": ""}, hosted=FAKE)
sup.start()
wait_for(lambda: sup.state()["state"] == "disabled", 5)
ah.importlib.util.find_spec = real_find
t.ok("ccxt missing → disabled 'thiếu thư viện' with the pip command, nothing started", sup.state()["state"] == "disabled"
     and "pip install --no-cache-dir ccxt==4.5.90" in sup.state()["reason"], sup.state()["reason"])
from vnbot import store  # noqa: E402

shared = store.open_store(TMP / "stockdb")
shared.db.run("CREATE TABLE trades(x)")
sup = ah.Supervisor(TMP / "stockdb", environ={**stock, "ALTCOIN_ENABLED": "1"}, hosted=FAKE)
t.ok("the stock's database already has altcoin tables → disabled 'DB đang dùng chung'",
     "DB đang dùng chung" in (sup._precheck() or "") and "trades" in sup._precheck())
store.reset()
logging.getLogger("vnbot.altcoin_host").removeHandler(handler)

# ── 13. main_botkeep on an old Python: a plain message, exit 1 ──
main_src = _common.REPO / "main_botkeep.py"
done = subprocess.run([PY, "-c", "import sys; sys.version_info = (3, 11, 9, 'final', 0); "
                       f"exec(compile(open({str(main_src)!r}, encoding='utf-8').read(), 'main_botkeep.py', 'exec'), "
                       "{'__name__': 'x'})"], env=BASE_ENV, capture_output=True, text=True)
t.ok("main_botkeep on Python < 3.12: exit 1 with 'cần Python 3.12 trở lên'", done.returncode == 1
     and "cần Python 3.12 trở lên" in done.stderr, done.stderr[-300:])

for sup in SUPS:
    sup.stop()
for proc_dir in Path("/proc").glob("[0-9]*"):          # a fake child left by a failed check: never outlive the suite
    try:
        if str(FAKE) in (proc_dir / "cmdline").read_bytes().decode(errors="replace"):
            os.kill(int(proc_dir.name), signal.SIGKILL)
    except OSError:
        pass
t.done()
