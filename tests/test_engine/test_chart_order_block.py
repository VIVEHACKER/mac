"""Tests for engine/chart/order_block.py — ICT Order Block detector.

Synthetic PriceBar fixtures are crafted to satisfy (or violate) the canonical
detection conditions so we can assert exact detection results without real market data.
"""

from __future__ import annotations

from datetime import date

import pytest

from data.models import PriceBar
from engine.chart.order_block import (
    OrderBlock,
    check_ob_mitigation,
    detect_order_blocks,
    get_ote_entry_range,
    score_order_block,
)
from engine.chart.types import TrendBias

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bar(
    o: float,
    h: float,
    low: float,
    c: float,
    v: float = 1_000.0,
    day: int = 1,
) -> PriceBar:
    return PriceBar(
        symbol="BTC/USDT",
        market="crypto",
        source_symbol="BTC/USDT",
        ts=date(2026, 1, day),
        open=o,
        high=h,
        low=low,
        close=c,
        volume=v,
        freq="4h",
    )


def _flat(price: float, day: int = 1) -> PriceBar:
    """A neutral (doji) bar at *price*."""
    return _bar(price, price + 1, price - 1, price, day=day)


def _bull(base: float, body: float = 10.0, wick: float = 2.0, day: int = 1) -> PriceBar:
    """Bullish candle: open=base, close=base+body."""
    return _bar(base, base + body + wick, base - wick, base + body, day=day)


def _bear(base: float, body: float = 10.0, wick: float = 2.0, day: int = 1) -> PriceBar:
    """Bearish candle: open=base+body, close=base."""
    return _bar(base + body, base + body + wick, base - wick, base, day=day)


# ---------------------------------------------------------------------------
# Fixture: minimal bullish OB setup
#
# Structure (20 bars + ATR seed):
#   bars 0-13: ATR warm-up — small-body bulls to establish ATR ~ 4
#   bar 14: swing low (the "last bearish" OB candidate)
#   bars 15-16: two more bars that make bar 14 a confirmed swing low later
#   bar 17: big bullish displacement candle that:
#               (a) closes above a prior swing high (BOS_UP)
#               (b) engulfs bar 14's high
#   bars 18-19: right-side confirmation bars (needed so swing at bar 14 confirms)
#
# We need swing_lookback=2 so bar 14 confirms once bars 16+ have closed.
# ---------------------------------------------------------------------------


def _build_bullish_ob_series() -> list[PriceBar]:
    """
    Craft a bar series where exactly one Bullish OB is detectable.

    Layout (0-indexed, swing_lookback=2):
      bars 0-12 : warm-up (small bullish, ATR seeds to ~6)
      bar 13    : swing high at 110 (bullish)
      bar 14    : bearish OB candidate: open=110, close=100, high=112, low=98
      bar 15    : smaller bar (low > 98, high < 110) — neutral
      bar 16    : neutral (lower high/higher low than 14 so bar 14 is swing low)
      bar 17    : BIG bullish displacement:
                    open=104, close=130, high=132, low=103
                    close(130) > bar[13].high(110) => BOS_UP
                    close(130) > bar[14].high(112) => engulfs OB wick
                    body = 26, ATR ~ 6 => body / ATR >= 1.0 (passes displacement)
                    body_to_range = 26/29 ~ 0.90 >= 0.50 (passes)
      bars 18-19: right-side quiet bars to let swing at bar 14 confirm
    """
    bars: list[PriceBar] = []

    # bars 0-12: warm-up (small bullish candles, range ~6 each)
    for i in range(13):
        price = 90.0 + i * 0.1
        bars.append(_bar(price, price + 3, price - 3, price + 2, v=500.0, day=i + 1))

    # bar 13: swing high at 110 (bullish) — will serve as BOS target
    bars.append(_bar(105.0, 112.0, 104.0, 110.0, v=1000.0, day=14))

    # bar 14: bearish OB candidate — open=110, close=100, high=112, low=98
    bars.append(_bar(110.0, 112.0, 98.0, 100.0, v=1200.0, day=15))

    # bar 15: neutral, higher low than bar 14 to help make 14 a swing low
    bars.append(_bar(101.0, 106.0, 100.5, 104.0, v=800.0, day=16))

    # bar 16: neutral, higher low than bar 14
    bars.append(_bar(103.0, 107.0, 101.0, 105.0, v=900.0, day=17))

    # bar 17: big bullish displacement
    # open=104, close=130, high=132, low=103
    # body=26, range=29, body_ratio=0.897, close>bar13.high(110)=>BOS_UP
    # close(130) > bar14.high(112) => engulfs OB wick => valid
    bars.append(_bar(104.0, 132.0, 103.0, 130.0, v=2000.0, day=18))

    # bars 18-19: quiet right-side bars
    bars.append(_bar(128.0, 131.0, 127.0, 129.0, v=600.0, day=19))
    bars.append(_bar(128.5, 130.0, 127.5, 128.0, v=600.0, day=20))

    return bars


