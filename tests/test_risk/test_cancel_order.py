"""Order cancel path (live-readiness audit P2: no cancel/replace existed, so a working limit
order that drifted away from the market could never be recalled). Contract:

  * ``BrokerAdapter.cancel_order(client_order_id)`` — cancels a WORKING order and returns the
    refreshed snapshot; a terminal order is a no-op (returns as-is); an unknown order returns
    ``None``. The cancel-vs-fill race (order fills between fetch and cancel) resolves to the
    terminal order, not an error.
  * The ledger records the cancel as a ``broker_cancel`` broker-order event, so a canceled
    intent counts as resolved and a partial fill's ``filled_qty`` still reconciles.
  * ``trader live-cancel`` is the operator surface. It deliberately requires NO live-readiness
    gate: canceling is risk-reducing and must stay available even while halted.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

from alpaca.common.exceptions import APIError

from trader import cli
from trader.execution.adapters.alpaca import AlpacaBrokerAdapter
from trader.execution.adapters.fake import FakeBrokerAdapter
from trader.execution.broker import BrokerOrder, BrokerTemporaryError
from trader.execution.intents import OrderIntent
from trader.execution.order_store import JsonlOrderStore


def _api_error(status: int, message: str = "boom") -> APIError:
    http_error = SimpleNamespace(response=SimpleNamespace(status_code=status))
    return APIError(json.dumps({"code": status * 100, "message": message}), http_error)


def _intent(symbol: str = "AAPL") -> OrderIntent:
    return OrderIntent(
        strategy="s",
        symbol=symbol,
        market="us",
        side="buy",
        qty=10,
        order_type="limit",
        limit_price=100.0,
    ).normalized()


def _broker_order(intent: OrderIntent, *, status: str, filled_qty: float = 0.0) -> BrokerOrder:
    return BrokerOrder(
        broker_order_id="b-1",
        client_order_id=intent.client_order_id,
        symbol=intent.symbol,
        market=intent.market,
        side=intent.side,
        qty=intent.qty,
        filled_qty=filled_qty,
        status=status,
        submitted_at=datetime.now(UTC),
    )


def _raw_order(intent: OrderIntent, *, status: str, filled_qty: float = 0.0) -> SimpleNamespace:
    return SimpleNamespace(
        id="b-1",
        client_order_id=intent.client_order_id,
        symbol=intent.symbol,
        side=intent.side,
        qty=intent.qty,
        filled_qty=filled_qty,
        status=status,
        submitted_at=datetime.now(UTC),
        filled_avg_price=None,
    )


# ---------------------------------------------------------------- FakeBrokerAdapter


def test_fake_cancel_working_order_preserves_partial_fill() -> None:
    intent = _intent()
    broker = FakeBrokerAdapter()
    broker.orders[intent.client_order_id] = _broker_order(intent, status="accepted", filled_qty=4)

    result = broker.cancel_order(intent.client_order_id)

    assert result is not None and result.status == "canceled"
    assert result.filled_qty == 4  # the partial fill survives the cancel
    refreshed = broker.get_order(intent.client_order_id)
    assert refreshed is not None and refreshed.status == "canceled"


def test_fake_cancel_unknown_order_returns_none() -> None:
    assert FakeBrokerAdapter().cancel_order("trd-nope") is None


def test_fake_cancel_terminal_order_is_noop() -> None:
    intent = _intent()
    broker = FakeBrokerAdapter()
    broker.orders[intent.client_order_id] = _broker_order(intent, status="filled", filled_qty=10)

    result = broker.cancel_order(intent.client_order_id)

    assert result is not None and result.status == "filled"  # unchanged, no error


# ---------------------------------------------------------------- AlpacaBrokerAdapter


def test_alpaca_cancel_calls_sdk_and_returns_refreshed_snapshot() -> None:
    intent = _intent()
    canceled_ids: list[str] = []
    states = iter([_raw_order(intent, status="accepted"), _raw_order(intent, status="canceled")])
    client = SimpleNamespace(
        get_order_by_client_id=lambda cid: next(states),
        cancel_order_by_id=lambda oid: canceled_ids.append(str(oid)),
    )
    adapter = AlpacaBrokerAdapter(client=client, timeout_s=0)

    result = adapter.cancel_order(intent.client_order_id)

    assert canceled_ids == ["b-1"]  # cancel targeted the broker order id
    assert result is not None and result.status == "canceled"


def test_alpaca_cancel_race_with_fill_resolves_to_terminal_order() -> None:
    # The order fills between our fetch and the cancel call: Alpaca answers 422 (not
    # cancelable). That is a benign race — re-fetch and report the terminal fill, no error.
    intent = _intent()
    states = iter(
        [
            _raw_order(intent, status="accepted"),
            _raw_order(intent, status="filled", filled_qty=10),
        ]
    )

    def cancel_raises(oid):
        raise _api_error(422, "order is not cancelable")

    client = SimpleNamespace(
        get_order_by_client_id=lambda cid: next(states),
        cancel_order_by_id=cancel_raises,
    )
    adapter = AlpacaBrokerAdapter(client=client, timeout_s=0)

    result = adapter.cancel_order(intent.client_order_id)

    assert result is not None and result.status == "filled" and result.filled_qty == 10


def test_alpaca_cancel_unknown_order_returns_none() -> None:
    def not_found(cid):
        raise _api_error(404, "order not found")

    client = SimpleNamespace(get_order_by_client_id=not_found)
    adapter = AlpacaBrokerAdapter(client=client, timeout_s=0)
    assert adapter.cancel_order("trd-nope") is None


def test_paper_broker_cancel_conforms_to_contract() -> None:
    # codex P2: every drop-in BrokerAdapter must implement cancel_order. PaperBroker fills
    # instantly, so there is never a working order to recall: known -> terminal no-op,
    # unknown -> None (same contract as the other adapters).
    from engine.paper import PaperBroker

    broker = PaperBroker(100_000.0, marks={"AAPL": 100.0})
    intent = _intent()
    order = broker.submit_order(intent)
    assert order.terminal  # instant fill

    result = broker.cancel_order(intent.client_order_id)
    assert result is not None and result.status == order.status  # no-op, truthfully reported
    assert broker.cancel_order("trd-nope") is None


def test_manual_broker_cancel_conforms_to_contract_and_directs_to_external() -> None:
    # Adding cancel_order to the BrokerAdapter protocol requires ManualBrokerAdapter (operator-
    # attested external broker) to implement it too. It cannot cancel via API — mirror its
    # submit_order: reject with guidance to cancel at the external broker (BrokerRejectedError,
    # which live-cancel surfaces as exit 1, not a crash).
    import pytest

    from trader.execution.adapters.manual import ManualBrokerAdapter
    from trader.execution.broker import AccountSnapshot, BrokerRejectedError

    broker = ManualBrokerAdapter(account=AccountSnapshot("manual", 0.0, 0.0, 0.0))
    with pytest.raises(BrokerRejectedError, match="external broker"):
        broker.cancel_order("trd-anything")


# ---------------------------------------------------------------- ledger integration


def test_broker_cancel_event_resolves_intent_and_updates_baseline(tmp_path) -> None:
    intent = _intent()
    store = JsonlOrderStore(tmp_path / "orders.jsonl")
    store.record_intent(intent)
    assert store.unresolved_intent_ids() == [intent.client_order_id]

    store.record_broker_order(
        "broker_cancel", _broker_order(intent, status="canceled", filled_qty=4)
    )

    assert store.unresolved_intent_ids() == []  # canceled = resolved
    latest = store.latest_broker_orders()[intent.client_order_id]
    assert latest["status"] == "canceled" and latest["filled_qty"] == 4  # partial fill reconciles


# ---------------------------------------------------------------- CLI live-cancel


def _run_cli_cancel(tmp_path, monkeypatch, broker, capsys) -> tuple[int, str]:
    monkeypatch.setattr(cli, "_live_broker_adapter", lambda *a, **k: broker)
    code = cli.main(
        [
            "live-cancel",
            _intent().client_order_id,
            "--broker",
            "fake",
            "--order-log",
            str(tmp_path / "orders.jsonl"),
        ]
    )
    return code, capsys.readouterr().out


def test_live_cancel_records_ledger_event_and_exits_zero(tmp_path, monkeypatch, capsys) -> None:
    intent = _intent()
    broker = FakeBrokerAdapter()
    broker.orders[intent.client_order_id] = _broker_order(intent, status="accepted", filled_qty=0)
    JsonlOrderStore(tmp_path / "orders.jsonl").record_intent(intent)

    code, out = _run_cli_cancel(tmp_path, monkeypatch, broker, capsys)

    assert code == 0
    assert "canceled" in out
    assert JsonlOrderStore(tmp_path / "orders.jsonl").unresolved_intent_ids() == []


def test_live_cancel_not_found_exits_two(tmp_path, monkeypatch, capsys) -> None:
    code, out = _run_cli_cancel(tmp_path, monkeypatch, FakeBrokerAdapter(), capsys)
    assert code == 2
    assert "not" in out.lower()  # not-found messaging


def test_live_cancel_uncertain_broker_exits_three_and_logs(tmp_path, monkeypatch, capsys) -> None:
    class FlakyBroker(FakeBrokerAdapter):
        def cancel_order(self, client_order_id: str) -> BrokerOrder | None:
            raise BrokerTemporaryError("network down; cancel state unknown")

    code, out = _run_cli_cancel(tmp_path, monkeypatch, FlakyBroker(), capsys)

    assert code == 3
    assert "uncertain" in out.lower()
    rows = JsonlOrderStore(tmp_path / "orders.jsonl").rows()
    assert any(
        (row.get("payload") or {}).get("event_type") == "broker_cancel_uncertain" for row in rows
    )


def test_live_cancel_terminal_order_reports_noop(tmp_path, monkeypatch, capsys) -> None:
    intent = _intent()
    broker = FakeBrokerAdapter()
    broker.orders[intent.client_order_id] = _broker_order(intent, status="filled", filled_qty=10)

    code, out = _run_cli_cancel(tmp_path, monkeypatch, broker, capsys)

    assert code == 0  # already terminal = nothing to recall, honest no-op
    assert "filled" in out
