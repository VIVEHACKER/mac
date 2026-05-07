from __future__ import annotations

from dataclasses import dataclass

from .fundamentals import (
    FundamentalsAnalysis,
    quality_score,
    value_score,
)
from .industry_rotation import IndustryScore
from .macro import MacroDashboard
from .market_data import MarketSnapshot, format_signed
from .metrics import TechnicalProfile
from .pattern_mining import PatternResult
from .signals import ForecastSignal


@dataclass(frozen=True)
class RecommendationFactor:
    name: str
    score: float
    weight: float
    read: str
    sources: tuple[str, ...]


@dataclass(frozen=True)
class MLRecommendationResult:
    ticker: str
    action: str
    confidence: str
    composite_score: float
    alpha_score: float
    risk_score: float
    catalyst_score: float
    macro_fit_score: float
    data_quality_score: float
    suggested_weight_pct: float
    expected_return_low_pct: float
    expected_return_high_pct: float
    expected_max_drawdown_pct: float
    horizon: str
    context: str
    snapshot: MarketSnapshot
    target_price: float | None
    stop_price: float | None
    factors: tuple[RecommendationFactor, ...]
    reasons: tuple[str, ...]
    risks: tuple[str, ...]
    ai_review_packet: tuple[str, ...]
    sources: tuple[str, ...]
    data_gaps: tuple[str, ...]


def build_ml_recommendation(
    *,
    ticker: str,
    snapshot: MarketSnapshot,
    technical: TechnicalProfile,
    macro_dashboard: MacroDashboard,
    fundamentals: FundamentalsAnalysis | None,
    signals: tuple[ForecastSignal, ...],
    pattern_results: tuple[PatternResult, ...],
    sector_score: IndustryScore | None,
    target_price: float | None,
    stop_price: float | None,
    horizon: str,
    context: str,
    risk_budget_pct: float,
    max_position_pct: float,
    data_gaps: tuple[str, ...] = (),
) -> MLRecommendationResult:
    normalized = ticker.strip().upper()
    factors = (
        technical_factor(technical),
        fundamentals_factor(fundamentals, snapshot.price),
        macro_factor(macro_dashboard),
        sector_factor(sector_score),
        catalyst_factor(signals),
        pattern_factor(normalized, pattern_results),
        reward_risk_factor(snapshot.price, target_price, stop_price),
        data_quality_factor(
            fundamentals=fundamentals,
            pattern_results=pattern_results,
            sector_score=sector_score,
            data_gaps=data_gaps,
        ),
    )
    alpha_score = weighted_average(factors)
    risk_score = estimate_risk_score(technical, macro_dashboard, signals, snapshot, sector_score)
    catalyst = next(factor.score for factor in factors if factor.name == "Catalyst")
    macro_fit = next(factor.score for factor in factors if factor.name == "Macro Fit")
    data_quality = next(factor.score for factor in factors if factor.name == "Data Quality")
    composite = clamp(alpha_score - max(0.0, risk_score - 50.0) * 0.45, 0.0, 100.0)
    action = classify_action(composite, risk_score, data_quality)
    confidence = classify_confidence(composite, risk_score, data_quality)
    expected_low, expected_high = expected_return_range(
        snapshot.price,
        target_price,
        stop_price,
        alpha_score,
        risk_score,
        pattern_results,
    )
    expected_drawdown = expected_max_drawdown(technical, risk_score)
    suggested_weight = suggested_position_weight(
        action=action,
        composite_score=composite,
        risk_score=risk_score,
        current_price=snapshot.price,
        stop_price=stop_price,
        risk_budget_pct=risk_budget_pct,
        max_position_pct=max_position_pct,
    )
    reasons = build_reasons(factors, action, suggested_weight)
    risks = build_risks(technical, macro_dashboard, signals, stop_price, snapshot.price, data_gaps)
    sources = tuple(sorted({snapshot.source, *macro_dashboard.sources, *(source for factor in factors for source in factor.sources)}))

    return MLRecommendationResult(
        ticker=normalized,
        action=action,
        confidence=confidence,
        composite_score=composite,
        alpha_score=alpha_score,
        risk_score=risk_score,
        catalyst_score=catalyst,
        macro_fit_score=macro_fit,
        data_quality_score=data_quality,
        suggested_weight_pct=suggested_weight,
        expected_return_low_pct=expected_low,
        expected_return_high_pct=expected_high,
        expected_max_drawdown_pct=expected_drawdown,
        horizon=horizon,
        context=context,
        snapshot=snapshot,
        target_price=target_price,
        stop_price=stop_price,
        factors=factors,
        reasons=reasons,
        risks=risks,
        ai_review_packet=build_ai_review_packet(normalized, factors, pattern_results, signals),
        sources=sources,
        data_gaps=data_gaps,
    )


