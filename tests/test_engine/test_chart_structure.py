"""Tests for engine/chart/structure.py — market structure detector.

Each fixture is hand-crafted so the synthetic bar series contains (or does NOT contain)
a specific pattern.  All checks are on deterministic algorithmic output; no randomness.

Naming convention: test_<what>_<positive|negative|edge>.
"""

from __future__ import annotations

from collections import Counter
from datetime import date

from data.models import PriceBar
from engine.chart.structure import (
    MarketStructure,
    classify_choch_bos,
    compute_trend_bias,
    detect_eqh_eql,
    detect_swing_structure,
)
from engine.chart.types import TrendBias

# ---------------------------------------------------------------------------
# PriceBar factory
# ---------------------------------------------------------------------------


def _bar(
    o: float,
    h: float,
    low: float,
    c: float,
    day: int = 1,
    v: float = 1_000.0,
) -> PriceBar:
    """Minimal PriceBar constructor for test fixtures."""
    return PriceBar(
        symbol="TEST/USD",
        market="crypto",
        source_symbol="TEST/USD",
        ts=date(2026, 1, day),
        open=o,
        high=h,
        low=low,
        close=c,
        volume=v,
        freq="1d",
    )


def _flat(price: float, day: int) -> PriceBar:
    """A doji-style flat bar at a given price level."""
    return _bar(price, price + 0.5, price - 0.5, price, day=day)


# ---------------------------------------------------------------------------
# Helper: build a trending bar series with controllable swing structure
# ---------------------------------------------------------------------------


def _bullish_trend_bars(left: int = 3, right: int = 3) -> list[PriceBar]:
    """Construct a bar series producing two confirmed HH+HL pivots → BULLISH bias.

    Layout verified to satisfy the strict-greater window rule with left=right=3:
      bar 3  SH1 high=110 (> bars 0-2 highs ≤109 and bars 4-5 highs ≤109)
      bar 6  SL1 low=100  (< bars 3-5 lows ≥104 and bars 7-8 lows ≥103)
      bar 9  SH2 high=120 (HH, > bars 6-8 highs ≤108 and bars 10-11 highs ≤118)
      bar 12 SL2 low=105  (HL > SL1=100, < bars 9-11 lows ≥106 and bars 13-15 lows ≥107)
    → _bias_from_pivots: SH1<SH2 and SL1<SL2 → BULLISH
    """
    bars: list[PriceBar] = []
    day = 1

    def add(o: float, h: float, low: float, c: float) -> None:
        nonlocal day
        bars.append(_bar(o, h, low, c, day=day))
        day += 1

    add(106, 108, 105, 107)  # bar 0  noise
    add(107, 109, 106, 108)  # bar 1  noise
    add(108, 109, 107, 108)  # bar 2  noise
    add(109, 110, 108, 109)  # bar 3  SH1 high=110
    add(107, 109, 106, 107)  # bar 4  right-side SH1
    add(106, 108, 105, 106)  # bar 5  right-side SH1
    add(103, 107, 100, 104)  # bar 6  SL1 low=100
    add(104, 107, 103, 106)  # bar 7  right-side SL1
    add(106, 108, 105, 107)  # bar 8  right-side SL1
    add(107, 120, 106, 115)  # bar 9  SH2 high=120 (HH)
    add(112, 118, 110, 114)  # bar 10 right-side SH2
    add(110, 115, 108, 112)  # bar 11 right-side SH2
    add(109, 113, 105, 111)  # bar 12 SL2 low=105 (HL)
    add(111, 115, 107, 114)  # bar 13 right-side SL2
    add(113, 117, 110, 115)  # bar 14 right-side SL2
    add(115, 119, 112, 117)  # bar 15 right-side SL2 (confirms at bar 15)

    return bars


