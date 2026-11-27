"""Radar sáng (vnbot.radar, /radar, the 09:30 job of DailyScheduler), offline: the bars, VCI, the news,
the morning quote and the AI faked.

- the RSI series matches features.calc_rsi; a bullish divergence found and not found;
- the score of a PHR-like candle (RSI oversold, divergence, stopping volume, less down than VN-Index,
  liquid, under EMA20 and MA50): 80, every component; a stock in an uptrend scores 40; too short: None;
- the report: the opening by VN-Index, the context line, the scan line with the named and the near
  misses, each block (Chiến sự, Tai mắt, the ex-date, Checklist papa, Quân lệnh at tick prices);
- Tai mắt: level 2 from the fake AI (JSON, E scored), level 1 when the AI raises, answers no JSON, or
  there is no AI (E unscored, one-off profit flagged by code), a bank (no figures);
- the scheduler: due from 09:30 to 11:30 on a session day, once (the stamp), retried three times then
  given up with a message, RADAR=0 off; /radar on Telegram and Zoom; the help and /docs.
"""
import json
import time
from datetime import datetime
from pathlib import Path

import _common  # noqa: F401 - clean env, no .env, offline, temp folder, repo on sys.path

_common.need("httpx")
import os  # noqa: E402

os.environ.update({"TELEGRAM_TOKEN": "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawQ", "TELEGRAM_ALLOWED_IDS": "111",
                   "TELEGRAM_REPORT_CHAT_IDS": "-100500"})
import _web_harness as wh  # noqa: E402
from tradingagents.dataflows import money_flows as mf  # noqa: E402
from tradingagents.dataflows import symbols  # noqa: E402
from tradingagents.dataflows.vendors import vci  # noqa: E402
from tradingagents.market import vn_rules  # noqa: E402
from tradingagents.quant import features as f  # noqa: E402
from vnbot import quote, radar  # noqa: E402
from vnbot import telegram_bot as tb  # noqa: E402
from vnbot.service import DailyScheduler  # noqa: E402

t = _common.Checks()
DATA = Path(wh.SETTINGS.data_dir)


def at(day, hh, mm):
    return datetime(int(day[:4]), int(day[5:7]), int(day[8:10]), hh, mm, tzinfo=vn_rules.VN_TZ)


# ═══════════════ 1. indicators ═══════════════
closes = [100 + ((i * 7) % 11) - 5 + i * 0.3 for i in range(80)]
series = radar.rsi_series(closes)
t.ok("rsi_series: None before 14 bars, then the same value as calc_rsi on every prefix",
     series[:14] == [None] * 14 and all(abs(series[i] - f.calc_rsi(closes[: i + 1])) < 1e-9 for i in range(14, 80)))


def falling(n=120, step=-200.0, start=40_000.0):
    return [start + step * i for i in range(n)]


# a lower low in price after a bounce: the RSI at the new low is higher than at the old
div_closes = falling(100) + [20_500, 20_900, 20_700, 20_300, 19_900]
div_rsi = radar.rsi_series(div_closes)
t.ok("divergence: the new low under the old, the RSI higher", radar.divergence(div_closes, div_rsi))
t.ok("...not when the last low is higher than the old one", not radar.divergence(falling(100) + [21_000, 21_200, 21_100, 21_300, 21_250],
                                                                                radar.rsi_series(falling(100) + [21_000, 21_200, 21_100, 21_300, 21_250])))

# ═══════════════ 2. a PHR-like signal bar ═══════════════
SIGNAL_DAY = "2026-11-26"
DAYS = [SIGNAL_DAY]
while len(DAYS) < 140:
    DAYS.insert(0, vn_rules.prev_session(DAYS[0]))


def bars_from(closes, volumes=None, *, last=None):
    """Bars ending on the signal day; ``last``: (open, high, low, close, volume) of the signal bar."""
    out = [{"Date": day, "Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c,
            "Volume": volumes[i] if volumes else 1_000_000}
           for i, (day, c) in enumerate(zip(DAYS[-len(closes):], closes, strict=True))]
    if last:
        o, h, lo, c, v = last
        out[-1].update(Open=o, High=h, Low=lo, Close=c, Volume=v)
    return out


