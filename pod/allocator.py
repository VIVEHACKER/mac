from __future__ import annotations


def vol_target_allocations(strategy_vols: dict[str, float], *, total_risk_budget: float = 1.0) -> dict[str, float]:
    inverse = {name: 1 / vol for name, vol in strategy_vols.items() if vol > 0}
    total = sum(inverse.values())
    if total <= 0:
        return {}
    return {name: total_risk_budget * value / total for name, value in inverse.items()}


def apply_regime_tilt(weights: dict[str, float], *, risk_on: bool) -> dict[str, float]:
    if not weights:
        return {}
    tilted = {
        name: weight * (1.2 if risk_on and "momentum" in name else 0.8 if not risk_on and "momentum" in name else 1.0)
        for name, weight in weights.items()
    }
    total = sum(tilted.values())
    return {name: weight / total for name, weight in tilted.items()}
