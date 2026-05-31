from __future__ import annotations

import argparse

from scripts.sec_edgar_ingest import extract_concept, resolve_tickers


def _args(**kw) -> argparse.Namespace:
    base = {"universe_csv": None, "tickers": None}
    base.update(kw)
    return argparse.Namespace(**base)


def test_resolve_from_tickers_flag():
    assert resolve_tickers(_args(tickers="aaa, bbb,CCC")) == ["AAA", "BBB", "CCC"]


def test_resolve_default_is_megacaps():
    from scripts.sec_edgar_ingest import TICKERS

    assert resolve_tickers(_args()) == TICKERS


def test_extract_concept_merges_across_tags():
    facts = {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {
                                "form": "10-K",
                                "end": "2017-12-31",
                                "filed": "2018-02-01",
                                "val": 100.0,
                            }
                        ]
                    }
                },
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {
                        "USD": [
                            {
                                "form": "10-K",
                                "end": "2024-12-31",
                                "filed": "2025-02-01",
                                "val": 200.0,
                            }
                        ]
                    }
                },
            }
        }
    }
    result = extract_concept(
        facts,
        ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"],
        "USD",
    )
    assert "2017-12-31" in result, "Old tag period must be present"
    assert "2024-12-31" in result, "New tag period must be present"
    assert result["2017-12-31"][0] == 100.0
    assert result["2024-12-31"][0] == 200.0


def test_extract_concept_earliest_filed_wins_on_period_conflict():
    facts = {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {
                                "form": "10-K",
                                "end": "2018-12-31",
                                "filed": "2019-03-01",
                                "val": 100.0,
                            }
                        ]
                    }
                },
                "SalesRevenueNet": {
                    "units": {
                        "USD": [
                            {
                                "form": "10-K",
                                "end": "2018-12-31",
                                "filed": "2019-01-15",
                                "val": 111.0,
                            }
                        ]
                    }
                },
            }
        }
    }
    result = extract_concept(facts, ["Revenues", "SalesRevenueNet"], "USD")
    assert "2018-12-31" in result
    assert result["2018-12-31"][0] == 111.0, "Earliest filed (2019-01-15) must win"


def test_extract_concept_ignores_non_10kq_forms():
    facts = {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {
                                "form": "8-K",
                                "end": "2023-12-31",
                                "filed": "2024-01-10",
                                "val": 999.0,
                            }
                        ]
                    }
                }
            }
        }
    }
    result = extract_concept(facts, ["Revenues"], "USD")
    assert result == {}, "8-K entries must be excluded"


def test_resolve_from_universe_csv(tmp_path):
    csv_path = tmp_path / "u.csv"
    csv_path.write_text(
        "universe,symbol,market,start_date,end_date,source,confidence,"
        "asset_class,asset_subclass,role\n"
        "SP400_600_CURRENT,AAA,us,2026-05-31,,ishares,medium,equity,us-mid-cap,risk\n"
        "SP400_600_CURRENT,BBB,us,2026-05-31,,ishares,medium,equity,us-small-cap,risk\n",
        encoding="utf-8",
    )
    assert resolve_tickers(_args(universe_csv=csv_path)) == ["AAA", "BBB"]


def _facts_with(**tag_vals) -> dict:
    """Build a companyfacts dict: tag -> {period_end: (val, unit)}."""
    us_gaap = {}
    for tag, (val, unit, end, filed) in tag_vals.items():
        us_gaap[tag] = {"units": {unit: [{"form": "10-K", "end": end, "filed": filed, "val": val}]}}
    return {"facts": {"us-gaap": us_gaap}}


def test_concept_tags_include_gross_and_assets():
    from scripts.sec_edgar_ingest import CONCEPT_TAGS

    assert "gross_profit" in CONCEPT_TAGS
    assert "cost_of_revenue" in CONCEPT_TAGS
    assert "assets" in CONCEPT_TAGS
    assert "operating_income" in CONCEPT_TAGS
    assert "GrossProfit" in CONCEPT_TAGS["gross_profit"]
    assert "Assets" in CONCEPT_TAGS["assets"]


def test_records_from_facts_populates_gross_profit_and_assets():
    from scripts.sec_edgar_ingest import records_from_facts

    facts = _facts_with(
        Revenues=(200.0, "USD", "2023-12-31", "2024-02-01"),
        GrossProfit=(140.0, "USD", "2023-12-31", "2024-02-01"),
        Assets=(400.0, "USD", "2023-12-31", "2024-02-01"),
        OperatingIncomeLoss=(60.0, "USD", "2023-12-31", "2024-02-01"),
    )
    recs = records_from_facts("NVDA", facts)
    assert len(recs) == 1
    r = recs[0]
    assert r.gross_profit == 140.0
    assert r.total_assets == 400.0
    assert r.operating_income == 60.0


def test_records_from_facts_derives_gross_profit_from_cogs():
    from scripts.sec_edgar_ingest import records_from_facts

    facts = _facts_with(
        Revenues=(200.0, "USD", "2023-12-31", "2024-02-01"),
        CostOfRevenue=(130.0, "USD", "2023-12-31", "2024-02-01"),
        Assets=(350.0, "USD", "2023-12-31", "2024-02-01"),
    )
    recs = records_from_facts("T", facts)
    assert len(recs) == 1
    # gross_profit derived = revenue - cogs = 70; cost_of_revenue stored raw
    assert recs[0].gross_profit == 70.0
    assert recs[0].cost_of_revenue == 130.0