# PHR-like: a long fall, a small bounce, a lower low (the RSI higher: a divergence), the signal candle
# from 27.300 down to 26.500 closing at 27.250 (77% up its range) on 1,8x volume; VN-Index -3,42% that
# day, the stock -0,73%
phr_closes = [40_000 - 120 * i for i in range(100)] + [28_300, 28_600, 28_900, 28_700, 28_200, 27_800, 27_450, 27_250]
PHR_VOL = [500_000] * 107 + [900_000]
PHR = bars_from(phr_closes, PHR_VOL, last=(27_300, 27_480, 26_500, 27_250, 900_000))
vni_closes = [1_700 + (i % 5) for i in range(107)] + [1_629.68]
VNI = bars_from(vni_closes)
VNI[-2]["Close"] = 1_687.45
UP = bars_from([20_000 + 40 * i for i in range(108)], [2_000_000] * 108)
BANK = bars_from([30_000 - 10 * i for i in range(108)], [3_000_000] * 108)
SHORT = bars_from([10_000] * 30)
BARS = {"PHR": PHR, "VNINDEX": VNI, "UPP": UP, "CTG": BANK, "NEW": SHORT}


def fake_bars(code, day):
    if code not in BARS:
        raise ValueError(f"no bars for {code}")
    return [dict(b) for b in BARS[code] if b["Date"] <= day]


vni_pct = (1_629.68 / 1_687.45 - 1) * 100
s = radar.analyze("PHR", PHR, vni_pct)
t.ok("PHR-like: RSI oversold, divergence, stopping volume, less down than VN-Index, liquid, under EMA20/MA50 -> 80",
     s is not None and s["score"] == 80 and s["points"] == {"rsi": 20, "divergence": 20, "stopping": 20, "market": 10,
                                                          "liquidity": 10, "ema20": 0, "ma50": 0}, s and s["points"])
t.ok("...the figures: close 27.250, -0,73%, close position 73%, volume 1,8x, support 26.500 (the candle's low), the checks",
     s["close"] == 27_250 and round(s["pct"], 2) == -0.73 and round(s["close_pos"], 2) == 0.77 and round(s["vol_ratio"], 1) == 1.8
     and s["support"] == 26_500 and s["rsi"] < 30 and s["checks"] == {"A": True, "B": True, "C": True, "G": False}, s)
u = radar.analyze("UPP", UP, vni_pct)
t.ok("an uptrend: liquid, above EMA20 and MA50, better than VN-Index -> 40; no divergence, RSI high",
     u["score"] == 40 and u["points"]["ema20"] == 10 and u["points"]["ma50"] == 10 and u["points"]["rsi"] == 0 and not u["divergence"], u["points"])
t.ok("too short a history: None", radar.analyze("NEW", SHORT, vni_pct) is None)

# ═══════════════ 3. the scan and the universe ═══════════════
mf.index_members = lambda group="VN30": ["PHR", "UPP", "CTG"] if group == "VN100" else ["UPP"]
codes, scope = radar.universe(["PHR", "NEW", "XXX"])
t.ok("universe: VN100 then the watchlist's extras, no repeat", codes == ["PHR", "UPP", "CTG", "NEW", "XXX"] and scope == "VN100 + watchlist")
signals, vp, errors = radar.scan(codes, SIGNAL_DAY, bars=fake_bars)
t.ok("scan: the signals highest first, VN-Index's %, the codes without data and the short one left out",
     [x["code"] for x in signals][0] == "PHR" and {x["code"] for x in signals} == {"PHR", "UPP", "CTG"} and round(vp, 2) == -3.42
     and errors == ["XXX"], ([x["code"] for x in signals], errors))

