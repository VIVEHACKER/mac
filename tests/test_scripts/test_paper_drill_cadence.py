from __future__ import annotations

from datetime import date

from scripts.paper_drill_cadence import business_days_between, is_rebalance_due


def test_business_days_between_excludes_weekends() -> None:
    # Mon 2026-06-01 .. Mon 2026-06-08 = 5 business days (Mon→Fri + next Mon = 5 steps)
    assert business_days_between(date(2026, 6, 1), date(2026, 6, 8)) == 5
    # same day = 0
    assert business_days_between(date(2026, 6, 1), date(2026, 6, 1)) == 0
    # Fri → next Mon = 1 business day (the weekend is skipped)
    assert business_days_between(date(2026, 6, 5), date(2026, 6, 8)) == 1


def test_rebalance_due_when_no_ledger() -> None:
    # never rebalanced → always due
    assert is_rebalance_due(last_rebal=None, today=date(2026, 6, 6)) is True


def test_rebalance_not_due_before_21_business_days() -> None:
    # 2026-06-05 (Fri) + 20 business days = 2026-07-03; on 2026-07-02 only ~19 elapsed
    assert is_rebalance_due(last_rebal=date(2026, 6, 5), today=date(2026, 6, 19)) is False


def test_rebalance_due_after_21_business_days() -> None:
    # 21 business days after Fri 2026-06-05 lands on Mon 2026-07-06
    assert is_rebalance_due(last_rebal=date(2026, 6, 5), today=date(2026, 7, 6)) is True
    # and any later date stays due
    assert is_rebalance_due(last_rebal=date(2026, 6, 5), today=date(2026, 7, 20)) is True


def test_custom_cadence() -> None:
    assert is_rebalance_due(last_rebal=date(2026, 6, 1), today=date(2026, 6, 8), cadence=5) is True
    assert is_rebalance_due(last_rebal=date(2026, 6, 1), today=date(2026, 6, 5), cadence=5) is False
