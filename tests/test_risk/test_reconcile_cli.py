"""Codex Step-2 P2: `trader live-reconcile --from-store` derives the expected positions from
the order log's recorded fills (auto-reconcile), instead of a hand-typed --expected string."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from risk.halt_state import HaltStateStore
from trader import cli
from trader.execution.broker import BrokerOrder
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