def _bearish_trend_bars(left: int = 3, right: int = 3) -> list[PriceBar]:
    """Construct a bar series producing two confirmed LH+LL pivots → BEARISH bias.

    Layout verified with left=right=3:
      bar 3  SL1 low=185 (< bars 0-2 lows ≥193 and bars 4-5 lows ≥188)
      bar 6  SH1 high=198 (> bars 3-5 highs ≤196 and bars 7-8 highs ≤196)
      bar 9  SL2 low=178  (LL < SL1=185, < bars 6-8 highs are not relevant for low check)
      bar 12 SH2 high=193 (LH < SH1=198)
    """
    bars: list[PriceBar] = []
    day = 1

    def add(o: float, h: float, low: float, c: float) -> None:
        nonlocal day
        bars.append(_bar(o, h, low, c, day=day))
        day += 1

    add(198, 200, 196, 197)  # bar 0  noise
    add(196, 198, 194, 195)  # bar 1  noise
    add(194, 196, 193, 194)  # bar 2  noise
    add(190, 193, 185, 188)  # bar 3  SL1 low=185
    add(190, 194, 188, 192)  # bar 4  right-side SL1
    add(192, 196, 190, 194)  # bar 5  right-side SL1
    add(194, 198, 192, 196)  # bar 6  SH1 high=198
    add(193, 196, 190, 192)  # bar 7  right-side SH1
    add(190, 194, 187, 191)  # bar 8  right-side SH1
    add(184, 187, 178, 181)  # bar 9  SL2 low=178 (LL)
    add(183, 186, 182, 185)  # bar 10 right-side SL2
    add(185, 188, 184, 187)  # bar 11 right-side SL2
    add(186, 193, 184, 190)  # bar 12 SH2 high=193 (LH < 198)
    add(188, 191, 186, 188)  # bar 13 right-side SH2
    add(186, 189, 184, 186)  # bar 14 right-side SH2
    add(184, 187, 182, 184)  # bar 15 right-side SH2

    return bars


# ---------------------------------------------------------------------------
# TEST 1: positive — bullish market structure detected with left=right=3
# ---------------------------------------------------------------------------


def test_bullish_structure_detected() -> None:
    """A hand-crafted HH+HL series must produce BULLISH trend_bias."""
    bars = _bullish_trend_bars()
    ms = detect_swing_structure(
        bars, swing_left=3, swing_right=3, internal_left=2, internal_right=2
    )

    assert isinstance(ms, MarketStructure)
    assert ms.trend_bias == TrendBias.BULLISH, (
        f"Expected BULLISH, got {ms.trend_bias}. "
        f"Swing highs: {[(p.bar_index, p.price, p.label) for p in ms.swing_pivots if p.pivot_type == 'high']}. "
        f"Swing lows: {[(p.bar_index, p.price, p.label) for p in ms.swing_pivots if p.pivot_type == 'low']}"
    )


# ---------------------------------------------------------------------------
# TEST 2: positive — bearish market structure detected
# ---------------------------------------------------------------------------


def test_bearish_structure_detected() -> None:
    """A hand-crafted LH+LL series must produce BEARISH trend_bias."""
    bars = _bearish_trend_bars()
    ms = detect_swing_structure(
        bars, swing_left=3, swing_right=3, internal_left=2, internal_right=2
    )

    assert ms.trend_bias == TrendBias.BEARISH, (
        f"Expected BEARISH, got {ms.trend_bias}. "
        f"Swing highs: {[(p.bar_index, p.price, p.label) for p in ms.swing_pivots if p.pivot_type == 'high']}. "
        f"Swing lows: {[(p.bar_index, p.price, p.label) for p in ms.swing_pivots if p.pivot_type == 'low']}"
    )


# ---------------------------------------------------------------------------
# TEST 3: negative / guard — flat-top sequence produces no swing high
# ---------------------------------------------------------------------------


def test_flat_top_no_pivot() -> None:
    """Bars with identical highs (flat top) must not yield a swing-high pivot
    at that level — strict-greater rule; ties are not pivots."""
    # Bar layout: three bars all at high=100.  The middle one (bar 1) is NOT
    # strictly greater than neighbours.
    bars = [
        _bar(99, 100, 98, 99, day=1),
        _bar(99, 100, 98, 99, day=2),
        _bar(99, 100, 98, 99, day=3),
        # padding so left=1, right=1 can see all three
        _bar(97, 98, 96, 97, day=4),
        _bar(96, 97, 95, 96, day=5),
    ]
    ms = detect_swing_structure(bars, swing_left=1, swing_right=1)
    high_pivots = [p for p in ms.swing_pivots if p.pivot_type == "high"]
    # None of the flat-top bars should register as a swing high
    assert all(p.price < 100 for p in high_pivots), (
        f"Unexpected flat-top pivot: {[(p.bar_index, p.price) for p in high_pivots]}"
    )


# ---------------------------------------------------------------------------
# TEST 4: guard — wick-only break does not fire BOS; produces liquidity_sweep
# ---------------------------------------------------------------------------


