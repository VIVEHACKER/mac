from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class StrategySignal:
    symbol: str
    market: str
    as_of: date
    score: float
    direction: str
    reason: str
