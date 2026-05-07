from __future__ import annotations

import csv
from dataclasses import dataclass, replace
from datetime import date
import html
from io import StringIO
import math
import re
from typing import Callable
from urllib.request import Request, urlopen


KOREAN_TICKERS_STOCKS_URL = "https://www.koreantickers.com/stocks"
KOREAN_TICKERS_REPORTS_URL = "https://www.koreantickers.com/reports"


@dataclass(frozen=True)
class CompanyMetrics:
    symbol: str
    name: str
    market: str
    sector: str
    industry: str = ""
    revenue_growth: float | None = None
    net_margin: float | None = None
    free_cash_flow: float | None = None
    liabilities_to_equity: float | None = None
    market_cap: float | None = None
    pe: float | None = None
    peg: float | None = None
    pbr: float | None = None
    ps: float | None = None
    one_month_return: float | None = None
    three_month_return: float | None = None
    six_month_return: float | None = None
    ytd_return: float | None = None
    report_count: int | None = None
    latest_report_date: str | None = None
    sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScoredCompany:
    symbol: str
    name: str
    market: str
    sector: str
    industry: str
    sector_rank: int
    company_rank: int
    technology_score: float
    business_score: float
    composite_score: float
    read: str
    reasons: tuple[str, ...]
    risks: tuple[str, ...]
    metrics: CompanyMetrics


@dataclass(frozen=True)
class SectorRanking:
    sector: str
    rank: int
    company_count: int
    technology_score: float
    business_score: float
    composite_score: float
    leaders: tuple[ScoredCompany, ...]


@dataclass(frozen=True)
class SectorRankingResult:
    market: str
    sectors: tuple[SectorRanking, ...]
    companies: tuple[ScoredCompany, ...]
    data_gaps: tuple[str, ...]
    sources: tuple[str, ...]
    as_of: date


class KoreanTickersProvider:
    def __init__(
        self,
        fetch_text: Callable[[str], str] | None = None,
        stocks_url: str = KOREAN_TICKERS_STOCKS_URL,
    ):
        self.fetch_text = fetch_text or default_fetch_text
        self.stocks_url = stocks_url

    def metrics(self, market: str = "kr") -> tuple[CompanyMetrics, ...]:
        market_key = market.strip().lower()
        rows = parse_korean_tickers_stocks_html(self.fetch_text(self.stocks_url))
        if market_key in {"kr", "korea"}:
            return rows
        allowed = {"kospi": "KOSPI", "kosdaq": "KOSDAQ"}.get(market_key)
        if allowed is None:
            return rows
        return tuple(row for row in rows if row.market.upper() == allowed)


def rank_sector_companies(
    market: str,
    metrics: tuple[CompanyMetrics, ...],
    *,
    per_sector_limit: int = 5,
    data_gaps: tuple[str, ...] = (),
    as_of: date | None = None,
) -> SectorRankingResult:
    scored = [score_company(metric) for metric in metrics if metric.symbol and metric.name]
    groups: dict[str, list[ScoredCompany]] = {}
    for company in scored:
        groups.setdefault(company.sector or "Unclassified", []).append(company)

    sector_rows: list[tuple[SectorRanking, list[ScoredCompany]]] = []
    for sector, companies in groups.items():
        ranked_companies = sorted(
            companies,
            key=lambda item: (item.composite_score, item.technology_score, item.business_score),
            reverse=True,
        )
        leaders = ranked_companies[: max(per_sector_limit, 1)]
        sector_rows.append(
            (
                SectorRanking(
                    sector=sector,
                    rank=0,
                    company_count=len(ranked_companies),
                    technology_score=average(company.technology_score for company in leaders),
                    business_score=average(company.business_score for company in leaders),
                    composite_score=average(company.composite_score for company in leaders),
                    leaders=(),
                ),
                ranked_companies,
            )
        )

    sector_rows.sort(
        key=lambda item: (
            item[0].composite_score,
            item[0].technology_score,
            item[0].company_count,
        ),
        reverse=True,
    )

    sectors: list[SectorRanking] = []
    companies_out: list[ScoredCompany] = []
    for sector_rank, (sector, companies) in enumerate(sector_rows, start=1):
        ranked_companies = tuple(
            replace(company, sector_rank=sector_rank, company_rank=company_rank)
            for company_rank, company in enumerate(companies, start=1)
        )
        sectors.append(
            replace(
                sector,
                rank=sector_rank,
                leaders=ranked_companies[: max(per_sector_limit, 1)],
            )
        )
        companies_out.extend(ranked_companies)

    return SectorRankingResult(
        market=market.upper(),
        sectors=tuple(sectors),
        companies=tuple(companies_out),
        data_gaps=tuple(data_gaps),
        sources=tuple(dedupe(source for metric in metrics for source in metric.sources)),
        as_of=as_of or date.today(),
    )


