"""Cross-market / cross-period momentum replication.

The strongest evidence that a backtested edge is real (not data-mined) is whether the
SAME rule reproduces on independent samples — other markets, other decades. This module
runs the transferable core of the IDEAL signal, cross-sectional 12-1 momentum, on any
price panel and measures whether momentum rank predicts forward return (rank-IC) and
whether the top-N beats the equal-weight benchmark.

Fundamentals are intentionally NOT required: momentum is the component Asness-Moskowitz-
Pedersen (2013) confirmed across US/UK/Europe/Japan, so a price-only replication is a
clean, independent test of the edge's generality.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from engine.significance import per_period_sharpe


@dataclass(frozen=True)
class ReplicationResult:
    region: str
    n_symbols: int
    n_rebalances: int
    long_sharpe: float
    bench_sharpe: float
    excess_ann: float
    mean_rank_ic: float
    monthly_win_rate: float
    long_monthly_returns: list[float]
    excess_monthly_returns: list[float]


def _rank(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0  # average rank for ties (1-based)
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank
        i = j + 1
    return ranks


def _spearman(x: list[float], y: list[float]) -> float:
    if len(x) < 2:
        return 0.0
    rx, ry = _rank(x), _rank(y)
    n = len(rx)
    mx = sum(rx) / n
    my = sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    vx = sum((a - mx) ** 2 for a in rx)
    vy = sum((b - my) ** 2 for b in ry)
    denom = math.sqrt(vx * vy)
    if denom == 0.0:
        return 0.0
    return cov / denom


def momentum_replication(
    prices: pd.DataFrame,
    *,
    region: str = "",
    top_n: int = 5,
    lookback: int = 252,
    skip: int = 21,
    rebalance_days: int = 21,
) -> ReplicationResult:
    """Run equal-weight top-N cross-sectional 12-1 momentum on a price panel.

    ``prices`` is a date-indexed DataFrame of close prices (columns = symbols). At each
    rebalance the momentum signal is ``close[t-skip]/close[t-skip-lookback] - 1`` (skip
    the most recent month), the top-N by momentum are held equal-weighted for
    ``rebalance_days``, and performance is compared to the equal-weight benchmark of all
    names with data. ``mean_rank_ic`` is the average Spearman correlation between
    momentum and the subsequent forward return — the direct measure of whether the edge
    exists in this sample.
    """

    symbols = list(prices.columns)
    if len(symbols) < 2:
        raise ValueError("need at least two symbols to rank cross-sectionally")

    n_rows = len(prices)
    first = lookback + skip
    if n_rows <= first + rebalance_days:
        raise ValueError("not enough price history for the requested lookback")

    values = {sym: prices[sym].to_numpy() for sym in symbols}
    periods_per_year = 252.0 / rebalance_days

    long_returns: list[float] = []
    excess_returns: list[float] = []
    ics: list[float] = []

    for idx in range(first, n_rows - rebalance_days, rebalance_days):
        moms: list[float] = []
        fwds: list[float] = []
        valid: list[tuple[float, float]] = []
        for sym in symbols:
            series = values[sym]
            past = series[idx - skip - lookback]
            recent = series[idx - skip]
            cur = series[idx]
            fwd = series[idx + rebalance_days]
            if any(p != p or p <= 0 for p in (past, recent, cur, fwd)):  # NaN or non-positive
                continue
            momentum = recent / past - 1.0
            forward = fwd / cur - 1.0
            moms.append(momentum)
            fwds.append(forward)
            valid.append((momentum, forward))
        if len(valid) < top_n + 1:
            continue

        ranked = sorted(valid, key=lambda mf: mf[0], reverse=True)
        top = ranked[:top_n]
        long_ret = sum(forward for _, forward in top) / len(top)
        bench_ret = sum(forward for _, forward in valid) / len(valid)
        long_returns.append(long_ret)
        excess_returns.append(long_ret - bench_ret)
        ics.append(_spearman(moms, fwds))

    n_rebalances = len(long_returns)
    bench_series = [lr - er for lr, er in zip(long_returns, excess_returns, strict=True)]
    long_sharpe = (
        per_period_sharpe(long_returns) * math.sqrt(periods_per_year) if n_rebalances else 0.0
    )
    bench_sharpe = (
        per_period_sharpe(bench_series) * math.sqrt(periods_per_year) if n_rebalances else 0.0
    )
    excess_ann = (sum(excess_returns) / n_rebalances) * periods_per_year if n_rebalances else 0.0
    mean_rank_ic = sum(ics) / len(ics) if ics else 0.0
    win_rate = sum(1 for e in excess_returns if e > 0) / n_rebalances if n_rebalances else 0.0

    return ReplicationResult(
        region=region,
        n_symbols=len(symbols),
        n_rebalances=n_rebalances,
        long_sharpe=long_sharpe,
        bench_sharpe=bench_sharpe,
        excess_ann=excess_ann,
        mean_rank_ic=mean_rank_ic,
        monthly_win_rate=win_rate,
        long_monthly_returns=long_returns,
        excess_monthly_returns=excess_returns,
    )
