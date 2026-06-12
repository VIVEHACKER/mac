from __future__ import annotations

from datetime import date

import pytest

from signals.vix_peak import PEAK_WINDOW, peak_decline, vix_peak_signal


def _window(peak: float, current: float, base: float = 15.0) -> list[float]:
    """A PEAK_WINDOW-long VIX path: flat base, a spike to ``peak``, ending at ``current``."""
    window = [base] * PEAK_WINDOW
    window[PEAK_WINDOW // 2] = peak
    window[-1] = current
    return window


def test_peak_decline_measures_peak_and_ratio() -> None:
    measured = peak_decline(_window(peak=40.0, current=28.0))
    assert measured is not None
    peak, ratio = measured
    assert peak == 40.0
    assert ratio == pytest.approx(0.7)


def test_peak_decline_fails_closed_on_short_or_bad_window() -> None:
    assert peak_decline([20.0] * (PEAK_WINDOW - 1)) is None
    bad = _window(40.0, 28.0)
    bad[3] = 0.0
    assert peak_decline(bad) is None


def test_signal_requires_stress_peak_and_material_decline() -> None:
    as_of = date(2020, 4, 15)
    # Peak 40 (>=30), now 28 = 70% of peak (<=80%) -> signal.
    fired = vix_peak_signal(as_of, _window(40.0, 28.0))
    assert fired is not None
    assert fired.direction == "long"
    assert fired.score == pytest.approx(0.3)

    # Peak below the stress level -> calm-market noise, no signal.
    assert vix_peak_signal(as_of, _window(25.0, 18.0)) is None

    # Stress peak but no material retreat (still 95% of peak) -> no signal.
    assert vix_peak_signal(as_of, _window(40.0, 38.0)) is None
