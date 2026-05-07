from __future__ import annotations

from statistics import pstdev


def composite_fair_value(values: dict[str, float | None], weights: dict[str, float] | None = None) -> tuple[float, float]:
    clean = {name: value for name, value in values.items() if value is not None and value > 0}
    if not clean:
        raise ValueError("at least one positive fair value is required")
    selected_weights = weights or dict.fromkeys(clean, 1.0)
    weighted_sum = sum(clean[name] * selected_weights.get(name, 0.0) for name in clean)
    total_weight = sum(selected_weights.get(name, 0.0) for name in clean)
    if total_weight <= 0:
        raise ValueError("weights must sum to a positive value")
    fair = weighted_sum / total_weight
    dispersion = pstdev(clean.values()) / fair if len(clean) > 1 and fair else 0.0
    return fair, dispersion


def confidence_from_dispersion(dispersion_pct: float, peer_count: int = 0) -> str:
    if peer_count and peer_count < 5:
        return "low"
    if dispersion_pct < 0.15:
        return "high"
    if dispersion_pct < 0.35:
        return "medium"
    return "low"
