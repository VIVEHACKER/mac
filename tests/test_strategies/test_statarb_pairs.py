from __future__ import annotations

from datetime import date, timedelta
from math import exp, sin

from data.models import PriceBar
from strategies.statarb_pairs import (
    analyze_pair,
    backtest_pair_mean_reversion,
    pairs_zscore_signal,
)


def test_analyze_pair_fits_hedge_ratio_and_emits_entry_signal() -> None:
    first, second = _synthetic_pair()

    analysis = analyze_pair(first, second, lookback=120, entry_z=2.0, min_observations=60)

    assert analysis is not None
    assert analysis.observations == 120
    assert 1.0 < analysis.hedge_ratio < 1.4
    assert analysis.z_score > 2.0
    assert analysis.signal is not None
    assert analysis.signal.long_symbol == "BBB"
    assert analysis.signal.short_symbol == "AAA"


def test_pairs_zscore_signal_keeps_compatibility_wrapper() -> None:
    first, second = _synthetic_pair()

    signal = pairs_zscore_signal(first, second, entry_z=2.0)

    assert signal is not None
    assert signal.long_symbol == "BBB"
    assert signal.short_symbol == "AAA"


def test_analyze_pair_returns_none_for_short_overlap() -> None:
    first, second = _synthetic_pair(length=10)

    assert analyze_pair(first, second, min_observations=30) is None


def test_pair_backtest_charges_execution_costs() -> None:
    first, second = _synthetic_pair(length=220, final_spike=False)

    result = backtest_pair_mean_reversion(
        first,
        second,
        lookback=50,
        entry_z=1.0,
        exit_z=0.2,
        min_observations=80,
        fee_bps=2.0,
        slippage_bps=3.0,
        min_trades=1,
        max_drawdown_limit=1.0,
    )

    assert result is not None
    assert result.trades > 0
    assert result.net_return < result.gross_return
    assert result.observations == 169


def _synthetic_pair(
    length: int = 120,
    *,
    final_spike: bool = True,
) -> tuple[list[PriceBar], list[PriceBar]]:
    start = date(2026, 1, 1)
    first: list[PriceBar] = []
    second: list[PriceBar] = []
    for index in range(length):
        second_log = 4.5 + index * 0.001
        residual = 0.004 * sin(index / 5)
        if final_spike and index == length - 1:
            residual = 0.08
        first_log = 0.2 + 1.15 * second_log + residual
        ts = start + timedelta(days=index)
        first.append(_bar("AAA", ts, exp(first_log)))
        second.append(_bar("BBB", ts, exp(second_log)))
    return first, second


def _bar(symbol: str, ts: date, close: float) -> PriceBar:
    return PriceBar(
        symbol=symbol,
        market="us",
        source_symbol=symbol,
        ts=ts,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=100,
    )
