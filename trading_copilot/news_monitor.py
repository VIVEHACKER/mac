from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from .events import EventItem, EventProvider


@dataclass(frozen=True)
class FastNewsItem:
    ticker: str
    source_type: str
    title: str
    published_at: datetime
    source: str
    provider: str
    sentiment: str = "neutral"


class FastNewsProvider(Protocol):
    def recent_news(self, ticker: str, limit: int = 10) -> tuple[FastNewsItem, ...]:
        pass


class MarketauxNewsProvider:
    def recent_news(self, ticker: str, limit: int = 10) -> tuple[FastNewsItem, ...]:
        return ()


class EventProviderNewsAdapter:
    def __init__(self, provider: EventProvider, provider_name: str):
        self.provider = provider
        self.provider_name = provider_name

    def recent_news(self, ticker: str, limit: int = 10) -> tuple[FastNewsItem, ...]:
        return tuple(event_to_fast_news(event, self.provider_name) for event in self.provider.recent_events(ticker, limit))


def collect_fast_news(
    ticker: str,
    providers: tuple[FastNewsProvider, ...],
    *,
    limit: int = 20,
) -> tuple[tuple[FastNewsItem, ...], tuple[str, ...]]:
    items: list[FastNewsItem] = []
    data_gaps: list[str] = []
    for provider in providers:
        try:
            items.extend(provider.recent_news(ticker, limit=limit))
        except Exception as exc:
            data_gaps.append(f"{provider.__class__.__name__}: {exc}")
    items.sort(key=lambda item: item.published_at, reverse=True)
    return tuple(items[:limit]), tuple(data_gaps)


def build_fast_news_report(
    ticker: str,
    items: tuple[FastNewsItem, ...],
    *,
    data_gaps: tuple[str, ...] = (),
) -> str:
    normalized = ticker.upper()
    lines = [
        f"# Fast News Monitor - {normalized}",
        "",
        "Not investment advice. Headlines are research inputs for human review.",
        "",
        "## Headlines",
    ]
    if items:
        for item in items:
            lines.append(
                f"- {item.published_at.isoformat()} [{item.provider}/{item.sentiment}] "
                f"{item.title} | Source: {item.source}"
            )
    else:
        lines.append("- No fast-news items available.")
    if data_gaps:
        lines.extend(["", "## Data Gaps"])
        lines.extend(f"- {gap}" for gap in data_gaps)
    return "\n".join(lines)


def event_to_fast_news(event: EventItem, provider_name: str) -> FastNewsItem:
    published = datetime.combine(event.published_at, datetime.min.time(), tzinfo=timezone.utc)
    return FastNewsItem(
        ticker=event.ticker,
        source_type=event.source_type,
        title=event.title,
        published_at=published,
        source=event.source,
        provider=provider_name,
    )
