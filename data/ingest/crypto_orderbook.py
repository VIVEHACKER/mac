from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import ccxt

from data.ingest.ccxt_crypto import normalize_crypto_symbol
from data.models import OrderBookLevel, OrderBookSnapshot


class CryptoOrderBookError(RuntimeError):
    pass


def fetch_order_book(
    symbol: str,
    *,
    exchange_id: str = "binance",
    limit: int = 20,
    fetcher: Callable[[str, int], dict[str, Any]] | None = None,
) -> OrderBookSnapshot:
    """Fetch one L2 order-book snapshot and normalize it into an OrderBookSnapshot.

    ``fetcher`` is injectable for testing (mirrors ccxt ``fetch_order_book`` shape):
    ``(symbol, limit) -> {"bids": [[price, size], ...], "asks": [...], "timestamp": int|None}``.
    Validation of ordering / crossed book is left to the detector (chart/orderbook.py);
    here we only normalize and guard against an empty side.
    """
    source_symbol = normalize_crypto_symbol(symbol)
    call = fetcher or _exchange_fetcher(exchange_id)
    book = call(source_symbol, limit)
    bids_raw = book.get("bids") or []
    asks_raw = book.get("asks") or []
    if not bids_raw or not asks_raw:
        raise CryptoOrderBookError(f"{source_symbol}: empty order book from {exchange_id}")
    bids = tuple(OrderBookLevel(price=float(lv[0]), size=float(lv[1])) for lv in bids_raw)
    asks = tuple(OrderBookLevel(price=float(lv[0]), size=float(lv[1])) for lv in asks_raw)
    ts_ms = book.get("timestamp")
    if ts_ms is None:
        ts_ms = int(time.time() * 1000)
    ts = datetime.fromtimestamp(int(ts_ms) / 1000, tz=UTC).replace(tzinfo=None)
    return OrderBookSnapshot(
        exchange=exchange_id,
        symbol=source_symbol,
        ts=ts,
        bids=bids,
        asks=asks,
        source=f"ccxt:{exchange_id}:orderbook",
    )


def _exchange_fetcher(exchange_id: str) -> Callable[[str, int], dict[str, Any]]:
    exchange_class = getattr(ccxt, exchange_id, None)
    if exchange_class is None:
        raise CryptoOrderBookError(f"unknown ccxt exchange: {exchange_id}")
    exchange = exchange_class({"enableRateLimit": True})

    def fetch(symbol: str, limit: int) -> dict[str, Any]:
        return exchange.fetch_order_book(symbol, limit=limit)

    return fetch
