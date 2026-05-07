from __future__ import annotations

from dataclasses import dataclass

from .events import EventItem
from .storage import normalize_ticker


@dataclass(frozen=True)
class ForecastSignal:
    ticker: str
    direction: str
    category: str
    earnings_impact: str
    confidence: str
    summary: str
    event: EventItem


def detect_forecast_signals(events: tuple[EventItem, ...]) -> tuple[ForecastSignal, ...]:
    signals: list[ForecastSignal] = []
    for event in events:
        signal = classify_event(event)
        if signal is not None:
            signals.append(signal)
    return tuple(signals)


def classify_event(event: EventItem) -> ForecastSignal | None:
    text = event.title.lower()
    if contains_any(text, GUIDANCE_NEGATIVE):
        return signal(event, "negative", "guidance", "estimate downside", "high")
    if contains_any(text, GUIDANCE_POSITIVE):
        return signal(event, "positive", "guidance", "estimate upside", "high")
    if contains_any(text, CONTRACT_NEGATIVE):
        return signal(event, "negative", "contract", "revenue visibility risk", "high")
    if contains_any(text, CONTRACT_POSITIVE):
        return signal(event, "positive", "contract", "revenue visibility", "high")
    if contains_any(text, DEMAND_NEGATIVE):
        return signal(event, "negative", "demand", "growth downside", "medium")
    if contains_any(text, DEMAND_POSITIVE):
        return signal(event, "positive", "demand", "growth support", "medium")
    if contains_any(text, MARGIN_NEGATIVE):
        return signal(event, "negative", "margin", "margin pressure", "medium")
    if contains_any(text, LEGAL_NEGATIVE):
        return signal(event, "negative", "regulatory", "multiple compression risk", "medium")
    if event.source_type.upper() == "SEC" and event.form.upper() in WATCH_FORMS:
        return signal(event, "watch", "filing", "requires model update check", "medium")
    return None


def signal(
    event: EventItem,
    direction: str,
    category: str,
    earnings_impact: str,
    confidence: str,
) -> ForecastSignal:
    return ForecastSignal(
        ticker=event.ticker,
        direction=direction,
        category=category,
        earnings_impact=earnings_impact,
        confidence=confidence,
        summary=event.title,
        event=event,
    )


def signal_adjustments(
    thesis_direction: str,
    signals: tuple[ForecastSignal, ...],
) -> tuple[int, list[str], list[str]]:
    direction = thesis_direction.lower().strip()
    score_delta = 0
    reasons: list[str] = []
    risks: list[str] = []
    for item in signals:
        if item.direction == "watch":
            risks.append(f"Forecast watch item: {item.category} - {item.summary}.")
            continue
        supports_view = (
            direction == "long" and item.direction == "positive"
        ) or (
            direction == "short" and item.direction == "negative"
        )
        if supports_view:
            score_delta += 1
            reasons.append(
                f"Positive forecast signal: {item.category} - {item.summary}."
            )
        else:
            score_delta -= 1
            risks.append(
                f"Negative forecast signal: {item.category} - {item.summary}."
            )
    return clamp(score_delta, -2, 2), reasons, risks


def format_signals_report(ticker: str, signals: tuple[ForecastSignal, ...]) -> str:
    normalized = normalize_ticker(ticker)
    lines = [
        f"# Earnings Forecast Signals - {normalized}",
        "",
        "No investment recommendation. These are leading indicators for forecast review.",
        "",
        "## Positive Catalysts",
    ]
    lines.extend(signal_lines(signals, "positive"))
    lines.extend(["", "## Risks"])
    lines.extend(signal_lines(signals, "negative"))
    lines.extend(["", "## Watch Items"])
    lines.extend(signal_lines(signals, "watch"))
    lines.extend(
        [
            "",
            "## Earnings Forecast Checklist",
            "- Contract wins/losses: revenue timing, backlog, customer concentration",
            "- Guidance language: revenue, margin, EPS, capex, demand",
            "- Demand signals: order growth, usage, renewal, churn, pipeline",
            "- Margin signals: input costs, cloud capacity, tariff, supply chain",
            "- Regulatory/legal signals: investigations, litigation, consent decrees",
            "- Model action: decide whether estimates, target, or invalidation need updating",
        ]
    )
    return "\n".join(lines)


def signal_lines(signals: tuple[ForecastSignal, ...], direction: str) -> list[str]:
    selected = [item for item in signals if item.direction == direction]
    if not selected:
        return ["- None detected."]
    lines: list[str] = []
    for item in selected:
        lines.append(
            f"- {item.event.published_at.isoformat()} | {item.category} | "
            f"{item.earnings_impact} | {item.summary}"
        )
        lines.append(f"  Source: {item.event.source}")
    return lines


def contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))


GUIDANCE_NEGATIVE = (
    "cuts guidance",
    "cut guidance",
    "lowers guidance",
    "guidance cut",
    "profit warning",
    "warns",
    "misses estimates",
    "demand slows",
    "weak demand",
)
GUIDANCE_POSITIVE = (
    "raises guidance",
    "raised guidance",
    "boosts forecast",
    "raises outlook",
    "beats estimates",
    "raises forecast",
)
CONTRACT_NEGATIVE = (
    "loses contract",
    "lost contract",
    "contract terminated",
    "terminates contract",
    "contract canceled",
    "contract cancelled",
    "contract dispute",
)
CONTRACT_POSITIVE = (
    "wins contract",
    "contract win",
    "contract award",
    "awarded contract",
    "multi-year cloud contract",
    "multi-year deal",
    "customer win",
    "strategic partnership",
)
DEMAND_NEGATIVE = (
    "order slowdown",
    "orders slow",
    "backlog declines",
    "churn rises",
    "renewal rates fall",
)
DEMAND_POSITIVE = (
    "strong demand",
    "orders rise",
    "record backlog",
    "backlog rises",
    "usage growth",
)
MARGIN_NEGATIVE = (
    "margin pressure",
    "costs rise",
    "cost inflation",
    "supply shortage",
    "capacity constraint",
)
LEGAL_NEGATIVE = (
    "investigation",
    "lawsuit",
    "probe",
    "antitrust",
    "regulatory scrutiny",
)
WATCH_FORMS = {"8-K", "10-Q", "10-K", "S-1", "S-3", "DEF 14A"}

