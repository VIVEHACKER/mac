from __future__ import annotations

from datetime import date, datetime

import pytest

from data.models import FundamentalRecord
from engine.core_basket import (
    CoreBasket,
    CoreHolding,
    RebalanceAction,
    format_core_basket,
    rebalance_core_basket,
    select_core_basket,
)


def _recs(symbol, *, rev, ni, fcf, gp, assets, eq, debt, sh, eps):
    """4 annual records 2020-2023; constant per-field except revenue ramp.

    gp = gross_profit per year (list), assets/eq/debt/sh/eps constant scalars.
    Mirrors tests/test_engine/test_compounder.py builder style."""
    out = []
    for i, year in enumerate((2020, 2021, 2022, 2023)):
        out.append(
            FundamentalRecord(
                symbol=symbol,
                market="us",
                period_end=date(year, 12, 31),
                asof_ts=datetime(year + 1, 3, 1),
                revenue=rev[i],
                net_income=ni[i],
                free_cash_flow=fcf[i],
                total_assets=assets,
                total_equity=eq,
                total_debt=debt,
                shares_out=sh,
                eps=eps,
                gross_profit=gp[i],
            )
        )
    return out


def _many(n, start_price=30.0):
    """Build n healthy, distinct names with varying cheapness so ranking is deterministic."""
    uni = {}
    for i in range(n):
        sym = f"S{i:02d}"
        # vary gp slightly so composites differ; all eligible & cheap
        recs = _recs(
            sym,
            rev=[100, 100, 100, 100],
            ni=[5, 5, 5, 5],
            fcf=[5, 5, 5, 5],
            gp=[40 + i, 40 + i, 40 + i, 40 + i],
            assets=100.0,
            eq=100.0,
            debt=10.0,
            sh=10.0,
            eps=1.0,
        )
        uni[sym] = (recs, start_price + i)  # higher i -> pricier (less cheap) but higher gp
    return uni


# ---------------------------------------------------------------- Task 1
def test_dataclasses_constructible():
    h = CoreHolding(
        symbol="AAA",
        weight=0.0769,
        composite=0.82,
        display_score=82.0,
        cheapness_pct=0.9,
        gp_pct=0.7,
        sector=None,
        flags=(),
        rationale="저평가+고GP",
    )
    b = CoreBasket(
        holdings=(h,),
        as_of=None,
        universe_size=1,
        eligible_count=1,
        target_n=13,
        max_weight=0.08,
        excluded=(),
    )
    a = RebalanceAction(symbol="AAA", action="hold", target_weight=0.0769, reason="여전히 적격")
    assert b.holdings[0].symbol == "AAA"
    assert a.action == "hold"


# ---------------------------------------------------------------- Task 2
def test_screen_excludes_cash_burner_and_overlevered_and_diluter():
    from engine.core_basket import _screen

    healthy = _recs(
        "OK",
        rev=[100, 110, 120, 130],
        ni=[10, 11, 12, 13],
        fcf=[8, 9, 10, 11],
        gp=[40, 44, 48, 52],
        assets=200.0,
        eq=100.0,
        debt=20.0,
        sh=50.0,
        eps=2.0,
    )
    burner = _recs(
        "BURN",
        rev=[100, 110, 120, 130],
        ni=[1, 1, 1, 1],
        fcf=[-5, -6, -7, -8],
        gp=[40, 44, 48, 52],
        assets=200.0,
        eq=100.0,
        debt=20.0,
        sh=50.0,
        eps=0.1,
    )
    levered = _recs(
        "LEV",
        rev=[100, 110, 120, 130],
        ni=[5, 5, 5, 5],
        fcf=[5, 5, 5, 5],
        gp=[40, 44, 48, 52],
        assets=500.0,
        eq=50.0,
        debt=400.0,
        sh=50.0,
        eps=1.0,
    )
    shares = [50.0, 65.0, 80.0, 95.0]
    diluter = [
        FundamentalRecord(
            symbol="DIL",
            market="us",
            period_end=date(y, 12, 31),
            asof_ts=datetime(y + 1, 3, 1),
            revenue=r,
            net_income=5.0,
            free_cash_flow=5.0,
            total_assets=200.0,
            total_equity=100.0,
            total_debt=20.0,
            shares_out=s,
            eps=1.0,
            gross_profit=g,
        )
        for y, r, s, g in zip(
            (2020, 2021, 2022, 2023), [100, 110, 120, 130], shares, [40, 44, 48, 52], strict=True
        )
    ]

    universe = {
        "OK": (healthy, 30.0),
        "BURN": (burner, 30.0),
        "LEV": (levered, 30.0),
        "DIL": (diluter, 30.0),
    }
    eligible, excluded = _screen(universe, sectors=None)
    assert "OK" in eligible
    ex = dict(excluded)
    assert "BURN" in ex and "fcf" in ex["BURN"].lower()
    assert "LEV" in ex and "debt" in ex["LEV"].lower()
    assert "DIL" in ex and ("dilut" in ex["DIL"].lower() or "share" in ex["DIL"].lower())


