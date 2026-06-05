"""Tests for the ADVISORY-ONLY chart-confirmation overlay on the AQR recommender.

The whole point of this overlay is that it is *advisory metadata only*: turning it on
must never change the action / confidence / rank a name receives. These tests use the
REAL chart engine (no mocking) and assert both that the metadata is populated when there
is enough history AND that the core recommendation is invariant to ``with_chart``.
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import date, datetime, timedelta

from data.models import FundamentalRecord, PriceBar
from valuation.recommendation import (
    AQREvaluation,
    ChartSummary,
    ValidatedStrategy,
    chart_confirmation,
    evaluate_ticker,
    format_evaluation,
    format_scan,
    scan_universe,
)

_VALID_DECISIONS = {"ENTER_NOW", "SCALE_IN", "WAIT_FOR_PULLBACK", "AVOID"}

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
            ts=date(2024, 1, 1) + timedelta(days=index),
            open=close,
            high=close * 1.01,
            low=close * 0.99,
            close=close,
            volume=1_000.0,
        )
        for index, close in enumerate(closes)
    ]


def _zigzag_bullish_bars(symbol: str, n: int = 60) -> list[PriceBar]:
    """A long, realistic uptrend with pullbacks so the swing-structure detector reads BULLISH.

    A perfectly smooth ramp produces no swings (RANGING/veto); the sinusoidal pullbacks
    here create higher-highs / higher-lows, giving the engine a real directional read.
    """

    out: list[PriceBar] = []
    price = 100.0
    for i in range(n):
        price = price + 0.8 + 1.5 * math.sin(i / 3.0)
        o = price - 0.5
        c = price
        h = max(o, c) + 1.2
        low = min(o, c) - 1.2
        out.append(
            PriceBar(
                symbol=symbol,
                market="us",
                source_symbol=symbol,
                ts=date(2024, 1, 1) + timedelta(days=i),
                open=o,
                high=h,
                low=low,
                close=c,
                volume=1_000.0 + 50.0 * math.sin(i / 2.0),
            )
        )
    return out


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


def _short_universe() -> tuple[dict[str, list[PriceBar]], dict[str, FundamentalRecord]]:
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


def _long_universe() -> tuple[dict[str, list[PriceBar]], dict[str, FundamentalRecord]]:
    """A universe whose price series are long enough (>=40 bars) for a chart read."""

    bars = {
        "AAA": _zigzag_bullish_bars("AAA", 60),
        "BBB": _bars("BBB", [10.0 + 0.1 * i for i in range(60)]),
        "CCC": _bars("CCC", [10.0] * 60),
    }
    fundamentals = {
        "AAA": _fundamentals("AAA", 50.0, 100.0, 40.0),
        "BBB": _fundamentals("BBB", 30.0, 100.0, 20.0),
        "CCC": _fundamentals("CCC", 10.0, 100.0, 5.0),
    }
    return bars, fundamentals


def _strip_chart(result: AQREvaluation) -> AQREvaluation:
    """Return a copy with the advisory field cleared, for exact equality comparison."""

    return replace(result, chart_summary=None)


# (a) with_chart=False -> result identical to before, chart_summary is None.
def test_with_chart_false_is_identical_and_has_no_chart_summary() -> None:
    bars, fundamentals = _long_universe()

    baseline = evaluate_ticker(
        ticker="AAA",
        bars_by_symbol=bars,
        fundamentals_by_symbol=fundamentals,
        strategy=_STRATEGY,
    )
    explicit_off = evaluate_ticker(
        ticker="AAA",
        bars_by_symbol=bars,
        fundamentals_by_symbol=fundamentals,
        strategy=_STRATEGY,
        with_chart=False,
    )

    assert baseline.chart_summary is None
    assert explicit_off.chart_summary is None
    # Default-vs-explicit-off must be the exact same evaluation.
    assert baseline == explicit_off
    # And the rendered report is byte-identical (no advisory section leaks in).
    assert format_evaluation(baseline) == format_evaluation(explicit_off)
    assert "참고용" not in format_evaluation(baseline)


# (b) with_chart=True + synthetic bullish daily series -> chart_summary populated, valid.
def test_with_chart_true_populates_valid_chart_summary() -> None:
    bars, fundamentals = _long_universe()

    result = evaluate_ticker(
        ticker="AAA",
        bars_by_symbol=bars,
        fundamentals_by_symbol=fundamentals,
        strategy=_STRATEGY,
        with_chart=True,
    )

    cs = result.chart_summary
    assert isinstance(cs, ChartSummary)
    assert cs.decision in _VALID_DECISIONS
    assert 0.0 <= cs.confluence <= 100.0
    assert cs.direction == "long"
    assert cs.trend_bias in {"BULLISH", "BEARISH", "RANGING"}
    # This particular fixture is engineered to read as a real uptrend, not RANGING.
    assert cs.trend_bias == "BULLISH"
    # The advisory section must now appear in the report.
    report = format_evaluation(result)
    assert "참고용" in report
    assert "advisory" in report


# (c) too-few bars (<40) -> chart_summary None, recommendation otherwise unchanged.
def test_too_few_bars_yields_no_chart_summary_but_unchanged_recommendation() -> None:
    bars, fundamentals = _short_universe()  # 3 bars each, well below the 40-bar floor

    without = evaluate_ticker(
        ticker="AAA",
        bars_by_symbol=bars,
        fundamentals_by_symbol=fundamentals,
        strategy=_STRATEGY,
    )
    with_flag = evaluate_ticker(
        ticker="AAA",
        bars_by_symbol=bars,
        fundamentals_by_symbol=fundamentals,
        strategy=_STRATEGY,
        with_chart=True,
    )

    # Not enough history -> the overlay declines to render an opinion.
    assert with_flag.chart_summary is None
    # chart_confirmation itself guards the bar floor directly.
    assert chart_confirmation(bars["AAA"]) is None
    # The rest of the evaluation is untouched.
    assert with_flag == without


# (d) action / confidence / rank are invariant to with_chart (advisory-only proof).
def test_action_confidence_rank_invariant_to_with_chart() -> None:
    bars, fundamentals = _long_universe()

    # Pin asof so the out-of-universe name (which would otherwise fall back to
    # datetime.now()) gets a deterministic timestamp — the point here is the chart
    # overlay's effect, not wall-clock jitter between two separate now() calls.
    pinned = datetime(2024, 3, 1)
    for ticker in ("AAA", "BBB", "CCC", "ZZZ"):  # incl. one outside the universe
        off = evaluate_ticker(
            ticker=ticker,
            bars_by_symbol=bars,
            fundamentals_by_symbol=fundamentals,
            strategy=_STRATEGY,
            asof_ts=pinned,
            with_chart=False,
        )
        on = evaluate_ticker(
            ticker=ticker,
            bars_by_symbol=bars,
            fundamentals_by_symbol=fundamentals,
            strategy=_STRATEGY,
            asof_ts=pinned,
            with_chart=True,
        )

        assert off.action == on.action
        assert off.confidence == on.confidence
        assert off.rank == on.rank
        assert off.percentile == on.percentile
        assert off.in_top_n == on.in_top_n
        # Everything except the advisory field is identical.
        assert _strip_chart(off) == _strip_chart(on)


# (e) scan_universe with_chart optionality.
def test_scan_universe_with_chart_optionality() -> None:
    bars, fundamentals = _long_universe()

    plain = scan_universe(
        bars_by_symbol=bars,
        fundamentals_by_symbol=fundamentals,
        strategy=_STRATEGY,
    )
    charted = scan_universe(
        bars_by_symbol=bars,
        fundamentals_by_symbol=fundamentals,
        strategy=_STRATEGY,
        with_chart=True,
    )

    # Default scan carries no chart metadata at all.
    assert all(r.chart_summary is None for r in plain)
    # Charted scan attaches at least one valid read (the engineered uptrend name).
    assert any(r.chart_summary is not None for r in charted)
    populated = [r for r in charted if r.chart_summary is not None]
    for r in populated:
        assert r.chart_summary is not None
        assert r.chart_summary.decision in _VALID_DECISIONS
        assert 0.0 <= r.chart_summary.confluence <= 100.0

    # Ranking and actions are invariant to the overlay.
    assert [r.ticker for r in plain] == [r.ticker for r in charted]
    assert [r.rank for r in plain] == [r.rank for r in charted]
    assert [r.action for r in plain] == [r.action for r in charted]

    # The default table is byte-identical; the charted table gains an advisory column.
    assert "참고용" not in format_scan(plain)
    assert "참고용" in format_scan(charted)
