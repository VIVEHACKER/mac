"""Tests for engine/chart/read.py — the confluence aggregator.

These exercise the *end-to-end* ``read_chart`` pipeline against real synthetic
``PriceBar`` fixtures (no detector mocking), plus the public helpers
(``build_signal_map``, ``apply_hard_veto``, ``get_invalidation_level``,
``confluence_score``).

Spec anchor: docs/CHART_READING.md "진입 타이밍 컨플루언스 프레임워크" (lines 2309-2736).
The behaviours asserted here map directly onto the spec's weight table, the four
hard-VETO rules, the decision thresholds, and the invalidation table.

All fixtures are deterministic — no randomness, no time-of-day dependence.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from data.models import OpenInterestRecord, PriceBar
from engine.chart.read import (
    _Features,
    _run_detectors,
    apply_hard_veto,
    build_signal_map,
    get_invalidation_level,
    read_chart,
)
from engine.chart.structure import MarketStructure, StructureEvent
from engine.chart.types import (
    ENTER_THRESHOLD,
    SCALE_IN_THRESHOLD,
    WAIT_THRESHOLD,
    EntryContext,
    EntryState,
    SignalContribution,
    TrendBias,
    confluence_score,
)

# ---------------------------------------------------------------------------
# PriceBar factory (positional fields, daily bars)
# ---------------------------------------------------------------------------

_BASE = date(2026, 1, 1)


def _bar(
    i: int,
    o: float,
    h: float,
    low: float,
    c: float,
    v: float = 1_000.0,
    symbol: str = "TEST/USD",
) -> PriceBar:
    """Minimal daily PriceBar; ``i`` indexes the day so ts stays monotonic."""
    return PriceBar(
        symbol,
        "crypto",
        symbol,
        _BASE + timedelta(days=i),
        o,
        h,
        low,
        c,
        v,
        "1d",
    )


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _zigzag_uptrend(seg: int = 8) -> list[PriceBar]:
    """A genuine zig-zag UPTREND: rising peaks (HH) and rising troughs (HL) with
    real pullbacks between legs.  Verified to yield BULLISH swing structure under
    the aggregator's default ``swing_left=swing_right=5`` — a monotonic ramp would
    give RANGING (no strict local pivots), so each anchor is a sharp pivot bar.
    """
    anchors: list[tuple[str, float]] = [
        ("L", 100),
        ("H", 120),
        ("L", 110),
        ("H", 135),
        ("L", 125),
        ("H", 150),
        ("L", 140),
        ("H", 165),
        ("L", 155),
    ]
    bars: list[PriceBar] = []
    i = 0
    prev = anchors[0][1]
    # leading filler below the first anchor
    for k in range(7):
        lvl = prev - 8 + k
        bars.append(_bar(i, lvl, lvl + 1, lvl - 1, lvl))
        i += 1
    for idx, (kind, level) in enumerate(anchors):
        # sharp pivot bar at the anchor
        if kind == "H":
            bars.append(_bar(i, level - 3, level, level - 4, level - 1))
        else:
            bars.append(_bar(i, level + 3, level + 4, level, level + 1))
        i += 1
        # interpolating filler toward the next anchor (the pullback / advance)
        if idx + 1 < len(anchors):
            nxt = anchors[idx + 1][1]
            for k in range(seg):
                frac = (k + 1) / (seg + 1)
                lvl = level + (nxt - level) * frac
                bars.append(_bar(i, lvl, lvl + 1.2, lvl - 1.2, lvl))
                i += 1
    last = anchors[-1][1]
    for _k in range(7):
        bars.append(_bar(i, last + 1, last + 2, last - 1, last + 1))
        i += 1
    return bars


def _choppy_sideways() -> list[PriceBar]:
    """A flat, mean-reverting series with no directional structure → RANGING bias."""
    bars: list[PriceBar] = []
    for i in range(90):
        base = 100.0 + (3.0 if i % 2 == 0 else -3.0)
        bars.append(_bar(i, base, base + 2, base - 2, base))
    return bars


def _cascade_long_fixture() -> tuple[list[PriceBar], list[OpenInterestRecord]]:
    """A bullish-structured series whose FINAL bar is a sharp price drop AND a sharp
    OI drop on the same bar → ``cascade_long``.  Structure remains BULLISH so the
    cascade VETO (rule 2) is isolated from the RANGING VETO (rule 4)."""
    bars = _zigzag_uptrend()
    n = len(bars)
    prev_close = bars[-2].close
    drop_c = prev_close * 0.965  # ~3.5% drop, exceeds 2% cascade threshold
    bars[-1] = _bar(n - 1, prev_close, prev_close + 0.5, drop_c - 0.5, drop_c)

    oi: list[OpenInterestRecord] = []
    for i in range(n):
        amount = 100_000.0
        if i == n - 1:
            amount = 100_000.0 * 0.94  # 6% OI drop, exceeds 3% cascade threshold
        oi.append(
            OpenInterestRecord(
                "binance",
                "TEST/USD",
                datetime(2026, 1, 1) + timedelta(days=i),
                amount,
            )
        )
    return bars, oi


def _structure_with_swing_choch(direction_of_choch: str) -> _Features:
    """A _Features whose structure carries a swing CHoCH in ``direction_of_choch``
    (``'BULLISH'`` | ``'BEARISH'``) while trend_bias is BULLISH."""
    ev = StructureEvent(
        event_type="swing_CHoCH",
        ts=date(2026, 1, 10),
        direction=direction_of_choch,
        level=120.0,
        zone_low=None,
        zone_high=None,
        label=None,
        trend_bias="BULLISH",
        strength=0.02,
        touch_count=None,
        mitigated=False,
        bar_index=50,
        pivot_bar_index=45,
        structure_scope="swing",
    )
    ms = MarketStructure(
        events=[ev],
        swing_pivots=[],
        internal_pivots=[],
        trend_bias=TrendBias.BULLISH,
        int_trend_bias=TrendBias.BULLISH,
        liquidity_levels=[],
        structure_levels={},
    )
    feat = _Features()
    feat.structure = ms
    return feat


# ===========================================================================
# Behaviour 1 — structurally valid ChartRead always
# ===========================================================================


def test_read_chart_returns_structurally_valid_chartread() -> None:
    bars = _zigzag_uptrend()
    read = read_chart(bars, direction="long")

    assert isinstance(read.decision, EntryState)
    assert isinstance(read.trend_bias, TrendBias)
    assert isinstance(read.contributions, list)
    assert all(isinstance(c, SignalContribution) for c in read.contributions)
    assert 0.0 <= read.confluence <= 100.0
    assert isinstance(read.reasons, list) and read.reasons  # non-empty
    assert isinstance(read.vetoed, bool)
    # The asof / symbol / timeframe come from the last bar.
    assert read.symbol == bars[-1].symbol
    assert read.timeframe == bars[-1].freq
    assert read.asof == str(bars[-1].ts)


def test_read_chart_valid_for_short_direction_too() -> None:
    bars = _zigzag_uptrend()
    read = read_chart(bars, direction="short")
    assert isinstance(read.decision, EntryState)
    assert 0.0 <= read.confluence <= 100.0
    assert read.direction == "short"


# ===========================================================================
# Behaviour 2 — bullish structure yields a constructive long read
# ===========================================================================


def test_bullish_uptrend_yields_bullish_bias_and_not_avoid_for_long() -> None:
    """Spec lines 2327 / 2531: an aligned HTF BULLISH structure must contribute a
    positive structure vote and the pipeline must produce a constructive (non-AVOID)
    read for a long thesis — i.e. the RANGING gate did NOT fire."""
    bars = _zigzag_uptrend()
    read = read_chart(bars, direction="long")

    assert read.trend_bias is TrendBias.BULLISH
    assert read.trend_bias is not TrendBias.RANGING
    assert read.decision is not EntryState.AVOID
    assert not read.vetoed
    # The structure vote must be present and aligned (+1) with the long thesis.
    structure_votes = [c for c in read.contributions if c.name == "structure"]
    assert structure_votes, "structure vote should be present for a trending series"
    assert structure_votes[0].direction == 1


# ===========================================================================
# Behaviour 3 — choppy series → RANGING → hard VETO → AVOID
# ===========================================================================


def test_choppy_series_is_ranging_and_vetoed_to_avoid() -> None:
    """Spec lines 2333 / 2460 (hard-VETO rule 4): HTF trend_bias=RANGING forces AVOID."""
    bars = _choppy_sideways()
    read = read_chart(bars, direction="long")

    assert read.trend_bias is TrendBias.RANGING
    assert read.vetoed is True
    assert read.decision is EntryState.AVOID
    assert any("VETO" in r for r in read.reasons)


# ===========================================================================
# Behaviour 4 — order-flow hard VETO: cascade_long
# ===========================================================================


def test_cascade_long_order_flow_forces_avoid_for_long() -> None:
    """Spec lines 2378 / 2458 (hard-VETO rule 2): cascade_long=True forces AVOID
    for a long thesis regardless of other votes.  Structure here is still BULLISH,
    so this isolates the cascade rule from the RANGING rule."""
    bars, oi = _cascade_long_fixture()
    read = read_chart(bars, direction="long", oi_records=oi)

    assert read.trend_bias is TrendBias.BULLISH  # not RANGING — cascade is the cause
    assert read.vetoed is True
    assert read.decision is EntryState.AVOID
    assert any("cascade_long" in r for r in read.reasons)


def test_cascade_long_does_not_veto_a_short_thesis() -> None:
    """cascade_long only AVOIDs longs; a short thesis is not vetoed by it."""
    bars, oi = _cascade_long_fixture()
    feat = _run_detectors(bars, order_book=None, oi_records=oi, funding_records=None)
    vetoed_long, _ = apply_hard_veto(feat, "long")
    vetoed_short, reason_short = apply_hard_veto(feat, "short")
    assert vetoed_long is True
    # The short side is not vetoed *by cascade_long* (it may still be vetoed by a
    # bearish structure flip from the drop, but never with the cascade_long reason).
    if vetoed_short:
        assert reason_short is None or "cascade_long" not in reason_short


# ===========================================================================
# Behaviour 5 — structure hard VETO: against-direction swing CHoCH
# ===========================================================================


def test_swing_choch_against_long_forces_veto() -> None:
    """Spec lines 2334 / 2457 (hard-VETO rule 1): a swing_CHoCH against the intended
    direction invalidates the structural thesis → VETO.  Tested directly on the
    helper with a crafted against-direction (BEARISH) swing CHoCH."""
    feat = _structure_with_swing_choch("BEARISH")
    vetoed, reason = apply_hard_veto(feat, "long")
    assert vetoed is True
    assert reason is not None and "CHoCH" in reason


def test_swing_choch_with_direction_does_not_veto() -> None:
    """A swing CHoCH *aligned* with the thesis (BULLISH for a long) must NOT veto."""
    feat = _structure_with_swing_choch("BULLISH")
    vetoed, reason = apply_hard_veto(feat, "long")
    assert vetoed is False
    assert reason is None


# ===========================================================================
# Behaviour 6 — stock mode: no order book, no OI must not crash
# ===========================================================================


def test_stock_mode_no_orderbook_no_oi_is_valid() -> None:
    """Spec lines 2519-2547 (worked example 2, KOSPI large-cap): with no order book
    and no OI, the pipeline degrades gracefully — no OBI / OI votes appear in the
    signal map and a valid ChartRead is returned."""
    bars = _zigzag_uptrend()
    read = read_chart(bars, direction="long", order_book=None, oi_records=None)

    assert isinstance(read.decision, EntryState)
    assert 0.0 <= read.confluence <= 100.0
    names = {c.name for c in read.contributions}
    assert "obi" not in names
    assert "open_interest" not in names


# ===========================================================================
# Behaviour 7 — direction mirror: long vs short structure votes oppose
# ===========================================================================


def test_direction_mirror_structure_votes_have_opposite_signs() -> None:
    """On the SAME bullish fixture, the structure vote for 'long' and 'short' must
    have opposite signs, and a short read on a bullish series must not be ENTER_NOW."""
    bars = _zigzag_uptrend()
    feat = _run_detectors(bars, order_book=None, oi_records=None, funding_records=None)

    long_votes = build_signal_map(feat, bars, "long")
    short_votes = build_signal_map(feat, bars, "short")
    long_struct = next(c for c in long_votes if c.name == "structure")
    short_struct = next(c for c in short_votes if c.name == "structure")
    assert long_struct.direction == -short_struct.direction
    assert long_struct.direction != 0

    short_read = read_chart(bars, direction="short")
    long_read = read_chart(bars, direction="long")
    assert short_read.decision is not EntryState.ENTER_NOW
    # Long should score strictly higher than short on a bullish series.
    assert long_read.confluence > short_read.confluence


# ===========================================================================
# Behaviour 8 — determinism / no-lookahead stability
# ===========================================================================


def test_read_chart_is_deterministic() -> None:
    """Two calls on the same bars give an identical decision and score."""
    bars = _zigzag_uptrend()
    r1 = read_chart(bars, direction="long")
    r2 = read_chart(bars, direction="long")
    assert r1.decision == r2.decision
    assert r1.confluence == r2.confluence
    assert r1.trend_bias == r2.trend_bias
    assert [c.name for c in r1.contributions] == [c.name for c in r2.contributions]


def test_decision_reflects_last_bar_not_input_alias() -> None:
    """The decision is anchored to bars[-1]; reading bars[:-1] may differ, but the
    full-series read is stable and references the final bar's timestamp."""
    bars = _zigzag_uptrend()
    full = read_chart(bars, direction="long")
    trimmed = read_chart(bars[:-1], direction="long")
    assert full.asof == str(bars[-1].ts)
    assert trimmed.asof == str(bars[-2].ts)
    # Re-reading the trimmed series is itself stable (determinism on a subseries).
    trimmed2 = read_chart(bars[:-1], direction="long")
    assert trimmed.decision == trimmed2.decision
    assert trimmed.confluence == trimmed2.confluence


