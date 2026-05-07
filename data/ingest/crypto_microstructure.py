from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, time
from typing import Any

import ccxt

from data.ingest.ccxt_crypto import normalize_crypto_symbol
from data.models import CryptoFundingRecord


class CryptoMicrostructureError(RuntimeError):
    pass


def fetch_funding_history(
    symbol: str,
    start: date,
    end: date,
    *,
    exchange_id: str = "binance",
    fetcher: Callable[[str, int, int], list[dict[str, Any]]] | None = None,
) -> list[CryptoFundingRecord]:
    source_symbol = normalize_crypto_symbol(symbol)
    since = _to_millis(start)
    end_ms = _to_millis(end) + 24 * 60 * 60 * 1000
    call = fetcher or _exchange_fetcher(exchange_id)
    rows = call(source_symbol, since, 1000)
    records = [
        _funding_record(row, exchange=exchange_id, symbol=source_symbol)
        for row in rows
        if since <= int(row.get("timestamp", 0)) < end_ms
    ]
    if not records:
        raise CryptoMicrostructureError(f"{source_symbol}: no funding rows returned")
    return sorted(records, key=lambda item: item.ts)


def _funding_record(row: dict[str, Any], *, exchange: str, symbol: str) -> CryptoFundingRecord:
    timestamp = int(row["timestamp"])
    next_timestamp = row.get("nextFundingTimestamp") or row.get("nextFundingTime")
    return CryptoFundingRecord(
        exchange=exchange,
        symbol=symbol,
        ts=datetime.fromtimestamp(timestamp / 1000, tz=UTC).replace(tzinfo=None),
        funding_rate=float(row.get("fundingRate") or row.get("rate") or 0.0),
        next_funding_ts=(
            datetime.fromtimestamp(int(next_timestamp) / 1000, tz=UTC).replace(tzinfo=None)
            if next_timestamp
            else None
        ),
        mark_price=float(row["markPrice"]) if row.get("markPrice") is not None else None,
        source=f"ccxt:{exchange}:funding",
    )


def _exchange_fetcher(exchange_id: str) -> Callable[[str, int, int], list[dict[str, Any]]]:
    exchange_class = getattr(ccxt, exchange_id, None)
    if exchange_class is None:
        raise CryptoMicrostructureError(f"unknown ccxt exchange: {exchange_id}")
    exchange = exchange_class({"enableRateLimit": True})
    if not hasattr(exchange, "fetch_funding_rate_history"):
        raise CryptoMicrostructureError(f"{exchange_id}: funding history is not supported")

    def fetch(symbol: str, since: int, limit: int) -> list[dict[str, Any]]:
        return exchange.fetch_funding_rate_history(symbol, since=since, limit=limit)

    return fetch


def _to_millis(value: date) -> int:
    return int(datetime.combine(value, time.min, tzinfo=UTC).timestamp() * 1000)