def test_screen_keeps_financial_with_high_debt_via_pb():
    from engine.core_basket import _screen

    bank = _recs(
        "BANK",
        rev=[100, 110, 120, 130],
        ni=[10, 11, 12, 13],
        fcf=[None, None, None, None],
        gp=[None, None, None, None],
        assets=1000.0,
        eq=100.0,
        debt=500.0,
        sh=50.0,
        eps=2.0,
    )
    universe = {"BANK": (bank, 30.0)}
    eligible, excluded = _screen(universe, sectors={"BANK": "financials"})
    assert "BANK" in eligible  # high d/e tolerated for financials, ranked by pb
    assert dict(excluded).get("BANK") is None


# ---------------------------------------------------------------- Task 3
def test_rank_prefers_cheap_high_gp_and_ignores_net_margin_roic():
    from engine.core_basket import _rank_eligible, _screen

    cheap_gp = _recs(
        "CHEAPGP",
        rev=[100, 100, 100, 100],
        ni=[5, 5, 5, 5],
        fcf=[5, 5, 5, 5],
        gp=[60, 60, 60, 60],
        assets=100.0,
        eq=100.0,
        debt=10.0,
        sh=10.0,
        eps=1.0,
    )
    exp_hq = _recs(
        "EXPHQ",
        rev=[100, 100, 100, 100],
        ni=[40, 40, 40, 40],
        fcf=[40, 40, 40, 40],
        gp=[30, 30, 30, 30],
        assets=100.0,
        eq=100.0,
        debt=10.0,
        sh=10.0,
        eps=4.0,
    )
    universe = {"CHEAPGP": (cheap_gp, 30.0), "EXPHQ": (exp_hq, 100.0)}
    eligible, _ = _screen(universe, sectors=None)
    ranked = _rank_eligible(eligible, w_value=0.6, w_gp=0.4)
    order = [r[0] for r in ranked]
    assert order[0] == "CHEAPGP"  # cheap + high GP wins
    assert order[-1] == "EXPHQ"  # high net_margin/roic did NOT lift it


# ---------------------------------------------------------------- Task 4
def test_select_n13_equal_weight_sums_to_one():
    basket = select_core_basket(_many(20), target_n=13, max_weight=0.08)
    assert len(basket.holdings) == 13
    for h in basket.holdings:
        assert h.weight <= 0.08 + 1e-9
    assert abs(sum(h.weight for h in basket.holdings) - 1.0) < 1e-6
    assert basket.eligible_count == 20
    assert basket.universe_size == 20


def test_select_n12_cap_binds_leaves_sleeve_cash():
    basket = select_core_basket(_many(12), target_n=13, max_weight=0.08)
    assert len(basket.holdings) == 12
    for h in basket.holdings:
        assert abs(h.weight - 0.08) < 1e-9
    total = sum(h.weight for h in basket.holdings)
    assert abs(total - 0.96) < 1e-6  # 12 * 0.08, remainder = sleeve cash


