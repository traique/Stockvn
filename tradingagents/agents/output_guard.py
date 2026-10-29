"""Output Guard: the code-level check that the LLM agents kept the system's decision.

The prompts tell every agent the action and the numbers are fixed; this node
does not trust them to (port of lananh-main ``_enforce_policy_contract``). It
reads the texts that carry the decision - the Research Manager's assessment,
the Trader's plan and the Portfolio Manager's final decision - and:

- an action label or advice that contradicts the system action ("MUA" when the
  system says NO_TRADE, "nên bán" when it says BUY) drops that text and puts
  the system's own decision summary in its place, with a note;
- a price that appears in none of the data the agents were given puts a
  warning and the system summary ahead of the text, which is kept;
- so does a fair value ("giá trị hợp lý", "định giá", Base/Bear/Bull/eab
  followed by a price: ``rfmt.fair_value_numbers``) that is not, within 0,5%,
  a price of the valuation card (``vn_valuation``) - or the session price; one
  within 0,5% passes both checks (a rounded Base);
- the final decision always ends with exactly one standard disclaimer, and
  ``final_rating`` is always the system action.

The Fundamentals Analyst's report is a price source for every later agent; it
gets the fair-value check alone (its other numbers come from its tools, which
no state field keeps), and a fair value it invented is no longer a known price.
The bull, bear and risk debaters argue sides by design and are not checked.
"""

from __future__ import annotations

import logging

from tradingagents.agents import vn_context
from tradingagents.dataflows import vn_valuation
from tradingagents.quant import engine, report_format as rfmt

logger = logging.getLogger(__name__)

CONFLICT_NOTE = (
    "⚠️ Phần diễn giải của AI nêu hành động/kế hoạch khác với hệ thống định lượng nên đã bị "
    "lược bỏ - chỉ dùng bản quyết định của hệ thống dưới đây."
)
UNKNOWN_PRICE_NOTE = (
    "⚠️ Phần diễn giải dưới đây có mốc giá không nằm trong số liệu hệ thống - chỉ dùng các mốc "
    "trong bản quyết định của hệ thống."
)

# The decision lines the renderers write themselves from the engine's output
# (schemas.render_*): their action and numbers are the system's, not the model's,
# so they are not checked. Only a line that is EXACTLY one of them is exempt: a
# model (or quoted news) line that merely starts with "**Recommendation**:" is
# narrative and is checked like any other.
_SYSTEM_LINE_PREFIXES = (
    "**Rating**:", "**Recommendation**:", "**Action**:", "FINAL TRANSACTION PROPOSAL:",
    "**Entry Zone**:", "**Stop Loss**:", "**Target 1**:", "**Target 2**:",
    "**Risk/Reward**:", "**Position Sizing**:", "**Price Target**:",
    "**Invalidation Level**:", "**Support Level**:",
)


def _system_lines(decision: dict) -> frozenset[str]:
    """The exact lines the renderers write for this decision."""
    from tradingagents.agents.schemas import _vnd, plan_lines

    action = decision.get("action") or "NO_TRADE"
    lines = {
        f"**Rating**: {action}",
        f"**Recommendation**: {action} (hành động của hệ thống định lượng)",
        f"**Action**: {action} (hành động của hệ thống định lượng)",
        f"FINAL TRANSACTION PROPOSAL: **{action}**",
        f"**Price Target**: {_vnd(decision.get('target_price'))}",
    }
    try:
        lines.update(plan_lines(decision))
    except Exception:  # noqa: BLE001 - a partial decision still gets the header lines
        pass
    return frozenset(lines)


def _is_system_line(line: str, system_lines: frozenset[str]) -> bool:
    return line.strip() in system_lines


def _narrative(text: str, system_lines: frozenset[str]) -> str:
    return "\n".join(line for line in (text or "").splitlines() if not _is_system_line(line, system_lines))


def _allowed_text(state) -> str:
    """Everything the agents were given or produced from data: where a price may come from."""
    decision = vn_context.quant_decision(state)
    parts = [
        state.get("quant_report") or "",
        decision.get("deterministic_summary") or "",
        engine.render_summary_from_state(decision),
        state.get("market_report") or "",
        state.get("fundamentals_report") or "",
        state.get("news_report") or "",
        state.get("sentiment_report") or "",
        state.get("portfolio_context") or "",
    ]
    return "\n".join(parts)


FAIR_VALUE_TOLERANCE = 0.005
_PLAN_KEYS = ("entry_low", "entry_high", "stop_price", "target_price", "target2")


def _card_prices(state) -> set[int]:
    """The valuation card's prices: the card in the engine's report, and the one the run's fundamentals
    tool read (the same ``vn_valuation.card`` cache; built there when the engine's node could not)."""
    known = vn_valuation.card_prices(state.get("quant_report") or "")
    symbol, day = state.get("company_of_interest"), state.get("trade_date")
    try:
        built = vn_valuation.cached_card(symbol, day) if symbol and day else None
    except ValueError:        # a run date that is not YYYY-MM-DD: the report's card alone
        built = None
    return known | (vn_valuation.card_prices(vn_valuation.render_card(built)) if built else set())


