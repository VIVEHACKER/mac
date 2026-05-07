from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from io import StringIO
from typing import Callable, Protocol
from urllib.parse import quote
from urllib.request import Request, urlopen


DEFAULT_SERIES_NAMES = {
    "CPIAUCSL": "Consumer Price Index",
    "CPILFESL": "Core Consumer Price Index",
    "UNRATE": "Unemployment Rate",
    "FEDFUNDS": "Effective Federal Funds Rate",
    "T10Y2Y": "10Y-2Y Treasury Spread",
    "INDPRO": "Industrial Production",
    "RSAFS": "Retail Sales",
}


class MacroDataProvider(Protocol):
    def series(self, series_id: str) -> "FredSeries":
        pass


@dataclass(frozen=True)
class MacroObservation:
    series_id: str
    observed_at: date
    value: float


@dataclass(frozen=True)
class FredSeries:
    series_id: str
    name: str
    source: str
    observations: tuple[MacroObservation, ...]


@dataclass(frozen=True)
class MacroMetric:
    label: str
    series_id: str
    value: float
    unit: str
    as_of: date
    trend: str
    source: str


@dataclass(frozen=True)
class MacroRegime:
    name: str
    summary: str
    watch_items: tuple[str, ...]
    portfolio_implications: tuple[str, ...]


@dataclass(frozen=True)
class MacroDashboard:
    as_of: date
    regime: MacroRegime
    inflation: tuple[MacroMetric, ...]
    labor_policy: tuple[MacroMetric, ...]
    growth_cycle: tuple[MacroMetric, ...]
    sources: tuple[str, ...]


class MacroDataError(RuntimeError):
    pass


class FredCsvProvider:
    BASE_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"

    def __init__(
        self,
        fetch_text: Callable[[str], str] | None = None,
        series_names: dict[str, str] | None = None,
    ):
        self.fetch_text = fetch_text or default_fetch_text
        self.series_names = series_names or DEFAULT_SERIES_NAMES

    def series(self, series_id: str) -> FredSeries:
        normalized = series_id.strip().upper()
        source = f"{self.BASE_URL}?id={quote(normalized)}"
        parsed = parse_fred_csv(self.fetch_text(source), normalized, source)
        return FredSeries(
            series_id=parsed.series_id,
            name=self.series_names.get(normalized, normalized),
            source=parsed.source,
            observations=parsed.observations,
        )


