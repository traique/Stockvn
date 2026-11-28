"""The thresholds frozen for a measurement (vnbot.quant_snapshot): the snapshot, only the calibratable
fields compared (the breakout switch and the defensive book's fixed fields never count), the
warning said once per set of values, AUTO_CALIBRATE=0 keeps the nightly calibration from running
(no parameter moves), the trading run's check, the page /bot (ETF card, snapshot card, the
"Chụp lại" button behind the password) (offline)."""
import importlib.util
import os
from dataclasses import replace
from types import SimpleNamespace as NS

import _common  # noqa: F401 - clean env, no .env, offline, temp folder, repo on sys.path

os.environ["TRADINGAGENTS_PARAMS_PATH"] = str(_common.ROOT / "quant_params.json")

from tradingagents.quant import params as qp  # noqa: E402
from vnbot import quant_snapshot as qs  # noqa: E402

t = _common.Checks()
DATA = _common.ROOT / "snap-data"
DATA.mkdir()

# ───────────── 1. the snapshot and what counts as a change ─────────────
t.ok("watched: the Engine's and the momentum's tunables, never the hand switch or the defensive book",
     set(qs.watched()) == set(qp.TUNABLE) | set(qp.MOMENTUM_TUNABLE) and "breakout_buy" not in qs.watched()
     and not any(k.startswith("fb_") for k in qs.watched()))
t.ok("no snapshot yet: no change, nothing to warn about", qs.load(DATA) is None and qs.changes(DATA) == [] and qs.warning(DATA) == ""
     and qs.status(DATA)["taken"] is False)
snap = qs.ensure(DATA, "2026-12-01")
t.ok("the first run takes it (the set in force = the defaults), with a fingerprint",
     snap["day"] == "2026-12-01" and snap["params"] == qs.values(qp.DEFAULT) and len(snap["fingerprint"]) == 7
     and qs.path(DATA).exists() and qs.ensure(DATA, "2026-12-02")["day"] == "2026-12-01")
qp.save(replace(qp.active(), breakout_buy=0), note="trang Bot giao dịch: MUA breakout tắt")
t.ok("turning the breakout switch is NOT a change of the measurement (the owner is told to turn it)",
     qs.changes(DATA) == [] and qs.warning(DATA) == "" and qs.status(DATA)["changed"] == []
     and qs.status(DATA)["now"] == snap["fingerprint"])
qp.save(replace(qp.active(), fb_top_k=12), note="tay")
t.ok("a defensive-book field set by hand is not a change either", qs.changes(DATA) == [])
qp.save(replace(qp.active(), confidence_buy_min=0.8), note="hiệu chỉnh đêm")
t.ok("a calibratable field moved: the change, named",
     qs.changes(DATA) == [("confidence_buy_min", 0.75, 0.8)] and qs.status(DATA)["changed"] == [{"name": "confidence_buy_min", "frozen": 0.75, "now": 0.8}]
     and qs.status(DATA)["now"] != snap["fingerprint"])
text = qs.warning(DATA, auto_calibrate=True)
t.ok("warned once: the line names the field and AUTO_CALIBRATE; the same set of values is not warned again",
     text.startswith("⚠️ Tham số Quant đổi giữa đợt đo (ảnh chụp 2026-12-01): confidence_buy_min 0.75→0.8")
     and "AUTO_CALIBRATE đang bật" in text and "Chụp lại tham số" in text and qs.warning(DATA, auto_calibrate=True) == "", text)
qp.save(replace(qp.active(), mom_top_k=4), note="hiệu chỉnh đêm")
text = qs.warning(DATA, auto_calibrate=False)
t.ok("another value moved: warned again (a new set), without the AUTO_CALIBRATE hint when it is off",
     "mom_top_k 5→4" in text and "confidence_buy_min 0.75→0.8" in text and "AUTO_CALIBRATE" not in text)
taken = qs.take(DATA, day="2026-12-05", note="chụp lại")
t.ok("taken again by hand: the set in force is the new reference, no change left",
     taken["note"] == "chụp lại" and qs.changes(DATA) == [] and qs.load(DATA)["day"] == "2026-12-05"
     and taken["params"]["confidence_buy_min"] == 0.8)
qp.save(qp.DEFAULT, note="test")

# ───────────── 2. AUTO_CALIBRATE=0: nothing moves at night ─────────────
from vnbot import settings as settings_module  # noqa: E402
from vnbot.jobs import JobQueue  # noqa: E402
from vnbot.service import DailyScheduler, Watchlist  # noqa: E402

os.environ["DATA_DIR"] = str(_common.ROOT / "data")
os.environ["AUTO_CALIBRATE"] = "0"
off = settings_module.load()
os.environ["AUTO_CALIBRATE"] = "1"
on = settings_module.load()
t.ok("AUTO_CALIBRATE=0 -> settings.calibrate False (1: True)", off.calibrate is False and on.calibrate is True)


class FakeBot:
    def __init__(self):
        self.calls = []

    def calibrate(self, day):
        self.calls.append(day)
        qp.save(replace(qp.active(), confidence_buy_min=0.7), note="đêm")
        return "⚙️ đổi"


