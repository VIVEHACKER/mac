from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PromotionGate:
    min_windows: int = 8
    min_positive_test_rate: float = 0.60
    min_average_test_excess: float = 0.0
    max_worst_test_drawdown: float = 0.30
    require_fee_stress: bool = True
    require_pit_audit: bool = True
    min_full_sample_annualized_return: float | None = None
    max_full_sample_drawdown: float | None = None
    min_stress_windows: int = 0
    min_worst_stress_return: float | None = None
    require_stress_pass: bool = False


LIVE_PROMOTION_GATE = PromotionGate(
    min_stress_windows=2,
    min_worst_stress_return=0.30,
    require_stress_pass=True,
    max_full_sample_drawdown=0.35,
)


@dataclass(frozen=True)
class StrategyEvidence:
    strategy_id: str
    parameter_label: str
    generated_at: datetime
    windows: int
    positive_test_rate: float
    average_test_annualized_excess: float
    worst_test_drawdown: float
    fee_stress_passed: bool
    pit_audit_passed: bool
    command: str = ""
    source_commit: str = ""
    notes: str = ""
    full_sample_annualized_return: float | None = None
    full_sample_max_drawdown: float | None = None
    stress_windows_tested: int = 0
    worst_stress_return: float | None = None
    stress_passed: bool = False


@dataclass(frozen=True)
class PromotionDecision:
    passed: bool
    reasons: tuple[str, ...]


def evaluate_promotion(
    evidence: StrategyEvidence,
    gate: PromotionGate | None = None,
) -> PromotionDecision:
    gate = gate or PromotionGate()
    reasons: list[str] = []
    if evidence.windows < gate.min_windows:
        reasons.append(f"windows {evidence.windows} < {gate.min_windows}")
    if evidence.positive_test_rate < gate.min_positive_test_rate:
        reasons.append(
            f"positive test rate {evidence.positive_test_rate:.2%} < "
            f"{gate.min_positive_test_rate:.2%}"
        )
    if evidence.average_test_annualized_excess <= gate.min_average_test_excess:
        reasons.append(
            f"average test annualized excess {evidence.average_test_annualized_excess:.2%} "
            f"<= {gate.min_average_test_excess:.2%}"
        )
    if evidence.worst_test_drawdown > gate.max_worst_test_drawdown:
        reasons.append(
            f"worst test drawdown {evidence.worst_test_drawdown:.2%} > "
            f"{gate.max_worst_test_drawdown:.2%}"
        )
    if gate.require_fee_stress and not evidence.fee_stress_passed:
        reasons.append("fee stress did not pass")
    if gate.require_pit_audit and not evidence.pit_audit_passed:
        reasons.append("PIT audit did not pass")
    if gate.min_full_sample_annualized_return is not None:
        if evidence.full_sample_annualized_return is None:
            reasons.append("full-sample annualized return is missing")
        elif evidence.full_sample_annualized_return < gate.min_full_sample_annualized_return:
            reasons.append(
                f"full-sample annualized return {evidence.full_sample_annualized_return:.2%} < "
                f"{gate.min_full_sample_annualized_return:.2%}"
            )
    if gate.max_full_sample_drawdown is not None:
        if evidence.full_sample_max_drawdown is None:
            reasons.append("full-sample drawdown is missing")
        elif evidence.full_sample_max_drawdown > gate.max_full_sample_drawdown:
            reasons.append(
                f"full-sample drawdown {evidence.full_sample_max_drawdown:.2%} > "
                f"{gate.max_full_sample_drawdown:.2%}"
            )
    if evidence.stress_windows_tested < gate.min_stress_windows:
        reasons.append(
            f"stress windows {evidence.stress_windows_tested} < {gate.min_stress_windows}"
        )
    if gate.require_stress_pass and not evidence.stress_passed:
        reasons.append("stress windows did not pass")
    if gate.min_worst_stress_return is not None:
        if evidence.worst_stress_return is None:
            reasons.append("worst stress return is missing")
        elif evidence.worst_stress_return < gate.min_worst_stress_return:
            reasons.append(
                f"worst stress return {evidence.worst_stress_return:.2%} < "
                f"{gate.min_worst_stress_return:.2%}"
            )
    return PromotionDecision(passed=not reasons, reasons=tuple(reasons))


