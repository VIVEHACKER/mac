from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from statistics import mean, pstdev

from data.models import PriceBar
from strategies.momentum import build_time_series_momentum_signals

TRADING_DAYS = 252


@dataclass(frozen=True)
class EquityPoint:
    ts: object
    equity: float
    benchmark_equity: float
    position: float
    asset_return: float
    strategy_return: float
    cost: float


@dataclass(frozen=True)
class BacktestResult:
    symbol: str
    market: str
    start: object
    end: object
    rows: int
    lookback: int
    initial_cash: float
    final_equity: float
    benchmark_final_equity: float
    benchmark_symbol: str
    benchmark_market: str
    total_return: float
    benchmark_return: float
    annualized_return: float
    benchmark_annualized_return: float
    excess_return: float
    annualized_excess_return: float
    sharpe: float
    max_drawdown: float
    trades: int
    exposure: float
    fee_bps: float
    total_cost: float
    equity_curve: list[EquityPoint]


def run_momentum_backtest(
    bars: list[PriceBar],
    lookback: int = 126,
    initial_cash: float = 10_000.0,
    fee_bps: float = 0.0,
    max_position: float = 1.0,
    benchmark_bars: list[PriceBar] | None = None,
) -> BacktestResult:
    if max_position < 0 or max_position > 1:
        raise ValueError("max_position must be between 0 and 1")
    ordered = sorted(bars, key=lambda bar: bar.ts)
    if len(ordered) <= lookback + 1:
        raise ValueError(f"not enough bars for lookback={lookback}; got {len(ordered)}")
    benchmark_closes = _benchmark_closes_by_date(benchmark_bars)
    benchmark_symbol = ordered[0].symbol
    benchmark_market = ordered[0].market
    if benchmark_bars:
        first_benchmark = sorted(benchmark_bars, key=lambda bar: bar.ts)[0]
        benchmark_symbol = first_benchmark.symbol
        benchmark_market = first_benchmark.market

    signals = build_time_series_momentum_signals(ordered, lookback=lookback)
    equity = initial_cash
    benchmark_equity = initial_cash
    curve: list[EquityPoint] = [
        EquityPoint(ordered[0].ts, equity, benchmark_equity, 0.0, 0.0, 0.0, 0.0)
    ]
    strategy_returns: list[float] = []
    positions: list[float] = []
    total_cost = 0.0

    for index in range(1, len(ordered)):
        previous_close = ordered[index - 1].close
        current_close = ordered[index].close
        asset_return = (current_close / previous_close) - 1.0
        previous_position = signals[index - 1].position * max_position
        prior_position = positions[-1] if positions else 0.0
        cost = abs(previous_position - prior_position) * fee_bps / 10_000
        strategy_return = previous_position * asset_return - cost
        equity *= 1.0 + strategy_return
        benchmark_return = (
            _benchmark_return(benchmark_closes, ordered[index - 1].ts, ordered[index].ts)
            if benchmark_closes
            else asset_return
        )
        benchmark_equity *= 1.0 + benchmark_return
        total_cost += cost
        strategy_returns.append(strategy_return)
        positions.append(previous_position)
        curve.append(
            EquityPoint(
                ordered[index].ts,
                equity,
                benchmark_equity,
                previous_position,
                asset_return,
                strategy_return,
                cost,
            )
        )

    total_return = (equity / initial_cash) - 1.0
    benchmark_return = (benchmark_equity / initial_cash) - 1.0
    years = max((ordered[-1].ts - ordered[0].ts).days / 365.25, 1 / TRADING_DAYS)
    annualized_return = (equity / initial_cash) ** (1 / years) - 1.0
    benchmark_annualized_return = (benchmark_equity / initial_cash) ** (1 / years) - 1.0
    sharpe = _sharpe(strategy_returns)
    max_drawdown = _max_drawdown([point.equity for point in curve])
    trades = sum(1 for before, after in zip(positions, positions[1:], strict=False) if before != after)
    exposure = mean(positions) if positions else 0.0

    first = ordered[0]
    return BacktestResult(
        symbol=first.symbol,
        market=first.market,
        start=ordered[0].ts,
        end=ordered[-1].ts,
        rows=len(ordered),
        lookback=lookback,
        initial_cash=initial_cash,
        final_equity=equity,
        benchmark_final_equity=benchmark_equity,
        benchmark_symbol=benchmark_symbol,
        benchmark_market=benchmark_market,
        total_return=total_return,
        benchmark_return=benchmark_return,
        annualized_return=annualized_return,
        benchmark_annualized_return=benchmark_annualized_return,
        excess_return=total_return - benchmark_return,
        annualized_excess_return=annualized_return - benchmark_annualized_return,
        sharpe=sharpe,
        max_drawdown=max_drawdown,
        trades=trades,
        exposure=exposure,
        fee_bps=fee_bps,
        total_cost=total_cost,
        equity_curve=curve,
    )


