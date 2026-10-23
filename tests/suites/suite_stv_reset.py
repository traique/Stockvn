"""/stvxoa danhmuc: the owner's Siêu tín hiệu account wiped at once, for good, and nothing else (offline)."""
import os
import tempfile
from datetime import datetime
from pathlib import Path

import _common  # noqa: F401 - clean env, no .env, offline, temp folder, repo on sys.path

DATA = Path(tempfile.mkdtemp(prefix="reset-"))
os.environ.update({"DATA_DIR": str(DATA), "TRADE_MODE": "signal", "SIEUTINHIEU": "1",
                   "TELEGRAM_TOKEN": "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawQ", "TELEGRAM_ALLOWED_IDS": "111",
                   "TRADINGAGENTS_PARAMS_PATH": str(DATA / "quant_params.json")})
from vnbot import settings as settings_module  # noqa: E402

SETTINGS = settings_module.load()
from tradingagents.market import vn_rules  # noqa: E402
from vnbot import external  # noqa: E402
import vnbot.trader as T  # noqa: E402

t = _common.Checks()
vn_rules.now_vn = lambda: datetime(2026, 10, 22, 16, 0, tzinfo=vn_rules.VN_TZ)


class WL:
    def get(self):
        return []


bot = T.TradingBot(SETTINGS, WL())
bot._stv_prefetch = lambda symbol: None          # no site request
t.ok("an unused account: nothing to wipe", "không có gì để xoá" in bot.stv_reset()
     and not bot._book_path("stv").exists())

bot.set_capital(200_000_000, book="stv")
bot.record_fill("mua", "GEX", 800, 23_900, "2026-10-21", book="stv")
bot.record_fill("mua", "FPT", 50, 57_000, "2026-10-22", book="stv")
bot.stv_existing("CII", 200, 19_579)
bot.set_capital(500_000_000)                                  # the bot's own real account
bot.record_fill("mua", "VCB", 100, 56_000, "2026-10-21")
alerts = external.SellAlerts(DATA)
alerts.remember([("GEX", {"day": "2026-10-22", "side": "SELL", "created": "2026-10-22T15:08"})], ["GEX"])
paper = bot.stv_path
paper.write_text('{"cash": 1, "start_cash": 1}', encoding="utf-8")
stw = external.Watchlist(DATA)
stw.path.parent.mkdir(parents=True, exist_ok=True)
stw.path.write_text('{"symbols": ["GEX", "HPG"]}', encoding="utf-8")
before_main = bot._book_path("real").read_text(encoding="utf-8")
before_watch = stw.path.read_text(encoding="utf-8")
cash_before = bot.real("stv").cash

msg = bot.stv_reset()
print(msg)
r = bot.real("stv")
t.ok("wiped: no holding, cash 0, no fill, no history", not r.positions and r.cash == 0 and r.start_cash == 0
     and not r.fills and not r.history and not r.orders and not r.meta, r.to_dict())
t.ok("the SELL alerts already sent are forgotten", not alerts.path.exists())
t.ok("the message lists what went, says there is no undo and how to start again",
     msg.startswith("🗑️ Đã xoá toàn bộ danh mục Siêu tín hiệu của anh:")
     and "• 3 mã: CII 200 cp, FPT 50 cp, GEX 800 cp" in msg and f"• tiền mặt {T._fmt(cash_before)} đ" in msg
     and "• 2 lệnh đã ghi" in msg and "Không hoàn tác được" in msg and "/stvvon" in msg and "/stvcosan" in msg, msg)
t.ok("nothing else touched: the bot's real account, the comparison book, the watchlist",
     bot._book_path("real").read_text(encoding="utf-8") == before_main and paper.exists()
     and stw.path.read_text(encoding="utf-8") == before_watch)
t.ok("no copy left behind (no undo, as chosen)", sorted(p.name for p in bot.dir.glob("real_stv*")) == ["real_stv.json"],
     sorted(p.name for p in bot.dir.glob("real_stv*")))
t.ok("wiping again: nothing to wipe", "không có gì để xoá" in bot.stv_reset())
bot.set_capital(100_000_000, book="stv")
t.ok("the account works again after the wipe", bot.real("stv").cash == 100_000_000 and bot.real("stv").start_cash == 100_000_000)

# a damaged book (moved aside by the ledger): the account starts again, the damaged copy kept
bot._book_path("stv").write_text("{not json", encoding="utf-8")
try:
    bot.real("stv")
except Exception:
    pass
aside = sorted(bot.dir.glob("real_stv.json.corrupt-*"))
msg2 = bot.stv_reset()
t.ok("a damaged book: wiped to a new one, the damaged copy kept and named",
     aside and aside[0].exists() and bot.real("stv").cash == 0 and "sổ cũ bị hỏng" in msg2 and aside[0].name in msg2, msg2)

# the command: only with the word danhmuc
from vnbot import pages  # noqa: E402
from vnbot import telegram_bot as tb  # noqa: E402
from vnbot.jobs import JobQueue  # noqa: E402
from vnbot.service import Watchlist  # noqa: E402

tg = tb.TelegramBot(SETTINGS, JobQueue(SETTINGS), Watchlist(SETTINGS))
tg.trader = bot
out = []
tg.send = lambda chat, text: out.append(text)
bot.set_capital(100_000_000, book="stv")
for text in ("/stvxoa", "/stvxoa abc", "/stvxoa danhmucc"):
    tg._handle({"chat": {"id": 111}, "from": {"id": 111}, "text": text})
t.ok("/stvxoa without 'danhmuc' (or a typo): nothing wiped, how to use it",
     len(out) == 3 and all("Dùng: /stvxoa danhmuc" in o for o in out) and bot.real("stv").cash == 100_000_000, out)
tg._handle({"chat": {"id": 222}, "from": {"id": 222}, "text": "/stvxoa danhmuc"})
t.ok("a stranger cannot wipe it", bot.real("stv").cash == 100_000_000 and "Bot chưa cho phép" in out[-1], out[-1])
tg._handle({"chat": {"id": 111}, "from": {"id": 111}, "text": "/stvxoa DanhMuc"})
t.ok("/stvxoa danhmuc wipes at once (no confirmation step)", out[-1].startswith("🗑️ Đã xoá") and bot.real("stv").cash == 0, out[-1])
tg._handle({"chat": {"id": 111}, "from": {"id": 111}, "text": "/trogiup"})
t.ok("listed in /trogiup and on the web's command list", "/stvxoa danhmuc" in out[-1]
     and any(c == "stvxoa danhmuc" for c, _, _ in pages.COMMANDS))
os.environ["SIEUTINHIEU"] = "0"
bot.set_capital(100_000_000, book="stv")
tg._handle({"chat": {"id": 111}, "from": {"id": 111}, "text": "/stvxoa danhmuc"})
t.ok("Siêu tín hiệu off: refused, nothing wiped", "đang tắt" in out[-1] and bot.real("stv").cash == 100_000_000, out[-1])
t.done()
