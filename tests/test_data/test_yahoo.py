from __future__ import annotations

from data.ingest.yahoo import _bar_from_quote


def test_bar_from_quote_uses_adjusted_close_ratio() -> None:
    bar = _bar_from_quote(
        symbol="SPY",
        market="us",
        source_symbol="SPY",
        timestamp=1_735_689_600,
        quote_data={
            "open": [100.0],
            "high": [110.0],
            "low": [90.0],
            "close": [100.0],
            "volume": [1_000],
        },
        adjusted_data={"adjclose": [50.0]},
        index=0,
        interval="1d",
        currency="USD",
        source="https://example.test",
    )

    assert bar is not None
    assert bar.open == 50.0
    assert bar.high == 55.0
    assert bar.low == 45.0
    assert bar.close == 50.0
