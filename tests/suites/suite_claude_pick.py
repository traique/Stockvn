"""Claude chọn bóng (vnbot.claude_pick): the candidates (the site's and the scan's), the
holdings, the request, the answer kept to what can be checked, the scoring (10 sessions and
the rule), the comparisons and their checkpoints, the spending cap, the alert notes, the
weekly review, the scheduler, /claude on Telegram and the /claude page (offline: a fake
Anthropic client, fake bars, no money spent)."""
import json
import os
import time
from datetime import datetime
from types import SimpleNamespace

import _common  # noqa: F401 - clean env, no .env, offline, temp folder, repo on sys.path
_common.need("httpx")

os.environ.update({"ANTHROPIC_API_KEY": "sk-ant-test-0123456789abcdef", "SIEUTINHIEU": "1",
                   "TELEGRAM_TOKEN": "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawQ", "TELEGRAM_ALLOWED_IDS": "111"})

from _web_harness import SETTINGS, build  # noqa: E402 - sets DATA_DIR and the web password
from fastapi.testclient import TestClient  # noqa: E402
from tradingagents.market import vn_rules  # noqa: E402
from vnbot import claude_pick as cp  # noqa: E402
from vnbot import external  # noqa: E402
from vnbot import trend_estimate as te  # noqa: E402
from vnbot.taisan import TaiSan  # noqa: E402

t = _common.Checks()
NOW = {"t": datetime(2026, 10, 23, 15, 55, tzinfo=vn_rules.VN_TZ)}
vn_rules.now_vn = lambda: NOW["t"]
DAY = "2026-10-23"          # a Friday: the week's last session


def at(day, hhmm):
    h, m = map(int, hhmm.split(":"))
    y, mo, d = map(int, day.split("-"))
    return datetime(y, mo, d, h, m, tzinfo=vn_rules.VN_TZ)


def sessions(first: str, n: int) -> list[str]:
    days = [first]
    while len(days) < n:
        days.append(vn_rules.add_sessions(days[-1], 1))
    return days


def bars_for(days, opens, lows=None, closes=None, vol=1_000_000):
    lows = lows or opens
    closes = closes or opens
    return [{"d": d, "o": float(o), "h": float(max(o, c)) * 1.01, "l": float(lo), "c": float(c), "v": float(vol)}
            for d, o, lo, c in zip(days, opens, lows, closes, strict=True)]


def sig(symbol, day, side, price, *, sl=None, tp1=None, strength=80, live=True, created=None):
    return {"symbol": symbol, "day": day, "side": side, "price": price, "sl": sl, "tp1": tp1,
            "entry_low": price * 0.99 if side == "BUY" else None, "entry_high": price * 1.01 if side == "BUY" else None,
            "strength_pct": strength, "live": live, "created": created or f"{day}T15:08"}


class Store(external.SignalStore):
    """The real store (file and ``state``), with the site never asked."""

    def refresh(self, symbols, **kwargs):
        self.refreshed = list(symbols)
        return 0, ""

    def put(self, symbol, signals, fetched=f"{DAY}T15:20"):
        data = self._read()
        data["symbols"][symbol] = {"fetched": fetched,
                                   "signals": sorted(signals, key=lambda s: (s["day"], s["created"]), reverse=True)}
        self._write(data)


def usage(inp=1000, out=500, searches=0):
    return SimpleNamespace(input_tokens=inp, output_tokens=out, cache_creation_input_tokens=0, cache_read_input_tokens=0,
                           server_tool_use=SimpleNamespace(web_search_requests=searches))


def response(content, stop, use=None, model="claude-opus-5-5"):
    return SimpleNamespace(content=content, stop_reason=stop, usage=use or usage(), model=model)


def found(*urls):
    return SimpleNamespace(type="web_search_tool_result",
                           content=[SimpleNamespace(type="web_search_result", url=u, title="t") for u in urls])


def submit(data, name=cp.TOOL):
    return SimpleNamespace(type="tool_use", name=name, id="tu_1", input=data)


def text_block(text):
    return SimpleNamespace(type="text", text=text, citations=None)


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


# ───────────── 1. configuration ─────────────
t.ok("on with the key and the site's signals", cp.enabled() and cp.why_off() == "")
os.environ["SIEUTINHIEU"] = "0"
t.ok("off without the site's signals, says why", not cp.enabled() and "SIEUTINHIEU" in cp.why_off())
os.environ["SIEUTINHIEU"] = "1"
key = os.environ.pop("ANTHROPIC_API_KEY")
t.ok("off without the key, says why", not cp.enabled() and "ANTHROPIC_API_KEY" in cp.why_off())
os.environ["ANTHROPIC_API_KEY"] = key
os.environ["CLAUDE_PICK"] = "0"
t.ok("CLAUDE_PICK=0 switches it off", not cp.enabled() and "CLAUDE_PICK=0" in cp.why_off())
del os.environ["CLAUDE_PICK"]
t.ok("defaults: Opus 5.5, medium, 15 USD a month, 10 searches, 15:50, the scan's BUYs, 5 alert notes, weekly",
     (cp.model(), cp.effort(), cp.monthly_cap(), cp.max_searches(), cp.pick_time(), cp.bot_candidates_on(),
      cp.alerts_per_day(), cp.weekly_on()) == ("claude-opus-5-5", "medium", 15.0, 10, "15:50", True, 5, True))
os.environ.update({"CLAUDE_PICK_EFFORT": "turbo", "CLAUDE_PICK_SEARCHES": "500", "CLAUDE_PICK_TIME": "14:00",
                   "CLAUDE_PICK_ALERTS": "99", "STV_MARKET": "0"})
t.ok("bad values fall back or are bounded; never before the site's final signals; no scan, no scan BUYs",
     (cp.effort(), cp.max_searches(), cp.pick_time(), cp.alerts_per_day(), cp.bot_candidates_on())
     == ("medium", 20, external.SITE_FINAL_TIME, 20, False))
for k in ("CLAUDE_PICK_EFFORT", "CLAUDE_PICK_SEARCHES", "CLAUDE_PICK_TIME", "CLAUDE_PICK_ALERTS", "STV_MARKET"):
    del os.environ[k]
t.ok("cost: tokens at the model's rates plus 1 cent a search",
     cp.cost_usd("claude-opus-5-5", {"input": 1_000_000, "output": 100_000, "searches": 5}) == 4 + 2 + 0.05)
t.ok("an unknown model is costed at the dearest rates", cp.cost_usd("claude-new", {"input": 1_000_000}) == 10.0)

