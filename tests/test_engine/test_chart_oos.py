from __future__ import annotations

import pytest

from engine.chart_oos import (
    ChartSignalEntry,
    append_signal,
    entry_key,
    load_chart_ledger,
    score_chart_ledger,
)


def _entry(symbol: str, ts: str, decision: str, conf: float, price: float) -> ChartSignalEntry:
    return ChartSignalEntry(
        logged_ts=ts,
        symbol=symbol,
        market="crypto",
        timeframe="4h",
        direction="long",
        decision=decision,
        confluence=conf,
        range_pos=0.3,
        mean_reversion=True,
        entry_price=price,
    )


def test_append_is_idempotent_and_refuses_rewrite(tmp_path) -> None:
    path = tmp_path / "ledger.jsonl"
    e = _entry("BTC/USDT", "2026-06-01T00:00:00", "ENTER_NOW", 72.0, 100.0)
    append_signal(path, e)
    loaded = load_chart_ledger(path)
    assert len(loaded) == 1
    assert loaded[0] == e
    # re-recording the same (symbol, tf, ts, direction) is blocked — the ledger is immutable
    with pytest.raises(ValueError):
        append_signal(path, e)


def test_score_buckets_and_act_minus_avoid() -> None:
    entries = [
        _entry("BTC/USDT", "2026-06-01T00:00:00", "ENTER_NOW", 75.0, 100.0),
        _entry("ETH/USDT", "2026-06-01T00:00:00", "SCALE_IN", 60.0, 50.0),
        _entry("SOL/USDT", "2026-06-01T00:00:00", "AVOID", 20.0, 10.0),
        _entry("BNB/USDT", "2026-06-01T00:00:00", "AVOID", 10.0, 5.0),
    ]
    realized = {
        entry_key(entries[0]): 0.04,  # ENTER +4%
        entry_key(entries[1]): 0.00,  # SCALE  0%
        entry_key(entries[2]): -0.02,  # AVOID -2%
        entry_key(entries[3]): 0.01,  # AVOID +1%
        # one immature entry intentionally absent → must be skipped
    }
    rec = score_chart_ledger(entries, realized, horizon=10, backtest_act_avoid=0.0032)

    assert rec.n_matured == 4
    assert rec.act_n == 2
    assert rec.act_mean_fwd == pytest.approx(0.02)  # (0.04 + 0.0) / 2
    assert rec.avoid_mean_fwd == pytest.approx(-0.005)  # (-0.02 + 0.01) / 2
    assert rec.act_minus_avoid == pytest.approx(0.025)
    assert rec.act_hit_rate == pytest.approx(0.5)  # one of two ACT > 0
    assert rec.vs_backtest == pytest.approx(0.025 / 0.0032)


def test_score_skips_immature_entries() -> None:
    entries = [
        _entry("BTC/USDT", "2026-06-01T00:00:00", "ENTER_NOW", 75.0, 100.0),
        _entry("ETH/USDT", "2026-06-02T00:00:00", "ENTER_NOW", 75.0, 50.0),
    ]
    realized = {entry_key(entries[0]): 0.03}  # only the first has matured
    rec = score_chart_ledger(entries, realized, horizon=10)
    assert rec.n_matured == 1
    assert rec.act_n == 1
    assert rec.act_mean_fwd == pytest.approx(0.03)


def test_score_empty_is_safe() -> None:
    rec = score_chart_ledger([], {}, horizon=10)
    assert rec.n_matured == 0
    assert rec.act_minus_avoid == 0.0
    assert rec.vs_backtest is None
