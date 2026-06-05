from __future__ import annotations

import copy
from datetime import UTC, datetime

from data.catalog import MarketDataCatalog
from data.ingest.sec_13f import _infotable_filename, _parse_acceptance, ingest_13f

_NS = "http://www.sec.gov/edgar/document/thirteenf/informationtable"


def _row(issuer: str, cusip: str, shares: int, put_call: str = "") -> str:
    pc = f"<putCall>{put_call}</putCall>" if put_call else ""
    return (
        f"<infoTable><nameOfIssuer>{issuer}</nameOfIssuer><titleOfClass>COM</titleOfClass>"
        f"<cusip>{cusip}</cusip>{pc}<value>1000000</value>"
        f"<shrsOrPrnAmt><sshPrnamt>{shares}</sshPrnamt><sshPrnamtType>SH</sshPrnamtType>"
        f"</shrsOrPrnAmt></infoTable>"
    )


def _table(rows: str) -> str:
    return f'<?xml version="1.0"?><informationTable xmlns="{_NS}">{rows}</informationTable>'


def _cover(amendment_type: str) -> str:
    return (
        '<?xml version="1.0"?><edgarSubmission><formData><coverPage><amendmentInfo>'
        f"<amendmentType>{amendment_type}</amendmentType>"
        "</amendmentInfo></coverPage></formData></edgarSubmission>"
    )


# CIK 1067983 = Berkshire. Two 13F filings for the same quarter (original + amendment) plus a 10-K.
_SUBMISSIONS = {
    "filings": {
        "recent": {
            "form": ["13F-HR", "13F-HR/A", "10-K"],
            "accessionNumber": [
                "0001067983-25-000001",
                "0001067983-25-000002",
                "0001067983-25-000003",
            ],
            "acceptanceDateTime": [
                "2025-05-15T16:00:00.000Z",
                "2025-05-20T16:00:00.000Z",
                "2025-02-01T16:00:00.000Z",
            ],
            "reportDate": ["2025-03-31", "2025-03-31", "2024-12-31"],
            # The cover page is primary_doc.xml — NOT the holdings table (the real-world gotcha).
            "primaryDocument": ["primary_doc.xml", "primary_doc.xml", "brka-10k.htm"],
        }
    }
}

_INDEX = {"directory": {"item": [{"name": "primary_doc.xml"}, {"name": "form13fInfoTable.xml"}]}}
_INFO_HR = _table(_row("APPLE INC", "037833100", 5000) + _row("MICROSOFT CORP", "594918104", 3000))
_INFO_HRA = _table(_row("APPLE INC", "037833100", 6000))  # restatement: drops MSFT, raises AAPL

_CUSIP_MAP = {"037833100": "AAPL", "594918104": "MSFT", "67066G104": "NVDA"}
_CIK_MAP = {"BERKSHIRE HATHAWAY INC": 1067983}


def _route_json(url: str) -> dict:
    if "submissions/CIK" in url:
        return _SUBMISSIONS
    if "000001/index.json" in url or "000002/index.json" in url:
        return _INDEX
    return {}


def _route_text(url: str) -> str:
    if url.endswith("primary_doc.xml"):
        return _cover("RESTATEMENT")  # only the amendment fetches its cover
    if "000001/" in url:
        return _INFO_HR
    if "000002/" in url:
        return _INFO_HRA
    return ""


def _ingest(cat: MarketDataCatalog, **kw) -> int:
    opts: dict = {
        "manager_cik_map": _CIK_MAP,
        "cusip_symbol_map": _CUSIP_MAP,
        "fetch_json": _route_json,
        "fetch_text": _route_text,
        "sleep": lambda *_: None,
    }
    opts.update(kw)  # let callers override any default (e.g. a tracing/flaky fetch)
    return ingest_13f(["BERKSHIRE HATHAWAY INC"], cat, **opts)


# ---- unit tests on the helpers -------------------------------------------------------------------


def test_infotable_filename_skips_extraneous_table_xml() -> None:
    # A cover/exhibit XML containing "table" must NOT be mistaken for the holdings table.
    index = {
        "directory": {
            "item": [
                {"name": "primary_doc.xml"},
                {"name": "R401CoverTable.xml"},  # exhibit — must be skipped
                {"name": "form13fInfoTable.xml"},  # the real holdings table
            ]
        }
    }
    assert _infotable_filename(index) == "form13fInfoTable.xml"


