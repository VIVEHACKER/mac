from __future__ import annotations

from datetime import date, timedelta

from data.models import PriceBar
from engine.backtest import format_backtest_report, run_momentum_backtest


def test_momentum_backtest_generates_metrics() -> None:
    bars = _bars("MSFT", [10, 11, 12, 13, 12, 14, 15])

    result = run_momentum_backtest(bars, lookback=2, initial_cash=1_000)

    assert result.symbol == "MSFT"
    assert result.rows == 7
    assert result.final_equity > 1_000
    assert result.trades >= 1
    assert "Momentum Backtest - MSFT" in format_backtest_report(result)


def test_momentum_backtest_applies_cost_and_position_cap() -> None:
    bars = _bars("MSFT", [10, 11, 12, 13, 12, 14, 15])

    no_fee = run_momentum_backtest(bars, lookback=2, initial_cash=1_000, fee_bps=0)
    with_fee = run_momentum_backtest(
        bars,
        lookback=2,
        initial_cash=1_000,
        fee_bps=10,
        max_position=0.5,
    )

    assert with_fee.final_equity < no_fee.final_equity
    assert with_fee.total_cost > 0
    assert with_fee.exposure <= 0.5


def test_momentum_backtest_compares_external_benchmark() -> None:
    bars = _bars("MSFT", [10, 11, 12, 13, 14, 15, 16])
    benchmark = _bars("SPY", [10, 10, 10, 10, 10, 10, 10])

    result = run_momentum_backtest(
        bars,
        lookback=2,
        initial_cash=1_000,
        benchmark_bars=benchmark,
    )

    report = format_backtest_report(result)
    assert result.benchmark_symbol == "SPY"
    assert result.excess_return > 0
    assert "Benchmark | SPY (us)" in report
    assert "Excess Return vs Benchmark" in report


def _bars(symbol: str, closes: list[float]) -> list[PriceBar]:
    return [
        PriceBar(
            symbol,
            "us",
            symbol,
            date(2025, 1, 1) + timedelta(days=index),
            close,
            close,
            close,
            close,
            100,
        )
        for index, close in enumerate(closes)
    ]
