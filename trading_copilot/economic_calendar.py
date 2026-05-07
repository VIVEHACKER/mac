from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import html
import re
from typing import Callable, Protocol
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


EASTERN = ZoneInfo("America/New_York")


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
    def upcoming_events(
        self,
        start: date,
        days: int,
    ) -> tuple[EconomicEvent, ...]:
        pass


class BlsIcsCalendarProvider:
    URL = "https://www.bls.gov/schedule/news_release/bls.ics"

    def __init__(self, fetch_text: Callable[[str], str] | None = None):
        self.fetch_text = fetch_text or default_fetch_text

    def upcoming_events(
        self,
        start: date,
        days: int,
    ) -> tuple[EconomicEvent, ...]:
        return filter_horizon(parse_bls_ics(self.fetch_text(self.URL), self.URL), start, days)


class FederalReserveFomcProvider:
    URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"

    def __init__(
        self,
        fetch_text: Callable[[str], str] | None = None,
        year: int | None = None,
    ):
        self.fetch_text = fetch_text or default_fetch_text
        self.year = year

    def upcoming_events(
        self,
        start: date,
        days: int,
    ) -> tuple[EconomicEvent, ...]:
        years = {start.year, (start + timedelta(days=days)).year}
        if self.year is not None:
            years = {self.year}
        text = self.fetch_text(self.URL)
        events: list[EconomicEvent] = []
        for year in sorted(years):
            events.extend(parse_fomc_calendar_html(text, year=year))
        return filter_horizon(tuple(events), start, days)


class KnownBls2026CalendarProvider:
    """Official BLS 2026 schedule fallback for environments that block BLS ICS."""

    def upcoming_events(
        self,
        start: date,
        days: int,
    ) -> tuple[EconomicEvent, ...]:
        return filter_horizon(tuple(known_bls_2026_events()), start, days)


class StaticEconomicCalendarProvider:
    def __init__(self, events: tuple[EconomicEvent, ...]):
        self.events = events

    def upcoming_events(
        self,
        start: date,
        days: int,
    ) -> tuple[EconomicEvent, ...]:
        return filter_horizon(self.events, start, days)


def collect_economic_events(
    providers: tuple[EconomicCalendarProvider, ...] | None = None,
    start: date | None = None,
    days: int = 60,
) -> EconomicCalendarResult:
    start = start or date.today()
    providers = providers or (
        FederalReserveFomcProvider(),
        BlsIcsCalendarProvider(),
        KnownBls2026CalendarProvider(),
    )
    events: list[EconomicEvent] = []
    data_gaps: list[str] = []
    for provider in providers:
        try:
            events.extend(provider.upcoming_events(start, days))
        except Exception as exc:
            data_gaps.append(f"{provider.__class__.__name__}: {exc}")
    ranked = sorted(dedupe_events(events), key=lambda event: event.scheduled_for)
    return build_economic_calendar_report(
        events=tuple(ranked),
        data_gaps=tuple(data_gaps),
        start=start,
        end=start + timedelta(days=days),
    )


def build_economic_calendar_report(
    events: tuple[EconomicEvent, ...],
    data_gaps: tuple[str, ...],
    start: date,
    end: date,
) -> EconomicCalendarResult:
    return EconomicCalendarResult(
        events=events,
        data_gaps=data_gaps,
        start=start,
        end=end,
    )


def parse_bls_ics(text: str, source: str) -> tuple[EconomicEvent, ...]:
    events: list[EconomicEvent] = []
    for block in ics_blocks(unfold_ics(text)):
        summary = block.get("SUMMARY", "").strip()
        release = classify_bls_release(summary)
        if release is None:
            continue
        scheduled_for = parse_ics_datetime(block.get("DTSTART"))
        if scheduled_for is None:
            continue
        event_source = block.get("URL") or source
        events.append(
            EconomicEvent(
                name=release[0],
                category=release[1],
                importance=release[2],
                scheduled_for=scheduled_for,
                source=event_source,
                notes="BLS scheduled news release.",
            )
        )
    return tuple(sorted(events, key=lambda event: event.scheduled_for))