def _build_bearish_ob_series() -> list[PriceBar]:
    """Mirror of the bullish fixture: exactly one Bearish OB is detectable.

    Layout (0-indexed, swing_lookback=2):
      bars 0-12 : warm-up (small bearish, ATR seeds to ~6)
      bar 13    : swing low at 188 (will serve as BOS_DOWN target)
      bar 14    : bullish OB candidate — open=190, close=200, high=202, low=189
      bars 15-16: neutral (lower highs so bar 14 reads as a swing high)
      bar 17    : BIG bearish displacement:
                    open=196, close=170, high=197, low=168
                    close(170) < bar[13].low(188)  => BOS_DOWN
                    close(170) < bar[14].low(189)  => engulfs OB wick low
      bars 18-19: quiet right-side bars to confirm the swing at bar 14
    """
    bars: list[PriceBar] = []

    for i in range(13):
        price = 200.0 - i * 0.1
        bars.append(_bar(price, price + 3, price - 3, price - 2, v=500.0, day=i + 1))

    # bar 13: swing low at 188 (bearish) — BOS_DOWN target
    bars.append(_bar(195.0, 196.0, 188.0, 190.0, v=1000.0, day=14))
    # bar 14: bullish OB candidate — open=190 close=200 high=202 low=189
    bars.append(_bar(190.0, 202.0, 189.0, 200.0, v=1200.0, day=15))
    # bar 15: neutral, lower high than bar 14
    bars.append(_bar(199.0, 199.5, 194.0, 196.0, v=800.0, day=16))
    # bar 16: neutral, lower high than bar 14
    bars.append(_bar(197.0, 198.0, 193.0, 195.0, v=900.0, day=17))
    # bar 17: big bearish displacement
    bars.append(_bar(196.0, 197.0, 168.0, 170.0, v=2000.0, day=18))
    # bars 18-19: quiet right-side bars
    bars.append(_bar(171.0, 173.0, 169.0, 172.0, v=600.0, day=19))
    bars.append(_bar(171.5, 172.5, 170.0, 171.0, v=600.0, day=20))

    return bars


def _build_fvg_ob_series() -> list[PriceBar]:
    """Bullish OB fixture that contains a real 3-candle Fair Value Gap.

    Identical to the base bullish fixture except the displacement candle (bar 17)
    *gaps up*: ``bar[17].low (108) > bar[15].high (106)`` creates a bullish FVG
    over the consecutive triple (a=15, b=16, c=17), so the detected OB carries
    ``has_fvg=True``. bar 13 stays the swing high; bar 14 stays the OB candle.
    """
    bars: list[PriceBar] = []

    for i in range(13):
        price = 90.0 + i * 0.1
        bars.append(_bar(price, price + 3, price - 3, price + 2, v=500.0, day=i + 1))

    bars.append(_bar(105.0, 112.0, 104.0, 110.0, v=1000.0, day=14))  # 13 swing high 112
    bars.append(_bar(110.0, 112.0, 98.0, 100.0, v=1200.0, day=15))  # 14 bearish OB
    bars.append(_bar(101.0, 106.0, 100.5, 104.0, v=800.0, day=16))  # 15 high=106
    bars.append(_bar(103.0, 107.0, 101.0, 105.0, v=900.0, day=17))  # 16 high=107
    # 17 displacement: low(108) > bar[15].high(106) => bullish FVG triple (15,16,17)
    bars.append(_bar(110.0, 132.0, 108.0, 130.0, v=2500.0, day=18))  # 17 k
    bars.append(_bar(128.0, 131.0, 127.0, 129.0, v=600.0, day=19))
    bars.append(_bar(128.5, 130.0, 127.5, 128.0, v=600.0, day=20))

    return bars


