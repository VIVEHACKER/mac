"""Forward out-of-sample (paper) ledger for chart-reading SIGNALS.

The backtest (docs/CHART_VALIDATION.md) measured the chart signal's forward-return edge on
*past* data — the same data the mean-reversion gate was tuned on. The only way to know if
that edge is real and not in-sample overfitting is a forward record: log each live signal,
pre-registered with the entry price and the bar timestamp, then score it against prices that
arrive *later*. A live ACT−AVOID spread far below the backtested figure is overfitting
revealing itself.

This module is the ledger I/O + scorer. It is price-source agnostic (realised forward
returns are passed in) so it stays pure and testable, mirroring engine/paper_oos.py.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path

_ACT = {"ENTER_NOW", "SCALE_IN"}
_BUCKETS = ("ENTER_NOW", "SCALE_IN", "WAIT_FOR_PULLBACK", "AVOID")


@dataclass(frozen=True)
class ChartSignalEntry:
    logged_ts: str  # ISO datetime of the decision bar (read.asof) — the moment of the call
    symbol: str
    market: str
    timeframe: str
    direction: str  # 'long' | 'short'
    decision: str  # ENTER_NOW / SCALE_IN / WAIT_FOR_PULLBACK / AVOID
    confluence: float
    range_pos: float
    mean_reversion: bool
    entry_price: float


@dataclass(frozen=True)
class ChartOOSBucket:
    decision: str
    n: int
    mean_fwd: float
    hit_rate: float


@dataclass(frozen=True)
class ChartOOSTrackRecord:
    horizon: int
    n_matured: int
    act_n: int
    act_mean_fwd: float
    act_hit_rate: float
    avoid_mean_fwd: float
    act_minus_avoid: float
    buckets: list[ChartOOSBucket]
    vs_backtest: float | None  # act_minus_avoid / backtest figure — the overfit-in-the-wild ratio


def entry_key(entry: ChartSignalEntry) -> str:
    """Stable identity for one pre-registered signal (one decision bar, one direction)."""
    return f"{entry.symbol}|{entry.timeframe}|{entry.logged_ts}|{entry.direction}"


def load_chart_ledger(path: Path) -> list[ChartSignalEntry]:
    if not Path(path).exists():
        return []
    entries: list[ChartSignalEntry] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            entries.append(ChartSignalEntry(**json.loads(line)))
    return entries


def append_signal(path: Path, entry: ChartSignalEntry) -> None:
    """Append a pre-registered signal. Refuses to rewrite an existing identity.

    The refusal is the point: a forward OOS record is only credible if a signal cannot be
    re-logged (and silently re-decided) after the fact.
    """
    path = Path(path)
    key = entry_key(entry)
    for existing in load_chart_ledger(path):
        if entry_key(existing) == key:
            raise ValueError(f"signal {key} already recorded — the chart OOS ledger is append-only")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(entry)) + "\n")


def score_chart_ledger(
    entries: list[ChartSignalEntry],
    realized: dict[str, float],
    *,
    horizon: int,
    backtest_act_avoid: float | None = None,
) -> ChartOOSTrackRecord:
    """Score the matured entries (those with a realised ``horizon``-bar forward return).

    ``realized`` maps ``entry_key(entry) -> forward return`` for entries whose horizon has
    elapsed; immature entries are simply absent and skipped. Returns per-bucket stats plus
    the ACT(ENTER+SCALE)−AVOID spread and, if given the backtested spread, the live/backtest
    ratio.
    """
    matured = [(e, realized[entry_key(e)]) for e in entries if entry_key(e) in realized]

    def _bucket(rets: list[float]) -> tuple[float, float]:
        if not rets:
            return 0.0, 0.0
        return statistics.mean(rets), sum(1 for r in rets if r > 0) / len(rets)

    buckets: list[ChartOOSBucket] = []
    for decision in _BUCKETS:
        rets = [r for e, r in matured if e.decision == decision]
        mean_fwd, hit = _bucket(rets)
        buckets.append(
            ChartOOSBucket(decision=decision, n=len(rets), mean_fwd=mean_fwd, hit_rate=hit)
        )

    act_rets = [r for e, r in matured if e.decision in _ACT]
    avoid_rets = [r for e, r in matured if e.decision == "AVOID"]
    act_mean, act_hit = _bucket(act_rets)
    avoid_mean, _ = _bucket(avoid_rets)
    spread = act_mean - avoid_mean
    vs_backtest = (
        spread / backtest_act_avoid
        if backtest_act_avoid is not None and backtest_act_avoid != 0.0
        else None
    )

    return ChartOOSTrackRecord(
        horizon=horizon,
        n_matured=len(matured),
        act_n=len(act_rets),
        act_mean_fwd=act_mean,
        act_hit_rate=act_hit,
        avoid_mean_fwd=avoid_mean,
        act_minus_avoid=spread,
        buckets=buckets,
        vs_backtest=vs_backtest,
    )
