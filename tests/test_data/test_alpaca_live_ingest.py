from __future__ import annotations

from datetime import UTC, datetime

from data.ingest.alpaca_live import alpaca_latest_bars_to_price_bars


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
