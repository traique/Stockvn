"""AI thẩm định tín hiệu on 9Router (UniRouter, Gemini web with its own web search): the two models
and their hand-over, what is asked, how the JSON answer is read and checked, what is recorded
and said (``ai_pick``)."""
import json
import os
from datetime import datetime
from types import SimpleNamespace as NS

import _common  # noqa: F401 - clean env, no .env, offline, temp folder, repo on sys.path

import httpx  # noqa: E402
import openai  # noqa: E402

from tradingagents.llm_clients import fallback_client as fc  # noqa: E402
from vnbot import ai_pick as cp  # noqa: E402

t = _common.Checks()
DATA = _common.ROOT / "data"
DATA.mkdir()
REQ = httpx.Request("POST", "https://router.example/v1")
DAY = "2026-10-23"
NOW = datetime(2026, 10, 23, 15, 55)
TOOL = cp.submit_tool(["AAA", "BBB"], ["HPG"])
ANSWER = {"pick": {"symbol": "AAA", "confidence": 60, "reason": "nền giá chặt",
                   "sources": ["[CafeF](https://cafef.vn/aaa-tang-von.chn)", "https://example.com/blog"]},
          "avoid": [{"symbol": "BBB", "reason": "đã tăng xa", "sources": []}], "ranking": ["AAA", "BBB"],
          "holdings": [{"symbol": "HPG", "view": "Giữ", "reason": "chưa thủng cắt lỗ", "sources": []}],
          "market_note": "thị trường đi ngang"}
ROUTER = {"ROUTER9_BASE_URL": "https://router.example/v1", "ROUTER9_API_KEY": "r9"}


def env(**values) -> None:
    for key in ("ROUTER9_BASE_URL", "ROUTER9_API_KEY", "AI_PICK_MODEL", "AI_PICK_MODEL_FAST", "SIEUTINHIEU", "AI_PICK", "AI_PICK_BOT",
                "LLM_MAX_CALLS_PER_DAY"):
        os.environ.pop(key, None)
    os.environ.update({k: str(v) for k, v in values.items()})
    fc.reset_provider_state()


class Router:
    """9Router's OpenAI-compatible client: answers the texts in turn, or raises ``error``
    (for every model) or ``fails[model]`` (for that model)."""

    def __init__(self, *texts, error=None, fails=None, finish="stop"):
        self.texts, self.error, self.fails, self.finish, self.seen = list(texts), error, fails or {}, finish, []
        self.chat = NS(completions=NS(create=self.create))

    def create(self, **kwargs):
        self.seen.append(json.loads(json.dumps(kwargs)))
        if self.error is not None:
            raise self.error
        if kwargs["model"] in self.fails:
            raise self.fails[kwargs["model"]]
        text = self.texts.pop(0) if self.texts else ""
        return NS(model=kwargs["model"].split("/")[-1], usage=NS(prompt_tokens=900, completion_tokens=300),
                  choices=[NS(finish_reason=self.finish, message=NS(content=text, tool_calls=None))])


def fenced(obj) -> str:
    return "Đây là kết quả:\n```json\n" + json.dumps(obj, ensure_ascii=False) + "\n```"


# ───────────── 1. configuration ─────────────
env(SIEUTINHIEU=1)
t.ok("without 9Router: off, and the message says what is missing", not cp.enabled() and "ROUTER9_BASE_URL" in cp.why_off())
env(SIEUTINHIEU=1, **ROUTER)
t.ok("with 9Router: on, Gemini Pro and Flash by default", cp.enabled() and cp.why_off() == ""
     and cp.main_model() == "gmw/gemini-pro" and cp.fast_model() == "gmw/gemini-flash")
env(**ROUTER, AI_PICK_MODEL="gmw/gemini-thinking", AI_PICK_MODEL_FAST="gmw/gemini-flash-lite")
t.ok("AI_PICK_MODEL and AI_PICK_MODEL_FAST pick them", cp.main_model() == "gmw/gemini-thinking"
     and cp.fast_model() == "gmw/gemini-flash-lite")
