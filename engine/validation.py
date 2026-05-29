from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from data.models import DelistingReturn, PriceBar, UniverseMember
from engine.factor_portfolio import (
    FactorPortfolioResult,
    FundamentalsInput,
    run_factor_rotation_backtest,
)
from engine.walkforward import WalkForwardReport, run_factor_walk_forward


@dataclass(frozen=True)
class StressWindow:
    name: str
    start: date
    end: date


@dataclass(frozen=True)
class StressWindowResult:
    window: StressWindow
    result: FactorPortfolioResult | None
    skipped_reason: str = ""


@dataclass(frozen=True)
class FactorValidationThresholds:
    min_walk_forward_windows: int = 8
    min_positive_test_rate: float = 0.60
    min_average_test_excess: float = 0.0
    max_worst_test_drawdown: float = 0.30
    min_parameter_positive_rate: float = 0.60
    min_stress_windows: int = 0
    min_stress_return: float = 0.0
    max_stress_drawdown: float = 0.35


@dataclass(frozen=True)
class FactorValidationSuite:
    symbols: tuple[str, ...]
    start: date
    end: date
    walk_forward: WalkForwardReport
    full_sample: FactorPortfolioResult
    fee_stress: tuple[FactorPortfolioResult, ...]
    parameter_variants: tuple[FactorPortfolioResult, ...]
    stress_windows: tuple[StressWindowResult, ...]
    thresholds: FactorValidationThresholds

    @property
    def worst_test_drawdown(self) -> float:
        if not self.walk_forward.rows:
            return 0.0
        return max(row.test.max_drawdown for row in self.walk_forward.rows)

    @property
    def fee_stress_passed(self) -> bool:
        if not self.fee_stress:
            return False
        return all(
            result.annualized_excess_return > self.thresholds.min_average_test_excess
            and result.max_drawdown <= self.thresholds.max_worst_test_drawdown
            for result in self.fee_stress
        )

    @property
    def parameter_positive_rate(self) -> float:
        if not self.parameter_variants:
            return 0.0
        return sum(result.annualized_excess_return > 0 for result in self.parameter_variants) / len(
            self.parameter_variants
        )

    @property
    def parameter_robustness_passed(self) -> bool:
        return self.parameter_positive_rate >= self.thresholds.min_parameter_positive_rate

    @property
    def tested_stress_windows(self) -> int:
        return sum(item.result is not None for item in self.stress_windows)

    @property
    def worst_stress_return(self) -> float:
        returns = [
            item.result.total_return for item in self.stress_windows if item.result is not None
        ]
        return min(returns) if returns else 0.0

    @property
    def stress_passed(self) -> bool:
        if self.tested_stress_windows < self.thresholds.min_stress_windows:
            return False
        return all(
            item.result is None
            or (
                item.result.total_return >= self.thresholds.min_stress_return
                and item.result.max_drawdown <= self.thresholds.max_stress_drawdown
            )
            for item in self.stress_windows
        )

    @property
    def promotion_passed(self) -> bool:
        return (
            len(self.walk_forward.rows) >= self.thresholds.min_walk_forward_windows
            and self.walk_forward.positive_test_rate >= self.thresholds.min_positive_test_rate
            and self.walk_forward.average_test_annualized_excess
            > self.thresholds.min_average_test_excess
            and self.worst_test_drawdown <= self.thresholds.max_worst_test_drawdown
            and self.fee_stress_passed
            and self.parameter_robustness_passed
            and self.stress_passed
        )


