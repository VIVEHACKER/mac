from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

from alpaca.common.exceptions import APIError

from risk.halt_state import HaltStateStore
from risk.policy import RiskPolicy
from risk.pretrade import evaluate_pretrade_order
from trader.execution.adapters.alpaca import AlpacaBrokerAdapter
from trader.execution.adapters.fake import FakeBrokerAdapter
from trader.execution.broker import (
    AccountSnapshot,
    BrokerClock,
    BrokerOrder,
    BrokerTemporaryError,
    PositionSnapshot,
)
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
        reference_equity=10_000.0,
    )

    assert result[0].status == "uncertain"
    assert halt.current().halted


def test_market_closed_blocks_live_batch_without_recording_intent(tmp_path) -> None:
    store = JsonlOrderStore(tmp_path / "orders.jsonl")
    halt = HaltStateStore(tmp_path / "halt.json")
    intent = _intent().normalized()
    broker = FakeBrokerAdapter(
        account=AccountSnapshot("test", buying_power=10_000, cash=10_000, equity=10_000),
        clock=BrokerClock(
            is_open=False,
            timestamp=datetime(2026, 5, 12, 12, tzinfo=UTC),
            next_open=datetime(2026, 5, 12, 13, 30, tzinfo=UTC),
        ),
    )

    result = process_order_intents(
        [intent],
        broker=broker,
        store=store,
        halt_store=halt,
        policy=RiskPolicy(max_order_notional=1_000, max_symbol_weight=1.0),
        marks={"QQQ": 100},
        dry_run=False,
        reference_equity=10_000.0,
    )

    assert result[0].status == "risk_block"
    assert "market is closed" in result[0].reasons[0]
    assert not store.has_intent(intent.client_order_id)
    assert not halt.current().halted


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


def test_kill_switch_latches_and_blocks_batch_on_daily_drawdown(tmp_path) -> None:
    """Portfolio kill-switch (previously a DEAD function) must halt the whole batch when the
    account has dropped past the daily-loss latch, and persist the halt for later cycles."""
    store = JsonlOrderStore(tmp_path / "orders.jsonl")
    halt = HaltStateStore(tmp_path / "halt.json")
    # equity 9_600 vs reference 10_000 = -4% daily, past max_daily_loss (2%)
    broker = FakeBrokerAdapter(
        account=AccountSnapshot("test", buying_power=9_600, cash=9_600, equity=9_600)
    )

    results = process_order_intents(
        [_intent().normalized()],
        broker=broker,
        store=store,
        halt_store=halt,
        policy=RiskPolicy(max_order_notional=1_000, max_symbol_weight=1.0),
        marks={"QQQ": 100},
        dry_run=False,
        reference_equity=10_000.0,
    )

    assert results[0].status == "risk_block"
    assert any("daily drawdown" in r for r in results[0].reasons)
    assert halt.current().halted
    # not recorded as an intent -> retryable once the halt is cleared
    assert not store.has_intent(_intent().normalized().client_order_id)


def test_kill_switch_latches_on_peak_drawdown_even_when_daily_flat(tmp_path) -> None:
    """The daily 2% latch cannot catch a slow multi-day bleed; the peak-drawdown latch must."""
    store = JsonlOrderStore(tmp_path / "orders.jsonl")
    halt = HaltStateStore(tmp_path / "halt.json")
    # daily flat (reference == current) but -26% from the all-time peak (sleeve latch = 25%)
    broker = FakeBrokerAdapter(
        account=AccountSnapshot("test", buying_power=7_400, cash=7_400, equity=7_400)
    )

    results = process_order_intents(
        [_intent().normalized()],
        broker=broker,
        store=store,
        halt_store=halt,
        policy=RiskPolicy(max_order_notional=1_000, max_symbol_weight=1.0),
        marks={"QQQ": 100},
        dry_run=False,
        reference_equity=7_400.0,
        peak_equity=10_000.0,
    )

    assert results[0].status == "risk_block"
    assert any("peak" in r for r in results[0].reasons)
    assert halt.current().halted


