"""Tests for engine/chart/candles.py — candlestick pattern detector.

Fixtures are hand-crafted synthetic PriceBar sequences designed to trigger
(or not trigger) each pattern type. All timestamps use date objects (1d bars).
"""

from __future__ import annotations

from datetime import date

from data.models import PriceBar
from engine.chart.candles import (
    CandlePattern,
    check_confirmation,
    check_confirmation_strict,
    classify_candle_strength,
    classify_direction,
    detect_candlestick_patterns,
    get_candle_entry_state,
    is_pattern_at_level,
    is_pattern_at_level_price,
)
from engine.chart.types import EntryState

# ---------------------------------------------------------------------------
# Helper: build PriceBar quickly
# ---------------------------------------------------------------------------


def _bar(
    ts: int,
    o: float,
    h: float,
    l: float,  # noqa: E741
    c: float,
    vol: float = 1000.0,
    sym: str = "TEST",
    market: str = "crypto",
) -> PriceBar:
    """Create a PriceBar with required positional fields."""
    return PriceBar(
        symbol=sym,
        market=market,
        source_symbol=sym,
        ts=date(2024, 1, ts),
        open=o,
        high=h,
        low=l,
        close=c,
        volume=vol,
        freq="1d",
    )


# ---------------------------------------------------------------------------
# Utility — quick downtrend / uptrend prefix
# ---------------------------------------------------------------------------


def _downtrend_prefix(n: int = 7, start: int = 1, start_price: float = 110.0) -> list[PriceBar]:
    """Produce *n* consistently declining bear bars (HH and LL both decreasing)."""
    bars = []
    p = start_price
    for k in range(n):
        o = p
        c = p - 2.0
        h = o + 0.5
        l = c - 0.5  # noqa: E741
        bars.append(_bar(start + k, o, h, l, c))
        p = c
    return bars


def _uptrend_prefix(n: int = 7, start: int = 1, start_price: float = 90.0) -> list[PriceBar]:
    """Produce *n* consistently rising bull bars."""
    bars = []
    p = start_price
    for k in range(n):
        o = p
        c = p + 2.0
        h = c + 0.5
        l = o - 0.5  # noqa: E741
        bars.append(_bar(start + k, o, h, l, c))
        p = c
    return bars


# ===========================================================================
# 1. classify_direction
# ===========================================================================


class TestClassifyDirection:
    def test_bullish_patterns(self) -> None:
        for name in ("hammer", "bullish_engulfing", "morning_star", "three_white_soldiers"):
            assert classify_direction(name) == "bullish", name

    def test_bearish_patterns(self) -> None:
        for name in ("shooting_star", "bearish_engulfing", "evening_star", "three_black_crows"):
            assert classify_direction(name) == "bearish", name

    def test_neutral_patterns(self) -> None:
        for name in ("doji", "spinning_top"):
            assert classify_direction(name) == "neutral", name

    def test_unknown_returns_neutral(self) -> None:
        assert classify_direction("not_a_real_pattern") == "neutral"


# ===========================================================================
# 2. Marubozu detection
# ===========================================================================


class TestMarubozu:
    def _marubozu_bar(self, ts: int = 10, bull: bool = True) -> PriceBar:
        # body = 10, range = 10.2 → body_pct ≈ 0.98; tiny wicks < 5% of body
        if bull:
            return _bar(ts, 100.0, 100.2, 99.9, 110.0)
        else:
            return _bar(ts, 110.0, 110.2, 99.9, 100.0)

    def test_positive_bull_marubozu(self) -> None:
        prefix = _downtrend_prefix(9, start=1)
        signal = self._marubozu_bar(ts=10, bull=True)
        bars = prefix + [signal]
        results = detect_candlestick_patterns(bars)
        names = [p.pattern_name for p in results]
        assert "bull_marubozu" in names

    def test_positive_bear_marubozu(self) -> None:
        prefix = _uptrend_prefix(9, start=1)
        signal = self._marubozu_bar(ts=10, bull=False)
        bars = prefix + [signal]
        results = detect_candlestick_patterns(bars)
        names = [p.pattern_name for p in results]
        assert "bear_marubozu" in names

    def test_negative_not_marubozu_with_large_wick(self) -> None:
        # Large upper wick — should NOT be marubozu
        bar_with_wick = _bar(10, 100.0, 115.0, 99.5, 110.0)
        prefix = _downtrend_prefix(9, start=1)
        bars = prefix + [bar_with_wick]
        results = detect_candlestick_patterns(bars)
        names = [p.pattern_name for p in results]
        assert "bull_marubozu" not in names

    def test_direction_is_bullish(self) -> None:
        prefix = _downtrend_prefix(9, start=1)
        signal = self._marubozu_bar(ts=10, bull=True)
        bars = prefix + [signal]
        results = detect_candlestick_patterns(bars)
        marubozus = [p for p in results if p.pattern_name == "bull_marubozu"]
        assert all(p.direction == "bullish" for p in marubozus)


# ===========================================================================
# 3. Doji detection
# ===========================================================================


