from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .industry_rotation import (
    IndustryDataError,
    PriceHistoryProvider,
    YahooHistoryProvider,
)
from .macro import (
    MacroDashboard,
    MacroDataError,
    MacroDataProvider,
    build_macro_dashboard,
    change_since_months,
    latest_observation,
    percent_change_since_months,
    safe_yoy_delta_since_months,
)


VIX_SYMBOL = "^VIX"
SPY_SYMBOL = "SPY"
HY_SPREAD_SERIES = "BAMLH0A0HYM2"


@dataclass(frozen=True)
class RegimeIndicators:
    cpi_yoy: float | None
    core_cpi_yoy: float | None
    cpi_6m_delta: float
    unemployment: float | None
    unemployment_6m_delta: float
    fed_funds: float | None
    fed_funds_6m_delta: float
    yield_curve_spread: float | None
    indpro_yoy: float | None
    retail_yoy: float | None
    vix: float | None
    vix_20d_avg: float | None
    spy_drawdown_252d: float | None
    credit_spread_hy: float | None
    sources: tuple[str, ...]
    vix_60d_max: float | None = None
    spy_off_60d_low: float | None = None


@dataclass(frozen=True)
class RegimeReading:
    primary: str
    label: str
    confidence: float
    triggers: tuple[str, ...]
    secondary: tuple[str, ...]
    summary: str
    indicators: RegimeIndicators


@dataclass(frozen=True)
class TargetAllocation:
    asset_class: str
    proxy_etf: str
    weight_pct: float
    rationale: str


@dataclass(frozen=True)
class PortfolioTemplate:
    regime: str
    description: str
    allocations: tuple[TargetAllocation, ...]
    avoid: tuple[str, ...]
    exit_triggers: tuple[str, ...]
    rebalance: str
    vix_throttle: str


@dataclass(frozen=True)
class RegimeReport:
    as_of: date
    reading: RegimeReading
    template: PortfolioTemplate


# --------------------------------------------------------------------------------------
# Indicator gathering
# --------------------------------------------------------------------------------------


def gather_indicators(
    macro_dashboard: MacroDashboard,
    macro_provider: MacroDataProvider,
    history_provider: PriceHistoryProvider,
) -> RegimeIndicators:
    sources: list[str] = []

    cpi_yoy = _try(macro_dashboard.inflation, lambda m: m[0].value if m else None)
    core_cpi_yoy = _try(macro_dashboard.inflation, lambda m: m[1].value if len(m) > 1 else None)
    cpi_6m_delta = _safe_delta(macro_provider, "CPIAUCSL", 6)
    unemployment = _try(macro_dashboard.labor_policy, lambda m: m[0].value if m else None)
    unemployment_6m_delta = _safe_change(macro_provider, "UNRATE", 6)
    fed_funds = _try(macro_dashboard.labor_policy, lambda m: m[1].value if len(m) > 1 else None)
    fed_funds_6m_delta = _safe_change(macro_provider, "FEDFUNDS", 6)
    yield_curve = _try(macro_dashboard.labor_policy, lambda m: m[2].value if len(m) > 2 else None)
    indpro_yoy = _try(macro_dashboard.growth_cycle, lambda m: m[0].value if m else None)
    retail_yoy = _try(macro_dashboard.growth_cycle, lambda m: m[1].value if len(m) > 1 else None)

    vix, vix_20d_avg, vix_60d_max, vix_source = _vix_metrics(history_provider)
    if vix_source:
        sources.append(vix_source)

    spy_drawdown, spy_off_low, spy_source = _spy_drawdown(history_provider)
    if spy_source:
        sources.append(spy_source)

    credit_spread, credit_source = _credit_spread(macro_provider)
    if credit_source:
        sources.append(credit_source)

    sources.extend(macro_dashboard.sources)

    return RegimeIndicators(
        cpi_yoy=cpi_yoy,
        core_cpi_yoy=core_cpi_yoy,
        cpi_6m_delta=cpi_6m_delta,
        unemployment=unemployment,
        unemployment_6m_delta=unemployment_6m_delta,
        fed_funds=fed_funds,
        fed_funds_6m_delta=fed_funds_6m_delta,
        yield_curve_spread=yield_curve,
        indpro_yoy=indpro_yoy,
        retail_yoy=retail_yoy,
        vix=vix,
        vix_20d_avg=vix_20d_avg,
        vix_60d_max=vix_60d_max,
        spy_drawdown_252d=spy_drawdown,
        spy_off_60d_low=spy_off_low,
        credit_spread_hy=credit_spread,
        sources=tuple(sources),
    )


def _try(values, fn):
    try:
        return fn(values)
    except Exception:
        return None


def _safe_delta(provider: MacroDataProvider, series_id: str, months: int) -> float:
    try:
        series = provider.series(series_id)
        return safe_yoy_delta_since_months(series, months)
    except Exception:
        return 0.0


def _safe_change(provider: MacroDataProvider, series_id: str, months: int) -> float:
    try:
        series = provider.series(series_id)
        return change_since_months(series, months)
    except Exception:
        return 0.0


def _vix_metrics(
    history_provider: PriceHistoryProvider,
) -> tuple[float | None, float | None, float | None, str | None]:
    try:
        history = history_provider.history(VIX_SYMBOL, range_period="3mo", interval="1d")
    except Exception:
        return None, None, None, None
    if len(history) < 2:
        return None, None, None, None
    closes = tuple(point.close for point in history)
    last = closes[-1]
    avg_window = closes[-20:] if len(closes) >= 20 else closes
    avg = sum(avg_window) / len(avg_window)
    peak_window = closes[-60:] if len(closes) >= 60 else closes
    peak = max(peak_window)
    return float(last), float(avg), float(peak), f"Yahoo chart {VIX_SYMBOL}"


