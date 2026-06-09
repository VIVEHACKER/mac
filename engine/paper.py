from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from trader.execution.broker import (
    AccountSnapshot,
    BrokerOrder,
    BrokerRejectedError,
    PositionSnapshot,
)
from trader.execution.intents import OrderIntent


@dataclass(frozen=True)
class PaperOrder:
    order_id: str
    strategy: str
    symbol: str
    market: str
    side: str
    qty: float
    price: float
    status: str
    ts: datetime


@dataclass(frozen=True)
class PaperPosition:
    symbol: str
    market: str
    qty: float
    avg_cost: float


class PaperBroker:
    def __init__(self, initial_cash: float = 10_000.0, *, marks: dict[str, float] | None = None):
        if initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        self.cash = initial_cash
        self.positions: dict[tuple[str, str], PaperPosition] = {}
        self.orders: list[PaperOrder] = []
        # Marks for market-order fills + equity/position valuation when PaperBroker is
        # driven through the BrokerAdapter protocol (a market OrderIntent carries no price).
        self._marks: dict[str, float] = {k.upper(): v for k, v in (marks or {}).items()}
        self._orders_by_coid: dict[str, BrokerOrder] = {}

    def submit_market_order(
        self,
        *,
        strategy: str,
        symbol: str,
        market: str,
        side: str,
        qty: float,
        price: float,
        ts: datetime,
    ) -> PaperOrder:
        if qty <= 0 or price <= 0:
            raise ValueError("qty and price must be positive")
        side_key = side.lower()
        if side_key not in {"buy", "sell"}:
            raise ValueError("side must be buy or sell")
        signed_qty = qty if side_key == "buy" else -qty
        self._apply_fill(
            symbol=symbol.upper(), market=market.lower(), signed_qty=signed_qty, price=price
        )
        order = PaperOrder(
            order_id=str(uuid4()),
            strategy=strategy,
            symbol=symbol.upper(),
            market=market.lower(),
            side=side_key,
            qty=qty,
            price=price,
            status="filled",
            ts=ts,
        )
        self.orders.append(order)
        return order

    def equity(self, marks: dict[str, float]) -> float:
        value = self.cash
        for position in self.positions.values():
            value += position.qty * marks.get(position.symbol, position.avg_cost)
        return value

    def gross_exposure(self, marks: dict[str, float]) -> float:
        equity = self.equity(marks)
        if equity <= 0:
            return float("inf")
        gross = sum(
            abs(position.qty) * marks.get(position.symbol, position.avg_cost)
            for position in self.positions.values()
        )
        return gross / equity

    # --- BrokerAdapter protocol: lets PaperBroker run the SAME process_order_intents()
    # execution path (pretrade gate + kill-switch + order store) that the live Alpaca
    # adapter does, so paper and live differ only by which broker is injected. ---

    def set_marks(self, marks: dict[str, float]) -> None:
        self._marks = {k.upper(): v for k, v in marks.items()}

    def get_account(self) -> AccountSnapshot:
        equity = self.equity(self._marks)
        return AccountSnapshot(
            account_id="paper",
            buying_power=self.cash,
            cash=self.cash,
            equity=equity,
            last_equity=equity,
        )

    def list_positions(self) -> list[PositionSnapshot]:
        return [
            PositionSnapshot(
                symbol=position.symbol,
                market=position.market,
                qty=position.qty,
                market_value=position.qty * self._marks.get(position.symbol, position.avg_cost),
                avg_entry_price=position.avg_cost,
            )
            for position in self.positions.values()
        ]

    def submit_order(self, intent: OrderIntent) -> BrokerOrder:
        order = intent.normalized()
        price = order.limit_price or self._marks.get(order.symbol)
        if not price or price <= 0:
            raise BrokerRejectedError(f"paper broker has no positive mark for {order.symbol}")
        if order.qty <= 0:
            raise BrokerRejectedError(f"order qty must be positive (got {order.qty})")
        paper = self.submit_market_order(
            strategy=order.strategy,
            symbol=order.symbol,
            market=order.market,
            side=order.side,
            qty=order.qty,
            price=price,
            ts=order.asof_ts or datetime.now(UTC),
        )
        broker_order = BrokerOrder(
            broker_order_id=paper.order_id,
            client_order_id=order.client_order_id,
            symbol=paper.symbol,
            market=paper.market,
            side=paper.side,
            qty=paper.qty,
            filled_qty=paper.qty,
            status="filled",
            submitted_at=paper.ts,
            filled_avg_price=paper.price,
        )
        self._orders_by_coid[order.client_order_id] = broker_order
        return broker_order

    def get_order(self, client_order_id: str) -> BrokerOrder | None:
        return self._orders_by_coid.get(client_order_id)

    def _apply_fill(self, *, symbol: str, market: str, signed_qty: float, price: float) -> None:
        key = (symbol, market)
        current = self.positions.get(key)
        previous_qty = current.qty if current else 0.0
        previous_cost = current.avg_cost if current else 0.0
        new_qty = previous_qty + signed_qty
        self.cash -= signed_qty * price
        if abs(new_qty) < 1e-12:
            self.positions.pop(key, None)
            return
        if previous_qty == 0 or (previous_qty > 0) == (signed_qty > 0):
            avg_cost = ((previous_qty * previous_cost) + (signed_qty * price)) / new_qty
        else:
            avg_cost = previous_cost
        self.positions[key] = PaperPosition(
            symbol=symbol, market=market, qty=new_qty, avg_cost=avg_cost
        )
