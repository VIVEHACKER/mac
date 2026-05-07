from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, time

import ccxt

from data.models import PriceBar


class CcxtDataError(RuntimeError):
    pass


def normalize_crypto_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper().replace("-", "/")
    if "/" not in normalized:
        return f"{normalized}/USDT"
    return normalized


def fetch_ccxt_bars(
    symbol: str,
    start: date,
    end: date,
    timeframe: str = "1d",
    exchange_id: str = "binance",
    fetch_ohlcv: Callable[[str, str, int, int], list[list[float]]] | None = None,
) -> list[PriceBar]:
    source_symbol = normalize_crypto_symbol(symbol)
    since = _to_millis(start)
    end_ms = _to_millis(end) + 24 * 60 * 60 * 1000
    fetcher = fetch_ohlcv or _exchange_fetcher(exchange_id)
    rows: list[list[float]] = []
    cursor = since
    seen: set[int] = set()

    while cursor < end_ms:
        batch = fetcher(source_symbol, timeframe, cursor, 1000)
        if not batch:
            break
        advanced = False
        for row in batch:
            timestamp = int(row[0])
            if timestamp >= end_ms:
                continue
            if timestamp not in seen:
                rows.append(row)
                seen.add(timestamp)
            if timestamp >= cursor:
                cursor = timestamp + 1
                advanced = True
        if not advanced or len(batch) < 1000:
            break

    bars = _rows_to_bars(rows, symbol=symbol, source_symbol=source_symbol, timeframe=timeframe)
    if not bars:
        raise CcxtDataError(f"{source_symbol}: no OHLCV bars returned from {exchange_id}")
    return bars


def _exchange_fetcher(exchange_id: str) -> Callable[[str, str, int, int], list[list[float]]]:
    exchange_class = getattr(ccxt, exchange_id, None)
    if exchange_class is None:
        raise CcxtDataError(f"unknown ccxt exchange: {exchange_id}")
    exchange = exchange_class({"enableRateLimit": True})
    return exchange.fetch_ohlcv


def _rows_to_bars(
    rows: list[list[float]],
    *,
    symbol: str,
    source_symbol: str,
    timeframe: str,
) -> list[PriceBar]:
    bars: list[PriceBar] = []
    for row in sorted(rows, key=lambda item: item[0]):
        if len(row) < 6:
            continue
        timestamp, open_value, high_value, low_value, close_value, volume_value = row[:6]
        bars.append(
            PriceBar(
                symbol=normalize_crypto_symbol(symbol),
                market="crypto",
                source_symbol=source_symbol,
                ts=datetime.fromtimestamp(timestamp / 1000, tz=UTC).date(),
                open=float(open_value),
                high=float(high_value),
                low=float(low_value),
                close=float(close_value),
                volume=float(volume_value),
                freq=timeframe,
                currency="USDT",
                source=f"ccxt:{source_symbol}",
            )
        )
    return bars


def _to_millis(value: date) -> int:
    return int(datetime.combine(value, time.min, tzinfo=UTC).timestamp() * 1000)
