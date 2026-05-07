from __future__ import annotations

from datetime import date

import pytest

from data.ingest.yahoo_options import YahooOptionChainError, fetch_yahoo_option_quotes


def test_fetch_yahoo_option_quotes_maps_calls_and_puts() -> None:
    fetched = fetch_yahoo_option_quotes(
        "SPY",
        asof_date=date(2026, 5, 8),
        target_days=30,
        ticker_factory=lambda _symbol: _FakeTicker(),
    )

    assert fetched.source == "yahoo-options:SPY"
    assert fetched.expirations == (date(2026, 5, 28), date(2026, 6, 17))
    assert len(fetched.quotes) == 4
    first = fetched.quotes[0]
    assert first.expiration == date(2026, 5, 28)
    assert first.strike == 100
    assert first.call_bid == 3.9
    assert first.put_ask == 4.1
    assert first.call_last_trade == date(2026, 5, 8)


def test_fetch_yahoo_option_quotes_rejects_missing_expirations() -> None:
    with pytest.raises(YahooOptionChainError, match="no option expirations"):
        fetch_yahoo_option_quotes(
            "SPY",
            asof_date=date(2026, 5, 8),
            ticker_factory=lambda _symbol: _EmptyTicker(),
        )


class _FakeFrame:
    def __init__(self, records: list[dict[str, object]]) -> None:
        self._records = records

    def to_dict(self, orient: str) -> list[dict[str, object]]:
        assert orient == "records"
        return self._records


class _FakeChain:
    def __init__(self, expiration: str) -> None:
        scale = 1.0 if expiration == "2026-05-28" else 1.1
        self.calls = _FakeFrame(
            [
                {"strike": 100, "bid": 3.9 * scale, "ask": 4.1 * scale, "lastTradeDate": "2026-05-08"},
                {"strike": 105, "bid": 1.9 * scale, "ask": 2.1 * scale, "lastTradeDate": "2026-05-08"},
            ]
        )
        self.puts = _FakeFrame(
            [
                {"strike": 100, "bid": 3.8 * scale, "ask": 4.1 * scale, "lastTradeDate": "2026-05-08"},
                {"strike": 105, "bid": 7.0 * scale, "ask": 7.2 * scale, "lastTradeDate": "2026-05-08"},
            ]
        )


class _FakeTicker:
    options = ("2026-05-28", "2026-06-17")

    def option_chain(self, expiration: str) -> _FakeChain:
        return _FakeChain(expiration)


class _EmptyTicker:
    options: tuple[str, ...] = ()
