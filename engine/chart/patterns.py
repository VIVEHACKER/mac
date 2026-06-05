"""Classical chart-pattern detector for the chart-reading engine.

Implements: Double Top/Bottom, Head-and-Shoulders (Top & Inverse),
Symmetrical/Ascending/Descending Triangles, Rising/Falling Wedges,
Flag/Pennant, Rectangle/Channel, and Cup-and-Handle.

All detections are strictly lookahead-free: pivot at index i is only
confirmed after bar i+swing_lookback has closed.  See docs/CHART_READING.md
section 8 (chart_patterns) for the canonical algorithm.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from data.models import PriceBar

# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SwingPivot:
    """A confirmed swing pivot point."""

    bar_index: int
    price: float
    direction: str  # 'H' | 'L'
    ts: date | datetime


@dataclass(frozen=True)
class PullbackRetest:
    """Result of a pullback retest check after a pattern breakout."""

    detected: bool
    bar_index: int | None = None
    ts: date | datetime | None = None
    entry_tag: str = "pullback_retest"
    risk_low: float | None = None  # neckline - ATR  (for bullish)
    risk_high: float | None = None  # neckline + ATR  (for bearish)


@dataclass
class ChartPattern:
    """A detected classical chart pattern.

    All fields follow docs/CHART_READING.md STEP 14 output spec.
    ``mitigated`` may be updated retroactively by later bars (pass the
    bar series to ``update_mitigation``).
    """

    pattern_type: str  # 'double_top', 'double_bottom', …
    direction: str  # 'bullish' | 'bearish' | 'neutral'
    ts_start: date | datetime
    ts_end: date | datetime
    ts_breakout: date | datetime | None
    zone_low: float
    zone_high: float
    neckline_price: float | None
    target_price: float | None
    pivot_sequence: list[tuple[date | datetime, float, str]] = field(default_factory=list)
    strength: float = 0.5  # 0–1 composite
    mitigated: bool = False
    ts_mitigated: date | datetime | None = None
    trend_context: str = "range"  # 'uptrend' | 'downtrend' | 'range'
    trendline_r_squared_top: float | None = None
    trendline_r_squared_bot: float | None = None
    pole_range: float | None = None  # flag/pennant only
    volume_ratio_at_breakout: float | None = None
    apex_bar_index: int | None = None  # triangle/wedge/pennant only

    # Internal bookkeeping — not part of public output spec
    _peak_prices: list[float] = field(default_factory=list, compare=False, repr=False)


# ---------------------------------------------------------------------------
# Parameters dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChartPatternParams:
    """All tunable parameters with spec defaults."""

    swing_lookback: int = 2
    peak_tolerance: float = 0.03
    min_peak_separation: int = 5
    min_pattern_bars: int = 10
    shoulder_sym_pct: float = 0.05
    time_sym_ratio: float = 2.5
    flat_slope_threshold: float = 0.1
    trendline_fit_tolerance: float = 0.01
    triangle_lookback_pivots: int = 8
    breakout_pct_apex_min: float = 0.50
    breakout_pct_apex_max: float = 0.75
    pole_min_pct: float = 0.04
    pole_min_bars: int = 3
    flag_min_bars: int = 5
    flag_max_bars: int = 20
    flag_retracement_max: float = 0.50
    breakout_vol_ratio: float = 1.5
    channel_lookback_pivots: int = 10
    price_buffer: float = 0.002
    cup_lookback_bars: int = 120
    cup_rim_tolerance: float = 0.03
    cup_depth_min: float = 0.12
    cup_depth_max: float = 0.33
    neckline_slope_limit: float = -0.005


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_DEFAULT_PARAMS = ChartPatternParams()


def _mean_volume(bars: list[PriceBar], current_idx: int, lookback: int = 20) -> float:
    start = max(0, current_idx - lookback + 1)
    vols = [bars[j].volume for j in range(start, current_idx + 1)]
    return statistics.mean(vols) if vols else 1.0


def _detect_swing_pivots(
    bars: list[PriceBar],
    swing_lookback: int,
) -> list[SwingPivot]:
    """Return all confirmed swing pivots (lookahead-safe).

    Pivot at index i is confirmed only when bar i+swing_lookback has closed,
    i.e. i+swing_lookback < len(bars).
    """
    n = len(bars)
    pivots: list[SwingPivot] = []
    # Confirmed range: swing_lookback <= i <= n-swing_lookback-1
    for i in range(swing_lookback, n - swing_lookback):
        window_highs = [bars[j].high for j in range(i - swing_lookback, i + swing_lookback + 1)]
        window_lows = [bars[j].low for j in range(i - swing_lookback, i + swing_lookback + 1)]
        is_sh = bars[i].high == max(window_highs)
        is_sl = bars[i].low == min(window_lows)
        if is_sh:
            pivots.append(SwingPivot(i, bars[i].high, "H", bars[i].ts))
        elif is_sl:
            pivots.append(SwingPivot(i, bars[i].low, "L", bars[i].ts))

    # Deduplicate consecutive same-direction pivots
    merged: list[SwingPivot] = []
    for pv in pivots:
        if merged and merged[-1].direction == pv.direction:
            prev = merged[-1]
            if (pv.direction == "H" and pv.price >= prev.price) or (
                pv.direction == "L" and pv.price <= prev.price
            ):
                merged[-1] = pv
            # else keep prev (higher H or lower L already stored)
        else:
            merged.append(pv)
    return merged


def _classify_trend(pivots: list[SwingPivot]) -> str:
    """Classify trend from last 2 confirmed swing highs and lows."""
    highs = [p for p in pivots if p.direction == "H"]
    lows = [p for p in pivots if p.direction == "L"]
    if len(highs) < 2 or len(lows) < 2:
        return "range"
    sh1, sh2 = highs[-2], highs[-1]
    sl1, sl2 = lows[-2], lows[-1]
    up = sh2.price > sh1.price and sl2.price > sl1.price
    down = sh2.price < sh1.price and sl2.price < sl1.price
    if up:
        return "uptrend"
    if down:
        return "downtrend"
    return "range"


def _trendline_fit(
    pts: list[tuple[int, float]],
) -> tuple[float, float, float, float]:
    """Least-squares line fit: returns (m, b, r_squared, residual_std)."""
    n = len(pts)
    if n < 2:
        return 0.0, pts[0][1] if pts else 0.0, 1.0, 0.0
    sum_x = sum(x for x, _ in pts)
    sum_y = sum(y for _, y in pts)
    sum_xy = sum(x * y for x, y in pts)
    sum_x2 = sum(x * x for x, _ in pts)
    denom = n * sum_x2 - sum_x**2
    m = (n * sum_xy - sum_x * sum_y) / denom if denom != 0 else 0.0
    b = (sum_y - m * sum_x) / n
    if n == 2:
        return m, b, 1.0, 0.0
    y_mean = sum_y / n
    ss_tot = sum((y - y_mean) ** 2 for _, y in pts)
    ss_res = sum((y - (m * x + b)) ** 2 for x, y in pts)
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0
    residuals = [abs(y - (m * x + b)) for x, y in pts]
    res_std = statistics.pstdev(residuals) if len(residuals) > 1 else 0.0
    return m, b, r2, res_std


def _passes_fit_tolerance(
    r2: float,
    res_std: float,
    mean_price: float,
    n_pts: int,
    tolerance: float,
) -> bool:
    """r_squared quality gate — only for >=3 pivot touches."""
    if n_pts < 3:
        return True
    return (res_std / mean_price) <= tolerance if mean_price > 0 else True


def _price_range(bars: list[PriceBar]) -> float:
    return max(b.high for b in bars) - min(b.low for b in bars) if bars else 1.0


def _vol_regression_slope(bars: list[PriceBar]) -> float:
    """Linear regression slope of volume over bar range (for volume contraction check)."""
    pts = [(i, bars[i].volume) for i in range(len(bars))]
    if len(pts) < 2:
        return 0.0
    m, _, _, _ = _trendline_fit(pts)
    return m


# ---------------------------------------------------------------------------
# STEP 3 — Double Top
# ---------------------------------------------------------------------------


def _detect_double_top(
    bars: list[PriceBar],
    pivots: list[SwingPivot],
    trend_context: str,
    params: ChartPatternParams,
) -> list[ChartPattern]:
    highs = [p for p in pivots if p.direction == "H"]
    lows = [p for p in pivots if p.direction == "L"]
    if len(highs) < 3:
        return []

    results: list[ChartPattern] = []
    # Use last 3 confirmed swing highs
    h1, h2, h3 = highs[-3], highs[-2], highs[-1]
    # h2=left peak, h3=right peak

    # Tolerance check
    if abs(h3.price - h2.price) / h2.price > params.peak_tolerance:
        return []

    # Separation
    if (h3.bar_index - h2.bar_index) < params.min_peak_separation:
        return []

    # Pattern span
    if (h3.bar_index - h1.bar_index) < params.min_pattern_bars:
        return []

    # Neckline = lowest SwingLow between h2 and h3
    between_lows = [p for p in lows if h2.bar_index < p.bar_index < h3.bar_index]
    if not between_lows:
        return []
    neckline_pv = min(between_lows, key=lambda p: p.price)
    neckline_price = neckline_pv.price

    # Volume: h3 volume < h2 volume
    vol_h2 = bars[h2.bar_index].volume
    vol_h3 = bars[h3.bar_index].volume
    vol_ok = vol_h3 < vol_h2

    # Zone
    zone_low = neckline_price
    zone_high = max(h2.price, h3.price)

    # ATR-style span guard (zone height >= ATR-ish guard skipped; rely on pattern span)
    # Strength
    sym_score = 1.0 - abs(h3.price - h2.price) / (h2.price * params.peak_tolerance)
    vol_score = 0.8 if vol_ok else 0.4
    strength = (sym_score + vol_score) / 2.0
    strength = max(0.0, min(1.0, strength))

    # Check breakout at the last bar
    ts_breakout = None
    vol_ratio_at_breakout = None
    last_bar = bars[-1]
    if last_bar.close < neckline_price:
        ts_breakout = last_bar.ts
        mean_vol = _mean_volume(bars, len(bars) - 1)
        vol_ratio_at_breakout = last_bar.volume / mean_vol if mean_vol > 0 else None

    target_price = neckline_price - (h2.price - neckline_price)

    pattern = ChartPattern(
        pattern_type="double_top",
        direction="bearish",
        ts_start=h2.ts,
        ts_end=h3.ts,
        ts_breakout=ts_breakout,
        zone_low=zone_low,
        zone_high=zone_high,
        neckline_price=neckline_price,
        target_price=target_price,
        pivot_sequence=[
            (h2.ts, h2.price, "H"),
            (neckline_pv.ts, neckline_pv.price, "L"),
            (h3.ts, h3.price, "H"),
        ],
        strength=strength,
        trend_context=trend_context,
        volume_ratio_at_breakout=vol_ratio_at_breakout,
        _peak_prices=[h2.price, h3.price],
    )
    results.append(pattern)
    return results


# ---------------------------------------------------------------------------
# STEP 4 — Double Bottom
# ---------------------------------------------------------------------------


def _detect_double_bottom(
    bars: list[PriceBar],
    pivots: list[SwingPivot],
    trend_context: str,
    params: ChartPatternParams,
) -> list[ChartPattern]:
    highs = [p for p in pivots if p.direction == "H"]
    lows = [p for p in pivots if p.direction == "L"]
    if len(lows) < 3:
        return []

    l1, l2, l3 = lows[-3], lows[-2], lows[-1]
    # l2=left trough, l3=right trough

    if abs(l3.price - l2.price) / l2.price > params.peak_tolerance:
        return []

    if (l3.bar_index - l2.bar_index) < params.min_peak_separation:
        return []

    if (l3.bar_index - l1.bar_index) < params.min_pattern_bars:
        return []

    # Neckline = highest SwingHigh between l2 and l3
    between_highs = [p for p in highs if l2.bar_index < p.bar_index < l3.bar_index]
    if not between_highs:
        return []
    neckline_pv = max(between_highs, key=lambda p: p.price)
    neckline_price = neckline_pv.price

    # Volume preference: l3 volume >= l2 volume (less strict)
    vol_l2 = bars[l2.bar_index].volume
    vol_l3 = bars[l3.bar_index].volume
    vol_ok = vol_l3 >= vol_l2

    zone_low = min(l2.price, l3.price)
    zone_high = neckline_price

    sym_score = 1.0 - abs(l3.price - l2.price) / (l2.price * params.peak_tolerance)
    vol_score = 0.8 if vol_ok else 0.5
    strength = (sym_score + vol_score) / 2.0
    strength = max(0.0, min(1.0, strength))

    ts_breakout = None
    vol_ratio_at_breakout = None
    last_bar = bars[-1]
    if last_bar.close > neckline_price:
        ts_breakout = last_bar.ts
        mean_vol = _mean_volume(bars, len(bars) - 1)
        vol_ratio_at_breakout = last_bar.volume / mean_vol if mean_vol > 0 else None

    target_price = neckline_price + (neckline_price - l2.price)

    pattern = ChartPattern(
        pattern_type="double_bottom",
        direction="bullish",
        ts_start=l2.ts,
        ts_end=l3.ts,
        ts_breakout=ts_breakout,
        zone_low=zone_low,
        zone_high=zone_high,
        neckline_price=neckline_price,
        target_price=target_price,
        pivot_sequence=[
            (l2.ts, l2.price, "L"),
            (neckline_pv.ts, neckline_pv.price, "H"),
            (l3.ts, l3.price, "L"),
        ],
        strength=strength,
        trend_context=trend_context,
        volume_ratio_at_breakout=vol_ratio_at_breakout,
        _peak_prices=[l2.price, l3.price],
    )
    return [pattern]


# ---------------------------------------------------------------------------
# STEP 5 — Head and Shoulders (top)
# ---------------------------------------------------------------------------


def _detect_head_and_shoulders(
    bars: list[PriceBar],
    pivots: list[SwingPivot],
    trend_context: str,
    params: ChartPatternParams,
) -> list[ChartPattern]:
    """Head and Shoulders (bearish reversal)."""
    # Collect alternating confirmed pivots; we need a 5-pivot H sequence:
    # ls_high(i1), ls_low(i2), head_high(i3), rs_low(i4), rs_high(i5)
    if len(pivots) < 5:
        return []

    # Try last 5 pivots as a candidate
    candidates = pivots[-5:]
    if len(candidates) < 5:
        return []

    ls_h, ls_l, head_h, rs_l, rs_h = candidates

    # Must alternate H/L/H/L/H
    if not (
        ls_h.direction == "H"
        and ls_l.direction == "L"
        and head_h.direction == "H"
        and rs_l.direction == "L"
        and rs_h.direction == "H"
    ):
        return []

    # Head is highest
    if not (head_h.price > ls_h.price and head_h.price > rs_h.price):
        return []

    # Shoulder symmetry
    if abs(rs_h.price - ls_h.price) / ls_h.price > params.shoulder_sym_pct:
        return []

    # Time symmetry
    left_span = head_h.bar_index - ls_h.bar_index
    right_span = rs_h.bar_index - head_h.bar_index
    if min(left_span, right_span) == 0:
        return []
    if max(left_span, right_span) / min(left_span, right_span) > params.time_sym_ratio:
        return []

    # False positive guard: rs_low should not be below ls_low (V-shape rejection)
    if rs_l.price < ls_l.price:
        return []

    # Neckline: line from (ls_l.bar_index, ls_l.price) to (rs_l.bar_index, rs_l.price)
    neck_pts = [(ls_l.bar_index, ls_l.price), (rs_l.bar_index, rs_l.price)]
    m_neck, b_neck, _, _ = _trendline_fit(neck_pts)
    neckline_slope = m_neck

    # Reject steeply downward neckline
    if neckline_slope < params.neckline_slope_limit:
        return []

    # Neckline at current (last) bar
    last_idx = len(bars) - 1
    neckline_at_last = m_neck * last_idx + b_neck

    # Neckline price for output (at right shoulder trough)
    neckline_price = rs_l.price

    zone_low = min(ls_l.price, rs_l.price)
    zone_high = head_h.price

    # Symmetry score
    sym_ratio = abs(rs_h.price - ls_h.price) / ls_h.price / params.shoulder_sym_pct
    sym_score = max(0.0, 1.0 - sym_ratio)
    strength = max(0.0, min(1.0, sym_score))

    # Target: neckline_at_breakout - (head - neckline_at_head)
    neckline_at_head = m_neck * head_h.bar_index + b_neck
    # Use neckline at right shoulder as reference breakout
    target_price = neckline_price - (head_h.price - neckline_at_head)

    # Breakout: last bar close below neckline at current bar
    ts_breakout = None
    vol_ratio_at_breakout = None
    last_bar = bars[-1]
    if last_bar.close < neckline_at_last:
        ts_breakout = last_bar.ts
        mean_vol = _mean_volume(bars, last_idx)
        vol_ratio_at_breakout = last_bar.volume / mean_vol if mean_vol > 0 else None

    pattern = ChartPattern(
        pattern_type="head_and_shoulders",
        direction="bearish",
        ts_start=ls_h.ts,
        ts_end=rs_h.ts,
        ts_breakout=ts_breakout,
        zone_low=zone_low,
        zone_high=zone_high,
        neckline_price=neckline_price,
        target_price=target_price,
        pivot_sequence=[
            (ls_h.ts, ls_h.price, "H"),
            (ls_l.ts, ls_l.price, "L"),
            (head_h.ts, head_h.price, "H"),
            (rs_l.ts, rs_l.price, "L"),
            (rs_h.ts, rs_h.price, "H"),
        ],
        strength=strength,
        trend_context=trend_context,
        volume_ratio_at_breakout=vol_ratio_at_breakout,
        _peak_prices=[ls_h.price, head_h.price, rs_h.price],
    )
    return [pattern]


# ---------------------------------------------------------------------------
# STEP 6 — Inverse Head and Shoulders (bottom)
# ---------------------------------------------------------------------------


def _detect_inverse_head_and_shoulders(
    bars: list[PriceBar],
    pivots: list[SwingPivot],
    trend_context: str,
    params: ChartPatternParams,
) -> list[ChartPattern]:
    if len(pivots) < 5:
        return []

    candidates = pivots[-5:]
    ls_l, ls_h, head_l, rs_h, rs_l = candidates

    # Must alternate L/H/L/H/L
    if not (
        ls_l.direction == "L"
        and ls_h.direction == "H"
        and head_l.direction == "L"
        and rs_h.direction == "H"
        and rs_l.direction == "L"
    ):
        return []

    # Head is lowest
    if not (head_l.price < ls_l.price and head_l.price < rs_l.price):
        return []

    # Shoulder symmetry
    if abs(rs_l.price - ls_l.price) / ls_l.price > params.shoulder_sym_pct:
        return []

    # Time symmetry
    left_span = head_l.bar_index - ls_l.bar_index
    right_span = rs_l.bar_index - head_l.bar_index
    if min(left_span, right_span) == 0:
        return []
    if max(left_span, right_span) / min(left_span, right_span) > params.time_sym_ratio:
        return []

    # Neckline: (ls_h, rs_h)
    neck_pts = [(ls_h.bar_index, ls_h.price), (rs_h.bar_index, rs_h.price)]
    m_neck, b_neck, _, _ = _trendline_fit(neck_pts)

    # Reject steeply downward neckline (same guard)
    if m_neck < params.neckline_slope_limit:
        return []

    last_idx = len(bars) - 1
    neckline_at_last = m_neck * last_idx + b_neck
    neckline_price = rs_h.price

    neckline_at_head = m_neck * head_l.bar_index + b_neck
    target_price = neckline_price + (neckline_at_head - head_l.price)

    zone_low = head_l.price
    zone_high = max(ls_h.price, rs_h.price)

    sym_ratio = abs(rs_l.price - ls_l.price) / ls_l.price / params.shoulder_sym_pct
    sym_score = max(0.0, 1.0 - sym_ratio)
    strength = max(0.0, min(1.0, sym_score))

    ts_breakout = None
    vol_ratio_at_breakout = None
    last_bar = bars[-1]
    if last_bar.close > neckline_at_last:
        ts_breakout = last_bar.ts
        mean_vol = _mean_volume(bars, last_idx)
        vol_ratio_at_breakout = last_bar.volume / mean_vol if mean_vol > 0 else None

    pattern = ChartPattern(
        pattern_type="inverse_head_and_shoulders",
        direction="bullish",
        ts_start=ls_l.ts,
        ts_end=rs_l.ts,
        ts_breakout=ts_breakout,
        zone_low=zone_low,
        zone_high=zone_high,
        neckline_price=neckline_price,
        target_price=target_price,
        pivot_sequence=[
            (ls_l.ts, ls_l.price, "L"),
            (ls_h.ts, ls_h.price, "H"),
            (head_l.ts, head_l.price, "L"),
            (rs_h.ts, rs_h.price, "H"),
            (rs_l.ts, rs_l.price, "L"),
        ],
        strength=strength,
        trend_context=trend_context,
        volume_ratio_at_breakout=vol_ratio_at_breakout,
        _peak_prices=[ls_l.price, head_l.price, rs_l.price],
    )
    return [pattern]


# ---------------------------------------------------------------------------
# STEP 8 — Triangles
# ---------------------------------------------------------------------------


def _detect_triangles(
    bars: list[PriceBar],
    pivots: list[SwingPivot],
    trend_context: str,
    params: ChartPatternParams,
) -> list[ChartPattern]:
    # Take last triangle_lookback_pivots alternating confirmed pivots
    used = pivots[-params.triangle_lookback_pivots :]
    highs_pts = [(p.bar_index, p.price) for p in used if p.direction == "H"]
    lows_pts = [(p.bar_index, p.price) for p in used if p.direction == "L"]

    if len(highs_pts) < 2 or len(lows_pts) < 2:
        return []

    m_top, b_top, r2_top, res_top = _trendline_fit(highs_pts)
    m_bot, b_bot, r2_bot, res_bot = _trendline_fit(lows_pts)

    mean_price = statistics.mean([p for _, p in highs_pts + lows_pts])

    # Fit tolerance for 3+ touches
    if not _passes_fit_tolerance(
        r2_top, res_top, mean_price, len(highs_pts), params.trendline_fit_tolerance
    ):
        return []
    if not _passes_fit_tolerance(
        r2_bot, res_bot, mean_price, len(lows_pts), params.trendline_fit_tolerance
    ):
        return []

    # Apex
    denom = m_top - m_bot
    if denom == 0:
        return []
    apex_x = (b_bot - b_top) / denom
    current_idx = len(bars) - 1

    if apex_x <= current_idx:
        return []  # Lines already crossed — pattern expired

    # Pattern start = first pivot in used
    if not used:
        return []
    start_idx = used[0].bar_index
    pattern_length = apex_x - start_idx
    if pattern_length <= 0:
        return []

    # Breakout timing check (current bar relative to apex)
    # Only classify — actual breakout detection is in check_breakout
    pct_to_apex = (current_idx - start_idx) / pattern_length

    # Volume contraction during formation
    used_bars = bars[start_idx : current_idx + 1]
    vol_slope = _vol_regression_slope(used_bars) if len(used_bars) >= 3 else 0.0

    bar_range_span = current_idx - start_idx if current_idx > start_idx else 1
    price_rng = _price_range(bars[start_idx : current_idx + 1]) if bars[start_idx:] else 1.0

    def is_flat(slope: float) -> bool:
        return abs(slope) / (price_rng / bar_range_span) < params.flat_slope_threshold

    # Classify triangle type
    if is_flat(m_top) and m_bot > 0:
        tri_type = "ascending_triangle"
        direction = "bullish"
    elif is_flat(m_bot) and m_top < 0:
        tri_type = "descending_triangle"
        direction = "bearish"
    elif m_top < 0 and m_bot > 0:
        # Check symmetry for symmetrical triangle
        if abs(abs(m_top) / abs(m_bot) - 1.0) < 0.5:
            tri_type = "symmetrical_triangle"
            direction = "neutral"
        else:
            tri_type = "symmetrical_triangle"
            direction = "neutral"
    else:
        return []

    base_height = max(p for _, p in highs_pts) - min(p for _, p in lows_pts)

    # Strength: r_squared mean + volume contraction bonus
    r2_mean = (r2_top + r2_bot) / 2.0
    vol_score = 0.7 if vol_slope < 0 else 0.5
    if vol_slope > 0:
        vol_score = 0.3
    strength = max(0.0, min(1.0, r2_mean * 0.7 + vol_score * 0.3))

    # Breakout detection
    ts_breakout = None
    vol_ratio_at_breakout = None
    last_bar = bars[-1]
    top_val = m_top * current_idx + b_top
    bot_val = m_bot * current_idx + b_bot
    breakout_dir: str | None = None

    if last_bar.close > top_val:
        breakout_dir = "bullish"
    elif last_bar.close < bot_val:
        breakout_dir = "bearish"

    if breakout_dir and params.breakout_pct_apex_min <= pct_to_apex <= params.breakout_pct_apex_max:
        ts_breakout = last_bar.ts
        mean_vol = _mean_volume(bars, current_idx)
        vol_ratio_at_breakout = last_bar.volume / mean_vol if mean_vol > 0 else None
        if tri_type == "symmetrical_triangle":
            direction = breakout_dir

    zone_low = min(p for _, p in lows_pts)
    zone_high = max(p for _, p in highs_pts)

    ts_start = used[0].ts
    ts_end = used[-1].ts

    target_price: float | None = None
    if ts_breakout:
        bp = last_bar.close
        target_price = bp + base_height if breakout_dir == "bullish" else bp - base_height

    pattern = ChartPattern(
        pattern_type=tri_type,
        direction=direction,
        ts_start=ts_start,
        ts_end=ts_end,
        ts_breakout=ts_breakout,
        zone_low=zone_low,
        zone_high=zone_high,
        neckline_price=None,
        target_price=target_price,
        pivot_sequence=[(p.ts, p.price, p.direction) for p in used],
        strength=strength,
        trend_context=trend_context,
        trendline_r_squared_top=r2_top,
        trendline_r_squared_bot=r2_bot,
        apex_bar_index=int(apex_x),
        volume_ratio_at_breakout=vol_ratio_at_breakout,
    )
    return [pattern]


# ---------------------------------------------------------------------------
# STEP 9 — Wedges
# ---------------------------------------------------------------------------


def _detect_wedges(
    bars: list[PriceBar],
    pivots: list[SwingPivot],
    trend_context: str,
    params: ChartPatternParams,
) -> list[ChartPattern]:
    used = pivots[-params.triangle_lookback_pivots :]
    highs_pts = [(p.bar_index, p.price) for p in used if p.direction == "H"]
    lows_pts = [(p.bar_index, p.price) for p in used if p.direction == "L"]

    # Wedge needs 3+ touches per line
    if len(highs_pts) < 3 or len(lows_pts) < 3:
        return []

    m_top, b_top, r2_top, res_top = _trendline_fit(highs_pts)
    m_bot, b_bot, r2_bot, res_bot = _trendline_fit(lows_pts)

    mean_price = statistics.mean([p for _, p in highs_pts + lows_pts])

    # Quality gate (3+ touches)
    if not _passes_fit_tolerance(
        r2_top, res_top, mean_price, len(highs_pts), params.trendline_fit_tolerance
    ):
        return []
    if not _passes_fit_tolerance(
        r2_bot, res_bot, mean_price, len(lows_pts), params.trendline_fit_tolerance
    ):
        return []

    # Both slopes same sign and converging
    rising_wedge = m_top > 0 and m_bot > 0 and m_bot > m_top
    falling_wedge = m_top < 0 and m_bot < 0 and m_top < m_bot

    if not (rising_wedge or falling_wedge):
        return []

    # Apex
    denom = m_top - m_bot
    if denom == 0:
        return []
    apex_x = (b_bot - b_top) / denom
    current_idx = len(bars) - 1

    if apex_x <= current_idx:
        return []

    # Check no bar body escaped price_buffer during formation
    if used:
        start_idx = used[0].bar_index
        current_price = bars[current_idx].close
        buffer = params.price_buffer * current_price
        for j in range(start_idx, current_idx + 1):
            top_val = m_top * j + b_top
            bot_val = m_bot * j + b_bot
            bar = bars[j]
            if bar.close > top_val + buffer or bar.close < bot_val - buffer:
                return []  # Pattern invalidated

    # Wedge width at pattern start
    start_idx = used[0].bar_index
    wedge_width = abs((m_top * start_idx + b_top) - (m_bot * start_idx + b_bot))

    direction = "bearish" if rising_wedge else "bullish"
    wedge_type = "rising_wedge" if rising_wedge else "falling_wedge"

    r2_mean = (r2_top + r2_bot) / 2.0
    strength = max(0.0, min(1.0, r2_mean))

    # Breakout detection
    ts_breakout = None
    vol_ratio_at_breakout = None
    last_bar = bars[-1]
    top_val_last = m_top * current_idx + b_top
    bot_val_last = m_bot * current_idx + b_bot

    breakout_triggered = (rising_wedge and last_bar.close < bot_val_last) or (
        falling_wedge and last_bar.close > top_val_last
    )

    if breakout_triggered:
        ts_breakout = last_bar.ts
        mean_vol = _mean_volume(bars, current_idx)
        vol_ratio_at_breakout = last_bar.volume / mean_vol if mean_vol > 0 else None

    target_price: float | None = None
    if ts_breakout:
        bp = last_bar.close
        target_price = bp + wedge_width if direction == "bullish" else bp - wedge_width

    zone_low = min(p for _, p in lows_pts)
    zone_high = max(p for _, p in highs_pts)
    ts_start = used[0].ts
    ts_end = used[-1].ts

    pattern = ChartPattern(
        pattern_type=wedge_type,
        direction=direction,
        ts_start=ts_start,
        ts_end=ts_end,
        ts_breakout=ts_breakout,
        zone_low=zone_low,
        zone_high=zone_high,
        neckline_price=None,
        target_price=target_price,
        pivot_sequence=[(p.ts, p.price, p.direction) for p in used],
        strength=strength,
        trend_context=trend_context,
        trendline_r_squared_top=r2_top,
        trendline_r_squared_bot=r2_bot,
        apex_bar_index=int(apex_x),
        volume_ratio_at_breakout=vol_ratio_at_breakout,
    )
    return [pattern]


# ---------------------------------------------------------------------------
# STEP 10 — Flag / Pennant
# ---------------------------------------------------------------------------


def _detect_flag_pennant(
    bars: list[PriceBar],
    pivots: list[SwingPivot],
    trend_context: str,
    params: ChartPatternParams,
) -> list[ChartPattern]:
    n = len(bars)
    if n < params.pole_min_bars + params.flag_min_bars + 2:
        return []

    current_idx = n - 1

    # Detect pole: search for a recent directional run
    # Scan backwards from current bar for a pole ending before flag consolidation
    max_flag = params.flag_max_bars
    min_flag = params.flag_min_bars

    results: list[ChartPattern] = []

    for flag_end in range(current_idx, current_idx + 1):
        # Flag body is the last min_flag..max_flag bars
        for flag_len in range(min_flag, min(max_flag + 1, current_idx)):
            flag_start = flag_end - flag_len + 1
            if flag_start < params.pole_min_bars:
                break
            pole_end = flag_start - 1

            # Try to find pole ending at pole_end
            for pole_len in range(params.pole_min_bars, min(pole_end + 1, 30)):
                pole_start = pole_end - pole_len + 1
                if pole_start < 0:
                    break

                pole_bars_slice = bars[pole_start : pole_end + 1]
                pole_range = pole_bars_slice[-1].close - pole_bars_slice[0].close
                pole_pct = (
                    abs(pole_range) / pole_bars_slice[0].close
                    if pole_bars_slice[0].close > 0
                    else 0
                )

                if pole_pct < params.pole_min_pct:
                    continue

                # Bullish pole: close increases; bearish pole: close decreases
                bullish_pole = pole_range > 0

                # Flag consolidation bars
                flag_bars_slice = bars[flag_start : flag_end + 1]

                # Fit trendlines to flag highs and lows
                flag_high_pts = [(j, bars[j].high) for j in range(flag_start, flag_end + 1)]
                flag_low_pts = [(j, bars[j].low) for j in range(flag_start, flag_end + 1)]

                if len(flag_high_pts) < 2 or len(flag_low_pts) < 2:
                    continue

                mh, bh, r2h, _ = _trendline_fit(flag_high_pts)
                ml, bl, r2l, _ = _trendline_fit(flag_low_pts)

                # Volume contraction during flag
                vol_slope = _vol_regression_slope(flag_bars_slice)
                if vol_slope >= 0:
                    continue  # Volume must contract

                # Retracement check
                abs_pole = abs(pole_range)
                flag_high = max(b.high for b in flag_bars_slice)
                flag_low = min(b.low for b in flag_bars_slice)
                flag_body_range = flag_high - flag_low
                if flag_body_range > abs_pole * params.flag_retracement_max:
                    continue

                # Flag vs Pennant classification
                parallel = abs(mh - ml) / (abs(max(mh, ml, key=abs)) + 1e-9) < 0.3
                converging = abs(mh + ml) < 0.2 * (abs(mh) + abs(ml) + 1e-9)
                is_flag = parallel and (
                    (bullish_pole and mh < 0 and ml < 0) or (not bullish_pole and mh > 0 and ml > 0)
                )
                is_pennant = converging and not parallel

                if not (is_flag or is_pennant):
                    continue

                pattern_type = "flag" if is_flag else "pennant"
                direction = "bullish" if bullish_pole else "bearish"

                # Apex for pennant
                apex_bar_index: int | None = None
                if is_pennant and (mh - ml) != 0:
                    apex_x = (bl - bh) / (mh - ml)
                    apex_bar_index = int(apex_x)

                # Breakout detection
                ts_breakout = None
                vol_ratio_at_breakout = None
                last_bar = bars[current_idx]
                top_val = mh * current_idx + bh
                bot_val = ml * current_idx + bl
                mean_vol = _mean_volume(bars, current_idx)

                broke_bull = bullish_pole and last_bar.close > top_val
                broke_bear = (not bullish_pole) and last_bar.close < bot_val
                if (
                    broke_bull or broke_bear
                ) and last_bar.volume >= params.breakout_vol_ratio * mean_vol:
                    ts_breakout = last_bar.ts
                    vol_ratio_at_breakout = last_bar.volume / mean_vol

                r2_mean = (r2h + r2l) / 2.0
                vol_score = 0.8  # already passed vol contraction
                strength = max(0.0, min(1.0, r2_mean * 0.7 + vol_score * 0.3))

                target_price: float | None = None
                if ts_breakout:
                    target_price = last_bar.close + (abs_pole if bullish_pole else -abs_pole)

                pattern = ChartPattern(
                    pattern_type=pattern_type,
                    direction=direction,
                    ts_start=bars[pole_start].ts,
                    ts_end=bars[flag_end].ts,
                    ts_breakout=ts_breakout,
                    zone_low=flag_low,
                    zone_high=flag_high,
                    neckline_price=None,
                    target_price=target_price,
                    pivot_sequence=[],
                    strength=strength,
                    trend_context=trend_context,
                    trendline_r_squared_top=r2h,
                    trendline_r_squared_bot=r2l,
                    pole_range=pole_range,
                    apex_bar_index=apex_bar_index,
                    volume_ratio_at_breakout=vol_ratio_at_breakout,
                )
                results.append(pattern)
                # Only report first valid flag found
                return results

    return results


# ---------------------------------------------------------------------------
# STEP 11 — Rectangle / Channel
# ---------------------------------------------------------------------------


def _detect_rectangle(
    bars: list[PriceBar],
    pivots: list[SwingPivot],
    trend_context: str,
    params: ChartPatternParams,
) -> list[ChartPattern]:
    used = pivots[-params.channel_lookback_pivots :]
    highs_pts = [(p.bar_index, p.price) for p in used if p.direction == "H"]
    lows_pts = [(p.bar_index, p.price) for p in used if p.direction == "L"]

    if len(highs_pts) < 2 or len(lows_pts) < 2:
        return []

    m_top, b_top, r2_top, res_top = _trendline_fit(highs_pts)
    m_bot, b_bot, r2_bot, res_bot = _trendline_fit(lows_pts)

    if not used:
        return []
    start_idx = used[0].bar_index
    current_idx = len(bars) - 1
    bar_range_span = max(current_idx - start_idx, 1)
    price_rng = _price_range(bars[start_idx : current_idx + 1]) if bars[start_idx:] else 1.0

    def is_flat(slope: float) -> bool:
        return abs(slope) / (price_rng / bar_range_span) < params.flat_slope_threshold

    if not (is_flat(m_top) and is_flat(m_bot)):
        return []

    # Pattern span
    span = used[-1].bar_index - used[0].bar_index
    if span < params.min_pattern_bars:
        return []

    mean_price = statistics.mean([p for _, p in highs_pts + lows_pts])
    if not _passes_fit_tolerance(
        r2_top, res_top, mean_price, len(highs_pts), params.trendline_fit_tolerance
    ):
        return []
    if not _passes_fit_tolerance(
        r2_bot, res_bot, mean_price, len(lows_pts), params.trendline_fit_tolerance
    ):
        return []

    mean_top = statistics.mean(p for _, p in highs_pts)
    mean_bot = statistics.mean(p for _, p in lows_pts)
    channel_height = mean_top - mean_bot

    last_bar = bars[-1]
    buffer = 0.003 * last_bar.close
    ts_breakout = None
    vol_ratio_at_breakout = None
    direction = "neutral"

    if last_bar.close > mean_top + buffer:
        ts_breakout = last_bar.ts
        direction = "bullish"
        mean_vol = _mean_volume(bars, current_idx)
        vol_ratio_at_breakout = last_bar.volume / mean_vol if mean_vol > 0 else None
    elif last_bar.close < mean_bot - buffer:
        ts_breakout = last_bar.ts
        direction = "bearish"
        mean_vol = _mean_volume(bars, current_idx)
        vol_ratio_at_breakout = last_bar.volume / mean_vol if mean_vol > 0 else None

    r2_mean = (r2_top + r2_bot) / 2.0
    strength = max(0.0, min(1.0, r2_mean))

    target_price: float | None = None
    if ts_breakout:
        bp = last_bar.close
        target_price = bp + channel_height if direction == "bullish" else bp - channel_height

    zone_low = mean_bot
    zone_high = mean_top
    ts_start = used[0].ts
    ts_end = used[-1].ts

    pattern = ChartPattern(
        pattern_type="rectangle",
        direction=direction,
        ts_start=ts_start,
        ts_end=ts_end,
        ts_breakout=ts_breakout,
        zone_low=zone_low,
        zone_high=zone_high,
        neckline_price=None,
        target_price=target_price,
        pivot_sequence=[(p.ts, p.price, p.direction) for p in used],
        strength=strength,
        trend_context=trend_context,
        trendline_r_squared_top=r2_top,
        trendline_r_squared_bot=r2_bot,
        volume_ratio_at_breakout=vol_ratio_at_breakout,
    )
    return [pattern]


# ---------------------------------------------------------------------------
# STEP 12 — Cup and Handle
# ---------------------------------------------------------------------------


def _detect_cup_and_handle(
    bars: list[PriceBar],
    pivots: list[SwingPivot],
    trend_context: str,
    params: ChartPatternParams,
) -> list[ChartPattern]:
    if trend_context != "uptrend":
        return []

    n = len(bars)
    if n < params.cup_lookback_bars // 4:
        return []

    highs = [p for p in pivots if p.direction == "H"]
    lows = [p for p in pivots if p.direction == "L"]

    if len(highs) < 2 or len(lows) < 1:
        return []

    # Window start
    window_start = max(0, n - params.cup_lookback_bars)

    # Left rim = last SwingHigh before cup start region
    left_rim_candidates = [h for h in highs if h.bar_index >= window_start]
    if len(left_rim_candidates) < 2:
        return []

    left_rim = left_rim_candidates[0]

    # Cup bottom = lowest SwingLow after left rim
    cup_bottom_candidates = [lv for lv in lows if lv.bar_index > left_rim.bar_index]
    if not cup_bottom_candidates:
        return []
    cup_bottom = min(cup_bottom_candidates, key=lambda p: p.price)

    # Right rim = next SwingHigh after cup bottom, near left rim price
    right_rim_candidates = [
        h
        for h in highs
        if h.bar_index > cup_bottom.bar_index
        and h.price >= left_rim.price * (1.0 - params.cup_rim_tolerance)
        and h.price <= left_rim.price * (1.0 + params.cup_rim_tolerance)
    ]
    if not right_rim_candidates:
        return []
    right_rim = right_rim_candidates[0]

    cup_depth = left_rim.price - cup_bottom.price
    if left_rim.price <= 0:
        return []
    depth_ratio = cup_depth / left_rim.price

    if not (params.cup_depth_min <= depth_ratio <= params.cup_depth_max):
        return []

    # U-shape check: at least 30% of cup bars near bottom
    cup_bars = bars[left_rim.bar_index : right_rim.bar_index + 1]
    if not cup_bars:
        return []
    bottom_threshold = cup_bottom.price + 0.1 * cup_depth
    near_bottom = sum(1 for b in cup_bars if b.close < bottom_threshold)
    if near_bottom / len(cup_bars) < 0.30:
        return []  # V-shape rejection

    # Handle = bars after right rim
    current_idx = n - 1
    handle_bars = bars[right_rim.bar_index : current_idx + 1]
    if not handle_bars:
        return []

    # Handle must be shorter than cup
    cup_bar_count = right_rim.bar_index - left_rim.bar_index
    handle_bar_count = len(handle_bars)
    if handle_bar_count >= cup_bar_count * 0.5:
        return []

    handle_lows = [lv for lv in lows if lv.bar_index > right_rim.bar_index]
    if not handle_lows:
        handle_low_price = min(b.low for b in handle_bars)
    else:
        handle_low_price = min(lv.price for lv in handle_lows)

    handle_depth = right_rim.price - handle_low_price
    if handle_depth > cup_depth * 0.50:
        return []  # Handle too deep
    if handle_low_price <= cup_bottom.price + cup_depth * 0.33:
        return []  # Handle low too deep in cup

    # Breakout: close > right_rim + volume confirmation
    ts_breakout = None
    vol_ratio_at_breakout = None
    last_bar = bars[current_idx]

    if last_bar.close > right_rim.price:
        mean_vol = _mean_volume(bars, current_idx, 10)
        vr = last_bar.volume / mean_vol if mean_vol > 0 else 0.0
        if vr >= 1.4:  # cup-and-handle uses 1.4x
            ts_breakout = last_bar.ts
            vol_ratio_at_breakout = vr

    target_price = right_rim.price + cup_depth
    zone_low = cup_bottom.price
    zone_high = right_rim.price

    strength = 0.7  # base strength for a confirmed cup structure
    if ts_breakout:
        strength = min(1.0, strength + 0.2)

    pattern = ChartPattern(
        pattern_type="cup_and_handle",
        direction="bullish",
        ts_start=left_rim.ts,
        ts_end=bars[current_idx].ts,
        ts_breakout=ts_breakout,
        zone_low=zone_low,
        zone_high=zone_high,
        neckline_price=right_rim.price,
        target_price=target_price,
        pivot_sequence=[
            (left_rim.ts, left_rim.price, "H"),
            (cup_bottom.ts, cup_bottom.price, "L"),
            (right_rim.ts, right_rim.price, "H"),
        ],
        strength=strength,
        trend_context=trend_context,
        volume_ratio_at_breakout=vol_ratio_at_breakout,
        _peak_prices=[left_rim.price, cup_bottom.price, right_rim.price],
    )
    return [pattern]


# ---------------------------------------------------------------------------
# STEP 13 — Mitigation / expiry update
# ---------------------------------------------------------------------------


def _update_mitigation(pattern: ChartPattern, bars: list[PriceBar]) -> ChartPattern:
    """Return a copy of the pattern with mitigated/ts_mitigated updated."""
    if pattern.mitigated:
        return pattern

    mitigated = False
    ts_mit: date | datetime | None = None

    # STEP 13: mitigation is only evaluated on bars that close AFTER the pattern
    # has finished forming.  A bar on or before ``ts_end`` predates the structure
    # and must never retroactively invalidate it (that would be a look-into-the-past
    # bug that corrupts the AVOID signal).  Anchor to the breakout bar when present,
    # otherwise to the formation end.
    mitigation_anchor = pattern.ts_breakout if pattern.ts_breakout is not None else pattern.ts_end

    if pattern.pattern_type == "double_top" and pattern._peak_prices:
        peak_max = max(pattern._peak_prices)
        tol = peak_max * 0.03
        for bar in bars:
            if bar.ts <= mitigation_anchor:
                continue
            if bar.close > peak_max + tol:
                mitigated = True
                ts_mit = bar.ts
                break

    elif pattern.pattern_type == "double_bottom" and pattern._peak_prices:
        trough_min = min(pattern._peak_prices)
        tol = trough_min * 0.03
        for bar in bars:
            if bar.ts <= mitigation_anchor:
                continue
            if bar.close < trough_min - tol:
                mitigated = True
                ts_mit = bar.ts
                break

    elif pattern.apex_bar_index is not None:
        # Triangles/wedges expire when apex bar index is reached by the series
        current_max_idx = len(bars) - 1
        if pattern.apex_bar_index <= current_max_idx:
            mitigated = True
            ts_mit = (
                bars[pattern.apex_bar_index].ts
                if pattern.apex_bar_index < len(bars)
                else bars[-1].ts
            )

    if mitigated:
        # Return updated — dataclass is frozen so we create a new one
        return ChartPattern(
            pattern_type=pattern.pattern_type,
            direction=pattern.direction,
            ts_start=pattern.ts_start,
            ts_end=pattern.ts_end,
            ts_breakout=pattern.ts_breakout,
            zone_low=pattern.zone_low,
            zone_high=pattern.zone_high,
            neckline_price=pattern.neckline_price,
            target_price=pattern.target_price,
            pivot_sequence=pattern.pivot_sequence,
            strength=pattern.strength,
            mitigated=True,
            ts_mitigated=ts_mit,
            trend_context=pattern.trend_context,
            trendline_r_squared_top=pattern.trendline_r_squared_top,
            trendline_r_squared_bot=pattern.trendline_r_squared_bot,
            pole_range=pattern.pole_range,
            volume_ratio_at_breakout=pattern.volume_ratio_at_breakout,
            apex_bar_index=pattern.apex_bar_index,
            _peak_prices=pattern._peak_prices,
        )
    return pattern


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_chart_patterns(
    bars: list[PriceBar],
    params: ChartPatternParams | None = None,
) -> list[ChartPattern]:
    """Run all chart pattern detectors over the bar series.

    Only confirmed pivots (i+swing_lookback has closed) are used — no lookahead.

    Parameters
    ----------
    bars:
        Price bars in ascending chronological order (oldest first).
    params:
        Tuning parameters; uses spec defaults if not supplied.

    Returns
    -------
    list[ChartPattern]
        All detected (and possibly already-broken-out) patterns at the last bar.
    """
    if params is None:
        params = _DEFAULT_PARAMS

    pivots = _detect_swing_pivots(bars, params.swing_lookback)
    if not pivots:
        return []

    trend_context = _classify_trend(pivots)

    all_patterns: list[ChartPattern] = []
    all_patterns.extend(_detect_double_top(bars, pivots, trend_context, params))
    all_patterns.extend(_detect_double_bottom(bars, pivots, trend_context, params))
    all_patterns.extend(_detect_head_and_shoulders(bars, pivots, trend_context, params))
    all_patterns.extend(_detect_inverse_head_and_shoulders(bars, pivots, trend_context, params))
    all_patterns.extend(_detect_triangles(bars, pivots, trend_context, params))
    all_patterns.extend(_detect_wedges(bars, pivots, trend_context, params))
    all_patterns.extend(_detect_flag_pennant(bars, pivots, trend_context, params))
    all_patterns.extend(_detect_rectangle(bars, pivots, trend_context, params))
    all_patterns.extend(_detect_cup_and_handle(bars, pivots, trend_context, params))

    # Apply mitigation checks
    all_patterns = [_update_mitigation(p, bars) for p in all_patterns]

    return all_patterns


def score_pattern_strength(
    pattern: ChartPattern,
    volume_ratio: float,
) -> float:
    """Composite 0–1 strength score blending structural quality and volume.

    Blends the pattern's existing ``strength`` (r_squared/symmetry component)
    with the supplied ``volume_ratio`` (breakout bar volume / mean volume).
    """
    vol_component = min(1.0, volume_ratio / 3.0)  # 3× avg → full score
    return max(0.0, min(1.0, pattern.strength * 0.6 + vol_component * 0.4))


def check_breakout(
    pattern: ChartPattern,
    bar: PriceBar,
    volume_ratio: float,
    params: ChartPatternParams | None = None,
) -> dict[str, Any]:
    """Check whether *bar* constitutes a valid breakout for *pattern*.

    Returns a dict with keys:
      - ``breakout``: bool
      - ``direction``: 'bullish' | 'bearish' | None
      - ``volume_confirmed``: bool
      - ``avoid``: bool (strength < 0.4 or pattern mitigated)
    """
    if params is None:
        params = _DEFAULT_PARAMS

    if pattern.mitigated:
        return {"breakout": False, "direction": None, "volume_confirmed": False, "avoid": True}

    if pattern.strength < 0.4:
        return {
            "breakout": False,
            "direction": None,
            "volume_confirmed": False,
            "avoid": True,
        }

    neckline = pattern.neckline_price
    vol_req = params.breakout_vol_ratio
    vol_confirmed = volume_ratio >= vol_req

    breakout = False
    direction: str | None = None

    if neckline is not None:
        if pattern.direction == "bearish" and bar.close < neckline:
            breakout = True
            direction = "bearish"
        elif pattern.direction == "bullish" and bar.close > neckline:
            breakout = True
            direction = "bullish"
    else:
        # Triangle/wedge/flag: check zone boundaries
        if bar.close > pattern.zone_high:
            breakout = True
            direction = "bullish"
        elif bar.close < pattern.zone_low:
            breakout = True
            direction = "bearish"

    avoid = not vol_confirmed or not breakout
    return {
        "breakout": breakout,
        "direction": direction,
        "volume_confirmed": vol_confirmed,
        "avoid": avoid,
    }


def classify_pattern_direction(pattern: ChartPattern) -> str:
    """Return the confluence direction string for this pattern.

    Returns 'bullish', 'bearish', or 'neutral'.
    Downgrades to 'neutral' if strength < 0.4 or pattern is mitigated.
    """
    if pattern.mitigated or pattern.strength < 0.4:
        return "neutral"
    return pattern.direction


def detect_pullback_retest(
    pattern: ChartPattern,
    bars: list[PriceBar],
    atr: float,
) -> PullbackRetest:
    """Detect a pullback retest of the neckline / breakout boundary.

    A pullback retest is identified when, after the breakout bar, price
    returns to the neckline (or zone boundary) and the *following* bar
    closes back in the breakout direction.

    Parameters
    ----------
    pattern:
        A pattern with ``ts_breakout`` already set.
    bars:
        Full bar series (ascending chronological order).
    atr:
        ATR(14) value at the current bar, used for risk level calculation.

    Returns
    -------
    PullbackRetest
        ``detected=True`` if a pullback retest is found, with bar index and
        risk levels filled in.
    """
    if pattern.ts_breakout is None:
        return PullbackRetest(detected=False)

    # Find breakout bar index
    bo_idx: int | None = None
    for i, bar in enumerate(bars):
        if bar.ts == pattern.ts_breakout:
            bo_idx = i
            break
    if bo_idx is None or bo_idx + 2 >= len(bars):
        return PullbackRetest(detected=False)

    neckline = pattern.neckline_price
    ref_level = (
        neckline
        if neckline is not None
        else (pattern.zone_high if pattern.direction == "bullish" else pattern.zone_low)
    )

    # Look for pullback after breakout
    for i in range(bo_idx + 1, len(bars) - 1):
        bar = bars[i]
        next_bar = bars[i + 1]

        if pattern.direction == "bullish":
            # Price pulls back to neckline level
            near_neckline = bar.low <= ref_level * 1.02
            # Next bar closes back above
            resumes = next_bar.close > ref_level
            if near_neckline and resumes:
                return PullbackRetest(
                    detected=True,
                    bar_index=i + 1,
                    ts=next_bar.ts,
                    entry_tag="pullback_retest",
                    risk_low=ref_level - atr,
                    risk_high=None,
                )
        else:  # bearish
            near_neckline = bar.high >= ref_level * 0.98
            resumes = next_bar.close < ref_level
            if near_neckline and resumes:
                return PullbackRetest(
                    detected=True,
                    bar_index=i + 1,
                    ts=next_bar.ts,
                    entry_tag="pullback_retest",
                    risk_low=None,
                    risk_high=ref_level + atr,
                )

    return PullbackRetest(detected=False)
