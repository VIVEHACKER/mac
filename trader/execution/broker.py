from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from trader.execution.intents import OrderIntent

TERMINAL_ORDER_STATUSES = {
    "filled",
    "canceled",
    "expired",
    "rejected",
    "stopped",
    "done_for_day",
}


class BrokerError(RuntimeError):
    pass


class BrokerRejectedError(BrokerError):
    pass


class BrokerTemporaryError(BrokerError):
    pass


@dataclass(frozen=True)
class AccountSnapshot:
    account_id: str
    buying_power: float
    cash: float
    equity: float
    trading_blocked: bool = False
    account_blocked: bool = False
    pattern_day_trader: bool = False
    daytrade_count: int = 0
    currency: str = "USD"
    # Prior trading-day close equity (the authoritative daily-loss kill-switch baseline). Optional:
    # brokers that do not expose it leave it None and the tracker falls back to local history.
    last_equity: float | None = None


@dataclass(frozen=True)
class PositionSnapshot:
    symbol: str
    market: str
    qty: float
    market_value: float
    avg_entry_price: float = 0.0


@dataclass(frozen=True)
class BrokerClock:
    is_open: bool
    timestamp: datetime
    next_open: datetime | None = None
    next_close: datetime | None = None


@dataclass(frozen=True)
class BrokerOrder:
    broker_order_id: str
    client_order_id: str
    symbol: str
    market: str
    side: str
    qty: float
    filled_qty: float
    status: str
    submitted_at: datetime
    filled_avg_price: float | None = None
    message: str = ""

    @property
    def terminal(self) -> bool:
        return self.status.lower() in TERMINAL_ORDER_STATUSES


class BrokerAdapter(Protocol):
    def get_account(self) -> AccountSnapshot: ...

    def list_positions(self) -> list[PositionSnapshot]: ...

    def get_clock(self) -> BrokerClock: ...

    def submit_order(self, intent: OrderIntent) -> BrokerOrder: ...

    def get_order(self, client_order_id: str) -> BrokerOrder | None: ...
