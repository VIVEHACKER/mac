from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, timedelta
from io import StringIO
import json
import os
import re
from typing import Any, Callable, Protocol
from urllib.parse import quote
from urllib.request import Request, urlopen

from .storage import normalize_ticker


@dataclass(frozen=True)
class EarningsEvent:
    ticker: str
    company_name: str
    report_date: date
    fiscal_date_ending: date
    estimate: float | None
    currency: str
    source: str


class EarningsCalendarProvider(Protocol):
    def earnings(
        self,
        ticker: str,
        horizon: str = "3month",
    ) -> tuple[EarningsEvent, ...]:
        pass


class AlphaVantageEarningsCalendarProvider:
    BASE_URL = "https://www.alphavantage.co/query"

    def __init__(
        self,
        api_key: str | None = None,
        fetch_text: Callable[[str], str] | None = None,
    ):
        self.api_key = api_key if api_key is not None else os.environ.get("ALPHAVANTAGE_API_KEY", "")
        self.fetch_text = fetch_text or default_fetch_text

    def earnings(
        self,
        ticker: str,
        horizon: str = "3month",
    ) -> tuple[EarningsEvent, ...]:
        if not self.api_key:
            raise ValueError("ALPHAVANTAGE_API_KEY is not set")
        normalized = normalize_ticker(ticker)
        source = (
            f"{self.BASE_URL}?function=EARNINGS_CALENDAR&symbol={quote(normalized)}"
            f"&horizon={quote(horizon)}&apikey={quote(self.api_key)}"
        )
        return parse_alpha_vantage_earnings_csv(
            self.fetch_text(source),
            ticker=normalized,
            source=source,
        )


class NasdaqEarningsCalendarProvider:
    BASE_URL = "https://api.nasdaq.com/api/calendar/earnings"

    def __init__(
        self,
        fetch_json: Callable[[str], dict[str, Any]] | None = None,
        today: Callable[[], date] | None = None,
    ):
        self.fetch_json = fetch_json or default_fetch_json
        self.today = today or date.today

    def earnings(
        self,
        ticker: str,
        horizon: str = "3month",
    ) -> tuple[EarningsEvent, ...]:
        normalized = normalize_ticker(ticker)
        start = self.today()
        days = horizon_days(horizon)
        found: list[EarningsEvent] = []
        for offset in range(days + 1):
            current = start + timedelta(days=offset)
            source = f"{self.BASE_URL}?date={current.isoformat()}"
            found.extend(parse_nasdaq_earnings_payload(self.fetch_json(source), normalized, source))
            if found:
                break
        return tuple(sorted(found, key=lambda event: event.report_date))


class HybridEarningsCalendarProvider:
    def __init__(
        self,
        primary: EarningsCalendarProvider | None = None,
        fallback: EarningsCalendarProvider | None = None,
    ):
        self.primary = primary or AlphaVantageEarningsCalendarProvider()
        self.fallback = fallback or NasdaqEarningsCalendarProvider()

    def earnings(
        self,
        ticker: str,
        horizon: str = "3month",
    ) -> tuple[EarningsEvent, ...]:
        try:
            events = self.primary.earnings(ticker, horizon=horizon)
            if events:
                return events
        except Exception:
            pass
        return self.fallback.earnings(ticker, horizon=horizon)


def parse_alpha_vantage_earnings_csv(
    text: str,
    ticker: str,
    source: str = "https://www.alphavantage.co/query?function=EARNINGS_CALENDAR",
) -> tuple[EarningsEvent, ...]:
    normalized = normalize_ticker(ticker)
    reader = csv.DictReader(StringIO(text))
    events: list[EarningsEvent] = []
    for row in reader:
        symbol = str(row.get("symbol") or "").upper()
        if symbol != normalized:
            continue
        report_date = parse_date(row.get("reportDate"))
        fiscal_date = parse_date(row.get("fiscalDateEnding"))
        if report_date is None or fiscal_date is None:
            continue
        events.append(
            EarningsEvent(
                ticker=normalized,
                company_name=str(row.get("name") or normalized),
                report_date=report_date,
                fiscal_date_ending=fiscal_date,
                estimate=parse_float(row.get("estimate")),
                currency=str(row.get("currency") or ""),
                source=source,
            )
        )
    return tuple(sorted(events, key=lambda event: event.report_date))


def parse_nasdaq_earnings_json(
    text: str,
    ticker: str,
    source: str,
) -> tuple[EarningsEvent, ...]:
    return parse_nasdaq_earnings_payload(json.loads(text), ticker, source)


