from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DcfResult:
    fair_value: float
    enterprise_value: float
    terminal_value: float
    wacc: float
    terminal_growth: float


def discounted_cash_flow(
    *,
    free_cash_flow: float,
    shares_out: float,
    net_debt: float = 0.0,
    growth: float = 0.05,
    wacc: float = 0.09,
    terminal_growth: float = 0.025,
    years: int = 5,
) -> DcfResult:
    if shares_out <= 0:
        raise ValueError("shares_out must be positive")
    if wacc <= terminal_growth:
        raise ValueError("wacc must be greater than terminal_growth")

    projected = [free_cash_flow * ((1 + growth) ** year) for year in range(1, years + 1)]
    discounted = [cash_flow / ((1 + wacc) ** year) for year, cash_flow in enumerate(projected, 1)]
    terminal_cash_flow = projected[-1] * (1 + terminal_growth)
    terminal_value = terminal_cash_flow / (wacc - terminal_growth)
    discounted_terminal = terminal_value / ((1 + wacc) ** years)
    enterprise_value = sum(discounted) + discounted_terminal
    equity_value = enterprise_value - net_debt
    return DcfResult(
        fair_value=equity_value / shares_out,
        enterprise_value=enterprise_value,
        terminal_value=terminal_value,
        wacc=wacc,
        terminal_growth=terminal_growth,
    )