# ───────────── 2. candidates and figures ─────────────
store = Store(SETTINGS.data_dir)
store.put("GEX", [sig("GEX", "2026-09-08", "SELL", 24550), sig("GEX", DAY, "BUY", 23850, sl=22400, tp1=26000, strength=90)])
store.put("CII", [sig("CII", DAY, "BUY", 12950, sl=12200, tp1=14100, strength=91)])
store.put("FPT", [sig("FPT", "2026-09-18", "SELL", 65180)])                                      # no new BUY
store.put("HPG", [sig("HPG", "2026-10-22", "BUY", 27000, sl=25500)])                             # yesterday's
store.put("OLD", [sig("OLD", DAY, "BUY", 10000, sl=9000, live=False)])                           # regenerated
store.put("LATE", [sig("LATE", DAY, "BUY", 10000, sl=9000, created="2026-10-26T09:30")])         # after the next open
WATCH = ["GEX", "CII", "FPT", "HPG", "OLD", "LATE"]
cands = cp.candidates(DAY, store, WATCH)
t.ok("only the session's live BUYs created before the next open, strongest first, marked the site's",
     [c["symbol"] for c in cands] == ["CII", "GEX"] and {c["source"] for c in cands} == {"site"}, cands)
t.ok("the site's levels are carried", cands[1]["sl"] == 22400 and cands[1]["tp1"] == 26000 and cands[1]["strength"] == 90)

days = sessions("2025-10-01", 260)
closes = [20000 + i * 20 for i in range(260)]
f = cp.features(bars_for(days, closes, vol=1_000_000))
t.ok("figures from the closes: last close, changes, gaps to the averages, value traded",
     f["close"] == closes[-1] and f["chg1"] == round((closes[-1] / closes[-2] - 1) * 100, 1)
     and f["ma200"] > 0 and f["value_bn"] == round(sum(closes[-20:]) * 1_000_000 / 20 / 1e9, 1) and f["vol_ratio"] == 1.0, f)
t.ok("too few sessions: no figures", cp.features(bars_for(days[:1], closes[:1])) == {})
line = cp.candidate_line(1, {**cands[1], "features": f})
t.ok("the prompt line: VND prices, the site's plan with its distance, the figures",
     line.startswith("1. GEX · trang MUA · tín hiệu 23.850 · độ mạnh 90%") and "mục tiêu 26.000 (+9,0%)" in line
     and "cắt lỗ 22.400 (-6,1%)" in line and "TB200" in line and "cách đỉnh 52 tuần" in line, line)


class FakeCloses:
    """The market scan's closes (``market_scan.MarketCloses``), in memory."""

    def __init__(self, series, day, values):
        self._series, self.day, self.values = series, day, values
        self.updated = []

    def update(self, through, **kwargs):
        self.updated.append(through)
        return 0, ""

    def codes_on(self, day):
        return set(self._series) if day == self.day else set()

    def universe(self, size, as_of):
        return sorted(self._series, key=lambda c: -self.values[c])[:size]

    def series(self, codes, as_of):
        return {c: self._series[c] for c in codes}, {}, {c: self.values[c] for c in codes}


down = [30000 - i * 40 for i in range(129)]
flip = down + [down[-1] * 1.12]
stay = [30000 - i * 40 for i in range(130)]
up = [20000 + i * 40 for i in range(130)]
t.ok("test data: the rule turns BUY on the last close of 'flip' only",
     te.levels(flip[:-1])["rule"] == "SELL" and te.levels(flip)["rule"] == "BUY" and te.levels(stay)["rule"] == "SELL"
     and te.levels(up[:-1])["rule"] == "BUY" and te.levels(up)["rule"] == "BUY")
scan = FakeCloses({"FLP": flip, "STY": stay, "UPP": up, "GEX": flip, "SML": flip},
                  DAY, {"FLP": 9e10, "STY": 8e10, "UPP": 7e10, "GEX": 6e10, "SML": 1e9})
bot = cp.bot_candidates(DAY, scan, 300, exclude=["GEX"], limit=15)
t.ok("the scan's candidates: turned BUY at the close, not already BUY or still SELL, not a site's BUY, most traded first",
     [c["symbol"] for c in bot] == ["FLP", "SML"] and all(c["source"] == "bot" and c["sl"] is None for c in bot), bot)
t.ok("their figures come from the scan's closes (no download): value traded, no 52-week claim on 130 closes",
     bot[0]["features"]["value_bn"] == 90.0 and bot[0]["features"]["high_span"] == 130
     and "cách đỉnh 130 phiên" in cp.candidate_line(3, bot[0]) and "bot ước tính" in cp.candidate_line(3, bot[0]))
t.ok("no close of the session in the scan: no candidates", cp.bot_candidates("2026-10-22", scan, 300, []) == [])
t.ok("the limit holds", len(cp.bot_candidates(DAY, scan, 300, [], limit=1)) == 1)

# ───────────── 3. the holdings ─────────────
TS = SETTINGS.data_dir / "acct"
account = TaiSan(TS)
account.add("DEPOSIT", "2026-05-04", amount=9_000_000)
account.add("BUY", "2026-05-12", symbol="CII", qty=200, price=19552)
account.add("BUY", "2026-06-09", symbol="FPT", qty=20, price=73891)
account.add("BUY", "2026-06-30", symbol="GEX", qty=50, price=31300)
account.set_levels("CII", 12200, None)
account.save_closes({"CII": {DAY: 13950}, "FPT": {DAY: 57000}, "GEX": {DAY: 24100}})
BARS = {}


def fake_bars(symbol, as_of):
    return [b for b in BARS.get(symbol, []) if b["d"] <= as_of]


hist = [DAY]
while len(hist) < 260:                                  # 260 sessions up to and including DAY
    hist.insert(0, vn_rules.prev_session(hist[0]))
BARS["GEX"] = bars_for(hist, [23000 + (i % 7) * 50 for i in range(len(hist))])
BARS["CII"] = bars_for(hist, [12500 + (i % 5) * 40 for i in range(len(hist))])
BARS["FPT"] = bars_for(hist, [60000 - i * 10 for i in range(len(hist))])
BARS["VNINDEX"] = bars_for(hist, [1700 + i * 0.3 for i in range(len(hist))])
held = cp.holdings_context(account, DAY, store, fake_bars)
by = {h["symbol"]: h for h in held}
t.ok("holdings as the code sees them: the cost as entered (fees in), the saved close, the P&L on it",
     by["FPT"]["avg"] == 73891 and by["FPT"]["close"] == 57000 and by["FPT"]["pnl_pct"] == round((57000 / 73891 - 1) * 100, 2))
t.ok("the rules: 🔴 the site's SELL is BÁN, a BUY without a level crossed is GIỮ",
     by["FPT"]["rule"] == "BÁN" and "🔴" in by["FPT"]["rule_why"] and by["GEX"]["rule"] == "GIỮ")
cash = 9_000_000 - 200 * 19552 - 20 * 73891 - 50 * 31300
stocks = 200 * 13950 + 20 * 57000 + 50 * 24100
t.ok("the weights: each holding at its close over the stocks and the cash",
     abs(sum(h["weight"] for h in held) - stocks / (stocks + cash) * 100) < 0.2
     and by["CII"]["weight"] == round(200 * 13950 / (stocks + cash) * 100, 1), [h["weight"] for h in held])
t.ok("⛔ under the cut-loss is BÁN", cp.rule_for(12000, 12200, None, {"side": "BUY", "day": DAY, "price": 1})[0] == "BÁN")
hline = cp.holding_line(by["FPT"])
t.ok("the holding line: cost as entered, P&L, the rule", "giá vốn 73.891 (chủ tài khoản nhập, đã gồm phí)" in hline
     and "Quy tắc của bot: BÁN" in hline and "trang BÁN từ 18/09" in hline, hline)

