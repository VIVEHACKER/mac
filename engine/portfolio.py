from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import sqrt
from statistics import mean, pstdev

from data.models import DelistingReturn, PriceBar, UniverseMember

TRADING_DAYS = 252


@dataclass(frozen=True)
class ScreenRow:
    symbol: str
    market: str
    as_of: date
    close: float
    lookback_return: float
    rows: int


@dataclass(frozen=True)
class PortfolioPoint:
    ts: date
    equity: float
    benchmark_equity: float
    holdings: tuple[str, ...]
    portfolio_return: float
    benchmark_return: float
    cost: float


@dataclass(frozen=True)
class AnnualReturn:
    year: int
    strategy_return: float
    benchmark_return: float
    excess_return: float


@dataclass(frozen=True)
class PortfolioBacktestResult:
    symbols: tuple[str, ...]
    universe_mode: str
    universe_name: str
    market: str
    start: date
    end: date
    rows: int
    lookback: int
    top_n: int
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
    benchmark_sharpe: float
    max_drawdown: float
    benchmark_max_drawdown: float
    rebalance_count: int
    average_holdings: float
    average_eligible_symbols: float
    delisting_returns_applied: int
    ended_members_without_delisting: int
    rebalance_days: int
    fee_bps: float
    total_cost: float
    equity_curve: list[PortfolioPoint]
    annual_returns: list[AnnualReturn]


def screen_momentum(
    bars_by_symbol: dict[str, list[PriceBar]],
    lookback: int = 126,
) -> list[ScreenRow]:
    rows: list[ScreenRow] = []
    for symbol, bars in bars_by_symbol.items():
        ordered = sorted(bars, key=lambda bar: bar.ts)
        if len(ordered) <= lookback:
            continue
        latest = ordered[-1]
        past = ordered[-1 - lookback]
        rows.append(
            ScreenRow(
                symbol=symbol.upper(),
                market=latest.market,
                as_of=latest.ts,
                close=latest.close,
                lookback_return=(latest.close / past.close) - 1.0,
                rows=len(ordered),
            )
        )
    return sorted(rows, key=lambda row: row.lookback_return, reverse=True)


