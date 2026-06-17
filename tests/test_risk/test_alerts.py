"""Step 4: structured logging + alerting on critical execution events.

Readiness-audit gap: kill-switch halts / uncertain broker states had no alert path. The
runner now emits a structured log AND (if configured) an external alert on the critical
events, and a broken notifier must never break the trading path.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from risk.halt_state import HaltStateStore
from risk.policy import RiskPolicy
from trader.execution.adapters.fake import FakeBrokerAdapter
from trader.execution.broker import AccountSnapshot, BrokerTemporaryError, PositionSnapshot
from trader.execution.intents import OrderIntent
from trader.execution.order_store import JsonlOrderStore
from trader.execution.runner import process_order_intents
from trader.operations.observability import (
    LoggingNotifier,
    NullNotifier,
    WebhookNotifier,
    get_logger,
    log_event,
)


def _intent(key: str = "2026-05-12") -> OrderIntent:
    return OrderIntent(
        strategy="approved-etf",
        symbol="qqq",
        market="us",
        side="buy",
        qty=2,
        order_type="limit",
        limit_price=100,
        rebalance_key=key,
        asof_ts=datetime(2026, 5, 12, tzinfo=UTC),
    ).normalized()


class _RecordingNotifier:
    def __init__(self) -> None:
        self.alerts: list[tuple[str, str, str, dict[str, Any]]] = []

    def notify(self, *, level: str, event: str, message: str, fields: Mapping[str, Any]) -> None:
        self.alerts.append((level, event, message, dict(fields)))

    def events(self) -> list[str]:
        return [event for _level, event, _msg, _fields in self.alerts]


class _BrokenNotifier:
    def notify(self, *, level: str, event: str, message: str, fields: Mapping[str, Any]) -> None:
        raise RuntimeError("notifier backend is down")


class _ReadFailsBroker:
    def get_account(self) -> AccountSnapshot:
        raise BrokerTemporaryError("network down reading account")

    def list_positions(self) -> list[PositionSnapshot]:
        return []

    def submit_order(self, intent: OrderIntent) -> object:
        raise AssertionError("must not submit when reads fail")

    def get_order(self, client_order_id: str) -> object:
        return None


def _run(broker, notifier, tmp_path, *, reference_equity=10_000.0):
    store = JsonlOrderStore(tmp_path / "orders.jsonl")
    halt = HaltStateStore(tmp_path / "halt.json")
    results = process_order_intents(
        [_intent()],
        broker=broker,
        store=store,
        halt_store=halt,
        policy=RiskPolicy(max_order_notional=1_000, max_symbol_weight=1.0),
        marks={"QQQ": 100},
        dry_run=False,
        reference_equity=reference_equity,
        notifier=notifier,
    )
    return results, halt


def test_kill_switch_latch_fires_alert(tmp_path) -> None:
    # equity 9_000 vs reference 10_000 = 10% daily loss > 2% latch -> kill-switch halts.
    broker = FakeBrokerAdapter(
        account=AccountSnapshot("t", buying_power=9_000, cash=9_000, equity=9_000)
    )
    notifier = _RecordingNotifier()
    _results, halt = _run(broker, notifier, tmp_path)
    assert halt.current().halted
    assert "kill_switch_halt" in notifier.events()


def test_uncertain_submit_fires_alert(tmp_path) -> None:
    broker = FakeBrokerAdapter(
        account=AccountSnapshot("t", buying_power=10_000, cash=10_000, equity=10_000),
        mode="timeout",
    )
    notifier = _RecordingNotifier()
    results, halt = _run(broker, notifier, tmp_path)
    assert results[0].status == "uncertain"
    assert halt.current().halted
    assert "broker_uncertain_submit" in notifier.events()


def test_broker_read_failure_fires_alert(tmp_path) -> None:
    notifier = _RecordingNotifier()
    results, halt = _run(_ReadFailsBroker(), notifier, tmp_path)
    assert results[0].status == "risk_block"
    assert halt.current().halted
    assert "broker_read_failed" in notifier.events()


def test_broken_notifier_does_not_break_trading(tmp_path) -> None:
    # A notifier that raises must NOT propagate — the halt must still latch.
    broker = FakeBrokerAdapter(
        account=AccountSnapshot("t", buying_power=10_000, cash=10_000, equity=10_000),
        mode="timeout",
    )
    results, halt = _run(broker, _BrokenNotifier(), tmp_path)
    assert results[0].status == "uncertain"
    assert halt.current().halted  # trading-path safety unaffected by the broken notifier


def test_no_notifier_still_processes(tmp_path) -> None:
    broker = FakeBrokerAdapter(
        account=AccountSnapshot("t", buying_power=10_000, cash=10_000, equity=10_000),
        mode="timeout",
    )
    results, halt = _run(broker, None, tmp_path)  # notifier=None -> structured-log only
    assert results[0].status == "uncertain"
    assert halt.current().halted


def test_webhook_notifier_swallows_delivery_errors() -> None:
    # An unreachable URL must not raise — a failed alert cannot break trading.
    notifier = WebhookNotifier("http://127.0.0.1:0/never", timeout_s=0.1)
    notifier.notify(level="critical", event="t", message="m", fields={"k": "v"})


def test_null_and_logging_notifiers_are_safe() -> None:
    NullNotifier().notify(level="info", event="t", message="m", fields={})
    LoggingNotifier().notify(level="warning", event="t", message="m", fields={"a": 1})
    log_event(get_logger("trader.test"), "unit", "ok", count=1)
