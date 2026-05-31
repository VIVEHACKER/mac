from __future__ import annotations

from scripts.fetch_sectors import sic_to_sector


def test_financials_range():
    assert sic_to_sector(6798) == "financials"  # REIT
    assert sic_to_sector(6021) == "financials"  # bank
    assert sic_to_sector(6411) == "financials"  # insurance
    assert sic_to_sector(6000) == "financials"
    assert sic_to_sector(6799) == "financials"


def test_non_financials():
    assert sic_to_sector(7372) == "tech"  # software
    assert sic_to_sector(2834) == "healthcare"  # pharma
    assert sic_to_sector(3721) == "industrials"  # aircraft
    assert sic_to_sector(5999) == "consumer"  # not financials (boundary just below)
    assert sic_to_sector(6800) == "other"  # just above financials range


def test_none_is_other():
    assert sic_to_sector(None) == "other"
