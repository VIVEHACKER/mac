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


def _ratio(num: float | None, den: float | None, *, den_positive: bool = False) -> float | None:
    if num is None or den is None or den == 0:
        return None
    if den_positive and den <= 0:
        return None
    return num / den


def operating_margin(rec: FundamentalRecord) -> float | None:
    return _ratio(rec.operating_income, rec.revenue, den_positive=True)


def net_margin(rec: FundamentalRecord) -> float | None:
    return _ratio(rec.net_income, rec.revenue, den_positive=True)


def _ols_slope(ys: list[float]) -> float:
    n = len(ys)
    xs = list(range(n))
    mx = sum(xs) / n
    my = sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    if denom == 0:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / denom


def margin_trend(records: Sequence[FundamentalRecord]) -> float | None:
    margins = [m for r in _sorted(records) if (m := net_margin(r)) is not None]
    if len(margins) < 2:
        return None
    return _ols_slope(margins)


def roic(rec: FundamentalRecord) -> float | None:
    if rec.net_income is None or rec.total_equity is None or rec.total_debt is None:
        return None
    capital = rec.total_equity + rec.total_debt
    if capital <= 0:
        return None
    return rec.net_income / capital


def fcf_margin(rec: FundamentalRecord) -> float | None:
    return _ratio(rec.free_cash_flow, rec.revenue, den_positive=True)


def fcf_conversion(rec: FundamentalRecord) -> float | None:
    if rec.net_income is None or rec.net_income <= 0 or rec.free_cash_flow is None:
        return None
    return rec.free_cash_flow / rec.net_income