class ResearchRegistry:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, evidence: StrategyEvidence, decision: PromotionDecision) -> None:
        row = {
            "record_type": "strategy_evidence",
            "strategy_id": evidence.strategy_id,
            "generated_at": evidence.generated_at.isoformat(),
            "evidence": _json_payload(evidence),
            "decision": _json_payload(decision),
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")

    def approved_strategy_ids(self) -> set[str]:
        return {
            strategy_id
            for strategy_id in {str(row.get("strategy_id", "")) for row in self.rows()}
            if strategy_id and self.strategy_is_approved(strategy_id)
        }

    def latest_record(self, strategy_id: str) -> dict[str, Any] | None:
        target = strategy_id.strip()
        latest: dict[str, Any] | None = None
        for row in self.rows():
            if str(row.get("strategy_id", "")).strip() == target:
                latest = row
        return latest

    def strategy_is_approved(self, strategy_id: str) -> bool:
        latest = self.latest_record(strategy_id)
        if latest is None:
            return False
        decision = latest.get("decision") or {}
        return bool(decision.get("passed"))

    def live_approval_issues(
        self,
        strategy_id: str,
        gate: PromotionGate = LIVE_PROMOTION_GATE,
    ) -> tuple[str, ...]:
        latest = self.latest_record(strategy_id)
        if latest is None:
            return ("latest registry decision is not approved",)
        decision = latest.get("decision") or {}
        decision_reasons = tuple(str(item) for item in decision.get("reasons") or ())
        if not bool(decision.get("passed")):
            suffix = ": " + "; ".join(decision_reasons) if decision_reasons else ""
            return (f"latest registry decision is not approved{suffix}",)
        evidence = _evidence_from_row(latest)
        live_decision = evaluate_promotion(evidence, gate)
        return live_decision.reasons

    def rows(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [
            json.loads(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


def make_evidence(
    *,
    strategy_id: str,
    parameter_label: str,
    windows: int,
    positive_test_rate: float,
    average_test_annualized_excess: float,
    worst_test_drawdown: float,
    fee_stress_passed: bool,
    pit_audit_passed: bool,
    command: str = "",
    source_commit: str = "",
    notes: str = "",
    full_sample_annualized_return: float | None = None,
    full_sample_max_drawdown: float | None = None,
    stress_windows_tested: int = 0,
    worst_stress_return: float | None = None,
    stress_passed: bool = False,
) -> StrategyEvidence:
    return StrategyEvidence(
        strategy_id=strategy_id,
        parameter_label=parameter_label,
        generated_at=datetime.now(UTC),
        windows=windows,
        positive_test_rate=positive_test_rate,
        average_test_annualized_excess=average_test_annualized_excess,
        worst_test_drawdown=worst_test_drawdown,
        fee_stress_passed=fee_stress_passed,
        pit_audit_passed=pit_audit_passed,
        command=command,
        source_commit=source_commit,
        notes=notes,
        full_sample_annualized_return=full_sample_annualized_return,
        full_sample_max_drawdown=full_sample_max_drawdown,
        stress_windows_tested=stress_windows_tested,
        worst_stress_return=worst_stress_return,
        stress_passed=stress_passed,
    )


def _evidence_from_row(row: dict[str, Any]) -> StrategyEvidence:
    payload = row.get("evidence") or {}
    generated_at = _parse_datetime(str(payload.get("generated_at") or row.get("generated_at") or ""))
    return StrategyEvidence(
        strategy_id=str(payload.get("strategy_id") or row.get("strategy_id") or ""),
        parameter_label=str(payload.get("parameter_label") or ""),
        generated_at=generated_at,
        windows=int(payload.get("windows", 0) or 0),
        positive_test_rate=float(payload.get("positive_test_rate", 0.0) or 0.0),
        average_test_annualized_excess=float(
            payload.get("average_test_annualized_excess", 0.0) or 0.0
        ),
        worst_test_drawdown=float(payload.get("worst_test_drawdown", 0.0) or 0.0),
        fee_stress_passed=bool(payload.get("fee_stress_passed", False)),
        pit_audit_passed=bool(payload.get("pit_audit_passed", False)),
        command=str(payload.get("command", "") or ""),
        source_commit=str(payload.get("source_commit", "") or ""),
        notes=str(payload.get("notes", "") or ""),
        full_sample_annualized_return=_optional_float(
            payload.get("full_sample_annualized_return")
        ),
        full_sample_max_drawdown=_optional_float(payload.get("full_sample_max_drawdown")),
        stress_windows_tested=int(payload.get("stress_windows_tested", 0) or 0),
        worst_stress_return=_optional_float(payload.get("worst_stress_return")),
        stress_passed=bool(payload.get("stress_passed", False)),
    )


def _parse_datetime(value: str) -> datetime:
    if not value:
        return datetime.now(UTC)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _json_payload(value: Any) -> dict[str, Any]:
    payload = asdict(value)
    for key, item in list(payload.items()):
        if isinstance(item, datetime):
            payload[key] = item.isoformat()
    return payload
