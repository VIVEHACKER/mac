from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
import os
from typing import Any, Callable, Protocol
from urllib.parse import quote
from urllib.request import Request, urlopen

from .storage import normalize_ticker


COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts"


@dataclass(frozen=True)
class StatementMetric:
    label: str
    value: float | None
    unit: str
    period_end: date | None
    filed: date | None
    source_tag: str


@dataclass(frozen=True)
class FundamentalsAnalysis:
    ticker: str
    revenue: float | None
    revenue_growth: float | None
    net_income: float | None
    net_margin: float | None
    assets: float | None
    liabilities: float | None
    equity: float | None
    liabilities_to_equity: float | None
    operating_cash_flow: float | None
    capital_expenditure: float | None
    free_cash_flow: float | None
    metrics: tuple[StatementMetric, ...]
    reads: tuple[str, ...]
    source: str
    operating_income: float | None = None
    diluted_eps: float | None = None
    shares_outstanding: float | None = None


class FundamentalsProvider(Protocol):
    def analysis(self, ticker: str) -> FundamentalsAnalysis:
        pass


class FundamentalsDataError(RuntimeError):
    pass


class SecCompanyFactsProvider:
    def __init__(self, fetch_json: Callable[[str], dict[str, Any]] | None = None):
        self.fetch_json = fetch_json or default_fetch_json
        self._ticker_to_cik: dict[str, int] | None = None

    def analysis(self, ticker: str) -> FundamentalsAnalysis:
        normalized = normalize_ticker(ticker)
        cik = self._lookup_cik(normalized)
        source = f"{COMPANY_FACTS_URL}/CIK{cik:010d}.json"
        return analyze_company_facts(normalized, self.fetch_json(source), source=source)

    def _lookup_cik(self, ticker: str) -> int:
        if self._ticker_to_cik is None:
            payload = self.fetch_json(COMPANY_TICKERS_URL)
            mapping: dict[str, int] = {}
            for row in payload.values():
                if not isinstance(row, dict):
                    continue
                row_ticker = str(row.get("ticker") or "").upper()
                cik = row.get("cik_str")
                if row_ticker and isinstance(cik, int):
                    mapping[row_ticker] = cik
            self._ticker_to_cik = mapping
        try:
            return self._ticker_to_cik[ticker]
        except KeyError as exc:
            raise FundamentalsDataError(f"{ticker}: ticker not found in SEC company_tickers") from exc


class YahooFundamentalsProvider:
    BASE_URL = "https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries"
    TYPES = (
        "annualTotalRevenue",
        "annualNetIncome",
        "annualTotalAssets",
        "annualTotalLiabilitiesNetMinorityInterest",
        "annualStockholdersEquity",
        "annualOperatingCashFlow",
        "annualCapitalExpenditure",
    )

    def __init__(self, fetch_json: Callable[[str], dict[str, Any]] | None = None):
        self.fetch_json = fetch_json or default_fetch_json

    def analysis(self, ticker: str) -> FundamentalsAnalysis:
        normalized = normalize_ticker(ticker)
        types = ",".join(self.TYPES)
        source = (
            f"{self.BASE_URL}/{quote(normalized)}?symbol={quote(normalized)}"
            f"&type={types}&period1=1577836800&period2=1893456000"
        )
        analysis = analyze_yahoo_timeseries(normalized, self.fetch_json(source), source)
        if analysis.metrics:
            return analysis
        fallback_payload = merge_yahoo_payloads(
            self.fetch_json(self._single_type_url(normalized, statement_type))
            for statement_type in self.TYPES
        )
        return analyze_yahoo_timeseries(normalized, fallback_payload, source)

    def _single_type_url(self, ticker: str, statement_type: str) -> str:
        return (
            f"{self.BASE_URL}/{quote(ticker)}?type={quote(statement_type)}"
            f"&period1=1577836800&period2=1893456000"
        )


class HybridFundamentalsProvider:
    def __init__(
        self,
        primary: FundamentalsProvider | None = None,
        fallback: FundamentalsProvider | None = None,
    ):
        self.primary = primary or SecCompanyFactsProvider()
        self.fallback = fallback or YahooFundamentalsProvider()

    def analysis(self, ticker: str) -> FundamentalsAnalysis:
        try:
            return self.primary.analysis(ticker)
        except Exception:
            return self.fallback.analysis(ticker)


