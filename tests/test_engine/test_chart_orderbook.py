"""Tests for engine/chart/orderbook.py — Order Book Depth analyser.

Synthetic fixtures are hand-crafted to trigger (and NOT trigger) each
detection path.  No pandas/numpy; pure stdlib.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime

import pytest

from data.models import OrderBookLevel, OrderBookSnapshot
from engine.chart.orderbook import (
    OrderBookAnalyser,
    analyze_order_book,
    classify_obi_regime,
    compute_delta_vamp,
    compute_obi_weighted,
    compute_obi_zscore,
    detect_order_walls,
    is_spread_wide,
)
from engine.chart.types import OBIRegime

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _snap(
    bids: list[tuple[float, float]],
    asks: list[tuple[float, float]],
    symbol: str = "BTC/USDT",
    ts: datetime | None = None,
) -> OrderBookSnapshot:
    """Build a minimal OrderBookSnapshot from raw (price, size) lists."""
    return OrderBookSnapshot(
        exchange="test",
        symbol=symbol,
        ts=ts or datetime(2026, 1, 1, 12, 0, 0),
        bids=tuple(OrderBookLevel(p, s) for p, s in bids),
        asks=tuple(OrderBookLevel(p, s) for p, s in asks),
        source="test",
    )


# ---------------------------------------------------------------------------
# 1. compute_obi_weighted: basic flat + weighted values
# ---------------------------------------------------------------------------


class TestComputeObiWeighted:
    def test_balanced_book_returns_zero(self) -> None:
        bids = tuple(OrderBookLevel(100 - i, 10.0) for i in range(5))
        asks = tuple(OrderBookLevel(101 + i, 10.0) for i in range(5))
        obi_flat, obi_weighted = compute_obi_weighted(bids, asks, obi_top_n=5)
        assert obi_flat == pytest.approx(0.0, abs=1e-9)
        assert obi_weighted == pytest.approx(0.0, abs=1e-9)

    def test_all_bid_pressure_returns_positive_one(self) -> None:
        bids = tuple(OrderBookLevel(100 - i, 100.0) for i in range(5))
        asks = tuple(OrderBookLevel(101 + i, 0.0) for i in range(5))
        obi_flat, obi_weighted = compute_obi_weighted(bids, asks, obi_top_n=5)
        # ask vol = 0 → total = bid_vol → OBI = 1.0
        assert obi_flat == pytest.approx(1.0)
        assert obi_weighted == pytest.approx(1.0)

    def test_top_n_truncation(self) -> None:
        # 10 levels but only top 3 should count
        bids = tuple(OrderBookLevel(100 - i, 1.0) for i in range(10))
        asks = tuple(OrderBookLevel(101 + i, 1.0) for i in range(10))
        obi_flat3, obi_w3 = compute_obi_weighted(bids, asks, obi_top_n=3)
        obi_flat10, obi_w10 = compute_obi_weighted(bids, asks, obi_top_n=10)
        # Balanced on all levels → both zero regardless of N
        assert obi_flat3 == pytest.approx(0.0, abs=1e-9)
        assert obi_flat10 == pytest.approx(0.0, abs=1e-9)
        # With imbalance only in first level, truncation matters:
        big_bids = [OrderBookLevel(100, 100.0)] + [
            OrderBookLevel(100 - i, 1.0) for i in range(1, 10)
        ]
        big_asks = tuple(OrderBookLevel(101 + i, 1.0) for i in range(10))
        _, obi_w_n3 = compute_obi_weighted(tuple(big_bids), big_asks, obi_top_n=3)
        _, obi_w_n10 = compute_obi_weighted(tuple(big_bids), big_asks, obi_top_n=10)
        # N=3: bid_weighted = 100*1 + 1*0.5 + 1*0.25 = 100.75; ask_weighted = 1+0.5+0.25 = 1.75
        assert obi_w_n3 > obi_w_n10  # imbalance dilutes as N grows

    def test_empty_returns_zero(self) -> None:
        obi_flat, obi_weighted = compute_obi_weighted((), (), obi_top_n=10)
        assert obi_flat == 0.0
        assert obi_weighted == 0.0


# ---------------------------------------------------------------------------
# 2. classify_obi_regime: 5-bucket classification
# ---------------------------------------------------------------------------


class TestClassifyObiRegime:
    @pytest.mark.parametrize(
        "value, expected",
        [
            (0.8, OBIRegime.STRONG_BID),
            (0.6, OBIRegime.STRONG_BID),
            (0.4, OBIRegime.MILD_BID),
            (0.2, OBIRegime.MILD_BID),
            (0.1, OBIRegime.NEUTRAL),
            (0.0, OBIRegime.NEUTRAL),
            (-0.1, OBIRegime.NEUTRAL),
            (-0.2, OBIRegime.MILD_ASK),
            (-0.4, OBIRegime.MILD_ASK),
            (-0.6, OBIRegime.STRONG_ASK),
            (-0.8, OBIRegime.STRONG_ASK),
        ],
    )
    def test_bucket_boundaries(self, value: float, expected: OBIRegime) -> None:
        assert classify_obi_regime(value) == expected

    def test_custom_threshold(self) -> None:
        # With strong_threshold=0.8, 0.7 should be MILD_BID
        assert classify_obi_regime(0.7, strong_threshold=0.8) == OBIRegime.MILD_BID
        assert classify_obi_regime(0.8, strong_threshold=0.8) == OBIRegime.STRONG_BID


# ---------------------------------------------------------------------------
# 3. compute_obi_zscore: rolling z-score
# ---------------------------------------------------------------------------


class TestComputeObiZscore:
    def test_fewer_than_10_samples_returns_none(self) -> None:
        series = [0.1, 0.2, 0.3]
        assert compute_obi_zscore(series) is None

    def test_constant_series_returns_zero(self) -> None:
        # std=0 → z-score formula yields 0.0
        series = [0.5] * 15
        result = compute_obi_zscore(series)
        assert result == pytest.approx(0.0, abs=1e-9)

    def test_outlier_returns_high_zscore(self) -> None:
        # 19 values of 0, one extreme value = large positive z-score
        series = [0.0] * 19 + [1.0]
        z = compute_obi_zscore(series, lookback=20)
        assert z is not None
        assert z > 1.5

    def test_deque_input_accepted(self) -> None:
        dq: deque[float] = deque([0.0] * 10 + [1.0], maxlen=60)
        z = compute_obi_zscore(dq)
        assert z is not None


# ---------------------------------------------------------------------------
# 4. detect_order_walls: positive + negative + suspect flag
# ---------------------------------------------------------------------------


class TestDetectOrderWalls:
    def _make_levels(
        self, sizes: list[float], start_price: float, step: float, descending: bool
    ) -> tuple[OrderBookLevel, ...]:
        levels = []
        for i, sz in enumerate(sizes):
            price = start_price - i * step if descending else start_price + i * step
            levels.append(OrderBookLevel(price, sz))
        return tuple(levels)

    def test_no_wall_when_all_equal(self) -> None:
        bids = self._make_levels([10.0] * 10, 100.0, 0.1, descending=True)
        asks = self._make_levels([10.0] * 10, 101.0, 0.1, descending=False)
        bid_walls, ask_walls = detect_order_walls(bids, asks, mid_price=100.5)
        assert bid_walls == []
        assert ask_walls == []

    def test_detects_wall_on_bid_side(self) -> None:
        # One bid level has size = 6 × median (= 6 × 10 > 5 × 10)
        sizes = [10.0] * 9 + [60.0]
        bids = self._make_levels(sizes, 100.0, 0.1, descending=True)
        asks = self._make_levels([10.0] * 10, 101.0, 0.1, descending=False)
        bid_walls, ask_walls = detect_order_walls(bids, asks, mid_price=100.5)
        assert len(bid_walls) == 1
        assert bid_walls[0].size == 60.0
        assert ask_walls == []

    def test_far_wall_is_suspect(self) -> None:
        # Wall price is 5% away from mid → suspect
        sizes = [10.0] * 9 + [60.0]
        # Last bid level at 100 - 9*1 = 91, which is ~9.5% from mid=100.5
        bids = self._make_levels(sizes, 100.0, 1.0, descending=True)
        asks = self._make_levels([10.0] * 10, 101.0, 0.1, descending=False)
        bid_walls, _ = detect_order_walls(bids, asks, mid_price=100.5, wall_distance_pct=2.0)
        assert len(bid_walls) == 1
        assert bid_walls[0].suspect is True

    def test_new_wall_not_in_prev_snapshot_is_suspect(self) -> None:
        sizes = [10.0] * 9 + [60.0]
        bids = self._make_levels(sizes, 100.0, 0.1, descending=True)
        asks = self._make_levels([10.0] * 10, 101.0, 0.1, descending=False)
        # Previous snapshot had NO walls (wall at bids[-1].price appears fresh)
        prev_bids: frozenset[float] = frozenset()
        bid_walls, _ = detect_order_walls(
            bids,
            asks,
            mid_price=100.5,
            prev_bid_walls=prev_bids,
            wall_distance_pct=2.0,
        )
        assert len(bid_walls) == 1
        assert bid_walls[0].suspect is True  # new wall → suspect


# ---------------------------------------------------------------------------
# 5. compute_delta_vamp: cross-multiply formula
# ---------------------------------------------------------------------------


class TestComputeDeltaVamp:
    def test_equal_volumes_give_mid(self) -> None:
        bids = (OrderBookLevel(100.0, 10.0),)
        asks = (OrderBookLevel(101.0, 10.0),)
        vamp, mid, delta = compute_delta_vamp(bids, asks)
        assert mid == pytest.approx(100.5)
        # equal vols: vamp = (10*101 + 10*100) / 20 = 2010/20 = 100.5
        assert vamp == pytest.approx(100.5)
        assert delta == pytest.approx(0.0, abs=1e-9)

    def test_heavy_bid_side_pushes_vamp_above_mid(self) -> None:
        # Large bid vol → vamp pulled toward ask price
        bids = (OrderBookLevel(100.0, 100.0),)
        asks = (OrderBookLevel(101.0, 1.0),)
        vamp, mid, delta = compute_delta_vamp(bids, asks)
        # vamp = (100*101 + 1*100) / 101 ≈ 100.99
        assert delta > 0.0

    def test_empty_returns_zeros(self) -> None:
        vamp, mid, delta = compute_delta_vamp((), ())
        assert vamp == 0.0
        assert mid == 0.0
        assert delta == 0.0


# ---------------------------------------------------------------------------
# 6. is_spread_wide
# ---------------------------------------------------------------------------


class TestIsSpreadWide:
    def test_narrow_spread(self) -> None:
        bids = (OrderBookLevel(100.0, 1.0),)
        asks = (OrderBookLevel(100.05, 1.0),)  # 0.05% spread
        assert is_spread_wide(bids, asks, mid_price=100.025, max_spread_pct=0.001) is False

    def test_wide_spread(self) -> None:
        bids = (OrderBookLevel(100.0, 1.0),)
        asks = (OrderBookLevel(101.0, 1.0),)  # 1% spread
        assert is_spread_wide(bids, asks, mid_price=100.5, max_spread_pct=0.001) is True

    def test_empty_book_is_wide(self) -> None:
        assert is_spread_wide((), (), mid_price=100.0) is True


# ---------------------------------------------------------------------------
# 7. analyze_order_book (aggregator): positive detection → LONG
# ---------------------------------------------------------------------------


class TestAnalyzeOrderBook:
    def _build_strong_bid_snap(self) -> OrderBookSnapshot:
        """Craft a snapshot that should yield regime=STRONG_BID and LONG direction."""
        # 10 bid levels, all large; 10 ask levels, all small → obi_weighted ≈ +1
        bids = [(100.0 - i * 0.1, 100.0) for i in range(10)]
        asks = [(100.1 + i * 0.1, 1.0) for i in range(10)]
        return _snap(bids, asks)

    def test_strong_bid_regime_detected(self) -> None:
        snap = self._build_strong_bid_snap()
        sig = analyze_order_book(snap)
        assert sig.regime == OBIRegime.STRONG_BID
        assert sig.obi_weighted > 0.6

    def test_strong_bid_delta_vamp_positive(self) -> None:
        snap = self._build_strong_bid_snap()
        sig = analyze_order_book(snap)
        # Large bid vol → vamp pulled toward ask side → delta_vamp > 0
        assert sig.delta_vamp > 0.0

    def test_output_fields_complete(self) -> None:
        snap = self._build_strong_bid_snap()
        sig = analyze_order_book(snap)
        assert sig.symbol == "BTC/USDT"
        assert sig.best_bid < sig.best_ask
        assert sig.mid == pytest.approx((sig.best_bid + sig.best_ask) / 2.0)
        assert -1.0 <= sig.obi_flat <= 1.0
        assert -1.0 <= sig.obi_weighted <= 1.0
        assert sig.spread_abs >= 0.0
        assert sig.cum_bid_depth_in_band >= 0.0
        assert sig.cum_ask_depth_in_band >= 0.0

    def test_wide_spread_forces_wait(self) -> None:
        # Spread ~1% on 100.5 mid → spread_pct ≈ 1.0 > 0.1 threshold → WAIT
        bids = [(100.0, 100.0)]
        asks = [(101.0, 1.0)]
        snap = _snap(bids, asks)
        sig = analyze_order_book(snap, spread_pct_threshold=0.10)
        assert sig.spread_is_wide is True
        assert sig.direction == "WAIT"

    def test_narrow_spread_strong_bid_produces_long(self) -> None:
        # Spread 0.01%: bid=100.00, ask=100.01
        bids = [(100.00 - i * 0.001, 100.0) for i in range(10)]
        asks = [(100.01 + i * 0.001, 1.0) for i in range(10)]
        snap = _snap(bids, asks)
        sig = analyze_order_book(snap, spread_pct_threshold=0.10)
        assert sig.spread_is_wide is False
        assert sig.regime == OBIRegime.STRONG_BID
        # z-score None (first snapshot, no history) → no z-score block
        assert sig.obi_zscore is None
        assert sig.direction == "LONG"

    def test_confirmed_ask_wall_within_depth_band_forces_wait(self) -> None:
        """Regression (spec line 2007 '롱 회피').

        A *confirmed* (non-suspect) ask wall inside depth_band_pct% of mid — but
        OUTSIDE the fixed 0.5% entry band — must suppress the LONG signal.
        Previously the code only checked a depth_band_pct/2 (0.5%) band, so a
        wall in the 0.5%-1.0% zone leaked through as a LONG.
        """
        bids = [(100.0 - i * 0.001, 100.0) for i in range(10)]
        # tiny asks except a big wall at ~0.75% above mid (inside 1.0% avoid band)
        asks = [(100.01 + i * 0.001, 1.0) for i in range(9)]
        asks.append((100.76, 60.0))  # 6x median → wall; ~0.75% from mid
        snap = _snap(bids, asks)
        analyser = OrderBookAnalyser(depth_band_pct=1.0, wall_distance_pct=2.0)
        # Prime previous-snapshot wall set so the wall is NOT flagged 'new' (suspect)
        analyser._prev_ask_wall_prices = frozenset({100.76})
        analyser._prev_bid_wall_prices = frozenset()
        sig = analyser.analyse(snap)
        # Sanity: the wall is detected, confirmed (non-suspect), and inside the band
        assert sig.regime == OBIRegime.STRONG_BID
        assert sig.spread_is_wide is False
        assert sig.delta_vamp > 0.0
        assert any(w.price == pytest.approx(100.76) and not w.suspect for w in sig.ask_walls)
        wall_dist_pct = abs(100.76 - sig.mid) / sig.mid * 100.0
        assert 0.5 < wall_dist_pct < 1.0  # in the zone the old code missed
        assert sig.direction == "WAIT"  # avoided, NOT LONG

    def test_long_survives_when_wall_outside_avoid_band(self) -> None:
        """Negative twin: a confirmed ask wall BEYOND depth_band_pct% does NOT block LONG."""
        bids = [(100.0 - i * 0.001, 100.0) for i in range(10)]
        asks = [(100.01 + i * 0.001, 1.0) for i in range(9)]
        asks.append((101.55, 60.0))  # ~1.5% above mid → outside 1.0% avoid band
        snap = _snap(bids, asks)
        analyser = OrderBookAnalyser(depth_band_pct=1.0, wall_distance_pct=5.0)
        analyser._prev_ask_wall_prices = frozenset({101.55})
        analyser._prev_bid_wall_prices = frozenset()
        sig = analyser.analyse(snap)
        wall_dist_pct = abs(101.55 - sig.mid) / sig.mid * 100.0
        assert wall_dist_pct > 1.0  # outside avoid band
        assert any(w.price == pytest.approx(101.55) and not w.suspect for w in sig.ask_walls)
        assert sig.direction == "LONG"  # wall is too far to block entry

    def test_suspect_ask_wall_does_not_block_long(self) -> None:
        """A SUSPECT (fresh/far) ask wall is ignored for entry-path blocking (spec)."""
        bids = [(100.0 - i * 0.001, 100.0) for i in range(10)]
        asks = [(100.01 + i * 0.001, 1.0) for i in range(9)]
        asks.append((100.4, 60.0))  # ~0.4% from mid, inside entry band
        snap = _snap(bids, asks)
        # Fresh analyser → prev wall sets are None → 'new' suspicion NOT triggered,
        # so force suspicion via distance instead: wall_distance_pct tiny → far_away.
        sig = analyze_order_book(snap, wall_distance_pct=0.1)
        # wall is ~0.4% > 0.1% wall_distance_pct → flagged suspect
        assert any(w.price == pytest.approx(100.4) and w.suspect for w in sig.ask_walls)
        # suspect ask wall must NOT block the LONG
        assert sig.direction == "LONG"

    def test_strong_ask_regime_detected(self) -> None:
        # Mirror: large asks, tiny bids
        bids = [(100.0 - i * 0.1, 1.0) for i in range(10)]
        asks = [(100.1 + i * 0.1, 100.0) for i in range(10)]
        snap = _snap(bids, asks)
        sig = analyze_order_book(snap)
        assert sig.regime == OBIRegime.STRONG_ASK
        assert sig.obi_weighted < -0.6

    def test_neutral_regime_returns_neutral_direction(self) -> None:
        bids = [(100.0 - i * 0.1, 10.0) for i in range(10)]
        asks = [(100.1 + i * 0.1, 10.0) for i in range(10)]
        snap = _snap(bids, asks)
        sig = analyze_order_book(snap)
        assert sig.regime == OBIRegime.NEUTRAL
        assert sig.direction == "NEUTRAL"

    def test_crossed_book_raises(self) -> None:
        # best_bid >= best_ask is invalid
        snap = _snap([(101.0, 1.0)], [(100.0, 1.0)])
        with pytest.raises(ValueError, match="Crossed"):
            analyze_order_book(snap)

    def test_empty_book_raises(self) -> None:
        snap = _snap([], [(100.0, 1.0)])
        with pytest.raises(ValueError):
            analyze_order_book(snap)


# ---------------------------------------------------------------------------
# 8. OrderBookAnalyser: rolling z-score accumulates across calls
# ---------------------------------------------------------------------------


class TestOrderBookAnalyserRolling:
    def _make_balanced_snap(self, mid: float = 100.05) -> OrderBookSnapshot:
        half = mid - 0.05
        bids = [(half - i * 0.1, 10.0) for i in range(10)]
        asks = [(half + 0.1 + i * 0.1, 10.0) for i in range(10)]
        return _snap(bids, asks)

    def test_zscore_none_before_10_samples(self) -> None:
        analyser = OrderBookAnalyser()
        snap = self._make_balanced_snap()
        for _ in range(9):
            sig = analyser.analyse(snap)
        assert sig.obi_zscore is None

    def test_zscore_available_after_10_samples(self) -> None:
        analyser = OrderBookAnalyser()
        snap = self._make_balanced_snap()
        for _ in range(10):
            sig = analyser.analyse(snap)
        assert sig.obi_zscore is not None
        # Constant balanced book → z-score near 0
        assert sig.obi_zscore == pytest.approx(0.0, abs=1e-6)

    def test_analyser_state_reuses_history(self) -> None:
        """Verify that analyser accumulates history, unlike a fresh one-shot call."""
        analyser = OrderBookAnalyser()
        snap = self._make_balanced_snap()
        # Feed 10 samples
        for _ in range(10):
            analyser.analyse(snap)
        # 11th call on fresh analyser should have no history
        fresh = OrderBookAnalyser()
        sig_fresh = fresh.analyse(snap)
        sig_rolling = analyser.analyse(snap)
        assert sig_fresh.obi_zscore is None
        assert sig_rolling.obi_zscore is not None


# ---------------------------------------------------------------------------
# 9. Wall nearest-wall fields
# ---------------------------------------------------------------------------


class TestNearestWallFields:
    def test_nearest_bid_wall_is_closest_to_mid(self) -> None:
        # Two bid walls: one at 99.5 (near), one at 95.0 (far)
        bids = [
            (99.9, 1.0),
            (99.8, 1.0),
            (99.7, 1.0),
            (99.6, 1.0),
            (99.5, 60.0),  # wall
            (99.4, 1.0),
            (99.3, 1.0),
            (99.2, 1.0),
            (99.1, 1.0),
            (95.0, 60.0),  # wall but far
        ]
        asks = [(100.1 + i * 0.1, 1.0) for i in range(10)]
        snap = _snap(bids, asks)
        sig = analyze_order_book(snap, wall_size_multiplier=5.0, wall_distance_pct=100.0)
        # nearest bid wall = max price among detected bid walls = 99.5
        assert sig.nearest_bid_wall_price == pytest.approx(99.5)
        assert sig.nearest_bid_wall_vol == pytest.approx(60.0)

    def test_no_walls_gives_none(self) -> None:
        bids = [(100.0 - i * 0.1, 10.0) for i in range(10)]
        asks = [(100.1 + i * 0.1, 10.0) for i in range(10)]
        snap = _snap(bids, asks)
        sig = analyze_order_book(snap)
        assert sig.nearest_bid_wall_price is None
        assert sig.nearest_ask_wall_price is None


# ---------------------------------------------------------------------------
# 10. depth_ratio and cumulative depth band
# ---------------------------------------------------------------------------


class TestCumulativeDepth:
    def test_heavy_bid_side_gives_ratio_gt_one(self) -> None:
        # All bid levels within 1% band; very small asks
        bids = [(100.0 - i * 0.1, 50.0) for i in range(10)]  # all within 1% of mid=100.05
        asks = [(100.1 + i * 0.1, 1.0) for i in range(10)]
        snap = _snap(bids, asks)
        sig = analyze_order_book(snap, depth_band_pct=1.0)
        assert sig.depth_ratio > 1.0

    def test_balanced_depth_ratio_near_one(self) -> None:
        bids = [(100.0 - i * 0.01, 10.0) for i in range(10)]
        asks = [(100.1 + i * 0.01, 10.0) for i in range(10)]
        snap = _snap(bids, asks)
        sig = analyze_order_book(snap, depth_band_pct=1.0)
        # Same sizes on each side within band → ratio ≈ 1
        assert sig.depth_ratio == pytest.approx(1.0, rel=0.1)
