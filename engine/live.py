from __future__ import annotations

import os
from dataclasses import dataclass

from risk.policy import RiskPolicy


class LiveTradingBlockedError(RuntimeError):
    pass


@dataclass(frozen=True)
class LiveTradingPolicy:
    enabled: bool
    risk_acknowledged: bool
    order_submission_enabled: bool
    strategy_id: str
    broker: str
    max_capital: float
    policy_version: str
    min_paper_days: int = 0
    min_shadow_days: int = 0
    min_paper_oos_periods: int = 0
    min_paper_oos_vs_backtest: float = 0.0
    paper_oos_backtest_excess: float = 0.08

    @property
    def ready(self) -> bool:
        return (
            self.enabled
            and self.risk_acknowledged
            and bool(self.strategy_id)
            and bool(self.broker)
            and self.max_capital > 0
            and bool(self.policy_version)
        )


def load_live_trading_policy() -> LiveTradingPolicy:
    broker = os.getenv("LIVE_BROKER", "").strip()
    live_broker = broker.lower() == "alpaca-live"
    # The forward-validation gates can be RAISED by env (stricter) but NOT silently lowered
    # below their safe live floor by a single .env toggle (e.g. =0). Weakening requires the
    # deliberate, auditable LIVE_ACCEPT_REDUCED_VALIDATION=true acknowledgement — otherwise the
    # floor holds (readiness-audit overlooked risk: "one .env toggle nukes forward validation").
    allow_reduced = _env_bool("LIVE_ACCEPT_REDUCED_VALIDATION")
    return LiveTradingPolicy(
        enabled=_env_bool("LIVE_TRADING_ENABLED"),
        risk_acknowledged=_env_bool("LIVE_TRADING_ACK_RISK"),
        order_submission_enabled=_env_bool("LIVE_ORDER_SUBMISSION_ENABLED"),
        strategy_id=os.getenv("LIVE_STRATEGY_ID", "").strip(),
        broker=broker,
        max_capital=float(os.getenv("LIVE_MAX_CAPITAL", "0") or "0"),
        policy_version=os.getenv("LIVE_POLICY_VERSION", "").strip(),
        min_paper_days=_gate_int("LIVE_MIN_PAPER_DAYS", 30 if live_broker else 0, allow_reduced),
        min_shadow_days=_gate_int("LIVE_MIN_SHADOW_DAYS", 10 if live_broker else 0, allow_reduced),
        min_paper_oos_periods=_gate_int(
            "LIVE_MIN_PAPER_OOS_PERIODS", 6 if live_broker else 0, allow_reduced
        ),
        min_paper_oos_vs_backtest=_gate_float(
            "LIVE_MIN_PAPER_OOS_VS_BACKTEST", 0.5 if live_broker else 0.0, allow_reduced
        ),
        paper_oos_backtest_excess=_env_float("LIVE_PAPER_OOS_BACKTEST_EXCESS", 0.08),
    )


def live_risk_policy(policy: LiveTradingPolicy | None = None) -> RiskPolicy:
    live_policy = policy or load_live_trading_policy()
    max_capital = live_policy.max_capital if live_policy.max_capital > 0 else 1_000.0
    allow_market_orders = _env_bool("LIVE_ALLOW_MARKET_ORDERS")
    allowed_order_types = ("market", "limit") if allow_market_orders else ("limit",)
    return RiskPolicy(
        policy_id=live_policy.policy_version or "default-live-v1",
        max_order_notional=max_capital * 0.25,
        max_daily_new_notional=max_capital,
        max_symbol_weight=0.35,
        max_gross_exposure=1.0,
        min_cash_fraction=0.02,
        max_limit_deviation=_env_float("LIVE_MAX_LIMIT_DEVIATION", 0.03),
        allowed_order_types=allowed_order_types,
    )


def assert_live_trading_enabled(policy: LiveTradingPolicy | None = None) -> None:
    current = policy or load_live_trading_policy()
    missing = []
    if not current.enabled:
        missing.append("LIVE_TRADING_ENABLED=true")
    if not current.risk_acknowledged:
        missing.append("LIVE_TRADING_ACK_RISK=true")
    if not current.strategy_id:
        missing.append("LIVE_STRATEGY_ID")
    if not current.broker:
        missing.append("LIVE_BROKER")
    if current.max_capital <= 0:
        missing.append("LIVE_MAX_CAPITAL>0")
    if not current.policy_version:
        missing.append("LIVE_POLICY_VERSION")
    if missing:
        raise LiveTradingBlockedError(
            "Live trading is disabled or incomplete. Missing: " + ", ".join(missing)
        )


def assert_live_order_submission_enabled(policy: LiveTradingPolicy | None = None) -> None:
    current = policy or load_live_trading_policy()
    assert_live_trading_enabled(current)
    if not current.order_submission_enabled:
        raise LiveTradingBlockedError(
            "Live order submission is disabled. Missing: LIVE_ORDER_SUBMISSION_ENABLED=true"
        )


def _env_bool(name: str) -> bool:
    return os.getenv(name, "").lower() == "true"


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    return float(raw)


def _gate_int(name: str, floor: int, allow_reduced: bool) -> int:
    """A forward-validation gate: env may RAISE it but not LOWER it below ``floor`` unless
    ``allow_reduced`` (LIVE_ACCEPT_REDUCED_VALIDATION) is set. ``floor`` is the safe live
    minimum (0 for paper/fake, so paper is unaffected)."""
    value = _env_int(name, floor)
    if value < floor and not allow_reduced:
        return floor
    return value


def _gate_float(name: str, floor: float, allow_reduced: bool) -> float:
    value = _env_float(name, floor)
    if value < floor and not allow_reduced:
        return floor
    return value
