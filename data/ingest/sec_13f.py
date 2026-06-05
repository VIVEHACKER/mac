"""SEC Form 13F-HR (institutional holdings) — information-table XML parser.

A 13F-HR is the quarterly holdings disclosure that institutional managers with >$100M AUM must file
within 45 days of quarter-end. The structured "information table" lists one ``infoTable`` row per
position: issuer name, CUSIP, market value, shares (or bond principal), and an optional put/call
marker. This module is the pure, testable parser; the network fetch + whale-manager filter + CUSIP
resolution live in the ingest entry point (added separately).

Three 13F-specific gotchas are handled here:
- **Namespace**: real tables carry a default XML namespace; matching is done on local tag names so
  namespaced and non-namespaced tables both parse.
- **Value units**: ``value`` was reported in THOUSANDS of dollars until filings made on/after
  2023-01-03, then in whole dollars. The parser auto-detects the era from ``asof_ts`` (the filing
  acceptance time) so a caller cannot silently 1000x a modern filing; an explicit ``value_multiplier``
  still overrides.
- **CUSIP, not ticker**: 13F identifies issuers by CUSIP only; ``symbol`` is resolved from an
  injected ``cusip_symbol_map`` (full CUSIP-9, then 8-char issue without check digit), upper-cased to
  match the sibling SEC records, and is "" when unmapped. The 6-char issuer prefix is deliberately
  NOT used — it spans every security class of an issuer (bonds/preferred/warrants) and would mislabel
  a bond CUSIP as the common-stock ticker.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from xml.etree import ElementTree as ET

from data.models import InstitutionalHoldingRecord

# Filings ACCEPTED on/after this date report 'value' in whole dollars; earlier ones in thousands.
_WHOLE_DOLLAR_CUTOVER = date(2023, 1, 3)
_OPTION_MARKERS = frozenset({"put", "call"})


def _local(tag: str) -> str:
    """Strip any ``{namespace}`` prefix from an ElementTree tag, leaving the local name."""
    return tag.rsplit("}", 1)[-1]


def _child_text(node: ET.Element, *names: str) -> str | None:
    """Follow a chain of child local-names (namespace-agnostic) and return the leaf text, or None."""
    cur: ET.Element = node
    for name in names:
        nxt = next((c for c in cur if _local(c.tag) == name), None)
        if nxt is None:
            return None
        cur = nxt
    text = cur.text
    return text.strip() if text and text.strip() else None


def _float(raw: str | None) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def _resolve_symbol(cusip: str, cusip_symbol_map: Mapping[str, str] | None) -> str:
    if not cusip_symbol_map:
        return ""
    for key in (cusip, cusip[:8]):  # full CUSIP-9, then the issue without its check digit
        if key in cusip_symbol_map:
            return cusip_symbol_map[
                key
            ].upper()  # match the upper-case convention of sibling records
    return ""


def parse_13f_infotable_xml(
    xml_bytes: bytes,
    *,
    manager: str,
    report_date: date,
    asof_ts: datetime,
    market: str = "us",
    value_multiplier: float | None = None,
    include_options: bool = False,
    cusip_symbol_map: Mapping[str, str] | None = None,
) -> list[InstitutionalHoldingRecord]:
    """Parse a 13F information table into InstitutionalHoldingRecords. Skips option lines (unless
    ``include_options``) and rows missing a CUSIP or value. ``value_multiplier`` defaults to the
    era-correct factor derived from ``asof_ts`` (x1000 before 2023-01-03, x1 after). Returns [] on
    malformed XML — never raises on an untrusted SEC payload."""
    if value_multiplier is None:
        value_multiplier = 1.0 if asof_ts.date() >= _WHOLE_DOLLAR_CUTOVER else 1000.0
    try:
        root = ET.fromstring(xml_bytes)
    except (ET.ParseError, LookupError, ValueError):
        return []

    rows: list[InstitutionalHoldingRecord] = []
    for it in (el for el in root.iter() if _local(el.tag) == "infoTable"):
        # Only the SEC-defined option markers count as options; any other value (incl. a literal
        # "None" sentinel some filers write) is a plain equity holding, not a line to drop.
        put_call_raw = _child_text(it, "putCall") or ""
        put_call = put_call_raw if put_call_raw.lower() in _OPTION_MARKERS else ""
        if put_call and not include_options:
            continue
        cusip = _child_text(it, "cusip")
        value_raw = _float(_child_text(it, "value"))
        if not cusip or value_raw is None:
            continue  # an incomplete row cannot be trusted as a holding
        cusip = cusip.replace(" ", "").upper()

        # Default a missing type to SH (the schema-required norm): a malformed row that omits the
        # type but carries a share count is an equity position, not a dropped one.
        sh_type = (_child_text(it, "shrsOrPrnAmt", "sshPrnamtType") or "SH").upper()
        sh_amt = _float(_child_text(it, "shrsOrPrnAmt", "sshPrnamt"))
        shares = sh_amt if sh_type == "SH" else None  # PRN is a bond principal, not a share count

        rows.append(
            InstitutionalHoldingRecord(
                symbol=_resolve_symbol(cusip, cusip_symbol_map),
                market=market,
                cusip=cusip,
                issuer_name=_child_text(it, "nameOfIssuer") or "",
                manager=manager,
                report_date=report_date,
                asof_ts=asof_ts,
                shares=shares,
                value_usd=value_raw * value_multiplier,
                put_call=put_call,
                source="sec:13f",
            )
        )
    return rows
