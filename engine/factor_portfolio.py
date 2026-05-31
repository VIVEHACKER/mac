from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from math import sqrt
from statistics import mean, pstdev

from data.models import DelistingReturn, FundamentalRecord, PriceBar, UniverseMember
from engine.portfolio import AnnualReturn

TRADING_DAYS = 252
FundamentalsInput = Mapping[str, FundamentalRecord | Sequence[FundamentalRecord]]


@dataclass(frozen=True)
class FactorWeights:
    momentum: float = 1.0
    reversal: float = 0.5
    low_volatility: float = 0.75
    value: float = 0.5
    quality: float = 0.5


DEFAULT_FACTOR_WEIGHTS = FactorWeights()


@dataclass(frozen=True)
class FactorScorePoint:
    symbol: str
    composite: float
    momentum: float
    reversal: float
    low_volatility: float
    value: float
    quality: float


@dataclass(frozen=True)
class FactorPortfolioPoint:
    ts: date
    equity: float
    benchmark_equity: float
    holdings: tuple[str, ...]
    weights: tuple[tuple[str, float], ...]
    portfolio_return: float
    benchmark_return: float
    cost: float
    risk_on: bool


@dataclass(frozen=True)
class FactorPortfolioResult:
    symbols: tuple[str, ...]
    market: str
    universe_mode: str
    universe_name: str
    start: date
    end: date
    rows: int
    momentum_lookback: int
    ensemble_momentum_lookbacks: tuple[int, ...]
    reversal_lookback: int
    volatility_lookback: int
    risk_filter_lookback: int
    ensemble_risk_filter_lookbacks: tuple[int, ...]
    risk_filter_vote_threshold: float
    top_n: int
    weighting: str
    max_risk_weight: float
    drawdown_guard: float
    defensive_only: bool
    defensive_symbols: tuple[str, ...]
    defensive_selection_lookback: int
    volatility_target: float
    max_leverage: float
    crash_hedge_symbols: tuple[str, ...]
    crash_hedge_weight: float
    crash_hedge_trigger_lookback: int
    crash_hedge_trigger_drawdown: float
    crash_hedge_selection_lookback: int
    crash_hedge_hold_days: int
    volume_lookback_short: int
    volume_lookback_long: int
    volume_weight: float
    initial_cash: float
    final_equity: float
    benchmark_final_equity: float
    benchmark_symbol: str
    benchmark_market: str
    total_return: float
    benchmark_return: float
    annualized_return: float
    benchmark_annualized_return: float
    annualized_excess_return: float
    sharpe: float
    benchmark_sharpe: float
    max_drawdown: float
    benchmark_max_drawdown: float
    rebalance_count: int
    average_holdings: float
    average_eligible_symbols: float
    average_gross_weight: float
    risk_on_ratio: float
    crash_hedge_active_ratio: float
    fundamental_record_count: int
    delisting_returns_applied: int
    ended_members_without_delisting: int
    fee_bps: float
    total_cost: float
    equity_curve: list[FactorPortfolioPoint]
    annual_returns: list[AnnualReturn]


