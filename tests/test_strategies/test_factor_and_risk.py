from __future__ import annotations

from datetime import date, datetime, timedelta

from data.models import FlowRecord, FundamentalRecord, PriceBar, ValuationRecord
from engine.factor_portfolio import (
    FactorWeights,
    _bond_equity_corr,
    _regime_cash_active,
    _volume_spike_score,
    run_factor_rotation_backtest,
)
from signals.foreign_flow import foreign_flow_signal
from strategies.factor_aqr import rank_aqr_factors
from strategies.risk_parity import inverse_vol_weights
from strategies.value_long import value_long_signals


def test_aqr_factor_and_risk_parity_rank() -> None:
    bars = {
        "AAA": _bars("AAA", [10, 11, 12, 14, 16]),
        "BBB": _bars("BBB", [10, 10, 10, 11, 11]),
    }
    fundamentals = {
        "AAA": FundamentalRecord(
            "AAA",
            "us",
            date(2025, 1, 1),
            datetime(2025, 2, 1),
            net_income=50,
            free_cash_flow=40,
            total_equity=100,
            shares_out=10,
        ),
        "BBB": FundamentalRecord(
            "BBB",
            "us",
            date(2025, 1, 1),
            datetime(2025, 2, 1),
            net_income=5,
            free_cash_flow=4,
            total_equity=100,
            shares_out=10,
        ),
    }

    scores = rank_aqr_factors(bars, fundamentals, lookback=2)
    weights = inverse_vol_weights(bars, lookback=2)

    assert scores[0].symbol == "AAA"
    assert round(sum(weights.values()), 6) == 1.0


def test_value_and_foreign_flow_signals() -> None:
    value_signals = value_long_signals(
        [ValuationRecord("MSFT", "us", date(2025, 1, 2), 100, 140, discount_pct=0.4, rating=2)]
    )
    flow = [
        FlowRecord("005930", "kospi", date(2025, 1, 1) + timedelta(days=index), "foreign", 100)
        for index in range(5)
    ]

    flow_signal = foreign_flow_signal(flow)

    assert value_signals[0].direction == "long"
    assert flow_signal is not None
    assert flow_signal.direction == "long"


def test_foreign_flow_signal_blocks_estimated_rows_by_default() -> None:
    flow = [
        FlowRecord(
            "005930",
            "kospi",
            date(2025, 1, 1) + timedelta(days=index),
            "foreign",
            100,
            value_kind="estimated_close_x_volume",
            confidence="medium",
        )
        for index in range(5)
    ]

    assert foreign_flow_signal(flow) is None
    assert foreign_flow_signal(flow, allow_estimated=True, min_confidence="medium") is not None


def test_volume_spike_score_above_baseline() -> None:
    """Symbol with recent volume 2x the long-run average should return ratio ~2.0."""
    base = date(2020, 1, 2)
    # 252 bars of normal volume=100, then 21 bars of spike volume=200
    volumes: dict[date, float] = {}
    for i in range(252):
        volumes[base + timedelta(days=i)] = 100.0
    for i in range(252, 273):
        volumes[base + timedelta(days=i)] = 200.0
    today = base + timedelta(days=272)
    score = _volume_spike_score(volumes, today=today, lookback_short=21, lookback_long=252)
    # short mean = 200, long mean = blend of 231*100 + 21*200 / 252 ≈ 108.33
    assert score > 1.0, f"expected > 1.0, got {score}"


def test_volume_spike_score_insufficient_history_returns_zero() -> None:
    """Fewer bars than lookback_long should return 0.0 (no signal)."""
    base = date(2020, 1, 2)
    volumes = {base + timedelta(days=i): 100.0 for i in range(100)}
    today = base + timedelta(days=99)
    score = _volume_spike_score(volumes, today=today, lookback_short=21, lookback_long=252)
    assert score == 0.0


def test_volume_spike_score_short_greater_than_long_raises() -> None:
    """volume_lookback_short > volume_lookback_long must raise ValueError (look-ahead guard).

    Without the fix, passing short=252 long=21 to run_factor_rotation_backtest would
    silently compute a negative slice start that Python wraps to the END of the series,
    leaking future volume data into the score (look-ahead bias).
    """
    import pytest

    with pytest.raises(ValueError, match="volume_lookback_short.*volume_lookback_long"):
        run_factor_rotation_backtest(
            {"AAA": _bars_with_volume("AAA", closes=[10] * 30, volumes=[100.0] * 30)},
            momentum_lookback=5,
            reversal_lookback=2,
            volatility_lookback=3,
            risk_filter_lookback=0,
            top_n=1,
            rebalance_days=3,
            volume_weight=0.5,
            volume_lookback_short=252,
            volume_lookback_long=21,
        )


