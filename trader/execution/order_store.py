from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from trader.execution.broker import TERMINAL_ORDER_STATUSES, BrokerOrder
from trader.execution.intents import OrderIntent

# Broker-order snapshot events (submit + in-batch poll + cross-cycle reconcile recovery).
_BROKER_ORDER_EVENTS = ("broker_submit", "broker_poll", "broker_reconcile")
# Non-broker events that cleanly resolve an intent without it ever being live at the broker
# (dry_run/risk_block = never sent; broker_reject = broker said no; broker_reconcile_absent =
# recovery confirmed the broker has no such order).
_RESOLVED_NONBROKER_EVENTS = (
    "dry_run",
    "risk_block",
    "broker_reject",
    "broker_reconcile_absent",
)


@dataclass(frozen=True)
class OrderEvent:
    event_type: str
    client_order_id: str
    ts: datetime
    status: str = ""
    broker_order_id: str = ""
    message: str = ""
    payload: dict[str, Any] | None = None


class JsonlOrderStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record_intent(self, intent: OrderIntent) -> None:
        self._append(
            {
                "record_type": "intent",
                "client_order_id": intent.client_order_id,
                "ts": _iso(intent.asof_ts),
                "payload": _dataclass_payload(intent),
            }
        )

    def record_event(self, event: OrderEvent) -> None:
        self._append(
            {
                "record_type": "event",
                "client_order_id": event.client_order_id,
                "ts": _iso(event.ts),
                "payload": _dataclass_payload(event),
            }
        )

    def record_broker_order(self, event_type: str, order: BrokerOrder) -> None:
        self.record_event(
            OrderEvent(
                event_type=event_type,
                client_order_id=order.client_order_id,
                ts=order.submitted_at,
                status=order.status,
                broker_order_id=order.broker_order_id,
                message=order.message,
                payload=_dataclass_payload(order),
            )
        )

    def has_intent(self, client_order_id: str) -> bool:
        return any(
            row.get("record_type") == "intent" and row.get("client_order_id") == client_order_id
            for row in self.rows()
        )

    def unresolved_intent_ids(self) -> list[str]:
        """Client order ids that were recorded (an intent exists) but never reached a recorded
        terminal resolution: no terminal broker order, and no clean dry_run/risk_block/
        broker_reject event. These may be live at the broker (process crashed between
        submit_order and the broker_submit record, or an uncertain submit) and must be
        resolved via get_order before new orders are sent. First-seen order is preserved."""
        intents: list[str] = []
        seen: set[str] = set()
        resolved: set[str] = set()
        for row in self.rows():
            cid = str(row.get("client_order_id") or "")
            if not cid:
                continue
            record_type = row.get("record_type")
            if record_type == "intent":
                if cid not in seen:
                    seen.add(cid)
                    intents.append(cid)
                continue
            if record_type != "event":
                continue
            payload = row.get("payload") or {}
            event_type = payload.get("event_type")
            # An intent is resolved by a clean non-broker event (never sent / rejected / absent)
            # or by a terminal broker-order snapshot. Anything else leaves it in-flight.
            if event_type in _RESOLVED_NONBROKER_EVENTS or (
                event_type in _BROKER_ORDER_EVENTS
                and str(payload.get("status", "")).lower() in TERMINAL_ORDER_STATUSES
            ):
                resolved.add(cid)
        return [cid for cid in intents if cid not in resolved]

    def latest_status(self, client_order_id: str) -> str | None:
        status: str | None = None
        for row in self.rows():
            if row.get("record_type") != "event" or row.get("client_order_id") != client_order_id:
                continue
            payload = row.get("payload") or {}
            status = payload.get("status") or payload.get("event_type")
        return status

    def latest_broker_orders(self) -> dict[str, dict[str, Any]]:
        """Latest broker-order snapshot per client_order_id, from broker_submit/broker_poll
        events. Returns the BrokerOrder payload dict (symbol/market/side/filled_qty/status/...);
        later events (e.g. a poll showing the final fill) overwrite earlier ones. Used to derive
        the reconciliation baseline from the system's own fills instead of a hand-typed string."""
        latest: dict[str, dict[str, Any]] = {}
        for row in self.rows():
            if row.get("record_type") != "event":
                continue
            event = row.get("payload") or {}
            if event.get("event_type") not in _BROKER_ORDER_EVENTS:
                continue
            order = event.get("payload") or {}
            cid = str(order.get("client_order_id") or row.get("client_order_id") or "")
            if cid:
                latest[cid] = order
        return latest

    def intent_count_on(self, day: date) -> int:
        return sum(
            1 for row in self.rows() if row.get("record_type") == "intent" and _row_date(row) == day
        )

    def buy_notional_on(self, day: date, marks: dict[str, float]) -> float:
        total = 0.0
        for row in self.rows():
            if row.get("record_type") != "intent" or _row_date(row) != day:
                continue
            payload = row.get("payload") or {}
            if str(payload.get("side", "")).lower() != "buy":
                continue
            symbol = str(payload.get("symbol", "")).upper()
            qty = float(payload.get("qty", 0.0) or 0.0)
            limit_price = payload.get("limit_price")
            if limit_price is None or limit_price == "":
                price = float(marks.get(symbol, 0.0))
            else:
                price = float(limit_price)
            total += max(qty * price, 0.0)
        return total

    def rows(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows

    def _append(self, row: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def _dataclass_payload(value: Any) -> dict[str, Any]:
    raw = asdict(value)
    return {key: _json_value(item) for key, item in raw.items()}


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _iso(value: datetime | None) -> str:
    if value is None:
        value = datetime.now(UTC)
    return value.isoformat()


def _row_date(row: dict[str, Any]) -> date | None:
    raw = row.get("ts")
    if not raw:
        return None
    return datetime.fromisoformat(str(raw)).date()
