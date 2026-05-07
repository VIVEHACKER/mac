from __future__ import annotations

from dataclasses import dataclass

from .events import EventItem
from .market_data import MarketSnapshot, format_signed
from .signals import ForecastSignal, detect_forecast_signals, signal_adjustments
from .storage import Thesis


@dataclass(frozen=True)
class RecommendationResult:
    ticker: str
    recommendation: str
    stance: str
    confidence: str
    score: int
    price: float
    currency: str
    target_price: float | None
    stop_price: float | None
    upside_percent: float | None
    risk_percent: float | None
    reward_risk: float | None
    horizon: str
    context: str
    thesis: Thesis | None
    snapshot: MarketSnapshot
    events: tuple[EventItem, ...]
    signals: tuple[ForecastSignal, ...]
    reasons: tuple[str, ...]
    risks: tuple[str, ...]


def score_recommendation(
    thesis: Thesis | None,
    snapshot: MarketSnapshot,
    target_price: float | None,
    stop_price: float | None,
    horizon: str,
    context: str,
    events: tuple[EventItem, ...] = (),
) -> RecommendationResult:
    signals = detect_forecast_signals(events)
    if thesis is None:
        return RecommendationResult(
            ticker=snapshot.ticker,
            recommendation="Wait",
            stance="insufficient data",
            confidence="low",
            score=0,
            price=snapshot.price,
            currency=snapshot.currency,
            target_price=target_price,
            stop_price=stop_price,
            upside_percent=None,
            risk_percent=None,
            reward_risk=None,
            horizon=horizon,
            context=context,
            thesis=None,
            snapshot=snapshot,
            events=events,
            signals=signals,
            reasons=("No recommendation without a stored thesis.",),
            risks=("Missing stored thesis and invalidation criteria.",),
        )

    direction = thesis.direction.lower().strip()
    if direction == "short":
        return _score_short(thesis, snapshot, target_price, stop_price, horizon, context, events, signals)
    return _score_long(thesis, snapshot, target_price, stop_price, horizon, context, events, signals)


def _score_long(
    thesis: Thesis,
    snapshot: MarketSnapshot,
    target_price: float | None,
    stop_price: float | None,
    horizon: str,
    context: str,
    events: tuple[EventItem, ...],
    signals: tuple[ForecastSignal, ...],
) -> RecommendationResult:
    upside = directional_return(snapshot.price, target_price) if target_price else None
    risk = long_stop_risk(snapshot.price, stop_price) if stop_price else None
    reward_risk = ratio(upside, risk)
    score = 1
    reasons = ["Stored thesis direction is long."]
    risks = [f"Invalidation: {thesis.invalidation}"]

    if upside is None:
        risks.append("No target price supplied.")
        score -= 1
    elif upside >= 15:
        score += 2
        reasons.append(f"Target implies {upside:.1f}% upside.")
    elif upside >= 5:
        score += 1
        reasons.append(f"Target implies {upside:.1f}% upside.")
    else:
        score -= 2
        risks.append(f"Target implies only {upside:.1f}% upside.")

    if risk is None or stop_price is None or stop_price >= snapshot.price:
        score -= 1
        risks.append("No valid downside stop below current price supplied.")
    else:
        reasons.append(f"Stop defines {risk:.1f}% downside risk.")
        risks.append("Stop must be honored; gaps can exceed planned risk.")

    if reward_risk is not None:
        if reward_risk >= 2:
            score += 2
            reasons.append(f"Reward/risk is {reward_risk:.1f}x.")
        elif reward_risk < 1:
            score -= 1
            risks.append(f"Reward/risk is only {reward_risk:.1f}x.")

    if abs(snapshot.change_percent) >= 5:
        score -= 1
        risks.append("Large latest-session move can make entry timing fragile.")

    event_penalty, event_risks = event_risk_adjustments(events)
    if event_penalty:
        score -= event_penalty
        risks.extend(event_risks)

    signal_delta, signal_reasons, signal_risks = signal_adjustments("long", signals)
    score += signal_delta
    reasons.extend(signal_reasons)
    risks.extend(signal_risks)

    recommendation, stance = classify_long(score)
    return build_result(
        thesis,
        snapshot,
        recommendation,
        stance,
        score,
        target_price,
        stop_price,
        upside,
        risk,
        reward_risk,
        horizon,
        context,
        events,
        signals,
        reasons,
        risks,
    )


