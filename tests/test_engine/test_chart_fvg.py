"""Tests for engine/chart/fvg.py — FVG detector.

Synthetic PriceBar fixtures are hand-crafted to trigger (or not trigger)
each detection path.  All bars use chronologically ascending dates.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from data.models import PriceBar
from engine.chart.fvg import (
    FVGZone,
    check_fvg_mitigation,
    classify_fvg_strength,
    detect_fvg,
    detect_ifvg,
    run_fvg,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bar(
    o: float,
    h: float,
    lo: float,
    c: float,
    *,
    idx: int = 0,
    symbol: str = "BTC/USDT",
    freq: str = "4h",
    volume: float = 1000.0,
) -> PriceBar:
    """Build a minimal PriceBar.  idx is used to set distinct, ascending dates.

    Uses timedelta from a fixed epoch so large idx values (e.g. eviction tests
    with 40+ bars) do not overflow the month.
    """
    return PriceBar(
        symbol=symbol,
        market="crypto",
        source_symbol=symbol,
        ts=date(2026, 1, 1) + timedelta(days=idx),
        open=o,
        high=h,
        low=lo,
        close=c,
        volume=volume,
        freq=freq,
    )


def _make_flat_bars(n: int, price: float = 100.0, body: float = 1.0) -> list[PriceBar]:
    """Return n quiet bars with a small body to seed the ATR / avg_body baseline."""
    bars = []
    for i in range(n):
        bars.append(_bar(price, price + body, price - body, price + body, idx=i))
    return bars


# ---------------------------------------------------------------------------
# Test 1 — Positive detection: bullish FVG
# ---------------------------------------------------------------------------


def test_detect_bullish_fvg_basic() -> None:
    """A clear 3-candle bullish FVG with a large displacement candle should be detected."""
    # 20 quiet baseline bars so ATR / avg_body are well-seeded
    bars = _make_flat_bars(20, price=100.0, body=1.0)

    # c1: regular bar whose high sets zone_low
    c1 = _bar(100.0, 102.0, 98.0, 101.0, idx=20)
    # c2: large bullish displacement candle (body >> avg_body)
    c2 = _bar(102.0, 115.0, 101.5, 114.0, idx=21)
    # c3: opens high, low > c1.high  →  c3.low(106) > c1.high(102) → bullish gap
    c3 = _bar(110.0, 120.0, 106.0, 118.0, idx=22)

    bars += [c1, c2, c3]

    zones = detect_fvg(bars)

    assert len(zones) >= 1
    bull_zones = [z for z in zones if z.direction == "bullish"]
    assert bull_zones, "Expected at least one bullish FVG"

    z = bull_zones[-1]
    assert z.zone_low == pytest.approx(c1.high)  # == 102.0
    assert z.zone_high == pytest.approx(c3.low)  # == 106.0
    assert z.zone_mid == pytest.approx(104.0)
    assert z.gap_size == pytest.approx(4.0)
    assert z.formation_bar_idx == len(bars) - 1
    assert not z.mitigated
    assert z.mitigation_type == "none"


# ---------------------------------------------------------------------------
# Test 2 — Positive detection: bearish FVG
# ---------------------------------------------------------------------------


def test_detect_bearish_fvg_basic() -> None:
    """A clear 3-candle bearish FVG should be detected with correct zone boundaries."""
    bars = _make_flat_bars(20, price=200.0, body=1.0)

    # c1: bar whose low sets zone_high
    c1 = _bar(200.0, 202.0, 196.0, 197.0, idx=20)
    # c2: large bearish displacement candle
    c2 = _bar(196.0, 196.5, 182.0, 183.0, idx=21)
    # c3: c3.high(188) < c1.low(196)  → bearish gap
    c3 = _bar(185.0, 188.0, 181.0, 184.0, idx=22)

    bars += [c1, c2, c3]

    zones = detect_fvg(bars)
    bear_zones = [z for z in zones if z.direction == "bearish"]
    assert bear_zones, "Expected at least one bearish FVG"

    z = bear_zones[-1]
    assert z.zone_high == pytest.approx(c1.low)  # == 196.0
    assert z.zone_low == pytest.approx(c3.high)  # == 188.0
    assert z.zone_mid == pytest.approx(192.0)
    assert z.gap_size == pytest.approx(8.0)
    assert not z.mitigated


# ---------------------------------------------------------------------------
# Test 3 — Negative guard: no gap (bars overlap)
# ---------------------------------------------------------------------------


def test_no_fvg_when_bars_overlap() -> None:
    """When c3.low <= c1.high, no bullish gap exists — should not detect FVG."""
    bars = _make_flat_bars(20, price=100.0, body=1.0)

    c1 = _bar(100.0, 105.0, 98.0, 104.0, idx=20)
    c2 = _bar(104.0, 110.0, 103.0, 109.0, idx=21)
    # c3.low = 104.5 < c1.high = 105 → no bull gap
    c3 = _bar(108.0, 112.0, 104.5, 111.0, idx=22)

    bars += [c1, c2, c3]
    zones = detect_fvg(bars)
    # Verify none of the newly formed zones are from our added bars
    new_zones = [z for z in zones if z.formation_bar_idx == len(bars) - 1]
    assert not new_zones, "Should not detect a FVG when c3.low <= c1.high"


# ---------------------------------------------------------------------------
# Test 4 — Negative guard: displacement candle wrong direction
# ---------------------------------------------------------------------------


def test_no_fvg_when_displacement_wrong_direction() -> None:
    """For a bullish gap, c2 must be bullish (close > open). Bearish c2 → reject."""
    bars = _make_flat_bars(20, price=100.0, body=1.0)

    c1 = _bar(100.0, 102.0, 98.0, 101.0, idx=20)
    # c2 is BEARISH even though a gap exists
    c2 = _bar(110.0, 111.0, 101.5, 103.0, idx=21)  # close < open → bearish
    c3 = _bar(110.0, 118.0, 106.0, 116.0, idx=22)

    bars += [c1, c2, c3]
    zones = detect_fvg(bars)
    new_zones = [z for z in zones if z.formation_bar_idx == len(bars) - 1]
    assert not new_zones, "Bearish displacement candle should invalidate bullish FVG"


# ---------------------------------------------------------------------------
# Test 5 — Mitigation: CE touch
# ---------------------------------------------------------------------------


def test_fvg_ce_mitigation() -> None:
    """When a later bar's low touches zone_mid but not zone_low, mitigation_type='ce'."""
    bars = _make_flat_bars(20, price=100.0, body=1.0)

    c1 = _bar(100.0, 102.0, 98.0, 101.0, idx=20)
    c2 = _bar(102.0, 116.0, 101.5, 115.0, idx=21)
    c3 = _bar(110.0, 120.0, 106.0, 119.0, idx=22)
    # zone_low=102, zone_high=106, zone_mid=104

    # Bar that touches CE (low=103.9 <= zone_mid=104) but not zone_low(102)
    ce_bar = _bar(110.0, 111.0, 103.9, 110.5, idx=23)

    bars += [c1, c2, c3, ce_bar]
    zones = detect_fvg(bars)
    bull_zones = [z for z in zones if z.direction == "bullish" and z.formation_bar_idx == 22]
    assert bull_zones, "Should have detected bullish FVG at idx 22"

    z = bull_zones[0]
    assert z.mitigation_type == "ce"
    assert z.partial_mitigated_ce is True
    assert not z.mitigated, "CE touch should not set mitigated=True"


