from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, time
from typing import Any

import ccxt

from data.ingest.ccxt_crypto import normalize_crypto_symbol
from data.models import OpenInterestRecord


class CryptoOpenInterestError(RuntimeError):
    pass


def to_perp_symbol(symbol: str, settle: str = "USDT") -> str:
    """Convert a spot symbol to the ccxt swap/perp form required by OI history.

    ``ccxt.fetch_open_interest_history`` only accepts swap symbols like ``BTC/USDT:USDT``;
    calling it with a spot symbol (``BTC/USDT``) errors (docs/CHART_READING.md §11).
    """
    spot = normalize_crypto_symbol(symbol)
    if ":" in spot:
        return spot
    return f"{spot}:{settle}"


def fetch_open_interest_history(
    symbol: str,
    start: date,
    end: date,
    *,
    timeframe: str = "1h",
    exchange_id: str = "binance",
    limit: int = 500,
    fetcher: Callable[[str, str, int, int], list[dict[str, Any]]] | None = None,
) -> list[OpenInterestRecord]:
    """Fetch open-interest history for a crypto perpetual, normalized to OpenInterestRecord.

    ``fetcher`` is injectable for testing (mirrors ccxt ``fetch_open_interest_history``):
    ``(perp_symbol, timeframe, since_ms, limit) -> [{"timestamp", "openInterestAmount",
    "openInterestValue", ...}, ...]``.
    """
    perp = to_perp_symbol(symbol)
    since = _to_millis(start)
    end_ms = _to_millis(end) + 24 * 60 * 60 * 1000
    call = fetcher or _exchange_fetcher(exchange_id)

    # Page through the history like fetch_ccxt_bars: a single call caps at `limit`
    # observations, which silently truncates wide ranges (e.g. 15m over several days).
    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    cursor = since
    while cursor < end_ms:
        batch = call(perp, timeframe, cursor, limit)
        if not batch:
            break
        advanced = False
        for row in batch:
            timestamp = int(row.get("timestamp", 0))
            if timestamp >= end_ms:
                continue
            if timestamp not in seen:
                rows.append(row)
                seen.add(timestamp)
            if timestamp >= cursor:
                cursor = timestamp + 1
                advanced = True
        if not advanced or len(batch) < limit:
            break

    records = [
        _oi_record(row, exchange=exchange_id, symbol=perp)
        for row in rows
        if since <= int(row.get("timestamp", 0)) < end_ms
    ]
    if not records:
        raise CryptoOpenInterestError(f"{perp}: no open-interest rows returned")
    return sorted(records, key=lambda item: item.ts)


def _oi_record(row: dict[str, Any], *, exchange: str, symbol: str) -> OpenInterestRecord:
    timestamp = int(row["timestamp"])
    amount = row.get("openInterestAmount")
    value = row.get("openInterestValue")
    if amount is not None:
        oi_amount = float(amount)
    elif value is not None:
        oi_amount = float(value)
    else:
        raise CryptoOpenInterestError(
            f"{symbol}: row missing both openInterestAmount and openInterestValue"
        )
    return OpenInterestRecord(
        exchange=exchange,
        symbol=symbol,
        ts=datetime.fromtimestamp(timestamp / 1000, tz=UTC).replace(tzinfo=None),
        open_interest_amount=oi_amount,
        open_interest_value=float(value) if value is not None else None,
        source=f"ccxt:{exchange}:oi",
    )


def _exchange_fetcher(
    exchange_id: str,
) -> Callable[[str, str, int, int], list[dict[str, Any]]]:
    exchange_class = getattr(ccxt, exchange_id, None)
    if exchange_class is None:
        raise CryptoOpenInterestError(f"unknown ccxt exchange: {exchange_id}")
    exchange = exchange_class({"enableRateLimit": True, "options": {"defaultType": "swap"}})
    if not exchange.has.get("fetchOpenInterestHistory"):
        raise CryptoOpenInterestError(f"{exchange_id}: open interest history not supported")

    def fetch(symbol: str, timeframe: str, since: int, limit: int) -> list[dict[str, Any]]:
        return exchange.fetch_open_interest_history(
            symbol, timeframe=timeframe, since=since, limit=limit
        )

    return fetch


def _to_millis(value: date) -> int:
    return int(datetime.combine(value, time.min, tzinfo=UTC).timestamp() * 1000)
