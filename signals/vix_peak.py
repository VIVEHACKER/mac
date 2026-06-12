"""VIX peak-decline signal — stress resolving, second of the signals/ backlog.

Condition (constants DECLARED 2026-06-13, before any test run — no grid was searched):
the trailing 21-day VIX maximum reached at least 30 (a genuine stress episode, the
classic threshold) AND the current VIX has retreated to at most 80% of that peak
(a material decline). Hypothesis: the panic is resolving → forward equity returns
above unconditional. As with vix_term, the hypothesis is NOT assumed:
`scripts/vix_peak_validation.py` judges it against the same pre-declared bars and the
verdict lands in the research ledger.

ADVISORY ONLY either way — signals earn influence over capital exclusively through
validation gates. Pure functions, no I/O.
"""

from __future__ import annotations

from datetime import date

from strategies._base import StrategySignal

PEAK_WINDOW = 21  # trading days over which the stress peak is measured
STRESS_LEVEL = 30.0  # the trailing peak must reach this for the episode to count
DECLINE_RATIO = 0.8  # current VIX must be <= peak * this (>=20% retreat)


def peak_decline(vix_window: list[float]) -> tuple[float, float] | None:
    """(trailing_peak, current/peak) for the window (current = last element).

    Returns ``None`` when the window is too short or contains non-positive values
    (bad data is not a regime).
    """
    if len(vix_window) < PEAK_WINDOW:
        return None
    tail = vix_window[-PEAK_WINDOW:]
    if min(tail) <= 0:
        return None
    peak = max(tail)
    return peak, tail[-1] / peak


def vix_peak_signal(
    as_of: date,
    vix_window: list[float],
    *,
    stress_level: float = STRESS_LEVEL,
    decline_ratio: float = DECLINE_RATIO,
) -> StrategySignal | None:
    """Stress-resolving flag: emitted only when a >=``stress_level`` peak inside the
    trailing window has retreated to <=``decline_ratio`` of itself.

    ``score`` is the depth of the retreat (1 − current/peak, larger = more resolved).
    ``direction`` carries the hypothesis (``"long"`` equities) — advisory until gated.
    """
    measured = peak_decline(vix_window)
    if measured is None:
        return None
    peak, ratio = measured
    if peak < stress_level or ratio > decline_ratio:
        return None
    return StrategySignal(
        symbol="SPY",
        market="us",
        as_of=as_of,
        score=1.0 - ratio,
        direction="long",
        reason=(
            f"VIX peak-decline: trailing {PEAK_WINDOW}d peak {peak:.1f} >= {stress_level:.0f}, "
            f"now {ratio:.0%} of peak (<= {decline_ratio:.0%})"
        ),
    )
