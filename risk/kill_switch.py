from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KillSwitchResult:
    halted: bool
    reasons: tuple[str, ...]


def check_kill_switch(
    *,
    start_equity: float,
    current_equity: float,
    gross_exposure: float,
    max_daily_drawdown: float = 0.02,
    max_gross_exposure: float = 1.0,
    peak_equity: float | None = None,
    max_drawdown_from_peak: float = 0.25,
) -> KillSwitchResult:
    if start_equity <= 0:
        raise ValueError("start_equity must be positive")
    reasons: list[str] = []
    drawdown = (start_equity - current_equity) / start_equity
    if drawdown > max_daily_drawdown:
        reasons.append(
            f"daily drawdown {drawdown * 100:.2f}% exceeds {max_daily_drawdown * 100:.2f}%"
        )
    # Sleeve / trailing latch: catches a slow multi-day bleed the daily latch misses.
    if peak_equity is not None:
        if peak_equity <= 0:
            raise ValueError("peak_equity must be positive when provided")
        peak_drawdown = (peak_equity - current_equity) / peak_equity
        if peak_drawdown > max_drawdown_from_peak:
            reasons.append(
                f"drawdown from peak {peak_drawdown * 100:.2f}% exceeds "
                f"{max_drawdown_from_peak * 100:.2f}%"
            )
    if gross_exposure > max_gross_exposure:
        reasons.append(f"gross exposure {gross_exposure:.2f} exceeds {max_gross_exposure:.2f}")
    return KillSwitchResult(halted=bool(reasons), reasons=tuple(reasons))
