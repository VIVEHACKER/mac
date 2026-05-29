from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DrillRecord:
    strategy_id: str
    mode: str
    day: date
    passed: bool
    submitted_count: int = 0
    blocked_count: int = 0
    notes: str = ""
    ts: datetime | None = None


@dataclass(frozen=True)
class DrillSummary:
    strategy_id: str
    as_of: date
    paper_consecutive_days: int
    shadow_consecutive_days: int
    required_paper_days: int
    required_shadow_days: int

    @property
    def passed(self) -> bool:
        return (
            self.paper_consecutive_days >= self.required_paper_days
            and self.shadow_consecutive_days >= self.required_shadow_days
        )

    @property
    def reasons(self) -> tuple[str, ...]:
        reasons: list[str] = []
        if self.paper_consecutive_days < self.required_paper_days:
            reasons.append(
                f"paper drill days {self.paper_consecutive_days} < {self.required_paper_days}"
            )
        if self.shadow_consecutive_days < self.required_shadow_days:
            reasons.append(
                f"shadow drill days {self.shadow_consecutive_days} < {self.required_shadow_days}"
            )
        return tuple(reasons)


class DrillLog:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: DrillRecord) -> None:
        self._append(
            {
                "record_type": "live_drill",
                "strategy_id": record.strategy_id,
                "mode": _normalize_mode(record.mode),
                "day": record.day.isoformat(),
                "ts": (record.ts or datetime.now(UTC)).isoformat(),
                "payload": _dataclass_payload(record),
            }
        )

    def summary(
        self,
        strategy_id: str,
        *,
        as_of: date,
        required_paper_days: int,
        required_shadow_days: int,
    ) -> DrillSummary:
        return DrillSummary(
            strategy_id=strategy_id,
            as_of=as_of,
            paper_consecutive_days=self.consecutive_passed_days(strategy_id, "paper", as_of),
            shadow_consecutive_days=self.consecutive_passed_days(strategy_id, "shadow", as_of),
            required_paper_days=required_paper_days,
            required_shadow_days=required_shadow_days,
        )

    def consecutive_passed_days(self, strategy_id: str, mode: str, as_of: date) -> int:
        passed_by_day: dict[date, bool] = {}
        target_strategy = strategy_id.strip()
        target_mode = _normalize_mode(mode)
        for row in self.rows():
            if str(row.get("strategy_id", "")).strip() != target_strategy:
                continue
            if _normalize_mode(str(row.get("mode", ""))) != target_mode:
                continue
            payload = row.get("payload") or {}
            day = date.fromisoformat(str(row.get("day") or payload.get("day")))
            passed_by_day[day] = bool(payload.get("passed", False))

        count = 0
        cursor = as_of
        while passed_by_day.get(cursor, False):
            count += 1
            cursor -= timedelta(days=1)
        return count

    def rows(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [
            json.loads(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def _append(self, row: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def _normalize_mode(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in {"paper", "shadow"}:
        raise ValueError("drill mode must be paper or shadow")
    return normalized


def _dataclass_payload(value: DrillRecord) -> dict[str, Any]:
    payload = asdict(value)
    payload["mode"] = _normalize_mode(value.mode)
    payload["day"] = value.day.isoformat()
    payload["ts"] = (value.ts or datetime.now(UTC)).isoformat()
    return payload
