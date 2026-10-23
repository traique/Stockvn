"""Shared by the web suites: the real FastAPI app built with stubs (no network, no trader)."""
import hashlib
import hmac
import os
import tempfile
import time
from pathlib import Path

import _common  # noqa: E402,F401 - clean env, no .env, offline, temp folder, repo on sys.path
_common.need("httpx")   # starlette's TestClient

DATA = Path(tempfile.mkdtemp(prefix="vnbot-sec-"))
os.environ.update({
    "DATA_DIR": str(DATA),
    "WEB_PASSWORD": os.environ.get("T_WEB_PASSWORD", "s3cret-pass"),
    "ZOOM_CLIENT_ID": "cid", "ZOOM_CLIENT_SECRET": "csecret", "ZOOM_BOT_JID": "bot@xmpp.zoom.us",
    "ZOOM_ACCOUNT_ID": "acc",
    "ZOOM_SECRET_TOKEN": os.environ.get("T_ZOOM_SECRET", "zsecret"),
    "PUBLIC_URL": os.environ.get("T_PUBLIC_URL", ""),
})

from vnbot import settings as settings_module  # noqa: E402

SETTINGS = settings_module.load()

from vnbot.jobs import JobQueue  # noqa: E402
from vnbot.service import Watchlist  # noqa: E402
from vnbot.zoom import ZoomChannel  # noqa: E402
from vnbot.channels import Switches  # noqa: E402


class FakeTrader:
    BOOK_NAMES = {"factor": "Phòng thủ", "engine": "Quant Engine", "momentum": "Động lượng"}
    running = False
    calls = []

    def __init__(self, settings):
        self.settings = settings

    def start_run(self, notify=None):
        self.calls.append("start_run")
        return "started"

    def reset_paper(self, amount, books):
        self.calls.append(("reset_paper", amount, books))
        return "reset"

    def paper_capital(self):
        return 1e9


def build(trader=False, zoom_post=None):
    jobs = JobQueue(SETTINGS)
    wl = Watchlist(SETTINGS)
    sw = Switches(SETTINGS.data_dir, {"intraday": True})
    zoom = ZoomChannel(state_path=SETTINGS.data_dir / "zoom_state.json", post=zoom_post, start=False)
    handled = []
    zoom.handler = lambda target, text: handled.append((target, text))
    zoom.alert_owner = lambda text: handled.append(("ALERT", text))
    from vnbot.web import create_app
    app = create_app(SETTINGS, jobs, wl, trader=FakeTrader(SETTINGS) if trader else None, zoom=zoom, switches=sw)
    return app, zoom, handled, jobs, wl, sw


def sign(secret, body: bytes, ts=None):
    ts = ts or str(int(time.time() * 1000))
    sig = "v0=" + hmac.new(secret.encode(), b"v0:" + ts.encode() + b":" + body, hashlib.sha256).hexdigest()
    return {"x-zm-signature": sig, "x-zm-request-timestamp": ts, "content-type": "application/json"}