notes = []
bot = FakeBot()
DailyScheduler(off, JobQueue(off), Watchlist(off), bot=bot, notify=notes.append)._calibrate("2026-12-01")
t.ok("off: the scheduler never calls the calibration - no parameter changes, nothing sent",
     bot.calls == [] and notes == [] and qp.active().confidence_buy_min == 0.75)
DailyScheduler(on, JobQueue(on), Watchlist(on), bot=bot, notify=notes.append)._calibrate("2026-12-01")
t.ok("on: it does (and the snapshot would then report the move)", bot.calls == ["2026-12-01"] and notes == ["⚙️ đổi"]
     and qp.active().confidence_buy_min == 0.7)
qp.save(qp.DEFAULT, note="test")

# ───────────── 3. the trading run's check ─────────────
from vnbot import trader as tr  # noqa: E402

DATA3 = _common.ROOT / "snap-data-3"
DATA3.mkdir()
sent = []
fake = NS(settings=NS(data_dir=DATA3, calibrate=False), notify=sent.append)
tr.TradingBot._frozen_params_check(fake, "2026-12-01")
t.ok("the run takes the snapshot on its first day and says nothing", qs.load(DATA3)["day"] == "2026-12-01" and sent == [])
qp.save(replace(qp.active(), min_rr_ratio=1.8), note="đêm")
tr.TradingBot._frozen_params_check(fake, "2026-12-02")
tr.TradingBot._frozen_params_check(fake, "2026-12-03")
t.ok("a move during the measurement: one Telegram line, not one per run",
     len(sent) == 1 and "min_rr_ratio 1.5→1.8" in sent[0], sent)
qp.save(qp.DEFAULT, note="test")

# ───────────── 4. the page /bot ─────────────
if importlib.util.find_spec("httpx") is None:
    print("(bỏ phần trang web: thiếu httpx - pip install -r requirements-dev.txt)")
else:
    from _web_harness import SETTINGS, JobQueue, Watchlist, ZoomChannel  # noqa: E402
    from fastapi.testclient import TestClient  # noqa: E402

    from vnbot import etf_bench
    from vnbot.trader import TradingBot  # noqa: E402
    from vnbot.web import create_app  # noqa: E402

    wl = Watchlist(SETTINGS)
    trader = TradingBot(SETTINGS, wl, notify=lambda text: None)
    app = create_app(SETTINGS, JobQueue(SETTINGS), wl, trader=trader,
                     zoom=ZoomChannel(state_path=SETTINGS.data_dir / "zoom_snap.json", start=False))
    c = TestClient(app, raise_server_exceptions=True)
    AUTH, SAME = ("x", "s3cret-pass"), {"origin": "http://testserver"}
    page = c.get("/bot", auth=AUTH).text
    t.ok("/bot: the ETF card before the first session, the snapshot card without a snapshot, the button",
         "Mốc so sánh: ETF E1VFVN30 mua-giữ" in page and "Chưa có phiên nào" in page and "Tham số đóng băng cho đợt đo" in page
         and "Chưa có ảnh chụp" in page and 'action="/bot/snapshot"' in page and "AUTO_CALIBRATE" in page, page[:200])
    t.ok("POST without the password -> 401; cross-site -> 403; no snapshot taken",
         c.post("/bot/snapshot").status_code == 401
         and c.post("/bot/snapshot", auth=AUTH, headers={"origin": "https://evil.example"}).status_code == 403
         and qs.load(SETTINGS.data_dir) is None)
    r = c.post("/bot/snapshot", auth=AUTH, headers=SAME, follow_redirects=False)
    t.ok("the button takes the snapshot and says so", r.status_code == 303 and "/bot?msg=" in r.headers["location"]
         and qs.load(SETTINGS.data_dir) is not None and qs.load(SETTINGS.data_dir)["note"].startswith("trang Bot giao dịch"))
    qp.save(replace(qp.active(), confidence_buy_min=0.825), note="đêm")
    page = c.get("/bot", auth=AUTH).text
    t.ok("/bot: a moved field is shown against the snapshot", "đã đổi 1 tham số" in page and "<code>confidence_buy_min</code>" in page)
    qp.save(qp.DEFAULT, note="test")
    import pandas as pd

    etf_bench.refresh(SETTINGS.data_dir, 1e9, "2026-12-02", frame=pd.DataFrame(
        {"Date": pd.to_datetime(["2026-12-01", "2026-12-02"]), "Open": [25000.0, 26000.0], "High": [25000.0, 26000.0],
         "Low": [25000.0, 26000.0], "Close": [25000.0, 26000.0], "Volume": [1e6, 1e6]}))
    page = c.get("/bot", auth=AUTH).text
    t.ok("/bot: the ETF card with its NAV, return and 'if sold today' once there are sessions",
         "Nếu bán hôm nay" in page and "từ 2026-12-02" in page and "sau phí bán và thuế 0,1%" in page)
    t.ok("/danhmuc text carries the ETF line", "📈 ETF E1VFVN30 mua-giữ: NAV" in trader.portfolio_text())

t.done()
