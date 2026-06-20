from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import pytest

from data.models import FundamentalRecord
from engine.fund_book import SleeveTarget, assemble_fund_book
from engine.momentum_basket import (
    momentum_sleeve_target,
    select_momentum_basket,
)

# Each symbol gets a distinct positive slope so 12-1 momentum differs and the cross-section ranks.
SYMS = ["AAA", "BBB", "CCC", "DDD", "EEE"]


def _prices(symbols: list[str], n: int = 300, start: str = "2024-01-01") -> pd.DataFrame:
    idx = pd.date_range(start, periods=n, freq="D")
    data = {s: [100.0 + i * (j + 1) * 0.05 for i in range(n)] for j, s in enumerate(symbols)}
    return pd.DataFrame(data, index=idx)


def _fundamentals(symbols: list[str]) -> dict[str, FundamentalRecord]:
    out: dict[str, FundamentalRecord] = {}
    for j, s in enumerate(symbols):
        out[s] = FundamentalRecord(
            symbol=s,
            market="us",
            period_end=date(2023, 12, 31),
            asof_ts=datetime(2024, 1, 15),
            net_income=10.0 + j,
            total_equity=100.0,
            free_cash_flow=8.0 + j,
            shares_out=1000.0,
        )
    return out


def test_basket_top_n_equals_universe_gives_equal_weight():
    # top_n=5, cap=0.20 -> 5 * 0.20 = 1.0 -> equal weight 0.20 each (validated cap-binding case)
    prices = _prices(SYMS)
    basket = select_momentum_basket(
        prices, _fundamentals(SYMS), SYMS, as_of=date(2024, 10, 1), top_n=5, cap=0.20
    )
    assert len(basket.holdings) == 5
    assert {h.symbol for h in basket.holdings} == set(SYMS)
    assert all(h.weight == pytest.approx(0.20) for h in basket.holdings)
    assert sum(h.weight for h in basket.holdings) == pytest.approx(1.0)
    assert basket.eligible_count == 5


def _vol_prices(symbols: list[str], n: int = 300) -> pd.DataFrame:
    # distinct ABOVE-FLOOR volatility per symbol: alternating +/- step_j daily returns
    # (linear _prices have sub-0.05-floor vol -> vol_estimate floors all -> equal weights).
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    data = {}
    for j, s in enumerate(symbols):
        # moderate, distinct daily vol per symbol (annualized ~0.8, all > 0.05 floor); a tight spread
        # so the 0.20 cap doesn't bind (extreme disparity wouldn't converge in the validated function's
        # 10 cap iterations — realistic megacap vols are close), letting inverse-vol weights just vary.
        step = 0.05 + 0.002 * j
        px = [100.0]
        for i in range(1, n):
            px.append(px[-1] * (1 + (step if i % 2 else -step)))
        data[s] = px
    return pd.DataFrame(data, index=idx)


def test_default_top_7_cap_20_uses_inverse_vol_not_equal_weight():
    # the VALIDATED default: 7 x 0.20 = 1.4 > 1.0 -> inverse-vol (NOT equal-weight); weights vary,
    # each <= cap, sum ~1.0. Adversarial-review HIGH (spec had claimed equal-weight).
    syms = [f"S{i}" for i in range(8)]  # 8 eligible, take top 7
    prices = _vol_prices(syms)
    basket = select_momentum_basket(
        prices, _fundamentals(syms), syms, as_of=date(2024, 10, 1), top_n=7, cap=0.20
    )
    assert len(basket.holdings) == 7
    weights = [h.weight for h in basket.holdings]
    assert sum(weights) == pytest.approx(1.0)
    assert all(w <= 0.20 + 1e-9 for w in weights)
    assert (
        len({round(w, 6) for w in weights}) > 1
    )  # inverse-vol -> not all identical (not equal-weight)


def test_lowercase_symbols_are_normalized_not_dropped():
    # adversarial-review HIGH: lowercase symbols must not silently miss uppercase price columns
    # (which would make vol_estimate fall back to 0.30 for all -> wrong weights / exclusion).
    prices = _prices(SYMS)  # columns AAA..EEE (uppercase)
    lower = [s.lower() for s in SYMS]
    basket = select_momentum_basket(
        prices, _fundamentals(SYMS), lower, as_of=date(2024, 10, 1), top_n=5, cap=0.20
    )
    assert {h.symbol for h in basket.holdings} == set(SYMS)  # normalized, all selected
    assert basket.excluded == ()


def test_infeasible_cap_in_basket_raises():
    # adversarial-review LOW: a degenerate universe (3 eligible) with top_n=7, cap=0.20 ->
    # weights_from_picks sees 3 x 0.20 = 0.6 < 1.0 -> infeasible cap -> ValueError.
    syms = ["AAA", "BBB", "CCC"]
    prices = _prices(syms)
    with pytest.raises(ValueError):
        select_momentum_basket(
            prices, _fundamentals(syms), syms, as_of=date(2024, 10, 1), top_n=7, cap=0.20
        )


def test_momentum_overlap_with_core_binds_8pct_cap():
    # adversarial-review LOW: a name in BOTH momentum and core sums across sleeves -> 8% cap binds.
    prices = _prices(SYMS)
    basket = select_momentum_basket(
        prices, _fundamentals(SYMS), SYMS, as_of=date(2024, 10, 1), top_n=5, cap=0.20
    )  # each momentum name 0.20 sleeve-weight -> 0.20*0.25 = 0.05 fund
    shared = next(iter(basket.holdings)).symbol
    core = SleeveTarget("core", 0.35, {shared: 1.0})  # shared at 1.0*0.35 = 0.35 fund
    momentum = momentum_sleeve_target(basket)
    book = assemble_fund_book([core, momentum], max_name_weight=0.08)
    pos = {p.symbol: p for p in book.positions}
    # shared = 0.35 + 0.05 = 0.40 -> capped at 0.08
    assert pos[shared].fund_weight == pytest.approx(0.08)
    assert pos[shared].capped is True


