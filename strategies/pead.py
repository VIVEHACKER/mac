from __future__ import annotations

from data.models import EarningsRecord

from ._base import StrategySignal


def pead_signals(records: list[EarningsRecord], *, min_surprise_pct: float = 5.0) -> list[StrategySignal]:
    signals: list[StrategySignal] = []
    for item in records:
        surprise = item.surprise_pct
        if surprise is None or abs(surprise) < min_surprise_pct:
            continue
        signals.append(
            StrategySignal(
                symbol=item.symbol,
                market=item.market,
                as_of=item.announce_ts.date(),
                score=surprise,
                direction="long" if surprise > 0 else "short",
                reason=f"EPS surprise {surprise:+.2f}%",
            )
        )
    return sorted(signals, key=lambda signal: abs(signal.score), reverse=True)