def run_momentum_rotation_backtest(
    bars_by_symbol: dict[str, list[PriceBar]],
    lookback: int = 126,
    top_n: int = 3,
    initial_cash: float = 10_000.0,
    rebalance_days: int = 21,
    fee_bps: float = 0.0,
    benchmark_bars: list[PriceBar] | None = None,
    universe_members: list[UniverseMember] | None = None,
    delisting_returns: list[DelistingReturn] | None = None,
) -> PortfolioBacktestResult:
    if lookback < 1:
        raise ValueError("lookback must be >= 1")
    if top_n < 1:
        raise ValueError("top_n must be >= 1")
    if rebalance_days < 1:
        raise ValueError("rebalance_days must be >= 1")
    close_by_symbol = {
        symbol.upper(): {bar.ts: bar.close for bar in sorted(bars, key=lambda item: item.ts)}
        for symbol, bars in bars_by_symbol.items()
        if bars
    }
    if not close_by_symbol:
        raise ValueError("no bars supplied")

    first_bar = next(bar for bars in bars_by_symbol.values() for bar in bars)
    benchmark_closes = _benchmark_closes_by_date(benchmark_bars)
    membership_by_symbol = _membership_by_symbol(universe_members)
    delistings_by_symbol = _delistings_by_symbol(delisting_returns)
    if membership_by_symbol:
        date_sets = [set(values) for values in close_by_symbol.values()]
        date_source: set[date] = set(benchmark_closes) if benchmark_closes else set().union(*date_sets)
        common_dates = sorted(date_source)
    else:
        common_dates = sorted(set.intersection(*(set(values) for values in close_by_symbol.values())))
    if len(common_dates) <= lookback + 1:
        raise ValueError(f"not enough common dates for lookback={lookback}; got {len(common_dates)}")
    benchmark_symbol = "Equal-weight universe"
    benchmark_market = first_bar.market
    if benchmark_bars:
        first_benchmark = sorted(benchmark_bars, key=lambda bar: bar.ts)[0]
        benchmark_symbol = first_benchmark.symbol
        benchmark_market = first_benchmark.market

    equity = initial_cash
    benchmark_equity = initial_cash
    curve: list[PortfolioPoint] = [
        PortfolioPoint(common_dates[lookback], equity, benchmark_equity, (), 0.0, 0.0, 0.0)
    ]
    returns: list[float] = []
    benchmark_returns: list[float] = []
    holding_counts: list[int] = []
    eligible_counts: list[int] = []
    rebalance_count = 0
    current_holdings: tuple[str, ...] = ()
    total_cost = 0.0
    delisting_returns_applied = 0

    for index in range(lookback, len(common_dates) - 1):
        today = common_dates[index]
        prior = common_dates[index - lookback]
        tomorrow = common_dates[index + 1]
        turnover = 0.0
        if (index - lookback) % rebalance_days == 0:
            scores = []
            eligible_symbols = _eligible_symbols(membership_by_symbol, today) or tuple(close_by_symbol)
            eligible_counts.append(len(eligible_symbols))
            for symbol in eligible_symbols:
                closes = close_by_symbol.get(symbol)
                if closes is None or today not in closes or prior not in closes:
                    continue
                score = (closes[today] / closes[prior]) - 1.0
                if score > 0:
                    scores.append((score, symbol))
            next_holdings = tuple(symbol for _, symbol in sorted(scores, reverse=True)[:top_n])
            turnover = _portfolio_turnover(current_holdings, next_holdings)
            if next_holdings != current_holdings:
                rebalance_count += 1
                current_holdings = next_holdings

        holdings = current_holdings
        cost = turnover * fee_bps / 10_000
        if holdings:
            holding_returns = []
            exited_symbols: list[str] = []
            for symbol in holdings:
                holding_return, exited = _holding_return(
                    close_by_symbol,
                    delistings_by_symbol,
                    symbol,
                    today,
                    tomorrow,
                )
                holding_returns.append(holding_return)
                if exited:
                    exited_symbols.append(symbol)
            portfolio_return = mean(holding_returns) - cost
        else:
            exited_symbols = []
            portfolio_return = -cost
        if benchmark_closes:
            benchmark_return = _benchmark_return(benchmark_closes, today, tomorrow)
        else:
            benchmark_returns_for_day = [
                (closes[tomorrow] / closes[today]) - 1.0
                for closes in close_by_symbol.values()
                if today in closes and tomorrow in closes
            ]
            if not benchmark_returns_for_day:
                raise ValueError(f"no benchmarkable bars for {today} to {tomorrow}")
            benchmark_return = mean(benchmark_returns_for_day)
        equity *= 1.0 + portfolio_return
        benchmark_equity *= 1.0 + benchmark_return
        total_cost += cost
        if exited_symbols:
            delisting_returns_applied += len(exited_symbols)
            current_holdings = tuple(symbol for symbol in current_holdings if symbol not in exited_symbols)
        returns.append(portfolio_return)
        benchmark_returns.append(benchmark_return)
        holding_counts.append(len(holdings))
        curve.append(
            PortfolioPoint(
                tomorrow,
                equity,
                benchmark_equity,
                holdings,
                portfolio_return,
                benchmark_return,
                cost,
            )
        )

    total_return = (equity / initial_cash) - 1.0
    benchmark_return = (benchmark_equity / initial_cash) - 1.0
    years = max((common_dates[-1] - common_dates[lookback]).days / 365.25, 1 / TRADING_DAYS)
    annualized_return = (equity / initial_cash) ** (1 / years) - 1.0
    benchmark_annualized_return = (benchmark_equity / initial_cash) ** (1 / years) - 1.0
    return PortfolioBacktestResult(
        symbols=tuple(sorted(close_by_symbol)),
        universe_mode="point-in-time" if membership_by_symbol else "static",
        universe_name=_universe_name(universe_members) if membership_by_symbol else "static-symbol-list",
        market=first_bar.market,
        start=common_dates[lookback],
        end=common_dates[-1],
        rows=len(common_dates) - lookback,
        lookback=lookback,
        top_n=top_n,
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
        sharpe=_sharpe(returns),
        benchmark_sharpe=_sharpe(benchmark_returns),
        max_drawdown=_max_drawdown([point.equity for point in curve]),
        benchmark_max_drawdown=_max_drawdown([point.benchmark_equity for point in curve]),
        rebalance_count=rebalance_count,
        average_holdings=mean(holding_counts) if holding_counts else 0.0,
        average_eligible_symbols=mean(eligible_counts) if eligible_counts else float(len(close_by_symbol)),
        delisting_returns_applied=delisting_returns_applied,
        ended_members_without_delisting=_ended_members_without_delisting(
            universe_members,
            delistings_by_symbol,
        ),
        rebalance_days=rebalance_days,
        fee_bps=fee_bps,
        total_cost=total_cost,
        equity_curve=curve,
        annual_returns=_annual_returns(curve),
    )


