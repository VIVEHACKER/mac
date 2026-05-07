from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from io import StringIO
from statistics import mean, median

from .industry_rotation import PriceHistoryProvider, PricePoint, YahooHistoryProvider
from .macro import (
    FredCsvProvider,
    FredSeries,
    MacroDataProvider,
    MacroObservation,
    observation_on_or_before,
    subtract_months,
)


@dataclass(frozen=True)
class Condition:
    label: str
    operator: str
    threshold: float

    def matches(self, value: float) -> bool:
        if self.operator == ">=":
            return value >= self.threshold
        if self.operator == ">":
            return value > self.threshold
        if self.operator == "<=":
            return value <= self.threshold
        if self.operator == "<":
            return value < self.threshold
        raise ValueError(f"Unsupported condition operator: {self.operator}")


@dataclass(frozen=True)
class OutcomeRule:
    kind: str
    threshold: float = 0.0

    @property
    def label(self) -> str:
        if self.kind == "positive_return":
            return f"Forward return > {self.threshold:.2f}%"
        if self.kind == "return_at_least":
            return f"Forward return >= {self.threshold:.2f}%"
        if self.kind == "drawdown_at_or_below":
            return f"Max drawdown <= {self.threshold:.2f}%"
        raise ValueError(f"Unsupported outcome kind: {self.kind}")

    def wins(self, forward_return: float, max_drawdown: float) -> bool:
        if self.kind == "positive_return":
            return forward_return > self.threshold
        if self.kind == "return_at_least":
            return forward_return >= self.threshold
        if self.kind == "drawdown_at_or_below":
            return max_drawdown <= self.threshold
        raise ValueError(f"Unsupported outcome kind: {self.kind}")


@dataclass(frozen=True)
class PatternSpec:
    condition_label: str
    condition: Condition
    asset: str
    horizon_days: int
    outcome: OutcomeRule
    series_id: str = ""
    transform: str = "level"
    transform_months: int = 0
    series_kind: str = "macro"


@dataclass(frozen=True)
class PatternTrial:
    event_date: date
    entry_date: date
    exit_date: date
    forward_return: float
    max_drawdown: float
    win: bool


@dataclass(frozen=True)
class PatternResult:
    condition: str
    series_id: str
    asset: str
    horizon_days: int
    outcome_label: str
    samples: int
    wins: int
    win_rate: float
    wilson_lower_95: float
    average_return: float
    median_return: float
    best_return: float
    worst_return: float
    worst_drawdown: float
    historical_perfect: bool
    read: str
    sources: tuple[str, ...]
    trials: tuple[PatternTrial, ...]


@dataclass(frozen=True)
class PatternMiningReport:
    as_of: date | None
    results: tuple[PatternResult, ...]
    errors: tuple[str, ...]
    hypotheses_tested: int
    min_samples: int
    assets: tuple[str, ...]
    horizons: tuple[int, ...]


