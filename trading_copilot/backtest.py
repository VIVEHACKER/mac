from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable

from datetime import timedelta as _timedelta

from .industry_rotation import (
    PriceHistoryProvider,
    PricePoint,
    YahooHistoryProvider,
)
from .long_history import YahooDailyHistoryProvider
from .macro import (
    FredCsvProvider,
    MacroDataError,
    MacroDataProvider,
    observation_on_or_before,
    percent_change_since_months,
    subtract_months,
)
from .regime import (
    RegimeIndicators,
    REGIME_LABELS,
    TEMPLATES,
    apply_vix_overlay,
    classify_regime,
)


# Symbols that appear in any portfolio template + the benchmark.
# Tickers we cannot reliably fetch (notes like "VIXY (caution)") are stripped to base.
TEMPLATE_ETFS: tuple[str, ...] = (
    "SPY", "VOO", "BIL", "SHV", "TLT", "TIP",
    "GLD", "SLV", "DBC", "XLE", "XLB", "XLF",
    "XLP", "XLU", "XLV", "XLI", "XLY", "XLC", "XLRE",
    "QQQ", "SMH", "IGV", "IWM", "EEM",
    "VTV", "SCHD", "UUP", "VIXY", "IBIT",
)
BENCHMARK = "SPY"
DEFAULT_HOLDING_DAYS = 21


@dataclass(frozen=True)
class BacktestPoint:
    as_of: date
    regime: str
    regime_label: str
    confidence: float
    triggers: tuple[str, ...]
    portfolio_return: float
    benchmark_return: float
    coverage_pct: float


@dataclass(frozen=True)
class BacktestResult:
    start: date
    end: date
    benchmark: str
    holding_days: int
    points: tuple[BacktestPoint, ...]
    cumulative_portfolio: float
    cumulative_benchmark: float
    avg_portfolio_return: float
    avg_benchmark_return: float
    portfolio_max_drawdown: float
    benchmark_max_drawdown: float
    portfolio_win_rate: float
    portfolio_beats_benchmark_rate: float
    regime_counts: dict[str, int]
    regime_returns: dict[str, tuple[float, float, int]]


def run_backtest(
    start: date,
    end: date,
    *,
    history_provider: PriceHistoryProvider | None = None,
    macro_provider: MacroDataProvider | None = None,
    holding_days: int = DEFAULT_HOLDING_DAYS,
    long_history: bool = False,
) -> BacktestResult:
    if history_provider is None:
        if long_history:
            fetch_start = date(start.year - 1, start.month, 1)
            fetch_end = end + _timedelta(days=int(holding_days * 1.6) + 30)
            history = YahooDailyHistoryProvider(fetch_start, fetch_end)
        else:
            history = YahooHistoryProvider()
    else:
        history = history_provider
    macro = macro_provider or FredCsvProvider()

    history_cache: dict[str, tuple[PricePoint, ...]] = {}
    for symbol in (BENCHMARK, "^VIX", *TEMPLATE_ETFS):
        try:
            history_cache[symbol] = history.history(symbol, range_period="5y", interval="1d")
        except Exception:
            history_cache[symbol] = ()

    macro_cache: dict[str, object] = {}

    def macro_series(series_id: str):
        if series_id in macro_cache:
            value = macro_cache[series_id]
            return value if value is not None else None
        try:
            value = macro.series(series_id)
        except Exception:
            value = None
        macro_cache[series_id] = value
        return value

    points: list[BacktestPoint] = []
    cursor = _normalize_first_of_month(start)
    last_cursor = _normalize_first_of_month(end)
    while cursor <= last_cursor:
        indicators = _gather_indicators_at(cursor, macro_series, history_cache)
        reading = classify_regime(indicators)
        base_template = TEMPLATES.get(reading.primary, TEMPLATES["transition"])
        template = apply_vix_overlay(base_template, indicators.vix)

        portfolio_return, coverage = _portfolio_forward_return(
            cursor, template, history_cache, holding_days
        )
        benchmark_return = _ticker_forward_return(cursor, BENCHMARK, history_cache, holding_days)

        if portfolio_return is not None and benchmark_return is not None:
            points.append(
                BacktestPoint(
                    as_of=cursor,
                    regime=reading.primary,
                    regime_label=reading.label,
                    confidence=reading.confidence,
                    triggers=reading.triggers,
                    portfolio_return=portfolio_return,
                    benchmark_return=benchmark_return,
                    coverage_pct=coverage,
                )
            )

        cursor = _add_months(cursor, 1)

    cumulative_portfolio = _compound([p.portfolio_return for p in points])
    cumulative_benchmark = _compound([p.benchmark_return for p in points])
    avg_port = _mean([p.portfolio_return for p in points])
    avg_bench = _mean([p.benchmark_return for p in points])
    port_dd = _max_drawdown_from_returns([p.portfolio_return for p in points])
    bench_dd = _max_drawdown_from_returns([p.benchmark_return for p in points])
    win_rate = _share(p.portfolio_return > 0 for p in points) if points else 0.0
    beat_rate = _share(p.portfolio_return > p.benchmark_return for p in points) if points else 0.0

    regime_counts: dict[str, int] = {}
    regime_buckets: dict[str, list[tuple[float, float]]] = {}
    for p in points:
        regime_counts[p.regime] = regime_counts.get(p.regime, 0) + 1
        regime_buckets.setdefault(p.regime, []).append((p.portfolio_return, p.benchmark_return))
    regime_returns: dict[str, tuple[float, float, int]] = {}
    for name, pairs in regime_buckets.items():
        port_avg = sum(x[0] for x in pairs) / len(pairs)
        bench_avg = sum(x[1] for x in pairs) / len(pairs)
        regime_returns[name] = (port_avg, bench_avg, len(pairs))

    return BacktestResult(
        start=start,
        end=end,
        benchmark=BENCHMARK,
        holding_days=holding_days,
        points=tuple(points),
        cumulative_portfolio=cumulative_portfolio,
        cumulative_benchmark=cumulative_benchmark,
        avg_portfolio_return=avg_port,
        avg_benchmark_return=avg_bench,
        portfolio_max_drawdown=port_dd,
        benchmark_max_drawdown=bench_dd,
        portfolio_win_rate=win_rate,
        portfolio_beats_benchmark_rate=beat_rate,
        regime_counts=regime_counts,
        regime_returns=regime_returns,
    )