t.ok("no Anthropic SDK, no trace of the old name", all(x not in open(path, encoding="utf-8").read().lower()
                                                       for x in ("anthropic", "claude") for path in (cp.__file__,)))

# ───────────── 2. the request ─────────────
env(**ROUTER)
r = Router(fenced(ANSWER))
raw, meta = cp.ask("đề bài", TOOL, client=r)
sent = r.seen[0]
t.ok("no tools are sent (Gemini web would be skipped, and its search with it)", "tools" not in sent and "tool_choice" not in sent)
t.ok("the main model, the system, the question and the JSON Schema", sent["model"] == "gmw/gemini-pro"
     and sent["messages"][0]["role"] == "system" and "Tìm trên web" in sent["messages"][0]["content"]
     and sent["messages"][1]["content"].startswith("đề bài")
     and json.dumps(TOOL["input_schema"], ensure_ascii=False) in sent["messages"][1]["content"])
t.ok("the JSON read from a fenced block, the meta", raw == ANSWER and meta["served_by"] == "gemini-pro" and not meta["fallback"]
     and meta["usage"] == {"input": 900, "output": 300})
r = Router("Tôi sẽ tìm tin trước.", json.dumps({"market_note": "x"}))
raw, _ = cp.ask("đề", TOOL, client=r)
t.ok("not JSON at first: asked once more, with the first answer and a nudge", raw == {"market_note": "x"} and len(r.seen) == 2
     and r.seen[1]["messages"][-1]["content"] == cp.NUDGE and r.seen[1]["messages"][-2]["role"] == "assistant")
try:
    cp.ask("đề", TOOL, client=Router("không", "vẫn không"))
    t.ok("never JSON: PickError after two answers", False)
except cp.PickError as exc:
    t.ok("never JSON: PickError after two answers", "JSON" in str(exc) and exc.meta["model"] == "gmw/gemini-pro")
text, _ = cp.ask("dữ kiện", None, client=Router("  • giữ kỷ luật  "))
t.ok("the weekly review: plain text, no schema", text == "• giữ kỷ luật")

QUOTA = openai.RateLimitError("quota", response=httpx.Response(429, request=REQ), body=None)
r = Router(fenced(ANSWER), fails={"gmw/gemini-pro": QUOTA})
raw, meta = cp.ask("đề", TOOL, client=r)
t.ok("Pro out of quota: Flash answers in the same call, the reason kept", raw == ANSWER
     and [x["model"] for x in r.seen] == ["gmw/gemini-pro", "gmw/gemini-flash"]
     and meta["model"] == "gmw/gemini-flash" and meta["fallback"] == "gmw/gemini-pro lỗi (HTTP 429)", meta)
t.ok("...and 9Router is not rested for the agents (it answered)", not fc.is_resting("router9"))
r = Router(fenced(ANSWER), fails={"gmw/gemini-pro": openai.APITimeoutError(request=REQ)})
raw, meta = cp.ask("đề", TOOL, client=r)
t.ok("Pro too slow: Flash answers", meta["model"] == "gmw/gemini-flash" and "APITimeoutError" in meta["fallback"])
r = Router("không phải JSON", fenced(ANSWER), fails={"gmw/gemini-pro": QUOTA})
raw, meta = cp.ask("đề", TOOL, client=r)
t.ok("the nudge goes to the model that answered", raw == ANSWER and r.seen[-1]["model"] == "gmw/gemini-flash"
     and [x["model"] for x in r.seen] == ["gmw/gemini-pro", "gmw/gemini-flash", "gmw/gemini-flash"])
r = Router("• ổn", fails={"gmw/gemini-flash": QUOTA})
text, meta = cp.ask("dữ kiện", None, client=r, fast=True)
t.ok("fast=True: Flash first, Pro when it fails", [x["model"] for x in r.seen] == ["gmw/gemini-flash", "gmw/gemini-pro"]
     and meta["model"] == "gmw/gemini-pro" and text == "• ổn")
try:
    cp.ask("đề", TOOL, client=Router(fails={"gmw/gemini-pro": QUOTA, "gmw/gemini-flash": QUOTA}))
    t.ok("both out of quota: Retry (asked again later)", False)
