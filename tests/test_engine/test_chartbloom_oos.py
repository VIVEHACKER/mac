"""A-1 forward-OOS CHoCH 원장 (engine/chartbloom_oos.py) 단위 테스트."""

import pytest

from engine.chartbloom_oos import (
    ChochSignalEntry,
    append_choch_signal,
    entry_key,
    load_chartbloom_ledger,
    score_chartbloom_ledger,
)


def _e(symbol="BTC/USDT", ts="2026-06-19T00:00:00", direction="long", has_fvg=True, price=100.0):
    return ChochSignalEntry(
        logged_ts=ts,
        symbol=symbol,
        market="crypto",
        timeframe="4h",
        direction=direction,
        has_fvg=has_fvg,
        entry_price=price,
    )


def test_append_and_load_roundtrip(tmp_path):
    p = tmp_path / "led.jsonl"
    append_choch_signal(p, _e(ts="2026-06-19T00:00:00"))
    append_choch_signal(p, _e(ts="2026-06-19T04:00:00", has_fvg=False))
    rows = load_chartbloom_ledger(p)
    assert len(rows) == 2
    assert rows[0].has_fvg is True and rows[1].has_fvg is False


def test_append_only_refuses_duplicate(tmp_path):
    p = tmp_path / "led.jsonl"
    e = _e()
    append_choch_signal(p, e)
    with pytest.raises(ValueError, match="append-only"):
        append_choch_signal(p, e)  # 동일 identity 재기록 거부


def test_entry_key_distinguishes_direction(tmp_path):
    assert entry_key(_e(direction="long")) != entry_key(_e(direction="short"))


def test_score_spread_positive_when_fvg_better():
    # +FVG 평균 +2%, no-FVG 평균 -1% → spread +3%p (A-1 방향)
    entries = [
        _e(ts="t1", has_fvg=True),
        _e(ts="t2", has_fvg=True),
        _e(ts="t3", has_fvg=False),
        _e(ts="t4", has_fvg=False),
    ]
    realized = {
        entry_key(entries[0]): 0.03,
        entry_key(entries[1]): 0.01,
        entry_key(entries[2]): -0.02,
        entry_key(entries[3]): 0.00,
    }
    rec = score_chartbloom_ledger(entries, realized, horizon=12, insample_spread=0.0104)
    assert rec.n_matured == 4
    assert rec.with_fvg_mean_fwd == pytest.approx(0.02)
    assert rec.no_fvg_mean_fwd == pytest.approx(-0.01)
    assert rec.fvg_minus_nofvg == pytest.approx(0.03)
    assert rec.vs_insample == pytest.approx(0.03 / 0.0104)


def test_score_skips_immature_entries():
    entries = [_e(ts="t1", has_fvg=True), _e(ts="t2", has_fvg=False)]
    realized = {entry_key(entries[0]): 0.05}  # 두 번째는 미성숙
    rec = score_chartbloom_ledger(entries, realized, horizon=12)
    assert rec.n_matured == 1
    assert rec.with_fvg_n == 1 and rec.no_fvg_n == 0
    assert rec.vs_insample is None  # insample_spread 미지정