def test_infotable_filename_matches_informationtable_variant() -> None:
    index = {"directory": {"item": [{"name": "primary_doc.xml"}, {"name": "InformationTable.xml"}]}}
    assert _infotable_filename(index) == "InformationTable.xml"


def test_parse_acceptance_naive_string_treated_as_utc() -> None:
    # A missing 'Z' must be read as UTC, not the host's local time (silent hour shift).
    assert _parse_acceptance("2025-05-15T16:00:00.000") == datetime(2025, 5, 15, 16, tzinfo=UTC)


# ---- end-to-end ingest ---------------------------------------------------------------------------


def test_ingest_stores_holdings_from_infotable_not_primary_doc(tmp_path) -> None:
    cat = MarketDataCatalog(tmp_path / "cat.duckdb")
    _ingest(cat)
    aapl = cat.get_institutional_holdings("AAPL", as_of=datetime(2025, 6, 1, tzinfo=UTC))
    assert len(aapl) == 1
    assert aapl[0].shares == 6000.0  # latest filing (the restatement amendment)
    assert aapl[0].manager == "BERKSHIRE HATHAWAY INC"


def test_restatement_amendment_tombstones_dropped_position(tmp_path) -> None:
    cat = MarketDataCatalog(tmp_path / "cat.duckdb")
    _ingest(cat)
    after = cat.get_institutional_holdings("MSFT", as_of=datetime(2025, 6, 1, tzinfo=UTC))
    assert len(after) == 1 and after[0].shares is None  # closed by the restatement
    between = cat.get_institutional_holdings("MSFT", as_of=datetime(2025, 5, 17, tzinfo=UTC))
    assert len(between) == 1 and between[0].shares == 3000.0  # PIT: still held before the amendment


def test_only_13f_forms_processed(tmp_path) -> None:
    cat = MarketDataCatalog(tmp_path / "cat.duckdb")
    fetched: list[str] = []

    def trace_text(url: str) -> str:
        fetched.append(url)
        return _route_text(url)

    _ingest(cat, fetch_text=trace_text)
    assert all("000003" not in u for u in fetched)  # the 10-K is never fetched


def test_report_date_from_manifest(tmp_path) -> None:
    cat = MarketDataCatalog(tmp_path / "cat.duckdb")
    _ingest(cat)
    aapl = cat.get_institutional_holdings("AAPL", as_of=datetime(2025, 6, 1, tzinfo=UTC))
    assert aapl[0].report_date.isoformat() == "2025-03-31"


def test_unknown_manager_makes_no_network_call(tmp_path) -> None:
    cat = MarketDataCatalog(tmp_path / "cat.duckdb")
    calls: list[str] = []

    def trace_json(url: str) -> dict:
        calls.append(url)
        return _route_json(url)

    n = ingest_13f(
        ["UNKNOWN FUND LP"],
        cat,
        manager_cik_map=_CIK_MAP,
        cusip_symbol_map=_CUSIP_MAP,
        fetch_json=trace_json,
        fetch_text=_route_text,
        sleep=lambda *_: None,
    )
    assert n == 0
    assert calls == []


def test_throttles_between_requests(tmp_path) -> None:
    cat = MarketDataCatalog(tmp_path / "cat.duckdb")
    sleeps: list[float] = []
    _ingest(cat, sleep=sleeps.append)
    assert sleeps  # SEC fair-access throttle applied


# ---- robustness / correctness (adversarial review) -----------------------------------------------


def test_malformed_report_date_skips_filing_not_aborts(tmp_path) -> None:
    bad = copy.deepcopy(_SUBMISSIONS)
    bad["filings"]["recent"]["reportDate"][1] = ""  # corrupt the amendment's reportDate
    cat = MarketDataCatalog(tmp_path / "cat.duckdb")
    n = _ingest(cat, fetch_json=lambda url: bad if "submissions/CIK" in url else _route_json(url))
    assert n > 0  # the valid original still stored — no crash
    orig = cat.get_institutional_holdings("AAPL", as_of=datetime(2025, 6, 1, tzinfo=UTC))
    assert orig[0].shares == 5000.0  # amendment skipped, original survives


def test_network_error_on_one_filing_does_not_abort(tmp_path) -> None:
    cat = MarketDataCatalog(tmp_path / "cat.duckdb")

    def flaky_json(url: str) -> dict:
        if "000001/index.json" in url:
            raise RuntimeError("SEC 503")  # the original's index fetch fails
        return _route_json(url)

    _ingest(cat, fetch_json=flaky_json)
    # The amendment (000002) still processed despite the original failing.
    aapl = cat.get_institutional_holdings("AAPL", as_of=datetime(2025, 6, 1, tzinfo=UTC))
    assert len(aapl) == 1 and aapl[0].shares == 6000.0


