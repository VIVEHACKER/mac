from __future__ import annotations

from typing import TypedDict

from trader.execution.rebalance import sized_targets


class _SizedTargetArgs(TypedDict):
    aum: float
    marks: dict[str, float]
    vols: dict[str, float]


class _SizedTargetArgsWithDownside(_SizedTargetArgs):
    downside_pct: float


def test_all_targets_respect_hard_concentration_cap() -> None:
    targets = sized_targets(
        ["AAA", "BBB", "CCC"],
        aum=1_000_000.0,
        marks={"AAA": 100.0, "BBB": 200.0, "CCC": 50.0},
        vols={"AAA": 0.35, "BBB": 0.35, "CCC": 0.35},
        max_position_pct=0.08,
    )
    marks = {"AAA": 100.0, "BBB": 200.0, "CCC": 50.0}
    for t in targets:
        weight = t.target_qty * marks[t.symbol] / 1_000_000.0
        assert weight <= 0.08 + 1e-9


def test_higher_downside_shrinks_position() -> None:
    common: _SizedTargetArgs = {
        "aum": 1_000_000.0,
        "marks": {"AAA": 100.0},
        "vols": {"AAA": 0.35},
    }
    low = sized_targets(["AAA"], downside_pct=0.20, **common)[0]
    high = sized_targets(["AAA"], downside_pct=0.40, **common)[0]
    assert high.target_qty <= low.target_qty


def test_kelly_edge_can_reduce_size() -> None:
    common: _SizedTargetArgsWithDownside = {
        "aum": 1_000_000.0,
        "marks": {"AAA": 100.0},
        "vols": {"AAA": 0.35},
        "downside_pct": 0.10,
    }
    no_edge = sized_targets(["AAA"], **common)[0]
    # A weak edge (52% win, +10% up vs -10% down) → half-Kelly ~0.02, far below the
    # risk-cap weight → Kelly binds and the position shrinks.
    with_edge = sized_targets(["AAA"], edges={"AAA": (0.52, 0.10)}, **common)[0]
    assert with_edge.target_qty < no_edge.target_qty


def test_skips_symbols_without_mark_or_vol() -> None:
    targets = sized_targets(
        ["AAA", "NOPRICE", "NOVOL"],
        aum=1_000_000.0,
        marks={"AAA": 100.0, "NOVOL": 100.0},
        vols={"AAA": 0.35, "NOPRICE": 0.35},
    )
    assert {t.symbol for t in targets} == {"AAA"}
