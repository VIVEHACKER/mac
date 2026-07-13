from __future__ import annotations

from trader.execution.intents import RebalancePlan, TargetPosition
from trader.execution.rebalance import plan_rebalance, targets_from_weights


def test_targets_from_weights_whole_shares() -> None:
    targets = targets_from_weights({"AAA": 0.5, "BBB": 0.5}, {"AAA": 100.0, "BBB": 200.0}, 10_000.0)
    assert {t.symbol: t.target_qty for t in targets} == {"AAA": 50.0, "BBB": 25.0}


def test_targets_from_weights_fractional_preserves_expensive_name() -> None:
    targets = targets_from_weights(
        {"EXPENSIVE": 0.2},
        {"EXPENSIVE": 5_000.0},
        10_000.0,
        fractional_decimals=6,
    )
    assert targets[0].target_qty == 0.4


def test_targets_from_weights_rejects_invalid_fractional_precision() -> None:
    import pytest

    with pytest.raises(ValueError, match="between 0 and 9"):
        targets_from_weights(
            {"AAA": 0.5},
            {"AAA": 100.0},
            10_000.0,
            fractional_decimals=10,
        )


def test_targets_skip_unpriced_symbol() -> None:
    targets = targets_from_weights({"AAA": 0.5, "ZZZ": 0.5}, {"AAA": 100.0}, 10_000.0)
    assert {t.symbol for t in targets} == {"AAA"}


def test_plan_generates_only_nonzero_deltas() -> None:
    plan = plan_rebalance(
        strategy="ideal",
        rebalance_key="2026-06-09",
        targets=[TargetPosition("AAA", "us", 50.0), TargetPosition("BBB", "us", 25.0)],
        current_qty={"AAA": 50.0, "BBB": 0.0, "CCC": 10.0},
        marks={"AAA": 100.0, "BBB": 200.0, "CCC": 50.0},
    )
    assert isinstance(plan, RebalancePlan)
    # AAA unchanged (delta 0) → no intent; BBB buy 25; CCC fully exited (sell 10).
    assert {(i.symbol, i.side): i.qty for i in plan.intents} == {
        ("BBB", "buy"): 25.0,
        ("CCC", "sell"): 10.0,
    }


def test_plan_orders_sells_before_buys() -> None:
    plan = plan_rebalance(
        strategy="ideal",
        rebalance_key="k",
        targets=[TargetPosition("AAA", "us", 10.0)],
        current_qty={"BBB": 5.0},
        marks={"AAA": 100.0, "BBB": 100.0},
    )
    assert [i.side for i in plan.intents] == ["sell", "buy"]


def test_plan_drops_dust_below_min_notional() -> None:
    plan = plan_rebalance(
        strategy="ideal",
        rebalance_key="k",
        targets=[TargetPosition("AAA", "us", 1.0)],
        current_qty={},
        marks={"AAA": 5.0},
        min_notional=50.0,
    )
    assert plan.intents == ()


def test_exit_preserves_the_holding_market() -> None:
    # Latent finding: exits used to hardcode market="us", so a crypto holding would be
    # "sold" as ("BTC", "us") — a naked short to the pretrade gate, while the real
    # position stranded. Exits must fire in the market the position is held in.
    plan = plan_rebalance(
        strategy="ideal",
        rebalance_key="k",
        targets=[TargetPosition("AAA", "us", 10.0)],
        current_qty={"BTC": 2.0},
        marks={"AAA": 100.0, "BTC": 50_000.0},
        current_markets={"BTC": "crypto"},
    )
    exits = [i for i in plan.intents if i.side == "sell"]
    assert [(i.symbol, i.market) for i in exits] == [("BTC", "crypto")]


def test_plan_intents_are_normalized_with_stable_ids() -> None:
    plan = plan_rebalance(
        strategy="ideal",
        rebalance_key="k",
        targets=[TargetPosition("AAA", "us", 3.0)],
        current_qty={},
        marks={"AAA": 100.0},
    )
    intent = plan.intents[0]
    assert intent.client_order_id  # normalized() populated it
    assert intent.rebalance_key == "k"