# ---------------------------------------------------------------------------
# Test 6 — Mitigation: full mitigation takes precedence over CE
# ---------------------------------------------------------------------------


def test_fvg_full_mitigation_priority_over_ce() -> None:
    """A bar that pierces zone_low must set mitigation_type='full', never 'ce'."""
    bars = _make_flat_bars(20, price=100.0, body=1.0)

    c1 = _bar(100.0, 102.0, 98.0, 101.0, idx=20)
    c2 = _bar(102.0, 116.0, 101.5, 115.0, idx=21)
    c3 = _bar(110.0, 120.0, 106.0, 119.0, idx=22)
    # zone_low=102, zone_high=106

    # Bar pierces zone_low (low=101.0 <= zone_low=102)
    full_bar = _bar(110.0, 111.0, 101.0, 110.5, idx=23)

    bars += [c1, c2, c3, full_bar]
    zones = detect_fvg(bars)
    bull_zones = [z for z in zones if z.direction == "bullish" and z.formation_bar_idx == 22]
    assert bull_zones

    z = bull_zones[0]
    assert z.mitigated is True
    assert z.mitigation_type == "full", "Full mitigation must override CE"
    assert z.mitigation_ts == full_bar.ts


# ---------------------------------------------------------------------------
# Test 7 — IFVG inversion after full mitigation
# ---------------------------------------------------------------------------