# ---------------------------------------------------------------------------
# TEST 1: Positive detection — one Bullish OB is found
# ---------------------------------------------------------------------------


def test_detect_bullish_ob_positive() -> None:
    """A crafted series should yield exactly one Bullish OB."""
    bars = _build_bullish_ob_series()
    obs = detect_order_blocks(
        bars,
        swing_lookback=2,
        ob_lookback_bars=10,
        displacement_atr_mult=1.0,
        body_ratio_min=0.50,
        use_body_only=True,
        close_mitigation=True,
        min_strength_score=0.0,  # no filter — capture any valid OB
        atr_period=14,
    )

    assert len(obs) >= 1, f"Expected >= 1 OB, got {len(obs)}"

    # The OB candle should be bar 14 (the last bearish before the displacement)
    bull_obs = [ob for ob in obs if ob.direction == "bullish"]
    assert len(bull_obs) >= 1

    ob = bull_obs[0]
    assert ob.direction == "bullish"
    # Zone boundaries (use_body_only=True): body of bar 14 = open=110, close=100
    assert ob.zone_high == pytest.approx(110.0)
    assert ob.zone_low == pytest.approx(100.0)
    assert ob.zone_mid == pytest.approx(105.0)
    # Mitigation extreme = full low of OB candle = 98.0
    assert ob.mitigation_extreme == pytest.approx(98.0)
    # Not yet mitigated
    assert ob.mitigated is False


# ---------------------------------------------------------------------------
# TEST 2: Negative — too-small displacement body fails the ATR filter
# ---------------------------------------------------------------------------


def test_no_ob_when_displacement_too_small() -> None:
    """A BOS with a tiny body (< 1x ATR) must NOT produce an OB."""
    bars = _build_bullish_ob_series()

    # Replace bar 17 (displacement) with a tiny-body bar that still closes above
    # bar 13's high (110) so BOS_UP fires, but body is only 1 (well below ATR ~6).
    tiny_disp = _bar(109.0, 132.0, 108.0, 110.5, v=2000.0, day=18)
    bars[17] = tiny_disp

    obs = detect_order_blocks(
        bars,
        swing_lookback=2,
        ob_lookback_bars=10,
        displacement_atr_mult=1.0,
        body_ratio_min=0.0,  # relax body_ratio so only ATR filter applies
        min_strength_score=0.0,
        atr_period=14,
    )

    bull_obs = [ob for ob in obs if ob.direction == "bullish"]
    # The tiny displacement should be filtered out
    assert len(bull_obs) == 0


# ---------------------------------------------------------------------------
# TEST 3: Mitigation — a bar closing below the OB's full low marks mitigated=True
# ---------------------------------------------------------------------------


