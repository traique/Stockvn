"""AI providers shared by the stock bot and the altcoin bot (offline):

- tradingagents/llm_clients/ai_share.py and altcoin/bot/ai_share.py are the same file; the ledger:
  fingerprints, the shared cap of the day, rests, a new day, a restart, stale or broken files;
- the stock's chain (fallback_client.run_chain): the altcoin's requests count against the stock's
  cap on the same key, a rest either bot puts holds for the other, which failures are shared;
- OpenRouter at call time (openrouter_models): ranked as the altcoin ranks (same constants, same
  list from the same answer), a model gone or busy upstream is left out and the next one answers,
  OPENROUTER_MAX_TRIES, "id,auto";
- the model that answered: LocalCompatibleChatOpenAI keeps it, run_chain hands it to last_call()
  and provider_status(), the critic records it;
- the altcoin's AIFilter on a stubbed HTTP: it skips what the stock rests or caps, counts its
  requests, shares its provider-wide rests (not a model busy upstream), gives a transport error
  back, records the served model, shows the sharing (/alt ai, public_stats).
"""
import ast
import json
import os
import sys
import time
from pathlib import Path

import _common  # noqa: F401 - clean env, no .env, offline, temp folder, repo on sys.path

t = _common.Checks()
REPO = _common.REPO
ALT = REPO / "altcoin"
SHARE = _common.ROOT / "ai_share"

# ── 1. one file, two places ──
t.ok("ai_share.py: the stock's and the altcoin's copies are the same file, byte for byte",
     (REPO / "tradingagents/llm_clients/ai_share.py").read_bytes() == (ALT / "bot/ai_share.py").read_bytes())

from tradingagents.llm_clients import ai_share  # noqa: E402

fp = ai_share.fingerprint("https://openrouter.ai/api/v1", "sk-or-1")
t.ok("fingerprint: 16 hex, same pair same value (a trailing / and the case of the URL aside), never the key",
     len(fp) == 16 and fp == ai_share.fingerprint("https://OpenRouter.ai/api/v1/", "sk-or-1")
     and fp != ai_share.fingerprint("https://openrouter.ai/api/v1", "sk-or-2") and "sk-or-1" not in fp)

alone = ai_share.Ledger("stock", "")
alone.register("openrouter", fp, 45)
alone.count("openrouter", fp)
t.ok("no AI_SHARE_DIR: nothing written, nothing shared", not alone.enabled and alone.check("openrouter", fp, 45) is None
     and not SHARE.exists())

stock = ai_share.Ledger("stock", SHARE)
alt = ai_share.Ledger("altcoin", SHARE)
stock.register("openrouter", fp, 45)
alt.register("openrouter", fp)
for _ in range(40):
    stock.count("openrouter", fp)
for _ in range(4):
    alt.count("openrouter", fp)
t.ok("shared cap: the stock's 45 holds for both (40 + 4 = 44: still open)", alt.check("openrouter", fp) is None
     and alt.peer_calls("openrouter", fp) == 40 and stock.peer_calls("openrouter", fp) == 4
     and alt.shared_cap("openrouter", fp) == 45, alt.check("openrouter", fp))
alt.count("openrouter", fp)
why = alt.cap_reached("openrouter", fp)
t.ok("...45 together: both are stopped, saying why", why and "45/45" in why and "stock" in why
     and stock.cap_reached("openrouter", fp, 45), why)
other = ai_share.fingerprint("https://openrouter.ai/api/v1", "sk-or-alt")
t.ok("another key: nothing shared", alt.check("openrouter", other) is None and alt.peer_calls("openrouter", other) == 0)
alt.count("openrouter", fp, -1)
t.ok("count -1 gives one back (a request that never reached the provider)", alt.calls("openrouter") == 4)

stock.register("router9", "r9", 0)
stock.rest("router9", "r9", 120, "rate limited (429)")
why = alt.peer_rest("router9", "r9")
t.ok("a rest of the stock holds for the altcoin, with its reason", why and "stock" in why and "429" in why, why)
stock.clear_rest("router9")
t.ok("...cleared when the provider answers again", alt.peer_rest("router9", "r9") is None)
stock.rest("router9", "r9", 0, "nothing")
t.ok("a rest of 0 s writes nothing", alt.peer_rest("router9", "r9") is None)

