from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, log, prod, sqrt
from statistics import mean, pstdev

from data.models import PriceBar


@dataclass(frozen=True)
class PairSignal:
    long_symbol: str
    short_symbol: str
    z_score: float
    hedge_ratio: float


@dataclass(frozen=True)
class PairAnalysis:
    first_symbol: str
    second_symbol: str
    observations: int
    hedge_ratio: float
    intercept: float
    spread_mean: float
    spread_std: float
    z_score: float
    correlation: float
    half_life_days: float | None
    state: str
    signal: PairSignal | None


@dataclass(frozen=True)
class PairBacktestResult:
    first_symbol: str
    second_symbol: str
    observations: int
    trades: int
    gross_return: float
    net_return: float
    annualized_return: float
    sharpe: float
    max_drawdown: float
    hit_rate: float
    fee_bps: float
    slippage_bps: float
    passed: bool
    reasons: tuple[str, ...]


def analyze_pair(
    first: list[PriceBar],
    second: list[PriceBar],
    *,
    lookback: int = 252,
    entry_z: float = 2.0,
    exit_z: float = 0.5,
    min_observations: int = 60,
) -> PairAnalysis | None:
    """Analyze a two-asset spread using log prices and an OLS hedge ratio."""
    if lookback < 2:
        raise ValueError("lookback must be >= 2")
    if min_observations < 2:
        raise ValueError("min_observations must be >= 2")
    if entry_z <= 0:
        raise ValueError("entry_z must be positive")
    if exit_z < 0:
        raise ValueError("exit_z must be >= 0")

    aligned = _aligned_log_prices(first, second, lookback=lookback)
    if len(aligned) < min_observations:
        return None

    first_logs = [item[0] for item in aligned]
    second_logs = [item[1] for item in aligned]
    regression = _linear_regression(y=first_logs, x=second_logs)
    if regression is None:
        return None
    intercept, hedge_ratio = regression
    spreads = [
        y - (intercept + hedge_ratio * x) for y, x in zip(first_logs, second_logs, strict=True)
    ]
    spread_std = pstdev(spreads)
    if spread_std == 0:
        return None

    spread_mean = mean(spreads)
    z_score = (spreads[-1] - spread_mean) / spread_std
    first_symbol = first[-1].symbol
    second_symbol = second[-1].symbol

    signal: PairSignal | None = None
    if z_score >= entry_z:
        signal = PairSignal(
            long_symbol=second_symbol,
            short_symbol=first_symbol,
            z_score=z_score,
            hedge_ratio=hedge_ratio,
        )
        state = "entry"
    elif z_score <= -entry_z:
        signal = PairSignal(
            long_symbol=first_symbol,
            short_symbol=second_symbol,
            z_score=z_score,
            hedge_ratio=hedge_ratio,
        )
        state = "entry"
    elif abs(z_score) <= exit_z:
        state = "exit"
    else:
        state = "watch"

    return PairAnalysis(
        first_symbol=first_symbol,
        second_symbol=second_symbol,
        observations=len(aligned),
        hedge_ratio=hedge_ratio,
        intercept=intercept,
        spread_mean=spread_mean,
        spread_std=spread_std,
        z_score=z_score,
        correlation=_correlation(first_logs, second_logs),
        half_life_days=_half_life(spreads),
        state=state,
        signal=signal,
    )


def pairs_zscore_signal(
    first: list[PriceBar],
    second: list[PriceBar],
    *,
    entry_z: float = 2.0,
) -> PairSignal | None:
    analysis = analyze_pair(
        first,
        second,
        lookback=max(len(first), len(second)),
        entry_z=entry_z,
        min_observations=30,
    )
    if analysis is None:
        return None
    return analysis.signal


def backtest_pair_mean_reversion(
    first: list[PriceBar],
    second: list[PriceBar],
    *,
    lookback: int = 252,
    entry_z: float = 2.0,
    exit_z: float = 0.5,
    min_observations: int = 80,
    fee_bps: float = 2.0,
    slippage_bps: float = 2.0,
    min_trades: int = 3,
    min_sharpe: float = 0.0,
    max_drawdown_limit: float = 0.2,
) -> PairBacktestResult | None:
    """Run a close-to-close rolling pair backtest with execution costs."""
    if lookback < 2:
        raise ValueError("lookback must be >= 2")
    if min_observations < lookback + 2:
        min_observations = lookback + 2
    if fee_bps < 0 or slippage_bps < 0:
        raise ValueError("fee_bps and slippage_bps must be >= 0")

    aligned = _aligned_prices(first, second)
    if len(aligned) < min_observations:
        return None

    returns: list[float] = []
    gross_returns: list[float] = []
    position = 0.0
    trades = 0
    cost_rate = (fee_bps + slippage_bps) / 10_000.0

    for index in range(lookback, len(aligned) - 1):
        window = aligned[index - lookback : index]
        first_logs = [log(item[1]) for item in window]
        second_logs = [log(item[2]) for item in window]
        regression = _linear_regression(y=first_logs, x=second_logs)
        if regression is None:
            continue
        intercept, hedge_ratio = regression
        spread_window = [
            y - (intercept + hedge_ratio * x)
            for y, x in zip(first_logs, second_logs, strict=True)
        ]
        spread_std = pstdev(spread_window)
        if spread_std == 0:
            continue

        today = aligned[index]
        next_day = aligned[index + 1]
        today_spread = log(today[1]) - (intercept + hedge_ratio * log(today[2]))
        z_score = (today_spread - mean(spread_window)) / spread_std
        desired_position = _next_pair_position(
            current=position,
            z_score=z_score,
            entry_z=entry_z,
            exit_z=exit_z,
        )

        spread_return = (
            log(next_day[1] / today[1]) - hedge_ratio * log(next_day[2] / today[2])
        ) / (1.0 + abs(hedge_ratio))
        gross_return = desired_position * spread_return
        turnover = abs(desired_position - position)
        cost = turnover * cost_rate
        if turnover > 0 and desired_position != 0:
            trades += 1
        gross_returns.append(gross_return)
        returns.append(gross_return - cost)
        position = desired_position

    if not returns:
        return None

    gross_return = prod(1.0 + item for item in gross_returns) - 1.0
    net_return = prod(1.0 + item for item in returns) - 1.0
    daily_std = pstdev(returns)
    sharpe = 0.0 if daily_std == 0 else mean(returns) / daily_std * sqrt(252)
    annualized_return = _annualized_return(net_return, len(returns))
    max_drawdown = _max_drawdown(returns)
    active_returns = [item for item in returns if item != 0]
    hit_rate = (
        sum(1 for item in active_returns if item > 0) / len(active_returns)
        if active_returns
        else 0.0
    )
    reasons = _pair_backtest_reasons(
        trades=trades,
        net_return=net_return,
        sharpe=sharpe,
        max_drawdown=max_drawdown,
        min_trades=min_trades,
        min_sharpe=min_sharpe,
        max_drawdown_limit=max_drawdown_limit,
    )
    return PairBacktestResult(
        first_symbol=first[-1].symbol,
        second_symbol=second[-1].symbol,
        observations=len(returns),
        trades=trades,
        gross_return=gross_return,
        net_return=net_return,
        annualized_return=annualized_return,
        sharpe=sharpe,
        max_drawdown=max_drawdown,
        hit_rate=hit_rate,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        passed=not reasons,
        reasons=tuple(reasons),
    )