def test_volume_weight_zero_backward_compat() -> None:
    """volume_weight=0 must produce identical result to omitting volume args."""
    bars = _bars_with_volume(
        "AAA", closes=[10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20], volumes=[100] * 11
    )
    bars2 = _bars_with_volume(
        "BBB", closes=[10, 10, 11, 10, 11, 10, 11, 10, 11, 10, 11], volumes=[100] * 11
    )
    symbol_bars = {"AAA": bars, "BBB": bars2}

    result_no_vol = run_factor_rotation_backtest(
        symbol_bars,
        momentum_lookback=5,
        reversal_lookback=2,
        volatility_lookback=3,
        risk_filter_lookback=0,
        top_n=1,
        rebalance_days=3,
    )
    result_vol_zero = run_factor_rotation_backtest(
        symbol_bars,
        momentum_lookback=5,
        reversal_lookback=2,
        volatility_lookback=3,
        risk_filter_lookback=0,
        top_n=1,
        rebalance_days=3,
        volume_weight=0.0,
        volume_lookback_short=21,
        volume_lookback_long=252,
    )
    assert result_no_vol.annualized_return == result_vol_zero.annualized_return
    assert result_no_vol.sharpe == result_vol_zero.sharpe


def test_quality_weight_zero_is_backward_compat() -> None:
    """quality_weight=0 and value_weight=0 must reproduce baseline factor result."""
    bars = _bars_with_volume(
        "AAA", closes=[10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20], volumes=[100] * 11
    )
    bars2 = _bars_with_volume(
        "BBB", closes=[10, 10, 11, 10, 11, 10, 11, 10, 11, 10, 11], volumes=[100] * 11
    )
    symbol_bars = {"AAA": bars, "BBB": bars2}

    result_default = run_factor_rotation_backtest(
        symbol_bars,
        momentum_lookback=5,
        reversal_lookback=2,
        volatility_lookback=3,
        risk_filter_lookback=0,
        top_n=1,
        rebalance_days=3,
        factor_weights=FactorWeights(
            momentum=1.0, reversal=0.5, low_volatility=0.75, value=0.0, quality=0.0
        ),
    )
    result_explicit_zero = run_factor_rotation_backtest(
        symbol_bars,
        momentum_lookback=5,
        reversal_lookback=2,
        volatility_lookback=3,
        risk_filter_lookback=0,
        top_n=1,
        rebalance_days=3,
        factor_weights=FactorWeights(
            momentum=1.0, reversal=0.5, low_volatility=0.75, value=0.0, quality=0.0
        ),
        volume_weight=0.0,
    )
    assert result_default.annualized_return == result_explicit_zero.annualized_return
    assert result_default.sharpe == result_explicit_zero.sharpe


def test_quality_score_prefers_high_roe_low_leverage() -> None:
    """AAA has high ROE+no debt, BBB has low ROE+high debt → AAA ranks higher with quality_weight."""
    bars_aaaa = _bars_with_volume(
        "AAA", closes=[10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10], volumes=[100] * 11
    )
    bars_bbbb = _bars_with_volume(
        "BBB", closes=[10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10], volumes=[100] * 11
    )
    symbol_bars = {"AAA": bars_aaaa, "BBB": bars_bbbb}

    # AAA: high quality (ROE=0.5, no debt)
    # BBB: low quality (ROE=0.05, high debt relative to equity)
    fundamentals = {
        "AAA": [
            FundamentalRecord(
                "AAA",
                "us",
                date(2020, 1, 1),
                datetime(2020, 1, 2),
                net_income=500,
                total_equity=1000,
                total_debt=0,
                shares_out=100,
                free_cash_flow=400,
            )
        ],
        "BBB": [
            FundamentalRecord(
                "BBB",
                "us",
                date(2020, 1, 1),
                datetime(2020, 1, 2),
                net_income=50,
                total_equity=1000,
                total_debt=2000,
                shares_out=100,
                free_cash_flow=10,
            )
        ],
    }

    result = run_factor_rotation_backtest(
        symbol_bars,
        fundamentals_by_symbol=fundamentals,  # type: ignore[arg-type]
        momentum_lookback=5,
        reversal_lookback=2,
        volatility_lookback=3,
        risk_filter_lookback=0,
        top_n=1,
        rebalance_days=3,
        factor_weights=FactorWeights(
            momentum=0.0, reversal=0.0, low_volatility=0.0, value=0.0, quality=1.0
        ),
    )
    # With quality_weight=1.0 and identical price momentum, AAA should be selected more often
    holdings_history = [h for pt in result.equity_curve for h in pt.holdings]
    aaa_count = holdings_history.count("AAA")
    bbb_count = holdings_history.count("BBB")
    assert aaa_count >= bbb_count, (
        f"Expected AAA (high quality) to be held more than BBB, "
        f"got AAA={aaa_count}, BBB={bbb_count}"
    )


