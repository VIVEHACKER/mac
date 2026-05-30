"""Fetch current S&P 400 (mid) + S&P 600 (small) constituents from iShares
IJH/IJR holdings CSVs and write a universe CSV for the compounder scan.

Pure functions (parse/write) are unit-tested; network fetch lives in main().
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# iShares holdings CSV download endpoints (fileType=csv). May change/block;
# main() falls back to a curated list if these fail.
ISHARES_URLS = {
    "us-mid-cap": "https://www.ishares.com/us/products/239763/ishares-core-sp-midcap-etf/1467271812596.ajax?fileType=csv&fileName=IJH_holdings&dataType=fund",
    "us-small-cap": "https://www.ishares.com/us/products/239774/ishares-core-sp-small-cap-etf/1467271812596.ajax?fileType=csv&fileName=IJR_holdings&dataType=fund",
}


def parse_ishares_holdings(text: str) -> list[str]:
    """Extract equity tickers from an iShares holdings CSV.

    The file has a metadata preamble, then a header row whose first column is
    "Ticker", then holdings rows. Keep rows whose Asset Class == "Equity" and
    whose ticker is a real symbol.
    """
    lines = text.splitlines()
    header_idx = None
    for i, line in enumerate(lines):
        # header row starts with the Ticker column (quoted or not)
        first = line.split(",", 1)[0].strip().strip('"')
        if first == "Ticker":
            header_idx = i
            break
    if header_idx is None:
        return []
    reader = csv.DictReader(io.StringIO("\n".join(lines[header_idx:])))
    out: list[str] = []
    for row in reader:
        ticker = (row.get("Ticker") or "").strip()
        asset_class = (row.get("Asset Class") or "").strip()
        if asset_class == "Equity" and ticker and ticker not in {"-", "USD"}:
            out.append(ticker.upper())
    return out