def _score_short(
    thesis: Thesis,
    snapshot: MarketSnapshot,
    target_price: float | None,
    stop_price: float | None,
    horizon: str,
    context: str,
    events: tuple[EventItem, ...],
    signals: tuple[ForecastSignal, ...],
) -> RecommendationResult:
    downside = short_capture(snapshot.price, target_price) if target_price else None
    risk = short_stop_risk(snapshot.price, stop_price) if stop_price else None
    reward_risk = ratio(downside, risk)
    score = 1
    reasons = ["Stored thesis direction is short."]
    risks = [f"Invalidation: {thesis.invalidation}"]

    if downside is None:
        risks.append("No target price supplied.")
        score -= 1
    elif downside >= 15:
        score += 2
        reasons.append(f"Target implies {downside:.1f}% downside capture.")
    elif downside >= 5:
        score += 1
        reasons.append(f"Target implies {downside:.1f}% downside capture.")
    else:
        score -= 2
        risks.append(f"Target implies only {downside:.1f}% downside capture.")

    if risk is None or stop_price is None or stop_price <= snapshot.price:
        score -= 1
        risks.append("No valid upside stop above current price supplied.")
    else:
        reasons.append(f"Stop defines {risk:.1f}% upside risk.")
        risks.append("Stop must be honored; gaps can exceed planned risk.")

    if reward_risk is not None:
        if reward_risk >= 2:
            score += 2
            reasons.append(f"Reward/risk is {reward_risk:.1f}x.")
        elif reward_risk < 1:
            score -= 1
            risks.append(f"Reward/risk is only {reward_risk:.1f}x.")

    event_penalty, event_risks = event_risk_adjustments(events)
    if event_penalty:
        score -= event_penalty
        risks.extend(event_risks)

    signal_delta, signal_reasons, signal_risks = signal_adjustments("short", signals)
    score += signal_delta
    reasons.extend(signal_reasons)
    risks.extend(signal_risks)

    recommendation, stance = classify_short(score)
    return build_result(
        thesis,
        snapshot,
        recommendation,
        stance,
        score,
        target_price,
        stop_price,
        downside,
        risk,
        reward_risk,
        horizon,
        context,
        events,
        signals,
        reasons,
        risks,
    )


def build_result(
    thesis: Thesis,
    snapshot: MarketSnapshot,
    recommendation: str,
    stance: str,
    score: int,
    target_price: float | None,
    stop_price: float | None,
    upside_percent: float | None,
    risk_percent: float | None,
    reward_risk: float | None,
    horizon: str,
    context: str,
    events: tuple[EventItem, ...],
    signals: tuple[ForecastSignal, ...],
    reasons: list[str],
    risks: list[str],
) -> RecommendationResult:
    confidence = "high" if reward_risk is not None and score >= 4 else "medium" if score >= 2 else "low"
    return RecommendationResult(
        ticker=snapshot.ticker,
        recommendation=recommendation,
        stance=stance,
        confidence=confidence,
        score=score,
        price=snapshot.price,
        currency=snapshot.currency,
        target_price=target_price,
        stop_price=stop_price,
        upside_percent=upside_percent,
        risk_percent=risk_percent,
        reward_risk=reward_risk,
        horizon=horizon,
        context=context,
        thesis=thesis,
        snapshot=snapshot,
        events=events,
        signals=signals,
        reasons=tuple(reasons),
        risks=tuple(risks),
    )