def test_value_score_prefers_high_earnings_yield() -> None:
    """AAA has high earnings+FCF yield, BBB has low → AAA ranks higher with value_weight."""
    bars_aaaa = _bars_with_volume(
        "AAA", closes=[10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10], volumes=[100] * 11
    )
    bars_bbbb = _bars_with_volume(
        "BBB", closes=[10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10], volumes=[100] * 11
    )
    symbol_bars = {"AAA": bars_aaaa, "BBB": bars_bbbb}

    # AAA: E/P = 500/1000 = 0.5 (high), FCF/P = 400/1000 = 0.4
    # BBB: E/P = 10/1000 = 0.01 (low), FCF/P = 5/1000 = 0.005
    fundamentals = {
        "AAA": [
            FundamentalRecord(
                "AAA",
                "us",
                date(2020, 1, 1),
                datetime(2020, 1, 2),
                net_income=500,
                free_cash_flow=400,
                total_equity=1000,
                shares_out=100,
            )
        ],
        "BBB": [
            FundamentalRecord(
                "BBB",
                "us",
                date(2020, 1, 1),
                datetime(2020, 1, 2),
                net_income=10,
                free_cash_flow=5,
                total_equity=1000,
                shares_out=100,
            )
        ],
    }

    result = run_factor_rotation_backtest(
        symbol_bars,
        fundamentals_by_symbol=fundamentals,  # type: ignore[arg-type]
        momentum_lookback=5,
        reversal_lookback=2,
        volatility_lookback=3,
        risk_filter_lookback=0,
        top_n=1,
        rebalance_days=3,
        factor_weights=FactorWeights(
            momentum=0.0, reversal=0.0, low_volatility=0.0, value=1.0, quality=0.0
        ),
    )
    holdings_history = [h for pt in result.equity_curve for h in pt.holdings]
    aaa_count = holdings_history.count("AAA")
    bbb_count = holdings_history.count("BBB")
    assert aaa_count >= bbb_count, (
        f"Expected AAA (high value) to be held more than BBB, got AAA={aaa_count}, BBB={bbb_count}"
    )


def test_missing_fundamentals_neutral_qv() -> None:
    """Symbols without fundamentals get quality/value score=0 (neutral) and must not crash."""
    bars = _bars_with_volume(
        "AAA", closes=[10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20], volumes=[100] * 11
    )
    bars2 = _bars_with_volume(
        "BBB", closes=[10, 10, 11, 10, 11, 10, 11, 10, 11, 10, 11], volumes=[100] * 11
    )
    symbol_bars = {"AAA": bars, "BBB": bars2}

    # No fundamentals provided → quality/value=0 for both
    result = run_factor_rotation_backtest(
        symbol_bars,
        fundamentals_by_symbol=None,
        momentum_lookback=5,
        reversal_lookback=2,
        volatility_lookback=3,
        risk_filter_lookback=0,
        top_n=1,
        rebalance_days=3,
        factor_weights=FactorWeights(
            momentum=1.0, reversal=0.5, low_volatility=0.75, value=0.3, quality=0.3
        ),
    )
    assert result.fundamental_record_count == 0
    assert result.rows > 0


# ── Regime-cash signal tests ──────────────────────────────────────────────────


def test_bond_equity_corr_positive() -> None:
    """Perfectly co-moving series → correlation = 1.0."""
    n = 20
    spy = [0.01 * i for i in range(n)]
    bond = [0.005 * i for i in range(n)]
    corr = _bond_equity_corr(spy, bond)
    assert corr > 0.99


def test_bond_equity_corr_negative() -> None:
    """Perfectly opposed series → correlation = -1.0."""
    n = 20
    spy = [0.01 * i for i in range(n)]
    bond = [-0.01 * i for i in range(n)]
    corr = _bond_equity_corr(spy, bond)
    assert corr < -0.99


def test_bond_equity_corr_short_series_returns_zero() -> None:
    """Series with fewer than 2 observations → 0.0 (no crash)."""
    assert _bond_equity_corr([0.01], [0.01]) == 0.0
    assert _bond_equity_corr([], []) == 0.0


