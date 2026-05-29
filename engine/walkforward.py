from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from itertools import product
from typing import cast

from data.models import DelistingReturn, PriceBar, UniverseMember
from engine.factor_portfolio import (
    FactorPortfolioResult,
    FundamentalsInput,
    run_factor_rotation_backtest,
)

SELECTION_METRICS = {
    "annualized-excess",
    "annualized-return",
    "excess-sharpe",
    "return-drawdown",
    "risk-first",
}


@dataclass(frozen=True)
class FactorParams:
    momentum_lookback: int
    reversal_lookback: int
    volatility_lookback: int
    risk_filter_lookback: int
    top_n: int
    weighting: str
    rebalance_days: int
    defensive_symbol: str | None
    max_risk_weight: float
    drawdown_guard: float
    defensive_only: bool
    crash_hedge_weight: float
    crash_hedge_trigger_lookback: int
    crash_hedge_trigger_drawdown: float
    crash_hedge_selection_lookback: int
    crash_hedge_hold_days: int

    @property
    def label(self) -> str:
        defensive = self.defensive_symbol or "cash"
        base = (
            f"M{self.momentum_lookback}/R{self.reversal_lookback}/"
            f"V{self.volatility_lookback}/RF{self.risk_filter_lookback}/"
            f"Top{self.top_n}/{self.weighting}/Reb{self.rebalance_days}/Def{defensive}/"
            f"MaxRisk{self.max_risk_weight:.2f}/DD{self.drawdown_guard:.2f}/"
            f"DefOnly{int(self.defensive_only)}"
        )
        if self.crash_hedge_weight <= 0:
            return base
        return (
            f"{base}/Crash{self.crash_hedge_weight:.2f}/"
            f"CT{self.crash_hedge_trigger_lookback}/"
            f"CDD{self.crash_hedge_trigger_drawdown:.2f}/"
            f"CS{self.crash_hedge_selection_lookback}/"
            f"CH{self.crash_hedge_hold_days}"
        )


@dataclass(frozen=True)
class WalkForwardRow:
    train_start: date
    train_end: date
    test_start: date
    test_end: date
    selected: FactorParams
    train: FactorPortfolioResult
    test: FactorPortfolioResult


@dataclass(frozen=True)
class WalkForwardReport:
    symbols: tuple[str, ...]
    selection_metric: str
    validation_years: int
    rows: list[WalkForwardRow]

    @property
    def positive_test_rate(self) -> float:
        if not self.rows:
            return 0.0
        return sum(row.test.annualized_excess_return > 0 for row in self.rows) / len(self.rows)

    @property
    def average_test_annualized_excess(self) -> float:
        if not self.rows:
            return 0.0
        return sum(row.test.annualized_excess_return for row in self.rows) / len(self.rows)


