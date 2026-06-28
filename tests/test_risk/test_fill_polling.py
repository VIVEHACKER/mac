"""Step 2: post-submit fill polling + auto-reconcile from the order store.

Addresses the readiness-audit gap "async fill (filled_qty=0) leaves the ledger stuck on a
non-terminal snapshot" and "reconcile compares against a hand-typed --expected string".
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from risk.halt_state import HaltStateStore
from risk.policy import RiskPolicy
from trader.execution.broker import (
    AccountSnapshot,
    BrokerClock,
    BrokerOrder,
    BrokerTemporaryError,
    PositionSnapshot,
)
from trader.execution.intents import OrderIntent
from trader.execution.order_store import JsonlOrderStore
from trader.execution.reconciler import expected_positions_from_store
from trader.execution.runner import FillPoll, process_order_intents


def _intent(symbol: str = "qqq", qty: float = 2, key: str = "2026-05-12") -> OrderIntent:
    return OrderIntent(
        strategy="approved-etf",
        symbol=symbol,
        market="us",
        side="buy",
        qty=qty,
        order_type="limit",
        limit_price=100,
        rebalance_key=key,
        asof_ts=datetime(2026, 5, 12, tzinfo=UTC),
    ).normalized()


def _account() -> AccountSnapshot:
    return AccountSnapshot("test", buying_power=10_000, cash=10_000, equity=10_000)


def _clock() -> BrokerClock:
    return BrokerClock(is_open=True, timestamp=datetime(2026, 5, 12, tzinfo=UTC))


def _order(cid: str, symbol: str, side: str, qty: float) -> BrokerOrder:
    return BrokerOrder(
        broker_order_id=f"b-{cid}",
        client_order_id=cid,
        symbol=symbol,
        market="us",
        side=side,
        qty=qty,
        filled_qty=qty,
        status="filled",
        submitted_at=datetime(2026, 5, 12, tzinfo=UTC),
    )


class _FillProgressionBroker:
    """submit returns a non-terminal accepted order (filled_qty=0); get_order walks a fill
    progression (e.g. partially_filled -> filled) on successive polls."""

    def __init__(self, statuses: list[tuple[str, float]]):
        self._statuses = statuses
        self._submitted: dict[str, BrokerOrder] = {}
        self._poll_idx: dict[str, int] = {}

    def get_account(self) -> AccountSnapshot:
        return _account()

    def list_positions(self) -> list[PositionSnapshot]:
        return []

    def get_clock(self) -> BrokerClock:
        return _clock()

    def submit_order(self, intent: OrderIntent) -> BrokerOrder:
        n = intent.normalized()
        order = BrokerOrder(
            broker_order_id="b1",
            client_order_id=n.client_order_id,
            symbol=n.symbol,
            market=n.market,
            side=n.side,
            qty=n.qty,
            filled_qty=0.0,
            status="accepted",
            submitted_at=datetime(2026, 5, 12, tzinfo=UTC),
        )
        self._submitted[n.client_order_id] = order
        self._poll_idx[n.client_order_id] = 0
        return order

    def get_order(self, client_order_id: str) -> BrokerOrder | None:
        base = self._submitted[client_order_id]
        i = self._poll_idx.get(client_order_id, 0)
        if i >= len(self._statuses):
            return base
        status, filled = self._statuses[i]
        self._poll_idx[client_order_id] = i + 1
        return replace(base, status=status, filled_qty=filled)


class _PollFailsBroker(_FillProgressionBroker):
    def get_order(self, client_order_id: str) -> BrokerOrder | None:
        raise BrokerTemporaryError("status endpoint blip")


def _run(broker, tmp_path, **kwargs):
    store = JsonlOrderStore(tmp_path / "orders.jsonl")
    halt = HaltStateStore(tmp_path / "halt.json")
    results = process_order_intents(
        [_intent()],
        broker=broker,
        store=store,
        halt_store=halt,
        policy=RiskPolicy(max_order_notional=1_000, max_symbol_weight=1.0),
        marks={"QQQ": 100},
        dry_run=False,
        reference_equity=10_000.0,
        sleep=lambda _seconds: None,
        **kwargs,
    )
    return results, store, halt


def _poll_events(store: JsonlOrderStore) -> list[dict]:
    return [
        (row.get("payload") or {})
        for row in store.rows()
        if (row.get("payload") or {}).get("event_type", "").startswith("broker_poll")
    ]


def test_fill_polling_records_partial_then_filled(tmp_path) -> None:
    broker = _FillProgressionBroker([("partially_filled", 1.0), ("filled", 2.0)])
    results, store, _ = _run(broker, tmp_path, fill_poll=FillPoll(max_polls=3, interval_s=0.0))

    assert results[0].status == "filled"  # final terminal status, not the accepted snapshot
    polls = _poll_events(store)
    assert [p["event_type"] for p in polls] == ["broker_poll", "broker_poll"]


def test_fill_polling_stops_at_terminal(tmp_path) -> None:
    broker = _FillProgressionBroker([("filled", 2.0), ("filled", 2.0)])
    _results, store, _ = _run(broker, tmp_path, fill_poll=FillPoll(max_polls=5, interval_s=0.0))
    # First poll is already terminal -> polling stops; exactly one broker_poll event.
    assert len(_poll_events(store)) == 1


def test_fill_polling_blip_records_uncertain_without_halt(tmp_path) -> None:
    broker = _PollFailsBroker([("filled", 2.0)])
    results, store, halt = _run(broker, tmp_path, fill_poll=FillPoll(max_polls=3, interval_s=0.0))

    # The order is already submitted (accepted); a status-check blip must NOT halt.
    assert results[0].status == "accepted"
    assert not halt.current().halted
    assert [p["event_type"] for p in _poll_events(store)] == ["broker_poll_uncertain"]


def test_no_polling_when_fill_poll_absent(tmp_path) -> None:
    broker = _FillProgressionBroker([("filled", 2.0)])
    _results, store, _ = _run(broker, tmp_path)  # no fill_poll -> default None
    assert _poll_events(store) == []


def test_expected_positions_from_store_nets_signed_fills(tmp_path) -> None:
    store = JsonlOrderStore(tmp_path / "orders.jsonl")
    # QQQ: buy 5 then sell 2 (two distinct orders) -> net +3
    store.record_broker_order("broker_submit", _order("c1", "QQQ", "buy", 5))
    store.record_broker_order("broker_submit", _order("c2", "QQQ", "sell", 2))
    # AAA: one buy recorded twice (submit then a fill poll) -> deduped by client_order_id -> +3
    store.record_broker_order("broker_submit", _order("c3", "AAA", "buy", 3))
    store.record_broker_order("broker_poll", _order("c3", "AAA", "buy", 3))
    # ZZZ: buy 2 then sell 2 -> nets to zero -> dropped
    store.record_broker_order("broker_submit", _order("c4", "ZZZ", "buy", 2))
    store.record_broker_order("broker_submit", _order("c5", "ZZZ", "sell", 2))

    expected = expected_positions_from_store(store)

    assert expected[("QQQ", "us")] == 3.0
    assert expected[("AAA", "us")] == 3.0  # the poll did not double-count
    assert ("ZZZ", "us") not in expected


def test_latest_broker_orders_keeps_last_snapshot_per_order(tmp_path) -> None:
    store = JsonlOrderStore(tmp_path / "orders.jsonl")
    store.record_broker_order(
        "broker_submit",
        replace(_order("c1", "QQQ", "buy", 4), filled_qty=0.0, status="accepted"),
    )
    store.record_broker_order("broker_poll", _order("c1", "QQQ", "buy", 4))  # final fill

    latest = store.latest_broker_orders()
    assert latest["c1"]["status"] == "filled"
    assert latest["c1"]["filled_qty"] == 4.0


class _CancelOnPollBroker:
    """submit returns accepted (filled 0); get_order returns a TERMINAL canceled (filled 0)."""

    def get_account(self) -> AccountSnapshot:
        return _account()

    def list_positions(self) -> list[PositionSnapshot]:
        return []

    def get_clock(self) -> BrokerClock:
        return _clock()

    def submit_order(self, intent: OrderIntent) -> BrokerOrder:
        n = intent.normalized()
        return BrokerOrder(
            broker_order_id="b1",
            client_order_id=n.client_order_id,
            symbol=n.symbol,
            market=n.market,
            side=n.side,
            qty=n.qty,
            filled_qty=0.0,
            status="accepted",
            submitted_at=datetime(2026, 5, 12, tzinfo=UTC),
        )

    def get_order(self, client_order_id: str) -> BrokerOrder:
        return BrokerOrder(
            broker_order_id="b1",
            client_order_id=client_order_id,
            symbol="AAA",
            market="us",
            side="buy",
            qty=40,
            filled_qty=0.0,
            status="canceled",
            submitted_at=datetime(2026, 5, 12, tzinfo=UTC),
        )


def test_terminal_nofill_poll_does_not_project(tmp_path) -> None:
    # Codex Step-2 P1: a buy that polls to a terminal canceled (filled_qty=0) must NOT project
    # its 4,000 notional. If it wrongly did, B's projected cash (6,000 -> 2,000) would breach
    # the 50% reserve and B would be risk_block; with the fix B's cash is intact and B submits.
    store = JsonlOrderStore(tmp_path / "orders.jsonl")
    halt = HaltStateStore(tmp_path / "halt.json")

    def _buy(symbol: str, key: str) -> OrderIntent:
        return OrderIntent(
            strategy="approved-etf",
            symbol=symbol,
            market="us",
            side="buy",
            qty=40,
            order_type="limit",
            limit_price=100,
            rebalance_key=key,
            asof_ts=datetime(2026, 5, 12, tzinfo=UTC),
        ).normalized()

    results = process_order_intents(
        [_buy("AAA", "a"), _buy("BBB", "b")],
        broker=_CancelOnPollBroker(),
        store=store,
        halt_store=halt,
        policy=RiskPolicy(
            max_order_notional=5_000,
            max_daily_new_notional=20_000,
            max_symbol_weight=1.0,
            max_gross_exposure=2.0,
            min_cash_fraction=0.5,
        ),
        marks={"AAA": 100, "BBB": 100},
        dry_run=False,
        reference_equity=10_000.0,
        fill_poll=FillPoll(max_polls=2, interval_s=0.0),
        sleep=lambda _seconds: None,
    )

    assert [r.status for r in results] == ["canceled", "canceled"]