CORE_ASSETS = ("SPY", "QQQ", "IWM")
BOND_ASSETS = ("SHY", "IEF", "TLT", "TIP", "LQD", "HYG")
EMERGING_MARKET_BOND_ASSETS = ("EMB", "VWOB", "PCY", "EMLC", "EMHY", "HYEM", "EMBD")
CASH_LIKE_ASSETS = ("SGOV", "BIL", "SHV", "TBIL", "USFR", "TFLO", "ICSH", "MINT", "JPST", "FLOT")
PRECIOUS_METALS_ASSETS = ("GLD", "SLV", "GDX", "SIL", "PPLT", "PALL", "GC=F", "SI=F")
ENERGY_ASSETS = ("USO", "BNO", "UNG", "UGA", "XLE", "XOP", "OIH", "CL=F", "BZ=F", "NG=F", "HO=F", "RB=F")
INDUSTRIAL_METALS_ASSETS = ("CPER", "COPX", "PICK", "XME", "DBB", "HG=F", "ALI=F")
CRITICAL_MINERALS_ASSETS = ("REMX", "URA", "URNM", "LIT", "LITP", "SETM")
COAL_ASSETS = ("BTU", "CNR", "AMR", "HCC", "SXC", "NRP")
AGRICULTURE_ASSETS = ("DBA", "CORN", "WEAT", "SOYB", "ZC=F", "ZW=F", "ZS=F")
COMMODITY_ASSETS = tuple(
    dict.fromkeys(
        PRECIOUS_METALS_ASSETS
        + ENERGY_ASSETS
        + INDUSTRIAL_METALS_ASSETS
        + CRITICAL_MINERALS_ASSETS
        + COAL_ASSETS
        + AGRICULTURE_ASSETS
    )
)
DEFAULT_ASSETS = ("SPY", "QQQ", "IWM", "TLT", "GLD")
ASSET_SETS = {
    "core": DEFAULT_ASSETS,
    "equity": CORE_ASSETS,
    "bonds": BOND_ASSETS,
    "em_bonds": EMERGING_MARKET_BOND_ASSETS,
    "cash": CASH_LIKE_ASSETS,
    "mmf": CASH_LIKE_ASSETS,
    "precious": PRECIOUS_METALS_ASSETS,
    "energy": ENERGY_ASSETS,
    "metals": INDUSTRIAL_METALS_ASSETS,
    "critical_minerals": CRITICAL_MINERALS_ASSETS,
    "coal": COAL_ASSETS,
    "agriculture": AGRICULTURE_ASSETS,
    "commodities": COMMODITY_ASSETS,
    "macro": tuple(dict.fromkeys(CORE_ASSETS + BOND_ASSETS + EMERGING_MARKET_BOND_ASSETS + CASH_LIKE_ASSETS + COMMODITY_ASSETS)),
    "all": tuple(dict.fromkeys(CORE_ASSETS + BOND_ASSETS + EMERGING_MARKET_BOND_ASSETS + CASH_LIKE_ASSETS + COMMODITY_ASSETS)),
}
DEFAULT_HORIZONS = (21, 63, 126, 252)
VIX_THRESHOLDS = (80.0, 60.0, 50.0, 40.0, 30.0)
RESERVE_CURRENCY_SPECS = (
    ("Euro", "EURUSD=X", "percent_change", 5.0),
    ("Pound", "GBPUSD=X", "percent_change", 5.0),
    ("Yen", "JPY=X", "inverse_percent_change", 5.0),
    ("Swiss Franc", "CHF=X", "inverse_percent_change", 5.0),
    ("Yuan", "CNY=X", "inverse_percent_change", 3.0),
)
CORN_THRESHOLDS = (20.0, 10.0)


def condition_event_dates(series: FredSeries, condition: Condition) -> tuple[date, ...]:
    events: list[date] = []
    was_true = False
    for observation in series.observations:
        is_true = condition.matches(observation.value)
        if is_true and not was_true:
            events.append(observation.observed_at)
        was_true = is_true
    return tuple(events)


def evaluate_pattern(
    spec: PatternSpec,
    condition_series: FredSeries,
    prices: tuple[PricePoint, ...],
    min_samples: int = 5,
) -> PatternResult:
    sorted_prices = tuple(sorted(prices, key=lambda item: item.observed_at))
    trials: list[PatternTrial] = []
    for event_date in condition_event_dates(condition_series, spec.condition):
        entry_index = first_price_index_on_or_after(sorted_prices, event_date)
        if entry_index is None:
            continue
        exit_index = entry_index + spec.horizon_days
        if exit_index >= len(sorted_prices):
            continue
        entry = sorted_prices[entry_index]
        exit_point = sorted_prices[exit_index]
        if entry.close == 0:
            continue
        window = sorted_prices[entry_index : exit_index + 1]
        forward_return = (exit_point.close - entry.close) / entry.close * 100.0
        max_drawdown = min((point.close - entry.close) / entry.close * 100.0 for point in window)
        trials.append(
            PatternTrial(
                event_date=event_date,
                entry_date=entry.observed_at,
                exit_date=exit_point.observed_at,
                forward_return=forward_return,
                max_drawdown=max_drawdown,
                win=spec.outcome.wins(forward_return, max_drawdown),
            )
        )

    samples = len(trials)
    wins = sum(1 for trial in trials if trial.win)
    returns = [trial.forward_return for trial in trials]
    win_rate = wins / samples * 100.0 if samples else 0.0
    lower = wilson_lower_bound(wins, samples)
    historical_perfect = samples >= min_samples and samples > 0 and wins == samples
    read = pattern_read(samples, wins, win_rate, lower, historical_perfect, min_samples)
    return PatternResult(
        condition=spec.condition_label,
        series_id=condition_series.series_id,
        asset=spec.asset.upper(),
        horizon_days=spec.horizon_days,
        outcome_label=spec.outcome.label,
        samples=samples,
        wins=wins,
        win_rate=win_rate,
        wilson_lower_95=lower,
        average_return=mean(returns) if returns else 0.0,
        median_return=median(returns) if returns else 0.0,
        best_return=max(returns) if returns else 0.0,
        worst_return=min(returns) if returns else 0.0,
        worst_drawdown=min((trial.max_drawdown for trial in trials), default=0.0),
        historical_perfect=historical_perfect,
        read=read,
        sources=tuple(sorted({condition_series.source, yahoo_history_source(spec.asset)})),
        trials=tuple(trials),
    )


