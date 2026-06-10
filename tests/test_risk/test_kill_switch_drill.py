"""Kill-switch / halt-latch drill — exercised end-to-end through the real execution
runner against PaperBroker (a drop-in BrokerAdapter stand-in for the live Alpaca
adapter). Closes the "kill-switch never exercised against a broker" gap: the survival
logic (daily-loss latch, peak-drawdown backstop, sticky halt) is broker-agnostic, so a
faithful stand-in validates it fully. The live adapter swap changes only the network/
fill layer, not this path. Runs in CI, so the drill can never silently regress.
"""

from __future__ import annotations

from engine.paper import PaperBroker
from risk.halt_state import HaltStateStore
from risk.policy import RiskPolicy
from trader.execution.intents import OrderIntent
from trader.execution.order_store import JsonlOrderStore
from trader.execution.runner import process_order_intents


def _intent(qty: float, symbol: str = "AAA", side: str = "buy") -> OrderIntent:
    return OrderIntent(
        strategy="ideal", symbol=symbol, market="us", side=side, qty=qty
    ).normalized()


def test_daily_loss_latches_halt_and_blocks_batch(tmp_path) -> None:
    marks = {"AAA": 100.0}
    broker = PaperBroker(100_000.0, marks=marks)
    broker.submit_order(_intent(500))  # 500 @ 100 → cash 50k, equity 100k
    reference_equity = broker.get_account().equity
    assert reference_equity == 100_000.0

    # Intraday -6% on the holding → portfolio equity 97k, a 3% daily loss (> 2% cap).
    marks["AAA"] = 94.0
    broker.set_marks(marks)
    assert broker.get_account().equity == 97_000.0

    halt_store = HaltStateStore(tmp_path / "halt.json")
    results = process_order_intents(
        [_intent(10)],
        broker=broker,
        store=JsonlOrderStore(tmp_path / "orders.jsonl"),
        halt_store=halt_store,
        policy=RiskPolicy(max_daily_loss=0.02),
        marks=marks,
        dry_run=False,
        reference_equity=reference_equity,
        peak_equity=reference_equity,
    )

    assert halt_store.current().halted
    assert "kill-switch" in halt_store.current().reason
    assert [r.status for r in results] == ["risk_block"]
    # The order never reached the broker — holding is unchanged.
    assert broker.positions[("AAA", "us")].qty == 500.0


def test_peak_drawdown_backstop_latches_on_slow_bleed(tmp_path) -> None:
    # Small daily loss (1.3% < 2%) but 26% below the peak (> 25% backstop).
    broker = PaperBroker(74_000.0, marks={"AAA": 100.0})
    halt_store = HaltStateStore(tmp_path / "halt.json")
    results = process_order_intents(
        [_intent(1)],
        broker=broker,
        store=JsonlOrderStore(tmp_path / "orders.jsonl"),
        halt_store=halt_store,
        policy=RiskPolicy(max_daily_loss=0.02, max_drawdown_from_peak=0.25),
        marks={"AAA": 100.0},
        dry_run=False,
        reference_equity=75_000.0,
        peak_equity=100_000.0,
    )
    assert halt_store.current().halted
    assert [r.status for r in results] == ["risk_block"]


def test_halt_latch_is_sticky_and_does_not_auto_resume(tmp_path) -> None:
    marks = {"AAA": 100.0}
    broker = PaperBroker(100_000.0, marks=marks)
    broker.submit_order(_intent(500))
    store = JsonlOrderStore(tmp_path / "orders.jsonl")
    halt_store = HaltStateStore(tmp_path / "halt.json")

    marks["AAA"] = 94.0
    broker.set_marks(marks)
    process_order_intents(
        [_intent(10)],
        broker=broker,
        store=store,
        halt_store=halt_store,
        policy=RiskPolicy(max_daily_loss=0.02),
        marks=marks,
        dry_run=False,
        reference_equity=100_000.0,
        peak_equity=100_000.0,
    )
    assert halt_store.current().halted

    # Market fully recovers, but the latch must hold — a halt is a deliberate pause,
    # not an auto-resetting threshold. A second cycle stays blocked until cleared.
    marks["AAA"] = 100.0
    broker.set_marks(marks)
    results = process_order_intents(
        [_intent(10)],
        broker=broker,
        store=store,
        halt_store=halt_store,
        policy=RiskPolicy(max_daily_loss=0.02),
        marks=marks,
        dry_run=False,
        reference_equity=100_000.0,
        peak_equity=100_000.0,
    )
    assert [r.status for r in results] == ["risk_block"]
    assert broker.positions[("AAA", "us")].qty == 500.0