again = ai_share.Ledger("altcoin", SHARE)
t.ok("a restart keeps the day's count", again.calls("openrouter") == 4)
data = json.loads((SHARE / "stock.json").read_text(encoding="utf-8"))
data["day"] = "2000-01-01"
(SHARE / "stock.json").write_text(json.dumps(data), encoding="utf-8")
t.ok("another day in the peer's file: its count does not hold today", alt.peer_calls("openrouter", fp) == 0
     and alt.cap_reached("openrouter", fp) is None)
yesterday = ai_share.Ledger("stock", SHARE)
t.ok("...and its own count starts again at 0 on its next start", yesterday.calls("openrouter") == 0)
data["ts"] = time.time() - ai_share.FRESH - 10
(SHARE / "stock.json").write_text(json.dumps(data), encoding="utf-8")
t.ok("a stale file (a bot gone for days) is ignored", not alt.peers())
(SHARE / "stock.json").write_text("{broken", encoding="utf-8")
t.ok("a broken file is ignored", not alt.peers())
alt.set_extra(ai={"calls_today": 3})
t.ok("extra: what the others show", any(p.get("extra", {}).get("ai", {}).get("calls_today") == 3
                                         for p in ai_share.Ledger("stock", SHARE).peers()))

# ── 2. the stock's chain ──
import httpx  # noqa: E402
import openai  # noqa: E402

from tradingagents.llm_clients import fallback_client as fc  # noqa: E402
from tradingagents.llm_clients import openrouter_models as orm  # noqa: E402

for f in SHARE.glob("*.json"):
    f.unlink()
os.environ.update(ROUTER9_BASE_URL="http://9router.test/v1", ROUTER9_API_KEY="r9-key", OPENROUTER_API_KEY="sk-or-1",
                  PROVIDER_ORDER="router9,openrouter", AI_SHARE_DIR=str(SHARE), LLM_MAX_CALLS_PER_DAY="0")
FREE = [{"id": f"vendor/m{i}:free", "context_length": 32000} for i in range(5)] + \
       [{"id": "google/gemma-4-31b-it:free", "context_length": 32000}, {"id": "openrouter/free"},
        {"id": "x/guard-7b:free"}, {"id": "y/tiny:free", "context_length": 4000},
        {"id": "z/img:free", "architecture": {"output_modalities": ["image"]}}, {"id": "paid/model"}]
orm.FETCH = lambda base: FREE
fp9 = ai_share.fingerprint("http://9router.test/v1", "r9-key")
alt = ai_share.Ledger("altcoin", SHARE)


def api_error(status, text=""):
    req = httpx.Request("POST", "https://x.test/v1/chat/completions")
    resp = httpx.Response(status, request=req, json={"error": {"message": text}})
    cls = {429: openai.RateLimitError, 404: openai.NotFoundError, 401: openai.AuthenticationError,
           403: openai.PermissionDeniedError, 400: openai.BadRequestError}.get(status, openai.InternalServerError)
    return cls(text or f"HTTP {status}", response=resp, body=None)


class Answer:
    def __init__(self, model):
        self.model = model


class Fake:
    """A chat model stand-in: ``script[model_name]`` is an exception to raise or a served name."""

    def __init__(self, model_name):
        self.model_name = model_name


script: dict = {}
asked: list = []


def call(model):
    asked.append(model.model_name)
    got = script.get(model.model_name, model.model_name)
    if isinstance(got, Exception):
        raise got
    return Answer(got)


def chain(spec="auto"):
    return [("router9", Fake("auto")), ("openrouter", fc.ModelPool(spec, "https://openrouter.ai/api/v1", Fake))]


def fresh():
    global alt
    fc.reset_provider_state()
    orm.reset()
    script.clear()
    asked.clear()
    for f in SHARE.glob("*.json"):
        f.unlink()
    alt = ai_share.Ledger("altcoin", SHARE)


fresh()
ranked = orm.models("auto", "https://openrouter.ai/api/v1")
t.ok("OpenRouter auto: the free text models, ranked (gemma-4-31b first), guards/tiny/image/paid out, router last",
     ranked[0] == "google/gemma-4-31b-it:free" and ranked[-1] == "openrouter/free"
     and not {"x/guard-7b:free", "y/tiny:free", "z/img:free", "paid/model"} & set(ranked) and len(ranked) == 7, ranked)
t.ok("'id,auto': that id first, then the free ones", orm.models("a/b:free,auto", "https://openrouter.ai/api/v1")[:2]
     == ["a/b:free", "google/gemma-4-31b-it:free"])