def build_recommendation_report(result: RecommendationResult) -> str:
    thesis_lines = (
        [
            f"Direction: {result.thesis.direction}",
            f"Statement: {result.thesis.statement}",
            f"Invalidation: {result.thesis.invalidation}",
        ]
        if result.thesis
        else ["No stored thesis."]
    )
    return "\n".join(
        [
            f"# Investment View - {result.ticker}",
            "",
            "Not investment advice. This is a research-only recommendation draft for human review.",
            "",
            f"Recommendation: {result.recommendation}",
            f"Stance: {result.stance}",
            f"Confidence: {result.confidence}",
            f"Score: {result.score}",
            "",
            "## Market Data",
            f"Price: {result.price:.2f} {result.currency}",
            f"Change: {format_signed(result.snapshot.change)} ({format_signed(result.snapshot.change_percent)}%)",
            f"As Of: {result.snapshot.as_of.isoformat()}",
            f"Source: {result.snapshot.source}",
            "",
            "## Assumptions",
            f"Target Price: {format_optional_price(result.target_price, result.currency)}",
            f"Stop Price: {format_optional_price(result.stop_price, result.currency)}",
            f"Reward/Risk: {format_optional_ratio(result.reward_risk)}",
            f"Horizon: {result.horizon}",
            f"Context: {result.context or 'None provided'}",
            "",
            "## Stored Thesis",
            *thesis_lines,
            "",
            "## Reasons",
            *bullet_lines(result.reasons),
            "",
            "## Risks And Checks",
            *bullet_lines(result.risks),
            "- Verify all assumptions and sources before acting.",
            "",
            "## Recent Events",
            *event_lines(result.events),
            "",
            "## Forecast Signals",
            *forecast_signal_lines(result.signals),
            "",
            "## Human Approval Gate",
            "- User must approve or reject this view after reviewing the evidence.",
            "- No order routing, broker API call, or trade execution is available here.",
        ]
    )


def directional_return(current: float | None, target: float | None) -> float | None:
    if current is None or target is None or current == 0:
        return None
    return (target - current) / current * 100


def short_capture(current: float | None, target: float | None) -> float | None:
    if current is None or target is None or current == 0:
        return None
    return (current - target) / current * 100


def long_stop_risk(current: float | None, stop: float | None) -> float | None:
    if current is None or stop is None or current == 0 or stop >= current:
        return None
    return (current - stop) / current * 100


def short_stop_risk(current: float | None, stop: float | None) -> float | None:
    if current is None or stop is None or current == 0 or stop <= current:
        return None
    return (stop - current) / current * 100


def ratio(reward_percent: float | None, risk_percent: float | None) -> float | None:
    if (
        reward_percent is None
        or risk_percent is None
        or reward_percent <= 0
        or risk_percent <= 0
    ):
        return None
    return reward_percent / risk_percent


def classify_long(score: int) -> tuple[str, str]:
    if score >= 4:
        return "Consider Buy", "bullish"
    if score >= 2:
        return "Watch", "constructive"
    if score <= -1:
        return "Avoid Add", "bearish"
    return "Wait", "neutral"


def classify_short(score: int) -> tuple[str, str]:
    if score >= 4:
        return "Consider Short", "bearish"
    if score >= 2:
        return "Watch", "cautious"
    if score <= -1:
        return "Avoid Short", "bullish"
    return "Wait", "neutral"


def format_optional_price(value: float | None, currency: str) -> str:
    return "Not supplied" if value is None else f"{value:.2f} {currency}"


def format_optional_ratio(value: float | None) -> str:
    return "Not available" if value is None else f"{value:.1f}x"


def bullet_lines(items: tuple[str, ...]) -> list[str]:
    return [f"- {item}" for item in items]


def event_risk_adjustments(events: tuple[EventItem, ...]) -> tuple[int, list[str]]:
    material_forms = {"8-K", "10-K", "10-Q", "S-1", "S-3", "DEF 14A"}
    penalty = 0
    risks: list[str] = []
    sec_events = tuple(event for event in events if event.source_type.upper() == "SEC")
    if any(event.form.upper() in material_forms for event in sec_events):
        penalty += 1
        risks.append(f"Recent SEC filing risk: {summarize_event_forms(sec_events)}.")
    return penalty, risks


def summarize_event_forms(events: tuple[EventItem, ...]) -> str:
    forms = []
    for event in events:
        form = event.form.upper()
        if form and form not in forms:
            forms.append(form)
    return ", ".join(forms) if forms else "recent filing"


def event_lines(events: tuple[EventItem, ...]) -> list[str]:
    if not events:
        return ["- No recent events supplied."]
    lines: list[str] = []
    for event in events:
        lines.append(f"- {event.published_at.isoformat()} | {event.form} | {event.title}")
        lines.append(f"  Source: {event.source}")
    return lines


def forecast_signal_lines(signals: tuple[ForecastSignal, ...]) -> list[str]:
    if not signals:
        return ["- No forecast signals detected."]
    lines: list[str] = []
    for item in signals:
        lines.append(
            f"- {item.direction} | {item.category} | {item.earnings_impact} | {item.summary}"
        )
        lines.append(f"  Source: {item.event.source}")
    return lines
