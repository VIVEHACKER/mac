"""Candlestick pattern detector — engine/chart/candles.py.

Implements the candlestick_patterns concept from docs/CHART_READING.md §9.
Detection is strictly no-lookahead: at bar index *t*, only bars[0..t] are used.
Result dataclasses are frozen; each pattern carries ts/symbol/market/freq metadata.

Public API
----------
detect_candlestick_patterns(bars, **kwargs) -> list[CandlePattern]
classify_candle_strength(pattern, bars)     -> int
check_confirmation(pattern, next_bar)       -> bool
check_confirmation_strict(pattern, pattern_bar, next_bar) -> bool
classify_direction(pattern_name)            -> str
is_pattern_at_level_price(price, levels, atr, tol_pct=0.005) -> bool
get_candle_entry_state(pattern, confirmed)  -> EntryState
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, datetime

from data.models import PriceBar
from engine.chart.types import EntryState

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CandlePattern:
    """A detected candlestick pattern on a single completed bar.

    Fields
    ------
    ts              : timestamp of the *signal* bar (last bar of the pattern).
    symbol          : instrument symbol.
    market          : market identifier (e.g. 'crypto', 'us_equity').
    freq            : bar frequency (e.g. '1d', '1h').
    pattern_name    : string identifier (e.g. 'hammer', 'bullish_engulfing').
    direction       : 'bullish' | 'bearish' | 'neutral'.
    bar_i           : 0-based index of the signal bar in the input list.
    prior_trend     : 'up' | 'down' | 'neutral' at the signal bar.
    strength        : 1-3 (1=WAIT, 2=ENTER_ON_CONFIRM, 3=ENTER_NOW).
    body_pct_signal : body/range ratio of the signal bar.
    vol_ratio       : signal-bar volume / 10-bar average volume.
    mitigated       : True if the pattern level has been traded through.
    gap_present     : for morning/evening star — True if c1 close ≠ star-body boundary.
    signal_high     : high of the signal bar (used by check_confirmation per spec).
    signal_low      : low of the signal bar (used by check_confirmation per spec).
    signal_close    : close of the signal bar (used by is_pattern_at_level per spec).
    """

    ts: date | datetime
    symbol: str
    market: str
    freq: str
    pattern_name: str
    direction: str
    bar_i: int
    prior_trend: str
    strength: int
    body_pct_signal: float
    vol_ratio: float
    mitigated: bool = False
    gap_present: bool = False
    signal_high: float | None = None
    signal_low: float | None = None
    signal_close: float | None = None


# ---------------------------------------------------------------------------
# Module-level pattern sets used by classify_direction
# ---------------------------------------------------------------------------

BULLISH_PATTERNS: frozenset[str] = frozenset(
    {
        "hammer",
        "inverted_hammer",
        "bull_marubozu",
        "dragonfly_doji",
        "bullish_engulfing",
        "bullish_harami",
        "piercing_line",
        "tweezer_bottom",
        "morning_star",
        "three_white_soldiers",
    }
)

BEARISH_PATTERNS: frozenset[str] = frozenset(
    {
        "hanging_man",
        "shooting_star",
        "bear_marubozu",
        "gravestone_doji",
        "bearish_engulfing",
        "bearish_harami",
        "dark_cloud_cover",
        "tweezer_top",
        "evening_star",
        "three_black_crows",
    }
)

# Patterns that span two bars (pattern is attributed to bars[i])
_DUAL_PATTERNS: frozenset[str] = frozenset(
    {
        "bullish_engulfing",
        "bearish_engulfing",
        "bullish_harami",
        "bearish_harami",
        "piercing_line",
        "dark_cloud_cover",
        "tweezer_bottom",
        "tweezer_top",
    }
)

# Patterns that span three bars
_TRIPLE_PATTERNS: frozenset[str] = frozenset(
    {
        "morning_star",
        "evening_star",
        "three_white_soldiers",
        "three_black_crows",
    }
)


# ---------------------------------------------------------------------------
# STEP 0 — geometry helpers (local, complementing types.py which is shared)
# ---------------------------------------------------------------------------


def _body(b: PriceBar) -> float:
    return abs(b.close - b.open)


def _range(b: PriceBar) -> float:
    rng = b.high - b.low
    return rng if rng != 0 else 1e-10


def _upper_wick(b: PriceBar) -> float:
    return b.high - max(b.open, b.close)


def _lower_wick(b: PriceBar) -> float:
    return min(b.open, b.close) - b.low


def _body_pct(b: PriceBar) -> float:
    return _body(b) / _range(b)


def _is_bull(b: PriceBar) -> bool:
    return b.close >= b.open


def _is_bear(b: PriceBar) -> bool:
    return b.close < b.open


def _body_mid(b: PriceBar) -> float:
    return (b.open + b.close) / 2.0


# ---------------------------------------------------------------------------
# STEP 0b — direction classifier
# ---------------------------------------------------------------------------


def classify_direction(pattern_name: str) -> str:
    """Return 'bullish', 'bearish', or 'neutral' for a pattern name."""
    if pattern_name in BULLISH_PATTERNS:
        return "bullish"
    if pattern_name in BEARISH_PATTERNS:
        return "bearish"
    return "neutral"


# ---------------------------------------------------------------------------
# STEP 1 — prior-trend detector
# ---------------------------------------------------------------------------


def _prior_trend(bars: list[PriceBar], i: int, n: int = 5) -> str:
    """Return 'up', 'down', or 'neutral' based on the N bars before index i."""
    if i < n:
        return "neutral"
    window = bars[i - n : i]
    highs = [b.high for b in window]
    lows = [b.low for b in window]
    hh = sum(1 for j in range(1, len(highs)) if highs[j] > highs[j - 1])
    lh = sum(1 for j in range(1, len(highs)) if highs[j] < highs[j - 1])
    hl = sum(1 for j in range(1, len(lows)) if lows[j] > lows[j - 1])
    ll = sum(1 for j in range(1, len(lows)) if lows[j] < lows[j - 1])
    score = (hh + hl) - (lh + ll)
    if score >= 2:
        return "up"
    if score <= -2:
        return "down"
    return "neutral"


# ---------------------------------------------------------------------------
# STEP 2 — single-candle detectors
# ---------------------------------------------------------------------------


def _is_marubozu(
    b: PriceBar,
    *,
    body_min: float = 0.95,
    shadow_pct: float = 0.05,
) -> str | None:
    bd = _body(b)
    if (
        _body_pct(b) >= body_min
        and _upper_wick(b) < shadow_pct * bd
        and _lower_wick(b) < shadow_pct * bd
    ):
        return "bull_marubozu" if _is_bull(b) else "bear_marubozu"
    return None


def _is_doji(b: PriceBar, *, doji_body_threshold: float = 0.05) -> str | None:
    # Dragonfly and gravestone take priority over plain doji
    if _body_pct(b) <= doji_body_threshold:
        rng = _range(b)
        uw = _upper_wick(b)
        lw = _lower_wick(b)
        # Dragonfly: long lower wick, tiny upper wick
        if uw <= 0.05 * rng and lw >= 0.60 * rng:
            return "dragonfly_doji"
        # Gravestone: long upper wick, tiny lower wick
        if lw <= 0.05 * rng and uw >= 0.60 * rng:
            return "gravestone_doji"
        return "doji"
    return None


def _is_dragonfly_doji(b: PriceBar) -> str | None:
    if _body_pct(b) > 0.05:
        return None
    rng = _range(b)
    if _upper_wick(b) <= 0.05 * rng and _lower_wick(b) >= 0.60 * rng:
        return "dragonfly_doji"
    return None


def _is_gravestone_doji(b: PriceBar) -> str | None:
    if _body_pct(b) > 0.05:
        return None
    rng = _range(b)
    if _lower_wick(b) <= 0.05 * rng and _upper_wick(b) >= 0.60 * rng:
        return "gravestone_doji"
    return None


def _is_spinning_top(
    b: PriceBar,
    *,
    body_max: float = 0.30,
    wick_min_ratio: float = 2.0,
) -> str | None:
    bp = _body_pct(b)
    bd = _body(b)
    if (
        0.05 <= bp <= body_max
        and bd > 0
        and _upper_wick(b) >= wick_min_ratio * bd
        and _lower_wick(b) >= wick_min_ratio * bd
    ):
        return "spinning_top"
    return None


def _is_hammer(
    bars: list[PriceBar],
    i: int,
    *,
    body_max: float = 0.35,
    wick_ratio: float = 2.0,
    upper_max: float = 0.10,
    trend_lookback: int = 5,
) -> str | None:
    if _prior_trend(bars, i, trend_lookback) != "down":
        return None
    b = bars[i]
    bd = _body(b)
    rng = _range(b)
    if bd == 0:
        return None
    if _body_pct(b) > body_max:
        return None
    if _lower_wick(b) < wick_ratio * bd:
        return None
    if _upper_wick(b) > upper_max * rng:
        return None
    body_bottom = min(b.open, b.close)
    if body_bottom < b.low + 0.60 * rng:
        return None
    return "hammer"


def _is_hanging_man(
    bars: list[PriceBar],
    i: int,
    *,
    body_max: float = 0.35,
    wick_ratio: float = 2.0,
    upper_max: float = 0.10,
    trend_lookback: int = 5,
) -> str | None:
    if _prior_trend(bars, i, trend_lookback) != "up":
        return None
    b = bars[i]
    bd = _body(b)
    rng = _range(b)
    if bd == 0:
        return None
    if _body_pct(b) > body_max:
        return None
    if _lower_wick(b) < wick_ratio * bd:
        return None
    if _upper_wick(b) > upper_max * rng:
        return None
    body_bottom = min(b.open, b.close)
    if body_bottom < b.low + 0.60 * rng:
        return None
    return "hanging_man"


def _is_shooting_star(
    bars: list[PriceBar],
    i: int,
    *,
    body_max: float = 0.35,
    wick_ratio: float = 2.0,
    lower_max: float = 0.10,
    trend_lookback: int = 5,
) -> str | None:
    if _prior_trend(bars, i, trend_lookback) != "up":
        return None
    b = bars[i]
    bd = _body(b)
    rng = _range(b)
    if bd == 0:
        return None
    if _body_pct(b) > body_max:
        return None
    if _upper_wick(b) < wick_ratio * bd:
        return None
    if _lower_wick(b) > lower_max * rng:
        return None
    body_top = max(b.open, b.close)
    if (b.high - body_top) < 0.60 * rng:
        return None
    return "shooting_star"


def _is_inverted_hammer(
    bars: list[PriceBar],
    i: int,
    *,
    body_max: float = 0.35,
    wick_ratio: float = 2.0,
    lower_max: float = 0.10,
    trend_lookback: int = 5,
) -> str | None:
    if _prior_trend(bars, i, trend_lookback) != "down":
        return None
    b = bars[i]
    bd = _body(b)
    rng = _range(b)
    if bd == 0:
        return None
    if _body_pct(b) > body_max:
        return None
    if _upper_wick(b) < wick_ratio * bd:
        return None
    if _lower_wick(b) > lower_max * rng:
        return None
    body_top = max(b.open, b.close)
    if (b.high - body_top) < 0.60 * rng:
        return None
    return "inverted_hammer"


# ---------------------------------------------------------------------------
# STEP 3 — dual-candle detectors (bars[i-1] = prev, bars[i] = curr)
# ---------------------------------------------------------------------------


def _is_bullish_engulfing(
    bars: list[PriceBar],
    i: int,
    *,
    trend_lookback: int = 5,
) -> str | None:
    if i < 1:
        return None
    if _prior_trend(bars, i, trend_lookback) != "down":
        return None
    prev, curr = bars[i - 1], bars[i]
    if not (_is_bear(prev) and _is_bull(curr)):
        return None
    if curr.open > prev.close:
        return None
    if curr.close < prev.open:
        return None
    if _body(curr) <= _body(prev):
        return None
    return "bullish_engulfing"


def _is_bearish_engulfing(
    bars: list[PriceBar],
    i: int,
    *,
    trend_lookback: int = 5,
) -> str | None:
    if i < 1:
        return None
    if _prior_trend(bars, i, trend_lookback) != "up":
        return None
    prev, curr = bars[i - 1], bars[i]
    if not (_is_bull(prev) and _is_bear(curr)):
        return None
    if curr.open < prev.close:
        return None
    if curr.close > prev.open:
        return None
    if _body(curr) <= _body(prev):
        return None
    return "bearish_engulfing"


def _is_bullish_harami(
    bars: list[PriceBar],
    i: int,
    *,
    trend_lookback: int = 5,
    first_body_min: float = 0.60,
) -> str | None:
    if i < 1:
        return None
    if _prior_trend(bars, i, trend_lookback) != "down":
        return None
    prev, curr = bars[i - 1], bars[i]
    if not (_is_bear(prev) and _is_bull(curr)):
        return None
    if _body_pct(prev) < first_body_min:
        return None
    prev_low_body = min(prev.open, prev.close)
    prev_high_body = max(prev.open, prev.close)
    curr_low_body = min(curr.open, curr.close)
    curr_high_body = max(curr.open, curr.close)
    if curr_low_body < prev_low_body or curr_high_body > prev_high_body:
        return None
    return "bullish_harami"


def _is_bearish_harami(
    bars: list[PriceBar],
    i: int,
    *,
    trend_lookback: int = 5,
    first_body_min: float = 0.60,
) -> str | None:
    if i < 1:
        return None
    if _prior_trend(bars, i, trend_lookback) != "up":
        return None
    prev, curr = bars[i - 1], bars[i]
    if not (_is_bull(prev) and _is_bear(curr)):
        return None
    if _body_pct(prev) < first_body_min:
        return None
    prev_low_body = min(prev.open, prev.close)
    prev_high_body = max(prev.open, prev.close)
    curr_low_body = min(curr.open, curr.close)
    curr_high_body = max(curr.open, curr.close)
    if curr_low_body < prev_low_body or curr_high_body > prev_high_body:
        return None
    return "bearish_harami"


def _is_piercing_line(
    bars: list[PriceBar],
    i: int,
    *,
    trend_lookback: int = 5,
) -> str | None:
    if i < 1:
        return None
    if _prior_trend(bars, i, trend_lookback) != "down":
        return None
    prev, curr = bars[i - 1], bars[i]
    if not (_is_bear(prev) and _is_bull(curr)):
        return None
    if curr.open >= prev.close:
        return None
    prev_mid = _body_mid(prev)
    if curr.close <= prev_mid:  # must be strictly above midpoint
        return None
    if curr.close >= prev.open:  # full engulfing — not piercing
        return None
    return "piercing_line"


def _is_dark_cloud_cover(
    bars: list[PriceBar],
    i: int,
    *,
    trend_lookback: int = 5,
) -> str | None:
    if i < 1:
        return None
    if _prior_trend(bars, i, trend_lookback) != "up":
        return None
    prev, curr = bars[i - 1], bars[i]
    if not (_is_bull(prev) and _is_bear(curr)):
        return None
    if curr.open <= prev.close:  # gap up required
        return None
    prev_mid = _body_mid(prev)
    if curr.close >= prev_mid:  # must be strictly below midpoint
        return None
    if curr.close <= prev.open:  # full engulfing
        return None
    return "dark_cloud_cover"


def _is_tweezer_bottom(
    bars: list[PriceBar],
    i: int,
    *,
    trend_lookback: int = 5,
    tol_pct: float = 0.003,
) -> str | None:
    if i < 1:
        return None
    if _prior_trend(bars, i, trend_lookback) != "down":
        return None
    prev, curr = bars[i - 1], bars[i]
    if not (_is_bear(prev) and _is_bull(curr)):
        return None
    if abs(prev.low - curr.low) > tol_pct * prev.low:
        return None
    return "tweezer_bottom"


def _is_tweezer_top(
    bars: list[PriceBar],
    i: int,
    *,
    trend_lookback: int = 5,
    tol_pct: float = 0.003,
) -> str | None:
    if i < 1:
        return None
    if _prior_trend(bars, i, trend_lookback) != "up":
        return None
    prev, curr = bars[i - 1], bars[i]
    if not (_is_bull(prev) and _is_bear(curr)):
        return None
    if abs(prev.high - curr.high) > tol_pct * prev.high:
        return None
    return "tweezer_top"


# ---------------------------------------------------------------------------
# STEP 4 — triple-candle detectors (c1=bars[i-2], c2=bars[i-1], c3=bars[i])
# ---------------------------------------------------------------------------


def _is_morning_star(
    bars: list[PriceBar],
    i: int,
    *,
    trend_lookback: int = 5,
    star_body_max: float = 0.30,
) -> tuple[str, bool] | None:
    if i < 2:
        return None
    # Prior trend is evaluated at i-2 (before c1)
    if _prior_trend(bars, i - 2, trend_lookback) != "down":
        return None
    c1, c2, c3 = bars[i - 2], bars[i - 1], bars[i]
    if not (_is_bear(c1) and _body_pct(c1) >= 0.50):
        return None
    if _body_pct(c2) > star_body_max:
        return None
    if not _is_bull(c3):
        return None
    c1_mid = (c1.open + c1.close) / 2.0
    if c3.close < c1_mid:
        return None
    # Star body must be completely below c1.close
    if max(c2.open, c2.close) >= c1.close:
        return None
    gap_present = abs(c1.close - max(c2.open, c2.close)) > 0
    return ("morning_star", gap_present)


def _is_evening_star(
    bars: list[PriceBar],
    i: int,
    *,
    trend_lookback: int = 5,
    star_body_max: float = 0.30,
) -> tuple[str, bool] | None:
    if i < 2:
        return None
    if _prior_trend(bars, i - 2, trend_lookback) != "up":
        return None
    c1, c2, c3 = bars[i - 2], bars[i - 1], bars[i]
    if not (_is_bull(c1) and _body_pct(c1) >= 0.50):
        return None
    if _body_pct(c2) > star_body_max:
        return None
    if not _is_bear(c3):
        return None
    c1_mid = (c1.open + c1.close) / 2.0
    if c3.close > c1_mid:
        return None
    # Star body must be completely above c1.close
    if min(c2.open, c2.close) <= c1.close:
        return None
    gap_present = abs(c1.close - min(c2.open, c2.close)) > 0
    return ("evening_star", gap_present)


def _is_three_white_soldiers(
    bars: list[PriceBar],
    i: int,
    *,
    body_min: float = 0.50,
    wick_max: float = 0.15,
) -> str | None:
    if i < 2:
        return None
    c1, c2, c3 = bars[i - 2], bars[i - 1], bars[i]
    for c in (c1, c2, c3):
        if not _is_bull(c):
            return None
        if _body_pct(c) < body_min:
            return None
        if _upper_wick(c) > wick_max * _range(c):
            return None
    if not (c1.open <= c2.open <= c1.close):
        return None
    if not (c2.open <= c3.open <= c2.close):
        return None
    if not (c2.close > c1.close and c3.close > c2.close):
        return None
    return "three_white_soldiers"


def _is_three_black_crows(
    bars: list[PriceBar],
    i: int,
    *,
    body_min: float = 0.50,
    wick_max: float = 0.15,
) -> str | None:
    if i < 2:
        return None
    c1, c2, c3 = bars[i - 2], bars[i - 1], bars[i]
    for c in (c1, c2, c3):
        if not _is_bear(c):
            return None
        if _body_pct(c) < body_min:
            return None
        if _lower_wick(c) > wick_max * _range(c):
            return None
    if not (c1.close <= c2.open <= c1.open):
        return None
    if not (c2.close <= c3.open <= c2.open):
        return None
    if not (c2.close < c1.close and c3.close < c2.close):
        return None
    return "three_black_crows"


# ---------------------------------------------------------------------------
# STEP 6 — strength scoring helper
# ---------------------------------------------------------------------------


def _compute_strength(
    pattern_name: str,
    bar_i: int,
    bars: list[PriceBar],
    *,
    strength_vol_ratio: float = 1.5,
) -> tuple[int, float, float]:
    """Return (strength, body_pct_signal, vol_ratio).

    Scoring rules from the spec:
    1. body_pct of signal bar > 1.2× mean(body_pct, last 10 bars)  → +1
    2. volume > strength_vol_ratio × mean(volume, last 10 bars)     → +1
    3. triple → +1; dual → 0; single → -1
    Final: max(1, min(3, score + 1)) where base score starts at 0.
    """
    b = bars[bar_i]
    bp_signal = _body_pct(b)
    vol_signal = b.volume

    lookback = bars[max(0, bar_i - 10) : bar_i]

    if lookback:
        avg_bp = statistics.mean(_body_pct(x) for x in lookback)
        avg_vol = statistics.mean(x.volume for x in lookback)
    else:
        avg_bp = bp_signal
        avg_vol = vol_signal

    vol_ratio = vol_signal / avg_vol if avg_vol > 0 else 1.0

    score = 0
    if bp_signal > 1.2 * avg_bp:
        score += 1
    if vol_ratio > strength_vol_ratio:
        score += 1

    # Pattern-class bonus/penalty
    if pattern_name in _TRIPLE_PATTERNS:
        score += 1
    elif pattern_name in _DUAL_PATTERNS:
        pass  # 0
    else:
        score -= 1  # single candle

    strength = max(1, min(3, score + 1))

    # Low-volume guard: force strength=1 if vol_ratio < 0.8
    if vol_ratio < 0.8:
        strength = 1

    return strength, bp_signal, vol_ratio


# ---------------------------------------------------------------------------
# STEP 5 — main scan loop → public aggregator
# ---------------------------------------------------------------------------


def detect_candlestick_patterns(
    bars: list[PriceBar],
    *,
    trend_lookback: int = 5,
    doji_body_threshold: float = 0.05,
    marubozu_body_min: float = 0.95,
    marubozu_shadow_pct: float = 0.05,
    spinning_top_body_max: float = 0.30,
    spinning_top_wick_min_ratio: float = 2.0,
    hammer_wick_ratio: float = 2.0,
    hammer_body_max: float = 0.35,
    hammer_upper_max: float = 0.10,
    shooting_star_wick_ratio: float = 2.0,
    shooting_star_body_max: float = 0.35,
    shooting_star_lower_max: float = 0.10,
    harami_first_body_min: float = 0.60,
    tweezer_tol_pct: float = 0.003,
    morning_star_body_max: float = 0.30,
    three_soldiers_body_min: float = 0.50,
    three_soldiers_wick_max: float = 0.15,
    strength_vol_ratio: float = 1.5,
) -> list[CandlePattern]:
    """Scan *bars* (ascending ts) and return every detected candlestick pattern.

    No-lookahead guarantee: at bar index *i* only bars[0..i] are accessed.
    The scan loop starts at ``trend_lookback + 2`` so that triple-pattern detectors
    calling ``_prior_trend(bars, i-2)`` always have a full lookback window.
    """
    if not bars:
        return []

    results: list[CandlePattern] = []
    start = trend_lookback + 2

    for i in range(start, len(bars)):
        b = bars[i]
        trend_here = _prior_trend(bars, i, trend_lookback)

        def _emit(
            name: str,
            *,
            gap: bool = False,
            _b: PriceBar = b,
            _i: int = i,
            _trend: str = trend_here,
            _bars: list[PriceBar] = bars,
            _svr: float = strength_vol_ratio,
        ) -> CandlePattern:
            strength, bp_sig, vr = _compute_strength(name, _i, _bars, strength_vol_ratio=_svr)
            return CandlePattern(
                ts=_b.ts,
                symbol=_b.symbol,
                market=_b.market,
                freq=_b.freq,
                pattern_name=name,
                direction=classify_direction(name),
                bar_i=_i,
                prior_trend=_trend,
                strength=strength,
                body_pct_signal=bp_sig,
                vol_ratio=vr,
                gap_present=gap,
                signal_high=_b.high,
                signal_low=_b.low,
                signal_close=_b.close,
            )

        # -- Single-candle --
        r = _is_marubozu(b, body_min=marubozu_body_min, shadow_pct=marubozu_shadow_pct)
        if r:
            results.append(_emit(r))

        r = _is_doji(b, doji_body_threshold=doji_body_threshold)
        if r:
            results.append(_emit(r))

        r = _is_spinning_top(
            b,
            body_max=spinning_top_body_max,
            wick_min_ratio=spinning_top_wick_min_ratio,
        )
        if r:
            results.append(_emit(r))

        # -- Single-candle with trend context --
        r = _is_hammer(
            bars,
            i,
            body_max=hammer_body_max,
            wick_ratio=hammer_wick_ratio,
            upper_max=hammer_upper_max,
            trend_lookback=trend_lookback,
        )
        if r:
            results.append(_emit(r))

        r = _is_hanging_man(
            bars,
            i,
            body_max=hammer_body_max,
            wick_ratio=hammer_wick_ratio,
            upper_max=hammer_upper_max,
            trend_lookback=trend_lookback,
        )
        if r:
            results.append(_emit(r))

        r = _is_shooting_star(
            bars,
            i,
            body_max=shooting_star_body_max,
            wick_ratio=shooting_star_wick_ratio,
            lower_max=shooting_star_lower_max,
            trend_lookback=trend_lookback,
        )
        if r:
            results.append(_emit(r))

        r = _is_inverted_hammer(
            bars,
            i,
            body_max=shooting_star_body_max,
            wick_ratio=shooting_star_wick_ratio,
            lower_max=shooting_star_lower_max,
            trend_lookback=trend_lookback,
        )
        if r:
            results.append(_emit(r))

        # -- Dual-candle --
        if i >= 1:
            for fn in (
                _is_bullish_engulfing,
                _is_bearish_engulfing,
                _is_bullish_harami,
                _is_bearish_harami,
                _is_piercing_line,
                _is_dark_cloud_cover,
            ):
                r2 = fn(bars, i, trend_lookback=trend_lookback)
                if r2:
                    results.append(_emit(r2))

            r2 = _is_tweezer_bottom(bars, i, trend_lookback=trend_lookback, tol_pct=tweezer_tol_pct)
            if r2:
                results.append(_emit(r2))

            r2 = _is_tweezer_top(bars, i, trend_lookback=trend_lookback, tol_pct=tweezer_tol_pct)
            if r2:
                results.append(_emit(r2))

        # -- Triple-candle --
        if i >= 2:
            ms = _is_morning_star(
                bars, i, trend_lookback=trend_lookback, star_body_max=morning_star_body_max
            )
            if ms:
                name_ms, gap_ms = ms
                results.append(_emit(name_ms, gap=gap_ms))

            es = _is_evening_star(
                bars, i, trend_lookback=trend_lookback, star_body_max=morning_star_body_max
            )
            if es:
                name_es, gap_es = es
                results.append(_emit(name_es, gap=gap_es))

            r3 = _is_three_white_soldiers(
                bars,
                i,
                body_min=three_soldiers_body_min,
                wick_max=three_soldiers_wick_max,
            )
            if r3:
                results.append(_emit(r3))

            r4 = _is_three_black_crows(
                bars,
                i,
                body_min=three_soldiers_body_min,
                wick_max=three_soldiers_wick_max,
            )
            if r4:
                results.append(_emit(r4))

    return results


# ---------------------------------------------------------------------------
# Public helpers required by the module map
# ---------------------------------------------------------------------------


def classify_candle_strength(pattern: CandlePattern, bars: list[PriceBar]) -> int:
    """Re-compute and return the strength (1-3) for an existing CandlePattern.

    This is a pure re-computation wrapper; it does not mutate the frozen dataclass.
    """
    strength, _, _ = _compute_strength(pattern.pattern_name, pattern.bar_i, bars)
    return strength


def check_confirmation(pattern: CandlePattern, next_bar: PriceBar) -> bool:
    """Return True when *next_bar* confirms the pattern's directional signal.

    Spec rules (docs/CHART_READING.md §9 "진입 관련성"):
    - Bullish pattern  → next_bar.close > signal-bar high
    - Bearish pattern  → next_bar.close < signal-bar low
    - Neutral (doji / spinning_top) → next bar must be directional (close != open).

    Patterns produced by :func:`detect_candlestick_patterns` carry ``signal_high`` /
    ``signal_low`` so the spec's strict high/low comparison is applied directly. For
    hand-built patterns that omit those fields, fall back to a directional-close proxy.
    """
    if pattern.direction == "bullish":
        if pattern.signal_high is not None:
            return next_bar.close > pattern.signal_high
        return next_bar.close > next_bar.open  # proxy when signal_high unknown
    if pattern.direction == "bearish":
        if pattern.signal_low is not None:
            return next_bar.close < pattern.signal_low
        return next_bar.close < next_bar.open  # proxy when signal_low unknown
    # neutral
    return next_bar.close != next_bar.open


def check_confirmation_strict(
    pattern: CandlePattern,
    pattern_bar: PriceBar,
    next_bar: PriceBar,
) -> bool:
    """Strict variant: compare next_bar.close against the *signal bar's* high/low.

    Bullish : next_bar.close > pattern_bar.high
    Bearish : next_bar.close < pattern_bar.low
    Neutral : next_bar.close != next_bar.open (directional move)
    """
    if pattern.direction == "bullish":
        return next_bar.close > pattern_bar.high
    if pattern.direction == "bearish":
        return next_bar.close < pattern_bar.low
    return next_bar.close != next_bar.open


def is_pattern_at_level(
    pattern: CandlePattern,
    levels: list[float],
    atr: float,
    tol_pct: float = 0.005,
) -> bool:
    """Return True if the pattern's signal-bar close is within *tol_pct* of any level.

    The tolerance is also bounded by 0.5 × ATR to avoid false confluences in
    fast-moving markets. ``levels`` is a list of price levels (support/resistance,
    Fibonacci retracements, VPOC, etc.). Patterns produced by
    :func:`detect_candlestick_patterns` carry ``signal_close``; if that field is
    unset (hand-built pattern), the caller must use :func:`is_pattern_at_level_price`.
    """
    if pattern.signal_close is None:
        return False
    return is_pattern_at_level_price(pattern.signal_close, levels, atr, tol_pct)


def is_pattern_at_level_price(
    price: float,
    levels: list[float],
    atr: float,
    tol_pct: float = 0.005,
) -> bool:
    """Check whether *price* sits within *tol_pct* (and ≤ 0.5 × ATR) of any level."""
    if not levels or atr <= 0:
        return False
    tol = min(tol_pct * price, 0.5 * atr)
    return any(abs(price - lvl) <= tol for lvl in levels)


def get_candle_entry_state(pattern: CandlePattern, confirmed: bool) -> EntryState:
    """Map pattern strength + confirmation to an EntryState.

    Spec rules:
    strength=1          → AVOID  (WAIT per spec, mapped to avoid until confirmed)
    strength=2          → WAIT_FOR_PULLBACK if not confirmed, SCALE_IN if confirmed
    strength=3          → ENTER_NOW (no confirmation needed for triple patterns)
    mitigated=True      → AVOID always
    inverted_hammer     → AVOID unless confirmed
    """
    if pattern.mitigated:
        return EntryState.AVOID
    if pattern.pattern_name == "inverted_hammer" and not confirmed:
        return EntryState.AVOID
    if pattern.strength == 3:
        return EntryState.ENTER_NOW
    if pattern.strength == 2:
        return EntryState.SCALE_IN if confirmed else EntryState.WAIT_FOR_PULLBACK
    # strength == 1
    return EntryState.AVOID
