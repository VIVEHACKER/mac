from __future__ import annotations

from datetime import date, datetime

from data.models import FundamentalRecord, PriceBar
from valuation.recommendation import (
    AQREvaluation,
    ValidatedStrategy,
    evaluate_ticker,
    format_evaluation,
    format_scan,
    load_validated_strategy,
    scan_universe,
)

_STRATEGY = ValidatedStrategy(
    strategy_id="aqr_top7_cap20_trail10",
    universe="sp100-pit",
    top_n=7,
    wf_positive_rate=0.87,
    psr=0.90,
    dsr=0.60,
    lookback=2,
)


def _bars(symbol: str, closes: list[float]) -> list[PriceBar]:
    return [
        PriceBar(
            symbol=symbol,
            market="us",
            source_symbol=symbol,
            ts=date(2024, 1, 1 + index),
            open=close,
            high=close * 1.01,
            low=close * 0.99,
            close=close,
            volume=1_000.0,
        )
        for index, close in enumerate(closes)
    ]


def _fundamentals(symbol: str, net_income: float, equity: float, fcf: float) -> FundamentalRecord:
    return FundamentalRecord(
        symbol=symbol,
        market="us",
        period_end=date(2023, 12, 31),
        asof_ts=datetime(2024, 1, 1),
        net_income=net_income,
        total_equity=equity,
        free_cash_flow=fcf,
        shares_out=100.0,
    )


def _universe() -> tuple[dict[str, list[PriceBar]], dict[str, FundamentalRecord]]:
    bars = {
        "AAA": _bars("AAA", [10.0, 11.0, 13.0]),
        "BBB": _bars("BBB", [10.0, 10.0, 11.0]),
        "CCC": _bars("CCC", [10.0, 10.0, 10.0]),
    }
    fundamentals = {
        "AAA": _fundamentals("AAA", 50.0, 100.0, 40.0),
        "BBB": _fundamentals("BBB", 30.0, 100.0, 20.0),
        "CCC": _fundamentals("CCC", 10.0, 100.0, 5.0),
    }
    return bars, fundamentals


def test_top_ranked_in_universe_name_is_a_buy_with_entry_ladder() -> None:
    bars, fundamentals = _universe()

    result = evaluate_ticker(
        ticker="AAA",
        bars_by_symbol=bars,
        fundamentals_by_symbol=fundamentals,
        strategy=_STRATEGY,
    )

    assert isinstance(result, AQREvaluation)
    assert result.in_validated_universe is True
    assert result.rank == 1
    assert result.in_top_n is True
    assert result.action == "BUY"
    assert result.confidence.band == "high"
    # The whole point: an average buy price (laddered entry) below the current price.
    assert result.entry_plan is not None
    assert result.current_price == 13.0
    assert result.fair_value is not None and result.fair_value > 0
    assert result.entry_plan.target_entry < result.current_price


def test_ticker_outside_validated_universe_is_avoided_and_capped() -> None:
    bars, fundamentals = _universe()

    result = evaluate_ticker(
        ticker="ZZZ",
        bars_by_symbol=bars,
        fundamentals_by_symbol=fundamentals,
        strategy=_STRATEGY,
    )

    assert result.in_validated_universe is False
    assert result.rank is None
    assert result.action == "AVOID"
    assert result.confidence.band == "low"
    assert result.confidence.score <= 25.0


def test_bottom_of_universe_name_is_not_a_buy() -> None:
    bars, fundamentals = _universe()

    result = evaluate_ticker(
        ticker="CCC",
        bars_by_symbol=bars,
        fundamentals_by_symbol=fundamentals,
        strategy=_STRATEGY,
    )

    assert result.in_validated_universe is True
    assert result.rank == 3
    assert result.action in {"HOLD", "AVOID"}
    assert result.action != "BUY"


def test_format_evaluation_renders_key_sections() -> None:
    bars, fundamentals = _universe()
    result = evaluate_ticker(
        ticker="AAA",
        bars_by_symbol=bars,
        fundamentals_by_symbol=fundamentals,
        strategy=_STRATEGY,
    )

    report = format_evaluation(result)

    assert isinstance(report, str)
    assert "AAA" in report
    assert result.action in report
    # The investor-facing essentials must all be present.
    assert "AQR" in report
    assert "1/3" in report  # rank / universe
    assert "Confidence" in report or "신뢰도" in report
    assert "Entry" in report or "평균" in report
    # The laddered average buy price must appear (the core of profit maximization).
    assert result.entry_plan is not None
    assert f"{result.entry_plan.target_entry:.2f}" in report


def test_momentum_winner_above_fair_value_still_has_coherent_target() -> None:
    # A momentum winner trading ABOVE its (credible, low) DCF fair value must not get a
    # target_exit below the current price. Entry/target are volatility-based, not value.
    bars = {
        "WIN": _bars("WIN", [80.0, 90.0, 100.0]),  # strong momentum, priced at 100
        "BBB": _bars("BBB", [10.0, 10.0, 11.0]),
        "CCC": _bars("CCC", [10.0, 10.0, 10.0]),
    }
    fundamentals = {
        # FCF/shares give a credible DCF (~0.5-2.0x of price) that sits BELOW the price.
        "WIN": _fundamentals("WIN", 30.0, 100.0, 30.0),
        "BBB": _fundamentals("BBB", 30.0, 100.0, 20.0),
        "CCC": _fundamentals("CCC", 10.0, 100.0, 5.0),
    }

    result = evaluate_ticker(
        ticker="WIN",
        bars_by_symbol=bars,
        fundamentals_by_symbol=fundamentals,
        strategy=_STRATEGY,
    )

    assert result.entry_plan is not None
    assert result.current_price == 100.0
    assert result.entry_plan.target_exit > result.current_price
    assert result.entry_plan.target_entry < result.current_price
    assert result.entry_plan.risk_reward > 0