def parse_fomc_calendar_html(text: str, year: int) -> tuple[EconomicEvent, ...]:
    panel = year_panel(text, year)
    if not panel:
        return ()
    events: list[EconomicEvent] = []
    row_pattern = re.compile(
        r'<div[^>]*fomc-meeting[^>]*>.*?'
        r'<div[^>]*fomc-meeting__month[^>]*>\s*<strong>(?P<month>[^<]+)</strong>.*?'
        r'<div[^>]*fomc-meeting__date[^>]*>(?P<date>[^<]+)</div>',
        re.IGNORECASE | re.DOTALL,
    )
    for match in row_pattern.finditer(panel):
        month_name = clean_html(match.group("month"))
        date_text = clean_html(match.group("date")).replace("*", "")
        decision_day = decision_day_from_range(date_text)
        month_number = month_to_number(month_name)
        if month_number is None or decision_day is None:
            continue
        meeting_range = f"{month_name} {date_text}"
        events.append(
            EconomicEvent(
                name="FOMC Rate Decision",
                category="fed",
                importance="critical",
                scheduled_for=datetime(year, month_number, decision_day, 14, 0, tzinfo=EASTERN),
                source=FederalReserveFomcProvider.URL,
                notes=f"{meeting_range}; policy statement usually 2:00pm ET, press conference usually 2:30pm ET.",
            )
        )
    return tuple(events)


def format_economic_calendar_report(result: EconomicCalendarResult) -> str:
    lines = [
        f"# Economic Release Calendar - {result.start.isoformat()} to {result.end.isoformat()}",
        "",
        "Not investment advice. This calendar is a planning checklist for market-moving macro events.",
        "",
        "## Upcoming Critical Events",
    ]
    if not result.events:
        lines.append("- No economic events found.")
    for event in result.events:
        lines.extend(
            [
                f"- {event.scheduled_for.isoformat()} | {event.importance.upper()} | {event.category} | {event.name}",
                f"  Notes: {event.notes or 'None'}",
                f"  Source: {event.source}",
            ]
        )
    if result.data_gaps:
        lines.extend(["", "## Data Gaps"])
        lines.extend(f"- {gap}" for gap in result.data_gaps)
    lines.extend(
        [
            "",
            "## Trading Prep",
            "- Before labor releases: check consensus payrolls, unemployment rate, wage growth, and participation rate.",
            "- Before CPI/PPI: check headline/core MoM, YoY, shelter, services ex-shelter, and energy/base effects.",
            "- Before FOMC: check implied Fed funds probabilities, SEP/dot plot timing, statement language, and press conference risk.",
            "- Reduce position-size decisions to pre-defined scenarios before the release.",
        ]
    )
    return "\n".join(lines)


def filter_horizon(
    events: tuple[EconomicEvent, ...],
    start: date,
    days: int,
) -> tuple[EconomicEvent, ...]:
    start_dt = datetime.combine(start, time.min, tzinfo=EASTERN)
    end_dt = datetime.combine(start + timedelta(days=days), time.max, tzinfo=EASTERN)
    return tuple(event for event in events if start_dt <= event.scheduled_for <= end_dt)


