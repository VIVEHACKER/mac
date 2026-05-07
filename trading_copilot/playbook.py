from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .industry_rotation import (
    IndustryRotationResult,
    IndustryScore,
    PriceHistoryProvider,
    YahooHistoryProvider,
    analyze_industries,
)
from .macro import (
    MacroDashboard,
    MacroDataProvider,
    build_macro_dashboard,
)
from .market_data import MarketDataProvider, MarketSnapshot, YahooChartProvider
from .metrics import TechnicalProfile, build_technical_profile
from .sizing import SizingPlan, plan_position
from .storage import normalize_ticker


CYCLE_RISK_ON_REGIMES = frozenset(
    {
        "Disinflationary Expansion",
        "Inflationary Expansion",
    }
)
CYCLE_RISK_OFF_REGIMES = frozenset(
    {
        "Late-Cycle Tightening / Stagflation Risk",
        "Slowdown / Recession Risk",
    }
)


@dataclass(frozen=True)
class CycleScore:
    label: str
    value: float
    is_risk_on: bool
    is_risk_off: bool


@dataclass(frozen=True)
class SectorScore:
    matched_industry: str | None
    leadership_score: float
    relative_3m: float
    relative_6m: float
    is_current_leader: bool
    is_emerging_leader: bool


@dataclass(frozen=True)
class TechnicalScore:
    momentum_value: float
    drawdown_value: float
    relative_strength_value: float
    composite: float


@dataclass(frozen=True)
class CompositeScore:
    cycle: float
    sector: float
    technical: float
    overall: float


@dataclass(frozen=True)
class TickerPlaybook:
    ticker: str
    as_of: date
    snapshot: MarketSnapshot
    technical: TechnicalProfile
    macro_regime_name: str
    cycle: CycleScore
    sector: SectorScore
    technical_score: TechnicalScore
    composite: CompositeScore
    sizing: SizingPlan | None
    recommendation: str
    reasons: tuple[str, ...]
    risks: tuple[str, ...]
    sector_table: tuple[IndustryScore, ...]


class PlaybookBuilder:
    def __init__(
        self,
        market_data: MarketDataProvider | None = None,
        history_provider: PriceHistoryProvider | None = None,
        macro_provider: MacroDataProvider | None = None,
    ):
        self.market_data = market_data or YahooChartProvider()
        self.history_provider = history_provider or YahooHistoryProvider()
        self.macro_provider = macro_provider

    def build(
        self,
        ticker: str,
        *,
        aum: float | None = None,
        target_vol: float = 0.35,
        kelly_multiplier: float = 0.5,
        max_position_pct: float = 0.25,
        max_risk_pct_of_aum: float = 0.02,
        win_probability: float = 0.55,
        upside_pct: float = 0.50,
        downside_pct: float = 0.20,
        sector_industry_hint: str | None = None,
    ) -> TickerPlaybook:
        normalized = normalize_ticker(ticker)
        snapshot = self.market_data.snapshot(normalized)
        history = self.history_provider.history(normalized, range_period="2y", interval="1d")
        benchmark_history = self.history_provider.history("SPY", range_period="2y", interval="1d")
        closes = tuple(point.close for point in history)
        benchmark_closes = tuple(point.close for point in benchmark_history)

        technical = build_technical_profile(closes, benchmark_closes=benchmark_closes)
        rotation = analyze_industries(history_provider=self.history_provider)
        macro_dashboard = build_macro_dashboard(self.macro_provider)

        cycle = score_cycle(macro_dashboard)
        sector = match_sector(rotation, sector_industry_hint)
        technical_score = score_technical(technical)
        composite = composite_score(cycle, sector, technical_score)
        recommendation, reasons, risks = derive_recommendation(
            cycle, sector, technical_score, composite, technical
        )

        sizing: SizingPlan | None = None
        if aum is not None and aum > 0 and snapshot.price > 0:
            asset_vol = max(technical.realized_vol_annualized, 0.05)
            sizing = plan_position(
                aum=aum,
                price=snapshot.price,
                win_probability=win_probability,
                upside_pct=upside_pct,
                downside_pct=downside_pct,
                asset_vol_annualized=asset_vol,
                target_vol_annualized=target_vol,
                kelly_multiplier=kelly_multiplier,
                max_position_pct=max_position_pct,
                max_risk_pct_of_aum=max_risk_pct_of_aum,
            )

        return TickerPlaybook(
            ticker=normalized,
            as_of=snapshot.as_of.date(),
            snapshot=snapshot,
            technical=technical,
            macro_regime_name=macro_dashboard.regime.name,
            cycle=cycle,
            sector=sector,
            technical_score=technical_score,
            composite=composite,
            sizing=sizing,
            recommendation=recommendation,
            reasons=reasons,
            risks=risks,
            sector_table=rotation.current_leaders[:5] + rotation.next_leaders[:3],
        )


