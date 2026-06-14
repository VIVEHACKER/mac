from __future__ import annotations

import pandas as pd
import pytest

from scripts.aqr_ideal_walkforward import BENCHMARK
from scripts.ideal_defensive_walkforward import MA_WINDOW, _ma_regime, _metrics


def test_metrics_excess_is_strategy_minus_spy() -> None:
    m = _metrics([0.02] * 12, [0.01] * 12, "2020-01-01", "2020-12-31")
    assert m["total_return"] == pytest.approx(1.02**12 - 1)
    assert m["excess"] == pytest.approx(m["ann"] - m["spy_ann"])
    assert m["excess"] > 0  # 2%/mo beats 1%/mo


def test_ma_regime_risk_on_when_above_ma_and_safe_when_short_history() -> None:
    idx = pd.bdate_range("2019-01-01", periods=MA_WINDOW + 5)
    # Rising series: last point is above its trailing MA -> risk_on True.
    rising = pd.DataFrame({BENCHMARK: [100 + i for i in range(len(idx))]}, index=idx)
    on = _ma_regime(rising, [idx[-1].strftime("%Y-%m-%d")])
    assert on == [True]

    # A date before MA_WINDOW of history exists -> default risk_on True (no spurious hedge).
    early = _ma_regime(rising, [idx[10].strftime("%Y-%m-%d")])
    assert early == [True]


def test_ma_regime_risk_off_below_ma() -> None:
    idx = pd.bdate_range("2019-01-01", periods=MA_WINDOW + 5)
    # Rise then sharp drop so the last close sits well below the trailing MA.
    vals = [100 + i for i in range(MA_WINDOW)] + [50, 40, 30, 20, 10]
    falling = pd.DataFrame({BENCHMARK: vals}, index=idx)
    assert _ma_regime(falling, [idx[-1].strftime("%Y-%m-%d")]) == [False]