class TestDoji:
    def test_positive_plain_doji(self) -> None:
        # body_pct ≈ 0 (open == close), symmetric wicks
        signal = _bar(10, 100.0, 105.0, 95.0, 100.0)  # open == close → doji
        prefix = _downtrend_prefix(9, start=1)
        bars = prefix + [signal]
        results = detect_candlestick_patterns(bars)
        names = [p.pattern_name for p in results]
        # Should be doji (or dragonfly/gravestone if wick criteria match — here symmetric)
        assert "doji" in names or "dragonfly_doji" in names or "gravestone_doji" in names

    def test_dragonfly_doji_detected(self) -> None:
        # open=close=high, long lower wick
        # high=100.1 (very close to open/close), low=90.0
        # body_pct = 0/10.1 ≈ 0; upper_wick = 100.1-100=0.1 < 5% of range; lower_wick = 10
        signal = _bar(10, 100.0, 100.1, 90.0, 100.0)
        prefix = _downtrend_prefix(9, start=1)
        bars = prefix + [signal]
        results = detect_candlestick_patterns(bars)
        names = [p.pattern_name for p in results]
        assert "dragonfly_doji" in names

    def test_gravestone_doji_detected(self) -> None:
        # open=close=low, long upper wick
        signal = _bar(10, 90.0, 100.0, 89.9, 90.0)
        prefix = _uptrend_prefix(9, start=1)
        bars = prefix + [signal]
        results = detect_candlestick_patterns(bars)
        names = [p.pattern_name for p in results]
        assert "gravestone_doji" in names

    def test_negative_doji_large_body(self) -> None:
        # body_pct = 50% → not a doji
        signal = _bar(10, 100.0, 110.0, 90.0, 105.0)
        prefix = _downtrend_prefix(9, start=1)
        bars = prefix + [signal]
        results = detect_candlestick_patterns(bars)
        names = [p.pattern_name for p in results]
        assert "doji" not in names


# ===========================================================================
# 4. Hammer detection
# ===========================================================================


class TestHammer:
    def _hammer_bar(self, ts: int = 10) -> PriceBar:
        # Small body near top of range; long lower wick; tiny upper wick
        # o=99, c=100, h=100.5, l=93 → range=7.5, body=1, lower_wick=6, upper_wick=0.5
        # body_pct=1/7.5=0.133 ≤ 0.35 ✓; lower_wick=6 ≥ 2×1=2 ✓; upper_wick=0.5 ≤ 0.1×7.5=0.75 ✓
        # body_bottom=99 ≥ 93 + 0.6×7.5=97.5 ✓ (99 ≥ 97.5)
        return _bar(ts, 99.0, 100.5, 93.0, 100.0)

    def test_positive_hammer_in_downtrend(self) -> None:
        prefix = _downtrend_prefix(9, start=1)
        signal = self._hammer_bar(ts=10)
        bars = prefix + [signal]
        results = detect_candlestick_patterns(bars)
        names = [p.pattern_name for p in results]
        assert "hammer" in names

    def test_negative_hammer_in_uptrend(self) -> None:
        # Same shape but in uptrend → hanging man, not hammer
        prefix = _uptrend_prefix(9, start=1)
        # Build a hammer-shape bar at uptrend prices
        p = prefix[-1].close
        signal = _bar(10, p - 0.5, p, p - 6.0, p - 0.1)
        bars = prefix + [signal]
        results = detect_candlestick_patterns(bars)
        names = [p2.pattern_name for p2 in results]
        assert "hammer" not in names

    def test_hammer_direction_bullish(self) -> None:
        prefix = _downtrend_prefix(9, start=1)
        signal = self._hammer_bar(ts=10)
        bars = prefix + [signal]
        results = detect_candlestick_patterns(bars)
        hammers = [p for p in results if p.pattern_name == "hammer"]
        assert len(hammers) >= 1
        assert all(p.direction == "bullish" for p in hammers)

    def test_negative_hammer_small_lower_wick(self) -> None:
        # Lower wick only 1× body — fails wick_ratio ≥ 2.0
        # body=5, lower_wick=4, upper_wick=0.5, range=9.5 → body_pct≈0.53 > 0.35 → also fails
        signal = _bar(10, 95.0, 100.5, 90.5, 100.0)
        prefix = _downtrend_prefix(9, start=1)
        bars = prefix + [signal]
        results = detect_candlestick_patterns(bars)
        names = [p.pattern_name for p in results]
        assert "hammer" not in names


# ===========================================================================
# 5. Hanging Man detection
# ===========================================================================


class TestHangingMan:
    def test_positive_hanging_man_in_uptrend(self) -> None:
        prefix = _uptrend_prefix(9, start=1)
        last_close = prefix[-1].close
        # Same shape as hammer but in uptrend
        # body near top, long lower wick
        o = last_close + 0.5
        c = last_close + 1.0
        h = c + 0.3
        low = c - 7.0  # long lower wick
        signal = _bar(10, o, h, low, c)
        bars = prefix + [signal]
        results = detect_candlestick_patterns(bars)
        names = [p.pattern_name for p in results]
        assert "hanging_man" in names

    def test_hanging_man_direction_bearish(self) -> None:
        prefix = _uptrend_prefix(9, start=1)
        last_close = prefix[-1].close
        o = last_close + 0.5
        c = last_close + 1.0
        h = c + 0.3
        low = c - 7.0
        signal = _bar(10, o, h, low, c)
        bars = prefix + [signal]
        results = detect_candlestick_patterns(bars)
        hm = [p for p in results if p.pattern_name == "hanging_man"]
        assert len(hm) >= 1
        assert all(p.direction == "bearish" for p in hm)