def test_ifvg_inversion_after_full_mitigation() -> None:
    """After full mitigation, a bar closing below zone_low inverts the FVG."""
    bars = _make_flat_bars(20, price=100.0, body=1.0)

    c1 = _bar(100.0, 102.0, 98.0, 101.0, idx=20)
    c2 = _bar(102.0, 116.0, 101.5, 115.0, idx=21)
    c3 = _bar(110.0, 120.0, 106.0, 119.0, idx=22)
    # zone_low=102, zone_high=106

    # Step 1: fully mitigate (low touches zone_low)
    mit_bar = _bar(110.0, 111.0, 101.5, 110.5, idx=23)
    # Step 2: inversion bar — close < zone_low=102
    inv_bar = _bar(103.0, 104.0, 100.0, 101.0, idx=24)  # close=101 < 102

    bars += [c1, c2, c3, mit_bar, inv_bar]
    zones = detect_fvg(bars)
    bull_zones = [z for z in zones if z.direction == "bullish" and z.formation_bar_idx == 22]
    assert bull_zones

    z = bull_zones[0]
    assert z.mitigated is True
    assert z.inverted is True
    assert z.ifvg_active is True
    assert z.ifvg_direction == "bearish"
    assert z.inversion_ts == inv_bar.ts


# ---------------------------------------------------------------------------
# Test 8 — IFVG requires full mitigation first (not just CE)
# ---------------------------------------------------------------------------


def test_ifvg_requires_full_mitigation_first() -> None:
    """IFVG must NOT trigger when the FVG is only CE-mitigated (never fully mitigated).

    Genuine negative case: price touches CE (zone_mid) but its low never reaches
    zone_low, so ``mitigated`` stays False. A subsequent body close below zone_low
    must still NOT invert, because Step 11 only monitors fully-mitigated FVGs.
    """
    bars = _make_flat_bars(20, price=100.0, body=1.0)

    c1 = _bar(100.0, 102.0, 98.0, 101.0, idx=20)
    c2 = _bar(102.0, 116.0, 101.5, 115.0, idx=21)
    c3 = _bar(110.0, 120.0, 106.0, 119.0, idx=22)
    # zone_low=102, zone_high=106, zone_mid=104

    # CE touch ONLY: low=103.5 reaches zone_mid(104) but stays strictly above
    # zone_low(102) → CE, not full mitigation.
    ce_bar = _bar(110.0, 111.0, 103.5, 110.5, idx=23)
    # A later bar whose body closes below zone_low BUT whose low never reached
    # zone_low at the time the FVG was still un-mitigated. Here low=102.5 stays
    # above zone_low, so the FVG is *never* fully mitigated, hence cannot invert.
    no_inv = _bar(103.5, 104.0, 102.5, 102.6, idx=24)

    bars += [c1, c2, c3, ce_bar, no_inv]
    zones = detect_fvg(bars)
    bull_zones = [z for z in zones if z.direction == "bullish" and z.formation_bar_idx == 22]
    assert bull_zones

    z = bull_zones[0]
    assert z.mitigation_type == "ce"
    assert z.mitigated is False, "Low never reached zone_low → not fully mitigated"
    assert z.inverted is False, "An un-mitigated FVG must never invert (Step 11)"
    assert z.ifvg_active is False


