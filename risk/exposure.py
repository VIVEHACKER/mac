"""Portfolio exposure monitor — gross/net, single-name, and sector concentration.

The pretrade gate (risk/pretrade.py) checks ONE order at a time; nothing watched the
book as a whole. This module reads the broker's PositionSnapshot list (paper or live —
same BrokerAdapter) and answers the portfolio-level questions a risk desk asks:

    how levered are we (gross), which way do we lean (net), what single name or
    sector could hurt us most, and are any of those outside policy?

Pure functions over frozen dataclasses, same discipline as kill_switch.py: no I/O, no
state — callable from the live loop, the dashboard, or a cron snapshot alike.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from trader.execution.broker import PositionSnapshot


@dataclass(frozen=True)
class ExposureReport:
    equity: float
    gross_exposure: float  # sum(|position|) / equity
    net_exposure: float  # (long - short) / equity
    long_exposure: float  # long market value / equity
    short_exposure: float  # |short market value| / equity
    symbol_weights: dict[str, float]  # |market value| / equity per symbol
    sector_weights: dict[str, float]  # |market value| / equity per sector ("" = unmapped)
    top_weight: float  # largest single-name weight
    top_symbol: str


@dataclass(frozen=True)
class ExposureLimits:
    max_gross_exposure: float = 1.0
    max_single_name: float = 0.25
    max_sector: float = 0.40
    min_net_exposure: float = 0.0  # long-only book should not lean net short
    max_net_exposure: float = 1.0


@dataclass(frozen=True)
class ExposureCheck:
    passed: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)


def build_exposure_report(
    positions: list[PositionSnapshot],
    equity: float,
    *,
    sectors: dict[str, str] | None = None,
) -> ExposureReport:
    """Aggregate the broker book into portfolio-level exposures.

    ``equity`` must be positive — exposure as a fraction of non-positive equity is
    undefined (and a halted/blown-up book should be handled by the kill-switch, not
    rendered as a ratio).
    """
    if equity <= 0:
        raise ValueError("equity must be positive to compute exposure ratios")
    sector_map = {k.upper(): v for k, v in (sectors or {}).items()}

    long_mv = 0.0
    short_mv = 0.0
    symbol_weights: dict[str, float] = {}
    sector_weights: dict[str, float] = {}
    for position in positions:
        mv = position.market_value
        if mv >= 0:
            long_mv += mv
        else:
            short_mv += -mv
        symbol = position.symbol.upper()
        weight = abs(mv) / equity
        symbol_weights[symbol] = symbol_weights.get(symbol, 0.0) + weight
        sector = sector_map.get(symbol, "")
        sector_weights[sector] = sector_weights.get(sector, 0.0) + weight

    top_symbol = max(symbol_weights, key=lambda s: symbol_weights[s], default="")
    return ExposureReport(
        equity=equity,
        gross_exposure=(long_mv + short_mv) / equity,
        net_exposure=(long_mv - short_mv) / equity,
        long_exposure=long_mv / equity,
        short_exposure=short_mv / equity,
        symbol_weights=symbol_weights,
        sector_weights=sector_weights,
        top_weight=symbol_weights.get(top_symbol, 0.0),
        top_symbol=top_symbol,
    )


def check_exposure_limits(
    report: ExposureReport, limits: ExposureLimits | None = None
) -> ExposureCheck:
    """Evaluate a report against policy limits; every breach is reported, none hidden."""
    lim = limits or ExposureLimits()
    reasons: list[str] = []
    if report.gross_exposure > lim.max_gross_exposure:
        reasons.append(
            f"gross exposure {report.gross_exposure:.2%} > limit {lim.max_gross_exposure:.2%}"
        )
    if report.net_exposure < lim.min_net_exposure:
        reasons.append(f"net exposure {report.net_exposure:.2%} < floor {lim.min_net_exposure:.2%}")
    if report.net_exposure > lim.max_net_exposure:
        reasons.append(f"net exposure {report.net_exposure:.2%} > limit {lim.max_net_exposure:.2%}")
    for symbol, weight in sorted(report.symbol_weights.items()):
        if weight > lim.max_single_name:
            reasons.append(f"single-name {symbol} {weight:.2%} > limit {lim.max_single_name:.2%}")
    for sector, weight in sorted(report.sector_weights.items()):
        if sector and weight > lim.max_sector:
            reasons.append(f"sector {sector} {weight:.2%} > limit {lim.max_sector:.2%}")
    return ExposureCheck(passed=not reasons, reasons=tuple(reasons))