# ===========================================================================
# 6. Shooting Star detection
# ===========================================================================


class TestShootingStar:
    def test_positive_shooting_star_in_uptrend(self) -> None:
        prefix = _uptrend_prefix(9, start=1)
        last_close = prefix[-1].close
        # Long upper wick, tiny body near bottom, tiny lower wick
        # o=last_close, c=last_close+0.5, h=last_close+8, l=last_close-0.3
        # body=0.5, upper_wick=7.5, lower_wick=0.3, range=8.3
        # body_pct=0.5/8.3≈0.060 ≤ 0.35 ✓; upper_wick=7.5 ≥ 2×0.5=1.0 ✓
        # lower_wick=0.3 ≤ 0.1×8.3=0.83 ✓; body_top=last_close+0.5
        # high - body_top = 7.5 ≥ 0.6×8.3=4.98 ✓
        o = last_close
        c = last_close + 0.5
        h = last_close + 8.0
        low = last_close - 0.3
        signal = _bar(10, o, h, low, c)
        bars = prefix + [signal]
        results = detect_candlestick_patterns(bars)
        names = [p.pattern_name for p in results]
        assert "shooting_star" in names

    def test_negative_shooting_star_in_downtrend(self) -> None:
        prefix = _downtrend_prefix(9, start=1)
        last_close = prefix[-1].close
        o = last_close
        c = last_close + 0.5
        h = last_close + 8.0
        low = last_close - 0.3
        signal = _bar(10, o, h, low, c)
        bars = prefix + [signal]
        results = detect_candlestick_patterns(bars)
        names = [p.pattern_name for p in results]
        assert "shooting_star" not in names


# ===========================================================================
# 7. Bullish Engulfing
# ===========================================================================


class TestBullishEngulfing:
    def test_positive_bullish_engulfing(self) -> None:
        # Downtrend prefix, then bear bar, then larger bull bar that engulfs
        prefix = _downtrend_prefix(8, start=1)
        last_p = prefix[-1].close
        bear = _bar(9, last_p, last_p + 0.2, last_p - 3.0, last_p - 2.5)
        # Bull bar: opens below bear's close, closes above bear's open
        bull = _bar(10, last_p - 3.0, last_p + 1.0, last_p - 3.5, last_p + 0.5)
        bars = prefix + [bear, bull]
        results = detect_candlestick_patterns(bars)
        names = [p.pattern_name for p in results]
        assert "bullish_engulfing" in names

    def test_negative_same_direction_engulfing(self) -> None:
        # Two bull bars — not valid bullish engulfing (needs bear prev)
        prefix = _downtrend_prefix(8, start=1)
        last_p = prefix[-1].close
        bull1 = _bar(9, last_p - 2.0, last_p + 0.5, last_p - 2.5, last_p)
        bull2 = _bar(10, last_p - 3.0, last_p + 2.0, last_p - 4.0, last_p + 1.5)
        bars = prefix + [bull1, bull2]
        results = detect_candlestick_patterns(bars)
        names = [p.pattern_name for p in results]
        assert "bullish_engulfing" not in names

    def test_engulfing_body_must_be_larger(self) -> None:
        # Curr body same as prev — strict check (>), should fail
        prefix = _downtrend_prefix(8, start=1)
        last_p = prefix[-1].close
        bear = _bar(9, last_p, last_p + 0.5, last_p - 2.0, last_p - 1.5)
        # body(bear) = 1.5; body(bull) = 1.5 → not strictly greater
        bull = _bar(10, last_p - 2.0, last_p + 0.5, last_p - 2.5, last_p - 0.5)
        bars = prefix + [bear, bull]
        results = detect_candlestick_patterns(bars)
        names = [p.pattern_name for p in results]
        assert "bullish_engulfing" not in names


# ===========================================================================
# 8. Bearish Engulfing
# ===========================================================================


class TestBearishEngulfing:
    def test_positive_bearish_engulfing(self) -> None:
        prefix = _uptrend_prefix(8, start=1)
        last_p = prefix[-1].close
        bull = _bar(9, last_p - 0.2, last_p + 2.0, last_p - 0.5, last_p + 1.5)
        bear = _bar(10, last_p + 2.0, last_p + 2.5, last_p - 1.0, last_p - 0.5)
        bars = prefix + [bull, bear]
        results = detect_candlestick_patterns(bars)
        names = [p.pattern_name for p in results]
        assert "bearish_engulfing" in names

    def test_bearish_engulfing_needs_uptrend(self) -> None:
        prefix = _downtrend_prefix(8, start=1)
        last_p = prefix[-1].close
        bull = _bar(9, last_p - 0.2, last_p + 2.0, last_p - 0.5, last_p + 1.5)
        bear = _bar(10, last_p + 2.0, last_p + 2.5, last_p - 1.0, last_p - 0.5)
        bars = prefix + [bull, bear]
        results = detect_candlestick_patterns(bars)
        names = [p.pattern_name for p in results]
        assert "bearish_engulfing" not in names


# ===========================================================================
# 9. Bullish Harami
# ===========================================================================


