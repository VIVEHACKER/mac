"""PIT discipline tests for the core-basket driver (scripts/core_basket.py).

The engine (engine/core_basket.py) is covered by tests/test_engine/test_core_basket.py.
This file pins the look-ahead-sensitive driver code — build_universe()/_price_asof() — where a
single off-by-one (`<` vs `<=`, latest-price-with-stale-fundamentals, no coverage guard) would leak
future information into an as_of cut and silently inflate any backtest built on this sleeve.
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import pytest

import scripts.core_basket as cb
from data.models import FundamentalRecord


def _rec(symbol: str, asof: datetime) -> FundamentalRecord:
    return FundamentalRecord(
        symbol=symbol,
        market="us",
        period_end=asof.date(),
        asof_ts=asof,
        revenue=100.0,
    )


def _setup(tmp_path, monkeypatch, *, funds, closes, symbols, sectors=None):
    """Wire crafted in-memory snapshots into build_universe and return its path kwargs."""
    snap = tmp_path / "fund.csv"
    snap.write_text("placeholder")
    prices = tmp_path / "prices.csv"
    prices.write_text("placeholder")
    uni = tmp_path / "uni.csv"
    uni.write_text("symbol\n" + "\n".join(symbols) + "\n")
    sec = tmp_path / "sec.csv"
    sec_map = sectors or dict.fromkeys(symbols, "technology")
    sec.write_text(
        "symbol,sector\n" + "\n".join(f"{s},{sec_map.get(s, 'technology')}" for s in symbols) + "\n"
    )
    flat = [r for recs in funds.values() for r in recs]
    monkeypatch.setattr(cb, "read_fundamentals_snapshot", lambda _p, verify=True: flat)
    monkeypatch.setattr(cb, "read_price_snapshot", lambda _p, verify=True: closes)
    return {"snapshot": snap, "prices": prices, "universe_csv": uni, "sectors_csv": sec}


# ------------------------------------------------------------------ _price_asof
def test_price_asof_returns_last_close_on_or_before() -> None:
    closes = pd.DataFrame(
        {"AAA": [10.0, 11.0, 12.0]},
        index=pd.to_datetime(["2020-01-02", "2020-01-03", "2020-01-06"]),
    )
    assert cb._price_asof(closes, "AAA", date(2020, 1, 3)) == 11.0
    # a date with no bar resolves to the last close strictly before it (no peeking ahead to 01-06)
    assert cb._price_asof(closes, "AAA", date(2020, 1, 5)) == 11.0


def test_price_asof_skips_trailing_nan() -> None:
    closes = pd.DataFrame(
        {"AAA": [10.0, float("nan")]},
        index=pd.to_datetime(["2020-01-02", "2020-01-03"]),
    )
    # dropna before the as_of slice -> the stale 01-02 close is used, not the NaN
    assert cb._price_asof(closes, "AAA", date(2020, 1, 3)) == 10.0


def test_price_asof_none_for_nonpositive_or_missing_symbol() -> None:
    closes = pd.DataFrame({"AAA": [0.0]}, index=pd.to_datetime(["2020-01-02"]))
    assert cb._price_asof(closes, "AAA", date(2020, 1, 2)) is None  # non-positive close
    assert cb._price_asof(closes, "ZZZ", date(2020, 1, 2)) is None  # symbol absent
    # as_of before any bar -> empty slice -> None
    closes2 = pd.DataFrame({"AAA": [10.0]}, index=pd.to_datetime(["2020-06-30"]))
    assert cb._price_asof(closes2, "AAA", date(2020, 1, 1)) is None


# --------------------------------------------------------------- build_universe
def test_default_as_of_resolves_to_cov_max_for_both_legs(tmp_path, monkeypatch) -> None:
    closes = pd.DataFrame({"AAA": [10.0, 11.0]}, index=pd.to_datetime(["2020-01-02", "2020-06-30"]))
    # The third record is dated AFTER cov_max. The resolved (None -> cov_max) cutoff must apply to
    # fundamentals too — if as_of=None ever regressed to "latest price + ALL fundamentals", this
    # post-cutoff record would leak in. Pin its exclusion so that regression fails right here.
    funds = {
        "AAA": [
            _rec("AAA", datetime(2019, 1, 1)),
            _rec("AAA", datetime(2020, 3, 31)),
            _rec("AAA", datetime(2020, 9, 30)),  # > cov_max (2020-06-30) -> must be excluded
        ]
    }
    kw = _setup(tmp_path, monkeypatch, funds=funds, closes=closes, symbols=["AAA"])
    universe, _sectors, effective = cb.build_universe(as_of=None, **kw)
    assert effective == date(2020, 6, 30)  # the snapshot's natural "now"
    recs, price = universe["AAA"]
    assert price == 11.0  # last close on/before the resolved cutoff
    asofs = sorted(r.asof_ts.date() for r in recs)
    assert asofs == [date(2019, 1, 1), date(2020, 3, 31)]  # 2020-09-30 leaked out -> excluded


def test_fundamentals_boundary_is_inclusive(tmp_path, monkeypatch) -> None:
    # A record dated EXACTLY on as_of is visible (a filing public on day D is usable at D's close);
    # a record dated one day later is not. Pin both directions so a flip to `<` is caught.
    closes = pd.DataFrame({"AAA": [10.0, 11.0]}, index=pd.to_datetime(["2020-01-02", "2020-06-30"]))
    funds = {
        "AAA": [
            _rec("AAA", datetime(2020, 1, 1)),
            _rec("AAA", datetime(2020, 6, 30)),  # == cutoff -> included
            _rec("AAA", datetime(2020, 7, 1)),  # > cutoff -> excluded (future leak)
        ]
    }
    kw = _setup(tmp_path, monkeypatch, funds=funds, closes=closes, symbols=["AAA"])
    universe, _sectors, _eff = cb.build_universe(as_of=date(2020, 6, 30), **kw)
    asofs = sorted(r.asof_ts.date() for r in universe["AAA"][0])
    assert asofs == [date(2020, 1, 1), date(2020, 6, 30)]


def test_drops_symbols_with_fewer_than_two_in_window_records(tmp_path, monkeypatch) -> None:
    closes = pd.DataFrame({"AAA": [10.0], "BBB": [20.0]}, index=pd.to_datetime(["2020-06-30"]))
    funds = {
        "AAA": [_rec("AAA", datetime(2020, 1, 1)), _rec("AAA", datetime(2020, 3, 1))],
        "BBB": [_rec("BBB", datetime(2020, 1, 1))],  # only one record
    }
    kw = _setup(tmp_path, monkeypatch, funds=funds, closes=closes, symbols=["AAA", "BBB"])
    universe, _sectors, _eff = cb.build_universe(as_of=date(2020, 6, 30), **kw)
    assert "AAA" in universe and "BBB" not in universe


def test_drops_symbol_without_in_window_price(tmp_path, monkeypatch) -> None:
    # CCC has enough fundamentals but no price column -> _price_asof None -> excluded.
    closes = pd.DataFrame({"AAA": [10.0, 11.0]}, index=pd.to_datetime(["2020-01-02", "2020-06-30"]))
    funds = {
        "AAA": [_rec("AAA", datetime(2020, 1, 1)), _rec("AAA", datetime(2020, 3, 1))],
        "CCC": [_rec("CCC", datetime(2020, 1, 1)), _rec("CCC", datetime(2020, 3, 1))],
    }
    kw = _setup(tmp_path, monkeypatch, funds=funds, closes=closes, symbols=["AAA", "CCC"])
    universe, _sectors, _eff = cb.build_universe(as_of=date(2020, 6, 30), **kw)
    assert "AAA" in universe and "CCC" not in universe


def test_rejects_as_of_outside_price_coverage(tmp_path, monkeypatch) -> None:
    closes = pd.DataFrame({"AAA": [10.0, 11.0]}, index=pd.to_datetime(["2020-01-02", "2020-06-30"]))
    funds = {"AAA": [_rec("AAA", datetime(2019, 1, 1)), _rec("AAA", datetime(2020, 3, 1))]}
    kw = _setup(tmp_path, monkeypatch, funds=funds, closes=closes, symbols=["AAA"])
    with pytest.raises(ValueError, match="outside the price snapshot coverage"):
        cb.build_universe(as_of=date(2019, 1, 1), **kw)  # before cov_min
    with pytest.raises(ValueError, match="outside the price snapshot coverage"):
        cb.build_universe(as_of=date(2021, 1, 1), **kw)  # after cov_max