# ───────────── 4. the request ─────────────
codes = ["CII", "GEX"]
tool = cp.submit_tool(codes, ["CII", "FPT", "GEX"])
args = cp.request_args("claude-opus-5-5", tool, 8, "medium")
tools = {x["name"]: x for x in args["tools"]}
search = tools["web_search"]
props = tools[cp.TOOL]["input_schema"]["properties"]
t.ok("web search: the 2026 tool, Vietnamese financial sites only, bounded",
     search["type"] == "web_search_20260209" and search["max_uses"] == 8 and "cafef.vn" in search["allowed_domains"])
t.ok("the answer tool is strict and only accepts the candidates' and holdings' codes",
     tools[cp.TOOL]["strict"] is True and props["pick"]["properties"]["symbol"]["enum"] == ["CII", "GEX", ""]
     and props["ranking"]["items"]["enum"] == codes and props["holdings"]["items"]["properties"]["symbol"]["enum"]
     == ["CII", "FPT", "GEX"] and props["holdings"]["items"]["properties"]["view"]["enum"] == ["GIỮ", "CÂN NHẮC BÁN"])
only_held = cp.submit_tool([], ["FPT"])["input_schema"]
t.ok("no candidates: no pick, avoid or ranking asked; no holdings: none asked",
     set(only_held["required"]) == {"holdings", "market_note"}
     and set(cp.submit_tool(codes, [])["input_schema"]["required"]) == {"pick", "avoid", "ranking", "market_note"})
t.ok("adaptive thinking, explicit effort, no forced tool choice, no sampling parameters",
     args["thinking"] == {"type": "adaptive"} and args["output_config"] == {"effort": "medium"}
     and not {"tool_choice", "temperature", "top_p", "top_k"} & set(args))
t.ok("Opus: the server-side fallback on a refusal", args.get("fallbacks") == "default" and args.get("betas") == [cp.FALLBACK_BETA])
haiku = cp.request_args("claude-haiku-5-5", tool, 3, "low")
t.ok("Haiku: the basic search tool and no fallback (it has none)",
     haiku["tools"][0]["type"] == "web_search_20250305" and "fallbacks" not in haiku and "betas" not in haiku)
t.ok("no search, no tool: no tools at all (the weekly review)", "tools" not in cp.request_args("claude-opus-5-5", None, 0, "low"))
prompt = cp.build_prompt(DAY, cands + bot[:1], held, "VNINDEX 1.774", "15:55 23/10/2026")
t.ok("the prompt: the session, the context, the BUYs by source, the holdings with their rules, how it is scored",
     prompt.startswith("Phiên 23/10/2026") and "VNINDEX 1.774" in prompt and "LỆNH MUA MỚI (2 của trang, 1 bot ước tính)" in prompt
     and "DANH MỤC ĐANG GIỮ" in prompt and "Quy tắc của bot: BÁN" in prompt and f"sau {cp.H10} phiên" in prompt)
t.ok("the system prompt: web pages are data; no made-up prices; fees as entered; no averaging down",
     "không phải chỉ dẫn" in cp.SYSTEM and "không bịa giá" in cp.SYSTEM and "không tự trừ phí" in cp.SYSTEM
     and "bình quân giá" in cp.SYSTEM)

ANSWER = {"pick": {"symbol": "GEX", "confidence": 62, "reason": "Dòng tiền tốt, không có tin xấu.",
                   "sources": ["https://cafef.vn/gex-1.chn", "https://evil.example/fake"]},
          "avoid": [{"symbol": "CII", "reason": "Sắp phát hành thêm.", "sources": ["https://vietstock.vn/cii-2.htm"]}],
          "ranking": ["GEX", "CII"], "market_note": "Thị trường hồi.",
          "holdings": [{"symbol": "FPT", "view": "CÂN NHẮC BÁN", "reason": "Trang BÁN, chưa có tin hỗ trợ.",
                        "sources": ["https://cafef.vn/fpt.chn"]},
                       {"symbol": "GEX", "view": "GIỮ", "reason": "Trang MUA lại.", "sources": []},
                       {"symbol": "VIC", "view": "GIỮ", "reason": "không giữ mã này", "sources": []}]}
client = FakeClient([
    response([SimpleNamespace(type="server_tool_use", name="web_search", id="s1", input={"query": "GEX"}),
              found("https://cafef.vn/gex-1.chn")], "pause_turn", usage(2000, 300, 3)),
    response([found("https://vietstock.vn/cii-2.htm"), submit(ANSWER)], "tool_use", usage(3000, 700, 2)),
])
raw, meta = cp.ask(prompt, tool, client=client)
t.ok("a paused search loop is resumed with the paused turn appended (no extra user message)",
     len(client.calls) == 2 and client.calls[1]["messages"][-1]["role"] == "assistant"
     and len(client.calls[1]["messages"]) == 2, [m["role"] for m in client.calls[1]["messages"]])
t.ok("the answer comes from submit_picks", raw == ANSWER)
t.ok("usage summed over the requests, cost at Opus 5.5 rates with the searches",
     meta["usage"]["input"] == 5000 and meta["usage"]["output"] == 1000 and meta["usage"]["searches"] == 5
     and meta["cost"] == round(5000 * 4 / 1e6 + 1000 * 20 / 1e6 + 0.05, 4), meta)
t.ok("the pages the searches returned are known", meta["urls"] == {"cafef.vn/gex-1.chn", "vietstock.vn/cii-2.htm"})

kept = cp.clean(ANSWER, cands, meta["urls"], ["CII", "FPT", "GEX"])
t.ok("a source the searches did not return is dropped", kept["pick"]["sources"] == ["https://cafef.vn/gex-1.chn"]
     and kept["views"]["FPT"]["sources"] == [])
t.ok("pick, avoid, ranking, note and the views on holdings kept (not on a stock not held)",
     kept["pick"]["symbol"] == "GEX" and kept["avoid"][0]["symbol"] == "CII" and kept["ranking"] == ["GEX", "CII"]
     and set(kept["views"]) == {"FPT", "GEX"} and kept["views"]["FPT"]["view"] == "CÂN NHẮC BÁN")
odd = cp.clean({"pick": {"symbol": "VIC", "confidence": 900, "reason": "x", "sources": []},
                "avoid": [{"symbol": "GEX", "reason": "a", "sources": []}, {"symbol": "GEX", "reason": "b", "sources": []},
                          {"symbol": "ZZZ", "reason": "c", "sources": []}],
                "ranking": ["GEX"], "market_note": "", "holdings": [{"symbol": "FPT", "view": "MUA THÊM", "reason": "x"}]},
               cands, set(), ["FPT"])
t.ok("a code outside the candidates is no pick; avoid de-duplicated; ranking completed; a view outside the two dropped",
     odd["pick"]["symbol"] == "" and odd["pick"]["confidence"] == 0 and [a["symbol"] for a in odd["avoid"]] == ["GEX"]
     and odd["ranking"] == ["GEX", "CII"] and odd["views"] == {}, odd)
