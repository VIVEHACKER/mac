from __future__ import annotations

from datetime import date

from scripts.fetch_latest_closes import latest_close_bar


def test_latest_close_bar_builds_pricebar():
    series = [(date(2026, 5, 27), 9.5), (date(2026, 5, 28), 10.25), (date(2026, 5, 29), None)]
    bar = latest_close_bar("AAA", series)
    assert bar is not None
    assert bar.symbol == "AAA"
    assert bar.close == 10.25  # last NON-None close
    assert bar.ts == date(2026, 5, 28)
    assert bar.market == "us"


def test_latest_close_bar_none_when_no_valid_close():
    assert latest_close_bar("BBB", [(date(2026, 5, 28), None)]) is None
    assert latest_close_bar("CCC", []) is None