except cp.Retry as exc:
    t.ok("both out of quota: Retry (asked again later)", "429" in str(exc) or "rate" in str(exc).lower(), str(exc))
env(**ROUTER, AI_PICK_MODEL="gmw/gemini-pro", AI_PICK_MODEL_FAST="gmw/gemini-pro")
r = Router(fails={"gmw/gemini-pro": QUOTA})
try:
    cp.ask("đề", TOOL, client=r)
except cp.Retry:
    pass
t.ok("the same model twice: asked once", len(r.seen) == 1)
env(**ROUTER)
try:
    cp.ask("x", TOOL, client=Router(error=openai.APIConnectionError(request=REQ)))
    t.ok("9Router unreachable: Retry", False)
except cp.Retry as exc:
    t.ok("9Router unreachable: Retry", "9Router" in str(exc))
t.ok("...and it rests, shared with the agents' chain", fc.is_resting("router9"))
r = Router(fenced(ANSWER))
try:
    cp.ask("x", TOOL, client=r)
    t.ok("while resting: Retry at once, nothing sent", False)
except cp.Retry:
    t.ok("while resting: Retry at once, nothing sent", r.seen == [])
env(**ROUTER, LLM_MAX_CALLS_PER_DAY=1)
cp.ask("x", TOOL, client=Router(fenced(ANSWER)))
try:
    cp.ask("x", TOOL, client=Router(fenced(ANSWER)))
    t.ok("the day's AI budget counts these answers", False)
except cp.Retry as exc:
    t.ok("the day's AI budget counts these answers", "hạn mức" in str(exc), str(exc))

# ───────────── 3. reading the answer ─────────────
out = cp.clean(ANSWER, [{"symbol": "AAA"}, {"symbol": "BBB"}], ["HPG"])
t.ok("a source kept only on the news sites (a Markdown link or a bare URL)",
     out["pick"]["sources"] == ["https://cafef.vn/aaa-tang-von.chn"])
t.ok("a view in any case is read (\"Giữ\" -> GIỮ)", out["views"]["HPG"]["view"] == "GIỮ")
out = cp.clean({"pick": {"symbol": "ZZZ", "confidence": 900}, "ranking": "AAA", "avoid": [{"symbol": "AAA"}, "x"]},
               [{"symbol": "AAA"}, {"symbol": "BBB"}], [])
t.ok("codes outside the candidates, a ranking that is not a list and odd items are dropped",
     out["pick"]["symbol"] == "" and out["ranking"] == ["AAA", "BBB"] and [a["symbol"] for a in out["avoid"]] == ["AAA"])
t.ok("www. and subdomains of a news site count; a look-alike does not",
     cp._sources(["https://www.vietstock.vn/a", "https://s.cafef.vn/b", "https://cafef.vn.evil.com/c"])
     == ["https://www.vietstock.vn/a", "https://s.cafef.vn/b"])


# ───────────── 4. a whole evening ─────────────
class Store:
    def refresh(self, symbols, fresh_after=None):
        return [], None

    def fetched_at(self, symbol):
        return f"{DAY}T15:20"

    def signals(self, symbol):
        return []

    def state(self, symbol, day, cutoff):
        return {"side": "BUY", "day": DAY, "live": True, "price": 20000, "sl": 18500, "tp1": 23000, "strength_pct": 70}


class Evening(cp.AiPick):
    def symbols(self):
        return ["AAA", "BBB"]

    def _held(self):
        return []


def bars(symbol, day):
    return [{"d": f"2026-10-{d:02d}", "o": 20000.0, "h": 20500.0, "l": 19800.0, "c": 20000.0 + d * 50, "v": 1e6}
            for d in range(1, 24)]


