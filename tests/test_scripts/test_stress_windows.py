from __future__ import annotations

import pytest

from scripts.stress_windows_validation import (
    GATE_MIN_MEAN_STRESS_EXCESS,
    GATE_MIN_WORST_STRESS_EXCESS,
    _total_return,
)


def test_total_return_compounds_monthly() -> None:
    assert _total_return([]) == 0.0
    assert _total_return([0.1, 0.1]) == pytest.approx(0.21)  # 1.1*1.1 - 1
    assert _total_return([0.5, -0.5]) == pytest.approx(-0.25)  # 1.5*0.5 - 1, path matters
    assert _total_return([-0.2, -0.2, -0.2]) == pytest.approx(0.8**3 - 1)


def test_gate_threshold_matches_promotion_gate() -> None:
    # Guards against drift between this script and the live-readiness gate it reports on.
    from trader.research_registry import LIVE_PROMOTION_GATE

    assert LIVE_PROMOTION_GATE.min_worst_stress_excess == GATE_MIN_WORST_STRESS_EXCESS
    assert LIVE_PROMOTION_GATE.min_mean_stress_excess == GATE_MIN_MEAN_STRESS_EXCESS
