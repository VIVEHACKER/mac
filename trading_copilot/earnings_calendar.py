from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol


@dataclass(frozen=True)
class EarningsEvent:
    ticker: str
    company_name: str
    report_date: date
    fiscal_date_ending: date | None = None
    estimate: float | None = None
    currency: str = "USD"
    source: str = ""


class EarningsCalendarProvider(Protocol):
    def earnings(self, ticker: str, horizon: str = "3month") -> tuple[EarningsEvent, ...]:
        pass


class HybridEarningsCalendarProvider:
    def earnings(self, ticker: str, horizon: str = "3month") -> tuple[EarningsEvent, ...]:
        return ()


def format_earnings_calendar_report(
    ticker: str,
    events: tuple[EarningsEvent, ...],
    *,
    data_gaps: tuple[str, ...] = (),
) -> str:
    normalized = ticker.upper()
    lines = [
        f"# Earnings Calendar - {normalized}",
        "",
        "Not investment advice. Earnings dates are research inputs for human review.",
        "",
        "## Events",
    ]
    if events:
        lines.extend(
            [
                "| Date | Company | Fiscal Period | EPS Estimate | Source |",
                "|---|---|---|---:|---|",
            ]
        )
        for event in events:
            fiscal = event.fiscal_date_ending.isoformat() if event.fiscal_date_ending else "N/A"
            estimate = "N/A" if event.estimate is None else f"{event.estimate:.2f} {event.currency}"
            lines.append(
                f"| {event.report_date.isoformat()} | {event.company_name} | {fiscal} | "
                f"{estimate} | {event.source or 'N/A'} |"
            )
    else:
        lines.append("- No earnings events available from configured providers.")
    if data_gaps:
        lines.extend(["", "## Data Gaps"])
        lines.extend(f"- {gap}" for gap in data_gaps)
    return "\n".join(lines)