env(**ROUTER, SIEUTINHIEU=1, AI_PICK_BOT=0)
evening_dir = DATA / "evening"
evening_dir.mkdir()
answer = {k: v for k, v in ANSWER.items() if k != "holdings"}
text = Evening(evening_dir, store=Store(), client=Router(fenced(answer)), bars=bars, now=lambda: NOW).run(DAY)
entry = cp.PickLog(evening_dir).get(DAY)
t.ok("the evening: recorded with the model that answered, no cost", entry["pick"]["symbol"] == "AAA"
     and entry["model"] == "gmw/gemini-pro" and entry["served_by"] == "gemini-pro" and not entry.get("pending")
     and "fallback" not in entry
     and "cost_usd" not in entry and entry["pick"]["sources"] == ["https://cafef.vn/aaa-tang-von.chn"], entry)
t.ok("...the message: the AI's pick and who answered", "⭐ AI chọn: " in text and "Trả lời: gmw/gemini-pro qua 9Router" in text
     and "claude" not in text.lower() and "USD" not in text, text)
t.ok("...the session is done (not asked again)", cp.PickLog(evening_dir).status(DAY, NOW) == "done")
r = Router(fenced(answer))
Evening(evening_dir, store=Store(), client=r, bars=bars, now=lambda: NOW).run(DAY)
t.ok("...a second run only shows it", r.seen == [])

env(**ROUTER, SIEUTINHIEU=1, AI_PICK_BOT=0)
bad_dir = DATA / "bad"
bad_dir.mkdir()
text = Evening(bad_dir, store=Store(), client=Router("xin lỗi", "không"), bars=bars, now=lambda: NOW).run(DAY)
t.ok("an answer that is never JSON: recorded as a failure, asked again on the next run",
     "Không có lựa chọn" in text and cp.PickLog(bad_dir).status(DAY, NOW) == "retryable")
text = Evening(bad_dir, store=Store(), client=Router(fenced(answer)), bars=bars, now=lambda: NOW).run(DAY)
t.ok("...and the next run answers it", cp.PickLog(bad_dir).get(DAY)["pick"]["symbol"] == "AAA")

env(**ROUTER, SIEUTINHIEU=1, AI_PICK_BOT=0)
retry_dir = DATA / "retry"
retry_dir.mkdir()
try:
    Evening(retry_dir, store=Store(), client=Router(error=openai.APIConnectionError(request=REQ)), bars=bars,
            now=lambda: NOW).run(DAY)
    t.ok("9Router down: Retry, the day free to try again", False)
except cp.Retry:
    saved = cp.PickLog(retry_dir).get(DAY)
    t.ok("9Router down: Retry, the day free to try again", saved and "9Router" in saved["error"]
         and cp.PickLog(retry_dir).status(DAY, NOW) == "retryable", saved)
text = Evening(retry_dir, now=lambda: NOW).give_up(DAY, "ConnectionError")
t.ok("given up after the day's tries: said once, the candidates kept", "không gọi được 9Router" in text
     and cp.PickLog(retry_dir).get(DAY).get("candidates"))

# ───────────── 5. the texts ─────────────
note = {"level": "stop", "symbol": "HPG", "at": f"{DAY}T10:30", "rule": "⛔", "cause": "chưa rõ", "news": "chưa có tin",
        "view": "GIỮ", "reason": "r"}
t.ok("an alert note says AI", "AI xem cảnh báo" in cp.note_text(note) and "AI: GIỮ" in cp.note_text(note))
t.ok("...and which model answered, with the hand-over", "Trả lời: gmw/gemini-pro qua 9Router (gmw/gemini-flash lỗi (HTTP 429))"
     in cp.note_text({**note, "model": "gmw/gemini-pro", "fallback": "gmw/gemini-flash lỗi (HTTP 429)"}))
week = {"week": "2026-W43", "day": DAY, "facts": ["f"], "text": "• ổn"}
t.ok("a weekly review says AI", "Nhận xét của AI:" in cp.week_text(week))

OLD_ENTRY = {"day": DAY, "at": f"{DAY}T15:52", "candidates": [], "note": "x", "model": "auto", "served_by": "9Router · auto",
             "via": "router9", "fallback": "chưa có " + "ANTHROPIC" + "_API_KEY"}