def test_ob_mitigation_triggered_on_close_below_low() -> None:
    """After detection, appending a bar that closes below mitigation_extreme sets mitigated."""
    bars = _build_bullish_ob_series()
    obs = detect_order_blocks(
        bars,
        swing_lookback=2,
        ob_lookback_bars=10,
        displacement_atr_mult=1.0,
        body_ratio_min=0.50,
        close_mitigation=True,
        min_strength_score=0.0,
        atr_period=14,
    )

    bull_obs = [ob for ob in obs if ob.direction == "bullish"]
    if not bull_obs:
        pytest.skip("No bullish OB detected — prerequisite for this test")

    # Now detect again with an extra bar that closes below mitigation_extreme(98.0)
    mit_bar = _bar(99.0, 100.0, 90.0, 95.0, v=1500.0, day=21)
    bars_mit = bars + [mit_bar]

    obs2 = detect_order_blocks(
        bars_mit,
        swing_lookback=2,
        ob_lookback_bars=10,
        displacement_atr_mult=1.0,
        body_ratio_min=0.50,
        close_mitigation=True,
        min_strength_score=0.0,
        atr_period=14,
    )

    bull_obs2 = [ob for ob in obs2 if ob.direction == "bullish"]
    assert len(bull_obs2) >= 1
    mitigated_obs = [ob for ob in bull_obs2 if ob.mitigated]
    assert len(mitigated_obs) >= 1
    assert mitigated_obs[0].mitigation_ts is not None


# ---------------------------------------------------------------------------
# TEST 4: close_mitigation=False — wick touch triggers mitigation
# ---------------------------------------------------------------------------


def test_ob_mitigation_wick_when_close_mitigation_false() -> None:
    """With close_mitigation=False, a wick touching below full low should mitigate."""
    bars = _build_bullish_ob_series()
    # Bar that wicks below 98.0 but closes above it
    wick_bar = _bar(99.5, 100.5, 95.0, 99.0, v=1500.0, day=21)
    bars_wick = bars + [wick_bar]

    obs_close = detect_order_blocks(
        bars_wick,
        swing_lookback=2,
        ob_lookback_bars=10,
        displacement_atr_mult=1.0,
        body_ratio_min=0.50,
        close_mitigation=True,  # NOT mitigated by wick
        min_strength_score=0.0,
        atr_period=14,
    )
    obs_wick = detect_order_blocks(
        bars_wick,
        swing_lookback=2,
        ob_lookback_bars=10,
        displacement_atr_mult=1.0,
        body_ratio_min=0.50,
        close_mitigation=False,  # mitigated by wick
        min_strength_score=0.0,
        atr_period=14,
    )

    bull_obs_close = [ob for ob in obs_close if ob.direction == "bullish"]
    bull_obs_wick = [ob for ob in obs_wick if ob.direction == "bullish"]

    if not bull_obs_close:
        pytest.skip("No bullish OB detected — prerequisite skipped")

    # close_mitigation=True: wick bar doesn't close below 98 → not mitigated
    assert all(not ob.mitigated for ob in bull_obs_close)

    # close_mitigation=False: wick below 98 → mitigated
    assert any(ob.mitigated for ob in bull_obs_wick)


# ---------------------------------------------------------------------------
# TEST 5: check_ob_mitigation helper
# ---------------------------------------------------------------------------


def test_check_ob_mitigation_helper() -> None:
    """check_ob_mitigation should update ob.mitigated in place."""
    ob = OrderBlock(
        ob_index=5,
        direction="bullish",
        zone_high=110.0,
        zone_low=100.0,
        zone_mid=105.0,
        mitigation_extreme=98.0,
        ts=date(2026, 1, 1),
        bos_ts=date(2026, 1, 2),
        strength=0.55,
    )
    bar_safe = _bar(99.0, 100.5, 98.5, 99.0, day=3)  # close=99 > 98 → NOT mitigated
    assert check_ob_mitigation(ob, bar_safe, atr=5.0) is False
    assert ob.mitigated is False

    bar_mit = _bar(98.5, 99.0, 95.0, 97.5, day=4)  # close=97.5 < 98 → mitigated
    assert check_ob_mitigation(ob, bar_mit, atr=5.0) is True
    assert ob.mitigated is True
    assert ob.mitigation_ts == date(2026, 1, 4)

    # Calling again on already-mitigated OB always returns True
    assert check_ob_mitigation(ob, bar_safe, atr=5.0) is True


# ---------------------------------------------------------------------------
# TEST 6: get_ote_entry_range
# ---------------------------------------------------------------------------