def test_kill_switch_allows_orders_when_within_limits(tmp_path) -> None:
    """The kill-switch must not false-trip on a healthy account."""
    store = JsonlOrderStore(tmp_path / "orders.jsonl")
    halt = HaltStateStore(tmp_path / "halt.json")
    # -1% daily, -5% from peak -> both within limits
    broker = FakeBrokerAdapter(
        account=AccountSnapshot("test", buying_power=9_900, cash=9_900, equity=9_900)
    )

    results = process_order_intents(
        [_intent().normalized()],
        broker=broker,
        store=store,
        halt_store=halt,
        policy=RiskPolicy(max_order_notional=1_000, max_symbol_weight=1.0),
        marks={"QQQ": 100},
        dry_run=True,
        reference_equity=10_000.0,
        peak_equity=10_400.0,
    )

    assert results[0].status == "accepted"
    assert not halt.current().halted


def test_kill_switch_latch_blocks_next_batch(tmp_path) -> None:
    """Once latched, a subsequent batch (even without reference_equity) fails closed via the
    pre-trade halt check."""
    store = JsonlOrderStore(tmp_path / "orders.jsonl")
    halt = HaltStateStore(tmp_path / "halt.json")
    broker = FakeBrokerAdapter(
        account=AccountSnapshot("test", buying_power=9_600, cash=9_600, equity=9_600)
    )

    process_order_intents(
        [_intent(rebalance_key="a").normalized()],
        broker=broker,
        store=store,
        halt_store=halt,
        policy=RiskPolicy(max_order_notional=1_000, max_symbol_weight=1.0),
        marks={"QQQ": 100},
        dry_run=False,
        reference_equity=10_000.0,
    )
    second = process_order_intents(
        [_intent(rebalance_key="b").normalized()],
        broker=broker,
        store=store,
        halt_store=halt,
        policy=RiskPolicy(max_order_notional=1_000, max_symbol_weight=1.0),
        marks={"QQQ": 100},
        dry_run=True,
    )

    assert second[0].status == "risk_block"
    assert any("halted" in r for r in second[0].reasons)


def test_kill_switch_does_not_overwrite_existing_halt(tmp_path) -> None:
    """A drawdown breach must NOT clobber a pre-existing manual/broker halt — otherwise clearing
    the kill-switch would silently resume into the unresolved original blocker (Codex P2)."""
    store = JsonlOrderStore(tmp_path / "orders.jsonl")
    halt = HaltStateStore(tmp_path / "halt.json")
    halt.activate("broker position mismatch", source="manual")
    broker = FakeBrokerAdapter(
        account=AccountSnapshot("test", buying_power=9_600, cash=9_600, equity=9_600)
    )

    results = process_order_intents(
        [_intent().normalized()],
        broker=broker,
        store=store,
        halt_store=halt,
        policy=RiskPolicy(max_order_notional=1_000, max_symbol_weight=1.0),
        marks={"QQQ": 100},
        dry_run=False,
        reference_equity=10_000.0,  # would trip the kill-switch on its own
    )

    assert results[0].status == "risk_block"  # blocked; origin (manual) preserved in halt store
    assert halt.current().reason == "broker position mismatch"  # original preserved
    assert halt.current().source == "manual"


def test_kill_switch_paused_order_is_retryable_after_clear(tmp_path) -> None:
    """A kill-switch pause must keep the SAME client_order_id retryable across cycles and after the
    halt clears — it must never be recorded as an intent (which would make it a duplicate)."""
    store = JsonlOrderStore(tmp_path / "orders.jsonl")
    halt = HaltStateStore(tmp_path / "halt.json")
    intent = _intent().normalized()
    drawdown = FakeBrokerAdapter(
        account=AccountSnapshot("test", buying_power=9_600, cash=9_600, equity=9_600)
    )
    policy = RiskPolicy(max_order_notional=1_000, max_symbol_weight=1.0)

    # cycle 1: kill-switch trips, order blocked + not recorded
    process_order_intents(
        [intent],
        broker=drawdown,
        store=store,
        halt_store=halt,
        policy=policy,
        marks={"QQQ": 100},
        dry_run=False,
        reference_equity=10_000.0,
    )
    # cycle 2: same id re-emitted while still halted -> still blocked, still not recorded
    process_order_intents(
        [intent],
        broker=drawdown,
        store=store,
        halt_store=halt,
        policy=policy,
        marks={"QQQ": 100},
        dry_run=False,
        reference_equity=10_000.0,
    )
    assert not store.has_intent(intent.client_order_id)

    # operator clears the halt + equity recovers -> the SAME order is retryable, not "duplicate"
    halt.clear("recovered", source="manual")
    healthy = FakeBrokerAdapter(
        account=AccountSnapshot("test", buying_power=10_000, cash=10_000, equity=10_000)
    )
    result = process_order_intents(
        [intent],
        broker=healthy,
        store=store,
        halt_store=halt,
        policy=policy,
        marks={"QQQ": 100},
        dry_run=True,
        reference_equity=10_000.0,
        peak_equity=10_000.0,
    )
    assert result[0].status == "accepted"


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


