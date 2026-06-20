from __future__ import annotations

from pathlib import Path

import pytest

from engine.fund_book import SleeveTarget, assemble_fund_book
from engine.fund_book_oos import (
    FundBookOOSEntry,
    append_entry,
    fund_book_to_entry,
    load_ledger,
    mark_prices_at_dates,
    score_ledger,
)


def _entry(
    rebal_date: str,
    weights: dict[str, float],
    prices: dict[str, float],
    bench_price: float,
) -> FundBookOOSEntry:
    return FundBookOOSEntry(
        rebal_date=rebal_date,
        weights=weights,
        entry_prices=prices,
        benchmark_symbol="SPY",
        benchmark_price=bench_price,
        sleeve_fractions={"core": 0.35, "hunt": 0.15},
        reserve_cash=1.0 - sum(weights.values()),
        invested=sum(weights.values()),
    )


# --------------------------------------------------------------------------- #
# ledger I/O (append-only)
# --------------------------------------------------------------------------- #


def test_append_and_load_roundtrip(tmp_path: Path):
    path = tmp_path / "fund-book-oos.jsonl"
    e = _entry("2026-01-01", {"A": 0.5, "B": 0.5}, {"A": 100.0, "B": 100.0}, 400.0)
    append_entry(path, e)
    loaded = load_ledger(path)
    assert len(loaded) == 1
    assert loaded[0].rebal_date == "2026-01-01"
    assert loaded[0].weights == {"A": 0.5, "B": 0.5}
    assert loaded[0].invested == pytest.approx(1.0)


def test_append_refuses_duplicate_rebal_date(tmp_path: Path):
    path = tmp_path / "fund-book-oos.jsonl"
    append_entry(path, _entry("2026-01-01", {"A": 1.0}, {"A": 100.0}, 400.0))
    with pytest.raises(ValueError):
        append_entry(path, _entry("2026-01-01", {"B": 1.0}, {"B": 50.0}, 400.0))


def test_load_missing_ledger_is_empty(tmp_path: Path):
    assert load_ledger(tmp_path / "nope.jsonl") == []


# --------------------------------------------------------------------------- #
# fund_book_to_entry
# --------------------------------------------------------------------------- #


def _book():
    return assemble_fund_book(
        [
            SleeveTarget("core", 0.35, {"AAA": 0.5, "BBB": 0.5}),
            SleeveTarget("hunt", 0.15, {"CCC": 1.0}),
        ],
        max_name_weight=0.08,
    )


def test_fund_book_to_entry_maps_positions_to_weights():
    book = _book()
    prices = {"AAA": 10.0, "BBB": 20.0, "CCC": 30.0}
    entry = fund_book_to_entry(
        book,
        rebal_date="2026-06-20",
        entry_prices=prices,
        benchmark_symbol="SPY",
        benchmark_price=500.0,
    )
    assert entry.weights == {p.symbol: p.fund_weight for p in book.positions}
    assert set(entry.entry_prices) == set(entry.weights)
    assert entry.invested == pytest.approx(book.invested)
    assert entry.sleeve_fractions == {"core": 0.35, "hunt": 0.15}


def test_fund_book_to_entry_raises_on_missing_entry_price():
    book = _book()
    with pytest.raises(ValueError):
        fund_book_to_entry(
            book,
            rebal_date="2026-06-20",
            entry_prices={"AAA": 10.0},  # BBB / CCC missing
            benchmark_symbol="SPY",
            benchmark_price=500.0,
        )


def test_fund_book_to_entry_raises_on_nonpositive_benchmark():
    book = _book()
    prices = {"AAA": 10.0, "BBB": 20.0, "CCC": 30.0}
    with pytest.raises(ValueError):
        fund_book_to_entry(
            book,
            rebal_date="2026-06-20",
            entry_prices=prices,
            benchmark_symbol="SPY",
            benchmark_price=0.0,
        )


# --------------------------------------------------------------------------- #
# score_ledger
# --------------------------------------------------------------------------- #


def test_score_two_entries_known_excess():
    entries = [
        _entry("2026-01-01", {"A": 0.5, "B": 0.5}, {"A": 100.0, "B": 100.0}, 400.0),
        _entry(
            "2026-02-01", {"A": 0.5, "B": 0.5}, {"A": 110.0, "B": 110.0}, 404.0
        ),  # open, not scored
    ]
    # period 0 marked at 2026-02-01: A 120 (+20%), B 110 (+10%) -> port 0.15; SPY 404 (+1%)
    marks = {"2026-02-01": {"A": 120.0, "B": 110.0, "SPY": 404.0}}
    rec = score_ledger(entries, marks)
    assert rec.n_periods == 1
    assert rec.cumulative_return == pytest.approx(0.15)
    assert rec.cumulative_benchmark == pytest.approx(0.01)
    assert rec.cumulative_excess == pytest.approx(0.14)
    assert rec.hit_rate == pytest.approx(1.0)
    assert rec.vs_backtest is None


