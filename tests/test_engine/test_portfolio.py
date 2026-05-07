from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from data.models import DelistingReturn, FundamentalRecord, PriceBar, UniverseMember
from engine.factor_portfolio import (
    FactorWeights,
    format_factor_portfolio_report,
    run_factor_rotation_backtest,
)
from engine.portfolio import (
    format_portfolio_report,
    format_screen_report,
    run_momentum_rotation_backtest,
    screen_momentum,
)
from engine.robustness import format_robustness_report, run_momentum_robustness_grid
from engine.walkforward import format_walk_forward_report, run_factor_walk_forward


def test_screen_momentum_ranks_symbols() -> None:
    bars = {
        "AAA": _bars("AAA", [10, 11, 12, 13]),
        "BBB": _bars("BBB", [10, 10, 10, 10.5]),
    }

    rows = screen_momentum(bars, lookback=2)

    assert [row.symbol for row in rows] == ["AAA", "BBB"]
    assert "Momentum Screen" in format_screen_report(rows)


def test_rotation_portfolio_backtest_runs() -> None:
    bars = {
        "AAA": _bars("AAA", [10, 11, 12, 13, 14, 15, 16]),
        "BBB": _bars("BBB", [10, 10, 10.5, 10.6, 10.7, 10.8, 11.0]),
        "CCC": _bars("CCC", [10, 9, 8, 8.5, 9, 9.5, 10]),
    }

    result = run_momentum_rotation_backtest(
        bars,
        lookback=2,
        top_n=2,
        initial_cash=1_000,
        rebalance_days=2,
        fee_bps=2,
    )

    assert result.final_equity > 1_000
    assert result.rebalance_count >= 1
    assert result.total_cost > 0
    report = format_portfolio_report(result)
    assert result.benchmark_sharpe >= 0
    assert result.annual_returns
    assert "Momentum Rotation Portfolio Backtest" in report
    assert "Annual Returns" in report


def test_rotation_portfolio_backtest_compares_external_benchmark() -> None:
    bars = {
        "AAA": _bars("AAA", [10, 11, 12, 13, 14, 15, 16]),
        "BBB": _bars("BBB", [10, 10, 10.5, 10.6, 10.7, 10.8, 11.0]),
    }
    benchmark = _bars("SPY", [10, 10, 10, 10, 10, 10, 10])

    result = run_momentum_rotation_backtest(
        bars,
        lookback=2,
        top_n=1,
        initial_cash=1_000,
        rebalance_days=2,
        benchmark_bars=benchmark,
    )

    report = format_portfolio_report(result)
    assert result.benchmark_symbol == "SPY"
    assert result.excess_return > 0
    assert "Benchmark | SPY (us)" in report
    assert "Excess Return vs Benchmark" in report


def test_rotation_portfolio_enforces_point_in_time_membership() -> None:
    bars = {
        "AAA": _bars("AAA", [10, 11, 12, 13, 14, 15, 16]),
        "BBB": _bars("BBB", [10, 20, 40, 80, 160, 320, 640]),
    }
    members = [
        UniverseMember("TEST", "AAA", "us", date(2025, 1, 1)),
        UniverseMember("TEST", "BBB", "us", date(2025, 1, 6)),
    ]

    result = run_momentum_rotation_backtest(
        bars,
        lookback=2,
        top_n=1,
        initial_cash=1_000,
        rebalance_days=1,
        universe_members=members,
    )

    assert result.universe_mode == "point-in-time"
    assert result.universe_name == "TEST"
    assert result.average_eligible_symbols < 2
    assert "Point-in-time universe membership was enforced" in format_portfolio_report(result)


def test_momentum_robustness_grid_reports_split_sample_results() -> None:
    bars = {
        "AAA": _bars("AAA", [10, 11, 12, 13, 14, 15, 16, 17, 18, 19]),
        "BBB": _bars("BBB", [10, 10, 10.5, 10.6, 10.7, 10.8, 11.0, 11.2, 11.3, 11.5]),
    }
    benchmark = _bars("SPY", [10, 10, 10, 10, 10, 10, 10, 10, 10, 10])

    report = run_momentum_robustness_grid(
        bars,
        benchmark_bars=benchmark,
        split_date=date(2025, 1, 5),
        lookbacks=(2,),
        top_ns=(1, 2),
        rebalance_days_values=(2,),
    )

    text = format_robustness_report(report)
    assert len(report.rows) == 2
    assert report.test_positive_rate == 1.0
    assert "Momentum Robustness Report" in text
    assert "Parameter Grid" in text


