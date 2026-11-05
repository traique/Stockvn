"""The altcoin bot's AI and version as the stock shows them (/ai, /trangthai, /phienban): read from the
file the altcoin writes in AI_SHARE_DIR (altcoin/bot/ai.py through ai_share.py), so nothing is asked
of the child process. Only while ALTCOIN_ENABLED=1; None before the altcoin's first start."""

from __future__ import annotations

import os
import time


def info() -> dict | None:
    """{"ts", "app": {name, version, build, release}, "ai": {enabled, calls_today, budget, last_model,
    last_served, last_error, ts}} of the altcoin, or None."""
    if (os.environ.get("ALTCOIN_ENABLED") or "").strip() != "1":
        return None
    from tradingagents.llm_clients.fallback_client import peer_status

    for peer in peer_status():
        if peer.get("who") == "altcoin":
            extra = peer.get("extra") or {}
            return {"ts": peer.get("ts") or 0, "app": extra.get("app") or {}, "ai": extra.get("ai") or {}}
    return None


def model_text(asked: str | None, served: str | None) -> str:
    """"9router/auto → gpt-5-mini" when the provider picked another model than the one asked."""
    asked = asked or ""
    tail = asked.split("/", 1)[-1]
    if served and served != tail and served != asked:
        return f"{asked} → {served}" if asked else served
    return asked or (served or "")


def ago(ts: float) -> str:
    seconds = max(0, int(time.time() - float(ts or 0)))
    if seconds < 90:
        return "vừa xong"
    if seconds < 5400:
        return f"{seconds // 60} phút trước"
    if seconds < 2 * 86400:
        return f"{seconds // 3600} giờ trước"
    return f"{seconds // 86400} ngày trước"


def version_line(data: dict | None) -> str:
    """"Altcoin 3.3.1 · bản dựng 7fe2138 · ✅ đúng bản phát hành", or "" when unknown."""
    app = (data or {}).get("app") or {}
    if not app.get("version"):
        return ""
    check = "✅ đúng bản phát hành" if app.get("release") else "⚠️ khác bản phát hành"
    return f"{app.get('name') or 'Altcoin'} {app['version']} · bản dựng {app.get('build') or '?'} · {check}"


def ai_lines(data: dict | None) -> list[str]:
    """The altcoin's AI in a few lines for Telegram/Zoom, [] when unknown."""
    ai = (data or {}).get("ai") or {}
    if not ai:
        return []
    if not ai.get("enabled"):
        return ["🪙 Altcoin: AI tắt hoặc chưa có nhà cung cấp"]
    lines = [f"🪙 Altcoin: AI hôm nay {ai.get('calls_today', 0)}/{ai.get('budget', '?')} lượt (hạn mức riêng của Altcoin)"
             + (f", model cuối {model_text(ai.get('last_model'), ai.get('last_served'))}" if ai.get("last_model") else "")
             + f" · cập nhật {ago(ai.get('ts') or data.get('ts'))}"]
    if ai.get("last_error"):
        lines.append(f"   Lỗi gần nhất: {str(ai['last_error'])[:160]}")
    return lines
