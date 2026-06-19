"""SEC bulk Insider Transactions Data Sets (Form 3/4/5) — quarterly TSV ingester.

The per-accession Form 4 ingester (sec_form4.py) is right for a few names' recent filings, but a
universe-scale historical backfill via per-accession XML is ~2M fetches (an issuer averages ~2,000
Form 4 filings). SEC's pre-parsed quarterly "Insider Transactions Data Sets" are the bulk path: one
~14MB zip per quarter holds every Form 3/4/5 transaction as TSVs WITH the transaction code and the
issuer ticker, so a 2011-2023 backfill is ~52 downloads, not millions of fetches.

This module filters to open-market BUYS (code ``P``) for a target universe and maps them to the same
``InsiderTradeRecord`` the per-accession path produces, so ``catalog.put_insider_trades`` (split
aggregation + dual-PIT) is reused unchanged. ``FILING_DATE`` is the point-in-time visibility key, set
to end-of-day (the bulk data is date-granular — no intraday acceptance time — and Form 4 must be
filed within 2 business days).

Known limitation: because the visibility key is date-granular, an original Form 4 and a SAME-DAY 4/A
correction share one asof_ts and are AGGREGATED (summed) rather than the amendment superseding the
original. Same-day amendments are rare and the effect is negligible for the rank-based IC validation
this feeds; cross-day amendments still supersede correctly (later asof_ts wins via the catalog's
QUALIFY). The per-accession ``sec_form4`` path (intraday acceptanceDateTime) handles same-day 4/A
precisely when that precision is needed.
"""

from __future__ import annotations

import csv
import io
import logging
import time
import zipfile
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime
from typing import TYPE_CHECKING
from urllib.request import Request, urlopen

from data.models import InsiderTradeRecord

if TYPE_CHECKING:
    from data.catalog import MarketDataCatalog

_log = logging.getLogger(__name__)
OPEN_MARKET_BUY = "P"
_FORM4 = ("4", "4/A")
USER_AGENT = "RegimeResearch jjuni@local.research"
_MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
    )
}


def _canon_sym(value: str) -> str:
    """Canonical share-class ticker. SEC bulk uses dots (BRK.B); exchanges/our universe use hyphens
    (BRK-B). Canonicalize to hyphen so the two notations match in the universe membership test."""
    return value.strip().upper().replace(".", "-")


def _parse_date(value: str | None) -> datetime | None:
    """SEC bulk date like '15-MAR-2023' -> datetime (date-granular; no intraday component)."""
    if not value:
        return None
    parts = value.strip().split("-")
    if len(parts) != 3:
        return None
    day, mon, year = parts
    month = _MONTHS.get(mon.upper())
    if month is None:
        return None
    try:
        return datetime(int(year), month, int(day))
    except ValueError:
        return None


def _float(value: str | None) -> float | None:
    if value is None or not value.strip():
        return None
    try:
        return float(value.replace(",", ""))
    except ValueError:
        return None


def _best_role(owners: list[tuple[str, str, str]]) -> str:
    """Role string across all (possibly joint) reporting owners. Real officer titles are JOINED (not
    first-wins) so the downstream weighter can pick the most senior — e.g. a joint VP+CEO filing
    yields "VP Sales; CEO" and the signal's max-over-substrings ranks it as CEO. Falls back to
    generic Officer > Director > 10% owner. ``owners`` = [(name, relationship, title)]."""
    titles: list[str] = []
    officer = director = ten_pct = False
    for _name, rel, title in owners:
        rl = rel.lower()
        if "officer" in rl:
            t = title.strip()
            if t and t.lower() != "see remarks":
                titles.append(t)  # a real officer title (CEO/CFO/...) — strongest signal
            officer = True
        if "director" in rl:
            director = True
        if "10%" in rl or "ten percent" in rl or "10 percent" in rl:
            ten_pct = True
    if titles:
        return "; ".join(dict.fromkeys(titles))  # de-duped, order-preserving
    if officer:
        return "Officer"
    if director:
        return "Director"
    if ten_pct:
        return "10% owner"
    return ""


