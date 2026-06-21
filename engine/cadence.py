"""Recording-cadence gate for the fund-book forward OOS ledger.

Pure date arithmetic: "is it time to record the next OOS snapshot?" so a scheduled job records on a
disciplined ~monthly cadence (the fund-book analog of the IDEAL line's validated 21-business-day gate)
instead of every run. Business days = Mon–Fri; no market-holiday calendar (the cadence is approximate,
not settlement-critical — holiday awareness is deferred). Makes no investment claim; it only paces
recording.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


def _validate_cadence(cadence_business_days: int) -> None:
    if cadence_business_days < 1:
        raise ValueError(f"cadence_business_days must be >= 1 (got {cadence_business_days})")


def business_days_between(start: date, end: date) -> int:
    """Business days (Mon–Fri) AFTER `start`, up to and including `end`. `end <= start` -> 0
    (never negative). Consecutive weekdays -> 1; a Fri→Mon span -> 1 (weekend skipped)."""
    if end <= start:
        return 0
    count = 0
    d = start
    while d < end:
        d += timedelta(days=1)
        if d.weekday() < 5:  # Mon=0 .. Fri=4
            count += 1
    return count


def is_due(last_recorded: date | None, today: date, *, cadence_business_days: int = 21) -> bool:
    """True if no prior record (T0 is always due) or at least `cadence_business_days` business days
    have elapsed since `last_recorded`."""
    _validate_cadence(cadence_business_days)
    if last_recorded is None:
        return True
    return business_days_between(last_recorded, today) >= cadence_business_days


def next_due_date(last_recorded: date, *, cadence_business_days: int = 21) -> date:
    """The earliest calendar date that is `cadence_business_days` business days after `last_recorded`
    (the date `is_due` flips True)."""
    _validate_cadence(cadence_business_days)
    d = last_recorded
    remaining = cadence_business_days
    while remaining > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            remaining -= 1
    return d


@dataclass(frozen=True)
class CadenceStatus:
    last_recorded: date | None
    today: date
    business_days_elapsed: int  # 0 when no prior record
    cadence_business_days: int
    due: bool
    business_days_until_due: int  # max(0, cadence - elapsed); 0 at T0


def cadence_status(
    last_recorded: date | None, today: date, *, cadence_business_days: int = 21
) -> CadenceStatus:
    """One-shot summary for a status line."""
    _validate_cadence(cadence_business_days)
    elapsed = 0 if last_recorded is None else business_days_between(last_recorded, today)
    due = is_due(last_recorded, today, cadence_business_days=cadence_business_days)
    until = 0 if last_recorded is None else max(0, cadence_business_days - elapsed)
    return CadenceStatus(
        last_recorded=last_recorded,
        today=today,
        business_days_elapsed=elapsed,
        cadence_business_days=cadence_business_days,
        due=due,
        business_days_until_due=until,
    )
