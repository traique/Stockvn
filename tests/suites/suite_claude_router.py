"""Claude chọn bóng with 9Router standing in: no key, Claude's API failing, the month's cap;
what is asked of 9Router, what it answers, what is recorded and said (``claude_pick``)."""
import json
import os
from datetime import datetime
from types import SimpleNamespace as NS

import _common  # noqa: F401 - clean env, no .env, offline, temp folder, repo on sys.path

import anthropic  # noqa: E402
import httpx  # noqa: E402
import openai  # noqa: E402

from tradingagents.llm_clients import fallback_client as fc  # noqa: E402
from vnbot import claude_pick as cp  # noqa: E402

t = _common.Checks()
DATA = _common.ROOT / "data"
DATA.mkdir()
REQ = httpx.Request("POST", "https://api.example/v1")
DAY = "2026-10-23"
NOW = datetime(2026, 10, 23, 15, 55)
TOOL = cp.submit_tool(["AAA", "BBB"], ["HPG"])
ANSWER = {"pick": {"symbol": "AAA", "confidence": 60, "reason": "nền giá chặt", "sources": ["https://cafef.vn/x"]},
          "avoid": [{"symbol": "BBB", "reason": "đã tăng xa", "sources": []}], "ranking": ["AAA", "BBB"],
          "holdings": [{"symbol": "HPG", "view": "GIỮ", "reason": "chưa thủng cắt lỗ", "sources": []}],
          "market_note": "thị trường đi ngang"}


def env(**values) -> None:
    for key in ("ANTHROPIC_API_KEY", "ROUTER9_BASE_URL", "ROUTER9_API_KEY", "ROUTER9_MODEL", "CLAUDE_PICK_ROUTER9",
                "CLAUDE_PICK_ROUTER9_MODEL", "CLAUDE_PICK_MONTHLY_USD", "SIEUTINHIEU", "CLAUDE_PICK", "CLAUDE_PICK_BOT"):
        os.environ.pop(key, None)
    os.environ.update({k: str(v) for k, v in values.items()})
    fc.reset_provider_state()


ROUTER = {"ROUTER9_BASE_URL": "https://router.example/v1", "ROUTER9_API_KEY": "r9"}


