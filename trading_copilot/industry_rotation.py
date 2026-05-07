from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timezone
from io import StringIO
import json
from typing import Any, Callable, Protocol
from urllib.parse import quote
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class IndustryProxy:
    symbol: str
    name: str
    group: str
    theme: str
    risk: str = ""


@dataclass(frozen=True)
class PricePoint:
    symbol: str
    observed_at: date
    close: float


@dataclass(frozen=True)
class ReturnProfile:
    one_month: float
    three_month: float
    six_month: float
    source_points: int


@dataclass(frozen=True)
class IndustryScore:
    symbol: str
    name: str
    group: str
    theme: str
    one_month: float
    three_month: float
    six_month: float
    relative_one_month: float
    relative_three_month: float
    relative_six_month: float
    acceleration: float
    leadership_score: float
    next_leader_score: float
    read: str
    source: str


@dataclass(frozen=True)
class IndustryRotationResult:
    benchmark_symbol: str
    benchmark_profile: ReturnProfile
    current_leaders: tuple[IndustryScore, ...]
    next_leaders: tuple[IndustryScore, ...]
    scores: tuple[IndustryScore, ...]
    errors: tuple[str, ...]
    as_of: date


class PriceHistoryProvider(Protocol):
    def history(
        self,
        symbol: str,
        range_period: str = "6mo",
        interval: str = "1d",
    ) -> tuple[PricePoint, ...]:
        pass


class IndustryDataError(RuntimeError):
    pass


class YahooHistoryProvider:
    BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart"

    def __init__(self, fetch_json: Callable[[str], dict[str, Any]] | None = None):
        self.fetch_json = fetch_json or default_fetch_json

    def history(
        self,
        symbol: str,
        range_period: str = "6mo",
        interval: str = "1d",
    ) -> tuple[PricePoint, ...]:
        normalized = symbol.strip().upper()
        if range_period.lower() == "max":
            period2 = int(datetime.now(timezone.utc).timestamp())
            source = (
                f"{self.BASE_URL}/{quote(normalized)}"
                f"?period1=0&period2={period2}&interval={quote(interval)}"
                "&events=history&includeAdjustedClose=true"
            )
        else:
            source = (
                f"{self.BASE_URL}/{quote(normalized)}"
                f"?range={quote(range_period)}&interval={quote(interval)}"
            )
        payload = self.fetch_json(source)
        result = extract_chart_result(payload, normalized)
        timestamps = result.get("timestamp")
        indicators = result.get("indicators", {})
        quotes = indicators.get("quote") if isinstance(indicators, dict) else None
        if not isinstance(timestamps, list) or not isinstance(quotes, list) or not quotes:
            raise IndustryDataError(f"{normalized}: Yahoo chart response has no history")
        closes = quotes[0].get("close") if isinstance(quotes[0], dict) else None
        if not isinstance(closes, list):
            raise IndustryDataError(f"{normalized}: Yahoo chart response has no close prices")
        adjusted = adjusted_closes(indicators)
        values = adjusted if adjusted is not None and len(adjusted) == len(closes) else closes

        points: list[PricePoint] = []
        for timestamp, close in zip(timestamps, values):
            if isinstance(close, bool) or not isinstance(close, (int, float)):
                continue
            if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
                continue
            observed_at = datetime.fromtimestamp(timestamp, tz=timezone.utc).date()
            points.append(PricePoint(normalized, observed_at, float(close)))
        if len(points) < 2:
            raise IndustryDataError(f"{normalized}: insufficient price history")
        return tuple(points)