def run_factor_rotation_backtest(
    bars_by_symbol: dict[str, list[PriceBar]],
    *,
    fundamentals_by_symbol: FundamentalsInput | None = None,
    universe_members: list[UniverseMember] | None = None,
    delisting_returns: list[DelistingReturn] | None = None,
    benchmark_bars: list[PriceBar] | None = None,
    momentum_lookback: int = 252,
    ensemble_momentum_lookbacks: tuple[int, ...] | None = None,
    reversal_lookback: int = 21,
    volatility_lookback: int = 63,
    risk_filter_lookback: int = 200,
    ensemble_risk_filter_lookbacks: tuple[int, ...] | None = None,
    risk_filter_vote_threshold: float = 0.5,
    top_n: int = 3,
    initial_cash: float = 10_000.0,
    rebalance_days: int = 21,
    fee_bps: float = 2.0,
    factor_weights: FactorWeights = DEFAULT_FACTOR_WEIGHTS,
    defensive_symbol: str | None = "TLT",
    weighting: str = "inverse-vol",
    max_risk_weight: float = 1.0,
    drawdown_guard: float = 0.0,
    defensive_only: bool = False,
    defensive_symbols: tuple[str | None, ...] | None = None,
    defensive_selection_lookback: int = 63,
    volatility_target: float = 0.0,
    max_leverage: float = 1.0,
    crash_hedge_symbols: tuple[str, ...] | None = None,
    crash_hedge_weight: float = 0.0,
    crash_hedge_trigger_lookback: int = 21,
    crash_hedge_trigger_drawdown: float = 0.10,
    crash_hedge_selection_lookback: int = 5,
    crash_hedge_hold_days: int = 0,
    # @AX:NOTE volume_lookback_short=21: ~1-month average (recent activity window)
    # @AX:NOTE volume_lookback_long=252: ~1-year average (baseline activity window)
    volume_lookback_short: int = 21,
    volume_lookback_long: int = 252,
    volume_weight: float = 0.0,
    trade_start: date | None = None,
    trade_end: date | None = None,
    # Regime-cash: when SPY–TLT correlation turns positive AND SPY < MA, force SHY/cash defensive
    regime_cash_enable: bool = False,
    regime_cash_corr_symbol: str = "TLT",
    regime_cash_corr_window: int = 60,
    regime_cash_corr_threshold: float = 0.2,
    regime_cash_override_symbol: str | None = "SHY",
) -> FactorPortfolioResult:
    if min(momentum_lookback, reversal_lookback, volatility_lookback) < 1:
        raise ValueError("momentum, reversal and volatility lookbacks must be >= 1")
    if risk_filter_lookback < 0:
        raise ValueError("risk_filter_lookback must be >= 0")
    if top_n < 1:
        raise ValueError("top_n must be >= 1")
    if rebalance_days < 1:
        raise ValueError("rebalance_days must be >= 1")
    if weighting not in {"inverse-vol", "equal"}:
        raise ValueError("weighting must be 'inverse-vol' or 'equal'")
    if not 0 < max_risk_weight <= 1:
        raise ValueError("max_risk_weight must be > 0 and <= 1")
    if not 0 <= drawdown_guard < 1:
        raise ValueError("drawdown_guard must be >= 0 and < 1")
    momentum_variants = _positive_lookbacks(
        ensemble_momentum_lookbacks,
        default=momentum_lookback,
        label="ensemble_momentum_lookbacks",
    )
    risk_filter_variants = _nonnegative_lookbacks(
        ensemble_risk_filter_lookbacks,
        default=risk_filter_lookback,
        label="ensemble_risk_filter_lookbacks",
    )
    if not 0 < risk_filter_vote_threshold <= 1:
        raise ValueError("risk_filter_vote_threshold must be > 0 and <= 1")
    if defensive_selection_lookback < 1:
        raise ValueError("defensive_selection_lookback must be >= 1")
    if volatility_target < 0:
        raise ValueError("volatility_target must be >= 0")
    if max_leverage <= 0:
        raise ValueError("max_leverage must be > 0")
    if not 0 <= crash_hedge_weight <= 1:
        raise ValueError("crash_hedge_weight must be >= 0 and <= 1")
    if crash_hedge_trigger_lookback < 1:
        raise ValueError("crash_hedge_trigger_lookback must be >= 1")
    if not 0 < crash_hedge_trigger_drawdown < 1:
        raise ValueError("crash_hedge_trigger_drawdown must be > 0 and < 1")
    if crash_hedge_selection_lookback < 1:
        raise ValueError("crash_hedge_selection_lookback must be >= 1")
    if crash_hedge_hold_days < 0:
        raise ValueError("crash_hedge_hold_days must be >= 0")
    if volume_lookback_short < 1:
        raise ValueError("volume_lookback_short must be >= 1")
    if volume_lookback_long < 1:
        raise ValueError("volume_lookback_long must be >= 1")
    if volume_lookback_short > volume_lookback_long:
        raise ValueError(
            "volume_lookback_short must be <= volume_lookback_long "
            f"(got short={volume_lookback_short}, long={volume_lookback_long}); "
            "a larger short window than the long window would index negatively "
            "and leak future volume data (look-ahead bias)"
        )
    if not 0.0 <= volume_weight <= 1.0:
        raise ValueError("volume_weight must be >= 0 and <= 1")
    if regime_cash_corr_window < 2:
        raise ValueError("regime_cash_corr_window must be >= 2")
    crash_hedges = _symbol_tuple(crash_hedge_symbols)
    defensive_choices = _defensive_symbol_tuple(defensive_symbol, defensive_symbols)
    fundamentals = _normalize_fundamentals(fundamentals_by_symbol)
    close_by_symbol = {
        symbol.upper(): {bar.ts: bar.close for bar in sorted(bars, key=lambda item: item.ts)}
        for symbol, bars in bars_by_symbol.items()
        if bars
    }
    volume_by_symbol: dict[str, dict[date, float]] = {
        symbol.upper(): {bar.ts: bar.volume for bar in sorted(bars, key=lambda item: item.ts)}
        for symbol, bars in bars_by_symbol.items()
        if bars
    }
    if not close_by_symbol:
        raise ValueError("no bars supplied")
    first_bar = next(bar for bars in bars_by_symbol.values() for bar in bars)
    benchmark_closes = _closes_by_date(benchmark_bars)
    membership_by_symbol = _membership_by_symbol(universe_members)
    delistings_by_symbol = _delistings_by_symbol(delisting_returns)
    common_dates = _common_dates(close_by_symbol, benchmark_closes, bool(membership_by_symbol))
    if trade_end is not None:
        common_dates = [item for item in common_dates if item <= trade_end]
    warmup = max(
        *momentum_variants,
        reversal_lookback,
        volatility_lookback,
        *risk_filter_variants,
        defensive_selection_lookback if len(defensive_choices) > 1 else 0,
        crash_hedge_trigger_lookback if crash_hedges else 0,
        crash_hedge_selection_lookback if crash_hedges else 0,
        volume_lookback_long if volume_weight > 0 else 0,
    )
    start_index = max(warmup, _first_index_on_or_after(common_dates, trade_start))
    if len(common_dates) <= start_index + 1:
        raise ValueError(f"not enough bars for warmup={warmup}; got {len(common_dates)}")

    benchmark_symbol = "Equal-weight universe"
    benchmark_market = first_bar.market
    if benchmark_bars:
        first_benchmark = sorted(benchmark_bars, key=lambda bar: bar.ts)[0]
        benchmark_symbol = first_benchmark.symbol
        benchmark_market = first_benchmark.market

    equity = initial_cash
    benchmark_equity = initial_cash
    base_weights: dict[str, float] = {}
    current_weights: dict[str, float] = {}
    curve: list[FactorPortfolioPoint] = [
        FactorPortfolioPoint(
            common_dates[start_index],
            equity,
            benchmark_equity,
            (),
            (),
            0.0,
            0.0,
            0.0,
            True,
        )
    ]
    returns: list[float] = []
    benchmark_returns: list[float] = []
    holding_counts: list[int] = []
    eligible_counts: list[int] = []
    gross_weights: list[float] = []
    risk_on_flags: list[bool] = []
    crash_hedge_flags: list[bool] = []
    rebalance_count = 0
    total_cost = 0.0
    delisting_returns_applied = 0
    high_water = initial_cash
    crash_hedge_days_remaining = 0

    for index in range(start_index, len(common_dates) - 1):
        today = common_dates[index]
        tomorrow = common_dates[index + 1]
        cost = 0.0
        market_risk_on = _risk_on_ensemble(
            benchmark_closes,
            today,
            risk_filter_variants,
            threshold=risk_filter_vote_threshold,
        )
        drawdown_guard_active = drawdown_guard > 0 and equity / high_water - 1.0 <= -drawdown_guard
        risk_on = market_risk_on and not drawdown_guard_active
        active_defensive_symbol = _select_defensive_symbol(
            close_by_symbol,
            defensive_choices,
            today=today,
            lookback=defensive_selection_lookback,
        )
        if regime_cash_enable and _regime_cash_active(
            benchmark_closes,
            close_by_symbol.get(regime_cash_corr_symbol.upper(), {}),
            today=today,
            corr_window=regime_cash_corr_window,
            corr_threshold=regime_cash_corr_threshold,
        ):
            # Bond-equity correlation turned positive AND benchmark below MA:
            # TLT is no longer a hedge; force override to SHY/cash.
            override = regime_cash_override_symbol
            override_upper = override.strip().upper() if override else ""
            if (
                not override_upper
                or override_upper in {"CASH", "NONE"}
                or (override_upper in close_by_symbol and today in close_by_symbol[override_upper])
            ):
                active_defensive_symbol = (
                    None
                    if (not override_upper or override_upper in {"CASH", "NONE"})
                    else override_upper
                )
        scheduled_rebalance = (index - start_index) % rebalance_days == 0
        target_base_weights: dict[str, float] | None = None
        if scheduled_rebalance:
            eligible = _eligible_symbols(membership_by_symbol, today) or tuple(
                sorted(close_by_symbol)
            )
            eligible_counts.append(len(eligible))
            risk_eligible = _risk_eligible_symbols(
                eligible,
                defensive_symbols=defensive_choices,
                defensive_only=defensive_only,
                excluded_symbols=crash_hedges,
            )
            target_base_weights = _target_weights_ensemble(
                close_by_symbol,
                fundamentals,
                risk_eligible,
                today=today,
                top_n=top_n,
                momentum_lookbacks=momentum_variants,
                reversal_lookback=reversal_lookback,
                volatility_lookback=volatility_lookback,
                factor_weights=factor_weights,
                weighting=weighting,
                volume_by_symbol=volume_by_symbol,
                volume_lookback_short=volume_lookback_short,
                volume_lookback_long=volume_lookback_long,
                volume_weight=volume_weight,
            )
            target_base_weights = _cap_risk_weights(
                target_base_weights,
                close_by_symbol,
                defensive_symbol=active_defensive_symbol,
                max_risk_weight=max_risk_weight,
                today=today,
            )
        if not risk_on:
            target_base_weights = _defensive_weights(
                close_by_symbol,
                defensive_symbol=active_defensive_symbol,
                today=today,
            )
        if target_base_weights is not None:
            target_base_weights = _apply_volatility_target(
                close_by_symbol,
                target_base_weights,
                today=today,
                volatility_target=volatility_target,
                volatility_lookback=volatility_lookback,
                max_leverage=max_leverage,
            )
            base_weights = target_base_weights
        crash_hedge_triggered = _crash_hedge_triggered(
            benchmark_closes,
            today,
            lookback=crash_hedge_trigger_lookback,
            trigger_drawdown=crash_hedge_trigger_drawdown,
            enabled=bool(crash_hedges) and crash_hedge_weight > 0,
        )
        if crash_hedge_hold_days > 0:
            if crash_hedge_triggered:
                crash_hedge_days_remaining = crash_hedge_hold_days
            crash_hedge_active = crash_hedge_days_remaining > 0
        else:
            crash_hedge_active = crash_hedge_triggered
        target_weights = target_base_weights
        crash_reweight_required = (
            target_base_weights is not None
            or crash_hedge_active
            or any(symbol in current_weights for symbol in crash_hedges)
        )
        if crash_hedges and crash_hedge_weight > 0 and crash_reweight_required:
            target_weights = _apply_crash_hedge(
                close_by_symbol,
                base_weights,
                hedge_symbols=crash_hedges,
                today=today,
                active=crash_hedge_active,
                hedge_weight=crash_hedge_weight,
                selection_lookback=crash_hedge_selection_lookback,
            )
        if crash_hedge_days_remaining > 0:
            crash_hedge_days_remaining -= 1
        if target_weights is not None:
            cost = _turnover(current_weights, target_weights) * fee_bps / 10_000
            if target_weights != current_weights:
                rebalance_count += 1
                current_weights = target_weights

        gross_return, exited_symbols = _weighted_return(
            close_by_symbol,
            current_weights,
            delistings_by_symbol,
            today,
            tomorrow,
        )
        portfolio_return = gross_return - cost
        benchmark_return = (
            _benchmark_return(benchmark_closes, today, tomorrow)
            if benchmark_closes
            else _equal_weight_return(close_by_symbol, today, tomorrow)
        )
        equity *= 1.0 + portfolio_return
        benchmark_equity *= 1.0 + benchmark_return
        high_water = max(high_water, equity)
        returns.append(portfolio_return)
        benchmark_returns.append(benchmark_return)
        holding_counts.append(len(current_weights))
        gross_weights.append(sum(abs(weight) for weight in current_weights.values()))
        risk_on_flags.append(risk_on)
        crash_hedge_flags.append(crash_hedge_active)
        total_cost += cost
        if exited_symbols:
            delisting_returns_applied += len(exited_symbols)
            current_weights = {
                symbol: weight
                for symbol, weight in current_weights.items()
                if symbol not in exited_symbols
            }
            base_weights = {
                symbol: weight
                for symbol, weight in base_weights.items()
                if symbol not in exited_symbols
            }
        curve.append(
            FactorPortfolioPoint(
                ts=tomorrow,
                equity=equity,
                benchmark_equity=benchmark_equity,
                holdings=tuple(current_weights),
                weights=tuple(sorted(current_weights.items())),
                portfolio_return=portfolio_return,
                benchmark_return=benchmark_return,
                cost=cost,
                risk_on=risk_on,
            )
        )

    total_return = (equity / initial_cash) - 1.0
    benchmark_return = (benchmark_equity / initial_cash) - 1.0
    years = max((common_dates[-1] - common_dates[start_index]).days / 365.25, 1 / TRADING_DAYS)
    annualized_return = (equity / initial_cash) ** (1 / years) - 1.0
    benchmark_annualized_return = (benchmark_equity / initial_cash) ** (1 / years) - 1.0
    return FactorPortfolioResult(
        symbols=tuple(sorted(close_by_symbol)),
        market=first_bar.market,
        universe_mode="point-in-time" if membership_by_symbol else "static",
        universe_name=_universe_name(universe_members)
        if membership_by_symbol
        else "static-symbol-list",
        start=common_dates[start_index],
        end=common_dates[-1],
        rows=len(common_dates) - start_index,
        momentum_lookback=momentum_lookback,
        ensemble_momentum_lookbacks=momentum_variants,
        reversal_lookback=reversal_lookback,
        volatility_lookback=volatility_lookback,
        risk_filter_lookback=risk_filter_lookback,
        ensemble_risk_filter_lookbacks=risk_filter_variants,
        risk_filter_vote_threshold=risk_filter_vote_threshold,
        top_n=top_n,
        weighting=weighting,
        max_risk_weight=max_risk_weight,
        drawdown_guard=drawdown_guard,
        defensive_only=defensive_only,
        defensive_symbols=_defensive_labels(defensive_choices),
        defensive_selection_lookback=defensive_selection_lookback,
        volatility_target=volatility_target,
        max_leverage=max_leverage,
        crash_hedge_symbols=crash_hedges,
        crash_hedge_weight=crash_hedge_weight,
        crash_hedge_trigger_lookback=crash_hedge_trigger_lookback,
        crash_hedge_trigger_drawdown=crash_hedge_trigger_drawdown,
        crash_hedge_selection_lookback=crash_hedge_selection_lookback,
        crash_hedge_hold_days=crash_hedge_hold_days,
        volume_lookback_short=volume_lookback_short,
        volume_lookback_long=volume_lookback_long,
        volume_weight=volume_weight,
        initial_cash=initial_cash,
        final_equity=equity,
        benchmark_final_equity=benchmark_equity,
        benchmark_symbol=benchmark_symbol,
        benchmark_market=benchmark_market,
        total_return=total_return,
        benchmark_return=benchmark_return,
        annualized_return=annualized_return,
        benchmark_annualized_return=benchmark_annualized_return,
        annualized_excess_return=annualized_return - benchmark_annualized_return,
        sharpe=_sharpe(returns),
        benchmark_sharpe=_sharpe(benchmark_returns),
        max_drawdown=_max_drawdown([point.equity for point in curve]),
        benchmark_max_drawdown=_max_drawdown([point.benchmark_equity for point in curve]),
        rebalance_count=rebalance_count,
        average_holdings=mean(holding_counts) if holding_counts else 0.0,
        average_eligible_symbols=mean(eligible_counts)
        if eligible_counts
        else float(len(close_by_symbol)),
        average_gross_weight=mean(gross_weights) if gross_weights else 0.0,
        risk_on_ratio=mean([1.0 if flag else 0.0 for flag in risk_on_flags])
        if risk_on_flags
        else 0.0,
        crash_hedge_active_ratio=(
            mean([1.0 if flag else 0.0 for flag in crash_hedge_flags]) if crash_hedge_flags else 0.0
        ),
        fundamental_record_count=sum(len(records) for records in fundamentals.values()),
        delisting_returns_applied=delisting_returns_applied,
        ended_members_without_delisting=_ended_members_without_delisting(
            universe_members,
            delistings_by_symbol,
        ),
        fee_bps=fee_bps,
        total_cost=total_cost,
        equity_curve=curve,
        annual_returns=_annual_returns(curve),
    )


