from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from scripts.paper_drill_combined import (
    LOWVOL_TOP_N,
    SLEEVE_IDEAL,
    SLEEVE_LOWVOL,
    STRATEGY_ID,
    blend_paper_weights,
    lowvol_weights,
)


def test_blend_merges_sleeves_at_declared_weights() -> None:
    ideal = {"AAA": 0.5, "BBB": 0.5}
    lowvol = {"BBB": 0.05, "CCC": 0.05}
    blended = blend_paper_weights(ideal, lowvol)
    assert blended["AAA"] == pytest.approx(SLEEVE_IDEAL * 0.5)
    # BBB sits in BOTH sleeves -> contributions sum.
    assert blended["BBB"] == pytest.approx(SLEEVE_IDEAL * 0.5 + SLEEVE_LOWVOL * 0.05)
    assert blended["CCC"] == pytest.approx(SLEEVE_LOWVOL * 0.05)


def test_blend_fails_closed_when_a_sleeve_is_empty() -> None:
    assert blend_paper_weights({}, {"AAA": 0.05}) == {}
    assert blend_paper_weights({"AAA": 0.5}, {}) == {}


def test_blend_of_fully_invested_sleeves_sums_to_one() -> None:
    ideal = {f"I{i}": 1 / 7 for i in range(7)}
    lowvol = {f"L{i}": 1 / 20 for i in range(20)}
    blended = blend_paper_weights(ideal, lowvol)
    assert sum(blended.values()) == pytest.approx(1.0)


def _prices(n_symbols: int, days: int = 200, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2025-06-02", periods=days)
    data = {}
    for i in range(n_symbols):
        # vol increases with index i -> the lowest-vol names are the first ones.
        daily_vol = 0.005 + 0.002 * i
        data[f"S{i:02d}"] = 100.0 * np.cumprod(1 + rng.normal(0, daily_vol, days))
    return pd.DataFrame(data, index=idx)


def test_lowvol_picks_the_calmest_names_equal_weight(monkeypatch: pytest.MonkeyPatch) -> None:
    prices = _prices(30)
    symbols = list(prices.columns)
    monkeypatch.setattr("scripts.paper_drill_combined.MEGACAPS", symbols)
    weights = lowvol_weights(prices, prices.index[-1])
    assert len(weights) == LOWVOL_TOP_N
    assert all(w == pytest.approx(1.0 / LOWVOL_TOP_N) for w in weights.values())
    # The calmest names (lowest synthetic vol = lowest index) dominate the selection.
    picked = sorted(weights)
    assert picked[0] == "S00"
    assert "S29" not in weights  # the wildest name is never in the calm set


def test_lowvol_fails_closed_without_enough_history(monkeypatch: pytest.MonkeyPatch) -> None:
    prices = _prices(25, days=60)  # < MIN_HISTORY for every name
    monkeypatch.setattr("scripts.paper_drill_combined.MEGACAPS", list(prices.columns))
    assert lowvol_weights(prices, prices.index[-1]) == {}


def test_strategy_id_is_isolated_from_ideal_ids() -> None:
    # The combined ledger/state namespace must never collide with the IDEAL line's.
    assert STRATEGY_ID == "combined_ideal80_lowvol20_pit110"
    from scripts.paper_drill import KNOWN_STRATEGY_IDS

    assert STRATEGY_ID not in KNOWN_STRATEGY_IDS.values()
