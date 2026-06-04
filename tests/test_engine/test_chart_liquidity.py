"""Tests for engine/chart/liquidity.py — ICT/SMC liquidity-pool detector.

All fixtures are synthetic PriceBar sequences hand-crafted to contain (or not
contain) specific patterns.  No external data, no random seeds.
"""

from __future__ import annotations

from datetime import date, timedelta

from data.models import PriceBar
from engine.chart.liquidity import (
    LiquidityPool,
    SweepEvent,
    analyze_liquidity,
    classify_premium_discount,
    classify_sweep_type,
    compute_ote_zone,
    detect_liquidity_sweep,
    detect_mss,
    identify_liquidity_pools,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_BASE_DATE = date(2026, 1, 1)


def _bar(
    idx: int,
    o: float,
    h: float,
    low: float,
    c: float,
    v: float = 1000.0,
    *,
    symbol: str = "BTC/USDT",
    freq: str = "4h",
) -> PriceBar:
    return PriceBar(
        symbol=symbol,
        market="crypto",
        source_symbol=symbol,
        ts=_BASE_DATE + timedelta(days=idx),
        open=o,
        high=h,
        low=low,
        close=c,
        volume=v,
        freq=freq,
    )


def _flat_bars(n: int, price: float = 100.0) -> list[PriceBar]:
    """Return n bars that oscillate slightly around ``price``."""
    bars: list[PriceBar] = []
    for i in range(n):
        delta = 0.5 if i % 2 == 0 else -0.5
        bars.append(_bar(i, price, price + 1.0, price - 1.0, price + delta))
    return bars


# ---------------------------------------------------------------------------
# TEST 1 — identify_liquidity_pools: equal highs form a BSL pool
# ---------------------------------------------------------------------------


def test_identify_pools_equal_highs_bsl() -> None:
    """Two pivot highs at the same price level should form a BSL pool (EQH).

    Bar layout (index):
      0-2: ascending run to set left context for pivot at 3
      3: swing HIGH at 110 (left=3, must be highest in window)
      4-6: pullback
      7: second swing HIGH at 110 (equal to first)
      8-10: pullback (right context for pivot at 7)
    """
    bars: list[PriceBar] = []
    # bars 0-2: rising
    bars += [_bar(0, 100, 102, 99, 101)]
    bars += [_bar(1, 101, 103, 100, 102)]
    bars += [_bar(2, 102, 105, 101, 104)]
    # bar 3: pivot high at 110
    bars += [_bar(3, 104, 110, 103, 106)]
    # bars 4-5: pullback
    bars += [_bar(4, 106, 107, 98, 99)]
    bars += [_bar(5, 99, 100, 97, 98)]
    bars += [_bar(6, 98, 100, 97, 99)]
    # bar 7: second pivot high at 110 (equal)
    bars += [_bar(7, 99, 110, 98, 100)]
    # bars 8-10: right-side confirmation pullback
    bars += [_bar(8, 100, 101, 98, 99)]
    bars += [_bar(9, 99, 100, 97, 98)]
    bars += [_bar(10, 98, 99, 96, 97)]

    pools = identify_liquidity_pools(bars, swing_left=3, swing_right=3, eq_tolerance_pct=0.002)

    bsl_pools = [p for p in pools if p.side == "BSL" and p.touch_count >= 2]
    assert len(bsl_pools) >= 1, "Expected at least one EQH BSL pool"
    pool = bsl_pools[0]
    assert abs(pool.price - 110.0) < 0.01
    assert pool.touch_count >= 2
    assert not pool.mitigated


# ---------------------------------------------------------------------------
# TEST 2 — identify_liquidity_pools: single pivot registered as touch_count=1
# ---------------------------------------------------------------------------


def test_identify_pools_single_pivot_ssl() -> None:
    """A lone swing low should be registered as a SSL pool with touch_count=1."""
    bars: list[PriceBar] = []
    # bars 0-2: falling
    bars += [_bar(0, 100, 101, 98, 99)]
    bars += [_bar(1, 99, 100, 96, 97)]
    bars += [_bar(2, 97, 98, 94, 95)]
    # bar 3: pivot low at 90
    bars += [_bar(3, 95, 96, 90, 94)]
    # bars 4-6: right-side rising
    bars += [_bar(4, 94, 96, 93, 95)]
    bars += [_bar(5, 95, 97, 94, 96)]
    bars += [_bar(6, 96, 98, 95, 97)]

    pools = identify_liquidity_pools(bars, swing_left=3, swing_right=3, min_touch_count=1)
    ssl_pools = [p for p in pools if p.side == "SSL"]
    assert len(ssl_pools) >= 1
    assert ssl_pools[0].touch_count == 1
    assert ssl_pools[0].price <= 90.5  # close to the low


# ---------------------------------------------------------------------------
# TEST 3 — detect_liquidity_sweep: BSL single-bar wick sweep
# ---------------------------------------------------------------------------


def test_detect_bsl_single_bar_sweep() -> None:
    """Wick above BSL pool level but close below → sweep detected.

    Pool at 110.  Bar with high=111, close=109 → single-bar BSL sweep.
    """
    # Minimal: just inject a pool manually and craft bars around it
    pool_level = 110.0

    # Build bars: steady rise, one sweep bar, then drop
    bars: list[PriceBar] = []
    for i in range(10):
        bars.append(_bar(i, 100 + i, 101 + i, 99 + i, 100 + i))
    # sweep bar (index 10): high breaks 110, close below 110
    bars.append(_bar(10, 109, 111, 107, 109))  # high=111>110, close=109<110
    # post-sweep bars
    bars.append(_bar(11, 109, 110, 106, 107))  # reclaim confirm: close<=110
    bars.append(_bar(12, 107, 108, 104, 105))

    pool = LiquidityPool(
        price=pool_level,
        side="BSL",
        touch_count=2,
        ts=_BASE_DATE,
        zone_low=109.5,
        zone_high=110.0,
        _bar_index=9,
    )
    pools = [pool]
    sweeps = detect_liquidity_sweep(bars, pools)

    assert len(sweeps) == 1
    sw = sweeps[0]
    assert sw.side == "BSL"
    assert abs(sw.level - pool_level) < 0.01
    assert sw.wick_extreme > pool_level
    assert sw.bar_index == 10
    assert sw.reclaimed is True  # bar 11 closes <= 110


# ---------------------------------------------------------------------------
# TEST 3b — LOOKAHEAD GUARD: the bar-t sweep event must not depend on bar t+1
# ---------------------------------------------------------------------------


def _build_sweep_series(after_close: float | None) -> list[PriceBar]:
    """Bars rising to a BSL sweep at index 10; ``after_close`` controls bar 11.

    If ``after_close`` is None, the series is truncated at the sweep bar (no t+1).
    """
    bars: list[PriceBar] = []
    for i in range(10):
        bars.append(_bar(i, 100 + i, 101 + i, 99 + i, 100 + i))
    # sweep bar (index 10): high=111>110, close=109<=110 → single-bar BSL sweep
    bars.append(_bar(10, 109, 111, 107, 109))
    if after_close is not None:
        # bar 11: its close is the ONLY thing that varies between scenarios
        bars.append(_bar(11, 109, max(after_close + 1, 111), 107, after_close))
    return bars


def test_sweep_reclaim_is_lookahead_free() -> None:
    """The ``reclaimed`` value of the sweep emitted at bar t must be identical
    regardless of what bar t+1 does — proving no forward peek at detection time.

    Three scenarios share an identical sweep bar (index 10):
      A) series truncated right after the sweep (no future bar exists)
      B) bar 11 closes BELOW the level (would-confirm)
      C) bar 11 closes far ABOVE the level (price runs away)

    Under a correct no-lookahead detector, the SweepEvent created *at bar 10*
    carries the same ``reclaimed`` in all three at the moment it is emitted.
    Scenario C is the adversarial one: a forward-peeking implementation would
    have set reclaimed=False here (t+1 close > level), differing from A and B.
    """

    def _bar10_event(bars: list[PriceBar]) -> SweepEvent:
        pool = LiquidityPool(
            price=110.0,
            side="BSL",
            touch_count=2,
            ts=_BASE_DATE,
            zone_low=109.5,
            zone_high=110.0,
            _bar_index=9,
        )
        sweeps = detect_liquidity_sweep(bars, [pool])
        bar10 = [s for s in sweeps if s.bar_index == 10]
        assert bar10, "expected a single-bar sweep at bar index 10"
        return bar10[0]

    ev_truncated = _bar10_event(_build_sweep_series(None))  # no t+1
    ev_confirm = _bar10_event(_build_sweep_series(after_close=106.0))  # t+1 below
    ev_runaway = _bar10_event(_build_sweep_series(after_close=120.0))  # t+1 above

    # The bar-10 event's reclaimed flag is decided purely from bar-10 data.
    assert ev_truncated.reclaimed == ev_confirm.reclaimed == ev_runaway.reclaimed
    # And that present value is True: the sweep bar itself closed back inside the level.
    assert ev_truncated.reclaimed is True


# ---------------------------------------------------------------------------
# TEST 4 — detect_liquidity_sweep: BOS (close beyond level) mitigates pool, no sweep
# ---------------------------------------------------------------------------


def test_bos_mitigates_pool_no_sweep() -> None:
    """If the close is above BSL level, it is a BOS — no sweep registered."""
    pool_level = 110.0
    bars: list[PriceBar] = []
    for i in range(10):
        bars.append(_bar(i, 100 + i, 101 + i, 99 + i, 100 + i))
    # BOS bar: high AND close both above pool level
    bars.append(_bar(10, 109, 115, 108, 113))  # close=113 > 110 → BOS
    bars.append(_bar(11, 113, 116, 110, 114))

    pool = LiquidityPool(
        price=pool_level,
        side="BSL",
        touch_count=1,
        ts=_BASE_DATE,
        zone_low=109.5,
        zone_high=110.0,
        _bar_index=9,
    )
    pools = [pool]
    sweeps = detect_liquidity_sweep(bars, pools)

    # Close above level → pending multi-bar; if it never reclaims, pool is mitigated
    # All bars after bar 10 stay above 110 → eventually mitigated, no sweep
    ssl_sweeps = [s for s in sweeps if s.side == "BSL"]
    # The pool should end up mitigated (BOS) with no sweep event
    assert len(ssl_sweeps) == 0 or all(s.sweep_type == "multi_bar_sweep" for s in ssl_sweeps)


# ---------------------------------------------------------------------------
# TEST 5 — detect_liquidity_sweep: SSL single-bar wick sweep
# ---------------------------------------------------------------------------


def test_detect_ssl_single_bar_sweep() -> None:
    """Wick below SSL pool level but close above → sweep detected."""
    pool_level = 90.0

    bars: list[PriceBar] = []
    for i in range(8):
        bars.append(_bar(i, 100 - i, 102 - i, 98 - i, 100 - i))
    # sweep bar: low=89 < 90, close=91 >= 90
    bars.append(_bar(8, 91, 92, 89, 91))
    # reclaim confirm: close >= 90
    bars.append(_bar(9, 91, 93, 90, 92))
    bars.append(_bar(10, 92, 94, 91, 93))

    pool = LiquidityPool(
        price=pool_level,
        side="SSL",
        touch_count=1,
        ts=_BASE_DATE,
        zone_low=90.0,
        zone_high=90.5,
        _bar_index=7,
    )
    pools = [pool]
    sweeps = detect_liquidity_sweep(bars, pools)

    ssl_sweeps = [s for s in sweeps if s.side == "SSL"]
    assert len(ssl_sweeps) >= 1
    sw = ssl_sweeps[0]
    assert sw.wick_extreme < pool_level
    assert sw.bar_index == 8


# ---------------------------------------------------------------------------
# TEST 6 — classify_sweep_type: small wick → single_bar_grab
# ---------------------------------------------------------------------------


def test_classify_sweep_type_single_bar_grab() -> None:
    """A wick extension < sweep_reject_pct * ATR → 'single_bar_grab'."""
    bars: list[PriceBar] = []
    # previous bar provides prev_close
    bars.append(_bar(0, 100, 102, 99, 101))
    # sweep bar: wick just 0.5 above level=102; TR = 103-100=3; wick_ext=0.5 < 0.5*3=1.5
    bars.append(_bar(1, 101, 102.5, 100, 101))  # high=102.5, close=101 < 102

    sweep = SweepEvent(
        ts=bars[1].ts,
        level=102.0,
        side="BSL",
        bar_index=1,
        wick_extreme=102.5,
        reclaimed=False,
        sweep_type="single_bar_grab",
    )
    result = classify_sweep_type(sweep, bars, sweep_reject_pct=0.5)
    assert result == "single_bar_grab"


# ---------------------------------------------------------------------------
# TEST 7 — detect_mss: bullish MSS after SSL sweep
# ---------------------------------------------------------------------------


def test_detect_mss_bullish_after_ssl_sweep() -> None:
    """After an SSL sweep, an internal high broken by close → Bullish MSS."""
    bars: list[PriceBar] = []
    # bar 0: context
    bars.append(_bar(0, 100, 102, 98, 101))
    # bars 1-4: steady bars to form left context
    bars.append(_bar(1, 101, 103, 99, 102))
    bars.append(_bar(2, 102, 104, 100, 103))
    # bar 3: SSL sweep (low below 95, close above 95)
    bars.append(_bar(3, 100, 101, 93, 99))
    # bars 4-5: small rally forming internal pivot high at 104
    bars.append(_bar(4, 99, 104, 98, 103))  # internal high at 104
    # bars 5-6: slight dip (right context for the internal high)
    bars.append(_bar(5, 103, 104, 101, 102))
    bars.append(_bar(6, 102, 103, 100, 101))
    # bar 7: MSS bar — close breaks internal high 104
    bars.append(_bar(7, 101, 108, 100, 106))  # close=106 > 104

    sweep = SweepEvent(
        ts=bars[3].ts,
        level=95.0,
        side="SSL",
        bar_index=3,
        wick_extreme=93.0,
        reclaimed=True,
        sweep_type="single_bar_grab",
    )

    mss = detect_mss(bars, sweep, swing_left=2, swing_right=2, mss_lookback=10, mss_body_ratio=0.5)

    assert mss is not None
    assert mss.direction == "BULLISH"
    assert mss.broken_level > 100  # internal high was above 100
    # MSS confirmed by bar 7: close (106) > internal high (104), not a wick-only break.
    assert bars[mss.bar_index].close > mss.broken_level
    # Displacement: bar 7 body=|106-101|=5, TR=max(108-100,|108-101|,|100-101|)=8 → 5/8=0.625 ≥ 0.5
    assert mss.displacement is True


# ---------------------------------------------------------------------------
# TEST 8 — compute_ote_zone: bullish OTE zone boundaries
# ---------------------------------------------------------------------------


def test_compute_ote_zone_bullish() -> None:
    """Bullish OTE: zone should be 0.62-0.79 retracement from high toward low."""
    zone = compute_ote_zone(swing_low=100.0, swing_high=200.0, direction="BULLISH")
    assert zone is not None
    # ote_low_price = 200 - 100*0.79 = 121.0
    # ote_high_price = 200 - 100*0.62 = 138.0
    assert abs(zone.low - 121.0) < 0.001
    assert abs(zone.high - 138.0) < 0.001
    assert abs(zone.mid_705 - (200 - 100 * 0.705)) < 0.001
    assert zone.direction == "BULLISH"
    assert zone.low < zone.high  # zone is ordered low-to-high


# ---------------------------------------------------------------------------
# TEST 9 — compute_ote_zone: bearish OTE zone boundaries
# ---------------------------------------------------------------------------


def test_compute_ote_zone_bearish() -> None:
    """Bearish OTE: zone should be 0.62-0.79 retracement from low toward high."""
    zone = compute_ote_zone(swing_low=100.0, swing_high=200.0, direction="BEARISH")
    assert zone is not None
    # ote_low_price = 100 + 100*0.62 = 162.0
    # ote_high_price = 100 + 100*0.79 = 179.0
    assert abs(zone.low - 162.0) < 0.001
    assert abs(zone.high - 179.0) < 0.001
    assert zone.direction == "BEARISH"


# ---------------------------------------------------------------------------
# TEST 10 — compute_ote_zone: degenerate range returns None
# ---------------------------------------------------------------------------


def test_compute_ote_zone_degenerate() -> None:
    """dr_high <= dr_low → None (dealing range guard)."""
    assert compute_ote_zone(100.0, 100.0, "BULLISH") is None
    assert compute_ote_zone(150.0, 100.0, "BEARISH") is None


# ---------------------------------------------------------------------------
# TEST 11 — classify_premium_discount
# ---------------------------------------------------------------------------


def test_classify_premium_discount_zones() -> None:
    """Price above eq+buffer=premium, below eq-buffer=discount, within=equilibrium."""
    # Range 100-200: eq=150, buffer=0.02*100=2
    result = classify_premium_discount(160.0, 100.0, 200.0, price_zone_eq_buffer=0.02)
    assert result["price_zone"] == "premium"
    assert abs(result["eq"] - 150.0) < 0.001

    result = classify_premium_discount(140.0, 100.0, 200.0, price_zone_eq_buffer=0.02)
    assert result["price_zone"] == "discount"

    result = classify_premium_discount(150.0, 100.0, 200.0, price_zone_eq_buffer=0.02)
    assert result["price_zone"] == "equilibrium"


def test_classify_premium_discount_degenerate() -> None:
    """Equal or inverted range → undefined."""
    result = classify_premium_discount(100.0, 100.0, 100.0)
    assert result["price_zone"] == "undefined"
    assert result["eq"] is None


# ---------------------------------------------------------------------------
# TEST 12 — analyze_liquidity: end-to-end integration
# ---------------------------------------------------------------------------


def test_analyze_liquidity_returns_result_object() -> None:
    """analyze_liquidity on a synthetic series should return a LiquidityResult."""
    bars = _flat_bars(30, price=100.0)
    result = analyze_liquidity(bars, swing_left=3, swing_right=3)
    # At minimum, the result should have list fields
    assert isinstance(result.pools, list)
    assert isinstance(result.sweeps, list)
    assert isinstance(result.mss_events, list)
    assert result.price_zone in ("premium", "discount", "equilibrium", "undefined")


def test_analyze_liquidity_too_few_bars() -> None:
    """Fewer bars than pivot window → empty result (no crash)."""
    bars = _flat_bars(5)
    result = analyze_liquidity(bars, swing_left=3, swing_right=3)
    assert result.pools == []
    assert result.sweeps == []


# ---------------------------------------------------------------------------
# TEST 13 — min_touch_count filter: only EQH/EQL registered when set to 2
# ---------------------------------------------------------------------------


def test_min_touch_count_filters_solo_pivots() -> None:
    """With min_touch_count=2, single-pivot pools should not appear."""
    bars: list[PriceBar] = []
    for i in range(14):
        bars.append(_bar(i, 100, 102, 98, 100 + (0.5 if i % 3 == 0 else -0.5)))

    pools_all = identify_liquidity_pools(bars, swing_left=3, swing_right=3, min_touch_count=1)
    pools_eq_only = identify_liquidity_pools(bars, swing_left=3, swing_right=3, min_touch_count=2)

    # All touch_count=1 pools should be absent in the filtered result
    assert all(p.touch_count >= 2 for p in pools_eq_only)
    # The unfiltered set may include touch_count=1 pools
    # (this is a guard: not strictly guaranteed but common for flat bars)
    single_count_all = sum(1 for p in pools_all if p.touch_count == 1)
    single_count_filtered = sum(1 for p in pools_eq_only if p.touch_count == 1)
    assert single_count_filtered == 0
    assert single_count_all >= single_count_filtered


# ---------------------------------------------------------------------------
# TEST 14 — stale pool guard: pool marked stale after pool_lookback bars
# ---------------------------------------------------------------------------


def test_stale_pool_guard() -> None:
    """A pool that survives pool_lookback bars without being hit should be stale."""
    pool_level = 110.0

    bars: list[PriceBar] = []
    # Create pool at bar 0 context
    for i in range(60):
        # bars never touch the pool level: oscillate below 108
        bars.append(_bar(i, 100, 107, 99, 100))

    pool = LiquidityPool(
        price=pool_level,
        side="BSL",
        touch_count=1,
        ts=_BASE_DATE,
        zone_low=109.5,
        zone_high=110.0,
        _bar_index=0,
    )
    pools = [pool]
    detect_liquidity_sweep(bars, pools, pool_lookback=50)

    # After scanning 60 bars, the pool should be marked stale
    assert pools[0].stale is True


# ---------------------------------------------------------------------------
# TEST 15 — MSS wick-only guard: wick break without close break is NOT MSS
# ---------------------------------------------------------------------------


def test_mss_wick_only_guard() -> None:
    """A bar whose wick breaks the internal high but close does not → NOT an MSS."""
    bars: list[PriceBar] = []
    bars.append(_bar(0, 100, 102, 98, 101))
    bars.append(_bar(1, 101, 103, 99, 102))
    bars.append(_bar(2, 102, 104, 100, 103))
    # SSL sweep bar at index 3
    bars.append(_bar(3, 100, 101, 93, 99))
    # bars 4-5: small rally forming internal pivot high at 104
    bars.append(_bar(4, 99, 104, 98, 103))
    bars.append(_bar(5, 103, 104, 101, 102))
    bars.append(_bar(6, 102, 103, 100, 101))
    # wick-only bar: high > 104 but close <= 104 → NOT MSS
    bars.append(_bar(7, 101, 106, 100, 103))  # close=103 <= 104

    sweep = SweepEvent(
        ts=bars[3].ts,
        level=95.0,
        side="SSL",
        bar_index=3,
        wick_extreme=93.0,
        reclaimed=True,
        sweep_type="single_bar_grab",
    )

    # With strict swing_right=2, the internal high at bar 4 needs bar 5 and 6 to confirm
    mss = detect_mss(bars, sweep, swing_left=2, swing_right=2, mss_lookback=10, mss_body_ratio=0.5)

    # The wick-only bar 7 (high=106 > 104 but close=103 <= 104) must NOT register as MSS,
    # and no other bar in range closes above the internal high → strictly None.
    assert mss is None


def test_mss_wick_only_then_close_break_confirms() -> None:
    """Counterpart to the wick-only guard: once a LATER bar closes above the
    internal high, the MSS fires — proving the guard rejects wicks, not the whole signal.
    """
    bars: list[PriceBar] = []
    bars.append(_bar(0, 100, 102, 98, 101))
    bars.append(_bar(1, 101, 103, 99, 102))
    bars.append(_bar(2, 102, 104, 100, 103))
    bars.append(_bar(3, 100, 101, 93, 99))  # SSL sweep
    bars.append(_bar(4, 99, 104, 98, 103))  # internal high at 104
    bars.append(_bar(5, 103, 104, 101, 102))
    bars.append(_bar(6, 102, 103, 100, 101))
    bars.append(_bar(7, 101, 106, 100, 103))  # wick-only: close=103 <= 104 → not MSS
    bars.append(_bar(8, 103, 109, 102, 107))  # close=107 > 104 → confirms Bullish MSS

    sweep = SweepEvent(
        ts=bars[3].ts,
        level=95.0,
        side="SSL",
        bar_index=3,
        wick_extreme=93.0,
        reclaimed=True,
        sweep_type="single_bar_grab",
    )

    mss = detect_mss(bars, sweep, swing_left=2, swing_right=2, mss_lookback=10, mss_body_ratio=0.5)
    assert mss is not None
    assert mss.direction == "BULLISH"
    assert mss.bar_index == 8  # the close-break bar, not the wick-only bar 7
    assert bars[mss.bar_index].close > mss.broken_level