def format_factor_portfolio_report(result: FactorPortfolioResult) -> str:
    benchmark_label = (
        result.benchmark_symbol
        if result.benchmark_symbol == "Equal-weight universe"
        else f"{result.benchmark_symbol} ({result.benchmark_market})"
    )
    lines = [
        "# Multi-Factor Rotation Portfolio Backtest",
        "",
        "Research-only output. This does not place orders or provide investment advice.",
        "",
        f"Universe: {', '.join(result.symbols)}",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Market | {result.market} |",
        f"| Universe Mode | {result.universe_mode} |",
        f"| Universe | {result.universe_name} |",
        f"| Window | {result.start} to {result.end} |",
        f"| Common Bars | {result.rows} |",
        f"| Momentum Lookback | {result.momentum_lookback} bars |",
        f"| Momentum Ensemble | {_lookback_ensemble_label(result.ensemble_momentum_lookbacks, result.momentum_lookback)} |",
        f"| Reversal Lookback | {result.reversal_lookback} bars |",
        f"| Volatility Lookback | {result.volatility_lookback} bars |",
        f"| Risk Filter | {_risk_filter_label(result.risk_filter_lookback)} |",
        f"| Risk Filter Ensemble | {_risk_filter_ensemble_label(result)} |",
        f"| Top N | {result.top_n} |",
        f"| Weighting | {result.weighting} |",
        f"| Max Risk Weight | {result.max_risk_weight * 100:.1f}% |",
        f"| Drawdown Guard | {_drawdown_guard_label(result.drawdown_guard)} |",
        f"| Defensive Basket | {', '.join(result.defensive_symbols)} |",
        f"| Defensive Selection Lookback | {result.defensive_selection_lookback} bars |",
        f"| Defensive Asset Ranking | {'excluded' if result.defensive_only else 'included'} |",
        f"| Volatility Target | {_volatility_target_label(result.volatility_target, result.max_leverage)} |",
        f"| Crash Hedge | {_crash_hedge_label(result)} |",
        f"| Volume Spike | {_volume_spike_label(result)} |",
        f"| Benchmark | {benchmark_label} |",
        f"| Initial Cash | {result.initial_cash:,.2f} |",
        f"| Final Equity | {result.final_equity:,.2f} |",
        f"| Strategy Return | {result.total_return * 100:+.2f}% |",
        f"| Benchmark Return | {result.benchmark_return * 100:+.2f}% |",
        f"| Annualized Return | {result.annualized_return * 100:+.2f}% |",
        f"| Benchmark Annualized Return | {result.benchmark_annualized_return * 100:+.2f}% |",
        f"| Annualized Excess Return | {result.annualized_excess_return * 100:+.2f}% |",
        f"| Sharpe | {result.sharpe:.2f} |",
        f"| Benchmark Sharpe | {result.benchmark_sharpe:.2f} |",
        f"| Max Drawdown | {result.max_drawdown * 100:.2f}% |",
        f"| Benchmark Max Drawdown | {result.benchmark_max_drawdown * 100:.2f}% |",
        f"| Rebalances | {result.rebalance_count} |",
        f"| Average Holdings | {result.average_holdings:.2f} |",
        f"| Average Eligible Symbols | {result.average_eligible_symbols:.2f} |",
        f"| Average Gross Weight | {result.average_gross_weight * 100:.1f}% |",
        f"| Risk-On Ratio | {result.risk_on_ratio * 100:.1f}% |",
        f"| Crash Hedge Active Ratio | {result.crash_hedge_active_ratio * 100:.1f}% |",
        f"| PIT Fundamental Records | {result.fundamental_record_count} |",
        f"| Delisting Returns Applied | {result.delisting_returns_applied} |",
        f"| Ended Members Missing Delisting Return | {result.ended_members_without_delisting} |",
        f"| Fee | {result.fee_bps:.2f} bps per turnover |",
        f"| Total Cost Drag | {result.total_cost * 100:.2f}% |",
        "",
        "## Annual Returns",
        "",
        "| Year | Strategy | Benchmark | Excess |",
        "|---:|---:|---:|---:|",
    ]
    for annual in result.annual_returns:
        lines.append(
            f"| {annual.year} | {annual.strategy_return * 100:+.2f}% | "
            f"{annual.benchmark_return * 100:+.2f}% | {annual.excess_return * 100:+.2f}% |"
        )
    lines.extend(
        [
            "",
            "## Last 5 Portfolio Points",
            "",
            "| Date | Equity | Benchmark | Holdings | Risk-On | Cost | Day Return |",
            "|---|---:|---:|---|---|---:|---:|",
        ]
    )
    for point in result.equity_curve[-5:]:
        holdings = ", ".join(f"{symbol}:{weight:.2f}" for symbol, weight in point.weights) or "Cash"
        lines.append(
            f"| {point.ts} | {point.equity:,.2f} | {point.benchmark_equity:,.2f} | "
            f"{holdings} | {'yes' if point.risk_on else 'no'} | {point.cost * 100:.3f}% | "
            f"{point.portfolio_return * 100:+.2f}% |"
        )
    lines.extend(["", "## Bias Control", "", _bias_note(result)])
    return "\n".join(lines)


