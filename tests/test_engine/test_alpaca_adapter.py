"""Unit tests for AlpacaBrokerAdapter error classification, timeout, and read retries.

These exercise the live broker path WITHOUT credentials or network by injecting a fake
client and raising REAL ``alpaca.common.exceptions.APIError`` instances — the gap the
readiness audit flagged: previously only FakeBrokerAdapter ever raised the broker error
contracts, so the runner's halt latch was dead code on the real adapter.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from alpaca.common.exceptions import APIError

from trader.execution.adapters.alpaca import AlpacaBrokerAdapter
from trader.execution.broker import BrokerRejectedError, BrokerTemporaryError
from trader.execution.intents import OrderIntent


def _api_error(status: int, message: str = "boom") -> APIError:
    """A real APIError whose ``.status_code`` property resolves to ``status``."""
    http_error = SimpleNamespace(response=SimpleNamespace(status_code=status))
    return APIError(json.dumps({"code": status * 100, "message": message}), http_error)


def _raise_then(exc: BaseException, times: int, then: object):
    def _effect(attempt: int) -> object:
        if attempt <= times:
            raise exc
        return then

    return _effect


def _order(status: str = "filled", filled_qty: float = 2.0) -> SimpleNamespace:
    return SimpleNamespace(
        id="o1",
        client_order_id="cid",
        symbol="QQQ",
        side="buy",
        qty=2.0,
        filled_qty=filled_qty,
        status=status,
        submitted_at=datetime(2026, 5, 12, tzinfo=UTC),
        filled_avg_price=100.0,
    )


def _account() -> SimpleNamespace:
    return SimpleNamespace(
        id="acc",
        buying_power=10_000,
        cash=10_000,
        equity=10_000,
        trading_blocked=False,
        account_blocked=False,
        pattern_day_trader=False,
        daytrade_count=0,
        currency="USD",
        last_equity=10_000,
    )


def _intent() -> OrderIntent:
    return OrderIntent(
        strategy="approved-etf",
        symbol="qqq",
        market="us",
        side="buy",
        qty=2,
        order_type="limit",
        limit_price=100,
        rebalance_key="2026-05-12",
        asof_ts=datetime(2026, 5, 12, tzinfo=UTC),
    )


class _FakeClient:
    """A stand-in for alpaca-py TradingClient. Each effect may be a value to return, an
    exception instance to raise, or a callable(attempt)->value-or-raise."""

    def __init__(self) -> None:
        self.submit_effect: object = None
        self.account_effect: object = None
        self.positions_effect: object = None
        self.get_order_effect: object = None
        self.submit_calls = 0
        self.account_calls = 0
        self.positions_calls = 0
        self.get_order_calls = 0

    @staticmethod
    def _resolve(effect: object, attempt: int) -> object:
        if callable(effect):
            return effect(attempt)
        if isinstance(effect, BaseException):
            raise effect
        return effect

    def submit_order(self, request: object) -> object:
        self.submit_calls += 1
        return self._resolve(self.submit_effect, self.submit_calls)

    def get_account(self) -> object:
        self.account_calls += 1
        return self._resolve(self.account_effect, self.account_calls)

    def get_all_positions(self) -> object:
        self.positions_calls += 1
        return self._resolve(self.positions_effect, self.positions_calls)

    def get_order_by_client_id(self, client_order_id: str) -> object:
        self.get_order_calls += 1
        return self._resolve(self.get_order_effect, self.get_order_calls)


def _adapter(client: _FakeClient, **kwargs: object) -> AlpacaBrokerAdapter:
    kwargs.setdefault("sleep", lambda _seconds: None)  # no real backoff in tests
    return AlpacaBrokerAdapter(client=client, **kwargs)  # type: ignore[arg-type]


# --- submit_order classification (the critical safety path) ---


def test_submit_4xx_is_rejected_and_not_retried() -> None:
    client = _FakeClient()
    client.submit_effect = _api_error(403, "insufficient buying power")
    with pytest.raises(BrokerRejectedError):
        _adapter(client).submit_order(_intent())
    assert client.submit_calls == 1  # a definite rejection is never retried


def test_submit_5xx_is_temporary_and_not_retried() -> None:
    client = _FakeClient()
    client.submit_effect = _api_error(503)
    with pytest.raises(BrokerTemporaryError):
        _adapter(client).submit_order(_intent())
    assert client.submit_calls == 1  # a submit is NEVER retried (it may already be placed)


def test_submit_rate_limit_429_is_temporary() -> None:
    client = _FakeClient()
    client.submit_effect = _api_error(429, "rate limited")
    with pytest.raises(BrokerTemporaryError):
        _adapter(client).submit_order(_intent())


def test_submit_network_error_is_temporary() -> None:
    client = _FakeClient()
    client.submit_effect = ConnectionError("connection reset by peer")
    with pytest.raises(BrokerTemporaryError):
        _adapter(client).submit_order(_intent())


def test_submit_timeout_is_temporary() -> None:
    client = _FakeClient()
    client.submit_effect = lambda _attempt: (time.sleep(0.5), _order())[1]
    with pytest.raises(BrokerTemporaryError, match="timed out"):
        _adapter(client, timeout_s=0.05).submit_order(_intent())


def test_submit_success_maps_order() -> None:
    client = _FakeClient()
    client.submit_effect = _order()
    order = _adapter(client).submit_order(_intent())
    assert order.symbol == "QQQ"
    assert order.status == "filled"
    assert order.filled_qty == 2.0


# --- read calls (account/positions/order) retry on transient errors ---


def test_get_account_retries_then_succeeds() -> None:
    client = _FakeClient()
    client.account_effect = _raise_then(_api_error(503), 2, _account())
    snapshot = _adapter(client, read_retries=2).get_account()
    assert snapshot.equity == 10_000.0
    assert client.account_calls == 3  # two transient failures, then success


def test_get_account_temporary_exhausts_retries() -> None:
    client = _FakeClient()
    client.account_effect = _api_error(503)
    with pytest.raises(BrokerTemporaryError):
        _adapter(client, read_retries=2).get_account()
    assert client.account_calls == 3


def test_get_account_4xx_is_rejected_not_retried() -> None:
    client = _FakeClient()
    client.account_effect = _api_error(401, "unauthorized")
    with pytest.raises(BrokerRejectedError):
        _adapter(client, read_retries=2).get_account()
    assert client.account_calls == 1  # bad credentials is a definite error, not retried


def test_list_positions_maps_and_retries() -> None:
    position = SimpleNamespace(symbol="qqq", qty=3.0, market_value=300.0, avg_entry_price=100.0)
    client = _FakeClient()
    client.positions_effect = _raise_then(_api_error(500), 1, [position])
    rows = _adapter(client, read_retries=1).list_positions()
    assert client.positions_calls == 2
    assert rows[0].symbol == "QQQ"
    assert rows[0].qty == 3.0


# --- get_order: 404 is a legitimate None; other failures must NOT be swallowed ---


def test_get_order_not_found_returns_none() -> None:
    client = _FakeClient()
    client.get_order_effect = _api_error(404, "order not found")
    assert _adapter(client).get_order("cid") is None


def test_get_order_network_error_is_temporary_not_swallowed() -> None:
    client = _FakeClient()
    client.get_order_effect = _api_error(503)
    with pytest.raises(BrokerTemporaryError):
        _adapter(client, read_retries=1).get_order("cid")
    assert client.get_order_calls == 2  # retried; not silently turned into None


def test_get_order_success_maps_partial_fill() -> None:
    client = _FakeClient()
    client.get_order_effect = _order(status="partially_filled", filled_qty=1.0)
    order = _adapter(client).get_order("cid")
    assert order is not None
    assert order.status == "partially_filled"
    assert order.filled_qty == 1.0
