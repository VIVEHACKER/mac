from __future__ import annotations

from dataclasses import dataclass
from statistics import mean, pstdev

from data.models import FundamentalRecord, PriceBar


@dataclass(frozen=True)
class FactorScore:
    symbol: str
    market: str
    value: float
    momentum: float
    quality: float
    composite: float


def rank_aqr_factors(
    bars_by_symbol: dict[str, list[PriceBar]],
    fundamentals_by_symbol: dict[str, FundamentalRecord],
    *,
    lookback: int = 126,
) -> list[FactorScore]:
    raw: list[tuple[str, str, float, float, float]] = []
    for symbol, bars in bars_by_symbol.items():
        ordered = sorted(bars, key=lambda bar: bar.ts)
        fundamentals = fundamentals_by_symbol.get(symbol.upper())
        if fundamentals is None or len(ordered) <= lookback:
            continue
        latest = ordered[-1]
        past = ordered[-1 - lookback]
        market_cap = (
            latest.close * fundamentals.shares_out
            if fundamentals.shares_out is not None and fundamentals.shares_out > 0
            else None
        )
        earnings_yield = (
            fundamentals.net_income / market_cap
            if market_cap and fundamentals.net_income is not None
            else 0.0
        )
        roe = (
            fundamentals.net_income / fundamentals.total_equity
            if fundamentals.total_equity and fundamentals.net_income is not None
            else 0.0
        )
        fcf_yield = (
            fundamentals.free_cash_flow / market_cap
            if market_cap and fundamentals.free_cash_flow is not None
            else 0.0
        )
        momentum = (latest.close / past.close) - 1.0
        quality = mean([roe, fcf_yield])
        raw.append((latest.symbol, latest.market, earnings_yield, momentum, quality))

    values = _z_scores([item[2] for item in raw])
    momentums = _z_scores([item[3] for item in raw])
    qualities = _z_scores([item[4] for item in raw])
    rows = [
        FactorScore(
            symbol=item[0],
            market=item[1],
            value=item[2],
            momentum=item[3],
            quality=item[4],
            composite=values[index] + momentums[index] + qualities[index],
        )
        for index, item in enumerate(raw)
    ]
    return sorted(rows, key=lambda row: row.composite, reverse=True)


def _z_scores(values: list[float]) -> list[float]:
    if not values:
        return []
    sigma = pstdev(values)
    if sigma == 0:
        return [0.0 for _ in values]
    mu = mean(values)
    return [(value - mu) / sigma for value in values]
