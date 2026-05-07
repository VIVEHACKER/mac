from __future__ import annotations

from datetime import date

from data.delistings import load_delisting_returns_csv
from data.fundamentals_csv import load_fundamentals_csv
from data.universe import load_universe_members_csv


def test_load_universe_members_csv(tmp_path) -> None:
    path = tmp_path / "universe.csv"
    path.write_text(
        "universe,symbol,market,start_date,end_date,source,confidence\n"
        "TEST,QQQ,us,2008-01-01,,manual,high\n",
        encoding="utf-8",
    )

    rows = load_universe_members_csv(path)

    assert rows[0].universe == "TEST"
    assert rows[0].symbol == "QQQ"
    assert rows[0].start_date == date(2008, 1, 1)
    assert rows[0].end_date is None


def test_load_delisting_returns_csv(tmp_path) -> None:
    path = tmp_path / "delistings.csv"
    path.write_text(
        "symbol,market,ts,return_pct,source,confidence\n"
        "OLD,us,2020-06-01,-1.0,crsp,high\n",
        encoding="utf-8",
    )

    rows = load_delisting_returns_csv(path)

    assert rows[0].symbol == "OLD"
    assert rows[0].ts == date(2020, 6, 1)
    assert rows[0].return_pct == -1.0


def test_load_fundamentals_csv(tmp_path) -> None:
    path = tmp_path / "fundamentals.csv"
    path.write_text(
        "symbol,market,period_end,asof_ts,net_income,free_cash_flow,total_equity,total_debt,shares_out,source\n"
        "AAA,us,2024-12-31,2025-02-15T09:30:00,100,80,500,50,10,filing\n",
        encoding="utf-8",
    )

    rows = load_fundamentals_csv(path)

    assert rows[0].symbol == "AAA"
    assert rows[0].period_end == date(2024, 12, 31)
    assert rows[0].net_income == 100
    assert rows[0].shares_out == 10
