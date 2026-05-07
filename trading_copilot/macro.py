from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from io import StringIO
import json
from typing import Callable, Protocol
from urllib.parse import quote
from urllib.request import Request, urlopen

from .industry_rotation import PriceHistoryProvider


DEFAULT_SERIES_NAMES = {
    "CPIAUCSL": "Consumer Price Index",
    "CPILFESL": "Core Consumer Price Index",
    "UNRATE": "Unemployment Rate",
    "FEDFUNDS": "Effective Federal Funds Rate",
    "T10Y2Y": "10Y-2Y Treasury Spread",
    "INDPRO": "Industrial Production",
    "RSAFS": "Retail Sales",
    "DTWEXBGS": "Nominal Broad U.S. Dollar Index",
    "DEXUSEU": "Euro vs USD",
    "DEXJPUS": "Japanese Yen vs USD",
    "DEXUSUK": "British Pound vs USD",
    "DEXSZUS": "Swiss Franc vs USD",
    "DEXCHUS": "Chinese Yuan vs USD",
    "PMAIZMTUSDM": "Global Corn Price",
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
    currencies_commodities: tuple[MacroMetric, ...] = ()
    sources: tuple[str, ...] = ()
    data_gaps: tuple[str, ...] = ()


class MacroDataError(RuntimeError):
    pass


class FredCsvProvider:
    BASE_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
    TEXT_BASE_URL = "https://fred.stlouisfed.org/data"
    FALLBACK_BASE_URL = "https://govspending.org/api/export/fred"

    def __init__(
        self,
        fetch_text: Callable[[str], str] | None = None,
        series_names: dict[str, str] | None = None,
    ):
        self.fetch_text = fetch_text or default_fetch_text
        self.series_names = series_names or DEFAULT_SERIES_NAMES
        self._prefer_fallback = False

    def series(self, series_id: str) -> FredSeries:
        normalized = series_id.strip().upper()
        source = f"{self.BASE_URL}?id={quote(normalized)}"
        if self._prefer_fallback:
            try:
                parsed = self._fallback_series(normalized)
            except Exception:
                try:
                    parsed = self._text_series(normalized)
                except Exception:
                    parsed = parse_fred_csv(self.fetch_text(source), normalized, source)
        else:
            try:
                parsed = parse_fred_csv(self.fetch_text(source), normalized, source)
            except Exception:
                self._prefer_fallback = True
                try:
                    parsed = self._fallback_series(normalized)
                except Exception:
                    parsed = self._text_series(normalized)
        return FredSeries(
            series_id=parsed.series_id,
            name=self.series_names.get(normalized, normalized),
            source=parsed.source,
            observations=parsed.observations,
        )

    def _fallback_series(self, series_id: str) -> FredSeries:
        fallback_source = f"{self.FALLBACK_BASE_URL}/{quote(series_id)}.json"
        try:
            return parse_govspending_json(
                self.fetch_text(fallback_source),
                series_id,
                fallback_source,
            )
        except Exception as fallback_error:
            raise MacroDataError(
                f"{series_id}: unable to fetch FRED CSV or fallback JSON"
            ) from fallback_error

    def _text_series(self, series_id: str) -> FredSeries:
        text_source = f"{self.TEXT_BASE_URL}/{quote(series_id)}"
        try:
            return parse_fred_text_table(
                self.fetch_text(text_source),
                series_id,
                text_source,
            )
        except Exception as text_error:
            raise MacroDataError(
                f"{series_id}: unable to fetch FRED CSV, fallback JSON, or FRED text"
            ) from text_error


def build_macro_dashboard(
    provider: MacroDataProvider | None = None,
    price_provider: PriceHistoryProvider | None = None,
) -> MacroDashboard:
    provider = provider or FredCsvProvider()
    cpi = provider.series("CPIAUCSL")
    core_cpi = provider.series("CPILFESL")
    unemployment = provider.series("UNRATE")
    fed_funds = provider.series("FEDFUNDS")
    yield_curve = provider.series("T10Y2Y")
    industrial_production = provider.series("INDPRO")
    retail_sales = provider.series("RSAFS")
    optional_series: dict[str, FredSeries] = {}
    data_gaps: list[str] = []
    for series_id in ("DTWEXBGS", "DEXUSEU", "DEXJPUS", "DEXUSUK", "DEXSZUS", "DEXCHUS", "PMAIZMTUSDM"):
        try:
            optional_series[series_id] = provider.series(series_id)
        except Exception as exc:
            data_gaps.append(f"{series_id}: {exc}")

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
    currencies_commodities = build_currency_commodity_metrics(optional_series, data_gaps)
    if price_provider is not None:
        existing_series_ids = {metric.series_id for metric in currencies_commodities}
        for fred_id, symbol, label, inverse in (
            ("DEXUSEU", "EURUSD=X", "Euro vs USD Proxy", False),
            ("DEXJPUS", "JPY=X", "Japanese Yen vs USD Proxy", True),
            ("DEXUSUK", "GBPUSD=X", "British Pound vs USD Proxy", False),
            ("DEXSZUS", "CHF=X", "Swiss Franc vs USD Proxy", True),
            ("DEXCHUS", "CNY=X", "Chinese Yuan vs USD Proxy", True),
        ):
            if fred_id not in existing_series_ids:
                metric = build_price_change_metric(
                    symbol=symbol,
                    label=label,
                    price_provider=price_provider,
                    data_gaps=data_gaps,
                    inverse=inverse,
                    trend_fn=(lambda value: describe_currency_strength(value, threshold=2.0))
                    if fred_id == "DEXCHUS"
                    else describe_currency_strength,
                )
                if metric is not None:
                    currencies_commodities = currencies_commodities + (metric,)
        corn_futures = build_price_change_metric(
            symbol="ZC=F",
            label="Corn Futures Proxy",
            price_provider=price_provider,
            data_gaps=data_gaps,
        )
        if corn_futures is not None:
            currencies_commodities = currencies_commodities + (corn_futures,)
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
                *(series.source for series in optional_series.values()),
            }
        )
    )
    all_metrics = inflation + labor_policy + growth_cycle + currencies_commodities
    return MacroDashboard(
        as_of=max(metric.as_of for metric in all_metrics),
        regime=regime,
        inflation=inflation,
        labor_policy=labor_policy,
        growth_cycle=growth_cycle,
        currencies_commodities=currencies_commodities,
        sources=sources,
        data_gaps=tuple(data_gaps),
    )


