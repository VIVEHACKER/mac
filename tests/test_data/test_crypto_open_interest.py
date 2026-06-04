from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest

from data.ingest.crypto_open_interest import (
    CryptoOpenInterestError,
    fetch_open_interest_history,
    to_perp_symbol,
)
from data.models import OpenInterestRecord


def test_to_perp_symbol_appends_settlement() -> None:
    assert to_perp_symbol("btc") == "BTC/USDT:USDT"
    assert to_perp_symbol("ETH/USDT") == "ETH/USDT:USDT"
    assert to_perp_symbol("BTC/USDT:USDT") == "BTC/USDT:USDT"


def test_fetch_oi_history_uses_injected_fetcher() -> None:
    ts_ms = int(datetime(2026, 5, 7, 0, 0, tzinfo=UTC).timestamp() * 1000)

    def fetcher(symbol: str, timeframe: str, since: int, limit: int) -> list[dict[str, Any]]:
        assert symbol == "BTC/USDT:USDT"
        assert timeframe == "1h"
        assert limit == 500
        return [
            {
                "timestamp": ts_ms,
                "openInterestAmount": 12345.0,
                "openInterestValue": 5.0e8,
                "symbol": "BTC/USDT:USDT",
            }
        ]

    recs = fetch_open_interest_history(
        "btc", start=date(2026, 5, 7), end=date(2026, 5, 7), fetcher=fetcher
    )

    assert len(recs) == 1
    assert isinstance(recs[0], OpenInterestRecord)
    assert recs[0].open_interest_amount == 12345.0
    assert recs[0].open_interest_value == 5.0e8
    assert recs[0].symbol == "BTC/USDT:USDT"
    assert recs[0].exchange == "binance"
    assert recs[0].ts == datetime(2026, 5, 7, 0, 0)


def test_fetch_oi_history_filters_out_of_range_rows() -> None:
    in_ms = int(datetime(2026, 5, 7, 1, 0, tzinfo=UTC).timestamp() * 1000)
    out_ms = int(datetime(2026, 5, 9, 0, 0, tzinfo=UTC).timestamp() * 1000)

    def fetcher(symbol: str, timeframe: str, since: int, limit: int) -> list[dict[str, Any]]:
        return [
            {"timestamp": in_ms, "openInterestAmount": 1.0, "symbol": symbol},
            {"timestamp": out_ms, "openInterestAmount": 2.0, "symbol": symbol},
        ]

    recs = fetch_open_interest_history(
        "btc", start=date(2026, 5, 7), end=date(2026, 5, 7), fetcher=fetcher
    )

    assert len(recs) == 1
    assert recs[0].open_interest_amount == 1.0


def test_fetch_oi_history_raises_when_empty() -> None:
    def fetcher(symbol: str, timeframe: str, since: int, limit: int) -> list[dict[str, Any]]:
        return []

    with pytest.raises(CryptoOpenInterestError):
        fetch_open_interest_history(
            "btc", start=date(2026, 5, 7), end=date(2026, 5, 7), fetcher=fetcher
        )
