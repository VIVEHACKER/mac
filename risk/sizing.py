"""Survival position sizing for the live / long-sleeve order path.

A position is sized to the SMALLEST weight implied by three constraints, then a hard concentration
cap, so a single name going to zero cannot exceed ~8% of the fund (the user holds through
drawdowns rather than stopping out, so the hard cap — not a stop — is the survival guarantee):

  1. fractional (half) Kelly      — bet more only with a real, well-estimated edge
  2. volatility target            — scale a high-vol name down to a target portfolio vol
  3. risk-of-loss cap             — limit the adverse-move loss to <= max_risk_pct_of_aum
  4. hard concentration cap        — <= max_position_pct of AUM regardless of the above

Formulas (kelly_fraction, vol_target_weight, min-composition) are ported verbatim from
trading-copilot/trading_copilot/sizing.py, which stays advisory-only; this module is the single
source of truth for the LIVE path. Leverage 0 is preserved (vol_target_weight max_leverage=1.0 and
the hard cap keep every weight <= 1.0).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SizingResult:
    qty: int
    target_weight: float  # smallest of the kelly/vol/risk constraints (pre hard cap), fraction
    capped_weight: float  # after the hard concentration cap, fraction
    actual_weight: float  # realized weight after integer-share flooring, fraction
    risk_pct_of_aum: float  # adverse-move (downside_pct) loss as a fraction of AUM
    binding: str  # which constraint set the size: kelly | vol | risk_cap | hard_cap


def kelly_fraction(win_probability: float, win_return: float, loss_return: float) -> float:
    """Full Kelly fraction. win_return / loss_return are absolute fractional outcomes
    (0.5 = +50%, 0.3 = -30%). Returns 0 for a non-positive or degenerate edge."""
    if win_probability <= 0 or win_probability >= 1:
        return 0.0
    if win_return <= 0 or loss_return <= 0:
        return 0.0
    p = win_probability
    q = 1.0 - p
    b = win_return / loss_return
    return max(0.0, (b * p - q) / b)


def vol_target_weight(
    asset_vol_annualized: float,
    target_vol_annualized: float,
    max_leverage: float = 1.0,
) -> float:
    """Weight needed for the asset's vol to match the target portfolio vol, capped at max_leverage
    (1.0 keeps the book unlevered). Returns 0 for non-positive vol."""
    if asset_vol_annualized <= 0:
        return 0.0
    return min(target_vol_annualized / asset_vol_annualized, max_leverage)


def size_position(
    aum: float,
    price: float,
    *,
    asset_vol_annualized: float,
    downside_pct: float,
    win_probability: float,
    upside_pct: float,
    kelly_multiplier: float = 0.5,
    max_position_pct: float = 0.08,
    max_risk_pct_of_aum: float = 0.02,
    target_vol_annualized: float = 0.35,
) -> SizingResult:
    """Size a long position to the smallest of half-Kelly / vol-target / risk-cap, then the hard
    concentration cap. ``downside_pct`` is the adverse move used to bound per-name risk (the user
    does not stop out, so it shrinks higher-risk names; the hard cap is the zero-loss backstop)."""
    if aum <= 0 or price <= 0:
        raise ValueError("aum and price must be positive")
    if downside_pct <= 0:
        raise ValueError("downside_pct must be positive")

    candidates = {
        "kelly": kelly_fraction(win_probability, upside_pct, downside_pct) * kelly_multiplier,
        "vol": vol_target_weight(asset_vol_annualized, target_vol_annualized, max_leverage=1.0),
        "risk_cap": max_risk_pct_of_aum / downside_pct,
    }
    binding = min(candidates, key=lambda k: candidates[k])
    target_weight = candidates[binding]
    capped_weight = min(target_weight, max_position_pct)
    if capped_weight < target_weight:
        binding = "hard_cap"

    qty = int(aum * capped_weight / price)  # floor: a name too small to size to 1 share -> 0
    actual_dollars = qty * price
    actual_weight = actual_dollars / aum
    risk_pct_of_aum = actual_dollars * downside_pct / aum
    return SizingResult(
        qty=qty,
        target_weight=target_weight,
        capped_weight=capped_weight,
        actual_weight=actual_weight,
        risk_pct_of_aum=risk_pct_of_aum,
        binding=binding,
    )
