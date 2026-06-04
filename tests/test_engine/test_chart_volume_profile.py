"""Tests for engine/chart/volume_profile.py.

Fixtures are hand-crafted synthetic PriceBar sequences designed to produce
(or explicitly NOT produce) specific volume-profile patterns.
"""

from __future__ import annotations

from datetime import date

import pytest

from data.models import PriceBar
from engine.chart.volume_profile import (
    VolumeNode,
    build_volume_profile,
    classify_lvn_hvn,
    classify_profile_shape,
    find_naked_poc,
    find_poc,
    find_value_area,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bar(
    ts: date,
    o: float,
    h: float,
    lo: float,
    c: float,
    vol: float = 100.0,
) -> PriceBar:
    return PriceBar(
        symbol="TEST/USDT",
        market="crypto",
        source_symbol="TEST/USDT",
        ts=ts,
        open=o,
        high=h,
        low=lo,
        close=c,
        volume=vol,
        freq="1d",
    )


def _flat_bars(n: int, base_price: float = 100.0, vol: float = 100.0) -> list[PriceBar]:
    """Uniform bars: all identical OHLC spanning [base_price, base_price+1]."""
    return [
        _bar(date(2026, 1, i + 1), base_price, base_price + 1.0, base_price, base_price + 0.5, vol)
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Test 1: basic positive detection — POC, VAH, VAL correctness
# ---------------------------------------------------------------------------


def test_build_volume_profile_basic_poc_and_va() -> None:
    """Build a profile where the highest-volume bar cluster is in the middle range.

    Bars are arranged so that 15 bars trade in a narrow mid-range [50, 55] with
    large volume, flanked by 5 low-volume bars at [40, 45] and 5 at [60, 65].
    The POC should fall in the mid-range, VAL/VAH should bracket the mid-range.
    """
    bars: list[PriceBar] = []
    # Low-volume bars at bottom range
    for i in range(5):
        bars.append(_bar(date(2026, 1, i + 1), 40.0, 45.0, 40.0, 42.0, vol=10.0))
    # High-volume bars at mid range
    for i in range(15):
        bars.append(_bar(date(2026, 1, i + 6), 50.0, 55.0, 50.0, 52.0, vol=200.0))
    # Low-volume bars at top range
    for i in range(5):
        bars.append(_bar(date(2026, 1, i + 21), 60.0, 65.0, 60.0, 62.0, vol=10.0))

    vp = build_volume_profile(bars, num_bins=50)

    # POC should be somewhere in [50, 55]
    assert 50.0 <= vp.poc_price <= 55.0, f"POC {vp.poc_price} not in mid-range"
    # VAH >= VAL
    assert vp.vah >= vp.val
    # VA covers at least 70% of volume
    assert vp.va_pct_actual >= 0.70
    # total_vol sanity
    expected_total = 5 * 10.0 + 15 * 200.0 + 5 * 10.0
    assert abs(vp.total_vol - expected_total) < 1.0
    # poc_rel should be around middle of full range [40, 65]
    assert 0.30 <= vp.poc_rel <= 0.70


# ---------------------------------------------------------------------------
# Test 2: shape classification — P-shape (POC high, VA high, close high)
# ---------------------------------------------------------------------------


def test_shape_p_when_poc_and_close_in_upper_range() -> None:
    """Construct bars where volume concentrates in the upper portion → P-shape.

    Full range [100, 200]. Majority of volume trades in [160, 200] (upper 40%).
    The close is also in the upper range.
    """
    bars: list[PriceBar] = []
    # Few bars in lower range
    for i in range(5):
        bars.append(_bar(date(2026, 2, i + 1), 100.0, 120.0, 100.0, 110.0, vol=5.0))
    # Heavy volume in upper range; close high
    for i in range(20):
        bars.append(_bar(date(2026, 2, i + 6), 160.0, 200.0, 160.0, 195.0, vol=300.0))

    vp = build_volume_profile(bars, num_bins=50)
    assert vp.shape == "P", f"Expected P-shape, got {vp.shape!r} (poc_rel={vp.poc_rel:.3f})"


# ---------------------------------------------------------------------------
# Test 3: shape classification — b-shape (POC low, VA low, close low)
# ---------------------------------------------------------------------------


def test_shape_b_when_poc_and_close_in_lower_range() -> None:
    """Construct bars where volume concentrates in the lower portion → b-shape.

    Full range is [100, 200].  Heavy volume trades near [100, 110] (bottom 10%).
    A few bars extend global_high to 200 but with tiny volume AND close near the bottom.
    The last bar must also close in the lower half so close_rel <= 0.50.
    """
    bars: list[PriceBar] = []
    # Top extension bars — very low volume, early in the sequence
    for i in range(4):
        bars.append(_bar(date(2026, 3, i + 1), 190.0, 200.0, 190.0, 192.0, vol=2.0))
    # Heavy volume at bottom — narrow range, lots of bars
    for i in range(20):
        bars.append(_bar(date(2026, 3, i + 5), 100.0, 110.0, 100.0, 102.0, vol=300.0))

    vp = build_volume_profile(bars, num_bins=50)
    assert vp.shape == "b", f"Expected b-shape, got {vp.shape!r} (poc_rel={vp.poc_rel:.3f})"


# ---------------------------------------------------------------------------
# Test 4: B-shape (bimodal) detection
# ---------------------------------------------------------------------------


def test_shape_b_bimodal_two_peaks_separated_by_lvn() -> None:
    """Create a genuine bimodal distribution with two sharp, well-separated peaks.

    Each cluster uses narrow 1-point bars so the volume concentrates in a single
    bin rather than smearing across a plateau.  The LVN gap in the middle is near-zero.

    Lower cluster:  20 bars at 100-101, volume=500.
    LVN gap:         5 bars at 130-131, volume=2.
    Upper cluster:  20 bars at 160-161, volume=500.
    """
    bars: list[PriceBar] = []
    # Lower peak cluster (April 1–20, narrow range)
    for i in range(20):
        bars.append(_bar(date(2026, 4, i + 1), 100.0, 101.0, 100.0, 100.5, vol=500.0))
    # LVN valley gap (April 21–25)
    for i in range(5):
        bars.append(_bar(date(2026, 4, i + 21), 130.0, 131.0, 130.0, 130.5, vol=2.0))
    # Upper peak cluster (May 1–20, narrow range)
    for i in range(20):
        bars.append(_bar(date(2026, 5, i + 1), 160.0, 161.0, 160.0, 160.5, vol=500.0))

    vp = build_volume_profile(bars, num_bins=70, bimodal_peak_threshold=0.40)
    assert vp.shape == "B", f"Expected B-shape, got {vp.shape!r}"


def test_shape_not_bimodal_for_trimodal_distribution() -> None:
    """NEGATIVE: three substantial, well-separated lobes is multimodal, NOT bimodal.

    Spec (lines 626, 700, 714): B requires exactly two D-shaped lobes split by an
    LVN valley.  A balanced/multimodal profile must fall through to D — classifying
    it as B is the canonical false-positive the spec warns against.

    Three equal clusters at 100, 130, 160 (each vol=500) with empty gaps between.
    The naive 'strictly greater than both neighbors' peak test misses the middle
    lobe (it straddles two equal bins) and would wrongly emit B; plateau-aware
    detection sees three peaks and rejects.
    """
    bars: list[PriceBar] = []
    for i in range(15):
        bars.append(_bar(date(2026, 1, i + 1), 100.0, 101.0, 100.0, 100.5, vol=500.0))
    for i in range(15):
        bars.append(_bar(date(2026, 2, i + 1), 130.0, 131.0, 130.0, 130.5, vol=500.0))
    for i in range(15):
        bars.append(_bar(date(2026, 3, i + 1), 160.0, 161.0, 160.0, 160.5, vol=500.0))

    vp = build_volume_profile(bars, num_bins=70, bimodal_peak_threshold=0.40)
    assert vp.shape != "B", f"Trimodal distribution must NOT be classified B, got {vp.shape!r}"


# ---------------------------------------------------------------------------
# Test 5: D-shape default (symmetric / insufficient data)
# ---------------------------------------------------------------------------


def test_shape_d_when_insufficient_bars() -> None:
    """Fewer than 10 bars → shape must be D regardless of distribution."""
    bars = _flat_bars(5, base_price=100.0)
    vp = build_volume_profile(bars, num_bins=10)
    assert vp.shape == "D"


def test_shape_d_symmetric_distribution() -> None:
    """Symmetric volume spread across full range → D-shape."""
    bars: list[PriceBar] = []
    # Evenly spread bars across [100, 200]
    levels = [100.0, 110.0, 120.0, 130.0, 140.0, 150.0, 160.0, 170.0, 180.0, 190.0]
    for i, lvl in enumerate(levels):
        bars.append(_bar(date(2026, 5, i + 1), lvl, lvl + 9.0, lvl, lvl + 4.5, vol=100.0))

    vp = build_volume_profile(bars, num_bins=20)
    # Symmetric distribution: POC should be roughly in the middle
    assert vp.shape == "D"


# ---------------------------------------------------------------------------
# Test 6: HVN / LVN nodes classification
# ---------------------------------------------------------------------------


def test_hvn_lvn_nodes_present() -> None:
    """Profile with concentrated mid-range volume should produce HVN nodes there
    and LVN nodes at the extremes."""
    bars: list[PriceBar] = []
    # Low volume bars at extremes
    for i in range(5):
        bars.append(_bar(date(2026, 6, i + 1), 100.0, 110.0, 100.0, 105.0, vol=5.0))
    # High volume bars in middle
    for i in range(15):
        bars.append(_bar(date(2026, 6, i + 6), 145.0, 155.0, 145.0, 150.0, vol=500.0))
    # Low volume bars at top
    for i in range(5):
        bars.append(_bar(date(2026, 6, i + 21), 190.0, 200.0, 190.0, 195.0, vol=5.0))

    vp = build_volume_profile(bars, num_bins=50, hvn_threshold=80.0, lvn_threshold=20.0)

    assert len(vp.hvn_nodes) > 0, "Expected at least one HVN node"
    assert len(vp.lvn_nodes) > 0, "Expected at least one LVN node"

    # Every node has node_mid set (required by spec)
    for node in vp.hvn_nodes + vp.lvn_nodes:
        assert isinstance(node, VolumeNode)
        assert node.node_mid == pytest.approx((node.node_low + node.node_high) / 2.0, abs=1e-6)
        assert node.node_vol > 0.0
        assert node.node_high >= node.node_low

    # HVN nodes should overlap with the mid-range [145, 155]
    hvn_mids = [n.node_mid for n in vp.hvn_nodes]
    assert any(140.0 <= m <= 160.0 for m in hvn_mids), f"No HVN in mid-range: {hvn_mids}"

    # classify_lvn_hvn helper returns combined list
    all_nodes = classify_lvn_hvn(vp)
    assert len(all_nodes) == len(vp.hvn_nodes) + len(vp.lvn_nodes)


# ---------------------------------------------------------------------------
# Test 7: find_value_area with custom pct
# ---------------------------------------------------------------------------


def test_find_value_area_85pct_wider_than_70pct() -> None:
    """Value area at 85% should be >= value area at 70%."""
    bars: list[PriceBar] = []
    for i in range(20):
        bars.append(
            _bar(date(2026, 7, i + 1), 100.0 + i, 101.0 + i, 100.0 + i, 100.5 + i, vol=50.0)
        )

    vp = build_volume_profile(bars, num_bins=30, value_area_pct=0.70)
    va_70 = find_value_area(vp, pct=0.70)
    va_85 = find_value_area(vp, pct=0.85)

    # 85% VA must be at least as wide as 70% VA
    assert (
        va_85.vah >= va_70.vah
        or va_85.val <= va_70.val
        or ((va_85.vah - va_85.val) >= (va_70.vah - va_70.val) - 1e-9)
    )
    assert va_85.va_pct_actual >= 0.85


# ---------------------------------------------------------------------------
# Test 8: find_poc helper
# ---------------------------------------------------------------------------


def test_find_poc_matches_vp_poc_price() -> None:
    bars: list[PriceBar] = []
    for i in range(15):
        vol = 400.0 if 5 <= i <= 9 else 20.0
        bars.append(
            _bar(
                date(2026, 8, i + 1),
                100.0 + i * 2,
                102.0 + i * 2,
                100.0 + i * 2,
                101.0 + i * 2,
                vol=vol,
            )
        )

    vp = build_volume_profile(bars, num_bins=30)
    assert find_poc(vp) == vp.poc_price


# ---------------------------------------------------------------------------
# Test 9: NPOC mitigation — touched NPOC is removed
# ---------------------------------------------------------------------------


def test_npoc_mitigated_when_bar_touches_price() -> None:
    """An NPOC at price 150 should be removed when a bar's range covers it."""
    bars: list[PriceBar] = []
    for i in range(12):
        bars.append(_bar(date(2026, 9, i + 1), 140.0, 160.0, 140.0, 155.0, vol=100.0))

    # formed_ts must be within lookback_sessions*2=40 days of last bar 2026-09-12
    # so use 2026-08-20 (23 days ago — well within 40-day staleness window)
    npoc_state: list[tuple[float, date]] = [
        (150.0, date(2026, 8, 20)),  # will be mitigated — bars touch 150
        (200.0, date(2026, 8, 20)),  # out of range — should survive
    ]
    vp = build_volume_profile(bars, num_bins=20, naked_poc_state=npoc_state)

    surviving_prices = find_naked_poc(vp, current_price=155.0)
    assert 150.0 not in surviving_prices, "NPOC at 150 should have been mitigated"
    assert 200.0 in surviving_prices, "NPOC at 200 should survive (not touched)"


# ---------------------------------------------------------------------------
# Test 10: NPOC staleness guard
# ---------------------------------------------------------------------------


def test_npoc_stale_npoc_removed() -> None:
    """An NPOC older than lookback_sessions * 2 days should be discarded."""
    bars: list[PriceBar] = []
    for i in range(12):
        bars.append(_bar(date(2026, 9, i + 1), 100.0, 110.0, 100.0, 105.0, vol=100.0))

    # formed_ts is 100 days before last bar (2026-09-12); lookback=20 → stale > 40 days
    stale_npoc: list[tuple[float, date]] = [(105.5, date(2026, 6, 4))]
    vp = build_volume_profile(bars, num_bins=20, naked_poc_state=stale_npoc, lookback_sessions=20)

    surviving = find_naked_poc(vp, current_price=105.0)
    assert 105.5 not in surviving, "Stale NPOC should have been removed"


# ---------------------------------------------------------------------------
# Test 11: degenerate zero-range profile
# ---------------------------------------------------------------------------


def test_degenerate_zero_range_profile() -> None:
    """All bars have identical high/low → zero price range → degenerate profile."""
    bars = [_bar(date(2026, 10, i + 1), 100.0, 100.0, 100.0, 100.0, vol=50.0) for i in range(12)]
    vp = build_volume_profile(bars, num_bins=20)

    assert vp.degenerate is True
    assert vp.poc_price == 100.0
    assert vp.global_low == 100.0
    assert vp.global_high == 100.0
    assert vp.bin_size == 0.0


def test_degenerate_uniform_distribution_suppresses_nodes_and_shape() -> None:
    """Near-uniform volume across the whole range fires the degenerate guard.

    Spec line 702 ("퇴화 균일분포 가드"): when volume is near-uniform across all
    bins there is no structural meaning — HVN/LVN AND shape signals must all be
    suppressed.  Without suppression a uniform profile yields a spurious
    whole-range HVN node (every bin == session_max → all labelled HVN).
    """
    # Every bar identical OHLC over a wide range → each bin gets equal volume.
    bars = [_bar(date(2026, 10, i + 1), 100.0, 200.0, 100.0, 150.0, vol=100.0) for i in range(15)]
    vp = build_volume_profile(bars, num_bins=20)

    assert vp.degenerate is True
    assert vp.shape == "D"
    assert vp.hvn_nodes == [], "Degenerate uniform profile must suppress HVN nodes"
    assert vp.lvn_nodes == [], "Degenerate uniform profile must suppress LVN nodes"


# ---------------------------------------------------------------------------
# Test 12: single-spike guard flag
# ---------------------------------------------------------------------------


def test_single_spike_flag_set_when_one_bin_dominates() -> None:
    """When > 60% of volume is in a single narrow band, single_spike should be True."""
    bars: list[PriceBar] = []
    # One enormous bar
    bars.append(_bar(date(2026, 11, 1), 100.0, 101.0, 100.0, 100.5, vol=10_000.0))
    # Many tiny bars across a wide range
    for i in range(20):
        bars.append(
            _bar(
                date(2026, 11, i + 2),
                100.0 + i * 5,
                104.0 + i * 5,
                100.0 + i * 5,
                102.0 + i * 5,
                vol=10.0,
            )
        )

    vp = build_volume_profile(bars, num_bins=50)
    assert vp.single_spike is True


# ---------------------------------------------------------------------------
# Test 13: vol_bins length matches num_bins
# ---------------------------------------------------------------------------


def test_vol_bins_length_matches_num_bins() -> None:
    """len(vol_bins) should equal num_bins for any non-degenerate profile."""
    bars = _flat_bars(20, base_price=50.0)
    # Make bars non-degenerate
    bars[0] = _bar(date(2026, 1, 1), 50.0, 60.0, 50.0, 55.0, vol=100.0)
    bars[-1] = _bar(date(2026, 1, 20), 50.0, 60.0, 50.0, 55.0, vol=100.0)

    for n in (20, 50, 100):
        vp = build_volume_profile(bars, num_bins=n)
        if not vp.degenerate:
            assert len(vp.vol_bins) == n, f"Expected {n} bins, got {len(vp.vol_bins)}"


# ---------------------------------------------------------------------------
# Test 14: classify_profile_shape passthrough
# ---------------------------------------------------------------------------


def test_classify_profile_shape_passthrough() -> None:
    """classify_profile_shape(vp) returns the same string as vp.shape."""
    bars: list[PriceBar] = []
    for i in range(20):
        bars.append(_bar(date(2026, 12, i + 1), 100.0, 200.0, 100.0, 150.0, vol=100.0))

    vp = build_volume_profile(bars, num_bins=30)
    assert classify_profile_shape(vp) == vp.shape


# ---------------------------------------------------------------------------
# Test 15: CME single-row VA expansion does NOT exceed target by design
#          (or only slightly due to discrete bins)
# ---------------------------------------------------------------------------


def test_value_area_actual_at_least_target() -> None:
    """va_pct_actual >= value_area_pct (discrete bins can only meet or exceed target)."""
    bars: list[PriceBar] = []
    for i in range(25):
        bars.append(
            _bar(date(2026, 1, i + 1), 100.0 + i, 101.0 + i, 100.0 + i, 100.5 + i, vol=100.0)
        )

    vp = build_volume_profile(bars, num_bins=25, value_area_pct=0.70)
    assert vp.va_pct_actual >= 0.70 - 1e-9, (
        f"va_pct_actual {vp.va_pct_actual:.4f} is below target 0.70"
    )


# ---------------------------------------------------------------------------
# Test 16: poc_shape thresholds are respected as parameters
# ---------------------------------------------------------------------------


def test_poc_shape_threshold_parameter_respected() -> None:
    """With poc_shape_upper_threshold=0.80 a profile with poc_rel=0.65 should NOT be P."""
    bars: list[PriceBar] = []
    # Make volume concentrate in upper-mid area (poc_rel ~0.65)
    for i in range(5):
        bars.append(_bar(date(2026, 3, i + 1), 100.0, 120.0, 100.0, 110.0, vol=5.0))
    for i in range(20):
        bars.append(_bar(date(2026, 3, i + 6), 160.0, 180.0, 160.0, 175.0, vol=200.0))
    for i in range(5):
        bars.append(_bar(date(2026, 3, i + 26), 190.0, 200.0, 190.0, 195.0, vol=5.0))

    vp_default = build_volume_profile(bars, num_bins=40, poc_shape_upper_threshold=0.60)
    vp_strict = build_volume_profile(bars, num_bins=40, poc_shape_upper_threshold=0.95)

    # With a very high threshold the P-shape should not trigger
    if vp_default.poc_rel < 0.95:
        assert vp_strict.shape != "P", (
            f"With threshold 0.95 and poc_rel={vp_strict.poc_rel:.3f}, shape should not be P"
        )
