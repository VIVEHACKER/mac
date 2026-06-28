from __future__ import annotations

from datetime import date, timedelta

import pytest

from data.models import PriceBar
from engine.validation import (
    FactorValidationThresholds,
    StressWindow,
    format_factor_validation_suite,
    run_factor_validation_suite,
)


def test_factor_validation_suite_runs_multiple_validation_layers() -> None:
    bars = {
        "AAA": _long_bars("AAA", 10.0, 0.0010),
        "BBB": _long_bars("BBB", 10.0, 0.0002),
        "TLT": _long_bars("TLT", 10.0, 0.0001),
    }
    benchmark = _long_bars("SPY", 10.0, 0.0003)

    suite = run_factor_validation_suite(
        bars,
        benchmark_bars=benchmark,
        start=date(2020, 1, 1),
        end=date(2023, 12, 31),
        momentum_lookback=21,
        reversal_lookback=5,
        volatility_lookback=10,
        risk_filter_lookback=20,
        top_n=1,
        rebalance_days=21,
        fee_bps=2,
        defensive_symbol="TLT",
        weighting="equal",
        max_risk_weight=1.0,
        drawdown_guard=0.0,
        defensive_only=True,
        train_years=1,
        validation_years=0,
        test_years=1,
        step_years=1,
        momentum_lookbacks=(21,),
        reversal_lookbacks=(5,),
        volatility_lookbacks=(10,),
        top_ns=(1,),
        risk_filter_lookbacks=(0, 20),
        weighting_modes=("equal",),
        rebalance_days_values=(21,),
        defensive_symbols=("TLT",),
        max_risk_weights=(1.0,),
        drawdown_guards=(0.0,),
        selection_metric="return-drawdown",
        fee_stress_bps=(2, 5),
        stress_windows=(StressWindow("test-stress", date(2022, 1, 1), date(2022, 6, 30)),),
        thresholds=FactorValidationThresholds(
            min_walk_forward_windows=1,
            min_positive_test_rate=0.5,
            min_parameter_positive_rate=0.5,
            min_stress_windows=1,
            min_stress_return=-1.0,
        ),
    )

    text = format_factor_validation_suite(suite)
    assert suite.walk_forward.rows
    assert suite.fee_stress
    assert suite.parameter_variants
    assert suite.tested_stress_windows == 1
    # Relative crisis evidence (for the SPY-relative live gate): per-window excess
    # = strategy total return - benchmark total return; worst/mean derive from it.
    excesses = suite.stress_window_excesses
    assert len(excesses) == 1
    window = suite.stress_windows[0].result
    assert window is not None
    assert excesses[0] == pytest.approx(window.total_return - window.benchmark_return)
    assert suite.worst_stress_excess == pytest.approx(min(excesses))
    assert suite.mean_stress_excess == pytest.approx(sum(excesses) / len(excesses))
    assert "Factor Validation Suite" in text
    assert "Fee Stress" in text
    assert "Parameter Perturbation" in text
    assert "Stress Windows" in text
    assert "Worst Stress Return" in text


def test_factor_validation_blocks_when_stress_return_is_too_low() -> None:
    bars = {
        "AAA": _long_bars("AAA", 10.0, 0.0010),
        "TLT": _long_bars("TLT", 10.0, 0.0001),
    }
    benchmark = _long_bars("SPY", 10.0, 0.0003)

    suite = run_factor_validation_suite(
        bars,
        benchmark_bars=benchmark,
        start=date(2020, 1, 1),
        end=date(2023, 12, 31),
        momentum_lookback=21,
        reversal_lookback=5,
        volatility_lookback=10,
        risk_filter_lookback=20,
        top_n=1,
        rebalance_days=21,
        fee_bps=2,
        defensive_symbol="TLT",
        weighting="equal",
        max_risk_weight=1.0,
        drawdown_guard=0.0,
        defensive_only=True,
        train_years=1,
        validation_years=0,
        test_years=1,
        step_years=1,
        momentum_lookbacks=(21,),
        reversal_lookbacks=(5,),
        volatility_lookbacks=(10,),
        top_ns=(1,),
        risk_filter_lookbacks=(0, 20),
        weighting_modes=("equal",),
        rebalance_days_values=(21,),
        defensive_symbols=("TLT",),
        max_risk_weights=(1.0,),
        drawdown_guards=(0.0,),
        selection_metric="annualized-return",
        fee_stress_bps=(2,),
        stress_windows=(StressWindow("high-bar", date(2022, 1, 1), date(2022, 6, 30)),),
        thresholds=FactorValidationThresholds(
            min_walk_forward_windows=1,
            min_positive_test_rate=0.0,
            min_parameter_positive_rate=0.0,
            min_stress_windows=1,
            min_stress_return=10.0,
        ),
    )

    text = format_factor_validation_suite(suite)
    assert not suite.stress_passed
    assert "worst stress return" in text


def _long_bars(symbol: str, start_close: float, daily_return: float) -> list[PriceBar]:
    close = start_close
    bars: list[PriceBar] = []
    for index in range(1_500):
        close *= 1 + daily_return
        ts = date(2020, 1, 1) + timedelta(days=index)
        bars.append(PriceBar(symbol, "us", symbol, ts, close, close, close, close, 100))
    return bars
