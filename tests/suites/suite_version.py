"""The version: one source (vnbot/version.py), the build fingerprint and the release check,
shown on the web (header pill, footer, /phienban, /health, the boot page) and in
Telegram/Zoom (/phienban, /trogiup, /trangthai), and announced once when it changes (offline)."""
import importlib.util
import os
import re
import shutil
import tempfile
from pathlib import Path

import _common  # noqa: F401 - clean env, no .env, offline, temp folder, repo on sys.path

t = _common.Checks()
from vnbot import version as ver  # noqa: E402

# ── the release notes ──
names = [v for v, _, _ in ver.NOTES]
t.ok("VERSION is the newest release in NOTES", names[0] == ver.VERSION and ver.NOTES[0][1] == ver.RELEASED, names)
nums = [tuple(int(x) for x in v.lstrip("v").split(".")) for v in names]
t.ok("versions are vMAJOR.MINOR.PATCH, newest first, no repeat",
     all(len(n) == 3 for n in nums) and nums == sorted(nums, reverse=True) and len(set(nums)) == len(nums), nums)
t.ok("dates YYYY-MM-DD, never newer than the release", all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) and d <= ver.RELEASED
                                                           for _, d, _ in ver.NOTES))
t.ok("every release says what changed (short lines)", all(notes and all(0 < len(n) <= 110 for n in notes)
                                                          for _, _, notes in ver.NOTES))

# ── the build fingerprint ──
b = ver.build()
t.ok("build: 7 hex characters, stable", re.fullmatch(r"[0-9a-f]{7}", b) and ver.build() == b, b)
files = [p.relative_to(ver.ROOT).as_posix() for p in ver._code_files()]
t.ok("build covers the code: tradingagents/, vnbot/, main_botkeep.py", "main_botkeep.py" in files
     and any(f.startswith("tradingagents/") for f in files) and "vnbot/trader.py" in files, files[:5])
t.ok("build leaves out version.py itself and the tests", "vnbot/version.py" not in files
     and not any(f.startswith("tests/") for f in files))

# a copy of a tiny tree: what changes the fingerprint and what does not
root = Path(tempfile.mkdtemp(prefix="vroot-"))
(root / "vnbot").mkdir()
(root / "tradingagents").mkdir()
shutil.copy(Path(ver.__file__), root / "vnbot" / "version.py")
(root / "vnbot" / "a.py").write_bytes(b"x = 1\n")
(root / "tradingagents" / "b.py").write_bytes(b"y = 2\n")
(root / "main_botkeep.py").write_bytes(b"main = 3\n")
spec = importlib.util.spec_from_file_location("version_copy", root / "vnbot" / "version.py")
vc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vc)
first = vc.build()
(root / "vnbot" / "a.py").write_bytes(b"x = 1\r\n")
vc.build.cache_clear()
t.ok("Windows line endings give the same build", vc.build() == first)
(root / "vnbot" / "a.py").write_bytes(b"x = 2\n")
vc.build.cache_clear()
changed = vc.build()
t.ok("one changed line gives another build", changed != first)
(root / "vnbot" / "a.py").write_bytes(b"x = 1\n")
(root / "vnbot" / "__pycache__").mkdir(exist_ok=True)
(root / "vnbot" / "__pycache__" / "a.cpython-313.py").write_bytes(b"junk")
vc.build.cache_clear()
t.ok("__pycache__ ignored", vc.build() == first)
(root / "vnbot" / "a.py").unlink()
vc.build.cache_clear()
t.ok("a missing file gives another build (a partial upload shows)", vc.build() != first)
(root / "vnbot" / "a.py").write_bytes(b"x = 1\n")
vc.build.cache_clear()
t.ok("stamp(): writes RELEASE_BUILD into version.py", vc.stamp() == first
     and f'RELEASE_BUILD = "{first}"' in (root / "vnbot" / "version.py").read_text(encoding="utf-8"))
vc.build.cache_clear()
t.ok("...and stamping does not change the build (version.py is left out)", vc.build() == first)