def _spy_drawdown(
    history_provider: PriceHistoryProvider,
) -> tuple[float | None, float | None, str | None]:
    try:
        history = history_provider.history(SPY_SYMBOL, range_period="1y", interval="1d")
    except Exception:
        return None, None, None
    if len(history) < 2:
        return None, None, None
    closes = tuple(point.close for point in history)
    peak = max(closes)
    last = closes[-1]
    if peak <= 0:
        return None, None, None
    drawdown = (last - peak) / peak
    off_low = None
    recent = closes[-60:] if len(closes) >= 60 else closes
    trough = min(recent)
    if trough > 0:
        off_low = (last - trough) / trough
    return float(drawdown), off_low, f"Yahoo chart {SPY_SYMBOL} 1y"


def _credit_spread(provider: MacroDataProvider) -> tuple[float | None, str | None]:
    try:
        series = provider.series(HY_SPREAD_SERIES)
        latest = latest_observation(series)
        return float(latest.value), series.source
    except (MacroDataError, KeyError, AttributeError, Exception):
        return None, None


# --------------------------------------------------------------------------------------
# Regime classification
# --------------------------------------------------------------------------------------


REGIME_LABELS = {
    "panic": "Panic / Risk-Off Crisis",
    "recovery": "V-Bottom Recovery",
    "recession": "Recession",
    "stagflation": "Stagflation Risk",
    "inflation_shock": "Inflation Shock",
    "deflation_risk": "Deflation Risk",
    "easy_money": "Easy Money / QE",
    "disinflation_expansion": "Disinflationary Expansion",
    "transition": "Mixed / Transition",
}


def classify_regime(indicators: RegimeIndicators) -> RegimeReading:
    triggers: list[str] = []
    secondary: list[str] = []

    primary = "transition"
    confidence = 0.5

    panic_trigger = _check_panic(indicators)
    recovery_trigger = _check_recovery(indicators) if not panic_trigger else None
    recession_trigger = _check_recession(indicators)
    inflation_trigger = _check_inflation_shock(indicators)
    deflation_trigger = _check_deflation(indicators)
    easy_money_trigger = _check_easy_money(indicators)
    stagflation_trigger = _check_stagflation(indicators)
    disinflation_trigger = _check_disinflation_expansion(indicators)

    candidates: list[tuple[str, float, list[str]]] = []
    if panic_trigger:
        candidates.append(("panic", 0.85, panic_trigger))
    if recovery_trigger:
        candidates.append(("recovery", 0.75, recovery_trigger))
    if recession_trigger:
        candidates.append(("recession", 0.75, recession_trigger))
    if inflation_trigger:
        candidates.append(("inflation_shock", 0.80, inflation_trigger))
    if deflation_trigger:
        candidates.append(("deflation_risk", 0.70, deflation_trigger))
    if easy_money_trigger:
        candidates.append(("easy_money", 0.70, easy_money_trigger))
    if stagflation_trigger:
        candidates.append(("stagflation", 0.65, stagflation_trigger))
    if disinflation_trigger:
        candidates.append(("disinflation_expansion", 0.65, disinflation_trigger))

    if candidates:
        primary, confidence, primary_triggers = candidates[0]
        triggers.extend(primary_triggers)
        for name, _, sub_triggers in candidates[1:]:
            secondary.append(name)
            triggers.extend([f"(also) {t}" for t in sub_triggers])

    summary = _build_summary(primary, indicators)

    return RegimeReading(
        primary=primary,
        label=REGIME_LABELS.get(primary, primary),
        confidence=confidence,
        triggers=tuple(triggers) or ("No regime triggers fired; default to transition.",),
        secondary=tuple(secondary),
        summary=summary,
        indicators=indicators,
    )


def _check_panic(indicators: RegimeIndicators) -> list[str] | None:
    triggers = []
    if indicators.vix is not None and indicators.vix >= 30.0:
        triggers.append(f"VIX {indicators.vix:.1f} ≥ 30 (elevated fear)")
    if indicators.spy_drawdown_252d is not None and indicators.spy_drawdown_252d <= -0.12:
        triggers.append(
            f"SPY drawdown {indicators.spy_drawdown_252d * 100:.1f}% from 252d high"
        )
    if indicators.credit_spread_hy is not None and indicators.credit_spread_hy >= 6.0:
        triggers.append(
            f"HY credit spread {indicators.credit_spread_hy:.2f} ≥ 6.0 (stress)"
        )
    if len(triggers) >= 2:
        return triggers
    if (
        indicators.vix is not None and indicators.vix >= 35.0
    ):
        return triggers or [f"VIX {indicators.vix:.1f} ≥ 35"]
    return None


def _check_recession(indicators: RegimeIndicators) -> list[str] | None:
    """Recession requires labor weakening AND a hard-data confirmation.

    Tightened from the original 'any 2 of 4 signals' rule which produced false
    positives in 2024 (rising unemployment off historic lows + flat curve was
    enough to fire even though INDPRO/retail were both expanding).
    """
    triggers = []
    sahm_zone = indicators.unemployment_6m_delta >= 0.5
    labor_absolute_high = (
        indicators.unemployment is not None and indicators.unemployment >= 4.5
    )
    indpro_contracting = (
        indicators.indpro_yoy is not None and indicators.indpro_yoy < 0
    )
    retail_contracting = (
        indicators.retail_yoy is not None and indicators.retail_yoy < 0
    )
    curve_deeply_inverted = (
        indicators.yield_curve_spread is not None
        and indicators.yield_curve_spread < -0.3
    )

    labor_breaking = sahm_zone or (
        labor_absolute_high and indicators.unemployment_6m_delta >= 0.3
    )
    hard_data_confirms = indpro_contracting or retail_contracting

    if not (labor_breaking and hard_data_confirms):
        return None

    if sahm_zone:
        triggers.append(
            f"Unemployment +{indicators.unemployment_6m_delta:.2f}ppt 6m (Sahm trigger zone)"
        )
    if labor_absolute_high and indicators.unemployment is not None:
        triggers.append(
            f"Unemployment level {indicators.unemployment:.1f}% (≥4.5%, off cycle low)"
        )
    if indpro_contracting:
        triggers.append(f"INDPRO {indicators.indpro_yoy:+.1f}% YoY (contracting)")
    if retail_contracting:
        triggers.append(f"Retail sales {indicators.retail_yoy:+.1f}% YoY (contracting)")
    if curve_deeply_inverted:
        triggers.append(
            f"10Y-2Y curve {indicators.yield_curve_spread:+.2f}ppt (deeply inverted)"
        )
    return triggers