# ---------------------------------------------------------------------------
# Test 9 — check_fvg_mitigation helper
# ---------------------------------------------------------------------------


def test_check_fvg_mitigation_returns_correct_type() -> None:
    """check_fvg_mitigation should return 'full', 'ce', or 'none' without mutating."""
    z = FVGZone(
        fvg_id="test_id",
        symbol="BTC/USDT",
        freq="4h",
        direction="bullish",
        ts=date(2026, 1, 1),
        formation_bar_idx=2,
        zone_low=100.0,
        zone_high=110.0,
        zone_mid=105.0,
        gap_size=10.0,
        gap_size_atr=1.0,
        strength="normal",
    )

    full_bar = _bar(108.0, 109.0, 99.0, 108.5, idx=0)
    ce_bar = _bar(108.0, 109.0, 104.5, 108.0, idx=1)
    miss_bar = _bar(112.0, 114.0, 111.0, 113.0, idx=2)

    assert check_fvg_mitigation(z, full_bar) == "full"
    assert check_fvg_mitigation(z, ce_bar) == "ce"
    assert check_fvg_mitigation(z, miss_bar) == "none"
    # Zone must not be mutated
    assert not z.mitigated
    assert z.mitigation_type == "none"


# ---------------------------------------------------------------------------
# Test 10 — classify_fvg_strength helper
# ---------------------------------------------------------------------------


def test_classify_fvg_strength_returns_correct_tier() -> None:
    """classify_fvg_strength should return 'strong'/'normal'/'weak' based on gap/ATR ratio."""
    z_strong = FVGZone(
        fvg_id="id1",
        symbol="BTC/USDT",
        freq="4h",
        direction="bullish",
        ts=date(2026, 1, 1),
        formation_bar_idx=2,
        zone_low=100.0,
        zone_high=120.0,
        zone_mid=110.0,
        gap_size=20.0,
        gap_size_atr=2.0,
        strength="strong",
    )
    z_tiny = FVGZone(
        fvg_id="id2",
        symbol="BTC/USDT",
        freq="4h",
        direction="bullish",
        ts=date(2026, 1, 2),
        formation_bar_idx=5,
        zone_low=100.0,
        zone_high=100.3,
        zone_mid=100.15,
        gap_size=0.3,
        gap_size_atr=0.1,
        strength="normal",
    )

    assert classify_fvg_strength(z_strong, atr=10.0) == "strong"
    assert classify_fvg_strength(z_tiny, atr=10.0) == "weak"


# ---------------------------------------------------------------------------
# Test 11 — run_fvg aggregator partitions correctly
# ---------------------------------------------------------------------------


def test_run_fvg_partitions_active_and_mitigated() -> None:
    """run_fvg should correctly partition zones into active/mitigated sub-lists."""
    bars = _make_flat_bars(20, price=100.0, body=1.0)

    c1 = _bar(100.0, 102.0, 98.0, 101.0, idx=20)
    c2 = _bar(102.0, 116.0, 101.5, 115.0, idx=21)
    c3 = _bar(110.0, 120.0, 106.0, 119.0, idx=22)
    # Mitigation bar — low touches zone_low
    mit_bar = _bar(110.0, 111.0, 101.5, 110.5, idx=23)

    bars += [c1, c2, c3, mit_bar]
    result = run_fvg(bars)

    assert result.all_fvgs
    # The FVG we created should be in mitigated, not active
    mitigated_ids = {z.fvg_id for z in result.mitigated_fvgs}
    active_ids = {z.fvg_id for z in result.active_fvgs}
    assert mitigated_ids.isdisjoint(active_ids), "Mitigated and active must be disjoint"

    # Verify the specific FVG is mitigated
    bull_zones = [z for z in result.all_fvgs if z.direction == "bullish"]
    assert any(z.mitigated for z in bull_zones)


