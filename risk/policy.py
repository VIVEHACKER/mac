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
    # Aggregate weight cap per sector (|projected market value in sector| / equity). Default 1.0 =
    # off, so existing callers are unaffected; the live policy sets a real cap and supplies a
    # symbol->sector map. Without that map the pre-trade gate cannot see sectors (audit P1: the
    # submission path was structurally sector-blind — only the exposure monitor knew sectors).
    max_sector_weight: float = 1.0
    max_gross_exposure: float = 1.0
    min_cash_fraction: float = 0.05
    max_limit_deviation: float = 0.03
    max_daily_loss: float = 0.02
    # Sleeve / trailing kill-switch: a -25% drawdown from the all-time equity peak halts trading.
    # The daily 2% latch cannot catch a slow multi-day bleed, so this is the second backstop.
    max_drawdown_from_peak: float = 0.25
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
