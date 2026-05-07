from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
import json
import os
from typing import Any, Callable, Protocol
from urllib.parse import quote
from urllib.request import Request, urlopen

from .events import EventProvider
from .storage import normalize_ticker


@dataclass(frozen=True)
class FastNewsItem:
    ticker: str
    source_type: str
    title: str
    published_at: datetime
    source: str
    provider: str = ""
    sentiment: str = ""


class FastNewsProvider(Protocol):
    def recent_news(self, ticker: str, limit: int = 10) -> tuple[FastNewsItem, ...]:
        pass


class MarketauxNewsProvider:
    BASE_URL = "https://api.marketaux.com/v1/news/all"

    def __init__(
        self,
        api_key: str | None = None,
        fetch_text: Callable[[str], str] | None = None,
    ):
        self.api_key = api_key if api_key is not None else os.environ.get("MARKETAUX_API_KEY", "")
        self.fetch_text = fetch_text or default_fetch_text

    def recent_news(self, ticker: str, limit: int = 10) -> tuple[FastNewsItem, ...]:
        if not self.api_key:
            raise ValueError("MARKETAUX_API_KEY is not set")
        normalized = normalize_ticker(ticker)
        url = (
            f"{self.BASE_URL}?symbols={quote(normalized)}&filter_entities=true"
            f"&language=en&limit={max(limit, 1)}&api_token={quote(self.api_key)}"
        )
        return parse_marketaux_news(self.fetch_text(url), normalized)


class EventProviderNewsAdapter:
    def __init__(self, provider: EventProvider, source_type: str):
        self.provider = provider
        self.source_type = source_type

    def recent_news(self, ticker: str, limit: int = 10) -> tuple[FastNewsItem, ...]:
        items: list[FastNewsItem] = []
        for event in self.provider.recent_events(ticker, limit=limit):
            items.append(
                FastNewsItem(
                    ticker=event.ticker,
                    source_type=self.source_type,
                    title=event.title,
                    published_at=datetime.combine(
                        event.published_at,
                        time.min,
                        tzinfo=timezone.utc,
                    ),
                    source=event.source,
                    provider=self.source_type,
                    sentiment="",
                )
            )
        return tuple(items)


def collect_fast_news(
    ticker: str,
    providers: tuple[FastNewsProvider, ...],
    limit: int = 20,
) -> tuple[tuple[FastNewsItem, ...], tuple[str, ...]]:
    items: list[FastNewsItem] = []
    data_gaps: list[str] = []
    for provider in providers:
        try:
            items.extend(provider.recent_news(ticker, limit=limit))
        except Exception as exc:
            data_gaps.append(f"{provider.__class__.__name__}: {exc}")
    ranked = sorted(items, key=lambda item: item.published_at, reverse=True)
    return tuple(ranked[:limit]), tuple(data_gaps)


def parse_marketaux_news(text: str, ticker: str) -> tuple[FastNewsItem, ...]:
    normalized = normalize_ticker(ticker)
    payload = json.loads(text)
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return ()
    items: list[FastNewsItem] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        url = str(row.get("url") or "").strip()
        published = parse_datetime(str(row.get("published_at") or ""))
        sentiment = sentiment_for_ticker(row.get("entities"), normalized)
        if title and url and published:
            items.append(
                FastNewsItem(
                    ticker=normalized,
                    source_type="MARKETAUX",
                    title=title,
                    published_at=published,
                    source=url,
                    provider=str(row.get("source") or "Marketaux"),
                    sentiment=sentiment,
                )
            )
    return tuple(items)


def build_fast_news_report(
    ticker: str,
    items: tuple[FastNewsItem, ...],
    data_gaps: tuple[str, ...] = (),
) -> str:
    normalized = normalize_ticker(ticker)
    lines = [
        f"# Fast News Monitor - {normalized}",
        "",
        "Not investment advice. Fast headlines are triggers for review, not conclusions.",
        "",
        "## Latest Items",
    ]
    if not items:
        lines.append("- No news items found.")
    for item in sorted(items, key=lambda value: value.published_at, reverse=True):
        sentiment = f" | sentiment: {item.sentiment}" if item.sentiment else ""
        provider = f" | {item.provider}" if item.provider else ""
        lines.extend(
            [
                f"- {item.published_at.isoformat()} | {item.source_type}{provider}{sentiment}",
                f"  {item.title}",
                f"  Source: {item.source}",
            ]
        )
    if data_gaps:
        lines.extend(["", "## Data Gaps"])
        lines.extend(f"- {gap}" for gap in data_gaps)
    lines.extend(
        [
            "",
            "## Review Rules",
            "- Verify whether the headline changes revenue, margin, guidance, regulation, or funding assumptions.",
            "- Check whether price has already moved before treating the item as an edge.",
            "- Confirm with SEC filings, company releases, or earnings-call transcript when material.",
        ]
    )
    return "\n".join(lines)


def sentiment_for_ticker(entities: Any, ticker: str) -> str:
    if not isinstance(entities, list):
        return ""
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        if str(entity.get("symbol") or "").upper() != ticker:
            continue
        score = entity.get("sentiment_score")
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            return ""
        if score >= 0.2:
            return "positive"
        if score <= -0.2:
            return "negative"
        return "neutral"
    return ""


def parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def default_fetch_text(url: str) -> str:
    request = Request(url, headers={"User-Agent": "trading-copilot/0.1"})
    with urlopen(request, timeout=15) as response:
        return response.read().decode("utf-8")