# ---------------------------------------------------------------------------
# Test 12 — gap below ATR threshold is filtered out
# ---------------------------------------------------------------------------


def test_small_gap_filtered_by_atr_threshold() -> None:
    """A gap smaller than min_gap_atr_mult * ATR should be rejected."""
    bars = _make_flat_bars(20, price=100.0, body=10.0)  # large body → large ATR

    # Create a tiny gap: c3.low(102.01) vs c1.high(102.0) → gap=0.01
    c1 = _bar(100.0, 102.0, 98.0, 101.0, idx=20)
    c2 = _bar(102.0, 115.0, 101.5, 114.0, idx=21)
    c3 = _bar(110.0, 120.0, 102.01, 118.0, idx=22)

    bars += [c1, c2, c3]
    # With large ATR (~20), min_gap=0.15*20=3.0; our gap=0.01 << 3.0
    zones = detect_fvg(bars, min_gap_atr_mult=0.15)
    new_zones = [z for z in zones if z.formation_bar_idx == len(bars) - 1]
    assert not new_zones, "Tiny gap should be filtered by ATR threshold"


# ---------------------------------------------------------------------------
# Test 13 — detect_ifvg returns only active IFVG zones
# ---------------------------------------------------------------------------


def test_detect_ifvg_returns_active_only() -> None:
    """detect_ifvg should return only zones where ifvg_active=True."""
    bars = _make_flat_bars(20, price=100.0, body=1.0)

    c1 = _bar(100.0, 102.0, 98.0, 101.0, idx=20)
    c2 = _bar(102.0, 116.0, 101.5, 115.0, idx=21)
    c3 = _bar(110.0, 120.0, 106.0, 119.0, idx=22)
    mit_bar = _bar(110.0, 111.0, 101.5, 110.5, idx=23)
    inv_bar = _bar(103.0, 104.0, 100.0, 101.0, idx=24)  # close=101 < zone_low=102

    bars += [c1, c2, c3, mit_bar, inv_bar]
    zones = detect_fvg(bars)
    active_ifvgs = detect_ifvg(bars, zones)

    assert any(z.ifvg_active for z in active_ifvgs), "Expected at least one active IFVG"
    assert all(z.ifvg_active for z in active_ifvgs), "detect_ifvg must only return active IFVGs"


# ---------------------------------------------------------------------------
# Test 14 — Bearish FVG inverts to a bullish IFVG (mirror of Test 7)
# ---------------------------------------------------------------------------


def test_bearish_fvg_inverts_to_bullish_ifvg() -> None:
    """A fully-mitigated bearish FVG whose later body closes above zone_high inverts."""
    bars = _make_flat_bars(20, price=200.0, body=1.0)

    c1 = _bar(200.0, 202.0, 196.0, 197.0, idx=20)
    c2 = _bar(196.0, 196.5, 182.0, 183.0, idx=21)
    c3 = _bar(185.0, 188.0, 181.0, 184.0, idx=22)
    # bearish zone: zone_high=196 (c1.low), zone_low=188 (c3.high), zone_mid=192

    # Full mitigation: a later bar's high reaches zone_high(196)
    mit_bar = _bar(190.0, 196.5, 189.0, 190.0, idx=23)
    # Inversion: body close above zone_high(196)
    inv_bar = _bar(195.0, 199.0, 194.0, 198.0, idx=24)  # close=198 > 196

    bars += [c1, c2, c3, mit_bar, inv_bar]
    zones = detect_fvg(bars)
    bear_zones = [z for z in zones if z.direction == "bearish" and z.formation_bar_idx == 22]
    assert bear_zones

    z = bear_zones[0]
    assert z.mitigated is True
    assert z.inverted is True
    assert z.ifvg_active is True
    assert z.ifvg_direction == "bullish", "Bearish FVG must invert to a bullish IFVG"
    assert z.inversion_ts == inv_bar.ts