DEFAULT_INDUSTRY_PROXIES = (
    IndustryProxy("XLK", "Technology", "Broad Sector", "offensive", "Mega-cap concentration and valuation risk."),
    IndustryProxy("XLC", "Communication Services", "Broad Sector", "offensive", "Platform concentration and ad-cycle risk."),
    IndustryProxy("XLY", "Consumer Discretionary", "Broad Sector", "cyclical", "Rate-sensitive consumer and valuation risk."),
    IndustryProxy("XLI", "Industrials", "Broad Sector", "cyclical", "Capex and economic-cycle sensitivity."),
    IndustryProxy("XLF", "Financials", "Broad Sector", "cyclical", "Credit cycle, rate curve, and capital-markets risk."),
    IndustryProxy("XLE", "Energy", "Broad Sector", "inflation", "Oil and gas price volatility."),
    IndustryProxy("XLB", "Materials", "Broad Sector", "inflation", "Commodity and China-demand sensitivity."),
    IndustryProxy("XLV", "Health Care", "Broad Sector", "defensive", "Policy, patent, and earnings-cycle risk."),
    IndustryProxy("XLP", "Consumer Staples", "Broad Sector", "defensive", "Margin pressure and valuation risk."),
    IndustryProxy("XLU", "Utilities", "Broad Sector", "defensive", "Duration and regulatory risk."),
    IndustryProxy("XLRE", "Real Estate", "Broad Sector", "defensive", "Rate and financing risk."),
    IndustryProxy("SMH", "Semiconductors", "Technology", "offensive", "Highly cyclical earnings and AI-capex crowding risk."),
    IndustryProxy("IGV", "Software", "Technology", "offensive", "Multiple compression and enterprise budget risk."),
    IndustryProxy("SKYY", "Cloud Computing", "Technology", "offensive", "Growth-stock and cloud-spend risk."),
    IndustryProxy("CIBR", "Cybersecurity", "Technology", "offensive", "Valuation and enterprise spending risk."),
    IndustryProxy("AIQ", "AI and Automation", "Technology", "offensive", "Theme crowding and index composition risk."),
    IndustryProxy("BOTZ", "Robotics and AI", "Technology", "offensive", "Theme crowding and industrial capex risk."),
    IndustryProxy("ITA", "Aerospace and Defense", "Industrials", "cyclical", "Budget, contract, and geopolitical risk."),
    IndustryProxy("PAVE", "Infrastructure", "Industrials", "cyclical", "Rate, fiscal, and construction-cycle risk."),
    IndustryProxy("IYT", "Transportation", "Industrials", "cyclical", "Freight-cycle and fuel-cost risk."),
    IndustryProxy("XHB", "Homebuilders", "Consumer / Housing", "cyclical", "Mortgage-rate and affordability risk."),
    IndustryProxy("XRT", "Retail", "Consumer / Retail", "cyclical", "Consumer demand and margin risk."),
    IndustryProxy("IBB", "Biotech", "Health Care", "offensive", "Trial, regulatory, and funding risk."),
    IndustryProxy("XBI", "Equal-Weight Biotech", "Health Care", "offensive", "High dispersion and financing risk."),
    IndustryProxy("KRE", "Regional Banks", "Financials", "cyclical", "Deposit, credit, and commercial real-estate risk."),
    IndustryProxy("KIE", "Insurance", "Financials", "cyclical", "Catastrophe, pricing, and rate risk."),
    IndustryProxy("XME", "Metals and Mining", "Materials", "inflation", "Commodity and global-demand risk."),
    IndustryProxy("COPX", "Copper Miners", "Materials", "inflation", "Copper price and mine-execution risk."),
    IndustryProxy("REMX", "Rare Earth / Strategic Metals", "Materials", "inflation", "Geopolitical, mining, and liquidity risk."),
    IndustryProxy("URA", "Uranium", "Energy / Materials", "inflation", "Policy, supply-contract, and commodity risk."),
    IndustryProxy("TAN", "Solar", "Energy Transition", "offensive", "Rates, subsidies, and margin pressure."),
    IndustryProxy("FAN", "Wind Energy", "Energy Transition", "offensive", "Rates, policy, and project economics risk."),
    IndustryProxy("XOP", "Oil and Gas E&P", "Energy", "inflation", "Oil and gas price volatility."),
    IndustryProxy("OIH", "Oil Services", "Energy", "inflation", "Capex cycle and commodity volatility."),
    IndustryProxy("GDX", "Gold Miners", "Materials", "defensive", "Gold price, cost inflation, and mining execution risk."),
    IndustryProxy("SIL", "Silver Miners", "Materials", "inflation", "Silver price and mining-equity risk."),
)