def parse_form345_tables(
    submission_rows: Iterable[dict],
    owner_rows: Iterable[dict],
    trans_rows: Iterable[dict],
    *,
    symbols: Sequence[str] | None = None,
    codes: tuple[str, ...] = (OPEN_MARKET_BUY,),
    market: str = "us",
) -> list[InsiderTradeRecord]:
    """Join the three bulk TSV tables into InsiderTradeRecords for the requested codes (default
    open-market buys) and ``symbols`` universe (None = all). Form 3/5 are excluded; only 4 / 4/A."""
    sym_set = {_canon_sym(s) for s in symbols} if symbols is not None else None
    subs: dict[str, tuple[str, datetime]] = {}
    for r in submission_rows:
        if r.get("DOCUMENT_TYPE") not in _FORM4:
            continue
        sym = _canon_sym(r.get("ISSUERTRADINGSYMBOL") or "")  # BRK.B -> BRK-B
        if not sym or (sym_set is not None and sym not in sym_set):
            continue
        asof = _parse_date(r.get("FILING_DATE"))
        acc = r.get("ACCESSION_NUMBER")
        if asof is None or not acc:
            continue
        # Visibility key = END of the filing day: EDGAR posts through the evening, so a same-day
        # market-open backtest must NOT see a filing before it was actually public (date-granular).
        subs[acc] = (sym, asof.replace(hour=23, minute=59, second=59))

    owners: dict[str, list[tuple[str, str, str]]] = {}
    for r in owner_rows:
        acc = r.get("ACCESSION_NUMBER")
        if acc in subs:
            owners.setdefault(acc, []).append(
                (
                    (r.get("RPTOWNERNAME") or "").strip(),
                    r.get("RPTOWNER_RELATIONSHIP") or "",
                    r.get("RPTOWNER_TITLE") or "",
                )
            )

    rows: list[InsiderTradeRecord] = []
    for r in trans_rows:
        if r.get("TRANS_CODE") not in codes:
            continue
        # A Form-4 submission can carry late-reported Form-5 transaction rows; keep only genuine
        # Form-4 transactions (the field is absent in older data / test fixtures -> accept).
        tft = r.get("TRANS_FORM_TYPE")
        if tft and tft not in _FORM4:
            continue
        # A real open-market BUY (code P) is an ACQUISITION; a P row flagged disposed (D) is a data
        # contradiction / off-market disposal — exclude it so disposals don't fake buys (Codex P2).
        if r.get("TRANS_ACQUIRED_DISP_CD") == "D":
            continue
        acc = r.get("ACCESSION_NUMBER")
        if acc not in subs:
            continue
        txn_date = _parse_date(r.get("TRANS_DATE"))
        if txn_date is None:
            continue
        shares = _float(r.get("TRANS_SHARES"))
        price = _float(r.get("TRANS_PRICEPERSHARE"))
        # A real open-market purchase needs a positive share count; a zero/negative price is an
        # off-market transfer / gift miscoded as P. SKIP such rows entirely — nulling the notional
        # but keeping the row would still fake a count-weighted conviction buy downstream (Codex P2).
        if shares is None or shares <= 0.0 or (price is not None and price <= 0.0):
            continue
        sym, asof = subs[acc]
        olist = owners.get(acc, [])
        value = (
            shares * price if price is not None else None
        )  # price may be legitimately unreported
        rows.append(
            InsiderTradeRecord(
                symbol=sym,
                market=market,
                txn_date=txn_date.date(),
                asof_ts=asof,
                insider_name="; ".join(n for n, _r, _t in olist if n),
                insider_role=_best_role(olist),
                txn_code=r["TRANS_CODE"],
                shares=shares,
                price=price,
                value_usd=value,
                source="sec:form345",
            )
        )
    return rows


def _read_tsv(zf: zipfile.ZipFile, name: str) -> list[dict]:
    with zf.open(name) as f:
        text = io.TextIOWrapper(f, encoding="utf-8", errors="replace")
        return list(csv.DictReader(text, delimiter="\t"))


def read_quarter_zip(zip_bytes: bytes) -> tuple[list[dict], list[dict], list[dict]]:
    """Extract the three tables this ingester needs from a quarterly form345 zip."""
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    return (
        _read_tsv(zf, "SUBMISSION.tsv"),
        _read_tsv(zf, "REPORTINGOWNER.tsv"),
        _read_tsv(zf, "NONDERIV_TRANS.tsv"),
    )


def quarter_url(year: int, quarter: int) -> str:
    return (
        "https://www.sec.gov/files/structureddata/data/"
        f"insider-transactions-data-sets/{year}q{quarter}_form345.zip"
    )


def fetch_zip(url: str) -> bytes:
    with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=120) as r:  # noqa: S310
        data: bytes = r.read()
        return data


def ingest_form345_bulk(
    quarters: Iterable[tuple[int, int]],
    catalog: MarketDataCatalog,
    *,
    symbols: Sequence[str] | None = None,
    codes: tuple[str, ...] = (OPEN_MARKET_BUY,),
    fetch_zip: Callable[[str], bytes] = fetch_zip,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Download each (year, quarter) bulk dataset, parse the requested codes for ``symbols``, and
    store them PIT (asof_ts = FILING_DATE). Returns the number of stored records. Network injectable."""
    stored = 0
    for year, quarter in quarters:
        try:
            zip_bytes = fetch_zip(quarter_url(year, quarter))
            sleep(0.5)  # be polite between large downloads
            subs, owners, trans = read_quarter_zip(zip_bytes)
            records = parse_form345_tables(subs, owners, trans, symbols=symbols, codes=codes)
        except Exception:  # noqa: BLE001 - a missing/corrupt quarter must not abort the backfill
            _log.warning("ingest_form345_bulk: skipping %dq%d (fetch/parse failed)", year, quarter)
            continue
        # Storage failures (DB lock/schema) are NOT swallowed — a broken backfill must not be
        # reported as completed (Codex P2); let put_insider_trades raise.
        stored += catalog.put_insider_trades(records)
    return stored
