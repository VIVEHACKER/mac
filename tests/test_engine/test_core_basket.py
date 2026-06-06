from __future__ import annotations

from datetime import date, datetime

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
        composite=1.2,
        display_score=88.0,
        cheapness_z=1.0,
        gp_z=0.5,
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
