"""Codex Step-2 P2: `trader live-reconcile --from-store` derives the expected positions from
the order log's recorded fills (auto-reconcile), instead of a hand-typed --expected string."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from risk.halt_state import HaltStateStore
from trader import cli
from trader.execution.adapters.fake import FakeBrokerAdapter
from trader.execution.broker import AccountSnapshot, BrokerOrder, PositionSnapshot
from trader.execution.intents import OrderIntent
from trader.execution.order_store import JsonlOrderStore


def _seed_log(path: Path) -> None:
    store = JsonlOrderStore(path)
    store.record_broker_order(
        "broker_submit",
        BrokerOrder(
            broker_order_id="b1",
            client_order_id="c1",
            symbol="QQQ",
            market="us",
            side="buy",
            qty=2,
            filled_qty=2,
            status="filled",
            submitted_at=datetime(2026, 5, 12, tzinfo=UTC),
        ),
    )


def test_live_reconcile_from_store_matches(tmp_path) -> None:
    log = tmp_path / "orders.jsonl"
    halt = tmp_path / "halt.json"
    _seed_log(log)
    code = cli.main(
        [
            "live-reconcile",
            "--from-store",
            "--broker",
            "fake",
            "--fake-position",
            "QQQ:us:2:200",
            "--order-log",
            str(log),
            "--halt-state",
            str(halt),
        ]
    )
    assert code == 0
    assert not HaltStateStore(halt).current().halted


def test_live_reconcile_from_store_detects_mismatch(tmp_path) -> None:
    log = tmp_path / "orders.jsonl"
    halt = tmp_path / "halt.json"
    _seed_log(log)  # store-derived expected = QQQ:2
    code = cli.main(
        [
            "live-reconcile",
            "--from-store",
            "--broker",
            "fake",
            "--fake-position",
            "QQQ:us:1:100",  # broker holds only 1 -> mismatch vs the recorded 2
            "--order-log",
            str(log),
            "--halt-state",
            str(halt),
        ]
    )
    assert code == 2
    assert HaltStateStore(halt).current().halted


def test_live_reconcile_from_store_self_heals_crashed_after_submit(tmp_path, monkeypatch) -> None:
    # Live-readiness P0: the process crashed after the broker filled the order but before the
    # broker_submit record. The store knows only the intent, so the from-store baseline is empty
    # and would FALSELY read as drift against the broker's real position. live-reconcile must
    # first recover the in-flight order (get_order) so the baseline reflects the true fill.
    log = tmp_path / "orders.jsonl"
    halt = tmp_path / "halt.json"
    intent = OrderIntent(
        strategy="aqr",
        symbol="QQQ",
        market="us",
        side="buy",
        qty=2,
        order_type="limit",
        limit_price=200.0,
    ).normalized()
    JsonlOrderStore(log).record_intent(intent)  # recorded intent, NO broker_submit (crash)

    broker = FakeBrokerAdapter(
        account=AccountSnapshot("fake", buying_power=1e6, cash=1e6, equity=1e6),
        positions=[PositionSnapshot("QQQ", "us", 2, 400.0)],  # broker actually holds the fill
    )
    broker.orders[intent.client_order_id] = BrokerOrder(
        broker_order_id="b1",
        client_order_id=intent.client_order_id,
        symbol="QQQ",
        market="us",
        side="buy",
        qty=2,
        filled_qty=2,
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
            str(log),
            "--halt-state",
            str(halt),
        ]
    )

    assert code == 0  # recovered fill matches broker -> no false drift
    assert not HaltStateStore(halt).current().halted
    # The recovered fill is now durably in the ledger.
    assert JsonlOrderStore(log).unresolved_intent_ids() == []


def test_live_reconcile_requires_a_baseline(tmp_path) -> None:
    halt = tmp_path / "halt.json"
    code = cli.main(
        [
            "live-reconcile",
            "--broker",
            "fake",
            "--fake-position",
            "QQQ:us:1:100",
            "--halt-state",
            str(halt),
        ]
    )
    assert code == 2  # neither --expected nor --from-store given
