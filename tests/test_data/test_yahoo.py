from __future__ import annotations

from datetime import UTC, date, datetime

import pandas as pd
import pytest

from data.ingest.yahoo import (
    YahooDataError,
    _bar_from_quote,
    aggregate_intraday_bars,
    fetch_yahoo_quotes,
)
from data.models import PriceBar


def test_bar_from_quote_uses_adjusted_close_ratio() -> None:
    bar = _bar_from_quote(
        symbol="SPY",
        market="us",
        source_symbol="SPY",
        timestamp=1_735_689_600,
        quote_data={
            "open": [100.0],
            "high": [110.0],
            "low": [90.0],
            "close": [100.0],
            "volume": [1_000],
        },
        adjusted_data={"adjclose": [50.0]},
        index=0,
        interval="1d",
        currency="USD",
        source="https://example.test",
    )

    assert bar is not None
    assert bar.open == 50.0
    assert bar.high == 55.0
    assert bar.low == 45.0
    assert bar.close == 50.0
    assert type(bar.ts) is date


def test_bar_from_quote_preserves_intraday_timestamp() -> None:
    bar = _bar_from_quote(
        symbol="AAPL",
        market="us",
        source_symbol="AAPL",
        timestamp=1_735_689_600,
        quote_data={
            "open": [100.0],
            "high": [101.0],
            "low": [99.0],
            "close": [100.5],
            "volume": [10],
        },
        adjusted_data={},
        index=0,
        interval="1m",
        currency="USD",
        source="https://example.test",
    )

    assert bar is not None
    assert isinstance(bar.ts, datetime)
    assert bar.ts == datetime(2025, 1, 1)


def test_aggregate_intraday_bars_keeps_ohlcv_and_day_boundary() -> None:
    def make_bar(ts: datetime, price: float) -> PriceBar:
        return PriceBar(
            symbol="AAPL",
            market="us",
            source_symbol="AAPL",
            ts=ts,
            open=price,
            high=price + 2,
            low=price - 1,
            close=price + 1,
            volume=10,
            freq="1h",
            currency="USD",
            source="yahoo",
        )

    bars = [
        make_bar(datetime(2026, 7, 9, 13, 30), 100),
        make_bar(datetime(2026, 7, 9, 14, 30), 101),
        make_bar(datetime(2026, 7, 9, 15, 30), 102),
        make_bar(datetime(2026, 7, 9, 16, 30), 103),
        make_bar(datetime(2026, 7, 10, 13, 30), 110),
    ]

    aggregated = aggregate_intraday_bars(bars, bars_per_bucket=4, frequency="4h")

    assert len(aggregated) == 2
    assert aggregated[0].open == 100
    assert aggregated[0].high == 105
    assert aggregated[0].low == 99
    assert aggregated[0].close == 104
    assert aggregated[0].volume == 40
    assert aggregated[1].ts == datetime(2026, 7, 10, 13, 30)


def test_fetch_yahoo_quotes_parses_multi_symbol_frame() -> None:
    index = pd.DatetimeIndex(
        [datetime(2026, 7, 10, 19, 58, tzinfo=UTC), datetime(2026, 7, 10, 19, 59, tzinfo=UTC)]
    )
    columns = pd.MultiIndex.from_product([["Close", "Open"], ["AAPL", "MSFT"]])
    frame = pd.DataFrame(
        [[315.0, 385.0, 310.0, 380.0], [315.3, 385.1, 310.0, 380.0]],
        index=index,
        columns=columns,
    )

    quotes = fetch_yahoo_quotes(["AAPL", "MSFT"], "us", download=lambda *args, **kwargs: frame)

    assert quotes["AAPL"].price == 315.3
    assert quotes["AAPL"].day_open == 310.0
    assert quotes["AAPL"].timestamp == datetime(2026, 7, 10, 19, 59, tzinfo=UTC)
    assert quotes["MSFT"].price == 385.1


def test_fetch_yahoo_quotes_rejects_invalid_symbols() -> None:
    with pytest.raises(YahooDataError, match="invalid Yahoo quote symbols"):
        fetch_yahoo_quotes(["AAPL;DROP"], "us", download=lambda *args, **kwargs: None)