def score_cycle(dashboard: MacroDashboard) -> CycleScore:
    name = dashboard.regime.name
    is_risk_on = name in CYCLE_RISK_ON_REGIMES
    is_risk_off = name in CYCLE_RISK_OFF_REGIMES
    if "Disinflationary Expansion" in name:
        value = 85.0
    elif "Inflationary Expansion" in name:
        value = 60.0
    elif "Mixed" in name or "Transition" in name:
        value = 50.0
    elif "Slowdown" in name or "Recession" in name:
        value = 20.0
    elif "Late-Cycle" in name or "Stagflation" in name:
        value = 25.0
    else:
        value = 50.0
    return CycleScore(label=name, value=value, is_risk_on=is_risk_on, is_risk_off=is_risk_off)


def match_sector(
    rotation: IndustryRotationResult,
    industry_hint: str | None,
) -> SectorScore:
    if industry_hint is None:
        return _strongest_sector(rotation)
    hint = industry_hint.strip().lower()
    if not hint:
        return _strongest_sector(rotation)
    for score in rotation.scores:
        if score.symbol.lower() == hint or hint in score.name.lower() or hint in score.theme.lower():
            return _wrap(rotation, score)
    return _strongest_sector(rotation)


def _strongest_sector(rotation: IndustryRotationResult) -> SectorScore:
    if not rotation.scores:
        return SectorScore(None, 0.0, 0.0, 0.0, False, False)
    top = rotation.scores[0]
    return _wrap(rotation, top)


def _wrap(rotation: IndustryRotationResult, score: IndustryScore) -> SectorScore:
    return SectorScore(
        matched_industry=f"{score.name} ({score.symbol})",
        leadership_score=score.leadership_score,
        relative_3m=score.relative_three_month,
        relative_6m=score.relative_six_month,
        is_current_leader=score in rotation.current_leaders,
        is_emerging_leader=score in rotation.next_leaders,
    )


def score_technical(technical: TechnicalProfile) -> TechnicalScore:
    momentum_value = _scale_momentum(technical.momentum_12_1)
    drawdown_value = _scale_drawdown(technical.max_drawdown_252d)
    rs_value = _scale_distance_from_high(technical.pct_from_high)
    composite = 0.5 * momentum_value + 0.25 * drawdown_value + 0.25 * rs_value
    return TechnicalScore(
        momentum_value=momentum_value,
        drawdown_value=drawdown_value,
        relative_strength_value=rs_value,
        composite=composite,
    )


def _scale_momentum(momentum: float) -> float:
    if momentum >= 1.0:
        return 95.0
    if momentum >= 0.5:
        return 80.0
    if momentum >= 0.2:
        return 65.0
    if momentum >= 0.0:
        return 50.0
    if momentum >= -0.2:
        return 30.0
    return 10.0


def _scale_drawdown(drawdown: float) -> float:
    if drawdown >= -0.05:
        return 90.0
    if drawdown >= -0.15:
        return 70.0
    if drawdown >= -0.30:
        return 50.0
    if drawdown >= -0.50:
        return 25.0
    return 10.0


def _scale_distance_from_high(pct_from_high: float) -> float:
    if pct_from_high >= -3.0:
        return 95.0
    if pct_from_high >= -10.0:
        return 75.0
    if pct_from_high >= -25.0:
        return 45.0
    return 20.0


