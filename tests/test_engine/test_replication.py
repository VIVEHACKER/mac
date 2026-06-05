from __future__ import annotations

import pandas as pd
import pytest

from engine.replication import ReplicationResult, momentum_replication


def _trending_prices(drifts: dict[str, float], n_days: int = 90) -> pd.DataFrame:
    dates = pd.bdate_range("2015-01-01", periods=n_days)
    data = {sym: [100.0 * (1.0 + g) ** i for i in range(n_days)] for sym, g in drifts.items()}
    return pd.DataFrame(data, index=dates)


def test_momentum_replication_detects_persistent_trend() -> None:
    # Monotone, distinct drifts -> momentum rank == forward-return rank at every
    # rebalance -> rank-IC ~ +1 and the top-N beats the equal-weight benchmark.
    prices = _trending_prices({"A": 0.004, "B": 0.003, "C": 0.002, "D": 0.001}, n_days=90)

    result = momentum_replication(
        prices, region="TEST", top_n=2, lookback=20, skip=2, rebalance_days=5
    )

    assert isinstance(result, ReplicationResult)
    assert result.n_symbols == 4
    assert result.n_rebalances > 0
    assert result.mean_rank_ic > 0.5
    assert result.excess_ann > 0.0
    assert result.monthly_win_rate > 0.5


def test_identical_symbols_have_no_cross_sectional_edge() -> None:
    prices = _trending_prices({"A": 0.002, "B": 0.002, "C": 0.002, "D": 0.002}, n_days=90)

    result = momentum_replication(
        prices, region="FLAT", top_n=2, lookback=20, skip=2, rebalance_days=5
    )

    assert result.excess_ann == pytest.approx(0.0, abs=1e-9)
    assert result.mean_rank_ic == pytest.approx(0.0, abs=1e-9)


def test_requires_at_least_two_symbols() -> None:
    prices = _trending_prices({"A": 0.002}, n_days=90)
    with pytest.raises(ValueError, match="two"):
        momentum_replication(prices, top_n=1, lookback=20, skip=2, rebalance_days=5)


def test_insufficient_history_yields_no_rebalances() -> None:
    prices = _trending_prices({"A": 0.002, "B": 0.001}, n_days=20)
    with pytest.raises(ValueError, match="history"):
        momentum_replication(prices, top_n=1, lookback=20, skip=2, rebalance_days=5)
