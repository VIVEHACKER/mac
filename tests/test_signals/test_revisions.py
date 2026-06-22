from __future__ import annotations

from datetime import date

import pytest

from signals.revisions import EstimateRevision, forward_ic, revision_signals
from strategies._base import StrategySignal

AS_OF = date(2026, 6, 22)


def _rev(
    symbol: str,
    *,
    tp: float | None = 100.0,
    tp_prev: float | None = 100.0,
    eps: float | None = 1.0,
    eps_prev: float | None = 1.0,
    n_up: int = 0,
    n_down: int = 0,
    n_total: int = 5,
) -> EstimateRevision:
    return EstimateRevision(
        symbol=symbol,
        market="us",
        as_of=AS_OF,
        target_price=tp,
        target_price_prev=tp_prev,
        eps_estimate=eps,
        eps_estimate_prev=eps_prev,
        n_up=n_up,
        n_down=n_down,
        n_total=n_total,
    )


def _score(sig: StrategySignal) -> float:
    return sig.score


# --------------------------------------------------------------------------- #
# component math
# --------------------------------------------------------------------------- #


def test_target_price_component_only():
    # tp +10%, eps flat, breadth 0 -> score = w_tp(0.35) * 0.10
    sigs = revision_signals([_rev("A", tp=110.0, tp_prev=100.0)])
    assert sigs[0].score == pytest.approx(0.35 * 0.10)
    assert sigs[0].direction == "up"


def test_eps_component_only():
    sigs = revision_signals([_rev("A", eps=1.1, eps_prev=1.0)])
    assert sigs[0].score == pytest.approx(0.40 * 0.10)


def test_breadth_component_only():
    # n_up 5, n_down 1, n_total 6 -> breadth (5-1)/6; score = w_breadth(0.25)*breadth
    sigs = revision_signals([_rev("A", n_up=5, n_down=1, n_total=6)])
    assert sigs[0].score == pytest.approx(0.25 * (4 / 6))


def test_blend_of_all_three():
    sigs = revision_signals(
        [_rev("A", tp=110.0, tp_prev=100.0, eps=1.1, eps_prev=1.0, n_up=4, n_down=0, n_total=4)]
    )
    expected = 0.35 * 0.10 + 0.40 * 0.10 + 0.25 * (4 / 4)
    assert sigs[0].score == pytest.approx(expected)


# --------------------------------------------------------------------------- #
# guards (no div-by-zero, undefined -> 0 component)
# --------------------------------------------------------------------------- #


def test_zero_prev_target_price_contributes_zero():
    sigs = revision_signals([_rev("A", tp=110.0, tp_prev=0.0)])  # tp component guarded -> 0
    assert sigs[0].score == pytest.approx(0.0)


def test_zero_current_target_price_contributes_zero():
    # 현재 타겟이 0/누락(피드 인코딩)일 때 tp 컴포넌트 미정의 — 거짓 다운그레이드 방지(Codex P2)
    sigs = revision_signals([_rev("A", tp=0.0, tp_prev=100.0)])
    assert sigs[0].score == pytest.approx(0.0)


def test_zero_prev_eps_contributes_zero():
    sigs = revision_signals([_rev("A", eps=1.1, eps_prev=0.0)])
    assert sigs[0].score == pytest.approx(0.0)


def test_none_fields_contribute_zero():
    sigs = revision_signals([_rev("A", tp=None, tp_prev=None, eps=None, eps_prev=None)])
    assert sigs[0].score == pytest.approx(0.0)
    assert sigs[0].direction == "flat"


# --------------------------------------------------------------------------- #
# screen / direction / order
# --------------------------------------------------------------------------- #


def test_thin_coverage_is_screened_out():
    sigs = revision_signals([_rev("A", n_total=2)], min_coverage=3)  # below coverage -> dropped
    assert sigs == []


def test_direction_down_on_negative_revisions():
    sigs = revision_signals(
        [_rev("A", tp=90.0, tp_prev=100.0, eps=0.9, eps_prev=1.0, n_up=0, n_down=4, n_total=4)]
    )
    assert sigs[0].score < 0
    assert sigs[0].direction == "down"


def test_flat_when_within_threshold():
    sigs = revision_signals([_rev("A")], up_threshold=0.01)  # all flat inputs -> 0 -> flat
    assert sigs[0].direction == "flat"


def test_sorted_by_score_desc():
    sigs = revision_signals(
        [
            _rev("LOW", eps=1.02, eps_prev=1.0),
            _rev("HIGH", eps=1.20, eps_prev=1.0),
            _rev("MID", eps=1.10, eps_prev=1.0),
        ]
    )
    assert [s.symbol for s in sigs] == ["HIGH", "MID", "LOW"]


def test_empty_input_returns_empty():
    assert revision_signals([]) == []


def test_reason_quotes_components():
    sigs = revision_signals([_rev("A", tp=110.0, tp_prev=100.0)])
    assert "목표가" in sigs[0].reason and "EPS" in sigs[0].reason


def test_invalid_min_coverage_raises():
    with pytest.raises(ValueError):
        revision_signals([_rev("A")], min_coverage=0)


def test_downgrade_filter_rejects_any_downgrade():
    # the video's signature rule: any target-price downgrade (n_down>0) -> drop with max_downgrades=0
    revs = [
        _rev("CLEAN", n_up=3, n_down=0, n_total=5),  # all up/flat -> kept
        _rev("BLUE", n_up=4, n_down=1, n_total=5),  # one downgrade -> dropped
    ]
    kept = {s.symbol for s in revision_signals(revs, max_downgrades=0)}
    assert kept == {"CLEAN"}
    # default (None) keeps both (continuous score only)
    assert {s.symbol for s in revision_signals(revs)} == {"CLEAN", "BLUE"}


def test_invalid_max_downgrades_raises():
    with pytest.raises(ValueError):
        revision_signals([_rev("A")], max_downgrades=-1)


# --------------------------------------------------------------------------- #
# forward_ic (validate-before-trust gate)
# --------------------------------------------------------------------------- #


def test_forward_ic_monotone_is_positive():
    scores = {"A": 0.1, "B": 0.2, "C": 0.3, "D": 0.4}
    rets = {"A": 0.01, "B": 0.02, "C": 0.03, "D": 0.04}  # perfectly rank-aligned
    assert forward_ic(scores, rets) == pytest.approx(1.0)


def test_forward_ic_inverted_is_negative():
    scores = {"A": 0.1, "B": 0.2, "C": 0.3, "D": 0.4}
    rets = {"A": 0.04, "B": 0.03, "C": 0.02, "D": 0.01}
    assert forward_ic(scores, rets) == pytest.approx(-1.0)


def test_forward_ic_too_few_overlap_is_none():
    assert forward_ic({"A": 0.1, "B": 0.2}, {"A": 0.01, "B": 0.02}) is None  # n<3
    assert forward_ic({"A": 0.1}, {"Z": 0.01}) is None  # no overlap
