"""The companion (vnbot/hoidap.py), offline: which messages it takes, the codes it reads in a
sentence, the facts block (price, Quant Engine, valuation card, Tài sản holding, alerts, the
session - every source faked, a failing one a line), the one AI request with the chat's memory,
the facts-only answer without a provider or when the AI fails, the Telegram routing (a sentence
goes to the companion, a lone unknown command still gets the command list, /hoi, /start)."""
import os
import tempfile
from datetime import datetime
from pathlib import Path

import _common  # noqa: F401 - clean env, no .env, offline, temp folder, repo on sys.path

_common.need("httpx")
os.environ.update({"TELEGRAM_TOKEN": "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawQ", "TELEGRAM_ALLOWED_IDS": "111"})
from tradingagents.market import vn_rules  # noqa: E402
from tradingagents.quant import engine  # noqa: E402
from vnbot import hoidap, quote  # noqa: E402
from vnbot import settings as settings_module  # noqa: E402
from vnbot import telegram_bot as tb  # noqa: E402
from vnbot.jobs import JobQueue  # noqa: E402
from vnbot.service import Watchlist  # noqa: E402

t = _common.Checks()
DATA = Path(tempfile.mkdtemp())
NOW = datetime(2026, 11, 20, 10, 32, tzinfo=vn_rules.VN_TZ)

# ── which messages ──
t.ok("a sentence is a question", hoidap.is_question("FPT có nên mua không") and hoidap.is_question("/FPT có nên mua"))
t.ok("a lone unknown command is not", not hoidap.is_question("/khongcolenhnay") and not hoidap.is_question("hello"))
t.ok("one long word is", hoidap.is_question("thịtrườngsaorồi?"))
t.ok("codes in a sentence: capitals", hoidap.codes_in("FPT có nên mua không? còn HPG thì sao") == ["FPT", "HPG"])
t.ok("lower case: only a known code, or not a common word",
     hoidap.codes_in("fpt có nên mua không", ["FPT"]) == ["FPT"] and hoidap.codes_in("mua cao bán thấp") == [])
t.ok("lower case unknown word kept for the price source to judge", hoidap.codes_in("vcb sao rồi") == ["VCB"])
t.ok("an index is not a code; at most 3", hoidap.codes_in("VNINDEX FPT HPG VCB MWG") == ["FPT", "HPG", "VCB"])
t.ok("assistant on by default", hoidap.enabled())

# ── the facts, every source faked ──
QUOTES = {"FPT": {"code": "FPT", "price": 45300.0, "ref": 45700.0, "open": 45600.0, "high": 46000.0, "low": 44650.0,
                  "volume": 15670800, "day": "2026-11-20", "index": False},
          "VNINDEX": {"code": "VNINDEX", "price": 1688.79, "ref": 1689.62, "open": None, "high": None, "low": None,
                      "volume": None, "day": "2026-11-20", "index": True}}


def fake_quote(code, now=None):
    if code == "DOWN":
        raise RuntimeError("DNSE timeout")
    if code not in QUOTES:
        raise quote.Unknown(code)
    return QUOTES[code]


class R:
    deterministic_summary = "FPT - 45.000 VND (đóng cửa phiên 2026-11-19)\nTín hiệu hệ thống: THEO DÕI (WATCH) | confidence 0.55"


def fake_analyze(code, day, **kw):
    if code == "DOWN":
        raise RuntimeError("no data")
    return R()


quote.stock_quote, engine.analyze = fake_quote, fake_analyze


class Holding:
    def __init__(self, symbol, qty, cost):
        self.symbol, self.qty, self.cost = symbol, qty, cost

    @property
    def avg(self):
        return self.cost / self.qty

    def pending_lots(self, day):
        return [("2026-11-19", 100)] if self.symbol == "FPT" else []


class Book:
    def __init__(self):
        self.holdings = {"FPT": Holding("FPT", 500, 500 * 50_000)}


class Account:
    def book(self):
        return Book()

    def summary(self, book=None, prices=None):
        return {"positions": [{"symbol": "FPT", "price": 45300.0, "pnl": -2_350_000.0, "pnl_pct": -9.4, "weight": 22.66}]}

    def levels(self):
        return {"FPT": {"stop": 44_000, "source": "owner", "target": 55_000}}

    def site_states(self, symbols, day, refresh=False):
        return {"FPT": {"side": "SELL", "day": "2026-11-18"}}

    def overview_text(self):
        return "💼 TÀI SẢN\nTổng tài sản 100.000.000\nVốn gốc 110.000.000 · lãi/lỗ -10.000.000 (-9,09%)\nLệnh: /tsmua · /tsban"


