from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256


@dataclass(frozen=True)
class OrderIntent:
    strategy: str
    symbol: str
    market: str
    side: str
    qty: float
    order_type: str = "market"
    limit_price: float | None = None
    time_in_force: str = "day"
    rebalance_key: str = ""
    reason: str = ""
    asof_ts: datetime | None = None
    client_order_id: str = ""

    def normalized(self) -> OrderIntent:
        intent = replace(
            self,
            strategy=self.strategy.strip(),
            symbol=self.symbol.upper().strip(),
            market=self.market.lower().strip(),
            side=self.side.lower().strip(),
            order_type=self.order_type.lower().strip(),
            time_in_force=self.time_in_force.lower().strip(),
            asof_ts=self.asof_ts or datetime.now(UTC),
        )
        if intent.client_order_id:
            return intent
        return replace(intent, client_order_id=build_client_order_id(intent))

    @property
    def notional_price(self) -> float:
        return self.limit_price or 0.0


@dataclass(frozen=True)
class TargetPosition:
    symbol: str
    market: str
    target_qty: float


@dataclass(frozen=True)
class RebalancePlan:
    strategy: str
    rebalance_key: str
    generated_at: datetime
    intents: tuple[OrderIntent, ...]


def build_client_order_id(intent: OrderIntent) -> str:
    payload = "|".join(
        [
            intent.strategy.strip(),
            intent.symbol.upper().strip(),
            intent.market.lower().strip(),
            intent.side.lower().strip(),
            _format_float(intent.qty),
            intent.order_type.lower().strip(),
            _format_float(intent.limit_price),
            intent.time_in_force.lower().strip(),
            intent.rebalance_key.strip(),
        ]
    )
    return "trd-" + sha256(payload.encode("utf-8")).hexdigest()[:28]


def _format_float(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.8f}".rstrip("0").rstrip(".")

