from __future__ import annotations

import math

import pytest

from engine.paper import PaperBroker
from risk.slippage import SlippageModel
from trader.execution.intents import OrderIntent


def test_slippage_bps_combines_spread_and_sqrt_impact() -> None:
    model = SlippageModel(half_spread_bps=1.0, impact_coefficient_bps=10.0)
    # participation 100/10_000 = 0.01 -> impact 10*sqrt(0.01)=1.0 -> 1.0 + 1.0 = 2.0 bps
    assert model.slippage_bps(qty=100, adv=10_000) == pytest.approx(2.0)


def test_slippage_grows_with_size() -> None:
    model = SlippageModel()
    small = model.slippage_bps(qty=100, adv=1_000_000)
    large = model.slippage_bps(qty=100_000, adv=1_000_000)
    assert large > small


def test_unknown_adv_charges_full_impact() -> None:
    model = SlippageModel(half_spread_bps=1.0, impact_coefficient_bps=10.0)
    assert model.slippage_bps(qty=100, adv=None) == pytest.approx(11.0)
    assert model.slippage_bps(qty=100, adv=0) == pytest.approx(11.0)


def test_slippage_is_capped() -> None:
    model = SlippageModel(impact_coefficient_bps=10_000.0, max_slippage_bps=100.0)
    assert model.slippage_bps(qty=1_000_000, adv=1) == 100.0


def test_zero_qty_has_no_slippage() -> None:
    assert SlippageModel().slippage_bps(qty=0, adv=1_000) == 0.0


def test_fill_price_buy_pays_up_sell_receives_down() -> None:
    model = SlippageModel(half_spread_bps=1.0, impact_coefficient_bps=10.0)
    buy = model.fill_price(side="buy", reference_price=100.0, qty=100, adv=10_000)
    sell = model.fill_price(side="sell", reference_price=100.0, qty=100, adv=10_000)
    assert buy == pytest.approx(100.0 * (1 + 2.0 / 10_000))
    assert sell == pytest.approx(100.0 * (1 - 2.0 / 10_000))
    assert math.isclose((buy - 100.0), (100.0 - sell))  # symmetric


def test_fill_price_rejects_bad_inputs() -> None:
    model = SlippageModel()
    with pytest.raises(ValueError, match="reference_price"):
        model.fill_price(side="buy", reference_price=0.0, qty=1, adv=1)
    with pytest.raises(ValueError, match="side"):
        model.fill_price(side="hold", reference_price=100.0, qty=1, adv=1)


def test_paper_broker_applies_slippage_to_fill() -> None:
    model = SlippageModel(half_spread_bps=1.0, impact_coefficient_bps=10.0)
    broker = PaperBroker(1_000_000.0, marks={"AAA": 100.0}, slippage=model, adv={"AAA": 10_000})
    intent = OrderIntent(strategy="t", symbol="AAA", market="us", side="buy", qty=100).normalized()
    order = broker.submit_order(intent)
    # 2.0 bps above the 100.0 mark.
    assert order.filled_avg_price == pytest.approx(100.02)
    assert broker.positions[("AAA", "us")].avg_cost == pytest.approx(100.02)


def test_paper_broker_default_has_no_slippage() -> None:
    broker = PaperBroker(1_000_000.0, marks={"AAA": 100.0})  # no slippage model
    intent = OrderIntent(strategy="t", symbol="AAA", market="us", side="buy", qty=100).normalized()
    order = broker.submit_order(intent)
    assert order.filled_avg_price == pytest.approx(100.0)
