from __future__ import annotations

from risk.halt_state import HaltRecord
from risk.policy import RiskCheckResult, RiskPolicy
from trader.execution.broker import AccountSnapshot, PositionSnapshot
from trader.execution.intents import OrderIntent


def evaluate_pretrade_order(
    intent: OrderIntent,
    *,
    policy: RiskPolicy,
    account: AccountSnapshot,
    positions: list[PositionSnapshot],
    marks: dict[str, float],
    halt: HaltRecord | None = None,
    orders_today: int = 0,
    new_notional_today: float = 0.0,
) -> RiskCheckResult:
    normalized = intent.normalized()
    reasons: list[str] = []
    if halt is not None and halt.halted:
        reasons.append(f"halted: {halt.reason}")
    if account.trading_blocked or account.account_blocked:
        reasons.append("broker account is blocked for trading")
    if normalized.market not in policy.allowed_markets:
        reasons.append(f"market {normalized.market} is not allowed")
    if normalized.order_type not in policy.allowed_order_types:
        reasons.append(f"order type {normalized.order_type} is not allowed")
    if normalized.side not in policy.allowed_sides:
        reasons.append(f"side {normalized.side} is not allowed")
    if normalized.qty <= 0:
        reasons.append("qty must be positive")

    reference_mark = marks.get(normalized.symbol)
    mark = normalized.limit_price or reference_mark
    if mark is None or mark <= 0:
        reasons.append(f"{normalized.symbol}: missing positive mark or limit price")
        notional = 0.0
    else:
        notional = normalized.qty * mark
        if (
            normalized.order_type == "limit"
            and normalized.limit_price is not None
            and reference_mark is not None
            and reference_mark > 0
            and policy.max_limit_deviation >= 0
        ):
            allowed_deviation = policy.max_limit_deviation
            if normalized.side == "buy":
                max_limit = reference_mark * (1 + allowed_deviation)
                if normalized.limit_price > max_limit:
                    reasons.append(
                        f"buy limit {normalized.limit_price:.2f} is more than "
                        f"{allowed_deviation:.1%} above mark {reference_mark:.2f}"
                    )
            elif normalized.side == "sell":
                min_limit = reference_mark * (1 - allowed_deviation)
                if normalized.limit_price < min_limit:
                    reasons.append(
                        f"sell limit {normalized.limit_price:.2f} is more than "
                        f"{allowed_deviation:.1%} below mark {reference_mark:.2f}"
                    )
        if notional > policy.max_order_notional:
            reasons.append(
                f"order notional {notional:.2f} exceeds {policy.max_order_notional:.2f}"
            )
        if normalized.side == "buy" and notional > account.buying_power:
            reasons.append(
                f"order notional {notional:.2f} exceeds buying power {account.buying_power:.2f}"
            )
        if new_notional_today + max(notional, 0.0) > policy.max_daily_new_notional:
            reasons.append("daily new notional limit would be exceeded")

    if orders_today >= policy.max_orders_per_day:
        reasons.append("daily order count limit reached")
    if account.pattern_day_trader is False and account.daytrade_count >= policy.max_daytrade_count:
        reasons.append("day-trade count limit reached")
    if account.equity <= 0:
        reasons.append("account equity must be positive")
    else:
        if normalized.side == "buy":
            if not policy.allow_margin and notional > account.cash:
                reasons.append(f"order notional {notional:.2f} exceeds cash {account.cash:.2f}")
            projected_cash_fraction = (account.cash - notional) / account.equity
            if projected_cash_fraction < policy.min_cash_fraction:
                reasons.append(
                    f"projected cash fraction {projected_cash_fraction:.2%} below "
                    f"{policy.min_cash_fraction:.2%}"
                )
        if normalized.side == "sell" and not policy.allow_short:
            current_qty = _position_qty(positions, normalized)
            if normalized.qty > max(current_qty, 0.0) + 1e-8:
                reasons.append(
                    f"short selling is not allowed: sell qty {normalized.qty:g} exceeds "
                    f"current qty {current_qty:g}"
                )
        projected_positions = _project_positions(positions, normalized, mark or 0.0)
        gross = sum(abs(position.market_value) for position in projected_positions) / account.equity
        if gross > policy.max_gross_exposure:
            reasons.append(f"gross exposure {gross:.2f} exceeds {policy.max_gross_exposure:.2f}")
        symbol_value = abs(
            next(
                (
                    position.market_value
                    for position in projected_positions
                    if position.symbol == normalized.symbol and position.market == normalized.market
                ),
                notional,
            )
        )
        if symbol_value / account.equity > policy.max_symbol_weight:
            reasons.append(
                f"{normalized.symbol} weight {symbol_value / account.equity:.2%} exceeds "
                f"{policy.max_symbol_weight:.2%}"
            )

    if reasons:
        return RiskCheckResult.fail(reasons)
    return RiskCheckResult.pass_()


def _position_qty(positions: list[PositionSnapshot], intent: OrderIntent) -> float:
    return sum(
        position.qty
        for position in positions
        if position.symbol.upper() == intent.symbol and position.market.lower() == intent.market
    )


def _project_positions(
    positions: list[PositionSnapshot],
    intent: OrderIntent,
    mark: float,
) -> list[PositionSnapshot]:
    delta_qty = intent.qty if intent.side == "buy" else -intent.qty
    key = (intent.symbol, intent.market)
    projected: list[PositionSnapshot] = []
    seen = False
    for position in positions:
        if (position.symbol, position.market) != key:
            projected.append(position)
            continue
        seen = True
        qty = position.qty + delta_qty
        projected.append(
            PositionSnapshot(
                symbol=position.symbol,
                market=position.market,
                qty=qty,
                market_value=qty * mark,
                avg_entry_price=position.avg_entry_price,
            )
        )
    if not seen:
        projected.append(
            PositionSnapshot(
                symbol=intent.symbol,
                market=intent.market,
                qty=delta_qty,
                market_value=delta_qty * mark,
            )
        )
    return projected
