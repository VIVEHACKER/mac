"""COT positioning-extreme signal — contrarian on crowded speculators.

Third backlog signal, and the first NOT derived from price/vol: it reads CFTC futures
positioning (data/ingest/cot_sp500.py). The classic "COT index" places the current
non-commercial (large-speculator) net position within its trailing range, 0–100. The
contrarian hypothesis: speculators crowded LONG (index high) precede WEAK forward
returns; crowded SHORT (index low) precede STRONG ones — they are the trend-following
"dumb money" that tops/bottoms with the crowd.

The hypothesis is NOT assumed: scripts/cot_validation.py judges it against pre-declared
bars and records the verdict in the research ledger. ADVISORY only until then; pure
functions, no I/O.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from strategies._base import StrategySignal

INDEX_WINDOW = 156  # trailing weeks (~3y) — the standard COT-index lookback
EXTREME_HIGH = 90.0  # speculators crowded long → contrarian bearish
EXTREME_LOW = 10.0  # speculators crowded short → contrarian bullish


def cot_index(net_history: Sequence[float], *, window: int = INDEX_WINDOW) -> float | None:
    """Where the latest net position sits in its trailing ``window`` range, 0–100.

    ``None`` when there is less than ``window`` of history. When the window is flat
    (max == min) the position is mid-range by definition → 50.
    """
    if len(net_history) < window:
        return None
    tail = net_history[-window:]
    lo, hi = min(tail), max(tail)
    if hi == lo:
        return 50.0
    return (tail[-1] - lo) / (hi - lo) * 100.0


def cot_extreme_signal(
    as_of: date,
    net_history: Sequence[float],
    *,
    window: int = INDEX_WINDOW,
    high: float = EXTREME_HIGH,
    low: float = EXTREME_LOW,
    symbol: str = "SPY",
) -> StrategySignal | None:
    """Contrarian flag when the speculator COT index is at an extreme, else ``None``.

    index >= ``high`` → crowded long → ``direction="short"``; index <= ``low`` →
    crowded short → ``direction="long"``. ``score`` is the distance past the threshold
    (always > 0). Advisory until the validation gate confirms information content.
    """
    idx = cot_index(net_history, window=window)
    if idx is None:
        return None
    if idx >= high:
        direction, score = "short", idx - high
        note = f"speculators crowded long (COT index {idx:.0f} >= {high:.0f})"
    elif idx <= low:
        direction, score = "long", low - idx
        note = f"speculators crowded short (COT index {idx:.0f} <= {low:.0f})"
    else:
        return None
    return StrategySignal(
        symbol=symbol,
        market="us",
        as_of=as_of,
        score=score,
        direction=direction,
        reason=f"COT positioning extreme: {note}",
    )
