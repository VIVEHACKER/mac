from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4


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
    def __init__(self, initial_cash: float = 10_000.0):
        if initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        self.cash = initial_cash
        self.positions: dict[tuple[str, str], PaperPosition] = {}
        self.orders: list[PaperOrder] = []

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
        self._apply_fill(symbol=symbol.upper(), market=market.lower(), signed_qty=signed_qty, price=price)
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
        gross = sum(abs(position.qty) * marks.get(position.symbol, position.avg_cost) for position in self.positions.values())
        return gross / equity

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
        self.positions[key] = PaperPosition(symbol=symbol, market=market, qty=new_qty, avg_cost=avg_cost)
