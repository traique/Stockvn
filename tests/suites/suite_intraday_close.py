"""The closing pass of the intraday monitor (vnbot/intraday.py), offline: once a session from
14:46 (the ATC has matched), on the closing price, labelled so; the keys of the session keep a
level already alerted on from being repeated; off with the "intraday" switch; nothing on a holiday."""
import json
import tempfile
from datetime import datetime
from pathlib import Path

import _common  # noqa: F401 - clean env, no .env, offline, temp folder, repo on sys.path
from tradingagents.market import vn_rules  # noqa: E402
from tradingagents.trading.ledger import Ledger  # noqa: E402
from vnbot import intraday as I  # noqa: E402
from vnbot import settings as settings_module  # noqa: E402
from vnbot.channels import Switches  # noqa: E402

t = _common.Checks()
SETTINGS = settings_module.load()


class IT:
    def __init__(self, real):
        self._real = real

    def real(self, name=None):
        return Ledger.from_dict(json.loads(json.dumps(self._real.to_dict())))

    def paper(self):
        return Ledger(cash=1e9, start_cash=1e9)


def book(stop=55_000):
    L = Ledger(cash=1e9, start_cash=1e9)
    L.record_fill("BUY", "FPT", 1000, 60_000.0, "2026-10-15")
    pos = L.positions["FPT"]
    pos.stop, pos.target1, pos.last_price, pos.last_date = stop, 70_000, 57_000, "2026-10-20"
    L.meta["close_marks"] = 1
    return L


def monitor(price, mode="signal", switch_on=True):
    sent = []
    data_dir = Path(tempfile.mkdtemp())
    s = settings_module.Settings(**{**SETTINGS.__dict__, "data_dir": data_dir, "trade_mode": mode})
    sw = Switches(data_dir, {"intraday": True})
    sw.set("intraday", switch_on)
    mon = I.IntradayMonitor(s, IT(book()), sent.append, price=lambda c, d: price, index_change=lambda d: None,
                            exchange_of=lambda c: "HOSE", closes=lambda c, on, d: None, switches=sw)
    return mon, sent


def at(hh, mm, day=(2026, 10, 21)):
    return datetime(*day, hh, mm, tzinfo=vn_rules.VN_TZ)


# ── the closing pass: labelled, once ──
mon, sent = monitor(54_000)
vn_rules.now_vn = lambda: at(14, 46)         # the header's time
mon.tick = mon.tick                           # (tick reads vn_rules.now_vn)
mon.tick()
t.ok("14:46: the closing pass alerts on the stop", len(sent) == 1 and "⛔ 💼 Thật FPT: giá đóng cửa 54.000 ≤ stop 55.000" in sent[0], sent)
t.ok("header and footer say it is the close", sent[0].startswith("🔔 Giá đóng cửa 14:46") and "nhắc sớm" in sent[0], sent[0][:120])
mon.tick()
t.ok("the pass runs once a session", len(sent) == 1, sent)
vn_rules.now_vn = lambda: at(14, 50)
mon.tick()
t.ok("still once (14:50)", len(sent) == 1)

# ── a level alerted on in the session is not repeated at the close ──
mon, sent = monitor(54_000)
vn_rules.now_vn = lambda: at(10, 0)
mon.tick()
t.ok("10:00: the session alert", len(sent) == 1 and "⛔ 💼 Thật FPT: giá 54.000 ≤ stop" in sent[0], sent)
vn_rules.now_vn = lambda: at(14, 47)
mon.tick()
t.ok("14:47: nothing new (same key)", len(sent) == 1, sent)

# ── before 14:46 (the ATC still matching) the pass does not run; the closing price above the stop says nothing ──
mon, sent = monitor(54_000)
vn_rules.now_vn = lambda: at(14, 45)
mon.tick()
t.ok("14:45: no closing pass yet", sent == [], sent)
mon, sent = monitor(56_000)
vn_rules.now_vn = lambda: at(14, 46)
mon.tick()
t.ok("close above the stop: silent", sent == [], sent)

# ── the switch, a holiday, the next day ──
mon, sent = monitor(54_000, switch_on=False)
vn_rules.now_vn = lambda: at(14, 46)
mon.tick()
t.ok("intraday switch off: no closing pass", sent == [], sent)
mon, sent = monitor(54_000)
vn_rules.now_vn = lambda: at(14, 46, (2026, 9, 2))
mon.tick()
t.ok("2/9 holiday: nothing (closed all day)", sent == [], sent)
mon, sent = monitor(54_000)
vn_rules.now_vn = lambda: at(14, 46)
mon.tick()
vn_rules.now_vn = lambda: at(14, 46, (2026, 10, 22))
mon.tick()
t.ok("the next session has its own closing pass", len(sent) == 2 and "22/10" not in sent[1] and sent[1].startswith("🔔 Giá đóng cửa"), sent)

t.done()
