from __future__ import annotations

from datetime import UTC, datetime

import pytest

from data.catalog import MarketDataCatalog
from data.ingest.sec_form4 import (
    _form4_xml_url,
    _parse_acceptance,
    _recent_form4,
    ingest_form4,
)

_P_BUY_XML = (
    '<?xml version="1.0"?><ownershipDocument>'
    "<issuer><issuerTradingSymbol>AAPL</issuerTradingSymbol></issuer>"
    "<reportingOwner><reportingOwnerId><rptOwnerName>COOK TIMOTHY D</rptOwnerName>"
    "</reportingOwnerId><reportingOwnerRelationship><isOfficer>1</isOfficer>"
    "<officerTitle>CEO</officerTitle></reportingOwnerRelationship></reportingOwner>"
    "<nonDerivativeTable><nonDerivativeTransaction>"
    "<securityTitle><value>Common Stock</value></securityTitle>"
    "<transactionDate><value>2025-03-01</value></transactionDate>"
    "<transactionCoding><transactionCode>P</transactionCode></transactionCoding>"
    "<transactionAmounts><transactionShares><value>1000</value></transactionShares>"
    "<transactionPricePerShare><value>150</value></transactionPricePerShare>"
    "</transactionAmounts></nonDerivativeTransaction></nonDerivativeTable>"
    "</ownershipDocument>"
)


def test_recent_form4_keeps_only_form4_and_amendments() -> None:
    subs = {
        "filings": {
            "recent": {
                "form": ["4", "10-Q", "4/A", "3"],
                "accessionNumber": ["a-4", "a-q", "a-4a", "a-3"],
                "acceptanceDateTime": ["t4", "tq", "t4a", "t3"],
                "primaryDocument": ["f4.xml", "q.htm", "f4a.xml", "f3.xml"],
            }
        }
    }
    out = _recent_form4(subs)
    assert [f["accession"] for f in out] == ["a-4", "a-4a"]
    assert out[0]["primary_doc"] == "f4.xml"


def test_form4_xml_url_dashless_accession_and_plain_cik() -> None:
    url = _form4_xml_url(320193, "0000320193-25-000077", "form4.xml")
    assert url == "https://www.sec.gov/Archives/edgar/data/320193/000032019325000077/form4.xml"


def test_form4_xml_url_strips_xsl_html_prefix_to_raw_xml() -> None:
    # primaryDocument frequently points at the XSL-rendered HTML view; the raw ownership XML is the
    # basename in the accession dir (real SEC behavior, caught by a live probe).
    url = _form4_xml_url(1318605, "0001104659-26-062860", "xslF345X06/tm2614845-1_4seq1.xml")
    assert url == (
        "https://www.sec.gov/Archives/edgar/data/1318605/000110465926062860/tm2614845-1_4seq1.xml"
    )


def test_parse_acceptance_utc() -> None:
    assert _parse_acceptance("2025-03-03T21:00:00.000Z") == datetime(2025, 3, 3, 21, tzinfo=UTC)


def test_ingest_form4_stores_buys_pit(tmp_path) -> None:
    cat = MarketDataCatalog(tmp_path / "cat.duckdb")
    subs = {
        "filings": {
            "recent": {
                "form": ["4"],
                "accessionNumber": ["0000320193-25-000077"],
                "acceptanceDateTime": ["2025-03-03T21:00:00.000Z"],
                "primaryDocument": ["form4.xml"],
            }
        }
    }

    n = ingest_form4(
        ["AAPL"],
        cat,
        cik_map={"AAPL": 320193},
        fetch_json=lambda url: subs,
        fetch_text=lambda url: _P_BUY_XML,
        sleep=lambda *_: None,
    )

    assert n == 1
    out = cat.get_insider_trades("AAPL")
    assert len(out) == 1
    assert out[0].txn_code == "P"
    assert out[0].value_usd == 150_000.0
    assert out[0].asof_ts.year == 2025  # acceptance time recorded as the PIT key
    # PIT: not visible before the filing was accepted
    assert cat.get_insider_trades("AAPL", as_of=datetime(2025, 3, 2, tzinfo=UTC)) == []


def test_ingest_form4_includes_archived_shards(tmp_path) -> None:
    """History ingest must include archived submission shards (filings.files), not just the recent
    block, or pre-cutoff insider data is silently missing for high-filing issuers (Codex P2)."""
    cat = MarketDataCatalog(tmp_path / "cat.duckdb")
    recent = {
        "filings": {
            "recent": {
                "form": ["4"],
                "accessionNumber": ["acc-recent"],
                "acceptanceDateTime": ["2025-03-03T21:00:00.000Z"],
                "primaryDocument": ["f4.xml"],
            },
            "files": [{"name": "CIK-shard-001.json"}],
        }
    }
    shard = {
        "form": ["4"],
        "accessionNumber": ["acc-old"],
        "acceptanceDateTime": ["2015-06-01T18:00:00.000Z"],
        "primaryDocument": ["old4.xml"],
    }
    old_xml = _P_BUY_XML.replace("2025-03-01", "2015-06-01")  # a distinct historical transaction

    n = ingest_form4(
        ["AAPL"],
        cat,
        cik_map={"AAPL": 320193},
        fetch_json=lambda url: shard if "shard-001" in url else recent,
        fetch_text=lambda url: old_xml if "old4.xml" in url else _P_BUY_XML,
        sleep=lambda *_: None,
    )
    assert n == 2  # recent + archived
    years = sorted(r.txn_date.year for r in cat.get_insider_trades("AAPL"))
    assert years == [2015, 2025]


