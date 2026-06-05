from __future__ import annotations

from datetime import date, timedelta

from data.models import PriceBar
from engine.chart.read import _range_position, read_chart
from engine.chart.types import EntryState, TrendBias

_BASE = date(2025, 1, 1)


def _bar(i: int, o: float, h: float, low: float, c: float) -> PriceBar:
    return PriceBar(
        symbol="BTC/USDT",
        market="crypto",
        source_symbol="BTC/USDT",
        ts=_BASE + timedelta(hours=4 * i),
        open=o,
        high=h,
        low=low,
        close=c,
        volume=1000.0,
        freq="4h",
    )


def _zigzag(anchors: list[tuple[str, float]], seg: int = 8) -> list[PriceBar]:
    """Build a BULLISH zig-zag from (kind, level) anchors with sharp pivot bars
    (same construction the aggregator's structure tests rely on)."""
    bars: list[PriceBar] = []
    i = 0
    prev = anchors[0][1]
    for k in range(7):
        lvl = prev - 8 + k
        bars.append(_bar(i, lvl, lvl + 1, lvl - 1, lvl))
        i += 1
    for idx, (kind, level) in enumerate(anchors):
        if kind == "H":
            bars.append(_bar(i, level - 3, level, level - 4, level - 1))
        else:
            bars.append(_bar(i, level + 3, level + 4, level, level + 1))
        i += 1
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


# Bullish, ending on a fresh high → premium close.
_PREMIUM = [
    ("L", 100),
    ("H", 120),
    ("L", 110),
    ("H", 135),
    ("L", 125),
    ("H", 150),
    ("L", 140),
    ("H", 165),
]
# Bullish, ending on a deep pullback (still a higher low) → discount close.
_DISCOUNT = [
    ("L", 100),
    ("H", 120),
    ("L", 110),
    ("H", 135),
    ("L", 125),
    ("H", 150),
    ("L", 141),
    ("H", 170),
    ("L", 143),
]


def test_range_position_high_at_top_low_at_bottom() -> None:
    rising = [_bar(i, 100 + i, 101 + i, 99 + i, 100.5 + i) for i in range(60)]
    assert _range_position(rising) > 0.9
    falling = [_bar(i, 160 - i, 161 - i, 159 - i, 159.5 - i) for i in range(60)]
    assert _range_position(falling) < 0.1


def test_mean_reversion_vetoes_premium_chase_long() -> None:
    bars = _zigzag(_PREMIUM)
    on = read_chart(bars, direction="long", mean_reversion=True)
    off = read_chart(bars, direction="long", mean_reversion=False)

    assert on.trend_bias is TrendBias.BULLISH  # not RANGING — isolates the location gate
    assert on.features["range_pos"] > 0.6  # premium
    assert on.vetoed is True
    assert "프리미엄" in str(on.features["veto_reason"])
    assert on.decision is EntryState.AVOID
    # with the gate off the premium veto is absent (behaviour unchanged)
    assert "프리미엄" not in str(off.features["veto_reason"])


def test_mean_reversion_allows_discount_long() -> None:
    bars = _zigzag(_DISCOUNT)
    on = read_chart(bars, direction="long", mean_reversion=True)
    off = read_chart(bars, direction="long", mean_reversion=False)

    assert on.trend_bias is TrendBias.BULLISH
    assert on.features["range_pos"] < 0.6  # discount — gate must stay inert
    assert "프리미엄" not in str(on.features["veto_reason"])
    assert on.decision is off.decision  # the gate changes nothing in discount


def test_mean_reversion_defaults_off_no_regression() -> None:
    bars = _zigzag(_PREMIUM)
    default = read_chart(bars, direction="long")
    explicit_off = read_chart(bars, direction="long", mean_reversion=False)
    assert default.decision is explicit_off.decision
    assert default.confluence == explicit_off.confluence
    assert "프리미엄" not in str(default.features["veto_reason"])
