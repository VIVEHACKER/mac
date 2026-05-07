from __future__ import annotations

from dataclasses import dataclass

from .market_data import MarketDataProvider


DEFAULT_SINGLE_STOCK_POOL = (
    "NVDA",
    "AVGO",
    "AMD",
    "MSFT",
    "META",
    "AMZN",
    "TSLA",
    "PLTR",
    "CRWD",
    "ARM",
)


@dataclass(frozen=True)
class PortfolioPosition:
    symbol: str
    name: str
    asset_class: str
    sleeve: str
    weight: float
    rationale: str
    risk: str


@dataclass(frozen=True)
class PortfolioDraft:
    target_annual_return: int
    positions: tuple[PortfolioPosition, ...]
    selected_single_stocks: tuple[str, ...]
    assumptions: tuple[str, ...]
    warnings: tuple[str, ...]


SINGLE_STOCK_WEIGHTS = (14.0, 10.0, 6.0)


def build_aggressive_portfolio(
    target_annual_return: int,
    market_data: MarketDataProvider,
    single_stock_pool: tuple[str, ...] = DEFAULT_SINGLE_STOCK_POOL,
) -> PortfolioDraft:
    selected_single_stocks = select_single_stocks(
        single_stock_pool, market_data, count=len(SINGLE_STOCK_WEIGHTS)
    )
    positions = list(base_positions())
    for rank, symbol in enumerate(selected_single_stocks):
        weight = SINGLE_STOCK_WEIGHTS[rank] if rank < len(SINGLE_STOCK_WEIGHTS) else 0.0
        positions.append(
            PortfolioPosition(
                symbol=symbol,
                name=f"{symbol} single stock",
                asset_class="single_stock",
                sleeve="High-conviction single stocks",
                weight=weight,
                rationale=(
                    "Ranked from the single-stock pool by latest-session relative price change. "
                    "This is short-horizon noise, not multi-week momentum."
                ),
                risk="Single-name drawdown, earnings miss, valuation compression.",
            )
        )

    return PortfolioDraft(
        target_annual_return=target_annual_return,
        positions=tuple(positions),
        selected_single_stocks=selected_single_stocks,
        assumptions=(
            "This is a research draft for an aggressive portfolio, not a guaranteed return plan.",
            "The 100% annual return target requires high volatility, leverage, and active risk control.",
            "Single-stock sleeve must contain exactly 3 names for upside concentration.",
            "Commodity exposures use liquid ETF/ETN proxies where direct spot access is unavailable.",
        ),
        warnings=(
            "Target annual return is not guaranteed.",
            "Leveraged ETFs can decay and can suffer severe drawdowns.",
            "Commodity ETFs/ETNs can diverge from spot prices due to roll yield, fees, and issuer structure.",
            "Rebalance rules, stop rules, and tax impact must be reviewed before use.",
        ),
    )


def select_single_stocks(
    pool: tuple[str, ...],
    market_data: MarketDataProvider,
    count: int,
) -> tuple[str, ...]:
    scored: list[tuple[float, str]] = []
    for symbol in dedupe_symbols(pool):
        try:
            snapshot = market_data.snapshot(symbol)
            score = snapshot.change_percent
        except Exception:
            score = -999.0
        scored.append((score, symbol))
    ranked = sorted(scored, key=lambda item: item[0], reverse=True)
    return tuple(symbol for _, symbol in ranked[:count])