def mine_default_patterns(
    macro_provider: MacroDataProvider | None = None,
    history_provider: PriceHistoryProvider | None = None,
    assets: tuple[str, ...] = DEFAULT_ASSETS,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    min_samples: int = 5,
) -> PatternMiningReport:
    macro = macro_provider or FredCsvProvider()
    history = history_provider or YahooHistoryProvider()
    normalized_assets = tuple(asset.strip().upper() for asset in assets if asset.strip())
    normalized_horizons = tuple(int(horizon) for horizon in horizons if int(horizon) > 0)
    results: list[PatternResult] = []
    errors: list[str] = []
    series_cache: dict[str, FredSeries] = {}
    price_cache: dict[str, tuple[PricePoint, ...]] = {}

    specs = default_pattern_specs(normalized_assets, normalized_horizons)
    for spec in specs:
        try:
            condition_series = condition_series_for_spec(spec, macro, history, series_cache, price_cache)
            prices = price_cache.get(spec.asset)
            if prices is None:
                prices = history.history(spec.asset, range_period="max", interval="1d")
                price_cache[spec.asset] = prices
            results.append(evaluate_pattern(spec, condition_series, prices, min_samples=min_samples))
        except Exception as exc:
            errors.append(f"{spec.condition_label} / {spec.asset} / {spec.horizon_days}d: {exc}")

    ranked = tuple(
        sorted(
            results,
            key=lambda result: (
                result.historical_perfect,
                result.wilson_lower_95,
                result.samples,
                result.average_return,
            ),
            reverse=True,
        )
    )
    latest_dates = [points[-1].observed_at for points in price_cache.values() if points]
    return PatternMiningReport(
        as_of=max(latest_dates) if latest_dates else None,
        results=ranked,
        errors=tuple(errors),
        hypotheses_tested=len(specs),
        min_samples=min_samples,
        assets=normalized_assets,
        horizons=normalized_horizons,
    )