def test_select_attaches_scores_and_rationale():
    basket = select_core_basket(_many(13), target_n=13)
    h = basket.holdings[0]
    assert 0.0 <= h.display_score <= 100.0
    assert h.rationale  # non-empty Korean rationale
    assert isinstance(h.flags, tuple)


# ---------------------------------------------------------------- Task 5
def test_rebalance_holds_slipped_winner_and_drops_thesis_break():
    target = select_core_basket(_many(13), target_n=13)
    target_syms = [h.symbol for h in target.holdings]
    held = {target_syms[0]: 0.20, "GONE": 0.10, target_syms[1]: 0.05}
    eligible = set(target_syms)  # GONE not eligible
    new_basket, actions = rebalance_core_basket(
        held, target, eligible, target_n=13, max_weight=0.08
    )
    amap = {a.symbol: a for a in actions}
    assert amap["GONE"].action == "drop"
    assert "GONE" not in {h.symbol for h in new_basket.holdings}
    assert amap[target_syms[0]].action == "trim_to_cap"
    assert amap[target_syms[0]].target_weight <= 0.08 + 1e-9
    assert abs(sum(h.weight for h in new_basket.holdings) - 1.0) < 1e-6
    assert all(h.weight <= 0.08 + 1e-9 for h in new_basket.holdings)


def test_rebalance_adds_to_reach_target_n():
    target = select_core_basket(_many(13), target_n=13)
    held = {target.holdings[0].symbol: 0.08}  # only 1 held
    eligible = {h.symbol for h in target.holdings}
    new_basket, actions = rebalance_core_basket(held, target, eligible, target_n=13)
    adds = [a for a in actions if a.action == "add"]
    assert len(new_basket.holdings) == 13
    assert len(adds) == 12
    assert abs(sum(h.weight for h in new_basket.holdings) - 1.0) < 1e-6


# ---------------------------------------------------------------- Task 6
def test_format_contains_honest_header_and_holdings():
    basket = select_core_basket(_many(13), target_n=13)
    txt = format_core_basket(basket)
    assert "알파" in txt  # honest framing mentions "no alpha claim"
    assert "net-margin" in txt.lower() or "net_margin" in txt.lower()
    assert "ROIC" in txt or "roic" in txt
    assert basket.holdings[0].symbol in txt
    assert "%" in txt  # weight column rendered as percent


# -------------------------------------------- review fix: value actually leads
def test_value_actually_leads_not_gp_dominated():
    """The honesty fix: with w_value=0.6 > w_gp=0.4, the CHEAPEST name (top cheapness,
    bottom GP) must outrank the HIGHEST-GP name (bottom cheapness, top GP). Percentile
    ranks make the nominal weights real; the old z-score blend let GP's fat tail dominate."""
    from engine.core_basket import _rank_eligible, _screen

    # price drives ps/pb; gp drives GP/assets. VALUE cheapest+lowest GP, QUAL priciest+highest GP.
    value = _recs(
        "VALUE",
        rev=[100, 100, 100, 100],
        ni=[5, 5, 5, 5],
        fcf=[5, 5, 5, 5],
        gp=[20, 20, 20, 20],
        assets=100.0,
        eq=100.0,
        debt=10.0,
        sh=10.0,
        eps=1.0,
    )
    mid = _recs(
        "MID",
        rev=[100, 100, 100, 100],
        ni=[5, 5, 5, 5],
        fcf=[5, 5, 5, 5],
        gp=[40, 40, 40, 40],
        assets=100.0,
        eq=100.0,
        debt=10.0,
        sh=10.0,
        eps=1.0,
    )
    qual = _recs(
        "QUAL",
        rev=[100, 100, 100, 100],
        ni=[5, 5, 5, 5],
        fcf=[5, 5, 5, 5],
        gp=[60, 60, 60, 60],
        assets=100.0,
        eq=100.0,
        debt=10.0,
        sh=10.0,
        eps=1.0,
    )
    universe = {"VALUE": (value, 10.0), "MID": (mid, 30.0), "QUAL": (qual, 60.0)}
    eligible, _ = _screen(universe, sectors=None)
    ranked = _rank_eligible(eligible, w_value=0.6, w_gp=0.4)
    order = [r[0] for r in ranked]
    assert order == ["VALUE", "MID", "QUAL"]  # cheap leads despite QUAL having the highest GP


