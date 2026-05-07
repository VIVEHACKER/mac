from __future__ import annotations

from data.models import FlowRecord
from strategies._base import StrategySignal

CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}


def foreign_flow_signal(
    records: list[FlowRecord],
    *,
    lookback: int = 5,
    allow_estimated: bool = False,
    min_confidence: str = "high",
) -> StrategySignal | None:
    if min_confidence not in CONFIDENCE_RANK:
        raise ValueError(f"unknown confidence level: {min_confidence}")
    minimum_rank = CONFIDENCE_RANK[min_confidence]
    foreign_rows = sorted(
        [
            item
            for item in records
            if item.investor == "foreign"
            and (allow_estimated or item.value_kind == "reported_value")
            and CONFIDENCE_RANK.get(item.confidence, -1) >= minimum_rank
        ],
        key=lambda item: item.ts,
    )
    if len(foreign_rows) < lookback:
        return None
    selected = foreign_rows[-lookback:]
    net = sum(item.net_value for item in selected)
    latest = selected[-1]
    return StrategySignal(
        symbol=latest.symbol,
        market=latest.market,
        as_of=latest.ts,
        score=net,
        direction="long" if net > 0 else "short",
        reason=f"foreign {lookback}d net flow {net:,.0f}",
    )