def default_pattern_specs(assets: tuple[str, ...], horizons: tuple[int, ...]) -> tuple[PatternSpec, ...]:
    specs: list[PatternSpec] = []
    for asset in assets:
        for horizon in horizons:
            for threshold in VIX_THRESHOLDS:
                label = f"VIX >= {threshold:.0f}"
                specs.append(
                    PatternSpec(
                        condition_label=label,
                        condition=Condition(label, ">=", threshold),
                        asset=asset,
                        horizon_days=horizon,
                        outcome=OutcomeRule("positive_return"),
                        series_id="VIXCLS",
                    )
                )
            label = "10Y-2Y < 0"
            specs.append(
                PatternSpec(
                    condition_label=label,
                    condition=Condition(label, "<", 0.0),
                    asset=asset,
                    horizon_days=horizon,
                    outcome=OutcomeRule("drawdown_at_or_below", threshold=-10.0),
                    series_id="T10Y2Y",
                )
            )
            specs.extend(
                (
                    PatternSpec(
                        condition_label="CPI YoY >= 3",
                        condition=Condition("CPI YoY >= 3", ">=", 3.0),
                        asset=asset,
                        horizon_days=horizon,
                        outcome=OutcomeRule("positive_return"),
                        series_id="CPIAUCSL",
                        transform="percent_change",
                        transform_months=12,
                    ),
                    PatternSpec(
                        condition_label="CPI YoY >= 5",
                        condition=Condition("CPI YoY >= 5", ">=", 5.0),
                        asset=asset,
                        horizon_days=horizon,
                        outcome=OutcomeRule("positive_return"),
                        series_id="CPIAUCSL",
                        transform="percent_change",
                        transform_months=12,
                    ),
                    PatternSpec(
                        condition_label="Fed Funds 6M Change >= 1",
                        condition=Condition("Fed Funds 6M Change >= 1", ">=", 1.0),
                        asset=asset,
                        horizon_days=horizon,
                        outcome=OutcomeRule("positive_return"),
                        series_id="FEDFUNDS",
                        transform="change",
                        transform_months=6,
                    ),
                    PatternSpec(
                        condition_label="Fed Funds 6M Change <= -1",
                        condition=Condition("Fed Funds 6M Change <= -1", "<=", -1.0),
                        asset=asset,
                        horizon_days=horizon,
                        outcome=OutcomeRule("positive_return"),
                        series_id="FEDFUNDS",
                        transform="change",
                        transform_months=6,
                    ),
                    PatternSpec(
                        condition_label="Dollar 6M Change >= 5",
                        condition=Condition("Dollar 6M Change >= 5", ">=", 5.0),
                        asset=asset,
                        horizon_days=horizon,
                        outcome=OutcomeRule("positive_return"),
                        series_id="DTWEXBGS",
                        transform="percent_change",
                        transform_months=6,
                    ),
                    PatternSpec(
                        condition_label="Dollar 6M Change <= -5",
                        condition=Condition("Dollar 6M Change <= -5", "<=", -5.0),
                        asset=asset,
                        horizon_days=horizon,
                        outcome=OutcomeRule("positive_return"),
                        series_id="DTWEXBGS",
                        transform="percent_change",
                        transform_months=6,
                    ),
                    PatternSpec(
                        condition_label="Unemployment 6M Change >= 0.5",
                        condition=Condition("Unemployment 6M Change >= 0.5", ">=", 0.5),
                        asset=asset,
                        horizon_days=horizon,
                        outcome=OutcomeRule("positive_return"),
                        series_id="UNRATE",
                        transform="change",
                        transform_months=6,
                    ),
                )
            )
            specs.extend(currency_strength_pattern_specs(asset, horizon))
            specs.extend(corn_price_pattern_specs(asset, horizon))
    return tuple(specs)


def expand_asset_set(name: str) -> tuple[str, ...]:
    key = name.strip().lower().replace("-", "_")
    try:
        return ASSET_SETS[key]
    except KeyError as exc:
        raise ValueError(f"Unknown asset set: {name}") from exc


def transformed_condition_series(series: FredSeries, spec: PatternSpec) -> FredSeries:
    if spec.transform == "level":
        return series
    if spec.transform == "percent_change":
        return derived_percent_change_series(series, months=spec.transform_months, label=spec.condition_label)
    if spec.transform == "inverse_percent_change":
        return derived_inverse_percent_change_series(series, months=spec.transform_months, label=spec.condition_label)
    if spec.transform == "change":
        return derived_change_series(series, months=spec.transform_months, label=spec.condition_label)
    raise ValueError(f"Unsupported transform: {spec.transform}")


def derived_percent_change_series(series: FredSeries, months: int, label: str) -> FredSeries:
    observations: list[MacroObservation] = []
    for observation in series.observations:
        try:
            prior = observation_on_or_before(series, subtract_months(observation.observed_at, months))
        except Exception:
            continue
        if prior.value == 0:
            continue
        value = (observation.value - prior.value) / prior.value * 100.0
        observations.append(
            MacroObservation(
                f"{series.series_id}_YOY_{months}M" if months == 12 else f"{series.series_id}_PCT_{months}M",
                observation.observed_at,
                value,
            )
        )
    return FredSeries(
        series_id=f"{series.series_id}_YOY_{months}M" if months == 12 else f"{series.series_id}_PCT_{months}M",
        name=label,
        source=series.source,
        observations=tuple(observations),
    )


def derived_inverse_percent_change_series(series: FredSeries, months: int, label: str) -> FredSeries:
    observations: list[MacroObservation] = []
    for observation in series.observations:
        try:
            prior = observation_on_or_before(series, subtract_months(observation.observed_at, months))
        except Exception:
            continue
        if prior.value == 0 or observation.value == 0:
            continue
        value = ((1.0 / observation.value) - (1.0 / prior.value)) / (1.0 / prior.value) * 100.0
        observations.append(
            MacroObservation(
                f"{series.series_id}_INV_PCT_{months}M",
                observation.observed_at,
                value,
            )
        )
    return FredSeries(
        series_id=f"{series.series_id}_INV_PCT_{months}M",
        name=label,
        source=series.source,
        observations=tuple(observations),
    )