t.ok("a record of the old version: no word of its old setup, said as old",
     "Trả lời: auto qua 9Router (bản cũ trước v1.8, không tìm tin trên web)" in cp.entry_text(OLD_ENTRY)
     and "ANTHROPIC" not in cp.entry_text(OLD_ENTRY) and "_" not in cp.entry_text(OLD_ENTRY).split("Trả lời:")[1])

# ───────────── 6. the page ─────────────
from vnbot import ai_pick_web as cpw  # noqa: E402

env(**ROUTER, SIEUTINHIEU=1)
body = cpw.page_body(cp.AiPick(evening_dir, now=lambda: NOW))
t.ok("the page leads with the pick sheet: the code pressed as chosen", '<section class="card sheet"' in body
     and 'id="pick">AAA</h2><span class="stamp">Đã chọn</span>' in body
     and body.index("sheet") < body.index("Kết quả đo tới nay"))
t.ok("...says the models that answer now and the one that answered",
     "Trả lời: gmw/gemini-pro, khi lỗi chuyển sang gmw/gemini-flash (qua 9Router)" in body
     and "<dd>gmw/gemini-pro qua 9Router</dd>" in body and "claude" not in body.lower() and "USD" not in body)
t.ok("...the candidates restack on a phone, every cell labelled", '<table class="stack">' in body and 'data-label="Giá"' in body)
t.ok("...no dotted meta strings or dash placeholders", " · " not in body and "—" not in body)
t.ok("...the cited source links to the news site", 'href="https://cafef.vn/aaa-tang-von.chn"' in body)
body = cpw.page_body(cp.AiPick(retry_dir, now=lambda: NOW))
t.ok("a failed session: the error on the sheet, no stamp", "không có lựa chọn" in body and 'class="stamp"' not in body)

# ───────────── 7. a server that ran it under its old name ─────────────
from vnbot import settings as settings_module  # noqa: E402

old_dir = DATA / "old"
old_dir.mkdir()
OLD = "CLAUDE" + "_PICK"
(old_dir / ("claude" + "_pick.json")).write_text(json.dumps([{"day": DAY, "candidates": []}]), encoding="utf-8")
(old_dir / ("claude" + "_notes.json")).write_text("[]", encoding="utf-8")
(old_dir / "settings.json").write_text(json.dumps({OLD + "_TIME": "16:00", OLD + "_MODEL": "claude-opus-5-5",
                                                   OLD + "_EFFORT": "high", "WATCHLIST": "FPT"}), encoding="utf-8")
env(**{OLD + "_ALERTS": "3", OLD + "_MODEL": "claude-opus-5-5"})
settings_module._carry_over_pick(old_dir)
saved = json.loads((old_dir / "settings.json").read_text(encoding="utf-8"))
t.ok("the measured sessions and notes are kept under the new names", cp.PickLog(old_dir).get(DAY) is not None
     and (old_dir / "ai_notes.json").exists() and not (old_dir / ("claude" + "_pick.json")).exists())
t.ok("saved settings renamed; an Anthropic model dropped; the rest untouched", saved.get("AI_PICK_TIME") == "16:00"
     and "AI_PICK_MODEL" not in saved and saved.get("WATCHLIST") == "FPT" and not any(k.startswith(OLD) for k in saved), saved)
t.ok("environment variables carried over, an Anthropic model not", os.environ.get("AI_PICK_ALERTS") == "3"
     and "AI_PICK_MODEL" not in os.environ and cp.alerts_per_day() == 3 and cp.main_model() == "gmw/gemini-pro")
os.environ.pop(OLD + "_ALERTS")
os.environ.pop("AI_PICK_ALERTS")
os.environ.pop(OLD + "_MODEL")
settings_module._carry_over_pick(old_dir)
t.ok("a second start changes nothing", json.loads((old_dir / "settings.json").read_text(encoding="utf-8")) == saved)

# ───────────── 8. a run by hand: the newest data of today ─────────────
from unittest import mock  # noqa: E402

from vnbot import market_scan  # noqa: E402

