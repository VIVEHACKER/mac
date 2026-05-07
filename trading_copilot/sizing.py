from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SizingPlan:
    aum: float
    price: float
    target_weight_pct: float
    capped_weight_pct: float
    target_dollars: float
    shares: int
    actual_dollars: float
    actual_weight_pct: float
    stop_price: float
    risk_dollars: float
    risk_pct_of_aum: float
    kelly_fraction: float
    vol_target_weight: float
    notes: tuple[str, ...]


def kelly_fraction(
    win_probability: float,
    win_return: float,
    loss_return: float,
) -> float:
    """Full Kelly fraction.

    win_return and loss_return are absolute fractional outcomes (e.g. 0.5 = +50%, 0.3 = -30%).
    Returns 0 if the bet has negative edge.
    """
    if win_probability <= 0 or win_probability >= 1:
        return 0.0
    if win_return <= 0 or loss_return <= 0:
        return 0.0
    p = win_probability
    q = 1.0 - p
    b = win_return / loss_return
    edge = (b * p - q) / b
    return max(0.0, edge)


def vol_target_weight(
    asset_vol_annualized: float,
    target_vol_annualized: float,
    max_leverage: float = 1.0,
) -> float:
    """Weight (or leverage if >1) needed for the asset's vol to match a target portfolio vol."""
    if asset_vol_annualized <= 0:
        return 0.0
    return min(target_vol_annualized / asset_vol_annualized, max_leverage)


def plan_position(
    aum: float,
    price: float,
    *,
    win_probability: float,
    upside_pct: float,
    downside_pct: float,
    asset_vol_annualized: float,
    target_vol_annualized: float = 0.35,
    kelly_multiplier: float = 0.5,
    max_position_pct: float = 0.25,
    max_risk_pct_of_aum: float = 0.02,
) -> SizingPlan:
    """Combine fractional Kelly, vol targeting, and a hard risk-of-loss cap.

    upside_pct, downside_pct, asset_vol_annualized are fractions (0.50 means 50%).
    Returns the smallest weight implied by all three constraints.
    """
    if aum <= 0 or price <= 0:
        raise ValueError("aum and price must be positive")
    if downside_pct <= 0:
        raise ValueError("downside_pct must be positive (fraction loss to stop)")

    notes: list[str] = []

    full_kelly = kelly_fraction(win_probability, upside_pct, downside_pct)
    fractional_kelly = full_kelly * kelly_multiplier
    notes.append(
        f"Full Kelly={full_kelly * 100:.1f}%, applied {kelly_multiplier:.2f}x = {fractional_kelly * 100:.1f}%"
    )

    vol_weight = vol_target_weight(asset_vol_annualized, target_vol_annualized, max_leverage=1.0)
    notes.append(
        f"Vol target {target_vol_annualized * 100:.0f}% / asset vol {asset_vol_annualized * 100:.1f}% "
        f"= {vol_weight * 100:.1f}%"
    )

    weight_from_risk_cap = max_risk_pct_of_aum / downside_pct
    notes.append(
        f"Risk cap {max_risk_pct_of_aum * 100:.1f}% AUM / stop loss {downside_pct * 100:.1f}% "
        f"= {weight_from_risk_cap * 100:.1f}%"
    )

    target_weight = min(fractional_kelly, vol_weight, weight_from_risk_cap)
    capped_weight = min(target_weight, max_position_pct)
    if capped_weight < target_weight:
        notes.append(f"Hard cap {max_position_pct * 100:.0f}% applied")

    target_dollars = aum * capped_weight
    shares = int(target_dollars / price) if price > 0 else 0
    actual_dollars = shares * price
    actual_weight = actual_dollars / aum if aum > 0 else 0.0

    stop_price = price * (1 - downside_pct)
    risk_dollars = shares * (price - stop_price)
    risk_pct = risk_dollars / aum if aum > 0 else 0.0

    return SizingPlan(
        aum=aum,
        price=price,
        target_weight_pct=target_weight * 100,
        capped_weight_pct=capped_weight * 100,
        target_dollars=target_dollars,
        shares=shares,
        actual_dollars=actual_dollars,
        actual_weight_pct=actual_weight * 100,
        stop_price=stop_price,
        risk_dollars=risk_dollars,
        risk_pct_of_aum=risk_pct * 100,
        kelly_fraction=full_kelly,
        vol_target_weight=vol_weight,
        notes=tuple(notes),
    )


def format_sizing_report(plan: SizingPlan) -> str:
    lines = [
        "## Position Sizing",
        f"- AUM: {plan.aum:,.0f}",
        f"- Price: {plan.price:.2f}",
        f"- Target weight (min of Kelly/Vol/RiskCap): {plan.target_weight_pct:.1f}%",
        f"- After concentration cap: {plan.capped_weight_pct:.1f}%",
        f"- Shares to buy: {plan.shares}",
        f"- Dollars deployed: {plan.actual_dollars:,.0f} "
        f"({plan.actual_weight_pct:.1f}% of AUM)",
        f"- Hard stop: {plan.stop_price:.2f}",
        f"- Max loss if stopped out: {plan.risk_dollars:,.0f} "
        f"({plan.risk_pct_of_aum:.2f}% of AUM)",
        "",
        "### How each constraint was computed",
    ]
    lines.extend(f"- {note}" for note in plan.notes)
    return "\n".join(lines)
