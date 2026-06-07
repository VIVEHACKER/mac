from __future__ import annotations

from datetime import date, datetime

import pytest

from data.models import FundamentalRecord
from engine.hunt_basket import (
    HuntBasket,
    HuntHolding,
    format_hunt_basket,
    select_hunt_basket,
)
from strategies._base import StrategySignal


def _sig(symbol, score, *, direction="long", reason="insider buys", as_of=date(2026, 6, 1)):
    return StrategySignal(
        symbol=symbol, market="us", as_of=as_of, score=score, direction=direction, reason=reason
    )


def _recs(symbol, *, rev, ni, fcf, gp, assets, eq, debt, sh, eps):
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


def _insider_universe(n):
    """n names each with a long insider signal (score descending) + fundamentals."""
    insider, universe = {}, {}
    for i in range(n):
        s = f"H{i:02d}"
        insider[s] = _sig(s, float(1_000_000 - i * 1000))
        universe[s] = (
            _recs(
                s,
                rev=[100, 100, 100, 100],
                ni=[5, 5, 5, 5],
                fcf=[5, 5, 5, 5],
                gp=[30, 30, 30, 30],
                assets=100.0,
                eq=100.0,
                debt=10.0,
                sh=10.0,
                eps=1.0,
            ),
            30.0,
        )
    return insider, universe


# ---------------------------------------------------------------- Task 1
def test_dataclasses_constructible():
    h = HuntHolding(
        symbol="AAA",
        weight=0.1667,
        fund_weight=0.025,
        insider_score=1_200_000.0,
        insider_reason="insider buys: 3 by 2",
        signal_flags=("외국인순매수",),
        sector=None,
        kill_thesis="진입=내부자매수; 청산=순매도전환",
        rationale="내부자 고확신",
    )
    b = HuntBasket(
        holdings=(h,),
        as_of=None,
        universe_size=1,
        signal_eligible_count=1,
        target_n=6,
        max_per_name=0.40,
        sleeve_fraction=0.15,
        sleeve_total_fund_weight=0.025,
        max_single_name_fund_loss=0.025,
        excluded=(),
    )
    assert b.holdings[0].symbol == "AAA"
    assert b.max_single_name_fund_loss == 0.025


# ---------------------------------------------------------------- Task 2
def test_screen_gates_on_insider_long_event_only():
    from engine.hunt_basket import _signal_eligible

    insider = {
        "BUY": _sig("BUY", 1_000_000.0, direction="long"),
        "NONE": None,
        "SHORT": _sig("SHORT", 5_000.0, direction="short"),
    }
    eligible, excluded = _signal_eligible(insider)
    assert "BUY" in eligible
    ex = dict(excluded)
    assert "NONE" in ex and "신호" in ex["NONE"]
    assert "SHORT" in ex


def test_screen_keeps_high_debt_name_unlike_core():
    from engine.hunt_basket import _signal_eligible

    insider = {"RISKY": _sig("RISKY", 800_000.0, direction="long")}
    eligible, excluded = _signal_eligible(insider)
    assert eligible == ["RISKY"]
    assert excluded == []


# ---------------------------------------------------------------- Task 3
def test_rank_by_insider_score_and_flags_do_not_change_order():
    from engine.hunt_basket import _rank_candidates

    insider = {"HI": _sig("HI", 2_000_000.0), "LO": _sig("LO", 100_000.0)}
    universe = {
        "HI": (
            _recs(
                "HI",
                rev=[100, 100, 100, 100],
                ni=[5, 5, 5, 5],
                fcf=[5, 5, 5, 5],
                gp=[30, 30, 30, 30],
                assets=100.0,
                eq=100.0,
                debt=10.0,
                sh=10.0,
                eps=1.0,
            ),
            50.0,
        ),
        "LO": (
            _recs(
                "LO",
                rev=[100, 100, 100, 100],
                ni=[5, 5, 5, 5],
                fcf=[5, 5, 5, 5],
                gp=[30, 30, 30, 30],
                assets=100.0,
                eq=100.0,
                debt=10.0,
                sh=10.0,
                eps=1.0,
            ),
            10.0,
        ),
    }
    eligible = ["HI", "LO"]
    foreign = {"LO": _sig("LO", 9_999_999.0, reason="foreign inflow")}
    ranked = _rank_candidates(
        eligible, insider, universe, foreign_flow=foreign, capital_signals=None
    )
    assert [r[0] for r in ranked] == ["HI", "LO"]