# ═══════════════ 4. Tai mắt: the figures, the AI, the fallbacks ═══════════════
Q = [{"yearReport": 2026, "lengthReport": 3, "publicDate": "2026-10-30T00:00:00", "isa3": 657.8e9, "isa5": 100.7e9,
      "isa11": 64.3e9, "isa14": 469.9e9, "isa20": 433.1e9},
     {"yearReport": 2026, "lengthReport": 2, "publicDate": "2026-07-30T00:00:00", "isa3": 406e9, "isa5": 139e9, "isa11": 189e9, "isa14": 264e9, "isa20": 370e9},
     {"yearReport": 2026, "lengthReport": 1, "publicDate": "2026-04-28T00:00:00", "isa3": 468e9, "isa5": 120e9, "isa11": 150e9, "isa14": 180e9, "isa20": 285e9},
     {"yearReport": 2025, "lengthReport": 4, "publicDate": "2026-01-28T00:00:00", "isa3": 499e9, "isa5": 90e9, "isa11": 40e9, "isa14": 10e9, "isa20": 35e9},
     {"yearReport": 2025, "lengthReport": 3, "publicDate": "2025-10-30T00:00:00", "isa3": 617e9, "isa5": 110e9, "isa11": 80e9, "isa14": 240e9, "isa20": 264.5e9}]
BANK_Q = [{"yearReport": 2026, "lengthReport": 3, "publicDate": "2026-10-30T00:00:00", "isb38": 1e12, "isa20": 5e12}]
STATEMENTS = {"PHR": Q, "CTG": BANK_Q}
VCI_DOWN = set()


def fake_statement(symbol, section):
    from tradingagents.dataflows.errors import NoMarketDataError, VendorUnavailableError

    if symbol in VCI_DOWN:
        raise VendorUnavailableError("VCI request failed")
    if symbol not in STATEMENTS:
        raise NoMarketDataError(symbol, symbol, "no statements")
    return {"quarters": [dict(r) for r in STATEMENTS[symbol]], "years": []}


vci.fetch_statement = fake_statement
fund = radar.fundamentals("PHR")
t.ok("fundamentals: the newest quarter, LNST +63,7% YoY, other income 108% of the profit -> one-off",
     fund["period"] == "Q3/2026" and round(fund["yoy"], 1) == 63.7 and round(fund["other_share"], 2) == 1.08 and fund["one_off"]
     and round(fund["gross_margin"], 1) == 15.3 and round(fund["core_yoy"], 1) == -19.6, fund)
t.ok("...a bank: None (no revenue line)", radar.fundamentals("CTG") is None)
lines = radar.figures_lines(fund)
t.ok("level 1 lines: the figures with their source, the one-off flag",
     lines[0] == "LNST Q3/2026 433 tỷ (+63,7% so với cùng kỳ); lợi nhuận hoạt động kinh doanh 64 tỷ (-19,6%); thu nhập khác ròng 470 tỷ; biên gộp 15,3% (VCI)."
     and lines[1].startswith("Thu nhập khác chiếm 108% LNST: lãi quý này chủ yếu không đến từ kinh doanh cốt lõi"), lines)


class Headline:
    def __init__(self, title, source, published):
        self.title, self.source, self.published, self.confirmed = title, source, published, True


NEWS = {"PHR": [Headline("PHR lãi lớn nhờ bán đất cho THACO", "CafeF", datetime(2026, 11, 15)),
                Headline("Giá cao su giảm tuần thứ ba", "VnExpress", datetime(2026, 11, 20))]}
fake_news = lambda code, end: list(NEWS.get(code, []))  # noqa: E731
PROMPTS = []


class FakeLLM:
    def __init__(self, answer):
        self.answer = answer

    def invoke(self, messages):
        PROMPTS.append(messages[-1].content)
        if isinstance(self.answer, Exception):
            raise self.answer

        class Reply:
            content = self.answer
        return Reply()