def technical_factor(technical: TechnicalProfile) -> RecommendationFactor:
    momentum_score = 50.0 + technical.momentum_3m * 120.0 + technical.momentum_12_1 * 60.0
    drawdown_penalty = min(abs(technical.max_drawdown_252d) * 70.0, 20.0)
    score = clamp(momentum_score - drawdown_penalty, 0.0, 100.0)
    read = (
        f"3m momentum {technical.momentum_3m * 100:+.1f}%, "
        f"12-1m momentum {technical.momentum_12_1 * 100:+.1f}%, "
        f"252d max drawdown {technical.max_drawdown_252d * 100:+.1f}%."
    )
    return RecommendationFactor("Technical", score, 0.17, read, ("Yahoo price history",))


def fundamentals_factor(
    fundamentals: FundamentalsAnalysis | None,
    price: float,
) -> RecommendationFactor:
    if fundamentals is None:
        return RecommendationFactor(
            "Fundamentals",
            50.0,
            0.17,
            "Fundamentals unavailable; neutral score used.",
            (),
        )
    quality, quality_notes = quality_score(fundamentals)
    value, value_notes = value_score(fundamentals, price)
    score = clamp((quality + value) / 2.0, 0.0, 100.0)
    notes = tuple(quality_notes[:2] + value_notes[:2])
    return RecommendationFactor("Fundamentals", score, 0.17, " ".join(notes), (fundamentals.source,))


def macro_factor(dashboard: MacroDashboard) -> RecommendationFactor:
    name = dashboard.regime.name
    if "Disinflationary Expansion" in name:
        score = 85.0
    elif "Inflationary Expansion" in name:
        score = 62.0
    elif "Mixed" in name or "Transition" in name:
        score = 50.0
    elif "Slowdown" in name or "Recession" in name:
        score = 25.0
    elif "Late-Cycle" in name or "Stagflation" in name:
        score = 30.0
    else:
        score = 50.0
    return RecommendationFactor("Macro Fit", score, 0.13, f"Current macro regime: {name}.", dashboard.sources)


def sector_factor(sector_score: IndustryScore | None) -> RecommendationFactor:
    if sector_score is None:
        return RecommendationFactor(
            "Sector Fit",
            50.0,
            0.11,
            "Sector/industry rotation proxy unavailable; neutral score used.",
            (),
        )
    score = clamp(
        50.0
        + sector_score.leadership_score * 1.25
        + sector_score.next_leader_score * 0.75
        + sector_score.relative_three_month * 0.25,
        0.0,
        100.0,
    )
    read = (
        f"{sector_score.symbol} {sector_score.name}: leadership {sector_score.leadership_score:+.1f}, "
        f"next-leader {sector_score.next_leader_score:+.1f}, 3m relative {sector_score.relative_three_month:+.1f}%."
    )
    return RecommendationFactor("Sector Fit", score, 0.11, read, (sector_score.source,))


def catalyst_factor(signals: tuple[ForecastSignal, ...]) -> RecommendationFactor:
    positives = sum(1 for signal in signals if signal.direction == "positive")
    negatives = sum(1 for signal in signals if signal.direction == "negative")
    watches = sum(1 for signal in signals if signal.direction == "watch")
    score = clamp(50.0 + positives * 18.0 - negatives * 24.0 - watches * 6.0, 0.0, 100.0)
    if not signals:
        read = "No forecast catalysts detected from supplied news/filings."
    else:
        read = f"{positives} positive, {negatives} negative, {watches} watch forecast signals."
    return RecommendationFactor("Catalyst", score, 0.13, read, tuple(signal.event.source for signal in signals))


def pattern_factor(ticker: str, results: tuple[PatternResult, ...]) -> RecommendationFactor:
    usable = tuple(result for result in results if result.asset.upper() == ticker.upper() and result.samples > 0)
    if not usable:
        return RecommendationFactor("Historical Pattern", 50.0, 0.11, "No usable historical pattern sample; neutral score used.", ())
    best = max(usable, key=lambda result: (result.wilson_lower_95, result.samples, result.average_return))
    quality_penalty = pattern_quality_penalty(best)
    score = clamp(
        30.0 + best.wilson_lower_95 * 0.50 + max(best.average_return, -20.0) - quality_penalty,
        0.0,
        100.0,
    )
    read = (
        f"{best.condition}: {best.wins}/{best.samples} wins, "
        f"Wilson lower {best.wilson_lower_95:.1f}%, avg return {best.average_return:+.1f}%, "
        f"quality penalty {quality_penalty:.1f}."
    )
    return RecommendationFactor("Historical Pattern", score, 0.11, read, best.sources)


