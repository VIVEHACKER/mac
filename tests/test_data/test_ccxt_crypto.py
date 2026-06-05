from __future__ import annotations

from datetime import UTC, date, datetime

from data.ingest.ccxt_crypto import fetch_ccxt_bars, normalize_crypto_symbol


def test_normalize_crypto_symbol_defaults_to_usdt() -> None:
    assert normalize_crypto_symbol("btc") == "BTC/USDT"
    assert normalize_crypto_symbol("eth-usdt") == "ETH/USDT"


def test_fetch_ccxt_bars_uses_injected_fetcher() -> None:
    timestamp = int(datetime(2026, 5, 7, tzinfo=UTC).timestamp() * 1000)

    def fetcher(symbol: str, timeframe: str, since: int, limit: int) -> list[list[float]]:
        assert symbol == "BTC/USDT"
        assert timeframe == "1d"
        assert limit == 1000
        assert since <= timestamp
        return [[timestamp, 100.0, 110.0, 90.0, 105.0, 123.0]]

    bars = fetch_ccxt_bars(
        "btc",
        start=date(2026, 5, 7),
        end=date(2026, 5, 7),
        fetch_ohlcv=fetcher,
    )

    assert len(bars) == 1
    assert bars[0].symbol == "BTC/USDT"
    assert bars[0].market == "crypto"
    assert bars[0].close == 105.0


def test_fetch_ccxt_bars_intraday_preserves_datetime() -> None:
    ts_ms = int(datetime(2026, 5, 7, 4, 0, tzinfo=UTC).timestamp() * 1000)

    def fetcher(symbol: str, timeframe: str, since: int, limit: int) -> list[list[float]]:
        assert timeframe == "4h"
        return [[ts_ms, 1.0, 2.0, 0.5, 1.5, 10.0]]

    bars = fetch_ccxt_bars(
        "btc",
        start=date(2026, 5, 7),
        end=date(2026, 5, 7),
        timeframe="4h",
        intraday=True,
        fetch_ohlcv=fetcher,
    )

    assert len(bars) == 1
    # intraday bars carry the full datetime in ts (datetime is a subclass of date,
    # so the daily catalog and existing date-based code remain unaffected)
    assert isinstance(bars[0].ts, datetime)
    assert bars[0].ts == datetime(2026, 5, 7, 4, 0)
    assert bars[0].freq == "4h"
