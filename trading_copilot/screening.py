from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .events import EventItem
from .market_data import MarketDataProvider, MarketSnapshot
from .signals import detect_forecast_signals
from .universe import UniverseMember


class EventLookup(Protocol):
    def events_for(self, ticker: str) -> tuple[EventItem, ...]:
        pass


@dataclass(frozen=True)
class ScreenCandidate:
    symbol: str
    name: str
    market: str
    score: int
    price: float | None
    change_percent: float | None
    reasons: tuple[str, ...]
    risks: tuple[str, ...]
    sources: tuple[str, ...]


def screen_members(
    members: tuple[UniverseMember, ...],
    market_data: MarketDataProvider,
    event_lookup: EventLookup,
    max_tickers: int,
) -> tuple[ScreenCandidate, ...]:
    selected = members if max_tickers == 0 else members[:max_tickers]
    candidates: list[ScreenCandidate] = []
    for member in selected:
        candidates.append(score_member(member, market_data, event_lookup.events_for(member.symbol)))
    return tuple(sorted(candidates, key=lambda item: (item.score, item.change_percent or -999), reverse=True))


def score_member(
    member: UniverseMember,
    market_data: MarketDataProvider,
    events: tuple[EventItem, ...],
) -> ScreenCandidate:
    reasons: list[str] = []
    risks: list[str] = []
    sources: list[str] = []
    score = 0
    snapshot: MarketSnapshot | None = None

    try:
        snapshot = market_data.snapshot(member.symbol)
        sources.append(snapshot.source)
        if snapshot.change_percent >= 2:
            score += 1
            reasons.append(f"Positive price momentum: {snapshot.change_percent:+.2f}%.")
        elif snapshot.change_percent <= -2:
            score -= 1
            risks.append(f"Negative price momentum: {snapshot.change_percent:+.2f}%.")
    except Exception as exc:
        risks.append(f"Quote unavailable: {exc}")

    signals = detect_forecast_signals(events)
    for event in events:
        sources.append(event.source)
    for signal in signals:
        if signal.direction == "positive":
            score += 3
            reasons.append(f"{signal.category} signal: {signal.summary}.")
        elif signal.direction == "negative":
            score -= 3
            risks.append(f"{signal.category} signal: {signal.summary}.")
        else:
            risks.append(f"Watch item: {signal.summary}.")

    return ScreenCandidate(
        symbol=member.symbol,
        name=member.name,
        market=member.market,
        score=score,
        price=snapshot.price if snapshot else None,
        change_percent=snapshot.change_percent if snapshot else None,
        reasons=tuple(reasons) if reasons else ("No positive screen signal detected.",),
        risks=tuple(risks),
        sources=tuple(dedupe(sources)),
    )


def format_screen_report(
    title: str,
    candidates: tuple[ScreenCandidate, ...],
    total_universe: int,
    processed: int,
    capped: bool,
    limit: int | None = None,
) -> str:
    shown = candidates if limit is None else candidates[:limit]
    lines = [
        f"# {title}",
        "",
        "No investment recommendation. This is a ranked research screen for human review.",
        "",
        f"Processed: {processed} / {total_universe}",
        f"Capped: {'yes' if capped else 'no'}",
        "",
        "## Ranked Candidates",
    ]
    if not shown:
        lines.append("- No candidates screened.")
        return "\n".join(lines)
    for index, candidate in enumerate(shown, start=1):
        price = "n/a" if candidate.price is None else f"{candidate.price:.2f}"
        change = "n/a" if candidate.change_percent is None else f"{candidate.change_percent:+.2f}%"
        lines.extend(
            [
                f"### {index}. {candidate.symbol} - {candidate.name}",
                f"- Market: {candidate.market}",
                f"- Score: {candidate.score}",
                f"- Price: {price}",
                f"- Change: {change}",
                f"- Reasons: {'; '.join(candidate.reasons)}",
                f"- Risks: {'; '.join(candidate.risks) if candidate.risks else 'None flagged by screen.'}",
                f"- Sources: {'; '.join(candidate.sources) if candidate.sources else 'None'}",
                "",
            ]
        )
    return "\n".join(lines).rstrip()


def candidates_to_csv(candidates: tuple[ScreenCandidate, ...]) -> str:
    lines = ["symbol,name,market,score,price,change_percent,reasons,risks,sources"]
    for item in candidates:
        lines.append(
            ",".join(
                csv_cell(value)
                for value in (
                    item.symbol,
                    item.name,
                    item.market,
                    str(item.score),
                    "" if item.price is None else f"{item.price:.2f}",
                    "" if item.change_percent is None else f"{item.change_percent:.2f}",
                    "; ".join(item.reasons),
                    "; ".join(item.risks),
                    "; ".join(item.sources),
                )
            )
        )
    return "\n".join(lines) + "\n"


def csv_cell(value: str) -> str:
    escaped = value.replace('"', '""')
    return f'"{escaped}"'


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result