def test_factor_rotation_uses_risk_filter_and_inverse_vol_weights() -> None:
    bars = {
        "AAA": _bars("AAA", [10, 11, 12, 13, 14, 15, 16, 17, 18, 19]),
        "BBB": _bars("BBB", [10, 10, 10.5, 10.6, 10.7, 10.8, 11.0, 11.2, 11.3, 11.5]),
        "TLT": _bars("TLT", [10, 10, 10, 10, 10.1, 10.1, 10.2, 10.2, 10.3, 10.3]),
    }
    benchmark = _bars("SPY", [10, 10, 10, 10, 10, 9, 8, 8, 8, 8])

    result = run_factor_rotation_backtest(
        bars,
        benchmark_bars=benchmark,
        momentum_lookback=2,
        reversal_lookback=1,
        volatility_lookback=2,
        risk_filter_lookback=2,
        top_n=2,
        rebalance_days=1,
    )

    text = format_factor_portfolio_report(result)
    assert result.final_equity > 0
    assert result.risk_on_ratio < 1
    assert "Multi-Factor Rotation Portfolio Backtest" in text
    assert "Risk-On Ratio" in text


def test_factor_rotation_exits_immediately_when_risk_filter_turns_off() -> None:
    bars = {
        "AAA": _bars("AAA", [10, 11, 12, 13, 13, 1, 1, 1]),
        "TLT": _bars("TLT", [10, 10, 10, 10, 10, 10, 10, 10]),
    }
    benchmark = _bars("SPY", [10, 10, 10, 5, 5, 5, 5, 5])

    result = run_factor_rotation_backtest(
        bars,
        benchmark_bars=benchmark,
        momentum_lookback=1,
        reversal_lookback=1,
        volatility_lookback=1,
        risk_filter_lookback=2,
        top_n=1,
        rebalance_days=10,
        weighting="equal",
        defensive_symbol="TLT",
        factor_weights=FactorWeights(momentum=1, reversal=0, low_volatility=0, value=0, quality=0),
    )

    risk_off_points = [point for point in result.equity_curve if not point.risk_on]
    assert risk_off_points
    assert dict(risk_off_points[0].weights) == {"TLT": 1.0}
    assert result.max_drawdown < 0.2


def test_factor_rotation_caps_risky_weight_into_defensive_asset() -> None:
    bars = {
        "AAA": _bars("AAA", [10, 11, 12, 13, 14, 15, 16, 17]),
        "TLT": _bars("TLT", [10, 10, 10, 10, 10, 10, 10, 10]),
    }

    result = run_factor_rotation_backtest(
        bars,
        momentum_lookback=2,
        reversal_lookback=1,
        volatility_lookback=2,
        risk_filter_lookback=0,
        top_n=1,
        rebalance_days=1,
        weighting="equal",
        defensive_symbol="TLT",
        max_risk_weight=0.6,
        factor_weights=FactorWeights(momentum=1, reversal=0, low_volatility=0, value=0, quality=0),
    )

    first_invested = next(point for point in result.equity_curve if point.weights)
    assert dict(first_invested.weights) == {"AAA": 0.6, "TLT": 0.4}
    assert "Max Risk Weight | 60.0%" in format_factor_portfolio_report(result)


def test_factor_rotation_can_exclude_defensive_asset_from_ranking() -> None:
    bars = {
        "AAA": _bars("AAA", [10, 11, 12, 13, 14, 15, 16, 17]),
        "TLT": _bars("TLT", [10, 20, 40, 80, 160, 320, 640, 1280]),
    }

    result = run_factor_rotation_backtest(
        bars,
        momentum_lookback=2,
        reversal_lookback=1,
        volatility_lookback=2,
        risk_filter_lookback=0,
        top_n=1,
        rebalance_days=1,
        weighting="equal",
        defensive_symbol="TLT",
        defensive_only=True,
        factor_weights=FactorWeights(momentum=1, reversal=0, low_volatility=0, value=0, quality=0),
    )

    first_invested = next(point for point in result.equity_curve if point.weights)
    assert dict(first_invested.weights) == {"AAA": 1.0}
    assert "Defensive Asset Ranking | excluded" in format_factor_portfolio_report(result)


