"""Load a symbol->sector map for the pre-trade sector cap.

The map is produced by ``scripts/fetch_sectors.py`` (SIC -> coarse sector) as
``data/sectors/<universe>-sectors.csv`` with columns ``symbol, sic, sector``. The pre-trade
gate (risk/pretrade.py) and the exposure monitor (risk/exposure.py) both consume a plain
``dict[str, str]``; this is the one place that reads the CSV so callers stay I/O-free.
"""

from __future__ import annotations

import csv
from pathlib import Path


def load_sector_map(path: Path | str) -> dict[str, str]:
    """Read a fetch_sectors CSV into ``{SYMBOL: sector}``.

    Symbols are upper-cased (matching the pre-trade / exposure lookups). Rows with a blank
    sector are skipped — an unclassifiable name must not seed a phantom bucket; the gate already
    skips symbols it cannot classify, so dropping them here keeps the map honest.
    """
    sector_map: dict[str, str] = {}
    with Path(path).open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            symbol = (row.get("symbol") or "").strip().upper()
            sector = (row.get("sector") or "").strip()
            if symbol and sector:
                sector_map[symbol] = sector
    return sector_map