def test_single_name_universe_is_not_treated_as_validated() -> None:
    # A universe of one cannot support a cross-sectional edge -> AVOID, capped low,
    # never a BUY from a degenerate ranking.
    bars = {"AAA": _bars("AAA", [10.0, 11.0, 13.0])}
    fundamentals = {"AAA": _fundamentals("AAA", 50.0, 100.0, 40.0)}

    result = evaluate_ticker(
        ticker="AAA",
        bars_by_symbol=bars,
        fundamentals_by_symbol=fundamentals,
        strategy=_STRATEGY,
    )

    assert result.in_validated_universe is False
    assert result.action == "AVOID"
    assert result.confidence.score <= 25.0


def test_provisional_strategy_surfaces_warning_in_reasons() -> None:
    bars, fundamentals = _universe()
    provisional_strategy = ValidatedStrategy(
        strategy_id="prov",
        universe="sp100-pit",
        top_n=7,
        wf_positive_rate=0.87,
        psr=0.80,
        dsr=0.55,
        lookback=2,
        provisional=True,
    )

    result = evaluate_ticker(
        ticker="AAA",
        bars_by_symbol=bars,
        fundamentals_by_symbol=fundamentals,
        strategy=provisional_strategy,
    )

    assert any("provisional" in reason.lower() for reason in result.confidence.reasons)


def test_incredible_dcf_falls_back_to_coherent_volatility_band() -> None:
    # Reproduces the AAPL bug: a naive single-stage DCF underestimates a high-priced
    # name (fair << price), which used to produce target_exit < current price and R/R 0.
    bars = {
        "AAA": _bars("AAA", [10.0, 11.0, 13.0]),
        "BBB": _bars("BBB", [10.0, 10.0, 11.0]),
        "OVR": _bars("OVR", [90.0, 95.0, 100.0]),
    }
    fundamentals = {
        "AAA": _fundamentals("AAA", 50.0, 100.0, 40.0),
        "BBB": _fundamentals("BBB", 30.0, 100.0, 20.0),
        "OVR": _fundamentals("OVR", 3.0, 50.0, 2.0),  # tiny FCF vs high price -> DCF << price
    }

    result = evaluate_ticker(
        ticker="OVR",
        bars_by_symbol=bars,
        fundamentals_by_symbol=fundamentals,
        strategy=_STRATEGY,
    )

    assert result.current_price == 100.0
    assert result.valuation_credible is False
    # The raw DCF is still reported for transparency, and is clearly far below price.
    assert result.fair_value is not None and result.fair_value < result.current_price * 0.5
    # The entry band must be coherent around the current price (not the broken DCF).
    assert result.entry_plan is not None
    assert result.entry_plan.target_exit > result.current_price
    assert result.entry_plan.stop_loss < result.current_price
    assert result.entry_plan.risk_reward > 0
    assert result.entry_plan.target_entry < result.current_price


def test_scan_universe_ranks_all_names_best_first() -> None:
    bars, fundamentals = _universe()

    results = scan_universe(
        bars_by_symbol=bars,
        fundamentals_by_symbol=fundamentals,
        strategy=_STRATEGY,
    )

    assert [r.ticker for r in results] == ["AAA", "BBB", "CCC"]
    assert [r.rank for r in results] == [1, 2, 3]
    assert all(r.in_validated_universe for r in results)
    assert all(r.universe_size == 3 for r in results)
    # Top of the universe within top-N is the strategy's actual BUY candidate.
    assert results[0].action == "BUY"
    # Every name carries its own entry band (full-universe coverage, not a few picks).
    assert all(r.entry_plan is not None for r in results)


def test_format_scan_renders_ranked_table_with_holdings_marked() -> None:
    bars, fundamentals = _universe()
    results = scan_universe(
        bars_by_symbol=bars, fundamentals_by_symbol=fundamentals, strategy=_STRATEGY
    )

    report = format_scan(results, top=3)

    assert "AAA" in report and "CCC" in report
    assert "action" in report.lower()
    assert "★" in report  # strategy holdings are flagged


def test_load_validated_strategy_reads_shipped_ideal_baseline() -> None:
    strategy = load_validated_strategy()

    assert isinstance(strategy, ValidatedStrategy)
    assert strategy.strategy_id == "aqr_top7_cap20_trail10"
    assert strategy.top_n == 7
    assert strategy.lookback == 126
    assert strategy.provisional is False
    assert 0.0 <= strategy.wf_positive_rate <= 1.0
    assert 0.0 <= strategy.psr <= 1.0
    assert 0.0 <= strategy.dsr <= 1.0
