from __future__ import annotations

import pytest

from engine.paper_oos import (
    OOSTrackRecord,
    PaperOOSEntry,
    append_entry,
    load_ledger,
    score_ledger,
)


def _entry(
    date: str, weights: dict[str, float], prices: dict[str, float], bench_px: float
) -> PaperOOSEntry:
    return PaperOOSEntry(
        rebal_date=date,
        strategy_id="aqr_top7",
        weights=weights,
        entry_prices=prices,
        benchmark_symbol="SPY",
        benchmark_price=bench_px,
    )


def test_score_ledger_realized_excess_over_closed_periods() -> None:
    entries = [
        _entry("2026-01-01", {"A": 0.5, "B": 0.5}, {"A": 100.0, "B": 100.0}, 400.0),
        _entry("2026-02-01", {"C": 0.5, "D": 0.5}, {"C": 100.0, "D": 100.0}, 408.0),
        _entry("2026-03-01", {"E": 1.0}, {"E": 100.0}, 420.0),
    ]
    marks = {
        "2026-02-01": {"A": 110.0, "B": 90.0, "SPY": 408.0},  # port 0%, bench +2% -> excess -2%
        "2026-03-01": {"C": 120.0, "D": 120.0, "SPY": 420.0},  # port +20%, bench +2.94% -> +17.06%
    }

    record = score_ledger(entries, marks, periods_per_year=12.0, backtest_excess_ann=0.08)

    assert isinstance(record, OOSTrackRecord)
    assert record.n_periods == 2  # only closed periods scored (3 entries -> 2 realized)
    assert record.hit_rate == pytest.approx(0.5)
    assert record.cumulative_return == pytest.approx(0.20, abs=1e-9)  # (1+0)*(1+0.2)-1
    assert record.cumulative_benchmark == pytest.approx(1.02 * (420 / 408) - 1, abs=1e-9)
    assert record.cumulative_excess == pytest.approx(
        record.cumulative_return - record.cumulative_benchmark, abs=1e-12
    )
    assert record.vs_backtest is not None


def test_score_ledger_renormalises_when_a_mark_is_missing() -> None:
    entries = [
        _entry("2026-01-01", {"A": 0.5, "B": 0.5}, {"A": 100.0, "B": 100.0}, 400.0),
        _entry("2026-02-01", {"X": 1.0}, {"X": 100.0}, 400.0),
    ]
    marks = {"2026-02-01": {"A": 110.0, "SPY": 400.0}}  # B missing -> A carries full weight

    record = score_ledger(entries, marks)

    assert record.n_periods == 1
    # only A present -> port return = A's +10%, bench flat -> excess +10%
    assert record.cumulative_return == pytest.approx(0.10, abs=1e-9)


def test_append_entry_is_pre_registered_and_refuses_duplicates(tmp_path) -> None:
    ledger = tmp_path / "oos.jsonl"
    entry = _entry("2026-01-01", {"A": 1.0}, {"A": 100.0}, 400.0)

    append_entry(ledger, entry)
    assert len(load_ledger(ledger)) == 1

    with pytest.raises(ValueError, match="already recorded"):
        append_entry(ledger, entry)  # same date+strategy -> immutable history, no rewrite