def run_factor_validation_suite(
    bars_by_symbol: dict[str, list[PriceBar]],
    *,
    benchmark_bars: list[PriceBar],
    fundamentals_by_symbol: FundamentalsInput | None = None,
    universe_members: list[UniverseMember] | None = None,
    delisting_returns: list[DelistingReturn] | None = None,
    start: date,
    end: date,
    momentum_lookback: int,
    reversal_lookback: int,
    volatility_lookback: int,
    risk_filter_lookback: int,
    top_n: int,
    rebalance_days: int,
    fee_bps: float,
    defensive_symbol: str | None,
    weighting: str,
    max_risk_weight: float,
    drawdown_guard: float,
    defensive_only: bool,
    train_years: int,
    validation_years: int,
    test_years: int,
    step_years: int,
    momentum_lookbacks: tuple[int, ...],
    reversal_lookbacks: tuple[int, ...],
    volatility_lookbacks: tuple[int, ...],
    top_ns: tuple[int, ...],
    risk_filter_lookbacks: tuple[int, ...],
    weighting_modes: tuple[str, ...],
    rebalance_days_values: tuple[int, ...],
    defensive_symbols: tuple[str | None, ...],
    max_risk_weights: tuple[float, ...],
    drawdown_guards: tuple[float, ...],
    selection_metric: str,
    fee_stress_bps: tuple[float, ...],
    stress_windows: tuple[StressWindow, ...],
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
    thresholds: FactorValidationThresholds | None = None,
) -> FactorValidationSuite:
    thresholds = thresholds or FactorValidationThresholds()
    walk_forward = run_factor_walk_forward(
        bars_by_symbol,
        benchmark_bars=benchmark_bars,
        fundamentals_by_symbol=fundamentals_by_symbol,
        universe_members=universe_members,
        delisting_returns=delisting_returns,
        start=start,
        end=end,
        train_years=train_years,
        validation_years=validation_years,
        test_years=test_years,
        step_years=step_years,
        momentum_lookbacks=momentum_lookbacks,
        reversal_lookbacks=reversal_lookbacks,
        volatility_lookbacks=volatility_lookbacks,
        top_ns=top_ns,
        risk_filter_lookback=risk_filter_lookback,
        risk_filter_lookbacks=risk_filter_lookbacks,
        weighting_modes=weighting_modes,
        selection_metric=selection_metric,
        rebalance_days=rebalance_days,
        rebalance_days_values=rebalance_days_values,
        fee_bps=fee_bps,
        defensive_symbol=defensive_symbol,
        defensive_symbols=defensive_symbols,
        max_risk_weight=max_risk_weight,
        max_risk_weights=max_risk_weights,
        drawdown_guard=drawdown_guard,
        drawdown_guards=drawdown_guards,
        defensive_only=defensive_only,
        ensemble_momentum_lookbacks=ensemble_momentum_lookbacks,
        ensemble_risk_filter_lookbacks=ensemble_risk_filter_lookbacks,
        risk_filter_vote_threshold=risk_filter_vote_threshold,
        defensive_basket=defensive_basket,
        defensive_selection_lookback=defensive_selection_lookback,
        volatility_target=volatility_target,
        max_leverage=max_leverage,
        crash_hedge_symbols=crash_hedge_symbols,
        crash_hedge_weight=crash_hedge_weight,
        crash_hedge_trigger_lookback=crash_hedge_trigger_lookback,
        crash_hedge_trigger_drawdown=crash_hedge_trigger_drawdown,
        crash_hedge_selection_lookback=crash_hedge_selection_lookback,
        crash_hedge_hold_days=crash_hedge_hold_days,
        crash_hedge_weights=crash_hedge_weights,
        crash_hedge_trigger_lookbacks=crash_hedge_trigger_lookbacks,
        crash_hedge_trigger_drawdowns=crash_hedge_trigger_drawdowns,
        crash_hedge_selection_lookbacks=crash_hedge_selection_lookbacks,
        crash_hedge_hold_days_values=crash_hedge_hold_days_values,
        regime_cash_enable=regime_cash_enable,
        regime_cash_corr_symbol=regime_cash_corr_symbol,
        regime_cash_corr_window=regime_cash_corr_window,
        regime_cash_corr_threshold=regime_cash_corr_threshold,
        regime_cash_override_symbol=regime_cash_override_symbol,
    )
    full_sample = _run_base(
        bars_by_symbol,
        benchmark_bars=benchmark_bars,
        fundamentals_by_symbol=fundamentals_by_symbol,
        universe_members=universe_members,
        delisting_returns=delisting_returns,
        start=start,
        end=end,
        momentum_lookback=momentum_lookback,
        reversal_lookback=reversal_lookback,
        volatility_lookback=volatility_lookback,
        risk_filter_lookback=risk_filter_lookback,
        top_n=top_n,
        rebalance_days=rebalance_days,
        fee_bps=fee_bps,
        defensive_symbol=defensive_symbol,
        weighting=weighting,
        max_risk_weight=max_risk_weight,
        drawdown_guard=drawdown_guard,
        defensive_only=defensive_only,
        ensemble_momentum_lookbacks=ensemble_momentum_lookbacks,
        ensemble_risk_filter_lookbacks=ensemble_risk_filter_lookbacks,
        risk_filter_vote_threshold=risk_filter_vote_threshold,
        defensive_basket=defensive_basket,
        defensive_selection_lookback=defensive_selection_lookback,
        volatility_target=volatility_target,
        max_leverage=max_leverage,
        crash_hedge_symbols=crash_hedge_symbols,
        crash_hedge_weight=crash_hedge_weight,
        crash_hedge_trigger_lookback=crash_hedge_trigger_lookback,
        crash_hedge_trigger_drawdown=crash_hedge_trigger_drawdown,
        crash_hedge_selection_lookback=crash_hedge_selection_lookback,
        crash_hedge_hold_days=crash_hedge_hold_days,
        regime_cash_enable=regime_cash_enable,
        regime_cash_corr_symbol=regime_cash_corr_symbol,
        regime_cash_corr_window=regime_cash_corr_window,
        regime_cash_corr_threshold=regime_cash_corr_threshold,
        regime_cash_override_symbol=regime_cash_override_symbol,
    )
    fee_results = tuple(
        _run_base(
            bars_by_symbol,
            benchmark_bars=benchmark_bars,
            fundamentals_by_symbol=fundamentals_by_symbol,
            universe_members=universe_members,
            delisting_returns=delisting_returns,
            start=start,
            end=end,
            momentum_lookback=momentum_lookback,
            reversal_lookback=reversal_lookback,
            volatility_lookback=volatility_lookback,
            risk_filter_lookback=risk_filter_lookback,
            top_n=top_n,
            rebalance_days=rebalance_days,
            fee_bps=fee,
            defensive_symbol=defensive_symbol,
            weighting=weighting,
            max_risk_weight=max_risk_weight,
            drawdown_guard=drawdown_guard,
            defensive_only=defensive_only,
            ensemble_momentum_lookbacks=ensemble_momentum_lookbacks,
            ensemble_risk_filter_lookbacks=ensemble_risk_filter_lookbacks,
            risk_filter_vote_threshold=risk_filter_vote_threshold,
            defensive_basket=defensive_basket,
            defensive_selection_lookback=defensive_selection_lookback,
            volatility_target=volatility_target,
            max_leverage=max_leverage,
            crash_hedge_symbols=crash_hedge_symbols,
            crash_hedge_weight=crash_hedge_weight,
            crash_hedge_trigger_lookback=crash_hedge_trigger_lookback,
            crash_hedge_trigger_drawdown=crash_hedge_trigger_drawdown,
            crash_hedge_selection_lookback=crash_hedge_selection_lookback,
            crash_hedge_hold_days=crash_hedge_hold_days,
            regime_cash_enable=regime_cash_enable,
            regime_cash_corr_symbol=regime_cash_corr_symbol,
            regime_cash_corr_window=regime_cash_corr_window,
            regime_cash_corr_threshold=regime_cash_corr_threshold,
            regime_cash_override_symbol=regime_cash_override_symbol,
        )
        for fee in fee_stress_bps
    )
    parameter_results = tuple(
        _run_base(
            bars_by_symbol,
            benchmark_bars=benchmark_bars,
            fundamentals_by_symbol=fundamentals_by_symbol,
            universe_members=universe_members,
            delisting_returns=delisting_returns,
            start=start,
            end=end,
            momentum_lookback=variant_momentum,
            reversal_lookback=reversal_lookback,
            volatility_lookback=volatility_lookback,
            risk_filter_lookback=variant_risk_filter,
            top_n=top_n,
            rebalance_days=rebalance_days,
            fee_bps=fee_bps,
            defensive_symbol=defensive_symbol,
            weighting=weighting,
            max_risk_weight=max_risk_weight,
            drawdown_guard=drawdown_guard,
            defensive_only=defensive_only,
            ensemble_momentum_lookbacks=ensemble_momentum_lookbacks,
            ensemble_risk_filter_lookbacks=ensemble_risk_filter_lookbacks,
            risk_filter_vote_threshold=risk_filter_vote_threshold,
            defensive_basket=defensive_basket,
            defensive_selection_lookback=defensive_selection_lookback,
            volatility_target=volatility_target,
            max_leverage=max_leverage,
            crash_hedge_symbols=crash_hedge_symbols,
            crash_hedge_weight=crash_hedge_weight,
            crash_hedge_trigger_lookback=crash_hedge_trigger_lookback,
            crash_hedge_trigger_drawdown=crash_hedge_trigger_drawdown,
            crash_hedge_selection_lookback=crash_hedge_selection_lookback,
            crash_hedge_hold_days=crash_hedge_hold_days,
            regime_cash_enable=regime_cash_enable,
            regime_cash_corr_symbol=regime_cash_corr_symbol,
            regime_cash_corr_window=regime_cash_corr_window,
            regime_cash_corr_threshold=regime_cash_corr_threshold,
            regime_cash_override_symbol=regime_cash_override_symbol,
        )
        for variant_momentum, variant_risk_filter in _parameter_variants(
            momentum_lookback,
            risk_filter_lookback,
        )
    )
    stress_results = tuple(
        _run_stress_window(
            window,
            bars_by_symbol,
            benchmark_bars=benchmark_bars,
            fundamentals_by_symbol=fundamentals_by_symbol,
            universe_members=universe_members,
            delisting_returns=delisting_returns,
            momentum_lookback=momentum_lookback,
            reversal_lookback=reversal_lookback,
            volatility_lookback=volatility_lookback,
            risk_filter_lookback=risk_filter_lookback,
            top_n=top_n,
            rebalance_days=rebalance_days,
            fee_bps=fee_bps,
            defensive_symbol=defensive_symbol,
            weighting=weighting,
            max_risk_weight=max_risk_weight,
            drawdown_guard=drawdown_guard,
            defensive_only=defensive_only,
            ensemble_momentum_lookbacks=ensemble_momentum_lookbacks,
            ensemble_risk_filter_lookbacks=ensemble_risk_filter_lookbacks,
            risk_filter_vote_threshold=risk_filter_vote_threshold,
            defensive_basket=defensive_basket,
            defensive_selection_lookback=defensive_selection_lookback,
            volatility_target=volatility_target,
            max_leverage=max_leverage,
            crash_hedge_symbols=crash_hedge_symbols,
            crash_hedge_weight=crash_hedge_weight,
            crash_hedge_trigger_lookback=crash_hedge_trigger_lookback,
            crash_hedge_trigger_drawdown=crash_hedge_trigger_drawdown,
            crash_hedge_selection_lookback=crash_hedge_selection_lookback,
            crash_hedge_hold_days=crash_hedge_hold_days,
            regime_cash_enable=regime_cash_enable,
            regime_cash_corr_symbol=regime_cash_corr_symbol,
            regime_cash_corr_window=regime_cash_corr_window,
            regime_cash_corr_threshold=regime_cash_corr_threshold,
            regime_cash_override_symbol=regime_cash_override_symbol,
        )
        for window in stress_windows
    )
    return FactorValidationSuite(
        symbols=tuple(sorted(bars_by_symbol)),
        start=start,
        end=end,
        walk_forward=walk_forward,
        full_sample=full_sample,
        fee_stress=fee_results,
        parameter_variants=parameter_results,
        stress_windows=stress_results,
        thresholds=thresholds,
    )