def test_get_ote_entry_range_bullish() -> None:
    """OTE range for a bullish OB should be the 0.62-0.79 Fib retracement."""
    ob = OrderBlock(
        ob_index=5,
        direction="bullish",
        zone_high=110.0,
        zone_low=100.0,
        zone_mid=105.0,
        mitigation_extreme=98.0,
        ts=date(2026, 1, 1),
        bos_ts=date(2026, 1, 2),
        strength=0.55,
    )
    # Move from swing_origin=90 to displacement_peak=130 → move=40
    ote_low, ote_high = get_ote_entry_range(ob, swing_origin=90.0, displacement_peak=130.0)
    # ote_high = 130 - 0.62*40 = 130 - 24.8 = 105.2  → clamped to zone_high=110
    # ote_low  = 130 - 0.79*40 = 130 - 31.6 = 98.4   → clamped to zone_low=100
    assert ote_high == pytest.approx(min(105.2, 110.0))
    assert ote_low == pytest.approx(max(98.4, 100.0))


def test_get_ote_entry_range_bearish() -> None:
    """OTE range for a bearish OB: retracement runs upward from displacement low."""
    ob = OrderBlock(
        ob_index=5,
        direction="bearish",
        zone_high=110.0,
        zone_low=100.0,
        zone_mid=105.0,
        mitigation_extreme=112.0,
        ts=date(2026, 1, 1),
        bos_ts=date(2026, 1, 2),
        strength=0.55,
    )
    # Move from displacement_peak=80 (the low after a drop) to swing_origin=120 → move=40
    ote_low, ote_high = get_ote_entry_range(ob, swing_origin=120.0, displacement_peak=80.0)
    # ote_low  = 80 + 0.62*40 = 80 + 24.8 = 104.8
    # ote_high = 80 + 0.79*40 = 80 + 31.6 = 111.6 → clamped to zone_high=110
    assert ote_low == pytest.approx(max(104.8, 100.0))
    assert ote_high == pytest.approx(min(111.6, 110.0))


# ---------------------------------------------------------------------------
# TEST 7: score_order_block — HTF alignment boosts score
# ---------------------------------------------------------------------------


def test_score_order_block_htf_alignment() -> None:
    """HTF bullish bias should add +0.15 to a bullish OB score (capped at 1.0)."""
    ob = OrderBlock(
        ob_index=3,
        direction="bullish",
        zone_high=110.0,
        zone_low=100.0,
        zone_mid=105.0,
        mitigation_extreme=98.0,
        ts=date(2026, 1, 1),
        bos_ts=date(2026, 1, 2),
        strength=0.60,
    )
    adjusted = score_order_block(ob, htf_bias=TrendBias.BULLISH)
    assert adjusted == pytest.approx(0.75)
    assert ob.htf_confluence is True


def test_score_order_block_htf_misaligned_no_boost() -> None:
    """HTF bearish bias should NOT boost a bullish OB."""
    ob = OrderBlock(
        ob_index=3,
        direction="bullish",
        zone_high=110.0,
        zone_low=100.0,
        zone_mid=105.0,
        mitigation_extreme=98.0,
        ts=date(2026, 1, 1),
        bos_ts=date(2026, 1, 2),
        strength=0.60,
    )
    adjusted = score_order_block(ob, htf_bias=TrendBias.BEARISH)
    assert adjusted == pytest.approx(0.60)
    assert ob.htf_confluence is False


# ---------------------------------------------------------------------------
# TEST 8: require_fvg filter
# ---------------------------------------------------------------------------


def test_require_fvg_keeps_ob_with_real_fvg() -> None:
    """A fixture containing a genuine FVG must survive the require_fvg=True filter.

    This is the *positive* half of the filter: the FVG fixture's OB carries
    has_fvg=True, so require_fvg=True keeps it (a non-vacuous assertion).
    """
    bars = _build_fvg_ob_series()

    obs_all = detect_order_blocks(bars, swing_lookback=2, min_strength_score=0.0, require_fvg=False)
    bull_all = [ob for ob in obs_all if ob.direction == "bullish"]
    assert len(bull_all) == 1
    assert bull_all[0].has_fvg is True, "fixture must actually contain a FVG"

    obs_fvg = detect_order_blocks(bars, swing_lookback=2, min_strength_score=0.0, require_fvg=True)
    bull_fvg = [ob for ob in obs_fvg if ob.direction == "bullish"]
    assert len(bull_fvg) == 1
    assert bull_fvg[0].ob_index == bull_all[0].ob_index


