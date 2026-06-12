"""VIX term-structure (VIX / VIX3M) regime signal — first of the signals/ backlog.

Backwardation (spot VIX above 3-month VIX) marks acute stress; the literature
hypothesis (mean reversion after panic) is that forward equity returns conditional on
backwardation are higher than normal. That hypothesis is NOT assumed here: the module
only measures; `scripts/vix_term_validation.py` judges the information content against
pre-declared bars and records the verdict in the research ledger.

ADVISORY ONLY either way: per the architecture's standing rule, a signal earns
influence over capital allocation exclusively through validation gates — this module
never feeds an order path directly.

Pure functions, no I/O — same discipline as signals/foreign_flow.py.
"""

from __future__ import annotations

from datetime import date

from strategies._base import StrategySignal

BACKWARDATION_THRESHOLD = 1.0  # VIX/VIX3M above this = stress (spot above the curve)


def term_ratio(vix: float, vix3m: float) -> float:
    """Spot-to-3-month VIX ratio. Raises on non-positive inputs (bad data, not a regime)."""
    if vix <= 0 or vix3m <= 0:
        raise ValueError(f"VIX inputs must be positive (got vix={vix}, vix3m={vix3m})")
    return vix / vix3m


def classify_term_structure(ratio: float, *, threshold: float = BACKWARDATION_THRESHOLD) -> str:
    """``"backwardation"`` (stress) when the ratio exceeds ``threshold``, else ``"contango"``."""
    return "backwardation" if ratio > threshold else "contango"


def vix_term_signal(
    as_of: date,
    vix: float,
    vix3m: float,
    *,
    threshold: float = BACKWARDATION_THRESHOLD,
) -> StrategySignal | None:
    """Regime flag for ``as_of``: emitted only on backwardation days, else ``None``.

    ``score`` is the backwardation depth (ratio − 1, > 0). ``direction`` carries the
    literature hypothesis (mean reversion → ``"long"`` equities) and is advisory until
    the validation gate confirms the information content.
    """
    ratio = term_ratio(vix, vix3m)
    if classify_term_structure(ratio, threshold=threshold) != "backwardation":
        return None
    return StrategySignal(
        symbol="SPY",
        market="us",
        as_of=as_of,
        score=ratio - 1.0,
        direction="long",
        reason=f"VIX term backwardation: VIX/VIX3M {ratio:.3f} > {threshold:.2f}",
    )
