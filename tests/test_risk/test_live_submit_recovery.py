"""Live-readiness P0 wiring: `live-submit` must (1) self-heal any in-flight orders via
reconcile_in_flight BEFORE sending a new order (so the ledger reflects prior crashed/uncertain
fills), and (2) pass a FillPoll so an async accepted/filled_qty=0 submit is polled to its real
fill instead of leaving a stale non-terminal snapshot. The full readiness gate and the broker
are injected here so the wiring is exercised without live credentials.
"""

from __future__ import annotations

from datetime import UTC, datetime

from trader import cli
from trader.execution.adapters.fake import FakeBrokerAdapter
from trader.execution.broker import AccountSnapshot, BrokerOrder
from trader.execution.intents import OrderIntent
from trader.execution.order_store import JsonlOrderStore
from trader.execution.runner import ExecutionResult, FillPoll


def test_live_submit_self_heals_in_flight_and_wires_fill_poll(tmp_path, monkeypatch) -> None:
    order_log = tmp_path / "orders.jsonl"
    # A prior order crashed after submit: intent recorded, no broker_submit event.
    stale = OrderIntent(
        strategy="aqr",
        symbol="MSFT",
        market="us",
        side="buy",
        qty=3,
        order_type="limit",
        limit_price=300.0,
    ).normalized()
    JsonlOrderStore(order_log).record_intent(stale)

    broker = FakeBrokerAdapter(account=AccountSnapshot("fake", 1e6, 1e6, 1e6))
    broker.orders[stale.client_order_id] = BrokerOrder(
        broker_order_id="b1",
        client_order_id=stale.client_order_id,
        symbol="MSFT",
        market="us",
        side="buy",
        qty=3,
        filled_qty=3,
        status="filled",
        submitted_at=datetime(2026, 6, 5, tzinfo=UTC),
    )

    captured: dict = {}

    def _capture(intents, **kwargs):
        captured["kwargs"] = kwargs
        return [ExecutionResult(intents[0].client_order_id, "submit", "accepted")]

    monkeypatch.setattr(cli, "_live_readiness_issues", lambda **k: [])
    monkeypatch.setattr(cli, "_live_broker_adapter", lambda *a, **k: broker)
    monkeypatch.setattr(cli, "process_order_intents", _capture)

    code = cli.main(
        [
            "live-submit",
            "QQQ",
            "--side",
            "buy",
            "--qty",
            "2",
            "--price",
            "200",
            "--broker",
            "fake",
            "--order-log",
            str(order_log),
            "--halt-state",
            str(tmp_path / "halt.json"),
            "--equity-state",
            str(tmp_path / "equity.json"),
            "--catalog-db",
            str(tmp_path / "cat.duckdb"),
        ]
    )

    assert code == 0
    # (1) self-heal ran before the new order: the crashed fill is now in the ledger.
    assert JsonlOrderStore(order_log).unresolved_intent_ids() == []
    # (2) the live path now polls for the real fill.
    assert isinstance(captured["kwargs"].get("fill_poll"), FillPoll)