def _target_weights_ensemble(
    close_by_symbol: dict[str, dict[date, float]],
    fundamentals: dict[str, tuple[FundamentalRecord, ...]],
    eligible: tuple[str, ...],
    *,
    today: date,
    top_n: int,
    momentum_lookbacks: tuple[int, ...],
    reversal_lookback: int,
    volatility_lookback: int,
    factor_weights: FactorWeights,
    weighting: str,
    volume_by_symbol: dict[str, dict[date, float]] | None = None,
    volume_lookback_short: int = 21,
    volume_lookback_long: int = 252,
    volume_weight: float = 0.0,
) -> dict[str, float]:
    if len(momentum_lookbacks) == 1:
        return _target_weights(
            close_by_symbol,
            fundamentals,
            eligible,
            today=today,
            top_n=top_n,
            momentum_lookback=momentum_lookbacks[0],
            reversal_lookback=reversal_lookback,
            volatility_lookback=volatility_lookback,
            factor_weights=factor_weights,
            weighting=weighting,
            volume_by_symbol=volume_by_symbol,
            volume_lookback_short=volume_lookback_short,
            volume_lookback_long=volume_lookback_long,
            volume_weight=volume_weight,
        )
    totals: dict[str, float] = {}
    used_variants = 0
    for momentum_lookback in momentum_lookbacks:
        weights = _target_weights(
            close_by_symbol,
            fundamentals,
            eligible,
            today=today,
            top_n=top_n,
            momentum_lookback=momentum_lookback,
            reversal_lookback=reversal_lookback,
            volatility_lookback=volatility_lookback,
            factor_weights=factor_weights,
            weighting=weighting,
            volume_by_symbol=volume_by_symbol,
            volume_lookback_short=volume_lookback_short,
            volume_lookback_long=volume_lookback_long,
            volume_weight=volume_weight,
        )
        if not weights:
            continue
        used_variants += 1
        for symbol, weight in weights.items():
            totals[symbol] = totals.get(symbol, 0.0) + weight
    if used_variants == 0:
        return {}
    return {symbol: weight / used_variants for symbol, weight in totals.items() if weight > 0}


