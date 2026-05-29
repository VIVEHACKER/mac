from __future__ import annotations

from dataclasses import dataclass

from trader.execution.broker import PositionSnapshot


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

