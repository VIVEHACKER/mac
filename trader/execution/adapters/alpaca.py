from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from trader.execution.broker import AccountSnapshot, BrokerOrder, PositionSnapshot
from trader.execution.intents import OrderIntent


class AlpacaBrokerAdapter:
    """Thin Alpaca adapter.

    The imports stay inside the methods so the rest of the system remains usable without
    credentials or a matching alpaca-py install. Alpaca's API owns the final account,
    order, and fill state; this adapter only normalizes that state into local snapshots.
    """

    def __init__(self, api_key: str, secret_key: str, *, paper: bool = True):
        from alpaca.trading.client import TradingClient

        self.client = TradingClient(api_key, secret_key, paper=paper)

    def get_account(self) -> AccountSnapshot:
        account = self.client.get_account()
        return AccountSnapshot(
            account_id=str(getattr(account, "id", "")),
            buying_power=float(getattr(account, "buying_power", 0.0)),
            cash=float(getattr(account, "cash", 0.0)),
            equity=float(getattr(account, "equity", 0.0)),
            trading_blocked=bool(getattr(account, "trading_blocked", False)),
            account_blocked=bool(getattr(account, "account_blocked", False)),
            pattern_day_trader=bool(getattr(account, "pattern_day_trader", False)),
            daytrade_count=int(getattr(account, "daytrade_count", 0) or 0),
            currency=str(getattr(account, "currency", "USD")),
        )

    def list_positions(self) -> list[PositionSnapshot]:
        rows = []
        for position in self.client.get_all_positions():
            rows.append(
                PositionSnapshot(
                    symbol=str(getattr(position, "symbol", "")).upper(),
                    market="us",
                    qty=float(getattr(position, "qty", 0.0)),
                    market_value=float(getattr(position, "market_value", 0.0)),
                    avg_entry_price=float(getattr(position, "avg_entry_price", 0.0)),
                )
            )
        return rows

    def submit_order(self, intent: OrderIntent) -> BrokerOrder:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

        normalized = intent.normalized()
        side = OrderSide.BUY if normalized.side == "buy" else OrderSide.SELL
        tif = _time_in_force(normalized.time_in_force, TimeInForce)
        request: Any
        if normalized.order_type == "limit":
            if normalized.limit_price is None:
                raise ValueError("limit order requires limit_price")
            request = LimitOrderRequest(
                symbol=normalized.symbol,
                qty=normalized.qty,
                side=side,
                time_in_force=tif,
                limit_price=normalized.limit_price,
                client_order_id=normalized.client_order_id,
            )
        else:
            request = MarketOrderRequest(
                symbol=normalized.symbol,
                qty=normalized.qty,
                side=side,
                time_in_force=tif,
                client_order_id=normalized.client_order_id,
            )
        return _map_alpaca_order(self.client.submit_order(request))

    def get_order(self, client_order_id: str) -> BrokerOrder | None:
        try:
            return _map_alpaca_order(self.client.get_order_by_client_id(client_order_id))
        except Exception:
            return None


def _time_in_force(value: str, enum_type):
    normalized = value.lower()
    if normalized == "gtc":
        return enum_type.GTC
    if normalized == "ioc":
        return enum_type.IOC
    if normalized == "fok":
        return enum_type.FOK
    return enum_type.DAY


def _map_alpaca_order(order) -> BrokerOrder:
    submitted_at = getattr(order, "submitted_at", None) or datetime.now(UTC)
    if getattr(submitted_at, "tzinfo", None) is None:
        submitted_at = submitted_at.replace(tzinfo=UTC)
    return BrokerOrder(
        broker_order_id=str(getattr(order, "id", "")),
        client_order_id=str(getattr(order, "client_order_id", "")),
        symbol=str(getattr(order, "symbol", "")).upper(),
        market="us",
        side=str(getattr(order, "side", "")).lower(),
        qty=float(getattr(order, "qty", 0.0)),
        filled_qty=float(getattr(order, "filled_qty", 0.0) or 0.0),
        status=str(getattr(order, "status", "")).lower(),
        submitted_at=submitted_at,
        filled_avg_price=_optional_float(getattr(order, "filled_avg_price", None)),
    )


def _optional_float(value) -> float | None:
    if value in (None, ""):
        return None
    return float(value)