def _target_weights(
    close_by_symbol: dict[str, dict[date, float]],
    fundamentals: dict[str, tuple[FundamentalRecord, ...]],
    eligible: tuple[str, ...],
    *,
    today: date,
    top_n: int,
    momentum_lookback: int,
    reversal_lookback: int,
    volatility_lookback: int,
    factor_weights: FactorWeights,
    weighting: str,
    volume_by_symbol: dict[str, dict[date, float]] | None = None,
    volume_lookback_short: int = 21,
    volume_lookback_long: int = 252,
    volume_weight: float = 0.0,
) -> dict[str, float]:
    raw = [
        _factor_raw_score(
            symbol,
            close_by_symbol[symbol],
            _fundamental_as_of(fundamentals.get(symbol, ()), today),
            today=today,
            momentum_lookback=momentum_lookback,
            reversal_lookback=reversal_lookback,
            volatility_lookback=volatility_lookback,
        )
        for symbol in eligible
        if symbol in close_by_symbol
    ]
    rows = [row for row in raw if row is not None]
    if not rows:
        return {}
    momentums = _z_scores([row.momentum for row in rows])
    reversals = _z_scores([row.reversal for row in rows])
    vols = _z_scores([row.low_volatility for row in rows])
    values = _z_scores([row.value for row in rows])
    qualities = _z_scores([row.quality for row in rows])

    # Compute cross-sectional volume-spike z-scores when volume_weight > 0
    use_volume = volume_weight > 0.0 and volume_by_symbol is not None
    volume_z: list[float]
    if use_volume:
        assert volume_by_symbol is not None
        vol_raw = [
            _volume_spike_score(
                volume_by_symbol.get(row.symbol, {}),
                today=today,
                lookback_short=volume_lookback_short,
                lookback_long=volume_lookback_long,
            )
            for row in rows
        ]
        volume_z = _z_scores(vol_raw)
    else:
        volume_z = [0.0] * len(rows)

    # Blend: when volume_weight > 0 the existing factor score is down-scaled
    # by (1 - volume_weight) and volume z-score is added at volume_weight.
    existing_scale = 1.0 - volume_weight if use_volume else 1.0
    scored = [
        FactorScorePoint(
            symbol=row.symbol,
            composite=(
                existing_scale
                * (
                    factor_weights.momentum * momentums[index]
                    - factor_weights.reversal * reversals[index]
                    + factor_weights.low_volatility * vols[index]
                    + factor_weights.value * values[index]
                    + factor_weights.quality * qualities[index]
                )
                + volume_weight * volume_z[index]
            ),
            momentum=row.momentum,
            reversal=row.reversal,
            low_volatility=row.low_volatility,
            value=row.value,
            quality=row.quality,
        )
        for index, row in enumerate(rows)
    ]
    selected = [
        row.symbol for row in sorted(scored, key=lambda row: row.composite, reverse=True)[:top_n]
    ]
    if weighting == "equal":
        return _equal_weights(selected)
    return _inverse_vol_weights(
        close_by_symbol, selected, today=today, lookback=volatility_lookback
    )


def _risk_eligible_symbols(
    eligible: tuple[str, ...],
    *,
    defensive_symbols: tuple[str | None, ...],
    defensive_only: bool,
    excluded_symbols: tuple[str, ...] = (),
) -> tuple[str, ...]:
    defensive = {symbol.upper() for symbol in defensive_symbols if symbol}
    excluded = {symbol.upper() for symbol in excluded_symbols}
    blocked = excluded | (defensive if defensive_only else set())
    if not blocked:
        return eligible
    return tuple(symbol for symbol in eligible if symbol.upper() not in blocked)


def _factor_raw_score(
    symbol: str,
    closes: dict[date, float],
    fundamentals: FundamentalRecord | None,
    *,
    today: date,
    momentum_lookback: int,
    reversal_lookback: int,
    volatility_lookback: int,
) -> FactorScorePoint | None:
    dates = sorted(closes)
    if today not in closes:
        return None
    index = dates.index(today)
    if index < max(momentum_lookback, reversal_lookback, volatility_lookback):
        return None
    momentum = (closes[today] / closes[dates[index - momentum_lookback]]) - 1.0
    reversal = (closes[today] / closes[dates[index - reversal_lookback]]) - 1.0
    returns = [
        (closes[dates[item]] / closes[dates[item - 1]]) - 1.0
        for item in range(index - volatility_lookback + 1, index + 1)
    ]
    volatility = pstdev(returns) * sqrt(TRADING_DAYS) if len(returns) > 1 else 0.0
    value = _value_score(closes[today], fundamentals)
    quality = _quality_score(fundamentals)
    return FactorScorePoint(
        symbol=symbol,
        composite=0.0,
        momentum=momentum,
        reversal=reversal,
        low_volatility=-volatility,
        value=value,
        quality=quality,
    )


def _value_score(price: float, fundamentals: FundamentalRecord | None) -> float:
    if fundamentals is None:
        return 0.0
    market_cap = (
        price * fundamentals.shares_out
        if fundamentals.shares_out is not None and fundamentals.shares_out > 0
        else None
    )
    if not market_cap:
        return 0.0
    earnings_yield = (
        fundamentals.net_income / market_cap if fundamentals.net_income is not None else 0.0
    )
    fcf_yield = (
        fundamentals.free_cash_flow / market_cap if fundamentals.free_cash_flow is not None else 0.0
    )
    return mean([earnings_yield, fcf_yield])


def _quality_score(fundamentals: FundamentalRecord | None) -> float:
    if fundamentals is None:
        return 0.0
    roe = (
        fundamentals.net_income / fundamentals.total_equity
        if fundamentals.total_equity and fundamentals.net_income is not None
        else 0.0
    )
    debt_penalty = (
        fundamentals.total_debt / fundamentals.total_equity
        if fundamentals.total_debt is not None and fundamentals.total_equity
        else 0.0
    )
    return roe - debt_penalty


def _volume_spike_score(
    volumes: dict[date, float],
    *,
    today: date,
    lookback_short: int,
    lookback_long: int,
) -> float:
    """Return vol_ratio = mean(volume[last lookback_short days]) / mean(volume[last lookback_long days]).

    Values > 1 indicate above-average activity (capital inflow signal).
    Returns 0.0 when there is insufficient history.
    """
    if today not in volumes:
        return 0.0
    dates = sorted(volumes)
    index = dates.index(today)
    if index < lookback_long:
        return 0.0
    short_vols = [volumes[dates[i]] for i in range(index - lookback_short + 1, index + 1)]
    long_vols = [volumes[dates[i]] for i in range(index - lookback_long + 1, index + 1)]
    avg_long = mean(long_vols)
    if avg_long <= 0:
        return 0.0
    return mean(short_vols) / avg_long


def _risk_on(
    benchmark_closes: dict[date, float],
    today: date,
    risk_filter_lookback: int,
) -> bool:
    if risk_filter_lookback == 0:
        return True
    if not benchmark_closes or today not in benchmark_closes:
        return True
    dates = sorted(benchmark_closes)
    index = dates.index(today)
    if index < risk_filter_lookback:
        return True
    average = mean(
        benchmark_closes[dates[item]] for item in range(index - risk_filter_lookback + 1, index + 1)
    )
    return benchmark_closes[today] >= average


def _risk_on_ensemble(
    benchmark_closes: dict[date, float],
    today: date,
    lookbacks: tuple[int, ...],
    *,
    threshold: float,
) -> bool:
    votes = [_risk_on(benchmark_closes, today, lookback) for lookback in lookbacks]
    if not votes:
        return True
    return mean([1.0 if vote else 0.0 for vote in votes]) >= threshold