def test_malformed_amendment_does_not_mass_tombstone(tmp_path) -> None:
    cat = MarketDataCatalog(tmp_path / "cat.duckdb")

    def bad_text(url: str) -> str:
        if url.endswith("primary_doc.xml"):
            return _cover("RESTATEMENT")
        if "000002/" in url:  # amendment holdings XML is garbage
            return "<<NOT XML>>"
        return _route_text(url)

    _ingest(cat, fetch_text=bad_text)
    # A garbage amendment must NOT be read as "everything sold" — the original survives intact.
    msft = cat.get_institutional_holdings("MSFT", as_of=datetime(2025, 6, 1, tzinfo=UTC))
    assert len(msft) == 1 and msft[0].shares == 3000.0
    aapl = cat.get_institutional_holdings("AAPL", as_of=datetime(2025, 6, 1, tzinfo=UTC))
    assert len(aapl) == 1 and aapl[0].shares == 5000.0  # original, not the garbage amendment


def test_new_holdings_amendment_is_additive_not_a_restatement(tmp_path) -> None:
    # A "NEW HOLDINGS" amendment lists ONLY the added positions — it must NOT tombstone everything
    # else the manager still holds (Codex P1: the most dangerous mis-read of 13F amendments).
    cat = MarketDataCatalog(tmp_path / "cat.duckdb")
    info_new = _table(_row("NVIDIA CORP", "67066G104", 1000))  # the amendment adds only NVDA

    def text_new(url: str) -> str:
        if url.endswith("primary_doc.xml"):
            return _cover("NEW HOLDINGS")
        if "000001/" in url:
            return _INFO_HR
        if "000002/" in url:
            return info_new
        return ""

    _ingest(cat, fetch_text=text_new)
    asof = datetime(2025, 6, 1, tzinfo=UTC)
    assert cat.get_institutional_holdings("AAPL", as_of=asof)[0].shares == 5000.0  # still held
    assert cat.get_institutional_holdings("MSFT", as_of=asof)[0].shares == 3000.0  # NOT tombstoned
    assert cat.get_institutional_holdings("NVDA", as_of=asof)[0].shares == 1000.0  # the addition


def test_empty_restatement_tombstones_all_prior_positions(tmp_path) -> None:
    # A VALID restatement that retains zero tracked positions (full exit) must close everything —
    # distinct from a malformed/failed fetch, which is skipped. (Codex P2)
    cat = MarketDataCatalog(tmp_path / "cat.duckdb")
    empty_table = _table("")  # well-formed information table with no holdings

    def text_empty(url: str) -> str:
        if url.endswith("primary_doc.xml"):
            return _cover("RESTATEMENT")
        if "000001/" in url:
            return _INFO_HR
        if "000002/" in url:
            return empty_table
        return ""

    _ingest(cat, fetch_text=text_empty)
    asof = datetime(2025, 6, 1, tzinfo=UTC)
    assert cat.get_institutional_holdings("AAPL", as_of=asof)[0].shares is None  # closed
    assert cat.get_institutional_holdings("MSFT", as_of=asof)[0].shares is None  # closed


def test_option_line_tombstoned_separately_from_equity(tmp_path) -> None:
    # With include_options, an amendment dropping a Call while keeping the equity for the same CUSIP
    # must tombstone ONLY the Call row, keying on (cusip, put_call).
    cat = MarketDataCatalog(tmp_path / "cat.duckdb")
    info_hr = _table(
        _row("APPLE INC", "037833100", 5000) + _row("APPLE INC", "037833100", 1000, "Call")
    )
    info_hra = _table(_row("APPLE INC", "037833100", 6000))  # Call dropped, equity raised

    def text_opt(url: str) -> str:
        if url.endswith("primary_doc.xml"):
            return _cover("RESTATEMENT")
        if "000001/" in url:
            return info_hr
        if "000002/" in url:
            return info_hra
        return ""

    _ingest(cat, fetch_text=text_opt, include_options=True)
    rows = cat.get_institutional_holdings("AAPL", as_of=datetime(2025, 6, 1, tzinfo=UTC))
    by_pc = {r.put_call: r.shares for r in rows}
    assert by_pc[""] == 6000.0  # equity raised
    assert by_pc["Call"] is None  # the option line was closed
