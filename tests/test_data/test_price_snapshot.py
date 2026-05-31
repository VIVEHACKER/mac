"""Tests for data.price_snapshot — reproducible pinned close-price snapshots.

Motivation: yfinance prices are unpinned, so forward-return ICs drift run-to-run for the SAME
fundamentals snapshot. Pinning the close matrix by content hash makes a validation byte-stable.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from data.price_snapshot import (
    PriceManifest,
    price_sha256,
    read_price_snapshot,
    write_price_snapshot,
)


def _closes() -> pd.DataFrame:
    idx = pd.to_datetime(["2020-01-02", "2020-01-03", "2020-01-06"])
    return pd.DataFrame({"AAA": [10.0, 11.0, 12.5], "BBB": [100.0, np.nan, 99.0]}, index=idx)


def test_write_creates_csv_and_manifest(tmp_path) -> None:
    m = write_price_snapshot(_closes(), tmp_path, name="px")
    assert isinstance(m, PriceManifest)
    assert (tmp_path / "px.csv").exists()
    assert (tmp_path / "px.manifest.json").exists()
    # 3 AAA + 2 BBB (the NaN cell is dropped)
    assert m.row_count == 5
    assert m.symbol_count == 2
    assert m.date_start == "2020-01-02"
    assert m.date_end == "2020-01-06"
    assert len(m.sha256) == 64


def test_round_trip_returns_wide_frame_and_verifies(tmp_path) -> None:
    write_price_snapshot(_closes(), tmp_path, name="px")
    wide = read_price_snapshot(tmp_path / "px.csv", verify=True)
    assert wide.loc[pd.Timestamp("2020-01-02"), "AAA"] == 10.0
    assert wide.loc[pd.Timestamp("2020-01-06"), "BBB"] == 99.0
    # the dropped NaN comes back as NaN in the wide pivot
    assert pd.isna(wide.loc[pd.Timestamp("2020-01-03"), "BBB"])


def test_hash_is_order_independent() -> None:
    a = _closes()
    b = a[["BBB", "AAA"]]  # reordered columns
    assert price_sha256(a) == price_sha256(b)


def test_tampering_detected(tmp_path) -> None:
    write_price_snapshot(_closes(), tmp_path, name="px")
    p = tmp_path / "px.csv"
    p.write_text(p.read_text(encoding="utf-8").replace("12.5", "13.5"), encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        read_price_snapshot(p, verify=True)


def test_missing_manifest_fails_closed(tmp_path) -> None:
    write_price_snapshot(_closes(), tmp_path, name="px")
    (tmp_path / "px.manifest.json").unlink()
    with pytest.raises(ValueError, match="manifest"):
        read_price_snapshot(tmp_path / "px.csv", verify=True)


def test_empty_matrix_rejected(tmp_path) -> None:
    with pytest.raises(ValueError, match="empty"):
        write_price_snapshot(pd.DataFrame(), tmp_path, name="px")
