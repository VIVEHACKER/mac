from __future__ import annotations

from datetime import date, datetime, timedelta

from data.models import FlowRecord, FundamentalRecord, PriceBar, ValuationRecord
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
        "AAA": FundamentalRecord("AAA", "us", date(2025, 1, 1), datetime(2025, 2, 1), net_income=50, free_cash_flow=40, total_equity=100, shares_out=10),
        "BBB": FundamentalRecord("BBB", "us", date(2025, 1, 1), datetime(2025, 2, 1), net_income=5, free_cash_flow=4, total_equity=100, shares_out=10),
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
