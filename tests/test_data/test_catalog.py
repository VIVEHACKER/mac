from __future__ import annotations

from datetime import date

from data.catalog import MarketDataCatalog
from data.models import PriceBar


def test_catalog_round_trips_price_bars(tmp_path) -> None:
    catalog = MarketDataCatalog(tmp_path / "test.duckdb")
    bars = [
        PriceBar("MSFT", "us", "MSFT", date(2025, 1, 2), 10.0, 11.0, 9.0, 10.5, 100),
        PriceBar("MSFT", "us", "MSFT", date(2025, 1, 3), 10.5, 12.0, 10.0, 11.5, 120),
    ]

    assert catalog.put_bars(bars) == 2

    loaded = catalog.get_bars("MSFT", start=date(2025, 1, 3))
    assert len(loaded) == 1
    assert loaded[0].close == 11.5
    assert loaded[0].ts == date(2025, 1, 3)


def test_catalog_replaces_duplicate_bar(tmp_path) -> None:
    catalog = MarketDataCatalog(tmp_path / "test.duckdb")
    first = PriceBar("MSFT", "us", "MSFT", date(2025, 1, 2), 10.0, 11.0, 9.0, 10.5, 100)
    replacement = PriceBar(
        "MSFT", "us", "MSFT", date(2025, 1, 2), 10.0, 11.0, 9.0, 10.75, 100
    )

    catalog.put_bars([first])
    catalog.put_bars([replacement])

    loaded = catalog.get_bars("MSFT")
    assert len(loaded) == 1
    assert loaded[0].close == 10.75
