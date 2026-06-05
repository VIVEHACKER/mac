from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime

from risk.halt_state import HaltStateStore
from risk.kill_switch import check_kill_switch
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
    reference_equity: float | None = None,
    peak_equity: float | None = None,
) -> list[ExecutionResult]:
    account = broker.get_account()
    positions = broker.list_positions()
    halt = halt_store.current()
    # Portfolio kill-switch — wired into the live loop (previously a dead, never-called function).
    # Latch a NEW halt when the account breaches the daily-loss or peak-drawdown latch, but NEVER
    # overwrite an existing halt (a prior manual/broker halt must survive, so clearing the
    # kill-switch does not silently resume into an unresolved blocker — Codex P2). Requires the
    # caller to supply reference (start-of-day) and peak equity; skipped if absent.
    if reference_equity is not None and not halt.halted:
        gross_exposure = (
            sum(abs(p.market_value) for p in positions) / account.equity
            if account.equity > 0
            else 0.0
        )
        kill = check_kill_switch(
            start_equity=reference_equity,
            current_equity=account.equity,
            gross_exposure=gross_exposure,
            max_daily_drawdown=policy.max_daily_loss,
            max_gross_exposure=policy.max_gross_exposure,
            peak_equity=peak_equity,
            max_drawdown_from_peak=policy.max_drawdown_from_peak,
        )
        if kill.halted:
            halt = halt_store.activate(
                "kill-switch: " + "; ".join(kill.reasons), source="kill-switch"
            )
    # An active halt (pre-existing OR just-latched) pauses the WHOLE batch, and the intents are NOT
    # recorded — a halt is a temporary pause, not a per-order rejection, so the same orders stay
    # retryable on every later cycle until the halt is cleared (Codex P2).
    if halt.halted:
        # Keep the established "risk_block" status (the CLI maps it to the gate-failure exit code);
        # the kill-switch vs manual/broker origin is carried in the reason ("halted: ...").
        reasons = (f"halted: {halt.reason}",)
        blocked: list[ExecutionResult] = []
        for raw in intents:
            intent = raw.normalized()
            store.record_event(
                OrderEvent(
                    event_type="halt_block",
                    client_order_id=intent.client_order_id,
                    ts=datetime.now(UTC),
                    status="blocked",
                    message=halt.reason,
                )
            )
            blocked.append(ExecutionResult(intent.client_order_id, "block", "risk_block", reasons))
        return blocked
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
        mark_price = intent.limit_price or marks.get(intent.symbol) or 0.0
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