def build_macro_dashboard(provider: MacroDataProvider | None = None) -> MacroDashboard:
    provider = provider or FredCsvProvider()
    cpi = provider.series("CPIAUCSL")
    core_cpi = provider.series("CPILFESL")
    unemployment = provider.series("UNRATE")
    fed_funds = provider.series("FEDFUNDS")
    yield_curve = provider.series("T10Y2Y")
    industrial_production = provider.series("INDPRO")
    retail_sales = provider.series("RSAFS")

    headline_cpi_yoy = percent_change_since_months(cpi, 12)
    core_cpi_yoy = percent_change_since_months(core_cpi, 12)
    headline_cpi_6m_delta = safe_yoy_delta_since_months(cpi, 6)
    core_cpi_6m_delta = safe_yoy_delta_since_months(core_cpi, 6)
    unemployment_6m_delta = change_since_months(unemployment, 6)
    fed_funds_6m_delta = change_since_months(fed_funds, 6)
    yield_curve_spread = latest_observation(yield_curve).value
    industrial_production_yoy = percent_change_since_months(industrial_production, 12)
    retail_sales_yoy = percent_change_since_months(retail_sales, 12)

    regime = classify_macro_regime(
        headline_cpi_yoy=headline_cpi_yoy,
        core_cpi_yoy=core_cpi_yoy,
        headline_cpi_6m_delta=headline_cpi_6m_delta,
        unemployment_6m_delta=unemployment_6m_delta,
        fed_funds_6m_delta=fed_funds_6m_delta,
        yield_curve_spread=yield_curve_spread,
        industrial_production_yoy=industrial_production_yoy,
        retail_sales_yoy=retail_sales_yoy,
    )

    inflation = (
        MacroMetric(
            label=DEFAULT_SERIES_NAMES["CPIAUCSL"],
            series_id="CPIAUCSL",
            value=headline_cpi_yoy,
            unit="% YoY",
            as_of=latest_observation(cpi).observed_at,
            trend=format_ppt_trend(headline_cpi_6m_delta, "YoY vs 6m ago"),
            source=cpi.source,
        ),
        MacroMetric(
            label=DEFAULT_SERIES_NAMES["CPILFESL"],
            series_id="CPILFESL",
            value=core_cpi_yoy,
            unit="% YoY",
            as_of=latest_observation(core_cpi).observed_at,
            trend=format_ppt_trend(core_cpi_6m_delta, "YoY vs 6m ago"),
            source=core_cpi.source,
        ),
    )
    labor_policy = (
        MacroMetric(
            label=DEFAULT_SERIES_NAMES["UNRATE"],
            series_id="UNRATE",
            value=latest_observation(unemployment).value,
            unit="%",
            as_of=latest_observation(unemployment).observed_at,
            trend=format_ppt_trend(unemployment_6m_delta, "over 6m"),
            source=unemployment.source,
        ),
        MacroMetric(
            label=DEFAULT_SERIES_NAMES["FEDFUNDS"],
            series_id="FEDFUNDS",
            value=latest_observation(fed_funds).value,
            unit="%",
            as_of=latest_observation(fed_funds).observed_at,
            trend=format_ppt_trend(fed_funds_6m_delta, "over 6m"),
            source=fed_funds.source,
        ),
        MacroMetric(
            label=DEFAULT_SERIES_NAMES["T10Y2Y"],
            series_id="T10Y2Y",
            value=yield_curve_spread,
            unit="ppt",
            as_of=latest_observation(yield_curve).observed_at,
            trend=describe_yield_curve(yield_curve_spread),
            source=yield_curve.source,
        ),
    )
    growth_cycle = (
        MacroMetric(
            label=DEFAULT_SERIES_NAMES["INDPRO"],
            series_id="INDPRO",
            value=industrial_production_yoy,
            unit="% YoY",
            as_of=latest_observation(industrial_production).observed_at,
            trend=describe_growth(industrial_production_yoy),
            source=industrial_production.source,
        ),
        MacroMetric(
            label=DEFAULT_SERIES_NAMES["RSAFS"],
            series_id="RSAFS",
            value=retail_sales_yoy,
            unit="% YoY",
            as_of=latest_observation(retail_sales).observed_at,
            trend=describe_growth(retail_sales_yoy),
            source=retail_sales.source,
        ),
    )
    sources = tuple(
        sorted(
            {
                cpi.source,
                core_cpi.source,
                unemployment.source,
                fed_funds.source,
                yield_curve.source,
                industrial_production.source,
                retail_sales.source,
            }
        )
    )
    return MacroDashboard(
        as_of=max(metric.as_of for metric in inflation + labor_policy + growth_cycle),
        regime=regime,
        inflation=inflation,
        labor_policy=labor_policy,
        growth_cycle=growth_cycle,
        sources=sources,
    )


