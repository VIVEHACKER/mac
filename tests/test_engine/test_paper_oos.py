from __future__ import annotations

import pytest

from engine.paper_oos import (
    OOSTrackRecord,
    PaperOOSEntry,
    append_entry,
    load_ledger,
    load_mark_price_history_csv,
    mark_prices_at_dates,
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


def test_price_history_csv_marks_last_available_close(tmp_path) -> None:
    prices = tmp_path / "prices.csv"
    prices.write_text(
        "\n".join(
            [
                "Date,A,SPY",
                "2026-01-31,101,400",
                "2026-02-28,105,410",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    history = load_mark_price_history_csv(prices)
    marks = mark_prices_at_dates(history, ["2026-02-01", "2026-03-01"])

    assert marks["2026-02-01"] == {"A": 101.0, "SPY": 400.0}
    assert marks["2026-03-01"] == {"A": 105.0, "SPY": 410.0}


def test_mark_prices_staleness_bound_drops_carried_forward_marks() -> None:
    # CSV ends 2026-04-01; a later close must NOT be priced with the stale mark.
    history = {
        "2026-03-01": {"A": 100.0, "SPY": 400.0},
        "2026-04-01": {"A": 110.0, "SPY": 410.0},
    }
    requested = ["2026-04-03", "2026-05-01"]

    unbounded = mark_prices_at_dates(history, requested)
    assert unbounded["2026-05-01"] == {"A": 110.0, "SPY": 410.0}  # legacy carry-forward

    bounded = mark_prices_at_dates(history, requested, max_staleness_days=7)
    assert bounded["2026-04-03"] == {"A": 110.0, "SPY": 410.0}  # 2d old -> fresh enough
    assert "2026-05-01" not in bounded  # 30d old -> dropped, period left unscored


def test_score_ledger_unscored_when_close_mark_too_stale() -> None:
    # The live-readiness gate must not count a period scored on a frozen price.
    entries = [
        _entry("2026-03-01", {"A": 1.0}, {"A": 100.0}, 400.0),
        _entry("2026-05-01", {"B": 1.0}, {"B": 100.0}, 400.0),
    ]
    history = {"2026-03-01": {"A": 100.0, "SPY": 400.0}, "2026-04-01": {"A": 130.0, "SPY": 410.0}}

    fresh = mark_prices_at_dates(history, ["2026-05-01"], max_staleness_days=7)
    assert score_ledger(entries, fresh).n_periods == 0  # close mark too stale -> not scored


def test_mark_prices_carries_sparse_symbols_independently() -> None:
    # Codex P2: a later SPY-only row must NOT erase A's still-fresh earlier close.
    history = {
        "2026-04-01": {"A": 100.0, "SPY": 400.0},
        "2026-04-02": {"SPY": 402.0},  # sparse row — A omitted
    }

    marks = mark_prices_at_dates(history, ["2026-04-02"], max_staleness_days=7)
    assert marks["2026-04-02"] == {"A": 100.0, "SPY": 402.0}  # A carried, SPY updated


def test_mark_prices_per_symbol_staleness_drops_only_stale_symbol() -> None:
    # A goes stale while SPY stays fresh -> only A is dropped, SPY remains.
    history = {
        "2026-03-01": {"A": 100.0},  # A's last mark (old)
        "2026-04-28": {"SPY": 410.0},  # SPY fresh near the close
    }

    marks = mark_prices_at_dates(history, ["2026-05-01"], max_staleness_days=7)
    assert marks["2026-05-01"] == {"SPY": 410.0}  # A (61d) dropped, SPY (3d) kept


def test_append_entry_is_pre_registered_and_refuses_duplicates(tmp_path) -> None:
    ledger = tmp_path / "oos.jsonl"
    entry = _entry("2026-01-01", {"A": 1.0}, {"A": 100.0}, 400.0)

    append_entry(ledger, entry)
    assert len(load_ledger(ledger)) == 1

    with pytest.raises(ValueError, match="already recorded"):
        append_entry(ledger, entry)  # same date+strategy -> immutable history, no rewrite