# ---------------------------------------------------------------- Task 4
def test_flags_collects_descriptive_signals_and_distress():
    from engine.hunt_basket import _collect_flags

    metrics = {"debt_to_equity": 3.5, "fcf_margin": -0.1, "share_growth": 0.0, "margin_trend": 0.0}
    flags = _collect_flags(
        "X",
        metrics,
        foreign=_sig("X", 1.0, direction="long"),
        capital=_sig(
            "X", 1.0, direction="short", reason="net issuance +12% (dilution) [large raise]"
        ),
    )
    assert "외국인순매수" in flags
    assert "대규모조달⚠" in flags
    assert "high-debt" in flags


def test_kill_thesis_is_fundamental_not_price():
    from engine.hunt_basket import _kill_thesis

    kt = _kill_thesis("insider buys: 3 by 2 in 90d", ("high-debt", "negative-fcf"))
    assert "내부자" in kt
    assert "청산" in kt
    assert "$" not in kt and "%" not in kt


# ---------------------------------------------------------------- Task 5
def test_select_six_names_equal_weight_and_survival_math():
    insider, universe = _insider_universe(8)
    basket = select_hunt_basket(
        insider, universe, target_n=6, max_per_name=0.40, sleeve_fraction=0.15
    )
    assert len(basket.holdings) == 6
    assert abs(sum(h.weight for h in basket.holdings) - 1.0) < 1e-6
    for h in basket.holdings:
        assert abs(h.weight - 1 / 6) < 1e-9
        assert abs(h.fund_weight - (1 / 6) * 0.15) < 1e-9
    assert abs(basket.sleeve_total_fund_weight - 0.15) < 1e-6
    assert abs(basket.max_single_name_fund_loss - (1 / 6) * 0.15) < 1e-9
    assert basket.signal_eligible_count == 8


def test_select_degenerate_two_names_caps_and_warns():
    insider, universe = _insider_universe(2)
    with pytest.warns(UserWarning, match="degenerate"):
        basket = select_hunt_basket(insider, universe, target_n=6, max_per_name=0.40)
    for h in basket.holdings:
        assert abs(h.weight - 0.40) < 1e-9
    assert abs(sum(h.weight for h in basket.holdings) - 0.80) < 1e-6


def test_select_attaches_kill_thesis_and_excludes_signalless():
    insider, universe = _insider_universe(3)
    insider["NOPE"] = None
    basket = select_hunt_basket(insider, universe, target_n=6)
    assert all(h.kill_thesis for h in basket.holdings)
    assert "NOPE" in dict(basket.excluded)


# ---------------------------------------------------------------- Task 6
def test_format_contains_honest_header_and_survival_math():
    insider, universe = _insider_universe(6)
    basket = select_hunt_basket(insider, universe, target_n=6)
    txt = format_hunt_basket(basket)
    assert "후보" in txt
    assert "미검증" in txt
    assert "내부자" in txt
    assert "단일종목" in txt or "단일" in txt
    assert "%" in txt
    assert basket.holdings[0].symbol in txt


# ---------------------------------------------------------------- Task 7
def test_select_empty_universe_no_crash():
    basket = select_hunt_basket({}, {}, target_n=6)
    assert basket.holdings == ()
    assert basket.signal_eligible_count == 0
    assert basket.max_single_name_fund_loss == 0.0


def test_select_no_eligible_when_all_signals_none():
    insider = {"A": None, "B": None}
    basket = select_hunt_basket(insider, {}, target_n=6)
    assert basket.holdings == ()
    assert len(basket.excluded) == 2


def test_capital_dilution_flag_does_not_change_rank():
    insider, universe = _insider_universe(3)
    capital = {"H00": _sig("H00", 1.0, direction="short", reason="net issuance +20% (dilution)")}
    basket = select_hunt_basket(insider, universe, target_n=6, capital_signals=capital)
    assert basket.holdings[0].symbol == "H00"
    assert "희석⚠" in basket.holdings[0].signal_flags