def analyze_industries(
    history_provider: PriceHistoryProvider | None = None,
    proxies: tuple[IndustryProxy, ...] = DEFAULT_INDUSTRY_PROXIES,
    benchmark_symbol: str = "SPY",
    current_limit: int = 10,
    next_limit: int = 10,
) -> IndustryRotationResult:
    provider = history_provider or YahooHistoryProvider()
    benchmark_points = provider.history(benchmark_symbol)
    benchmark_profile = return_profile(benchmark_points)
    scores: list[IndustryScore] = []
    errors: list[str] = []
    latest_dates = [benchmark_points[-1].observed_at]

    for proxy in proxies:
        try:
            points = provider.history(proxy.symbol)
            latest_dates.append(points[-1].observed_at)
            scores.append(score_proxy(proxy, points, benchmark_profile))
        except Exception as exc:
            errors.append(f"{proxy.symbol}: {exc}")

    ranked = tuple(sorted(scores, key=lambda item: item.leadership_score, reverse=True))
    current_leaders = ranked[: max(current_limit, 0)]
    current_symbols = {score.symbol for score in current_leaders}
    next_pool = (score for score in scores if score.symbol not in current_symbols)
    next_leaders = tuple(
        sorted(next_pool, key=lambda item: item.next_leader_score, reverse=True)[
            : max(next_limit, 0)
        ]
    )
    return IndustryRotationResult(
        benchmark_symbol=benchmark_symbol.upper(),
        benchmark_profile=benchmark_profile,
        current_leaders=current_leaders,
        next_leaders=next_leaders,
        scores=ranked,
        errors=tuple(errors),
        as_of=max(latest_dates),
    )


def score_proxy(
    proxy: IndustryProxy,
    points: tuple[PricePoint, ...],
    benchmark: ReturnProfile,
) -> IndustryScore:
    profile = return_profile(points)
    relative_one = profile.one_month - benchmark.one_month
    relative_three = profile.three_month - benchmark.three_month
    relative_six = profile.six_month - benchmark.six_month
    acceleration = profile.one_month - profile.three_month / 3.0
    leadership_score = relative_one * 0.25 + relative_three * 0.40 + relative_six * 0.35
    next_score = (
        relative_one * 0.45
        + acceleration * 0.30
        + relative_three * 0.20
        - max(relative_six, 0.0) * 0.05
    )
    return IndustryScore(
        symbol=proxy.symbol.upper(),
        name=proxy.name,
        group=proxy.group,
        theme=proxy.theme,
        one_month=profile.one_month,
        three_month=profile.three_month,
        six_month=profile.six_month,
        relative_one_month=relative_one,
        relative_three_month=relative_three,
        relative_six_month=relative_six,
        acceleration=acceleration,
        leadership_score=leadership_score,
        next_leader_score=next_score,
        read=score_read(leadership_score, next_score, acceleration),
        source=(
            f"https://query1.finance.yahoo.com/v8/finance/chart/"
            f"{quote(proxy.symbol.upper())}?range=6mo&interval=1d"
        ),
    )


def return_profile(points: tuple[PricePoint, ...]) -> ReturnProfile:
    if len(points) < 2:
        raise IndustryDataError("price history requires at least two points")
    return ReturnProfile(
        one_month=return_since(points, 21),
        three_month=return_since(points, 63),
        six_month=return_since(points, 126),
        source_points=len(points),
    )


def return_since(points: tuple[PricePoint, ...], trading_days: int) -> float:
    latest = points[-1].close
    prior_index = max(0, len(points) - 1 - trading_days)
    prior = points[prior_index].close
    if prior == 0:
        raise IndustryDataError("prior close is zero")
    return (latest - prior) / prior * 100.0


def format_industry_report(result: IndustryRotationResult) -> str:
    lines = [
        f"# Industry Leadership Radar - {result.as_of.isoformat()}",
        "",
        "Not investment advice. This ranks ETF proxies for research, not trade execution.",
        "",
        "## Benchmark",
        (
            f"- {result.benchmark_symbol}: 1M {format_pct(result.benchmark_profile.one_month)}, "
            f"3M {format_pct(result.benchmark_profile.three_month)}, "
            f"6M {format_pct(result.benchmark_profile.six_month)}"
        ),
        "",
        "## Current Leaders",
        format_score_table(result.current_leaders, include_next_score=False),
        "",
        "## Next Leader Candidates",
        format_score_table(result.next_leaders, include_next_score=True),
        "",
        "## Interpretation Rules",
        "- Current leaders are ranked by 1M/3M/6M relative strength versus the benchmark.",
        "- Next leader candidates emphasize recent relative strength and acceleration.",
        "- A candidate still needs confirmation from earnings revisions, macro regime, and news/contracts.",
        "",
        "## How To Use",
        "- Use current leaders as the first industry filter for `screen-all`.",
        "- Use next candidates as watchlist candidates before a broad market rotation becomes obvious.",
        "- Reject any industry where the top ETF is strong but underlying stocks lack breadth.",
    ]
    if result.errors:
        lines.extend(["", "## Data Gaps"])
        lines.extend(f"- {error}" for error in result.errors)
    lines.extend(["", "## Sources"])
    for score in result.scores:
        lines.append(f"- {score.symbol}: {score.source}")
    return "\n".join(lines)


