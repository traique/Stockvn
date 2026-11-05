"""The altcoin's AI and version on the stock's pages and commands (offline): /ai (the providers shared
on the same key, the altcoin's card, who answered each critic verdict), /phienban, Telegram/Zoom
/trangthai and /phienban - all read from the file the altcoin writes in AI_SHARE_DIR."""
import json
import os
from types import SimpleNamespace

import _common  # noqa: F401 - clean env, no .env, offline, temp folder, repo on sys.path

os.environ.update(ALTCOIN_ENABLED="1", ROUTER9_BASE_URL="http://9router.test/v1", ROUTER9_API_KEY="r9-key",
                  OPENROUTER_API_KEY="sk-or-1", PROVIDER_ORDER="router9,openrouter")

from _web_harness import *  # noqa: E402,F401,F403
from fastapi.testclient import TestClient  # noqa: E402

from tradingagents.llm_clients import ai_share  # noqa: E402
from tradingagents.llm_clients import fallback_client as fc  # noqa: E402
from vnbot import altcoin_ai  # noqa: E402

t = _common.Checks()
share = SETTINGS.data_dir / "ai_share"
t.ok("settings: AI_SHARE_DIR and OPENROUTER_MODELS_CACHE under DATA_DIR", os.environ["AI_SHARE_DIR"] == str(share)
     and os.environ["OPENROUTER_MODELS_CACHE"] == str(SETTINGS.data_dir / "openrouter_models.json"))
t.ok("no altcoin file yet: nothing shown", altcoin_ai.info() is None)

alt = ai_share.Ledger("altcoin", share)
fp_or = ai_share.fingerprint("https://openrouter.ai/api/v1", "sk-or-1")
alt.register("openrouter", fp_or)
for _ in range(3):
    alt.count("openrouter", fp_or)
alt.set_extra(app={"name": "Altcoin", "version": "3.3.1", "build": "abc1234", "release": True},
              ai={"enabled": True, "calls_today": 7, "budget": 80, "last_model": "9router/auto",
                  "last_served": "gpt-5-mini", "last_error": None, "ts": 0})
info = altcoin_ai.info()
t.ok("altcoin_ai.info: version and AI from the altcoin's file", info["app"]["version"] == "3.3.1"
     and info["ai"]["calls_today"] == 7, info)
t.ok("version_line / ai_lines", altcoin_ai.version_line(info) == "Altcoin 3.3.1 · bản dựng abc1234 · ✅ đúng bản phát hành"
     and "7/80" in altcoin_ai.ai_lines(info)[0] and "9router/auto → gpt-5-mini" in altcoin_ai.ai_lines(info)[0],
     altcoin_ai.ai_lines(info))
t.ok("model_text: the arrow only when another model answered", altcoin_ai.model_text("auto", "gpt-5-mini") ==
     "auto → gpt-5-mini" and altcoin_ai.model_text("openrouter/a/b:free", "a/b:free") == "openrouter/a/b:free"
     and altcoin_ai.model_text("auto", None) == "auto")

(SETTINGS.data_dir / "critic_log.json").write_text(json.dumps([
    {"day": "2026-11-04", "code": "FPT", "action": "BUY", "price": 100, "stop": 95, "target": 110, "verdict": "AGREE",
     "confidence": 70, "reason": "ổn", "outcome": None, "model": "9Router auto → gpt-5-mini"}]), encoding="utf-8")
app, zoom, handled, jobs, wl, sw = build(trader=True)
c = TestClient(app, raise_server_exceptions=False)
AUTH = ("x", "s3cret-pass")
page = c.get("/ai", auth=AUTH).text
t.ok("/ai: OpenRouter shared with the altcoin: its 3 requests next to the stock's, against the shared cap",
     "+ 3 Altcoin" in page and "dùng chung khoá với Altcoin" in page, page[page.find("Hạn mức AI"):][:1500])
t.ok("/ai: the altcoin's card (version, its own budget, the model that answered)", "Altcoin · AI" in page
     and "7</b> / 80" in page and "9router/auto → gpt-5-mini" in page and 'href="/altcoin/ai"' in page)
t.ok("/ai: who answered each critic verdict", "9Router auto → gpt-5-mini" in page)
alt.rest("openrouter", fp_or, 300, "rate limited: free-models-per-day")
page = c.get("/ai", auth=AUTH).text
t.ok("/ai: a rest the altcoin put on the shared provider is shown", "nghỉ theo Altcoin" in page)
page = c.get("/phienban", auth=AUTH).text
t.ok("/phienban: the altcoin's version and build", "3.3.1" in page and "abc1234" in page and "/altcoin/phienban" in page)

from vnbot.telegram_bot import TelegramBot  # noqa: E402

text = TelegramBot._status(SimpleNamespace(jobs=SimpleNamespace(recent=lambda n: [])))
t.ok("/trangthai: the shared provider (+ Altcoin), the rest the altcoin put, the altcoin's AI line",
     "0 + 3 Altcoin/45" in text and "nghỉ theo Altcoin" in text and "🪙 Altcoin: AI hôm nay 7/80" in text, text)

os.environ["ALTCOIN_ENABLED"] = "0"
t.ok("ALTCOIN_ENABLED not 1: the altcoin is not shown", altcoin_ai.info() is None
     and "Altcoin · AI" not in c.get("/ai", auth=AUTH).text)
fc.reset_provider_state()
t.done()