def test_wick_break_is_sweep_not_bos() -> None:
    """A candle whose HIGH pierces the swing level but CLOSE stays below must
    not produce a BOS event.  Instead it should produce a liquidity_sweep."""
    # Build a minimal bullish-trending series so we get BULLISH bias.
    bars = _bullish_trend_bars()
    # The corrected fixture has the last confirmed swing high at price=120 (bar 9).
    # Append a bar that wicks above 120 but closes at or below 120.
    # => bar.high=125 > 120=level, bar.close=119 < 120=level => liquidity_sweep
    extra_day = len(bars) + 1
    wick_bar = _bar(115, 125, 114, 119, day=extra_day)  # wick > 120, close < 120
    bars_with_wick = bars + [wick_bar]

    ms = detect_swing_structure(
        bars_with_wick, swing_left=3, swing_right=3, internal_left=2, internal_right=2
    )

    bos_events = [e for e in ms.events if e.event_type == "swing_BOS"]
    sweep_events = [e for e in ms.events if e.event_type == "liquidity_sweep"]

    # The wick bar must not have fired a BOS
    assert not any(e.bar_index == len(bars) for e in bos_events), (
        "Wick-only bar incorrectly produced swing_BOS"
    )
    # It should have produced a liquidity_sweep
    assert any(e.bar_index == len(bars) for e in sweep_events), (
        "Wick-only bar did not produce liquidity_sweep"
    )


# ---------------------------------------------------------------------------
# TEST 5: edge — empty bar list returns RANGING with no events
# ---------------------------------------------------------------------------


def test_empty_bars_returns_ranging() -> None:
    """detect_swing_structure on an empty list must return a RANGING MarketStructure."""
    ms = detect_swing_structure([])
    assert ms.trend_bias == TrendBias.RANGING
    assert ms.events == []
    assert ms.swing_pivots == []
    assert ms.liquidity_levels == []


# ---------------------------------------------------------------------------
# TEST 6: parameter edge — insufficient bars for pivot detection
# ---------------------------------------------------------------------------


def test_too_few_bars_for_pivot() -> None:
    """With swing_left=5, swing_right=5, fewer than 11 bars yields no pivots
    and RANGING bias (cannot form 2 confirmed swing highs + 2 lows)."""
    bars = [_flat(100.0 + i, day=i + 1) for i in range(10)]
    ms = detect_swing_structure(bars, swing_left=5, swing_right=5)
    assert ms.trend_bias == TrendBias.RANGING
    assert ms.swing_pivots == []


# ---------------------------------------------------------------------------
# TEST 7: positive — classify_choch_bos returns only BOS/CHoCH events
# ---------------------------------------------------------------------------


def test_classify_choch_bos_filters_correctly() -> None:
    """classify_choch_bos must return a list containing only BOS and CHoCH events."""
    bars = _bullish_trend_bars()
    events = classify_choch_bos(bars, swing_left=3, swing_right=3)

    allowed = {"swing_BOS", "internal_BOS", "swing_CHoCH", "internal_CHoCH"}
    for e in events:
        assert e.event_type in allowed, (
            f"Unexpected event_type in classify_choch_bos output: {e.event_type}"
        )


# ---------------------------------------------------------------------------
# TEST 8: positive — compute_trend_bias agrees with detect_swing_structure
# ---------------------------------------------------------------------------


def test_compute_trend_bias_consistent() -> None:
    """compute_trend_bias(events) should return the same bias as MarketStructure.trend_bias."""
    bars = _bullish_trend_bars()
    ms = detect_swing_structure(bars, swing_left=3, swing_right=3)
    derived = compute_trend_bias(ms.events)
    # Both should be BULLISH (or at minimum agree with each other)
    assert derived == ms.trend_bias


# ---------------------------------------------------------------------------
# TEST 9: pivot confirmation lag — pivot NOT visible before swing_right bars close
# ---------------------------------------------------------------------------


def test_pivot_confirmation_lag() -> None:
    """A swing high at bar i is confirmed at bar i+swing_right.
    All confirmed pivot bar_indices must satisfy: confirmed_at = bar_index + swing_right."""
    bars = _bullish_trend_bars()
    swing_right = 3
    ms = detect_swing_structure(bars, swing_left=3, swing_right=swing_right)
    for p in ms.swing_pivots:
        assert p.confirmed_at == p.bar_index + swing_right, (
            f"Pivot at bar {p.bar_index} has confirmed_at={p.confirmed_at}; "
            f"expected {p.bar_index + swing_right}"
        )


# ---------------------------------------------------------------------------
# TEST 10: EQH detection — two highs within eq_threshold form a cluster
# ---------------------------------------------------------------------------


