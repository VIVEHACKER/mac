from __future__ import annotations

from datetime import UTC, date, datetime

from data.ingest.sec_form4 import parse_form4_xml

ASOF = datetime(2025, 3, 3, 21, 0, tzinfo=UTC)  # SEC filing acceptance (external to the XML)


def _doc(reporting: str, tables: str) -> bytes:
    return (
        '<?xml version="1.0"?>'
        "<ownershipDocument>"
        "<issuer><issuerCik>0000320193</issuerCik>"
        "<issuerTradingSymbol>AAPL</issuerTradingSymbol></issuer>"
        f"{reporting}{tables}"
        "</ownershipDocument>"
    ).encode()


_CEO = (
    "<reportingOwner><reportingOwnerId><rptOwnerName>COOK TIMOTHY D</rptOwnerName>"
    "</reportingOwnerId><reportingOwnerRelationship><isDirector>0</isDirector>"
    "<isOfficer>1</isOfficer><isTenPercentOwner>0</isTenPercentOwner>"
    "<officerTitle>Chief Executive Officer</officerTitle>"
    "</reportingOwnerRelationship></reportingOwner>"
)


def _txn(code: str, shares: str = "1000", price: str | None = "150.50") -> str:
    price_node = (
        f"<transactionPricePerShare><value>{price}</value></transactionPricePerShare>"
        if price is not None
        else ""
    )
    return (
        "<nonDerivativeTransaction>"
        "<securityTitle><value>Common Stock</value></securityTitle>"
        "<transactionDate><value>2025-03-01</value></transactionDate>"
        f"<transactionCoding><transactionCode>{code}</transactionCode></transactionCoding>"
        "<transactionAmounts>"
        f"<transactionShares><value>{shares}</value></transactionShares>"
        f"{price_node}"
        "<transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>"
        "</transactionAmounts></nonDerivativeTransaction>"
    )


def test_parses_open_market_buy() -> None:
    xml = _doc(_CEO, f"<nonDerivativeTable>{_txn('P')}</nonDerivativeTable>")
    rows = parse_form4_xml(xml, asof_ts=ASOF)

    assert len(rows) == 1
    r = rows[0]
    assert r.symbol == "AAPL"
    assert r.market == "us"
    assert r.txn_date == date(2025, 3, 1)
    assert r.asof_ts == ASOF
    assert r.insider_name == "COOK TIMOTHY D"
    assert r.insider_role == "Chief Executive Officer"
    assert r.txn_code == "P"
    assert r.shares == 1000.0
    assert r.price == 150.50
    assert r.value_usd == 150_500.0


def test_keeps_only_open_market_buys_by_default() -> None:
    tables = (
        "<nonDerivativeTable>"
        f"{_txn('P')}{_txn('A')}{_txn('S')}{_txn('F')}{_txn('M')}"
        "</nonDerivativeTable>"
    )
    rows = parse_form4_xml(_doc(_CEO, tables), asof_ts=ASOF)
    assert [r.txn_code for r in rows] == ["P"]


def test_derivative_only_filing_yields_nothing() -> None:
    deriv = (
        "<derivativeTable><derivativeTransaction>"
        "<transactionCoding><transactionCode>P</transactionCode></transactionCoding>"
        "</derivativeTransaction></derivativeTable>"
    )
    assert parse_form4_xml(_doc(_CEO, deriv), asof_ts=ASOF) == []


def test_missing_price_yields_none_not_raise() -> None:
    xml = _doc(_CEO, f"<nonDerivativeTable>{_txn('P', price=None)}</nonDerivativeTable>")
    rows = parse_form4_xml(xml, asof_ts=ASOF)
    assert len(rows) == 1
    assert rows[0].shares == 1000.0
    assert rows[0].price is None
    assert rows[0].value_usd is None


def test_director_and_ten_percent_owner_roles() -> None:
    director = (
        "<reportingOwner><reportingOwnerId><rptOwnerName>SMITH JANE</rptOwnerName>"
        "</reportingOwnerId><reportingOwnerRelationship><isDirector>1</isDirector>"
        "<isOfficer>0</isOfficer><isTenPercentOwner>0</isTenPercentOwner>"
        "</reportingOwnerRelationship></reportingOwner>"
    )
    rows = parse_form4_xml(
        _doc(director, f"<nonDerivativeTable>{_txn('P')}</nonDerivativeTable>"), asof_ts=ASOF
    )
    assert rows[0].insider_role == "Director"

    owner = (
        "<reportingOwner><reportingOwnerId><rptOwnerName>BIGCO LP</rptOwnerName>"
        "</reportingOwnerId><reportingOwnerRelationship><isDirector>0</isDirector>"
        "<isOfficer>0</isOfficer><isTenPercentOwner>1</isTenPercentOwner>"
        "</reportingOwnerRelationship></reportingOwner>"
    )
    rows = parse_form4_xml(
        _doc(owner, f"<nonDerivativeTable>{_txn('P')}</nonDerivativeTable>"), asof_ts=ASOF
    )
    assert rows[0].insider_role == "10% owner"


def test_custom_codes_can_include_sells() -> None:
    tables = f"<nonDerivativeTable>{_txn('P')}{_txn('S')}</nonDerivativeTable>"
    rows = parse_form4_xml(_doc(_CEO, tables), asof_ts=ASOF, codes=("P", "S"))
    assert sorted(r.txn_code for r in rows) == ["P", "S"]


def test_joint_filing_combines_owners_without_double_counting() -> None:
    # e.g. Berkshire (10% owner entity) + Buffett (officer) co-file one open-market buy
    entity = (
        "<reportingOwner><reportingOwnerId><rptOwnerName>BERKSHIRE HATHAWAY INC</rptOwnerName>"
        "</reportingOwnerId><reportingOwnerRelationship><isDirector>0</isDirector>"
        "<isOfficer>0</isOfficer><isTenPercentOwner>1</isTenPercentOwner>"
        "</reportingOwnerRelationship></reportingOwner>"
    )
    xml = _doc(entity + _CEO, f"<nonDerivativeTable>{_txn('P')}</nonDerivativeTable>")
    rows = parse_form4_xml(xml, asof_ts=ASOF)

    assert len(rows) == 1  # ONE transaction, not one-per-owner (no share double-count)
    assert rows[0].shares == 1000.0
    assert "BERKSHIRE HATHAWAY INC" in rows[0].insider_name
    assert "COOK TIMOTHY D" in rows[0].insider_name
    assert (
        rows[0].insider_role == "Chief Executive Officer"
    )  # highest-priority role across co-filers


def test_malformed_xml_returns_empty() -> None:
    assert parse_form4_xml(b"not xml at all", asof_ts=ASOF) == []
    # bogus encoding declaration raises LookupError (not ParseError) — must still return []
    assert (
        parse_form4_xml(b'<?xml version="1.0" encoding="bad"?><ownershipDocument/>', asof_ts=ASOF)
        == []
    )
