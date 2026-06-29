from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from trader.execution.broker import BrokerAdapter, BrokerError, PositionSnapshot
from trader.execution.order_store import JsonlOrderStore, OrderEvent


@dataclass(frozen=True)
class InFlightResolution:
    client_order_id: str
    outcome: str
    status: str
    message: str = ""


def reconcile_in_flight(store: JsonlOrderStore, broker: BrokerAdapter) -> list[InFlightResolution]:
    """Resolve every recorded-but-unresolved intent against the broker by client_order_id and
    bring the true order state back into the store. Closes the live-readiness P0 where an order
    that filled at the broker (crash after submit, or an uncertain submit) never lands in the
    ledger, so the reconcile baseline silently under-counts the real position.

    Per intent: a terminal order is recorded and resolved; a working order is recorded but kept
    unresolved for the next cycle; a missing order (None) is marked not-found and resolved (it
    never reached the broker); a broker read error keeps it unresolved so a later cycle retries.
    """
    resolutions: list[InFlightResolution] = []
    for cid in store.unresolved_intent_ids():
        try:
            order = broker.get_order(cid)
        except BrokerError as exc:
            # State still unknown — do NOT record a resolving event, so the intent stays in the
            # unresolved set and a later cycle retries. The attempt is logged for audit.
            store.record_event(
                OrderEvent(
                    event_type="broker_reconcile_uncertain",
                    client_order_id=cid,
                    ts=datetime.now(UTC),
                    status="uncertain",
                    message=str(exc),
                )
            )
            resolutions.append(InFlightResolution(cid, "uncertain", "uncertain", str(exc)))
            continue
        if order is None:
            # The broker has no order for this client_order_id: it never reached the broker
            # (crash before submit, or an uncertain submit that did not go through). Resolve it.
            store.record_event(
                OrderEvent(
                    event_type="broker_reconcile_absent",
                    client_order_id=cid,
                    ts=datetime.now(UTC),
                    status="not_found",
                    message="broker has no order for this client_order_id",
                )
            )
            resolutions.append(InFlightResolution(cid, "not_found", "not_found"))
            continue
        store.record_broker_order("broker_reconcile", order)
        outcome = "resolved_terminal" if order.terminal else "still_working"
        resolutions.append(InFlightResolution(cid, outcome, order.status))
    return resolutions


@dataclass(frozen=True)
class ReconciliationIssue:
    symbol: str
    market: str
    expected_qty: float
    actual_qty: float
    message: str


def reconcile_positions(
    expected: dict[tuple[str, str], float],
    actual: list[PositionSnapshot],
    *,
    qty_tolerance: float = 1e-8,
) -> list[ReconciliationIssue]:
    actual_by_key = {
        (position.symbol.upper(), position.market.lower()): position.qty for position in actual
    }
    issues: list[ReconciliationIssue] = []
    for key in sorted(set(expected) | set(actual_by_key)):
        expected_qty = expected.get(key, 0.0)
        actual_qty = actual_by_key.get(key, 0.0)
        if abs(expected_qty - actual_qty) <= qty_tolerance:
            continue
        symbol, market = key
        issues.append(
            ReconciliationIssue(
                symbol=symbol,
                market=market,
                expected_qty=expected_qty,
                actual_qty=actual_qty,
                message=f"expected {expected_qty:.8f}, broker has {actual_qty:.8f}",
            )
        )
    return issues


def expected_positions_from_store(store: JsonlOrderStore) -> dict[tuple[str, str], float]:
    """Net FILLED quantity per (symbol, market) the system believes it holds from its OWN
    order history (buy +, sell -), derived from the order store's latest broker-order
    snapshots. Assumes a flat start — pre-existing or manually-opened positions are not tracked
    here. Use as the reconcile baseline instead of a hand-typed --expected string, so position
    drift is checked against actual recorded fills rather than an operator's memory."""
    expected: dict[tuple[str, str], float] = {}
    for order in store.latest_broker_orders().values():
        symbol = str(order.get("symbol", "")).upper()
        if not symbol:
            continue
        filled = float(order.get("filled_qty", 0.0) or 0.0)
        if filled == 0.0:
            continue
        market = str(order.get("market", "")).lower()
        side = str(order.get("side", "")).lower()
        signed = filled if side == "buy" else -filled
        key = (symbol, market)
        expected[key] = expected.get(key, 0.0) + signed
    return {key: qty for key, qty in expected.items() if abs(qty) > 1e-9}
