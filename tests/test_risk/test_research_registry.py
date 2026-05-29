from __future__ import annotations

from trader.research_registry import ResearchRegistry, evaluate_promotion, make_evidence


def test_promotion_gate_blocks_weak_evidence() -> None:
    evidence = make_evidence(
        strategy_id="weak",
        parameter_label="M63",
        windows=3,
        positive_test_rate=0.4,
        average_test_annualized_excess=-0.01,
        worst_test_drawdown=0.5,
        fee_stress_passed=False,
        pit_audit_passed=False,
    )

    decision = evaluate_promotion(evidence)

    assert not decision.passed
    assert len(decision.reasons) >= 5


def test_registry_records_approved_strategy(tmp_path) -> None:
    registry = ResearchRegistry(tmp_path / "registry.jsonl")
    evidence = make_evidence(
        strategy_id="qqq-tlt-defensive",
        parameter_label="M63/R5/V21",
        windows=8,
        positive_test_rate=0.625,
        average_test_annualized_excess=0.0208,
        worst_test_drawdown=0.23,
        fee_stress_passed=True,
        pit_audit_passed=True,
    )
    decision = evaluate_promotion(evidence)

    registry.append(evidence, decision)

    assert decision.passed
    assert registry.approved_strategy_ids() == {"qqq-tlt-defensive"}


def test_registry_uses_latest_decision_for_approval(tmp_path) -> None:
    registry = ResearchRegistry(tmp_path / "registry.jsonl")
    approved = make_evidence(
        strategy_id="qqq-tlt-defensive",
        parameter_label="M63/R5/V21",
        windows=8,
        positive_test_rate=0.625,
        average_test_annualized_excess=0.0208,
        worst_test_drawdown=0.23,
        fee_stress_passed=True,
        pit_audit_passed=True,
    )
    blocked = make_evidence(
        strategy_id="qqq-tlt-defensive",
        parameter_label="M63/R5/V21/crash",
        windows=8,
        positive_test_rate=0.25,
        average_test_annualized_excess=-0.0035,
        worst_test_drawdown=0.3466,
        fee_stress_passed=False,
        pit_audit_passed=True,
    )

    registry.append(approved, evaluate_promotion(approved))
    registry.append(blocked, evaluate_promotion(blocked))

    assert not registry.strategy_is_approved("qqq-tlt-defensive")
    assert registry.approved_strategy_ids() == set()


def test_live_approval_requires_stress_evidence_even_after_research_approval(tmp_path) -> None:
    registry = ResearchRegistry(tmp_path / "registry.jsonl")
    evidence = make_evidence(
        strategy_id="research-approved-only",
        parameter_label="M63/R5/V21",
        windows=8,
        positive_test_rate=0.75,
        average_test_annualized_excess=0.03,
        worst_test_drawdown=0.20,
        fee_stress_passed=True,
        pit_audit_passed=True,
    )

    registry.append(evidence, evaluate_promotion(evidence))

    assert registry.strategy_is_approved("research-approved-only")
    issues = registry.live_approval_issues("research-approved-only")
    assert any("stress windows" in issue for issue in issues)
    assert any("worst stress return" in issue for issue in issues)


def test_live_approval_accepts_crash_stress_evidence(tmp_path) -> None:
    registry = ResearchRegistry(tmp_path / "registry.jsonl")
    evidence = make_evidence(
        strategy_id="live-approved",
        parameter_label="M63/R5/V21/crash",
        windows=8,
        positive_test_rate=0.75,
        average_test_annualized_excess=0.03,
        worst_test_drawdown=0.20,
        fee_stress_passed=True,
        pit_audit_passed=True,
        full_sample_annualized_return=0.18,
        full_sample_max_drawdown=0.25,
        stress_windows_tested=2,
        worst_stress_return=0.35,
        stress_passed=True,
    )

    registry.append(evidence, evaluate_promotion(evidence))

    assert registry.live_approval_issues("live-approved") == ()