GOOD = json.dumps({"lines": ["Lãi Q3 433 tỷ chủ yếu từ 470 tỷ thu nhập khác, khoản một lần từ bán đất (VCI, Q3/2026; CafeF, 15/11).",
                             "Lợi nhuận hoạt động cốt lõi 64 tỷ, giảm 19,6% so với cùng kỳ (VCI).",
                             "Chưa có tin hỗ trợ mua ngay; giá cao su vẫn giảm (VnExpress, 20/11)."],
                   "supportive": False, "one_off": True}, ensure_ascii=False)
got = radar.intel("PHR", "2026-11-27", FakeLLM(GOOD), news=fake_news)
t.ok("level 2: the AI's three lines, E scored, one-off", got["level"] == 2 and len(got["lines"]) == 3 and got["supportive"] is False
     and got["one_off"] and got["lines"][0].startswith("Lãi Q3 433 tỷ"), got)
t.ok("...the prompt carries the figures and the headlines with source and day",
     "LNST Q3/2026 433 tỷ" in PROMPTS[-1] and "- PHR lãi lớn nhờ bán đất cho THACO (CafeF, 15/11)" in PROMPTS[-1], PROMPTS[-1])
got = radar.intel("PHR", "2026-11-27", FakeLLM("```json\n" + GOOD + "\n```"), news=fake_news)
t.ok("...JSON inside a code fence still read", got["level"] == 2)
got = radar.intel("PHR", "2026-11-27", FakeLLM(RuntimeError("9Router: 503")), news=fake_news)
t.ok("the AI raising: level 1, the figures, E unscored, one-off by code, the reason",
     got["level"] == 1 and got["supportive"] is None and got["one_off"] and got["lines"][0].startswith("LNST Q3/2026")
     and "9Router: 503" in got["source"], got)
got = radar.intel("PHR", "2026-11-27", FakeLLM("Xin lỗi, tôi không thể."), news=fake_news)
t.ok("the AI answering no JSON: level 1", got["level"] == 1 and "không đúng định dạng" in got["source"], got["source"])
got = radar.intel("PHR", "2026-11-27", None, news=fake_news)
t.ok("no AI configured: level 1, said so", got["level"] == 1 and "chưa cấu hình AI" in got["source"])
got = radar.intel("CTG", "2026-11-27", FakeLLM(GOOD), news=fake_news)
t.ok("a bank with the AI: still level 2 (the AI reads the news; the figures line says none)", got["level"] == 2)
VCI_DOWN.add("PHR")
got = radar.intel("PHR", "2026-11-27", None, news=fake_news)
VCI_DOWN.clear()
t.ok("VCI down, no AI: the block says the figures are unread", got["lines"][0].startswith("Báo cáo tài chính: chưa đọc được"), got)

# ═══════════════ 5. the report ═══════════════
EVENTS = {"PHR": [{"eventCode": "DIV", "exrightDate": "2026-11-13T00:00:00", "valuePerShare": 1500.0,
                   "eventTitleVi": "Trả cổ tức bằng tiền mặt - Đợt 1 2026 - 1,500 VND"}]}
vci.fetch_events = lambda symbol, from_date, to_date, codes="DIV,ISS", size=50, **kw: list(EVENTS.get(symbol, []))
symbols.listing_entry = lambda code: {"symbol": code, "board": "HSX"}
QUOTES = {"VNINDEX": {"price": 1_629.68, "ref": 1_687.45, "volume": None, "day": "2026-11-27"},
          "PHR": {"price": 27_250.0, "ref": 27_450.0, "volume": 49_200, "day": "2026-11-27"}}
quote.stock_quote = lambda code, now=None: {"code": code, "index": code == "VNINDEX", "open": None, "high": None, "low": None,
                                            **QUOTES[code]}
FOREIGN = {"PHR": {"day": "2026-11-26", "net": -15_200.0}}


def fake_morning(code, now):
    q = QUOTES[code]
    return {"at": now.strftime("%H:%M"), "price": q["price"], "ref": q["ref"], "pct": (q["price"] / q["ref"] - 1) * 100,
            "volume": q["volume"], "foreign": FOREIGN.get(code)}