# ---------------------------------------------------------------------------
# Test 15 — Same-bar full mitigation AND inversion (state-machine ordering)
# ---------------------------------------------------------------------------


def test_same_bar_full_mitigation_and_inversion() -> None:
    """One violent bar may both fully mitigate and invert; both flags must be set.

    Spec 함정: 'process full mitigation first, then if the inversion condition is
    also met, mark inverted on the same bar.'
    """
    bars = _make_flat_bars(20, price=100.0, body=1.0)

    c1 = _bar(100.0, 102.0, 98.0, 101.0, idx=20)
    c2 = _bar(102.0, 116.0, 101.5, 115.0, idx=21)
    c3 = _bar(110.0, 120.0, 106.0, 119.0, idx=22)
    # zone_low=102, zone_high=106

    # Single bar: low=100 (<= zone_low → full) AND close=101 (< zone_low → invert)
    violent = _bar(105.0, 106.0, 100.0, 101.0, idx=23)

    bars += [c1, c2, c3, violent]
    zones = detect_fvg(bars)
    z = [z for z in zones if z.direction == "bullish" and z.formation_bar_idx == 22][0]

    assert z.mitigated is True
    assert z.mitigation_type == "full"
    assert z.inverted is True
    assert z.ifvg_direction == "bearish"
    assert z.mitigation_ts == violent.ts
    assert z.inversion_ts == violent.ts


# ---------------------------------------------------------------------------
# Test 16 — max_active_fvgs eviction must not fabricate mitigation / IFVG
# ---------------------------------------------------------------------------


def test_eviction_does_not_fabricate_mitigation_or_ifvg() -> None:
    """A zone dropped purely for capacity must NOT be reported as mitigated, and
    must NEVER invert into a phantom IFVG even if a later bar closes through it.

    Regression guard: the previous implementation marked the evicted zone
    mitigated_type='full' (with mitigation_ts=None), which let the IFVG monitor
    invert a zone price never actually touched — injecting a fake signal.
    """
    bars = _make_flat_bars(20, price=100.0, body=1.0)

    # FVG #1: zone_low=102, zone_high=106. Price will stay far above it.
    c1 = _bar(100.0, 102.0, 98.0, 101.0, idx=20)
    c2 = _bar(102.0, 116.0, 101.5, 115.0, idx=21)
    c3 = _bar(110.0, 120.0, 106.0, 119.0, idx=22)
    bars += [c1, c2, c3]

    # Quiet bars well above the zone — FVG #1 is never touched by price.
    for k in range(23, 40):
        bars.append(_bar(118.0, 120.0, 116.0, 119.0, idx=k))

    # FVG #2 — forces eviction of FVG #1 when max_active_fvgs=1.
    c1b = _bar(118.0, 119.0, 116.0, 118.0, idx=39)
    c2b = _bar(118.0, 135.0, 117.5, 134.0, idx=40)
    c3b = _bar(130.0, 140.0, 125.0, 139.0, idx=41)
    bars += [c1b, c2b, c3b]

    # Later bars that close below FVG #1's zone_low(102) — would have spuriously
    # inverted the (falsely-mitigated) evicted zone under the old code.
    for k in range(42, 46):
        bars.append(_bar(101.0, 101.5, 100.0, 101.0, idx=k))

    result = run_fvg(bars, max_active_fvgs=1)
    z1 = [z for z in result.all_fvgs if z.formation_bar_idx == 22][0]

    assert z1.evicted is True
    assert z1.mitigated is False, "Capacity eviction must not fabricate a mitigation"
    assert z1.mitigation_type == "none"
    assert z1.inverted is False, "Evicted (untouched) zone must never invert"
    assert z1.ifvg_active is False
    # Excluded from actionable sub-lists but retained for audit in all_fvgs.
    assert z1 not in result.active_fvgs
    assert z1 not in result.mitigated_fvgs
    assert z1 not in result.active_ifvgs
    assert z1 in result.all_fvgs