def score_company(metric: CompanyMetrics) -> ScoredCompany:
    sector = metric.sector.strip() or "Unclassified"
    technology_score = weighted_average(
        (
            (range_score(metric.revenue_growth, low=-20.0, high=50.0, missing=45.0), 0.35),
            (range_score(metric.net_margin, low=-10.0, high=30.0, missing=45.0), 0.18),
            (report_coverage_score(metric.report_count), 0.15),
            (range_score(metric.three_month_return, low=-30.0, high=80.0, missing=45.0), 0.17),
            (sector_technology_intensity(sector, metric.industry), 0.15),
        )
    )
    business_score = weighted_average(
        (
            (range_score(metric.revenue_growth, low=-20.0, high=40.0, missing=45.0), 0.20),
            (range_score(metric.net_margin, low=-10.0, high=30.0, missing=45.0), 0.25),
            (balance_sheet_score(metric.free_cash_flow, metric.liabilities_to_equity), 0.20),
            (valuation_score(metric.pe, metric.peg, metric.pbr, metric.ps), 0.20),
            (scale_and_coverage_score(metric.market_cap, metric.report_count), 0.15),
        )
    )
    composite_score = technology_score * 0.45 + business_score * 0.55
    reasons, risks = score_notes(metric, technology_score, business_score)
    return ScoredCompany(
        symbol=metric.symbol.upper(),
        name=metric.name,
        market=metric.market,
        sector=sector,
        industry=metric.industry,
        sector_rank=0,
        company_rank=0,
        technology_score=round(technology_score, 1),
        business_score=round(business_score, 1),
        composite_score=round(composite_score, 1),
        read=composite_read(composite_score),
        reasons=tuple(reasons),
        risks=tuple(risks),
        metrics=metric,
    )


def format_sector_ranking_report(result: SectorRankingResult) -> str:
    lines = [
        f"# Sector Company Rankings - {result.market}",
        "",
        "Not investment advice. This ranks companies for research triage, not trade execution.",
        "",
        f"As of: {result.as_of.isoformat()}",
        "",
        "## Sector Rankings",
    ]
    if not result.sectors:
        lines.append("- No sector rankings available.")
    else:
        lines.extend(
            [
                "| Rank | Sector | Companies | Technology Capability | Business Viability | Composite | Leaders |",
                "|---:|---|---:|---:|---:|---:|---|",
            ]
        )
        for sector in result.sectors:
            leaders = ", ".join(company.symbol for company in sector.leaders) or "None"
            lines.append(
                f"| {sector.rank} | {sector.sector} | {sector.company_count} | "
                f"{sector.technology_score:.1f} | {sector.business_score:.1f} | "
                f"{sector.composite_score:.1f} | {leaders} |"
            )

    lines.extend(["", "## Company Rankings by Sector"])
    for sector in result.sectors:
        lines.extend(
            [
                "",
                f"### {sector.rank}. {sector.sector}",
                "| Rank | Symbol | Company | Technology Capability | Business Viability | Composite | Rev Growth | 3M | Reports | Read |",
                "|---:|---|---|---:|---:|---:|---:|---:|---:|---|",
            ]
        )
        for company in sector.leaders:
            metric = company.metrics
            lines.append(
                f"| {company.company_rank} | {company.symbol} | {company.name} | "
                f"{company.technology_score:.1f} | {company.business_score:.1f} | "
                f"{company.composite_score:.1f} | {format_pct_or_na(metric.revenue_growth)} | "
                f"{format_pct_or_na(metric.three_month_return)} | "
                f"{metric.report_count if metric.report_count is not None else 'n/a'} | {company.read} |"
            )
            lines.append(
                f"|  |  | Reasons |  |  |  |  |  |  | {'; '.join(company.reasons)} |"
            )
            if company.risks:
                lines.append(
                    f"|  |  | Risks |  |  |  |  |  |  | {'; '.join(company.risks)} |"
                )

    lines.extend(
        [
            "",
            "## Scoring Model",
            "- Technology Capability: revenue growth, margin/pricing power, sector technology intensity, 3M momentum, and analyst/report coverage.",
            "- Business Viability: growth, margin, free cash flow/leverage, valuation discipline, market scale, and coverage.",
            "- Korean coverage uses KoreanTickers stock and report metadata when available; report count is a coverage signal, not proof of quality.",
        ]
    )
    if result.data_gaps:
        lines.extend(["", "## Data Gaps"])
        lines.extend(f"- {gap}" for gap in result.data_gaps)
    if result.sources:
        lines.extend(["", "## Sources"])
        lines.extend(f"- {source}" for source in result.sources)
    return "\n".join(lines)