def base_positions() -> tuple[PortfolioPosition, ...]:
    return (
        PortfolioPosition("TQQQ", "ProShares UltraPro QQQ", "leveraged_equity_etf", "Leveraged growth", 16.0, "3x Nasdaq-100 upside engine.", "High volatility and path-dependent decay."),
        PortfolioPosition("SOXL", "Direxion Daily Semiconductor Bull 3x Shares", "leveraged_equity_etf", "Leveraged growth", 8.0, "3x semiconductor beta.", "Large drawdowns in chip downcycles."),
        PortfolioPosition("TLT", "iShares 20+ Year Treasury Bond ETF", "bond", "Convex hedge", 6.0, "Duration hedge if growth shock hits.", "Rates can rise and hurt long-duration bonds."),
        PortfolioPosition("TMF", "Direxion Daily 20+ Year Treasury Bull 3x Shares", "bond", "Leveraged convex hedge", 4.0, "Leveraged duration convexity sleeve.", "Path-dependent decay and rate sensitivity."),
        PortfolioPosition("GLD", "SPDR Gold Shares", "commodity", "Gold", 5.0, "Gold risk hedge.", "Can lag in real-rate spikes."),
        PortfolioPosition("SLV", "iShares Silver Trust", "commodity", "Silver", 3.0, "High-beta precious metals exposure.", "More volatile than gold."),
        PortfolioPosition("CPER", "United States Copper Index Fund", "commodity", "Copper", 5.0, "Copper demand and electrification proxy.", "Cyclical demand and futures roll risk."),
        PortfolioPosition("JJU", "Aluminum ETN proxy", "commodity", "Aluminum", 3.0, "Aluminum exposure proxy.", "ETN/product structure and liquidity must be verified."),
        PortfolioPosition("USO", "United States Oil Fund", "commodity", "WTI crude oil", 4.0, "WTI crude oil futures proxy.", "Roll yield and oil volatility."),
        PortfolioPosition("UNG", "United States Natural Gas Fund", "commodity", "Natural gas", 3.0, "Natural gas futures proxy.", "Very high volatility and roll risk."),
        PortfolioPosition("REMX", "VanEck Rare Earth/Strategic Metals ETF", "commodity", "Rare Earth", 3.0, "Rare earth and strategic metals equity proxy.", "Mining equity risk and geopolitical risk."),
        PortfolioPosition("PICK", "iShares MSCI Global Metals & Mining Producers ETF", "commodity", "Iron ore proxy", 3.0, "Iron ore/mining producer proxy.", "Equity proxy, not direct iron ore spot exposure."),
        PortfolioPosition("DBB", "Invesco DB Base Metals Fund", "commodity", "Base metals basket", 4.0, "Diversifies copper/aluminum/zinc exposure.", "Futures roll and broad industrial cycle risk."),
        PortfolioPosition("CASH", "Cash or Treasury bills", "cash", "Risk buffer", 3.0, "Dry powder for rebalance and drawdowns.", "Cash drag in strong bull markets."),
    )


def format_portfolio_report(draft: PortfolioDraft) -> str:
    lines = [
        "# Aggressive 100% Target Portfolio Draft",
        "",
        "Research-only draft. This is not investment advice, and the target return is not guaranteed.",
        "",
        f"Target annual return: {draft.target_annual_return}%",
        f"Single Stocks: {len(draft.selected_single_stocks)} ({', '.join(draft.selected_single_stocks)})",
        f"Total Weight: {sum(position.weight for position in draft.positions):.1f}%",
        "",
        "## Allocation",
        "| Symbol | Sleeve | Asset Class | Weight | Rationale | Key Risk |",
        "|---|---|---:|---:|---|---|",
    ]
    for position in draft.positions:
        lines.append(
            f"| {position.symbol} | {position.sleeve} | {position.asset_class} | "
            f"{position.weight:.1f}% | {position.rationale} | {position.risk} |"
        )
    lines.extend(["", "## Assumptions"])
    lines.extend(f"- {item}" for item in draft.assumptions)
    lines.extend(["", "## Warnings"])
    lines.extend(f"- {item}" for item in draft.warnings)
    lines.extend(
        [
            "",
            "## Operating Rules To Define Before Use",
            "- Maximum portfolio drawdown stop",
            "- Rebalance frequency and drift bands",
            "- Single-stock thesis invalidation rules",
            "- Leveraged ETF stop and volatility throttle",
            "- Commodity sleeve roll/liquidity review",
        ]
    )
    return "\n".join(lines)


def portfolio_to_csv(draft: PortfolioDraft) -> str:
    lines = ["symbol,name,sleeve,asset_class,weight,rationale,risk"]
    for position in draft.positions:
        lines.append(
            ",".join(
                csv_cell(value)
                for value in (
                    position.symbol,
                    position.name,
                    position.sleeve,
                    position.asset_class,
                    f"{position.weight:.1f}",
                    position.rationale,
                    position.risk,
                )
            )
        )
    return "\n".join(lines) + "\n"


def dedupe_symbols(pool: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for symbol in pool:
        normalized = symbol.strip().upper()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return tuple(result)


def csv_cell(value: str) -> str:
    return f'"{value.replace(chr(34), chr(34) + chr(34))}"'