def format_backtest_report(result: BacktestResult) -> str:
    lines = [
        f"# Macro Regime Backtest - {result.start.isoformat()} to {result.end.isoformat()}",
        "",
        "Not investment advice. This is an out-of-sample style review of the regime "
        "classifier and the portfolio templates against historical ETF prices. "
        "Past performance does not predict future results.",
        "",
        f"Benchmark: {result.benchmark}",
        f"Holding window: {result.holding_days} trading days (~1 month forward).",
        f"Periods sampled: {len(result.points)}",
        "",
        "## Aggregate Performance",
        f"- Cumulative portfolio: {result.cumulative_portfolio * 100:+.2f}%",
        f"- Cumulative benchmark ({result.benchmark}): {result.cumulative_benchmark * 100:+.2f}%",
        f"- Excess vs benchmark: {(result.cumulative_portfolio - result.cumulative_benchmark) * 100:+.2f}%",
        f"- Avg monthly portfolio return: {result.avg_portfolio_return * 100:+.2f}%",
        f"- Avg monthly benchmark return: {result.avg_benchmark_return * 100:+.2f}%",
        f"- Portfolio max drawdown (compounded path): {result.portfolio_max_drawdown * 100:.2f}%",
        f"- Benchmark max drawdown (compounded path): {result.benchmark_max_drawdown * 100:.2f}%",
        f"- Portfolio win rate (positive months): {result.portfolio_win_rate * 100:.1f}%",
        f"- Beat-benchmark rate (months): {result.portfolio_beats_benchmark_rate * 100:.1f}%",
        "",
        "## Regime Distribution and Per-Regime Returns",
        "| Regime | Count | Avg Portfolio | Avg Benchmark | Excess |",
        "|---|---:|---:|---:|---:|",
    ]
    for regime in sorted(result.regime_counts.keys(), key=lambda r: -result.regime_counts[r]):
        port_avg, bench_avg, count = result.regime_returns.get(regime, (0.0, 0.0, 0))
        lines.append(
            f"| {REGIME_LABELS.get(regime, regime)} | {count} | "
            f"{port_avg * 100:+.2f}% | {bench_avg * 100:+.2f}% | "
            f"{(port_avg - bench_avg) * 100:+.2f}% |"
        )

    lines.extend(["", "## Period Detail", "| As Of | Regime | Coverage | Portfolio | Benchmark | Excess |", "|---|---|---:|---:|---:|---:|"])
    for p in result.points:
        lines.append(
            f"| {p.as_of.isoformat()} | {p.regime_label} | {p.coverage_pct:.0f}% | "
            f"{p.portfolio_return * 100:+.2f}% | {p.benchmark_return * 100:+.2f}% | "
            f"{(p.portfolio_return - p.benchmark_return) * 100:+.2f}% |"
        )

    lines.extend(
        [
            "",
            "## Caveats",
            "- Each period uses the macro / VIX / drawdown data available on or before "
            "the as-of date; lookahead bias is avoided.",
            "- Portfolio coverage <100% means some template ETFs had no usable price "
            "history; weights of missing assets are dropped and remaining weights are "
            "normalized to 100.",
            "- VIX call spread, IBIT, and other thin proxies are treated as cash if "
            "history is unavailable.",
            "- Transaction costs, spread, and tax are not modelled.",
            "- 21-day forward window means each month is evaluated as buy-at-open / "
            "sell-21d-later; real rebalancing has frictions this misses.",
            "- The classifier and template thresholds were authored with current "
            "market structure in mind, so the recent backtest is partly in-sample.",
        ]
    )
    return "\n".join(lines)


