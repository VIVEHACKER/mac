"""Content-hashed snapshots of PIT fundamental records, for reproducibility.

A background re-ingest silently changed ``fundamentals_q`` (3,383 → 7,291
records) and made a documented backtest non-reproducible. Pinning a backtest to
a content-hashed snapshot guarantees the inputs are byte-stable: the same hash
implies the same fundamentals, regardless of later live-DB drift.

The CSV is written in the exact schema ``data.fundamentals_csv`` reads, so a
snapshot is a drop-in fundamentals source. The hash is computed over a
canonical (sorted, fixed-format) serialization, so it is order-independent.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from data.models import FundamentalRecord

# Column order matches data.fundamentals_csv expectations.
SNAPSHOT_COLUMNS = (
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
    "gross_profit",
    "cost_of_revenue",
    "source",
)

_NUMERIC = (
    "revenue",
    "operating_income",
    "net_income",
    "free_cash_flow",
    "total_assets",
    "total_equity",
    "total_debt",
    "shares_out",
    "eps",
    "gross_profit",
    "cost_of_revenue",
)

# The content hash excludes `source` (provenance, not financial data): `data.fundamentals_csv`
# defaults empty source to "csv:fundamentals" on load, which would break round-trip
# verification. See `_hash_columns()` — the hash basis is derived per-snapshot from its column
# set so adding an optional column never invalidates older pins.


@dataclass(frozen=True)
class SnapshotManifest:
    """Provenance for a fundamentals snapshot."""

    name: str
    sha256: str
    record_count: int
    symbol_count: int
    period_start: str
    period_end: str
    columns: tuple[str, ...]


def _cell(record: FundamentalRecord, column: str) -> str:
    if column == "symbol":
        return record.symbol
    if column == "market":
        return record.market
    if column == "period_end":
        return record.period_end.isoformat()
    if column == "asof_ts":
        return record.asof_ts.isoformat()
    if column == "source":
        return record.source
    value = getattr(record, column)
    if value is None:
        return ""
    fval = float(value)
    # NaN/inf are normalized to "missing" — repr(nan) reloads as nan and nan != nan
    # would make the hash non-reproducible. repr() round-trips finite floats exactly.
    if not math.isfinite(fval):
        return ""
    return repr(fval)


def _csv_lines(records: Sequence[FundamentalRecord]) -> list[str]:
    """Sorted full rows (including source) for the CSV body."""
    return sorted(",".join(_cell(rec, col) for col in SNAPSHOT_COLUMNS) for rec in records)


def _hash_columns(columns: Sequence[str]) -> tuple[str, ...]:
    """The hash basis = the given column set minus the provenance-only ``source`` field."""
    return tuple(c for c in columns if c != "source")


def _hash_lines(records: Sequence[FundamentalRecord], hcols: Sequence[str]) -> list[str]:
    """Sorted rows over ``hcols`` — the canonical basis for the content hash."""
    return sorted(",".join(_cell(rec, col) for col in hcols) for rec in records)


def snapshot_sha256(
    records: Sequence[FundamentalRecord], columns: Sequence[str] = SNAPSHOT_COLUMNS
) -> str:
    """Order-independent content hash of the records over ``columns`` (excludes ``source``).

    The column set is parameterized so a snapshot written under an OLD schema (fewer columns)
    still verifies against its own manifest: pass the manifest's recorded ``columns``. Adding a
    new optional column to ``SNAPSHOT_COLUMNS`` therefore does NOT break old snapshots.
    """
    hcols = _hash_columns(columns)
    digest = hashlib.sha256()
    digest.update(",".join(hcols).encode("utf-8"))
    digest.update(b"\n")
    for line in _hash_lines(records, hcols):
        digest.update(line.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def write_fundamentals_snapshot(
    records: Sequence[FundamentalRecord], out_dir: Path, name: str
) -> SnapshotManifest:
    """Write ``<out_dir>/<name>.csv`` + ``<name>.manifest.json``; return manifest."""
    if not records:
        raise ValueError("cannot snapshot an empty record set")
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = _csv_lines(records)
    header = ",".join(SNAPSHOT_COLUMNS)
    (out_dir / f"{name}.csv").write_text(header + "\n" + "\n".join(lines) + "\n", encoding="utf-8")

    periods = sorted(rec.period_end for rec in records)
    manifest = SnapshotManifest(
        name=name,
        sha256=snapshot_sha256(records),
        record_count=len(records),
        symbol_count=len({rec.symbol for rec in records}),
        period_start=periods[0].isoformat(),
        period_end=periods[-1].isoformat(),
        columns=SNAPSHOT_COLUMNS,
    )
    (out_dir / f"{name}.manifest.json").write_text(
        json.dumps(asdict(manifest), indent=2), encoding="utf-8"
    )
    return manifest


def read_fundamentals_snapshot(csv_path: Path, verify: bool = True) -> list[FundamentalRecord]:
    """Load a snapshot CSV, optionally verifying its content hash against the manifest."""
    from data.fundamentals_csv import load_fundamentals_csv

    records = load_fundamentals_csv(csv_path)
    if verify:
        manifest_path = csv_path.with_suffix(".manifest.json")
        if not manifest_path.exists():
            # Fail closed: no manifest means no tamper detection.
            raise ValueError(
                f"missing manifest for {csv_path.name}; cannot verify snapshot integrity "
                f"(pass verify=False to load unverified)"
            )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected = manifest["sha256"]
        # Hash against the manifest's recorded column set so snapshots written under an older
        # schema still verify (a newly-added optional column must not invalidate old pins).
        cols = tuple(manifest.get("columns") or SNAPSHOT_COLUMNS)
        actual = snapshot_sha256(records, columns=cols)
        if actual != expected:
            raise ValueError(
                f"snapshot hash mismatch for {csv_path.name}: "
                f"expected {expected[:12]}…, got {actual[:12]}… (tampered or stale)"
            )
        # Null out any numeric field NOT covered by the manifest's columns: the hash only
        # verified `cols`, so an extra CSV column (e.g. gross_profit added to an old-schema
        # snapshot) is UNVERIFIED and must not leak into downstream metrics.
        unverified = [c for c in _NUMERIC if c not in cols]
        if unverified:
            blanks = dict.fromkeys(unverified, None)
            records = [FundamentalRecord(**{**asdict(r), **blanks}) for r in records]
    return records