def _aligned_log_prices(
    first: list[PriceBar],
    second: list[PriceBar],
    *,
    lookback: int,
) -> list[tuple[float, float]]:
    first_by_date = {bar.ts: bar.close for bar in first if bar.close > 0}
    second_by_date = {bar.ts: bar.close for bar in second if bar.close > 0}
    dates = sorted(set(first_by_date) & set(second_by_date))
    selected = dates[-lookback:]
    return [(log(first_by_date[day]), log(second_by_date[day])) for day in selected]


def _aligned_prices(first: list[PriceBar], second: list[PriceBar]) -> list[tuple[object, float, float]]:
    first_by_date = {bar.ts: bar.close for bar in first if bar.close > 0}
    second_by_date = {bar.ts: bar.close for bar in second if bar.close > 0}
    dates = sorted(set(first_by_date) & set(second_by_date))
    return [(day, first_by_date[day], second_by_date[day]) for day in dates]


def _linear_regression(y: list[float], x: list[float]) -> tuple[float, float] | None:
    if len(y) != len(x) or len(y) < 2:
        return None
    x_mean = mean(x)
    y_mean = mean(y)
    denominator = sum((item - x_mean) ** 2 for item in x)
    if denominator == 0:
        return None
    hedge_ratio = (
        sum((xi - x_mean) * (yi - y_mean) for yi, xi in zip(y, x, strict=True))
        / denominator
    )
    intercept = y_mean - hedge_ratio * x_mean
    if not isfinite(hedge_ratio) or not isfinite(intercept):
        return None
    return intercept, hedge_ratio


def _correlation(first: list[float], second: list[float]) -> float:
    if len(first) != len(second) or len(first) < 2:
        return 0.0
    first_std = pstdev(first)
    second_std = pstdev(second)
    if first_std == 0 or second_std == 0:
        return 0.0
    first_mean = mean(first)
    second_mean = mean(second)
    covariance = mean(
        (left - first_mean) * (right - second_mean)
        for left, right in zip(first, second, strict=True)
    )
    return covariance / (first_std * second_std)


def _next_pair_position(
    *,
    current: float,
    z_score: float,
    entry_z: float,
    exit_z: float,
) -> float:
    if abs(z_score) <= exit_z:
        return 0.0
    if z_score >= entry_z:
        return -1.0
    if z_score <= -entry_z:
        return 1.0
    return current


def _half_life(spreads: list[float]) -> float | None:
    if len(spreads) < 3:
        return None
    lagged = spreads[:-1]
    deltas = [
        current - previous for previous, current in zip(spreads[:-1], spreads[1:], strict=True)
    ]
    regression = _linear_regression(y=deltas, x=lagged)
    if regression is None:
        return None
    _, slope = regression
    if slope >= 0:
        return None
    half_life = -log(2) / slope
    if not isfinite(half_life) or half_life <= 0:
        return None
    return half_life


def _annualized_return(cumulative_return: float, observations: int) -> float:
    if observations <= 0 or cumulative_return <= -1.0:
        return 0.0
    return (1.0 + cumulative_return) ** (252 / observations) - 1.0


def _max_drawdown(returns: list[float]) -> float:
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for item in returns:
        equity *= 1.0 + item
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak)
    return max_drawdown


def _pair_backtest_reasons(
    *,
    trades: int,
    net_return: float,
    sharpe: float,
    max_drawdown: float,
    min_trades: int,
    min_sharpe: float,
    max_drawdown_limit: float,
) -> list[str]:
    reasons: list[str] = []
    if trades < min_trades:
        reasons.append(f"trades {trades} < required {min_trades}")
    if net_return <= 0:
        reasons.append(f"net return {net_return * 100:.2f}% <= 0")
    if sharpe < min_sharpe:
        reasons.append(f"sharpe {sharpe:.2f} < required {min_sharpe:.2f}")
    if max_drawdown > max_drawdown_limit:
        reasons.append(
            f"max drawdown {max_drawdown * 100:.2f}% > limit {max_drawdown_limit * 100:.2f}%"
        )
    return reasons