def derived_change_series(series: FredSeries, months: int, label: str) -> FredSeries:
    observations: list[MacroObservation] = []
    for observation in series.observations:
        try:
            prior = observation_on_or_before(series, subtract_months(observation.observed_at, months))
        except Exception:
            continue
        observations.append(
            MacroObservation(
                f"{series.series_id}_CHG_{months}M",
                observation.observed_at,
                observation.value - prior.value,
            )
        )
    return FredSeries(
        series_id=f"{series.series_id}_CHG_{months}M",
        name=label,
        source=series.source,
        observations=tuple(observations),
    )


def currency_strength_pattern_specs(asset: str, horizon: int) -> tuple[PatternSpec, ...]:
    specs: list[PatternSpec] = []
    for currency_name, series_id, transform, threshold in RESERVE_CURRENCY_SPECS:
        for operator, signed_threshold in ((">=", threshold), ("<=", -threshold)):
            label = f"{currency_name} 6M Strength {operator} {signed_threshold:.0f}"
            specs.append(
                PatternSpec(
                    condition_label=label,
                    condition=Condition(label, operator, signed_threshold),
                    asset=asset,
                    horizon_days=horizon,
                    outcome=OutcomeRule("positive_return"),
                    series_id=series_id,
                    transform=transform,
                    transform_months=6,
                    series_kind="price",
                )
            )
    return tuple(specs)


def corn_price_pattern_specs(asset: str, horizon: int) -> tuple[PatternSpec, ...]:
    specs: list[PatternSpec] = []
    for threshold in CORN_THRESHOLDS:
        for operator, signed_threshold in ((">=", threshold), ("<=", -threshold)):
            label = f"Corn 6M Change {operator} {signed_threshold:.0f}"
            specs.append(
                PatternSpec(
                    condition_label=label,
                    condition=Condition(label, operator, signed_threshold),
                    asset=asset,
                    horizon_days=horizon,
                    outcome=OutcomeRule("positive_return"),
                    series_id="ZC=F",
                    transform="percent_change",
                    transform_months=6,
                    series_kind="price",
                )
            )
    return tuple(specs)


def condition_series_for_spec(
    spec: PatternSpec,
    macro: MacroDataProvider,
    history: PriceHistoryProvider,
    series_cache: dict[str, FredSeries],
    price_cache: dict[str, tuple[PricePoint, ...]],
) -> FredSeries:
    if spec.series_kind == "macro":
        series = series_cache.get(spec.series_id)
        if series is None:
            series = macro.series(spec.series_id)
            series_cache[spec.series_id] = series
        return transformed_condition_series(series, spec)
    if spec.series_kind == "price":
        prices = price_cache.get(spec.series_id)
        if prices is None:
            prices = history.history(spec.series_id, range_period="max", interval="1d")
            price_cache[spec.series_id] = prices
        return transformed_condition_series(price_points_to_series(spec.series_id, prices), spec)
    raise ValueError(f"Unsupported series kind: {spec.series_kind}")


def price_points_to_series(symbol: str, prices: tuple[PricePoint, ...]) -> FredSeries:
    normalized = symbol.upper()
    observations = tuple(
        MacroObservation(normalized, point.observed_at, point.close)
        for point in sorted(prices, key=lambda item: item.observed_at)
    )
    if not observations:
        raise ValueError(f"{normalized}: no price observations")
    return FredSeries(
        series_id=normalized,
        name=normalized,
        source=yahoo_history_source(normalized),
        observations=observations,
    )


def first_price_index_on_or_after(points: tuple[PricePoint, ...], target: date) -> int | None:
    for index, point in enumerate(points):
        if point.observed_at >= target:
            return index
    return None


def wilson_lower_bound(wins: int, samples: int, z: float = 1.96) -> float:
    if samples <= 0:
        return 0.0
    p_hat = wins / samples
    denominator = 1.0 + z * z / samples
    center = p_hat + z * z / (2.0 * samples)
    margin = z * ((p_hat * (1.0 - p_hat) + z * z / (4.0 * samples)) / samples) ** 0.5
    return (center - margin) / denominator * 100.0


