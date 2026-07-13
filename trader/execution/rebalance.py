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

from risk.sizing import size_position
from trader.execution.intents import OrderIntent, RebalancePlan, TargetPosition


def targets_from_weights(
    weights: dict[str, float],
    marks: dict[str, float],
    capital: float,
    *,
    market: str = "us",
    fractional_decimals: int | None = None,
) -> list[TargetPosition]:
    """Target positions for ``capital`` allocated by ``weights`` at ``marks``.

    A symbol with no mark (or a non-positive mark) is skipped — it cannot be sized.
    Callers that require full coverage should validate ``marks`` before calling. By
    default quantities are whole shares. ``fractional_decimals`` floors fractional
    quantities to that precision so target notional never exceeds the allocation.
    """
    if capital < 0:
        raise ValueError("capital must be non-negative")
    if fractional_decimals is not None and not 0 <= fractional_decimals <= 9:
        raise ValueError("fractional_decimals must be between 0 and 9")
    targets: list[TargetPosition] = []
    for symbol, weight in weights.items():
        mark = marks.get(symbol) or marks.get(symbol.upper())
        if mark is None or mark <= 0:
            continue
        raw_qty = capital * weight / mark
        if fractional_decimals is None:
            qty = float(math.floor(raw_qty))
        else:
            scale = 10**fractional_decimals
            qty = math.floor(raw_qty * scale) / scale
        targets.append(TargetPosition(symbol=symbol.upper(), market=market, target_qty=float(qty)))
    return targets


def plan_rebalance(
    *,
    strategy: str,
    rebalance_key: str,
    targets: list[TargetPosition],
    current_qty: dict[str, float],
    marks: dict[str, float],
    current_markets: dict[str, str] | None = None,
    generated_at: datetime | None = None,
    min_notional: float = 0.0,
) -> RebalancePlan:
    """Build a ``RebalancePlan``: per-symbol (target − current) → buy/sell intents.

    Symbols currently held but absent from ``targets`` are fully exited — in the market
    they are actually held in (``current_markets[symbol]``, default ``"us"``). Without
    that, a non-US holding would be exited as a US intent, which the pretrade gate sees
    as a naked short on a book that holds nothing in ``("SYM", "us")`` (adversarial-review
    latent finding; fixed before any live wiring depends on it). Dust trades whose
    notional is below ``min_notional`` are dropped. Sells are emitted before buys so the
    runner's cumulative cash projection frees buying power before it is consumed.
    """
    generated = generated_at or datetime.now(UTC)
    target_by_symbol = {t.symbol.upper(): t for t in targets}
    held = {symbol.upper(): qty for symbol, qty in current_qty.items()}
    held_markets = {s.upper(): m.lower() for s, m in (current_markets or {}).items()}
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
            market=target.market if target else held_markets.get(symbol, "us"),
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


def sized_targets(
    symbols: list[str],
    *,
    aum: float,
    marks: dict[str, float],
    vols: dict[str, float],
    downside_pct: float = 0.30,
    edges: dict[str, tuple[float, float]] | None = None,
    market: str = "us",
    max_position_pct: float = 0.08,
    target_vol_annualized: float = 0.35,
    max_risk_pct_of_aum: float = 0.02,
    kelly_multiplier: float = 0.5,
) -> list[TargetPosition]:
    """Risk-aware target positions, replacing naive equal-weight / full exposure.

    Each name is sized by ``risk.sizing.size_position`` — the smaller of vol-target /
    risk-cap / hard concentration cap, plus half-Kelly when ``edges[symbol] =
    (win_probability, upside_pct)`` supplies a real per-name edge. A symbol missing a
    positive mark or vol is skipped.

    OPT-IN: this differs from the cap-0.20 sizing the deploy candidate was validated
    under, so the backtest must be re-run with it and pass the gate before it drives
    live capital (claims are earned via the gate, never asserted).
    """
    edge_map = edges or {}
    targets: list[TargetPosition] = []
    for symbol in symbols:
        mark = marks.get(symbol) or marks.get(symbol.upper())
        vol = vols.get(symbol) or vols.get(symbol.upper())
        if not mark or mark <= 0 or not vol or vol <= 0:
            continue
        win_probability, upside_pct = edge_map.get(symbol, (None, None))
        result = size_position(
            aum,
            mark,
            asset_vol_annualized=vol,
            downside_pct=downside_pct,
            win_probability=win_probability,
            upside_pct=upside_pct,
            kelly_multiplier=kelly_multiplier,
            max_position_pct=max_position_pct,
            max_risk_pct_of_aum=max_risk_pct_of_aum,
            target_vol_annualized=target_vol_annualized,
        )
        if result.qty > 0:
            targets.append(
                TargetPosition(symbol=symbol.upper(), market=market, target_qty=float(result.qty))
            )
    return targets
