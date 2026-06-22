"""Tests for the extracted momentum primitives (engine/momentum_weights.py).

These functions were moved verbatim from scripts/aqr_ideal_walkforward.py (which had NO tests); these
pin the behavior so the re-point + any future change can't silently drift from the validated config.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import pytest

from engine.momentum_weights import build_pricebars, vol_estimate, weights_from_picks


@dataclass(frozen=True)
class _Pick:
    symbol: str


def _frame(symbols: list[str], n: int, start: str = "2024-01-01") -> pd.DataFrame:
    idx = pd.date_range(start, periods=n, freq="D")
    # deterministic, distinct per-symbol drift so vols differ
    data = {s: [100.0 + (i * (j + 1) * 0.1) for i in range(n)] for j, s in enumerate(symbols)}
    return pd.DataFrame(data, index=idx)


# --------------------------------------------------------------------------- #
# weights_from_picks
# --------------------------------------------------------------------------- #


def test_empty_picks_returns_empty():
    assert weights_from_picks([], _frame(["A"], 300), pd.Timestamp("2024-06-01")) == {}


def test_cap_binding_gives_equal_weight():
    # top-5 x 0.20 cap == 1.0 -> equal weight 1/5 each (the validated top-N=1/cap case)
    picks = [_Pick(s) for s in ["A", "B", "C", "D", "E"]]
    w = weights_from_picks(
        picks, _frame(["A", "B", "C", "D", "E"], 300), pd.Timestamp("2024-06-01")
    )
    assert w == pytest.approx({"A": 0.2, "B": 0.2, "C": 0.2, "D": 0.2, "E": 0.2})


def test_infeasible_cap_raises():
    # 3 names x 0.20 = 0.60 < 1.0 -> cannot reach full investment, must raise
    picks = [_Pick(s) for s in ["A", "B", "C"]]
    # 메시지는 종목을 더 늘리도록 안내해야 한다(줄이면 capacity 더 작아짐, Codex P3)
    with pytest.raises(ValueError, match="at least"):
        weights_from_picks(
            picks, _frame(["A", "B", "C"], 300), pd.Timestamp("2024-06-01"), cap=0.20
        )


def test_inverse_vol_path_respects_cap_and_sums_to_one():
    # slack cap (2 names x 0.60 = 1.2 > 1.0) -> inverse-vol weighting, each <= cap, sum ~1.0
    picks = [_Pick("A"), _Pick("B")]
    w = weights_from_picks(picks, _frame(["A", "B"], 300), pd.Timestamp("2024-06-01"), cap=0.60)
    assert sum(w.values()) == pytest.approx(1.0)
    assert all(v <= 0.60 + 1e-9 for v in w.values())


def test_weights_from_picks_matches_deployed_paper_drill():
    """Fidelity: the extracted engine weighting must equal the deployed paper_drill copy bit-for-bit —
    the whole point of extracting (vs reimplementing) is the SAME validated portfolio. Adversarial
    review HIGH: this was the untested core justification. Exercises the inverse-vol path (slack cap),
    where any vol/normalisation divergence would surface."""
    from scripts.paper_drill import weights_from_picks as paper_drill_weights

    picks = [_Pick(s) for s in ["A", "B", "C"]]
    prices = _frame(["A", "B", "C"], 300)
    rebal = pd.Timestamp("2024-09-01")
    cap = 0.50  # 3 x 0.50 = 1.5 > 1.0 -> inverse-vol path
    ours = weights_from_picks(picks, prices, rebal, cap=cap)
    theirs = paper_drill_weights(picks, prices, rebal, cap=cap)
    assert ours.keys() == theirs.keys()
    for s in ours:
        assert ours[s] == pytest.approx(theirs[s], abs=1e-12)


# --------------------------------------------------------------------------- #
# vol_estimate
# --------------------------------------------------------------------------- #


def test_vol_estimate_missing_symbol_fallback():
    assert vol_estimate(_frame(["A"], 300), "ZZZ", pd.Timestamp("2024-06-01")) == pytest.approx(
        0.30
    )


def test_vol_estimate_short_history_fallback():
    # window=63, need >= 31 returns; a 10-bar frame falls back to 0.30
    assert vol_estimate(_frame(["A"], 10), "A", pd.Timestamp("2024-06-01")) == pytest.approx(0.30)


def test_vol_estimate_returns_annualized_positive():
    v = vol_estimate(_frame(["A"], 300), "A", pd.Timestamp("2024-09-01"))
    assert v >= 0.05  # floored


# --------------------------------------------------------------------------- #
# build_pricebars
# --------------------------------------------------------------------------- #


def test_build_pricebars_too_few_bars_returns_empty():
    assert build_pricebars(_frame(["A"], 100), "A", pd.Timestamp("2024-12-01")) == []


def test_build_pricebars_enough_bars_returns_list():
    bars = build_pricebars(_frame(["A"], 300), "A", pd.Timestamp("2024-12-01"))
    assert len(bars) == 260  # tail(lookback_bars)
    assert all(b.symbol == "A" for b in bars)


def test_build_pricebars_missing_symbol_returns_empty():
    assert build_pricebars(_frame(["A"], 300), "ZZZ", pd.Timestamp("2024-12-01")) == []


def test_build_pricebars_pit_excludes_after_end():
    # end mid-series: only bars up to end are eligible; with 300 bars and an early end -> < 260 -> []
    frame = _frame(["A"], 300)
    early_end = frame.index[100]
    assert build_pricebars(frame, "A", early_end) == []  # only 101 bars <= end < 260