def _check_recovery(indicators: RegimeIndicators) -> list[str] | None:
    """V-bottom recovery: panic peaked recently and the market is starting back up.

    Three independent paths so the V can be caught even when one signal lags:
    A) VIX is well off its 60d peak (<= 60% of peak) — fear unwinding
    B) SPY has bounced >=5% off its 60d low while drawdown is still negative
    C) Original conservative path: VIX descending vs 20d avg + drawdown band
    Any single path is enough; multiple paths raise confidence implicitly.
    """
    if indicators.vix is None or indicators.spy_drawdown_252d is None:
        return None
    vix = indicators.vix
    drawdown = indicators.spy_drawdown_252d

    triggers: list[str] = []

    # Path A: VIX off panic-level peak — peak must have hit 50+ (true crisis),
    # current VIX <= 45% of peak, drawdown still negative. This deliberately
    # only fires after 2008/2020-grade events; quieter pullbacks fall to Path C.
    if (
        indicators.vix_60d_max is not None
        and indicators.vix_60d_max >= 50.0
        and drawdown < -0.05
    ):
        peak = indicators.vix_60d_max
        if vix <= peak * 0.45 and vix > 18.0:
            triggers.append(
                f"VIX {vix:.1f} is {(1 - vix / peak) * 100:.0f}% off 60d peak {peak:.1f} (crisis-grade)"
            )

    # Path B: SPY bounced from trough — require >=10% off-low (real bounce,
    # not noise) and drawdown -8% to -25% (must have been real sell-off).
    if (
        indicators.spy_off_60d_low is not None
        and indicators.spy_off_60d_low >= 0.10
        and drawdown < -0.08
        and drawdown > -0.25
    ):
        triggers.append(
            f"SPY +{indicators.spy_off_60d_low * 100:.1f}% off 60d low, "
            f"still {drawdown * 100:.1f}% from highs"
        )

    # Path C: original conservative VIX vs 20d avg
    if indicators.vix_20d_avg is not None:
        vix_20d = indicators.vix_20d_avg
        drawdown_band = -0.18 < drawdown < -0.03
        if vix > 18.0 and vix < vix_20d * 0.92 and drawdown_band:
            triggers.append(
                f"VIX {vix:.1f} below 20d avg {vix_20d:.1f} (fear unwinding)"
            )

    return triggers if triggers else None


def _check_inflation_shock(indicators: RegimeIndicators) -> list[str] | None:
    cpi = indicators.cpi_yoy
    core = indicators.core_cpi_yoy
    triggers = []
    hot = (cpi is not None and cpi >= 4.0) or (core is not None and core >= 4.0)
    if not hot:
        return None
    triggers.append(
        f"CPI {cpi:+.1f}% / Core {core:+.1f}% YoY (hot)"
        if cpi is not None and core is not None
        else "CPI hot ≥ 4% YoY"
    )
    accelerating = indicators.cpi_6m_delta >= 0.3
    growing = indicators.indpro_yoy is None or indicators.indpro_yoy >= 0
    if accelerating:
        triggers.append(f"CPI accelerating {indicators.cpi_6m_delta:+.2f}ppt 6m")
    if growing and indicators.indpro_yoy is not None:
        triggers.append(f"INDPRO {indicators.indpro_yoy:+.1f}% (growth still positive)")
    if (accelerating and growing) or (cpi is not None and cpi >= 5.0):
        return triggers
    return None


def _check_deflation(indicators: RegimeIndicators) -> list[str] | None:
    cpi = indicators.cpi_yoy
    if cpi is None:
        return None
    triggers = []
    cooling = indicators.cpi_6m_delta <= -0.3
    if cpi < 1.0 and cooling:
        triggers.append(f"CPI {cpi:+.1f}% YoY (sub-1%)")
        triggers.append(f"6m delta {indicators.cpi_6m_delta:+.2f}ppt (cooling)")
        if indicators.indpro_yoy is not None and indicators.indpro_yoy < 0:
            triggers.append(f"INDPRO {indicators.indpro_yoy:+.1f}% YoY")
        return triggers
    if cpi <= 0:
        triggers.append(f"CPI {cpi:+.1f}% YoY (outright deflation)")
        return triggers
    return None


def _check_easy_money(indicators: RegimeIndicators) -> list[str] | None:
    cuts = indicators.fed_funds_6m_delta <= -0.5
    if not cuts:
        return None
    triggers = [f"Fed Funds {indicators.fed_funds_6m_delta:+.2f}ppt 6m (cutting)"]
    if indicators.vix is not None and indicators.vix < 20:
        triggers.append(f"VIX {indicators.vix:.1f} < 20 (calm)")
    if indicators.cpi_yoy is not None and indicators.cpi_yoy < 3.0:
        triggers.append(f"CPI {indicators.cpi_yoy:+.1f}% YoY (under target/comfortable)")
    if len(triggers) >= 2:
        return triggers
    return None


