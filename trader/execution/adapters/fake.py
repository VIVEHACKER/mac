from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from trader.execution.broker import (
    AccountSnapshot,
    BrokerClock,
    BrokerOrder,
    BrokerRejectedError,
    BrokerTemporaryError,
    PositionSnapshot,
)
from trader.execution.intents import OrderIntent


class FakeBrokerAdapter:
    def __init__(
        self,
        *,
        account: AccountSnapshot | None = None,
        positions: list[PositionSnapshot] | None = None,
        mode: str = "fill",
        fill_ratio: float = 1.0,
        clock: BrokerClock | None = None,
    ):
        self.account = account or AccountSnapshot(
            account_id="fake",
            buying_power=100_000.0,
            cash=100_000.0,
            equity=100_000.0,
        )
        self.positions = {
            (item.symbol.upper(), item.market.lower()): item for item in positions or []
        }
        self.mode = mode
        self.fill_ratio = fill_ratio
        self.clock = clock or BrokerClock(is_open=True, timestamp=datetime.now(UTC))
        self.orders: dict[str, BrokerOrder] = {}

    def get_account(self) -> AccountSnapshot:
        return self.account

    def list_positions(self) -> list[PositionSnapshot]:
        return list(self.positions.values())

    def get_clock(self) -> BrokerClock:
        return self.clock

    def submit_order(self, intent: OrderIntent) -> BrokerOrder:
        normalized = intent.normalized()
        if self.mode == "reject":
            raise BrokerRejectedError("fake broker rejected order")
        if self.mode == "timeout":
            raise BrokerTemporaryError("fake broker timed out after submit")

        filled_qty = normalized.qty * self.fill_ratio if self.mode == "partial" else normalized.qty
        status = "partially_filled" if filled_qty < normalized.qty else "filled"
        order = BrokerOrder(
            broker_order_id=f"fake-{uuid4()}",
            client_order_id=normalized.client_order_id,
            symbol=normalized.symbol,
            market=normalized.market,
            side=normalized.side,
            qty=normalized.qty,
            filled_qty=filled_qty,
            status=status,
            submitted_at=datetime.now(UTC),
            filled_avg_price=normalized.limit_price,
        )
        self.orders[normalized.client_order_id] = order
        return order

    def get_order(self, client_order_id: str) -> BrokerOrder | None:
        return self.orders.get(client_order_id)

    def cancel_order(self, client_order_id: str) -> BrokerOrder | None:
        """Cancel contract mirror of the live adapters: unknown -> None, terminal -> no-op
        (returned unchanged), working -> canceled snapshot with the partial fill preserved."""
        order = self.orders.get(client_order_id)
        if order is None:
            return None
        if order.terminal:
            return order
        canceled = replace(order, status="canceled")
        self.orders[client_order_id] = canceled
        return canceled
