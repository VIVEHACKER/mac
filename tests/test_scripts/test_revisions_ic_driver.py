"""Tests for the revisions forward-IC driver helpers (scripts/revisions_ic.py).

The IC harness itself is covered by tests/test_signals/test_revisions.py. These pin the driver's pure
data-shaping: CSV -> EstimateRevision snapshots, and prices -> N-day forward returns (PIT: entry at/<=
snapshot date, forward strictly after).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

import scripts.revisions_ic as ric


def test_load_revisions_csv_groups_by_date(tmp_path: Path):
    csv = tmp_path / "rev.csv"
    csv.write_text(
        "date,symbol,target_price,target_price_prev,eps_estimate,eps_estimate_prev,n_up,n_down,n_total\n"
        "2026-01-31,AAA,110,100,1.1,1.0,3,0,5\n"
        "2026-01-31,BBB,90,100,0.9,1.0,0,2,5\n"
        "2026-02-28,AAA,120,110,1.2,1.1,4,0,6\n"
    )
    snaps = ric.load_revisions_csv(csv)
    assert set(snaps) == {date(2026, 1, 31), date(2026, 2, 28)}
    assert len(snaps[date(2026, 1, 31)]) == 2
    a = next(r for r in snaps[date(2026, 1, 31)] if r.symbol == "AAA")
    assert a.target_price == 110.0 and a.eps_estimate_prev == 1.0 and a.n_up == 3


def test_load_revisions_csv_missing_columns_raises(tmp_path: Path):
    csv = tmp_path / "bad.csv"
    csv.write_text("date,ticker\n2026-01-31,AAA\n")
    try:
        ric.load_revisions_csv(csv)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_forward_returns_pit_and_horizon():
    # daily prices; snapshot at 2026-01-31, fwd 21d -> entry close on/<= 1-31, sell first >= 2-21
    idx = pd.date_range("2026-01-01", periods=80, freq="D")
    prices = pd.DataFrame({"AAA": [100.0 + i for i in range(80)], "BBB": [50.0] * 80}, index=idx)
    snaps = {
        date(2026, 1, 31): [
            ric.EstimateRevision(
                symbol="AAA",
                market="us",
                as_of=date(2026, 1, 31),
                target_price=None,
                target_price_prev=None,
                eps_estimate=None,
                eps_estimate_prev=None,
                n_up=0,
                n_down=0,
                n_total=5,
            )
        ]
    }
    fwd = ric.forward_returns_from_prices(prices, snaps, fwd_days=21)
    # entry = close at 1-31 (index 30 -> 130.0); sell = first >= 2-21 (index 51 -> 151.0)
    assert fwd[date(2026, 1, 31)]["AAA"] == (151.0 / 130.0 - 1.0)


def test_forward_returns_skips_when_no_future_bar():
    idx = pd.date_range("2026-01-01", periods=10, freq="D")  # too short for a 21d horizon
    prices = pd.DataFrame({"AAA": [100.0 + i for i in range(10)]}, index=idx)
    snaps = {
        date(2026, 1, 5): [
            ric.EstimateRevision(
                symbol="AAA",
                market="us",
                as_of=date(2026, 1, 5),
                target_price=None,
                target_price_prev=None,
                eps_estimate=None,
                eps_estimate_prev=None,
                n_up=0,
                n_down=0,
                n_total=5,
            )
        ]
    }
    assert ric.forward_returns_from_prices(prices, snaps, fwd_days=21) == {}
