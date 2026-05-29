from __future__ import annotations

from datetime import UTC, datetime

from risk.halt_state import HaltStateStore
from risk.policy import RiskPolicy
from risk.pretrade import evaluate_pretrade_order
from trader.execution.adapters.fake import FakeBrokerAdapter
from trader.execution.broker import AccountSnapshot, PositionSnapshot
from trader.execution.intents import OrderIntent
from trader.execution.order_store import JsonlOrderStore
from trader.execution.reconciler import reconcile_positions
from trader.execution.runner import process_order_intents


def test_order_intent_idempotency_is_stable() -> None:
    first = _intent().normalized()
    second = _intent().normalized()

    assert first.client_order_id == second.client_order_id


def test_pretrade_blocks_order_before_broker_when_notional_too_large() -> None:
    result = evaluate_pretrade_order(
        _intent(qty=100).normalized(),
        policy=RiskPolicy(max_order_notional=500),
        account=AccountSnapshot("test", buying_power=100_000, cash=100_000, equity=100_000),
        positions=[],
        marks={"QQQ": 100},
    )

    assert not result.passed
    assert any("order notional" in reason for reason in result.reasons)


def test_pretrade_blocks_naked_short_when_shorting_disabled() -> None:
    sell_intent = _intent(qty=3).normalized()
    result = evaluate_pretrade_order(
        OrderIntent(
            strategy=sell_intent.strategy,
            symbol=sell_intent.symbol,
            market=sell_intent.market,
            side="sell",
            qty=sell_intent.qty,
            order_type=sell_intent.order_type,
            limit_price=sell_intent.limit_price,
            rebalance_key=sell_intent.rebalance_key,
            asof_ts=sell_intent.asof_ts,
        ).normalized(),
        policy=RiskPolicy(max_order_notional=1_000, max_symbol_weight=1.0),
        account=AccountSnapshot("test", buying_power=100_000, cash=100_000, equity=100_000),
        positions=[PositionSnapshot("QQQ", "us", qty=1, market_value=100)],
        marks={"QQQ": 100},
    )

    assert not result.passed
    assert any("short selling is not allowed" in reason for reason in result.reasons)


def test_pretrade_blocks_buy_that_breaks_cash_reserve() -> None:
    result = evaluate_pretrade_order(
        _intent(qty=99).normalized(),
        policy=RiskPolicy(
            max_order_notional=10_000,
            max_symbol_weight=1.0,
            max_gross_exposure=1.0,
            min_cash_fraction=0.05,
        ),
        account=AccountSnapshot("test", buying_power=10_000, cash=10_000, equity=10_000),
        positions=[],
        marks={"QQQ": 100},
    )

    assert not result.passed
    assert any("projected cash fraction" in reason for reason in result.reasons)


def test_pretrade_blocks_limit_price_far_from_mark() -> None:
    intent = OrderIntent(
        strategy="approved-etf",
        symbol="QQQ",
        market="us",
        side="buy",
        qty=2,
        order_type="limit",
        limit_price=110,
        rebalance_key="2026-05-12",
        asof_ts=datetime(2026, 5, 12, tzinfo=UTC),
    ).normalized()

    result = evaluate_pretrade_order(
        intent,
        policy=RiskPolicy(max_order_notional=1_000, max_symbol_weight=1.0, max_limit_deviation=0.03),
        account=AccountSnapshot("test", buying_power=100_000, cash=100_000, equity=100_000),
        positions=[],
        marks={"QQQ": 100},
    )

    assert not result.passed
    assert any("above mark" in reason for reason in result.reasons)


def test_runner_dry_run_records_intent_and_skips_duplicate(tmp_path) -> None:
    store = JsonlOrderStore(tmp_path / "orders.jsonl")
    halt = HaltStateStore(tmp_path / "halt.json")
    broker = FakeBrokerAdapter(
        account=AccountSnapshot("test", buying_power=10_000, cash=10_000, equity=10_000)
    )
    intent = _intent().normalized()

    first = process_order_intents(
        [intent],
        broker=broker,
        store=store,
        halt_store=halt,
        policy=RiskPolicy(max_order_notional=1_000, max_symbol_weight=1.0),
        marks={"QQQ": 100},
        dry_run=True,
    )
    second = process_order_intents(
        [intent],
        broker=broker,
        store=store,
        halt_store=halt,
        policy=RiskPolicy(max_order_notional=1_000, max_symbol_weight=1.0),
        marks={"QQQ": 100},
        dry_run=True,
    )

    assert first[0].status == "accepted"
    assert second[0].status == "duplicate"
    assert store.has_intent(intent.client_order_id)


def test_fake_broker_timeout_latches_halt(tmp_path) -> None:
    store = JsonlOrderStore(tmp_path / "orders.jsonl")
    halt = HaltStateStore(tmp_path / "halt.json")
    broker = FakeBrokerAdapter(
        account=AccountSnapshot("test", buying_power=10_000, cash=10_000, equity=10_000),
        mode="timeout",
    )

    result = process_order_intents(
        [_intent().normalized()],
        broker=broker,
        store=store,
        halt_store=halt,
        policy=RiskPolicy(max_order_notional=1_000, max_symbol_weight=1.0),
        marks={"QQQ": 100},
        dry_run=False,
    )

    assert result[0].status == "uncertain"
    assert halt.current().halted


def test_runner_enforces_daily_order_count_from_order_log(tmp_path) -> None:
    store = JsonlOrderStore(tmp_path / "orders.jsonl")
    halt = HaltStateStore(tmp_path / "halt.json")
    broker = FakeBrokerAdapter(
        account=AccountSnapshot("test", buying_power=10_000, cash=10_000, equity=10_000)
    )

    first = process_order_intents(
        [_intent(rebalance_key="first").normalized()],
        broker=broker,
        store=store,
        halt_store=halt,
        policy=RiskPolicy(max_order_notional=1_000, max_symbol_weight=1.0, max_orders_per_day=1),
        marks={"QQQ": 100},
        dry_run=True,
    )
    second = process_order_intents(
        [_intent(rebalance_key="second").normalized()],
        broker=broker,
        store=store,
        halt_store=halt,
        policy=RiskPolicy(max_order_notional=1_000, max_symbol_weight=1.0, max_orders_per_day=1),
        marks={"QQQ": 100},
        dry_run=True,
    )

    assert first[0].status == "accepted"
    assert second[0].status == "risk_block"
    assert any("daily order count" in reason for reason in second[0].reasons)


def test_reconciler_reports_position_mismatch() -> None:
    issues = reconcile_positions(
        {("QQQ", "us"): 2.0},
        [PositionSnapshot("QQQ", "us", qty=1.0, market_value=100)],
    )

    assert len(issues) == 1
    assert issues[0].symbol == "QQQ"


def _intent(qty: float = 2, rebalance_key: str = "2026-05-12") -> OrderIntent:
    return OrderIntent(
        strategy="approved-etf",
        symbol="qqq",
        market="us",
        side="buy",
        qty=qty,
        order_type="limit",
        limit_price=100,
        rebalance_key=rebalance_key,
        asof_ts=datetime(2026, 5, 12, tzinfo=UTC),
    )
