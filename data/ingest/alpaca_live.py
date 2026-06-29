from __future__ import annotations

import inspect
import logging
import threading
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime
from typing import Any

from data.models import PriceBar


class AlpacaStreamTimeoutError(TimeoutError):
    """Raised when the Alpaca stream produces no terminal condition before timeout."""


def fetch_alpaca_latest_stock_bars(
    symbols: Iterable[str],
    *,
    api_key: str,
    secret_key: str,
    feed: str = "iex",
) -> list[PriceBar]:
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockLatestBarRequest

    client = StockHistoricalDataClient(api_key, secret_key)
    request = StockLatestBarRequest(
        symbol_or_symbols=[symbol.upper() for symbol in symbols],
        feed=_data_feed(feed),
    )
    return alpaca_latest_bars_to_price_bars(client.get_stock_latest_bar(request), feed=feed)


def stream_alpaca_stock_bars(
    symbols: Iterable[str],
    *,
    api_key: str,
    secret_key: str,
    feed: str = "iex",
    on_bar: Callable[[PriceBar], None],
    max_bars: int | None = None,
    timeout_s: float | None = None,
) -> int:
    """Stream Alpaca stock bars and call ``on_bar`` for each normalized bar."""

    from alpaca.data.live import StockDataStream

    stream = StockDataStream(api_key, secret_key, feed=_data_feed(feed))
    count = 0
    timed_out = False

    async def handle_bar(raw_bar: Any) -> None:
        nonlocal count
        bar = alpaca_stream_bar_to_price_bar(raw_bar, feed=feed)
        on_bar(bar)
        count += 1
        if max_bars is not None and count >= max_bars:
            stop_ws = getattr(stream, "stop_ws", None)
            if stop_ws is not None:
                maybe_awaitable = stop_ws()
                if inspect.isawaitable(maybe_awaitable):
                    await maybe_awaitable
            else:
                stream.stop()

    def stop_after_timeout() -> None:
        nonlocal timed_out
        timed_out = True
        stream.stop()

    stream.subscribe_bars(handle_bar, *[symbol.upper() for symbol in symbols])
    timeout_timer: threading.Timer | None = None
    timeout_value: float | None = None
    sdk_logger = logging.getLogger("alpaca.data.live.websocket")
    previous_sdk_level: int | None = None
    if timeout_s is not None and timeout_s > 0:
        timeout_value = float(timeout_s)
        timeout_timer = threading.Timer(timeout_value, stop_after_timeout)
        timeout_timer.daemon = True
        timeout_timer.start()
        previous_sdk_level = sdk_logger.level
        sdk_logger.setLevel(logging.CRITICAL)
    try:
        stream.run()
    finally:
        if timeout_timer is not None:
            timeout_timer.cancel()
        if previous_sdk_level is not None:
            sdk_logger.setLevel(previous_sdk_level)
    if timed_out:
        assert timeout_value is not None
        raise AlpacaStreamTimeoutError(f"Alpaca stock bar stream timed out after {timeout_value:.1f}s")
    return count


def alpaca_latest_bars_to_price_bars(response: Any, *, feed: str = "iex") -> list[PriceBar]:
    bars: list[PriceBar] = []
    for symbol, raw_bar in _iter_bar_items(response):
        bars.append(_alpaca_bar_to_price_bar(symbol, raw_bar, feed=feed))
    return bars


def alpaca_stream_bar_to_price_bar(raw_bar: Any, *, feed: str = "iex") -> PriceBar:
    return _alpaca_bar_to_price_bar(
        _bar_symbol(raw_bar),
        raw_bar,
        feed=feed,
        source_kind="stream_bar",
    )


def _iter_bar_items(response: Any):
    if isinstance(response, dict):
        return response.items()
    data = getattr(response, "data", None)
    if isinstance(data, dict):
        return data.items()
    if hasattr(response, "items"):
        return response.items()
    raise ValueError("unsupported Alpaca latest bar response")


def _alpaca_bar_to_price_bar(
    symbol: str,
    raw_bar: Any,
    *,
    feed: str,
    source_kind: str = "latest_bar",
) -> PriceBar:
    ts = _date_from_timestamp(_bar_value(raw_bar, "timestamp", "t"))
    return PriceBar(
        symbol=symbol.upper(),
        market="us",
        source_symbol=symbol.upper(),
        ts=ts,
        open=float(_bar_value(raw_bar, "open", "o")),
        high=float(_bar_value(raw_bar, "high", "h")),
        low=float(_bar_value(raw_bar, "low", "l")),
        close=float(_bar_value(raw_bar, "close", "c")),
        volume=float(_bar_value(raw_bar, "volume", "v")),
        currency="USD",
        source=f"alpaca:{feed.lower()}:{source_kind}",
    )


def _bar_symbol(raw_bar: Any) -> str:
    return str(_bar_value(raw_bar, "symbol", "S")).upper()


def _bar_value(raw_bar: Any, *names: str) -> Any:
    if isinstance(raw_bar, dict):
        for name in names:
            if name in raw_bar:
                return raw_bar[name]
    for name in names:
        if hasattr(raw_bar, name):
            return getattr(raw_bar, name)
    raise ValueError(f"Alpaca bar is missing {'/'.join(names)}")


def _date_from_timestamp(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    ts = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.date()


def _data_feed(feed: str):
    from alpaca.data.enums import DataFeed

    normalized = feed.strip().lower()
    for item in DataFeed:
        if item.value == normalized:
            return item
    raise ValueError(f"unsupported Alpaca data feed: {feed}")