def test_top_n_smaller_than_universe_selects_best():
    # 5 eligible, top_n=3, cap=0.40 -> 3 * 0.40 = 1.2 > 1.0 -> inverse-vol, each <= 0.40, sum 1.0
    prices = _prices(SYMS)
    basket = select_momentum_basket(
        prices, _fundamentals(SYMS), SYMS, as_of=date(2024, 10, 1), top_n=3, cap=0.40
    )
    assert len(basket.holdings) == 3
    assert sum(h.weight for h in basket.holdings) == pytest.approx(1.0)
    assert all(h.weight <= 0.40 + 1e-9 for h in basket.holdings)
    # ranks are 1-based and the selected names are the top-3 of the cross-section
    assert {h.rank for h in basket.holdings} <= {1, 2, 3}


def test_insufficient_history_symbol_excluded():
    # DDD/EEE get only 100 bars (< 260) -> excluded; the 3 long names remain (top_n=3 cap .40)
    long_prices = _prices(["AAA", "BBB", "CCC"], n=300)
    short = _prices(["DDD", "EEE"], n=100)
    prices = pd.concat([long_prices, short], axis=1)
    funds = _fundamentals(SYMS)
    basket = select_momentum_basket(prices, funds, SYMS, as_of=date(2024, 10, 1), top_n=3, cap=0.40)
    held = {h.symbol for h in basket.holdings}
    assert "DDD" not in held and "EEE" not in held
    reasons = dict(basket.excluded)
    assert "데이터 부족" in reasons.get("DDD", "")


def test_missing_fundamentals_symbol_excluded():
    prices = _prices(SYMS)
    funds = _fundamentals(["AAA", "BBB", "CCC", "DDD"])  # EEE has no fundamentals
    basket = select_momentum_basket(prices, funds, SYMS, as_of=date(2024, 10, 1), top_n=4, cap=0.30)
    held = {h.symbol for h in basket.holdings}
    assert "EEE" not in held
    assert "EEE" in dict(basket.excluded)


def test_empty_universe_gives_empty_basket():
    basket = select_momentum_basket(
        _prices(SYMS), _fundamentals(SYMS), [], as_of=date(2024, 10, 1), top_n=5, cap=0.20
    )
    assert basket.holdings == ()
    assert basket.eligible_count == 0


def test_invalid_top_n_or_cap_raises():
    prices = _prices(SYMS)
    funds = _fundamentals(SYMS)
    with pytest.raises(ValueError):
        select_momentum_basket(prices, funds, SYMS, as_of=date(2024, 10, 1), top_n=0)
    with pytest.raises(ValueError):
        select_momentum_basket(prices, funds, SYMS, as_of=date(2024, 10, 1), cap=1.5)


def test_momentum_sleeve_target_shape():
    prices = _prices(SYMS)
    basket = select_momentum_basket(
        prices, _fundamentals(SYMS), SYMS, as_of=date(2024, 10, 1), top_n=5, cap=0.20
    )
    sleeve = momentum_sleeve_target(basket)
    assert isinstance(sleeve, SleeveTarget)
    assert sleeve.name == "momentum"
    assert sleeve.fraction == pytest.approx(0.25)
    assert sleeve.weights == {h.symbol: h.weight for h in basket.holdings}
    assert sum(sleeve.weights.values()) == pytest.approx(1.0)


def test_pit_post_as_of_prices_do_not_change_basket():
    # A post-as_of spike in one name must not affect its rank/weight (build_pricebars slices <= as_of).
    full = _prices(SYMS, n=300)
    as_of = full.index[290].date()  # 291 bars <= as_of (>= 260 eligible)
    spiked = full.copy()
    spiked.iloc[291:, spiked.columns.get_loc("AAA")] = 9_999.0  # future spike
    truncated = full.loc[: full.index[290]]
    b_spiked = select_momentum_basket(
        spiked, _fundamentals(SYMS), SYMS, as_of=as_of, top_n=5, cap=0.20
    )
    b_trunc = select_momentum_basket(
        truncated, _fundamentals(SYMS), SYMS, as_of=as_of, top_n=5, cap=0.20
    )
    assert {h.symbol: round(h.weight, 9) for h in b_spiked.holdings} == {
        h.symbol: round(h.weight, 9) for h in b_trunc.holdings
    }


def test_fund_book_composition_core_hunt_momentum_no_leverage():
    prices = _prices(SYMS)
    basket = select_momentum_basket(
        prices, _fundamentals(SYMS), SYMS, as_of=date(2024, 10, 1), top_n=5, cap=0.20
    )
    core = SleeveTarget("core", 0.35, {"XOM": 0.5, "JNJ": 0.5})
    hunt = SleeveTarget("hunt", 0.15, {"NVDA": 1.0})
    momentum = momentum_sleeve_target(basket)  # fraction 0.25
    book = assemble_fund_book([core, hunt, momentum], max_name_weight=0.08)
    # Sigma fractions 0.35 + 0.15 + 0.25 = 0.75 <= 1.0 -> reserve >= 0.25, no leverage
    assert book.reserve_cash >= 0.25 - 1e-9
    held = {p.symbol for p in book.positions}
    assert "AAA" in held  # a momentum name made it into the fund book