def _bond_equity_corr(
    spy_returns: list[float],
    bond_returns: list[float],
) -> float:
    """Pearson correlation between two equal-length return series."""
    n = len(spy_returns)
    if n < 2:
        return 0.0
    mu_s = mean(spy_returns)
    mu_b = mean(bond_returns)
    cov = sum((s - mu_s) * (b - mu_b) for s, b in zip(spy_returns, bond_returns, strict=True)) / n
    std_s = pstdev(spy_returns)
    std_b = pstdev(bond_returns)
    if std_s == 0 or std_b == 0:
        return 0.0
    return cov / (std_s * std_b)


def _regime_cash_active(
    benchmark_closes: dict[date, float],
    bond_closes: dict[date, float],
    *,
    today: date,
    corr_window: int,
    corr_threshold: float,
) -> bool:
    """Return True when bond-equity correlation > threshold AND benchmark < its MA.

    Conditions (both must hold):
    - SPY < SPY MA(corr_window)  [market already risk-off or weakening]
    - rolling corr(SPY daily returns, bond daily returns, corr_window) > corr_threshold
      [bonds are moving WITH equities — no longer a hedge]
    """
    if not benchmark_closes or not bond_closes or today not in benchmark_closes:
        return False
    bench_dates = sorted(benchmark_closes)
    idx = bench_dates.index(today)
    if idx < corr_window:
        return False
    # SPY < MA check
    window_closes = [
        benchmark_closes[bench_dates[i]] for i in range(idx - corr_window + 1, idx + 1)
    ]
    ma = mean(window_closes)
    if benchmark_closes[today] >= ma:
        return False
    # Build aligned daily-return pairs over corr_window bars
    spy_rets: list[float] = []
    bond_rets: list[float] = []
    for i in range(idx - corr_window + 1, idx + 1):
        d_prev = bench_dates[i - 1]
        d_cur = bench_dates[i]
        if d_prev not in bond_closes or d_cur not in bond_closes:
            continue
        prev_spy = benchmark_closes[d_prev]
        cur_spy = benchmark_closes[d_cur]
        if prev_spy <= 0:
            continue
        spy_rets.append(cur_spy / prev_spy - 1.0)
        bond_rets.append(bond_closes[d_cur] / bond_closes[d_prev] - 1.0)
    if len(spy_rets) < 2:
        return False
    corr = _bond_equity_corr(spy_rets, bond_rets)
    return corr > corr_threshold


def _select_defensive_symbol(
    close_by_symbol: dict[str, dict[date, float]],
    defensive_symbols: tuple[str | None, ...],
    *,
    today: date,
    lookback: int,
) -> str | None:
    if not defensive_symbols:
        return None
    if len(defensive_symbols) == 1:
        symbol = defensive_symbols[0]
        if (
            symbol
            and symbol.upper() in close_by_symbol
            and today in close_by_symbol[symbol.upper()]
        ):
            return symbol.upper()
        return None
    scored: list[tuple[float, str]] = []
    available: list[str] = []
    cash_allowed = any(symbol is None for symbol in defensive_symbols)
    for symbol in defensive_symbols:
        if symbol is None:
            continue
        upper = symbol.upper()
        closes = close_by_symbol.get(upper)
        if not closes or today not in closes:
            continue
        available.append(upper)
        dates = sorted(closes)
        index = dates.index(today)
        if index >= lookback:
            scored.append(((closes[today] / closes[dates[index - lookback]]) - 1.0, upper))
    if scored:
        best_momentum, best_symbol = max(scored, key=lambda item: item[0])
        if best_momentum > 0 or not cash_allowed:
            return best_symbol
    if cash_allowed:
        return None
    return available[0] if available else None


def _defensive_weights(
    close_by_symbol: dict[str, dict[date, float]],
    *,
    defensive_symbol: str | None,
    today: date,
) -> dict[str, float]:
    if (
        defensive_symbol
        and defensive_symbol.upper() in close_by_symbol
        and today in close_by_symbol[defensive_symbol.upper()]
    ):
        return {defensive_symbol.upper(): 1.0}
    return {}


def _crash_hedge_triggered(
    benchmark_closes: dict[date, float],
    today: date,
    *,
    lookback: int,
    trigger_drawdown: float,
    enabled: bool,
) -> bool:
    if not enabled or not benchmark_closes or today not in benchmark_closes:
        return False
    dates = sorted(benchmark_closes)
    index = dates.index(today)
    if index < 1:
        return False
    start_index = max(0, index - lookback + 1)
    peak = max(benchmark_closes[dates[item]] for item in range(start_index, index + 1))
    if peak <= 0:
        return False
    drawdown = benchmark_closes[today] / peak - 1.0
    if index < lookback or benchmark_closes[dates[index - lookback]] <= 0:
        return drawdown <= -trigger_drawdown
    trailing_return = benchmark_closes[today] / benchmark_closes[dates[index - lookback]] - 1.0
    return drawdown <= -trigger_drawdown and trailing_return <= -trigger_drawdown


def _apply_crash_hedge(
    close_by_symbol: dict[str, dict[date, float]],
    weights: dict[str, float],
    *,
    hedge_symbols: tuple[str, ...],
    today: date,
    active: bool,
    hedge_weight: float,
    selection_lookback: int,
) -> dict[str, float]:
    hedge_set = set(hedge_symbols)
    base = {symbol: weight for symbol, weight in weights.items() if symbol not in hedge_set}
    if not active:
        return {symbol: weight for symbol, weight in base.items() if weight > 0}
    selected = _select_defensive_symbol(
        close_by_symbol,
        (*hedge_symbols, None),
        today=today,
        lookback=selection_lookback,
    )
    scaled = {
        symbol: weight * (1.0 - hedge_weight)
        for symbol, weight in base.items()
        if weight * (1.0 - hedge_weight) > 0
    }
    if selected:
        scaled[selected] = scaled.get(selected, 0.0) + hedge_weight
    return scaled


def _apply_volatility_target(
    close_by_symbol: dict[str, dict[date, float]],
    weights: dict[str, float],
    *,
    today: date,
    volatility_target: float,
    volatility_lookback: int,
    max_leverage: float,
) -> dict[str, float]:
    if not weights or volatility_target <= 0:
        return weights
    realized_volatility = _portfolio_realized_volatility(
        close_by_symbol,
        weights,
        today=today,
        lookback=volatility_lookback,
    )
    if realized_volatility <= 0:
        return weights
    scale = min(max_leverage, volatility_target / realized_volatility)
    return {symbol: weight * scale for symbol, weight in weights.items() if weight * scale > 0}


def _portfolio_realized_volatility(
    close_by_symbol: dict[str, dict[date, float]],
    weights: dict[str, float],
    *,
    today: date,
    lookback: int,
) -> float:
    weighted_symbols = [
        symbol for symbol, weight in weights.items() if weight != 0 and symbol in close_by_symbol
    ]
    if not weighted_symbols:
        return 0.0
    common_dates = sorted(
        set.intersection(*(set(close_by_symbol[symbol]) for symbol in weighted_symbols))
    )
    if today not in common_dates:
        return 0.0
    index = common_dates.index(today)
    if index < lookback:
        return 0.0
    returns: list[float] = []
    for item in range(index - lookback + 1, index + 1):
        previous = common_dates[item - 1]
        current = common_dates[item]
        returns.append(
            sum(
                weights[symbol]
                * ((close_by_symbol[symbol][current] / close_by_symbol[symbol][previous]) - 1.0)
                for symbol in weighted_symbols
            )
        )
    return pstdev(returns) * sqrt(TRADING_DAYS) if len(returns) > 1 else 0.0


