"""Telegram security: only the allowed users run commands (also in the report group), the
bot token never appears in its logs, edited messages and buttons are ignored (offline)."""
import io
import logging
import os

import _common  # noqa: F401 - clean env, no .env, offline, temp folder, repo on sys.path

TOKEN = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawQ"
os.environ.update({"TELEGRAM_TOKEN": TOKEN, "TELEGRAM_ALLOWED_IDS": "111", "TELEGRAM_REPORT_CHAT_IDS": "-100500"})

from vnbot import settings as settings_module  # noqa: E402
from vnbot import telegram_bot as tb  # noqa: E402
from vnbot.jobs import JobQueue  # noqa: E402
from vnbot.service import Watchlist  # noqa: E402

t = _common.Checks()
SETTINGS2 = settings_module.load()
wl = Watchlist(SETTINGS2)
bot = tb.TelegramBot(SETTINGS2, JobQueue(SETTINGS2), wl)

url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
t.ok("redact() hides the configured token in a URL", TOKEN not in tb.redact(url) and "<TOKEN>" in tb.redact(url))
t.ok("a bare token is hidden too", TOKEN not in tb._TOKEN_RE.sub("<T>", "token " + TOKEN))
buf = io.StringIO()
handler = logging.StreamHandler(buf)
logging.getLogger().addHandler(handler)
logging.getLogger().setLevel(logging.INFO)
try:
    raise RuntimeError("boom " + url)
except RuntimeError:
    logging.getLogger("vnbot.telegram_bot").exception("x %s", url)
logging.getLogger().removeHandler(handler)
t.ok("token not in the Telegram module's log (message and traceback)", TOKEN not in buf.getvalue(), buf.getvalue()[-300:])

sent = []
bot.send = lambda chat, text: sent.append((chat, text[:60]))


class Trader:
    def __init__(self):
        self.calls = []

    def set_capital(self, value, book="real"):
        self.calls.append(("von", value, book))
        return "ok"

    def start_run(self, notify=None):
        self.calls.append("chay")
        return "ok"

    def portfolio_text(self):
        self.calls.append("danhmuc")
        return "pf"

    def record_fill(self, *args, **kwargs):
        self.calls.append("khop")
        return "ok"


bot.trader = Trader()


def m(user, chat, text):
    return {"chat": {"id": chat}, "from": {"id": user}, "text": text}


bot._handle(m(222, -100500, "/von 5000000"))       # a member of the report group, not allowed
bot._handle(m(222, 222, "/chay"))
bot._handle(m(222, -100500, "/theodoi FPT"))
bot._handle(m(222, 222, "/khop mua FPT 100 100000"))
bot._handle(m(222, 222, "/stvvon 1000000000"))
bot._handle(m(None, -100500, "/danhmuc"))            # no sender
bot._handle(m("111", -100500, "/von 1"))             # a string id is not the allowed user 111
t.ok("strangers run nothing", bot.trader.calls == [], bot.trader.calls)
t.ok("strangers cannot change the watchlist", wl.get() == [], wl.get())
bot._handle(m(111, -100500, "/danhmuc"))
t.ok("the allowed user works, also in the group", bot.trader.calls == ["danhmuc"] and sent[-1] == (-100500, "pf"),
     (bot.trader.calls, sent[-1:]))

offset, msgs = bot._accept([
    {"update_id": 1, "edited_message": {"text": "/von 1", "chat": {"id": 1}, "from": {"id": 111}}},
    {"update_id": 2, "callback_query": {"data": "x"}},
    {"update_id": 3, "message": {"text": "/von 1", "chat": {"id": 1}, "from": {"id": 111}}}])
t.ok("edited messages and buttons ignored", [x["text"] for x in msgs] == ["/von 1"] and offset == 4, (msgs, offset))

for raw in ("1²", "56.3", "abc", "", "1.2345"):
    try:
        tb._amount(raw)
        t.ok(f"amount {raw!r} refused", False)
    except ValueError as exc:
        t.ok(f"amount {raw!r} refused with a Vietnamese message", "Số không hợp lệ" in str(exc), str(exc))
t.ok("amounts the Vietnamese way", tb._amount("56.300") == 56_300 and tb._amount("200.000.000") == 200_000_000
     and tb._amount("56300") == 56_300)

t.done()
