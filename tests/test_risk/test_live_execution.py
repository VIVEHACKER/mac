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
        policy=RiskPolicy(
            max_order_notional=1_000, max_symbol_weight=1.0, max_limit_deviation=0.03
        ),
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


def test_batch_cumulative_pretrade_rejects_second_buy_that_jointly_exceeds_symbol_weight(
    tmp_path,
) -> None:
    """Regression: two individually-valid buys must not both pass when they jointly
    exceed max_symbol_weight.

    Setup:
      equity = 10_000, no existing positions
      max_symbol_weight = 0.30  (30 %)
      each buy: 25 QQQ @ $100 = $2 500 notional → single weight 25 % < 30 % (passes alone)
      together: $5 000 notional → weight 50 % >> 30 % (second must be blocked)
    """
    store = JsonlOrderStore(tmp_path / "orders.jsonl")
    halt = HaltStateStore(tmp_path / "halt.json")
    broker = FakeBrokerAdapter(
        account=AccountSnapshot("test", buying_power=10_000, cash=10_000, equity=10_000)
    )

    # Two distinct intents for the same symbol in a single batch.
    intent_a = OrderIntent(
        strategy="approved-etf",
        symbol="QQQ",
        market="us",
        side="buy",
        qty=25,
        order_type="limit",
        limit_price=100,
        rebalance_key="batch-a",
        asof_ts=datetime(2026, 5, 12, tzinfo=UTC),
    ).normalized()
    intent_b = OrderIntent(
        strategy="approved-etf",
        symbol="QQQ",
        market="us",
        side="buy",
        qty=25,
        order_type="limit",
        limit_price=100,
        rebalance_key="batch-b",
        asof_ts=datetime(2026, 5, 12, tzinfo=UTC),
    ).normalized()

    _policy = RiskPolicy(
        max_order_notional=5_000,
        max_daily_new_notional=10_000,
        max_symbol_weight=0.30,
        max_gross_exposure=2.0,
        min_cash_fraction=0.0,
        max_orders_per_day=20,
    )

    # Verify individually: each alone would pass symbol-weight check.
    for intent in (intent_a, intent_b):
        check = evaluate_pretrade_order(
            intent,
            policy=_policy,
            account=AccountSnapshot("test", buying_power=10_000, cash=10_000, equity=10_000),
            positions=[],
            marks={"QQQ": 100},
        )
        assert check.passed, f"Expected individual intent to pass, got: {check.reasons}"

    # Now submit BOTH in a single batch — second must be blocked cumulatively.
    results = process_order_intents(
        [intent_a, intent_b],
        broker=broker,
        store=store,
        halt_store=halt,
        policy=_policy,
        marks={"QQQ": 100},
        dry_run=True,
    )

    assert results[0].status == "accepted", f"First buy unexpectedly blocked: {results[0].reasons}"
    assert results[1].status == "risk_block", (
        "BUG: second buy should be blocked by cumulative symbol-weight check, "
        f"but got status={results[1].status!r}"
    )
    assert any("weight" in r for r in results[1].reasons), (
        f"Expected weight-related block reason, got: {results[1].reasons}"
    )


def test_risk_reducing_sell_not_blocked_by_daily_new_notional_cap() -> None:
    """P2 regression: a SELL that reduces an existing long position must NOT be
    counted against the daily new-notional cap.

    Setup:
      - existing long: 20 QQQ @ $100 (market_value = $2 000)
      - daily new-notional cap: $1 500
      - new_notional_today already at $1 400  (only $100 headroom left)
      - sell order: 10 QQQ @ $100 = $1 000 notional  (> $100 headroom)

    Before fix: pretrade rejects with "daily new notional limit would be exceeded"
    After fix : pretrade passes (sell reduces exposure, cap is irrelevant)
    """
    sell_intent = OrderIntent(
        strategy="approved-etf",
        symbol="QQQ",
        market="us",
        side="sell",
        qty=10,
        order_type="limit",
        limit_price=100,
        rebalance_key="2026-05-12-sell",
        asof_ts=datetime(2026, 5, 12, tzinfo=UTC),
    ).normalized()

    result = evaluate_pretrade_order(
        sell_intent,
        policy=RiskPolicy(
            max_order_notional=2_000,
            max_daily_new_notional=1_500,
            max_symbol_weight=1.0,
            max_gross_exposure=2.0,
            min_cash_fraction=0.0,
            allow_short=False,
        ),
        account=AccountSnapshot("test", buying_power=100_000, cash=100_000, equity=10_000),
        positions=[PositionSnapshot("QQQ", "us", qty=20, market_value=2_000)],
        marks={"QQQ": 100},
        new_notional_today=1_400.0,  # only $100 headroom — sell notional ($1 000) exceeds it
    )

    assert result.passed, (
        "Risk-reducing SELL must not be blocked by the daily new-notional cap. "
        f"Got reasons: {result.reasons}"
    )
    assert not any("daily new notional" in r for r in result.reasons)


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