class TestBullishHarami:
    def test_positive_bullish_harami(self) -> None:
        # Downtrend: big bear bar, then small bull inside its body
        prefix = _downtrend_prefix(8, start=1)
        # Big bear: open=102, close=95 → body=7, range≈7.5, body_pct≈0.93 ≥ 0.60 ✓
        big_bear = _bar(9, 102.0, 102.5, 94.5, 95.0)
        # Small bull: open=97 > 95 (bear close), close=99 < 102 (bear open) — inside body
        small_bull = _bar(10, 97.0, 99.5, 96.5, 99.0)
        bars = prefix + [big_bear, small_bull]
        results = detect_candlestick_patterns(bars)
        names = [p.pattern_name for p in results]
        assert "bullish_harami" in names

    def test_negative_harami_body_outside(self) -> None:
        # curr body exceeds prev body → not harami
        prefix = _downtrend_prefix(8, start=1)
        big_bear = _bar(9, 102.0, 102.5, 94.5, 95.0)
        # curr.close > prev.open → outside
        outside_bull = _bar(10, 97.0, 104.0, 96.5, 103.5)
        bars = prefix + [big_bear, outside_bull]
        results = detect_candlestick_patterns(bars)
        names = [p.pattern_name for p in results]
        assert "bullish_harami" not in names


# ===========================================================================
# 10. Piercing Line
# ===========================================================================


class TestPiercingLine:
    def test_positive_piercing_line(self) -> None:
        # Downtrend: big bear, then bull opens below bear close and closes above midpoint
        prefix = _downtrend_prefix(8, start=1)
        # bear: open=105, close=98 → body_mid=101.5
        bear = _bar(9, 105.0, 105.5, 97.5, 98.0)
        # bull: open=97.5 < 98 (below bear close) ✓; close=102 > 101.5 (mid) ✓; close=102 < 105 ✓
        bull = _bar(10, 97.5, 103.0, 97.0, 102.0)
        bars = prefix + [bear, bull]
        results = detect_candlestick_patterns(bars)
        names = [p.pattern_name for p in results]
        assert "piercing_line" in names

    def test_negative_piercing_closes_at_midpoint(self) -> None:
        # close == midpoint → strict inequality fails
        prefix = _downtrend_prefix(8, start=1)
        bear = _bar(9, 105.0, 105.5, 97.5, 98.0)
        # body_mid = (105+98)/2 = 101.5; bull closes exactly at 101.5
        bull = _bar(10, 97.5, 102.0, 97.0, 101.5)
        bars = prefix + [bear, bull]
        results = detect_candlestick_patterns(bars)
        names = [p.pattern_name for p in results]
        assert "piercing_line" not in names


# ===========================================================================
# 11. Dark Cloud Cover
# ===========================================================================


class TestDarkCloudCover:
    def test_positive_dark_cloud_cover(self) -> None:
        # Uptrend: big bull, then bear gaps up and closes below midpoint
        prefix = _uptrend_prefix(8, start=1)
        # bull: open=95, close=102 → body_mid=98.5
        bull = _bar(9, 95.0, 102.5, 94.5, 102.0)
        # bear: open=103 > 102 (gap up) ✓; close=97 < 98.5 (mid) ✓; close=97 > 95 ✓
        bear = _bar(10, 103.0, 104.0, 96.5, 97.0)
        bars = prefix + [bull, bear]
        results = detect_candlestick_patterns(bars)
        names = [p.pattern_name for p in results]
        assert "dark_cloud_cover" in names

    def test_negative_dark_cloud_no_gap_up(self) -> None:
        # No gap: curr.open <= prev.close → fails
        prefix = _uptrend_prefix(8, start=1)
        bull = _bar(9, 95.0, 102.5, 94.5, 102.0)
        bear = _bar(10, 102.0, 103.0, 96.5, 97.0)  # open == prev.close, no gap
        bars = prefix + [bull, bear]
        results = detect_candlestick_patterns(bars)
        names = [p.pattern_name for p in results]
        assert "dark_cloud_cover" not in names


# ===========================================================================
# 12. Tweezer Bottom
# ===========================================================================


class TestTweezerBottom:
    def test_positive_tweezer_bottom(self) -> None:
        prefix = _downtrend_prefix(8, start=1)
        # bear then bull with matching lows (within 0.3% tolerance)
        prev_low = 95.0
        bear = _bar(9, 100.0, 100.5, prev_low, 96.0)
        bull = _bar(10, 96.0, 101.0, prev_low + 0.1, 100.5)
        bars = prefix + [bear, bull]
        results = detect_candlestick_patterns(bars)
        names = [p.pattern_name for p in results]
        assert "tweezer_bottom" in names

    def test_negative_tweezer_bottom_lows_too_far(self) -> None:
        prefix = _downtrend_prefix(8, start=1)
        bear = _bar(9, 100.0, 100.5, 95.0, 96.0)
        # Low differs by 1.0 on 95.0 base = 1.05% >> 0.3%
        bull = _bar(10, 96.0, 101.0, 94.0, 100.5)
        bars = prefix + [bear, bull]
        results = detect_candlestick_patterns(bars)
        names = [p.pattern_name for p in results]
        assert "tweezer_bottom" not in names


# ===========================================================================
# 13. Morning Star
# ===========================================================================


