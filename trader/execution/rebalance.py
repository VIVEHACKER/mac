"""Turn target portfolio weights into a broker-agnostic ``RebalancePlan``.

This is the single decision→order step shared by paper and live: the SAME target
weights produce the SAME ``OrderIntent``s, which the SAME
``process_order_intents()`` runner executes against ANY ``BrokerAdapter`` —
``PaperBroker`` for paper, ``AlpacaBrokerAdapter`` for live. That is what makes
"paper = live = same code" true at the decision/order layer.

It activates the previously-defined-but-unused ``TargetPosition`` / ``RebalancePlan``
abstractions. The validated backtest / walk-forward math is untouched — this only
formalises the order generation that ``scripts/paper_drill.py`` previously inlined
as a markdown table.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

from trader.execution.intents import OrderIntent, RebalancePlan, TargetPosition


def targets_from_weights(
    weights: dict[str, float],
    marks: dict[str, float],
    capital: float,
    *,
    market: str = "us",
) -> list[TargetPosition]:
    """Whole-share target positions for ``capital`` allocated by ``weights`` at ``marks``.

    A symbol with no mark (or a non-positive mark) is skipped — it cannot be sized.
    Callers that require full coverage should validate ``marks`` before calling.
    """
    if capital < 0:
        raise ValueError("capital must be non-negative")
    targets: list[TargetPosition] = []
    for symbol, weight in weights.items():
        mark = marks.get(symbol) or marks.get(symbol.upper())
        if mark is None or mark <= 0:
            continue
        qty = math.floor(capital * weight / mark)
        targets.append(TargetPosition(symbol=symbol.upper(), market=market, target_qty=float(qty)))
    return targets


def plan_rebalance(
    *,
    strategy: str,
    rebalance_key: str,
    targets: list[TargetPosition],
    current_qty: dict[str, float],
    marks: dict[str, float],
    generated_at: datetime | None = None,
    min_notional: float = 0.0,
) -> RebalancePlan:
    """Build a ``RebalancePlan``: per-symbol (target − current) → buy/sell intents.

    Symbols currently held but absent from ``targets`` are fully exited. Dust trades
    whose notional is below ``min_notional`` are dropped. Sells are emitted before
    buys so the runner's cumulative cash projection frees buying power before it is
    consumed.
    """
    generated = generated_at or datetime.now(UTC)
    target_by_symbol = {t.symbol.upper(): t for t in targets}
    held = {symbol.upper(): qty for symbol, qty in current_qty.items()}
    marks_u = {symbol.upper(): price for symbol, price in marks.items()}

    buys: list[OrderIntent] = []
    sells: list[OrderIntent] = []
    for symbol in sorted(set(target_by_symbol) | set(held)):
        target = target_by_symbol.get(symbol)
        target_q = target.target_qty if target else 0.0
        delta = target_q - held.get(symbol, 0.0)
        if abs(delta) < 1e-9:
            continue
        mark = marks_u.get(symbol, 0.0)
        if min_notional > 0 and mark > 0 and abs(delta) * mark < min_notional:
            continue
        side = "buy" if delta > 0 else "sell"
        intent = OrderIntent(
            strategy=strategy,
            symbol=symbol,
            market=target.market if target else "us",
            side=side,
            qty=abs(delta),
            order_type="market",
            rebalance_key=rebalance_key,
            reason=f"rebalance {symbol} {held.get(symbol, 0.0):g}->{target_q:g}",
            asof_ts=generated,
        )
        (buys if side == "buy" else sells).append(intent)

    intents = tuple(intent.normalized() for intent in (*sells, *buys))
    return RebalancePlan(
        strategy=strategy,
        rebalance_key=rebalance_key,
        generated_at=generated,
        intents=intents,
    )
