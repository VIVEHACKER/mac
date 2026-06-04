"""Tests for engine/chart/volume.py — volume-analysis detector.

Covers:
- RVOL computation and classification
- OBV computation
- CMF computation
- No-Demand detection (positive + negative)
- No-Supply detection (positive + negative)
- Climax top / bottom detection
- VDU (Volume Dry-Up) detection
- OBV divergence (bullish + bearish)
- Lookahead-free confirmation (1-bar delay)
- Edge cases: zero-range bar, insufficient data, liquidity guard
"""

from __future__ import annotations

from datetime import date

import pytest

from data.models import PriceBar
from engine.chart.volume import (
    VolumeBarResult,
    analyse_volume,
    classify_rvol,
    compute_cmf,
    compute_evr,
    compute_obv,
    compute_rvol,
    detect_climax,
    detect_no_demand,
    detect_no_supply,
    detect_vdu,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _bar(
    o: float,
    h: float,
    lo: float,
    c: float,
    v: float,
    *,
    sym: str = "TEST",
    d: date | None = None,
) -> PriceBar:
    return PriceBar(
        symbol=sym,
        market="us",
        source_symbol=sym,
        ts=d or date(2026, 1, 1),
        open=o,
        high=h,
        low=lo,
        close=c,
        volume=v,
        freq="1d",
    )


def _make_bars(
    n: int,
    base_price: float = 100.0,
    base_vol: float = 10_000.0,
) -> list[PriceBar]:
    """Create n flat (doji-ish) bars with consistent price and volume."""
    bars: list[PriceBar] = []
    for i in range(n):
        p = base_price + i * 0.01
        bars.append(_bar(p, p + 1.0, p - 1.0, p + 0.5, base_vol, d=date(2026, 1, i + 1)))
    return bars


# ---------------------------------------------------------------------------
# 1. compute_rvol + classify_rvol
# ---------------------------------------------------------------------------


class TestComputeRvol:
    def test_warmup_period_returns_none(self) -> None:
        bars = _make_bars(25, base_vol=10_000.0)
        rvol = compute_rvol(bars, lookback=20)
        # First 19 entries (indices 0..18) must be None
        for i in range(19):
            assert rvol[i] is None, f"expected None at index {i}"

    def test_constant_volume_gives_rvol_one(self) -> None:
        bars = _make_bars(25, base_vol=10_000.0)
        rvol = compute_rvol(bars, lookback=20)
        # All non-None entries should be 1.0 for constant volume
        for i in range(19, 25):
            assert rvol[i] == pytest.approx(1.0), f"index {i}"

    def test_double_volume_gives_rvol_around_two(self) -> None:
        bars = _make_bars(30, base_vol=10_000.0)
        # Double the volume of the last bar
        doubled = list(bars)
        last = bars[-1]
        doubled[-1] = _bar(
            last.open,
            last.high,
            last.low,
            last.close,
            20_000.0,
            d=date(2026, 1, 30),
        )
        rvol = compute_rvol(doubled, lookback=20)
        # Last bar RVOL: window average ≈ (19*10000 + 20000)/20 = 10500; 20000/10500 ≈ 1.905
        assert rvol[-1] is not None
        assert rvol[-1] > 1.5  # clearly elevated

    def test_classify_rvol_buckets(self) -> None:
        assert classify_rvol(None) == "undefined"
        assert classify_rvol(0.3) == "dry_up"
        assert classify_rvol(0.5) == "normal"
        assert classify_rvol(1.0) == "normal"
        assert classify_rvol(1.5) == "elevated"
        assert classify_rvol(2.5) == "elevated"
        assert classify_rvol(3.0) == "spike"
        assert classify_rvol(3.9) == "spike"
        assert classify_rvol(4.0) == "climax"
        assert classify_rvol(10.0) == "climax"


# ---------------------------------------------------------------------------
# 2. compute_obv
# ---------------------------------------------------------------------------


class TestComputeObv:
    def test_rising_bars_accumulate_positively(self) -> None:
        bars = [
            _bar(100.0, 101.0, 99.0, 101.0, 1000.0, d=date(2026, 1, 1)),
            _bar(101.0, 102.0, 100.0, 102.0, 2000.0, d=date(2026, 1, 2)),
            _bar(102.0, 103.0, 101.0, 103.0, 1500.0, d=date(2026, 1, 3)),
        ]
        obv = compute_obv(bars)
        assert obv[0] == 1000.0
        assert obv[1] == 3000.0  # 1000 + 2000
        assert obv[2] == 4500.0  # 3000 + 1500

    def test_falling_bar_subtracts_volume(self) -> None:
        bars = [
            _bar(100.0, 101.0, 99.0, 101.0, 1000.0, d=date(2026, 1, 1)),
            _bar(101.0, 102.0, 99.0, 99.0, 3000.0, d=date(2026, 1, 2)),
        ]
        obv = compute_obv(bars)
        assert obv[1] == -2000.0  # 1000 - 3000

    def test_unchanged_close_neutral_granville(self) -> None:
        bars = [
            _bar(100.0, 101.0, 99.0, 100.0, 500.0, d=date(2026, 1, 1)),
            _bar(100.0, 101.0, 99.0, 100.0, 800.0, d=date(2026, 1, 2)),
        ]
        obv = compute_obv(bars)
        assert obv[1] == obv[0]  # Granville: unchanged price → unchanged OBV

    def test_empty_bars(self) -> None:
        assert compute_obv([]) == []


# ---------------------------------------------------------------------------
# 3. compute_cmf
# ---------------------------------------------------------------------------


class TestComputeCmf:
    def test_all_close_at_high_gives_positive_cmf(self) -> None:
        """Bars closing at the high → CLV = +1 → CMF should be +1."""
        bars = [_bar(99.0, 102.0, 99.0, 102.0, 1000.0, d=date(2026, 1, i + 1)) for i in range(25)]
        cmf = compute_cmf(bars, period=21)
        # All bars close at high (CLV=1) → CMF = 1.0
        for val in cmf[20:]:
            assert val == pytest.approx(1.0), f"expected +1 got {val}"

    def test_all_close_at_low_gives_negative_cmf(self) -> None:
        bars = [_bar(102.0, 102.0, 99.0, 99.0, 1000.0, d=date(2026, 1, i + 1)) for i in range(25)]
        cmf = compute_cmf(bars, period=21)
        for val in cmf[20:]:
            assert val == pytest.approx(-1.0), f"expected -1 got {val}"

    def test_warmup_period_none(self) -> None:
        bars = _make_bars(30)
        cmf = compute_cmf(bars, period=21)
        for i in range(20):
            assert cmf[i] is None

    def test_zero_range_bar_handled(self) -> None:
        """Doji bar (high == low) must not crash."""
        bars = [_bar(100.0, 100.0, 100.0, 100.0, 500.0, d=date(2026, 1, i + 1)) for i in range(25)]
        cmf = compute_cmf(bars, period=21)
        # Should produce 0.0 (mfv=0 for doji), no exception
        for val in cmf[20:]:
            # cmf = 0 / total_vol or None for zero-vol; either is acceptable
            assert val == pytest.approx(0.0) or val is None


# ---------------------------------------------------------------------------
# 4. No-Demand detection
# ---------------------------------------------------------------------------


class TestNodemand:
    def _build_no_demand_bars(self) -> list[PriceBar]:
        """Build a sequence ending in a textbook no-demand bar.

        Pattern bar at index 16:
        - Up bar (close > open)
        - Narrow spread (< 60% of avg spread)
        - Volume lower than both previous bars
        - Close in lower half (close_loc 'lower' or 'mid')
        Then bar 17 closes below bar 16 → no_demand_confirmed emitted at bar 17.
        """
        bars: list[PriceBar] = []
        # 14 baseline bars with wide spread, high volume
        for i in range(14):
            bars.append(_bar(100.0, 105.0, 95.0, 102.0, 50_000.0, d=date(2026, 1, i + 1)))
        # Two high-volume bars before the pattern (indices 14, 15)
        bars.append(_bar(102.0, 107.0, 97.0, 105.0, 60_000.0, d=date(2026, 1, 15)))
        bars.append(_bar(105.0, 110.0, 100.0, 108.0, 55_000.0, d=date(2026, 1, 16)))
        # Pattern bar (index 16): up bar, NARROW spread (1pt), low volume, close near low
        # avg_spread ≈ 10 → narrow spread = 1/10 = 0.1 < 0.6 ✓
        # volume 5000 < min(60000, 55000) ✓
        # close_loc: open=108, high=109, low=108, close=108.3 → (0.3/1) = 0.3 → 'lower' ✓
        bars.append(_bar(108.0, 109.0, 108.0, 108.3, 5_000.0, d=date(2026, 1, 17)))
        # Confirmation bar (index 17): closes below pattern bar close (108.3)
        bars.append(_bar(108.3, 109.0, 107.0, 107.5, 40_000.0, d=date(2026, 1, 18)))
        return bars

    def test_no_demand_detected_at_pattern_bar(self) -> None:
        bars = self._build_no_demand_bars()
        flags = detect_no_demand(bars)
        assert flags[16]["no_demand"] is True, "no_demand expected at index 16"

    def test_no_demand_confirmed_at_next_bar(self) -> None:
        bars = self._build_no_demand_bars()
        flags = detect_no_demand(bars)
        assert flags[17]["no_demand_confirmed"] is True, "confirmation expected at index 17"

    def test_no_demand_not_triggered_on_down_bar(self) -> None:
        """Down bar cannot be a no-demand bar (requires up bar)."""
        bars: list[PriceBar] = []
        for i in range(16):
            bars.append(_bar(100.0, 105.0, 95.0, 102.0, 50_000.0, d=date(2026, 1, i + 1)))
        # Down bar with low volume + narrow spread → should NOT trigger no_demand
        bars.append(_bar(102.0, 102.5, 101.5, 101.8, 5_000.0, d=date(2026, 1, 17)))
        flags = detect_no_demand(bars)
        assert flags[-1]["no_demand"] is False

    def test_no_demand_not_triggered_when_volume_not_low(self) -> None:
        """Volume must be less than BOTH previous two bars."""
        bars: list[PriceBar] = []
        for i in range(14):
            bars.append(_bar(100.0, 105.0, 95.0, 102.0, 10_000.0, d=date(2026, 1, i + 1)))
        bars.append(_bar(102.0, 107.0, 97.0, 105.0, 5_000.0, d=date(2026, 1, 15)))
        bars.append(_bar(105.0, 110.0, 100.0, 108.0, 5_000.0, d=date(2026, 1, 16)))
        # Volume 6000 is NOT < min(5000, 5000) → no trigger
        bars.append(_bar(108.0, 109.0, 108.0, 108.3, 6_000.0, d=date(2026, 1, 17)))
        flags = detect_no_demand(bars)
        assert flags[-1]["no_demand"] is False


# ---------------------------------------------------------------------------
# 5. No-Supply detection
# ---------------------------------------------------------------------------


class TestNoSupply:
    def _build_no_supply_bars(self) -> list[PriceBar]:
        """Build a sequence ending in a textbook no-supply bar.

        Pattern bar at index 16:
        - Down bar (close < open)
        - Narrow spread
        - Volume lower than both previous bars
        - Close in upper third (close_loc 'upper')
        Bar 17 closes above bar 16 → no_supply_confirmed at bar 17.
        """
        bars: list[PriceBar] = []
        for i in range(14):
            bars.append(_bar(100.0, 105.0, 95.0, 102.0, 50_000.0, d=date(2026, 1, i + 1)))
        bars.append(_bar(102.0, 107.0, 97.0, 105.0, 60_000.0, d=date(2026, 1, 15)))
        bars.append(_bar(105.0, 110.0, 100.0, 108.0, 55_000.0, d=date(2026, 1, 16)))
        # Pattern bar (index 16): DOWN bar (open > close), NARROW spread, low volume, close near HIGH
        # open=109.0, high=109.0, low=108.0, close=108.8 → (0.8/1.0)=0.8 → 'upper' ✓
        # down_bar: close(108.8) < open(109.0) ✓
        bars.append(_bar(109.0, 109.0, 108.0, 108.8, 4_000.0, d=date(2026, 1, 17)))
        # Confirmation bar: closes above 108.8
        bars.append(_bar(108.8, 110.0, 108.0, 109.5, 30_000.0, d=date(2026, 1, 18)))
        return bars

    def test_no_supply_detected(self) -> None:
        bars = self._build_no_supply_bars()
        flags = detect_no_supply(bars)
        assert flags[16]["no_supply"] is True

    def test_no_supply_confirmed_at_next_bar(self) -> None:
        bars = self._build_no_supply_bars()
        flags = detect_no_supply(bars)
        assert flags[17]["no_supply_confirmed"] is True

    def test_no_supply_not_on_up_bar(self) -> None:
        """Up bar cannot be a no-supply bar (requires down bar)."""
        bars: list[PriceBar] = []
        for i in range(16):
            bars.append(_bar(100.0, 105.0, 95.0, 102.0, 50_000.0, d=date(2026, 1, i + 1)))
        # Up bar — should not match no_supply
        bars.append(_bar(100.0, 101.0, 100.0, 100.8, 4_000.0, d=date(2026, 1, 17)))
        flags = detect_no_supply(bars)
        assert flags[-1]["no_supply"] is False

    def test_no_supply_weak_on_mid_close(self) -> None:
        """Down bar with narrow spread + low vol but a MID close → weak flag only.

        close_loc 'mid' is the weak no-supply variant (spec step 9); the strong
        ``no_supply`` flag (and therefore its confirmation) must NOT fire.
        """
        bars: list[PriceBar] = []
        for i in range(14):
            bars.append(_bar(100.0, 105.0, 95.0, 102.0, 50_000.0, d=date(2026, 1, i + 1)))
        bars.append(_bar(102.0, 107.0, 97.0, 105.0, 60_000.0, d=date(2026, 1, 15)))
        bars.append(_bar(105.0, 110.0, 100.0, 108.0, 55_000.0, d=date(2026, 1, 16)))
        # Pattern bar (index 16): down bar, narrow spread, low vol, MID close.
        # open=109.0, high=109.0, low=108.0, close=108.5 → (0.5/1.0)=0.5 → 'mid'.
        bars.append(_bar(109.0, 109.0, 108.0, 108.5, 4_000.0, d=date(2026, 1, 17)))
        flags = detect_no_supply(bars)
        assert flags[16]["no_supply"] is False
        assert flags[16]["no_supply_weak"] is True

    def test_no_supply_confirmed_only_after_next_bar(self) -> None:
        """The pattern bar itself must never carry no_supply_confirmed=True (1-bar delay)."""
        bars = self._build_no_supply_bars()
        flags = detect_no_supply(bars)
        assert flags[16]["no_supply_confirmed"] is False
        assert flags[17]["no_supply_confirmed"] is True


# ---------------------------------------------------------------------------
# 6. VDU (Volume Dry-Up)
# ---------------------------------------------------------------------------


class TestVdu:
    def test_vdu_detected_on_classic_dry_up(self) -> None:
        """Five consecutive bars with shrinking volume < 50% of avg."""
        # 20 baseline bars with high volume
        bars: list[PriceBar] = []
        for i in range(20):
            bars.append(_bar(100.0, 102.0, 98.0, 101.0, 20_000.0, d=date(2026, 1, i + 1)))
        # 5 dry-up bars: volume is well below average (~1000 < 50% of 20000=10000)
        # and strictly decreasing, with narrowing spread
        dry_vols = [2_000.0, 1_800.0, 1_600.0, 1_400.0, 1_000.0]
        dry_spreads = [(3.0, 1.0), (2.5, 1.2), (2.0, 1.4), (1.5, 1.6), (1.0, 1.8)]
        for j, (vol, (h_off, l_off)) in enumerate(zip(dry_vols, dry_spreads, strict=True)):
            bars.append(
                _bar(
                    101.0,
                    101.0 + h_off,
                    101.0 - l_off,
                    101.2,
                    vol,
                    d=date(2026, 1, 21 + j),
                )
            )
        flags = detect_vdu(bars, rvol_period=20, vdu_bars=5, vdu_vol_threshold=0.50)
        assert flags[-1] is True, "VDU zone end expected at last bar"

    def test_vdu_not_detected_when_volume_too_high(self) -> None:
        """If any bar in the window exceeds the threshold, no VDU."""
        bars = _make_bars(30, base_vol=20_000.0)
        # Last bar has high volume — not a dry up
        flags = detect_vdu(bars, rvol_period=20)
        assert flags[-1] is False


# ---------------------------------------------------------------------------
# 7. Climax detection via analyse_volume
# ---------------------------------------------------------------------------


class TestClimaxDetection:
    def _build_climax_top_bars(self, n_baseline: int = 25) -> list[PriceBar]:
        """Build bars ending in a climax-top bar."""
        bars: list[PriceBar] = []
        avg_vol = 10_000.0
        for i in range(n_baseline):
            bars.append(
                _bar(
                    100.0 + i,
                    101.0 + i,
                    99.0 + i,
                    100.5 + i,
                    avg_vol,
                    d=date(2026, 1, i + 1),
                )
            )
        # Climax-top bar:
        # - RVOL must be >= 3.0: volume = 40000, avg ≈ 10000 → RVOL ≈ 4.0 (well after warmup)
        # - Wide spread (> 1.4x avg spread): spread=10 vs avg≈2 → ratio=5
        # - Up bar (close > open)
        # - close_loc in ('mid', 'lower'): close near the low of the bar's range
        # - New N-bar high: close > max previous 20 closes
        # Previous max close ≈ 100.5 + 24 = 124.5
        last_i = n_baseline
        bars.append(
            _bar(
                124.6,  # open
                135.0,  # high — very wide spread
                124.5,  # low
                125.5,  # close — near low (loc ≈ (125.5-124.5)/(135-124.5)=1/10.5≈0.095 → 'lower')
                40_000.0,  # volume → RVOL ≈ 4
                d=date(2026, 1, last_i + 1),
            )
        )
        return bars

    def _build_climax_bottom_bars(self, n_baseline: int = 25) -> list[PriceBar]:
        """Build bars ending in a selling-climax (climax-bottom) bar.

        Selling climax (standard Wyckoff): down bar, very high volume, wide spread,
        close in UPPER third (sharp rebound off the low), and a new N-bar low close.
        """
        bars: list[PriceBar] = []
        avg_vol = 10_000.0
        # Steadily declining baseline so the climax bar can make a new 20-bar low close.
        for i in range(n_baseline):
            p = 130.0 - i
            bars.append(_bar(p, p + 1.0, p - 1.0, p - 0.5, avg_vol, d=date(2026, 1, i + 1)))
        # Climax-bottom bar at index n_baseline:
        # - down bar: close (104.0) < open (105.0)
        # - RVOL ≈ 3.5: volume 40000 vs avg ≈ 10000+ (after warmup)
        # - wide spread: 10.5 range vs avg ≈ 2 → ratio ≈ 4
        # - close_loc 'upper': (104-95)/(105.5-95) ≈ 0.857 → strong rebound off low
        # - new 20-bar low close: 104.0 < min prior closes (≈ 105.5)
        # - open gap vs prev close (105.5): 0.47% < 3% → not gap-excluded
        last_i = n_baseline
        bars.append(_bar(105.0, 105.5, 95.0, 104.0, 40_000.0, d=date(2026, 1, last_i + 1)))
        return bars

    def test_climax_top_detected(self) -> None:
        bars = self._build_climax_top_bars()
        results = analyse_volume(bars)
        assert results[-1].climax_top is True, "climax_top expected at last bar"
        assert results[-1].climax_bottom is False

    def test_climax_bottom_detected(self) -> None:
        bars = self._build_climax_bottom_bars()
        results = analyse_volume(bars)
        r = results[-1]
        assert r.climax_bottom is True, "climax_bottom expected at last bar"
        assert r.climax_top is False
        assert r.climax_bottom_weak is False
        assert r.close_loc == "upper"

    def test_climax_bottom_excluded_on_gap_open(self) -> None:
        """A >3% gap-open bar must NOT be classified as a climax (data-feed guard)."""
        bars = self._build_climax_bottom_bars()
        # Re-open the final bar 4% below the prior close to trip the gap guard.
        prev_close = bars[-2].close
        gapped_open = prev_close * 0.96  # 4% gap down
        last = bars[-1]
        bars[-1] = _bar(
            gapped_open,
            gapped_open + 0.5,
            gapped_open - 10.0,
            gapped_open - 9.0,  # still a down bar, close in upper third, new low
            last.volume,
            d=date(2026, 1, len(bars)),
        )
        results = analyse_volume(bars)
        assert results[-1].climax_bottom is False, "gap-open bar must be excluded from climax"

    def test_no_climax_when_rvol_low(self) -> None:
        bars = _make_bars(30)
        results = analyse_volume(bars)
        for r in results:
            assert r.climax_top is False
            assert r.climax_bottom is False


class TestDetectClimaxHelper:
    def test_helper_classifies_top(self) -> None:
        """Up bar, high RVOL, wide spread, close in lower half → 'top'."""
        bar = _bar(100.0, 110.0, 99.5, 100.5, 40_000.0)  # close near low → 'lower'
        assert detect_climax(bar, rvol=4.0, spread_pct=5.0) == "top"

    def test_helper_classifies_bottom(self) -> None:
        """Down bar, close in upper third → 'bottom'."""
        bar = _bar(105.0, 105.5, 95.0, 104.0, 40_000.0)  # close near high → 'upper'
        assert detect_climax(bar, rvol=4.0, spread_pct=5.0) == "bottom"

    def test_helper_rejects_narrow_spread(self) -> None:
        bar = _bar(105.0, 105.5, 95.0, 104.0, 40_000.0)
        # spread_pct must be > 1.4; 1.4 itself is rejected
        assert detect_climax(bar, rvol=4.0, spread_pct=1.4) == "none"

    def test_helper_rejects_low_rvol(self) -> None:
        bar = _bar(105.0, 105.5, 95.0, 104.0, 40_000.0)
        assert detect_climax(bar, rvol=2.9, spread_pct=5.0) == "none"

    def test_helper_none_on_missing_inputs(self) -> None:
        bar = _bar(105.0, 105.5, 95.0, 104.0, 40_000.0)
        assert detect_climax(bar, rvol=None, spread_pct=5.0) == "none"
        assert detect_climax(bar, rvol=4.0, spread_pct=None) == "none"


# ---------------------------------------------------------------------------
# 8. OBV divergence
# ---------------------------------------------------------------------------


class TestObvDivergence:
    def test_bearish_divergence_price_high_obv_low(self) -> None:
        """Price makes higher high but OBV EMA makes lower high → bearish divergence.

        Construction (all bars have fixed ts; position matters, not ts):
          - 22 flat bars (close=100.0, vol=5000): OBV stays flat / oscillates slightly.
          - p1 (idx=22): up bar, high close=105.0, BIG volume=50000 → OBV jumps large.
          - valley (idx=23): down bar, close=102.0, vol=5000 → OBV drops moderately.
          - p2 (idx=24): up bar, higher close=107.0 (>105), TINY volume=1000 → OBV barely rises.
          - confirm (idx=25): down bar confirming p2 as a high pivot, close=104.0.
          - trigger (idx=26): any bar → at this point both p1 and p2 are confirmed pivots.
            price[p2] > price[p1] (107 > 105) but obv_ema[p2] << obv_ema[p1] → bearish.
        """
        bars: list[PriceBar] = []
        # 22 baseline flat bars
        for _ in range(22):
            bars.append(_bar(99.5, 100.5, 99.5, 100.0, 5_000.0))
        # p1 candidate (idx=22): big up bar
        bars.append(_bar(100.0, 106.0, 99.0, 105.0, 50_000.0))
        # valley (idx=23): down bar
        bars.append(_bar(105.0, 105.5, 101.5, 102.0, 5_000.0))
        # p2 candidate (idx=24): higher price, tiny volume
        bars.append(_bar(102.0, 108.0, 101.0, 107.0, 1_000.0))
        # confirmation of p2 (idx=25): lower close than p2 → p2 is high pivot
        bars.append(_bar(107.0, 107.5, 103.0, 104.0, 5_000.0))
        # trigger bar (idx=26): p2 confirmed, both pivots visible in divergence_lookback
        bars.append(_bar(104.0, 105.0, 103.0, 103.5, 5_000.0))

        # obv_ema_period=1 means ema==obv (no lag), so divergence is based on raw OBV.
        # obv[p1]=55000, obv[p2]=51000 → obv[p2] < obv[p1] while close[p2] > close[p1] → bearish.
        results = analyse_volume(bars, obv_ema_period=1, divergence_lookback=15)
        div_signals = [r.obv_divergence for r in results[-5:]]
        assert "bearish" in div_signals, f"expected bearish divergence, got {div_signals}"

    def test_bullish_divergence_price_low_obv_high(self) -> None:
        """Price makes a lower low but OBV makes a higher low → bullish divergence.

        Mirror of the bearish construction:
          - 22 flat bars.
          - p1 (idx=22): big DOWN bar, low close=95.0, BIG volume=50000 → OBV drops large.
          - peak (idx=23): up bar, close=98.0 → OBV recovers slightly.
          - p2 (idx=24): LOWER close=93.0 (<95) but TINY volume=1000 → OBV barely drops.
          - confirm (idx=25): higher close=96.0 → p2 confirmed as a low pivot.
          - trigger (idx=26): both low pivots confirmed; price[p2] < price[p1] (93 < 95)
            while obv[p2] > obv[p1] → bullish divergence.
        """
        bars: list[PriceBar] = []
        for _ in range(22):
            bars.append(_bar(100.5, 101.5, 99.5, 100.0, 5_000.0))
        bars.append(_bar(100.0, 101.0, 94.0, 95.0, 50_000.0))  # p1 low pivot
        bars.append(_bar(95.0, 98.5, 94.5, 98.0, 5_000.0))  # peak
        bars.append(_bar(98.0, 99.0, 92.0, 93.0, 1_000.0))  # p2 lower low, tiny vol
        bars.append(_bar(93.0, 97.0, 92.5, 96.0, 5_000.0))  # confirm p2
        bars.append(_bar(96.0, 97.0, 95.0, 96.5, 5_000.0))  # trigger
        results = analyse_volume(bars, obv_ema_period=1, divergence_lookback=15)
        div_signals = [r.obv_divergence for r in results[-5:]]
        assert "bullish" in div_signals, f"expected bullish divergence, got {div_signals}"

    def test_no_divergence_when_obv_agrees_with_price(self) -> None:
        """Two confirmed high pivots where BOTH price and OBV make higher highs.

        This is the discriminating negative case: there ARE >= 2 pivots (so the
        insufficient-pivots guard does not apply), but because OBV agrees with price
        (no effort/result mismatch), no divergence may be reported.
        """
        bars: list[PriceBar] = []
        for _ in range(22):
            bars.append(_bar(99.5, 100.5, 99.5, 100.0, 5_000.0))
        bars.append(_bar(100.0, 106.0, 99.0, 105.0, 50_000.0))  # p1 high pivot, big vol
        bars.append(_bar(105.0, 105.5, 101.5, 102.0, 5_000.0))  # valley
        bars.append(_bar(102.0, 108.0, 101.0, 107.0, 60_000.0))  # p2 higher high, ALSO big vol
        bars.append(_bar(107.0, 107.5, 103.0, 104.0, 5_000.0))  # confirm p2
        bars.append(_bar(104.0, 105.0, 103.0, 103.5, 5_000.0))  # trigger
        results = analyse_volume(bars, obv_ema_period=1, divergence_lookback=15)
        for r in results:
            assert r.obv_divergence is None, "OBV agrees with price → must not signal divergence"

    def test_no_divergence_with_insufficient_pivots(self) -> None:
        """With only one pivot, no divergence should be reported."""
        bars = _make_bars(30)
        results = analyse_volume(bars)
        for r in results:
            assert r.obv_divergence is None


# ---------------------------------------------------------------------------
# 9. EVR (Effort vs Result) labels
# ---------------------------------------------------------------------------


class TestEvr:
    def test_absorption_label(self) -> None:
        """High RVOL (>=2) + narrow spread + up bar → 'absorption'."""
        bars: list[PriceBar] = []
        avg_vol = 10_000.0
        # 20 baseline bars
        for i in range(20):
            bars.append(_bar(100.0, 105.0, 95.0, 102.0, avg_vol, d=date(2026, 1, i + 1)))
        # EVR bar: up bar, RVOL ≈ 3 (30000/10000), narrow spread (0.5 vs avg≈10 → 0.05)
        bars.append(_bar(102.0, 102.3, 102.0, 102.2, 30_000.0, d=date(2026, 1, 21)))
        evr = compute_evr(bars, rvol_period=20, spread_lookback=14)
        assert evr[-1] == "absorption"

    def test_effortless_fall_label(self) -> None:
        """Low RVOL (<0.7) + wide spread + down bar → 'effortless_fall'."""
        bars: list[PriceBar] = []
        avg_vol = 10_000.0
        for i in range(20):
            bars.append(_bar(100.0, 102.0, 98.0, 101.0, avg_vol, d=date(2026, 1, i + 1)))
        # Wide spread ≈ 20 vs avg≈4 → ratio=5 (>1.4); down bar; low volume (<0.7*10000=7000)
        bars.append(_bar(101.0, 102.0, 82.0, 83.0, 5_000.0, d=date(2026, 1, 21)))
        evr = compute_evr(bars, rvol_period=20, spread_lookback=14)
        assert evr[-1] == "effortless_fall"


# ---------------------------------------------------------------------------
# 10. Full aggregator smoke test + liquidity guard
# ---------------------------------------------------------------------------


class TestAnalyseVolume:
    def test_returns_same_length_as_bars(self) -> None:
        bars = _make_bars(30)
        results = analyse_volume(bars)
        assert len(results) == 30

    def test_result_type(self) -> None:
        bars = _make_bars(25)
        results = analyse_volume(bars)
        for r in results:
            assert isinstance(r, VolumeBarResult)

    def test_liquidity_guard_low_volume(self) -> None:
        """Bars with avg volume < 1000 units → rvol_class='undefined'."""
        bars = [_bar(100.0, 101.0, 99.0, 100.5, 50.0, d=date(2026, 1, i + 1)) for i in range(25)]
        results = analyse_volume(bars)
        for r in results[19:]:  # after warmup
            assert r.rvol_class == "undefined", f"expected 'undefined', got {r.rvol_class}"

    def test_zero_range_bar_does_not_crash(self) -> None:
        """Doji bars (high==low) must not raise exceptions."""
        bars: list[PriceBar] = []
        for i in range(25):
            bars.append(_bar(100.0, 100.0, 100.0, 100.0, 5_000.0, d=date(2026, 1, i + 1)))
        results = analyse_volume(bars)
        assert len(results) == 25
        for r in results:
            assert r.close_loc == "mid"

    def test_output_fields_present(self) -> None:
        bars = _make_bars(25)
        r = analyse_volume(bars)[-1]
        assert hasattr(r, "ts")
        assert hasattr(r, "rvol")
        assert hasattr(r, "obv")
        assert hasattr(r, "cmf")
        assert hasattr(r, "no_demand")
        assert hasattr(r, "climax_top")
        assert hasattr(r, "vdu_zone_end")
        assert hasattr(r, "ob_imbalance")
        assert hasattr(r, "oi_context")
        # crypto optional fields None when not supplied
        assert r.ob_imbalance is None
        assert r.oi_context is None

    def test_adl_gap_distortion_flagged(self) -> None:
        """A bar that opens >1% away from prior close sets adl_gap_distortion=True."""
        bars = [
            _bar(100.0, 101.0, 99.0, 100.5, 5_000.0, d=date(2026, 1, 1)),
            # Open gaps up 5% from 100.5
            _bar(105.6, 106.0, 105.0, 105.5, 5_000.0, d=date(2026, 1, 2)),
        ]
        results = analyse_volume(bars)
        assert results[1].adl_gap_distortion is True
        assert results[0].adl_gap_distortion is False

    def test_no_demand_confirmed_requires_next_bar_lower(self) -> None:
        """Pattern bar itself should NOT have no_demand_confirmed=True."""
        bars: list[PriceBar] = []
        for i in range(14):
            bars.append(_bar(100.0, 105.0, 95.0, 102.0, 50_000.0, d=date(2026, 1, i + 1)))
        bars.append(_bar(102.0, 107.0, 97.0, 105.0, 60_000.0, d=date(2026, 1, 15)))
        bars.append(_bar(105.0, 110.0, 100.0, 108.0, 55_000.0, d=date(2026, 1, 16)))
        # Pattern bar (index 16): up bar, narrow spread, low vol, mid/lower close
        bars.append(_bar(108.0, 109.0, 108.0, 108.3, 5_000.0, d=date(2026, 1, 17)))
        results = analyse_volume(bars)
        # Pattern bar itself should NOT have no_demand_confirmed
        assert results[16].no_demand_confirmed is False
        assert results[16].no_demand is True


# ---------------------------------------------------------------------------
# 11. Causal / lookahead regression — the definitive guard
# ---------------------------------------------------------------------------


class TestLookaheadFree:
    """A detector is lookahead-free iff the output for bar *i* is identical whether
    or not bars after *i* exist. We verify this by re-running ``analyse_volume`` on
    every prefix ``bars[:k+1]`` and asserting bar *k*'s signals match the full run.

    This catches any signal that secretly consults a future bar to DECIDE existence
    (as opposed to a retroactive write that legitimately lands on a later index).
    """

    # Fields whose value at bar i is the model's decision *as of* bar i.
    _CAUSAL_FIELDS = (
        "rvol",
        "rvol_class",
        "obv",
        "obv_ema",
        "obv_divergence",
        "adl",
        "adl_divergence",
        "cmf",
        "cmf_signal",
        "no_demand",
        "no_demand_confirmed",
        "no_supply",
        "no_supply_weak",
        "no_supply_confirmed",
        "evr_label",
        "climax_top",
        "climax_bottom",
        "climax_bottom_weak",
        "vdu_zone_end",
    )

    def _mixed_series(self) -> list[PriceBar]:
        """A deterministic series that actually triggers VSA, climax and divergence."""
        bars: list[PriceBar] = []
        # Declining baseline so divergences and a climax-bottom can form.
        for i in range(25):
            p = 130.0 - i
            bars.append(_bar(p, p + 1.0, p - 1.0, p - 0.5, 10_000.0, d=date(2026, 1, i + 1)))
        # Selling climax bar.
        bars.append(_bar(105.0, 105.5, 95.0, 104.0, 40_000.0, d=date(2026, 2, 1)))
        # No-supply pattern + confirmation.
        bars.append(_bar(106.0, 108.0, 102.0, 104.5, 30_000.0, d=date(2026, 2, 2)))
        bars.append(_bar(105.0, 105.0, 104.0, 104.6, 3_000.0, d=date(2026, 2, 3)))  # down, narrow
        bars.append(_bar(104.6, 110.0, 104.0, 109.0, 25_000.0, d=date(2026, 2, 4)))  # confirms up
        # No-demand pattern + confirmation.
        bars.append(_bar(109.0, 112.0, 108.0, 111.0, 40_000.0, d=date(2026, 2, 5)))
        bars.append(_bar(111.0, 112.0, 111.0, 111.3, 4_000.0, d=date(2026, 2, 6)))  # up, narrow
        bars.append(_bar(111.3, 112.0, 109.0, 110.0, 30_000.0, d=date(2026, 2, 7)))  # confirms down
        return bars

    def test_signal_at_bar_i_unaffected_by_future(self) -> None:
        bars = self._mixed_series()
        full = analyse_volume(bars)
        for k in range(2, len(bars)):
            prefix = analyse_volume(bars[: k + 1])
            f = full[k]
            p = prefix[k]
            for field in self._CAUSAL_FIELDS:
                fv = getattr(f, field)
                pv = getattr(p, field)
                if isinstance(fv, float) and isinstance(pv, float):
                    assert fv == pytest.approx(pv), f"bar {k} field {field}: {fv} != {pv}"
                else:
                    assert fv == pv, f"LOOKAHEAD at bar {k}, field {field}: full={fv} prefix={pv}"

    def test_confirmation_fires_exactly_one_bar_late(self) -> None:
        """A confirmed VSA signal must be absent until its confirming bar is visible."""
        bars = self._mixed_series()
        full = analyse_volume(bars)
        # Find any confirmed no_supply/no_demand bar; assert it was False one prefix earlier.
        for k in range(1, len(bars)):
            if full[k].no_supply_confirmed or full[k].no_demand_confirmed:
                earlier = analyse_volume(bars[:k])  # confirming bar k hidden
                # The pattern bar (k-1) must not yet show a confirmation.
                assert earlier[k - 1].no_supply_confirmed is False
                assert earlier[k - 1].no_demand_confirmed is False
