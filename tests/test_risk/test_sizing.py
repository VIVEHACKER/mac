from __future__ import annotations

import pytest

from risk.sizing import kelly_fraction, size_position, vol_target_weight


def test_kelly_fraction_positive_edge() -> None:
    # p=0.6, win=0.5, loss=0.3 -> b=5/3, edge=(b*p-q)/b = (1.0-0.4)/(5/3) = 0.36
    assert kelly_fraction(0.6, 0.5, 0.3) == pytest.approx(0.36, abs=1e-9)


def test_kelly_fraction_zero_on_negative_or_degenerate_edge() -> None:
    assert kelly_fraction(0.3, 0.5, 0.5) == 0.0  # negative edge
    assert kelly_fraction(0.0, 0.5, 0.3) == 0.0
    assert kelly_fraction(1.0, 0.5, 0.3) == 0.0
    assert kelly_fraction(0.6, 0.0, 0.3) == 0.0
    assert kelly_fraction(0.6, 0.5, 0.0) == 0.0


def test_vol_target_weight_caps_at_max_leverage_and_zero_floor() -> None:
    assert vol_target_weight(0.35, 0.35) == pytest.approx(1.0)
    assert vol_target_weight(0.70, 0.35) == pytest.approx(0.5)
    assert vol_target_weight(0.10, 0.35) == pytest.approx(1.0)  # capped at max_leverage 1.0
    assert vol_target_weight(0.0, 0.35) == 0.0


def test_risk_cap_binds_and_caps_risk_at_2pct() -> None:
    # half-Kelly 0.18, vol 1.0, risk_cap 0.02/0.30=0.0667 -> risk_cap is smallest
    r = size_position(
        100_000.0,
        100.0,
        asset_vol_annualized=0.35,
        downside_pct=0.30,
        win_probability=0.6,
        upside_pct=0.5,
    )
    assert r.binding == "risk_cap"
    assert r.capped_weight == pytest.approx(0.0667, abs=1e-3)
    assert r.risk_pct_of_aum <= 0.02 + 1e-9
    assert r.qty == 66  # floor(100_000*0.0667/100)


def test_hard_cap_binds_for_high_conviction_low_vol() -> None:
    # tight downside + low vol + high edge -> all constraints loose, 8% hard cap binds
    r = size_position(
        100_000.0,
        100.0,
        asset_vol_annualized=0.10,
        downside_pct=0.05,
        win_probability=0.9,
        upside_pct=2.0,
    )
    assert r.binding == "hard_cap"
    assert r.capped_weight == pytest.approx(0.08)
    assert r.actual_weight <= 0.08 + 1e-9


def test_share_flooring_and_too_small_to_size() -> None:
    r = size_position(
        100_000.0,
        100.0,
        asset_vol_annualized=0.35,
        downside_pct=0.30,
        win_probability=0.6,
        upside_pct=0.5,
    )
    assert r.qty == int(r.capped_weight * 100_000.0 / 100.0)  # floor(capped budget / price)
    # price exceeds the whole capped dollar budget -> qty 0 (skip, not mis-weight)
    tiny = size_position(
        1_000.0,
        5_000.0,
        asset_vol_annualized=0.35,
        downside_pct=0.30,
        win_probability=0.6,
        upside_pct=0.5,
    )
    assert tiny.qty == 0


def test_rejects_invalid_inputs() -> None:
    for kwargs in (
        {"aum": 0.0, "price": 100.0},
        {"aum": 100_000.0, "price": 0.0},
    ):
        with pytest.raises(ValueError):
            size_position(
                **kwargs,
                asset_vol_annualized=0.35,
                downside_pct=0.30,
                win_probability=0.6,
                upside_pct=0.5,
            )
    with pytest.raises(ValueError):
        size_position(
            100_000.0,
            100.0,
            asset_vol_annualized=0.35,
            downside_pct=0.0,
            win_probability=0.6,
            upside_pct=0.5,
        )


@pytest.mark.parametrize("downside", [0.05, 0.15, 0.30, 0.50, 0.80])
@pytest.mark.parametrize("vol", [0.10, 0.35, 0.80])
@pytest.mark.parametrize("win", [0.5, 0.7, 0.95])
def test_survival_invariant_no_position_exceeds_caps(downside, vol, win) -> None:
    """The Phase 0.2 acceptance test: NO sized position can exceed 8% of AUM (so a single name to
    ZERO costs <= 8%) and the downside-move risk never exceeds 2% of AUM, for any input."""
    r = size_position(
        1_000_000.0,
        250.0,
        asset_vol_annualized=vol,
        downside_pct=downside,
        win_probability=win,
        upside_pct=1.0,
    )
    assert r.actual_weight <= 0.08 + 1e-9
    assert r.risk_pct_of_aum <= 0.02 + 1e-9
