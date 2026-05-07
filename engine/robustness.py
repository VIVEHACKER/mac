from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from statistics import median

from data.models import DelistingReturn, PriceBar, UniverseMember
from engine.portfolio import PortfolioBacktestResult, run_momentum_rotation_backtest


@dataclass(frozen=True)
class RobustnessRow:
    lookback: int
    top_n: int
    rebalance_days: int
    train: PortfolioBacktestResult
    test: PortfolioBacktestResult

    @property
    def test_beats_benchmark(self) -> bool:
        return self.test.annualized_excess_return > 0


@dataclass(frozen=True)
class RobustnessReport:
    symbols: tuple[str, ...]
    benchmark_symbol: str
    benchmark_market: str
    split_date: date
    rows: list[RobustnessRow]

    @property
    def test_positive_rate(self) -> float:
        if not self.rows:
            return 0.0
        return sum(row.test_beats_benchmark for row in self.rows) / len(self.rows)

    @property
    def median_test_annualized_excess(self) -> float:
        if not self.rows:
            return 0.0
        return median(row.test.annualized_excess_return for row in self.rows)

    @property
    def best_train_row(self) -> RobustnessRow | None:
        if not self.rows:
            return None
        return max(self.rows, key=lambda row: row.train.annualized_excess_return)

    @property
    def best_test_row(self) -> RobustnessRow | None:
        if not self.rows:
            return None
        return max(self.rows, key=lambda row: row.test.annualized_excess_return)


def run_momentum_robustness_grid(
    bars_by_symbol: dict[str, list[PriceBar]],
    *,
    benchmark_bars: list[PriceBar],
    split_date: date,
    lookbacks: tuple[int, ...] = (63, 126, 252),
    top_ns: tuple[int, ...] = (1, 2, 3),
    rebalance_days_values: tuple[int, ...] = (21, 63),
    initial_cash: float = 10_000.0,
    fee_bps: float = 2.0,
    universe_members: list[UniverseMember] | None = None,
    delisting_returns: list[DelistingReturn] | None = None,
) -> RobustnessReport:
    if not benchmark_bars:
        raise ValueError("benchmark_bars is required for robustness checks")
    benchmark = sorted(benchmark_bars, key=lambda bar: bar.ts)
    rows: list[RobustnessRow] = []

    train_bars = {
        symbol: _slice_bars(bars, end=split_date) for symbol, bars in bars_by_symbol.items()
    }
    test_bars = {
        symbol: _slice_bars(bars, start=split_date) for symbol, bars in bars_by_symbol.items()
    }
    train_benchmark = _slice_bars(benchmark, end=split_date)
    test_benchmark = _slice_bars(benchmark, start=split_date)

    for lookback in lookbacks:
        for top_n in top_ns:
            for rebalance_days in rebalance_days_values:
                try:
                    train = run_momentum_rotation_backtest(
                        train_bars,
                        lookback=lookback,
                        top_n=top_n,
                        initial_cash=initial_cash,
                        rebalance_days=rebalance_days,
                        fee_bps=fee_bps,
                        benchmark_bars=train_benchmark,
                        universe_members=universe_members,
                        delisting_returns=delisting_returns,
                    )
                    test = run_momentum_rotation_backtest(
                        test_bars,
                        lookback=lookback,
                        top_n=top_n,
                        initial_cash=initial_cash,
                        rebalance_days=rebalance_days,
                        fee_bps=fee_bps,
                        benchmark_bars=test_benchmark,
                        universe_members=universe_members,
                        delisting_returns=delisting_returns,
                    )
                except ValueError:
                    continue
                rows.append(
                    RobustnessRow(
                        lookback=lookback,
                        top_n=top_n,
                        rebalance_days=rebalance_days,
                        train=train,
                        test=test,
                    )
                )

    first_benchmark = benchmark[0]
    return RobustnessReport(
        symbols=tuple(sorted(bars_by_symbol)),
        benchmark_symbol=first_benchmark.symbol,
        benchmark_market=first_benchmark.market,
        split_date=split_date,
        rows=sorted(rows, key=lambda row: row.train.annualized_excess_return, reverse=True),
    )


def format_robustness_report(report: RobustnessReport) -> str:
    benchmark_label = f"{report.benchmark_symbol} ({report.benchmark_market})"
    lines = [
        "# Momentum Robustness Report",
        "",
        "Research-only output. This checks parameter sensitivity and split-sample behavior.",
        "",
        f"Universe: {', '.join(report.symbols)}",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Benchmark | {benchmark_label} |",
        f"| Split Date | {report.split_date} |",
        f"| Parameter Sets Tested | {len(report.rows)} |",
        f"| Test Beat Rate | {report.test_positive_rate * 100:.1f}% |",
        f"| Median Test Annualized Excess | {report.median_test_annualized_excess * 100:+.2f}% |",
    ]
    if report.best_train_row:
        row = report.best_train_row
        lines.extend(
            [
                f"| Best Train Params | L{row.lookback}/Top{row.top_n}/R{row.rebalance_days} |",
                f"| Best Train Annualized Excess | {row.train.annualized_excess_return * 100:+.2f}% |",
                f"| Its Test Annualized Excess | {row.test.annualized_excess_return * 100:+.2f}% |",
            ]
        )
    if report.best_test_row:
        row = report.best_test_row
        lines.extend(
            [
                f"| Best Test Params | L{row.lookback}/Top{row.top_n}/R{row.rebalance_days} |",
                f"| Best Test Annualized Excess | {row.test.annualized_excess_return * 100:+.2f}% |",
            ]
        )
    lines.extend(
        [
            "",
            "## Parameter Grid",
            "",
            "| Lookback | Top N | Rebalance | Train Ann. Excess | Test Ann. Excess | Test Sharpe | Test MDD | Test Beats |",
            "|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for row in report.rows:
        lines.append(
            f"| {row.lookback} | {row.top_n} | {row.rebalance_days} | "
            f"{row.train.annualized_excess_return * 100:+.2f}% | "
            f"{row.test.annualized_excess_return * 100:+.2f}% | "
            f"{row.test.sharpe:.2f} | {row.test.max_drawdown * 100:.2f}% | "
            f"{'yes' if row.test_beats_benchmark else 'no'} |"
        )
    lines.extend(
        [
            "",
            "## Bias Notes",
            "",
            _bias_note(report),
            "- A strategy should not be promoted to live sizing unless most nearby parameter sets beat the benchmark out of sample.",
        ]
    )
    return "\n".join(lines)


def _bias_note(report: RobustnessReport) -> str:
    if report.rows and report.rows[0].train.universe_mode == "point-in-time":
        return (
            "- Point-in-time universe membership was enforced. Explicit delisting returns are applied "
            "when supplied; ended members without them are reported in the underlying portfolio rows."
        )
    return (
        "- Static supplied symbols were used. This is not survivorship-free index constituent history."
    )


def _slice_bars(
    bars: list[PriceBar],
    *,
    start: date | None = None,
    end: date | None = None,
) -> list[PriceBar]:
    return [
        bar
        for bar in sorted(bars, key=lambda item: item.ts)
        if (start is None or bar.ts >= start) and (end is None or bar.ts <= end)
    ]
