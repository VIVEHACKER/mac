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

import json
import logging
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

from data.models import InsiderTradeRecord

if TYPE_CHECKING:
    from data.catalog import MarketDataCatalog

_log = logging.getLogger(__name__)

OPEN_MARKET_BUY = "P"
# SEC requires a descriptive User-Agent or it returns 403. ~10 req/s limit -> 0.15s between calls.
USER_AGENT = "RegimeResearch jjuni@local.research"
_FORM4_FORMS = ("4", "4/A")


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


# --------------------------------------------------------------------------------------------------
# Network / ingest layer. Self-contained (no scripts/ dependency); network callables are injectable
# so the orchestration is unit-testable without hitting SEC.
# --------------------------------------------------------------------------------------------------


def fetch_json(url: str, retries: int = 3, backoff: float = 1.5) -> dict:
    last_exc: Exception | None = None
    for i in range(retries):
        try:
            req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urlopen(req, timeout=30) as r:  # noqa: S310 (fixed https SEC host)
                result: dict = json.load(r)
                return result
        except (HTTPError, URLError, TimeoutError) as e:
            last_exc = e
            time.sleep(backoff * (i + 1))
    raise RuntimeError(f"fetch failed: {url} :: {last_exc}")


def fetch_text(url: str, retries: int = 3, backoff: float = 1.5) -> str:
    last_exc: Exception | None = None
    for i in range(retries):
        try:
            req = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(req, timeout=30) as r:  # noqa: S310 (fixed https SEC host)
                raw: bytes = r.read()
                return raw.decode("utf-8", errors="replace")
        except (HTTPError, URLError, TimeoutError) as e:
            last_exc = e
            time.sleep(backoff * (i + 1))
    raise RuntimeError(f"fetch failed: {url} :: {last_exc}")


def submissions_url(cik: int) -> str:
    return f"https://data.sec.gov/submissions/CIK{cik:010d}.json"


def _form4_from_arrays(block: dict) -> list[dict]:
    """Filter a SEC filings block (parallel arrays) to Form 4 (and 4/A) rows. Works for both the
    main submissions 'filings.recent' block and an archived shard JSON (top-level arrays)."""
    forms = block.get("form", [])
    accs = block.get("accessionNumber", [])
    accepts = block.get("acceptanceDateTime", [])
    docs = block.get("primaryDocument", [])
    out: list[dict] = []
    for form, acc, accept, doc in zip(forms, accs, accepts, docs, strict=False):
        if form in _FORM4_FORMS:
            out.append({"accession": acc, "acceptance": accept, "primary_doc": doc})
    return out


def _recent_form4(submissions: dict) -> list[dict]:
    """Form 4 rows from the main submissions 'recent' block (most recent ~1000 filings)."""
    return _form4_from_arrays(submissions.get("filings", {}).get("recent", {}))


def _form4_xml_url(cik: int, accession: str, primary_doc: str) -> str:
    """Archives path uses the CIK without leading zeros and the accession without dashes. The
    submissions ``primaryDocument`` often points at the XSL-rendered HTML view (e.g.
    'xslF345X06/foo.xml', which serves HTML, not XML); the raw ownership XML is the basename in the
    accession directory, so strip any stylesheet path prefix."""
    doc = primary_doc.rsplit("/", 1)[-1]
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{doc}"


def _parse_acceptance(value: str) -> datetime:
    """SEC acceptanceDateTime like '2025-03-03T21:00:00.000Z' -> aware UTC datetime."""
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def ingest_form4(
    tickers: list[str],
    catalog: MarketDataCatalog,
    *,
    cik_map: dict[str, int] | None = None,
    codes: tuple[str, ...] = (OPEN_MARKET_BUY,),
    fetch_json: Callable[[str], dict] = fetch_json,
    fetch_text: Callable[[str], str] = fetch_text,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Fetch each ticker's Form 4 history (submissions manifest -> per-accession XML), parse the
    requested transaction codes (default open-market buys), and store them PIT (asof_ts = SEC
    acceptance time). Returns the number of stored records. Network callables are injectable."""
    if cik_map is None:  # pragma: no cover - real-run convenience; tests inject a map
        from scripts.sec_edgar_ingest import load_cik_map

        cik_map = load_cik_map()
    stored = 0
    for ticker in tickers:
        # Share-class ticker conventions differ: SEC company_tickers.json uses the HYPHEN form
        # (verified: BRK-B, BF-A), while callers may pass either hyphen or dot (BRK.B). Try all
        # variants so the lookup succeeds regardless of which side uses which (Codex P2).
        candidates = {
            ticker.upper(),
            ticker.replace("-", ".").upper(),
            ticker.replace(".", "-").upper(),
        }
        cik = next((cik_map[c] for c in candidates if c in cik_map), None)
        if cik is None:
            continue
        submissions = fetch_json(submissions_url(cik))
        sleep(0.15)  # throttle after EVERY SEC request, incl. tickers with no Form 4 (Codex P2)
        filings = _recent_form4(submissions)
        # High-filing issuers page older filings into archived shard JSONs; include them so history
        # ingest is complete, not just the recent ~1000 (Codex P2). Shards hold top-level arrays.
        for shard in submissions.get("filings", {}).get("files", []):
            name = shard.get("name")
            if not name:
                continue
            shard_json = fetch_json(f"https://data.sec.gov/submissions/{name}")
            sleep(0.15)
            filings = filings + _form4_from_arrays(shard_json)
        skipped_legacy = 0
        for filing in filings:
            # Legacy (pre-2003) Form 4s have an HTML/TXT primaryDocument — XML became mandatory in
            # 2003. Feeding those to the XML parser would silently yield [] and hide the gap, so
            # skip + count them explicitly (no silent incompleteness). Parsing the legacy format is
            # out of scope: it predates the project's 2009+ backtest window (Codex P2).
            if not filing["primary_doc"].rsplit("/", 1)[-1].lower().endswith(".xml"):
                skipped_legacy += 1
                continue
            url = _form4_xml_url(cik, filing["accession"], filing["primary_doc"])
            records = parse_form4_xml(
                fetch_text(url).encode("utf-8"),
                asof_ts=_parse_acceptance(filing["acceptance"]),
                codes=codes,
            )
            # Multi-class issuers share one CIK; the XML may report a different class symbol
            # (e.g. GOOGL while ingesting GOOG). Store under the REQUESTED ticker so the caller's
            # get_insider_trades(ticker) finds them (Codex P2).
            records = [replace(r, symbol=ticker.upper()) for r in records]
            stored += catalog.put_insider_trades(records)
            sleep(0.15)
        if skipped_legacy:
            _log.warning(
                "ingest_form4 %s: skipped %d legacy non-XML Form 4 filing(s) (pre-2003)",
                ticker,
                skipped_legacy,
            )
    return stored
