from __future__ import annotations

from dataclasses import dataclass

from trader.execution.broker import PositionSnapshot
from trader.execution.order_store import JsonlOrderStore


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
