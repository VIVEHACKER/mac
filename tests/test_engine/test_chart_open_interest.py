"""Tests for engine/chart/open_interest.py.

Fixtures are hand-crafted synthetic PriceBar + OpenInterestRecord sequences
designed to trigger (and avoid) each detection path without any lookahead.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from data.models import CryptoFundingRecord, OpenInterestRecord, PriceBar
from engine.chart.open_interest import (
    OISignal,
    analyze_open_interest,
    classify_funding_state,
    classify_oi_quadrant,
    compute_oi_zscore,
    detect_cascade_liquidation,
    detect_oi_squeeze_risk,
)
from engine.chart.types import FundingState, OIQuadrant

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE = datetime(2026, 1, 1, 0, 0, 0)


def _bar(
    i: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float = 1000.0,
    symbol: str = "BTC/USDT:USDT",
) -> PriceBar:
    # Use datetime so bar timestamps align with OI/FR record datetimes.
    # datetime is a subclass of date so PriceBar.ts: date accepts it.
    return PriceBar(
        symbol=symbol,
        market="crypto",
        source_symbol=symbol,
        ts=_BASE + timedelta(hours=i),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        freq="1h",
    )


def _oi(i: int, amount: float, symbol: str = "BTC/USDT:USDT") -> OpenInterestRecord:
    return OpenInterestRecord(
        exchange="binance",
        symbol=symbol,
        ts=_BASE + timedelta(hours=i),
        open_interest_amount=amount,
        open_interest_value=amount * 30_000.0,
        source="test",
    )


def _fr(i: int, rate: float, symbol: str = "BTC/USDT:USDT") -> CryptoFundingRecord:
    return CryptoFundingRecord(
        exchange="binance",
        symbol=symbol,
        ts=_BASE + timedelta(hours=i),
        funding_rate=rate,
        source="test",
    )


# ---------------------------------------------------------------------------
# Unit tests for sub-functions
# ---------------------------------------------------------------------------


def test_classify_oi_quadrant_all_four() -> None:
    assert classify_oi_quadrant(100.0, 0.05) is OIQuadrant.BULL_TREND
    assert classify_oi_quadrant(-100.0, 0.05) is OIQuadrant.BEAR_TREND
    assert classify_oi_quadrant(100.0, -0.05) is OIQuadrant.SHORT_COVER
    assert classify_oi_quadrant(-100.0, -0.05) is OIQuadrant.LONG_LIQ


def test_classify_oi_quadrant_neutral_on_flat() -> None:
    # price unchanged → NEUTRAL regardless of OI direction
    assert classify_oi_quadrant(0.0, 0.05) is OIQuadrant.NEUTRAL
    assert classify_oi_quadrant(0.0, -0.05) is OIQuadrant.NEUTRAL
    # OI unchanged → NEUTRAL
    assert classify_oi_quadrant(100.0, 0.0) is OIQuadrant.NEUTRAL


def test_compute_oi_zscore_returns_none_when_insufficient_data() -> None:
    # 4 samples, window=50 → need >= 25, so None
    series: list[float | None] = [float(x) for x in range(4)]
    result = compute_oi_zscore(series, lookback=50)
    assert result is None


def test_compute_oi_zscore_uniform_series_returns_zero() -> None:
    series: list[float | None] = [100.0] * 50
    result = compute_oi_zscore(series, lookback=50)
    assert result == 0.0


def test_compute_oi_zscore_known_value() -> None:
    import math

    # [3,4,5,6,7]: mean=5, pstdev = sqrt(mean of squared deviations)
    # deviations: -2,-1,0,1,2 → squared: 4,1,0,1,4 → mean=2 → pstdev=sqrt(2)
    series: list[float | None] = [3.0, 4.0, 5.0, 6.0, 7.0]
    result = compute_oi_zscore(series, lookback=4)
    expected = (7.0 - 5.0) / math.sqrt(2)  # ≈ 1.4142
    assert result == pytest.approx(expected, abs=0.01)


def test_classify_funding_state_thresholds() -> None:
    assert classify_funding_state(0.001) is FundingState.LONG_HEAVY
    assert classify_funding_state(0.0003) is FundingState.LONG_LEAN
    assert classify_funding_state(0.00005) is FundingState.NEUTRAL
    assert classify_funding_state(-0.0003) is FundingState.SHORT_LEAN
    assert classify_funding_state(-0.001) is FundingState.SHORT_HEAVY


def test_classify_funding_state_boundary_exact() -> None:
    # At the exact boundary of extreme threshold
    assert classify_funding_state(0.0005) is FundingState.LONG_HEAVY
    assert classify_funding_state(-0.0005) is FundingState.SHORT_HEAVY
    # At neutral boundary
    assert classify_funding_state(0.0001) is FundingState.LONG_LEAN
    assert classify_funding_state(-0.0001) is FundingState.SHORT_LEAN


def test_detect_cascade_liquidation_long() -> None:
    cl, cs = detect_cascade_liquidation(
        price_prev=50000.0,
        price_curr=48500.0,  # -3% drop
        oi_prev=100_000.0,
        oi_curr=95_000.0,  # -5% OI drop
        cascade_price_move_pct=0.02,
        cascade_oi_drop_pct=0.03,
    )
    assert cl is True
    assert cs is False


def test_detect_cascade_liquidation_short() -> None:
    cl, cs = detect_cascade_liquidation(
        price_prev=50000.0,
        price_curr=51500.0,  # +3% rise
        oi_prev=100_000.0,
        oi_curr=95_000.0,  # -5% OI drop
        cascade_price_move_pct=0.02,
        cascade_oi_drop_pct=0.03,
    )
    assert cl is False
    assert cs is True


def test_detect_cascade_below_threshold_no_signal() -> None:
    # price move < threshold
    cl, cs = detect_cascade_liquidation(
        price_prev=50000.0,
        price_curr=50500.0,  # only 1% move
        oi_prev=100_000.0,
        oi_curr=97_000.0,  # 3% OI drop
        cascade_price_move_pct=0.02,
        cascade_oi_drop_pct=0.03,
    )
    assert cl is False
    assert cs is False


def test_detect_oi_squeeze_risk_long() -> None:
    lsr, ssr, lse, sse = detect_oi_squeeze_risk(
        fr_state=FundingState.LONG_HEAVY,
        oi_rising=True,
        oi_zscore=1.5,
        zscore_buildup_threshold=1.0,
        zscore_extreme_threshold=2.0,
    )
    assert lsr is True
    assert ssr is False
    assert lse is False  # zscore < 2.0
    assert sse is False


def test_detect_oi_squeeze_risk_short_extreme() -> None:
    lsr, ssr, lse, sse = detect_oi_squeeze_risk(
        fr_state=FundingState.SHORT_HEAVY,
        oi_rising=True,
        oi_zscore=2.5,
        zscore_buildup_threshold=1.0,
        zscore_extreme_threshold=2.0,
    )
    assert lsr is False
    assert ssr is True
    assert lse is False
    assert sse is True


def test_detect_oi_squeeze_risk_requires_oi_rising() -> None:
    # Funding is extreme but OI is not rising → no squeeze risk
    lsr, ssr, _lse, _sse = detect_oi_squeeze_risk(
        fr_state=FundingState.LONG_HEAVY,
        oi_rising=False,
        oi_zscore=2.5,
        zscore_buildup_threshold=1.0,
        zscore_extreme_threshold=2.0,
    )
    assert lsr is False
    assert ssr is False


def test_detect_oi_squeeze_risk_none_fr_state() -> None:
    lsr, ssr, lse, sse = detect_oi_squeeze_risk(
        fr_state=None,
        oi_rising=True,
        oi_zscore=3.0,
    )
    assert not any([lsr, ssr, lse, sse])


# ---------------------------------------------------------------------------
# Integration: analyze_open_interest
# ---------------------------------------------------------------------------


def _make_bull_trend_bars_and_oi(n: int = 10) -> tuple[list[PriceBar], list[OpenInterestRecord]]:
    """Rising price + rising OI → BULL_TREND for all bars after bar 0."""
    bars = [_bar(i, 100 + i, 102 + i, 99 + i, 101 + i) for i in range(n)]
    oi = [_oi(i, 10_000 + i * 200) for i in range(n)]
    return bars, oi


def test_analyze_returns_one_signal_per_bar() -> None:
    bars, oi = _make_bull_trend_bars_and_oi(5)
    signals = analyze_open_interest(bars, oi)
    assert len(signals) == 5


def test_analyze_empty_bars_returns_empty() -> None:
    assert analyze_open_interest([], []) == []


def test_analyze_bull_trend_quadrant() -> None:
    bars, oi = _make_bull_trend_bars_and_oi(8)
    signals = analyze_open_interest(bars, oi)
    # Bar 0 has no previous bar, so NEUTRAL; bars 1+ should be BULL_TREND
    assert signals[0].quadrant is OIQuadrant.NEUTRAL
    for sig in signals[1:]:
        assert sig.quadrant is OIQuadrant.BULL_TREND, f"Expected BULL_TREND, got {sig.quadrant}"


def test_analyze_bear_trend_quadrant() -> None:
    """Falling price + rising OI → BEAR_TREND."""
    n = 6
    bars = [_bar(i, 110 - i, 112 - i, 108 - i, 109 - i) for i in range(n)]
    oi = [_oi(i, 10_000 + i * 300) for i in range(n)]
    signals = analyze_open_interest(bars, oi)
    for sig in signals[1:]:
        assert sig.quadrant is OIQuadrant.BEAR_TREND


def test_analyze_short_cover_quadrant() -> None:
    """Rising price + falling OI → SHORT_COVER."""
    n = 6
    bars = [_bar(i, 100 + i, 102 + i, 99 + i, 101 + i) for i in range(n)]
    oi = [_oi(i, 10_000 - i * 200) for i in range(n)]
    signals = analyze_open_interest(bars, oi)
    for sig in signals[1:]:
        assert sig.quadrant is OIQuadrant.SHORT_COVER


def test_analyze_long_liq_quadrant() -> None:
    """Falling price + falling OI → LONG_LIQ."""
    n = 6
    bars = [_bar(i, 110 - i, 112 - i, 108 - i, 109 - i) for i in range(n)]
    oi = [_oi(i, 10_000 - i * 200) for i in range(n)]
    signals = analyze_open_interest(bars, oi)
    for sig in signals[1:]:
        assert sig.quadrant is OIQuadrant.LONG_LIQ


def test_analyze_oi_buildup_streak() -> None:
    """3+ consecutive OI rises → oi_buildup=True."""
    n = 10
    bars = [_bar(i, 100, 102, 99, 101) for i in range(n)]
    oi = [_oi(i, 10_000 + i * 500) for i in range(n)]
    signals = analyze_open_interest(bars, oi, buildup_streak_bars=3)
    # bars 0,1,2 → streak 0,1,2; bar 3+ → streak >= 3
    assert signals[0].oi_buildup is False
    assert signals[1].oi_buildup is False
    assert signals[2].oi_buildup is False
    assert signals[3].oi_buildup is True
    assert signals[9].oi_buildup_streak == 9


def test_analyze_oi_zscore_extreme() -> None:
    """OI that shoots up far above its recent average → oi_extreme=True."""
    n = 60
    # Stable OI for first 55 bars, then a large spike on bar 55
    bars = [_bar(i, 100, 102, 99, 101) for i in range(n)]
    baseline = 10_000.0
    oi_vals = [baseline] * 55 + [baseline * 3.0] * 5  # 3x spike
    oi = [_oi(i, oi_vals[i]) for i in range(n)]
    signals = analyze_open_interest(bars, oi, oi_zscore_window=50)
    # The spike bars should be extreme
    assert signals[59].oi_extreme is True


def test_analyze_no_oi_extreme_when_stable() -> None:
    """Stable OI → z-score near 0 → oi_extreme=False."""
    n = 60
    bars = [_bar(i, 100, 102, 99, 101) for i in range(n)]
    oi = [_oi(i, 10_000.0 + i * 1.0) for i in range(n)]  # very slow linear growth
    signals = analyze_open_interest(bars, oi, oi_zscore_window=50)
    # None should be extreme for a smoothly increasing series
    assert not any(s.oi_extreme for s in signals)


def test_analyze_cascade_long_signal() -> None:
    """Price drops 3% + OI drops 5% on same bar → cascade_long=True."""
    bars = [
        _bar(0, 50000, 50100, 49900, 50000),
        _bar(1, 50000, 50050, 48400, 48500),  # ~3% drop
    ]
    oi = [
        _oi(0, 100_000),
        _oi(1, 94_000),  # 6% drop
    ]
    signals = analyze_open_interest(bars, oi, cascade_price_move_pct=0.02, cascade_oi_drop_pct=0.03)
    assert signals[1].cascade_long is True
    assert signals[1].cascade_short is False
    assert signals[1].direction == "AVOID"


def test_analyze_cascade_short_signal() -> None:
    """Price rises 3% + OI drops 5% → cascade_short=True."""
    bars = [
        _bar(0, 50000, 50100, 49900, 50000),
        _bar(1, 50000, 52000, 49900, 51500),  # +3% rise
    ]
    oi = [
        _oi(0, 100_000),
        _oi(1, 94_000),  # 6% drop
    ]
    signals = analyze_open_interest(bars, oi, cascade_price_move_pct=0.02, cascade_oi_drop_pct=0.03)
    assert signals[1].cascade_short is True
    assert signals[1].cascade_long is False
    assert signals[1].direction == "AVOID"


def test_analyze_no_cascade_when_oi_stable() -> None:
    bars = [
        _bar(0, 50000, 50100, 49900, 50000),
        _bar(1, 50000, 50050, 48400, 48500),  # 3% price drop
    ]
    oi = [
        _oi(0, 100_000),
        _oi(1, 99_000),  # only 1% OI drop, below 3% threshold
    ]
    signals = analyze_open_interest(bars, oi, cascade_price_move_pct=0.02, cascade_oi_drop_pct=0.03)
    assert signals[1].cascade_long is False
    assert signals[1].cascade_short is False


def test_analyze_long_squeeze_risk_with_funding() -> None:
    """LONG_LEAN funding + rising OI + z-score >= 1.0 → long_squeeze_risk=True."""
    n = 60
    # Rising OI baseline so we get z-score buildup
    bars = [_bar(i, 100, 102, 99, 101) for i in range(n)]
    oi_vals = [10_000.0 + i * 100 for i in range(n)]
    # Spike the last 5 bars to push z-score high
    for i in range(55, 60):
        oi_vals[i] = 30_000.0
    oi = [_oi(i, oi_vals[i]) for i in range(n)]
    # LONG_LEAN funding for all bars
    fr = [_fr(i, 0.0003) for i in range(n)]  # 0.03%/period → LONG_LEAN
    signals = analyze_open_interest(bars, oi, fr)
    # Bars 55-59 have high z-score + oi_rising + LONG_LEAN → long_squeeze_risk
    # At least one of the spike bars should trigger
    spike_signals = signals[55:]
    assert any(s.long_squeeze_risk for s in spike_signals), (
        f"Expected long_squeeze_risk in spike bars; z-scores: "
        f"{[s.oi_zscore for s in spike_signals]}"
    )


def test_analyze_short_squeeze_extreme_gives_long_direction() -> None:
    """SHORT_HEAVY funding + extreme OI → direction=LONG (crowd-contra)."""
    n = 60
    bars = [_bar(i, 100, 102, 99, 101) for i in range(n)]
    oi_vals = [10_000.0] * 55 + [50_000.0] * 5  # large spike → extreme z-score
    oi = [_oi(i, oi_vals[i]) for i in range(n)]
    fr = [_fr(i, -0.001) for i in range(n)]  # SHORT_HEAVY
    signals = analyze_open_interest(bars, oi, fr)
    spike_signals = signals[55:]
    extreme_signals = [s for s in spike_signals if s.short_squeeze_extreme]
    assert extreme_signals, "Expected short_squeeze_extreme in spike bars"
    for s in extreme_signals:
        assert s.direction == "LONG"


def test_analyze_bearish_divergence() -> None:
    """Price makes new 10-bar high but OI does NOT → bearish_div=True."""
    # Set up 10 bars where price keeps going up but OI peaks early and falls
    n = 12
    # Price steadily rises
    bars = [_bar(i, 100 + i, 102 + i, 99 + i, 101 + i) for i in range(n)]
    # OI peaks at bar 5 and falls after
    oi_vals = [10_000 + i * 200 for i in range(6)] + [11_000 - j * 100 for j in range(6)]
    oi = [_oi(i, oi_vals[i]) for i in range(n)]
    signals = analyze_open_interest(bars, oi, divergence_lookback=10)
    # Bar 11 should show bearish_div: price at highest but OI is below peak
    # (price=112 is new high; OI=10500 is below peak of 11000)
    assert signals[11].bearish_div is True


def test_analyze_bullish_divergence() -> None:
    """Price makes new N-bar low but OI does NOT → bullish_div=True."""
    n = 12
    # Price steadily falls
    bars = [_bar(i, 110 - i, 112 - i, 108 - i, 109 - i) for i in range(n)]
    # OI falls early and stabilizes / rises later
    oi_vals = [10_000 - i * 200 for i in range(6)] + [9_000 + j * 100 for j in range(6)]
    oi = [_oi(i, oi_vals[i]) for i in range(n)]
    signals = analyze_open_interest(bars, oi, divergence_lookback=10)
    # Bar 11: price=98 is new low; OI=9500 is above minimum of 8800 → bullish_div
    assert signals[11].bullish_div is True


def test_analyze_no_divergence_when_oi_confirms() -> None:
    """Price new high AND OI new high → no bearish divergence."""
    n = 12
    bars = [_bar(i, 100 + i, 102 + i, 99 + i, 101 + i) for i in range(n)]
    oi = [_oi(i, 10_000 + i * 500) for i in range(n)]
    signals = analyze_open_interest(bars, oi, divergence_lookback=10)
    assert not any(s.bearish_div for s in signals)


def test_analyze_flat_price_is_not_a_divergence() -> None:
    """Regression: a flat price series makes neither a new high nor a new low.

    Previously the divergence test used ``close >= max(window)`` / ``close <= min(window)``
    against a window that *included* the current bar, so a flat close registered as BOTH a
    new high and a new low — firing bearish_div and bullish_div on the same bar. A flat
    price never *makes* a new N-bar extreme, so neither divergence may fire.
    """
    n = 12
    bars = [_bar(i, 100.0, 101.0, 99.0, 100.0) for i in range(n)]  # perfectly flat close
    oi = [_oi(i, 20_000.0 - i * 100.0) for i in range(n)]  # OI strictly falling
    signals = analyze_open_interest(bars, oi, divergence_lookback=10)
    assert not any(s.bearish_div for s in signals)
    assert not any(s.bullish_div for s in signals)


def test_analyze_divergence_flags_mutually_exclusive() -> None:
    """No single bar may report both bearish_div and bullish_div (incoherent).

    A bar cannot simultaneously make a new high and a new low, so the two divergence
    flags must never both be True on the same bar — for any input shape.
    """
    n = 12
    # Flat price + OI that dips then partially recovers (current OI neither max nor min).
    bars = [_bar(i, 100.0, 101.0, 99.0, 100.0) for i in range(n)]
    oi_vals = [
        20_000.0,
        19_000.0,
        18_000.0,
        17_000.0,
        16_000.0,
        15_000.0,
        16_000.0,
        17_000.0,
        18_000.0,
        18_500.0,
        18_700.0,
        18_900.0,
    ]
    oi = [_oi(i, oi_vals[i]) for i in range(n)]
    signals = analyze_open_interest(bars, oi, divergence_lookback=10)
    for s in signals:
        assert not (s.bearish_div and s.bullish_div), (
            "bearish_div and bullish_div must be mutually exclusive on a single bar"
        )


def test_analyze_strength_range() -> None:
    """strength must always be in [0, 1]."""
    bars, oi = _make_bull_trend_bars_and_oi(20)
    signals = analyze_open_interest(bars, oi)
    for s in signals:
        assert 0.0 <= s.strength <= 1.0


def test_analyze_strength_with_multiple_flags() -> None:
    """With oi_buildup and cascade_long active, strength > 0."""
    bars = [_bar(i, 100 + i * 0.01, 102, 99, 101 + i * 0.01) for i in range(5)]
    # Large OI rise for buildup, then sudden big drop
    bars_cascade = bars + [
        _bar(5, 101.05, 102, 95, 96),  # ~5% price drop
    ]
    oi_base = [_oi(i, 10_000 + i * 500) for i in range(5)]
    oi_cascade = oi_base + [_oi(5, 8_000)]  # 20% OI drop
    signals = analyze_open_interest(
        bars_cascade, oi_cascade, cascade_price_move_pct=0.02, cascade_oi_drop_pct=0.03
    )
    assert signals[5].cascade_long is True
    assert signals[5].strength > 0.0


def test_analyze_oi_capitulation() -> None:
    """OI drops 30%+ from window max → oi_capitulation=True."""
    n = 10
    bars = [_bar(i, 100, 102, 99, 101) for i in range(n)]
    # OI peaks at bar 3 then drops sharply to 60% of peak = 40% drop
    oi_vals = [10_000.0, 12_000.0, 14_000.0, 15_000.0] + [9_000.0] * 6
    oi = [_oi(i, oi_vals[i]) for i in range(n)]
    signals = analyze_open_interest(bars, oi, oi_zscore_window=50)
    # From bar 4 onward: window max=15000, current=9000 → 40% drop >= 30%
    for sig in signals[4:]:
        assert sig.oi_capitulation is True


def test_analyze_no_capitulation_when_oi_stable() -> None:
    n = 10
    bars = [_bar(i, 100, 102, 99, 101) for i in range(n)]
    oi = [_oi(i, 10_000 + i * 100) for i in range(n)]
    signals = analyze_open_interest(bars, oi)
    assert not any(s.oi_capitulation for s in signals)


def test_analyze_feed_stagnation_guard() -> None:
    """3+ consecutive zero OI changes → treated as None, no buildup streak."""
    n = 10
    bars = [_bar(i, 100, 102, 99, 101) for i in range(n)]
    # Same OI for bars 2-6 (zero changes) — should break streak
    oi_vals = [10_000.0, 10_500.0] + [11_000.0] * 5 + [11_500.0, 12_000.0, 12_500.0]
    oi = [_oi(i, oi_vals[i]) for i in range(n)]
    signals = analyze_open_interest(bars, oi, buildup_streak_bars=3)
    # Bars 4,5,6 have zero change: 3rd zero → treated as None, streak reset
    # So oi_buildup should NOT fire during stagnation period
    # Bar 2: chg=500/10500>0; Bar 3: chg=0; Bar 4: chg=0; Bar 5: chg=0 (3rd zero)
    # After guard, bar 5 oi_chg=None → streak broken
    assert signals[5].oi_buildup is False


def test_analyze_signal_fields_types() -> None:
    """All OISignal fields should be the right types."""
    bars, oi = _make_bull_trend_bars_and_oi(3)
    signals = analyze_open_interest(bars, oi)
    for s in signals:
        assert isinstance(s, OISignal)
        assert isinstance(s.quadrant, OIQuadrant)
        assert isinstance(s.oi_buildup, bool)
        assert isinstance(s.oi_extreme, bool)
        assert isinstance(s.strength, float)
        assert isinstance(s.direction, str)


def test_analyze_direction_bull_trend_no_squeeze_gives_long() -> None:
    """BULL_TREND with no squeeze risk → direction=LONG."""
    bars, oi = _make_bull_trend_bars_and_oi(5)
    # Neutral funding
    fr = [_fr(i, 0.00005) for i in range(5)]  # NEUTRAL funding
    signals = analyze_open_interest(bars, oi, fr)
    for sig in signals[1:]:
        if sig.quadrant is OIQuadrant.BULL_TREND and not sig.long_squeeze_risk:
            assert sig.direction == "LONG"


def test_analyze_short_cover_gives_wait_direction() -> None:
    """SHORT_COVER quadrant (price up + OI down) → direction=WAIT."""
    n = 6
    bars = [_bar(i, 100 + i, 102 + i, 99 + i, 101 + i) for i in range(n)]
    oi = [_oi(i, 10_000 - i * 200) for i in range(n)]
    signals = analyze_open_interest(bars, oi)
    for sig in signals[1:]:
        assert sig.quadrant is OIQuadrant.SHORT_COVER
        # No other override → WAIT
        if not any(
            [
                sig.cascade_long,
                sig.cascade_short,
                sig.short_squeeze_extreme,
                sig.long_squeeze_extreme,
                sig.short_squeeze_risk,
                sig.long_squeeze_risk,
            ]
        ):
            assert sig.direction == "WAIT"


def test_analyze_funding_period_auto_detect_8h() -> None:
    """8-hour funding records → detected funding_period_hours ≈ 8.0."""
    bars = [_bar(i, 100, 102, 99, 101) for i in range(5)]
    oi = [_oi(i, 10_000.0) for i in range(5)]
    # 8-hour spacing
    fr = [
        CryptoFundingRecord(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            ts=datetime(2026, 1, 1, 0, 0) + timedelta(hours=j * 8),
            funding_rate=0.0001,
            source="test",
        )
        for j in range(5)
    ]
    signals = analyze_open_interest(bars, oi, fr)
    assert all(s.funding_period_hours == pytest.approx(8.0) for s in signals)


def test_analyze_funding_period_fallback_when_no_fr() -> None:
    """No funding records → fallback to 8.0 hours."""
    bars, oi = _make_bull_trend_bars_and_oi(3)
    signals = analyze_open_interest(bars, oi, None)
    assert all(s.funding_period_hours == 8.0 for s in signals)


def test_analyze_oi_value_none_when_no_records() -> None:
    """No OI records → oi_value=None, quadrant=NEUTRAL throughout."""
    bars = [_bar(i, 100 + i, 102 + i, 99 + i, 101 + i) for i in range(5)]
    signals = analyze_open_interest(bars, [])
    for sig in signals:
        assert sig.oi_value is None
        assert sig.quadrant is OIQuadrant.NEUTRAL


def test_analyze_no_lookahead_bar0_neutral() -> None:
    """Bar 0 has no previous bar, so quadrant must always be NEUTRAL."""
    bars, oi = _make_bull_trend_bars_and_oi(10)
    signals = analyze_open_interest(bars, oi)
    assert signals[0].quadrant is OIQuadrant.NEUTRAL
    assert signals[0].oi_chg_pct is None
    assert signals[0].oi_buildup is False
    assert signals[0].cascade_long is False
    assert signals[0].cascade_short is False


def test_analyze_no_lookahead_truncation_invariance() -> None:
    """Strongest lookahead guarantee: the signal for bar k computed from bars[0..k]
    must equal the signal at index k from the full run. If any field used future bars,
    truncating the input would change earlier outputs.
    """
    import random

    rng = random.Random(7)
    n = 60
    closes = [100.0]
    oivals = [10_000.0]
    for _ in range(1, n):
        closes.append(closes[-1] * (1 + rng.uniform(-0.03, 0.03)))
        oivals.append(max(100.0, oivals[-1] * (1 + rng.uniform(-0.05, 0.06))))
    bars = [_bar(i, closes[i], closes[i] + 1, closes[i] - 1, closes[i]) for i in range(n)]
    oi = [_oi(i, oivals[i]) for i in range(n)]
    fr = [_fr(i, rng.uniform(-0.001, 0.001)) for i in range(n)]

    full = analyze_open_interest(bars, oi, fr)
    fields = [
        "quadrant",
        "oi_value",
        "oi_chg_pct",
        "oi_zscore",
        "oi_buildup",
        "oi_buildup_streak",
        "oi_extreme",
        "oi_capitulation",
        "funding_state",
        "long_squeeze_risk",
        "short_squeeze_risk",
        "long_squeeze_extreme",
        "short_squeeze_extreme",
        "cascade_long",
        "cascade_short",
        "bearish_div",
        "bullish_div",
        "direction",
        "strength",
    ]
    for k in range(1, n + 1):
        trunc = analyze_open_interest(bars[:k], oi[:k], fr[:k])
        last, ref = trunc[-1], full[k - 1]
        for fld in fields:
            assert getattr(last, fld) == getattr(ref, fld), (
                f"lookahead leak at bar {k - 1}, field {fld}: "
                f"{getattr(last, fld)!r} != {getattr(ref, fld)!r}"
            )
