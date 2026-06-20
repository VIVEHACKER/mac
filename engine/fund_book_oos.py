"""Fund-book forward out-of-sample (paper) track-record ledger.

The assembled barbell fund book (core + hunt + momentum + bridge) only earns genuinely NEW evidence
about whether it survives out of sample by being recorded FORWARD and scored on realised prices. To be
evidence (not theatre) each book snapshot is (1) PRE-REGISTERED immutably with a timestamp and the entry
prices at that moment, never rewritten; (2) SCORED on realised prices later, accumulating live excess vs
a benchmark.

HONEST FRAMING: unlike the standalone IDEAL momentum line (which has a backtested +8.15%/yr to
overfit-check against), the assembled barbell has NO single backtested edge — core + hunt make no alpha
claim; only the momentum sleeve does. So this ledger is PURE FORWARD OBSERVATION (realised fund return
vs benchmark); `vs_backtest` defaults to None for the composite (a single barbell expectation would be a
fabricated number). Mirrors trader/engine/paper_oos.py so the two ledgers read alike; kept separate (no
cross-worktree import). Price-source agnostic — marks are passed in, so it stays pure and testable.
"""

from __future__ import annotations

import csv
import json
import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

from engine.significance import per_period_sharpe


@dataclass(frozen=True)
class FundBookOOSEntry:
    rebal_date: str  # ISO date, e.g. "2026-06-20"
    weights: dict[str, float]  # symbol -> fund-level invested weight (sums to `invested` <= 1.0)
    entry_prices: dict[str, float]  # symbol -> price at rebal_date
    benchmark_symbol: str
    benchmark_price: float  # benchmark price at rebal_date
    sleeve_fractions: dict[str, float]  # provenance: which barbell policy produced this book
    reserve_cash: float
    invested: float


@dataclass(frozen=True)
class FundBookOOSRecord:
    n_periods: int
    cumulative_return: float
    cumulative_benchmark: float
    cumulative_excess: float
    annualized_excess: float
    hit_rate: float
    excess_sharpe: float
    periods_per_year: float
    vs_backtest: (
        float | None
    )  # annualized_excess / backtest_excess_ann, only if explicitly provided


def load_ledger(path: Path) -> list[FundBookOOSEntry]:
    if not Path(path).exists():
        return []
    entries: list[FundBookOOSEntry] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            entries.append(FundBookOOSEntry(**json.loads(line)))
    return entries


def append_entry(path: Path, entry: FundBookOOSEntry) -> None:
    """Append a pre-registered fund-book snapshot. Refuses to overwrite an existing `rebal_date`.

    The refusal is the point: a forward OOS record is only credible if history cannot be rewritten
    after the fact. There is one assembled fund book per date, so the date alone is the key."""
    path = Path(path)
    for existing in load_ledger(path):
        if existing.rebal_date == entry.rebal_date:
            raise ValueError(
                f"fund book for {entry.rebal_date} already recorded — the forward OOS ledger is "
                "append-only and cannot be rewritten"
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(entry), default=str) + "\n")


def fund_book_to_entry(
    book: object,
    *,
    rebal_date: str,
    entry_prices: dict[str, float],
    benchmark_symbol: str,
    benchmark_price: float,
) -> FundBookOOSEntry:
    """Convert an assembled engine.fund_book.FundBook to a pre-registered entry. Fail-closed: a held
    symbol with no (positive) entry price, or a non-positive benchmark price, raises — you cannot
    pre-register a buy with no price."""
    if benchmark_price <= 0.0:
        raise ValueError(f"benchmark_price must be positive (got {benchmark_price})")
    weights = {p.symbol: p.fund_weight for p in book.positions}  # type: ignore[attr-defined]
    missing = [s for s in weights if s not in entry_prices or entry_prices[s] <= 0.0]
    if missing:
        raise ValueError(f"no positive entry price for held symbols: {sorted(missing)}")
    return FundBookOOSEntry(
        rebal_date=rebal_date,
        weights=weights,
        entry_prices={s: float(entry_prices[s]) for s in weights},
        benchmark_symbol=benchmark_symbol,
        benchmark_price=float(benchmark_price),
        sleeve_fractions=dict(book.sleeve_fractions),  # type: ignore[attr-defined]
        reserve_cash=book.reserve_cash,  # type: ignore[attr-defined]
        invested=book.invested,  # type: ignore[attr-defined]
    )