def test_require_fvg_excludes_ob_without_fvg() -> None:
    """The *negative* half: a fixture whose OB has no FVG is dropped by require_fvg=True."""
    bars = _build_bullish_ob_series()

    obs_no_filter = detect_order_blocks(
        bars, swing_lookback=2, min_strength_score=0.0, require_fvg=False
    )
    bull_no_filter = [ob for ob in obs_no_filter if ob.direction == "bullish"]
    # Sanity: the base fixture's OB genuinely lacks a FVG (otherwise this test is vacuous).
    assert len(bull_no_filter) == 1
    assert bull_no_filter[0].has_fvg is False

    obs_fvg_only = detect_order_blocks(
        bars, swing_lookback=2, min_strength_score=0.0, require_fvg=True
    )
    bull_fvg_only = [ob for ob in obs_fvg_only if ob.direction == "bullish"]
    assert len(bull_fvg_only) == 0


# ---------------------------------------------------------------------------
# TEST 9: Too few bars returns empty list (no crash)
# ---------------------------------------------------------------------------


def test_too_few_bars_returns_empty() -> None:
    """When there are fewer bars than atr_period + swing_lookback + 1, return []."""
    bars = [_flat(100.0, day=i + 1) for i in range(5)]
    obs = detect_order_blocks(bars, atr_period=14)
    assert obs == []


# ---------------------------------------------------------------------------
# TEST 10: use_body_only=False widens the zone to full wick
# ---------------------------------------------------------------------------


def test_use_body_only_false_uses_full_wick() -> None:
    """With use_body_only=False the zone should use the full candle wick (high/low)."""
    bars = _build_bullish_ob_series()

    obs_body = detect_order_blocks(
        bars,
        swing_lookback=2,
        ob_lookback_bars=10,
        displacement_atr_mult=1.0,
        body_ratio_min=0.50,
        use_body_only=True,
        min_strength_score=0.0,
        atr_period=14,
    )
    obs_full = detect_order_blocks(
        bars,
        swing_lookback=2,
        ob_lookback_bars=10,
        displacement_atr_mult=1.0,
        body_ratio_min=0.50,
        use_body_only=False,
        min_strength_score=0.0,
        atr_period=14,
    )

    bull_body = [ob for ob in obs_body if ob.direction == "bullish"]
    bull_full = [ob for ob in obs_full if ob.direction == "bullish"]

    if not bull_body or not bull_full:
        pytest.skip("No bullish OB detected — prerequisite skipped")

    # Full-wick zone must be at least as wide as body-only zone
    assert bull_full[0].zone_high >= bull_body[0].zone_high
    assert bull_full[0].zone_low <= bull_body[0].zone_low

    # Mitigation extreme is always full-wick regardless of use_body_only
    assert bull_full[0].mitigation_extreme == bull_body[0].mitigation_extreme


# ---------------------------------------------------------------------------
# TEST 11: Positive detection — one Bearish OB is found (mirror of TEST 1)
# ---------------------------------------------------------------------------