class Monitor:
    def today(self, day):
        return [{"at": "09:40", "text": "⛔ 💼 Tài sản FPT: giá 44.000 ≤ stop 44.000, 400/500 cp bán được"}]


block, kept = hoidap.context("FPT có nên mua không", NOW, codes=["FPT", "XYZ"], account=Account(), data_dir=DATA,
                             monitor=Monitor(), watchlist=["FPT", "HPG"])
t.ok("session line with VN-Index", "Thời điểm: 10:32 20/11/2026, đang khớp lệnh liên tục." in block and "VN-Index: 1.688,79 điểm (-0,05%)." in block, block[:200])
t.ok("the price of FPT", "🔴 FPT: 45.300 -400 (-0,88%)" in block, block)
t.ok("the Quant Engine's block", "Quant Engine (quyết định của bot" in block and "THEO DÕI (WATCH)" in block)
t.ok("the holding: quantity, cost, P&L, cut-loss, T+2, the site",
     "anh đang giữ FPT 500 cp, giá vốn 50.000, giá 45.300: -2.350.000 (-9,40%), tỷ trọng 22,7%; cắt lỗ 44.000 (anh đặt); "
     "mục tiêu 55.000; chưa về (T+2): 100 cp bán được từ chiều 19/11; Siêu tín hiệu BÁN từ 18/11." in block, block)
t.ok("an unknown code is said so and dropped", kept == ["FPT"] and "XYZ: không có mã này" in block)
t.ok("today's alerts and the watchlist", "Cảnh báo bot đã gửi hôm nay:" in block and "- 09:40 ⛔" in block and "Watchlist: FPT, HPG." in block)
t.ok("no portfolio block when a stock is named and the question is not about it", "TÀI SẢN" not in block)
block2, _ = hoidap.context("danh mục của anh sao rồi", NOW, codes=[], account=Account(), data_dir=DATA)
t.ok("the portfolio when asked (or no stock named), without its command line", "Tổng tài sản 100.000.000" in block2 and "Lệnh:" not in block2, block2)
block3, kept3 = hoidap.context("DOWN sao rồi", NOW, codes=["DOWN"], account=None, data_dir=DATA)
t.ok("a source down is a line, the code kept", kept3 == ["DOWN"] and "chưa lấy được giá" in block3 and "Quant Engine: chưa tính được cho DOWN" in block3, block3)
holiday = hoidap.session_lines(datetime(2026, 9, 2, 10, 0, tzinfo=vn_rules.VN_TZ))
t.ok("a holiday says so and names the next session", "thị trường nghỉ (nghỉ lễ), phiên tới 03/09" in holiday[0], holiday)


# ── the AI request and the memory ──
class LLM:
    def __init__(self, reply="Theo Quant Engine FPT đang THEO DÕI, anh chưa nên mua.", fail=False):
        self.reply, self.fail, self.calls = reply, fail, []

    def invoke(self, messages):
        self.calls.append(messages)
        if self.fail:
            raise RuntimeError("every provider resting")

        class Reply:
            content = self.reply
        return Reply()


os.environ["GROQ_API_KEY"] = "gsk_test"          # a provider configured: the AI is asked
llm = LLM()
out = hoidap.answer("FPT có nên mua không?", 111, data_dir=DATA, llm_factory=lambda: llm, account=Account(), now=NOW)
t.ok("the AI's words come back", out == llm.reply, out)
msgs = llm.calls[-1]
t.ok("one request: system, then the question with the facts", len(msgs) == 2 and msgs[0].content == hoidap.SYSTEM
     and msgs[1].content.startswith("CÂU HỎI: FPT có nên mua không?") and "BỐI CẢNH" in msgs[1].content and "🔴 FPT: 45.300" in msgs[1].content, [m.content[:60] for m in msgs])