def _fair_values(text: str, state) -> tuple[list[int], set[int]]:
    """(fair values in ``text`` that are not, within 0,5%, a card price or the session price - quoted
    as often as a fair value after "định giá" -, the ones that are). The engine's plan levels after
    Base/Bull/Bear are its own scenarios, not fair values."""
    decision = vn_context.quant_decision(state)
    known = _card_prices(state)
    if decision.get("price"):
        known.add(round(float(decision["price"])))
    plan = {round(float(decision[k])) for k in _PLAN_KEYS if decision.get(k)}
    invented, matched = [], set()
    for v in rfmt.fair_value_numbers(text, plan):
        if any(abs(v - k) <= FAIR_VALUE_TOLERANCE * k for k in known):
            matched.add(v)
        else:
            invented.append(v)
    return invented, matched


def _allowed_prices(state) -> set[int]:
    """Prices of ``_allowed_text``, less the fair values the fundamentals report invented."""
    fundamentals = state.get("fundamentals_report") or ""
    others = rfmt.price_numbers(_allowed_text({**state, "fundamentals_report": ""}))
    return others | (rfmt.price_numbers(fundamentals) - set(_fair_values(fundamentals, state)[0]))


def _price_note(label: str, unknown: list[int], invented: list[int]) -> str:
    parts = []
    if unknown:
        parts.append(f"có mốc giá ngoài dữ liệu ({', '.join(rfmt.fmt_price(v) for v in unknown[:5])})")
    if invented:
        parts.append(f"giá trị hợp lý không có trên thẻ định giá ({', '.join(rfmt.fmt_price(v) for v in invented[:5])})")
    return f"{label}: " + "; ".join(parts)


def guard_text(text: str, state, *, label: str) -> tuple[str, str | None]:
    """The text to keep for one agent output, and a note when it was changed."""
    if not text:
        return text, None
    decision = vn_context.quant_decision(state)
    action = vn_context.action_of(state)
    summary = decision.get("deterministic_summary") or engine.render_summary_from_state(decision)
    allowed = engine.COMPATIBLE_ACTIONS.get(action, frozenset({action}))
    system_lines = _system_lines(decision)
    narrative = _narrative(text, system_lines)
    conflicts = rfmt.find_action_conflicts(narrative, allowed)
    if conflicts:
        logger.warning("%s contradicted the system action %s: %s", label, action, conflicts[:5])
        head = next((line for line in text.splitlines() if _is_system_line(line, system_lines)), "")
        kept = f"{head}\n\n" if head else ""
        return (
            f"{kept}{CONFLICT_NOTE}\n\n{summary}",
            f"{label}: bỏ phần diễn giải vì mâu thuẫn với action {action} ({', '.join(conflicts[:3])})",
        )
    allowed_prices = _allowed_prices(state)
    invented, matched = _fair_values(narrative, state)
    unknown = sorted(v for v in rfmt.price_numbers(narrative)
                     if v not in allowed_prices and v not in invented and v not in matched)
    if unknown or invented:
        logger.warning("%s cited prices outside the data: %s; fair values off the card: %s", label, unknown[:5], invented[:5])
        return f"{UNKNOWN_PRICE_NOTE}\n\n{summary}\n\n{text}", _price_note(label, unknown, invented)
    return text, None


def guard_fundamentals(state) -> tuple[str, str | None]:
    """The fundamentals report with the unknown-price warning ahead when it gives a fair value
    the valuation card does not have; its other numbers are left as they are."""
    text = state.get("fundamentals_report") or ""
    invented = _fair_values(text, state)[0]
    if not invented:
        return text, None
    label = "Fundamentals Analyst"
    logger.warning("%s gave fair values off the card: %s", label, invented[:5])
    decision = vn_context.quant_decision(state)
    summary = decision.get("deterministic_summary") or engine.render_summary_from_state(decision)
    return f"{UNKNOWN_PRICE_NOTE}\n\n{summary}\n\n{text}", _price_note(label, [], invented)


def guard_early(node, key: str, label: str):
    """``node`` with its ``key`` output checked at once, before later agents read it.

    The Research Manager's plan feeds the Trader, the risk team and the
    Portfolio Manager; the Trader's plan feeds the last two. Checked only at
    the end, a contradiction there would already have shaped every later prompt.
    """
    def guarded_node(state) -> dict:
        out = node(state)
        text = (out or {}).get(key)
        if not text:
            return out
        guarded, note = guard_text(text, {**state, **out}, label=label)
        if note is None:
            return out
        logger.info("Output Guard (early) changed %s", label)
        notes = [n for n in (state.get("guard_notes") or "").splitlines() if n]
        return {**out, key: guarded, "guard_notes": "\n".join([*notes, note])}

    return guarded_node


def create_output_guard():
    def output_guard_node(state) -> dict:
        # Notes from the early checks (after the Research Manager and the Trader) are kept.
        notes: list[str] = [n for n in (state.get("guard_notes") or "").splitlines() if n]
        updates: dict = {}
        fundamentals, note = guard_fundamentals(state)
        if note:
            notes.append(note)
            updates["fundamentals_report"] = fundamentals
        for key, label in (
            ("investment_plan", "Research Manager"),
            ("trader_investment_plan", "Trader"),
            ("final_trade_decision", "Portfolio Manager"),
        ):
            if any(n.startswith(f"{label}:") for n in notes):
                continue      # already checked and rewritten by guard_early: a second pass would repeat its note
            guarded, note = guard_text(state.get(key) or "", state, label=label)
            if note:
                notes.append(note)
                updates[key] = guarded
        final = updates.get("final_trade_decision", state.get("final_trade_decision") or "")
        updates["final_trade_decision"] = rfmt.ensure_disclaimer(rfmt.clean_analysis_output(final))
        updates["final_rating"] = vn_context.action_of(state)
        updates["guard_notes"] = "\n".join(notes)
        return updates

    return output_guard_node