class Claude:
    """Anthropic's client: raises ``error``, or answers ``answer`` through the tool."""

    def __init__(self, error=None, answer=None, stop="tool_use"):
        self.error, self.answer, self.stop, self.calls = error, answer, stop, 0
        self.beta = NS(messages=NS(create=self.create))

    def create(self, **kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        usage = NS(input_tokens=1000, output_tokens=500, cache_creation_input_tokens=0, cache_read_input_tokens=0,
                   iterations=None, server_tool_use=NS(web_search_requests=2))
        content = [NS(type="tool_use", name=kwargs["tools"][-1]["name"], input=self.answer)] if self.stop == "tool_use" else []
        return NS(model=kwargs["model"], usage=usage, content=content, stop_reason=self.stop)


class Router:
    """9Router's OpenAI-compatible client: raises ``error``, or answers through the tool call
    (``args``), or in text (``text``)."""

    def __init__(self, error=None, args=None, text=None, finish="tool_calls"):
        self.error, self.args, self.text, self.finish, self.seen = error, args, text, finish, []
        self.chat = NS(completions=NS(create=self.create))

    def create(self, **kwargs):
        self.seen.append(kwargs)
        if self.error is not None:
            raise self.error
        calls = None
        if self.args is not None:
            calls = [NS(function=NS(name=kwargs["tools"][0]["function"]["name"], arguments=json.dumps(self.args)))]
        return NS(model=kwargs["model"], usage=NS(prompt_tokens=900, completion_tokens=300),
                  choices=[NS(finish_reason=self.finish, message=NS(content=self.text, tool_calls=calls))])


def pick(claude=None, router=None) -> cp.ClaudePick:
    return cp.ClaudePick(DATA, client=claude, router=router, now=lambda: NOW)


# ───────────── 1. who answers ─────────────
env(SIEUTINHIEU=1)
t.ok("nothing configured: off, and the message names both ways",
     not cp.enabled() and "ANTHROPIC_API_KEY" in cp.why_off() and "9Router" in cp.why_off() and cp.route()["main"] == "")
env(SIEUTINHIEU=1, **ROUTER)
t.ok("only 9Router: on, 9Router answers", cp.enabled() and cp.why_off() == "" and cp.route()["main"] == "router9"
     and not cp.route()["backup"])
env(SIEUTINHIEU=1, ANTHROPIC_API_KEY="k", **ROUTER)
t.ok("both: Claude answers, 9Router stands by", cp.route() == {**cp.route(), "main": "claude", "backup": True})
env(SIEUTINHIEU=1, CLAUDE_PICK_ROUTER9=0, **ROUTER)
t.ok("CLAUDE_PICK_ROUTER9=0: 9Router never stands in", not cp.enabled() and not cp.router_ready())
env(SIEUTINHIEU=1, ROUTER9_API_KEY="r9")
t.ok("9Router without its endpoint is not ready", not cp.router_ready())
env(ROUTER9_MODEL="gpt-x", **ROUTER)
t.ok("the model: ROUTER9_MODEL, CLAUDE_PICK_ROUTER9_MODEL first", cp.router_model() == "gpt-x"
     and (os.environ.update(CLAUDE_PICK_ROUTER9_MODEL="cc/claude-sonnet") or cp.router_model() == "cc/claude-sonnet"))

# ───────────── 2. the request to 9Router ─────────────
env(**ROUTER, ROUTER9_MODEL="gpt-x")
r = Router(args=ANSWER)
raw, meta = cp.ask_router("đề bài", TOOL, client=r)
sent = r.seen[0]
t.ok("the same question as a function call, the tool forced", sent["model"] == "gpt-x"
     and sent["tools"][0]["function"]["name"] == cp.TOOL and sent["tools"][0]["function"]["parameters"] == TOOL["input_schema"]
     and sent["tool_choice"] == {"type": "function", "function": {"name": cp.TOOL}}
     and sent["messages"][1] == {"role": "user", "content": "đề bài"})
t.ok("the system says there is no search and no source", sent["messages"][0]["content"].startswith(cp.SYSTEM[:40])
     and "KHÔNG có công cụ web_search" in sent["messages"][0]["content"])
t.ok("the answer and the meta: via 9Router, no cost, no page found", raw == ANSWER and meta["via"] == "router9"
     and meta["cost"] == 0.0 and meta["urls"] == set() and meta["served_by"] == "9Router · gpt-x"
     and meta["usage"]["input"] == 900 and meta["usage"]["output"] == 300)
t.ok("a source it cites is dropped (no search was made)", cp.clean(raw, [{"symbol": "AAA"}, {"symbol": "BBB"}], meta["urls"],
                                                                   ["HPG"])["pick"]["sources"] == [])
raw, _ = cp.ask_router("đề bài", TOOL, client=Router(text='Đây là kết quả:\n```json\n{"pick": {"symbol": "AAA"}}\n```'))
t.ok("an answer written as JSON in the text is read", raw == {"pick": {"symbol": "AAA"}})
text, _ = cp.ask_router("dữ kiện", None, client=Router(text="  • giữ kỷ luật  "))
t.ok("without a tool: the text", text == "• giữ kỷ luật")
for name, router, word in (("no answer through the tool", Router(text="tôi không chắc"), "không gửi kết quả"),
                           ("cut by max_tokens", Router(text="{", finish="length"), "bị cắt"),
                           ("an empty text", Router(text=""), None)):
    try:
        cp.ask_router("x", TOOL if word else None, client=router)
        t.ok(f"{name}: PickError", False)
    except cp.PickError as exc:
        t.ok(f"{name}: PickError, nothing paid", (word is None or word in str(exc)) and exc.meta["cost"] == 0.0, str(exc))
env(**ROUTER)
try:
    cp.ask_router("x", TOOL, client=Router(error=openai.APIConnectionError(request=REQ)))
    t.ok("9Router unreachable: Retry", False)
except cp.Retry as exc:
    t.ok("9Router unreachable: Retry", "9Router" in str(exc))
t.ok("...and it rests, shared with the agents' chain", fc.is_resting("router9"))
r = Router(args=ANSWER)
try:
    cp.ask_router("x", TOOL, client=r)
    t.ok("while resting: Retry at once, nothing sent", False)
except cp.Retry:
    t.ok("while resting: Retry at once, nothing sent", r.seen == [])
env(**ROUTER, LLM_MAX_CALLS_PER_DAY=1)
cp.ask_router("x", TOOL, client=Router(args=ANSWER))
try:
    cp.ask_router("x", TOOL, client=Router(args=ANSWER))
    t.ok("the day's AI budget counts 9Router's answers", False)
except cp.Retry as exc:
    t.ok("the day's AI budget counts 9Router's answers", "hạn mức" in str(exc), str(exc))
os.environ.pop("LLM_MAX_CALLS_PER_DAY")

# ───────────── 3. Claude first, 9Router when it cannot ─────────────
env(**ROUTER, ROUTER9_MODEL="gpt-x")
c, r = Claude(answer=ANSWER), Router(args=ANSWER)
raw, meta = pick(c, r)._ask("đề", TOOL)
t.ok("no key: 9Router answers, Claude never called", c.calls == 0 and meta["via"] == "router9"
     and meta["fallback"] == "chưa có ANTHROPIC_API_KEY" and raw == ANSWER)
env(**ROUTER, ANTHROPIC_API_KEY="k")
c, r = Claude(answer=ANSWER), Router(args=ANSWER)
raw, meta = pick(c, r)._ask("đề", TOOL)
t.ok("Claude answers: 9Router not called", c.calls == 1 and r.seen == [] and "via" not in meta and meta["cost"] > 0)
cases = (("Claude unreachable", anthropic.APIConnectionError(request=REQ), "Claude không trả lời"),
         ("key refused (401)", anthropic.AuthenticationError("bad key", response=httpx.Response(401, request=REQ), body=None),
          "AuthenticationError"),
         ("server error (529)", anthropic.APIStatusError("overloaded", response=httpx.Response(529, request=REQ), body=None),
          "Claude không trả lời"),
         ("credit exhausted (400)", anthropic.BadRequestError("credit balance is too low",
                                                              response=httpx.Response(400, request=REQ), body=None), "credit"))
for name, error, word in cases:
    env(**ROUTER, ANTHROPIC_API_KEY="k")
    r = Router(args=ANSWER)
    raw, meta = pick(Claude(error=error), r)._ask("đề", TOOL)
    t.ok(f"{name}: 9Router answers, the reason kept", raw == ANSWER and meta["via"] == "router9" and word in meta["fallback"]
         and meta["cost"] == 0.0 and len(r.seen) == 1, meta.get("fallback"))
env(**ROUTER, ANTHROPIC_API_KEY="k")
raw, meta = pick(Claude(error=anthropic.APITimeoutError(request=REQ)), Router(args=ANSWER))._ask("đề", TOOL)
t.ok("Claude timed out: 9Router answers, Claude's estimated cost still counted", meta["via"] == "router9"
     and meta["cost"] > 0 and meta["estimated"] and meta["claude_model"] == cp.model())
try:
    pick(Claude(stop="refusal"), Router(args=ANSWER))._ask("đề", TOOL)
    t.ok("a refusal is Claude's answer: no stand-in", False)
except cp.PickError as exc:
    t.ok("a refusal is Claude's answer: no stand-in", "từ chối" in str(exc))
env(**ROUTER, ANTHROPIC_API_KEY="k", CLAUDE_PICK_ROUTER9=0)
try:
    pick(Claude(error=anthropic.APIConnectionError(request=REQ)), Router(args=ANSWER))._ask("đề", TOOL)
    t.ok("stand-in off: Claude's Retry as before", False)
except cp.Retry:
    t.ok("stand-in off: Claude's Retry as before", True)
env(**ROUTER, ANTHROPIC_API_KEY="k", CLAUDE_PICK_MONTHLY_USD=0)
c = Claude(answer=ANSWER)
raw, meta = pick(c, Router(args=ANSWER))._ask("đề", TOOL)
t.ok("the month's cap reached: 9Router answers, Claude not paid", c.calls == 0 and meta["via"] == "router9"
     and "CLAUDE_PICK_MONTHLY_USD" in meta["fallback"])
env(ANTHROPIC_API_KEY="k", CLAUDE_PICK_MONTHLY_USD=0)
t.ok("the cap without 9Router: blocked as before", "CLAUDE_PICK_MONTHLY_USD" in pick()._blocked())
env(**ROUTER, ANTHROPIC_API_KEY="k")
try:
    pick(Claude(error=anthropic.APIConnectionError(request=REQ)), Router(error=openai.APIConnectionError(request=REQ)))._ask("đề", TOOL)
    t.ok("both unreachable, nothing paid: Retry naming both", False)
except cp.Retry as exc:
    t.ok("both unreachable, nothing paid: Retry naming both", "Claude" in str(exc) and "9Router" in str(exc))
env(**ROUTER, ANTHROPIC_API_KEY="k")
try:
    pick(Claude(error=anthropic.APITimeoutError(request=REQ)), Router(error=openai.APIConnectionError(request=REQ)))._ask("đề", TOOL)
    t.ok("Claude paid then 9Router down: PickError carrying Claude's cost", False)
except cp.PickError as exc:
    t.ok("Claude paid then 9Router down: PickError carrying Claude's cost", exc.meta["cost"] > 0 and "9Router" in str(exc))


# ───────────── 4. a whole evening through 9Router ─────────────
class Store:
    def refresh(self, symbols, fresh_after=None):
        return [], None

    def fetched_at(self, symbol):
        return f"{DAY}T15:20"

    def signals(self, symbol):
        return []

    def state(self, symbol, day, cutoff):
        return {"side": "BUY", "day": DAY, "live": True, "price": 20000, "sl": 18500, "tp1": 23000, "strength_pct": 70}


class Evening(cp.ClaudePick):
    def symbols(self):
        return ["AAA", "BBB"]

    def _held(self):
        return []


def bars(symbol, day):
    return [{"d": f"2026-10-{d:02d}", "o": 20000.0, "h": 20500.0, "l": 19800.0, "c": 20000.0 + d * 50, "v": 1e6}
            for d in range(1, 24)]


env(**ROUTER, ROUTER9_MODEL="gpt-x", SIEUTINHIEU=1, CLAUDE_PICK_BOT=0)
evening_dir = DATA / "evening"
evening_dir.mkdir()
r = Router(args={k: v for k, v in ANSWER.items() if k != "holdings"})
text = Evening(evening_dir, store=Store(), router=r, bars=bars, now=lambda: NOW).run(DAY)
entry = cp.PickLog(evening_dir).get(DAY)
t.ok("the evening without a key: answered by 9Router and recorded so", entry["pick"]["symbol"] == "AAA"
     and entry["via"] == "router9" and entry["served_by"] == "9Router · gpt-x" and not entry.get("pending")
     and entry["fallback"] == "chưa có ANTHROPIC_API_KEY" and not entry.get("cost_usd") and "effort" not in entry, entry)
t.ok("...the message says who answered and why", "9Router trả lời thay Claude (9Router · gpt-x)" in text
     and "chưa có ANTHROPIC_API_KEY" in text and "Claude chọn: " in text, text)
t.ok("...and the session counts as done (not asked again)", cp.PickLog(evening_dir).status(DAY, NOW) == "done")

env(**ROUTER, SIEUTINHIEU=1, CLAUDE_PICK_BOT=0)
retry_dir = DATA / "retry"
retry_dir.mkdir()
try:
    Evening(retry_dir, store=Store(), router=Router(error=openai.APIConnectionError(request=REQ)), bars=bars,
            now=lambda: NOW).run(DAY)
    t.ok("9Router down without a key: Retry, the day free to try again", False)
except cp.Retry:
    saved = cp.PickLog(retry_dir).get(DAY)
    t.ok("9Router down without a key: Retry, the day free to try again", saved and "9Router" in saved["error"]
         and not saved.get("cost_usd") and cp.PickLog(retry_dir).status(DAY, NOW) != "done", saved)

# ───────────── 5. the texts ─────────────
note = {"level": "stop", "symbol": "HPG", "at": f"{DAY}T10:30", "rule": "⛔", "cause": "chưa rõ", "news": "chưa xem tin",
        "view": "GIỮ", "reason": "r", "via": "router9", "served_by": "9Router · gpt-x", "fallback": "Claude không trả lời"}
t.ok("an alert note by 9Router says so", "9Router trả lời thay Claude" in cp.note_text(note) and "9Router: GIỮ" in cp.note_text(note))
week = {"week": "2026-W43", "day": DAY, "facts": ["f"], "text": "• ổn", "via": "router9", "served_by": "9Router · gpt-x"}
t.ok("a weekly review by 9Router says so", "Nhận xét của 9Router:" in cp.week_text(week))
t.ok("Claude's own texts unchanged", "9Router" not in cp.week_text({**week, "via": None}))

# ───────────── 6. the page ─────────────
from vnbot import claude_pick_web as cpw  # noqa: E402

env(**ROUTER, ROUTER9_MODEL="gpt-x", SIEUTINHIEU=1)
body = cpw.page_body(cp.ClaudePick(evening_dir, now=lambda: NOW))
t.ok("the page leads with the pick sheet: the code pressed as chosen", '<section class="card sheet"' in body
     and 'id="pick">AAA</h2><span class="stamp">Đã chọn</span>' in body
     and body.index("sheet") < body.index("Kết quả đo tới nay"))
t.ok("...says who answers now and who answered this session",
     "Trả lời: 9Router (gpt-x), vì chưa có ANTHROPIC_API_KEY" in body and "9Router (gpt-x) trả lời thay Claude" in body)
t.ok("...the candidates restack on a phone, every cell labelled", '<table class="stack">' in body and 'data-label="Giá"' in body)
t.ok("...no dotted meta strings or dash placeholders", " · " not in body.split("<style")[0] and "—" not in body)
os.environ["ANTHROPIC_API_KEY"] = "k"
t.ok("with a key: Claude answers, 9Router stands by", "Trả lời: Claude (claude-opus-5-5), dự phòng 9Router (gpt-x)" in cpw.page_body(
    cp.ClaudePick(evening_dir, now=lambda: NOW)))
body = cpw.page_body(cp.ClaudePick(retry_dir, now=lambda: NOW))
t.ok("a failed session: the error on the sheet, no stamp", "không có lựa chọn" in body and 'class="stamp"' not in body)

t.done()