def pattern_read(
    samples: int,
    wins: int,
    win_rate: float,
    wilson_lower_95: float,
    historical_perfect: bool,
    min_samples: int,
) -> str:
    if samples < min_samples:
        return (
            f"Insufficient sample: {samples} observed cases; minimum configured sample is {min_samples}. "
            "Do not treat this as a pattern."
        )
    if historical_perfect:
        return (
            f"Historical perfect sample: {wins}/{samples} wins. This is not a guarantee; "
            f"the 95% Wilson lower bound is {wilson_lower_95:.2f}%."
        )
    if win_rate >= 80.0:
        return (
            f"High historical hit rate: {wins}/{samples} wins. The 95% Wilson lower bound is "
            f"{wilson_lower_95:.2f}%, so size it as probabilistic evidence."
        )
    return (
        f"Mixed historical evidence: {wins}/{samples} wins. The 95% Wilson lower bound is "
        f"{wilson_lower_95:.2f}%."
    )


def format_pattern_report(report: PatternMiningReport, limit: int = 25) -> str:
    lines = [
        f"# Historical Pattern Mining{f' - {report.as_of.isoformat()}' if report.as_of else ''}",
        "",
        "Not investment advice. These are historical conditional statistics, not forecasts or guarantees.",
        "",
        "## Method",
        "- A sample is counted only when the macro condition starts, not on every day of a continuous regime.",
        "- Win rate is paired with the 95% Wilson lower bound to penalize small samples.",
        "- Multiple-testing risk is real: many hypotheses can produce a perfect historical sample by chance.",
        "",
        "## Search Space",
        f"- Assets: {', '.join(report.assets) if report.assets else 'None'}",
        f"- Horizons: {', '.join(str(horizon) for horizon in report.horizons)} trading days",
        f"- Hypotheses tested: {report.hypotheses_tested}",
        f"- Minimum sample for a reported perfect pattern: {report.min_samples}",
        "",
        "## Ranked Patterns",
        "| Condition | Asset | Horizon | Outcome | Samples | Wins | Win Rate | Wilson 95% Lower | Avg Return | Worst Return | Worst Drawdown | Read |",
        "|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    shown = report.results[: max(limit, 0)]
    if not shown:
        lines.append("| n/a | n/a | 0 | n/a | 0 | 0 | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | No patterns evaluated. |")
    for result in shown:
        lines.append(
            f"| {result.condition} | {result.asset} | {result.horizon_days} | "
            f"{result.outcome_label} | {result.samples} | {result.wins} | "
            f"{result.win_rate:.2f}% | {result.wilson_lower_95:.2f}% | "
            f"{result.average_return:+.2f}% | {result.worst_return:+.2f}% | "
            f"{result.worst_drawdown:+.2f}% | {result.read} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation Rules",
            "- A 100% historical win rate is only a description of the observed sample.",
            "- Prefer patterns with higher sample counts and stronger Wilson lower bounds.",
            "- Check whether the condition has an economic mechanism before using it in a thesis.",
            "- Retest on out-of-sample periods before increasing position size.",
            "",
            "## Sources",
        ]
    )
    sources = sorted({source for result in report.results for source in result.sources})
    if sources:
        lines.extend(f"- {source}" for source in sources)
    else:
        lines.append("- None")
    if report.errors:
        lines.extend(["", "## Data Gaps"])
        lines.extend(f"- {error}" for error in report.errors)
    return "\n".join(lines)


def pattern_results_to_csv(report: PatternMiningReport) -> str:
    output = StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(
        [
            "condition",
            "asset",
            "horizon_days",
            "outcome",
            "samples",
            "wins",
            "win_rate",
            "wilson_lower_95",
            "average_return",
            "median_return",
            "best_return",
            "worst_return",
            "worst_drawdown",
            "historical_perfect",
            "read",
            "sources",
        ]
    )
    for result in report.results:
        writer.writerow(
            [
                result.condition,
                result.asset,
                result.horizon_days,
                result.outcome_label,
                result.samples,
                result.wins,
                f"{result.win_rate:.4f}",
                f"{result.wilson_lower_95:.4f}",
                f"{result.average_return:.4f}",
                f"{result.median_return:.4f}",
                f"{result.best_return:.4f}",
                f"{result.worst_return:.4f}",
                f"{result.worst_drawdown:.4f}",
                str(result.historical_perfect).lower(),
                result.read,
                "; ".join(result.sources),
            ]
        )
    return output.getvalue()


def yahoo_history_source(symbol: str) -> str:
    return (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol.upper()}"
        "?period1=0&period2=current&interval=1d"
    )
