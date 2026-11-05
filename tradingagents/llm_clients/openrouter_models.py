"""OpenRouter's free models, found as the altcoin bot finds them (altcoin/bot/ai.py
``_discover_openrouter``: the same filter and ranking, kept equal by tests/suites/suite_ai_share.py),
so the two bots on "auto" ask the same model first.

- ``models(spec, base_url)``: the models to try, in order, for OPENROUTER_MODEL - model ids
  separated by commas, where "auto" stands for the free models OpenRouter lists now (the
  default is "auto" alone), minus the ones cooling down;
- the free list comes from GET <base>/models (public: no request of the day spent), kept
  ``REFRESH`` seconds in memory and in OPENROUTER_MODELS_CACHE (a JSON file) when that is set;
  OpenRouter unreachable: the last list, else ``DEFAULT_FREE``;
- ``cool(model, seconds, why)``: a model that is gone (404, not a valid id, refused) or busy
  upstream is left out that long, the others still answer.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# The altcoin's ranking (altcoin/bot/ai.py _OR_BLOCK, _OR_PREFER, OPENROUTER_MAX_MODELS): free models that do
# not fit an analysis (safety, audio, too small), then big models that answer JSON well, slow reasoners last.
BLOCK = ("guard", "safety", "lyria", "audio", "tts", "whisper", "embed", "-1b", "-2b", "-3b", "e2b", "e4b", "vl-",
         "inkling")
PREFER = ("gemma-4-31b", "nemotron-3-super", "deepseek", "qwen3", "minimax", "llama-4", "gemma-4-26b",
          "glm", "kimi", "gpt-oss", "mistral", "laguna", "nemotron-3-ultra", "nemotron")
MAX_MODELS = 8
MIN_CONTEXT = 8000
REFRESH = 12 * 3600
DEFAULT_FREE = ("nvidia/nemotron-3-super-120b-a12b:free", "openrouter/free")
CACHE_V = 1

_lock = threading.Lock()
_cache: dict = {"ts": 0.0, "models": [], "retry": 0.0}     # retry: no fetch before (it failed)
_cooling: dict[str, tuple[float, str]] = {}      # model -> (until, why)


def rank_free(data: list) -> list[str]:
    """The ':free' text models of a GET /models answer ("data"), ranked; "openrouter/free" (the
    router that picks one itself) last, as a safety net."""
    found = []
    for m in data or []:
        mid = str((m or {}).get("id", "")) if isinstance(m, dict) else ""
        if not (mid.endswith(":free") or mid == "openrouter/free"):
            continue
        if any(b in mid.lower() for b in BLOCK):
            continue
        outs = (m.get("architecture") or {}).get("output_modalities") or ["text"]
        if "text" not in outs:
            continue
        try:
            ctx = int(m.get("context_length") or 0)
        except (TypeError, ValueError):
            ctx = 0
        if ctx and ctx < MIN_CONTEXT:
            continue
        found.append(mid)

    def rank(mid: str) -> int:
        for i, pref in enumerate(PREFER):
            if pref in mid:
                return i
        return len(PREFER) + (100 if mid == "openrouter/free" else 0)

    found.sort(key=rank)
    router = "openrouter/free" in found
    found = [m for m in found if m != "openrouter/free"][:MAX_MODELS]
    return found + (["openrouter/free"] if router else [])


def _fetch(base_url: str) -> list:
    import httpx

    r = httpx.get(f"{base_url.rstrip('/')}/models", timeout=15)
    r.raise_for_status()
    return r.json().get("data") or []


FETCH = _fetch          # the tests' stand-in


def _cache_path() -> Path | None:
    where = (os.environ.get("OPENROUTER_MODELS_CACHE") or "").strip()
    return Path(where) if where else None


def free_models(base_url: str, force: bool = False) -> list[str]:
    """The free models now (memory, then the cache file, then OpenRouter every REFRESH s)."""
    with _lock:
        if not _cache["models"] and _cache_path():
            try:
                saved = json.loads(_cache_path().read_text(encoding="utf-8"))
                if saved.get("v") == CACHE_V and saved.get("models"):
                    _cache.update(ts=float(saved.get("ts") or 0), models=[str(m) for m in saved["models"]])
            except (OSError, ValueError, AttributeError):
                pass
        if not force and _cache["models"] and time.time() - _cache["ts"] < REFRESH:
            return list(_cache["models"])
        if not force and time.time() < _cache["retry"]:          # OpenRouter unreachable a moment ago
            return list(_cache["models"] or DEFAULT_FREE)
    try:
        found = rank_free(FETCH(base_url))
    except Exception as exc:  # noqa: BLE001 - the last list keeps working
        logger.warning("OpenRouter: không lấy được danh sách model free (%s)", exc)
        found = []
    with _lock:
        if found:
            _cache.update(ts=time.time(), models=found)
            logger.info("OpenRouter: %d model free: %s", len(found), ", ".join(found[:6]))
            path = _cache_path()
            if path:
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    tmp = path.with_name(path.name + ".tmp")
                    tmp.write_text(json.dumps({"v": CACHE_V, "ts": _cache["ts"], "models": found}), encoding="utf-8")
                    os.replace(tmp, path)
                except OSError:
                    pass
        else:
            _cache["retry"] = time.time() + 1800           # unreachable: try again in 30 minutes, not on every call
        return list(_cache["models"] or DEFAULT_FREE)


def models(spec: str, base_url: str) -> list[str]:
    """The models of ``spec`` ("auto", "a/b:free", "a/b:free,auto"...) to try, in order."""
    names = [m.strip() for m in (spec or "auto").split(",") if m.strip()] or ["auto"]
    out: list[str] = []
    for name in names:
        out += free_models(base_url) if name.lower() == "auto" else [name]
    now = time.time()
    with _lock:
        return [m for m in dict.fromkeys(out) if _cooling.get(m, (0.0, ""))[0] <= now]


def cool(model: str, seconds: float, why: str) -> None:
    if model and seconds > 0:
        with _lock:
            _cooling[model] = (time.time() + seconds, str(why)[:120])
        logger.warning("OpenRouter: bỏ qua model %s trong %.0f giây (%s)", model, seconds, why)


def cooling() -> dict[str, tuple[int, str]]:
    """{model: (seconds left, why)} of the models left out now."""
    now = time.time()
    with _lock:
        return {m: (int(until - now), why) for m, (until, why) in _cooling.items() if until > now}


def reset() -> None:
    with _lock:
        _cache.update(ts=0.0, models=[], retry=0.0)
        _cooling.clear()