class TestMorningStar:
    def test_positive_morning_star(self) -> None:
        # Downtrend (bars 1-8), then c1 big bear, c2 small star below c1 close, c3 big bull
        prefix = _downtrend_prefix(8, start=1)
        # c1: big bear, body_pct ≥ 0.50
        c1 = _bar(9, 105.0, 105.5, 97.5, 98.0)  # body=7, range≈8, body_pct≈0.875
        # c2: small star, body entirely below c1.close(=98): max(open,close) < 98
        c2 = _bar(10, 96.0, 96.8, 95.2, 96.5)  # body_pct=(0.5/1.6)≈0.31 ≤ 0.30? Need ≤0.30
        # Let's use a tighter star: open=96, close=96.2, high=96.5, low=95.8
        # body_pct=0.2/0.7≈0.286 ≤ 0.30 ✓; max(open,close)=96.2 < 98 ✓
        c2 = _bar(10, 96.0, 96.5, 95.8, 96.2)
        # c3: bull, close ≥ c1_mid=(105+98)/2=101.5
        c3 = _bar(11, 97.0, 104.0, 96.5, 103.0)
        bars = prefix + [c1, c2, c3]
        results = detect_candlestick_patterns(bars)
        names = [p.pattern_name for p in results]
        assert "morning_star" in names

    def test_negative_morning_star_star_not_below_c1_close(self) -> None:
        # Star body touches c1.close → fails the "completely below" guard
        prefix = _downtrend_prefix(8, start=1)
        c1 = _bar(9, 105.0, 105.5, 97.5, 98.0)
        # max(c2.open, c2.close) = 98.0 = c1.close → guard fails (must be <)
        c2 = _bar(10, 97.5, 98.5, 97.0, 98.0)
        c3 = _bar(11, 98.5, 104.0, 98.0, 103.0)
        bars = prefix + [c1, c2, c3]
        results = detect_candlestick_patterns(bars)
        names = [p.pattern_name for p in results]
        assert "morning_star" not in names


# ===========================================================================
# 14. Evening Star
# ===========================================================================


class TestEveningStar:
    def test_positive_evening_star(self) -> None:
        prefix = _uptrend_prefix(8, start=1)
        # c1: big bull, body_pct ≥ 0.50
        c1 = _bar(9, 95.0, 103.5, 94.5, 103.0)  # body=8, range≈9, body_pct≈0.89
        # c2: small star above c1.close(=103); min(open,close) > 103
        c2 = _bar(10, 104.0, 104.8, 103.5, 104.2)  # body_pct=(0.2/1.3)≈0.15 ≤ 0.30 ✓
        # c3: bear, close ≤ c1_mid=(95+103)/2=99
        c3 = _bar(11, 103.5, 104.0, 97.0, 98.5)
        bars = prefix + [c1, c2, c3]
        results = detect_candlestick_patterns(bars)
        names = [p.pattern_name for p in results]
        assert "evening_star" in names

    def test_negative_evening_star_c3_not_deep_enough(self) -> None:
        prefix = _uptrend_prefix(8, start=1)
        c1 = _bar(9, 95.0, 103.5, 94.5, 103.0)
        c2 = _bar(10, 104.0, 104.8, 103.5, 104.2)
        # c3 close = 100.5 > c1_mid=99 → fails penetration check
        c3 = _bar(11, 103.5, 104.0, 99.5, 100.5)
        bars = prefix + [c1, c2, c3]
        results = detect_candlestick_patterns(bars)
        names = [p.pattern_name for p in results]
        assert "evening_star" not in names


# ===========================================================================
# 15. Three White Soldiers
# ===========================================================================


class TestThreeWhiteSoldiers:
    def _soldiers(self, start_ts: int, base: float) -> list[PriceBar]:
        # Three consecutive large bull bars, each opening inside prior body, closing higher
        c1 = _bar(start_ts, base, base + 6.0, base - 0.2, base + 5.5, vol=2000.0)
        # c2 opens inside c1 body (base+2), closes above c1.close(base+5.5)
        c2 = _bar(start_ts + 1, base + 2.0, base + 10.5, base + 1.8, base + 10.0, vol=2000.0)
        # c3 opens inside c2 body (base+5), closes above c2.close(base+10)
        c3 = _bar(start_ts + 2, base + 5.0, base + 15.5, base + 4.8, base + 15.0, vol=2000.0)
        return [c1, c2, c3]

    def test_positive_three_white_soldiers(self) -> None:
        prefix = _downtrend_prefix(8, start=1)
        base = prefix[-1].close
        soldiers = self._soldiers(start_ts=9, base=base)
        bars = prefix + soldiers
        results = detect_candlestick_patterns(bars)
        names = [p.pattern_name for p in results]
        assert "three_white_soldiers" in names

    def test_negative_three_white_soldiers_c2_opens_outside(self) -> None:
        prefix = _downtrend_prefix(8, start=1)
        base = prefix[-1].close
        c1 = _bar(9, base, base + 6.0, base - 0.2, base + 5.5, vol=2000.0)
        # c2 opens above c1.close → outside body
        c2 = _bar(10, base + 6.0, base + 10.5, base + 5.8, base + 10.0, vol=2000.0)
        c3 = _bar(11, base + 7.0, base + 15.5, base + 6.8, base + 15.0, vol=2000.0)
        bars = prefix + [c1, c2, c3]
        results = detect_candlestick_patterns(bars)
        names = [p.pattern_name for p in results]
        assert "three_white_soldiers" not in names


# ===========================================================================
# 16. Three Black Crows
# ===========================================================================


