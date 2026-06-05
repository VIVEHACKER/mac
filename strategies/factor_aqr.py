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


@dataclass(frozen=True)
class RankedFactor:
    """A FactorScore enriched with its position inside the cross-sectional universe.

    The AQR composite is only meaningful relative to the whole universe (it is a
    cross-sectional Z-score sum), so a single ticker's conviction is its *rank*,
    not its absolute score. ``percentile`` is 100 at the top of the universe and
    0 at the bottom.
    """

    score: FactorScore
    rank: int
    universe_size: int
    percentile: float


def aqr_ranked(
    bars_by_symbol: dict[str, list[PriceBar]],
    fundamentals_by_symbol: dict[str, FundamentalRecord],
    *,
    lookback: int = 126,
) -> list[RankedFactor]:
    """Rank the entire universe with the validated AQR signal and attach percentiles.

    Wraps :func:`rank_aqr_factors` (the exact scorer used by the validated IDEAL
    walk-forward line) without modifying it. Returned list is sorted best-first.
    """

    scores = rank_aqr_factors(bars_by_symbol, fundamentals_by_symbol, lookback=lookback)
    universe_size = len(scores)
    ranked: list[RankedFactor] = []
    for index, score in enumerate(scores):
        rank = index + 1
        if universe_size <= 1:
            # A cross-sectional Z-score is undefined for a universe of one; report a
            # neutral percentile so a degenerate universe cannot drive top conviction.
            percentile = 50.0
        else:
            percentile = 100.0 * (universe_size - rank) / (universe_size - 1)
        ranked.append(
            RankedFactor(
                score=score,
                rank=rank,
                universe_size=universe_size,
                percentile=percentile,
            )
        )
    return ranked


def aqr_rank_for(
    symbol: str,
    bars_by_symbol: dict[str, list[PriceBar]],
    fundamentals_by_symbol: dict[str, FundamentalRecord],
    *,
    lookback: int = 126,
) -> RankedFactor | None:
    """Return one ticker's :class:`RankedFactor`, computed over the full universe.

    Returns ``None`` when the ticker is absent from the universe or lacks the
    history/fundamentals required to be scored.
    """

    target = symbol.upper()
    for ranked in aqr_ranked(bars_by_symbol, fundamentals_by_symbol, lookback=lookback):
        if ranked.score.symbol.upper() == target:
            return ranked
    return None


def _z_scores(values: list[float]) -> list[float]:
    if not values:
        return []
    sigma = pstdev(values)
    if sigma == 0:
        return [0.0 for _ in values]
    mu = mean(values)
    return [(value - mu) / sigma for value in values]