def backtest_to_csv(result: BacktestResult) -> str:
    lines = ["as_of,regime,confidence,coverage_pct,portfolio_return,benchmark_return,excess"]
    for p in result.points:
        lines.append(
            f"{p.as_of.isoformat()},{p.regime},{p.confidence:.2f},{p.coverage_pct:.1f},"
            f"{p.portfolio_return:.6f},{p.benchmark_return:.6f},"
            f"{p.portfolio_return - p.benchmark_return:.6f}"
        )
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------------------
# Helpers (point-in-time indicator lookup, forward returns, math)
# --------------------------------------------------------------------------------------


def _gather_indicators_at(
    as_of: date,
    macro_series_lookup,
    history_cache: dict[str, tuple[PricePoint, ...]],
) -> RegimeIndicators:
    cpi = macro_series_lookup("CPIAUCSL")
    core_cpi = macro_series_lookup("CPILFESL")
    unemp = macro_series_lookup("UNRATE")
    fedf = macro_series_lookup("FEDFUNDS")
    curve = macro_series_lookup("T10Y2Y")
    indpro = macro_series_lookup("INDPRO")
    retail = macro_series_lookup("RSAFS")
    credit = macro_series_lookup("BAMLH0A0HYM2")

    cpi_yoy = _try_pct(cpi, 12, as_of)
    core_cpi_yoy = _try_pct(core_cpi, 12, as_of)
    cpi_6m_delta = _yoy_delta(cpi, 6, as_of)
    unemployment = _try_value(unemp, as_of)
    unemployment_6m_delta = _try_change(unemp, 6, as_of)
    fed_funds = _try_value(fedf, as_of)
    fed_funds_6m_delta = _try_change(fedf, 6, as_of)
    yield_curve = _try_value(curve, as_of)
    indpro_yoy = _try_pct(indpro, 12, as_of)
    retail_yoy = _try_pct(retail, 12, as_of)
    credit_spread_hy = _try_value(credit, as_of)

    vix_hist = [p for p in history_cache.get("^VIX", ()) if p.observed_at <= as_of]
    vix = vix_hist[-1].close if vix_hist else None
    vix_window = vix_hist[-20:] if vix_hist else []
    vix_20d_avg = (sum(p.close for p in vix_window) / len(vix_window)) if vix_window else None

    spy_hist = [p for p in history_cache.get(BENCHMARK, ()) if p.observed_at <= as_of][-252:]
    spy_drawdown_252d = None
    if len(spy_hist) >= 2:
        peak = max(p.close for p in spy_hist)
        last = spy_hist[-1].close
        if peak > 0:
            spy_drawdown_252d = (last - peak) / peak

    return RegimeIndicators(
        cpi_yoy=cpi_yoy,
        core_cpi_yoy=core_cpi_yoy,
        cpi_6m_delta=cpi_6m_delta or 0.0,
        unemployment=unemployment,
        unemployment_6m_delta=unemployment_6m_delta or 0.0,
        fed_funds=fed_funds,
        fed_funds_6m_delta=fed_funds_6m_delta or 0.0,
        yield_curve_spread=yield_curve,
        indpro_yoy=indpro_yoy,
        retail_yoy=retail_yoy,
        vix=vix,
        vix_20d_avg=vix_20d_avg,
        spy_drawdown_252d=spy_drawdown_252d,
        credit_spread_hy=credit_spread_hy,
        sources=(),
    )


