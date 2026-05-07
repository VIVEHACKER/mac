from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from data.models import UniverseMember


def load_universe_members_csv(path: Path) -> list[UniverseMember]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    return [_member_from_row(row, source_path=path) for row in rows]


def _member_from_row(row: dict[str, str], *, source_path: Path) -> UniverseMember:
    universe = _required(row, "universe")
    symbol = _required(row, "symbol")
    market = row.get("market") or "us"
    start_date = date.fromisoformat(_required(row, "start_date"))
    raw_end = row.get("end_date") or ""
    return UniverseMember(
        universe=universe,
        symbol=symbol,
        market=market,
        start_date=start_date,
        end_date=date.fromisoformat(raw_end) if raw_end else None,
        source=row.get("source") or f"csv:{source_path.name}",
        confidence=row.get("confidence") or "high",
    )


def _required(row: dict[str, str], key: str) -> str:
    value = row.get(key)
    if not value:
        raise ValueError(f"universe CSV missing required column value: {key}")
    return value
