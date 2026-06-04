"""SEC Form 4 (insider transactions) — XML parser.

Parses the per-accession Form 4 ownership document into InsiderTradeRecord rows. The default keeps
only open-market BUYS (transaction code ``P``): that is the documented edge (a CEO/CFO/10%-owner
spending real cash), whereas grants/vests/sales (A/M/F/S) add net-zero noise.

``asof_ts`` (the SEC filing acceptance time) is NOT in the Form 4 body — it comes from the
submissions manifest — so the caller passes it in; it is the point-in-time visibility key.
The network fetch + manifest filtering live in the ingest entry point (added separately); this
module is the pure, testable parser.
"""

from __future__ import annotations

from datetime import date, datetime
from xml.etree import ElementTree as ET

from data.models import InsiderTradeRecord

OPEN_MARKET_BUY = "P"


def _text(node: ET.Element | None, path: str) -> str | None:
    if node is None:
        return None
    found = node.findtext(path)
    return found.strip() if found and found.strip() else None


def _float(node: ET.Element | None, path: str) -> float | None:
    raw = _text(node, path)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _best_role(relationships: list[ET.Element | None]) -> str:
    """Highest-priority role across ALL (possibly joint) reporting owners: officer title >
    Director > 10% owner. A joint Form 4 is ONE transaction co-filed by several insiders, so the
    most-significant role drives the signal weight (raw, not deduped)."""
    director = ten_pct = False
    for reln in relationships:
        if reln is None:
            continue
        if _text(reln, "isOfficer") in ("1", "true"):
            return _text(reln, "officerTitle") or "Officer"
        if _text(reln, "isDirector") in ("1", "true"):
            director = True
        if _text(reln, "isTenPercentOwner") in ("1", "true"):
            ten_pct = True
    if director:
        return "Director"
    if ten_pct:
        return "10% owner"
    return ""


def parse_form4_xml(
    xml_bytes: bytes,
    *,
    asof_ts: datetime,
    market: str = "us",
    codes: tuple[str, ...] = (OPEN_MARKET_BUY,),
) -> list[InsiderTradeRecord]:
    """Parse a Form 4 ownership document into InsiderTradeRecords, keeping only ``codes`` (default
    open-market buys). Returns [] on malformed XML or a derivative-only filing — never raises."""
    try:
        root = ET.fromstring(xml_bytes)
    except (ET.ParseError, LookupError, ValueError):
        # malformed XML, a bogus encoding declaration (LookupError), or undefined entity — never
        # raise on an untrusted SEC payload; an unparseable filing is simply "no insider trades".
        return []

    symbol = _text(root.find("issuer"), "issuerTradingSymbol")
    if not symbol:
        return []
    # A joint filing co-files several reporting owners against ONE set of transactions. Combine all
    # co-filer names and take the highest-priority role, but still emit ONE record per transaction
    # (one record per owner would double-count the shares) — Codex P2.
    owners = root.findall("reportingOwner")
    insider_name = "; ".join(n for o in owners if (n := _text(o, "reportingOwnerId/rptOwnerName")))
    role = _best_role([o.find("reportingOwnerRelationship") for o in owners])

    rows: list[InsiderTradeRecord] = []
    for txn in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        code = _text(txn, "transactionCoding/transactionCode")
        if code not in codes:
            continue
        txn_date_raw = _text(txn, "transactionDate/value")
        if txn_date_raw is None:
            continue
        try:
            txn_date = date.fromisoformat(txn_date_raw)
        except ValueError:
            continue
        shares = _float(txn, "transactionAmounts/transactionShares/value")
        price = _float(txn, "transactionAmounts/transactionPricePerShare/value")
        value_usd = shares * price if shares is not None and price is not None else None
        rows.append(
            InsiderTradeRecord(
                symbol=symbol.upper(),
                market=market,
                txn_date=txn_date,
                asof_ts=asof_ts,
                insider_name=insider_name,
                insider_role=role,
                txn_code=code,
                shares=shares,
                price=price,
                value_usd=value_usd,
                source="sec:form4",
            )
        )
    return rows