class TestThreeBlackCrows:
    def _crows(self, start_ts: int, base: float) -> list[PriceBar]:
        c1 = _bar(start_ts, base, base + 0.2, base - 6.0, base - 5.5, vol=2000.0)
        # c2 opens inside c1 body (base-2), closes below c1.close
        c2 = _bar(start_ts + 1, base - 2.0, base - 1.8, base - 10.5, base - 10.0, vol=2000.0)
        # c3 opens inside c2 body, closes below c2.close
        c3 = _bar(start_ts + 2, base - 5.0, base - 4.8, base - 15.5, base - 15.0, vol=2000.0)
        return [c1, c2, c3]

    def test_positive_three_black_crows(self) -> None:
        prefix = _uptrend_prefix(8, start=1)
        base = prefix[-1].close
        crows = self._crows(start_ts=9, base=base)
        bars = prefix + crows
        results = detect_candlestick_patterns(bars)
        names = [p.pattern_name for p in results]
        assert "three_black_crows" in names


# ===========================================================================
# 17. Scan loop start index — no lookahead guard
# ===========================================================================


class TestScanLoopGuard:
    def test_minimum_bars_needed(self) -> None:
        # With trend_lookback=5, scan starts at index 7 (5+2).
        # With 7 bars, only index 7 (0-based: bar at position 7) is scanned → len must be ≥ 8
        bars = _downtrend_prefix(8, start=1)  # 8 bars → last index=7
        # Should run without error and may or may not find patterns
        results = detect_candlestick_patterns(bars)
        assert isinstance(results, list)

    def test_too_few_bars_returns_empty(self) -> None:
        bars = _downtrend_prefix(5, start=1)  # only 5 bars; start=7, loop body never executes
        results = detect_candlestick_patterns(bars)
        assert results == []

    def test_empty_input(self) -> None:
        results = detect_candlestick_patterns([])
        assert results == []


# ===========================================================================
# 18. Strength scoring
# ===========================================================================


class TestStrengthScoring:
    def test_single_candle_base_strength(self) -> None:
        # Single candle patterns start with score=0 then -1 → score=-1 → max(1, score+1)=max(1,0)=1
        prefix = _downtrend_prefix(9, start=1)
        # hammer-shape bar
        signal = _bar(10, 99.0, 100.5, 93.0, 100.0)
        bars = prefix + [signal]
        results = detect_candlestick_patterns(bars)
        hammers = [p for p in results if p.pattern_name == "hammer"]
        if hammers:
            assert hammers[0].strength >= 1
            assert hammers[0].strength <= 3

    def test_strength_via_classify_candle_strength(self) -> None:
        prefix = _downtrend_prefix(9, start=1)
        signal = _bar(10, 99.0, 100.5, 93.0, 100.0)
        bars = prefix + [signal]
        results = detect_candlestick_patterns(bars)
        for p in results:
            s = classify_candle_strength(p, bars)
            assert 1 <= s <= 3

    def test_high_volume_increases_strength(self) -> None:
        prefix = _downtrend_prefix(9, start=1)
        # Bull marubozu with very high volume
        signal = _bar(10, 100.0, 100.2, 99.9, 110.0, vol=50000.0)
        bars = prefix + [signal]
        results = detect_candlestick_patterns(bars)
        marubozus = [p for p in results if p.pattern_name == "bull_marubozu"]
        if marubozus:
            assert marubozus[0].vol_ratio > 1.0  # high volume relative to prefix


# ===========================================================================
# 19. check_confirmation
# ===========================================================================


