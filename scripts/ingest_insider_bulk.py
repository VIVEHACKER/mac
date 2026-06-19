"""Backfill insider open-market buys for the validation universe from SEC bulk Form 345 datasets.

One quarterly zip (~14MB) per call replaces the ~2M per-accession fetches the XML path would need.
Writes to the catalog's ``insider_trades`` table (idempotent: re-running a quarter replaces it).
Default universe = US symbols with >=2 shares_out quarters AND price bars (the net-issuance IC set),
so the insider IC can be run on the same panel. Default DB = the sibling `trader` worktree catalog.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.catalog import MarketDataCatalog  # noqa: E402
from data.ingest.sec_form345_bulk import ingest_form345_bulk  # noqa: E402

DEFAULT_DB = ROOT.parent / "trader" / "data" / "store" / "trader.duckdb"


def _universe(db: Path) -> list[str]:
    con = duckdb.connect(str(db), read_only=True)
    rows = con.execute(
        """
        WITH f AS (
            SELECT symbol FROM fundamentals_q
            WHERE market='us' AND shares_out IS NOT NULL GROUP BY symbol HAVING COUNT(*) >= 2
        ), b AS (SELECT DISTINCT symbol FROM bars WHERE market='us')
        SELECT symbol FROM f WHERE symbol IN (SELECT symbol FROM b) ORDER BY symbol
        """
    ).fetchall()
    con.close()
    return [r[0] for r in rows]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--from-year", type=int, default=2010)
    ap.add_argument("--to-year", type=int, default=2024)
    args = ap.parse_args()
    if not args.db.exists():
        raise SystemExit(f"catalog DB not found: {args.db}")

    universe = _universe(args.db)
    quarters = [(y, q) for y in range(args.from_year, args.to_year + 1) for q in (1, 2, 3, 4)]
    print(
        f"universe={len(universe)} symbols; {len(quarters)} quarters "
        f"{args.from_year}q1..{args.to_year}q4",
        flush=True,
    )
    cat = MarketDataCatalog(args.db)
    total = 0
    for y, q in quarters:
        # ingest_form345_bulk isolates fetch/parse failures internally (logs + skips the quarter);
        # a STORAGE failure propagates here and aborts — a broken backfill must not print DONE.
        n = ingest_form345_bulk([(y, q)], cat, symbols=universe)
        total += n
        print(f"  {y}q{q}: +{n} buys (running total {total})", flush=True)
    print(f"DONE: {total} insider open-market buys stored for {len(universe)} symbols", flush=True)


if __name__ == "__main__":
    main()