def composite_score(
    cycle: CycleScore,
    sector: SectorScore,
    technical: TechnicalScore,
) -> CompositeScore:
    sector_value = _sector_to_value(sector)
    overall = 0.30 * cycle.value + 0.30 * sector_value + 0.40 * technical.composite
    return CompositeScore(
        cycle=cycle.value,
        sector=sector_value,
        technical=technical.composite,
        overall=overall,
    )


def _sector_to_value(sector: SectorScore) -> float:
    if sector.matched_industry is None:
        return 50.0
    base = 50.0 + max(min(sector.leadership_score, 50.0), -50.0)
    if sector.is_current_leader:
        base += 5.0
    elif sector.is_emerging_leader:
        base += 3.0
    return max(0.0, min(100.0, base))


def derive_recommendation(
    cycle: CycleScore,
    sector: SectorScore,
    technical: TechnicalScore,
    composite: CompositeScore,
    profile: TechnicalProfile,
) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    reasons: list[str] = []
    risks: list[str] = []

    if cycle.is_risk_on:
        reasons.append(f"Macro regime is risk-on: {cycle.label}.")
    elif cycle.is_risk_off:
        risks.append(f"Macro regime is risk-off: {cycle.label}. Concentration is more dangerous here.")
    else:
        risks.append(f"Macro regime is mixed: {cycle.label}. Require stronger company-level edge.")

    if sector.matched_industry:
        if sector.is_current_leader:
            reasons.append(
                f"Sector ({sector.matched_industry}) is a current leader; "
                f"relative 3m vs SPY={sector.relative_3m:+.1f}%."
            )
        elif sector.is_emerging_leader:
            reasons.append(
                f"Sector ({sector.matched_industry}) shows emerging leadership; "
                f"relative 3m vs SPY={sector.relative_3m:+.1f}%."
            )
        else:
            risks.append(f"Sector ({sector.matched_industry}) is not in the current or emerging leader set.")

    if profile.momentum_12_1 >= 0.2:
        reasons.append(f"12-1m momentum {profile.momentum_12_1 * 100:+.1f}% supports trend continuation.")
    elif profile.momentum_12_1 <= -0.1:
        risks.append(f"12-1m momentum {profile.momentum_12_1 * 100:+.1f}% is negative; not a momentum entry.")

    if profile.pct_from_high >= -10:
        reasons.append(f"Price is {profile.pct_from_high:.1f}% from 52w high — relative strength.")
    if profile.pct_from_high <= -30:
        risks.append(
            f"Price is {profile.pct_from_high:.1f}% from 52w high; falling-knife risk if no clear basing pattern."
        )

    if profile.max_drawdown_252d <= -0.4:
        risks.append(
            f"252d max drawdown {profile.max_drawdown_252d * 100:.0f}% — confirm thesis still holds before adding."
        )

    if profile.realized_vol_annualized >= 0.6:
        risks.append(
            f"Annualized realized vol {profile.realized_vol_annualized * 100:.0f}% — size for survivable drawdown."
        )

    if composite.overall >= 75 and not cycle.is_risk_off:
        recommendation = "Strong Buy Window"
    elif composite.overall >= 60:
        recommendation = "Constructive Buy"
    elif composite.overall >= 45:
        recommendation = "Watch / Stage Entry"
    else:
        recommendation = "Avoid New Long"

    return recommendation, tuple(reasons), tuple(risks)