t.ok("the pick heads the ranking; a picked code is never also avoided",
     cp.clean({**ANSWER, "ranking": ["CII", "GEX"], "avoid": []}, cands, set())["ranking"][0] == "GEX"
     and cp.clean({**ANSWER, "avoid": [{"symbol": "GEX", "reason": "x", "sources": []}]}, cands, set())["avoid"] == [])

nudge = FakeClient([response([text_block("Tôi chọn GEX.")], "end_turn"), response([submit(ANSWER)], "tool_use")])
raw, _ = cp.ask(prompt, tool, client=nudge)
t.ok("an answer in plain text: asked once more for submit_picks", raw == ANSWER
     and nudge.calls[1]["messages"][-1] == {"role": "user", "content": cp.NUDGE.format(tool=cp.TOOL)})
text, _ = cp.ask("tuần", None, client=FakeClient([response([text_block("• Tốt.")], "end_turn")]), searches=0)
t.ok("without a tool: the text of the answer", text == "• Tốt.")
try:
    cp.ask(prompt, tool, client=FakeClient([response([], "refusal", usage(800, 0))]))
    t.ok("a refusal is an error", False)
except cp.PickError as exc:
    t.ok("a refusal is an error that still carries what was spent", "từ chối" in str(exc)
         and exc.meta["cost"] == round(800 * 4 / 1e6, 4))
try:
    cp.ask(prompt, tool, client=FakeClient([response([text_block("?")], "end_turn")] * 2))
    t.ok("no submit_picks after the nudge is an error", False)
except cp.PickError as exc:
    t.ok("no submit_picks after the nudge is an error", "submit_picks" in str(exc))
try:
    import anthropic
    import httpx2
except ImportError:
    anthropic = None
    print("skip: anthropic not installed (the Retry check and the SDK round trip)")
