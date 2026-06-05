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

import logging
import time
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

from data.models import InstitutionalHoldingRecord

from .sec_form4 import fetch_json, fetch_text, submissions_url

if TYPE_CHECKING:
    from data.catalog import MarketDataCatalog

_log = logging.getLogger(__name__)

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


# --------------------------------------------------------------------------------------------------
# Network / ingest layer. Self-contained; network callables are injectable so the orchestration is
# unit-testable without hitting SEC. Reuses the Form 4 SEC net helpers (same host, fair-access rules).
# --------------------------------------------------------------------------------------------------

_13F_FORMS = ("13F-HR", "13F-HR/A")


def _parse_acceptance(value: str) -> datetime:
    """SEC acceptanceDateTime like '2025-05-15T16:00:00.000Z' -> aware UTC datetime. If SEC ever
    omits the 'Z', treat the naive result as UTC (never the host's local time)."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _13f_from_arrays(block: dict) -> list[dict]:
    """Filter a SEC filings block (parallel arrays) to 13F-HR (and /A) rows. Works for both the main
    submissions 'filings.recent' block and an archived shard JSON (top-level arrays). Carries the
    manifest reportDate (the quarter-end periodOfReport, not in the information table) and the form
    (to tell an amendment from an original)."""
    forms = block.get("form", [])
    accs = block.get("accessionNumber", [])
    accepts = block.get("acceptanceDateTime", [])
    reports = block.get("reportDate", [])
    arrays = (forms, accs, accepts, reports)
    if len({len(a) for a in arrays}) > 1:
        # reportDate is load-bearing for the tombstone diff; a ragged manifest would silently drop
        # trailing filings, so surface it rather than truncating in silence.
        _log.warning(
            "_13f_from_arrays: ragged manifest arrays (form=%d acc=%d accept=%d report=%d)",
            *(len(a) for a in arrays),
        )
    out: list[dict] = []
    for form, acc, accept, report in zip(forms, accs, accepts, reports, strict=False):
        if form in _13F_FORMS:
            out.append(
                {"accession": acc, "acceptance": accept, "report_date": report, "form": form}
            )
    return out


def _accession_index_url(cik: int, accession: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/index.json"


def _archive_url(cik: int, accession: str, filename: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{filename}"


def _infotable_filename(index_json: dict) -> str | None:
    """Pick the information-table XML from an accession's file index. The cover page is
    ``primary_doc.xml``; the holdings are a SEPARATE XML (e.g. 'form13fInfoTable.xml'). Match the
    specific SEC infotable names (NOT a bare 'table', which also hits cover/exhibit XMLs), then fall
    back to any non-cover .xml."""
    items = index_json.get("directory", {}).get("item", [])
    xmls = [
        str(it.get("name", "")) for it in items if str(it.get("name", "")).lower().endswith(".xml")
    ]
    for name in xmls:
        low = name.lower()
        if "infotable" in low or "informationtable" in low or "form13f" in low:
            return name
    for name in xmls:
        if name.lower() != "primary_doc.xml":
            return name
    return None


def _amendment_type(cover_xml: bytes) -> str:
    """Read amendmentType ('RESTATEMENT' | 'NEW HOLDINGS' | ...) from a 13F cover page. '' if absent
    or unparseable. Only a RESTATEMENT is a full-snapshot replacement; NEW HOLDINGS is additive."""
    try:
        root = ET.fromstring(cover_xml)
    except (ET.ParseError, LookupError, ValueError):
        return ""
    for el in root.iter():
        if _local(el.tag) == "amendmentType":
            return (el.text or "").strip().upper()
    return ""


def _is_restatement(
    cik: int,
    filing: dict,
    *,
    fetch_text: Callable[[str], str],
    sleep: Callable[[float], None],
) -> bool:
    """A 13F-HR/A is a full-snapshot RESTATEMENT (tombstone-diffable) only if its cover page says so.
    An original 13F-HR is the baseline (never a restatement diff). On any ambiguity, return False so
    a partial/NEW-HOLDINGS amendment is treated additively, never mass-tombstoning the quarter."""
    if not filing["form"].endswith("/A"):
        return False
    try:
        cover = fetch_text(_archive_url(cik, filing["accession"], "primary_doc.xml"))
        sleep(0.15)
    except Exception:  # noqa: BLE001 - any fetch failure -> safe additive default
        return False
    return _amendment_type(cover.encode("utf-8")) == "RESTATEMENT"


def _fetch_holdings(
    cik: int,
    filing: dict,
    *,
    manager: str,
    report_date: date,
    asof_ts: datetime,
    cusip_symbol_map: Mapping[str, str] | None,
    include_options: bool,
    fetch_json: Callable[[str], dict],
    fetch_text: Callable[[str], str],
    sleep: Callable[[float], None],
) -> list[InstitutionalHoldingRecord] | None:
    """Fetch + parse one filing's information table. Returns None (not []) when the filing could not
    be fetched or no holdings table was found, so the caller can distinguish 'failed' from 'empty'
    and never treat a network/parse failure as a real empty snapshot."""
    acc = filing["accession"]
    try:
        index_json = fetch_json(_accession_index_url(cik, acc))
        sleep(0.15)
    except Exception:  # noqa: BLE001 - isolate one filing's network failure from the whole run
        _log.warning("ingest_13f %s: index fetch failed for %s", manager, acc)
        sleep(0.15)
        return None
    info_name = _infotable_filename(index_json)
    if info_name is None:
        _log.warning("ingest_13f %s: no information table in %s", manager, acc)
        return None
    try:
        xml = fetch_text(_archive_url(cik, acc, info_name))
        sleep(0.15)
    except Exception:  # noqa: BLE001 - isolate one filing's network failure from the whole run
        _log.warning("ingest_13f %s: infotable fetch failed for %s", manager, acc)
        sleep(0.15)
        return None
    raw = xml.encode("utf-8")
    try:
        # Well-formedness probe: the parser returns [] for BOTH malformed XML and a valid-but-empty
        # table. Distinguish them — malformed -> None (skip, never mass-tombstone); a well-formed
        # table that retains no rows -> [] (a real empty snapshot a restatement can act on).
        ET.fromstring(raw)
    except (ET.ParseError, LookupError, ValueError):
        _log.warning("ingest_13f %s: malformed information table in %s", manager, acc)
        return None
    return parse_13f_infotable_xml(
        raw,
        manager=manager,
        report_date=report_date,
        asof_ts=asof_ts,
        cusip_symbol_map=cusip_symbol_map,
        include_options=include_options,
    )


def ingest_13f(
    managers: list[str],
    catalog: MarketDataCatalog,
    *,
    manager_cik_map: dict[str, int],
    cusip_symbol_map: Mapping[str, str] | None = None,
    include_options: bool = False,
    fetch_json: Callable[[str], dict] = fetch_json,
    fetch_text: Callable[[str], str] = fetch_text,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Fetch each manager's 13F-HR history (submissions manifest -> accession index -> information
    table XML), parse it, resolve CUSIPs to tickers, and store PIT (asof_ts = SEC acceptance time).

    A RESTATEMENT amendment is a full-snapshot replacement, so any CUSIP it drops vs the prior filing
    for that quarter gets a None-shares TOMBSTONE (PIT-safe exit — the original stays visible between
    the two filings). A NEW-HOLDINGS amendment (or an unparseable/failed one) is treated additively:
    it never tombstones, because mis-reading a partial amendment as a full snapshot would falsely
    close most of the quarter's positions. A failed/empty fetch is skipped without touching state.
    Returns the number of stored records. Network callables are injectable."""
    stored = 0
    for manager in managers:
        cik = manager_cik_map.get(manager)
        if cik is None:
            continue
        submissions = fetch_json(submissions_url(cik))
        sleep(0.15)
        filings = _13f_from_arrays(submissions.get("filings", {}).get("recent", {}))
        # Deep history pages into archived shards (top-level arrays); include them for completeness.
        for shard in submissions.get("filings", {}).get("files", []):
            name = shard.get("name")
            if not name:
                continue
            shard_json = fetch_json(f"https://data.sec.gov/submissions/{name}")
            sleep(0.15)
            filings = filings + _13f_from_arrays(shard_json)
        # Oldest acceptance first so an amendment is processed AFTER the original it supersedes.
        filings.sort(key=lambda f: f["acceptance"])
        # (cusip, put_call) pairs held per quarter, so a dropped option line is tombstoned distinctly
        # from a dropped equity line in the same CUSIP.
        last_pairs: dict[date, set[tuple[str, str]]] = {}
        for filing in filings:
            try:
                report_date = date.fromisoformat(filing["report_date"])
                asof_ts = _parse_acceptance(filing["acceptance"])
            except (ValueError, KeyError) as exc:
                _log.warning(
                    "ingest_13f %s: bad date in %s: %s", manager, filing.get("accession", "?"), exc
                )
                continue
            restatement = _is_restatement(cik, filing, fetch_text=fetch_text, sleep=sleep)
            records = _fetch_holdings(
                cik,
                filing,
                manager=manager,
                report_date=report_date,
                asof_ts=asof_ts,
                cusip_symbol_map=cusip_symbol_map,
                include_options=include_options,
                fetch_json=fetch_json,
                fetch_text=fetch_text,
                sleep=sleep,
            )
            if records is None:
                # Failed fetch / wrong file / malformed XML (None, NOT []). Do NOT treat as a real
                # empty snapshot — that would mass-tombstone the quarter. Leave prior state untouched.
                # A well-formed table with zero rows IS [] and proceeds (a restatement closes all).
                continue
            stored += catalog.put_institutional_holdings(records)
            current = {(r.cusip, r.put_call) for r in records}
            prior = last_pairs.get(report_date, set())
            if restatement:
                dropped = prior - current
                if dropped:
                    market = records[0].market if records else "us"  # 13F is US-only
                    stored += catalog.put_institutional_holdings(
                        [
                            InstitutionalHoldingRecord(
                                symbol=_resolve_symbol(cusip, cusip_symbol_map),
                                market=market,
                                cusip=cusip,
                                issuer_name="",
                                manager=manager,
                                report_date=report_date,
                                asof_ts=asof_ts,
                                shares=None,
                                value_usd=None,
                                put_call=put_call,
                                source="sec:13f",
                            )
                            for cusip, put_call in dropped
                        ]
                    )
                last_pairs[report_date] = current
            else:
                # Original baseline OR additive (NEW HOLDINGS) amendment: never drop; union holdings.
                last_pairs[report_date] = prior | current
            sleep(0.15)
    return stored