def _check_stagflation(indicators: RegimeIndicators) -> list[str] | None:
    cpi_hot = indicators.cpi_yoy is not None and indicators.cpi_yoy >= 3.0
    growth_weak = indicators.indpro_yoy is not None and indicators.indpro_yoy < 1.0
    if cpi_hot and growth_weak:
        return [
            f"CPI {indicators.cpi_yoy:+.1f}% YoY (still hot)",
            f"INDPRO {indicators.indpro_yoy:+.1f}% YoY (weak growth)",
        ]
    return None


def _check_disinflation_expansion(indicators: RegimeIndicators) -> list[str] | None:
    cooling = indicators.cpi_6m_delta < -0.2
    growing = indicators.indpro_yoy is not None and indicators.indpro_yoy >= 1.0
    not_hot = indicators.cpi_yoy is None or indicators.cpi_yoy < 3.0
    if cooling and growing and not_hot:
        return [
            f"CPI cooling {indicators.cpi_6m_delta:+.2f}ppt 6m",
            f"INDPRO {indicators.indpro_yoy:+.1f}% YoY (expanding)",
        ]
    return None


def _build_summary(primary: str, indicators: RegimeIndicators) -> str:
    if primary == "panic":
        return (
            "Risk-off crisis conditions: elevated VIX, equity drawdown, or credit "
            "stress. Capital preservation outweighs upside chasing."
        )
    if primary == "recovery":
        return (
            "Post-panic V-bottom recovery: SPY off the lows, VIX falling but still "
            "elevated. Risk-on but with fast-revert hedges in case the recovery aborts."
        )
    if primary == "recession":
        return (
            "Recession signals firing: rising unemployment plus contracting "
            "production / retail / inverted curve."
        )
    if primary == "inflation_shock":
        return (
            "Hot CPI is still accelerating with growth intact. Real assets "
            "outperform and duration becomes a tax."
        )
    if primary == "deflation_risk":
        return (
            "Disinflation has tipped toward outright deflation risk: sub-1% CPI "
            "with continued 6m cooling. Long bonds and USD favored."
        )
    if primary == "easy_money":
        return (
            "Fed cutting / liquidity easing while VIX is calm. Long-duration risk "
            "assets benefit until the impulse exhausts."
        )
    if primary == "stagflation":
        return (
            "Inflation sticky alongside weak growth. Defensive value, commodities, "
            "and tail hedges favored over high-multiple growth."
        )
    if primary == "disinflation_expansion":
        return (
            "Inflation cooling while growth still expands — historically the most "
            "constructive regime for risk assets, but transitions are abrupt."
        )
    return (
        "Macro signals do not align cleanly. Treat as a transition state and "
        "demand stronger company-level conviction before sizing up."
    )


# --------------------------------------------------------------------------------------
# Portfolio templates
# --------------------------------------------------------------------------------------


def _alloc(asset: str, etf: str, weight: float, rationale: str) -> TargetAllocation:
    return TargetAllocation(
        asset_class=asset, proxy_etf=etf, weight_pct=weight, rationale=rationale
    )


