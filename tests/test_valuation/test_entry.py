from __future__ import annotations

from datetime import datetime

from valuation.composite import composite_fair_value
from valuation.dcf import discounted_cash_flow
from valuation.entry import average_true_range_pct, make_entry_plan
from valuation.score import discount_pct, rating_from_discount


def test_valuation_and_entry_plan() -> None:
    dcf = discounted_cash_flow(free_cash_flow=100, shares_out=10, net_debt=0)
    fair, dispersion = composite_fair_value({"dcf": dcf.fair_value, "multiple": dcf.fair_value * 1.1})
    disc = discount_pct(8, fair)
    plan = make_entry_plan(
        symbol="MSFT",
        market="us",
        current_price=8,
        fair_value=fair,
        atr_pct=average_true_range_pct([9, 10], [7, 8], [8, 9]),
        asof_ts=datetime(2025, 1, 2),
    )

    assert fair > 0
    assert dispersion >= 0
    assert rating_from_discount(disc) >= 1
    assert plan.stop_loss < plan.target_entry < plan.target_exit