def run_factor_walk_forward(
    bars_by_symbol: dict[str, list[PriceBar]],
    *,
    benchmark_bars: list[PriceBar],
    fundamentals_by_symbol: FundamentalsInput | None = None,
    universe_members: list[UniverseMember] | None = None,
    delisting_returns: list[DelistingReturn] | None = None,
    start: date,
    end: date,
    train_years: int = 5,
    validation_years: int = 0,
    test_years: int = 3,
    step_years: int = 1,
    momentum_lookbacks: tuple[int, ...] = (126, 252),
    reversal_lookbacks: tuple[int, ...] = (21,),
    volatility_lookbacks: tuple[int, ...] = (63,),
    top_ns: tuple[int, ...] = (2, 3),
    risk_filter_lookback: int = 200,
    risk_filter_lookbacks: tuple[int, ...] | None = None,
    weighting_modes: tuple[str, ...] = ("inverse-vol",),
    selection_metric: str = "annualized-excess",
    rebalance_days: int = 21,
    rebalance_days_values: tuple[int, ...] | None = None,
    fee_bps: float = 2.0,
    defensive_symbol: str | None = "TLT",
    defensive_symbols: tuple[str | None, ...] | None = None,
    max_risk_weight: float = 1.0,
    max_risk_weights: tuple[float, ...] | None = None,
    drawdown_guard: float = 0.0,
    drawdown_guards: tuple[float, ...] | None = None,
    defensive_only: bool = False,
    ensemble_momentum_lookbacks: tuple[int, ...] | None = None,
    ensemble_risk_filter_lookbacks: tuple[int, ...] | None = None,
    risk_filter_vote_threshold: float = 0.5,
    defensive_basket: tuple[str | None, ...] | None = None,
    defensive_selection_lookback: int = 63,
    volatility_target: float = 0.0,
    max_leverage: float = 1.0,
    crash_hedge_symbols: tuple[str, ...] | None = None,
    crash_hedge_weight: float = 0.0,
    crash_hedge_trigger_lookback: int = 21,
    crash_hedge_trigger_drawdown: float = 0.10,
    crash_hedge_selection_lookback: int = 5,
    crash_hedge_hold_days: int = 0,
    crash_hedge_weights: tuple[float, ...] | None = None,
    crash_hedge_trigger_lookbacks: tuple[int, ...] | None = None,
    crash_hedge_trigger_drawdowns: tuple[float, ...] | None = None,
    crash_hedge_selection_lookbacks: tuple[int, ...] | None = None,
    crash_hedge_hold_days_values: tuple[int, ...] | None = None,
    regime_cash_enable: bool = False,
    regime_cash_corr_symbol: str = "TLT",
    regime_cash_corr_window: int = 60,
    regime_cash_corr_threshold: float = 0.2,
    regime_cash_override_symbol: str | None = "SHY",
) -> WalkForwardReport:
    if min(train_years, test_years, step_years) < 1:
        raise ValueError("train_years, test_years and step_years must be >= 1")
    if validation_years < 0 or validation_years >= train_years:
        raise ValueError("validation_years must be >= 0 and smaller than train_years")
    risk_filters = risk_filter_lookbacks or (risk_filter_lookback,)
    if any(item < 0 for item in risk_filters):
        raise ValueError("risk filter lookbacks must be >= 0")
    if any(item not in {"inverse-vol", "equal"} for item in weighting_modes):
        raise ValueError("weighting modes must be 'inverse-vol' or 'equal'")
    rebalances = rebalance_days_values or (rebalance_days,)
    if any(item < 1 for item in rebalances):
        raise ValueError("rebalance_days values must be >= 1")
    defensive_choices = defensive_symbols or (defensive_symbol,)
    risk_weight_choices = max_risk_weights or (max_risk_weight,)
    if any(not 0 < item <= 1 for item in risk_weight_choices):
        raise ValueError("max risk weights must be > 0 and <= 1")
    drawdown_guard_choices = drawdown_guards or (drawdown_guard,)
    if any(not 0 <= item < 1 for item in drawdown_guard_choices):
        raise ValueError("drawdown guards must be >= 0 and < 1")
    if ensemble_momentum_lookbacks and any(item < 1 for item in ensemble_momentum_lookbacks):
        raise ValueError("ensemble momentum lookbacks must be >= 1")
    if ensemble_risk_filter_lookbacks and any(item < 0 for item in ensemble_risk_filter_lookbacks):
        raise ValueError("ensemble risk filter lookbacks must be >= 0")
    if not 0 < risk_filter_vote_threshold <= 1:
        raise ValueError("risk_filter_vote_threshold must be > 0 and <= 1")
    if defensive_selection_lookback < 1:
        raise ValueError("defensive_selection_lookback must be >= 1")
    if volatility_target < 0:
        raise ValueError("volatility_target must be >= 0")
    if max_leverage <= 0:
        raise ValueError("max_leverage must be > 0")
    crash_weight_choices = crash_hedge_weights or (crash_hedge_weight,)
    if any(not 0 <= item <= 1 for item in crash_weight_choices):
        raise ValueError("crash hedge weights must be >= 0 and <= 1")
    crash_trigger_lookback_choices = crash_hedge_trigger_lookbacks or (
        crash_hedge_trigger_lookback,
    )
    if any(item < 1 for item in crash_trigger_lookback_choices):
        raise ValueError("crash hedge trigger lookbacks must be >= 1")
    crash_trigger_drawdown_choices = crash_hedge_trigger_drawdowns or (
        crash_hedge_trigger_drawdown,
    )
    if any(not 0 < item < 1 for item in crash_trigger_drawdown_choices):
        raise ValueError("crash hedge trigger drawdowns must be > 0 and < 1")
    crash_selection_lookback_choices = crash_hedge_selection_lookbacks or (
        crash_hedge_selection_lookback,
    )
    if any(item < 1 for item in crash_selection_lookback_choices):
        raise ValueError("crash hedge selection lookbacks must be >= 1")
    crash_hold_day_choices = crash_hedge_hold_days_values or (crash_hedge_hold_days,)
    if any(item < 0 for item in crash_hold_day_choices):
        raise ValueError("crash hedge hold days must be >= 0")
    if selection_metric not in SELECTION_METRICS:
        raise ValueError(f"selection_metric must be one of {sorted(SELECTION_METRICS)}")
    max_lookback = max(
        (
            *momentum_lookbacks,
            *(ensemble_momentum_lookbacks or ()),
            *reversal_lookbacks,
            *volatility_lookbacks,
            *risk_filters,
            *(ensemble_risk_filter_lookbacks or ()),
            defensive_selection_lookback if defensive_basket and len(defensive_basket) > 1 else 0,
            *(crash_trigger_lookback_choices if crash_hedge_symbols else (0,)),
            *(crash_selection_lookback_choices if crash_hedge_symbols else (0,)),
        )
    )
    rows: list[WalkForwardRow] = []
    train_start = start
    while True:
        train_end = _add_years(train_start, train_years)
        test_start = train_end
        test_end = min(_add_years(test_start, test_years), end)
        if test_start >= end or test_end <= test_start:
            break
        score_start = _add_years(train_end, -validation_years) if validation_years else train_start
        train_warmup_start = _warmup_start(train_start, max_lookback)
        score_warmup_start = _warmup_start(score_start, max_lookback)
        test_warmup_start = _warmup_start(test_start, max_lookback)
        train_bars = _slice_bars_by_symbol(bars_by_symbol, train_warmup_start, train_end)
        score_bars = _slice_bars_by_symbol(bars_by_symbol, score_warmup_start, train_end)
        test_bars = _slice_bars_by_symbol(bars_by_symbol, test_warmup_start, test_end)
        train_benchmark = _slice_bars(benchmark_bars, train_warmup_start, train_end)
        score_benchmark = _slice_bars(benchmark_bars, score_warmup_start, train_end)
        test_benchmark = _slice_bars(benchmark_bars, test_warmup_start, test_end)
        candidates: list[tuple[FactorParams, FactorPortfolioResult]] = []
        for (
            momentum,
            reversal,
            volatility,
            risk_filter,
            top_n,
            weighting,
            candidate_rebalance,
            candidate_defensive,
            candidate_max_risk,
            candidate_drawdown_guard,
            candidate_crash_weight,
            candidate_crash_trigger_lookback,
            candidate_crash_trigger_drawdown,
            candidate_crash_selection_lookback,
            candidate_crash_hold_days,
        ) in product(
            momentum_lookbacks,
            reversal_lookbacks,
            volatility_lookbacks,
            risk_filters,
            top_ns,
            weighting_modes,
            rebalances,
            defensive_choices,
            risk_weight_choices,
            drawdown_guard_choices,
            crash_weight_choices,
            crash_trigger_lookback_choices,
            crash_trigger_drawdown_choices,
            crash_selection_lookback_choices,
            crash_hold_day_choices,
        ):
            momentum_value = cast(int, momentum)
            reversal_value = cast(int, reversal)
            volatility_value = cast(int, volatility)
            risk_filter_value = cast(int, risk_filter)
            top_n_value = cast(int, top_n)
            weighting_value = cast(str, weighting)
            rebalance_value = cast(int, candidate_rebalance)
            defensive_value = cast(str | None, candidate_defensive)
            max_risk_value = cast(float, candidate_max_risk)
            drawdown_guard_value = cast(float, candidate_drawdown_guard)
            crash_weight_value = cast(float, candidate_crash_weight)
            crash_trigger_lookback_value = cast(int, candidate_crash_trigger_lookback)
            crash_trigger_drawdown_value = cast(float, candidate_crash_trigger_drawdown)
            crash_selection_lookback_value = cast(int, candidate_crash_selection_lookback)
            crash_hold_days_value = cast(int, candidate_crash_hold_days)
            params = FactorParams(
                momentum_value,
                reversal_value,
                volatility_value,
                risk_filter_value,
                top_n_value,
                weighting_value,
                rebalance_value,
                defensive_value,
                max_risk_value,
                drawdown_guard_value,
                defensive_only,
                crash_weight_value,
                crash_trigger_lookback_value,
                crash_trigger_drawdown_value,
                crash_selection_lookback_value,
                crash_hold_days_value,
            )
            try:
                score = run_factor_rotation_backtest(
                    score_bars,
                    benchmark_bars=score_benchmark,
                    fundamentals_by_symbol=fundamentals_by_symbol,
                    universe_members=universe_members,
                    delisting_returns=delisting_returns,
                    momentum_lookback=momentum_value,
                    reversal_lookback=reversal_value,
                    volatility_lookback=volatility_value,
                    risk_filter_lookback=risk_filter_value,
                    top_n=top_n_value,
                    rebalance_days=rebalance_value,
                    fee_bps=fee_bps,
                    defensive_symbol=defensive_value,
                    weighting=weighting_value,
                    max_risk_weight=max_risk_value,
                    drawdown_guard=drawdown_guard_value,
                    defensive_only=defensive_only,
                    ensemble_momentum_lookbacks=ensemble_momentum_lookbacks,
                    ensemble_risk_filter_lookbacks=ensemble_risk_filter_lookbacks,
                    risk_filter_vote_threshold=risk_filter_vote_threshold,
                    defensive_symbols=defensive_basket,
                    defensive_selection_lookback=defensive_selection_lookback,
                    volatility_target=volatility_target,
                    max_leverage=max_leverage,
                    crash_hedge_symbols=crash_hedge_symbols,
                    crash_hedge_weight=crash_weight_value,
                    crash_hedge_trigger_lookback=crash_trigger_lookback_value,
                    crash_hedge_trigger_drawdown=crash_trigger_drawdown_value,
                    crash_hedge_selection_lookback=crash_selection_lookback_value,
                    crash_hedge_hold_days=crash_hold_days_value,
                    trade_start=score_start,
                    trade_end=train_end,
                    regime_cash_enable=regime_cash_enable,
                    regime_cash_corr_symbol=regime_cash_corr_symbol,
                    regime_cash_corr_window=regime_cash_corr_window,
                    regime_cash_corr_threshold=regime_cash_corr_threshold,
                    regime_cash_override_symbol=regime_cash_override_symbol,
                )
            except ValueError:
                continue
            candidates.append((params, score))
        if candidates:
            selected, _score = max(
                candidates,
                key=lambda item: _selection_score(item[1], selection_metric),
            )
            try:
                train = run_factor_rotation_backtest(
                    train_bars,
                    benchmark_bars=train_benchmark,
                    fundamentals_by_symbol=fundamentals_by_symbol,
                    universe_members=universe_members,
                    delisting_returns=delisting_returns,
                    momentum_lookback=selected.momentum_lookback,
                    reversal_lookback=selected.reversal_lookback,
                    volatility_lookback=selected.volatility_lookback,
                    risk_filter_lookback=selected.risk_filter_lookback,
                    top_n=selected.top_n,
                    rebalance_days=selected.rebalance_days,
                    fee_bps=fee_bps,
                    defensive_symbol=selected.defensive_symbol,
                    weighting=selected.weighting,
                    max_risk_weight=selected.max_risk_weight,
                    drawdown_guard=selected.drawdown_guard,
                    defensive_only=selected.defensive_only,
                    ensemble_momentum_lookbacks=ensemble_momentum_lookbacks,
                    ensemble_risk_filter_lookbacks=ensemble_risk_filter_lookbacks,
                    risk_filter_vote_threshold=risk_filter_vote_threshold,
                    defensive_symbols=defensive_basket,
                    defensive_selection_lookback=defensive_selection_lookback,
                    volatility_target=volatility_target,
                    max_leverage=max_leverage,
                    crash_hedge_symbols=crash_hedge_symbols,
                    crash_hedge_weight=selected.crash_hedge_weight,
                    crash_hedge_trigger_lookback=selected.crash_hedge_trigger_lookback,
                    crash_hedge_trigger_drawdown=selected.crash_hedge_trigger_drawdown,
                    crash_hedge_selection_lookback=selected.crash_hedge_selection_lookback,
                    crash_hedge_hold_days=selected.crash_hedge_hold_days,
                    trade_start=train_start,
                    trade_end=train_end,
                    regime_cash_enable=regime_cash_enable,
                    regime_cash_corr_symbol=regime_cash_corr_symbol,
                    regime_cash_corr_window=regime_cash_corr_window,
                    regime_cash_corr_threshold=regime_cash_corr_threshold,
                    regime_cash_override_symbol=regime_cash_override_symbol,
                )
                test = run_factor_rotation_backtest(
                    test_bars,
                    benchmark_bars=test_benchmark,
                    fundamentals_by_symbol=fundamentals_by_symbol,
                    universe_members=universe_members,
                    delisting_returns=delisting_returns,
                    momentum_lookback=selected.momentum_lookback,
                    reversal_lookback=selected.reversal_lookback,
                    volatility_lookback=selected.volatility_lookback,
                    risk_filter_lookback=selected.risk_filter_lookback,
                    top_n=selected.top_n,
                    rebalance_days=selected.rebalance_days,
                    fee_bps=fee_bps,
                    defensive_symbol=selected.defensive_symbol,
                    weighting=selected.weighting,
                    max_risk_weight=selected.max_risk_weight,
                    drawdown_guard=selected.drawdown_guard,
                    defensive_only=selected.defensive_only,
                    ensemble_momentum_lookbacks=ensemble_momentum_lookbacks,
                    ensemble_risk_filter_lookbacks=ensemble_risk_filter_lookbacks,
                    risk_filter_vote_threshold=risk_filter_vote_threshold,
                    defensive_symbols=defensive_basket,
                    defensive_selection_lookback=defensive_selection_lookback,
                    volatility_target=volatility_target,
                    max_leverage=max_leverage,
                    crash_hedge_symbols=crash_hedge_symbols,
                    crash_hedge_weight=selected.crash_hedge_weight,
                    crash_hedge_trigger_lookback=selected.crash_hedge_trigger_lookback,
                    crash_hedge_trigger_drawdown=selected.crash_hedge_trigger_drawdown,
                    crash_hedge_selection_lookback=selected.crash_hedge_selection_lookback,
                    crash_hedge_hold_days=selected.crash_hedge_hold_days,
                    trade_start=test_start,
                    trade_end=test_end,
                    regime_cash_enable=regime_cash_enable,
                    regime_cash_corr_symbol=regime_cash_corr_symbol,
                    regime_cash_corr_window=regime_cash_corr_window,
                    regime_cash_corr_threshold=regime_cash_corr_threshold,
                    regime_cash_override_symbol=regime_cash_override_symbol,
                )
                rows.append(
                    WalkForwardRow(
                        train_start=train_start,
                        train_end=train_end,
                        test_start=test_start,
                        test_end=test_end,
                        selected=selected,
                        train=train,
                        test=test,
                    )
                )
            except ValueError:
                pass
        train_start = _add_years(train_start, step_years)
    return WalkForwardReport(
        symbols=tuple(sorted(bars_by_symbol)),
        selection_metric=selection_metric,
        validation_years=validation_years,
        rows=rows,
    )