TEMPLATES: dict[str, PortfolioTemplate] = {
    "recovery": PortfolioTemplate(
        regime="recovery",
        description=(
            "V-bottom recovery playbook. Panic has passed (drawdown easing, VIX "
            "falling) but the market is not yet at new highs. Lean risk-on with "
            "fast hedges in case the recovery aborts."
        ),
        allocations=(
            _alloc("US large-cap", "VOO", 22.0, "Standard recovery beneficiary."),
            _alloc("Growth tech", "QQQ", 16.0, "Multiple expansion as fear unwinds."),
            _alloc("Semiconductors", "SMH", 10.0, "High-beta cyclical leverage in early cycle."),
            _alloc("Small-cap", "IWM", 8.0, "Highest beta to recovery; cheap valuations."),
            _alloc("Quality dividend", "SCHD", 6.0, "Stable cash flow during the rebuild."),
            _alloc("Energy", "XLE", 5.0, "Late-cycle confirmation; cyclical earnings."),
            _alloc("Long Treasuries", "TLT", 6.0, "Mild duration if recovery aborts."),
            _alloc("Gold", "GLD", 7.0, "Tail hedge if Fed has to ease again."),
            _alloc("Cash / T-bills", "BIL", 20.0, "Optionality if the V-bottom turns into W."),
        ),
        avoid=(
            "Pure defensives at full size (XLU, XLP, UUP overweight)",
            "Long-vol products at full size (VIXY decays fast)",
        ),
        exit_triggers=(
            "VIX < 18 sustained 2+ weeks → upgrade to easy_money / disinflation_expansion",
            "SPY makes a new 252d high → upgrade to expansion regime",
            "VIX spikes back above 30 → revert to panic template",
        ),
        rebalance="Bi-weekly during the recovery; daily monitor VIX and credit spreads.",
        vix_throttle="VIX > 25 → trim QQQ/SMH 25%; VIX > 30 → revert toward panic.",
    ),
    "panic": PortfolioTemplate(
        regime="panic",
        description=(
            "Crisis playbook. VIX 30+, credit spreads widening, or equity drawdown "
            "12%+. Goal: preserve capital, maintain dry powder, hedge convexity."
        ),
        allocations=(
            _alloc("Cash / T-bills", "BIL", 30.0, "Dry powder for redeployment after the bottom."),
            _alloc("Long Treasuries", "TLT", 15.0, "Duration hedge for emergency rate cuts."),
            _alloc("Gold", "GLD", 15.0, "Crisis hedge; uncorrelated to equities."),
            _alloc("US Dollar", "UUP", 10.0, "Flight to safety bid during global panic."),
            _alloc("Consumer Staples", "XLP", 10.0, "Defensive earnings; lower drawdown."),
            _alloc("Utilities", "XLU", 5.0, "Yield + defensive cash flow."),
            _alloc("Healthcare", "XLV", 5.0, "Defensive demand."),
            _alloc("Long-vol / VIX call spread", "VIXY (caution)", 5.0, "Tail-risk hedge; manage decay carefully."),
            _alloc("Quality dividend", "SCHD", 5.0, "High-quality balance sheets, low payout risk."),
        ),
        avoid=(
            "Leveraged ETFs (TQQQ, SOXL, TMF)",
            "High-beta growth (ARKK, single-name SaaS)",
            "Crypto, EM equities, frontier markets",
            "High-yield credit (HYG)",
        ),
        exit_triggers=(
            "VIX < 20 sustained 4+ weeks",
            "HY credit spread tightens below 5.0",
            "SPY recovers above 200d moving average",
        ),
        rebalance="Weekly during stress; reassess panic triggers daily.",
        vix_throttle="VIX > 35 → halve gross exposure further; VIX > 50 → 50% cash floor.",
    ),
    "recession": PortfolioTemplate(
        regime="recession",
        description=(
            "Demand failing, unemployment rising. Defensive + duration. Wait for "
            "earnings reset before adding cyclicals."
        ),
        allocations=(
            _alloc("Long Treasuries", "TLT", 25.0, "Bond bull market on rate cuts + flight to safety."),
            _alloc("Cash / T-bills", "BIL", 20.0, "Optionality for the bottom."),
            _alloc("Consumer Staples", "XLP", 12.0, "Demand inelastic."),
            _alloc("Healthcare", "XLV", 10.0, "Demand inelastic."),
            _alloc("Utilities", "XLU", 8.0, "Yield + defensive."),
            _alloc("Gold", "GLD", 10.0, "Hedge against policy panic."),
            _alloc("US Dollar", "UUP", 5.0, "Flight to safety until Fed eases hard."),
            _alloc("Quality large-cap", "VTV", 10.0, "Earnings resilience inside the index."),
        ),
        avoid=(
            "Cyclicals (XLI, XLF banks)",
            "Small caps (IWM)",
            "Energy and materials commodities",
            "Levered everything",
        ),
        exit_triggers=(
            "Unemployment 6m delta turns negative (peak unemployment passed)",
            "Yield curve un-inverts and steepens",
            "INDPRO YoY back above 0%",
        ),
        rebalance="Monthly with macro update review.",
        vix_throttle="VIX > 30 → reduce equity sleeve by 30%; VIX > 40 → halve.",
    ),
    "inflation_shock": PortfolioTemplate(
        regime="inflation_shock",
        description=(
            "CPI 4%+ accelerating with growth intact. Real assets and pricing power "
            "win; duration becomes a tax."
        ),
        allocations=(
            _alloc("Energy", "XLE", 15.0, "Pricing power + cash flow with oil up."),
            _alloc("Commodities basket", "DBC", 12.0, "Direct inflation exposure."),
            _alloc("Gold", "GLD", 12.0, "Real-rate hedge; works if Fed is behind the curve."),
            _alloc("Silver / metals", "SLV", 5.0, "Higher-beta precious; demand-sensitive."),
            _alloc("TIPS", "TIP", 12.0, "Inflation-protected duration; better than nominal."),
            _alloc("Materials", "XLB", 8.0, "Pricing power on inputs."),
            _alloc("Financials", "XLF", 10.0, "Higher rates → wider net interest margin."),
            _alloc("Value equity", "VTV", 12.0, "Value beats growth in high-rate regimes."),
            _alloc("Short-duration cash", "SHV", 14.0, "Capture yield without duration risk."),
        ),
        avoid=(
            "Long-duration growth (TLT, ZM, SHOP, long-dated tech)",
            "Long Treasuries until Fed pivots",
            "REITs (rate-sensitive)",
        ),
        exit_triggers=(
            "CPI 6m delta turns negative (peak inflation)",
            "Fed signals pause / pivot",
            "Energy cycle rolls over (oil < 200d MA)",
        ),
        rebalance="Monthly; weekly during commodity volatility.",
        vix_throttle="VIX > 25 → trim energy + materials; VIX > 35 → defensive shift.",
    ),
    "deflation_risk": PortfolioTemplate(
        regime="deflation_risk",
        description=(
            "CPI sub-1% with continued cooling. Demand failing. Bonds and USD favored."
        ),
        allocations=(
            _alloc("Long Treasuries", "TLT", 30.0, "Deflation = bond bull; long duration shines."),
            _alloc("US Dollar", "UUP", 15.0, "Deflation strengthens USD globally."),
            _alloc("Cash / T-bills", "BIL", 20.0, "Capital preservation."),
            _alloc("Consumer Staples", "XLP", 10.0, "Defensive demand."),
            _alloc("Healthcare", "XLV", 10.0, "Defensive demand."),
            _alloc("Utilities", "XLU", 5.0, "Yield + defensive."),
            _alloc("Gold", "GLD", 5.0, "Hedge against deflation panic and policy response."),
            _alloc("Quality large-cap", "VTV", 5.0, "Pricing power survives deflation."),
        ),
        avoid=(
            "Energy, materials, mining",
            "Industrials, banks (NIM compression)",
            "Commodities basket",
            "EM and high-yield credit",
        ),
        exit_triggers=(
            "CPI YoY back above 1.5%",
            "Fed stops easing",
            "Commodity index breaks above 200d MA",
        ),
        rebalance="Monthly.",
        vix_throttle="VIX > 30 → +10% cash; VIX > 40 → +20% cash + raise gold.",
    ),
    "easy_money": PortfolioTemplate(
        regime="easy_money",
        description=(
            "Fed cutting / liquidity easing with VIX calm. Risk-on regime; "
            "long-duration growth assets benefit most."
        ),
        allocations=(
            _alloc("US large-cap growth", "QQQ", 22.0, "Long-duration growth re-rates on lower rates."),
            _alloc("Semiconductors", "SMH", 15.0, "Cyclical leverage to capex + AI cycle."),
            _alloc("Software", "IGV", 10.0, "Multiple expansion in low-rate env."),
            _alloc("Small-cap", "IWM", 8.0, "Cheap valuations + rate sensitivity."),
            _alloc("Emerging markets", "EEM", 8.0, "Weak USD + carry."),
            _alloc("Real estate", "XLRE", 5.0, "Lower discount rate + cap-rate compression."),
            _alloc("Long Treasuries", "TLT", 8.0, "Lock in rates before further cuts."),
            _alloc("Gold", "GLD", 5.0, "Inflation tail hedge if QE overshoots."),
            _alloc("Crypto exposure", "IBIT (or BTC direct)", 4.0, "Liquidity-sensitive risk asset."),
            _alloc("Cash / T-bills", "BIL", 15.0, "Optionality if regime breaks."),
        ),
        avoid=(
            "Short-duration defensives if you cap upside",
            "USD index (UUP)",
        ),
        exit_triggers=(
            "Fed pauses or hawkish surprise",
            "VIX > 25 sustained",
            "Yield curve steepens aggressively",
        ),
        rebalance="Monthly.",
        vix_throttle="VIX > 22 → trim QQQ/SMH 25%; VIX > 30 → defensive shift.",
    ),
    "stagflation": PortfolioTemplate(
        regime="stagflation",
        description=(
            "Inflation sticky alongside weak growth. Hardest regime for stocks; "
            "real assets + value + tail hedges."
        ),
        allocations=(
            _alloc("Gold", "GLD", 18.0, "Best stagflation asset historically."),
            _alloc("Energy", "XLE", 12.0, "Real cash flow with inflation."),
            _alloc("Commodities", "DBC", 10.0, "Direct inflation exposure."),
            _alloc("TIPS", "TIP", 12.0, "Inflation-protected duration."),
            _alloc("Value equity", "VTV", 10.0, "Earnings durability in low-multiple names."),
            _alloc("Short-duration cash", "SHV", 18.0, "Yield without duration."),
            _alloc("Healthcare", "XLV", 7.0, "Pricing power + defensive demand."),
            _alloc("Consumer Staples", "XLP", 7.0, "Pricing power."),
            _alloc("Long-vol hedge", "VIXY (small)", 3.0, "Tail-risk hedge."),
            _alloc("US Dollar", "UUP", 3.0, "Hedge if global stagflation hits EM harder."),
        ),
        avoid=(
            "High-multiple growth (QQQ-heavy)",
            "Long Treasuries (TLT)",
            "REITs",
            "Levered everything",
        ),
        exit_triggers=(
            "CPI cools below 3% sustained",
            "Growth re-accelerates (INDPRO + retail YoY both > 2%)",
            "Fed pivots and curve normalizes",
        ),
        rebalance="Monthly with weekly inflation print review.",
        vix_throttle="VIX > 25 → +10% cash; VIX > 35 → defensive shift.",
    ),
    "disinflation_expansion": PortfolioTemplate(
        regime="disinflation_expansion",
        description=(
            "Inflation cooling while growth still expands. Goldilocks. Most "
            "constructive for risk assets, but transitions are abrupt."
        ),
        allocations=(
            _alloc("US large-cap growth", "QQQ", 20.0, "Multiple expansion + earnings growth."),
            _alloc("Semiconductors", "SMH", 12.0, "Capex cycle continues."),
            _alloc("Software", "IGV", 10.0, "Lower discount rate + revenue growth."),
            _alloc("Communication services", "XLC", 5.0, "Ad cycle support."),
            _alloc("Industrials", "XLI", 8.0, "Capex + reshoring beneficiaries."),
            _alloc("Financials", "XLF", 8.0, "Steeper curve + lower credit cost."),
            _alloc("Small-cap", "IWM", 7.0, "Earnings recovery + multiple expansion."),
            _alloc("Long Treasuries", "TLT", 8.0, "Mild duration hedge as yields drift down."),
            _alloc("Gold", "GLD", 5.0, "Tail hedge."),
            _alloc("Cash / T-bills", "BIL", 15.0, "Optionality + rebalance fuel."),
            _alloc("Real estate", "XLRE", 2.0, "Mild beneficiary of rate stability."),
        ),
        avoid=(
            "Short-vol strategies (mean reversion of complacency)",
            "Heavy defensive overload (cap upside)",
        ),
        exit_triggers=(
            "Inflation re-accelerates (CPI 6m delta back > 0.3)",
            "Unemployment 6m delta > 0.3 (labor breaking)",
            "Yield curve aggressively flattens (recession warning)",
        ),
        rebalance="Monthly.",
        vix_throttle="VIX > 22 → trim 20% from QQQ/SMH; VIX > 30 → defensive shift.",
    ),
    "transition": PortfolioTemplate(
        regime="transition",
        description=(
            "Macro signals do not align cleanly. Use balanced exposure with extra "
            "cash, smaller positions, and tighter invalidation."
        ),
        allocations=(
            _alloc("US large-cap", "VOO", 25.0, "Core market exposure."),
            _alloc("Quality value", "VTV", 10.0, "Earnings durability."),
            _alloc("Long Treasuries", "TLT", 12.0, "Duration hedge."),
            _alloc("TIPS", "TIP", 8.0, "Inflation-protected duration."),
            _alloc("Gold", "GLD", 10.0, "Crisis hedge."),
            _alloc("Energy", "XLE", 5.0, "Inflation + crisis hedge."),
            _alloc("Healthcare", "XLV", 5.0, "Defensive."),
            _alloc("Cash / T-bills", "BIL", 25.0, "Higher cash than usual."),
        ),
        avoid=(
            "Concentrated single-regime bets (heavy long bonds OR heavy commodities)",
            "Leveraged ETFs",
        ),
        exit_triggers=(
            "Any of the explicit regime checks fire",
            "VIX > 25 sustained",
        ),
        rebalance="Monthly.",
        vix_throttle="VIX > 22 → +10% cash; VIX > 30 → +20% cash.",
    ),
}


