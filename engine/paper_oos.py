"""Forward out-of-sample (paper) track-record ledger.

Paper trading is the only source of genuinely NEW forward evidence about whether the
backtested edge survives. To be evidence (not theatre) it must be: (1) PRE-REGISTERED —
each rebalance's picks are appended immutably with a timestamp and the entry prices at
that moment, never rewritten; (2) SCORED on realised prices later, accumulating the live
excess vs the benchmark; (3) COMPARED to the backtest expectation — a live excess far
below the backtested figure is overfitting revealing itself in the wild.

This module is the ledger I/O + scorer. It is deliberately price-source agnostic (marks
are passed in) so it stays pure and testable.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

from engine.significance import per_period_sharpe


@dataclass(frozen=True)
class PaperOOSEntry:
    rebal_date: str  # ISO date, e.g. "2026-01-15"
    strategy_id: str
    weights: dict[str, float]  # symbol -> portfolio weight (sums ~1)
    entry_prices: dict[str, float]  # symbol -> price at rebal_date
    benchmark_symbol: str
    benchmark_price: float  # benchmark price at rebal_date


@dataclass(frozen=True)
class OOSTrackRecord:
    n_periods: int
    cumulative_return: float
    cumulative_benchmark: float
    cumulative_excess: float
    annualized_excess: float
    hit_rate: float
    excess_sharpe: float
    periods_per_year: float
    vs_backtest: float | None  # annualized_excess / backtest_excess_ann, if provided


def load_ledger(path: Path) -> list[PaperOOSEntry]:
    if not Path(path).exists():
        return []
    entries: list[PaperOOSEntry] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            entries.append(PaperOOSEntry(**json.loads(line)))
    return entries


def append_entry(path: Path, entry: PaperOOSEntry) -> None:
    """Append a pre-registered rebalance. Refuses to overwrite an existing (date, strategy).

    The refusal is the point: a forward OOS record is only credible if history cannot be
    rewritten after the fact. Re-recording the same rebalance is blocked.
    """

    path = Path(path)
    for existing in load_ledger(path):
        if existing.rebal_date == entry.rebal_date and existing.strategy_id == entry.strategy_id:
            raise ValueError(
                f"rebalance {entry.rebal_date} for {entry.strategy_id} already recorded — "
                "the forward OOS ledger is append-only and cannot be rewritten"
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(entry), default=str) + "\n")


def _period_return(entry: PaperOOSEntry, marks: dict[str, float]) -> float | None:
    """Weighted realised return of one entry's picks, renormalised over symbols with marks."""

    total_weight = 0.0
    weighted = 0.0
    for symbol, weight in entry.weights.items():
        mark = marks.get(symbol)
        buy = entry.entry_prices.get(symbol)
        if mark is None or buy is None or buy <= 0:
            continue
        weighted += weight * (mark / buy - 1.0)
        total_weight += weight
    if total_weight <= 0.0:
        return None
    return weighted / total_weight  # renormalise to the symbols we could mark


def score_ledger(
    entries: list[PaperOOSEntry],
    mark_prices: dict[str, dict[str, float]],
    *,
    periods_per_year: float = 12.0,
    backtest_excess_ann: float | None = None,
) -> OOSTrackRecord:
    """Score realised forward excess over the CLOSED periods of the ledger.

    Each consecutive pair (entry_i, entry_{i+1}) is one realised holding period: entry_i's
    picks are marked at entry_{i+1}'s date (``mark_prices[next_date]``), compared to the
    benchmark over the same window. The still-open final entry is not scored (no realised
    outcome yet). ``vs_backtest`` is the realised annualised excess divided by the
    backtested expectation — the overfit-in-the-wild ratio.
    """

    excesses: list[float] = []
    port_factors: list[float] = []
    bench_factors: list[float] = []

    for i in range(len(entries) - 1):
        cur = entries[i]
        marks = mark_prices.get(entries[i + 1].rebal_date)
        if not marks:
            continue
        port_ret = _period_return(cur, marks)
        bench_mark = marks.get(cur.benchmark_symbol)
        if port_ret is None or bench_mark is None or cur.benchmark_price <= 0:
            continue
        bench_ret = bench_mark / cur.benchmark_price - 1.0
        excesses.append(port_ret - bench_ret)
        port_factors.append(1.0 + port_ret)
        bench_factors.append(1.0 + bench_ret)

    n = len(excesses)
    if n == 0:
        return OOSTrackRecord(
            n_periods=0,
            cumulative_return=0.0,
            cumulative_benchmark=0.0,
            cumulative_excess=0.0,
            annualized_excess=0.0,
            hit_rate=0.0,
            excess_sharpe=0.0,
            periods_per_year=periods_per_year,
            vs_backtest=None,
        )

    cumulative_return = math.prod(port_factors) - 1.0
    cumulative_benchmark = math.prod(bench_factors) - 1.0
    annualized_excess = (sum(excesses) / n) * periods_per_year
    hit_rate = sum(1 for e in excesses if e > 0) / n
    excess_sharpe = per_period_sharpe(excesses) * math.sqrt(periods_per_year)
    if backtest_excess_ann is not None and backtest_excess_ann != 0.0:
        vs_backtest: float | None = annualized_excess / backtest_excess_ann
    else:
        vs_backtest = None

    return OOSTrackRecord(
        n_periods=n,
        cumulative_return=cumulative_return,
        cumulative_benchmark=cumulative_benchmark,
        cumulative_excess=cumulative_return - cumulative_benchmark,
        annualized_excess=annualized_excess,
        hit_rate=hit_rate,
        excess_sharpe=excess_sharpe,
        periods_per_year=periods_per_year,
        vs_backtest=vs_backtest,
    )