def _cap_risk_weights(
    weights: dict[str, float],
    close_by_symbol: dict[str, dict[date, float]],
    *,
    defensive_symbol: str | None,
    max_risk_weight: float,
    today: date,
) -> dict[str, float]:
    if not weights or max_risk_weight >= 1:
        return weights
    defensive = defensive_symbol.upper() if defensive_symbol else None
    capped: dict[str, float] = {}
    overflow = 0.0
    for symbol, weight in weights.items():
        if defensive and symbol.upper() == defensive:
            capped[symbol] = capped.get(symbol, 0.0) + weight
            continue
        kept = min(weight, max_risk_weight)
        capped[symbol] = capped.get(symbol, 0.0) + kept
        overflow += weight - kept
    if overflow <= 0:
        return capped
    if defensive and defensive in close_by_symbol and today in close_by_symbol[defensive]:
        capped[defensive] = capped.get(defensive, 0.0) + overflow
    return {symbol: weight for symbol, weight in capped.items() if weight > 0}


def _inverse_vol_weights(
    close_by_symbol: dict[str, dict[date, float]],
    symbols: list[str],
    *,
    today: date,
    lookback: int,
) -> dict[str, float]:
    risk_units: dict[str, float] = {}
    for symbol in symbols:
        closes = close_by_symbol[symbol]
        dates = sorted(closes)
        if today not in closes:
            continue
        index = dates.index(today)
        if index < lookback:
            continue
        returns = [
            (closes[dates[item]] / closes[dates[item - 1]]) - 1.0
            for item in range(index - lookback + 1, index + 1)
        ]
        vol = pstdev(returns) * sqrt(TRADING_DAYS) if len(returns) > 1 else 0.0
        if vol > 0:
            risk_units[symbol] = 1 / vol
    total = sum(risk_units.values())
    if total == 0:
        return _equal_weights(symbols)
    return {symbol: value / total for symbol, value in risk_units.items()}


def _equal_weights(symbols: list[str]) -> dict[str, float]:
    equal = 1 / len(symbols) if symbols else 0.0
    return dict.fromkeys(symbols, equal)


def _weighted_return(
    close_by_symbol: dict[str, dict[date, float]],
    weights: dict[str, float],
    delistings_by_symbol: dict[str, tuple[DelistingReturn, ...]],
    today: date,
    tomorrow: date,
) -> tuple[float, tuple[str, ...]]:
    if not weights:
        return 0.0, ()
    total = 0.0
    exited: list[str] = []
    for symbol, weight in weights.items():
        holding_return, exited_symbol = _holding_return(
            close_by_symbol,
            delistings_by_symbol,
            symbol,
            today,
            tomorrow,
        )
        total += weight * holding_return
        if exited_symbol:
            exited.append(symbol)
    return total, tuple(exited)


def _holding_return(
    close_by_symbol: dict[str, dict[date, float]],
    delistings_by_symbol: dict[str, tuple[DelistingReturn, ...]],
    symbol: str,
    today: date,
    tomorrow: date,
) -> tuple[float, bool]:
    closes = close_by_symbol[symbol]
    if today not in closes:
        raise ValueError(f"{symbol}: missing held-symbol price for {today}")
    if tomorrow in closes:
        return (closes[tomorrow] / closes[today]) - 1.0, False
    delisting_return = _delisting_return(delistings_by_symbol, symbol, today, tomorrow)
    if delisting_return is None:
        raise ValueError(
            f"{symbol}: missing held-symbol price for {today} to {tomorrow}; "
            "provide delisting-adjusted PIT data or --delisting-returns-csv"
        )
    return delisting_return.return_pct, True


def _equal_weight_return(
    close_by_symbol: dict[str, dict[date, float]],
    today: date,
    tomorrow: date,
) -> float:
    returns = [
        (closes[tomorrow] / closes[today]) - 1.0
        for closes in close_by_symbol.values()
        if today in closes and tomorrow in closes
    ]
    if not returns:
        raise ValueError(f"no benchmarkable bars for {today} to {tomorrow}")
    return mean(returns)


def _benchmark_return(closes: dict[date, float], today: date, tomorrow: date) -> float:
    if today not in closes or tomorrow not in closes:
        raise ValueError(f"benchmark is missing bars for {today} to {tomorrow}")
    return (closes[tomorrow] / closes[today]) - 1.0


def _turnover(before: dict[str, float], after: dict[str, float]) -> float:
    symbols = set(before) | set(after)
    return sum(abs(after.get(symbol, 0.0) - before.get(symbol, 0.0)) for symbol in symbols)


def _closes_by_date(bars: list[PriceBar] | None) -> dict[date, float]:
    if not bars:
        return {}
    return {bar.ts: bar.close for bar in sorted(bars, key=lambda item: item.ts)}


def _normalize_fundamentals(
    fundamentals_by_symbol: FundamentalsInput | None,
) -> dict[str, tuple[FundamentalRecord, ...]]:
    if not fundamentals_by_symbol:
        return {}
    rows: dict[str, tuple[FundamentalRecord, ...]] = {}
    for symbol, records in fundamentals_by_symbol.items():
        values: tuple[FundamentalRecord, ...] = (
            (records,) if isinstance(records, FundamentalRecord) else tuple(records)
        )
        rows[symbol.upper()] = tuple(sorted(values, key=lambda item: item.asof_ts))
    return rows


def _fundamental_as_of(
    records: tuple[FundamentalRecord, ...],
    as_of: date,
) -> FundamentalRecord | None:
    usable = [record for record in records if record.asof_ts.date() <= as_of]
    if not usable:
        return None
    return max(usable, key=lambda item: (item.asof_ts, item.period_end))


def _delistings_by_symbol(
    delisting_returns: list[DelistingReturn] | None,
) -> dict[str, tuple[DelistingReturn, ...]]:
    if not delisting_returns:
        return {}
    rows: dict[str, list[DelistingReturn]] = {}
    for item in delisting_returns:
        rows.setdefault(item.symbol.upper(), []).append(item)
    return {
        symbol: tuple(sorted(items, key=lambda item: item.ts)) for symbol, items in rows.items()
    }


def _delisting_return(
    delistings_by_symbol: dict[str, tuple[DelistingReturn, ...]],
    symbol: str,
    today: date,
    tomorrow: date,
) -> DelistingReturn | None:
    for item in delistings_by_symbol.get(symbol.upper(), ()):
        if today < item.ts <= tomorrow:
            return item
    return None


def _common_dates(
    close_by_symbol: dict[str, dict[date, float]],
    benchmark_closes: dict[date, float],
    has_pit_membership: bool,
) -> list[date]:
    if has_pit_membership:
        date_sets = [set(values) for values in close_by_symbol.values()]
        return sorted(set(benchmark_closes) if benchmark_closes else set().union(*date_sets))
    return sorted(set.intersection(*(set(values) for values in close_by_symbol.values())))


def _first_index_on_or_after(dates: list[date], target: date | None) -> int:
    if target is None:
        return 0
    for index, item in enumerate(dates):
        if item >= target:
            return index
    return len(dates)