def format_walk_forward_report(report: WalkForwardReport) -> str:
    lines = [
        "# Factor Walk-Forward Report",
        "",
        "Research-only output. Each row selects parameters on the train window and evaluates them on the following test window.",
        "",
        f"Universe: {', '.join(report.symbols)}",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Windows | {len(report.rows)} |",
        f"| Selection Metric | {report.selection_metric} |",
        f"| Train Validation Years | {report.validation_years} |",
        f"| Positive Test Rate | {report.positive_test_rate * 100:.1f}% |",
        f"| Average Test Annualized Excess | {report.average_test_annualized_excess * 100:+.2f}% |",
        "",
        "## Windows",
        "",
        "| Train | Test | Selected Params | Train Ann. Excess | Test Ann. Excess | Test Sharpe | Test MDD |",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    for row in report.rows:
        lines.append(
            f"| {row.train_start} to {row.train_end} | {row.test_start} to {row.test_end} | "
            f"{row.selected.label} | {row.train.annualized_excess_return * 100:+.2f}% | "
            f"{row.test.annualized_excess_return * 100:+.2f}% | "
            f"{row.test.sharpe:.2f} | {row.test.max_drawdown * 100:.2f}% |"
        )
    return "\n".join(lines)


def _slice_bars_by_symbol(
    bars_by_symbol: dict[str, list[PriceBar]],
    start: date,
    end: date,
) -> dict[str, list[PriceBar]]:
    return {symbol: _slice_bars(bars, start, end) for symbol, bars in bars_by_symbol.items()}


def _slice_bars(bars: list[PriceBar], start: date, end: date) -> list[PriceBar]:
    return [bar for bar in bars if start <= bar.ts <= end]


def _add_years(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(year=value.year + years, day=28)


def _warmup_start(value: date, lookback: int) -> date:
    calendar_days = lookback * 365 // 252 + 45
    return date.fromordinal(value.toordinal() - calendar_days)


def _selection_score(result: FactorPortfolioResult, metric: str) -> float:
    if metric == "annualized-excess":
        return result.annualized_excess_return
    if metric == "annualized-return":
        return result.annualized_return
    if metric == "excess-sharpe":
        return result.sharpe - result.benchmark_sharpe
    if metric == "return-drawdown":
        return result.annualized_excess_return - result.max_drawdown
    if metric == "risk-first":
        excess_sharpe = result.sharpe - result.benchmark_sharpe
        return (
            result.annualized_excess_return + (0.05 * excess_sharpe) - (0.5 * result.max_drawdown)
        )
    raise ValueError(f"unknown selection metric: {metric}")
