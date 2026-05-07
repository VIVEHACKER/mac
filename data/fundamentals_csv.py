from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path

from data.models import FundamentalRecord


def load_fundamentals_csv(path: Path) -> list[FundamentalRecord]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    return [_fundamental_from_row(row) for row in rows]


def _fundamental_from_row(row: dict[str, str]) -> FundamentalRecord:
    return FundamentalRecord(
        symbol=_required(row, "symbol"),
        market=row.get("market") or "us",
        period_end=date.fromisoformat(_required(row, "period_end")),
        asof_ts=datetime.fromisoformat(_required(row, "asof_ts")),
        revenue=_optional_float(row, "revenue"),
        operating_income=_optional_float(row, "operating_income"),
        net_income=_optional_float(row, "net_income"),
        free_cash_flow=_optional_float(row, "free_cash_flow"),
        total_assets=_optional_float(row, "total_assets"),
        total_equity=_optional_float(row, "total_equity"),
        total_debt=_optional_float(row, "total_debt"),
        shares_out=_optional_float(row, "shares_out"),
        eps=_optional_float(row, "eps"),
        source=row.get("source") or "csv:fundamentals",
    )


def _optional_float(row: dict[str, str], key: str) -> float | None:
    value = row.get(key)
    if value is None or value == "":
        return None
    return float(value)


def _required(row: dict[str, str], key: str) -> str:
    value = row.get(key)
    if not value:
        raise ValueError(f"fundamentals CSV missing required column value: {key}")
    return value