def sector_rankings_to_csv(result: SectorRankingResult) -> str:
    output = StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(
        [
            "sector_rank",
            "company_rank",
            "sector",
            "symbol",
            "name",
            "market",
            "technology_score",
            "business_score",
            "composite_score",
            "revenue_growth",
            "net_margin",
            "three_month_return",
            "report_count",
            "read",
            "sources",
        ]
    )
    for company in result.companies:
        metric = company.metrics
        writer.writerow(
            [
                company.sector_rank,
                company.company_rank,
                company.sector,
                company.symbol,
                company.name,
                company.market,
                f"{company.technology_score:.1f}",
                f"{company.business_score:.1f}",
                f"{company.composite_score:.1f}",
                "" if metric.revenue_growth is None else f"{metric.revenue_growth:.4f}",
                "" if metric.net_margin is None else f"{metric.net_margin:.4f}",
                "" if metric.three_month_return is None else f"{metric.three_month_return:.4f}",
                "" if metric.report_count is None else str(metric.report_count),
                company.read,
                "; ".join(metric.sources),
            ]
        )
    return output.getvalue()


def parse_korean_tickers_stocks_html(text: str) -> tuple[CompanyMetrics, ...]:
    rows = re.findall(r"<tr\b[^>]*>(.*?)</tr>", text, flags=re.IGNORECASE | re.DOTALL)
    metrics: list[CompanyMetrics] = []
    for row in rows:
        if "/stock/" not in row:
            continue
        cells = re.findall(r"<td\b[^>]*>(.*?)</td>", row, flags=re.IGNORECASE | re.DOTALL)
        if len(cells) < 16:
            continue
        code_match = re.search(r'href="/stock/(\d{6})"', cells[0])
        if not code_match:
            continue
        code = code_match.group(1)
        market = clean_cell_text(cells[1]).upper()
        name = anchor_text_for_stock(cells[2], code) or code
        sector = first_anchor_text(cells[15]) or "Unclassified"
        report_count = parse_int(first_regex(cells[-1], r"(\d+)\s*(?:<!--.*?-->\s*)?reports?"))
        latest_report_date = first_anchor_text(cells[-1], href_prefix="/reports/")
        symbol = korean_symbol(code, market)
        sources = [
            KOREAN_TICKERS_STOCKS_URL,
            f"{KOREAN_TICKERS_REPORTS_URL}?q={code}",
        ]
        latest_report_href = first_anchor_href(cells[-1], href_prefix="/reports/")
        if latest_report_href:
            sources.append(f"https://www.koreantickers.com{latest_report_href}")
        metrics.append(
            CompanyMetrics(
                symbol=symbol,
                name=name,
                market=market,
                sector=sector,
                revenue_growth=parse_percent(cells[14]),
                market_cap=parse_money_abbrev(cells[4]),
                pe=parse_float_cell(cells[10]),
                pbr=parse_float_cell(cells[11]),
                ps=parse_float_cell(cells[12]),
                peg=parse_float_cell(cells[13]),
                three_month_return=parse_percent(cells[6]),
                ytd_return=parse_percent(cells[7]),
                report_count=report_count,
                latest_report_date=latest_report_date,
                sources=tuple(dedupe(sources)),
            )
        )
    return tuple(metrics)


def weighted_average(items: tuple[tuple[float, float], ...]) -> float:
    total_weight = sum(weight for _, weight in items)
    if total_weight == 0:
        return 0.0
    return sum(value * weight for value, weight in items) / total_weight


def average(values) -> float:
    collected = list(values)
    if not collected:
        return 0.0
    return round(sum(collected) / len(collected), 1)


def range_score(value: float | None, *, low: float, high: float, missing: float) -> float:
    if value is None:
        return missing
    if high <= low:
        return missing
    return clamp((value - low) / (high - low) * 100.0)