def classify_macro_regime(
    headline_cpi_yoy: float,
    core_cpi_yoy: float,
    headline_cpi_6m_delta: float,
    unemployment_6m_delta: float,
    fed_funds_6m_delta: float,
    yield_curve_spread: float,
    industrial_production_yoy: float,
    retail_sales_yoy: float,
) -> MacroRegime:
    inflation_hot = headline_cpi_yoy >= 3.0 or core_cpi_yoy >= 3.0
    inflation_cooling = headline_cpi_6m_delta <= -0.3
    inflation_rising = headline_cpi_6m_delta >= 0.3
    labor_weakening = unemployment_6m_delta >= 0.3
    policy_tightening = fed_funds_6m_delta >= 0.25
    curve_inverted = yield_curve_spread < 0.0
    growth_soft = industrial_production_yoy < 1.0 or retail_sales_yoy < 2.0
    growth_contracting = industrial_production_yoy < 0.0 or retail_sales_yoy < 0.0
    growth_expanding = industrial_production_yoy >= 1.0 and retail_sales_yoy >= 2.0

    if inflation_hot and (inflation_rising or policy_tightening or curve_inverted) and growth_soft:
        return MacroRegime(
            name="Late-Cycle Tightening / Stagflation Risk",
            summary=(
                "Inflation is still above the comfort zone while policy, curve, or growth "
                "signals point to restrictive conditions."
            ),
            watch_items=(
                "Inflation prints that re-accelerate instead of cooling.",
                "Unemployment and payroll revisions that confirm labor weakening.",
                "Credit spreads and earnings guidance for demand deterioration.",
                "Commodity shocks that keep input costs high.",
            ),
            portfolio_implications=(
                "Avoid treating leveraged growth exposure as a permanent allocation.",
                "Keep duration hedges under review, but respect rate-spike risk.",
                "Prefer balance-sheet quality and explicit thesis invalidation rules.",
                "Commodity exposure can help inflation shocks but can reverse if demand breaks.",
            ),
        )

    if inflation_cooling and (labor_weakening or growth_contracting or curve_inverted):
        return MacroRegime(
            name="Slowdown / Recession Risk",
            summary=(
                "Inflation is cooling while labor, curve, or growth indicators point to "
                "demand risk."
            ),
            watch_items=(
                "Jobless claims, payroll revisions, and unemployment trend.",
                "Earnings estimate cuts and management commentary on demand.",
                "Credit stress and liquidity conditions.",
            ),
            portfolio_implications=(
                "Reduce cyclical and levered beta until growth confirms a turn.",
                "Duration and cash buffers matter more than upside chasing.",
                "Commodity demand proxies can underperform even if supply stories look tight.",
            ),
        )

    if not inflation_hot and inflation_cooling and growth_expanding and not labor_weakening:
        return MacroRegime(
            name="Disinflationary Expansion",
            summary=(
                "Growth is still expanding while inflation pressure is easing, usually a "
                "more constructive backdrop for risk assets."
            ),
            watch_items=(
                "Whether inflation keeps falling without a labor-market break.",
                "Whether earnings breadth improves beyond a few mega-cap names.",
                "Whether the yield curve normalizes without credit stress.",
            ),
            portfolio_implications=(
                "Growth beta can work, but valuation and crowding still need discipline.",
                "Cyclicals can be screened for earnings revision strength.",
                "Keep hedges sized because regime transitions can be abrupt.",
            ),
        )

    if inflation_hot and growth_expanding:
        return MacroRegime(
            name="Inflationary Expansion",
            summary=(
                "Demand is still expanding while inflation is elevated, which can support "
                "nominal growth but raises rate and margin risk."
            ),
            watch_items=(
                "Wage growth, commodity prices, and margin pressure.",
                "Central-bank reaction to sticky inflation.",
                "Whether revenue growth is real-volume growth or mostly price.",
            ),
            portfolio_implications=(
                "Commodity and industrial screens deserve attention.",
                "Long-duration growth needs stricter rate-sensitivity checks.",
                "Pricing-power stocks deserve priority over margin-fragile names.",
            ),
        )

    return MacroRegime(
        name="Mixed / Transition Regime",
        summary=(
            "Macro signals are not aligned enough for a clean cycle label. Treat this as "
            "a transition state and require tighter confirmation."
        ),
        watch_items=(
            "Next CPI and core CPI print.",
            "Labor trend over the next two reports.",
            "Yield curve direction and credit spreads.",
            "Earnings revisions by sector.",
        ),
        portfolio_implications=(
            "Do not overfit one macro story to every position.",
            "Use smaller sizing and clearer invalidation points.",
            "Favor diversified exposure until the regime signal strengthens.",
        ),
    )


def format_macro_report(dashboard: MacroDashboard) -> str:
    lines = [
        f"# Macro Cycle Dashboard - {dashboard.as_of.isoformat()}",
        "",
        "Not investment advice. This is a macro research dashboard for human review.",
        "",
        "## Market Structure Read",
        f"Regime: {dashboard.regime.name}",
        "",
        dashboard.regime.summary,
        "",
        "## Inflation",
        format_metric_table(dashboard.inflation),
        "",
        "## Labor, Policy, and Yield Curve",
        format_metric_table(dashboard.labor_policy),
        "",
        "## Growth and Consumer Demand",
        format_metric_table(dashboard.growth_cycle),
        "",
        "## What To Watch",
    ]
    lines.extend(f"- {item}" for item in dashboard.regime.watch_items)
    lines.extend(["", "## Portfolio Research Implications"])
    lines.extend(f"- {item}" for item in dashboard.regime.portfolio_implications)
    lines.extend(["", "## Sources"])
    lines.extend(f"- {source}" for source in dashboard.sources)
    return "\n".join(lines)