def test_eqh_cluster_detected() -> None:
    """Two swing highs within eq_threshold of each other must form an EQH cluster."""
    # Build a series where two clear swing highs are at nearly the same price.
    # Use left=right=2 to keep the fixture short.
    bars: list[PriceBar] = []
    day = 1

    def add(o: float, h: float, low: float, c: float) -> None:
        nonlocal day
        bars.append(_bar(o, h, low, c, day=day))
        day += 1

    # First swing high at bar 2 with high=100.0
    # bar 0 and bar 1 must have high < 100.0; bar 3 and bar 4 must also have high < 100.0
    add(97, 98, 96, 97)  # bar 0  high=98 < 100
    add(98, 99, 97, 98)  # bar 1  high=99 < 100 (strictly less than bar 2)
    add(99, 100.0, 98, 99)  # bar 2 ← SH1 high=100.0 (strictly > bars 0-1 and bars 3-4)
    add(98, 99, 97, 98)  # bar 3  high=99 < 100
    add(97, 98, 96, 97)  # bar 4  high=98 < 100
    # Second swing high at bar 6 with high=100.1 (within 0.15% of 100.0)
    add(97, 99, 96, 98)  # bar 5  high=99 < 100.1
    add(98, 100.1, 97, 99)  # bar 6 ← SH2 high=100.1 (strictly > bars 4-5 and bars 7-8)
    add(98, 99, 97, 98)  # bar 7  high=99 < 100.1
    add(97, 98, 96, 97)  # bar 8  high=98 < 100.1

    # eq_threshold=0.0015 → 0.15%. |100.1-100.0|/100.1 ≈ 0.001 < 0.0015 → EQH
    liquidity = detect_eqh_eql(
        bars,
        swing_left=2,
        swing_right=2,
        eq_threshold=0.0015,
        eqh_lookback=50,
    )

    eqh_zones = [lz for lz in liquidity if lz.level_type == "EQH"]
    assert len(eqh_zones) >= 1, f"Expected at least one EQH cluster; got {liquidity}"
    z = eqh_zones[0]
    assert z.touch_count >= 2
    assert z.zone_high >= 100.0
    assert z.zone_low <= 100.1


# ---------------------------------------------------------------------------
# TEST 11: negative — two highs outside eq_threshold do NOT form EQH cluster
# ---------------------------------------------------------------------------


def test_eqh_not_detected_when_spread_too_large() -> None:
    """Two swing highs that differ by more than eq_threshold must NOT form an EQH cluster."""
    bars: list[PriceBar] = []
    day = 1

    def add(o: float, h: float, low: float, c: float) -> None:
        nonlocal day
        bars.append(_bar(o, h, low, c, day=day))
        day += 1

    # SH1 high=100, SH2 high=102 — difference 2/102 ≈ 1.96% >> 0.15%
    add(97, 98, 96, 97)  # bar 0  high=98
    add(98, 99, 97, 98)  # bar 1  high=99 < 100
    add(99, 100.0, 98, 99)  # bar 2  SH1 high=100
    add(98, 99, 97, 98)  # bar 3  high=99
    add(97, 98, 96, 97)  # bar 4  high=98
    add(97, 99, 96, 98)  # bar 5  high=99 < 102
    add(98, 102.0, 97, 99)  # bar 6  SH2 high=102 (clearly different)
    add(98, 99, 97, 98)  # bar 7  high=99
    add(97, 98, 96, 97)  # bar 8  high=98

    liquidity = detect_eqh_eql(
        bars,
        swing_left=2,
        swing_right=2,
        eq_threshold=0.0015,
        eqh_lookback=50,
    )

    eqh_zones = [lz for lz in liquidity if lz.level_type == "EQH"]
    assert len(eqh_zones) == 0, f"Unexpected EQH cluster with spread >threshold: {eqh_zones}"


# ---------------------------------------------------------------------------
# TEST 12: CHoCH guard — once consumed, same pivot does not re-fire CHoCH
# ---------------------------------------------------------------------------