t.ok("an id alone: that id only", orm.models("a/b:free", "https://openrouter.ai/api/v1") == ["a/b:free"])

src = (ALT / "bot/ai.py").read_text(encoding="utf-8")
consts = {n.targets[0].id: ast.literal_eval(n.value) for n in ast.parse(src).body
          if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id in ("_OR_BLOCK", "_OR_PREFER")}
t.ok("ranking constants equal to the altcoin's (_OR_BLOCK, _OR_PREFER)",
     consts == {"_OR_BLOCK": orm.BLOCK, "_OR_PREFER": orm.PREFER}, consts)

fresh()
result = fc.run_chain(chain(), call)
t.ok("9Router auto answers; the model it picked is kept (last_call, provider_status)",
     result.model == "auto" and fc.last_call().get("served") == "auto" and asked == ["auto"])
script["auto"] = "gpt-5-mini"
fc.run_chain(chain(), call)
st = {r["name"]: r for r in fc.provider_status()}
t.ok("...the served name when the endpoint says another (9Router on auto)", fc.last_call() ==
     {"provider": "router9", "label": "9Router", "model": "auto", "served": "gpt-5-mini", "ts": fc.last_call()["ts"]}
     and st["router9"]["served"] == "gpt-5-mini", (fc.last_call(), st["router9"]))
t.ok("the stock's own file: its requests on each provider, its cap (OpenRouter 45)",
     alt.peer_calls("router9", fp9) == 2 and alt.shared_cap("openrouter", fp) == 45)

fresh()
alt.register("router9", fp9)
alt.rest("router9", fp9, 300, "rate limited: busy")
result = fc.run_chain(chain(), call)
t.ok("the altcoin rests 9Router: the stock goes straight to OpenRouter", asked == ["google/gemma-4-31b-it:free"]
     and fc.is_resting("router9"), asked)
st = {r["name"]: r for r in fc.provider_status()}
t.ok("...shown: resting by the other bot, with its reason", st["router9"]["peer_resting"]
     and "altcoin" in st["router9"]["last_error"] and st["router9"]["shared_with"] == ["altcoin"], st["router9"])

fresh()
alt.register("openrouter", fp)
for _ in range(45):
    alt.count("openrouter", fp)
script["auto"] = api_error(500, "gateway down")
try:
    fc.run_chain(chain(), call)
    raised = ""
except Exception as exc:  # noqa: BLE001
    raised = str(exc)
t.ok("the altcoin's 45 OpenRouter requests today: the stock's cap is reached, OpenRouter gets nothing",
     asked == ["auto"] and "45/45" in raised, (asked, raised))
st = {r["name"]: r for r in fc.provider_status()}
t.ok("...shown: calls of the other bot on the same key against the shared cap",
     st["openrouter"]["peer_calls"] == 45 and st["openrouter"]["cap"] == 45 and st["openrouter"]["calls_today"] == 0)
t.ok("a 9Router 500: the stock rests it and shares the rest (the endpoint fails, not the request)",
     alt.peer_rest("router9", fp9) is not None)

fresh()
script["auto"] = api_error(400, "context length exceeded")
fc.run_chain(chain(), call)
t.ok("a 9Router 400 (this request too long): the stock rests it as before, but the altcoin is not told",
     fc.is_resting("router9") and alt.peer_rest("router9", fp9) is None)

fresh()
script["auto"] = openai.APIConnectionError(request=httpx.Request("POST", "http://9router.test/v1"))
fc.run_chain(chain(), call)
t.ok("9Router unreachable: shared rest, and the request given back in the shared count",
     alt.peer_rest("router9", fp9) is not None and alt.peer_calls("router9", fp9) == 0)

fresh()
script.update({"auto": api_error(429, "busy"), "google/gemma-4-31b-it:free": api_error(404, "No endpoints found"),
               "vendor/m0:free": api_error(429, "vendor/m0:free is temporarily rate-limited upstream")})
result = fc.run_chain(chain(), call)
t.ok("OpenRouter: a model gone (404) and one busy upstream are left out, the third answers, OpenRouter does not rest",
     asked == ["auto", "google/gemma-4-31b-it:free", "vendor/m0:free", "vendor/m1:free"]
     and result.model == "vendor/m1:free" and not fc.is_resting("openrouter")
     and set(orm.cooling()) == {"google/gemma-4-31b-it:free", "vendor/m0:free"}, asked)
