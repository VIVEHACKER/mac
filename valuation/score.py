from __future__ import annotations


def discount_pct(current_price: float, fair_value: float) -> float:
    if current_price <= 0:
        raise ValueError("current_price must be positive")
    return (fair_value - current_price) / current_price


def rating_from_discount(value: float) -> int:
    if value >= 0.5:
        return 3
    if value >= 0.25:
        return 2
    if value >= 0.1:
        return 1
    if value <= -0.5:
        return -3
    if value <= -0.25:
        return -2
    if value <= -0.1:
        return -1
    return 0
