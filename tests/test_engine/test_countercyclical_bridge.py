from __future__ import annotations

import pytest

from engine.core_basket import CoreBasket, CoreHolding
from engine.countercyclical_bridge import (
    DEFAULT_LADDER,
    bridge_sleeve_target,
    compute_deployment,
    default_value_gate,
    format_deployment,
    ladder_fraction,
    market_drawdown,
)
from engine.fund_book import SleeveTarget, assemble_fund_book

# --------------------------------------------------------------------------- #
# market_drawdown
# --------------------------------------------------------------------------- #


def test_flat_series_has_zero_drawdown():
    assert market_drawdown([100.0, 100.0, 100.0]) == pytest.approx(0.0)


def test_monotone_up_series_has_zero_drawdown():
    assert market_drawdown([90.0, 95.0, 100.0]) == pytest.approx(0.0)


def test_off_peak_drawdown_is_peak_to_last():
    assert market_drawdown([80.0, 100.0, 75.0]) == pytest.approx(0.25)


def test_deep_but_valid_crash_stays_below_one():
    # with positive prices dd = (peak-last)/peak < 1 always; the [0,1] clamp upper bound is defensive
    assert market_drawdown([100.0, 1.0]) == pytest.approx(0.99)


def test_empty_series_raises():
    with pytest.raises(ValueError):
        market_drawdown([])


def test_non_positive_peak_raises():
    with pytest.raises(ValueError):
        market_drawdown([0.0, 0.0])


def test_nan_in_prices_raises():
    # fail-closed: NaN must not silently mask as drawdown=0 (review CRITICAL/HIGH PIT-safety finding)
    with pytest.raises(ValueError):
        market_drawdown([100.0, float("nan"), 95.0])


def test_inf_in_prices_raises():
    with pytest.raises(ValueError):
        market_drawdown([100.0, float("inf"), 95.0])


def test_negative_price_raises():
    # a negative last would give dd = (peak - neg)/peak > 1 -> clamp to 1 -> fail-OPEN full deploy; guard it
    with pytest.raises(ValueError):
        market_drawdown([100.0, -5.0])


def test_zero_interior_price_raises():
    with pytest.raises(ValueError):
        market_drawdown([100.0, 0.0, 95.0])


# --------------------------------------------------------------------------- #
# ladder / compute_deployment
# --------------------------------------------------------------------------- #


def test_below_first_threshold_deploys_zero():
    d = compute_deployment(0.05, True, budget=0.15)
    assert d.deployed_fraction == pytest.approx(0.0)
    assert d.tranche_index == 0


@pytest.mark.parametrize(
    "dd,expected_frac_of_budget,rung",
    [
        (0.099, 0.0, 0),
        (0.10, 1.0 / 3.0, 1),
        (0.199, 1.0 / 3.0, 1),
        (0.20, 2.0 / 3.0, 2),
        (0.299, 2.0 / 3.0, 2),
        (0.30, 1.0, 3),
        (0.50, 1.0, 3),
    ],
)
def test_ladder_rung_boundaries(dd, expected_frac_of_budget, rung):
    d = compute_deployment(dd, True, budget=0.15)
    assert d.deployed_fraction == pytest.approx(0.15 * expected_frac_of_budget)
    assert d.tranche_index == rung


def test_gate_closed_deploys_zero_at_every_drawdown():
    for dd in (0.0, 0.15, 0.25, 0.35, 0.60):
        d = compute_deployment(dd, False, budget=0.15)
        assert d.deployed_fraction == pytest.approx(0.0)
        assert d.tranche_index == 0
        assert d.value_gate_open is False


def test_deployed_never_exceeds_budget_over_sweep():
    for i in range(61):
        dd = i / 100.0
        d = compute_deployment(dd, True, budget=0.15)
        assert 0.0 <= d.deployed_fraction <= 0.15 + 1e-12


def test_drawdown_clamped_above_one():
    d = compute_deployment(1.5, True, budget=0.15)
    assert d.drawdown == pytest.approx(1.0)
    assert d.deployed_fraction == pytest.approx(0.15)


def test_budget_out_of_range_raises():
    with pytest.raises(ValueError):
        compute_deployment(0.25, True, budget=1.5)
    with pytest.raises(ValueError):
        compute_deployment(0.25, True, budget=-0.1)


def test_non_ascending_ladder_thresholds_raise():
    with pytest.raises(ValueError):
        compute_deployment(0.25, True, budget=0.15, ladder=((0.20, 0.5), (0.10, 1.0)))


def test_non_monotone_cumulative_raises():
    with pytest.raises(ValueError):
        compute_deployment(0.25, True, budget=0.15, ladder=((0.10, 0.8), (0.20, 0.5)))


def test_cumulative_above_one_raises():
    with pytest.raises(ValueError):
        compute_deployment(0.25, True, budget=0.15, ladder=((0.10, 0.5), (0.20, 1.2)))


def test_threshold_out_of_unit_range_raises():
    with pytest.raises(ValueError):
        compute_deployment(0.25, True, budget=0.15, ladder=((0.0, 0.5),))


def test_ladder_fraction_helper():
    assert ladder_fraction(0.05, DEFAULT_LADDER) == pytest.approx(0.0)
    assert ladder_fraction(0.25, DEFAULT_LADDER) == pytest.approx(2.0 / 3.0)