def format_score_table(
    scores: tuple[IndustryScore, ...],
    include_next_score: bool,
) -> str:
    if not scores:
        return "- No industry scores available."
    if include_next_score:
        rows = [
            "| Symbol | Industry | Group | 1M | 3M | 6M | Rel 1M | Accel | Next Score | Read |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|---|",
        ]
        for score in scores:
            rows.append(
                f"| {score.symbol} | {score.name} | {score.group} | "
                f"{format_pct(score.one_month)} | {format_pct(score.three_month)} | "
                f"{format_pct(score.six_month)} | {format_pct(score.relative_one_month)} | "
                f"{format_pct(score.acceleration)} | {score.next_leader_score:.2f} | "
                f"{score.read} |"
            )
        return "\n".join(rows)

    rows = [
        "| Symbol | Industry | Group | 1M | 3M | 6M | Rel 3M | Leadership Score | Read |",
        "|---|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for score in scores:
        rows.append(
            f"| {score.symbol} | {score.name} | {score.group} | "
            f"{format_pct(score.one_month)} | {format_pct(score.three_month)} | "
            f"{format_pct(score.six_month)} | {format_pct(score.relative_three_month)} | "
            f"{score.leadership_score:.2f} | {score.read} |"
        )
    return "\n".join(rows)


def industry_scores_to_csv(scores: tuple[IndustryScore, ...]) -> str:
    output = StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(
        [
            "symbol",
            "name",
            "group",
            "theme",
            "one_month",
            "three_month",
            "six_month",
            "relative_one_month",
            "relative_three_month",
            "relative_six_month",
            "acceleration",
            "leadership_score",
            "next_leader_score",
            "read",
            "source",
        ]
    )
    for score in scores:
        writer.writerow(
            [
                score.symbol,
                score.name,
                score.group,
                score.theme,
                f"{score.one_month:.4f}",
                f"{score.three_month:.4f}",
                f"{score.six_month:.4f}",
                f"{score.relative_one_month:.4f}",
                f"{score.relative_three_month:.4f}",
                f"{score.relative_six_month:.4f}",
                f"{score.acceleration:.4f}",
                f"{score.leadership_score:.4f}",
                f"{score.next_leader_score:.4f}",
                score.read,
                score.source,
            ]
        )
    return output.getvalue()


def score_read(leadership_score: float, next_score: float, acceleration: float) -> str:
    if leadership_score > 10.0:
        return "Established leadership"
    if next_score > 4.0 and acceleration > 0.0:
        return "Emerging rotation candidate"
    if leadership_score > 0.0:
        return "Positive relative strength"
    if acceleration > 0.0:
        return "Improving but not confirmed"
    return "Lagging or unconfirmed"


def format_pct(value: float) -> str:
    return f"{value:+.2f}%"


def extract_chart_result(payload: dict[str, Any], symbol: str) -> dict[str, Any]:
    chart = payload.get("chart")
    if not isinstance(chart, dict):
        raise IndustryDataError(f"{symbol}: invalid Yahoo chart response")
    if chart.get("error"):
        raise IndustryDataError(f"{symbol}: Yahoo chart error: {chart['error']}")
    results = chart.get("result")
    if not isinstance(results, list) or not results:
        raise IndustryDataError(f"{symbol}: Yahoo chart response has no result")
    first = results[0]
    if not isinstance(first, dict):
        raise IndustryDataError(f"{symbol}: Yahoo chart result is invalid")
    return first


def adjusted_closes(indicators: Any) -> list[Any] | None:
    if not isinstance(indicators, dict):
        return None
    adjusted_payload = indicators.get("adjclose")
    if not isinstance(adjusted_payload, list) or not adjusted_payload:
        return None
    first = adjusted_payload[0]
    if not isinstance(first, dict):
        return None
    adjusted = first.get("adjclose")
    return adjusted if isinstance(adjusted, list) else None


def default_fetch_json(url: str) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": "trading-copilot/0.1"})
    with urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))
