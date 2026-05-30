from __future__ import annotations

from datetime import date, datetime

import pytest

from data.models import FundamentalRecord
from engine.compounder_metrics import (
    eps_growth,
    revenue_cagr,
    revenue_growth_acceleration,
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
    assert revenue_cagr(recs, years=3) == pytest.approx(0.30, abs=1e-3)


def test_revenue_cagr_returns_none_when_history_short():
    recs = [_rec(2022, revenue=100.0), _rec(2023, revenue=130.0)]
    assert revenue_cagr(recs, years=3) is None


def test_revenue_cagr_none_on_nonpositive_start():
    recs = [_rec(2020, revenue=0.0), _rec(2023, revenue=100.0)]
    assert revenue_cagr(recs, years=3) is None


def test_revenue_growth_acceleration_positive():
    # YoY: 2022/2021-1 = 0.30 ; 2023/2022-1 = 0.40 -> accel = +0.10
    recs = [_rec(2021, revenue=100.0), _rec(2022, revenue=130.0), _rec(2023, revenue=182.0)]
    assert revenue_growth_acceleration(recs) == pytest.approx(0.10, abs=1e-9)


def test_eps_growth_handles_sign():
    recs = [_rec(2020, eps=-1.0), _rec(2023, eps=1.0)]
    # (1 - (-1)) / abs(-1) = 2.0
    assert eps_growth(recs, years=3) == pytest.approx(2.0, abs=1e-9)


def test_eps_growth_both_negative():
    # -2.0 -> -1.0: (-1.0 - (-2.0)) / abs(-2.0) = 1.0/2.0 = 0.5
    # reads as "positive improvement" but is informational only
    recs = [_rec(2020, eps=-2.0), _rec(2023, eps=-1.0)]
    assert eps_growth(recs, years=3) == pytest.approx(0.5, abs=1e-9)


from engine.compounder_metrics import (  # noqa: E402
    operating_margin,
    net_margin,
    margin_trend,
    roic,
    fcf_margin,
    fcf_conversion,
)


def test_margins():
    r = _rec(2023, revenue=200.0, net_income=40.0, operating_income=60.0)
    assert net_margin(r) == 0.20
    assert operating_margin(r) == 0.30
    assert net_margin(_rec(2023, revenue=0.0, net_income=1.0)) is None


def test_margin_trend_slope_positive():
    # net margins 0.10, 0.20, 0.30 over x=0,1,2 -> OLS slope 0.10
    recs = [
        _rec(2021, revenue=100.0, net_income=10.0),
        _rec(2022, revenue=100.0, net_income=20.0),
        _rec(2023, revenue=100.0, net_income=30.0),
    ]
    assert margin_trend(recs) == pytest.approx(0.10, abs=1e-9)


def test_roic_and_fcf():
    r = _rec(
        2023,
        revenue=200.0,
        net_income=40.0,
        free_cash_flow=30.0,
        total_equity=100.0,
        total_debt=100.0,
    )
    assert roic(r) == pytest.approx(0.20, abs=1e-9)  # 40/(100+100)
    assert fcf_margin(r) == pytest.approx(0.15, abs=1e-9)  # 30/200
    assert fcf_conversion(r) == pytest.approx(0.75, abs=1e-9)  # 30/40
    assert fcf_conversion(_rec(2023, net_income=-5.0, free_cash_flow=10.0)) is None
    assert roic(_rec(2023, net_income=10.0, total_equity=0.0, total_debt=0.0)) is None


def test_record_tolerance_boundary():
    # Case 1: prior record exactly at target date (diff ≈ 0) → CAGR returned
    # latest=2023-12-31 rev=200, start=2020-12-31 rev=100, years=3
    # target = 2023-12-31 - 3*365.25 days ≈ 2020-12-30, diff = 1 day ≤ 120 → accepted
    recs_within = [
        _rec(2020, revenue=100.0),
        _rec(2023, revenue=200.0),
    ]
    result_within = revenue_cagr(recs_within, years=3)
    assert result_within is not None
    # 100 -> 200 over 3y ≈ (2)^(1/3) - 1 ≈ 0.2599
    assert result_within == pytest.approx((200.0 / 100.0) ** (1.0 / 3) - 1.0, abs=1e-9)

    # Case 2: prior record at 2020-06-30 → diff from ~2020-12-30 target ≈ 184 days > 120 → None
    recs_beyond = [
        FundamentalRecord(
            symbol="T",
            market="us",
            period_end=date(2020, 6, 30),
            asof_ts=datetime(2020, 9, 1),
            revenue=100.0,
        ),
        FundamentalRecord(
            symbol="T",
            market="us",
            period_end=date(2023, 12, 31),
            asof_ts=datetime(2024, 3, 1),
            revenue=200.0,
        ),
    ]
    assert revenue_cagr(recs_beyond, years=3) is None
