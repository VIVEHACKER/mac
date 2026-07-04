from __future__ import annotations

from datetime import UTC, datetime

from trader.execution.broker import (
    AccountSnapshot,
    BrokerClock,
    BrokerOrder,
    BrokerRejectedError,
    PositionSnapshot,
)
from trader.execution.intents import OrderIntent


class ManualBrokerAdapter:
    """Operator-attested external broker state.

    This adapter intentionally does not place orders. It lets the live readiness,
    reconciliation, and ticket-generation paths run without Alpaca while keeping the actual
    execution step outside the system until a real broker API adapter exists.
    """

    def __init__(
        self,
        *,
        account: AccountSnapshot,
        positions: list[PositionSnapshot] | None = None,
        clock: BrokerClock | None = None,
    ):
        self.account = account
        self.positions = positions or []
        self.clock = clock or BrokerClock(is_open=False, timestamp=datetime.now(UTC))

    def get_account(self) -> AccountSnapshot:
        return self.account

    def list_positions(self) -> list[PositionSnapshot]:
        return list(self.positions)

    def get_clock(self) -> BrokerClock:
        return self.clock

    def submit_order(self, intent: OrderIntent) -> BrokerOrder:
        raise BrokerRejectedError(
            "manual broker does not submit orders automatically; use live-ticket and execute "
            "the ticket in the external broker"
        )

    def get_order(self, client_order_id: str) -> BrokerOrder | None:
        return None
