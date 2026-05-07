from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class TechnicalProfile:
    last_close: float
    high_252d: float
    low_252d: float
    pct_from_high: float
    pct_from_low: float
    momentum_12_1: float
    momentum_3m: float
    momentum_1m: float
    realized_vol_annualized: float
    max_drawdown_252d: float
    beta_vs_benchmark: float
    correlation_vs_benchmark: float
    sample_days: int


def log_returns(closes: tuple[float, ...]) -> tuple[float, ...]:
    if len(closes) < 2:
        return ()
    out: list[float] = []
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        curr = closes[i]
        if prev <= 0 or curr <= 0:
            continue
        out.append(math.log(curr / prev))
    return tuple(out)


def realized_volatility(returns: tuple[float, ...], annualization: int = 252) -> float:
    n = len(returns)
    if n < 2:
        return 0.0
    mean = sum(returns) / n
    variance = sum((r - mean) ** 2 for r in returns) / (n - 1)
    return math.sqrt(variance * annualization)


def momentum_return(closes: tuple[float, ...], lookback_days: int, skip_days: int = 0) -> float:
    """Simple return over a window. lookback_days back, optionally excluding the most recent skip_days."""
    if not closes:
        return 0.0
    end_index = len(closes) - 1 - skip_days
    start_index = end_index - lookback_days
    if start_index < 0 or end_index <= start_index:
        return 0.0
    start = closes[start_index]
    end = closes[end_index]
    if start <= 0:
        return 0.0
    return (end - start) / start


def max_drawdown(closes: tuple[float, ...]) -> float:
    if not closes:
        return 0.0
    peak = closes[0]
    worst = 0.0
    for price in closes:
        if price > peak:
            peak = price
        if peak > 0:
            drawdown = (price - peak) / peak
            if drawdown < worst:
                worst = drawdown
    return worst


def beta_and_correlation(
    asset_returns: tuple[float, ...],
    benchmark_returns: tuple[float, ...],
) -> tuple[float, float]:
    n = min(len(asset_returns), len(benchmark_returns))
    if n < 30:
        return 0.0, 0.0
    a = asset_returns[-n:]
    b = benchmark_returns[-n:]
    a_mean = sum(a) / n
    b_mean = sum(b) / n
    cov = sum((a[i] - a_mean) * (b[i] - b_mean) for i in range(n)) / (n - 1)
    var_b = sum((b[i] - b_mean) ** 2 for i in range(n)) / (n - 1)
    var_a = sum((a[i] - a_mean) ** 2 for i in range(n)) / (n - 1)
    if var_b <= 0 or var_a <= 0:
        return 0.0, 0.0
    beta = cov / var_b
    correlation = cov / math.sqrt(var_a * var_b)
    return beta, correlation


def build_technical_profile(
    closes: tuple[float, ...],
    benchmark_closes: tuple[float, ...] = (),
) -> TechnicalProfile:
    if not closes:
        raise ValueError("closes is empty")

    window = closes[-252:] if len(closes) >= 252 else closes
    high = max(window)
    low = min(window)
    last = closes[-1]

    asset_returns = log_returns(closes)
    benchmark_returns = log_returns(benchmark_closes) if benchmark_closes else ()
    beta, correlation = beta_and_correlation(asset_returns, benchmark_returns)

    return TechnicalProfile(
        last_close=last,
        high_252d=high,
        low_252d=low,
        pct_from_high=(last - high) / high * 100 if high > 0 else 0.0,
        pct_from_low=(last - low) / low * 100 if low > 0 else 0.0,
        momentum_12_1=momentum_return(closes, lookback_days=231, skip_days=21),
        momentum_3m=momentum_return(closes, lookback_days=63),
        momentum_1m=momentum_return(closes, lookback_days=21),
        realized_vol_annualized=realized_volatility(asset_returns[-63:] if len(asset_returns) >= 63 else asset_returns),
        max_drawdown_252d=max_drawdown(window),
        beta_vs_benchmark=beta,
        correlation_vs_benchmark=correlation,
        sample_days=len(closes),
    )
