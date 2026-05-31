from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from scripts.compounder_oos_validation import (
    annual_turnover,
    composite_values,
    spearman,
    write_price_snapshot_path,
)


def test_spearman_handles_perfect_rankings() -> None:
    assert spearman([1.0, 2.0, 3.0], [10.0, 20.0, 30.0]) == pytest.approx(1.0)
    assert spearman([1.0, 2.0, 3.0], [30.0, 20.0, 10.0]) == pytest.approx(-1.0)


def test_composite_values_orients_higher_and_lower_better_metrics() -> None:
    metrics: dict[str, dict[str, float | None]] = {
        "A": {"gross_profitability": 3.0, "ps": 1.0},
        "B": {"gross_profitability": 2.0, "ps": 5.0},
        "C": {"gross_profitability": 1.0, "ps": 10.0},
    }

    values = composite_values(metrics, [("gross_profitability", 1), ("ps", -1)])

    assert values["A"] > values["B"] > values["C"]
    assert values["A"] == pytest.approx((1.0 + (1.0 - 1.0 / 3.0)) / 2.0)
    assert values["B"] == pytest.approx((2.0 / 3.0 + (1.0 - 2.0 / 3.0)) / 2.0)
    assert values["C"] == pytest.approx((1.0 / 3.0 + 0.0) / 2.0)


def test_annual_turnover_uses_top_decile_replacements() -> None:
    cells = {
        "gross_quality": {
            date(2020, 6, 30): {3: {"top": ["A", "B"]}},
            date(2021, 6, 30): {3: {"top": ["A", "C"]}},
            date(2022, 6, 30): {3: {"top": ["D", "C"]}},
        }
    }

    turnover = annual_turnover(cells, "gross_quality", 3)

    # One of two names is replaced in each roughly one-year interval.
    assert turnover == pytest.approx(0.5, abs=0.002)


def test_write_price_snapshot_path_writes_manifest_next_to_csv(tmp_path) -> None:
    closes = pd.DataFrame(
        {"AAA": [10.0, 11.0]},
        index=pd.to_datetime(["2020-01-02", "2020-01-03"]),
    )
    csv_path = tmp_path / "prices.csv"

    write_price_snapshot_path(closes, csv_path)

    assert csv_path.exists()
    assert (tmp_path / "prices.manifest.json").exists()


def test_write_price_snapshot_path_requires_csv_suffix(tmp_path) -> None:
    closes = pd.DataFrame({"AAA": [10.0]}, index=pd.to_datetime(["2020-01-02"]))
    with pytest.raises(ValueError, match=".csv"):
        write_price_snapshot_path(closes, tmp_path / "prices")
