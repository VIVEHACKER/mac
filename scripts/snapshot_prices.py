"""Pin the validation universe's daily closes to a content-hashed snapshot.

Run this once so the compounder validation scripts can load PINNED prices (--prices) instead of
re-downloading from yfinance each run (which drifts the ICs). The snapshot CSV is gitignored
(large); only the manifest is tracked.

Usage:
    python scripts/snapshot_prices.py [NAME]   # default: prices-2026-06-27
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yfinance as yf  # noqa: E402

from data.price_snapshot import write_price_snapshot  # noqa: E402

SNAP_DIR = ROOT / "data" / "snapshots"
UNIVERSES = [
    ROOT / "data" / "universes" / "sp400-600-current.csv",
    ROOT / "data" / "universes" / "megacap-gp.csv",
]
PRICE_START = "2011-01-01"
PRICE_END = "2026-06-27"


def load_symbols() -> list[str]:
    syms: set[str] = set()
    for path in UNIVERSES:
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("symbol"):
                    syms.add(r["symbol"].upper())
    return sorted(syms)


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "prices-2026-06-27"
    symbols = load_symbols()
    print(f"Downloading {len(symbols)} symbols ({PRICE_START}..{PRICE_END})...")
    raw = yf.download(symbols, start=PRICE_START, end=PRICE_END, auto_adjust=True, progress=False)
    closes = raw["Close"]
    manifest = write_price_snapshot(closes, SNAP_DIR, name=name)
    print(f"Wrote {SNAP_DIR / (name + '.csv')}")
    print(f"  rows: {manifest.row_count}  symbols: {manifest.symbol_count}")
    print(f"  dates: {manifest.date_start} … {manifest.date_end}")
    print(f"  sha256: {manifest.sha256}")


if __name__ == "__main__":
    main()
