from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from data.models import DelistingReturn


def load_delisting_returns_csv(path: Path) -> list[DelistingReturn]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    return [_delisting_from_row(row, source_path=path) for row in rows]


def _delisting_from_row(row: dict[str, str], *, source_path: Path) -> DelistingReturn:
    symbol = _required(row, "symbol")
    market = row.get("market") or "us"
    return_pct = float(_required(row, "return_pct"))
    return DelistingReturn(
        symbol=symbol,
        market=market,
        ts=date.fromisoformat(_required(row, "ts")),
        return_pct=return_pct,
        source=row.get("source") or f"csv:{source_path.name}",
        confidence=row.get("confidence") or "high",
    )


def _required(row: dict[str, str], key: str) -> str:
    value = row.get(key)
    if not value:
        raise ValueError(f"delisting CSV missing required column value: {key}")
    return value