# ── the release check and the texts ──
real_release = ver.RELEASE_BUILD
ver.RELEASE_BUILD = b
t.ok("released code: ✅ and no '(đã sửa)'", ver.is_release() and ver.status_line().startswith("✅")
     and "(đã sửa)" not in ver.label() and ver.label().startswith(f"{ver.VERSION} · "))
ver.RELEASE_BUILD = "fffffff"
t.ok("other code: ⚠️ with both builds, '(đã sửa)' in the label", not ver.is_release()
     and "⚠️" in ver.status_line() and "fffffff" in ver.status_line() and "(đã sửa)" in ver.label())
ver.RELEASE_BUILD = b
txt = ver.text()
t.ok("/phienban text: version, build, check, uptime, what's new",
     txt.startswith(f"🏷️ TradingAgents VN {ver.VERSION} (phát hành ") and f"Bản dựng {b} · ✅" in txt
     and "Chạy từ " in txt and f"Có gì mới ở {ver.VERSION}" in txt and max(len(x) for x in txt.splitlines()) <= 110, txt)
t.ok("uptime reads well", ver.uptime(ver.STARTED + 3 * 86400 + 2 * 3600 + 5 * 60) == "3 ngày 2 giờ 5 phút"
     and ver.uptime(ver.STARTED + 59) == "0 phút")
oldest = names[-1]
t.ok("changes since the oldest listed release: every release after it",
     [v for v, _, _ in ver.changes_since(oldest)] == names[:names.index(oldest)])
t.ok("changes since an unknown version: only the current release", [v for v, _, _ in ver.changes_since("v0.0.1")] == names[:1])
first_start = ver.update_notice("")
t.ok("first start with versions: says which version runs", first_start.startswith(f"🏷️ Bot đang chạy TradingAgents VN {ver.VERSION}"))
previous = names[1] if len(names) > 1 else "v0.9.0"      # an unknown older version: every note is new
upd = ver.update_notice(f"{previous} 1234567")
t.ok("an update: 'old → new' with the new notes", upd.startswith(f"🆕 Bot đã cập nhật: {previous} → {ver.VERSION}")
     and ver.NOTES[0][2][0] in upd and (len(ver.NOTES) < 2 or ver.NOTES[1][2][0] not in upd), upd)
t.ok("an ordinary restart: nothing to say", ver.update_notice(f"{ver.VERSION} {b}") is None)
t.ok("same version, other code: warned", ver.update_notice(f"{ver.VERSION} 1234567").startswith("⚠️ Code của bot vừa đổi"))
t.ok("info() for /health and the page", ver.info()["version"] == ver.VERSION and ver.info()["release"] is True)

# ── the start: announced once, quiet on a restart, never breaking the start ──
import main_botkeep  # noqa: E402 - only defines things on import (main() is not run)

sent = []
main_botkeep._announce_version(sent.append)
main_botkeep._announce_version(sent.append)
t.ok("start: announced once, then quiet", len(sent) == 1 and sent[0].startswith("🏷️ Bot đang chạy"), sent)
(main_botkeep.SETTINGS.data_dir / "last_version.txt").write_text(f"{previous} 1234567", encoding="utf-8")
main_botkeep._announce_version(sent.append)
t.ok("start after an upload: 'Bot đã cập nhật'", len(sent) == 2 and sent[1].startswith("🆕 Bot đã cập nhật"), sent[-1:])


def boom(text):
    raise RuntimeError("telegram down")


(main_botkeep.SETTINGS.data_dir / "last_version.txt").write_text("", encoding="utf-8")
main_botkeep._announce_version(boom)
t.ok("a failing notice never stops the start", True)

# ── Telegram / Zoom ──
os.environ.update({"TELEGRAM_TOKEN": "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawQ", "TELEGRAM_ALLOWED_IDS": "111"})
from vnbot import settings as settings_module  # noqa: E402
from vnbot import telegram_bot as tb  # noqa: E402
from vnbot.jobs import JobQueue  # noqa: E402
from vnbot.service import Watchlist  # noqa: E402

