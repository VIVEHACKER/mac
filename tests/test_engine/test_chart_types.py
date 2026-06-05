from __future__ import annotations

from datetime import date

from data.models import PriceBar
from engine.chart.types import (
    EntryState,
    bar_body,
    bar_range,
    confluence_score,
    decide_entry_state,
    is_bullish,
    lower_wick,
    upper_wick,
)


def _bar(o: float, h: float, low: float, c: float, v: float = 1.0) -> PriceBar:
    return PriceBar(
        symbol="BTC/USDT",
        market="crypto",
        source_symbol="BTC/USDT",
        ts=date(2026, 5, 7),
        open=o,
        high=h,
        low=low,
        close=c,
        volume=v,
        freq="4h",
    )


def test_confluence_score_all_aligned_is_100() -> None:
    assert confluence_score({"a": (0.3, 1), "b": (0.7, 1)}) == 100.0


def test_confluence_score_mixed_directions() -> None:
    # raw = 0.3 - 0.2 + 0 = 0.1 ; max_raw = 1.0 -> 10.0
    score = confluence_score({"struct": (0.3, 1), "oi": (0.2, -1), "vp": (0.5, 0)})
    assert round(score, 4) == 10.0


def test_confluence_score_clamps_at_zero() -> None:
    assert confluence_score({"a": (0.3, -1)}) == 0.0


def test_confluence_score_empty_is_zero() -> None:
    assert confluence_score({}) == 0.0


def test_decide_entry_state_thresholds() -> None:
    assert decide_entry_state(75.0, veto=False) is EntryState.ENTER_NOW
    assert decide_entry_state(70.0, veto=False) is EntryState.ENTER_NOW
    assert decide_entry_state(60.0, veto=False) is EntryState.SCALE_IN
    assert decide_entry_state(40.0, veto=False) is EntryState.WAIT_FOR_PULLBACK
    assert decide_entry_state(20.0, veto=False) is EntryState.AVOID


def test_decide_entry_state_veto_forces_avoid() -> None:
    assert decide_entry_state(95.0, veto=True) is EntryState.AVOID


def test_bar_geometry_helpers() -> None:
    bar = _bar(100.0, 110.0, 95.0, 105.0)
    assert bar_body(bar) == 5.0
    assert bar_range(bar) == 15.0
    assert upper_wick(bar) == 5.0  # high - max(open, close)
    assert lower_wick(bar) == 5.0  # min(open, close) - low
    assert is_bullish(bar) is True
    assert is_bullish(_bar(105.0, 110.0, 95.0, 100.0)) is False
