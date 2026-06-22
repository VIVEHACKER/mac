"""Tests for the free yfinance revision adapter's pure mapping (scripts/revisions_yf.py).

Network fetch is not unit-tested; this pins the frames -> EstimateRevision mapping on constructed
pandas frames (the shape yfinance 1.4.1 returns).
"""

from __future__ import annotations

from datetime import date

import pandas as pd

import scripts.revisions_yf as yfa

AS_OF = date(2026, 6, 23)


def _eps_trend() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "current": [8.80, 2.00],
            "7daysAgo": [8.79, 2.01],
            "30daysAgo": [8.50, 1.98],
            "60daysAgo": [8.40, 1.95],
            "90daysAgo": [8.30, 1.90],
        },
        index=["0y", "+1y"],
    )


def _eps_revisions() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "upLast7days": [1, 0],
            "upLast30days": [35, 20],
            "downLast30days": [2, 3],
            "downLast7Days": [1, 0],
        },
        index=["0y", "+1y"],
    )


def test_mapping_30d_window_0y():
    r = yfa.revision_from_yf_frames(
        "aapl", _eps_trend(), _eps_revisions(), {"mean": 314.4}, as_of=AS_OF
    )
    assert r is not None
    assert r.symbol == "AAPL"
    assert r.eps_estimate == 8.80 and r.eps_estimate_prev == 8.50  # current vs 30daysAgo
    assert r.n_up == 35 and r.n_down == 2 and r.n_total == 37
    assert r.target_price == 314.4 and r.target_price_prev is None  # no free prev


def test_mapping_7d_window_uses_7day_cols():
    r = yfa.revision_from_yf_frames(
        "aapl", _eps_trend(), _eps_revisions(), None, as_of=AS_OF, window="7d"
    )
    assert r.eps_estimate_prev == 8.79  # 7daysAgo
    assert r.n_up == 1 and r.n_down == 1  # upLast7days / downLast7Days
    assert r.target_price is None  # no price_targets passed


def test_mapping_missing_period_returns_none():
    assert (
        yfa.revision_from_yf_frames(
            "X", _eps_trend(), _eps_revisions(), None, as_of=AS_OF, period="0q"
        )
        is None
    )


def test_mapping_feeds_revision_signals_real_shape():
    # the mapped record flows through the real signal (eps +3.5%, breadth (35-2)/37)
    from signals.revisions import revision_signals

    r = yfa.revision_from_yf_frames(
        "AAA", _eps_trend(), _eps_revisions(), {"mean": 100.0}, as_of=AS_OF
    )
    sigs = revision_signals([r])
    assert sigs[0].symbol == "AAA" and sigs[0].direction == "up"


def _rec(sym: str, eps: float, eps_prev: float, n_up: int, n_down: int):
    from signals.revisions import EstimateRevision

    return EstimateRevision(
        symbol=sym,
        market="us",
        as_of=AS_OF,
        target_price=None,
        target_price_prev=None,
        eps_estimate=eps,
        eps_estimate_prev=eps_prev,
        n_up=n_up,
        n_down=n_down,
        n_total=n_up + n_down,
    )


def test_record_appends_and_is_loadable_by_ic_driver(tmp_path):
    import scripts.revisions_ic as ric

    path = tmp_path / "rev.csv"
    revs = [_rec("AAA", 1.1, 1.0, 5, 0), _rec("BBB", 0.9, 1.0, 0, 4)]
    assert yfa.append_revisions_csv(path, revs) == 2
    loaded = ric.load_revisions_csv(path)  # round-trips through the IC driver's loader
    assert set(loaded) == {AS_OF}
    a = next(r for r in loaded[AS_OF] if r.symbol == "AAA")
    assert a.eps_estimate == 1.1 and a.eps_estimate_prev == 1.0 and a.n_up == 5


def test_record_refuses_duplicate_date(tmp_path):
    import pytest

    path = tmp_path / "rev.csv"
    yfa.append_revisions_csv(path, [_rec("AAA", 1.1, 1.0, 5, 0)])
    with pytest.raises(ValueError):
        yfa.append_revisions_csv(path, [_rec("BBB", 1.2, 1.0, 3, 0)])