out2 = hoidap.answer("còn nếu giá xuống 44.000 thì sao?", 111, data_dir=DATA, llm_factory=lambda: llm, account=Account(), now=NOW)
msgs = llm.calls[-1]
t.ok("the next question carries the last exchange", len(msgs) == 4 and msgs[1].content == "FPT có nên mua không?" and msgs[2].content == llm.reply, [m.content[:40] for m in msgs])
t.ok("memory on disk, bounded", len(hoidap.Memory(DATA, 111).turns()) == 2)
t.ok("another chat has its own memory", hoidap.Memory(DATA, 222).turns() == [])
t.ok("/hoi quen clears it", hoidap.answer("quen", 111, data_dir=DATA).startswith("Đã quên") and hoidap.Memory(DATA, 111).turns() == [])
t.ok("empty question: the usage", hoidap.answer("", 111, data_dir=DATA) == hoidap.USAGE)
bad = LLM(fail=True)
out = hoidap.answer("FPT có nên mua không?", 333, data_dir=DATA, llm_factory=lambda: bad, account=Account(), now=NOW)
t.ok("the AI failing: the facts, said why", out.startswith("🤝 AI đang nghỉ (RuntimeError)") and "🔴 FPT: 45.300" in out and hoidap.NOTE in out, out[:200])
del os.environ["GROQ_API_KEY"]
out = hoidap.answer("FPT có nên mua không?", 444, data_dir=DATA, llm_factory=lambda: llm, account=Account(), now=NOW)
t.ok("no provider: the facts, no request made", out.startswith("🤝 Chưa cấu hình nhà cung cấp AI") and "anh đang giữ FPT" in out and len(llm.calls) == 2, out[:160])
progress = []
hoidap.answer("FPT sao rồi", 555, data_dir=DATA, now=NOW, progress=progress.append)
t.ok("progress told when a stock is named", progress == ["⏳ Đang gom số liệu FPT (giá, Quant Engine, định giá, danh mục)..."], progress)
progress = []
hoidap.answer("thị trường sao rồi", 555, data_dir=DATA, now=NOW, progress=progress.append)
t.ok("no progress note without a stock", progress == [])

# ── Telegram and Zoom routing ──
SETTINGS = settings_module.load()
bot = tb.TelegramBot(SETTINGS, JobQueue(SETTINGS), Watchlist(SETTINGS))
sent = []
bot.send = lambda chat, text: sent.append((chat, text))
bot.taisan = Account()
bot.intraday = Monitor()
vn_rules.now_vn = lambda: NOW


def msg(user, chat, text):
    return {"chat": {"id": chat}, "from": {"id": user}, "text": text}


help_text = bot._help()
bot._handle(msg(111, 111, "FPT có nên mua không"))
t.ok("Telegram: a sentence goes to the companion (no provider: the facts)", sent[-1][1].startswith("🤝 Chưa cấu hình") and "anh đang giữ FPT" in sent[-1][1], sent[-1][1][:120])
t.ok("...after the progress note", sent[-2][1].startswith("⏳ Đang gom số liệu FPT"), sent[-2])
bot._handle(msg(111, 111, "/hoi danh mục của anh sao rồi"))
t.ok("/hoi: the companion with the portfolio", "Tổng tài sản 100.000.000" in sent[-1][1], sent[-1][1][:200])
bot._handle(msg(111, "zoom:abc", "/hôm nay thị trường thế nào"))
t.ok("Zoom (a '/' in front): the companion", sent[-1][0] == "zoom:abc" and "VN-Index: 1.688,79" in sent[-1][1], sent[-1][1][:200])
bot._handle(msg(111, 111, "/khongcolenhnay"))
t.ok("a lone unknown command: the command list", sent[-1][1] == help_text)
bot._handle(msg(111, 111, "hello"))
t.ok("one short word: the command list", sent[-1][1] == help_text)
bot._handle(msg(111, 111, "FPT"))
t.ok("codes only: the price, not the companion", sent[-1][1].startswith("💹 Giá realtime"), sent[-1][1][:60])
bot._handle(msg(111, 111, "/start"))
t.ok("/start: the welcome, not the wall of commands", sent[-1][1].startswith("👋 Chào anh") and "/trogiup" in sent[-1][1] and len(sent[-1][1]) < 900, sent[-1][1][:80])
t.ok("/trogiup names the companion, still one message", "Hỏi gì cũng được" in help_text and len(help_text) <= tb.MAX_MESSAGE)
os.environ["HOIDAP"] = "0"
bot._handle(msg(111, 111, "FPT có nên mua không"))
t.ok("HOIDAP=0: the command list as before", sent[-1][1] == help_text)
bot._handle(msg(111, 111, "/hoi FPT có nên mua không"))
t.ok("/hoi still works when off", sent[-1][1].startswith("🤝 "))
del os.environ["HOIDAP"]
bot._handle(msg(222, 222, "FPT có nên mua không"))
t.ok("a stranger gets nothing from the companion", "chưa cho phép" in sent[-1][1])

t.done()