def format_factor_validation_suite(suite: FactorValidationSuite) -> str:
    lines = [
        "# Factor Validation Suite",
        "",
        "Research-only output. This validates whether a strategy candidate is strong enough to be promoted toward paper/shadow live drills.",
        "",
        f"Universe: {', '.join(suite.symbols)}",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Verdict | {'PASS' if suite.promotion_passed else 'BLOCK'} |",
        f"| Window | {suite.start} to {suite.end} |",
        f"| Walk-forward Windows | {len(suite.walk_forward.rows)} |",
        f"| Positive Test Rate | {suite.walk_forward.positive_test_rate * 100:.1f}% |",
        f"| Avg Test Annualized Excess | {suite.walk_forward.average_test_annualized_excess * 100:+.2f}% |",
        f"| Worst Test MDD | {suite.worst_test_drawdown * 100:.2f}% |",
        f"| Full-sample Ann. Excess | {suite.full_sample.annualized_excess_return * 100:+.2f}% |",
        f"| Full-sample MDD | {suite.full_sample.max_drawdown * 100:.2f}% |",
        f"| Fee Stress | {'PASS' if suite.fee_stress_passed else 'BLOCK'} |",
        f"| Parameter Positive Rate | {suite.parameter_positive_rate * 100:.1f}% |",
        f"| Stress Windows Tested | {suite.tested_stress_windows} |",
        f"| Worst Stress Return | {suite.worst_stress_return * 100:+.2f}% |",
        f"| Min Stress Return Required | {suite.thresholds.min_stress_return * 100:+.2f}% |",
        f"| Stress Windows | {'PASS' if suite.stress_passed else 'BLOCK'} |",
        "",
        "## Walk-forward Windows",
        "",
        "| Train | Test | Selected Params | Test Ann. Excess | Test Sharpe | Test MDD |",
        "|---|---|---|---:|---:|---:|",
    ]
    for row in suite.walk_forward.rows:
        lines.append(
            f"| {row.train_start} to {row.train_end} | {row.test_start} to {row.test_end} | "
            f"{row.selected.label} | {row.test.annualized_excess_return * 100:+.2f}% | "
            f"{row.test.sharpe:.2f} | {row.test.max_drawdown * 100:.2f}% |"
        )
    lines.extend(
        [
            "",
            "## Fee Stress",
            "",
            "| Fee bps | Ann. Excess | MDD | Final Equity |",
            "|---:|---:|---:|---:|",
        ]
    )
    for result in suite.fee_stress:
        lines.append(
            f"| {result.fee_bps:.2f} | {result.annualized_excess_return * 100:+.2f}% | "
            f"{result.max_drawdown * 100:.2f}% | {result.final_equity:,.2f} |"
        )
    lines.extend(
        [
            "",
            "## Parameter Perturbation",
            "",
            "| Momentum | Risk Filter | Ann. Excess | MDD |",
            "|---:|---:|---:|---:|",
        ]
    )
    for result in suite.parameter_variants:
        lines.append(
            f"| {result.momentum_lookback} | {result.risk_filter_lookback} | "
            f"{result.annualized_excess_return * 100:+.2f}% | {result.max_drawdown * 100:.2f}% |"
        )
    lines.extend(
        [
            "",
            "## Stress Windows",
            "",
            "| Window | Range | Total Return | Ann. Excess | MDD | Status |",
            "|---|---|---:|---:|---:|---|",
        ]
    )
    for item in suite.stress_windows:
        if item.result is None:
            lines.append(
                f"| {item.window.name} | {item.window.start} to {item.window.end} | n/a | n/a | n/a | "
                f"skipped: {item.skipped_reason} |"
            )
        else:
            lines.append(
                f"| {item.window.name} | {item.window.start} to {item.window.end} | "
                f"{item.result.total_return * 100:+.2f}% | "
                f"{item.result.annualized_excess_return * 100:+.2f}% | "
                f"{item.result.max_drawdown * 100:.2f}% | tested |"
            )
    lines.extend(["", "## Blocking Reasons", ""])
    reasons = _blocking_reasons(suite)
    lines.extend(f"- {reason}" for reason in reasons or ["None"])
    return "\n".join(lines)