def test_factor_rotation_uses_fundamentals_point_in_time() -> None:
    bars = {
        "AAA": _bars("AAA", [10, 10.1, 10.2, 10.3, 10.4, 10.5]),
        "BBB": _bars("BBB", [10, 10.1, 10.2, 10.3, 10.4, 10.5]),
    }
    fundamentals = {
        "AAA": [
            FundamentalRecord(
                "AAA",
                "us",
                date(2025, 3, 31),
                datetime(2025, 2, 1),
                net_income=1_000,
                free_cash_flow=1_000,
                shares_out=10,
            )
        ],
        "BBB": [
            FundamentalRecord(
                "BBB",
                "us",
                date(2024, 12, 31),
                datetime(2025, 1, 1),
                net_income=100,
                free_cash_flow=100,
                shares_out=10,
            )
        ],
    }

    result = run_factor_rotation_backtest(
        bars,
        fundamentals_by_symbol=fundamentals,
        momentum_lookback=2,
        reversal_lookback=1,
        volatility_lookback=2,
        risk_filter_lookback=0,
        top_n=1,
        rebalance_days=1,
        factor_weights=FactorWeights(
            momentum=0,
            reversal=0,
            low_volatility=0,
            value=1,
            quality=0,
        ),
        weighting="equal",
    )

    held_symbols = {symbol for point in result.equity_curve for symbol in point.holdings}
    assert held_symbols == {"BBB"}
    assert result.fundamental_record_count == 2


def test_rotation_portfolio_applies_delisting_return_for_missing_next_price() -> None:
    bars = {
        "AAA": _bars("AAA", [10, 11, 12, 13], start=date(2025, 1, 1)),
        "BBB": _bars("BBB", [10, 10, 10, 10, 10, 10], start=date(2025, 1, 1)),
    }
    benchmark = _bars("SPY", [10, 10, 10, 10, 10, 10], start=date(2025, 1, 1))
    members = [
        UniverseMember("TEST", "AAA", "us", date(2025, 1, 1), date(2025, 1, 5)),
        UniverseMember("TEST", "BBB", "us", date(2025, 1, 1)),
    ]

    result = run_momentum_rotation_backtest(
        bars,
        lookback=1,
        top_n=1,
        initial_cash=1_000,
        rebalance_days=5,
        benchmark_bars=benchmark,
        universe_members=members,
        delisting_returns=[
            DelistingReturn("AAA", "us", date(2025, 1, 5), return_pct=-0.5, source="test")
        ],
    )

    assert result.delisting_returns_applied == 1
    assert result.final_equity < 1_000
    assert "Delisting Returns Applied" in format_portfolio_report(result)


def test_rotation_portfolio_blocks_missing_delisting_return() -> None:
    bars = {
        "AAA": _bars("AAA", [10, 11, 12, 13], start=date(2025, 1, 1)),
        "BBB": _bars("BBB", [10, 10, 10, 10, 10, 10], start=date(2025, 1, 1)),
    }
    benchmark = _bars("SPY", [10, 10, 10, 10, 10, 10], start=date(2025, 1, 1))
    members = [
        UniverseMember("TEST", "AAA", "us", date(2025, 1, 1), date(2025, 1, 5)),
        UniverseMember("TEST", "BBB", "us", date(2025, 1, 1)),
    ]

    with pytest.raises(ValueError, match="delisting"):
        run_momentum_rotation_backtest(
            bars,
            lookback=1,
            top_n=1,
            initial_cash=1_000,
            rebalance_days=5,
            benchmark_bars=benchmark,
            universe_members=members,
        )


def test_factor_walk_forward_selects_train_params_and_tests_them() -> None:
    bars = {
        "AAA": _long_bars("AAA", 10.0, 0.0010),
        "BBB": _long_bars("BBB", 10.0, 0.0002),
        "TLT": _long_bars("TLT", 10.0, 0.0001),
    }
    benchmark = _long_bars("SPY", 10.0, 0.0003)

    report = run_factor_walk_forward(
        bars,
        benchmark_bars=benchmark,
        start=date(2020, 1, 1),
        end=date(2023, 12, 31),
        train_years=1,
        test_years=2,
        step_years=1,
        momentum_lookbacks=(21,),
        reversal_lookbacks=(5,),
        volatility_lookbacks=(10,),
        top_ns=(1, 2),
        risk_filter_lookback=20,
        risk_filter_lookbacks=(0, 20),
        weighting_modes=("inverse-vol", "equal"),
        selection_metric="return-drawdown",
    )

    text = format_walk_forward_report(report)
    assert report.rows
    assert report.positive_test_rate >= 0
    assert "Factor Walk-Forward Report" in text
    assert "Selection Metric | return-drawdown" in text


def _bars(
    symbol: str,
    closes: list[float],
    *,
    start: date = date(2025, 1, 1),
) -> list[PriceBar]:
    return [
        PriceBar(
            symbol,
            "us",
            symbol,
            start + timedelta(days=index),
            close,
            close,
            close,
            close,
            100,
        )
        for index, close in enumerate(closes)
    ]


def _long_bars(symbol: str, start_close: float, daily_return: float) -> list[PriceBar]:
    closes: list[float] = []
    close = start_close
    for _ in range(1_500):
        close *= 1 + daily_return
        closes.append(close)
    return _bars(symbol, closes, start=date(2020, 1, 1))
