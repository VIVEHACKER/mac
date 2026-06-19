"""PIT discipline tests for the hunt-basket driver (scripts/hunt_basket.py).

The engine is covered by tests/test_engine/test_hunt_basket.py. This pins the look-ahead-sensitive
driver assembly — build_hunt_inputs()/_price_asof() — where a single off-by-one (the insider cutoff,
`<` vs `<=` on fundamentals, latest-price-with-stale-fundamentals, no coverage guard) would leak
future information into an as_of cut. Unique basename avoids a pytest import-file collision with
tests/test_engine/test_hunt_basket.py.
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import pytest

import scripts.hunt_basket as hb
from data.models import FundamentalRecord, InsiderTradeRecord


def _fund(symbol: str, asof: datetime) -> FundamentalRecord:
    return FundamentalRecord(
        symbol=symbol, market="us", period_end=asof.date(), asof_ts=asof, revenue=100.0
    )


def _buy(symbol: str, asof_ts: datetime, txn_date: date) -> InsiderTradeRecord:
    return InsiderTradeRecord(
        symbol=symbol,
        market="us",
        txn_date=txn_date,
        asof_ts=asof_ts,
        insider_name="JANE CEO",
        insider_role="CEO",
        txn_code="P",
        shares=1000.0,
        price=10.0,
        value_usd=10_000.0,
        source="test",
    )


class _FakeCatalog:
    """Records the as_of each get_insider_trades is called with; returns crafted trades per symbol."""

    def __init__(self, trades_by_sym: dict[str, list[InsiderTradeRecord]]):
        self.trades_by_sym = trades_by_sym
        self.calls: list[tuple[str, datetime | None]] = []

    def get_insider_trades(self, symbol, market="us", as_of=None, limit=0):
        self.calls.append((symbol, as_of))
        return list(self.trades_by_sym.get(symbol, []))


def _setup(tmp_path, monkeypatch, *, funds, closes, symbols):
    snap = tmp_path / "fund.csv"
    snap.write_text("placeholder")
    prices = tmp_path / "prices.csv"
    prices.write_text("placeholder")
    uni = tmp_path / "uni.csv"
    uni.write_text("symbol\n" + "\n".join(symbols) + "\n")
    sec = tmp_path / "sec.csv"
    sec.write_text("symbol,sector\n" + "\n".join(f"{s},technology" for s in symbols) + "\n")
    flat = [r for recs in funds.values() for r in recs]
    monkeypatch.setattr(hb, "read_fundamentals_snapshot", lambda _p, verify=True: flat)
    monkeypatch.setattr(hb, "read_price_snapshot", lambda _p, verify=True: closes)
    return {"snapshot": snap, "prices": prices, "universe_csv": uni, "sectors_csv": sec}


# ------------------------------------------------------------------ _price_asof
def test_price_asof_last_close_on_or_before() -> None:
    closes = pd.DataFrame(
        {"AAA": [10.0, 11.0, 12.0]},
        index=pd.to_datetime(["2020-01-02", "2020-01-03", "2020-01-06"]),
    )
    assert hb._price_asof(closes, "AAA", date(2020, 1, 3)) == 11.0
    assert hb._price_asof(closes, "AAA", date(2020, 1, 5)) == 11.0  # no peek ahead to 01-06


def test_price_asof_none_for_nonpositive_missing_or_before_coverage() -> None:
    closes = pd.DataFrame({"AAA": [0.0]}, index=pd.to_datetime(["2020-01-02"]))
    assert hb._price_asof(closes, "AAA", date(2020, 1, 2)) is None  # non-positive
    assert hb._price_asof(closes, "ZZZ", date(2020, 1, 2)) is None  # absent symbol
    closes2 = pd.DataFrame({"AAA": [10.0]}, index=pd.to_datetime(["2020-06-30"]))
    assert hb._price_asof(closes2, "AAA", date(2020, 1, 1)) is None  # before any bar


# --------------------------------------------------------------- build_hunt_inputs
def test_insider_fetched_at_effective_eod_microsecond_cutoff(tmp_path, monkeypatch) -> None:
    # The insider PIT leg: trades must be fetched with as_of = effective EOD (23:59:59.999999) so a
    # same-day late-evening filing the signal funcs would include is not dropped at the catalog.
    closes = pd.DataFrame({"AAA": [10.0, 11.0]}, index=pd.to_datetime(["2020-01-02", "2020-06-30"]))
    funds = {"AAA": [_fund("AAA", datetime(2020, 1, 1)), _fund("AAA", datetime(2020, 3, 1))]}
    cat = _FakeCatalog({})
    kw = _setup(tmp_path, monkeypatch, funds=funds, closes=closes, symbols=["AAA"])
    hb.build_hunt_inputs(catalog=cat, as_of=date(2020, 6, 30), **kw)
    assert cat.calls == [("AAA", datetime(2020, 6, 30, 23, 59, 59, 999999))]


def test_default_as_of_resolves_to_cov_max_for_all_legs(tmp_path, monkeypatch) -> None:
    closes = pd.DataFrame({"AAA": [10.0, 11.0]}, index=pd.to_datetime(["2020-01-02", "2020-06-30"]))
    # third fundamental is AFTER cov_max: a None->cov_max regression that applied the cut to prices
    # only (keeping all fundamentals) would leak it in. Pin its exclusion.
    funds = {
        "AAA": [
            _fund("AAA", datetime(2020, 1, 1)),
            _fund("AAA", datetime(2020, 3, 1)),
            _fund("AAA", datetime(2020, 9, 30)),  # > cov_max -> must be excluded
        ]
    }
    cat = _FakeCatalog({})
    kw = _setup(tmp_path, monkeypatch, funds=funds, closes=closes, symbols=["AAA"])
    _ins, _cap, universe, _sec, effective = hb.build_hunt_inputs(catalog=cat, as_of=None, **kw)
    assert effective == date(2020, 6, 30)  # snapshot's natural "now"
    assert cat.calls[0][1] == datetime(2020, 6, 30, 23, 59, 59, 999999)  # insider cut = cov_max EOD
    recs, price = universe["AAA"]
    assert price == 11.0
    assert sorted(r.asof_ts.date() for r in recs) == [date(2020, 1, 1), date(2020, 3, 1)]


def test_fundamentals_boundary_inclusive(tmp_path, monkeypatch) -> None:
    closes = pd.DataFrame({"AAA": [10.0, 11.0]}, index=pd.to_datetime(["2020-01-02", "2020-06-30"]))
    funds = {
        "AAA": [
            _fund("AAA", datetime(2020, 1, 1)),
            _fund("AAA", datetime(2020, 6, 30)),  # == cutoff -> included
            _fund("AAA", datetime(2020, 7, 1)),  # > cutoff -> excluded
        ]
    }
    cat = _FakeCatalog({})
    kw = _setup(tmp_path, monkeypatch, funds=funds, closes=closes, symbols=["AAA"])
    _ins, _cap, universe, _sec, _eff = hb.build_hunt_inputs(
        catalog=cat, as_of=date(2020, 6, 30), **kw
    )
    assert sorted(r.asof_ts.date() for r in universe["AAA"][0]) == [
        date(2020, 1, 1),
        date(2020, 6, 30),
    ]


def test_rejects_as_of_outside_price_coverage(tmp_path, monkeypatch) -> None:
    closes = pd.DataFrame({"AAA": [10.0, 11.0]}, index=pd.to_datetime(["2020-01-02", "2020-06-30"]))
    funds = {"AAA": [_fund("AAA", datetime(2019, 1, 1)), _fund("AAA", datetime(2020, 3, 1))]}
    cat = _FakeCatalog({})
    kw = _setup(tmp_path, monkeypatch, funds=funds, closes=closes, symbols=["AAA"])
    with pytest.raises(ValueError, match="outside price coverage"):
        hb.build_hunt_inputs(catalog=cat, as_of=date(2019, 1, 1), **kw)
    with pytest.raises(ValueError, match="outside price coverage"):
        hb.build_hunt_inputs(catalog=cat, as_of=date(2021, 1, 1), **kw)


def test_eligible_on_insider_alone_stays_out_of_universe(tmp_path, monkeypatch) -> None:
    # A name with an insider buy but NO in-window fundamentals/price gets a long insider signal yet
    # is absent from universe AND capital_signals (the symmetry the engine relies on): huntable on
    # insider alone, but with empty fundamentals downstream.
    closes = pd.DataFrame({"AAA": [10.0, 11.0]}, index=pd.to_datetime(["2020-01-02", "2020-06-30"]))
    funds = {"AAA": [_fund("AAA", datetime(2020, 1, 1)), _fund("AAA", datetime(2020, 3, 1))]}
    # NOFUND has a recent P buy but no fundamentals and no price column.
    trades = {"NOFUND": [_buy("NOFUND", datetime(2020, 6, 1), date(2020, 5, 29))]}
    cat = _FakeCatalog(trades)
    kw = _setup(tmp_path, monkeypatch, funds=funds, closes=closes, symbols=["AAA", "NOFUND"])
    insider, capital, universe, _sec, _eff = hb.build_hunt_inputs(
        catalog=cat, as_of=date(2020, 6, 30), **kw
    )
    assert insider["NOFUND"] is not None and insider["NOFUND"].direction == "long"
    assert "NOFUND" not in universe  # no fundamentals/price
    assert "NOFUND" not in capital  # symmetric with universe
    assert "AAA" in universe  # fully-evaluated name does enter universe + capital legs
    assert "AAA" in capital
