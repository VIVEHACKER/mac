"""Size-dependent slippage / market-impact model for realistic fills.

A flat per-trade bps haircut (the backtest's ``--slippage-bps``) ignores that a large
order moves the price against itself. This models the effective fill price as::

    half-spread (bps)  +  temporary impact ∝ sqrt(participation)

where ``participation = order shares / average daily volume (ADV)``. The square-root
impact law is the standard form (Almgren et al.); defaults are conservative for liquid
US large-caps. When ADV is unknown the full impact coefficient is charged (conservative).

OPT-IN by design: the validated AQR walk-forward used a flat fee/slippage assumption
(``fee0bps`` for the deploy candidate). Enabling this model in the paper/live path
changes the strategy's expected fill, so the backtest must be re-run WITH it and pass
the gate before it drives the deploy candidate — otherwise paper ≠ backtest and the
validated number no longer transfers. Default-off everywhere preserves that fidelity.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class SlippageModel:
    half_spread_bps: float = 1.0  # half the quoted bid-ask spread (paid on every fill)
    impact_coefficient_bps: float = 10.0  # impact in bps at 100% ADV participation
    max_slippage_bps: float = 100.0  # cap — a single order should not model >1% slip

    def slippage_bps(self, *, qty: float, adv: float | None) -> float:
        """Expected slippage (bps) for an order of ``qty`` shares against ``adv``.

        ``adv`` is average daily volume in shares; ``None``/0 (unknown liquidity) charges
        the full impact coefficient — the conservative assumption.
        """
        if qty <= 0:
            return 0.0
        if adv and adv > 0:
            impact = self.impact_coefficient_bps * math.sqrt(qty / adv)
        else:
            impact = self.impact_coefficient_bps
        return min(self.half_spread_bps + impact, self.max_slippage_bps)

    def fill_price(
        self, *, side: str, reference_price: float, qty: float, adv: float | None
    ) -> float:
        """Effective fill price: a buy pays UP, a sell receives DOWN, by the slippage bps."""
        if reference_price <= 0:
            raise ValueError("reference_price must be positive")
        factor = self.slippage_bps(qty=qty, adv=adv) / 10_000.0
        normalized_side = side.lower().strip()
        if normalized_side == "buy":
            return reference_price * (1.0 + factor)
        if normalized_side == "sell":
            return reference_price * (1.0 - factor)
        raise ValueError("side must be buy or sell")