def report_coverage_score(report_count: int | None) -> float:
    if report_count is None:
        return 40.0
    return clamp(math.log1p(max(report_count, 0)) / math.log1p(30) * 100.0)


def balance_sheet_score(free_cash_flow: float | None, liabilities_to_equity: float | None) -> float:
    cash_score = 50.0
    if free_cash_flow is not None:
        cash_score = 80.0 if free_cash_flow > 0 else 20.0

    leverage_score = 50.0
    if liabilities_to_equity is not None:
        if liabilities_to_equity <= 0.5:
            leverage_score = 90.0
        elif liabilities_to_equity <= 1.0:
            leverage_score = 75.0
        elif liabilities_to_equity <= 2.0:
            leverage_score = 55.0
        elif liabilities_to_equity <= 4.0:
            leverage_score = 30.0
        else:
            leverage_score = 10.0
    return cash_score * 0.55 + leverage_score * 0.45


def valuation_score(
    pe: float | None,
    peg: float | None,
    pbr: float | None,
    ps: float | None,
) -> float:
    parts: list[float] = []
    if pe is not None and pe > 0:
        if pe <= 10:
            parts.append(85.0)
        elif pe <= 20:
            parts.append(75.0)
        elif pe <= 35:
            parts.append(60.0)
        elif pe <= 60:
            parts.append(38.0)
        else:
            parts.append(15.0)
    if peg is not None and peg > 0:
        if peg <= 0.75:
            parts.append(90.0)
        elif peg <= 1.5:
            parts.append(75.0)
        elif peg <= 2.5:
            parts.append(55.0)
        elif peg <= 4.0:
            parts.append(35.0)
        else:
            parts.append(15.0)
    if pbr is not None and pbr > 0:
        if pbr <= 1.0:
            parts.append(80.0)
        elif pbr <= 3.0:
            parts.append(65.0)
        elif pbr <= 7.0:
            parts.append(45.0)
        else:
            parts.append(22.0)
    if ps is not None and ps > 0:
        if ps <= 1.0:
            parts.append(80.0)
        elif ps <= 4.0:
            parts.append(65.0)
        elif ps <= 10.0:
            parts.append(42.0)
        else:
            parts.append(20.0)
    return average(parts) if parts else 50.0


def scale_and_coverage_score(market_cap: float | None, report_count: int | None) -> float:
    scale_score = 50.0
    if market_cap is not None and market_cap > 0:
        scale_score = clamp((math.log10(market_cap) - 8.0) / 4.0 * 100.0)
    coverage_score = report_coverage_score(report_count)
    return scale_score * 0.65 + coverage_score * 0.35


def sector_technology_intensity(sector: str, industry: str = "") -> float:
    text = f"{sector} {industry}".lower()
    high = (
        "semiconductor",
        "software",
        "cybersecurity",
        "robotics",
        "automation",
        "biotech",
        "biopharma",
        "medical devices",
        "battery",
        "displays",
        "precision",
        "electronics",
        "technology",
        "ai",
    )
    medium = (
        "aerospace",
        "defense",
        "automobiles",
        "electrical",
        "industrial",
        "shipbuilding",
        "clean energy",
        "solar",
        "machinery",
        "materials",
        "telecommunications",
    )
    if any(keyword in text for keyword in high):
        return 90.0
    if any(keyword in text for keyword in medium):
        return 70.0
    return 50.0


def score_notes(
    metric: CompanyMetrics,
    technology_score: float,
    business_score: float,
) -> tuple[list[str], list[str]]:
    reasons: list[str] = []
    risks: list[str] = []
    if metric.revenue_growth is not None:
        if metric.revenue_growth >= 15:
            reasons.append(f"Revenue growth is strong at {metric.revenue_growth:.1f}%.")
        elif metric.revenue_growth < 0:
            risks.append(f"Revenue growth is negative at {metric.revenue_growth:.1f}%.")
    else:
        risks.append("Revenue growth unavailable.")

    if metric.net_margin is not None:
        if metric.net_margin >= 15:
            reasons.append(f"Margin profile supports pricing power at {metric.net_margin:.1f}%.")
        elif metric.net_margin < 0:
            risks.append(f"Margin is negative at {metric.net_margin:.1f}%.")

    if metric.three_month_return is not None and metric.three_month_return >= 20:
        reasons.append(f"3M market confirmation is positive at {metric.three_month_return:.1f}%.")
    if metric.report_count is not None and metric.report_count >= 10:
        reasons.append(f"Broad KoreanTickers coverage: {metric.report_count} reports.")
    if metric.pe is not None and metric.pe > 60:
        risks.append(f"Valuation risk: PER {metric.pe:.1f}.")
    if metric.liabilities_to_equity is not None and metric.liabilities_to_equity > 2:
        risks.append(f"Balance-sheet leverage risk: liabilities/equity {metric.liabilities_to_equity:.1f}x.")
    if metric.report_count == 0:
        risks.append("No KoreanTickers research coverage found.")
    if not reasons:
        reasons.append(f"Balanced proxy score: tech {technology_score:.1f}, business {business_score:.1f}.")
    return reasons, risks