NOW = at("2026-11-27", 9, 30)
text = radar.report(NOW, ["PHR", "NEW", "XXX"], FakeLLM(GOOD), bars=fake_bars, morning=fake_morning, news=fake_news)
lines = text.splitlines()
t.ok("the opening when VN-Index is down hard and a stock is named", lines[0] == "Địch rũ tới chân thành. Chưa thấy tiền thật, ta chưa rút kiếm.")
t.ok("the context line: the time, VN-Index, the named stock's price, change and volume this morning",
     lines[1] == "27/11/2026, 09:30 | VN-Index 1.629,68 (-3,42%) | PHR 27.250 (-0,73%), KL 49.200 cp", lines[1])
t.ok("the scan line: the scope, the signal day, the named with its score, the near misses with theirs",
     lines[3] == "Radar quét VN100 + watchlist (5 mã) trên nến ngày 26/11: 1 mã đạt chuẩn (từ 70/100): PHR 80. Gần ngưỡng: CTG 40, UPP 40."
     and lines[4] == "Không đọc được: XXX", lines[3:5])
t.ok("Chiến sự: RSI oversold with the divergence, under EMA20 by %, support and resistance, the stopping volume, VN-Index",
     "━━ PHR · 80/100 ━━" in text and "Kỹ thuật: RSI(14) " in text and "quá bán, có phân kỳ dương. Giá nằm dưới EMA20 (" in text
     and ". Hỗ trợ 26.500, kháng cự " in text and "(30 phiên)." in text
     and "Lực cầu: nến phiên trước rút chân 77% với volume gấp 1,8 lần MA20, dấu hiệu bên mua chặn đà rơi (stopping volume)." in text
     and "So với thị trường: VN-Index mất 3,42% còn PHR chỉ mất 0,73%, tức PHR chịu đòn tốt hơn mặt bằng chung." in text, text)
t.ok("Tai mắt: the AI's lines, the signal-day note with this morning's volume against MA20 and the foreign session",
     "Tai mắt\nLãi Q3 433 tỷ chủ yếu từ 470 tỷ thu nhập khác" in text
     and "Tín hiệu được chấm trên nến ngày 26/11. Sáng nay KL tới 09:30: 49.200 cp (10% MA20); khối ngoại phiên 26/11 bán ròng 15.200 cp." in text, text)
t.ok("the ex-date inside the window, with the amount",
     "Ngày 13/11 là GDKHQ (Trả cổ tức bằng tiền mặt - Đợt 1 2026 - 1,500 VND 1.500 đ/cp): phần giá giảm hôm đó là chia, không phải bán." in text, text)
t.ok("Checklist papa: A B ✓, E ✗ from the AI, G ✗ with the MA note, C ✓ with the value, the verdict",
     "Mẫu hình + phân kỳ (A, B): ✓ ✓\nTin hỗ trợ (E): ✗\nMA (G): ✗, giá đang dưới cả EMA20, chưa nói đến MA50 và MA100\n"
     "Thanh khoản TB20 trên 5 tỷ (C): ✓ (" in text and "Tổng thể: đây là điểm uốn kỹ thuật, chưa phải mã đủ chuẩn để mua theo checklist." in text, text)
t.ok("Quân lệnh: the probe 2-3% over the support at HOSE ticks, the stop 2% under, the target EMA20, R:R, the rules, the warning",
     "PHR đóng cửa giữ trên 26.500 và volume từ 1,5x MA20 trở lên → mua thăm dò 5% NAV ở 27.050–27.300, stop 25.950, mục tiêu " in text
     and "(EMA20). R:R khoảng " in text and "Thủng 26.500 → bỏ, vì nền bị phá." in text
     and "Không mua đuổi trong phiên. Không bình quân giá xuống dưới 25.950." in text
     and "Lãi chủ yếu từ khoản khác, giá nằm dưới EMA20: đây là cú bật kỹ thuật, chưa phải đổi xu hướng. Chưa có xác nhận lúc ATC thì vào lệnh nghiêng về cờ bạc." in text, text)
