"""Live-readiness P1 (codex adversarial review): the live path must FAIL CLOSED on unresolved
in-flight orders.

  (1) `live-submit --submit` must NOT place a new order while a prior intent is still working at
      the broker or its state is unknown (double-fill / unhedged exposure risk).
  (2) `live-reconcile --from-store` must exit non-zero when a recovery is uncertain/still-working,
      even with no position mismatch — a "clean" exit on an incomplete baseline is guesswork.

Shadow mode (no --submit) is intentionally exempt from (1): it places no real order.
"""

from __future__ import annotations

from datetime import UTC, datetime

from trader import cli
from trader.execution.adapters.fake import FakeBrokerAdapter
from trader.execution.broker import AccountSnapshot, BrokerOrder, PositionSnapshot
from trader.execution.intents import OrderIntent
from trader.execution.order_store import JsonlOrderStore
from trader.execution.runner import ExecutionResult


def _record_still_working(order_log) -> FakeBrokerAdapter:
    """A prior intent whose broker order is non-terminal (accepted, unfilled) -> still_working."""
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
        filled_qty=0.0,
        status="accepted",  # non-terminal -> reconcile -> "still_working" (unresolved)
        submitted_at=datetime(2026, 6, 5, tzinfo=UTC),
    )
    return broker


def _submit_argv(order_log, tmp_path, *, submit: bool) -> list[str]:
    argv = [
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
    if submit:
        argv.append("--submit")
    return argv


def test_live_submit_blocks_when_prior_intent_unresolved(tmp_path, monkeypatch) -> None:
    order_log = tmp_path / "orders.jsonl"
    broker = _record_still_working(order_log)
    called = {"n": 0}

    def _spy(intents, **kwargs):
        called["n"] += 1
        return [ExecutionResult(intents[0].client_order_id, "submit", "accepted")]

    monkeypatch.setattr(cli, "_live_readiness_issues", lambda **k: [])
    monkeypatch.setattr(cli, "_live_broker_adapter", lambda *a, **k: broker)
    monkeypatch.setattr(cli, "process_order_intents", _spy)

    code = cli.main(_submit_argv(order_log, tmp_path, submit=True))

    assert code == 2
    # The new order must never be sized/submitted while prior exposure is unresolved.
    assert called["n"] == 0


def test_live_submit_shadow_not_blocked_by_unresolved(tmp_path, monkeypatch) -> None:
    order_log = tmp_path / "orders.jsonl"
    broker = _record_still_working(order_log)
    called = {"n": 0}

    def _spy(intents, **kwargs):
        called["n"] += 1
        return [ExecutionResult(intents[0].client_order_id, "shadow", "dry-run")]

    monkeypatch.setattr(cli, "_live_readiness_issues", lambda **k: [])
    monkeypatch.setattr(cli, "_live_broker_adapter", lambda *a, **k: broker)
    monkeypatch.setattr(cli, "process_order_intents", _spy)

    # No --submit: shadow inspection is allowed even with an unresolved prior order (no real order).
    cli.main(_submit_argv(order_log, tmp_path, submit=False))

    assert called["n"] == 1


def test_live_reconcile_fails_when_in_flight_unresolved(tmp_path, monkeypatch) -> None:
    order_log = tmp_path / "orders.jsonl"
    broker = _record_still_working(order_log)  # broker has no positions -> no mismatch
    monkeypatch.setattr(cli, "_live_broker_adapter", lambda *a, **k: broker)

    code = cli.main(
        [
            "live-reconcile",
            "--from-store",
            "--broker",
            "fake",
            "--order-log",
            str(order_log),
            "--halt-state",
            str(tmp_path / "halt.json"),
        ]
    )

    # No position mismatch, but a still-working order leaves the baseline incomplete -> exit 2.
    assert code == 2


def test_live_reconcile_clean_when_all_recoveries_terminal(tmp_path, monkeypatch) -> None:
    order_log = tmp_path / "orders.jsonl"
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
    broker = FakeBrokerAdapter(
        account=AccountSnapshot("fake", 1e6, 1e6, 1e6),
        # Terminal fill that matches the broker position -> resolved_terminal, no mismatch.
        positions=[PositionSnapshot("MSFT", "us", 3.0, 600.0)],
    )
    broker.orders[stale.client_order_id] = BrokerOrder(
        broker_order_id="b1",
        client_order_id=stale.client_order_id,
        symbol="MSFT",
        market="us",
        side="buy",
        qty=3,
        filled_qty=3.0,
        status="filled",
        submitted_at=datetime(2026, 6, 5, tzinfo=UTC),
    )
    monkeypatch.setattr(cli, "_live_broker_adapter", lambda *a, **k: broker)

    code = cli.main(
        [
            "live-reconcile",
            "--from-store",
            "--broker",
            "fake",
            "--order-log",
            str(order_log),
            "--halt-state",
            str(tmp_path / "halt.json"),
        ]
    )

    assert code == 0
