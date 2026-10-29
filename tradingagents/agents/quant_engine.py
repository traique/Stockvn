"""Graph node running the quantitative engine beside the analysts.

The engine decides the action and every number of the plan before any LLM
agent reasons about it (see ``tradingagents.quant.engine``). A failure here
never ends the run: the decision falls back to NO_TRADE with no plan, and the
agents are told the engine could not run.

A bank's valuation card (``vn_valuation``) follows the engine's report: the
Output Guard reads its prices as known ones. It is text only, computed here and
not in ``engine.analyze``, which runs for every stock of a scan.
"""

from __future__ import annotations

import logging

from tradingagents.portfolio import PortfolioContext
from tradingagents.quant import engine

logger = logging.getLogger(__name__)


def _portfolio(state) -> PortfolioContext | None:
    raw = state.get("portfolio")
    if not raw:
        return None
    try:
        return PortfolioContext.model_validate(raw)
    except Exception as exc:  # noqa: BLE001 - a bad book must not sink the decision
        logger.warning("Ignoring unreadable portfolio in state: %s", exc)
        return None


def _valuation_card(symbol: str, trade_date: str) -> str:
    """The valuation card of a bank for the report; "" for another stock or when it cannot be built."""
    from tradingagents.dataflows import vn_valuation

    try:
        return vn_valuation.card_text(symbol, trade_date)
    except Exception as exc:  # noqa: BLE001 - the card is enrichment, never a reason to fail
        logger.warning("Valuation card of %s on %s unavailable: %s", symbol, trade_date, exc)
        return ""


def create_quant_engine():
    def quant_engine_node(state) -> dict:
        symbol = state["company_of_interest"]
        trade_date = state["trade_date"]
        try:
            result = engine.analyze(symbol, trade_date, portfolio=_portfolio(state))
        except Exception as exc:  # noqa: BLE001 - degrade to NO_TRADE, never crash the run
            logger.warning("Quant engine failed for %s on %s: %s", symbol, trade_date, exc)
            return {
                "quant_decision": {
                    "symbol": symbol,
                    "as_of_date": trade_date,
                    "action": "NO_TRADE",
                    "reasons": [f"hệ thống định lượng không chạy được: {exc}"],
                },
                "quant_report": "",
            }
        card = _valuation_card(symbol, trade_date)
        return {"quant_decision": result.to_state(), "quant_report": result.report + (f"\n\n{card}" if card else "")}

    return quant_engine_node