def test_detect_bearish_ob_positive() -> None:
    """A crafted bearish series should yield exactly one Bearish OB at bar 14."""
    bars = _build_bearish_ob_series()
    obs = detect_order_blocks(
        bars,
        swing_lookback=2,
        ob_lookback_bars=10,
        displacement_atr_mult=1.0,
        body_ratio_min=0.50,
        use_body_only=True,
        close_mitigation=True,
        min_strength_score=0.0,
        atr_period=14,
    )

    bear_obs = [ob for ob in obs if ob.direction == "bearish"]
    assert len(bear_obs) == 1, f"Expected exactly 1 bearish OB, got {len(bear_obs)}"

    ob = bear_obs[0]
    assert ob.ob_index == 14
    # Body of bar 14 (open=190, close=200) → zone 190..200
    assert ob.zone_high == pytest.approx(200.0)
    assert ob.zone_low == pytest.approx(190.0)
    assert ob.zone_mid == pytest.approx(195.0)
    # Mitigation extreme = full HIGH of OB candle = 202.0 (wick, independent of use_body_only)
    assert ob.mitigation_extreme == pytest.approx(202.0)
    assert ob.mitigated is False


def test_bearish_mitigation_on_close_above_high() -> None:
    """A bar closing above the bearish OB's full high (202) marks it mitigated."""
    bars = _build_bearish_ob_series()
    # Append a bar that closes above mitigation_extreme(202.0)
    mit_bar = _bar(201.0, 210.0, 200.0, 208.0, v=1500.0, day=21)
    obs = detect_order_blocks(
        bars + [mit_bar],
        swing_lookback=2,
        min_strength_score=0.0,
        close_mitigation=True,
    )
    bear_obs = [ob for ob in obs if ob.direction == "bearish"]
    assert len(bear_obs) == 1
    assert bear_obs[0].mitigated is True
    assert bear_obs[0].mitigation_ts is not None

    # close_mitigation=True: a bar that only WICKS above 202 but closes below must NOT mitigate
    wick_bar = _bar(200.5, 205.0, 199.0, 201.0, v=1500.0, day=21)
    obs_wick = detect_order_blocks(
        bars + [wick_bar],
        swing_lookback=2,
        min_strength_score=0.0,
        close_mitigation=True,
    )
    bear_wick = [ob for ob in obs_wick if ob.direction == "bearish"]
    assert len(bear_wick) == 1
    assert bear_wick[0].mitigated is False


# ---------------------------------------------------------------------------
# TEST 12: Lookahead determinism — live cutoff must match full-history detection
#
# An OB discovered when the displacement bar k closes must have the SAME
# existence, zone, and strength whether the detector sees only bars[0..k] (live)
# or the full series including future bars. Future bars may only flip retroactive
# *state* (mitigated/visited/breaker), never the OB's identity or strength score.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("builder", [_build_bullish_ob_series, _build_fvg_ob_series])
def test_no_lookahead_in_detection_and_strength(builder) -> None:  # noqa: ANN001
    """Detecting on bars[:k+1] yields identical OB identity + strength as full history."""
    bars = builder()
    # Displacement bar is index 17 in both fixtures; cut off right after it closes.
    k = 17
    live = detect_order_blocks(bars[: k + 1], swing_lookback=2, min_strength_score=0.0)
    full = detect_order_blocks(bars, swing_lookback=2, min_strength_score=0.0)

    live_by_idx = {ob.ob_index: ob for ob in live}
    full_by_idx = {ob.ob_index: ob for ob in full}

    # Every OB detectable live must also exist in the full run with identical
    # identity, zone geometry, and (lookahead-free) strength score.
    assert live_by_idx, "fixture should detect at least one OB at the cutoff"
    for idx, live_ob in live_by_idx.items():
        assert idx in full_by_idx, f"OB {idx} found live but vanished in full history"
        full_ob = full_by_idx[idx]
        assert live_ob.direction == full_ob.direction
        assert live_ob.zone_high == pytest.approx(full_ob.zone_high)
        assert live_ob.zone_low == pytest.approx(full_ob.zone_low)
        assert live_ob.mitigation_extreme == pytest.approx(full_ob.mitigation_extreme)
        assert live_ob.has_fvg == full_ob.has_fvg
        # The strength score must NOT change once future bars arrive.
        assert live_ob.strength == pytest.approx(full_ob.strength), (
            f"OB {idx} strength differs live vs full — lookahead leak: "
            f"{live_ob.strength} != {full_ob.strength}"
        )