def load_mark_price_history_csv(path: Path) -> dict[str, dict[str, float]]:
    """Load a close-price CSV as ``date -> symbol -> close`` marks. First column = date; every other
    numeric column = a symbol. Empty/non-numeric cells are skipped (sparse files still score)."""
    history: dict[str, dict[str, float]] = {}
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            return history
        date_column = _detect_date_column(reader.fieldnames)
        for row in reader:
            raw_date = (row.get(date_column) or "").strip()
            if not raw_date:
                continue
            mark_date = date.fromisoformat(raw_date[:10]).isoformat()
            marks: dict[str, float] = {}
            for column, raw_value in row.items():
                if column == date_column or raw_value is None:
                    continue
                value = raw_value.strip()
                if not value:
                    continue
                try:
                    marks[column.strip().upper()] = float(value)
                except ValueError:
                    continue
            if marks:
                history[mark_date] = marks
    return history


def mark_prices_at_dates(
    price_history: dict[str, dict[str, float]],
    dates: list[str],
    *,
    max_staleness_days: int | None = None,
) -> dict[str, dict[str, float]]:
    """Last available marks at or before each requested ISO date. Freshness is tracked PER SYMBOL (a
    sparse later row must not erase a still-fresh earlier close). ``max_staleness_days`` bounds the
    carry-forward per symbol so a stale file can't silently score closed periods with frozen prices."""
    available_dates = sorted(date.fromisoformat(mark_date) for mark_date in price_history)
    marks: dict[str, dict[str, float]] = {}
    latest_by_symbol: dict[str, tuple[date, float]] = {}
    cursor = 0
    for requested in sorted({date.fromisoformat(item[:10]) for item in dates}):
        while cursor < len(available_dates) and available_dates[cursor] <= requested:
            observed = available_dates[cursor]
            for symbol, value in price_history[observed.isoformat()].items():
                latest_by_symbol[symbol] = (observed, value)
            cursor += 1
        row = {
            symbol: value
            for symbol, (observed, value) in latest_by_symbol.items()
            if max_staleness_days is None or (requested - observed).days <= max_staleness_days
        }
        if row:
            marks[requested.isoformat()] = row
    return marks


def _detect_date_column(fieldnames: Sequence[str]) -> str:
    for candidate in fieldnames:
        if candidate.strip().lower() in {"date", "ts", "timestamp", "datetime"}:
            return candidate
    return fieldnames[0]


def _period_return(entry: FundBookOOSEntry, marks: dict[str, float]) -> float | None:
    """Weighted realised return of one entry's invested book, renormalised over symbols with marks
    (idle reserve cash is implicitly flat — it neither helps nor hurts the invested book's excess)."""
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
    return weighted / total_weight


def score_ledger(
    entries: list[FundBookOOSEntry],
    mark_prices: dict[str, dict[str, float]],
    *,
    periods_per_year: float = 12.0,
    backtest_excess_ann: float | None = None,
) -> FundBookOOSRecord:
    """Score realised forward excess over the CLOSED periods of the ledger. Each consecutive pair
    (entry_i, entry_{i+1}) is one realised holding period: entry_i's book is marked at entry_{i+1}'s
    date and compared to the benchmark over the same window. The still-open final entry is not scored.
    ``vs_backtest`` is computed only when ``backtest_excess_ann`` is explicitly given (None for the
    composite barbell, which has no single backtested edge)."""
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
        return FundBookOOSRecord(
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
    vs_backtest = (
        annualized_excess / backtest_excess_ann
        if backtest_excess_ann is not None and backtest_excess_ann != 0.0
        else None
    )

    return FundBookOOSRecord(
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
