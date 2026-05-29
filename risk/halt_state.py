from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class HaltRecord:
    halted: bool
    reason: str
    source: str
    ts: datetime


class HaltStateStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def current(self) -> HaltRecord:
        if not self.path.exists():
            return HaltRecord(False, "", "default", datetime.now(UTC))
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        return HaltRecord(
            halted=bool(raw.get("halted", False)),
            reason=str(raw.get("reason", "")),
            source=str(raw.get("source", "")),
            ts=datetime.fromisoformat(raw["ts"]),
        )

    def activate(self, reason: str, *, source: str = "system") -> HaltRecord:
        record = HaltRecord(True, reason, source, datetime.now(UTC))
        self._write(record)
        return record

    def clear(self, reason: str, *, source: str = "manual") -> HaltRecord:
        record = HaltRecord(False, reason, source, datetime.now(UTC))
        self._write(record)
        return record

    def _write(self, record: HaltRecord) -> None:
        payload = asdict(record)
        payload["ts"] = record.ts.isoformat()
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