# --------------------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------------------


def build_regime_report(
    macro_provider: MacroDataProvider | None = None,
    history_provider: PriceHistoryProvider | None = None,
) -> RegimeReport:
    macro = macro_provider
    history = history_provider or YahooHistoryProvider()
    macro_dashboard = build_macro_dashboard(macro)
    indicators = gather_indicators(macro_dashboard, macro_provider, history)
    reading = classify_regime(indicators)
    base_template = TEMPLATES.get(reading.primary, TEMPLATES["transition"])
    template = apply_vix_overlay(base_template, indicators.vix)
    return RegimeReport(
        as_of=macro_dashboard.as_of,
        reading=reading,
        template=template,
    )


# Regimes whose risk profile is sensitive to VIX (calm = lean risk-on, stress = lean risk-off).
# Stable / explicit-stance regimes (panic, inflation_shock, deflation_risk) are NOT overlaid
# because their templates already encode the risk posture.
_VIX_SENSITIVE_REGIMES = frozenset(
    {"transition", "disinflation_expansion", "easy_money", "recovery", "stagflation"}
)
_VIX_OVERLAY_RULES: dict[str, int] = {
    "BIL": -10,
    "SHV": -8,
    "VOO": +5,
    "QQQ": +4,
    "IWM": +2,
    "SMH": +2,
    "VTV": +2,
}


