from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from trader.execution.broker import (
    AccountSnapshot,
    BrokerClock,
    BrokerOrder,
    BrokerRejectedError,
    BrokerTemporaryError,
    PositionSnapshot,
)
from trader.execution.intents import OrderIntent

# Defaults chosen for an unattended daily-rebalance batch: a single broker call should
# return in well under 15s, and reads (account/positions/order status) are safe to retry
# a couple of times on a transient blip. submit_order is NEVER retried (see submit_order).
DEFAULT_TIMEOUT_S = 15.0
DEFAULT_READ_RETRIES = 2
DEFAULT_BACKOFF_S = 0.5

# 4xx statuses that are TRANSIENT/uncertain rather than a definite client-side rejection.
# 408 request-timeout and 429 rate-limit may succeed on retry and (for submit) leave the
# broker in an uncertain state, so they are treated as temporary, not as a hard rejection.
_TRANSIENT_4XX = {408, 429}

_NOT_FOUND = object()


class _CallTimeoutError(Exception):
    """A broker call exceeded its wall-clock budget; the broker state is unknown."""


class AlpacaBrokerAdapter:
    """Thin Alpaca adapter with fail-closed error classification.

    The alpaca imports stay inside the methods so the rest of the system remains usable
    without credentials or a matching alpaca-py install. Alpaca's API owns the final
    account, order, and fill state; this adapter only normalizes that state into local
    snapshots AND maps SDK/network failures onto the two broker error contracts the
    execution runner relies on:

      * ``BrokerRejectedError``  — a DEFINITE client-side rejection (4xx, e.g. bad params /
        insufficient buying power). The order was NOT placed; the runner records a reject.
      * ``BrokerTemporaryError`` — an UNCERTAIN outcome (network error, timeout, 5xx, 429).
        The order MIGHT have been placed; the runner latches a halt and stops the batch.

    Without this mapping a raw ``APIError``/network exception would propagate past the
    runner's ``except BrokerTemporaryError`` halt latch, so the live safety contract was
    effectively dead code on the real broker (only ``FakeBrokerAdapter`` ever exercised it).

    ``client`` may be injected for testing; otherwise a real ``TradingClient`` is built.
    """

    def __init__(
        self,
        api_key: str = "",
        secret_key: str = "",
        *,
        paper: bool = True,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        read_retries: int = DEFAULT_READ_RETRIES,
        backoff_s: float = DEFAULT_BACKOFF_S,
        sleep: Callable[[float], None] = time.sleep,
        client: Any | None = None,
    ):
        if client is None:
            from alpaca.trading.client import TradingClient

            client = TradingClient(api_key, secret_key, paper=paper)
        self.client = client
        self._timeout_s = timeout_s
        self._read_retries = max(0, read_retries)
        self._backoff_s = max(0.0, backoff_s)
        self._sleep = sleep

    def get_account(self) -> AccountSnapshot:
        account = self._request(self.client.get_account, "get account", retry=True)
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
            last_equity=_optional_float(getattr(account, "last_equity", None)),
        )

    def list_positions(self) -> list[PositionSnapshot]:
        rows = []
        for position in self._request(self.client.get_all_positions, "list positions", retry=True):
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

    def get_clock(self) -> BrokerClock:
        clock = self._request(self.client.get_clock, "get market clock", retry=True)
        timestamp = _datetime_utc(getattr(clock, "timestamp", None))
        return BrokerClock(
            is_open=bool(getattr(clock, "is_open", False)),
            timestamp=timestamp or datetime.now(UTC),
            next_open=_datetime_utc(getattr(clock, "next_open", None)),
            next_close=_datetime_utc(getattr(clock, "next_close", None)),
        )

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
        # retry=False: a submit that times out / 5xx / network-errors may ALREADY have been
        # accepted by the broker. Retrying could double-submit; the idempotent client_order_id
        # would have the broker reject the dup, but the safe contract is "uncertain -> halt and
        # let the next cycle (or a human) reconcile", not "retry blindly".
        raw = self._request(lambda: self.client.submit_order(request), "submit order", retry=False)
        return _map_alpaca_order(raw)

    def get_order(self, client_order_id: str) -> BrokerOrder | None:
        # A genuine 404 (no such order) is a legitimate None, NOT an error. Any OTHER failure
        # (network/timeout/5xx) is surfaced as BrokerTemporaryError instead of being swallowed
        # to None — a swallowed network error would masquerade as "order does not exist" and
        # could drive a silent duplicate submit on the next reconcile pass.
        raw = self._request(
            lambda: self.client.get_order_by_client_id(client_order_id),
            "get order",
            retry=True,
            not_found_ok=True,
        )
        if raw is _NOT_FOUND:
            return None
        return _map_alpaca_order(raw)

    def cancel_order(self, client_order_id: str) -> BrokerOrder | None:
        """Cancel a working order by client id; see BrokerAdapter.cancel_order for the contract.

        Unlike submit, cancel is safe to retry: a second cancel of the same order id answers
        422 (not cancelable), which lands in the benign-race branch below. The 422 race —
        the order reached a terminal state between our fetch and the cancel — resolves by
        re-fetching and returning the terminal order, because "you cannot recall it, here is
        what actually happened" is a truthful outcome, not an operator error."""
        current = self.get_order(client_order_id)
        if current is None:
            return None
        if current.terminal:
            return current  # nothing live at the broker to recall
        try:
            self._request(
                lambda: self.client.cancel_order_by_id(current.broker_order_id),
                "cancel order",
                retry=True,
            )
        except BrokerRejectedError:
            refreshed = self.get_order(client_order_id)
            if refreshed is not None and refreshed.terminal:
                return refreshed  # filled/canceled in the race window — benign
            raise
        return self.get_order(client_order_id) or current

    def _request(
        self,
        fn: Callable[[], Any],
        kind: str,
        *,
        retry: bool,
        not_found_ok: bool = False,
    ) -> Any:
        from alpaca.common.exceptions import APIError

        attempts = (self._read_retries + 1) if retry else 1
        last_temporary: BrokerTemporaryError | None = None
        for attempt in range(attempts):
            try:
                return _call_with_timeout(fn, self._timeout_s)
            except APIError as exc:
                status = _safe_status(exc)
                if not_found_ok and status == 404:
                    return _NOT_FOUND
                if status is not None and 400 <= status < 500 and status not in _TRANSIENT_4XX:
                    # Definite client-side rejection — do not retry, do not treat as uncertain.
                    raise BrokerRejectedError(_api_msg(exc, kind, status)) from exc
                last_temporary = BrokerTemporaryError(_api_msg(exc, kind, status))
            except _CallTimeoutError as exc:
                last_temporary = BrokerTemporaryError(
                    f"alpaca {kind} timed out after {self._timeout_s:.0f}s; broker state uncertain"
                )
                last_temporary.__cause__ = exc
            except Exception as exc:  # network / connection / unknown -> uncertain
                last_temporary = BrokerTemporaryError(
                    f"alpaca {kind} failed ({type(exc).__name__}: {exc}); broker state uncertain"
                )
                last_temporary.__cause__ = exc
            if attempt + 1 < attempts:
                self._sleep(self._backoff_s * (attempt + 1))
        assert last_temporary is not None  # attempts >= 1 guarantees one assignment on failure
        raise last_temporary