t.ok("...left out for a while (gone: 6 h, busy: 10 min); the next call starts at the next model",
     orm.cooling()["google/gemma-4-31b-it:free"][0] > 5 * 3600 and orm.cooling()["vendor/m0:free"][0] <= 600
     and orm.models("auto", "x")[0] == "vendor/m1:free")
t.ok("...a 429 that is not upstream (the key's day spent) rests OpenRouter for both bots",
     fc.is_resting("router9") and alt.peer_rest("router9", fp9))

fresh()
fc.reset_provider_state()
script.update({m: api_error(404, "gone") for m in ranked[:3]})
os.environ["OPENROUTER_MAX_TRIES"] = "2"
try:
    fc.run_chain([("openrouter", fc.ModelPool("auto", "x", Fake))], call)
except Exception:  # noqa: BLE001
    pass
t.ok("OPENROUTER_MAX_TRIES=2: two OpenRouter models at most in one call", asked == ranked[:2], asked)
del os.environ["OPENROUTER_MAX_TRIES"]

fresh()
script.update({"auto": api_error(500, "x"), "google/gemma-4-31b-it:free": api_error(429, "Rate limit exceeded: "
                                                                                       "free-models-per-day")})
try:
    fc.run_chain(chain(), call)
except Exception:  # noqa: BLE001
    pass
t.ok("OpenRouter's day spent (429, not upstream): no second OpenRouter model in the same call, rest shared",
     asked == ["auto", "google/gemma-4-31b-it:free"] and fc.is_resting("openrouter")
     and alt.peer_rest("openrouter", fp), asked)

from tradingagents.llm_clients.openai_client import LocalCompatibleChatOpenAI, served_model  # noqa: E402

llm = LocalCompatibleChatOpenAI(model="auto", base_url="http://9router.test/v1", api_key="k")
llm._create_chat_result({"model": "gpt-5-mini", "choices": [{"index": 0, "finish_reason": "stop",
                                                              "message": {"role": "assistant", "content": "ok"}}]})
t.ok("LocalCompatibleChatOpenAI keeps the model the endpoint names, once", served_model() == "gpt-5-mini"
     and served_model() is None)

from vnbot import critic  # noqa: E402

fresh()
script["auto"] = "gpt-5-mini"
fc.run_chain(chain(), call)
t.ok("critic.answered_by: '9Router auto → gpt-5-mini'", critic.answered_by() == "9Router auto → gpt-5-mini",
     critic.answered_by())

# ── 3. the altcoin's AIFilter ──
for f in SHARE.glob("*.json"):
    f.unlink()
fc.reset_provider_state()
sys.path.insert(0, str(ALT))
import requests  # noqa: E402

from bot import ai as alt_ai  # noqa: E402
from bot.config import cfg  # noqa: E402

cfg.AI_ENABLED, cfg.AI_PROVIDER_ORDER, cfg.AI_MAX_TRIES = True, ["9router", "openrouter"], 6
cfg.NINEROUTER_BASE_URL, cfg.NINEROUTER_API_KEY, cfg.NINEROUTER_MODELS = "http://9router.test/v1", "r9-key", ["auto"]
cfg.OPENROUTER_API_KEY, cfg.OPENROUTER_MODELS = "sk-or-1", ["a/b:free", "c/d:free"]


class Store:
    def __init__(self):
        self.d = {}

    def get(self, k):
        return self.d.get(k)

    def set(self, k, v):
        self.d[k] = v

    def recent_lessons(self, n):
        return []


class Resp:
    def __init__(self, status, body):
        self.status_code, self.body, self.text = status, body, json.dumps(body)

    def json(self):
        return self.body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)


http: dict = {}
posted: list = []


def post(url, headers=None, json=None, timeout=None):  # noqa: A002 - requests' own name
    model = json["model"]
    posted.append(model)
    got = http.get(model, ("ok", model))
    if isinstance(got, Exception):
        raise got
    if got[0] == "ok":
        return Resp(200, {"model": got[1], "choices": [{"message": {"content": '{"decision": "SKIP", "confidence": 40}'}}]})
    return Resp(got[0], {"error": {"message": got[1]}})


alt_ai.requests.post = post
f9 = AIFilter = alt_ai.AIFilter(Store())
res = f9._ask("s", "u", "decision")
t.ok("altcoin: 9Router auto answers; the served model is kept (result, stats, last)", res["served"] == "auto"
     and posted == ["auto"] and f9.last_served == "auto")