def format_backtest_report(result: BacktestResult) -> str:
    benchmark_label = (
        f"{result.benchmark_symbol} buy/hold"
        if result.benchmark_symbol == result.symbol and result.benchmark_market == result.market
        else f"{result.benchmark_symbol} ({result.benchmark_market})"
    )
    lines = [
        f"# Momentum Backtest - {result.symbol}",
        "",
        "Research-only output. This does not place orders or provide investment advice.",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Market | {result.market} |",
        f"| Window | {result.start} to {result.end} |",
        f"| Bars | {result.rows} |",
        f"| Lookback | {result.lookback} bars |",
        f"| Benchmark | {benchmark_label} |",
        f"| Initial Cash | {result.initial_cash:,.2f} |",
        f"| Final Equity | {result.final_equity:,.2f} |",
        f"| Strategy Return | {result.total_return * 100:+.2f}% |",
        f"| Benchmark Return | {result.benchmark_return * 100:+.2f}% |",
        f"| Excess Return vs Benchmark | {result.excess_return * 100:+.2f}% |",
        f"| Annualized Return | {result.annualized_return * 100:+.2f}% |",
        f"| Benchmark Annualized Return | {result.benchmark_annualized_return * 100:+.2f}% |",
        f"| Annualized Excess Return | {result.annualized_excess_return * 100:+.2f}% |",
        f"| Sharpe | {result.sharpe:.2f} |",
        f"| Max Drawdown | {result.max_drawdown * 100:.2f}% |",
        f"| Trades | {result.trades} |",
        f"| Average Exposure | {result.exposure * 100:.1f}% |",
        f"| Fee | {result.fee_bps:.2f} bps per turnover |",
        f"| Total Cost Drag | {result.total_cost * 100:.2f}% |",
        "",
        "## Last 5 Equity Points",
        "",
        "| Date | Equity | Benchmark | Position | Cost | Strategy Day |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for point in result.equity_curve[-5:]:
        lines.append(
            f"| {point.ts} | {point.equity:,.2f} | {point.benchmark_equity:,.2f} | "
            f"{point.position:.2f} | {point.cost * 100:.3f}% | "
            f"{point.strategy_return * 100:+.2f}% |"
        )
    return "\n".join(lines)


def _sharpe(returns: list[float]) -> float:
    if len(returns) < 2:
        return 0.0
    volatility = pstdev(returns)
    if volatility == 0:
        return 0.0
    return mean(returns) / volatility * sqrt(TRADING_DAYS)


def _max_drawdown(equity_values: list[float]) -> float:
    peak = equity_values[0]
    worst = 0.0
    for value in equity_values:
        peak = max(peak, value)
        drawdown = (peak - value) / peak if peak else 0.0
        worst = max(worst, drawdown)
    return worst


def _benchmark_closes_by_date(bars: list[PriceBar] | None) -> dict[object, float]:
    if not bars:
        return {}
    return {bar.ts: bar.close for bar in sorted(bars, key=lambda item: item.ts)}


def _benchmark_return(closes: dict[object, float], previous_ts: object, current_ts: object) -> float:
    try:
        previous_close = closes[previous_ts]
        current_close = closes[current_ts]
    except KeyError as exc:
        raise ValueError(
            f"benchmark is missing bars for {previous_ts} to {current_ts}"
        ) from exc
    return (current_close / previous_close) - 1.0
