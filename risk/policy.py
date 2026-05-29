from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RiskPolicy:
    policy_id: str = "default-live-v1"
    allowed_markets: tuple[str, ...] = ("us",)
    allowed_order_types: tuple[str, ...] = ("market", "limit")
    allowed_sides: tuple[str, ...] = ("buy", "sell")
    allow_margin: bool = False
    allow_short: bool = False
    max_order_notional: float = 1_000.0
    max_daily_new_notional: float = 2_000.0
    max_symbol_weight: float = 0.25
    max_gross_exposure: float = 1.0
    min_cash_fraction: float = 0.05
    max_limit_deviation: float = 0.03
    max_daily_loss: float = 0.02
    max_orders_per_day: int = 20
    max_daytrade_count: int = 3


@dataclass(frozen=True)
class RiskCheckResult:
    passed: bool
    reasons: tuple[str, ...] = ()

    @classmethod
    def pass_(cls) -> RiskCheckResult:
        return cls(True, ())

    @classmethod
    def fail(cls, reasons: list[str]) -> RiskCheckResult:
        return cls(False, tuple(reasons))
