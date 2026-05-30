"""Point-in-time compounder metrics. Pure functions over period_end-sorted
FundamentalRecords. Every function returns float | None and never raises on
missing/degenerate inputs (None = insufficient data)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta

from data.models import FundamentalRecord

_TOL_DAYS = 120


def _sorted(records: Sequence[FundamentalRecord]) -> list[FundamentalRecord]:
    return sorted(records, key=lambda r: r.period_end)


def _latest(records: Sequence[FundamentalRecord]) -> FundamentalRecord | None:
    s = _sorted(records)
    return s[-1] if s else None


def _record_years_before(
    records: Sequence[FundamentalRecord], years: float
) -> FundamentalRecord | None:
    s = _sorted(records)
    if len(s) < 2:
        return None
    target = s[-1].period_end - timedelta(days=round(365.25 * years))
    best, best_diff = None, None
    for r in s[:-1]:
        diff = abs((r.period_end - target).days)
        if best_diff is None or diff < best_diff:
            best, best_diff = r, diff
    if best is not None and best_diff is not None and best_diff <= _TOL_DAYS:
        return best
    return None


def revenue_cagr(records: Sequence[FundamentalRecord], years: int = 3) -> float | None:
    latest = _latest(records)
    start = _record_years_before(records, years)
    if latest is None or start is None or latest.revenue is None or start.revenue is None:
        return None
    if start.revenue <= 0 or latest.revenue <= 0:
        return None
    return (latest.revenue / start.revenue) ** (1.0 / years) - 1.0


def revenue_growth_acceleration(records: Sequence[FundamentalRecord]) -> float | None:
    latest = _latest(records)
    one = _record_years_before(records, 1)
    two = _record_years_before(records, 2)
    if None in (latest, one, two):
        return None
    if not all(r.revenue and r.revenue > 0 for r in (latest, one, two)):
        return None
    yoy_recent = latest.revenue / one.revenue - 1.0
    yoy_prior = one.revenue / two.revenue - 1.0
    return yoy_recent - yoy_prior


def eps_growth(records: Sequence[FundamentalRecord], years: int = 3) -> float | None:
    """Return signed EPS growth over *years* years: (latest - start) / abs(start).

    Note: when both start.eps and latest.eps are negative the formula can return a
    positive value (e.g. -2.0 → -1.0 yields +0.5, appearing as "improvement"); this
    metric is informational only and is not used as a score input.
    """
    latest = _latest(records)
    start = _record_years_before(records, years)
    if latest is None or start is None or latest.eps is None or start.eps is None:
        return None
    if start.eps == 0:
        return None
    return (latest.eps - start.eps) / abs(start.eps)
