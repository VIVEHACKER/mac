from __future__ import annotations

from datetime import date, timedelta

import pytest

from engine.cadence import (
    business_days_between,
    cadence_status,
    is_due,
    next_due_date,
)

# --------------------------------------------------------------------------- #
# business_days_between
# --------------------------------------------------------------------------- #


def test_same_day_is_zero():
    assert business_days_between(date(2026, 6, 22), date(2026, 6, 22)) == 0


def test_consecutive_weekdays_is_one():
    # Mon 2026-06-22 -> Tue 2026-06-23
    assert business_days_between(date(2026, 6, 22), date(2026, 6, 23)) == 1


def test_friday_to_monday_skips_weekend():
    # Fri 2026-06-19 -> Mon 2026-06-22 (Sat/Sun skipped) = 1 business day
    assert business_days_between(date(2026, 6, 19), date(2026, 6, 22)) == 1


def test_week_to_week_is_five():
    # Mon 2026-06-22 -> Mon 2026-06-29 = 5 business days
    assert business_days_between(date(2026, 6, 22), date(2026, 6, 29)) == 5


def test_end_before_start_is_zero():
    assert business_days_between(date(2026, 6, 29), date(2026, 6, 22)) == 0


# --------------------------------------------------------------------------- #
# is_due / next_due_date
# --------------------------------------------------------------------------- #


def test_t0_always_due():
    assert is_due(None, date(2026, 6, 22)) is True


def test_due_when_elapsed_at_or_above_cadence():
    last = date(2026, 5, 1)  # well over 21 business days before late June
    assert is_due(last, date(2026, 6, 22), cadence_business_days=21) is True


def test_not_due_when_just_recorded():
    assert is_due(date(2026, 6, 22), date(2026, 6, 22), cadence_business_days=21) is False


def test_next_due_date_flips_is_due():
    last = date(2026, 6, 1)  # a Monday
    due = next_due_date(last, cadence_business_days=21)
    assert business_days_between(last, due) == 21
    assert is_due(last, due, cadence_business_days=21) is True
    # the business day BEFORE due has only 20 elapsed -> not yet due
    prev_bday = due - timedelta(days=1)
    while prev_bday.weekday() >= 5:  # step back over a weekend to a weekday
        prev_bday -= timedelta(days=1)
    assert business_days_between(last, prev_bday) == 20
    assert is_due(last, prev_bday, cadence_business_days=21) is False


def test_cadence_status_fields_consistent():
    st = cadence_status(date(2026, 6, 1), date(2026, 6, 22), cadence_business_days=21)
    assert st.due == (st.business_days_elapsed >= st.cadence_business_days)
    assert st.business_days_until_due == max(0, st.cadence_business_days - st.business_days_elapsed)
    assert st.business_days_until_due >= 0


def test_invalid_cadence_raises():
    with pytest.raises(ValueError):
        is_due(None, date(2026, 6, 22), cadence_business_days=0)
    with pytest.raises(ValueError):
        next_due_date(date(2026, 6, 1), cadence_business_days=0)
    with pytest.raises(ValueError):
        cadence_status(date(2026, 6, 1), date(2026, 6, 22), cadence_business_days=-1)
