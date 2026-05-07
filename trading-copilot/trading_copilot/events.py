from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from email.utils import parsedate_to_datetime
import json
import os
from typing import Any, Callable, Protocol
from urllib.parse import quote
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from .storage import normalize_ticker


class EventProvider(Protocol):
    def recent_events(self, ticker: str, limit: int = 5) -> tuple["EventItem", ...]:
        pass


@dataclass(frozen=True)
class EventItem:
    ticker: str
    source_type: str
    title: str
    published_at: date
    source: str
    form: str = ""


class EventDataError(RuntimeError):
    pass


class SecEdgarProvider:
    COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
    SUBMISSIONS_URL = "https://data.sec.gov/submissions"
    ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data"

    def __init__(self, fetch_json: Callable[[str], dict[str, Any]] | None = None):
        self.fetch_json = fetch_json or default_fetch_json
        self._ticker_to_cik: dict[str, int] | None = None

    def recent_events(self, ticker: str, limit: int = 5) -> tuple[EventItem, ...]:
        normalized = normalize_ticker(ticker)
        cik = self._lookup_cik(normalized)
        submission = self.fetch_json(
            f"{self.SUBMISSIONS_URL}/CIK{cik:010d}.json"
        )
        recent = submission.get("filings", {}).get("recent")
        if not isinstance(recent, dict):
            raise EventDataError(f"{normalized}: SEC submissions response has no recent filings")

        events: list[EventItem] = []
        for index, accession in enumerate(get_list(recent, "accessionNumber")):
            if len(events) >= limit:
                break
            form = value_at(recent, "form", index) or "UNKNOWN"
            filing_date = parse_date(value_at(recent, "filingDate", index), normalized)
            primary_document = value_at(recent, "primaryDocument", index) or ""
            description = value_at(recent, "primaryDocDescription", index) or "Filing"
            title = f"{form} filed {filing_date.isoformat()} - {description}"
            events.append(
                EventItem(
                    ticker=normalized,
                    source_type="SEC",
                    title=title,
                    published_at=filing_date,
                    source=sec_archive_url(cik, accession, primary_document),
                    form=form,
                )
            )
        return tuple(events)

    def _lookup_cik(self, ticker: str) -> int:
        if self._ticker_to_cik is None:
            payload = self.fetch_json(self.COMPANY_TICKERS_URL)
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
            raise EventDataError(f"{ticker}: ticker not found in SEC company_tickers") from exc


class NewsRssProvider:
    BASE_URL = "https://news.google.com/rss/search"

    def __init__(self, fetch_text: Callable[[str], str] | None = None):
        self.fetch_text = fetch_text or default_fetch_text

    def recent_events(self, ticker: str, limit: int = 5) -> tuple[EventItem, ...]:
        normalized = normalize_ticker(ticker)
        url = f"{self.BASE_URL}?q={quote(f'{normalized} stock')}&hl=en-US&gl=US&ceid=US:en"
        root = ET.fromstring(self.fetch_text(url))
        events: list[EventItem] = []
        for item in root.findall(".//item"):
            if len(events) >= limit:
                break
            title = child_text(item, "title") or "Untitled news item"
            link = child_text(item, "link") or url
            published = parse_rss_date(child_text(item, "pubDate"))
            events.append(
                EventItem(
                    ticker=normalized,
                    source_type="NEWS",
                    title=title.strip(),
                    published_at=published,
                    source=link.strip(),
                    form="NEWS",
                )
            )
        return tuple(events)


DEFAULT_CONTACT = "trading-copilot lemon1825@naver.com"


def _user_agent() -> str:
    contact = os.environ.get("TRADING_COPILOT_CONTACT", DEFAULT_CONTACT).strip() or DEFAULT_CONTACT
    return contact


def default_fetch_json(url: str) -> dict[str, Any]:
    request = Request(
        url,
        headers={
            "User-Agent": _user_agent(),
            "Accept-Encoding": "identity",
        },
    )
    with urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def default_fetch_text(url: str) -> str:
    request = Request(url, headers={"User-Agent": _user_agent()})
    with urlopen(request, timeout=15) as response:
        return response.read().decode("utf-8")


def get_list(payload: dict[str, Any], key: str) -> list[Any]:
    value = payload.get(key)
    return value if isinstance(value, list) else []


def value_at(payload: dict[str, Any], key: str, index: int) -> str | None:
    values = get_list(payload, key)
    if index >= len(values):
        return None
    value = values[index]
    return str(value) if value is not None else None


def parse_date(value: str | None, ticker: str) -> date:
    if not value:
        raise EventDataError(f"{ticker}: filing is missing filingDate")
    return date.fromisoformat(value)


def parse_rss_date(value: str | None) -> date:
    if not value:
        return date.today()
    return parsedate_to_datetime(value).date()


def child_text(parent: ET.Element, child_name: str) -> str | None:
    child = parent.find(child_name)
    return child.text if child is not None else None


def sec_archive_url(cik: int, accession: str, primary_document: str) -> str:
    accession_no_dashes = accession.replace("-", "")
    document = quote(primary_document)
    return f"{SecEdgarProvider.ARCHIVES_URL}/{cik}/{accession_no_dashes}/{document}"


def format_events_report(ticker: str, events: tuple[EventItem, ...]) -> str:
    normalized = normalize_ticker(ticker)
    lines = [
        f"# Recent SEC Events - {normalized}",
        "",
        "No investment recommendation. These are source events for human review.",
        "",
    ]
    if not events:
        lines.append("- No SEC filings found.")
        return "\n".join(lines)
    for event in events:
        lines.extend(
            [
                f"- {event.published_at.isoformat()} | {event.form} | {event.title}",
                f"  Source: {event.source}",
            ]
        )
    return "\n".join(lines)


def format_news_report(ticker: str, events: tuple[EventItem, ...]) -> str:
    normalized = normalize_ticker(ticker)
    lines = [
        f"# Recent News - {normalized}",
        "",
        "No investment recommendation. These are source events for human review.",
        "",
    ]
    if not events:
        lines.append("- No news items found.")
        return "\n".join(lines)
    for event in events:
        lines.extend(
            [
                f"- {event.published_at.isoformat()} | {event.title}",
                f"  Source: {event.source}",
            ]
        )
    return "\n".join(lines)