def format_playbook_report(playbook: TickerPlaybook) -> str:
    lines = [
        f"# Buy-Now Playbook - {playbook.ticker}",
        "",
        "Not investment advice. This combines macro regime, sector rotation, and technicals into a "
        "single confidence read. Always verify the underlying thesis manually.",
        "",
        f"As Of: {playbook.as_of.isoformat()}",
        f"Recommendation: {playbook.recommendation}",
        f"Composite Score: {playbook.composite.overall:.1f} / 100",
        "",
        "## Score Breakdown",
        f"- Macro Cycle: {playbook.composite.cycle:.1f} ({playbook.cycle.label})",
        f"- Sector: {playbook.composite.sector:.1f} "
        f"({playbook.sector.matched_industry or 'no sector matched'})",
        f"- Technical: {playbook.composite.technical:.1f}",
        "",
        "## Technicals",
        f"- Last close: {playbook.technical.last_close:.2f}",
        f"- 52w high / low: {playbook.technical.high_252d:.2f} / {playbook.technical.low_252d:.2f}",
        f"- From 52w high: {playbook.technical.pct_from_high:.1f}%",
        f"- Momentum 12-1m / 3m / 1m: "
        f"{playbook.technical.momentum_12_1 * 100:+.1f}% / "
        f"{playbook.technical.momentum_3m * 100:+.1f}% / "
        f"{playbook.technical.momentum_1m * 100:+.1f}%",
        f"- Annualized vol (60d): {playbook.technical.realized_vol_annualized * 100:.1f}%",
        f"- Max drawdown (252d): {playbook.technical.max_drawdown_252d * 100:.1f}%",
        f"- Beta vs SPY: {playbook.technical.beta_vs_benchmark:.2f} "
        f"(corr {playbook.technical.correlation_vs_benchmark:.2f})",
        "",
        "## Reasons To Buy",
    ]
    if playbook.reasons:
        lines.extend(f"- {item}" for item in playbook.reasons)
    else:
        lines.append("- No clear positive signals at this composite level.")

    lines.extend(["", "## Risks And Checks"])
    if playbook.risks:
        lines.extend(f"- {item}" for item in playbook.risks)
    else:
        lines.append("- No major risks flagged by the screen, but verify thesis manually.")

    if playbook.sizing is not None:
        lines.extend(
            [
                "",
                "## Position Sizing",
                f"- AUM: {playbook.sizing.aum:,.0f}",
                f"- Target weight (min Kelly/Vol/Risk): {playbook.sizing.target_weight_pct:.1f}%",
                f"- After concentration cap: {playbook.sizing.capped_weight_pct:.1f}%",
                f"- Shares: {playbook.sizing.shares}",
                f"- Dollars deployed: {playbook.sizing.actual_dollars:,.0f} "
                f"({playbook.sizing.actual_weight_pct:.1f}% of AUM)",
                f"- Hard stop: {playbook.sizing.stop_price:.2f}",
                f"- Loss if stopped: {playbook.sizing.risk_dollars:,.0f} "
                f"({playbook.sizing.risk_pct_of_aum:.2f}% of AUM)",
                "",
                "### Constraint trace",
            ]
        )
        lines.extend(f"- {note}" for note in playbook.sizing.notes)
    else:
        lines.extend(
            [
                "",
                "## Position Sizing",
                "- AUM not provided. Run with --aum to compute share count and risk caps.",
            ]
        )

    lines.extend(
        [
            "",
            "## Sector Context",
            "| Symbol | Industry | 3m vs SPY | 6m vs SPY | Leadership |",
            "|---|---|---:|---:|---:|",
        ]
    )
    for score in playbook.sector_table:
        lines.append(
            f"| {score.symbol} | {score.name} | "
            f"{score.relative_three_month:+.1f}% | "
            f"{score.relative_six_month:+.1f}% | "
            f"{score.leadership_score:+.1f} |"
        )

    lines.extend(
        [
            "",
            "## Operating Rules",
            "- This is a screen, not a trigger. Confirm catalyst, valuation, and thesis manually.",
            "- Honor the hard stop. Single-name 1000% wins still need an invalidation point.",
            "- Re-run weekly. Macro regime and sector leadership shift; the composite score is not static.",
            "- Fundamentals (ROE, margin, growth, P/E) are NOT in this version yet — add manually before sizing.",
        ]
    )
    return "\n".join(lines)


__all__ = [
    "TickerPlaybook",
    "CycleScore",
    "SectorScore",
    "TechnicalScore",
    "CompositeScore",
    "PlaybookBuilder",
    "score_cycle",
    "match_sector",
    "score_technical",
    "composite_score",
    "derive_recommendation",
    "format_playbook_report",
]
