from __future__ import annotations

from math import sqrt
from statistics import pstdev

from data.models import PriceBar


def inverse_vol_weights(
    bars_by_symbol: dict[str, list[PriceBar]],
    *,
    lookback: int = 63,
) -> dict[str, float]:
    risk_units: dict[str, float] = {}
    for symbol, bars in bars_by_symbol.items():
        ordered = sorted(bars, key=lambda bar: bar.ts)
        if len(ordered) <= lookback:
            continue
        returns = [
            (ordered[index].close / ordered[index - 1].close) - 1.0
            for index in range(len(ordered) - lookback, len(ordered))
        ]
        vol = pstdev(returns) * sqrt(252)
        if vol > 0:
            risk_units[symbol.upper()] = 1 / vol
    total = sum(risk_units.values())
    if total == 0:
        return {}
    return {symbol: value / total for symbol, value in risk_units.items()}