def analyze_company_facts(
    ticker: str,
    payload: dict[str, Any],
    source: str,
) -> FundamentalsAnalysis:
    normalized = normalize_ticker(ticker)
    revenue_metric, prior_revenue = latest_and_prior(
        payload,
        ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"),
    )
    net_income_metric, _ = latest_and_prior(payload, ("NetIncomeLoss",))
    assets_metric, _ = latest_and_prior(payload, ("Assets",))
    liabilities_metric, _ = latest_and_prior(payload, ("Liabilities",))
    equity_metric, _ = latest_and_prior(
        payload,
        ("StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
    )
    ocf_metric, _ = latest_and_prior(payload, ("NetCashProvidedByUsedInOperatingActivities",))
    capex_metric, _ = latest_and_prior(
        payload,
        ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"),
    )
    op_income_metric, _ = latest_and_prior(payload, ("OperatingIncomeLoss",))
    eps_value = extract_diluted_eps(payload)
    shares_value = extract_shares_outstanding(payload)

    revenue = value_of(revenue_metric)
    prior_revenue_value = value_of(prior_revenue)
    net_income = value_of(net_income_metric)
    assets = value_of(assets_metric)
    liabilities = value_of(liabilities_metric)
    equity = value_of(equity_metric)
    operating_cash_flow = value_of(ocf_metric)
    capital_expenditure = value_of(capex_metric)

    revenue_growth = pct_change(revenue, prior_revenue_value)
    net_margin = pct_ratio(net_income, revenue)
    liabilities_to_equity = ratio(liabilities, equity)
    free_cash_flow = (
        operating_cash_flow - capital_expenditure
        if operating_cash_flow is not None and capital_expenditure is not None
        else None
    )

    metrics = tuple(
        metric
        for metric in (
            revenue_metric,
            net_income_metric,
            assets_metric,
            liabilities_metric,
            equity_metric,
            ocf_metric,
            capex_metric,
        )
        if metric is not None
    )
    return FundamentalsAnalysis(
        ticker=normalized,
        revenue=revenue,
        revenue_growth=revenue_growth,
        net_income=net_income,
        net_margin=net_margin,
        assets=assets,
        liabilities=liabilities,
        equity=equity,
        liabilities_to_equity=liabilities_to_equity,
        operating_cash_flow=operating_cash_flow,
        capital_expenditure=capital_expenditure,
        free_cash_flow=free_cash_flow,
        metrics=metrics,
        reads=build_reads(revenue_growth, net_margin, liabilities_to_equity, free_cash_flow),
        source=source,
        operating_income=value_of(op_income_metric),
        diluted_eps=eps_value,
        shares_outstanding=shares_value,
    )


def analyze_yahoo_timeseries(
    ticker: str,
    payload: dict[str, Any],
    source: str,
) -> FundamentalsAnalysis:
    normalized = normalize_ticker(ticker)
    revenue_metric, prior_revenue = yahoo_latest_and_prior(payload, "annualTotalRevenue", "Revenue")
    net_income_metric, _ = yahoo_latest_and_prior(payload, "annualNetIncome", "Net Income")
    assets_metric, _ = yahoo_latest_and_prior(payload, "annualTotalAssets", "Assets")
    liabilities_metric, _ = yahoo_latest_and_prior(
        payload,
        "annualTotalLiabilitiesNetMinorityInterest",
        "Liabilities",
    )
    equity_metric, _ = yahoo_latest_and_prior(payload, "annualStockholdersEquity", "Stockholders Equity")
    ocf_metric, _ = yahoo_latest_and_prior(payload, "annualOperatingCashFlow", "Operating Cash Flow")
    capex_metric, _ = yahoo_latest_and_prior(payload, "annualCapitalExpenditure", "Capital Expenditure")

    revenue = value_of(revenue_metric)
    prior_revenue_value = value_of(prior_revenue)
    net_income = value_of(net_income_metric)
    assets = value_of(assets_metric)
    liabilities = value_of(liabilities_metric)
    equity = value_of(equity_metric)
    operating_cash_flow = value_of(ocf_metric)
    capital_expenditure = value_of(capex_metric)

    revenue_growth = pct_change(revenue, prior_revenue_value)
    net_margin = pct_ratio(net_income, revenue)
    liabilities_to_equity = ratio(liabilities, equity)
    free_cash_flow = yahoo_free_cash_flow(operating_cash_flow, capital_expenditure)
    metrics = tuple(
        metric
        for metric in (
            revenue_metric,
            net_income_metric,
            assets_metric,
            liabilities_metric,
            equity_metric,
            ocf_metric,
            capex_metric,
        )
        if metric is not None
    )
    return FundamentalsAnalysis(
        ticker=normalized,
        revenue=revenue,
        revenue_growth=revenue_growth,
        net_income=net_income,
        net_margin=net_margin,
        assets=assets,
        liabilities=liabilities,
        equity=equity,
        liabilities_to_equity=liabilities_to_equity,
        operating_cash_flow=operating_cash_flow,
        capital_expenditure=capital_expenditure,
        free_cash_flow=free_cash_flow,
        metrics=metrics,
        reads=build_reads(revenue_growth, net_margin, liabilities_to_equity, free_cash_flow),
        source=source,
    )


def merge_yahoo_payloads(payloads) -> dict[str, Any]:
    result: list[Any] = []
    for payload in payloads:
        values = payload.get("timeseries", {}).get("result", [])
        if isinstance(values, list):
            result.extend(values)
    return {"timeseries": {"result": result}}


def latest_and_prior(
    payload: dict[str, Any],
    tags: tuple[str, ...],
) -> tuple[StatementMetric | None, StatementMetric | None]:
    rows: list[StatementMetric] = []
    for tag in tags:
        rows.extend(metrics_for_tag(payload, tag))
    rows.sort(key=lambda metric: (metric.filed or date.min, metric.period_end or date.min))
    if not rows:
        return None, None
    latest = rows[-1]
    prior_candidates = [
        row
        for row in rows[:-1]
        if row.source_tag == latest.source_tag and row.unit == latest.unit
    ]
    prior = prior_candidates[-1] if prior_candidates else (rows[-2] if len(rows) > 1 else None)
    return latest, prior


def metrics_for_tag(payload: dict[str, Any], tag: str) -> list[StatementMetric]:
    fact = (
        payload.get("facts", {})
        .get("us-gaap", {})
        .get(tag)
    )
    if not isinstance(fact, dict):
        return []
    units = fact.get("units")
    if not isinstance(units, dict):
        return []
    values = units.get("USD") or units.get("shares") or units.get("USD/shares")
    if not isinstance(values, list):
        return []
    metrics: list[StatementMetric] = []
    for row in values:
        if not isinstance(row, dict):
            continue
        form = str(row.get("form") or "")
        if form not in {"10-K", "10-Q"}:
            continue
        value = row.get("val")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        metrics.append(
            StatementMetric(
                label=label_for_tag(tag),
                value=float(value),
                unit="USD",
                period_end=parse_date(row.get("end")),
                filed=parse_date(row.get("filed")),
                source_tag=tag,
            )
        )
    return metrics


def yahoo_latest_and_prior(
    payload: dict[str, Any],
    tag: str,
    label: str,
) -> tuple[StatementMetric | None, StatementMetric | None]:
    rows = yahoo_metrics_for_tag(payload, tag, label)
    rows.sort(key=lambda metric: metric.period_end or date.min)
    if not rows:
        return None, None
    latest = rows[-1]
    prior = rows[-2] if len(rows) > 1 else None
    return latest, prior


def yahoo_metrics_for_tag(
    payload: dict[str, Any],
    tag: str,
    label: str,
) -> list[StatementMetric]:
    result = payload.get("timeseries", {}).get("result", [])
    if not isinstance(result, list):
        return []
    rows: list[StatementMetric] = []
    for result_item in result:
        if not isinstance(result_item, dict):
            continue
        values = result_item.get(tag)
        if not isinstance(values, list):
            continue
        for row in values:
            if not isinstance(row, dict):
                continue
            reported_value = row.get("reportedValue")
            raw = reported_value.get("raw") if isinstance(reported_value, dict) else None
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                continue
            rows.append(
                StatementMetric(
                    label=label,
                    value=float(raw),
                    unit=str(row.get("currencyCode") or "USD"),
                    period_end=parse_date(row.get("asOfDate")),
                    filed=None,
                    source_tag=tag,
                )
            )
    return rows


def yahoo_free_cash_flow(
    operating_cash_flow: float | None,
    capital_expenditure: float | None,
) -> float | None:
    if operating_cash_flow is None or capital_expenditure is None:
        return None
    if capital_expenditure < 0:
        return operating_cash_flow + capital_expenditure
    return operating_cash_flow - capital_expenditure


def format_fundamentals_report(analysis: FundamentalsAnalysis) -> str:
    lines = [
        f"# Fundamentals Snapshot - {analysis.ticker}",
        "",
        "Not investment advice. Statement facts are research inputs for human review.",
        "",
        "## Statement Metrics",
        "| Metric | Value | Period End | Filed | SEC Tag |",
        "|---|---:|---:|---:|---|",
    ]
    for metric in analysis.metrics:
        lines.append(
            f"| {metric.label} | {format_number(metric.value)} {metric.unit} | "
            f"{format_date(metric.period_end)} | {format_date(metric.filed)} | {metric.source_tag} |"
        )
    lines.extend(
        [
            "",
            "## Derived Reads",
            f"- Revenue Growth: {format_pct_or_na(analysis.revenue_growth)}",
            f"- Net Margin: {format_pct_or_na(analysis.net_margin)}",
            f"- Liabilities / Equity: {format_ratio_or_na(analysis.liabilities_to_equity)}",
            f"- Free Cash Flow: {format_number(analysis.free_cash_flow)} USD",
            "",
            "## Interpretation",
        ]
    )
    lines.extend(f"- {read}" for read in analysis.reads)
    lines.extend(["", "## Source", f"- {analysis.source}"])
    return "\n".join(lines)


def build_reads(
    revenue_growth: float | None,
    net_margin: float | None,
    liabilities_to_equity: float | None,
    free_cash_flow: float | None,
) -> tuple[str, ...]:
    reads: list[str] = []
    if revenue_growth is None:
        reads.append("Revenue growth could not be calculated from available annual facts.")
    elif revenue_growth >= 10:
        reads.append(f"Revenue growth is strong at {revenue_growth:.2f}%.")
    elif revenue_growth >= 0:
        reads.append(f"Revenue growth is positive but moderate at {revenue_growth:.2f}%.")
    else:
        reads.append(f"Revenue is contracting at {revenue_growth:.2f}%.")

    if net_margin is None:
        reads.append("Net margin could not be calculated.")
    elif net_margin >= 20:
        reads.append(f"Net margin is high at {net_margin:.2f}%.")
    elif net_margin >= 0:
        reads.append(f"Net margin is positive at {net_margin:.2f}%.")
    else:
        reads.append(f"Net margin is negative at {net_margin:.2f}%.")

    if liabilities_to_equity is None:
        reads.append("Balance-sheet leverage could not be calculated.")
    elif liabilities_to_equity <= 1:
        reads.append(f"Liabilities to equity is contained at {liabilities_to_equity:.2f}x.")
    else:
        reads.append(f"Liabilities to equity is elevated at {liabilities_to_equity:.2f}x.")

    if free_cash_flow is None:
        reads.append("Free cash flow could not be calculated from operating cash flow and capex.")
    elif free_cash_flow > 0:
        reads.append("Free cash flow is positive.")
    else:
        reads.append("Free cash flow is negative.")
    return tuple(reads)


def value_of(metric: StatementMetric | None) -> float | None:
    return metric.value if metric is not None else None


def pct_change(latest: float | None, prior: float | None) -> float | None:
    if latest is None or prior in (None, 0):
        return None
    return (latest - prior) / prior * 100.0


def pct_ratio(numerator: float | None, denominator: float | None) -> float | None:
    base = ratio(numerator, denominator)
    return base * 100.0 if base is not None else None


def ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def parse_date(value: Any) -> date | None:
    if not value:
        return None
    return date.fromisoformat(str(value))


def label_for_tag(tag: str) -> str:
    labels = {
        "Revenues": "Revenue",
        "RevenueFromContractWithCustomerExcludingAssessedTax": "Revenue",
        "SalesRevenueNet": "Revenue",
        "NetIncomeLoss": "Net Income",
        "Assets": "Assets",
        "Liabilities": "Liabilities",
        "StockholdersEquity": "Stockholders Equity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest": "Stockholders Equity",
        "NetCashProvidedByUsedInOperatingActivities": "Operating Cash Flow",
        "PaymentsToAcquirePropertyPlantAndEquipment": "Capital Expenditure",
    }
    return labels.get(tag, tag)


def format_number(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:,.0f}"


def format_pct_or_na(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2f}%"


def format_ratio_or_na(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2f}x"


def format_date(value: date | None) -> str:
    return "N/A" if value is None else value.isoformat()


def extract_diluted_eps(payload: dict[str, Any]) -> float | None:
    facts = payload.get("facts", {}) if isinstance(payload, dict) else {}
    us_gaap = facts.get("us-gaap", {}) if isinstance(facts, dict) else {}
    for tag in ("EarningsPerShareDiluted", "EarningsPerShareBasic"):
        entry = us_gaap.get(tag)
        if not isinstance(entry, dict):
            continue
        units = entry.get("units")
        if not isinstance(units, dict):
            continue
        for unit_key, observations in units.items():
            if "shares" not in str(unit_key).lower():
                continue
            annual = [
                obs
                for obs in observations
                if isinstance(obs, dict)
                and obs.get("form") == "10-K"
                and obs.get("fp") == "FY"
                and obs.get("end")
                and isinstance(obs.get("val"), (int, float))
                and not isinstance(obs.get("val"), bool)
            ]
            if annual:
                annual.sort(key=lambda obs: obs["end"], reverse=True)
                return float(annual[0]["val"])
    return None


def extract_shares_outstanding(payload: dict[str, Any]) -> float | None:
    facts = payload.get("facts", {}) if isinstance(payload, dict) else {}
    dei = facts.get("dei", {}) if isinstance(facts, dict) else {}
    entry = dei.get("EntityCommonStockSharesOutstanding")
    if not isinstance(entry, dict):
        return None
    units = entry.get("units")
    if not isinstance(units, dict):
        return None
    observations = units.get("shares", [])
    valid = [
        obs
        for obs in observations
        if isinstance(obs, dict)
        and obs.get("end")
        and isinstance(obs.get("val"), (int, float))
        and not isinstance(obs.get("val"), bool)
    ]
    if not valid:
        return None
    valid.sort(key=lambda obs: obs["end"], reverse=True)
    return float(valid[0]["val"])


def operating_margin(analysis: FundamentalsAnalysis) -> float | None:
    return pct_ratio(analysis.operating_income, analysis.revenue)


def return_on_equity(analysis: FundamentalsAnalysis) -> float | None:
    return pct_ratio(analysis.net_income, analysis.equity)


def return_on_assets(analysis: FundamentalsAnalysis) -> float | None:
    return pct_ratio(analysis.net_income, analysis.assets)


def trailing_pe(analysis: FundamentalsAnalysis, price: float) -> float | None:
    if price <= 0:
        return None
    eps = analysis.diluted_eps
    if eps is None and analysis.net_income is not None and analysis.shares_outstanding:
        if analysis.shares_outstanding > 0:
            eps = analysis.net_income / analysis.shares_outstanding
    if eps is None or eps <= 0:
        return None
    return price / eps


def quality_score(analysis: FundamentalsAnalysis) -> tuple[float, tuple[str, ...]]:
    notes: list[str] = []
    score = 0.0

    pts, note = _score_quality_roe(return_on_equity(analysis))
    score += pts
    notes.append(note)

    pts, note = _score_quality_margin(operating_margin(analysis), analysis.net_margin)
    score += pts
    notes.append(note)

    pts, note = _score_quality_growth(analysis.revenue_growth)
    score += pts
    notes.append(note)

    pts, note = _score_quality_roa(return_on_assets(analysis))
    score += pts
    notes.append(note)

    pts, note = _score_quality_leverage(analysis.liabilities_to_equity)
    score += pts
    notes.append(note)

    return score, tuple(notes)


def value_score(
    analysis: FundamentalsAnalysis,
    price: float,
    override_pe: float | None = None,
) -> tuple[float, tuple[str, ...]]:
    notes: list[str] = []
    if override_pe is not None and override_pe > 0:
        pe = override_pe
        pe_source = "Yahoo quoteSummary"
    else:
        pe = trailing_pe(analysis, price)
        pe_source = "SEC EPS"
    if pe is None:
        notes.append("Trailing P/E unavailable (negative earnings or missing share data) (0/50)")
        pe_pts = 0.0
    elif pe <= 10:
        pe_pts = 50.0
        notes.append(f"Trailing P/E {pe:.1f} ({pe_source}, cheap) -> 50/50")
    elif pe <= 15:
        pe_pts = 40.0
        notes.append(f"Trailing P/E {pe:.1f} ({pe_source}) -> 40/50")
    elif pe <= 25:
        pe_pts = 30.0
        notes.append(f"Trailing P/E {pe:.1f} ({pe_source}) -> 30/50")
    elif pe <= 40:
        pe_pts = 18.0
        notes.append(f"Trailing P/E {pe:.1f} ({pe_source}) -> 18/50")
    elif pe <= 80:
        pe_pts = 8.0
        notes.append(f"Trailing P/E {pe:.1f} ({pe_source}, expensive) -> 8/50")
    else:
        pe_pts = 0.0
        notes.append(f"Trailing P/E {pe:.1f} ({pe_source}, very expensive) -> 0/50")

    growth = analysis.revenue_growth
    if pe is None or growth is None or growth <= 0:
        peg_pts = 25.0
        notes.append("PEG: not applicable (no growth or earnings) (25/50 default)")
    else:
        peg = pe / growth
        if peg <= 0.5:
            peg_pts, note_peg = 50.0, f"PEG (P/E / growth%) {peg:.2f} (very cheap vs growth) -> 50/50"
        elif peg <= 1.0:
            peg_pts, note_peg = 40.0, f"PEG {peg:.2f} (fair vs growth) -> 40/50"
        elif peg <= 1.5:
            peg_pts, note_peg = 30.0, f"PEG {peg:.2f} -> 30/50"
        elif peg <= 2.0:
            peg_pts, note_peg = 18.0, f"PEG {peg:.2f} -> 18/50"
        elif peg <= 4.0:
            peg_pts, note_peg = 8.0, f"PEG {peg:.2f} (expensive vs growth) -> 8/50"
        else:
            peg_pts, note_peg = 0.0, f"PEG {peg:.2f} (very expensive vs growth) -> 0/50"
        notes.append(note_peg)

    return pe_pts + peg_pts, tuple(notes)


def _score_quality_roe(roe: float | None) -> tuple[float, str]:
    if roe is None:
        return 0.0, "ROE: unavailable (0/25)"
    if roe >= 25.0:
        pts = 25.0
    elif roe >= 18.0:
        pts = 22.0
    elif roe >= 12.0:
        pts = 17.0
    elif roe >= 6.0:
        pts = 10.0
    elif roe >= 0.0:
        pts = 4.0
    else:
        pts = 0.0
    return pts, f"ROE {roe:+.1f}% -> {pts:.0f}/25"


def _score_quality_margin(operating: float | None, net: float | None) -> tuple[float, str]:
    margin = operating if operating is not None else net
    label = "Operating margin" if operating is not None else "Net margin"
    if margin is None:
        return 0.0, f"{label}: unavailable (0/25)"
    if margin >= 25.0:
        pts = 25.0
    elif margin >= 18.0:
        pts = 22.0
    elif margin >= 12.0:
        pts = 17.0
    elif margin >= 6.0:
        pts = 10.0
    elif margin >= 0.0:
        pts = 4.0
    else:
        pts = 0.0
    return pts, f"{label} {margin:+.1f}% -> {pts:.0f}/25"


def _score_quality_growth(growth: float | None) -> tuple[float, str]:
    if growth is None:
        return 0.0, "Revenue growth: unavailable (0/20)"
    if growth >= 30.0:
        pts = 20.0
    elif growth >= 15.0:
        pts = 17.0
    elif growth >= 5.0:
        pts = 12.0
    elif growth >= 0.0:
        pts = 6.0
    else:
        pts = 0.0
    return pts, f"Revenue growth {growth:+.1f}% YoY -> {pts:.0f}/20"


def _score_quality_roa(roa: float | None) -> tuple[float, str]:
    if roa is None:
        return 0.0, "ROA: unavailable (0/15)"
    if roa >= 12.0:
        pts = 15.0
    elif roa >= 7.0:
        pts = 12.0
    elif roa >= 3.0:
        pts = 8.0
    elif roa >= 0.0:
        pts = 3.0
    else:
        pts = 0.0
    return pts, f"ROA {roa:+.1f}% -> {pts:.0f}/15"


def _score_quality_leverage(leverage: float | None) -> tuple[float, str]:
    if leverage is None:
        return 0.0, "Liabilities/Equity: unavailable (0/15)"
    if leverage <= 0.5:
        pts = 15.0
    elif leverage <= 1.0:
        pts = 12.0
    elif leverage <= 2.0:
        pts = 8.0
    elif leverage <= 4.0:
        pts = 4.0
    else:
        pts = 0.0
    return pts, f"Liabilities/Equity {leverage:.2f} -> {pts:.0f}/15"


def default_fetch_json(url: str) -> dict[str, Any]:
    user_agent = os.environ.get(
        "TRADING_COPILOT_CONTACT",
        "trading-copilot lemon1825@naver.com",
    )
    request = Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept-Encoding": "identity",
        },
    )
    with urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))
