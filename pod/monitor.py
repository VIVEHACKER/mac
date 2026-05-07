from __future__ import annotations

from math import sqrt
from statistics import mean, pstdev


def strategy_health(returns: list[float]) -> dict[str, float]:
    if not returns:
        return {"return": 0.0, "sharpe": 0.0, "max_drawdown": 0.0}
    total_return = 1.0
    curve = [1.0]
    for item in returns:
        total_return *= 1 + item
        curve.append(total_return)
    volatility = pstdev(returns) if len(returns) > 1 else 0.0
    sharpe = mean(returns) / volatility * sqrt(252) if volatility else 0.0
    return {"return": total_return - 1.0, "sharpe": sharpe, "max_drawdown": _max_drawdown(curve)}


def _max_drawdown(values: list[float]) -> float:
    peak = values[0]
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        worst = max(worst, (peak - value) / peak if peak else 0.0)
    return worst
