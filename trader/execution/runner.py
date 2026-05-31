from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime

from risk.halt_state import HaltStateStore
from risk.policy import RiskPolicy
from risk.pretrade import _project_positions, evaluate_pretrade_order
from trader.execution.broker import (
    BrokerAdapter,
    BrokerRejectedError,
    BrokerTemporaryError,
)
from trader.execution.intents import OrderIntent
from trader.execution.order_store import JsonlOrderStore, OrderEvent


@dataclass(frozen=True)
class ExecutionResult:
    client_order_id: str
    action: str
    status: str
    reasons: tuple[str, ...] = ()


def process_order_intents(
    intents: list[OrderIntent],
    *,
    broker: BrokerAdapter,
    store: JsonlOrderStore,
    halt_store: HaltStateStore,
    policy: RiskPolicy,
    marks: dict[str, float],
    dry_run: bool = True,
) -> list[ExecutionResult]:
    account = broker.get_account()
    positions = broker.list_positions()
    halt = halt_store.current()
    results: list[ExecutionResult] = []
    for raw in intents:
        intent = raw.normalized()
        intent_day = (intent.asof_ts or datetime.now(UTC)).date()
        if store.has_intent(intent.client_order_id):
            results.append(ExecutionResult(intent.client_order_id, "skip", "duplicate"))
            continue
        check = evaluate_pretrade_order(
            intent,
            policy=policy,
            account=account,
            positions=positions,
            marks=marks,
            halt=halt,
            orders_today=store.intent_count_on(intent_day),
            new_notional_today=store.buy_notional_on(intent_day, marks),
        )
        store.record_intent(intent)
        if not check.passed:
            store.record_event(
                OrderEvent(
                    event_type="risk_block",
                    client_order_id=intent.client_order_id,
                    ts=datetime.now(UTC),
                    status="blocked",
                    message="; ".join(check.reasons),
                )
            )
            results.append(
                ExecutionResult(intent.client_order_id, "block", "risk_block", check.reasons)
            )
            continue
        # --- Cumulative batch accumulation ---
        # Project in-memory state forward so subsequent intents in the same batch
        # are evaluated against the exposure already committed by earlier accepted
        # intents. Risk-reducing sells lower exposure and free cash; buys consume
        # cash/buying_power and inflate symbol weight.
        mark_price = marks.get(intent.symbol) or intent.limit_price or 0.0
        if mark_price > 0:
            positions = _project_positions(positions, intent, mark_price)
            if intent.side == "buy":
                notional = intent.qty * mark_price
                account = replace(
                    account,
                    cash=account.cash - notional,
                    buying_power=account.buying_power - notional,
                )
        if dry_run:
            store.record_event(
                OrderEvent(
                    event_type="dry_run",
                    client_order_id=intent.client_order_id,
                    ts=datetime.now(UTC),
                    status="accepted",
                    message="pre-trade checks passed; broker not called",
                )
            )
            results.append(ExecutionResult(intent.client_order_id, "dry_run", "accepted"))
            continue
        try:
            order = broker.submit_order(intent)
        except BrokerRejectedError as exc:
            store.record_event(
                OrderEvent(
                    event_type="broker_reject",
                    client_order_id=intent.client_order_id,
                    ts=datetime.now(UTC),
                    status="rejected",
                    message=str(exc),
                )
            )
            results.append(
                ExecutionResult(intent.client_order_id, "submit", "rejected", (str(exc),))
            )
            continue
        except BrokerTemporaryError as exc:
            halt_store.activate(
                f"uncertain broker submit state for {intent.client_order_id}: {exc}",
                source="execution-runner",
            )
            store.record_event(
                OrderEvent(
                    event_type="broker_uncertain",
                    client_order_id=intent.client_order_id,
                    ts=datetime.now(UTC),
                    status="uncertain",
                    message=str(exc),
                )
            )
            results.append(
                ExecutionResult(intent.client_order_id, "submit", "uncertain", (str(exc),))
            )
            continue
        store.record_broker_order("broker_submit", order)
        results.append(ExecutionResult(intent.client_order_id, "submit", order.status))
    return results
