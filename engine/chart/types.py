"""Shared types and primitives for the chart-reading engine.

Cross-module enums, bar-geometry helpers, and the confluence-scoring primitives used by
``engine/chart/read.py``. Detector-specific result dataclasses live in their own modules
(``fvg.py``, ``order_block.py`, ...); only genuinely shared contracts belong here.

See docs/CHART_READING.md "진입 타이밍 컨플루언스 프레임워크" for the scoring model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from data.models import PriceBar


class TrendBias(Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    RANGING = "RANGING"


class EntryState(Enum):
    ENTER_NOW = "ENTER_NOW"
    SCALE_IN = "SCALE_IN"
    WAIT_FOR_PULLBACK = "WAIT_FOR_PULLBACK"
    AVOID = "AVOID"


class OIQuadrant(Enum):
    BULL_TREND = "BULL_TREND"
    BEAR_TREND = "BEAR_TREND"
    SHORT_COVER = "SHORT_COVER"
    LONG_LIQ = "LONG_LIQ"
    NEUTRAL = "NEUTRAL"


class OBIRegime(Enum):
    STRONG_BID = "STRONG_BID"
    MILD_BID = "MILD_BID"
    NEUTRAL = "NEUTRAL"
    MILD_ASK = "MILD_ASK"
    STRONG_ASK = "STRONG_ASK"


class FundingState(Enum):
    LONG_HEAVY = "LONG_HEAVY"
    LONG_LEAN = "LONG_LEAN"
    NEUTRAL = "NEUTRAL"
    SHORT_LEAN = "SHORT_LEAN"
    SHORT_HEAVY = "SHORT_HEAVY"


class PriceZone(Enum):
    """Premium / discount classification of price within a dealing range (SMC)."""

    PREMIUM = "PREMIUM"
    DISCOUNT = "DISCOUNT"
    EQUILIBRIUM = "EQUILIBRIUM"


# Decision thresholds (docs/CHART_READING.md "결정 임계값").
ENTER_THRESHOLD = 70.0
SCALE_IN_THRESHOLD = 50.0
WAIT_THRESHOLD = 35.0


@dataclass(frozen=True)
class EntryContext:
    """Context needed to compute an invalidation level for an entry decision."""

    direction: str  # 'long' | 'short'
    atr14: float
    sweep_extreme: float | None = None
    ob_mitigation_extreme: float | None = None
    spring_low: float | None = None
    tr_high: float | None = None
    htf_swing_low: float | None = None
    htf_swing_high: float | None = None


@dataclass(frozen=True)
class SignalContribution:
    """A single weighted, directional vote feeding the confluence score.

    ``direction`` is +1 (aligned with the intended trade direction), 0 (neutral / N/A),
    or -1 (opposed). ``weight`` is the applied weight (already adjusted per the spec's
    down-grading conditions). ``layer`` groups votes for reporting (htf / ltf / derived).
    """

    name: str
    weight: float
    direction: int
    layer: str = ""
    note: str = ""


# ---------------------------------------------------------------------------
# Confluence scoring primitives
# ---------------------------------------------------------------------------


def confluence_score(signals: dict[str, tuple[float, int]]) -> float:
    """Normalize weighted directional votes to a 0–100 confluence score.

    ``signals`` maps a signal name to ``(applied_weight, direction)`` with
    ``direction in {-1, 0, 1}``. Returns ``(sum(w*d) / sum(w)) * 100`` clamped to
    ``[0, 100]``; an empty map or zero total weight yields ``0.0``.
    """
    if not signals:
        return 0.0
    raw = sum(weight * direction for weight, direction in signals.values())
    max_raw = sum(weight for weight, _ in signals.values())
    if max_raw <= 0:
        return 0.0
    return max(0.0, min(100.0, (raw / max_raw) * 100.0))


def decide_entry_state(score: float, *, veto: bool) -> EntryState:
    """Map a confluence score (0–100) to an entry decision; a hard veto forces AVOID."""
    if veto:
        return EntryState.AVOID
    if score >= ENTER_THRESHOLD:
        return EntryState.ENTER_NOW
    if score >= SCALE_IN_THRESHOLD:
        return EntryState.SCALE_IN
    if score >= WAIT_THRESHOLD:
        return EntryState.WAIT_FOR_PULLBACK
    return EntryState.AVOID


def signals_to_map(contributions: list[SignalContribution]) -> dict[str, tuple[float, int]]:
    """Adapt a list of SignalContribution into the dict ``confluence_score`` expects."""
    return {c.name: (c.weight, c.direction) for c in contributions}


# ---------------------------------------------------------------------------
# Bar geometry helpers (shared by structure / candles / volume detectors)
# ---------------------------------------------------------------------------


def bar_body(bar: PriceBar) -> float:
    """Absolute candle body size = |close - open|."""
    return abs(bar.close - bar.open)


def bar_range(bar: PriceBar) -> float:
    """Full candle range (spread) = high - low."""
    return bar.high - bar.low


def upper_wick(bar: PriceBar) -> float:
    """Upper wick = high - max(open, close)."""
    return bar.high - max(bar.open, bar.close)


def lower_wick(bar: PriceBar) -> float:
    """Lower wick = min(open, close) - low."""
    return min(bar.open, bar.close) - bar.low


def is_bullish(bar: PriceBar) -> bool:
    return bar.close > bar.open


def is_bearish(bar: PriceBar) -> bool:
    return bar.close < bar.open


def body_pct(bar: PriceBar) -> float:
    """Body as a fraction of full range; 0.0 when the bar has no range."""
    rng = bar_range(bar)
    return bar_body(bar) / rng if rng > 0 else 0.0


@dataclass
class ChartRead:
    """Top-level result of reading a chart: the entry decision plus its evidence.

    Detector outputs are stored as plain lists/dicts so the aggregator (``read.py``)
    and the CLI/dashboard can render them without importing every detector module.
    """

    symbol: str
    market: str
    timeframe: str
    asof: str
    direction: str  # 'long' | 'short'
    decision: EntryState
    confluence: float
    trend_bias: TrendBias
    entry_zone: tuple[float, float] | None = None
    invalidation: float | None = None
    contributions: list[SignalContribution] = field(default_factory=list)
    features: dict[str, object] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    vetoed: bool = False
