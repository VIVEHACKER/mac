"""Content-hashed snapshots of daily close prices, for reproducible validation.

The compounder validation scripts pull prices from yfinance, which is UNPINNED: each run
returns slightly different history (late-arriving adjusted closes, vendor revisions), so the
forward-return ICs drift run-to-run (observed +0.031..+0.070 for the same fundamentals
snapshot). Pinning the close matrix by content hash makes a validation byte-reproducible: same
hash => same prices => same IC, independent of later yfinance drift.

Stored long-format (symbol,date,close) so the hash is order-independent and NaN-robust, the
same discipline as data.fundamentals_snapshot. read_price_snapshot returns a wide DataFrame
(date index x symbol columns) that is a drop-in for the `closes` used by the validation scripts.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class PriceManifest:
    name: str
    sha256: str
    row_count: int
    symbol_count: int
    date_start: str
    date_end: str


def _long_rows(closes: pd.DataFrame) -> list[str]:
    """Sorted 'symbol,date,repr(close)' lines, dropping NaN/inf (non-reproducible) cells."""
    rows: list[str] = []
    for sym in closes.columns:
        col = closes[sym]
        for ts, val in col.items():
            f = float(val)
            if not math.isfinite(f):
                continue
            rows.append(f"{sym},{pd.Timestamp(ts).date().isoformat()},{f!r}")
    rows.sort()
    return rows


def price_sha256(closes: pd.DataFrame) -> str:
    """Order-independent content hash of the close matrix (NaN/inf cells excluded)."""
    return rows_sha256(_long_rows(closes))


def rows_sha256(rows: list[str]) -> str:
    """Order-independent content hash of stored ``symbol,date,close`` rows."""
    digest = hashlib.sha256()
    digest.update(b"symbol,date,close\n")
    for line in sorted(rows):
        digest.update(line.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def write_price_snapshot(closes: pd.DataFrame, out_dir: Path, name: str) -> PriceManifest:
    """Write ``<out_dir>/<name>.csv`` (long format) + ``<name>.manifest.json``; return manifest."""
    if closes.empty:
        raise ValueError("cannot snapshot an empty price matrix")
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = _long_rows(closes)
    (out_dir / f"{name}.csv").write_text(
        "symbol,date,close\n" + "\n".join(lines) + "\n", encoding="utf-8"
    )
    dates = sorted({line.split(",")[1] for line in lines})
    syms = {line.split(",")[0] for line in lines}
    manifest = PriceManifest(
        name=name,
        sha256=price_sha256(closes),
        row_count=len(lines),
        symbol_count=len(syms),
        date_start=dates[0] if dates else "",
        date_end=dates[-1] if dates else "",
    )
    (out_dir / f"{name}.manifest.json").write_text(
        json.dumps(asdict(manifest), indent=2), encoding="utf-8"
    )
    return manifest


def read_price_snapshot(csv_path: Path, verify: bool = True) -> pd.DataFrame:
    """Load a long-format price snapshot into a wide DataFrame (date index x symbol columns).

    Drop-in for the `closes` DataFrame the validation scripts index as ``closes[sym]``.
    """
    df = pd.read_csv(csv_path)
    wide = df.pivot(index="date", columns="symbol", values="close")
    wide.index = pd.to_datetime(wide.index)
    wide = wide.sort_index()
    if verify:
        manifest_path = csv_path.with_suffix(".manifest.json")
        if not manifest_path.exists():
            raise ValueError(
                f"missing manifest for {csv_path.name}; cannot verify price snapshot integrity "
                f"(pass verify=False to load unverified)"
            )
        expected = json.loads(manifest_path.read_text(encoding="utf-8"))["sha256"]
        lines = csv_path.read_text(encoding="utf-8").splitlines()
        actual = rows_sha256([line for line in lines[1:] if line.strip()])
        if actual != expected:
            raise ValueError(
                f"price snapshot hash mismatch for {csv_path.name}: "
                f"expected {expected[:12]}…, got {actual[:12]}… (tampered or stale)"
            )
    return wide