t.ok("the footer explains the points and names /nhanh", lines[-1].startswith("Điểm: RSI ≤ 30 20 · phân kỳ 20") and "/nhanh MÃ" in lines[-1])
text = radar.report(NOW, ["PHR"], FakeLLM(RuntimeError("down")), bars=fake_bars, morning=fake_morning, news=fake_news)
t.ok("the AI down: level 1 in the block, said so, E unscored",
     "Tai mắt\nLNST Q3/2026 433 tỷ" in text and "(Mức 1: số liệu VCI, AI không trả lời (down).)" in text
     and "Tin hỗ trợ (E): chưa chấm (AI không trả lời)" in text, text)
text = radar.report(NOW, ["PHR"], None, bars=fake_bars, morning=fake_morning, news=fake_news, score_min=90)
t.ok("nobody over the threshold: the quiet opening, the scan line says so, no block",
     text.splitlines()[0] == "Radar im lặng. Không mã nào đạt chuẩn, hôm nay giữ tiền là một vị thế."
     and "không mã nào đạt 90/100. Gần ngưỡng: PHR 80, CTG 40, UPP 40." in text and "━━ PHR" not in text, text)
QUOTES["VNINDEX"] = {"price": 1_700.0, "ref": 1_687.45, "volume": None, "day": "2026-11-27"}
text = radar.report(NOW, ["PHR"], None, bars=fake_bars, morning=fake_morning, news=fake_news)
t.ok("VN-Index up this morning: the calm opening", text.splitlines()[0] == "Trận địa yên. Radar có mã, chờ ATC xác nhận rồi mới tính.", text.splitlines()[0])
QUOTES["VNINDEX"] = {"price": 1_629.68, "ref": 1_687.45, "volume": None, "day": "2026-11-27"}
text = radar.report(at("2026-11-27", 16, 0), ["PHR"], None, bars=fake_bars, morning=fake_morning, news=fake_news)
t.ok("after 15:05 the signal day is today", "trên nến ngày 27/11" in text or "trên nến ngày 26/11" in text)
t.ok("orders(): HNX ticks of 100", radar.orders(s, "HNX")["entry_low"] == 27_000 and radar.orders(s, "HOSE")["entry_low"] == 27_050)

# ═══════════════ 6. the scheduler ═══════════════
radar._bars = fake_bars
radar._morning = fake_morning
radar.headlines = fake_news
SENT = []
sched = DailyScheduler(wh.SETTINGS, wh.JobQueue(wh.SETTINGS), wh.Watchlist(wh.SETTINGS), notify=SENT.append,
                       critic_llm=FakeLLM(GOOD))
sched.watchlist.set(["PHR"])
CLOCK = {"t": at("2026-11-27", 9, 29)}
vn_rules.now_vn = lambda: CLOCK["t"]
t.ok("09:29: not due", sched._radar_due() is None)
CLOCK["t"] = at("2026-11-27", 9, 30)
t.ok("09:30 on a session day: due", sched._radar_due() == "2026-11-27")
sched.tick()
t.ok("the tick sends the report to the report chats and stamps the day",
     len(SENT) == 1 and SENT[0].startswith("Địch rũ tới chân thành") and "━━ PHR · 80/100 ━━" in SENT[0]
     and (DATA / "last_radar.txt").read_text(encoding="utf-8") == "2026-11-27", [x[:60] for x in SENT])
