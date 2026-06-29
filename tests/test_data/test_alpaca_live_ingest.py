from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any

import pytest

from data.ingest.alpaca_live import (
    AlpacaStreamTimeoutError,
    alpaca_latest_bars_to_price_bars,
    alpaca_stream_bar_to_price_bar,
    stream_alpaca_stock_bars,
)
from data.models import PriceBar


def test_alpaca_latest_bars_map_to_live_grade_price_bars() -> None:
    bars = alpaca_latest_bars_to_price_bars(
        {
            "QQQ": {
                "timestamp": datetime(2026, 5, 25, 20, tzinfo=UTC),
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100.5,
                "volume": 1_000,
            }
        },
        feed="iex",
    )

    assert len(bars) == 1
    assert bars[0].symbol == "QQQ"
    assert bars[0].source == "alpaca:iex:latest_bar"


def test_alpaca_stream_bar_maps_to_live_grade_price_bar() -> None:
    bar = alpaca_stream_bar_to_price_bar(
        {
            "S": "QQQ",
            "t": datetime(2026, 5, 25, 20, tzinfo=UTC),
            "o": 100,
            "h": 101,
            "l": 99,
            "c": 100.5,
            "v": 1_000,
        },
        feed="sip",
    )

    assert bar.symbol == "QQQ"
    assert bar.source == "alpaca:sip:stream_bar"


def test_alpaca_stream_stops_after_max_bars(monkeypatch: pytest.MonkeyPatch) -> None:
    import alpaca.data.live as alpaca_live_module

    class _FakeStream:
        instances: list[_FakeStream] = []

        def __init__(self, api_key: str, secret_key: str, *, feed: object) -> None:
            self.api_key = api_key
            self.secret_key = secret_key
            self.feed = feed
            self.handler: Any = None
            self.symbols: tuple[str, ...] = ()
            self.stopped = False
            self.__class__.instances.append(self)

        def subscribe_bars(self, handler: Any, *symbols: str) -> None:
            self.handler = handler
            self.symbols = symbols

        async def stop_ws(self) -> None:
            self.stopped = True

        def run(self) -> None:
            asyncio.run(
                self.handler(
                    {
                        "S": "qqq",
                        "t": datetime(2026, 5, 25, 20, tzinfo=UTC),
                        "o": 100,
                        "h": 101,
                        "l": 99,
                        "c": 100.5,
                        "v": 1_000,
                    }
                )
            )
            if not self.stopped:
                asyncio.run(
                    self.handler(
                        {
                            "S": "qqq",
                            "t": datetime(2026, 5, 25, 20, tzinfo=UTC),
                            "o": 100,
                            "h": 101,
                            "l": 99,
                            "c": 100.5,
                            "v": 1_000,
                        }
                    )
                )

    monkeypatch.setattr(alpaca_live_module, "StockDataStream", _FakeStream)
    bars: list[PriceBar] = []

    count = stream_alpaca_stock_bars(
        ["qqq"],
        api_key="key",
        secret_key="secret",
        on_bar=bars.append,
        max_bars=1,
    )

    assert count == 1
    assert len(bars) == 1
    assert _FakeStream.instances[0].symbols == ("QQQ",)
    assert _FakeStream.instances[0].stopped


def test_alpaca_stream_timeout_stops_idle_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    import alpaca.data.live as alpaca_live_module

    class _IdleStream:
        instances: list[_IdleStream] = []

        def __init__(self, api_key: str, secret_key: str, *, feed: object) -> None:
            self.stopped = False
            self.__class__.instances.append(self)

        def subscribe_bars(self, handler: Any, *symbols: str) -> None:
            pass

        def stop(self) -> None:
            self.stopped = True

        def run(self) -> None:
            while not self.stopped:
                time.sleep(0.001)

    monkeypatch.setattr(alpaca_live_module, "StockDataStream", _IdleStream)

    with pytest.raises(AlpacaStreamTimeoutError, match="timed out"):
        stream_alpaca_stock_bars(
            ["QQQ"],
            api_key="key",
            secret_key="secret",
            on_bar=lambda bar: None,
            timeout_s=0.02,
        )

    assert _IdleStream.instances[0].stopped