def format_screen_report(rows: list[ScreenRow]) -> str:
    lines = [
        "# Momentum Screen",
        "",
        "| Rank | Symbol | Market | As Of | Close | Lookback Return | Rows |",
        "|---:|---|---|---|---:|---:|---:|",
    ]
    for rank, row in enumerate(rows, start=1):
        lines.append(
            f"| {rank} | {row.symbol} | {row.market} | {row.as_of} | "
            f"{row.close:.2f} | {row.lookback_return * 100:+.2f}% | {row.rows} |"
        )
    return "\n".join(lines)


def format_portfolio_report(result: PortfolioBacktestResult) -> str:
    benchmark_label = (
        result.benchmark_symbol
        if result.benchmark_symbol == "Equal-weight universe"
        else f"{result.benchmark_symbol} ({result.benchmark_market})"
    )
    lines = [
        "# Momentum Rotation Portfolio Backtest",
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
        f"| Lookback | {result.lookback} bars |",
        f"| Top N | {result.top_n} |",
        f"| Rebalance Every | {result.rebalance_days} bars |",
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
        f"| Benchmark Sharpe | {result.benchmark_sharpe:.2f} |",
        f"| Max Drawdown | {result.max_drawdown * 100:.2f}% |",
        f"| Benchmark Max Drawdown | {result.benchmark_max_drawdown * 100:.2f}% |",
        f"| Rebalances | {result.rebalance_count} |",
        f"| Average Holdings | {result.average_holdings:.2f} |",
        f"| Average Eligible Symbols | {result.average_eligible_symbols:.2f} |",
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
            "| Date | Equity | Benchmark | Holdings | Cost | Day Return |",
            "|---|---:|---:|---|---:|---:|",
        ]
    )
    for point in result.equity_curve[-5:]:
        holdings = ", ".join(point.holdings) if point.holdings else "Cash"
        lines.append(
            f"| {point.ts} | {point.equity:,.2f} | {point.benchmark_equity:,.2f} | "
            f"{holdings} | {point.cost * 100:.3f}% | {point.portfolio_return * 100:+.2f}% |"
        )
    lines.extend(
        [
            "",
            "## Bias Control",
            "",
            _bias_note(result),
        ]
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


def _annual_returns(curve: list[PortfolioPoint]) -> list[AnnualReturn]:
    by_year: dict[int, list[PortfolioPoint]] = {}
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
            if any(_active_member(member, as_of) for member in memberships)
        )
    )


def _active_member(member: UniverseMember, as_of: date) -> bool:
    return member.start_date <= as_of and (member.end_date is None or as_of <= member.end_date)


def _universe_name(universe_members: list[UniverseMember] | None) -> str:
    if not universe_members:
        return "static-symbol-list"
    names = sorted({member.universe for member in universe_members})
    return ",".join(names)


def _delistings_by_symbol(
    delisting_returns: list[DelistingReturn] | None,
) -> dict[str, tuple[DelistingReturn, ...]]:
    if not delisting_returns:
        return {}
    rows: dict[str, list[DelistingReturn]] = {}
    for item in delisting_returns:
        rows.setdefault(item.symbol.upper(), []).append(item)
    return {
        symbol: tuple(sorted(items, key=lambda item: item.ts))
        for symbol, items in rows.items()
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


def _bias_note(result: PortfolioBacktestResult) -> str:
    if result.universe_mode == "point-in-time":
        note = (
            "Point-in-time universe membership was enforced at each rebalance. "
            "Delisting returns are applied when a held symbol has no next price."
        )
        if result.ended_members_without_delisting:
            return (
                f"{note} Warning: {result.ended_members_without_delisting} ended universe member(s) "
                "do not have explicit delisting returns, so stock-universe evidence remains incomplete."
            )
        return note
    return (
        "Static symbol list was used. This report is not survivorship-free and must not be treated "
        "as high-confidence stock-universe evidence."
    )


def _portfolio_turnover(before: tuple[str, ...], after: tuple[str, ...]) -> float:
    if not before and not after:
        return 0.0
    before_weight = 1 / len(before) if before else 0.0
    after_weight = 1 / len(after) if after else 0.0
    symbols = set(before) | set(after)
    return sum(
        abs((after_weight if symbol in after else 0.0) - (before_weight if symbol in before else 0.0))
        for symbol in symbols
    )


def _benchmark_closes_by_date(bars: list[PriceBar] | None) -> dict[date, float]:
    if not bars:
        return {}
    return {bar.ts: bar.close for bar in sorted(bars, key=lambda item: item.ts)}


def _benchmark_return(closes: dict[date, float], previous_ts: date, current_ts: date) -> float:
    try:
        previous_close = closes[previous_ts]
        current_close = closes[current_ts]
    except KeyError as exc:
        raise ValueError(
            f"benchmark is missing bars for {previous_ts} to {current_ts}"
        ) from exc
    return (current_close / previous_close) - 1.0