def pattern_quality_penalty(result: PatternResult) -> float:
    sample_penalty = max(0.0, 1.0 - min(result.samples / 20.0, 1.0)) * 15.0
    drawdown_penalty = max(0.0, abs(result.worst_drawdown) - 15.0) * 0.35
    return_penalty = max(0.0, -result.worst_return) * 0.75
    return sample_penalty + drawdown_penalty + return_penalty


def reward_risk_factor(
    current_price: float,
    target_price: float | None,
    stop_price: float | None,
) -> RecommendationFactor:
    upside = upside_pct(current_price, target_price)
    risk = downside_risk_pct(current_price, stop_price)
    if upside is None or risk is None:
        return RecommendationFactor("Reward/Risk", 45.0, 0.10, "Target or valid stop is missing.", ())
    rr = upside / risk if risk > 0 else 0.0
    score = clamp(35.0 + rr * 18.0 + min(upside, 40.0) * 0.4, 0.0, 100.0)
    return RecommendationFactor("Reward/Risk", score, 0.10, f"Upside {upside:.1f}%, stop risk {risk:.1f}%, reward/risk {rr:.1f}x.", ())


def data_quality_factor(
    *,
    fundamentals: FundamentalsAnalysis | None,
    pattern_results: tuple[PatternResult, ...],
    sector_score: IndustryScore | None,
    data_gaps: tuple[str, ...],
) -> RecommendationFactor:
    score = 100.0
    issues: list[str] = []
    if fundamentals is None:
        score -= 18.0
        issues.append("fundamentals missing")
    if not pattern_results:
        score -= 14.0
        issues.append("historical pattern evidence missing")
    if sector_score is None:
        score -= 10.0
        issues.append("sector fit missing")
    if data_gaps:
        score -= min(36.0, len(data_gaps) * 12.0)
        issues.append(f"{len(data_gaps)} data gap(s)")
    score = clamp(score, 0.0, 100.0)
    read = "Complete enough for scored research." if not issues else "Quality limits: " + ", ".join(issues) + "."
    return RecommendationFactor("Data Quality", score, 0.08, read, ())


def estimate_risk_score(
    technical: TechnicalProfile,
    dashboard: MacroDashboard,
    signals: tuple[ForecastSignal, ...],
    snapshot: MarketSnapshot,
    sector_score: IndustryScore | None,
) -> float:
    vol_risk = clamp(technical.realized_vol_annualized * 180.0, 0.0, 100.0)
    drawdown_risk = clamp(abs(technical.max_drawdown_252d) * 180.0, 0.0, 100.0)
    macro_risk = 70.0 if "Recession" in dashboard.regime.name or "Stagflation" in dashboard.regime.name else 45.0
    signal_risk = clamp(35.0 + sum(1 for signal in signals if signal.direction == "negative") * 22.0, 0.0, 100.0)
    gap_risk = 75.0 if abs(snapshot.change_percent) >= 7.0 else 35.0
    trend_risk = 85.0 if technical.momentum_3m < -0.08 or technical.max_drawdown_252d < -0.25 else 35.0
    sector_risk = clamp(55.0 - sector_score.leadership_score if sector_score is not None else 55.0, 0.0, 100.0)
    return clamp(
        vol_risk * 0.10
        + drawdown_risk * 0.30
        + macro_risk * 0.10
        + signal_risk * 0.18
        + gap_risk * 0.10
        + trend_risk * 0.20
        + sector_risk * 0.02,
        0.0,
        100.0,
    )


def classify_action(composite_score: float, risk_score: float, data_quality_score: float) -> str:
    if data_quality_score < 55.0 and composite_score >= 58.0:
        return "Watch"
    if data_quality_score < 70.0 and composite_score >= 70.0:
        return "Watch"
    if composite_score >= 70.0 and risk_score < 72.0:
        return "Consider Buy"
    if composite_score >= 58.0 and risk_score < 82.0:
        return "Watch"
    if composite_score < 45.0 or risk_score >= 82.0:
        return "Avoid Add"
    return "Wait"


def classify_confidence(composite_score: float, risk_score: float, data_quality_score: float) -> str:
    if data_quality_score < 70.0:
        return "low"
    if composite_score >= 75.0 and risk_score < 55.0:
        return "high"
    if composite_score >= 55.0 and risk_score < 75.0:
        return "medium"
    return "low"