def _try_pct(series, months: int, as_of: date) -> float | None:
    if series is None:
        return None
    try:
        return percent_change_since_months(series, months, as_of=as_of)
    except (MacroDataError, Exception):
        return None


def _try_value(series, as_of: date) -> float | None:
    if series is None:
        return None
    try:
        obs = observation_on_or_before(series, as_of)
        return obs.value
    except Exception:
        return None


def _try_change(series, months: int, as_of: date) -> float | None:
    if series is None:
        return None
    try:
        latest = observation_on_or_before(series, as_of)
        ref_date = subtract_months(as_of, months)
        ref = observation_on_or_before(series, ref_date)
        return latest.value - ref.value
    except Exception:
        return None


def _yoy_delta(series, months_back: int, as_of: date) -> float | None:
    if series is None:
        return None
    latest_yoy = _try_pct(series, 12, as_of)
    if latest_yoy is None:
        return None
    ref_date = subtract_months(as_of, months_back)
    ref_yoy = _try_pct(series, 12, ref_date)
    if ref_yoy is None:
        return None
    return latest_yoy - ref_yoy


def _portfolio_forward_return(
    as_of: date,
    template,
    history_cache: dict[str, tuple[PricePoint, ...]],
    holding_days: int,
) -> tuple[float | None, float]:
    weighted_return = 0.0
    used_weight = 0.0
    total_weight = 0.0
    for alloc in template.allocations:
        symbol = _normalize_etf_symbol(alloc.proxy_etf)
        total_weight += alloc.weight_pct
        ret = _ticker_forward_return(as_of, symbol, history_cache, holding_days)
        if ret is None:
            continue
        weighted_return += alloc.weight_pct * ret
        used_weight += alloc.weight_pct
    if used_weight <= 0 or total_weight <= 0:
        return None, 0.0
    normalized = weighted_return / used_weight
    coverage = used_weight / total_weight * 100.0
    return normalized, coverage


def _ticker_forward_return(
    as_of: date,
    symbol: str,
    history_cache: dict[str, tuple[PricePoint, ...]],
    holding_days: int,
) -> float | None:
    history = history_cache.get(symbol)
    if not history:
        return None
    entry_index = None
    for i, point in enumerate(history):
        if point.observed_at >= as_of:
            entry_index = i
            break
    if entry_index is None:
        return None
    exit_index = entry_index + holding_days
    if exit_index >= len(history):
        return None
    entry_price = history[entry_index].close
    exit_price = history[exit_index].close
    if entry_price <= 0:
        return None
    return (exit_price - entry_price) / entry_price


def _normalize_etf_symbol(label: str) -> str:
    """Strip parenthetical caveats like 'VIXY (caution)' or 'IBIT (or BTC direct)'."""
    base = label.strip().split("(", 1)[0].strip()
    return base.upper()


def _normalize_first_of_month(d: date) -> date:
    return date(d.year, d.month, 1)


def _add_months(d: date, months: int) -> date:
    month_index = d.year * 12 + d.month - 1 + months
    year = month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def _compound(returns: Iterable[float]) -> float:
    value = 1.0
    for r in returns:
        value *= (1.0 + r)
    return value - 1.0


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _share(condition_iter: Iterable[bool]) -> float:
    items = list(condition_iter)
    if not items:
        return 0.0
    return sum(1 for v in items if v) / len(items)


def _max_drawdown_from_returns(returns: list[float]) -> float:
    if not returns:
        return 0.0
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for r in returns:
        equity *= (1.0 + r)
        if equity > peak:
            peak = equity
        drawdown = (equity - peak) / peak
        if drawdown < worst:
            worst = drawdown
    return worst


__all__ = [
    "BacktestPoint",
    "BacktestResult",
    "TEMPLATE_ETFS",
    "BENCHMARK",
    "DEFAULT_HOLDING_DAYS",
    "run_backtest",
    "format_backtest_report",
    "backtest_to_csv",
]