# --- live broker safety contract through the REAL Alpaca adapter (audit: "verified 0 times") ---


def _api_error(status: int, message: str = "boom") -> APIError:
    http_error = SimpleNamespace(response=SimpleNamespace(status_code=status))
    return APIError(json.dumps({"code": status * 100, "message": message}), http_error)


class _ScriptedAlpacaClient:
    """Minimal alpaca-py TradingClient stand-in: reads succeed, submit raises ``submit_exc``."""

    def __init__(self, submit_exc: BaseException):
        self._submit_exc = submit_exc

    def get_account(self) -> SimpleNamespace:
        return SimpleNamespace(
            id="acc",
            buying_power=10_000,
            cash=10_000,
            equity=10_000,
            trading_blocked=False,
            account_blocked=False,
            pattern_day_trader=False,
            daytrade_count=0,
            currency="USD",
            last_equity=10_000,
        )

    def get_all_positions(self) -> list[object]:
        return []

    def get_clock(self) -> SimpleNamespace:
        return SimpleNamespace(
            is_open=True,
            timestamp=datetime(2026, 5, 12, 14, 30, tzinfo=UTC),
            next_open=datetime(2026, 5, 13, 13, 30, tzinfo=UTC),
            next_close=datetime(2026, 5, 12, 20, tzinfo=UTC),
        )

    def submit_order(self, request: object) -> object:
        raise self._submit_exc

    def get_order_by_client_id(self, client_order_id: str) -> object:
        return None


class _FailingReadBroker:
    """A broker whose account read fails (network down) — must fail closed, never submit."""

    def get_account(self) -> AccountSnapshot:
        raise BrokerTemporaryError("network down reading account")

    def list_positions(self) -> list[PositionSnapshot]:
        return []

    def get_clock(self) -> BrokerClock:
        return BrokerClock(is_open=True, timestamp=datetime(2026, 5, 12, tzinfo=UTC))

    def submit_order(self, intent: OrderIntent) -> BrokerOrder:
        raise AssertionError("submit_order must not be reached when reads fail")

    def get_order(self, client_order_id: str) -> BrokerOrder | None:
        return None

    def cancel_order(self, client_order_id: str) -> BrokerOrder | None:
        raise AssertionError("cancel_order must not be reached when reads fail")


def test_alpaca_5xx_submit_latches_halt_through_runner(tmp_path) -> None:
    store = JsonlOrderStore(tmp_path / "orders.jsonl")
    halt = HaltStateStore(tmp_path / "halt.json")
    broker = AlpacaBrokerAdapter(
        client=_ScriptedAlpacaClient(_api_error(503)), sleep=lambda _seconds: None
    )

    result = process_order_intents(
        [_intent().normalized()],
        broker=broker,
        store=store,
        halt_store=halt,
        policy=RiskPolicy(max_order_notional=1_000, max_symbol_weight=1.0),
        marks={"QQQ": 100},
        dry_run=False,
        reference_equity=10_000.0,
    )

    assert result[0].status == "uncertain"
    assert halt.current().halted  # the live halt latch fires on a REAL alpaca 5xx