def composite_read(score: float) -> str:
    if score >= 75:
        return "High-priority sector leader"
    if score >= 62:
        return "Positive watchlist candidate"
    if score >= 48:
        return "Needs confirmation"
    return "Weak or incomplete profile"


def clean_cell_text(value: str) -> str:
    no_comments = re.sub(r"<!--.*?-->", "", value, flags=re.DOTALL)
    no_tags = re.sub(r"<[^>]+>", " ", no_comments)
    return re.sub(r"\s+", " ", html.unescape(no_tags)).strip()


def first_anchor_text(value: str, href_prefix: str | None = None) -> str | None:
    pattern = r'<a\b([^>]*)>(.*?)</a>'
    for attrs, body in re.findall(pattern, value, flags=re.IGNORECASE | re.DOTALL):
        if href_prefix is not None:
            href = first_regex(attrs, r'href="([^"]+)"')
            if href is None or not href.startswith(href_prefix):
                continue
        text = clean_cell_text(body)
        if text:
            return text
    return None


def first_anchor_href(value: str, href_prefix: str | None = None) -> str | None:
    for href in re.findall(r'<a\b[^>]*href="([^"]+)"', value, flags=re.IGNORECASE):
        decoded = html.unescape(href)
        if href_prefix is None or decoded.startswith(href_prefix):
            return decoded
    return None


def anchor_text_for_stock(value: str, code: str) -> str | None:
    pattern = rf'<a\b[^>]*href="/stock/{re.escape(code)}"[^>]*>(.*?)</a>'
    match = re.search(pattern, value, flags=re.IGNORECASE | re.DOTALL)
    return clean_cell_text(match.group(1)) if match else None


def first_regex(value: str, pattern: str) -> str | None:
    match = re.search(pattern, value, flags=re.IGNORECASE | re.DOTALL)
    return match.group(1) if match else None


def parse_percent(value: str) -> float | None:
    text = clean_cell_text(value).replace(",", "")
    match = re.search(r"[-+]?\d+(?:\.\d+)?\s*%", text)
    if not match:
        return None
    return float(match.group(0).replace("%", "").strip())


def parse_float_cell(value: str) -> float | None:
    text = clean_cell_text(value).replace(",", "")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not match:
        return None
    return float(match.group(0))


def parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    return int(value.replace(",", "").strip())


def parse_money_abbrev(value: str) -> float | None:
    text = clean_cell_text(value).replace(",", "")
    match = re.search(r"\$([-+]?\d+(?:\.\d+)?)([TMBK]?)", text, flags=re.IGNORECASE)
    if not match:
        return None
    number = float(match.group(1))
    multiplier = {
        "T": 1_000_000_000_000.0,
        "B": 1_000_000_000.0,
        "M": 1_000_000.0,
        "K": 1_000.0,
        "": 1.0,
    }[match.group(2).upper()]
    return number * multiplier


def korean_symbol(code: str, market: str) -> str:
    suffix = "KQ" if market.upper() == "KOSDAQ" else "KS"
    return f"{code}.{suffix}"


def format_pct_or_na(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.2f}%"


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def dedupe(values) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def default_fetch_text(url: str) -> str:
    request = Request(url, headers={"User-Agent": "trading-copilot/0.1"})
    with urlopen(request, timeout=20) as response:
        encoding = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(encoding, errors="replace")


__all__ = [
    "CompanyMetrics",
    "KoreanTickersProvider",
    "ScoredCompany",
    "SectorRanking",
    "SectorRankingResult",
    "format_sector_ranking_report",
    "parse_korean_tickers_stocks_html",
    "rank_sector_companies",
    "sector_rankings_to_csv",
]
