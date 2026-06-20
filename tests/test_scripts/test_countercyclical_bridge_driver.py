"""PIT discipline tests for the countercyclical-bridge driver (scripts/countercyclical_bridge.py).

The engine is covered by tests/test_engine/test_countercyclical_bridge.py. This pins the
look-ahead-sensitive driver piece — load_market_prices() — where a single off-by-one (`>` vs `>=` on
as_of, or window slicing the wrong end) would leak future prices into an as_of cut or compute the peak
over the wrong window. Unique basename avoids a pytest import-file collision.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

import scripts.countercyclical_bridge as cb


def _write_history(path: Path, rows: list[tuple[str, float]]) -> None:
    lines = ["date,close"] + [f"{d},{c}" for d, c in rows]
    path.write_text("\n".join(lines) + "\n")


def test_load_excludes_rows_after_as_of(tmp_path: Path):
    csv = tmp_path / "spy.csv"
    _write_history(
        csv,
        [
            ("2026-05-29", 100.0),
            ("2026-06-01", 110.0),
            ("2026-06-02", 999.0),  # after as_of — must NOT be read
        ],
    )
    closes = cb.load_market_prices(csv, date(2026, 6, 1), window=252)
    assert closes == [100.0, 110.0]  # 999.0 excluded; oldest->newest order


def test_load_returns_oldest_to_newest_even_if_csv_unsorted(tmp_path: Path):
    csv = tmp_path / "spy.csv"
    _write_history(
        csv,
        [
            ("2026-06-01", 110.0),
            ("2026-05-29", 100.0),  # out of order in the file
            ("2026-05-30", 105.0),
        ],
    )
    closes = cb.load_market_prices(csv, None, window=252)
    assert closes == [100.0, 105.0, 110.0]


def test_load_window_keeps_the_last_n_closes(tmp_path: Path):
    csv = tmp_path / "spy.csv"
    _write_history(
        csv,
        [
            ("2026-05-28", 100.0),
            ("2026-05-29", 101.0),
            ("2026-05-30", 102.0),
            ("2026-06-01", 103.0),
        ],
    )
    closes = cb.load_market_prices(csv, None, window=2)
    assert closes == [102.0, 103.0]  # trailing window, newest end


def test_load_then_drawdown_is_pit_correct(tmp_path: Path):
    # Peak (120) is AFTER as_of and must be excluded; within the cut, peak=110, last=99 -> 10% drawdown.
    csv = tmp_path / "spy.csv"
    _write_history(
        csv,
        [
            ("2026-05-29", 110.0),
            ("2026-06-01", 99.0),
            ("2026-06-05", 120.0),  # future peak — excluded by as_of
        ],
    )
    closes = cb.load_market_prices(csv, date(2026, 6, 1), window=252)
    assert cb.market_drawdown(closes) == pytest.approx(0.10)
