"""Tests for data.fundamentals_snapshot — reproducible PIT fundamental pins.

Motivation: a background re-ingest silently doubled ``fundamentals_q``
(3,383 → 7,291 records) and made the documented Variant N backtest
non-reproducible (CAGR 19.91% → 14.04%). Snapshotting pins the dataset by
content hash so a backtest can be replayed against an exact, verified version.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from data.fundamentals_csv import load_fundamentals_csv
from data.fundamentals_snapshot import (
    SnapshotManifest,
    read_fundamentals_snapshot,
    snapshot_sha256,
    write_fundamentals_snapshot,
)
from data.models import FundamentalRecord


def _rec(symbol: str, period: str, asof: str, ni: float | None = 100.0) -> FundamentalRecord:
    return FundamentalRecord(
        symbol=symbol,
        market="us",
        period_end=date.fromisoformat(period),
        asof_ts=datetime.fromisoformat(asof),
        net_income=ni,
        total_equity=500.0,
        shares_out=10.0,
        free_cash_flow=80.0,
        source="sec_edgar:companyfacts",
    )


def _sample() -> list[FundamentalRecord]:
    return [
        _rec("AAPL", "2023-12-31", "2024-02-01"),
        _rec("AAPL", "2024-03-31", "2024-05-01"),
        _rec("MSFT", "2023-12-31", "2024-01-25", ni=None),
    ]


def test_write_snapshot_creates_csv_and_manifest(tmp_path) -> None:
    manifest = write_fundamentals_snapshot(_sample(), tmp_path, name="2026-05-29")
    assert isinstance(manifest, SnapshotManifest)
    assert (tmp_path / "2026-05-29.csv").exists()
    assert (tmp_path / "2026-05-29.manifest.json").exists()
    assert manifest.record_count == 3
    assert manifest.symbol_count == 2
    assert manifest.period_start == "2023-12-31"
    assert manifest.period_end == "2024-03-31"
    assert len(manifest.sha256) == 64


def test_snapshot_round_trips_through_load_fundamentals_csv(tmp_path) -> None:
    records = _sample()
    write_fundamentals_snapshot(records, tmp_path, name="snap")
    loaded = load_fundamentals_csv(tmp_path / "snap.csv")
    assert len(loaded) == len(records)
    by_key = {(r.symbol, r.period_end): r for r in loaded}
    for r in records:
        got = by_key[(r.symbol, r.period_end)]
        assert got.net_income == r.net_income
        assert got.total_equity == r.total_equity
        assert got.asof_ts == r.asof_ts


def test_hash_is_order_independent(tmp_path) -> None:
    records = _sample()
    shuffled = [records[2], records[0], records[1]]
    assert snapshot_sha256(records) == snapshot_sha256(shuffled)


def test_hash_changes_when_a_value_changes() -> None:
    base = _sample()
    mutated = list(base)
    mutated[0] = _rec("AAPL", "2023-12-31", "2024-02-01", ni=999.0)
    assert snapshot_sha256(base) != snapshot_sha256(mutated)


def test_read_snapshot_verifies_hash(tmp_path) -> None:
    write_fundamentals_snapshot(_sample(), tmp_path, name="snap")
    loaded = read_fundamentals_snapshot(tmp_path / "snap.csv", verify=True)
    assert len(loaded) == 3


def test_read_snapshot_detects_tampering(tmp_path) -> None:
    write_fundamentals_snapshot(_sample(), tmp_path, name="snap")
    csv_path = tmp_path / "snap.csv"
    text = csv_path.read_text(encoding="utf-8")
    csv_path.write_text(text.replace("100.0", "100.5"), encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        read_fundamentals_snapshot(csv_path, verify=True)


def test_manifest_hash_matches_snapshot_sha256(tmp_path) -> None:
    records = _sample()
    manifest = write_fundamentals_snapshot(records, tmp_path, name="snap")
    assert manifest.sha256 == snapshot_sha256(records)


def test_empty_source_round_trips_and_verifies(tmp_path) -> None:
    # source defaults to 'csv:fundamentals' on load; the hash must ignore source
    # (provenance, not data) so an empty-source record still verifies.
    rec = FundamentalRecord(
        symbol="ZZZ",
        market="us",
        period_end=date(2024, 3, 31),
        asof_ts=datetime(2024, 5, 1),
        net_income=10.0,
        source="",
    )
    write_fundamentals_snapshot([rec], tmp_path, name="snap")
    loaded = read_fundamentals_snapshot(tmp_path / "snap.csv", verify=True)
    assert len(loaded) == 1


def test_read_raises_when_manifest_missing(tmp_path) -> None:
    write_fundamentals_snapshot(_sample(), tmp_path, name="snap")
    (tmp_path / "snap.manifest.json").unlink()
    with pytest.raises(ValueError, match="manifest"):
        read_fundamentals_snapshot(tmp_path / "snap.csv", verify=True)


def test_nan_and_inf_normalized_to_missing(tmp_path) -> None:
    nan_rec = FundamentalRecord(
        symbol="NAN",
        market="us",
        period_end=date(2024, 3, 31),
        asof_ts=datetime(2024, 5, 1),
        net_income=float("nan"),
        free_cash_flow=float("inf"),
        total_equity=42.0,
        source="x",
    )
    # hash must be reproducible despite NaN (NaN != NaN would otherwise break it)
    assert snapshot_sha256([nan_rec]) == snapshot_sha256([nan_rec])
    write_fundamentals_snapshot([nan_rec], tmp_path, name="snap")
    loaded = read_fundamentals_snapshot(tmp_path / "snap.csv", verify=True)
    assert loaded[0].net_income is None
    assert loaded[0].free_cash_flow is None
    assert loaded[0].total_equity == 42.0


def test_none_value_round_trips(tmp_path) -> None:
    rec = FundamentalRecord(
        symbol="NUL",
        market="us",
        period_end=date(2024, 3, 31),
        asof_ts=datetime(2024, 5, 1),
        net_income=None,
        total_equity=100.0,
        source="x",
    )
    write_fundamentals_snapshot([rec], tmp_path, name="snap")
    loaded = read_fundamentals_snapshot(tmp_path / "snap.csv", verify=True)
    assert loaded[0].net_income is None
    assert loaded[0].total_equity == 100.0