sched.tick()
t.ok("...once a day", len(SENT) == 1)
(DATA / "last_radar.txt").unlink()
CLOCK["t"] = at("2026-11-27", 11, 30)
t.ok("11:30: too late, skipped", sched._radar_due() is None)
CLOCK["t"] = at("2026-11-28", 9, 40)
t.ok("a Saturday: not due", sched._radar_due() is None)
CLOCK["t"] = at("2026-11-27", 9, 40)
radar._bars = lambda code, day: (_ for _ in ()).throw(RuntimeError("DNSE down"))
mf.index_members = lambda group="VN30": (_ for _ in ()).throw(RuntimeError("VCI down"))
SENT.clear()
sched.tick()
t.ok("a failure: nothing sent, retried in 10 minutes", SENT == [] and sched._radar_tries == 1 and sched._radar_next > time.time() + 500)
sched._radar_next = 0.0
sched.tick()
sched._radar_next = 0.0
sched.tick()
t.ok("the third failure: the owner told, the day stamped", len(SENT) == 1 and SENT[0].startswith("⚠️ Radar sáng 27/11 không chạy được sau 3 lần")
     and (DATA / "last_radar.txt").read_text(encoding="utf-8") == "2026-11-27", SENT)
mf.index_members = lambda group="VN30": ["PHR", "UPP", "CTG"] if group == "VN100" else ["UPP"]
radar._bars = fake_bars
off = DailyScheduler(wh.SETTINGS.__class__(**{**wh.SETTINGS.__dict__, "radar": False}), wh.JobQueue(wh.SETTINGS),
                     wh.Watchlist(wh.SETTINGS), notify=SENT.append)
(DATA / "last_radar.txt").unlink()
t.ok("RADAR=0: never due", off._radar_due() is None)

# ═══════════════ 7. Telegram and Zoom ═══════════════
TG = []
bot = tb.TelegramBot(wh.SETTINGS, wh.JobQueue(wh.SETTINGS), wh.Watchlist(wh.SETTINGS))
bot._call = lambda method, http_timeout=30, **params: TG.append((method, params)) or {}
bot._critic_llm = FakeLLM(GOOD)
bot.watchlist.set(["PHR"])


def sent(chat):
    return [p["text"] for m, p in TG if m == "sendMessage" and p["chat_id"] == chat]


bot._handle({"chat": {"id": 111}, "from": {"id": 111}, "text": "/radar"})
t.ok("Telegram /radar: the progress line, then the report", sent(111)[0].startswith("⏳ Radar đang quét VN100 + watchlist")
     and "━━ PHR · 80/100 ━━" in sent(111)[1], [x[:60] for x in sent(111)])
bot._handle({"chat": {"id": 111}, "from": {"id": 111}, "text": "/radar FPT"})
t.ok("/radar with a word: the usage", sent(111)[-1] == "⚠️ Dùng: /radar (quét VN100 + watchlist ngay, như bản tin 09:30).", sent(111)[-1])
bot._handle({"chat": {"id": 222}, "from": {"id": 222}, "text": "/radar"})
t.ok("a stranger: refused", sent(222)[-1].startswith("Bot chưa cho phép"))
t.ok("the help names /radar, still one message", "/radar - radar sáng" in bot._help() and len(bot._help()) <= tb.MAX_MESSAGE)
from vnbot import pages  # noqa: E402

t.ok("/docs lists radar", "<code>radar</code>" in pages.docs_body())

app, zoom, handled, _, _, sw = wh.build()
zoom.handler = bot.zoom_command
bot.zoom = zoom
zoom.pair("a@xmpp.zoom.us", "A")
from fastapi.testclient import TestClient  # noqa: E402

zc = TestClient(app, raise_server_exceptions=False)
body = json.dumps({"event": "bot_notification", "payload": {
    "userJid": "a@xmpp.zoom.us", "cmd": "radar", "userName": "A", "accountId": "acc", "toJid": "bot@xmpp.zoom.us",
    "messageId": "m1"}}).encode()
zc.post("/webhook/zoom", content=body, headers=wh.sign("zsecret", body))
end = time.monotonic() + 10
got = []
while time.monotonic() < end and len(got) < 2:
    if not zoom.queue.empty():
        got.append(zoom.queue.get_nowait()[1])
    else:
        time.sleep(0.02)
t.ok("Zoom 'radar': the report back to Zoom", any("━━ PHR · 80/100 ━━" in g for g in got), [g[:60] for g in got])

t.done()
