"""Pin the current catalog fundamentals to a content-hashed snapshot.

Run this BEFORE any backtest or paper-drill you intend to reproduce. The
snapshot decouples results from later live-DB drift (the background re-ingest
that broke Variant N). Backtests/paper-drill then load the snapshot CSV instead
of `as_of=None` live reads.

Usage:
    python scripts/snapshot_fundamentals.py [NAME]
Default NAME: "fundamentals-current".
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import duckdb  # noqa: E402

from data.catalog import DEFAULT_CATALOG_PATH  # noqa: E402
from data.fundamentals_snapshot import write_fundamentals_snapshot  # noqa: E402
from data.models import FundamentalRecord  # noqa: E402

SNAP_DIR = ROOT / "data" / "snapshots"
_COLS = [
    "symbol",
    "market",
    "period_end",
    "asof_ts",
    "revenue",
    "operating_income",
    "net_income",
    "free_cash_flow",
    "total_assets",
    "total_equity",
    "total_debt",
    "shares_out",
    "eps",
    "source",
]


def _to_record(row: tuple) -> FundamentalRecord:
    d = dict(zip(_COLS, row, strict=True))
    period_end = d["period_end"]
    asof = d["asof_ts"]
    return FundamentalRecord(
        symbol=d["symbol"],
        market=d["market"],
        period_end=period_end
        if isinstance(period_end, date)
        else date.fromisoformat(str(period_end)),
        asof_ts=asof if isinstance(asof, datetime) else datetime.fromisoformat(str(asof)),
        revenue=d["revenue"],
        operating_income=d["operating_income"],
        net_income=d["net_income"],
        free_cash_flow=d["free_cash_flow"],
        total_assets=d["total_assets"],
        total_equity=d["total_equity"],
        total_debt=d["total_debt"],
        shares_out=d["shares_out"],
        eps=d["eps"],
        source=d["source"] or "",
    )


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "fundamentals-current"
    db_path = ROOT / DEFAULT_CATALOG_PATH
    con = duckdb.connect(str(db_path), read_only=True)
    rows = con.execute(f"SELECT {', '.join(_COLS)} FROM fundamentals_q").fetchall()
    con.close()
    records = [_to_record(r) for r in rows]
    manifest = write_fundamentals_snapshot(records, SNAP_DIR, name=name)
    print(f"Wrote {SNAP_DIR / (name + '.csv')}")
    print(f"  records: {manifest.record_count}  symbols: {manifest.symbol_count}")
    print(f"  period:  {manifest.period_start} … {manifest.period_end}")
    print(f"  sha256:  {manifest.sha256}")


if __name__ == "__main__":
    main()