def test_alpaca_4xx_submit_records_reject_without_halt(tmp_path) -> None:
    store = JsonlOrderStore(tmp_path / "orders.jsonl")
    halt = HaltStateStore(tmp_path / "halt.json")
    broker = AlpacaBrokerAdapter(
        client=_ScriptedAlpacaClient(_api_error(403, "insufficient buying power")),
        sleep=lambda _seconds: None,
    )

    result = process_order_intents(
        [_intent().normalized()],
        broker=broker,
        store=store,
        halt_store=halt,
        policy=RiskPolicy(max_order_notional=1_000, max_symbol_weight=1.0),
        marks={"QQQ": 100},
        dry_run=False,
        reference_equity=10_000.0,
    )

    assert result[0].status == "rejected"
    assert not halt.current().halted  # a definite rejection is not an uncertain state


def test_broker_read_failure_blocks_batch_and_latches_halt(tmp_path) -> None:
    store = JsonlOrderStore(tmp_path / "orders.jsonl")
    halt = HaltStateStore(tmp_path / "halt.json")

    result = process_order_intents(
        [_intent().normalized()],
        broker=_FailingReadBroker(),
        store=store,
        halt_store=halt,
        policy=RiskPolicy(max_order_notional=1_000, max_symbol_weight=1.0),
        marks={"QQQ": 100},
        dry_run=False,
        reference_equity=10_000.0,
    )

    assert result[0].status == "risk_block"
    assert any("unavailable" in reason for reason in result[0].reasons)
    assert halt.current().halted


def test_broker_read_failure_does_not_overwrite_existing_halt(tmp_path) -> None:
    """A transient broker-read failure must NOT clobber a pre-existing manual halt —
    else clearing the read-failure halt would silently resume past the original
    unresolved blocker (Codex P2)."""
    store = JsonlOrderStore(tmp_path / "orders.jsonl")
    halt = HaltStateStore(tmp_path / "halt.json")
    halt.activate("broker position mismatch", source="manual")

    result = process_order_intents(
        [_intent().normalized()],
        broker=_FailingReadBroker(),
        store=store,
        halt_store=halt,
        policy=RiskPolicy(max_order_notional=1_000, max_symbol_weight=1.0),
        marks={"QQQ": 100},
        dry_run=False,
        reference_equity=10_000.0,
    )

    assert result[0].status == "risk_block"
    assert halt.current().reason == "broker position mismatch"  # original preserved
    assert halt.current().source == "manual"


def test_uncertain_submit_halts_rest_of_batch(tmp_path) -> None:
    # Codex P1: an uncertain (temporary) submit latches a halt, and EVERY remaining intent in
    # the batch must be blocked rather than submitted into the uncertain broker state.
    store = JsonlOrderStore(tmp_path / "orders.jsonl")
    halt = HaltStateStore(tmp_path / "halt.json")
    broker = FakeBrokerAdapter(
        account=AccountSnapshot("test", buying_power=10_000, cash=10_000, equity=10_000),
        mode="timeout",
    )

    results = process_order_intents(
        [_intent(rebalance_key="first").normalized(), _intent(rebalance_key="second").normalized()],
        broker=broker,
        store=store,
        halt_store=halt,
        policy=RiskPolicy(max_order_notional=1_000, max_symbol_weight=1.0),
        marks={"QQQ": 100},
        dry_run=False,
        reference_equity=10_000.0,
    )

    assert results[0].status == "uncertain"
    # Blocked by the latched halt (status risk_block), NOT submitted (which would be "uncertain").
    assert results[1].status == "risk_block"
    assert any("halted" in reason for reason in results[1].reasons)
    assert halt.current().halted


def test_rejected_submit_does_not_project_exposure(tmp_path) -> None:
    # Codex P2: a rejected order must NOT project onto the book. If A's rejection wrongly
    # projected its 4,000 notional, B's projected cash (6,000 -> 2,000) would breach the 50%
    # reserve and B would be risk_block; with the fix the book is unmoved and B reaches the broker.
    store = JsonlOrderStore(tmp_path / "orders.jsonl")
    halt = HaltStateStore(tmp_path / "halt.json")
    broker = FakeBrokerAdapter(
        account=AccountSnapshot("test", buying_power=10_000, cash=10_000, equity=10_000),
        mode="reject",
    )

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
        broker=broker,
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
    )

    assert [r.status for r in results] == ["rejected", "rejected"]
    assert not halt.current().halted