def build_currency_commodity_metrics(
    series_by_id: dict[str, FredSeries],
    data_gaps: list[str],
) -> tuple[MacroMetric, ...]:
    metrics: list[MacroMetric] = []

    def append_metric(
        series_id: str,
        value_fn: Callable[[FredSeries], float],
        trend_fn: Callable[[float], str],
    ) -> None:
        series = series_by_id.get(series_id)
        if series is None:
            return
        try:
            value = value_fn(series)
        except Exception as exc:
            data_gaps.append(f"{series_id}: {exc}")
            return
        metrics.append(
            MacroMetric(
                label=DEFAULT_SERIES_NAMES[series_id],
                series_id=series_id,
                value=value,
                unit="% 6M",
                as_of=latest_observation(series).observed_at,
                trend=trend_fn(value),
                source=series.source,
            )
        )

    append_metric("DTWEXBGS", lambda series: percent_change_since_months(series, 6), describe_broad_dollar)
    append_metric("DEXUSEU", lambda series: percent_change_since_months(series, 6), describe_currency_strength)
    append_metric("DEXJPUS", lambda series: inverse_percent_change_since_months(series, 6), describe_currency_strength)
    append_metric("DEXUSUK", lambda series: percent_change_since_months(series, 6), describe_currency_strength)
    append_metric("DEXSZUS", lambda series: inverse_percent_change_since_months(series, 6), describe_currency_strength)
    append_metric(
        "DEXCHUS",
        lambda series: inverse_percent_change_since_months(series, 6),
        lambda value: describe_currency_strength(value, threshold=2.0),
    )
    append_metric("PMAIZMTUSDM", lambda series: percent_change_since_months(series, 6), describe_commodity_pressure)
    return tuple(metrics)


def build_price_change_metric(
    symbol: str,
    label: str,
    price_provider: PriceHistoryProvider,
    data_gaps: list[str],
    inverse: bool = False,
    trend_fn: Callable[[float], str] | None = None,
) -> MacroMetric | None:
    normalized = symbol.upper()
    try:
        prices = tuple(sorted(price_provider.history(normalized, range_period="6mo", interval="1d"), key=lambda item: item.observed_at))
        if len(prices) < 2:
            raise MacroDataError(f"{normalized}: not enough price observations")
        first = prices[0]
        latest = prices[-1]
        if first.close == 0:
            raise MacroDataError(f"{normalized}: starting price is zero")
        if inverse:
            if latest.close == 0:
                raise MacroDataError(f"{normalized}: latest price is zero")
            value = ((1.0 / latest.close) - (1.0 / first.close)) / (1.0 / first.close) * 100.0
        else:
            value = (latest.close - first.close) / first.close * 100.0
    except Exception as exc:
        data_gaps.append(f"{normalized}: {exc}")
        return None
    return MacroMetric(
        label=label,
        series_id=normalized,
        value=value,
        unit="% 6M",
        as_of=latest.observed_at,
        trend=(trend_fn or describe_commodity_pressure)(value),
        source=f"https://query1.finance.yahoo.com/v8/finance/chart/{normalized}?range=6mo&interval=1d",
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
        "## Currencies and Commodities",
        format_metric_table(dashboard.currencies_commodities),
        "",
        "## What To Watch",
    ]
    lines.extend(f"- {item}" for item in dashboard.regime.watch_items)
    lines.extend(["", "## Portfolio Research Implications"])
    lines.extend(f"- {item}" for item in dashboard.regime.portfolio_implications)
    lines.extend(["", "## Sources"])
    lines.extend(f"- {source}" for source in dashboard.sources)
    if dashboard.data_gaps:
        lines.extend(["", "## Data Gaps"])
        lines.extend(f"- {gap}" for gap in dashboard.data_gaps)
    return "\n".join(lines)