def parse_stress_windows(value: str) -> tuple[StressWindow, ...]:
    windows: list[StressWindow] = []
    for raw in value.split(","):
        item = raw.strip()
        if not item:
            continue
        parts = item.split(":")
        if len(parts) != 3:
            raise ValueError("stress windows must use name:YYYY-MM-DD:YYYY-MM-DD")
        windows.append(
            StressWindow(parts[0], date.fromisoformat(parts[1]), date.fromisoformat(parts[2]))
        )
    return tuple(windows)


def _run_base(
    bars_by_symbol: dict[str, list[PriceBar]],
    *,
    benchmark_bars: list[PriceBar],
    fundamentals_by_symbol: FundamentalsInput | None,
    universe_members: list[UniverseMember] | None,
    delisting_returns: list[DelistingReturn] | None,
    start: date,
    end: date,
    momentum_lookback: int,
    reversal_lookback: int,
    volatility_lookback: int,
    risk_filter_lookback: int,
    top_n: int,
    rebalance_days: int,
    fee_bps: float,
    defensive_symbol: str | None,
    weighting: str,
    max_risk_weight: float,
    drawdown_guard: float,
    defensive_only: bool,
    ensemble_momentum_lookbacks: tuple[int, ...] | None,
    ensemble_risk_filter_lookbacks: tuple[int, ...] | None,
    risk_filter_vote_threshold: float,
    defensive_basket: tuple[str | None, ...] | None,
    defensive_selection_lookback: int,
    volatility_target: float,
    max_leverage: float,
    crash_hedge_symbols: tuple[str, ...] | None,
    crash_hedge_weight: float,
    crash_hedge_trigger_lookback: int,
    crash_hedge_trigger_drawdown: float,
    crash_hedge_selection_lookback: int,
    crash_hedge_hold_days: int,
    regime_cash_enable: bool = False,
    regime_cash_corr_symbol: str = "TLT",
    regime_cash_corr_window: int = 60,
    regime_cash_corr_threshold: float = 0.2,
    regime_cash_override_symbol: str | None = "SHY",
) -> FactorPortfolioResult:
    return run_factor_rotation_backtest(
        bars_by_symbol,
        benchmark_bars=benchmark_bars,
        fundamentals_by_symbol=fundamentals_by_symbol,
        universe_members=universe_members,
        delisting_returns=delisting_returns,
        momentum_lookback=momentum_lookback,
        reversal_lookback=reversal_lookback,
        volatility_lookback=volatility_lookback,
        risk_filter_lookback=risk_filter_lookback,
        top_n=top_n,
        rebalance_days=rebalance_days,
        fee_bps=fee_bps,
        defensive_symbol=defensive_symbol,
        weighting=weighting,
        max_risk_weight=max_risk_weight,
        drawdown_guard=drawdown_guard,
        defensive_only=defensive_only,
        ensemble_momentum_lookbacks=ensemble_momentum_lookbacks,
        ensemble_risk_filter_lookbacks=ensemble_risk_filter_lookbacks,
        risk_filter_vote_threshold=risk_filter_vote_threshold,
        defensive_symbols=defensive_basket,
        defensive_selection_lookback=defensive_selection_lookback,
        volatility_target=volatility_target,
        max_leverage=max_leverage,
        crash_hedge_symbols=crash_hedge_symbols,
        crash_hedge_weight=crash_hedge_weight,
        crash_hedge_trigger_lookback=crash_hedge_trigger_lookback,
        crash_hedge_trigger_drawdown=crash_hedge_trigger_drawdown,
        crash_hedge_selection_lookback=crash_hedge_selection_lookback,
        crash_hedge_hold_days=crash_hedge_hold_days,
        trade_start=start,
        trade_end=end,
        regime_cash_enable=regime_cash_enable,
        regime_cash_corr_symbol=regime_cash_corr_symbol,
        regime_cash_corr_window=regime_cash_corr_window,
        regime_cash_corr_threshold=regime_cash_corr_threshold,
        regime_cash_override_symbol=regime_cash_override_symbol,
    )