def _membership_by_symbol(
    universe_members: list[UniverseMember] | None,
) -> dict[str, tuple[UniverseMember, ...]]:
    if not universe_members:
        return {}
    rows: dict[str, list[UniverseMember]] = {}
    for member in universe_members:
        rows.setdefault(member.symbol.upper(), []).append(member)
    return {symbol: tuple(members) for symbol, members in rows.items()}


def _eligible_symbols(
    membership_by_symbol: dict[str, tuple[UniverseMember, ...]],
    as_of: date,
) -> tuple[str, ...]:
    if not membership_by_symbol:
        return ()
    return tuple(
        sorted(
            symbol
            for symbol, memberships in membership_by_symbol.items()
            if any(
                member.start_date <= as_of and (member.end_date is None or as_of <= member.end_date)
                for member in memberships
            )
        )
    )


def _universe_name(universe_members: list[UniverseMember] | None) -> str:
    if not universe_members:
        return "static-symbol-list"
    return ",".join(sorted({member.universe for member in universe_members}))


def _ended_members_without_delisting(
    universe_members: list[UniverseMember] | None,
    delistings_by_symbol: dict[str, tuple[DelistingReturn, ...]],
) -> int:
    if not universe_members:
        return 0
    count = 0
    for member in universe_members:
        if member.end_date is None:
            continue
        has_delisting_return = any(
            item.market == member.market.lower() and item.ts >= member.end_date
            for item in delistings_by_symbol.get(member.symbol.upper(), ())
        )
        if not has_delisting_return:
            count += 1
    return count


def _annual_returns(curve: list[FactorPortfolioPoint]) -> list[AnnualReturn]:
    by_year: dict[int, list[FactorPortfolioPoint]] = {}
    for point in curve:
        by_year.setdefault(point.ts.year, []).append(point)
    rows: list[AnnualReturn] = []
    for year, points in sorted(by_year.items()):
        first = points[0]
        last = points[-1]
        strategy_return = (last.equity / first.equity) - 1.0
        benchmark_return = (last.benchmark_equity / first.benchmark_equity) - 1.0
        rows.append(
            AnnualReturn(
                year=year,
                strategy_return=strategy_return,
                benchmark_return=benchmark_return,
                excess_return=strategy_return - benchmark_return,
            )
        )
    return rows


def _positive_lookbacks(
    values: tuple[int, ...] | None,
    *,
    default: int,
    label: str,
) -> tuple[int, ...]:
    lookbacks = tuple(values or (default,))
    if not lookbacks:
        raise ValueError(f"{label} must not be empty")
    if any(item < 1 for item in lookbacks):
        raise ValueError(f"{label} values must be >= 1")
    return tuple(dict.fromkeys(lookbacks))


def _nonnegative_lookbacks(
    values: tuple[int, ...] | None,
    *,
    default: int,
    label: str,
) -> tuple[int, ...]:
    lookbacks = tuple(values or (default,))
    if not lookbacks:
        raise ValueError(f"{label} must not be empty")
    if any(item < 0 for item in lookbacks):
        raise ValueError(f"{label} values must be >= 0")
    return tuple(dict.fromkeys(lookbacks))


def _defensive_symbol_tuple(
    defensive_symbol: str | None,
    defensive_symbols: tuple[str | None, ...] | None,
) -> tuple[str | None, ...]:
    choices = defensive_symbols if defensive_symbols is not None else (defensive_symbol,)
    normalized: list[str | None] = []
    for symbol in choices:
        if symbol is None:
            normalized.append(None)
            continue
        clean = symbol.strip().upper()
        normalized.append(None if clean in {"", "CASH", "NONE"} else clean)
    if not normalized:
        return (None,)
    return tuple(dict.fromkeys(normalized))


def _symbol_tuple(symbols: tuple[str, ...] | None) -> tuple[str, ...]:
    if not symbols:
        return ()
    return tuple(dict.fromkeys(symbol.strip().upper() for symbol in symbols if symbol.strip()))


def _defensive_labels(defensive_symbols: tuple[str | None, ...]) -> tuple[str, ...]:
    return tuple(symbol if symbol is not None else "CASH" for symbol in defensive_symbols)


def _z_scores(values: list[float]) -> list[float]:
    if not values:
        return []
    sigma = pstdev(values)
    if sigma == 0:
        return [0.0 for _ in values]
    mu = mean(values)
    return [(value - mu) / sigma for value in values]


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


def _bias_note(result: FactorPortfolioResult) -> str:
    if result.universe_mode == "point-in-time":
        note = (
            "Point-in-time universe membership was enforced at each rebalance. "
            "PIT fundamentals were selected by as-of timestamp at each rebalance."
        )
        if result.ended_members_without_delisting:
            return (
                f"{note} Warning: {result.ended_members_without_delisting} ended universe member(s) "
                "do not have explicit delisting returns, so stock-universe evidence remains incomplete."
            )
        return f"{note} Delisting returns are applied when a held symbol has no next price."
    return (
        "Static symbol list was used. This report is not survivorship-free and must not be treated "
        "as high-confidence stock-universe evidence."
    )


def _risk_filter_label(lookback: int) -> str:
    if lookback == 0:
        return "disabled"
    return f"Benchmark {lookback}-bar moving average"


def _lookback_ensemble_label(lookbacks: tuple[int, ...], baseline: int) -> str:
    if lookbacks == (baseline,):
        return "disabled"
    return ", ".join(f"{lookback} bars" for lookback in lookbacks)


def _risk_filter_ensemble_label(result: FactorPortfolioResult) -> str:
    if result.ensemble_risk_filter_lookbacks == (result.risk_filter_lookback,):
        return "disabled"
    lookbacks = ", ".join(
        "disabled" if lookback == 0 else f"{lookback} bars"
        for lookback in result.ensemble_risk_filter_lookbacks
    )
    return f"{lookbacks}; vote >= {result.risk_filter_vote_threshold:.0%}"


def _volatility_target_label(target: float, max_leverage: float) -> str:
    if target == 0:
        return "disabled"
    return f"{target * 100:.1f}% annualized, max {max_leverage:.2f}x gross"


def _crash_hedge_label(result: FactorPortfolioResult) -> str:
    if not result.crash_hedge_symbols or result.crash_hedge_weight == 0:
        return "disabled"
    return (
        f"{', '.join(result.crash_hedge_symbols)} at {result.crash_hedge_weight * 100:.1f}% "
        f"when {result.crash_hedge_trigger_lookback}-bar benchmark drawdown <= "
        f"-{result.crash_hedge_trigger_drawdown * 100:.1f}%; "
        f"selection {result.crash_hedge_selection_lookback} bars; "
        f"hold {result.crash_hedge_hold_days or 'signal'} days"
    )


def _drawdown_guard_label(threshold: float) -> str:
    if threshold == 0:
        return "disabled"
    return f"switch to defensive at {threshold * 100:.1f}% strategy drawdown"


def _volume_spike_label(result: FactorPortfolioResult) -> str:
    if result.volume_weight == 0.0:
        return "disabled"
    return (
        f"weight={result.volume_weight:.2f} "
        f"short={result.volume_lookback_short}d "
        f"long={result.volume_lookback_long}d"
    )