# -------------------------------------------- review fix: sector cap
def test_sector_cap_prevents_single_sector_dominance():
    uni = _many(8)
    sectors = {f"S0{i}": ("consumer" if i < 4 else "tech") for i in range(8)}
    basket = select_core_basket(uni, sectors=sectors, target_n=4, max_per_sector=2)
    from collections import Counter

    counts = Counter(h.sector for h in basket.holdings)
    assert len(basket.holdings) == 4
    assert all(c <= 2 for c in counts.values())  # no sector exceeds the cap


def test_sector_cap_backfills_rather_than_starving():
    # 5 consumer (ranked high) + 1 tech, target_n=4, cap=2: cap would give 2+1=3 < 4,
    # so it backfills overflow consumer to reach 4 (breadth beats an empty slot).
    uni = _many(6)
    sectors = {f"S0{i}": ("consumer" if i < 5 else "tech") for i in range(6)}
    basket = select_core_basket(uni, sectors=sectors, target_n=4, max_per_sector=2)
    assert len(basket.holdings) == 4  # not starved to 3


# -------------------------------------------- review fix: edge cases
def test_select_empty_universe_no_crash():
    basket = select_core_basket({}, target_n=13)
    assert basket.holdings == ()
    assert basket.universe_size == 0


def test_select_single_name_warns_degenerate():
    with pytest.warns(UserWarning, match="degenerate"):
        basket = select_core_basket(_many(1), target_n=13)
    assert len(basket.holdings) == 1
    assert abs(basket.holdings[0].weight - 0.08) < 1e-9  # 1/1 clamped to 8% cap


def test_select_target_n_larger_than_eligible():
    basket = select_core_basket(_many(3), target_n=13)
    assert len(basket.holdings) == 3


def test_rank_ties_are_deterministic():
    from engine.core_basket import _rank_eligible, _screen

    twin_a = _recs(
        "AAA",
        rev=[100, 100, 100, 100],
        ni=[5, 5, 5, 5],
        fcf=[5, 5, 5, 5],
        gp=[40, 40, 40, 40],
        assets=100.0,
        eq=100.0,
        debt=10.0,
        sh=10.0,
        eps=1.0,
    )
    twin_b = _recs(
        "BBB",
        rev=[100, 100, 100, 100],
        ni=[5, 5, 5, 5],
        fcf=[5, 5, 5, 5],
        gp=[40, 40, 40, 40],
        assets=100.0,
        eq=100.0,
        debt=10.0,
        sh=10.0,
        eps=1.0,
    )
    eligible, _ = _screen({"BBB": (twin_b, 30.0), "AAA": (twin_a, 30.0)}, sectors=None)
    ranked = _rank_eligible(eligible, w_value=0.6, w_gp=0.4)
    assert [r[0] for r in ranked] == ["AAA", "BBB"]  # tie broken by symbol


def test_rebalance_all_ineligible_empty_target_warns():
    empty_target = CoreBasket(
        holdings=(),
        as_of=None,
        universe_size=0,
        eligible_count=0,
        target_n=13,
        max_weight=0.08,
        excluded=(),
    )
    with pytest.warns(UserWarning, match="degenerate"):
        new_basket, actions = rebalance_core_basket(
            {"X": 0.5, "Y": 0.5}, empty_target, eligible=set(), target_n=13
        )
    assert new_basket.holdings == ()
    assert all(a.action == "drop" for a in actions)


def test_cap_redistribute_rejects_negative_weights():
    from engine.core_basket import _cap_redistribute

    with pytest.raises(ValueError, match="non-negative"):
        _cap_redistribute({"A": 0.5, "B": -0.1}, max_weight=0.08)