def _run_stress_window(
    window: StressWindow,
    bars_by_symbol: dict[str, list[PriceBar]],
    **kwargs,
) -> StressWindowResult:
    try:
        result = _run_base(
            bars_by_symbol,
            start=window.start,
            end=window.end,
            **kwargs,
        )
    except ValueError as exc:
        return StressWindowResult(window, None, str(exc))
    return StressWindowResult(window, result)


def _parameter_variants(
    momentum_lookback: int, risk_filter_lookback: int
) -> tuple[tuple[int, int], ...]:
    momentums = _bounded_lookbacks(momentum_lookback)
    risk_filters = _bounded_nonnegative_lookbacks(risk_filter_lookback)
    return tuple((momentum, risk_filter) for momentum in momentums for risk_filter in risk_filters)


def _bounded_lookbacks(value: int) -> tuple[int, ...]:
    return tuple(sorted({max(1, int(value * 0.75)), value, max(1, int(value * 1.25))}))


def _bounded_nonnegative_lookbacks(value: int) -> tuple[int, ...]:
    if value == 0:
        return (0,)
    return tuple(sorted({0, max(1, int(value * 0.75)), value, max(1, int(value * 1.25))}))


def _blocking_reasons(suite: FactorValidationSuite) -> list[str]:
    reasons: list[str] = []
    if len(suite.walk_forward.rows) < suite.thresholds.min_walk_forward_windows:
        reasons.append(
            f"walk-forward windows {len(suite.walk_forward.rows)} < {suite.thresholds.min_walk_forward_windows}"
        )
    if suite.walk_forward.positive_test_rate < suite.thresholds.min_positive_test_rate:
        reasons.append(
            f"positive test rate {suite.walk_forward.positive_test_rate:.1%} < "
            f"{suite.thresholds.min_positive_test_rate:.1%}"
        )
    if (
        suite.walk_forward.average_test_annualized_excess
        <= suite.thresholds.min_average_test_excess
    ):
        reasons.append("average test annualized excess is not positive enough")
    if suite.worst_test_drawdown > suite.thresholds.max_worst_test_drawdown:
        reasons.append("worst test drawdown exceeds threshold")
    if not suite.fee_stress_passed:
        reasons.append("fee stress failed")
    if not suite.parameter_robustness_passed:
        reasons.append("parameter perturbation failed")
    if not suite.stress_passed:
        if suite.worst_stress_return < suite.thresholds.min_stress_return:
            reasons.append(
                f"worst stress return {suite.worst_stress_return:.1%} < "
                f"{suite.thresholds.min_stress_return:.1%}"
            )
        reasons.append("stress-window requirement failed")
    return reasons
