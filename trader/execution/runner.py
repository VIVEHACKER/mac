from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime

from risk.halt_state import HaltStateStore
from risk.kill_switch import check_kill_switch
from risk.policy import RiskPolicy
from risk.pretrade import _project_positions, evaluate_pretrade_order
from trader.execution.broker import (
    AccountSnapshot,
    BrokerAdapter,
    BrokerError,
    BrokerRejectedError,
    BrokerTemporaryError,
    PositionSnapshot,
)
from trader.execution.intents import OrderIntent
from trader.execution.order_store import JsonlOrderStore, OrderEvent


@dataclass(frozen=True)
class ExecutionResult:
    client_order_id: str
    action: str
    status: str
    reasons: tuple[str, ...] = ()


def _project_after_fill(
    positions: list[PositionSnapshot],
    account: AccountSnapshot,
    intent: OrderIntent,
    marks: dict[str, float],
) -> tuple[list[PositionSnapshot], AccountSnapshot]:
    """Project an ACCEPTED order onto the in-memory book so later intents in the same batch
    see the committed exposure (sells free cash/reduce exposure; buys consume cash). Applied
    ONLY after a successful dry-run or live submit — a rejected or uncertain order never moved
    the book, so its exposure must NOT leak into later risk checks (Codex P2)."""
    mark_price = intent.limit_price or marks.get(intent.symbol) or 0.0
    if mark_price <= 0:
        return positions, account
    positions = _project_positions(positions, intent, mark_price)
    if intent.side == "buy":
        notional = intent.qty * mark_price
        account = replace(
            account,
            cash=account.cash - notional,
            buying_power=account.buying_power - notional,
        )
    return positions, account


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
    # Fail-closed arming check (adversarial-review finding): reference_equity=None used to
    # silently skip the whole kill-switch block — a future live/paper entry point that forgot
    # the kwarg would run unprotected with every test green. Real submissions must be armed;
    # "kill_switch 항상 켬" is a project invariant, so refuse loudly instead of skipping.
    if not dry_run and reference_equity is None:
        raise ValueError(
            "dry_run=False requires reference_equity (start-of-day equity) to arm the "
            "kill-switch; refusing to submit unarmed. Pass it (and peak_equity for the "
            "peak-drawdown backstop), or use dry_run=True."
        )
    # Fail-closed broker reads: account/positions are needed to arm the kill-switch and run
    # pre-trade checks. If the broker errors here (network/timeout/5xx -> BrokerTemporaryError,
    # or a hard 4xx -> BrokerRejectedError, e.g. bad credentials), we CANNOT proceed safely.
    # Refuse the whole batch; on a live run also latch a halt so a manual resume is required
    # (an uncertain broker connection must not silently resume into unprotected submissions).
    try:
        account = broker.get_account()
        positions = broker.list_positions()
    except BrokerError as exc:
        reason = f"broker account/positions unavailable: {exc}"
        if not dry_run:
            halt_store.activate(reason, source="execution-runner")
        blocked_reads: list[ExecutionResult] = []
        for raw in intents:
            intent = raw.normalized()
            store.record_event(
                OrderEvent(
                    event_type="broker_uncertain",
                    client_order_id=intent.client_order_id,
                    ts=datetime.now(UTC),
                    status="uncertain",
                    message=reason,
                )
            )
            blocked_reads.append(
                ExecutionResult(intent.client_order_id, "block", "risk_block", (reason,))
            )
        return blocked_reads
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
        # A halt latched mid-batch (e.g. an uncertain submit below) must stop the WHOLE
        # remaining batch: once the broker state is uncertain we do NOT keep sending orders
        # (Codex P1). These intents are not recorded, so they stay retryable after resume.
        if halt.halted:
            store.record_event(
                OrderEvent(
                    event_type="halt_block",
                    client_order_id=intent.client_order_id,
                    ts=datetime.now(UTC),
                    status="blocked",
                    message=halt.reason,
                )
            )
            results.append(
                ExecutionResult(
                    intent.client_order_id, "block", "risk_block", (f"halted: {halt.reason}",)
                )
            )
            continue
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
            # A dry-run assumes the order fills; project so later intents in this batch see
            # the committed exposure (cumulative batch accumulation).
            positions, account = _project_after_fill(positions, account, intent, marks)
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
            # Capture the latched halt so the loop-top guard blocks every remaining intent in
            # this batch instead of submitting more orders into an uncertain state (Codex P1).
            halt = halt_store.activate(
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
        # Project ONLY after a successful submit — a rejected or uncertain order never moved
        # the book, so its exposure must not leak into later intents' risk checks (Codex P2).
        positions, account = _project_after_fill(positions, account, intent, marks)
    return results
