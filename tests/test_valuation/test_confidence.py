from __future__ import annotations

from valuation.confidence import ConfidenceBreakdown, calibrated_confidence


def test_top_validated_name_earns_high_confidence() -> None:
    result = calibrated_confidence(
        aqr_percentile=100.0,
        in_validated_universe=True,
        wf_positive_rate=0.87,
        psr=0.90,
        dsr=0.60,
        valuation_confidence="high",
    )

    assert isinstance(result, ConfidenceBreakdown)
    assert result.band == "high"
    assert result.score >= 70.0
    assert result.in_validated_universe is True


def test_default_valuation_confidence_is_low_fail_safe() -> None:
    # Omitting valuation_confidence must NOT silently apply the optimistic 1.0x factor.
    explicit_low = calibrated_confidence(
        aqr_percentile=100.0,
        in_validated_universe=True,
        wf_positive_rate=0.87,
        psr=0.90,
        dsr=0.60,
        valuation_confidence="low",
    )
    defaulted = calibrated_confidence(
        aqr_percentile=100.0,
        in_validated_universe=True,
        wf_positive_rate=0.87,
        psr=0.90,
        dsr=0.60,
    )

    assert defaulted.score == explicit_low.score


def test_provisional_reliability_is_flagged_in_reasons() -> None:
    result = calibrated_confidence(
        aqr_percentile=100.0,
        in_validated_universe=True,
        wf_positive_rate=0.87,
        psr=0.80,
        dsr=0.55,
        valuation_confidence="high",
        provisional=True,
    )

    assert any("provisional" in reason.lower() for reason in result.reasons)


def test_out_of_universe_is_capped_even_when_signal_is_strong() -> None:
    # A name outside the validated universe has NO proven edge -> must be capped low,
    # regardless of how strong its (untrusted) percentile looks.
    result = calibrated_confidence(
        aqr_percentile=100.0,
        in_validated_universe=False,
        wf_positive_rate=0.95,
        psr=0.99,
        dsr=0.80,
    )

    assert result.score <= 25.0
    assert result.band == "low"
    assert any("universe" in reason.lower() for reason in result.reasons)


def test_mid_universe_name_is_medium() -> None:
    result = calibrated_confidence(
        aqr_percentile=70.0,
        in_validated_universe=True,
        wf_positive_rate=0.87,
        psr=0.90,
        dsr=0.60,
        valuation_confidence="high",
    )

    assert result.band == "medium"
    assert 45.0 <= result.score < 70.0


def test_weak_strategy_reliability_drags_score_down() -> None:
    strong = calibrated_confidence(
        aqr_percentile=100.0,
        in_validated_universe=True,
        wf_positive_rate=0.90,
        psr=0.90,
        dsr=0.90,
    )
    weak = calibrated_confidence(
        aqr_percentile=100.0,
        in_validated_universe=True,
        wf_positive_rate=0.20,
        psr=0.20,
        dsr=0.10,
    )

    assert weak.score < strong.score
    assert weak.reliability < strong.reliability


def test_inputs_are_clamped_to_valid_ranges() -> None:
    result = calibrated_confidence(
        aqr_percentile=150.0,
        in_validated_universe=True,
        wf_positive_rate=1.5,
        psr=-0.2,
        dsr=2.0,
    )

    assert 0.0 <= result.score <= 100.0
    assert 0.0 <= result.reliability <= 1.0
