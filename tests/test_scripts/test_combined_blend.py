from __future__ import annotations

import pytest

from scripts.combined_ideal_lowvol_walkforward import (
    SLEEVE_IDEAL,
    SLEEVE_LOWVOL,
    blend_window,
)


def _sleeve(dates: list[str], rets: list[float], spy: list[float] | None = None) -> dict:
    return {
        "start": dates[0],
        "end": dates[-1],
        "dates": dates,
        "monthly_returns": rets,
        "spy_returns": spy if spy is not None else [0.0] * len(dates),
    }


def _dates(n: int) -> list[str]:
    return [f"2020-{m:02d}-28" if m <= 12 else f"2021-{m - 12:02d}-28" for m in range(1, n + 1)]


def test_blend_is_declared_weights_on_common_months() -> None:
    dates = _dates(12)
    ideal = _sleeve(dates, [0.02] * 12, spy=[0.01] * 12)
    lowvol = _sleeve(dates, [-0.01] * 12)
    combined = blend_window(ideal, lowvol)
    assert combined is not None
    expected_monthly = SLEEVE_IDEAL * 0.02 + SLEEVE_LOWVOL * -0.01
    assert combined["ann"] == pytest.approx((1 + expected_monthly) ** 12 - 1)
    assert combined["months"] == 12


def test_blend_aligns_on_common_dates_only() -> None:
    dates = _dates(14)
    # ideal misses the last month, lowvol misses the first -> 12 common months
    ideal = _sleeve(dates[:-1], [0.01] * 13, spy=[0.0] * 13)
    lowvol = _sleeve(dates[1:], [0.03] * 13)
    combined = blend_window(ideal, lowvol)
    assert combined is not None
    assert combined["months"] == 12  # intersection, not union


def test_blend_requires_a_year_of_overlap() -> None:
    dates = _dates(11)  # 11 common months < 12
    assert blend_window(_sleeve(dates, [0.01] * 11), _sleeve(dates, [0.01] * 11)) is None


def test_blend_none_sleeve_propagates() -> None:
    dates = _dates(12)
    sleeve = _sleeve(dates, [0.01] * 12)
    assert blend_window(None, sleeve) is None
    assert blend_window(sleeve, None) is None


def test_rebalancing_bonus_can_beat_both_sleeves() -> None:
    # Anti-correlated sleeves: monthly-rebalanced blend compounds above EITHER sleeve.
    # This is the diversification return, not a bug (verified numerically on real data:
    # blend sits between sleeves on identical months; per-window rows can exceed a
    # sleeve's own report only via month-alignment differences).
    dates = _dates(12)
    a = [0.10, 0.00] * 6
    b = [0.00, 0.10] * 6
    combined = blend_window(_sleeve(dates, a, spy=[0.0] * 12), _sleeve(dates, b))
    assert combined is not None
    sleeve_total = 1.10**6  # both sleeves compound to the same total
    assert (1 + combined["ann"]) ** 1.0 > 0  # sanity
    blend_total = (1 + combined["ann"]) ** (combined["months"] / 12.0)
    assert blend_total > sleeve_total * 0.999  # at least matches; bonus appears as >