def parse_fred_csv(text: str, series_id: str, source: str) -> FredSeries:
    normalized = series_id.strip().upper()
    reader = csv.DictReader(StringIO(text))
    date_field = fred_date_field(reader.fieldnames)
    if date_field is None:
        raise MacroDataError(f"{normalized}: FRED CSV is missing date field")
    if normalized not in reader.fieldnames:
        raise MacroDataError(f"{normalized}: FRED CSV is missing value column")

    observations: list[MacroObservation] = []
    for row in reader:
        value = parse_fred_value(row.get(normalized))
        if value is None:
            continue
        observed_at = date.fromisoformat(str(row[date_field]))
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


def parse_fred_text_table(text: str, series_id: str, source: str) -> FredSeries:
    normalized = series_id.strip().upper()
    observations: list[MacroObservation] = []
    for line in text.splitlines():
        parsed = parse_fred_text_observation(line)
        if parsed is None:
            continue
        observed_at, value = parsed
        observations.append(MacroObservation(normalized, observed_at, value))
    if not observations:
        raise MacroDataError(f"{normalized}: FRED text endpoint has no numeric observations")
    observations.sort(key=lambda item: item.observed_at)
    return FredSeries(
        series_id=normalized,
        name=DEFAULT_SERIES_NAMES.get(normalized, normalized),
        source=source,
        observations=tuple(observations),
    )


def parse_fred_text_observation(line: str) -> tuple[date, float] | None:
    stripped = line.strip()
    if not stripped:
        return None
    if "|" in stripped:
        parts = [part.strip() for part in stripped.split("|")]
    else:
        parts = stripped.split()
    if len(parts) < 2:
        return None
    try:
        observed_at = date.fromisoformat(parts[0])
    except ValueError:
        return None
    value = parse_fred_value(parts[-1])
    if value is None:
        return None
    return observed_at, value


def parse_govspending_json(text: str, series_id: str, source: str) -> FredSeries:
    normalized = series_id.strip().upper()
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise MacroDataError(f"{normalized}: fallback JSON is not an object")
    observations_payload = payload.get("observations")
    if not isinstance(observations_payload, list):
        raise MacroDataError(f"{normalized}: fallback JSON is missing observations")

    observations: list[MacroObservation] = []
    for row in observations_payload:
        if not isinstance(row, dict):
            continue
        value = parse_fred_value(str(row.get("value")) if row.get("value") is not None else None)
        if value is None:
            continue
        observed_at = date.fromisoformat(str(row["date"]))
        observations.append(MacroObservation(normalized, observed_at, value))
    if not observations:
        raise MacroDataError(f"{normalized}: fallback JSON has no numeric observations")
    observations.sort(key=lambda item: item.observed_at)
    return FredSeries(
        series_id=normalized,
        name=str(payload.get("title") or DEFAULT_SERIES_NAMES.get(normalized, normalized)),
        source=source,
        observations=tuple(observations),
    )


def fred_date_field(fieldnames: list[str] | None) -> str | None:
    if fieldnames is None:
        return None
    for candidate in ("observation_date", "DATE", "date"):
        if candidate in fieldnames:
            return candidate
    return None


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


def inverse_percent_change_since_months(
    series: FredSeries,
    months: int,
    as_of: date | None = None,
) -> float:
    latest = latest_observation(series, as_of)
    prior = observation_on_or_before(series, subtract_months(latest.observed_at, months))
    if latest.value == 0 or prior.value == 0:
        raise MacroDataError(f"{series.series_id}: exchange rate value is zero")
    return ((1.0 / latest.value) - (1.0 / prior.value)) / (1.0 / prior.value) * 100.0


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


def describe_broad_dollar(change: float) -> str:
    if change >= 5.0:
        return "Dollar strengthening; watch global liquidity and commodity pressure"
    if change <= -5.0:
        return "Dollar weakening; can ease global liquidity and support commodities"
    return "No large 6-month dollar move"


def describe_currency_strength(change: float, threshold: float = 3.0) -> str:
    if change >= threshold:
        return "Strengthening versus USD"
    if change <= -threshold:
        return "Weakening versus USD"
    return "Range-bound versus USD"


def describe_commodity_pressure(change: float) -> str:
    if change >= 20.0:
        return "Sharp food/input-cost pressure"
    if change <= -20.0:
        return "Sharp food/input-cost relief"
    return "No large 6-month corn shock"


def default_fetch_text(url: str) -> str:
    request = Request(url, headers={"User-Agent": "trading-copilot/0.1"})
    with urlopen(request, timeout=15) as response:
        return response.read().decode("utf-8")
