"""Assumptions of the valuation card (``vn_valuation``), set by hand per valuation profile.

They are judgement, not measurements: the card prints them next to every number they move, and a
sensitivity line for Ke. Nothing here is a Quant Engine parameter (``quant.params``), nothing is
calibrated, and nothing of the engine, policy or simulator reads this module.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, replace

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Assumptions:
    ke: float        # cost of equity, a year
    lam: float       # λ: the share of the excess return (ROE − Ke) lost each year
    g: float         # long-run growth of the book
    margin: float    # safety margin: eab = Base × (1 − margin)


# Banks: Ke 12% = the 10-year government bond yield (about 3-4% in 2025-2026) plus an equity risk
# premium of about 8-9% for Vietnam, the range VN brokers commonly use for banks (12-14%). Not checked
# live; the operator overrides it with VALUATION_KE_BANK (in %). The CTG profile PDF used 7,94%, which
# moves its Base about 8,5% higher (CTG 28/10/2026: 29.984 against 27.643). λ 0,4, g 0 and the 13,7%
# margin are the PDF's.
PROFILES = {"banking": Assumptions(ke=0.12, lam=0.4, g=0.0, margin=0.137)}
ENV_KE = {"banking": "VALUATION_KE_BANK"}
SENSITIVITY_STEP = 0.02     # the card's sensitivity line: Base at Ke − 2, Ke and Ke + 2 points
KE_BOUNDS = (0.04, 0.30)


def sensitivity_ke(ke: float) -> tuple[float, ...]:
    """The Ke values of the sensitivity line, around the Ke in use."""
    return tuple(round(ke + step, 6) for step in (-SENSITIVITY_STEP, 0.0, SENSITIVITY_STEP))


def assumptions(profile_key: str) -> Assumptions | None:
    """The profile's assumptions, Ke overridden by its environment variable (in %, e.g. 13 or 12,5)."""
    base = PROFILES.get(profile_key)
    env = ENV_KE.get(profile_key)
    raw = (os.environ.get(env) or "").strip().rstrip("%").replace(",", ".") if env else ""
    if base is None or not raw:
        return base
    try:
        ke = float(raw) / 100
    except ValueError:
        ke = None
    if ke is None or not KE_BOUNDS[0] <= ke <= KE_BOUNDS[1]:
        logger.warning("%s=%r ignored: Ke in %% from %g to %g", env, raw, KE_BOUNDS[0] * 100, KE_BOUNDS[1] * 100)
        return base
    return replace(base, ke=ke)
