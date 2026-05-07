from __future__ import annotations

from dataclasses import dataclass

from data.models import PriceBar


@dataclass(frozen=True)
class MomentumSignal:
    ts: object
    close: float
    lookback_return: float
    position: float


def build_time_series_momentum_signals(
    bars: list[PriceBar],
    lookback: int = 126,
    threshold: float = 0.0,
) -> list[MomentumSignal]:
    if lookback < 1:
        raise ValueError("lookback must be >= 1")
    ordered = sorted(bars, key=lambda bar: bar.ts)
    signals: list[MomentumSignal] = []
    for index, bar in enumerate(ordered):
        if index < lookback:
            signals.append(MomentumSignal(bar.ts, bar.close, 0.0, 0.0))
            continue
        past_close = ordered[index - lookback].close
        lookback_return = (bar.close / past_close) - 1.0
        position = 1.0 if lookback_return > threshold else 0.0
        signals.append(MomentumSignal(bar.ts, bar.close, lookback_return, position))
    return signals