def test_ingest_form4_skips_legacy_non_xml_and_surfaces_it(tmp_path, caplog) -> None:
    """Pre-2003 Form 4s have an HTML/TXT primaryDocument; they must be skipped explicitly (logged),
    not silently fed to the XML parser and dropped (Codex P2)."""
    import logging

    cat = MarketDataCatalog(tmp_path / "cat.duckdb")
    fetched: list[str] = []

    def fake_text(url: str) -> str:
        fetched.append(url)
        return _P_BUY_XML

    subs = {
        "filings": {
            "recent": {
                "form": ["4", "4"],
                "accessionNumber": ["acc-modern", "acc-legacy"],
                "acceptanceDateTime": ["2025-03-03T21:00:00.000Z", "2002-01-01T18:00:00.000Z"],
                "primaryDocument": ["f4.xml", "j8739_4.htm"],  # modern XML + legacy HTML
            }
        }
    }
    with caplog.at_level(logging.WARNING):
        n = ingest_form4(
            ["AAPL"],
            cat,
            cik_map={"AAPL": 320193},
            fetch_json=lambda url: subs,
            fetch_text=fake_text,
            sleep=lambda *_: None,
        )
    assert n == 1  # only the modern XML filing stored
    assert all("j8739_4.htm" not in u for u in fetched)  # legacy doc never fetched/parsed
    assert any("legacy non-XML" in r.message for r in caplog.records)  # skip surfaced


def test_ingest_form4_remaps_to_requested_ticker(tmp_path) -> None:
    """Multi-class issuer: requesting GOOG while the XML reports GOOGL must store under GOOG so the
    caller's get_insider_trades('GOOG') finds the records (Codex P2)."""
    cat = MarketDataCatalog(tmp_path / "cat.duckdb")
    googl_xml = _P_BUY_XML.replace("AAPL", "GOOGL")
    subs = {
        "filings": {
            "recent": {
                "form": ["4"],
                "accessionNumber": ["0001652044-25-000001"],
                "acceptanceDateTime": ["2025-03-03T21:00:00.000Z"],
                "primaryDocument": ["form4.xml"],
            }
        }
    }
    n = ingest_form4(
        ["GOOG"],
        cat,
        cik_map={"GOOG": 1652044},
        fetch_json=lambda url: subs,
        fetch_text=lambda url: googl_xml,
        sleep=lambda *_: None,
    )
    assert n == 1
    assert len(cat.get_insider_trades("GOOG")) == 1  # stored under the requested ticker
    assert cat.get_insider_trades("GOOGL") == []


def test_ingest_form4_throttles_even_with_no_filings(tmp_path) -> None:
    """A ticker with no Form 4 filings must still throttle after the submissions request so a
    universe run does not hammer SEC (Codex P2)."""
    cat = MarketDataCatalog(tmp_path / "cat.duckdb")
    sleeps: list[float] = []
    empty = {
        "filings": {
            "recent": {
                "form": ["10-K"],
                "accessionNumber": ["x"],
                "acceptanceDateTime": ["t"],
                "primaryDocument": ["d.htm"],
            }
        }
    }
    ingest_form4(
        ["AAPL"],
        cat,
        cik_map={"AAPL": 320193},
        fetch_json=lambda url: empty,
        fetch_text=lambda url: "",
        sleep=sleeps.append,
    )
    assert sleeps  # throttled at least once despite zero Form 4 filings




@pytest.mark.parametrize("caller_ticker", ["BRK-B", "BRK.B"])
def test_ingest_form4_resolves_share_class_alias_either_way(tmp_path, caller_ticker) -> None:
    """Whatever convention the caller uses (hyphen BRK-B or dot BRK.B), it must resolve to the
    real SEC CIK-map key (hyphenated, BRK-B) — not be silently skipped (Codex P2)."""
    cat = MarketDataCatalog(tmp_path / "cat.duckdb")
    subs = {
        "filings": {
            "recent": {
                "form": ["4"],
                "accessionNumber": ["acc-1"],
                "acceptanceDateTime": ["2025-03-03T21:00:00.000Z"],
                "primaryDocument": ["f4.xml"],
            }
        }
    }
    n = ingest_form4(
        [caller_ticker],
        cat,
        cik_map={"BRK-B": 1067983},  # real SEC company_tickers.json uses the hyphen form
        fetch_json=lambda url: subs,
        fetch_text=lambda url: _P_BUY_XML.replace("AAPL", "BRKB"),
        sleep=lambda *_: None,
    )
    assert n == 1
    assert len(cat.get_insider_trades(caller_ticker)) == 1  # stored under the requested ticker


def test_ingest_form4_skips_unknown_ticker(tmp_path) -> None:
    cat = MarketDataCatalog(tmp_path / "cat.duckdb")
    calls: list[str] = []

    def record_json(url: str) -> dict:
        calls.append(url)  # list.append returns None; a helper keeps this mypy-clean (Codex P2)
        return {}

    n = ingest_form4(
        ["NOPE"],
        cat,
        cik_map={"AAPL": 320193},
        fetch_json=record_json,
        fetch_text=lambda url: "",
        sleep=lambda *_: None,
    )
    assert n == 0
    assert calls == []  # no network for an unmapped ticker