# ===========================================================================
# Helper-level tests — confluence_score thresholds & invalidation table
# ===========================================================================


def test_confluence_score_normalisation_and_clamp() -> None:
    """Spec lines 2404-2439: score = (sum(w*d)/sum(w))*100, clamped [0,100];
    weight<=0 votes are ignored; empty/zero-weight map → 0."""
    # All aligned → 100.
    assert confluence_score({"a": (0.5, 1), "b": (0.5, 1)}) == 100.0
    # Half opposed → 0 (0.5*1 + 0.5*-1)=0.
    assert confluence_score({"a": (0.5, 1), "b": (0.5, -1)}) == 0.0
    # Mixed: (0.7*1 + 0.3*-1) / (0.7+0.3) = 0.4 → 40.0
    assert confluence_score({"a": (0.7, 1), "b": (0.3, -1)}) == 40.0
    # Empty / no active weight → 0.0
    assert confluence_score({}) == 0.0
    assert confluence_score({"z": (0.0, 1)}) == 0.0


def test_threshold_ordering_matches_spec() -> None:
    """Spec lines 2446-2451: ENTER 70 / SCALE_IN 50 / WAIT 35."""
    assert ENTER_THRESHOLD == 70.0
    assert SCALE_IN_THRESHOLD == 50.0
    assert WAIT_THRESHOLD == 35.0