if anthropic is not None:
    def conn_error():
        return anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))

    try:
        cp.ask(prompt, tool, client=FakeClient([conn_error()]))
        t.ok("a network failure before anything was paid is Retry", False)
    except cp.Retry:
        t.ok("a network failure before anything was paid is Retry (tried again later, nothing recorded)", True)
    try:
        cp.ask(prompt, tool, client=FakeClient([response([found("https://x.vn/a")], "pause_turn", usage(900, 100)),
                                                conn_error()]))
        t.ok("a network failure after a paid turn is recorded with its cost", False)
    except cp.PickError as exc:
        t.ok("a network failure after a paid turn is recorded with its cost (never asked again for free)",
             exc.meta["cost"] > 0)

    sent_bodies = []
    replies = [
        {"content": [{"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search", "input": {"query": "GEX"}},
                     {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1",
                      "content": [{"type": "web_search_result", "url": "https://cafef.vn/gex-1.chn", "title": "GEX",
                                   "encrypted_content": "x", "page_age": None}]}],
         "stop_reason": "pause_turn"},
        {"content": [{"type": "text", "text": "Xong.", "citations": [
                          {"type": "web_search_result_location", "url": "https://vietstock.vn/cii-2.htm", "title": "a",
                           "encrypted_index": "x", "cited_text": "y"}]},
                     {"type": "tool_use", "id": "toolu_1", "name": cp.TOOL, "input": ANSWER}],
         "stop_reason": "tool_use"},
    ]

    def handler(request):
        sent_bodies.append((dict(request.headers), json.loads(request.content)))
        reply = replies.pop(0)
        return httpx2.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "stop_sequence": None,
            **reply, "usage": {"input_tokens": 1000, "output_tokens": 200, "cache_creation_input_tokens": 0,
                               "cache_read_input_tokens": 0, "server_tool_use": {"web_search_requests": 2, "web_fetch_requests": 0}}})

    sdk = anthropic.Anthropic(api_key="sk-ant-x", max_retries=0,
                              http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
    raw, meta = cp.ask(prompt, tool, client=sdk)
    headers, body = sent_bodies[0]
    t.ok("SDK: the fallback beta header and the request body as the API expects",
         "server-side-fallback-2026-07-01" in headers.get("anthropic-beta", "") and body["fallbacks"] == "default"
         and body["thinking"] == {"type": "adaptive"} and body["output_config"] == {"effort": "medium"}
         and [x["name"] for x in body["tools"]] == ["web_search", cp.TOOL] and body["tools"][1]["strict"] is True, body.keys())
    second = sent_bodies[1][1]["messages"]
    t.ok("SDK: the paused turn goes back as it came (the search blocks serialized)",
         [m["role"] for m in second] == ["user", "assistant"]
         and [b["type"] for b in second[1]["content"]] == ["server_tool_use", "web_search_tool_result"], second[-1:])
    t.ok("SDK: the answer, the usage and the pages (search results and citations) read from the typed reply",
         raw == ANSWER and meta["usage"]["searches"] == 4 and meta["usage"]["input"] == 2000
         and meta["urls"] == {"cafef.vn/gex-1.chn", "vietstock.vn/cii-2.htm"}, meta)

# ───────────── 5. scoring ─────────────
after = sessions("2026-10-26", 70)
cand = {"symbol": "GEX", "sl": 22400}
r = cp.settle_one(cand, DAY, bars_for(after[:5], [23900, 23000, 22000, 23000, 23500],
                                       lows=[23000, 22000, 21900, 22800, 23200]), [])
t.ok("rule: cut-loss from T+2 only (the T+1 low is ignored), T+2 sells at the level",
     r["status"] == "closed" and r["sessions"] == 2 and r["exit"] == 22000 and r["why"] == "chạm cắt lỗ", r)
r = cp.settle_one(cand, DAY, bars_for(after[:4], [23900, 24000, 24100, 22000], lows=[23800, 23900, 24000, 21800]), [])
t.ok("rule: a gap below the cut-loss sells at the open", r["exit"] == 22000 and r["sessions"] == 3, r)
r = cp.settle_one(cand, DAY, bars_for(after[:6], [23900, 24500, 25000, 25500, 26000, 26500]), [after[3]])
t.ok("rule: the site's SELL, sold at the next open", r["status"] == "closed" and r["exit"] == 26000 and r["why"] == "trang BÁN", r)
r = cp.settle_one(cand, DAY, bars_for(after[:4], [23900, 24500, 25000, 25500], closes=[24000, 24600, 25100, 25600]), [after[0]])
t.ok("rule: a SELL before T+2, sold at the close of T+2", r["exit"] == 25100 and r["sessions"] == 2 and "T+2" in r["why"], r)
r = cp.settle_one(cand, DAY, bars_for(after[:62], [23900] * 62), [])
t.ok(f"rule: closed after {cp.MAX_HOLD} sessions", r["status"] == "closed" and r["sessions"] == cp.MAX_HOLD, r)
t.ok("rule: before the first session, nothing", cp.settle_one(cand, DAY, [], []) is None)
r = cp.settle_one(cand, DAY, bars_for(after[:6], [23900, 24500, 25000, 25500, 26000, 26500]), ["2026-10-20"])
t.ok("rule: a SELL older than the BUY does not count", r["status"] == "open", r)
q = cp.settle_h10(bars_for(after[:12], [20000 + i * 100 for i in range(12)]))
t.ok(f"quick: the open after the session against the close {cp.H10} sessions later",
     q["status"] == "closed" and q["entry"] == 20000 and q["exit"] == 21000 and q["pct"] == 5.0, q)
q = cp.settle_h10(bars_for(after[:4], [20000, 20100, 20200, 20300]))
t.ok("quick: before 10 sessions, open at the last close", q["status"] == "open" and q["sessions"] == 3, q)
s = cp.settle_after(bars_for([DAY] + after[:10], [100] + [100 + i for i in range(1, 11)]), DAY)
t.ok("a view: the close 5 and 10 sessions later against the day's close", s == {"r5": 5.0, "r10": 10.0}, s)


def entry(day, results, pick, avoid=(), ranking=None, bot=()):
    cs = [{"symbol": s, "source": "bot" if s in bot else "site", "strength": 90 - i, "sl": 1,
           "r10": {"status": "closed", "pct": pct} if pct is not None else None,
           "result": {"status": "closed", "pct": pct} if pct is not None and s not in bot else None}
          for i, (s, pct) in enumerate(results.items())]
    return {"day": day, "at": f"{day}T15:55", "candidates": cs, "pick": {"symbol": pick},
            "avoid": [{"symbol": a} for a in avoid], "ranking": ranking or list(results)}


log_days = [entry("2026-09-01", {"A": 5.0, "B": -3.0, "C": 1.0, "D": 0.0}, "A", ["B"], ["A", "C", "D", "B"]),
            entry("2026-09-02", {"A": -2.0, "B": 4.0}, "B", ranking=["B", "A"]),
            entry("2026-09-03", {"A": 1.0, "B": 1.0}, ""),
            entry("2026-09-04", {"A": 2.0, "B": None}, "A"),
            entry("2026-09-07", {"A": 7.0}, "A"),                       # one candidate: no choice
            entry("2026-09-08", {"A": 3.0, "X": -1.0}, "X", bot=["X"]),  # the pick is the scan's
            {"day": "2026-09-09", "candidates": [], "note": "x"},
            {"day": "2026-09-10", "candidates": [{"symbol": "A", "r10": None}], "error": "y"}]
st = cp.stats(log_days)
t.ok("only sessions with two or more candidates, all scored, are compared; one candidate is no choice",
     st["n"] == 3 and st["nopick"] == 1 and st["pending"] == 1, st)
t.ok("pick against the session's average and the site's strongest",
     st["pick_mean"] == (5.0 + 4.0 - 1.0) / 3 and st["mean"] == (0.75 + 1.0 + 1.0) / 3
     and st["strongest"] == (5.0 - 2.0 + 3.0) / 3 and st["beat"] == 2, st)
t.ok("the ranking: top half against the bottom half, every choice session",
     st["spread_n"] == 4 and st["spread"] == ((5.0 + 1.0) / 2 - (0.0 - 3.0) / 2 + 6.0 + 0.0 + 4.0) / 4, st)
t.ok("avoided against the others, every scored candidate (one-candidate sessions too)",
     st["avoided"] == 1 and st["avoided_mean"] == -3.0 and st["kept"] == 11, st)
t.ok("the rule's comparison only on the site's BUYs (a scan pick is not in it)", st["rule_n"] == 2, st)
t.ok("no checkpoint yet: no verdict", st["checkpoints"] == [] and "Chưa kết luận" in "\n".join(cp.stats_lines(st)))


def series(n, good, start=1):
    out = []
    for i in range(n):
        day = f"2026-{1 + (i + start) // 28:02d}-{1 + (i + start) % 28:02d}"
        out.append(entry(day, {"A": (3.0 + i % 3) if good else (-3.0 - i % 3), "B": -1.0 if good else 1.0, "C": 0.0}, "A"))
    return out


better = cp.stats(series(25, True))
t.ok("checkpoint 20 reached: the verdict on exactly those 20 sessions, preliminary",
     len(better["checkpoints"]) == 1 and better["checkpoints"][0]["size"] == 20
     and "TỐT HƠN" in better["checkpoints"][0]["text"] and not better["checkpoints"][0]["final"], better["checkpoints"])
mixed = series(20, True) + series(25, False, start=40)
cps = cp.stats(mixed)["checkpoints"]
t.ok("each checkpoint is fixed on its first sessions: later sessions never change the 20-session verdict",
     len(cps) == 2 and "TỐT HƠN" in cps[0]["text"] and cps[1]["final"] and cps[0]["until"] < cps[1]["until"], cps)
t.ok("a clearly worse pick says so", "KÉM HƠN" in cp.stats(series(22, False))["checkpoints"][0]["text"])
lines = "\n".join(cp.stats_lines(st))
t.ok("the comparison as text", "Đã chấm 3 phiên có lựa chọn" in lines and "mốc kết luận tiếp theo: 20 phiên" in lines
     and "Nửa trên bảng xếp hạng" in lines and "Lệnh Claude bảo tránh: 1 lệnh" in lines, lines)
views = cp.stats([{"day": "2026-09-01", "candidates": [], "holdings": [
    {"symbol": "FPT", "view": "CÂN NHẮC BÁN", "rule": "BÁN", "result": {"r10": -4.0}},
    {"symbol": "GEX", "view": "GIỮ", "rule": "BÁN", "result": {"r10": 3.0}},
    {"symbol": "CII", "view": "GIỮ", "rule": "GIỮ", "result": {"r10": -1.0}}]}])["views"]
t.ok("holding views: the close 10 sessions later per view, and where Claude went against the rule",
     views["CÂN NHẮC BÁN"]["n"] == 1 and views["GIỮ"]["n"] == 2 and views["GIỮ"]["r10"] == 1.0
     and views["GIỮ"]["against_n"] == 1 and views["GIỮ"]["against_r10"] == 3.0 and views["CÂN NHẮC BÁN"]["against_n"] == 0)

# ───────────── 6. an evening ─────────────


class Pick(cp.ClaudePick):
    def symbols(self):
        return WATCH


NO_SCAN = FakeCloses({}, DAY, {})
scan_day = FakeCloses({"FLP": flip, "STY": stay}, DAY, {"FLP": 9e10, "STY": 8e10})
client = FakeClient([response([found("https://cafef.vn/gex-1.chn", "https://vietstock.vn/cii-2.htm", "https://cafef.vn/fpt.chn"),
                               submit(ANSWER)], "tool_use", usage(6000, 900, 4))])
pick = Pick(SETTINGS.data_dir, store=store, client=client, bars=fake_bars, account=account, closes=scan_day)
text = pick.run()
saved = pick.log.get(DAY)
t.ok("one request for the latest session, recorded", len(client.calls) == 1 and saved and saved["pick"]["symbol"] == "GEX"
     and saved["cost_usd"] > 0 and saved["model"] == "claude-opus-5-5", saved)
t.ok("the site's signals refreshed first, the holdings with them", store.refreshed == ["CII", "FPT", "GEX", "HPG", "OLD", "LATE"])
t.ok("the candidates: the site's (with figures) then the scan's", [c["symbol"] for c in saved["candidates"]] == ["CII", "GEX", "FLP"]
     and all(c.get("features") for c in saved["candidates"]) and scan_day.updated == [DAY])
t.ok("the holdings with Claude's views", {h["symbol"]: h.get("view") for h in saved["holdings"]}
     == {"CII": None, "FPT": "CÂN NHẮC BÁN", "GEX": "GIỮ"}, saved["holdings"])
sent_prompt = client.calls[0]["messages"][0]["content"]
t.ok("the prompt had the market context and the portfolio", "VNINDEX" in sent_prompt and "trang đang MUA 4/6 mã" in sent_prompt
     and "DANH MỤC ĐANG GIỮ" in sent_prompt and "FLP · bot ước tính" in sent_prompt, sent_prompt[:400])
t.ok("the message: shadow only, the pick with the site's plan, what to avoid, the views, the cost",
     text.startswith("🤖 CLAUDE CHỌN BÓNG · phiên 23/10") and "KHÔNG phải lệnh" in text and "⭐ Claude chọn: 🟢 GEX 23.850" in text
     and "cắt lỗ 22.400" in text and "⛔ Nên tránh (1)" in text and "💼 DANH MỤC CỦA ANH" in text
     and "FPT" in text and "Claude: CÂN NHẮC BÁN" in text and "Chi phí lượt này" in text, text)
t.ok("a session is answered once: asked again, nothing is sent to Claude", pick.run().startswith("🤖") and len(client.calls) == 1)

NOW["t"] = at("2026-10-26", "15:55")


class DownStore(Store):
    def refresh(self, symbols, **kwargs):
        return 0, "sieutinhieu: 503"


down_pick = Pick(SETTINGS.data_dir, store=DownStore(SETTINGS.data_dir), client=FakeClient([]), bars=fake_bars,
                 account=account, closes=NO_SCAN)
try:
    down_pick.run()
    t.ok("the site's signals of the session not fetched: tried again later", False)
except cp.Retry as exc:
    t.ok("the site's signals of the session not fetched: tried again later, nothing recorded",
         "chưa lấy được tín hiệu" in str(exc) and down_pick.log.get("2026-10-26") is None, str(exc))
holdings_only = FakeClient([response([found("https://cafef.vn/fpt.chn"), submit({
    "holdings": [{"symbol": "FPT", "view": "CÂN NHẮC BÁN", "reason": "r", "sources": ["https://cafef.vn/fpt.chn"]}],
    "market_note": "Yên."})], "tool_use")])
quiet = Pick(SETTINGS.data_dir, store=store, client=holdings_only, bars=fake_bars, account=account, closes=NO_SCAN)
text = quiet.run()
t.ok("no new BUY but holdings: Claude asked about the portfolio only", len(holdings_only.calls) == 1
     and "pick" not in holdings_only.calls[0]["tools"][1]["input_schema"]["properties"]
     and "Phiên này không có lệnh MUA mới" in text and "Claude: CÂN NHẮC BÁN" in text, text)
empty_dir = SETTINGS.data_dir / "empty"
none = Pick(empty_dir, store=Store(empty_dir), client=FakeClient([]), bars=fake_bars, account=TaiSan(empty_dir / "a"),
            closes=NO_SCAN)
t.ok("no new BUY and no holdings: recorded without asking Claude", "danh mục trống" in none.run())

store.put("FPT", [sig("FPT", "2026-09-18", "SELL", 65180), sig("FPT", "2026-10-27", "BUY", 58000, sl=55000)])
NOW["t"] = at("2026-10-27", "15:55")
data = json.loads((SETTINGS.data_dir / cp.FILE).read_text())
data[0]["cost_usd"] = 15.0                           # this month's spending at the cap
(SETTINGS.data_dir / cp.FILE).write_text(json.dumps(data))
capped = Pick(SETTINGS.data_dir, store=store, client=FakeClient([]), bars=fake_bars, account=account, closes=NO_SCAN)
text = capped.run()
t.ok("the monthly cap: Claude is not asked, the session says why", "CLAUDE_PICK_MONTHLY_USD" in text
     and capped.log.get("2026-10-27")["error"])
data = json.loads((SETTINGS.data_dir / cp.FILE).read_text())
data[0]["cost_usd"] = 0.2
(SETTINGS.data_dir / cp.FILE).write_text(json.dumps(data))
retry = FakeClient([response([found("https://cafef.vn/fpt-2.chn"),
                              submit({"pick": {"symbol": "FPT", "confidence": 55, "reason": "ok", "sources": []},
                                      "avoid": [], "ranking": ["FPT"], "market_note": "", "holdings": []})], "tool_use")])
capped._client = retry
capped.run()
t.ok("a session that cost nothing may be tried again (here: after the cap was raised)", len(retry.calls) == 1
     and capped.log.get("2026-10-27")["pick"]["symbol"] == "FPT")

NOW["t"] = at("2026-10-28", "15:55")
store.put("HPG", [sig("HPG", "2026-10-22", "BUY", 27000, sl=25500), sig("HPG", "2026-10-28", "BUY", 27500, sl=26000)])
bad = Pick(SETTINGS.data_dir, store=store, client=FakeClient([response([], "refusal", usage(900, 0))]), bars=fake_bars,
           account=account, closes=NO_SCAN)
text = bad.run()
saved = bad.log.get("2026-10-28")
t.ok("a refusal: recorded with its cost, said, not asked again", "từ chối" in text and saved["cost_usd"] > 0
     and saved["error"] and bad.run() and len(bad._client.calls) == 1)
t.ok("give_up never replaces a session already recorded", bad.give_up("2026-10-28", "x") and
     bad.log.get("2026-10-28")["cost_usd"] > 0)

# settling the evening of 23/10
later = sessions("2026-10-26", 12)
for code, opens in (("GEX", [24000 + i * 200 for i in range(12)]), ("CII", [12900, 12500, 12100, 12000] + [12500] * 8),
                    ("FLP", [100.0 + i for i in range(12)]), ("FPT", [57000 - i * 300 for i in range(12)])):
    lows = [o * 0.99 for o in opens]
    BARS[code] = [b for b in BARS.get(code, []) if b["d"] <= DAY] + bars_for(later, opens, lows=lows)
BARS["FLP"] = bars_for([DAY], [99.0]) + BARS["FLP"]
store.put("GEX", [sig("GEX", DAY, "BUY", 23850, sl=22400, tp1=26000, strength=90), sig("GEX", later[2], "SELL", 25000)])
NOW["t"] = at(later[-1], "16:00")
closed = pick.settle()
day23 = pick.log.get(DAY)
by = {c["symbol"]: c for c in day23["candidates"]}
t.ok("settled: every candidate's 10-session result", closed >= 3 and all(c["r10"]["status"] == "closed" for c in by.values())
     and by["GEX"]["r10"]["pct"] == round((26000 / 24000 - 1) * 100, 2), {k: c["r10"] for k, c in by.items()})
t.ok("settled: the rule on the site's BUYs only (GEX sold the open after the site's SELL, CII at its cut-loss)",
     by["GEX"]["result"]["why"] == "trang BÁN" and by["GEX"]["result"]["exit"] == 24800
     and by["CII"]["result"]["why"] == "chạm cắt lỗ" and "result" not in by["FLP"] or by["FLP"].get("result") is None,
     {k: c.get("result") for k, c in by.items()})
fpt_view = next(h for h in day23["holdings"] if h["symbol"] == "FPT")
t.ok("settled: the holding views against the day's close", fpt_view["result"]["r10"] == round(
     (BARS["FPT"][-3]["c"] / next(b["c"] for b in BARS["FPT"] if b["d"] == DAY) - 1) * 100, 2), fpt_view["result"])
st = pick.stats()
t.ok("the comparison counts the session (three candidates, all scored)", st["n"] == 1 and st["spread_n"] == 1, st)
hist_text = cp.history_text(pick.log.days())
t.ok("history: one line a session", "23/10: 3 lệnh · chọn GEX +8,3%" in hist_text, hist_text)

# ───────────── 7. the alert notes ─────────────
NOW["t"] = at("2026-10-29", "10:32")
NOTE = {"cause": "theo thị trường chung", "news": "Không có tin riêng.", "view": "CÂN NHẮC BÁN",
        "reason": "Trang đã BÁN.", "sources": ["https://cafef.vn/fpt-3.chn"]}
note_client = FakeClient([response([found("https://cafef.vn/fpt-3.chn"), submit(NOTE, cp.NOTE_TOOL)], "tool_use",
                                   usage(2000, 300, 2))] * 3)
notes = Pick(SETTINGS.data_dir, store=store, client=note_client, bars=fake_bars, account=account, closes=NO_SCAN)
text = notes.alert_note("sell", "FPT", 55000, "2026-10-29")
t.ok("an alert on a holding: Claude looks at its news, at most 3 searches, says the cause and a view",
     text and text.startswith("🤖 Claude xem cảnh báo 🔴 FPT · 10:32") and "theo thị trường chung" in text
     and "CÂN NHẮC BÁN" in text and "không phải lệnh" in text
     and note_client.calls[0]["tools"][0]["max_uses"] == cp.ALERT_SEARCHES, text)
t.ok("the note is kept with its cost and the rule", notes.notes.alerts_on("2026-10-29")[0]["cost_usd"] > 0
     and "BÁN" in notes.notes.alerts_on("2026-10-29")[0]["rule"])
t.ok("the same alert again the same day: not asked again", notes.alert_note("sell", "FPT", 54000, "2026-10-29") is None
     and len(note_client.calls) == 1)
t.ok("not a holding, or the 🎯 target: not asked", notes.alert_note("sell", "VIC", 1, "2026-10-29") is None
     and notes.alert_note("target", "GEX", 1, "2026-10-29") is None and len(note_client.calls) == 1)
os.environ["CLAUDE_PICK_ALERTS"] = "1"
t.ok("the day's quota used: not asked", notes.alert_note("stop", "CII", 12000, "2026-10-29") is None)
os.environ["CLAUDE_PICK_ALERTS"] = "0"
t.ok("CLAUDE_PICK_ALERTS=0: never", notes.alert_note("stop", "CII", 12000, "2026-10-30") is None)
del os.environ["CLAUDE_PICK_ALERTS"]
t.ok("the month's spending counts the notes too", notes.month_cost("2026-10") > notes.log.month_cost("2026-10"))

hooked = []
real_note = cp.ClaudePick.alert_note
cp.ClaudePick.alert_note = lambda self, level, symbol, price, day: hooked.append((level, symbol)) or "📝"
said = []
account.on_alert = cp.alert_hook(SETTINGS.data_dir, said.append)
account.record_alert("stop", "CII", 12000, "2026-10-29")
account.record_alert("stop", "CII", 11900, "2026-10-29")          # the same alert: kept once
account.record_alert("target", "GEX", 26000, "2026-10-29")        # 🎯: no note
for _ in range(100):
    if said:
        break
    time.sleep(0.02)
t.ok("Tài sản calls the hook once per new alert; Claude's note goes out in the background",
     hooked == [("stop", "CII")] and said == ["📝"], (hooked, said))
cp.ClaudePick.alert_note = real_note
account.on_alert = lambda *a: (_ for _ in ()).throw(RuntimeError("hook down"))
t.ok("a failing hook never stops the alert being kept", account.record_alert("sell", "GEX", 23000, "2026-10-29") is True)
account.on_alert = None

# ───────────── 8. the weekly review ─────────────
NOW["t"] = at("2026-10-30", "16:00")
account.add("BUY", "2026-10-27", symbol="FPT", qty=10, price=58500)           # bought while... the site BUY of 27/10
account.add("BUY", "2026-10-28", symbol="CII", qty=100, price=12100)          # bought while the site still BUYs CII
store.put("CII", [sig("CII", DAY, "BUY", 12950, sl=12200), sig("CII", "2026-10-27", "SELL", 12300)])
t.ok("the week's last session: due once", Pick(SETTINGS.data_dir, store=store, account=account).week_due("2026-10-30")
     and not Pick(SETTINGS.data_dir, store=store, account=account).week_due("2026-10-29"))
facts = Pick(SETTINGS.data_dir, store=store, account=account, bars=fake_bars).week_facts("2026-10-30")
joined = "\n".join(facts)
t.ok("the week's facts by the code: buys against the site's state and how far above its price, alerts followed or not",
     "27/10 MUA FPT 10 cp @ 58.500 · trang đang MUA từ 27/10 @ 58.000, mua cao hơn giá tín hiệu +0,9%" in joined
     and "28/10 MUA CII 100 cp @ 12.100 · trang đang BÁN" in joined and "⛔ CII @ 12.000: CHƯA bán" in joined
     and "Tài sản cuối tuần" in joined, joined)
week_client = FakeClient([response([text_block("• Anh chưa bán CII khi ⛔.\n• Tuần tới: đặt lệnh bán ngay khi ⛔.")], "end_turn",
                                   usage(1500, 200))])
weekly = Pick(SETTINGS.data_dir, store=store, client=week_client, bars=fake_bars, account=account)
text = weekly.weekly_review("2026-10-30")
t.ok("the review: no search, low effort, the facts and Claude's comments, once a week",
     "tools" not in week_client.calls[0] and week_client.calls[0]["output_config"] == {"effort": "low"}
     and text.startswith("🗓️ CLAUDE TỔNG KẾT TUẦN 2026-W44") and "CHƯA bán" in text and "Tuần tới" in text
     and weekly.weekly_review("2026-10-30") == text and len(week_client.calls) == 1, text)
t.ok("/claude tuan text", weekly.last_week_text() == text)

# ───────────── 9. the scheduler ─────────────
from vnbot.jobs import JobQueue  # noqa: E402
from vnbot.service import DailyScheduler, Watchlist  # noqa: E402

notes_out = []
sched = DailyScheduler(SETTINGS, JobQueue(SETTINGS), Watchlist(SETTINGS), bot=None, notify=notes_out.append)
NOW["t"] = at("2026-11-02", "15:49")
t.ok("not due before 15:50", sched._claude_due() is None)
NOW["t"] = at("2026-11-02", "15:51")
t.ok("due from 15:50 on a session day", sched._claude_due() == "2026-11-02")
NOW["t"] = at("2026-10-31", "16:00")
t.ok("no evening on a Saturday", sched._claude_due() is None)
NOW["t"] = at(DAY, "16:00")
t.ok("not due once the session is recorded", sched._claude_due() is None)
os.environ["CLAUDE_PICK"] = "0"
NOW["t"] = at("2026-11-02", "16:00")
t.ok("not due when switched off", sched._claude_due() is None)
del os.environ["CLAUDE_PICK"]
NOW["t"] = at(DAY, "16:00")
t.ok("the week's review is due after the week's last evening (23/10 recorded, its week not reviewed)",
     sched._claude_week_due() == DAY)
NOW["t"] = at("2026-10-30", "16:00")
t.ok("not before the evening of that session is recorded", sched._claude_week_due() is None)

calls = []


def failing(self, day=None):
    calls.append(day)
    raise cp.Retry("APIConnectionError: down")


real_run = cp.ClaudePick.run
cp.ClaudePick.run = failing
for _ in range(sched.CLAUDE_MAX_TRIES):
    sched._claude_next = 0
    sched._claude_run("2026-11-02")
t.ok("Claude unreachable: tried again later, then given up once and said",
     len(calls) == sched.CLAUDE_MAX_TRIES and len(notes_out) == 1 and "không gọi được Claude" in notes_out[0]
     and cp.PickLog(SETTINGS.data_dir).get("2026-11-02")["error"], notes_out)


def broken(self, day=None):
    calls.append(day)
    raise KeyError("bug")


cp.ClaudePick.run = broken
sched._claude_next = 0
sched._claude_run("2026-11-03")
t.ok("an unexpected fault waits like an unreachable Claude (never a request every 30 s)",
     sched._claude_next > time.time() + 60)
cp.ClaudePick.run = real_run
real_review = cp.ClaudePick.weekly_review
cp.ClaudePick.weekly_review = lambda self, day: (_ for _ in ()).throw(RuntimeError("down"))
sched._claude_week(DAY)
t.ok("a failed weekly review waits", sched._week_next > time.time() + 60)
cp.ClaudePick.weekly_review = real_review

# ───────────── 10. Telegram / Zoom ─────────────
from vnbot.telegram_bot import HELP, TelegramBot, redact  # noqa: E402

tg = TelegramBot(SETTINGS, JobQueue(SETTINGS), Watchlist(SETTINGS))
sent = []
tg.send = lambda chat_id, text: sent.append(text)
t.ok("help lists /claude", "/claude chay" in HELP and "/claude tuan" in HELP)
NOW["t"] = at("2026-11-04", "16:00")
tg._handle({"chat": {"id": 111}, "from": {"id": 111}, "text": "/claude"})
t.ok("/claude: the latest session and the comparison", sent[-1].startswith("🤖 CLAUDE CHỌN BÓNG") and "Đã chấm" in sent[-1], sent[-1][:200])
tg._handle({"chat": {"id": 111}, "from": {"id": 111}, "text": "/claude lichsu"})
t.ok("/claude lichsu: the history", "phiên gần nhất" in sent[-1], sent[-1][:200])
tg._handle({"chat": {"id": 111}, "from": {"id": 111}, "text": "/claude tuan"})
t.ok("/claude tuan: the last weekly review", sent[-1].startswith("🗓️ CLAUDE TỔNG KẾT TUẦN"), sent[-1][:120])
tg._handle({"chat": {"id": "zoom-x"}, "from": {"id": "zoom"}, "text": "/claude"})
t.ok("typed in Zoom too", sent[-1].startswith("🤖 CLAUDE CHỌN BÓNG"))
key = os.environ.pop("ANTHROPIC_API_KEY")
tg._handle({"chat": {"id": 111}, "from": {"id": 111}, "text": "/claude chay"})
t.ok("/claude chay without the key: says what is missing, nothing sent to Claude",
     sent[-1].startswith("⚠️") and "ANTHROPIC_API_KEY" in sent[-1], sent[-1])
os.environ["ANTHROPIC_API_KEY"] = key
t.ok("the API key is never echoed in an error", "sk-ant-test" not in redact(f"bad key {key}"))
tg._handle({"chat": {"id": 111}, "from": {"id": 111}, "text": "/claude abc"})
t.ok("/claude with an unknown word: the usage", "Dùng: /claude" in sent[-1])

# ───────────── 11. the page ─────────────
from vnbot import claude_pick_web as cw  # noqa: E402
from vnbot import ui  # noqa: E402

t.ok("the page is in the Tiện ích menu", ("claude", "/claude", "Claude chọn bóng") in ui.TOOLS)
app, *_ = build(trader=False)
c = TestClient(app, raise_server_exceptions=False)
AUTH = ("x", "s3cret-pass")
t.ok("the page needs the password", c.get("/claude").status_code == 401)
page = c.get("/claude", auth=AUTH).text
t.ok("the page: the comparison, the method, the latest session, the alert notes, the week, the history",
     "Claude chọn bóng" in page and "Cách chấm điểm" in page and "Claude xem cảnh báo" in page
     and "Tổng kết tuần 2026-W44" in page and "Lịch sử" in page and "Đã chấm" in page, page[-3000:])
card = cw._latest(pick.log.get(DAY))
t.ok("the latest card: the pick, its source as a safe outside link, the avoided, every candidate with both results",
     "⭐ GEX" in card and 'href="https://cafef.vn/gex-1.chn" rel="noopener noreferrer"' in card and "Nên tránh" in card
     and "CII" in card and "trang BÁN" in card and "bot ước tính" in card, card[:600])
views_card = cw._views(pick.log.get(DAY))
t.ok("the portfolio card: the rule, Claude's view, the price 10 sessions later", "CÂN NHẮC BÁN" in views_card
     and "🔴 trang BÁN" in views_card and "Giá sau 10 phiên" in views_card)
hostile = {**pick.log.get(DAY), "pick": {"symbol": "GEX", "confidence": 1, "reason": "<script>alert(1)</script>",
                                          "sources": ["javascript:alert(1)"]}}
card = cw._latest(hostile)
t.ok("Claude's text is escaped on the page", "<script>alert" not in card and "&lt;script&gt;" in card)
t.ok("only http(s) links are shown", "javascript:" not in card, card[:800])
os.environ["CLAUDE_PICK"] = "0"
r = c.post("/claude/chay", auth=AUTH, headers={"origin": "http://testserver"}, follow_redirects=False)
t.ok("the button when switched off: back with the reason, nothing run",
     r.status_code == 303 and "CLAUDE_PICK" in __import__("urllib.parse").parse.unquote(r.headers["location"]))
r = c.post("/claude/chay", auth=AUTH, headers={"origin": "https://evil.example"}, follow_redirects=False)
t.ok("the button refuses another site's form", r.status_code == 403, r.status_code)
del os.environ["CLAUDE_PICK"]

t.done()
