"""Wyckoff/VSA readings of a stock's daily bars - causal: the reading of session ``i``
reads the bars up to ``i`` only (the AmiBroker script this comes from picks its range,
its springs and its channel with later bars, so its past labels move as new bars come).

- The trading range of session ``i`` is the ``lookback`` sessions before it: support =
  their lowest low, resistance = their highest high.
- ``position_pct``: where the close sits in that range (0 = support, 100 = resistance;
  under 0 = below support, over 100 = above resistance).
- ``volume_ratio`` / ``spread_ratio``: the session's volume and high-low spread against
  their averages over the 20 sessions before.

Events, as in the Wyckoff/VSA literature (thresholds set in advance, not fitted):

- **Spring**: a range (width up to ``max_width_pct``) whose support is pierced in the
  session (low under it) and the close back on or above it.
- **No supply** (test): a down close on low volume (<= 0.75 x) and a narrow spread
  (<= 1 x) in the lower 30% of a range.
- **SOW** (sign of weakness): a down bar (close under open) closing under the support on
  high volume (>= 1.25 x).

Research code, not part of the bot: none of the three ideas beat what the bot already does
(README.md in this folder), so nothing here is used by its decisions or alerts.
"""

from __future__ import annotations

from dataclasses import dataclass

LOOKBACK = 60
VOLUME_SESSIONS = 20
MAX_WIDTH_PCT = 40.0
LOW_VOLUME, HIGH_VOLUME = 0.75, 1.25
NO_SUPPLY_MAX_POSITION = 30.0


@dataclass(frozen=True)
class Reading:
    support: float
    resistance: float
    width_pct: float
    position_pct: float
    volume_ratio: float | None
    spread_ratio: float | None
    spring: bool
    no_supply: bool
    sow: bool


def read(opens, highs, lows, closes, volumes, i: int, *, lookback: int = LOOKBACK,
         max_width_pct: float = MAX_WIDTH_PCT) -> Reading | None:
    """The reading of session ``i`` (indexes into the five lists, oldest first); None
    without ``lookback`` sessions before it or with a bad price."""
    if i < max(lookback, VOLUME_SESSIONS) or i >= len(closes):
        return None
    support = min(lows[i - lookback:i])
    resistance = max(highs[i - lookback:i])
    if not support or support <= 0 or resistance <= support:
        return None
    o, h, low, c, v = opens[i], highs[i], lows[i], closes[i], volumes[i]
    width = (resistance / support - 1) * 100
    position = (c - support) / (resistance - support) * 100
    vols = [x for x in volumes[i - VOLUME_SESSIONS:i] if x and x > 0]
    vma = sum(vols) / len(vols) if vols else 0.0
    volume_ratio = v / vma if vma > 0 and v is not None else None
    spreads = [hh - ll for hh, ll in zip(highs[i - VOLUME_SESSIONS:i], lows[i - VOLUME_SESSIONS:i], strict=True)]
    sma = sum(spreads) / len(spreads) if spreads else 0.0
    spread_ratio = (h - low) / sma if sma > 0 else None
    in_range = width <= max_width_pct
    spring = in_range and low < support <= c
    no_supply = (in_range and c < closes[i - 1] and volume_ratio is not None and volume_ratio <= LOW_VOLUME
                 and spread_ratio is not None and spread_ratio <= 1.0 and 0 <= position <= NO_SUPPLY_MAX_POSITION)
    sow = c < support and c < o and volume_ratio is not None and volume_ratio >= HIGH_VOLUME
    return Reading(support, resistance, round(width, 3), round(position, 3), volume_ratio, spread_ratio,
                   spring, no_supply, sow)


def series(opens, highs, lows, closes, volumes, *, lookback: int = LOOKBACK,
           max_width_pct: float = MAX_WIDTH_PCT) -> list[Reading | None]:
    """The reading of every session (None before there are enough bars)."""
    return [read(opens, highs, lows, closes, volumes, i, lookback=lookback, max_width_pct=max_width_pct)
            for i in range(len(closes))]