S2 = settings_module.load()
bot = tb.TelegramBot(S2, JobQueue(S2), Watchlist(S2))
out = []
bot.send = lambda chat, text: out.append(text)
bot._handle({"chat": {"id": 111}, "from": {"id": 111}, "text": "/phienban"})
bot._handle({"chat": {"id": 111}, "from": {"id": 111}, "text": "/version"})
t.ok("/phienban and /version answer the version text", out[:2] == [ver.text()] * 2 or
     (out and out[0].startswith(f"🏷️ TradingAgents VN {ver.VERSION}") and out[1].startswith("🏷️")), out[:1])
bot._handle({"chat": {"id": 111}, "from": {"id": 111}, "text": "/trogiup"})
t.ok("/trogiup: the version on the first line and /phienban listed",
     out[-1].startswith(f"TradingAgents VN {ver.VERSION} - ") and "/phienban" in out[-1], out[-1][:80])
bot._handle({"chat": {"id": 111}, "from": {"id": 111}, "text": "/trangthai"})
t.ok("/trangthai starts with the version and the release check",
     out[-1].startswith(f"🏷️ {ver.VERSION} · bản dựng {b} · ✅"), out[-1][:120])
bot._handle({"chat": {"id": 222}, "from": {"id": 222}, "text": "/phienban"})
t.ok("strangers do not get it", "Bot chưa cho phép" in out[-1], out[-1])

# ── the web ──
if importlib.util.find_spec("httpx") is None:
    print("SKIP web checks: thiếu httpx (pip install -r requirements-dev.txt)")
else:
    from _web_harness import build as build_app  # noqa: E402
    from fastapi.testclient import TestClient  # noqa: E402

    from vnbot.boot import LateApp  # noqa: E402

    app, *_ = build_app(trader=True)
    c = TestClient(app, raise_server_exceptions=False)
    auth = ("x", "s3cret-pass")
    t.ok("/phienban needs the password", c.get("/phienban").status_code == 401)
    r = c.get("/phienban", auth=auth)
    page = r.text
    t.ok("/phienban: version, build, release check, every release",
         r.status_code == 200 and f">{ver.VERSION}<" in page and f"<code>{b}</code>" in page
         and "đúng bản phát hành" in page and all(f"<h3>{v}</h3>" in page for v in names), r.status_code)
    t.ok("header pill links to /phienban on every page", 'class="ver-pill" href="/phienban"' in page
         and f'<span class="mono">{ver.VERSION}</span></a>' in page)
    t.ok("footer: version, build and the history link", f"TradingAgents VN {ver.VERSION}</span>" in page
         and f"bản dựng {ver.build()}" in page
         and '<a href="/phienban">lịch sử phiên bản</a>' in page)
    ver.RELEASE_BUILD = "fffffff"
    page2 = c.get("/phienban", auth=auth).text
    t.ok("other code: red pill (⚠) and a warning on the page", 'class="ver-pill warn"' in page2 and "khác bản phát hành" in page2
         and "upload thiếu file" in page2)
    ver.RELEASE_BUILD = b
    nf = c.get("/no-such-page", auth=auth)
    t.ok("error pages (seen without the password) show no version", nf.status_code == 404
         and 'class="ver-pill' not in nf.text and "lịch sử phiên bản" not in nf.text)
    h = c.get("/health").json()
    t.ok("/health has the version", h.get("ok") is True and h.get("version") == ver.VERSION, h)
    boot = TestClient(LateApp())
    t.ok("while booting: /health and the page show the version", boot.get("/health").json().get("version") == ver.VERSION
         and f"phiên bản {ver.VERSION}" in boot.get("/").text)
    late = LateApp()
    late.failed("ImportError: x")
    t.ok("a failed start shows the version too", f"(Phiên bản {ver.VERSION})" in TestClient(late).get("/").text)

ver.RELEASE_BUILD = real_release
t.done()