def parse_fred_csv(text: str, series_id: str, source: str) -> FredSeries:
    normalized = series_id.strip().upper()
    reader = csv.DictReader(StringIO(text))
    if reader.fieldnames is None or "observation_date" not in reader.fieldnames:
        raise MacroDataError(f"{normalized}: FRED CSV is missing observation_date")
    if normalized not in reader.fieldnames:
        raise MacroDataError(f"{normalized}: FRED CSV is missing value column")

    observations: list[MacroObservation] = []
    for row in reader:
        value = parse_fred_value(row.get(normalized))
        if value is None:
            continue
        observed_at = date.fromisoformat(str(row["observation_date"]))
        observations.append(MacroObservation(normalized, observed_at, value))
    if not observations:
        raise MacroDataError(f"{normalized}: FRED CSV has no numeric observations")
    observations.sort(key=lambda item: item.observed_at)
    return FredSeries(
        series_id=normalized,
        name=DEFAULT_SERIES_NAMES.get(normalized, normalized),
        source=source,
        observations=tuple(observations),
    )


def parse_fred_value(value: str | None) -> float | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped or stripped == ".":
        return None
    return float(stripped)


def percent_change_since_months(
    series: FredSeries,
    months: int,
    as_of: date | None = None,
) -> float:
    latest = latest_observation(series, as_of)
    prior = observation_on_or_before(series, subtract_months(latest.observed_at, months))
    if prior.value == 0:
        raise MacroDataError(f"{series.series_id}: prior value is zero")
    return (latest.value - prior.value) / prior.value * 100.0


def change_since_months(series: FredSeries, months: int, as_of: date | None = None) -> float:
    latest = latest_observation(series, as_of)
    prior = observation_on_or_before(series, subtract_months(latest.observed_at, months))
    return latest.value - prior.value


def safe_yoy_delta_since_months(series: FredSeries, months: int) -> float:
    latest = latest_observation(series)
    latest_yoy = percent_change_since_months(series, 12, latest.observed_at)
    reference = observation_on_or_before(series, subtract_months(latest.observed_at, months))
    try:
        reference_yoy = percent_change_since_months(series, 12, reference.observed_at)
    except MacroDataError:
        return 0.0
    return latest_yoy - reference_yoy


def latest_observation(series: FredSeries, as_of: date | None = None) -> MacroObservation:
    if as_of is None:
        return series.observations[-1]
    return observation_on_or_before(series, as_of)


def observation_on_or_before(series: FredSeries, target: date) -> MacroObservation:
    for observation in reversed(series.observations):
        if observation.observed_at <= target:
            return observation
    raise MacroDataError(f"{series.series_id}: no observation on or before {target.isoformat()}")


def subtract_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 - months
    year = month_index // 12
    month = month_index % 12 + 1
    return date(year, month, min(value.day, days_in_month(year, month)))


def days_in_month(year: int, month: int) -> int:
    if month == 2:
        leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
        return 29 if leap else 28
    if month in (4, 6, 9, 11):
        return 30
    return 31


def format_metric_table(metrics: tuple[MacroMetric, ...]) -> str:
    rows = [
        "| Indicator | Value | As Of | Trend / Read | Source |",
        "|---|---:|---:|---|---|",
    ]
    for metric in metrics:
        rows.append(
            f"| {metric.label} ({metric.series_id}) | {metric.value:.2f}{metric.unit} | "
            f"{metric.as_of.isoformat()} | {metric.trend} | {metric.source} |"
        )
    return "\n".join(rows)


def format_ppt_trend(delta: float, suffix: str) -> str:
    return f"{delta:+.2f} ppt {suffix}"


def describe_yield_curve(spread: float) -> str:
    if spread < 0:
        return "Inverted; market is pricing restrictive policy or slowdown risk"
    if spread < 0.5:
        return "Flat; cycle signal is still cautious"
    return "Positive; curve is no longer inverted"


def describe_growth(yoy: float) -> str:
    if yoy < 0:
        return "Contracting versus a year ago"
    if yoy < 2:
        return "Soft positive growth"
    return "Expanding"


def default_fetch_text(url: str) -> str:
    request = Request(url, headers={"User-Agent": "trading-copilot/0.1"})
    with urlopen(request, timeout=15) as response:
        return response.read().decode("utf-8")