def _vix_tier_multiplier(vix: float) -> tuple[float, str]:
    """Return (direction multiplier, label) for VIX-based weight shift.

    Four tiers:
      VIX < 14    : very calm   -> +1.5x risk-on
      14 <= VIX < 18 : calm     -> +1.0x risk-on
      18 <= VIX <= 25 : normal  -> 0 (no overlay)
      25 < VIX <= 32 : stress   -> -1.0x risk-off
      VIX > 32    : crisis      -> -1.5x risk-off (panic territory; rarely hits sensitive regimes)
    """
    if vix < 14.0:
        return 1.5, f"VIX {vix:.1f} < 14 (very calm) → equity boosted 1.5x"
    if vix < 18.0:
        return 1.0, f"VIX {vix:.1f} < 18 (calm) → equity boosted"
    if vix > 32.0:
        return -1.5, f"VIX {vix:.1f} > 32 (crisis) → equity trimmed 1.5x"
    if vix > 25.0:
        return -1.0, f"VIX {vix:.1f} > 25 (stress) → equity trimmed"
    return 0.0, ""


def apply_vix_overlay(template: PortfolioTemplate, vix: float | None) -> PortfolioTemplate:
    """Adjust template weights based on VIX level (4-tier).

    Only applies to risk-on / mixed regimes; explicit panic and inflation/deflation
    templates are left untouched because their stance is already deliberate.
    """
    if vix is None or template.regime not in _VIX_SENSITIVE_REGIMES:
        return template

    direction, label = _vix_tier_multiplier(vix)
    if direction == 0.0:
        return template

    new_allocs = []
    for alloc in template.allocations:
        delta = _VIX_OVERLAY_RULES.get(alloc.proxy_etf, 0) * direction
        new_weight = alloc.weight_pct + delta
        if new_weight < 0:
            new_weight = 0.0
        if delta != 0:
            note = f"{alloc.rationale} [VIX overlay {delta:+.1f}pp]"
        else:
            note = alloc.rationale
        new_allocs.append(
            TargetAllocation(
                asset_class=alloc.asset_class,
                proxy_etf=alloc.proxy_etf,
                weight_pct=new_weight,
                rationale=note,
            )
        )

    total = sum(a.weight_pct for a in new_allocs)
    if total > 0 and abs(total - 100.0) > 0.001:
        scale = 100.0 / total
        new_allocs = [
            TargetAllocation(
                asset_class=a.asset_class,
                proxy_etf=a.proxy_etf,
                weight_pct=a.weight_pct * scale,
                rationale=a.rationale,
            )
            for a in new_allocs
        ]

    return PortfolioTemplate(
        regime=template.regime,
        description=f"{template.description} [{label}]",
        allocations=tuple(new_allocs),
        avoid=template.avoid,
        exit_triggers=template.exit_triggers,
        rebalance=template.rebalance,
        vix_throttle=template.vix_throttle,
    )