def test_invalidation_level_enter_now_long_uses_sweep_extreme() -> None:
    """Spec lines 2468/2512: ENTER_NOW long off a sweep → sweep_extreme - 0.5*ATR."""
    ctx = EntryContext(direction="long", atr14=2.0, sweep_extreme=100.0)
    level = get_invalidation_level(EntryState.ENTER_NOW, ctx, "long")
    assert level == 100.0 - 0.5 * 2.0


def test_invalidation_level_enter_now_short_mirrors_above() -> None:
    """Spec line 2471: ENTER_NOW short off a sweep → sweep_extreme + 0.5*ATR."""
    ctx = EntryContext(direction="short", atr14=2.0, sweep_extreme=100.0)
    level = get_invalidation_level(EntryState.ENTER_NOW, ctx, "short")
    assert level == 100.0 + 0.5 * 2.0


def test_invalidation_level_spring_takes_priority_for_long() -> None:
    """Spec line 2470: a Wyckoff spring_low is the most specific basis for a long."""
    ctx = EntryContext(direction="long", atr14=2.0, sweep_extreme=100.0, spring_low=95.0)
    level = get_invalidation_level(EntryState.ENTER_NOW, ctx, "long")
    assert level == 95.0


def test_invalidation_level_scale_in_uses_tr_high() -> None:
    """Spec line 2473: SCALE_IN (BUEC/LPS) → TR_high * 0.97."""
    ctx = EntryContext(direction="long", atr14=2.0, tr_high=200.0)
    level = get_invalidation_level(EntryState.SCALE_IN, ctx, "long")
    assert level == 200.0 * 0.97


def test_invalidation_level_wait_uses_htf_swing() -> None:
    """Spec lines 2474-2475: WAIT_FOR_PULLBACK long → HTF swing low; short → swing high."""
    ctx = EntryContext(direction="long", atr14=2.0, htf_swing_low=90.0, htf_swing_high=110.0)
    assert get_invalidation_level(EntryState.WAIT_FOR_PULLBACK, ctx, "long") == 90.0
    assert get_invalidation_level(EntryState.WAIT_FOR_PULLBACK, ctx, "short") == 110.0


def test_invalidation_level_avoid_is_none() -> None:
    ctx = EntryContext(direction="long", atr14=2.0, sweep_extreme=100.0)
    assert get_invalidation_level(EntryState.AVOID, ctx, "long") is None