http["auto"] = ("ok", "gpt-5-mini")
res = f9._ask("s", "u", "decision")
row = next(r for r in f9.public_stats()["models"] if r["model"] == "9router/auto")
t.ok("...'9router/auto → gpt-5-mini' when 9Router picks another", res["served"] == "gpt-5-mini"
     and row["last_served"] == "gpt-5-mini" and row["served"] == {"auto": 1, "gpt-5-mini": 1}, row)
mine = json.loads((SHARE / "altcoin.json").read_text(encoding="utf-8"))
t.ok("altcoin: its file has its requests per provider (9Router under the stock's name), its version and AI state",
     mine["providers"]["router9"]["calls"] == 2 and mine["providers"]["router9"]["key"] == fp9
     and mine["extra"]["ai"]["last_served"] == "gpt-5-mini" and mine["extra"]["ai"]["calls_today"] == 2
     and mine["extra"]["app"]["name"] == "Altcoin" and mine["extra"]["app"]["version"], mine)
t.ok("...the stock sees them on the same key", fc.provider_status()[0]["peer_calls"] == 2
     and fc.peer_status()[0]["who"] == "altcoin")

posted.clear()
fc._rest("router9", 300, "rate limited (429)", share=True)
res = f9._ask("s", "u", "decision")
st = f9.public_stats()
t.ok("altcoin: the stock rests 9Router → straight to OpenRouter, no failure recorded on 9Router",
     posted == ["a/b:free"] and res["model"] == "openrouter/a/b:free"
     and next(r for r in st["models"] if r["model"] == "9router/auto")["fail"] == 0, (posted, res))
fc.reset_provider_state()
for f in SHARE.glob("stock.json"):
    f.unlink()

stock_l = ai_share.Ledger("stock", SHARE)
stock_l.register("openrouter", fp, 45)
for _ in range(45):
    stock_l.count("openrouter", fp)
posted.clear()
http["auto"] = (429, "busy")
res = f9._ask("s", "u", "decision")
t.ok("altcoin: the stock's 45 OpenRouter requests today → the altcoin sends none, says why",
     res is None and posted == ["auto"] and "/45 lượt" in (f9.last_error or "") and "stock" in f9.last_error,
     (posted, f9.last_error))
t.ok("...and its 9Router 429 is shared: the stock rests 9Router too", fc.is_resting("router9"))
(SHARE / "stock.json").unlink()
fc.reset_provider_state()

posted.clear()
f9.cooldown.clear()
f9.fail_streak.clear()
for f in SHARE.glob("altcoin.json"):
    f.unlink()
f9.share = alt_ai.Ledger("altcoin")
http.update({"auto": requests.ConnectionError("refused"), "a/b:free": (429, "a/b:free is temporarily rate-limited upstream")})
res = f9._ask("s", "u", "decision")
mine = json.loads((SHARE / "altcoin.json").read_text(encoding="utf-8"))
t.ok("altcoin: 9Router unreachable → its request given back, rest shared; OpenRouter busy upstream → not shared",
     posted == ["auto", "a/b:free", "c/d:free"] and res["model"] == "openrouter/c/d:free"
     and mine["providers"]["router9"]["calls"] == 0 and fc.is_resting("router9") and not fc.is_resting("openrouter"),
     (posted, mine["providers"]))
http["c/d:free"] = (429, "Rate limit exceeded: free-models-per-day")
f9.cooldown.clear()
f9._ask("s", "u", "decision")
t.ok("altcoin: OpenRouter's day spent (429, not upstream) → the stock rests OpenRouter too", fc.is_resting("openrouter"))

stock_l = ai_share.Ledger("stock", SHARE)
stock_l.register("openrouter", fp, 45)
stock_l.register("router9", fp9, 0)
text = f9.public_stats()["shared"]
t.ok("altcoin public_stats: the providers shared with the stock, the calls of both, the shared cap",
     {x["provider"] for x in text} == {"9router", "openrouter"}
     and next(x for x in text if x["provider"] == "openrouter")["cap"] == 45, text)

from bot import main as alt_main  # noqa: E402


class _Bot:
    ai = f9


lines = alt_main.Bot.ai_text(_Bot())
t.ok("/alt ai: the sharing, and '9router/auto → gpt-5-mini'", "dùng chung khoá với stock" in lines
     and "9router/auto → gpt-5-mini" in lines, lines)

t.done()