FRI = datetime(2026, 10, 23, 10, 30)
plan = cp.manual_plan(FRI)
t.ok("in the session: today, on the matched prices", plan["day"] == "2026-10-23" and plan["live"] and "10:30" in plan["note"])
plan = cp.manual_plan(datetime(2026, 10, 23, 15, 30))
t.ok("after the site's final signals: today, final", plan["day"] == "2026-10-23" and not plan["live"]
     and "đã chốt" in plan["note"])
plan = cp.manual_plan(datetime(2026, 10, 23, 8, 0))
t.ok("before the open: the last session, said so", plan["day"] == "2026-10-22" and not plan["live"]
     and "chưa mở cửa" in plan["note"])
plan = cp.manual_plan(datetime(2026, 10, 24, 11, 0))
t.ok("on a day off: the last session, said so", plan["day"] == "2026-10-23" and "không giao dịch" in plan["note"])


class Closes:
    def series(self, codes, as_of):
        return {c: [20000.0 + i * 10 for i in range(60)] for c in codes}, {}, {}


SNAP = {"at": "2026-10-23T10:30", "basis": "2026-10-22", "size": 300, "rows": [
    {"symbol": "CCC", "state": "SELL", "price": 21500, "level": 21000, "dist": 2.4, "flip": "BUY", "value": 9e9},
    {"symbol": "DDD", "state": "BUY", "price": 9000, "level": 9500, "dist": -5.3, "flip": "SELL", "value": 8e9},
    {"symbol": "EEE", "state": "SELL", "price": 30500, "level": 30100, "dist": 1.3, "flip": "BUY", "value": 7e9}]}
with mock.patch.object(market_scan, "scan", return_value=SNAP) as scanned:
    got, why = cp.live_candidates(Closes(), 300, FRI)
t.ok("live candidates: the stocks past the level that turns them to BUY now, marked live",
     [c["symbol"] for c in got] == ["CCC", "EEE"] and all(c["live"] and c["source"] == "bot" for c in got)
     and got[0]["price"] == 21500 and got[0]["level"] == 21000 and why == "" and scanned.called)
t.ok("...the prompt says the price is matched, not closed", "giá khớp 21.500 đã qua mức 21.000, chưa chốt"
     in cp.candidate_line(1, got[0]))
with mock.patch.object(market_scan, "scan", return_value={"error": "ngoài giờ giao dịch"}):
    t.ok("no scan: none, and why", cp.live_candidates(Closes(), 300, FRI) == ([], "ngoài giờ giao dịch"))

env(**ROUTER, SIEUTINHIEU=1)
live_dir = DATA / "live"
live_dir.mkdir()
r = Router(fenced({"pick": {"symbol": "CCC", "confidence": 55, "reason": "vượt mức", "sources": []}, "avoid": [],
                   "ranking": ["CCC", "EEE"], "market_note": "x"}))
with mock.patch.object(market_scan, "scan", return_value=SNAP):
    text = Evening(live_dir, store=Store(), client=r, bars=bars, closes=Closes(), now=lambda: FRI).run(
        "2026-10-23", force=True, live=True)
entry = cp.PickLog(live_dir).get("2026-10-23")
t.ok("a live run: today, the live candidates, recorded as provisional", entry["pick"]["symbol"] == "CCC"
     and entry["provisional"] and entry["live"] == "2026-10-23T10:30" and [c["symbol"] for c in entry["candidates"]] == ["CCC", "EEE"])
t.ok("...the question says it is the session, the matched prices", "ĐANG TRONG PHIÊN 23/10 lúc 10:30" in r.seen[0]["messages"][1]["content"])
t.ok("...the message says the data is of the session and asked again at the close", "Dữ liệu trong phiên lúc 10:30" in text
     and "trong phiên, giá khớp" in text, text)
t.ok("...the scheduler asks again with the final signals", cp.PickLog(live_dir).status("2026-10-23", FRI) == "retryable")
r = Router(fenced(answer))
Evening(live_dir, store=Store(), client=r, bars=bars, now=lambda: datetime(2026, 10, 23, 15, 50)).run("2026-10-23")
entry = cp.PickLog(live_dir).get("2026-10-23")
t.ok("the evening run replaces it, keeping a trace of the earlier answer", entry["pick"]["symbol"] == "AAA"
     and not entry.get("provisional") and entry["earlier"] == [{"at": "2026-10-23T10:30", "pick": "CCC", "live": True}]
     and cp.PickLog(live_dir).status("2026-10-23", FRI) == "done")
