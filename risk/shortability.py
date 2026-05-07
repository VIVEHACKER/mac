from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass(frozen=True)
class ShortAvailability:
    symbol: str
    market: str
    asof_date: date
    shortable: bool
    borrow_fee_bps: float | None = None
    source: str = ""
    confidence: str = "low"


@dataclass(frozen=True)
class ShortabilityCheck:
    symbol: str
    market: str
    asof_date: date
    passed: bool
    borrow_fee_bps: float | None
    source: str
    confidence: str
    warnings: tuple[str, ...]
    reasons: tuple[str, ...]


def load_short_availability_csv(path: Path) -> list[ShortAvailability]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = [_normalize_row(row) for row in reader]
    return [_availability_from_row(row, source_path=path) for row in rows]


def check_shortability(
    symbol: str,
    market: str,
    rows: list[ShortAvailability],
    *,
    asof_date: date,
    max_borrow_fee_bps: float = 500.0,
    require_row: bool = False,
    max_age_days: int = 2,
    min_confidence: str = "medium",
) -> ShortabilityCheck:
    if max_borrow_fee_bps < 0:
        raise ValueError("max_borrow_fee_bps must be >= 0")
    if max_age_days < 0:
        raise ValueError("max_age_days must be >= 0")
    required_confidence = min_confidence.lower()
    if required_confidence not in _CONFIDENCE_RANK:
        raise ValueError("min_confidence must be one of low, medium, high")
    target_symbol = symbol.upper()
    target_market = market.lower()
    candidates = [
        row
        for row in rows
        if row.symbol.upper() == target_symbol
        and row.market.lower() == target_market
        and row.asof_date <= asof_date
    ]
    if not candidates:
        warning = f"{target_symbol}: no shortability row at or before {asof_date}"
        missing_reasons = (warning,) if require_row else ()
        warnings = () if require_row else (warning,)
        return ShortabilityCheck(
            symbol=target_symbol,
            market=target_market,
            asof_date=asof_date,
            passed=not missing_reasons,
            borrow_fee_bps=None,
            source="missing",
            confidence="missing",
            warnings=warnings,
            reasons=missing_reasons,
        )

    latest = max(candidates, key=lambda item: item.asof_date)
    failure_reasons: list[str] = []
    age_days = (asof_date - latest.asof_date).days
    if age_days > max_age_days:
        failure_reasons.append(f"{target_symbol}: shortability row is {age_days} days old")
    latest_confidence = latest.confidence.lower()
    if _CONFIDENCE_RANK.get(latest_confidence, 0) < _CONFIDENCE_RANK[required_confidence]:
        failure_reasons.append(
            f"{target_symbol}: confidence {latest.confidence} < required {required_confidence}"
        )
    if not latest.shortable:
        failure_reasons.append(f"{target_symbol}: not shortable")
    if latest.borrow_fee_bps is not None and latest.borrow_fee_bps > max_borrow_fee_bps:
        failure_reasons.append(
            f"{target_symbol}: borrow fee {latest.borrow_fee_bps:.2f} bps > "
            f"limit {max_borrow_fee_bps:.2f} bps"
        )
    return ShortabilityCheck(
        symbol=target_symbol,
        market=target_market,
        asof_date=latest.asof_date,
        passed=not failure_reasons,
        borrow_fee_bps=latest.borrow_fee_bps,
        source=latest.source or "shortability-csv",
        confidence=latest_confidence,
        warnings=(),
        reasons=tuple(failure_reasons),
    )


def _availability_from_row(row: dict[str, str], *, source_path: Path) -> ShortAvailability:
    symbol = _required(row, "symbol").upper()
    market = (row.get("market") or "us").lower()
    asof_value = row.get("asof_date") or row.get("date")
    asof_date = date.fromisoformat(asof_value) if asof_value else date.min
    return ShortAvailability(
        symbol=symbol,
        market=market,
        asof_date=asof_date,
        shortable=_parse_bool(_required(row, "shortable")),
        borrow_fee_bps=_optional_float(row.get("borrow_fee_bps")),
        source=row.get("source") or f"csv:{source_path.name}",
        confidence=(row.get("confidence") or "low").lower(),
    )


def _normalize_row(row: dict[str, str]) -> dict[str, str]:
    return {key.strip().lower(): value.strip() for key, value in row.items() if key is not None}


def _required(row: dict[str, str], key: str) -> str:
    value = row.get(key)
    if not value:
        raise ValueError(f"shortability CSV missing required column value: {key}")
    return value


def _optional_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    return float(value.replace(",", ""))


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y"}:
        return True
    if normalized in {"0", "false", "no", "n"}:
        return False
    raise ValueError(f"invalid boolean value: {value}")


_CONFIDENCE_RANK = {"low": 1, "medium": 2, "high": 3}
