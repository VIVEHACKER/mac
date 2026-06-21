from __future__ import annotations

import pytest

from engine.fund_book import SleeveTarget, assemble_fund_book
from engine.fund_exposure import compute_exposure, format_exposure


def _book(sleeves, max_name_weight=0.08):
    return assemble_fund_book(sleeves, max_name_weight=max_name_weight)


# --------------------------------------------------------------------------- #
# sector exposure
# --------------------------------------------------------------------------- #


def test_sector_grouping_and_counts():
    book = _book([SleeveTarget("core", 0.40, {"A": 0.5, "B": 0.5})], max_name_weight=1.0)
    sectors = {"A": "Tech", "B": "Tech"}
    rep = compute_exposure(book, sectors)
    assert len(rep.sector_exposures) == 1
    se = rep.sector_exposures[0]
    assert se.sector == "Tech"
    assert se.weight == pytest.approx(0.40)
    assert se.n_names == 2


def test_multi_sector_max_sector():
    book = _book([SleeveTarget("core", 0.60, {"A": 0.5, "B": 0.5})], max_name_weight=1.0)
    sectors = {"A": "Tech", "B": "Energy"}
    rep = compute_exposure(book, sectors)
    assert rep.max_sector_weight == pytest.approx(0.30)
    assert {se.sector for se in rep.sector_exposures} == {"Tech", "Energy"}


def test_unknown_sector_when_missing_or_none():
    book = _book([SleeveTarget("core", 0.40, {"A": 1.0})], max_name_weight=1.0)
    assert compute_exposure(book, None).sector_exposures[0].sector == "Unknown"
    assert compute_exposure(book, {"X": "Tech"}).sector_exposures[0].sector == "Unknown"


# --------------------------------------------------------------------------- #
# sleeve attribution
# --------------------------------------------------------------------------- #


def test_sleeve_attribution_sums_to_invested():
    book = _book(
        [SleeveTarget("core", 0.35, {"A": 0.5, "B": 0.5}), SleeveTarget("hunt", 0.15, {"C": 1.0})],
        max_name_weight=1.0,
    )
    rep = compute_exposure(book, {})
    attr = {a.sleeve: a.weight for a in rep.sleeve_attribution}
    assert attr["core"] == pytest.approx(0.35)
    assert attr["hunt"] == pytest.approx(0.15)
    assert sum(a.weight for a in rep.sleeve_attribution) == pytest.approx(book.invested)


def test_capped_overlapping_name_attribution_scaled_to_invested():
    # A in both core (0.35*0.5=0.175) and bridge (0.15*0.5=0.075) -> raw 0.25 capped to 0.08.
    # attribution must scale the contributions to the capped 0.08, split proportionally, and the
    # whole book's sleeve attribution must still sum to invested.
    book = _book(
        [
            SleeveTarget("core", 0.35, {"A": 0.5, "B": 0.5}),
            SleeveTarget("bridge", 0.15, {"A": 1.0}),
        ],
        max_name_weight=0.08,
    )
    rep = compute_exposure(book, {})
    total = sum(a.weight for a in rep.sleeve_attribution)
    assert total == pytest.approx(book.invested)
    # A raw = core 0.35*0.5=0.175 + bridge 0.15*1.0=0.15 = 0.325 -> capped 0.08, scale 0.08/0.325.
    #   A core-share = 0.175*0.08/0.325, A bridge-share = 0.15*0.08/0.325.
    # B (core only) = 0.175 -> capped 0.08 (core attribution).
    attr = {a.sleeve: a.weight for a in rep.sleeve_attribution}
    a_core = 0.175 * 0.08 / 0.325
    a_bridge = 0.15 * 0.08 / 0.325
    assert attr["core"] == pytest.approx(a_core + 0.08, abs=1e-9)
    assert attr["bridge"] == pytest.approx(a_bridge, abs=1e-9)


# --------------------------------------------------------------------------- #
# concentration
# --------------------------------------------------------------------------- #


def test_effective_n_of_equal_book():
    # 4 names each 0.10 (invested 0.40) -> effN = invested^2 / Σw^2 = 0.16 / (4*0.01) = 4.0
    book = _book(
        [SleeveTarget("core", 0.40, {"A": 0.25, "B": 0.25, "C": 0.25, "D": 0.25})],
        max_name_weight=1.0,
    )
    rep = compute_exposure(book, {})
    assert rep.effective_n == pytest.approx(4.0)
    assert rep.herfindahl == pytest.approx(4 * 0.10**2)


def test_top_name_and_top_n_weight():
    book = _book(
        [SleeveTarget("core", 1.0, {"A": 0.5, "B": 0.3, "C": 0.2})],
        max_name_weight=1.0,
    )
    rep = compute_exposure(book, {}, top_n=2)
    assert rep.top_name == "A"
    assert rep.top_name_weight == pytest.approx(0.5)
    assert rep.top_n_weight == pytest.approx(0.8)  # A + B


# --------------------------------------------------------------------------- #
# flags / edges / format
# --------------------------------------------------------------------------- #


def test_sector_concentration_flag_fires():
    book = _book([SleeveTarget("core", 0.60, {"A": 1.0})], max_name_weight=1.0)
    rep = compute_exposure(book, {"A": "Tech"}, sector_warn=0.40)  # 0.60 > 0.40
    assert any("섹터" in f for f in rep.flags)


def test_capped_name_flag_fires():
    book = _book([SleeveTarget("core", 0.50, {"A": 1.0})], max_name_weight=0.08)
    rep = compute_exposure(book, {})
    assert any("캡" in f for f in rep.flags)


def test_empty_book_is_zero_report():
    book = _book([SleeveTarget("core", 0.0, {})], max_name_weight=0.08)
    rep = compute_exposure(book, {})
    assert rep.n_positions == 0
    assert rep.effective_n == pytest.approx(0.0)
    assert any("빈" in f for f in rep.flags)


def test_invalid_params_raise():
    book = _book([SleeveTarget("core", 0.40, {"A": 1.0})], max_name_weight=1.0)
    with pytest.raises(ValueError):
        compute_exposure(book, {}, sector_warn=1.5)
    with pytest.raises(ValueError):
        compute_exposure(book, {}, top_n=0)


def test_format_exposure_has_framing_and_lines():
    book = _book([SleeveTarget("core", 0.40, {"A": 0.5, "B": 0.5})], max_name_weight=1.0)
    text = format_exposure(compute_exposure(book, {"A": "Tech", "B": "Energy"}))
    assert "리스크 모델 아님" in text  # descriptive-not-risk-model framing
    assert "섹터" in text and "슬리브" in text