r = Router(fenced({**answer, "pick": {"symbol": "BBB", "confidence": 50, "reason": "r", "sources": []}}))
Evening(live_dir, store=Store(), client=r, bars=bars, now=lambda: datetime(2026, 10, 23, 16, 10)).run("2026-10-23", force=True)
entry = cp.PickLog(live_dir).get("2026-10-23")
t.ok("by hand on an answered session: asked again (force), the earlier answers kept", len(r.seen) == 1
     and entry["pick"]["symbol"] == "BBB" and [x["pick"] for x in entry["earlier"]] == ["CCC", "AAA"])


class Holder(cp.AiPick):
    pass


h = Holder(live_dir, board=lambda symbols: {"HPG": {"price": 24000}}, now=lambda: FRI)
out = h._at_matched_prices([{"symbol": "HPG", "qty": 100, "avg": 26000, "close": 26500, "stop": 25000, "target": None,
                             "site": None, "rule": "GIỮ", "rule_why": "x"}])
t.ok("holdings in the session: the matched price, the rule judged on it", out[0]["close"] == 24000 and out[0]["live"]
     and out[0]["rule"] == "BÁN" and out[0]["pnl_pct"] == round((24000 / 26000 - 1) * 100, 2))
t.ok("...said as the matched price in the question", "giá khớp 24.000" in cp.holding_line({**out[0], "weight": 10}))

# the page: what the button will use, and where the run is
cpw._LAST.clear()
with mock.patch.object(cp, "manual_plan", return_value={"day": "2026-10-23", "live": True, "note": "giá khớp trong phiên hôm nay lúc 10:30"}):
    body = cpw.page_body(cp.AiPick(live_dir, now=lambda: FRI))
t.ok("the button says what a run now will use", "Chạy ngay với dữ liệu mới nhất" in body
     and "Bấm lúc này: phiên 23/10, giá khớp trong phiên hôm nay lúc 10:30." in body and "Đang gửi…" in body)
cpw._LAST.update(at="10:30", day="2026-10-23", note="giá khớp trong phiên hôm nay lúc 10:30")
with mock.patch.object(cp.AiPick, "running", new_callable=mock.PropertyMock, return_value=True):
    body = cpw.page_body(cp.AiPick(live_dir, now=lambda: FRI))
t.ok("while it runs: since when, on which data", "Chạy tay lúc 10:30: phiên 23/10, dữ liệu giá khớp trong phiên hôm nay lúc 10:30. "
     "Đang chạy" in body)
cpw._LAST.update(result="Xong lúc 10:32: AI chọn CCC.", kind="ok")
body = cpw.page_body(cp.AiPick(live_dir, now=lambda: FRI))
t.ok("when it is done: the result", "Xong lúc 10:32: AI chọn CCC." in body and "alert-ok" in body)

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

app = FastAPI()
cpw.register(app, live_dir, guard=lambda: None, post_guard=[])
calls = []


def fake_run(self, day=None, *, force=False, live=False):
    calls.append((day, force, live))
    return "ok"


cpw._LAST.clear()
with mock.patch.object(cp.AiPick, "run", fake_run), \
        mock.patch.object(cp, "manual_plan", return_value={"day": "2026-10-23", "live": True, "note": "giá khớp lúc 10:30"}):
    page = TestClient(app).post("/thamdinh/chay")
old = TestClient(app).get("/chonbong", follow_redirects=False)
t.ok("the old address leads to the new one", old.status_code == 301 and old.headers["location"] == "/thamdinh")
t.ok("the button: a forced run of today's live data, the page then says so", calls == [("2026-10-23", True, True)]
     and page.status_code == 200 and "Chạy tay lúc" in page.text and "AI chọn BBB" in page.text, (calls, cpw._LAST))

t.done()