def known_bls_2026_events() -> list[EconomicEvent]:
    events: list[EconomicEvent] = []
    schedules = [
        (
            "Employment Situation",
            "labor",
            "critical",
            "https://www.bls.gov/schedule/news_release/empsit.htm",
            ((5, 8), (6, 5), (7, 2), (8, 7), (9, 4), (10, 2), (11, 6), (12, 4)),
        ),
        (
            "Consumer Price Index",
            "inflation",
            "critical",
            "https://www.bls.gov/schedule/news_release/cpi.htm",
            ((5, 12), (6, 10), (7, 14), (8, 12), (9, 11), (10, 14), (11, 10), (12, 10)),
        ),
        (
            "Producer Price Index",
            "inflation",
            "high",
            "https://www.bls.gov/schedule/news_release/ppi.htm",
            ((5, 13), (6, 11), (7, 15), (8, 13), (9, 10), (10, 15), (11, 13), (12, 15)),
        ),
        (
            "Job Openings and Labor Turnover Survey",
            "labor",
            "high",
            "https://www.bls.gov/schedule/news_release/jolts.htm",
            ((6, 2), (6, 30), (8, 4), (9, 1), (9, 29), (11, 3), (12, 1)),
        ),
    ]
    for name, category, importance, source, month_days in schedules:
        for month, day in month_days:
            events.append(
                EconomicEvent(
                    name=name,
                    category=category,
                    importance=importance,
                    scheduled_for=datetime(2026, month, day, 8 if name != "Job Openings and Labor Turnover Survey" else 10, 30 if name != "Job Openings and Labor Turnover Survey" else 0, tzinfo=EASTERN),
                    source=source,
                    notes="BLS 2026 official schedule fallback; verify if BLS updates the calendar.",
                )
            )
    return events


def dedupe_events(events: list[EconomicEvent]) -> tuple[EconomicEvent, ...]:
    seen: set[tuple[str, datetime]] = set()
    result: list[EconomicEvent] = []
    for event in events:
        key = (event.name, event.scheduled_for)
        if key in seen:
            continue
        seen.add(key)
        result.append(event)
    return tuple(result)


def unfold_ics(text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in text.replace("\r\n", "\n").split("\n"):
        if raw_line.startswith((" ", "\t")) and lines:
            lines[-1] += raw_line[1:]
        else:
            lines.append(raw_line)
    return lines


def ics_blocks(lines: list[str]) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in lines:
        if line == "BEGIN:VEVENT":
            current = {}
            continue
        if line == "END:VEVENT":
            if current is not None:
                blocks.append(current)
            current = None
            continue
        if current is None or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.split(";", 1)[0].upper()
        current[key] = value.strip()
    return blocks


def classify_bls_release(summary: str) -> tuple[str, str, str] | None:
    normalized = summary.lower()
    if "employment situation" in normalized:
        return ("Employment Situation", "labor", "critical")
    if "consumer price index" in normalized:
        return ("Consumer Price Index", "inflation", "critical")
    if "producer price index" in normalized:
        return ("Producer Price Index", "inflation", "high")
    if "job openings and labor turnover" in normalized or "jolts" in normalized:
        return ("Job Openings and Labor Turnover Survey", "labor", "high")
    if "real earnings" in normalized:
        return ("Real Earnings", "labor/inflation", "medium")
    return None


def parse_ics_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    stripped = value.strip()
    if re.fullmatch(r"\d{8}", stripped):
        parsed_date = date.fromisoformat(f"{stripped[:4]}-{stripped[4:6]}-{stripped[6:8]}")
        return datetime.combine(parsed_date, time.min, tzinfo=timezone.utc)
    if stripped.endswith("Z"):
        stripped = stripped[:-1]
    parsed = datetime.strptime(stripped, "%Y%m%dT%H%M%S")
    return parsed.replace(tzinfo=timezone.utc)


def year_panel(text: str, year: int) -> str:
    start_match = re.search(rf">{year} FOMC Meetings<", text)
    if not start_match:
        return ""
    next_match = re.search(r"<div class=\"panel panel-default\">", text[start_match.end():])
    end = start_match.end() + next_match.start() if next_match else len(text)
    return text[start_match.start():end]


def clean_html(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", value)).strip()


def decision_day_from_range(value: str) -> int | None:
    numbers = [int(match) for match in re.findall(r"\d+", value)]
    return numbers[-1] if numbers else None


def month_to_number(value: str) -> int | None:
    months = {
        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
    }
    return months.get(value.strip().lower())


def default_fetch_text(url: str) -> str:
    request = Request(url, headers={"User-Agent": "trading-copilot/0.1"})
    with urlopen(request, timeout=15) as response:
        return response.read().decode("utf-8", errors="ignore")