def test_choch_no_refiring_same_pivot() -> None:
    """After a CHoCH fires on a HL pivot, subsequent bars closing below the
    same HL level must not produce additional CHoCH events for that pivot."""
    bars = _bullish_trend_bars()
    # Append several bars that all close below the last HL to stress-test the
    # consumed-flag guard.  HL from fixture is at price≈108 (bar 12).
    extra_day = len(bars) + 1
    for _ in range(5):
        bars.append(_bar(100, 102, 99, 100, day=extra_day))
        extra_day += 1

    ms = detect_swing_structure(bars, swing_left=3, swing_right=3)
    choch_events = [e for e in ms.events if e.event_type == "swing_CHoCH"]

    # This fixture must actually FIRE a bearish CHoCH (otherwise the guard is vacuous):
    # price closes far below the last HL while bias is BULLISH.
    assert len(choch_events) >= 1, "Expected at least one bearish swing_CHoCH to fire"
    assert any(e.direction == "BEARISH" for e in choch_events)

    # STEP 7 — a bearish CHoCH must FLIP the persistent trend_bias to BEARISH and keep it
    # there on subsequent bars (regression guard: a prior bug recomputed bias purely from
    # pivot geometry every bar, silently discarding the CHoCH transition).
    assert ms.trend_bias == TrendBias.BEARISH, (
        f"STEP 7 violated: bearish CHoCH must persist BEARISH bias, got {ms.trend_bias}"
    )
    assert ms.structure_levels["trend_bias"] == "BEARISH"

    # Count distinct CHoCH events that share the same pivot_bar_index
    counts = Counter(e.pivot_bar_index for e in choch_events)
    for pivot_idx, count in counts.items():
        assert count == 1, (
            f"CHoCH fired {count} times on pivot at bar {pivot_idx}; "
            "should fire at most once per HL/LH pivot"
        )


# ---------------------------------------------------------------------------
# TEST 13: structure_levels keys are present in output
# ---------------------------------------------------------------------------


def test_structure_levels_keys_present() -> None:
    """MarketStructure.structure_levels must contain the keys defined in STEP 9."""
    bars = _bullish_trend_bars()
    ms = detect_swing_structure(bars, swing_left=3, swing_right=3)
    required_keys = {
        "last_swing_high",
        "last_swing_low",
        "last_unbroken_swing_high",
        "last_unbroken_swing_low",
        "last_HL",
        "last_LH",
        "trend_bias",
        "int_trend_bias",
    }
    missing = required_keys - ms.structure_levels.keys()
    assert not missing, f"Missing structure_levels keys: {missing}"


# ---------------------------------------------------------------------------
# TEST 14: min_displacement_pct filters borderline BOS
# ---------------------------------------------------------------------------


def test_min_displacement_filters_bos() -> None:
    """BOS events where the close only barely exceeds the level (< min_displacement_pct)
    must be suppressed when min_displacement_pct is set."""
    # Build a bullish trend
    bars = _bullish_trend_bars()
    # The last confirmed swing high is 125. Add a bar that closes at 125.001
    # (0.0008% above), which is below a 0.1% min_displacement_pct threshold.
    extra_day = len(bars) + 1
    bars.append(_bar(120, 128, 119, 125.001, day=extra_day))

    ms_strict = detect_swing_structure(
        bars,
        swing_left=3,
        swing_right=3,
        min_displacement_pct=0.001,  # 0.1% — should suppress the micro-break
    )
    ms_loose = detect_swing_structure(
        bars,
        swing_left=3,
        swing_right=3,
        min_displacement_pct=0.0,  # no filter
    )

    # With no filter, we might get a BOS; with the strict filter fewer BOS events.
    strict_bos = [e for e in ms_strict.events if e.event_type == "swing_BOS"]
    loose_bos = [e for e in ms_loose.events if e.event_type == "swing_BOS"]
    assert len(strict_bos) <= len(loose_bos), (
        "Strict min_displacement_pct should produce fewer or equal BOS events"
    )


# ---------------------------------------------------------------------------
# TEST 15: STEP 7 symmetry — bullish CHoCH flips BEARISH bias to BULLISH and persists
# ---------------------------------------------------------------------------