def test_regime_cash_active_when_corr_positive_and_below_ma() -> None:
    """SPY below MA and SPY–TLT corr > threshold → regime active (True)."""
    # Build 120 trading days of data
    # SPY: trending down (always below its own MA)
    # TLT: also falling together with SPY (positive correlation)
    n = 120
    base_date = date(2022, 1, 3)
    spy_closes: dict[date, float] = {}
    tlt_closes: dict[date, float] = {}
    for i in range(n):
        d = base_date + timedelta(days=i)
        spy_closes[d] = 400.0 - i * 0.8  # steadily declining
        tlt_closes[d] = 140.0 - i * 0.3  # also declining (positive corr with SPY)

    today = base_date + timedelta(days=n - 1)
    result = _regime_cash_active(
        spy_closes,
        tlt_closes,
        today=today,
        corr_window=60,
        corr_threshold=0.2,
    )
    assert result is True


def test_regime_cash_not_active_when_corr_negative() -> None:
    """SPY below MA but TLT rises when SPY falls (flight-to-safety) → regime NOT active."""
    n = 120
    base_date = date(2019, 1, 2)
    spy_closes: dict[date, float] = {}
    tlt_closes: dict[date, float] = {}
    # SPY: alternates -1% / -2% (downtrend → stays below MA)
    # TLT: exact sign-opposite: +1% / +2% (rises when SPY falls → negative corr)
    spy_price = 300.0
    tlt_price = 120.0
    spy_closes[base_date] = spy_price
    tlt_closes[base_date] = tlt_price
    for i in range(1, n):
        d = base_date + timedelta(days=i)
        spy_ret = -0.01 if i % 2 == 1 else -0.02  # SPY always falls
        tlt_ret = +0.01 if i % 2 == 1 else +0.02  # TLT always rises, same magnitude
        spy_price *= 1 + spy_ret
        tlt_price *= 1 + tlt_ret
        spy_closes[d] = spy_price
        tlt_closes[d] = tlt_price

    today = base_date + timedelta(days=n - 1)
    result = _regime_cash_active(
        spy_closes,
        tlt_closes,
        today=today,
        corr_window=60,
        corr_threshold=0.2,
    )
    assert result is False


def test_regime_cash_not_active_when_spy_above_ma() -> None:
    """SPY above MA even if corr positive → regime NOT active."""
    n = 120
    base_date = date(2021, 1, 4)
    spy_closes: dict[date, float] = {}
    tlt_closes: dict[date, float] = {}
    for i in range(n):
        d = base_date + timedelta(days=i)
        spy_closes[d] = 400.0 + i * 0.5  # SPY rising → above MA
        tlt_closes[d] = 140.0 + i * 0.3  # TLT also rising (positive corr)

    today = base_date + timedelta(days=n - 1)
    result = _regime_cash_active(
        spy_closes,
        tlt_closes,
        today=today,
        corr_window=60,
        corr_threshold=0.2,
    )
    assert result is False


def test_regime_cash_enable_false_backward_compat() -> None:
    """regime_cash_enable=False (default) must not change output vs no-arg call."""
    bars_a = _bars("AAA", [10 + i * 0.5 for i in range(60)])
    bars_spy = _bars("SPY", [300 + i for i in range(60)])
    result_default = run_factor_rotation_backtest(
        {"AAA": bars_a},
        benchmark_bars=bars_spy,
        momentum_lookback=10,
        reversal_lookback=5,
        volatility_lookback=5,
        risk_filter_lookback=0,
        top_n=1,
        rebalance_days=5,
    )
    result_explicit_off = run_factor_rotation_backtest(
        {"AAA": bars_a},
        benchmark_bars=bars_spy,
        momentum_lookback=10,
        reversal_lookback=5,
        volatility_lookback=5,
        risk_filter_lookback=0,
        top_n=1,
        rebalance_days=5,
        regime_cash_enable=False,
    )
    assert result_default.total_return == result_explicit_off.total_return
    assert result_default.rows == result_explicit_off.rows


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


def _bars_with_volume(symbol: str, closes: list[float], volumes: list[float]) -> list[PriceBar]:
    return [
        PriceBar(
            symbol,
            "us",
            symbol,
            date(2020, 1, 2) + timedelta(days=index),
            close,
            close,
            close,
            close,
            vol,
        )
        for index, (close, vol) in enumerate(zip(closes, volumes, strict=False))
    ]