def test_one_rung_ladder_deploys_full_budget_at_threshold():
    d = compute_deployment(0.25, True, budget=0.15, ladder=((0.10, 1.0),))
    assert d.tranche_index == 1
    assert d.n_tranches == 1
    assert d.deployed_fraction == pytest.approx(0.15)


def test_under_deploy_ladder_caps_below_budget_by_design():
    # last cumulative 0.75 < 1.0 -> a deliberately conservative ladder never reaches full budget
    ladder = ((0.10, 0.5), (0.20, 0.75))
    d = compute_deployment(0.30, True, budget=0.15, ladder=ladder)
    assert d.deployed_fraction == pytest.approx(0.15 * 0.75)
    assert d.tranche_index == 2


# --------------------------------------------------------------------------- #
# default_value_gate
# --------------------------------------------------------------------------- #


def _holding(symbol: str, cheapness: float | None) -> CoreHolding:
    return CoreHolding(
        symbol=symbol,
        weight=0.077,
        composite=0.5,
        display_score=50.0,
        cheapness_pct=cheapness,
        gp_pct=0.5,
        sector="Tech",
        flags=(),
        rationale="",
    )


def _basket(*cheapness: float | None) -> CoreBasket:
    holdings = tuple(_holding(f"S{i}", c) for i, c in enumerate(cheapness))
    return CoreBasket(
        holdings=holdings,
        as_of=None,
        universe_size=len(holdings),
        eligible_count=len(holdings),
        target_n=13,
        max_weight=0.08,
        excluded=(),
    )


def test_value_gate_open_when_median_cheapness_at_or_above_threshold():
    assert default_value_gate(_basket(0.6, 0.7, 0.8), threshold=0.55) is True


def test_value_gate_closed_when_median_cheapness_below_threshold():
    assert default_value_gate(_basket(0.2, 0.3, 0.4), threshold=0.55) is False


def test_value_gate_ignores_none_cheapness():
    assert default_value_gate(_basket(0.6, None, 0.7), threshold=0.55) is True


def test_value_gate_all_none_is_closed():
    assert default_value_gate(_basket(None, None), threshold=0.55) is False


def test_value_gate_empty_basket_is_closed():
    assert default_value_gate(_basket(), threshold=0.55) is False


def test_value_gate_exactly_at_threshold_is_open():
    # boundary: median exactly == threshold -> open (>=)
    assert default_value_gate(_basket(0.55, 0.55), threshold=0.55) is True


# --------------------------------------------------------------------------- #
# bridge_sleeve_target / format_deployment
# --------------------------------------------------------------------------- #


def test_bridge_sleeve_target_scales_core_weights():
    d = compute_deployment(0.25, True, budget=0.15)  # deploys 0.10
    sleeve = bridge_sleeve_target(d, {"A": 0.5, "B": 0.5})
    assert isinstance(sleeve, SleeveTarget)
    assert sleeve.name == "bridge"
    assert sleeve.fraction == pytest.approx(0.10)
    assert sleeve.weights == {"A": 0.5, "B": 0.5}


def test_bridge_sleeve_target_zero_deployment_is_valid_sleeve():
    d = compute_deployment(0.05, True, budget=0.15)  # deploys 0
    sleeve = bridge_sleeve_target(d, {"A": 1.0})
    assert sleeve.fraction == pytest.approx(0.0)
    assert sleeve.weights == {"A": 1.0}


def test_format_deployment_restates_framing():
    d = compute_deployment(0.25, True, budget=0.15)
    text = format_deployment(d)
    assert "알파" in text  # honest framing header present
    assert "배치" in text


# --------------------------------------------------------------------------- #
# fund_book integration
# --------------------------------------------------------------------------- #


def test_bridge_sums_into_core_and_respects_cap_via_fund_book():
    core_weights = {"A": 0.5, "B": 0.5}
    d = compute_deployment(0.35, True, budget=0.15)  # full budget -> 0.15
    core = SleeveTarget("core", 0.35, core_weights)
    hunt = SleeveTarget("hunt", 0.15, {"C": 1.0})
    bridge = bridge_sleeve_target(d, core_weights)
    book = assemble_fund_book([core, hunt, bridge], max_name_weight=0.08)
    w = {p.symbol: p.fund_weight for p in book.positions}
    # A: core 0.35*0.5=0.175 + bridge 0.15*0.5=0.075 = 0.25 -> capped at 0.08
    assert w["A"] == pytest.approx(0.08)
    assert any(p.symbol == "A" and p.capped for p in book.positions)
    # C: hunt 0.15*1.0=0.15 -> capped at 0.08
    assert w["C"] == pytest.approx(0.08)
    # leverage guard intact: fractions 0.35+0.15+0.15=0.65 <= 1.0
    assert book.reserve_cash > 0.0


def test_zero_deployment_bridge_does_not_change_book():
    core_weights = {"A": 1.0}
    d = compute_deployment(0.0, True, budget=0.15)  # deploys 0
    book_without = assemble_fund_book(
        [SleeveTarget("core", 0.35, core_weights)], max_name_weight=1.0
    )
    book_with = assemble_fund_book(
        [SleeveTarget("core", 0.35, core_weights), bridge_sleeve_target(d, core_weights)],
        max_name_weight=1.0,
    )
    assert book_with.invested == pytest.approx(book_without.invested)
