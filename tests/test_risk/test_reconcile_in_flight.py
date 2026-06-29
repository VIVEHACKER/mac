"""Settlement-integrity recovery (live-readiness audit P0): after a crash between
broker.submit_order and the broker_submit record, or after an uncertain submit, the order
may be live at the broker while the local ledger is blind to it. `reconcile_in_flight`
queries the broker by client_order_id for every recorded-but-unresolved intent and brings
the true terminal state back into the order store, so the ledger stops lying about positions.
"""

from __future__ import annotations

from datetime import UTC, datetime

from trader.execution.adapters.fake import FakeBrokerAdapter
from trader.execution.broker import BrokerError, BrokerOrder
from trader.execution.intents import OrderIntent
from trader.execution.order_store import JsonlOrderStore
from trader.execution.reconciler import expected_positions_from_store, reconcile_in_flight


def _intent(symbol: str = "AAPL", side: str = "buy", qty: float = 10.0) -> OrderIntent:
    return OrderIntent(
        strategy="aqr",
        symbol=symbol,
        market="us",
        side=side,
        qty=qty,
        order_type="limit",
        limit_price=100.0,
    ).normalized()


def _filled_order(intent: OrderIntent, *, filled_qty: float | None = None) -> BrokerOrder:
    qty = intent.qty if filled_qty is None else filled_qty
    return BrokerOrder(
        broker_order_id="b-1",
        client_order_id=intent.client_order_id,
        symbol=intent.symbol,
        market=intent.market,
        side=intent.side,
        qty=intent.qty,
        filled_qty=qty,
        status="filled",
        submitted_at=datetime.now(UTC),
        filled_avg_price=100.0,
    )


def test_reconcile_in_flight_records_fill_for_intent_crashed_after_submit(tmp_path) -> None:
    # The intent was recorded, then the process died after the broker accepted+filled it but
    # before the broker_submit event was written. The store knows the intent, not the fill.
    store = JsonlOrderStore(tmp_path / "orders.jsonl")
    intent = _intent()
    store.record_intent(intent)
    assert expected_positions_from_store(store) == {}  # ledger believes it holds nothing

    broker = FakeBrokerAdapter()
    broker.orders[intent.client_order_id] = _filled_order(intent)  # broker actually has the fill

    resolutions = reconcile_in_flight(store, broker)

    assert [r.client_order_id for r in resolutions] == [intent.client_order_id]
    assert resolutions[0].outcome == "resolved_terminal"
    # The real fill is now in the ledger -> the reconcile baseline reflects the true position.
    assert expected_positions_from_store(store) == {("AAPL", "us"): 10.0}


def test_reconcile_in_flight_marks_not_found_when_broker_has_no_such_order(tmp_path) -> None:
    # The process crashed BEFORE submit (or an uncertain submit never reached the broker), so
    # the broker has no order for this client_order_id. Must resolve cleanly, not crash, and
    # leave no phantom position in the ledger.
    store = JsonlOrderStore(tmp_path / "orders.jsonl")
    intent = _intent()
    store.record_intent(intent)
    broker = FakeBrokerAdapter()  # broker.orders is empty -> get_order returns None

    resolutions = reconcile_in_flight(store, broker)

    assert resolutions[0].outcome == "not_found"
    assert expected_positions_from_store(store) == {}
    # A not-found order is resolved: it is not re-queried on the next cycle.
    assert store.unresolved_intent_ids() == []


def test_reconcile_in_flight_leaves_intent_unresolved_when_broker_read_is_uncertain(
    tmp_path,
) -> None:
    # A transient broker error during get_order must NOT be swallowed as resolved: the order
    # state is still unknown, so the intent stays in the unresolved set for the next cycle.
    store = JsonlOrderStore(tmp_path / "orders.jsonl")
    intent = _intent()
    store.record_intent(intent)

    class UnreachableBroker(FakeBrokerAdapter):
        def get_order(self, client_order_id: str) -> BrokerOrder | None:
            raise BrokerError("broker temporarily unreachable")

    resolutions = reconcile_in_flight(store, UnreachableBroker())

    assert resolutions[0].outcome == "uncertain"
    assert store.unresolved_intent_ids() == [intent.client_order_id]


def test_reconcile_in_flight_records_working_order_but_keeps_it_unresolved(tmp_path) -> None:
    # A non-terminal (accepted, unfilled) order is recorded so the ledger reflects the latest
    # snapshot, but it stays unresolved so a later cycle re-checks for the fill.
    store = JsonlOrderStore(tmp_path / "orders.jsonl")
    intent = _intent()
    store.record_intent(intent)
    broker = FakeBrokerAdapter()
    broker.orders[intent.client_order_id] = BrokerOrder(
        broker_order_id="b-1",
        client_order_id=intent.client_order_id,
        symbol=intent.symbol,
        market=intent.market,
        side=intent.side,
        qty=intent.qty,
        filled_qty=0.0,
        status="accepted",
        submitted_at=datetime.now(UTC),
    )

    resolutions = reconcile_in_flight(store, broker)

    assert resolutions[0].outcome == "still_working"
    assert store.unresolved_intent_ids() == [intent.client_order_id]


def test_reconcile_in_flight_skips_already_resolved_intents(tmp_path) -> None:
    # An intent whose fill is already recorded must NOT be re-queried (no duplicate work, and
    # a flaky get_order cannot corrupt an already-settled order).
    store = JsonlOrderStore(tmp_path / "orders.jsonl")
    intent = _intent()
    store.record_intent(intent)
    store.record_broker_order("broker_submit", _filled_order(intent))

    class ExplodingBroker(FakeBrokerAdapter):
        def get_order(self, client_order_id: str) -> BrokerOrder | None:
            raise AssertionError("resolved intent must not be re-queried")

    assert store.unresolved_intent_ids() == []
    assert reconcile_in_flight(store, ExplodingBroker()) == []