def test_score_renormalises_over_marked_symbols_only():
    entries = [
        _entry("2026-01-01", {"A": 0.4, "B": 0.4}, {"A": 100.0, "B": 100.0}, 400.0),
        _entry("2026-02-01", {"A": 0.4, "B": 0.4}, {"A": 100.0, "B": 100.0}, 400.0),
    ]
    marks = {"2026-02-01": {"A": 120.0, "SPY": 400.0}}  # only A has a mark
    rec = score_ledger(entries, marks)
    # renormalised over A only -> port return = A's +20%; bench flat -> excess 0.20
    assert rec.cumulative_return == pytest.approx(0.20)
    assert rec.cumulative_excess == pytest.approx(0.20)


def test_score_benchmark_outperformance_gives_negative_excess():
    entries = [
        _entry("2026-01-01", {"A": 1.0}, {"A": 100.0}, 400.0),
        _entry("2026-02-01", {"A": 1.0}, {"A": 100.0}, 440.0),
    ]
    marks = {"2026-02-01": {"A": 105.0, "SPY": 440.0}}  # A +5%, SPY +10%
    rec = score_ledger(entries, marks)
    assert rec.cumulative_excess == pytest.approx(-0.05)
    assert rec.hit_rate == pytest.approx(0.0)


def test_score_vs_backtest_only_when_provided():
    entries = [
        _entry("2026-01-01", {"A": 1.0}, {"A": 100.0}, 400.0),
        _entry("2026-02-01", {"A": 1.0}, {"A": 110.0}, 404.0),
    ]
    marks = {"2026-02-01": {"A": 112.0, "SPY": 404.0}}
    rec = score_ledger(entries, marks, backtest_excess_ann=0.08)
    assert rec.vs_backtest is not None
    assert rec.vs_backtest == pytest.approx(rec.annualized_excess / 0.08)


def test_score_skips_symbol_with_zero_entry_price():
    # adversarial-review MEDIUM: a non-positive entry price must be skipped (renormalise over the rest)
    entries = [
        _entry("2026-01-01", {"A": 0.5, "B": 0.5}, {"A": 0.0, "B": 100.0}, 400.0),  # A buy = 0
        _entry("2026-02-01", {"A": 0.5, "B": 0.5}, {"A": 0.0, "B": 100.0}, 400.0),
    ]
    marks = {"2026-02-01": {"A": 120.0, "B": 110.0, "SPY": 400.0}}
    rec = score_ledger(entries, marks)
    # A skipped (buy <= 0) -> renormalise over B only: +10%; bench flat -> excess 0.10
    assert rec.cumulative_return == pytest.approx(0.10)
    assert rec.cumulative_excess == pytest.approx(0.10)


def test_score_skips_period_with_missing_benchmark_mark():
    # adversarial-review MEDIUM: benchmark symbol absent from exit marks -> period skipped (fail-closed)
    entries = [
        _entry("2026-01-01", {"A": 1.0}, {"A": 100.0}, 400.0),
        _entry("2026-02-01", {"A": 1.0}, {"A": 100.0}, 400.0),
    ]
    marks = {"2026-02-01": {"A": 120.0}}  # no SPY
    rec = score_ledger(entries, marks)
    assert rec.n_periods == 0


def test_score_reserve_cash_treated_flat():
    # adversarial-review LOW: weights summing < 1.0 (reserve cash) -> reserve neither helps nor hurts;
    # port return is over the invested names only, NOT diluted by reserve.
    entries = [
        _entry("2026-01-01", {"A": 0.5}, {"A": 100.0}, 400.0),  # invested 0.5, reserve 0.5
        _entry("2026-02-01", {"A": 0.5}, {"A": 100.0}, 400.0),
    ]
    marks = {"2026-02-01": {"A": 120.0, "SPY": 400.0}}
    rec = score_ledger(entries, marks)
    # A +20%, renormalised over invested (A only) -> 0.20 (NOT 0.10 from diluting by reserve)
    assert rec.cumulative_return == pytest.approx(0.20)


def test_score_empty_or_single_entry_is_zero_record():
    assert score_ledger([], {}).n_periods == 0
    single = [_entry("2026-01-01", {"A": 1.0}, {"A": 100.0}, 400.0)]
    assert score_ledger(single, {}).n_periods == 0


def test_mark_prices_respects_max_staleness():
    history = {
        "2026-01-01": {"A": 100.0},
        "2026-01-05": {"SPY": 400.0},  # A not refreshed here
    }
    # request 2026-02-01: A's last mark is 2026-01-01 (27 days old). staleness 10 drops it.
    out = mark_prices_at_dates(history, ["2026-02-01"], max_staleness_days=10)
    assert "A" not in out.get("2026-02-01", {})
    # unbounded keeps it
    out2 = mark_prices_at_dates(history, ["2026-02-01"], max_staleness_days=None)
    assert out2["2026-02-01"]["A"] == pytest.approx(100.0)
