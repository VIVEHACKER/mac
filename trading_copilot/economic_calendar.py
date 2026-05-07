from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Protocol


@dataclass(frozen=True)
class EconomicEvent:
    name: str
    category: str
    importance: str
    scheduled_for: datetime
    source: str
    notes: str = ""


@dataclass(frozen=True)
class EconomicCalendarResult:
    events: tuple[EconomicEvent, ...]
    data_gaps: tuple[str, ...]
    start: date
    end: date


class EconomicCalendarProvider(Protocol):
    def upcoming_events(self, start: date, days: int) -> tuple[EconomicEvent, ...]:
        pass


def collect_economic_events(
    providers: tuple[EconomicCalendarProvider, ...] | None = None,
    *,
    start: date | None = None,
    days: int = 60,
) -> EconomicCalendarResult:
    start_date = start or date.today()
    end_date = start_date + timedelta(days=max(days, 0))
    events: list[EconomicEvent] = []
    data_gaps: list[str] = []
    for provider in providers or (StaticMajorReleaseProvider(),):
        try:
            events.extend(provider.upcoming_events(start_date, days))
        except Exception as exc:
            data_gaps.append(f"{provider.__class__.__name__}: {exc}")
    return EconomicCalendarResult(
        events=tuple(sorted(events, key=lambda item: item.scheduled_for)),
        data_gaps=tuple(data_gaps),
        start=start_date,
        end=end_date,
    )


class StaticMajorReleaseProvider:
    def upcoming_events(self, start: date, days: int) -> tuple[EconomicEvent, ...]:
        return ()


def format_economic_calendar_report(result: EconomicCalendarResult) -> str:
    lines = [
        "# Economic Release Calendar",
        "",
        "Not investment advice. Macro dates are research inputs for human review.",
        "",
        f"Window: {result.start.isoformat()} to {result.end.isoformat()}",
        "",
        "## Events",
    ]
    if result.events:
        lines.extend(
            [
                "| Date/Time UTC | Event | Category | Importance | Notes | Source |",
                "|---|---|---|---|---|---|",
            ]
        )
        for event in result.events:
            scheduled = event.scheduled_for.astimezone(timezone.utc).isoformat()
            lines.append(
                f"| {scheduled} | {event.name} | {event.category} | {event.importance} | "
                f"{event.notes or 'N/A'} | {event.source} |"
            )
    else:
        lines.append("- No economic release events available from configured providers.")
    if result.data_gaps:
        lines.extend(["", "## Data Gaps"])
        lines.extend(f"- {gap}" for gap in result.data_gaps)
    return "\n".join(lines)