class TestCheckConfirmation:
    def test_bullish_confirmation_uses_signal_high_not_next_bar_open(self) -> None:
        # Spec: bullish confirmation requires next_bar.close > SIGNAL BAR high.
        # signal_high=100.5; a next bar that is bullish (close>open) but whose close
        # is still BELOW the signal high must NOT confirm — guards against the
        # circular "next bar vs itself" bug.
        pattern = CandlePattern(
            ts=date(2024, 1, 10),
            symbol="TEST",
            market="crypto",
            freq="1d",
            pattern_name="hammer",
            direction="bullish",
            bar_i=9,
            prior_trend="down",
            strength=2,
            body_pct_signal=0.15,
            vol_ratio=1.2,
            signal_high=100.5,
            signal_low=93.0,
        )
        bullish_but_below_high = _bar(11, 99.0, 100.2, 98.5, 100.0)  # close 100.0 < 100.5
        assert check_confirmation(pattern, bullish_but_below_high) is False
        above_signal_high = _bar(11, 100.0, 102.0, 99.5, 101.0)  # close 101.0 > 100.5
        assert check_confirmation(pattern, above_signal_high) is True

    def test_bearish_confirmation_uses_signal_low(self) -> None:
        pattern = CandlePattern(
            ts=date(2024, 1, 10),
            symbol="TEST",
            market="crypto",
            freq="1d",
            pattern_name="shooting_star",
            direction="bearish",
            bar_i=9,
            prior_trend="up",
            strength=2,
            body_pct_signal=0.15,
            vol_ratio=1.2,
            signal_high=110.0,
            signal_low=100.0,
        )
        bearish_but_above_low = _bar(11, 105.0, 106.0, 100.5, 101.0)  # close 101 > 100 low
        assert check_confirmation(pattern, bearish_but_above_low) is False
        below_signal_low = _bar(11, 101.0, 101.5, 98.0, 99.0)  # close 99 < 100 low
        assert check_confirmation(pattern, below_signal_low) is True

    def test_detected_pattern_carries_signal_high_low(self) -> None:
        # End-to-end: detector must populate signal_high/low so check_confirmation
        # can apply the spec's strict rule instead of the directional proxy.
        prefix = _downtrend_prefix(9, start=1)
        signal = _bar(10, 99.0, 100.5, 93.0, 100.0)  # hammer shape
        bars = prefix + [signal]
        results = detect_candlestick_patterns(bars)
        hammers = [p for p in results if p.pattern_name == "hammer"]
        assert hammers, "expected a hammer to be detected"
        h = hammers[0]
        assert h.signal_high == 100.5
        assert h.signal_low == 93.0
        # Next bar closing at 100.4 (below signal high 100.5) must NOT confirm.
        assert check_confirmation(h, _bar(11, 100.0, 100.45, 99.0, 100.4)) is False
        # Next bar closing at 101.0 (above signal high) must confirm.
        assert check_confirmation(h, _bar(11, 100.0, 101.5, 99.0, 101.0)) is True

    def test_neutral_confirmation_requires_directional_close(self) -> None:
        pattern = CandlePattern(
            ts=date(2024, 1, 10),
            symbol="TEST",
            market="crypto",
            freq="1d",
            pattern_name="doji",
            direction="neutral",
            bar_i=9,
            prior_trend="neutral",
            strength=1,
            body_pct_signal=0.0,
            vol_ratio=1.0,
            signal_high=105.0,
            signal_low=95.0,
        )
        flat = _bar(11, 100.0, 101.0, 99.0, 100.0)  # close == open → not directional
        assert check_confirmation(pattern, flat) is False
        directional = _bar(11, 100.0, 103.0, 99.5, 102.0)
        assert check_confirmation(pattern, directional) is True

    def test_strict_confirmation_uses_pattern_bar_high(self) -> None:
        pattern = CandlePattern(
            ts=date(2024, 1, 10),
            symbol="TEST",
            market="crypto",
            freq="1d",
            pattern_name="hammer",
            direction="bullish",
            bar_i=9,
            prior_trend="down",
            strength=2,
            body_pct_signal=0.15,
            vol_ratio=1.2,
        )
        pattern_bar = _bar(10, 99.0, 100.5, 93.0, 100.0)
        next_bar_above = _bar(11, 100.0, 102.0, 99.5, 101.0)  # close=101 > pattern_bar.high=100.5
        next_bar_below = _bar(11, 100.0, 100.4, 99.5, 100.4)  # close=100.4 < high=100.5
        assert check_confirmation_strict(pattern, pattern_bar, next_bar_above) is True
        assert check_confirmation_strict(pattern, pattern_bar, next_bar_below) is False


# ===========================================================================
# 20. get_candle_entry_state
# ===========================================================================


class TestGetCandleEntryState:
    def _pattern(
        self, strength: int, name: str = "hammer", mitigated: bool = False
    ) -> CandlePattern:
        return CandlePattern(
            ts=date(2024, 1, 10),
            symbol="TEST",
            market="crypto",
            freq="1d",
            pattern_name=name,
            direction="bullish",
            bar_i=9,
            prior_trend="down",
            strength=strength,
            body_pct_signal=0.15,
            vol_ratio=1.2,
            mitigated=mitigated,
        )

    def test_strength_3_enter_now(self) -> None:
        assert get_candle_entry_state(self._pattern(3), confirmed=False) == EntryState.ENTER_NOW

    def test_strength_2_confirmed_scale_in(self) -> None:
        assert get_candle_entry_state(self._pattern(2), confirmed=True) == EntryState.SCALE_IN

    def test_strength_2_not_confirmed_wait(self) -> None:
        assert (
            get_candle_entry_state(self._pattern(2), confirmed=False)
            == EntryState.WAIT_FOR_PULLBACK
        )

    def test_strength_1_avoid(self) -> None:
        assert get_candle_entry_state(self._pattern(1), confirmed=True) == EntryState.AVOID

    def test_mitigated_always_avoid(self) -> None:
        assert (
            get_candle_entry_state(self._pattern(3, mitigated=True), confirmed=True)
            == EntryState.AVOID
        )

    def test_inverted_hammer_needs_confirmation(self) -> None:
        p = CandlePattern(
            ts=date(2024, 1, 10),
            symbol="TEST",
            market="crypto",
            freq="1d",
            pattern_name="inverted_hammer",
            direction="bullish",
            bar_i=9,
            prior_trend="down",
            strength=3,
            body_pct_signal=0.10,
            vol_ratio=1.2,
        )
        assert get_candle_entry_state(p, confirmed=False) == EntryState.AVOID
        assert get_candle_entry_state(p, confirmed=True) == EntryState.ENTER_NOW


# ===========================================================================
# 21. is_pattern_at_level_price
# ===========================================================================


class TestIsPatternAtLevelPrice:
    def test_price_within_tolerance(self) -> None:
        assert is_pattern_at_level_price(100.0, [100.3], atr=2.0, tol_pct=0.005) is True

    def test_price_outside_tolerance(self) -> None:
        # |100 - 102| = 2.0; tol = min(0.005*100, 0.5*2) = min(0.5, 1.0) = 0.5 → 2.0 > 0.5
        assert is_pattern_at_level_price(100.0, [102.0], atr=2.0, tol_pct=0.005) is False

    def test_empty_levels_returns_false(self) -> None:
        assert is_pattern_at_level_price(100.0, [], atr=2.0) is False

    def test_zero_atr_returns_false(self) -> None:
        assert is_pattern_at_level_price(100.0, [100.0], atr=0.0) is False