def parse_nasdaq_earnings_payload(
    payload: dict[str, Any],
    ticker: str,
    source: str,
) -> tuple[EarningsEvent, ...]:
    normalized = normalize_ticker(ticker)
    data = payload.get("data") if isinstance(payload, dict) else None
    rows = data.get("rows") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return ()
    events: list[EarningsEvent] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").upper()
        if symbol != normalized:
            continue
        report_date = parse_nasdaq_date(row.get("reportDate")) or source_date(source)
        fiscal_date = parse_nasdaq_fiscal_period(row.get("fiscalQuarterEnding"))
        if report_date is None:
            continue
        events.append(
            EarningsEvent(
                ticker=normalized,
                company_name=str(row.get("name") or normalized),
                report_date=report_date,
                fiscal_date_ending=fiscal_date or report_date,
                estimate=parse_money(row.get("epsForecast")),
                currency="USD",
                source=source,
            )
        )
    return tuple(sorted(events, key=lambda event: event.report_date))


def format_earnings_calendar_report(
    ticker: str,
    events: tuple[EarningsEvent, ...],
    data_gaps: tuple[str, ...] = (),
) -> str:
    normalized = normalize_ticker(ticker)
    lines = [
        f"# Earnings Calendar - {normalized}",
        "",
        "Not investment advice. Calendar dates are planning inputs and must be verified before use.",
        "",
        "## Upcoming Earnings",
    ]
    if not events:
        lines.append("- No earnings events found.")
    for event in events:
        estimate = "N/A" if event.estimate is None else f"{event.estimate:.2f} {event.currency}".strip()
        lines.extend(
            [
                f"- {event.report_date.isoformat()} | {event.company_name}",
                f"  Fiscal Period End: {event.fiscal_date_ending.isoformat()} | EPS Estimate: {estimate}",
                f"  Source: {event.source}",
            ]
        )
    if data_gaps:
        lines.extend(["", "## Data Gaps"])
        lines.extend(f"- {gap}" for gap in data_gaps)
    lines.extend(
        [
            "",
            "## Pre-Event Checklist",
            "- Check expected report date, call time, and company IR page.",
            "- Review consensus revenue/EPS, guide-down risk, and whisper expectations.",
            "- Identify contract, regulatory, or margin items that can change forward estimates.",
        ]
    )
    return "\n".join(lines)


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


def parse_nasdaq_date(value: Any) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return date.fromisoformat(text)
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if not match:
        return None
    month, day, year = (int(part) for part in match.groups())
    return date(year, month, day)


def source_date(source: str) -> date | None:
    match = re.search(r"[?&]date=(\d{4}-\d{2}-\d{2})", source)
    if not match:
        return None
    return date.fromisoformat(match.group(1))


def parse_nasdaq_fiscal_period(value: Any) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    match = re.fullmatch(r"([A-Za-z]{3})/(\d{4})", text)
    if not match:
        return None
    month = month_number(match.group(1))
    year = int(match.group(2))
    if month is None:
        return None
    return date(year, month, month_end_day(year, month))


def parse_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def parse_money(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).replace("$", "").replace(",", "").strip()
    if not text or text in {"--", "N/A"}:
        return None
    return float(text)


def horizon_days(horizon: str) -> int:
    mapping = {
        "3month": 90,
        "6month": 180,
        "12month": 365,
    }
    return mapping.get(horizon, 90)


def month_number(value: str) -> int | None:
    months = {
        "jan": 1,
        "feb": 2,
        "mar": 3,
        "apr": 4,
        "may": 5,
        "jun": 6,
        "jul": 7,
        "aug": 8,
        "sep": 9,
        "oct": 10,
        "nov": 11,
        "dec": 12,
    }
    return months.get(value.strip().lower()[:3])


def month_end_day(year: int, month: int) -> int:
    if month == 2:
        leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
        return 29 if leap else 28
    if month in {4, 6, 9, 11}:
        return 30
    return 31


def default_fetch_json(url: str) -> dict[str, Any]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) trading-copilot/0.1",
        "Accept": "application/json,text/plain,*/*",
        "Origin": "https://www.nasdaq.com",
        "Referer": "https://www.nasdaq.com/market-activity/earnings",
    }
    request = Request(url, headers=headers)
    with urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def default_fetch_text(url: str) -> str:
    request = Request(url, headers={"User-Agent": "trading-copilot/0.1"})
    with urlopen(request, timeout=15) as response:
        return response.read().decode("utf-8")