def _call_with_timeout(fn: Callable[[], Any], timeout_s: float | None) -> Any:
    """Run ``fn`` with a wall-clock cap without relying on SDK-level timeout support.

    alpaca-py's TradingClient exposes no timeout knob, so a stuck socket would otherwise hang
    an unattended cron batch forever. The call runs on a daemon thread; if it overruns we stop
    waiting and raise ``_CallTimeoutError`` (the orphaned thread cannot be killed but, being a daemon,
    will not block interpreter exit). The original exception, if any, is re-raised in the caller.
    """
    if not timeout_s or timeout_s <= 0:
        return fn()
    box: dict[str, Any] = {}

    def runner() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 — captured to re-raise in the caller thread
            box["error"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join(timeout_s)
    if thread.is_alive():
        raise _CallTimeoutError(f"call exceeded {timeout_s:.0f}s")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _safe_status(exc: Any) -> int | None:
    try:
        status = getattr(exc, "status_code", None)
    except Exception:
        return None
    return int(status) if isinstance(status, int) else None


def _api_msg(exc: Any, kind: str, status: int | None) -> str:
    return f"alpaca {kind} API error (status={status}): {exc}"


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
    submitted_at = _datetime_utc(getattr(order, "submitted_at", None)) or datetime.now(UTC)
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


def _datetime_utc(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if not isinstance(value, datetime):
        value = datetime.fromisoformat(str(value))
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