def expected_return_range(
    current_price: float,
    target_price: float | None,
    stop_price: float | None,
    alpha_score: float,
    risk_score: float,
    pattern_results: tuple[PatternResult, ...],
) -> tuple[float, float]:
    target_upside = upside_pct(current_price, target_price)
    stop_risk = downside_risk_pct(current_price, stop_price)
    pattern_average = max((result.average_return for result in pattern_results), default=0.0)
    model_mid = (alpha_score - 50.0) * 0.35 + pattern_average * 0.35
    high = max(model_mid + 6.0, target_upside if target_upside is not None else model_mid + 6.0)
    low = model_mid - risk_score * 0.12
    if stop_risk is not None:
        low = min(low, -stop_risk)
    return round(low, 2), round(high, 2)


def expected_max_drawdown(technical: TechnicalProfile, risk_score: float) -> float:
    drawdown = min(technical.max_drawdown_252d * 100.0, -risk_score * 0.35)
    return round(drawdown, 2)


def suggested_position_weight(
    *,
    action: str,
    composite_score: float,
    risk_score: float,
    current_price: float,
    stop_price: float | None,
    risk_budget_pct: float,
    max_position_pct: float,
) -> float:
    if action not in {"Consider Buy", "Watch"}:
        return 0.0
    base = max_position_pct * (composite_score / 100.0) * (1.0 - min(risk_score, 90.0) / 140.0)
    stop_risk = downside_risk_pct(current_price, stop_price)
    if stop_risk is not None and stop_risk > 0:
        base = min(base, risk_budget_pct / stop_risk * 100.0)
    if action == "Watch":
        base *= 0.50
    return round(clamp(base, 0.0, max_position_pct), 2)


def build_reasons(
    factors: tuple[RecommendationFactor, ...],
    action: str,
    suggested_weight_pct: float,
) -> tuple[str, ...]:
    strong = [factor for factor in factors if factor.score >= 65.0]
    reasons = [f"{factor.name}: {factor.read}" for factor in strong[:4]]
    reasons.append(f"Action is {action}; suggested research weight is {suggested_weight_pct:.2f}% before human review.")
    return tuple(reasons)


def build_risks(
    technical: TechnicalProfile,
    dashboard: MacroDashboard,
    signals: tuple[ForecastSignal, ...],
    stop_price: float | None,
    current_price: float,
    data_gaps: tuple[str, ...],
) -> tuple[str, ...]:
    risks: list[str] = []
    if technical.max_drawdown_252d < -0.20:
        risks.append(f"Large historical drawdown: {technical.max_drawdown_252d * 100:.1f}% over the lookback window.")
    if technical.realized_vol_annualized > 0.45:
        risks.append(f"High realized volatility: {technical.realized_vol_annualized * 100:.1f}% annualized.")
    if "Recession" in dashboard.regime.name or "Stagflation" in dashboard.regime.name:
        risks.append(f"Macro regime risk: {dashboard.regime.name}.")
    for signal in signals:
        if signal.direction == "negative":
            risks.append(f"Negative forecast signal: {signal.category} - {signal.summary}.")
        elif signal.direction == "watch":
            risks.append(f"Forecast watch item: {signal.category} - {signal.summary}.")
    if downside_risk_pct(current_price, stop_price) is None:
        risks.append("No valid stop below current price; sizing cannot be tied to a defined loss budget.")
    for gap in data_gaps:
        risks.append(f"Data gap: {gap}")
    if not risks:
        risks.append("No major model risk flag was triggered, but model error and regime shift risk remain.")
    return tuple(risks)


def build_ai_review_packet(
    ticker: str,
    factors: tuple[RecommendationFactor, ...],
    pattern_results: tuple[PatternResult, ...],
    signals: tuple[ForecastSignal, ...],
) -> tuple[str, ...]:
    best_pattern = max(pattern_results, key=lambda result: result.wilson_lower_95, default=None)
    packet = [
        f"Review {ticker} as a research-only recommendation. Challenge the model conclusion before agreeing with it.",
        "Check whether positive evidence is already priced in and whether the thesis has a clear invalidation trigger.",
    ]
    packet.extend(f"{factor.name}: score {factor.score:.1f}/100 - {factor.read}" for factor in factors)
    if best_pattern is not None:
        packet.append(
            f"Best historical pattern: {best_pattern.condition}, {best_pattern.wins}/{best_pattern.samples} wins, "
            f"Worst return {best_pattern.worst_return:+.1f}%."
        )
    if signals:
        packet.append("Forecast signals: " + "; ".join(f"{signal.direction} {signal.category}" for signal in signals))
    packet.append("Return a final view only after listing the strongest counterargument.")
    return tuple(packet)