def format_regime_report(report: RegimeReport) -> str:
    reading = report.reading
    template = report.template
    indicators = reading.indicators

    lines = [
        f"# Macro Regime Engine - {report.as_of.isoformat()}",
        "",
        "Not investment advice. This combines FRED macro indicators with VIX, SPY "
        "drawdown, and HY credit spreads into a regime classifier and a target "
        "portfolio template. Verify the underlying conditions before trading.",
        "",
        f"## Primary Regime: {reading.label}",
        f"Confidence: {reading.confidence * 100:.0f}%",
        "",
        reading.summary,
    ]

    if reading.secondary:
        secondary_labels = ", ".join(REGIME_LABELS.get(s, s) for s in reading.secondary)
        lines.extend(["", f"Secondary regimes also firing: {secondary_labels}"])

    lines.extend(["", "## Triggers"])
    for trigger in reading.triggers:
        lines.append(f"- {trigger}")

    lines.extend(
        [
            "",
            "## Indicator Snapshot",
            "| Indicator | Value | Read |",
            "|---|---:|---|",
            _row("Headline CPI YoY", indicators.cpi_yoy, "%", _read_cpi(indicators.cpi_yoy)),
            _row("Core CPI YoY", indicators.core_cpi_yoy, "%", _read_cpi(indicators.core_cpi_yoy)),
            _row("CPI YoY 6m delta", indicators.cpi_6m_delta, "ppt", _read_delta(indicators.cpi_6m_delta)),
            _row("Unemployment", indicators.unemployment, "%", "labor market level"),
            _row("Unemployment 6m delta", indicators.unemployment_6m_delta, "ppt", _read_unemp(indicators.unemployment_6m_delta)),
            _row("Fed Funds", indicators.fed_funds, "%", "policy rate"),
            _row("Fed Funds 6m delta", indicators.fed_funds_6m_delta, "ppt", _read_fed(indicators.fed_funds_6m_delta)),
            _row("10Y-2Y curve", indicators.yield_curve_spread, "ppt", _read_curve(indicators.yield_curve_spread)),
            _row("INDPRO YoY", indicators.indpro_yoy, "%", _read_growth(indicators.indpro_yoy)),
            _row("Retail YoY", indicators.retail_yoy, "%", _read_growth(indicators.retail_yoy)),
            _row("VIX (latest)", indicators.vix, "", _read_vix(indicators.vix)),
            _row("VIX 20d avg", indicators.vix_20d_avg, "", _read_vix(indicators.vix_20d_avg)),
            _row(
                "SPY 252d drawdown",
                None if indicators.spy_drawdown_252d is None else indicators.spy_drawdown_252d * 100,
                "%",
                _read_drawdown(indicators.spy_drawdown_252d),
            ),
            _row("HY credit spread", indicators.credit_spread_hy, "ppt", _read_credit(indicators.credit_spread_hy)),
        ]
    )

    lines.extend(
        [
            "",
            f"## Suggested Portfolio: {template.regime.replace('_', ' ').title()}",
            template.description,
            "",
            "| Asset Class | Proxy ETF | Weight | Rationale |",
            "|---|---|---:|---|",
        ]
    )
    total_weight = 0.0
    for alloc in template.allocations:
        total_weight += alloc.weight_pct
        lines.append(
            f"| {alloc.asset_class} | {alloc.proxy_etf} | {alloc.weight_pct:.1f}% | {alloc.rationale} |"
        )
    lines.append(f"| **Total** | | **{total_weight:.1f}%** | |")

    lines.extend(["", "## Avoid"])
    lines.extend(f"- {item}" for item in template.avoid)

    lines.extend(["", "## Exit / Regime-Change Triggers"])
    lines.extend(f"- {item}" for item in template.exit_triggers)

    lines.extend(
        [
            "",
            "## Operating Rules",
            f"- Rebalance: {template.rebalance}",
            f"- VIX throttle: {template.vix_throttle}",
            "- This template is a research starting point. Adjust to your actual "
            "AUM, tax situation, and existing positions.",
            "- Single-name 1000% wins live INSIDE this allocation framework, not "
            "outside it. Use the playbook command per ticker for sizing.",
        ]
    )

    if indicators.sources:
        lines.extend(["", "## Sources"])
        for source in indicators.sources:
            lines.append(f"- {source}")

    return "\n".join(lines)


def _row(label: str, value: float | None, unit: str, read: str) -> str:
    if value is None:
        return f"| {label} | n/a | {read} |"
    if unit == "%":
        return f"| {label} | {value:+.2f}% | {read} |"
    if unit == "ppt":
        return f"| {label} | {value:+.2f} ppt | {read} |"
    return f"| {label} | {value:.2f} | {read} |"


def _read_cpi(v: float | None) -> str:
    if v is None:
        return "n/a"
    if v >= 4.0:
        return "hot"
    if v >= 3.0:
        return "above target"
    if v >= 2.0:
        return "near target"
    if v >= 1.0:
        return "soft"
    return "deflation risk"


def _read_delta(v: float) -> str:
    if v >= 0.3:
        return "accelerating"
    if v <= -0.3:
        return "cooling"
    return "stable"


def _read_unemp(v: float) -> str:
    if v >= 0.5:
        return "Sahm trigger zone"
    if v >= 0.2:
        return "rising"
    if v <= -0.2:
        return "falling"
    return "stable"


def _read_fed(v: float) -> str:
    if v >= 0.25:
        return "tightening"
    if v <= -0.5:
        return "cutting hard"
    if v <= -0.1:
        return "easing"
    return "neutral"


def _read_curve(v: float | None) -> str:
    if v is None:
        return "n/a"
    if v < -0.3:
        return "deeply inverted"
    if v < 0:
        return "inverted (recession risk)"
    if v < 0.5:
        return "flat"
    return "positive"


def _read_growth(v: float | None) -> str:
    if v is None:
        return "n/a"
    if v >= 2.0:
        return "expanding"
    if v >= 0:
        return "soft positive"
    return "contracting"


def _read_vix(v: float | None) -> str:
    if v is None:
        return "n/a"
    if v >= 35:
        return "panic"
    if v >= 25:
        return "stress"
    if v >= 20:
        return "elevated"
    if v >= 15:
        return "normal"
    return "calm"


def _read_drawdown(v: float | None) -> str:
    if v is None:
        return "n/a"
    if v <= -0.20:
        return "bear-market drawdown"
    if v <= -0.10:
        return "correction"
    if v <= -0.05:
        return "pullback"
    return "near highs"


def _read_credit(v: float | None) -> str:
    if v is None:
        return "n/a"
    if v >= 8.0:
        return "crisis-level"
    if v >= 6.0:
        return "stress"
    if v >= 4.0:
        return "elevated"
    return "calm"


__all__ = [
    "RegimeIndicators",
    "RegimeReading",
    "RegimeReport",
    "TargetAllocation",
    "PortfolioTemplate",
    "TEMPLATES",
    "REGIME_LABELS",
    "gather_indicators",
    "classify_regime",
    "build_regime_report",
    "format_regime_report",
    "apply_vix_overlay",
]
