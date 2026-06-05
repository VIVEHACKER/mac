from __future__ import annotations

from dataclasses import dataclass

# Weights for blending the validated strategy's reliability signals into one 0-1 score.
# Walk-forward out-of-sample positivity is weighted highest because it is the most
# direct evidence that the edge survives unseen data; PSR/DSR add distributional and
# selection-bias correction. These mirror the gates in out/MASTER-REPORT-2008-2026.md.
_WF_WEIGHT = 0.40
_PSR_WEIGHT = 0.30
_DSR_WEIGHT = 0.30

# A name outside the validated universe has no proven forward edge, so its confidence
# is hard-capped here no matter how strong its (untrusted) cross-sectional rank looks.
_OUT_OF_UNIVERSE_CAP = 25.0

# Valuation dispersion -> multiplier on the final score (wide DCF/multiple disagreement
# means the fair-value anchor, and therefore the entry band, is less trustworthy).
_VALUATION_FACTOR = {"high": 1.0, "medium": 0.9, "low": 0.8}

_HIGH_BAND = 70.0
_MEDIUM_BAND = 45.0


@dataclass(frozen=True)
class ConfidenceBreakdown:
    score: float
    band: str
    reliability: float
    signal_strength: float
    in_validated_universe: bool
    reasons: tuple[str, ...]


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def calibrated_confidence(
    *,
    aqr_percentile: float,
    in_validated_universe: bool,
    wf_positive_rate: float,
    psr: float,
    dsr: float,
    valuation_confidence: str = "low",
    provisional: bool = False,
    out_of_universe_cap: float = _OUT_OF_UNIVERSE_CAP,
) -> ConfidenceBreakdown:
    """Calibrate a 0-100 confidence from real validation statistics, not a guess.

    confidence = signal_strength x strategy_reliability x valuation_factor, where
    ``signal_strength`` is the ticker's percentile within the validated universe and
    ``strategy_reliability`` blends the validated strategy's walk-forward positive
    rate, PSR and DSR. Names outside the validated universe are hard-capped low.

    ``valuation_confidence`` defaults to ``"low"`` so that callers which have NOT
    computed a credible multi-method fair value fail safe (the 0.8x factor) rather
    than silently inheriting the optimistic 1.0x. Pass ``"high"`` only when a credible
    fair value from >= 2 methods exists. ``provisional=True`` (the strategy's PSR/DSR
    are placeholders pending re-validation) prepends a visible warning to ``reasons``.
    """

    percentile = _clamp(aqr_percentile, 0.0, 100.0)
    wf = _clamp(wf_positive_rate, 0.0, 1.0)
    psr_v = _clamp(psr, 0.0, 1.0)
    dsr_v = _clamp(dsr, 0.0, 1.0)

    reliability = _WF_WEIGHT * wf + _PSR_WEIGHT * psr_v + _DSR_WEIGHT * dsr_v
    signal_strength = percentile / 100.0
    valuation_factor = _VALUATION_FACTOR.get(valuation_confidence, 0.8)

    raw_score = 100.0 * signal_strength * reliability * valuation_factor

    reasons: list[str] = []
    if in_validated_universe:
        score = raw_score
        reasons.append(
            f"top {100.0 - percentile:.0f}% of the validated universe (percentile {percentile:.0f})"
        )
    else:
        score = min(raw_score, out_of_universe_cap)
        reasons.append(
            "outside the validated universe — no proven forward edge, "
            f"confidence hard-capped at {out_of_universe_cap:.0f}"
        )

    reliability_note = (
        " ⚠️ PSR/DSR are provisional placeholders pending P3 re-validation" if provisional else ""
    )
    reasons.append(
        f"strategy reliability {reliability:.2f} "
        f"(WF {wf:.2f}, PSR {psr_v:.2f}, DSR {dsr_v:.2f}){reliability_note}"
    )
    if valuation_factor < 1.0:
        reasons.append(f"valuation dispersion '{valuation_confidence}' (x{valuation_factor:.2f})")

    score = _clamp(score, 0.0, 100.0)
    if score >= _HIGH_BAND:
        band = "high"
    elif score >= _MEDIUM_BAND:
        band = "medium"
    else:
        band = "low"

    return ConfidenceBreakdown(
        score=score,
        band=band,
        reliability=reliability,
        signal_strength=signal_strength,
        in_validated_universe=in_validated_universe,
        reasons=tuple(reasons),
    )
