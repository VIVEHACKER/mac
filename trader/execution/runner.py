from __future__ import annotations

import logging
import time
from collections.abc import Callable
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
    BrokerOrder,
    BrokerRejectedError,
    BrokerTemporaryError,
    PositionSnapshot,
)
from trader.execution.intents import OrderIntent
from trader.execution.order_store import JsonlOrderStore, OrderEvent
from trader.operations.observability import (
    Notifier,
    get_logger,
    level_to_pylevel,
    log_event,
)

_LOGGER = get_logger("trader.execution")


def _alert(
    notifier: Notifier | None, *, level: str, event: str, message: str, **fields: object
) -> None:
    """Structured-log a critical execution event AND, if a notifier is configured, push an
    external alert. A broken notifier must never break trading, so its errors are swallowed.
    Without a notifier the event is still logged (closes the audit's no-observability gap).
    Severity is preserved (critical->CRITICAL) so log-based alerting sees the true level."""
    log_event(_LOGGER, event, message, level=level_to_pylevel(level), **fields)
    if notifier is not None:
        try:
            notifier.notify(level=level, event=event, message=message, fields=fields)
        except Exception as exc:  # noqa: BLE001 — a failed alert must not break the trading path
            log_event(_LOGGER, "notifier_error", f"notifier raised: {exc}", level=logging.WARNING)


@dataclass(frozen=True)
class ExecutionResult:
    client_order_id: str
    action: str
    status: str
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class FillPoll:
    """Post-submit fill-polling config. A live market order returns accepted/new with
    filled_qty=0 (async fill); without polling the order_store keeps that non-terminal
    snapshot forever and the ledger diverges from the real position (readiness-audit gap).
    Opt-in: process_order_intents only polls when a FillPoll is passed."""

    max_polls: int = 5
    interval_s: float = 1.0


def _poll_until_terminal(
    broker: BrokerAdapter,
    order: BrokerOrder,
    store: JsonlOrderStore,
    fill_poll: FillPoll | None,
    sleep: Callable[[float], None],
) -> BrokerOrder:
    """Poll get_order until the order is terminal (or polls run out), recording each fresh
    snapshot so partial->filled transitions land in the ledger. The order is ALREADY live,
    so a status-check blip (BrokerError) is NOT a reason to halt — it is recorded and polling
    stops; reconciliation against the broker catches any residual drift."""
    if fill_poll is None or order.terminal:
        return order
    current = order
    for _ in range(fill_poll.max_polls):
        sleep(fill_poll.interval_s)
        try:
            polled = broker.get_order(current.client_order_id)
        except BrokerError as exc:
            store.record_event(
                OrderEvent(
                    event_type="broker_poll_uncertain",
                    client_order_id=current.client_order_id,
                    ts=datetime.now(UTC),
                    status="uncertain",
                    message=str(exc),
                )
            )
            return current
        if polled is None:
            continue
        current = polled
        store.record_broker_order("broker_poll", current)
        if current.terminal:
            break
    return current


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
    fill_poll: FillPoll | None = None,
    sleep: Callable[[float], None] = time.sleep,
    notifier: Notifier | None = None,
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
        # Latch a halt so a manual resume is required — but NEVER overwrite an existing
        # halt's reason/source. A prior manual/reconciliation halt must survive: if this
        # transient read-failure reason replaced it, clearing the read-failure halt would
        # silently resume trading past the original unresolved blocker (Codex P2). Mirrors
        # the kill-switch latch below, which also refuses to overwrite an active halt.
        if not dry_run and not halt_store.current().halted:
            halt_store.activate(reason, source="execution-runner")
        _alert(
            notifier, level="critical", event="broker_read_failed", message=reason, dry_run=dry_run
        )
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
    if not dry_run and not halt.halted:
        try:
            clock = broker.get_clock()
        except BrokerError as exc:
            reason = f"broker market clock unavailable: {exc}"
            halt = halt_store.activate(reason, source="execution-runner")
            _alert(
                notifier,
                level="critical",
                event="broker_clock_failed",
                message=reason,
                dry_run=dry_run,
            )
        else:
            if not clock.is_open:
                next_open = clock.next_open.isoformat() if clock.next_open else "unknown"
                reason = f"market is closed at {clock.timestamp.isoformat()}; next_open={next_open}"
                blocked_closed: list[ExecutionResult] = []
                for raw in intents:
                    intent = raw.normalized()
                    store.record_event(
                        OrderEvent(
                            event_type="market_closed_block",
                            client_order_id=intent.client_order_id,
                            ts=datetime.now(UTC),
                            status="blocked",
                            message=reason,
                        )
                    )
                    blocked_closed.append(
                        ExecutionResult(intent.client_order_id, "block", "risk_block", (reason,))
                    )
                return blocked_closed
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
            _alert(notifier, level="critical", event="kill_switch_halt", message=halt.reason)
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
            _alert(
                notifier,
                level="critical",
                event="broker_uncertain_submit",
                message=str(exc),
                client_order_id=intent.client_order_id,
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
        # Poll for the real fill so an async accepted/filled_qty=0 order does not leave the
        # ledger stuck on a non-terminal snapshot (no-op unless fill_poll is configured).
        order = _poll_until_terminal(broker, order, store, fill_poll, sleep)
        results.append(ExecutionResult(intent.client_order_id, "submit", order.status))
        # Project the ACTUAL committed quantity onto the book for later intents in this batch.
        # If polling confirmed a terminal outcome we know exactly what filled, so project
        # filled_qty (a terminal no-fill — async rejected/canceled — projects 0 = no change,
        # so a rejected sell does NOT free phantom room for a later buy: Codex Step-2 P1). An
        # order still working (non-terminal, e.g. unpolled) projects the full intent, the
        # conservative assumption that it will fill.
        proj_qty = order.filled_qty if order.terminal else intent.qty
        if proj_qty > 0:
            positions, account = _project_after_fill(
                positions, account, replace(intent, qty=proj_qty), marks
            )
    return results