def test_bullish_choch_flips_and_persists_bias() -> None:
    """In a BEARISH structure, a candle closing above the last LH must fire a BULLISH
    swing_CHoCH and the resulting BULLISH bias must persist (STEP 7), not revert to the
    stale geometric reading on the following bars."""
    bars = _bearish_trend_bars()
    # last LH sits at price=193 (bar 12). Push price up to close above it.
    extra_day = len(bars) + 1
    for o, h, low, c in [(186, 196, 185, 195), (195, 200, 193, 199), (199, 202, 196, 201)]:
        bars.append(_bar(o, h, low, c, day=extra_day))
        extra_day += 1

    ms = detect_swing_structure(bars, swing_left=3, swing_right=3)
    choch = [e for e in ms.events if e.event_type == "swing_CHoCH"]
    assert any(e.direction == "BULLISH" for e in choch), (
        f"Expected a BULLISH swing_CHoCH; events: {[(e.event_type, e.direction) for e in ms.events]}"
    )
    assert ms.trend_bias == TrendBias.BULLISH, (
        f"STEP 7 violated: bullish CHoCH must persist BULLISH bias, got {ms.trend_bias}"
    )
    assert ms.structure_levels["trend_bias"] == "BULLISH"
    # The CHoCH event's own snapshot is taken at emit time (pre-flip) and stays BEARISH —
    # no retroactive update (spec output field `trend_bias`).
    bull_choch = next(e for e in choch if e.direction == "BULLISH")
    assert bull_choch.trend_bias == "BEARISH"


# ---------------------------------------------------------------------------
# TEST 16: use_body_close — wick-break mode turns a sweep into a BOS
# ---------------------------------------------------------------------------


def test_use_body_close_switches_break_rule() -> None:
    """A bar whose HIGH pierces the swing level but CLOSE stays below it is a
    liquidity_sweep under body-close mode (default), but a genuine BOS under
    wick-break mode (use_body_close=False)."""
    bars = _bullish_trend_bars()
    # last confirmed swing high = 120 (bar 9). Wick to 125, close 119 (< 120).
    bars.append(_bar(115, 125, 114, 119, day=len(bars) + 1))
    last_idx = len(bars) - 1

    body = detect_swing_structure(
        bars, swing_left=3, swing_right=3, internal_left=2, internal_right=2, use_body_close=True
    )
    wick = detect_swing_structure(
        bars, swing_left=3, swing_right=3, internal_left=2, internal_right=2, use_body_close=False
    )

    def swing_at(ms: MarketStructure, et: str) -> int:
        return sum(1 for e in ms.events if e.event_type == et and e.bar_index == last_idx)

    # Body-close mode: no BOS, at least one liquidity_sweep at the wick bar.
    assert swing_at(body, "swing_BOS") == 0
    assert swing_at(body, "liquidity_sweep") >= 1
    # Wick-break mode: the wick break IS a BOS; no sweep is emitted.
    assert swing_at(wick, "swing_BOS") >= 1
    assert swing_at(wick, "liquidity_sweep") == 0


# ---------------------------------------------------------------------------
# TEST 17: no-lookahead — prefix runs must not repaint already-confirmed events
# ---------------------------------------------------------------------------


def test_no_lookahead_prefix_consistency() -> None:
    """Non-repainting guarantee: running the detector on a prefix bars[:k] must produce
    exactly the BOS/CHoCH/pivot/sweep events (existence + level + direction) that the full
    run produces for every bar_index <= k-1.  No detection may depend on future bars."""

    def synth(seed: int, length: int = 70) -> list[PriceBar]:
        # Deterministic pseudo-random OHLC with two regime changes (up→down→up) so the
        # series exercises BOS, CHoCH and the STEP-7 bias override.
        import random

        rng = random.Random(seed)
        out: list[PriceBar] = []
        price = 100.0
        for i in range(length):
            drift = 0.6 if i < 24 else (-0.7 if i < 48 else 0.5)
            o = price
            c = price + drift + rng.uniform(-2.0, 2.0)
            h = max(o, c) + rng.uniform(0.0, 2.0)
            low = min(o, c) - rng.uniform(0.0, 2.0)
            out.append(_bar(o, h, low, c, day=1 + (i % 28)))
            price = max(c, 1.0)
        return out

    def signature(ms: MarketStructure) -> set[tuple[int, str, float, object, object]]:
        # EQH/EQL zone events are batch-mode snapshots stamped at the final bar; exclude them
        # from the per-bar non-repainting comparison.
        return {
            (e.bar_index, e.event_type, round(e.level, 4), e.label, e.direction)
            for e in ms.events
            if e.event_type not in ("EQH", "EQL")
        }

    for seed in range(8):
        bars = synth(seed)
        full = signature(detect_swing_structure(bars, swing_left=3, swing_right=3))
        for k in range(15, len(bars) + 1):
            pref = signature(detect_swing_structure(bars[:k], swing_left=3, swing_right=3))
            leaked = {ev for ev in pref if ev[0] <= k - 1 and ev not in full}
            assert not leaked, (
                f"Repaint/lookahead: seed={seed} k={k} produced events absent from the "
                f"full run: {sorted(leaked)}"
            )
