from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from data.models import EntryPlanRecord


@dataclass(frozen=True)
class LadderStep:
    price: float
    weight: float
    reason: str


def average_true_range_pct(highs: list[float], lows: list[float], closes: list[float], window: int = 20) -> float:
    if len(closes) < 2:
        raise ValueError("at least two closes are required")
    start = max(1, len(closes) - window)
    ranges = []
    for index in range(start, len(closes)):
        ranges.append(
            max(
                highs[index] - lows[index],
                abs(highs[index] - closes[index - 1]),
                abs(lows[index] - closes[index - 1]),
            )
        )
    atr = sum(ranges) / len(ranges)
    return atr / closes[-1] if closes[-1] else 0.0


def make_entry_plan(
    *,
    symbol: str,
    market: str,
    current_price: float,
    fair_value: float,
    atr_pct: float,
    asof_ts: datetime,
    margin_of_safety: float = 0.25,
    expected_holding_days: int = 180,
) -> EntryPlanRecord:
    if current_price <= 0 or fair_value <= 0:
        raise ValueError("current_price and fair_value must be positive")
    atr = max(atr_pct, 0.005)
    mos_floor = fair_value * (1 - margin_of_safety)
    ladder = [
        LadderStep(price=current_price * (1 - atr), weight=0.3, reason="first volatility dip"),
        LadderStep(price=current_price * (1 - 2 * atr), weight=0.4, reason="second volatility dip"),
        LadderStep(price=min(mos_floor, current_price * (1 - 3 * atr)), weight=0.3, reason="margin-of-safety floor"),
    ]
    avg_entry = sum(step.price * step.weight for step in ladder)
    stop_loss = min(step.price for step in ladder) * 0.92
    target_exit = fair_value * 1.15
    risk = max(avg_entry - stop_loss, 0.01)
    reward = max(target_exit - avg_entry, 0.0)
    return EntryPlanRecord(
        symbol=symbol.upper(),
        market=market.lower(),
        asof_ts=asof_ts,
        current_price=current_price,
        fair_value=fair_value,
        target_entry=avg_entry,
        stop_loss=stop_loss,
        target_exit=target_exit,
        ladder_json=json.dumps([step.__dict__ for step in ladder], ensure_ascii=False),
        risk_reward=reward / risk,
        expected_holding_days=expected_holding_days,
        source="valuation.entry",
    )
