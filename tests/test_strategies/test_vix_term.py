from __future__ import annotations

from datetime import date

import pytest

from signals.vix_term import classify_term_structure, term_ratio, vix_term_signal


def test_term_ratio_basic() -> None:
    assert term_ratio(20.0, 25.0) == pytest.approx(0.8)
    assert term_ratio(30.0, 25.0) == pytest.approx(1.2)


def test_term_ratio_rejects_bad_data() -> None:
    with pytest.raises(ValueError, match="positive"):
        term_ratio(0.0, 25.0)
    with pytest.raises(ValueError, match="positive"):
        term_ratio(20.0, -1.0)


def test_classify_threshold_boundary() -> None:
    assert classify_term_structure(1.001) == "backwardation"
    assert classify_term_structure(1.0) == "contango"  # exactly at threshold = not stress
    assert classify_term_structure(0.85) == "contango"


def test_signal_emitted_only_on_backwardation() -> None:
    quiet = vix_term_signal(date(2026, 6, 11), vix=19.44, vix3m=21.42)
    assert quiet is None  # contango day -> no flag

    stress = vix_term_signal(date(2020, 3, 16), vix=82.69, vix3m=60.0)
    assert stress is not None
    assert stress.symbol == "SPY"
    assert stress.direction == "long"  # literature hypothesis, advisory until gated
    assert stress.score == pytest.approx(82.69 / 60.0 - 1.0)
    assert "backwardation" in stress.reason
