"""A-1 게이트: FVG 미동반 CHoCH 구조 가중 캡 (CHARTBLOOM_VALIDATION_RESULTS.md).

검증 근거: CHoCH 단독은 forward 음수(t=-2.3@+24, n=1591), CHoCH+FVG는 양수 유의(t=3.0).
→ FVG 미동반 CHoCH-driven 구조 가중을 _W_STRUCTURE_NOFVG_CHOCH로 캡.
"""

from types import SimpleNamespace

from engine.chart import read
from engine.chart.read import (
    _W_STRUCTURE,
    _W_STRUCTURE_COMBO,
    _W_STRUCTURE_NOFVG_CHOCH,
    _has_supporting_fvg,
    _structure_vote,
)
from engine.chart.types import TrendBias


def _ev(event_type, bar_index):
    return SimpleNamespace(event_type=event_type, bar_index=bar_index)


def _fvg(direction, *, mitigated=False, mitigation_type="none"):
    return SimpleNamespace(
        direction=direction, mitigated=mitigated, mitigation_type=mitigation_type
    )


def _feat(events, active_fvgs, bias=TrendBias.BULLISH):
    ms = SimpleNamespace(trend_bias=bias, events=events)
    fr = SimpleNamespace(active_fvgs=active_fvgs)
    return SimpleNamespace(structure=ms, fvgs=fr)


def test_choch_without_supporting_fvg_is_capped():
    feat = _feat([_ev("internal_CHoCH", 10)], [])  # 반전 CHoCH, FVG 없음
    v = _structure_vote(feat, "long")
    assert v is not None
    assert v.weight == _W_STRUCTURE_NOFVG_CHOCH
    assert "A-1" in v.note


def test_choch_with_supporting_fvg_not_capped():
    feat = _feat([_ev("internal_CHoCH", 10)], [_fvg("bullish")])  # 동일방향 FVG 동반
    v = _structure_vote(feat, "long")
    assert v.weight >= _W_STRUCTURE  # 캡 미적용


def test_opposite_direction_fvg_does_not_support():
    feat = _feat([_ev("internal_CHoCH", 10)], [_fvg("bearish")])  # 반대방향 FVG는 지지 아님
    v = _structure_vote(feat, "long")
    assert v.weight == _W_STRUCTURE_NOFVG_CHOCH


def test_mitigated_fvg_does_not_support():
    feat = _feat([_ev("internal_CHoCH", 10)], [_fvg("bullish", mitigation_type="full")])
    v = _structure_vote(feat, "long")
    assert v.weight == _W_STRUCTURE_NOFVG_CHOCH


def test_latest_event_bos_not_capped():
    # CHoCH@5 후 BOS@12 (최신=BOS, 연속성) → 캡 미적용 + 콤보 가중
    feat = _feat([_ev("internal_CHoCH", 5), _ev("internal_BOS", 12)], [])
    v = _structure_vote(feat, "long")
    assert v.weight == _W_STRUCTURE_COMBO


def test_gate_off_restores_full_weight(monkeypatch):
    monkeypatch.setattr(read, "_CHOCH_FVG_GATE", False)
    feat = _feat([_ev("internal_CHoCH", 10)], [])
    v = _structure_vote(feat, "long")
    assert v.weight == _W_STRUCTURE


def test_has_supporting_fvg_helper():
    assert _has_supporting_fvg(_feat([], [_fvg("bullish")]), TrendBias.BULLISH) is True
    assert _has_supporting_fvg(_feat([], [_fvg("bearish")]), TrendBias.BULLISH) is False
    assert _has_supporting_fvg(_feat([], []), TrendBias.BULLISH) is False
