from __future__ import annotations

import pytest

from engine.paper import PaperBroker
from risk.halt_state import HaltStateStore
from risk.policy import RiskPolicy
from trader.execution.adapters.fake import FakeBrokerAdapter
from trader.execution.broker import AccountSnapshot, BrokerRejectedError
from trader.execution.intents import OrderIntent
from trader.execution.order_store import JsonlOrderStore
from trader.execution.rebalance import plan_rebalance, targets_from_weights
from trader.execution.runner import process_order_intents


def _permissive_policy() -> RiskPolicy:
    return RiskPolicy(
        max_order_notional=1_000_000,
        max_daily_new_notional=1_000_000,
        max_symbol_weight=1.0,
        max_gross_exposure=2.0,
        min_cash_fraction=0.0,
    )


def test_paper_broker_conforms_to_broker_adapter() -> None:
    broker = PaperBroker(100_000.0, marks={"AAA": 100.0})
    assert broker.get_account().equity == 100_000.0

    intent = OrderIntent(strategy="t", symbol="AAA", market="us", side="buy", qty=10).normalized()
    order = broker.submit_order(intent)

    assert order.status == "filled"
    assert order.filled_qty == 10
    assert order.filled_avg_price == 100.0
    assert broker.get_order(intent.client_order_id) is order
    assert {p.symbol: p.qty for p in broker.list_positions()} == {"AAA": 10.0}


def test_paper_broker_rejects_unpriced_symbol() -> None:
    broker = PaperBroker(100_000.0)  # no marks supplied
    intent = OrderIntent(strategy="t", symbol="AAA", market="us", side="buy", qty=1).normalized()
    with pytest.raises(BrokerRejectedError):
        broker.submit_order(intent)


def test_paper_and_fake_broker_share_the_same_runner(tmp_path) -> None:
    # SAME RebalancePlan + SAME process_order_intents — only the broker differs
    # (PaperBroker stands in for the live Alpaca adapter). This is the "paper = live
    # = same decision/execution code" property.
    marks = {"AAA": 100.0, "BBB": 200.0}
    targets = targets_from_weights({"AAA": 0.5, "BBB": 0.5}, marks, 100_000.0)
    intents = list(
        plan_rebalance(
            strategy="ideal",
            rebalance_key="2026-06-09",
            targets=targets,
            current_qty={},
            marks=marks,
        ).intents
    )

    paper = PaperBroker(100_000.0, marks=marks)
    fake = FakeBrokerAdapter(
        account=AccountSnapshot("fake", buying_power=100_000, cash=100_000, equity=100_000)
    )

    def run(broker, tag):
        return process_order_intents(
            intents,
            broker=broker,
            store=JsonlOrderStore(tmp_path / f"{tag}.jsonl"),
            halt_store=HaltStateStore(tmp_path / f"{tag}.json"),
            policy=_permissive_policy(),
            marks=marks,
            dry_run=False,
            reference_equity=100_000.0,
            peak_equity=100_000.0,
        )

    paper_results = run(paper, "paper")
    fake_results = run(fake, "fake")

    # Same intents flow through the same gate to the same decisions.
    assert [r.action for r in paper_results] == [r.action for r in fake_results]
    assert all(r.status == "filled" for r in paper_results)
    # And the paper broker actually applied the fills — live-equivalent state change.
    assert {p.symbol: p.qty for p in paper.list_positions()} == {"AAA": 500.0, "BBB": 250.0}
