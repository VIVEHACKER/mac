from __future__ import annotations

from data.models import ValuationRecord

from ._base import StrategySignal


def value_long_signals(records: list[ValuationRecord], *, min_rating: int = 2) -> list[StrategySignal]:
    return [
        StrategySignal(
            symbol=item.symbol,
            market=item.market,
            as_of=item.asof_date,
            score=float(item.rating),
            direction="long",
            reason=f"valuation rating {item.rating}, discount {item.discount_pct * 100:+.1f}%",
        )
        for item in sorted(records, key=lambda record: record.rating, reverse=True)
        if item.rating >= min_rating
    ]
