"""Tests for engine/chart/patterns.py — classical chart pattern detector.

Fixtures are hand-crafted synthetic PriceBar sequences that contain (or
deliberately do NOT contain) the target pattern.  Every assertion references
only bars that have already "closed" relative to the detector's position in
the series — no lookahead.
"""

from __future__ import annotations

from datetime import date, timedelta

from data.models import PriceBar
from engine.chart.patterns import (
    ChartPattern,
    ChartPatternParams,
    _classify_trend,
    _detect_swing_pivots,
    _update_mitigation,
    check_breakout,
    classify_pattern_direction,
    detect_chart_patterns,
    detect_pullback_retest,
    score_pattern_strength,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_DATE = date(2025, 1, 1)


def _bar(
    idx: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float = 1000.0,
    freq: str = "1d",
) -> PriceBar:
    """Build a PriceBar at a date offset from _BASE_DATE."""
    return PriceBar(
        symbol="TEST",
        market="crypto",
        source_symbol="TEST/USDT",
        ts=_BASE_DATE + timedelta(days=idx),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        freq=freq,
    )


def _flat_bar(idx: int, price: float, volume: float = 1000.0) -> PriceBar:
    return _bar(idx, price, price + 1, price - 1, price, volume)


# ---------------------------------------------------------------------------
# Unit: swing pivot detection (no lookahead)
# ---------------------------------------------------------------------------


def test_swing_pivot_detection_basic() -> None:
    """Swing highs and lows are correctly identified with swing_lookback=2."""
    # Build 10 bars: rises to a peak at idx=4 then falls
    prices = [100, 102, 104, 106, 110, 108, 105, 103, 101, 99]
    bars = [_bar(i, p, p + 2, p - 2, p) for i, p in enumerate(prices)]
    pivots = _detect_swing_pivots(bars, swing_lookback=2)

    # Peak at idx=4 (high=112) should be confirmed as SwingHigh
    swing_highs = [p for p in pivots if p.direction == "H"]
    assert any(p.bar_index == 4 for p in swing_highs), f"Expected pivot H at idx=4, got {pivots}"


def test_swing_pivot_no_lookahead() -> None:
    """Pivots at the very last 'swing_lookback' bars must NOT be reported (not yet confirmed)."""
    # Peak at last bar — cannot be confirmed
    prices = [100, 102, 104, 106, 110]
    bars = [_bar(i, p, p + 2, p - 2, p) for i, p in enumerate(prices)]
    pivots = _detect_swing_pivots(bars, swing_lookback=2)

    # Bar index 4 is the last bar — cannot be confirmed with lookback=2 (needs idx+2=6 closed)
    # Confirmed range: [2, len-3] = [2, 2] only
    assert not any(p.bar_index >= 3 for p in pivots), (
        "Pivot at last-2 or later bars should not be confirmed"
    )


# ---------------------------------------------------------------------------
# Double Top: positive detection
# ---------------------------------------------------------------------------


def _make_double_top_bars() -> list[PriceBar]:
    """Hand-crafted double-top: three confirmed swing highs (H1=103, H2=112, H3=112).

    H2 is the left peak, H3 is the right peak (same price level → within tolerance).
    Neckline SwingLow sits between H2 and H3 at ~98.  Last bar closes below neckline.
    Needs swing_lookback=2 → 3 guard bars at the tail ensure all pivots are confirmed.
    """
    bars: list[PriceBar] = []
    # Pre-pattern: gentle rise establishing H1 at idx=2
    bars.append(_bar(0, 95, 97, 94, 96, volume=1000.0))
    bars.append(_bar(1, 96, 99, 95, 98, volume=1000.0))
    bars.append(_bar(2, 98, 103, 97, 102, volume=1200.0))  # H1 (high=103)
    bars.append(_bar(3, 101, 103, 98, 99, volume=900.0))
    bars.append(_bar(4, 98, 100, 96, 97, volume=800.0))

    # Rise to H2 (left peak, idx=6, high=112)
    bars.append(_bar(5, 97, 104, 96, 103, volume=1300.0))
    bars.append(_bar(6, 103, 112, 102, 110, volume=2000.0))  # H2 left peak
    bars.append(_bar(7, 109, 111, 107, 108, volume=1400.0))

    # Pullback to neckline area (idx=10 becomes neckline SwingLow at price=98)
    bars.append(_bar(8, 107, 109, 103, 104, volume=1000.0))
    bars.append(_bar(9, 103, 105, 99, 100, volume=900.0))
    bars.append(_bar(10, 99, 102, 98, 100, volume=850.0))  # neckline pivot L
    bars.append(_bar(11, 100, 103, 99, 101, volume=900.0))
    bars.append(_bar(12, 101, 104, 100, 102, volume=950.0))

    # Rise to H3 (right peak, idx=14, high=112 — same as H2, lower vol=1600 < 2000)
    bars.append(_bar(13, 102, 106, 101, 104, volume=1100.0))
    bars.append(_bar(14, 104, 112, 103, 110, volume=1600.0))  # H3 right peak
    bars.append(_bar(15, 109, 111, 107, 108, volume=1200.0))

    # Pullback toward neckline; last bar closes clearly below neckline (97 < 98)
    bars.append(_bar(16, 107, 109, 103, 104, volume=900.0))
    bars.append(_bar(17, 103, 105, 99, 100, volume=800.0))
    bars.append(_bar(18, 99, 101, 96, 97, volume=2200.0))  # breakout bar
    bars.append(_bar(19, 97, 99, 94, 96, volume=1900.0))  # guard bar
    bars.append(_bar(20, 96, 98, 93, 95, volume=1700.0))  # guard bar

    return bars


def test_double_top_detected() -> None:
    bars = _make_double_top_bars()
    params = ChartPatternParams(swing_lookback=2, min_pattern_bars=8, min_peak_separation=4)
    patterns = detect_chart_patterns(bars, params)
    double_tops = [p for p in patterns if p.pattern_type == "double_top"]
    assert double_tops, (
        f"Expected double_top to be detected. All patterns: {[p.pattern_type for p in patterns]}"
    )
    dt = double_tops[0]
    assert dt.direction == "bearish"
    assert dt.neckline_price is not None
    assert dt.target_price is not None
    assert dt.target_price < dt.neckline_price, "Double-top target should be below neckline"


# ---------------------------------------------------------------------------
# Double Top: negative (peaks too far apart in price → exceeds peak_tolerance)
# ---------------------------------------------------------------------------


def test_double_top_rejected_when_peaks_too_different() -> None:
    """Two peaks that differ by more than peak_tolerance are NOT a double top."""
    bars: list[PriceBar] = []
    # Peak 1 at ~110, peak 2 at ~120 — 9% difference, well above 3%
    for i in range(6):
        bars.append(_flat_bar(i, 100.0 + i, 1000.0))

    bars.append(_bar(6, 109, 112, 108, 110, volume=2000.0))  # peak 1 = 110
    for i in range(7):
        bars.append(_flat_bar(7 + i, 102.0, 900.0))

    bars.append(_bar(14, 118, 122, 117, 120, volume=1800.0))  # peak 2 = 120 (too high)
    for i in range(5):
        bars.append(_flat_bar(15 + i, 100.0, 700.0))

    bars.append(_bar(20, 98, 100, 96, 98, volume=1500.0))  # breakout bar
    # Guard bars
    bars.append(_flat_bar(21, 97.0))
    bars.append(_flat_bar(22, 96.0))

    params = ChartPatternParams(swing_lookback=2, peak_tolerance=0.03)
    patterns = detect_chart_patterns(bars, params)
    double_tops = [p for p in patterns if p.pattern_type == "double_top"]
    assert not double_tops, "Double top should be rejected when peaks differ > peak_tolerance"


# ---------------------------------------------------------------------------
# Double Bottom: positive detection
# ---------------------------------------------------------------------------


def _make_double_bottom_bars() -> list[PriceBar]:
    """Two troughs near 90, neckline near 100, breakout above neckline."""
    bars: list[PriceBar] = []
    # Pre-pattern small dip establishes L0 (idx=2 low=91) so the detector has 3 confirmed lows.
    bars.append(_bar(0, 98, 101, 95, 96, volume=1000.0))
    bars.append(_bar(1, 96, 98, 92, 93, volume=1100.0))
    bars.append(_bar(2, 93, 96, 91, 95, volume=1000.0))  # L0 (low=91)
    bars.append(_bar(3, 95, 99, 94, 97, volume=900.0))
    bars.append(_bar(4, 97, 101, 96, 99, volume=950.0))

    # Mini-peak H0 (idx=5 or 6), needed to separate lows
    bars.append(_bar(5, 99, 104, 98, 103, volume=1100.0))
    bars.append(_bar(6, 102, 105, 101, 103, volume=1000.0))  # H0 high=105

    # Fall to L1 (left main trough, idx=9 low=87)
    bars.append(_bar(7, 102, 104, 97, 98, volume=1100.0))
    bars.append(_bar(8, 97, 99, 88, 89, volume=1400.0))
    bars.append(_bar(9, 89, 92, 87, 90, volume=1500.0))  # L1 low=87
    bars.append(_bar(10, 90, 94, 89, 92, volume=1200.0))

    # Rise to neckline H1 (~102, idx=13)
    bars.append(_bar(11, 92, 97, 91, 95, volume=1100.0))
    bars.append(_bar(12, 95, 101, 94, 100, volume=1300.0))
    bars.append(_bar(13, 99, 102, 98, 100, volume=1200.0))  # H1 neckline high=102

    # Fall to L2 (right trough, idx=16 low=87, higher vol than L1)
    bars.append(_bar(14, 99, 101, 96, 97, volume=1100.0))
    bars.append(_bar(15, 96, 98, 90, 91, volume=1400.0))
    bars.append(_bar(16, 90, 93, 87, 90, volume=1800.0))  # L2 low=87, vol 1800 > 1500
    bars.append(_bar(17, 90, 94, 89, 92, volume=1500.0))

    # Rise through neckline (breakout > 102)
    bars.append(_bar(18, 92, 97, 91, 95, volume=1300.0))
    bars.append(_bar(19, 95, 101, 94, 99, volume=1500.0))
    bars.append(_bar(20, 99, 104, 98, 102, volume=2500.0))  # breakout close > 102
    bars.append(_bar(21, 102, 106, 101, 104, volume=2000.0))  # guard
    bars.append(_bar(22, 104, 108, 103, 106, volume=1800.0))  # guard

    return bars


def test_double_bottom_detected() -> None:
    bars = _make_double_bottom_bars()
    params = ChartPatternParams(swing_lookback=2, min_pattern_bars=6, min_peak_separation=4)
    patterns = detect_chart_patterns(bars, params)
    double_bottoms = [p for p in patterns if p.pattern_type == "double_bottom"]
    assert double_bottoms, f"Expected double_bottom. All: {[p.pattern_type for p in patterns]}"
    db = double_bottoms[0]
    assert db.direction == "bullish"
    assert db.target_price is not None
    assert db.target_price > db.neckline_price  # type: ignore[operator]


# ---------------------------------------------------------------------------
# Trend classification
# ---------------------------------------------------------------------------


def test_classify_trend_uptrend() -> None:
    """HH+HL sequence → uptrend."""
    prices = [100, 98, 104, 102, 108, 106, 112, 110]
    bars = [_bar(i, p - 1, p + 1, p - 2, p) for i, p in enumerate(prices)]
    pivots = _detect_swing_pivots(bars, swing_lookback=1)
    trend = _classify_trend(pivots)
    assert trend == "uptrend"


def test_classify_trend_downtrend() -> None:
    """LH+LL sequence → downtrend."""
    prices = [110, 112, 106, 108, 102, 104, 98, 100]
    bars = [_bar(i, p - 1, p + 1, p - 2, p) for i, p in enumerate(prices)]
    pivots = _detect_swing_pivots(bars, swing_lookback=1)
    trend = _classify_trend(pivots)
    assert trend == "downtrend"


def test_classify_trend_range_when_insufficient_pivots() -> None:
    """Fewer than 2 swing highs/lows → range."""
    bars = [_flat_bar(i, 100.0) for i in range(5)]
    pivots = _detect_swing_pivots(bars, swing_lookback=2)
    trend = _classify_trend(pivots)
    assert trend == "range"


# ---------------------------------------------------------------------------
# score_pattern_strength
# ---------------------------------------------------------------------------


def test_score_pattern_strength_high_volume() -> None:
    """Very high volume ratio should bring strength close to 1."""
    # Build a minimal double_top pattern object for testing
    pattern = ChartPattern(
        pattern_type="double_top",
        direction="bearish",
        ts_start=_BASE_DATE,
        ts_end=_BASE_DATE + timedelta(days=10),
        ts_breakout=None,
        zone_low=95.0,
        zone_high=110.0,
        neckline_price=100.0,
        target_price=90.0,
        strength=0.8,
        trend_context="downtrend",
    )
    score = score_pattern_strength(pattern, volume_ratio=3.0)
    assert score >= 0.7, f"Expected >= 0.7 with high volume, got {score}"


def test_score_pattern_strength_clamped() -> None:
    """score_pattern_strength always returns 0–1."""
    pattern = ChartPattern(
        pattern_type="double_bottom",
        direction="bullish",
        ts_start=_BASE_DATE,
        ts_end=_BASE_DATE + timedelta(days=5),
        ts_breakout=None,
        zone_low=90.0,
        zone_high=100.0,
        neckline_price=100.0,
        target_price=110.0,
        strength=1.0,
        trend_context="uptrend",
    )
    assert 0.0 <= score_pattern_strength(pattern, 100.0) <= 1.0
    assert 0.0 <= score_pattern_strength(pattern, 0.0) <= 1.0


# ---------------------------------------------------------------------------
# check_breakout
# ---------------------------------------------------------------------------


def test_check_breakout_bearish_neckline() -> None:
    """Breakout fires when close < neckline for a bearish pattern."""
    pattern = ChartPattern(
        pattern_type="double_top",
        direction="bearish",
        ts_start=_BASE_DATE,
        ts_end=_BASE_DATE + timedelta(days=10),
        ts_breakout=None,
        zone_low=95.0,
        zone_high=110.0,
        neckline_price=100.0,
        target_price=90.0,
        strength=0.75,
        trend_context="downtrend",
    )
    bar = _bar(15, 99, 100, 97, 98, volume=3000.0)
    result = check_breakout(pattern, bar, volume_ratio=2.0)
    assert result["breakout"] is True
    assert result["direction"] == "bearish"
    assert result["volume_confirmed"] is True
    assert result["avoid"] is False


def test_check_breakout_avoid_when_mitigated() -> None:
    """Mitigated pattern always returns avoid=True."""
    pattern = ChartPattern(
        pattern_type="double_top",
        direction="bearish",
        ts_start=_BASE_DATE,
        ts_end=_BASE_DATE + timedelta(days=10),
        ts_breakout=None,
        zone_low=95.0,
        zone_high=110.0,
        neckline_price=100.0,
        target_price=90.0,
        strength=0.8,
        mitigated=True,
        trend_context="downtrend",
    )
    bar = _bar(15, 98, 100, 96, 97, volume=3000.0)
    result = check_breakout(pattern, bar, volume_ratio=2.5)
    assert result["avoid"] is True
    assert result["breakout"] is False


def test_check_breakout_avoid_low_strength() -> None:
    """Patterns with strength < 0.4 should have avoid=True."""
    pattern = ChartPattern(
        pattern_type="double_top",
        direction="bearish",
        ts_start=_BASE_DATE,
        ts_end=_BASE_DATE + timedelta(days=10),
        ts_breakout=None,
        zone_low=95.0,
        zone_high=110.0,
        neckline_price=100.0,
        target_price=90.0,
        strength=0.3,  # below threshold
        trend_context="downtrend",
    )
    bar = _bar(15, 98, 100, 96, 97, volume=3000.0)
    result = check_breakout(pattern, bar, volume_ratio=2.0)
    assert result["avoid"] is True


# ---------------------------------------------------------------------------
# classify_pattern_direction
# ---------------------------------------------------------------------------


def test_classify_pattern_direction_bullish() -> None:
    pattern = ChartPattern(
        pattern_type="double_bottom",
        direction="bullish",
        ts_start=_BASE_DATE,
        ts_end=_BASE_DATE + timedelta(days=10),
        ts_breakout=None,
        zone_low=90.0,
        zone_high=100.0,
        neckline_price=100.0,
        target_price=110.0,
        strength=0.7,
        trend_context="uptrend",
    )
    assert classify_pattern_direction(pattern) == "bullish"


def test_classify_pattern_direction_neutral_when_mitigated() -> None:
    pattern = ChartPattern(
        pattern_type="double_bottom",
        direction="bullish",
        ts_start=_BASE_DATE,
        ts_end=_BASE_DATE + timedelta(days=10),
        ts_breakout=None,
        zone_low=90.0,
        zone_high=100.0,
        neckline_price=100.0,
        target_price=110.0,
        strength=0.7,
        mitigated=True,
        trend_context="uptrend",
    )
    assert classify_pattern_direction(pattern) == "neutral"


def test_classify_pattern_direction_neutral_low_strength() -> None:
    pattern = ChartPattern(
        pattern_type="symmetrical_triangle",
        direction="neutral",
        ts_start=_BASE_DATE,
        ts_end=_BASE_DATE + timedelta(days=10),
        ts_breakout=None,
        zone_low=95.0,
        zone_high=105.0,
        neckline_price=None,
        target_price=None,
        strength=0.35,
        trend_context="range",
    )
    assert classify_pattern_direction(pattern) == "neutral"


# ---------------------------------------------------------------------------
# detect_pullback_retest
# ---------------------------------------------------------------------------


def test_detect_pullback_retest_bullish() -> None:
    """After a bullish breakout, a retest of neckline followed by a recovery is detected."""
    neckline = 100.0
    # Bars: ..., breakout bar (ts = BASE+10), pullback bar (low touches neckline),
    #        recovery bar (close > neckline)
    breakout_ts = _BASE_DATE + timedelta(days=10)
    bars: list[PriceBar] = []
    for i in range(10):
        bars.append(_flat_bar(i, 95.0))
    bars.append(_bar(10, 100, 103, 99, 102, volume=2000.0))  # breakout bar
    bars.append(_bar(11, 101, 102, 98, 99, volume=800.0))  # pullback bar (low < neckline area)
    bars.append(_bar(12, 99, 103, 98, 101, volume=1200.0))  # recovery

    pattern = ChartPattern(
        pattern_type="double_bottom",
        direction="bullish",
        ts_start=_BASE_DATE,
        ts_end=breakout_ts,
        ts_breakout=breakout_ts,
        zone_low=90.0,
        zone_high=neckline,
        neckline_price=neckline,
        target_price=110.0,
        strength=0.8,
        trend_context="uptrend",
    )

    result = detect_pullback_retest(pattern, bars, atr=2.0)
    assert result.detected is True
    assert result.risk_low is not None
    assert result.risk_low < neckline


def test_detect_pullback_retest_no_breakout() -> None:
    """If pattern has no breakout yet, pullback retest is not detected."""
    bars = [_flat_bar(i, 100.0) for i in range(10)]
    pattern = ChartPattern(
        pattern_type="double_top",
        direction="bearish",
        ts_start=_BASE_DATE,
        ts_end=_BASE_DATE + timedelta(days=5),
        ts_breakout=None,  # no breakout yet
        zone_low=95.0,
        zone_high=110.0,
        neckline_price=100.0,
        target_price=90.0,
        strength=0.7,
        trend_context="downtrend",
    )
    result = detect_pullback_retest(pattern, bars, atr=2.0)
    assert result.detected is False


# ---------------------------------------------------------------------------
# Parameter edge case: min_peak_separation guard
# ---------------------------------------------------------------------------


def test_double_top_rejected_when_peaks_too_close() -> None:
    """Two peaks separated by fewer than min_peak_separation bars → rejected."""
    bars: list[PriceBar] = []
    # Pre bars
    for i in range(5):
        bars.append(_flat_bar(i, 100.0))

    # Peak 1 at bar 5
    bars.append(_bar(5, 109, 111, 108, 110, volume=2000.0))
    # Only 2 bars between peaks (need >= 5)
    bars.append(_flat_bar(6, 105.0))
    bars.append(_flat_bar(7, 104.0))
    # Peak 2 at bar 8 (only 2 bars from peak1)
    bars.append(_bar(8, 109, 111, 108, 109, volume=1500.0))

    # Neckline pivot
    for i in range(5):
        bars.append(_flat_bar(9 + i, 100.0))

    # Breakout
    bars.append(_bar(14, 98, 100, 96, 97, volume=1800.0))
    bars.append(_flat_bar(15, 96.0))
    bars.append(_flat_bar(16, 95.0))

    params = ChartPatternParams(swing_lookback=2, min_peak_separation=5)
    patterns = detect_chart_patterns(bars, params)
    double_tops = [p for p in patterns if p.pattern_type == "double_top"]
    assert not double_tops, "Should reject double-top when peaks are too close together"


# ---------------------------------------------------------------------------
# Head and Shoulders: positive detection
# ---------------------------------------------------------------------------


def _make_head_and_shoulders_bars() -> list[PriceBar]:
    """Hand-crafted H&S: LS=105, HEAD=115, RS=106, neckline≈98."""
    bars: list[PriceBar] = []
    # Pre-uptrend
    for i in range(5):
        bars.append(_flat_bar(i, 95.0 + i * 1.0))

    # Left shoulder: bar 6 = 105
    bars.append(_bar(5, 103, 106, 102, 105, volume=1400.0))
    bars.append(_bar(6, 105, 107, 104, 105, volume=1800.0))  # LS high
    bars.append(_bar(7, 104, 105, 102, 103, volume=1200.0))

    # LS trough (bar 8-10)
    for i in range(3):
        bars.append(_bar(8 + i, 100.0 - i, 101.0 - i, 97.0 - i, 99.0 - i, volume=900.0))
    bars.append(_bar(11, 98, 99, 97, 98, volume=800.0))  # LS_low pivot

    # Head: bar 14 = 115
    for i in range(3):
        bars.append(_bar(12 + i, 100 + i * 3, 102 + i * 3, 99 + i * 3, 101 + i * 3, volume=1200.0))
    bars.append(_bar(15, 114, 116, 113, 115, volume=2500.0))  # HEAD high
    bars.append(_bar(16, 113, 115, 112, 113, volume=1800.0))

    # RS trough (bar 17-19)
    for i in range(3):
        bars.append(_bar(17 + i, 110 - i * 3, 111 - i * 3, 108 - i * 3, 109 - i * 3, volume=1000.0))
    bars.append(_bar(20, 99, 100, 98, 99, volume=900.0))  # RS_low pivot

    # Right shoulder: bar 23 = 106
    for i in range(3):
        bars.append(_bar(21 + i, 100 + i, 102 + i, 99 + i, 101 + i, volume=1100.0))
    bars.append(_bar(24, 105, 107, 104, 106, volume=1600.0))  # RS high
    bars.append(_bar(25, 104, 106, 103, 105, volume=1200.0))

    # Breakdown bars
    bars.append(_bar(26, 103, 104, 100, 100, volume=1400.0))
    bars.append(_bar(27, 99, 101, 96, 97, volume=2000.0))  # breakout below neckline ≈ 98

    # Guard confirmation bars
    bars.append(_bar(28, 97, 99, 95, 96, volume=1800.0))
    bars.append(_bar(29, 96, 98, 94, 95, volume=1500.0))

    return bars


def test_head_and_shoulders_detected() -> None:
    bars = _make_head_and_shoulders_bars()
    params = ChartPatternParams(swing_lookback=2, min_pattern_bars=8)
    patterns = detect_chart_patterns(bars, params)
    h_s = [p for p in patterns if p.pattern_type == "head_and_shoulders"]
    assert h_s, f"Expected head_and_shoulders. All: {[p.pattern_type for p in patterns]}"
    hs = h_s[0]
    assert hs.direction == "bearish"
    assert hs.zone_high > hs.zone_low


# ---------------------------------------------------------------------------
# Rectangle: positive detection
# ---------------------------------------------------------------------------


def _make_rectangle_bars() -> list[PriceBar]:
    """Flat rectangle: price oscillates between 95 and 105 for 20+ bars."""
    bars: list[PriceBar] = []
    # Pre
    for i in range(5):
        bars.append(_flat_bar(i, 100.0))

    # Alternating pivots at 105 (highs) and 95 (lows), 5 cycles
    for cycle in range(5):
        base = 5 + cycle * 4
        bars.append(_bar(base, 100, 106, 99, 105, volume=1000.0))  # high
        bars.append(_bar(base + 1, 104, 106, 98, 103, volume=900.0))
        bars.append(_bar(base + 2, 100, 101, 94, 95, volume=1000.0))  # low
        bars.append(_bar(base + 3, 96, 100, 93, 97, volume=900.0))

    # Breakout bar (bar 25): close well above top (105) + buffer
    bars.append(_bar(25, 105, 109, 104, 108, volume=2500.0))

    # Guard bars
    bars.append(_bar(26, 108, 110, 107, 109, volume=1800.0))
    bars.append(_bar(27, 109, 111, 108, 110, volume=1600.0))

    return bars


def test_rectangle_detected() -> None:
    bars = _make_rectangle_bars()
    params = ChartPatternParams(swing_lookback=2, min_pattern_bars=10, channel_lookback_pivots=8)
    patterns = detect_chart_patterns(bars, params)
    rects = [p for p in patterns if p.pattern_type == "rectangle"]
    assert rects, f"Expected rectangle. All: {[p.pattern_type for p in patterns]}"
    rect = rects[0]
    assert rect.zone_high > rect.zone_low


# ---------------------------------------------------------------------------
# Minimal bars guard: too few bars returns empty list
# ---------------------------------------------------------------------------


def test_empty_result_when_too_few_bars() -> None:
    bars = [_flat_bar(i, 100.0) for i in range(4)]
    patterns = detect_chart_patterns(bars)
    assert patterns == [], f"Expected empty result for 4 bars, got {patterns}"


# ---------------------------------------------------------------------------
# detect_chart_patterns: returns list of ChartPattern instances
# ---------------------------------------------------------------------------


def test_detect_returns_chart_pattern_objects() -> None:
    bars = _make_double_top_bars()
    params = ChartPatternParams(swing_lookback=2, min_pattern_bars=8, min_peak_separation=4)
    patterns = detect_chart_patterns(bars, params)
    for p in patterns:
        assert isinstance(p, ChartPattern)
        assert p.direction in ("bullish", "bearish", "neutral")
        assert p.trend_context in ("uptrend", "downtrend", "range")
        assert 0.0 <= p.strength <= 1.0


# ---------------------------------------------------------------------------
# Mitigation (STEP 13): only bars AFTER formation may mitigate a pattern.
# ---------------------------------------------------------------------------


def test_mitigation_ignores_pre_formation_bars() -> None:
    """A bar that closes through the peak level *before* the pattern formed must
    NOT retroactively mark the pattern mitigated (look-into-the-past guard)."""
    # Double-top formed between day 5 (ts_start) and day 10 (ts_end), peaks at 110.
    # Day 1 closes at 120 — above peak + tol — but it predates the structure.
    prices = [100, 120, 100, 105, 108, 110, 107, 110, 105, 103, 102, 101, 100]
    bars = [_bar(i, p, p + 1, p - 1, p) for i, p in enumerate(prices)]
    pattern = ChartPattern(
        pattern_type="double_top",
        direction="bearish",
        ts_start=_BASE_DATE + timedelta(days=5),
        ts_end=_BASE_DATE + timedelta(days=10),
        ts_breakout=None,
        zone_low=100.0,
        zone_high=110.0,
        neckline_price=102.0,
        target_price=94.0,
        strength=0.8,
        trend_context="range",
        _peak_prices=[110.0, 110.0],
    )
    out = _update_mitigation(pattern, bars)
    assert out.mitigated is False, "Pre-formation spike must not mitigate the pattern"
    assert out.ts_mitigated is None


def test_mitigation_fires_on_post_formation_bar() -> None:
    """A bar that closes through the peak level *after* formation DOES mitigate."""
    prices = [100, 100, 100, 105, 108, 110, 107, 110, 105, 103, 102, 101, 130]
    bars = [_bar(i, p, p + 1, p - 1, p) for i, p in enumerate(prices)]
    pattern = ChartPattern(
        pattern_type="double_top",
        direction="bearish",
        ts_start=_BASE_DATE + timedelta(days=5),
        ts_end=_BASE_DATE + timedelta(days=10),
        ts_breakout=None,
        zone_low=100.0,
        zone_high=110.0,
        neckline_price=102.0,
        target_price=94.0,
        strength=0.8,
        trend_context="range",
        _peak_prices=[110.0, 110.0],
    )
    out = _update_mitigation(pattern, bars)
    assert out.mitigated is True
    assert out.ts_mitigated == _BASE_DATE + timedelta(days=12)


def test_mitigation_double_bottom_ignores_pre_formation_dip() -> None:
    """Symmetric guard for double-bottom: a deep pre-formation dip must not mitigate."""
    # Troughs at 90, formed day 5..10. Day 1 closes at 80 (below trough - tol) but predates it.
    prices = [100, 80, 100, 96, 93, 90, 94, 90, 95, 98, 100, 101, 102]
    bars = [_bar(i, p, p + 1, p - 1, p) for i, p in enumerate(prices)]
    pattern = ChartPattern(
        pattern_type="double_bottom",
        direction="bullish",
        ts_start=_BASE_DATE + timedelta(days=5),
        ts_end=_BASE_DATE + timedelta(days=10),
        ts_breakout=None,
        zone_low=90.0,
        zone_high=100.0,
        neckline_price=100.0,
        target_price=110.0,
        strength=0.8,
        trend_context="range",
        _peak_prices=[90.0, 90.0],
    )
    out = _update_mitigation(pattern, bars)
    assert out.mitigated is False, "Pre-formation dip must not mitigate the double-bottom"


# ---------------------------------------------------------------------------
# Continuation pattern: ascending triangle positive detection
# ---------------------------------------------------------------------------


def _make_ascending_triangle_bars() -> list[PriceBar]:
    """Flat resistance near 110 with rising support — a real ascending triangle."""
    bars: list[PriceBar] = []
    top = 110.0
    low_base = 95.0
    idx = 0
    for cyc in range(5):
        lo = low_base + cyc * 2.5
        bars.append(_bar(idx, lo, top, lo - 1, top - 1, volume=1500 - cyc * 200))
        idx += 1
        bars.append(_bar(idx, top - 1, top, top - 3, top - 2, volume=1300 - cyc * 200))
        idx += 1
        bars.append(_bar(idx, top - 2, top - 1, lo, lo + 0.5, volume=1200 - cyc * 150))
        idx += 1
        bars.append(_bar(idx, lo + 0.5, lo + 3, lo, lo + 1, volume=1100 - cyc * 150))
        idx += 1
    # Two guard bars so the last pivots are confirmed.
    bars.append(_bar(idx, lo + 1, lo + 2, lo, lo + 1, volume=800))
    idx += 1
    bars.append(_bar(idx, lo + 1, lo + 2, lo, lo + 1, volume=800))
    return bars


def test_ascending_triangle_detected() -> None:
    bars = _make_ascending_triangle_bars()
    params = ChartPatternParams(swing_lookback=2, triangle_lookback_pivots=10)
    patterns = detect_chart_patterns(bars, params)
    tris = [p for p in patterns if p.pattern_type == "ascending_triangle"]
    assert tris, f"Expected ascending_triangle. All: {[p.pattern_type for p in patterns]}"
    tri = tris[0]
    assert tri.direction == "bullish"
    assert tri.apex_bar_index is not None
    # Apex must lie in the future (lookahead guard): strictly beyond the last bar.
    assert tri.apex_bar_index > len(bars) - 1
    assert tri.trendline_r_squared_top is not None