def build_counterarguments(result: MLRecommendationResult) -> tuple[str, ...]:
    out: list[str] = []
    weak = [factor for factor in result.factors if factor.score < 55.0]
    for factor in weak[:3]:
        out.append(f"{factor.name}: {factor.read}")
    if result.risk_score >= 60.0:
        out.append(f"Risk score is elevated at {result.risk_score:.1f}/100.")
    if result.data_quality_score < 80.0:
        out.append(f"Data quality is only {result.data_quality_score:.1f}/100; reduce confidence.")
    if not out:
        out.append("Main counterargument: model inputs can be stale or overfit even when all factors look supportive.")
    return tuple(out)


def format_ml_recommendation_report(result: MLRecommendationResult) -> str:
    lines = [
        f"# ML + AI Recommendation - {result.ticker}",
        "",
        "Not investment advice. This is a research-only model output for human review.",
        "",
        "## Recommendation",
        f"Action: {result.action}",
        f"Confidence: {result.confidence}",
        f"Composite Score: {result.composite_score:.1f}/100",
        f"Alpha Score: {result.alpha_score:.1f}/100",
        f"Risk Score: {result.risk_score:.1f}/100",
        f"Catalyst Score: {result.catalyst_score:.1f}/100",
        f"Macro Fit: {result.macro_fit_score:.1f}/100",
        f"Data Quality: {result.data_quality_score:.1f}/100",
        f"Suggested Weight: {result.suggested_weight_pct:.2f}%",
        f"Expected Return Range: {result.expected_return_low_pct:+.2f}% to {result.expected_return_high_pct:+.2f}%",
        f"Expected Max Drawdown: {result.expected_max_drawdown_pct:+.2f}%",
        f"Horizon: {result.horizon}",
        f"Context: {result.context or 'None provided'}",
        "",
        "## Market Data",
        f"Price: {result.snapshot.price:.2f} {result.snapshot.currency}",
        f"Change: {format_signed(result.snapshot.change)} ({format_signed(result.snapshot.change_percent)}%)",
        f"Target Price: {format_optional_price(result.target_price, result.snapshot.currency)}",
        f"Stop Price: {format_optional_price(result.stop_price, result.snapshot.currency)}",
        f"As Of: {result.snapshot.as_of.isoformat()}",
        "",
        "## Factor Breakdown",
        "| Factor | Score | Weight | Read |",
        "|---|---:|---:|---|",
    ]
    for factor in result.factors:
        lines.append(f"| {factor.name} | {factor.score:.1f} | {factor.weight * 100:.0f}% | {factor.read} |")
    lines.extend(["", "## Why It Scored This Way"])
    lines.extend(f"- {reason}" for reason in result.reasons)
    lines.extend(["", "## Risks / Invalidation Checks"])
    lines.extend(f"- {risk}" for risk in result.risks)
    lines.extend(["", "## Counterargument"])
    lines.extend(f"- {item}" for item in build_counterarguments(result))
    lines.extend(["", "## AI Review Packet"])
    lines.extend(f"- {item}" for item in result.ai_review_packet)
    lines.extend(["", "## Sources"])
    lines.extend(f"- {source}" for source in result.sources if source)
    if result.data_gaps:
        lines.extend(["", "## Data Gaps"])
        lines.extend(f"- {gap}" for gap in result.data_gaps)
    lines.extend(
        [
            "",
            "## Human Approval Gate",
            "- Human must review the evidence, counterargument, sizing, and invalidation before any action.",
            "- No order routing, broker API call, or trade execution is available here.",
            "- A high score is not a guarantee; it is a prioritized research signal.",
        ]
    )
    return "\n".join(lines)


def weighted_average(factors: tuple[RecommendationFactor, ...]) -> float:
    total_weight = sum(factor.weight for factor in factors)
    if total_weight <= 0:
        return 0.0
    return clamp(sum(factor.score * factor.weight for factor in factors) / total_weight, 0.0, 100.0)


def upside_pct(current_price: float, target_price: float | None) -> float | None:
    if target_price is None or current_price <= 0:
        return None
    return (target_price - current_price) / current_price * 100.0


def downside_risk_pct(current_price: float, stop_price: float | None) -> float | None:
    if stop_price is None or current_price <= 0 or stop_price >= current_price:
        return None
    return (current_price - stop_price) / current_price * 100.0


def format_optional_price(value: float | None, currency: str) -> str:
    return "Not supplied" if value is None else f"{value:.2f} {currency}"


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))
