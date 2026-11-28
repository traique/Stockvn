"""v3.5.1 – lịch sự kiện CPI / FOMC (bot/calendar.py) và luật bóng event_window: đổi giờ ET → UTC đúng cả giờ mùa hè,
cửa sổ ±EVENT_WINDOW_MIN, EVENT_DATES thêm sự kiện, luật khớp theo context nhưng KHÔNG chặn (không trong DEFAULT_BLOCKS),
dòng tín hiệu bóng ghi tên sự kiện, /status có dòng lịch, lịch hết thì cảnh báo."""
import os
import shutil
import sys
import time
from datetime import datetime, timezone

D = "/tmp/okx_test_calendar"
shutil.rmtree(D, ignore_errors=True)
os.environ.update(MODE="paper", DATA_DIR=D, AI_ENABLED="false", DATABASE_URL="",
                  EVENT_DATES="UNLOCK-ARB@2027-02-16T00:00Z, CPI@2027-01-13T08:30ET, hỏng, X@notadate")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bot import calendar as cal
from bot import strategy as S
from bot.config import cfg


def utc(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc).timestamp()


ev = dict((n + str(a), a) for n, a in cal.events())
# FOMC 09/12/2026 14:00 ET = 19:00 UTC (giờ mùa đông); CPI 10/06/2026 08:30 ET = 12:30 UTC (giờ mùa hè)
assert any(n == "FOMC" and a == utc(2026, 12, 9, 19, 0) for n, a in cal.events())
assert any(n == "CPI" and a == utc(2026, 6, 10, 12, 30) for n, a in cal.events())
assert any(n == "CPI" and a == utc(2026, 12, 10, 13, 30) for n, a in cal.events())
print("✓ đổi giờ ET → UTC đúng theo mùa")

assert any(n == "UNLOCK-ARB" and a == utc(2027, 2, 16, 0, 0) for n, a in cal.events())
assert any(n == "CPI" and a == utc(2027, 1, 13, 13, 30) for n, a in cal.events())
assert [a for _, a in cal.events()] == sorted(a for _, a in cal.events())
print("✓ EVENT_DATES thêm sự kiện (ET và Z), dòng hỏng bị bỏ, danh sách tăng dần")

t0 = utc(2026, 12, 9, 19, 0)
assert cal.active_event(t0) == "FOMC" and cal.active_event(t0 - 30 * 60) == "FOMC" and cal.active_event(t0 + 30 * 60) == "FOMC"
assert cal.active_event(t0 - 31 * 60) == "" and cal.active_event(t0 + 31 * 60) == ""
os.environ["EVENT_WINDOW_MIN"] = "60"
assert cal.active_event(t0 - 50 * 60) == "FOMC"
os.environ["EVENT_WINDOW_MIN"] = "30"
assert [n for n, _ in cal.upcoming(t0 - 3600, hours=48)] == ["FOMC", "CPI"]
print("✓ cửa sổ ±30 phút (EVENT_WINDOW_MIN đổi được), sự kiện sắp tới")

assert "event_window" in S.RULES and "event_window" not in cfg.DEFAULT_BLOCKS and "event_window" not in S.active_blocks()
assert S.RULES["event_window"][1]("long", {"event": "CPI"}) and not S.RULES["event_window"][1]("long", {"event": ""})
assert not S.RULES["event_window"][1]("short", {})
sig = S.Signal("X/USDT:USDT", "long", 6.0, 100.0, 0.6, 98.8, 103.6, ["t"], {"event": "FOMC", "setup": "trend", "hour": 21})
assert "event_window" in S.matching_rules(sig.side, S.rule_ctx(sig)) and S.blocking_rule(sig) == ""
print("✓ luật event_window khớp theo context nhưng không chặn (chỉ đo)")

d = cal.describe(t0 - 2 * 3600)
assert d.startswith("📅 Sắp tới: FOMC") and "giờ VN" in d, d
assert "Đang trong cửa sổ FOMC" in cal.describe(t0 + 60)
assert "đã hết" in cal.describe(utc(2040, 1, 1, 0, 0))
print("✓ dòng /status: sắp tới / đang trong cửa sổ / lịch đã hết")

# tín hiệu bóng ghi tên sự kiện vào features (main.scan: btc["event"] → diagnose: ctx.update(market))
from bot.storage import Store
st = Store()
sig.candle_ts = int(time.time() // 900) * 900 - 900
sid = st.log_signal(sig, None, False, source="not_sent")
import json
f = json.loads(st.db.run("SELECT features FROM signals WHERE sid=?", (sid,), fetch=True)[0]["features"])
assert f["event"] == "FOMC"
print("✓ dòng tín hiệu bóng ghi event=FOMC")
print("TẤT CẢ OK")
