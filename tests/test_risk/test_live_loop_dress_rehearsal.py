"""Live-loop dress rehearsal — the full deploy-candidate execution chain composed
end-to-end against PaperBroker (a BrokerAdapter stand-in for live Alpaca), with NO
API keys:

    risk-aware sizing  →  RebalancePlan  →  gated process_order_intents()  →  fills
                       →  reconcile broker positions vs intended end-state

Everything except the literal broker network/fill call is exercised. The kill-switch
arm of the same path is drilled in test_kill_switch_drill.py.
"""

from __future__ import annotations

import pytest

from engine.paper import PaperBroker
from risk.halt_state import HaltStateStore
from risk.policy import RiskPolicy
from trader.execution.adapters.fake import FakeBrokerAdapter
from trader.execution.broker import AccountSnapshot, PositionSnapshot
from trader.execution.order_store import JsonlOrderStore
from trader.execution.rebalance import plan_rebalance, sized_targets
from trader.execution.reconciler import reconcile_positions
from trader.execution.runner import process_order_intents

AUM = 1_000_000.0
MARKS = {"AAA": 100.0, "BBB": 200.0, "CCC": 50.0}
VOLS = {"AAA": 0.35, "BBB": 0.35, "CCC": 0.35}


def _policy() -> RiskPolicy:
    return RiskPolicy(
        max_order_notional=1_000_000,
        max_daily_new_notional=1_000_000,
        max_symbol_weight=0.10,
        max_gross_exposure=2.0,
        min_cash_fraction=0.0,
    )


def test_full_rebalance_cycle_executes_and_reconciles(tmp_path) -> None:
    # 1) risk-aware sizing (vol-target / risk-cap / hard-cap — no edge → no Kelly)
    targets = sized_targets(
        ["AAA", "BBB", "CCC"], aum=AUM, marks=MARKS, vols=VOLS, max_position_pct=0.08
    )
    assert targets, "sizing produced no targets"

    # 2) target → orders (from a flat book)
    plan = plan_rebalance(
        strategy="ideal",
        rebalance_key="2026-06-10",
        targets=targets,
        current_qty={},
        marks=MARKS,
    )

    # 3) gated execution against the PaperBroker (same runner the live adapter uses)
    broker = PaperBroker(AUM, marks=MARKS)
    results = process_order_intents(
        list(plan.intents),
        broker=broker,
        store=JsonlOrderStore(tmp_path / "orders.jsonl"),
        halt_store=HaltStateStore(tmp_path / "halt.json"),
        policy=_policy(),
        marks=MARKS,
        dry_run=False,
        reference_equity=AUM,  # arms the kill-switch — required for any real submission
        peak_equity=AUM,
    )
    assert results, "no intents executed"
    assert all(r.status == "filled" for r in results), [r.status for r in results]

    # 4) the broker's book matches the intended end-state exactly — no reconciliation breaks
    expected = {(t.symbol, t.market): t.target_qty for t in targets}
    issues = reconcile_positions(expected, broker.list_positions())
    assert issues == [], [i.message for i in issues]


def test_reconcile_detects_position_drift() -> None:
    broker = PaperBroker(AUM, marks=MARKS)
    broker.submit_order(
        plan_rebalance(
            strategy="ideal",
            rebalance_key="k",
            targets=sized_targets(["AAA"], aum=AUM, marks=MARKS, vols=VOLS),
            current_qty={},
            marks=MARKS,
        ).intents[0]
    )
    held = {p.symbol: p.qty for p in broker.list_positions()}["AAA"]

    # Intend a different book than the broker holds → reconciliation must flag the gap.
    expected = {("AAA", "us"): held + 5, ("ZZZ", "us"): 10.0}
    issues = reconcile_positions(expected, broker.list_positions())
    flagged = {i.symbol for i in issues}
    assert flagged == {"AAA", "ZZZ"}


def test_unarmed_live_submission_is_refused(tmp_path) -> None:
    # Adversarial-review finding: reference_equity=None used to silently DISARM the
    # kill-switch. The contract is now fail-closed — a real submission without the
    # arming equity must refuse loudly, never run unprotected.
    broker = PaperBroker(AUM, marks=MARKS)
    intents = list(
        plan_rebalance(
            strategy="ideal",
            rebalance_key="k",
            targets=sized_targets(["AAA"], aum=AUM, marks=MARKS, vols=VOLS),
            current_qty={},
            marks=MARKS,
        ).intents
    )
    with pytest.raises(ValueError, match="kill-switch"):
        process_order_intents(
            intents,
            broker=broker,
            store=JsonlOrderStore(tmp_path / "orders.jsonl"),
            halt_store=HaltStateStore(tmp_path / "halt.json"),
            policy=_policy(),
            marks=MARKS,
            dry_run=False,  # real submission…
            # …but reference_equity intentionally omitted
        )


def _fake_run(tmp_path, mode: str):
    """Run the sized rebalance plan through the runner against a FakeBroker in ``mode``."""
    targets = sized_targets(
        ["AAA", "BBB", "CCC"], aum=AUM, marks=MARKS, vols=VOLS, max_position_pct=0.08
    )
    intents = list(
        plan_rebalance(
            strategy="ideal",
            rebalance_key="2026-06-10",
            targets=targets,
            current_qty={},
            marks=MARKS,
        ).intents
    )
    broker = FakeBrokerAdapter(
        account=AccountSnapshot("fake", buying_power=AUM, cash=AUM, equity=AUM),
        mode=mode,
        fill_ratio=0.5,
    )
    results = process_order_intents(
        intents,
        broker=broker,
        store=JsonlOrderStore(tmp_path / "orders.jsonl"),
        halt_store=HaltStateStore(tmp_path / "halt.json"),
        policy=_policy(),
        marks=MARKS,
        dry_run=False,
        reference_equity=AUM,
        peak_equity=AUM,
    )
    return targets, intents, broker, results


def test_partial_fills_leave_detectable_drift(tmp_path) -> None:
    # Adversarial-review finding: partial fills flowed through as success with no test
    # proving the drift is catchable. Contract: reconcile against the broker's REAL
    # fills must flag every shortfall.
    targets, intents, broker, results = _fake_run(tmp_path, mode="partial")
    assert all(r.status == "partially_filled" for r in results)

    actual = [
        PositionSnapshot(
            symbol=order.symbol,
            market=order.market,
            qty=order.filled_qty,
            market_value=order.filled_qty * MARKS[order.symbol],
        )
        for order in (broker.get_order(i.client_order_id) for i in intents)
        if order is not None
    ]
    expected = {(t.symbol, t.market): t.target_qty for t in targets}
    issues = reconcile_positions(expected, actual)

    assert {i.symbol for i in issues} == {t.symbol for t in targets}  # every shortfall flagged
    for issue in issues:
        assert issue.actual_qty == pytest.approx(issue.expected_qty * 0.5)


def test_rejected_orders_leave_book_unchanged_and_drift_visible(tmp_path) -> None:
    targets, _intents, broker, results = _fake_run(tmp_path, mode="reject")
    assert all(r.status == "rejected" for r in results)
    assert broker.list_positions() == []  # nothing filled — book untouched

    # The failure is visible, not silent: reconcile flags the full intended book missing.
    expected = {(t.symbol, t.market): t.target_qty for t in targets}
    issues = reconcile_positions(expected, broker.list_positions())
    assert {i.symbol for i in issues} == {t.symbol for t in targets}
