from __future__ import annotations

import pytest

from scripts.compounder_heldout_oos import (
    partial_spearman,
    sector_neutral_ic,
    spearman,
)


def test_partial_spearman_removes_size_proxy_effect() -> None:
    # x and y look correlated only because both are monotonic in z.
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    ys = [1.0, 2.0, 3.0, 4.0, 5.0]
    zs = [1.0, 2.0, 3.0, 4.0, 5.0]

    assert spearman(xs, ys) == pytest.approx(1.0)
    assert partial_spearman(xs, ys, zs) is None


def test_partial_spearman_retains_effect_not_explained_by_size() -> None:
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    ys = [1.0, 2.0, 4.0, 8.0, 16.0]
    zs = [5.0, 1.0, 4.0, 2.0, 3.0]

    result = partial_spearman(xs, ys, zs)
    assert result is not None
    assert result > 0.9


def test_sector_neutral_ic_averages_within_sector_rank_ic() -> None:
    vals = {f"A{i}": float(i) for i in range(12)} | {f"B{i}": float(i) for i in range(12)}
    fwd = {f"A{i}": float(i) for i in range(12)} | {f"B{i}": float(11 - i) for i in range(12)}
    sectors = {f"A{i}": "tech" for i in range(12)} | {f"B{i}": "industrial" for i in range(12)}

    # One sector is +1, the other is -1, equal count-weighted => near zero.
    assert sector_neutral_ic(vals, fwd, sectors) == pytest.approx(0.0)
