from __future__ import annotations

import pytest

from engine.fund_book import (
    SleeveTarget,
    assemble_fund_book,
    format_fund_book,
)


def test_single_sleeve_fund_weight_is_weight_times_fraction():
    # max_name_weight=1.0 isolates the weight*fraction math (the 8% cap is covered separately); with
    # the default 8% cap these 0.175 weights would correctly bind to 0.08 (see the cap test).
    book = assemble_fund_book(
        [SleeveTarget("core", 0.35, {"A": 0.5, "B": 0.5})], max_name_weight=1.0
    )
    w = {p.symbol: p.fund_weight for p in book.positions}
    assert w == pytest.approx({"A": 0.175, "B": 0.175})
    assert book.invested == pytest.approx(0.35)
    assert book.reserve_cash == pytest.approx(0.65)


def test_shared_symbol_sums_across_sleeves():
    # A is both core-screened and insider-bought -> its fund weight is the SUM of both contributions.
    book = assemble_fund_book(
        [
            SleeveTarget("core", 0.35, {"A": 0.5}),
            SleeveTarget("hunt", 0.15, {"A": 1.0}),
        ],
        max_name_weight=1.0,  # disable cap to test the raw sum
    )
    a = next(p for p in book.positions if p.symbol == "A")
    assert a.fund_weight == pytest.approx(0.5 * 0.35 + 1.0 * 0.15)  # 0.325
    assert dict(a.contributions) == pytest.approx({"core": 0.175, "hunt": 0.15})


def test_global_cap_binds_and_overflow_goes_to_reserve():
    book = assemble_fund_book([SleeveTarget("core", 0.35, {"A": 1.0})], max_name_weight=0.08)
    a = book.positions[0]
    assert a.symbol == "A"
    assert a.fund_weight == pytest.approx(0.08)  # 0.35 -> capped to 0.08
    assert a.capped is True
    assert book.invested == pytest.approx(0.08)
    assert book.reserve_cash == pytest.approx(0.92)  # overflow becomes cash, no redistribution


def test_fractions_sum_over_one_raises_leverage_guard():
    with pytest.raises(ValueError, match="leverage|fraction"):
        assemble_fund_book([SleeveTarget("a", 0.6, {"X": 1.0}), SleeveTarget("b", 0.5, {"Y": 1.0})])


def test_sleeve_weights_sum_over_one_raises():
    with pytest.raises(ValueError, match="weights"):
        assemble_fund_book([SleeveTarget("a", 0.3, {"X": 0.6, "Y": 0.6})])


def test_negative_weight_raises():
    with pytest.raises(ValueError, match="non-negative|negative"):
        assemble_fund_book([SleeveTarget("a", 0.5, {"X": -0.1})])


def test_fraction_out_of_range_raises():
    with pytest.raises(ValueError, match="fraction"):
        assemble_fund_book([SleeveTarget("a", 1.5, {"X": 1.0})])


def test_reserve_cash_is_one_minus_invested():
    book = assemble_fund_book([SleeveTarget("core", 0.35, {"A": 0.5, "B": 0.5})])
    assert book.reserve_cash == pytest.approx(1.0 - book.invested)


def test_provenance_records_per_sleeve_contributions():
    book = assemble_fund_book(
        [
            SleeveTarget("core", 0.35, {"A": 0.5}),
            SleeveTarget("hunt", 0.15, {"A": 0.4}),
        ],
        max_name_weight=1.0,
    )
    a = next(p for p in book.positions if p.symbol == "A")
    assert dict(a.contributions) == pytest.approx({"core": 0.175, "hunt": 0.06})
    assert a.fund_weight == pytest.approx(0.235)


def test_empty_sleeves_gives_empty_book_full_reserve():
    book = assemble_fund_book([])
    assert book.positions == ()
    assert book.invested == pytest.approx(0.0)
    assert book.reserve_cash == pytest.approx(1.0)
    assert book.n_positions == 0


def test_positions_ordered_by_weight_desc_then_symbol():
    book = assemble_fund_book(
        [SleeveTarget("core", 1.0, {"A": 0.2, "B": 0.5, "C": 0.3})], max_name_weight=1.0
    )
    assert [p.symbol for p in book.positions] == ["B", "C", "A"]
    assert book.top_name_weight == pytest.approx(0.5)


def test_format_contains_honest_header():
    book = assemble_fund_book([SleeveTarget("core", 0.35, {"A": 0.5, "B": 0.5})])
    out = format_fund_book(book)
    assert "알파" in out  # honest "no alpha claim" framing travels with the report
    assert "%" in out
