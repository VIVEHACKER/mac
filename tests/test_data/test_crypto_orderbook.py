from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from data.ingest.crypto_orderbook import CryptoOrderBookError, fetch_order_book
from data.models import OrderBookSnapshot


def test_fetch_order_book_parses_injected_snapshot() -> None:
    ts_ms = int(datetime(2026, 5, 7, 12, 0, tzinfo=UTC).timestamp() * 1000)

    def fetcher(symbol: str, limit: int) -> dict[str, Any]:
        assert symbol == "BTC/USDT"
        assert limit == 20
        return {
            "bids": [[100.0, 5.0], [99.5, 3.0]],
            "asks": [[100.5, 2.0], [101.0, 4.0]],
            "timestamp": ts_ms,
            "symbol": "BTC/USDT",
            "nonce": 1,
        }

    snap = fetch_order_book("btc", fetcher=fetcher)

    assert isinstance(snap, OrderBookSnapshot)
    assert snap.symbol == "BTC/USDT"
    assert snap.exchange == "binance"
    assert len(snap.bids) == 2
    assert snap.bids[0].price == 100.0
    assert snap.bids[0].size == 5.0
    assert snap.asks[0].price == 100.5
    assert snap.asks[0].size == 2.0
    # naive-UTC timestamp, matching CryptoFundingRecord convention
    assert snap.ts == datetime(2026, 5, 7, 12, 0)


def test_fetch_order_book_falls_back_to_local_ts_when_none() -> None:
    def fetcher(symbol: str, limit: int) -> dict[str, Any]:
        return {"bids": [[1.0, 1.0]], "asks": [[2.0, 1.0]], "timestamp": None}

    snap = fetch_order_book("eth", fetcher=fetcher)

    assert isinstance(snap.ts, datetime)
    assert snap.symbol == "ETH/USDT"


def test_fetch_order_book_raises_on_empty_side() -> None:
    def fetcher(symbol: str, limit: int) -> dict[str, Any]:
        return {"bids": [], "asks": [[2.0, 1.0]], "timestamp": 0}

    with pytest.raises(CryptoOrderBookError):
        fetch_order_book("btc", fetcher=fetcher)
