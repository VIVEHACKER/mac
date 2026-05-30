from __future__ import annotations

from datetime import date, datetime

from data.models import FundamentalRecord
from engine.compounder_metrics import (
    revenue_cagr,
    revenue_growth_acceleration,
    eps_growth,
)


def _rec(year: int, revenue=None, net_income=None, eps=None, **kw) -> FundamentalRecord:
    return FundamentalRecord(
        symbol="T",
        market="us",
        period_end=date(year, 12, 31),
        asof_ts=datetime(year + 1, 3, 1),
        revenue=revenue,
        net_income=net_income,
        eps=eps,
        **kw,
    )


def test_revenue_cagr_three_year():
    recs = [
        _rec(2020, revenue=100.0),
        _rec(2021, revenue=130.0),
        _rec(2022, revenue=169.0),
        _rec(2023, revenue=219.7),
    ]
    # 100 -> 219.7 over 3y ≈ 30%
    assert revenue_cagr(recs, years=3) == __import__("pytest").approx(0.30, abs=1e-3)


def test_revenue_cagr_returns_none_when_history_short():
    recs = [_rec(2022, revenue=100.0), _rec(2023, revenue=130.0)]
    assert revenue_cagr(recs, years=3) is None


def test_revenue_cagr_none_on_nonpositive_start():
    recs = [_rec(2020, revenue=0.0), _rec(2023, revenue=100.0)]
    assert revenue_cagr(recs, years=3) is None


def test_revenue_growth_acceleration_positive():
    # YoY: 2022/2021-1 = 0.30 ; 2023/2022-1 = 0.40 -> accel = +0.10
    recs = [_rec(2021, revenue=100.0), _rec(2022, revenue=130.0), _rec(2023, revenue=182.0)]
    assert revenue_growth_acceleration(recs) == __import__("pytest").approx(0.10, abs=1e-9)


def test_eps_growth_handles_sign():
    recs = [_rec(2020, eps=-1.0), _rec(2023, eps=1.0)]
    # (1 - (-1)) / abs(-1) = 2.0
    assert eps_growth(recs, years=3) == __import__("pytest").approx(2.0, abs=1e-9)