class TestIsPatternAtLevel:
    def _pattern(self, signal_close: float | None) -> CandlePattern:
        return CandlePattern(
            ts=date(2024, 1, 10),
            symbol="TEST",
            market="crypto",
            freq="1d",
            pattern_name="hammer",
            direction="bullish",
            bar_i=9,
            prior_trend="down",
            strength=2,
            body_pct_signal=0.15,
            vol_ratio=1.2,
            signal_close=signal_close,
        )

    def test_detected_pattern_is_at_level(self) -> None:
        # Functional (not a perpetual-False stub): a pattern whose signal close
        # sits on a level must report True.
        p = self._pattern(signal_close=100.0)
        assert is_pattern_at_level(p, [100.2], atr=2.0, tol_pct=0.005) is True

    def test_detected_pattern_off_level(self) -> None:
        p = self._pattern(signal_close=100.0)
        assert is_pattern_at_level(p, [105.0], atr=2.0, tol_pct=0.005) is False

    def test_pattern_without_signal_close_returns_false(self) -> None:
        p = self._pattern(signal_close=None)
        assert is_pattern_at_level(p, [100.0], atr=2.0) is False

    def test_real_detected_pattern_carries_signal_close(self) -> None:
        prefix = _downtrend_prefix(9, start=1)
        signal = _bar(10, 99.0, 100.5, 93.0, 100.0)
        bars = prefix + [signal]
        results = detect_candlestick_patterns(bars)
        assert results, "expected at least one detected pattern"
        p = results[0]
        assert p.signal_close == bars[p.bar_i].close
        # close is 100.0 → a level at 100.3 within 0.5% tolerance must match.
        assert is_pattern_at_level(p, [100.3], atr=4.0) is True


# ===========================================================================
# 22. Pattern metadata fields
# ===========================================================================


class TestPatternMetadata:
    def test_pattern_has_required_fields(self) -> None:
        prefix = _downtrend_prefix(9, start=1)
        signal = _bar(10, 99.0, 100.5, 93.0, 100.0)
        bars = prefix + [signal]
        results = detect_candlestick_patterns(bars)
        for p in results:
            assert isinstance(p.ts, (date,))
            assert isinstance(p.symbol, str)
            assert isinstance(p.market, str)
            assert isinstance(p.freq, str)
            assert isinstance(p.pattern_name, str)
            assert p.direction in ("bullish", "bearish", "neutral")
            assert 1 <= p.strength <= 3
            assert p.vol_ratio >= 0
            assert isinstance(p.mitigated, bool)

    def test_bar_i_within_bounds(self) -> None:
        prefix = _downtrend_prefix(9, start=1)
        signal = _bar(10, 99.0, 100.5, 93.0, 100.0)
        bars = prefix + [signal]
        results = detect_candlestick_patterns(bars)
        for p in results:
            assert 0 <= p.bar_i < len(bars)

    def test_symbol_propagated_correctly(self) -> None:
        # Use custom symbol
        def _bar_sym(ts: int, o: float, h: float, l: float, c: float) -> PriceBar:  # noqa: E741
            return PriceBar(
                symbol="BTC",
                market="crypto",
                source_symbol="BTC/USDT",
                ts=date(2024, 1, ts),
                open=o,
                high=h,
                low=l,
                close=c,
                volume=1000.0,
                freq="1h",
            )

        prefix = [
            _bar_sym(k + 1, 100.0 - k * 2, 100.5 - k * 2, 93.0 - k * 2, 98.0 - k * 2)
            for k in range(10)
        ]
        signal = _bar_sym(11, 80.0, 81.0, 74.0, 80.8)
        bars = prefix + [signal]
        results = detect_candlestick_patterns(bars)
        for p in results:
            assert p.symbol == "BTC"
            assert p.market == "crypto"
            assert p.freq == "1h"


# ===========================================================================
# 23. No lookahead — triple pattern requires i-2 to have full trend window
# ===========================================================================


class TestNoLookahead:
    def test_triple_pattern_prior_trend_uses_i_minus_2(self) -> None:
        # Build a scenario where prior_trend(bars, i-2) ≠ prior_trend(bars, i)
        # If there were lookahead, we'd detect incorrectly.
        # Here we build uptrend then abrupt reversal and verify morning_star not triggered
        # when there's insufficient downtrend history before c1.
        up = _uptrend_prefix(n=10, start=1)
        # Add three bars — c1 bear, c2 small star, c3 bull
        last_p = up[-1].close
        c1 = _bar(11, last_p, last_p + 0.5, last_p - 7.0, last_p - 6.5)
        c2 = _bar(12, last_p - 7.0, last_p - 6.7, last_p - 7.8, last_p - 7.2)
        c3 = _bar(13, last_p - 7.0, last_p - 1.0, last_p - 7.5, last_p - 2.0)
        bars = up + [c1, c2, c3]
        results = detect_candlestick_patterns(bars)
        # prior_trend at i-2 (c1) would be 'up' (because uptrend prefix), so morning_star
        # must NOT fire (requires 'down' trend before c1)
        ms = [p for p in results if p.pattern_name == "morning_star"]
        assert len(ms) == 0, "morning_star should not fire when prior trend is not 'down'"
