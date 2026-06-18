"""Tests for the quality-gated momentum/volume surge screener (advisory)."""

from __future__ import annotations

from datetime import date, timedelta

from data.models import PriceBar
from engine.screener import Candidate, ScreenConfig, screen_surge

_N = 80


def _bars(
    symbol: str, closes: list[float], volumes: list[float], market: str = "kr"
) -> list[PriceBar]:
    start = date(2026, 1, 1)
    return [
        PriceBar(
            symbol=symbol,
            market=market,
            source_symbol=symbol,
            ts=start + timedelta(days=i),
            open=c,
            high=c * 1.02,
            low=c * 0.98,
            close=c,
            volume=v,
        )
        for i, (c, v) in enumerate(zip(closes, volumes, strict=True))
    ]


def _rising(start: float = 100.0, step: float = 0.7, n: int = _N) -> list[float]:
    return [start + i * step for i in range(n)]


def _falling(start: float = 160.0, step: float = 0.7, n: int = _N) -> list[float]:
    return [start - i * step for i in range(n)]


def _surge_vol(
    base: float = 1000.0, surge: float = 3000.0, window: int = 5, n: int = _N
) -> list[float]:
    return [base] * (n - window) + [surge] * window


def _flat_vol(v: float = 1000.0, n: int = _N) -> list[float]:
    return [v] * n


def test_liquidity_gate_excludes_junk() -> None:
    cfg = ScreenConfig(min_avg_turnover=100_000)
    bars = {
        "GOOD": _bars("GOOD", _rising(), _surge_vol(base=1000, surge=3000)),
        "JUNK": _bars("JUNK", _rising(), _surge_vol(base=5, surge=15)),  # same surge, tiny $vol
    }
    syms = [c.symbol for c in screen_surge(bars, cfg)]
    assert "GOOD" in syms
    assert "JUNK" not in syms  # 잡주 excluded by the liquidity gate


def test_quality_gate_excludes_low_quality_and_unknown() -> None:
    bars = {
        "A": _bars("A", _rising(), _surge_vol()),
        "B": _bars("B", _rising(), _surge_vol()),
        "C": _bars("C", _rising(), _surge_vol()),
    }
    # quality provided -> below min_quality excluded, and a symbol missing from the map excluded.
    result = screen_surge(bars, ScreenConfig(min_quality=0.0), quality={"A": 1.0, "B": -1.0})
    syms = [c.symbol for c in result]
    assert "A" in syms
    assert "B" not in syms  # below min_quality
    assert "C" not in syms  # unknown quality is treated as a fail when a quality map is supplied


def test_volume_surge_gate_excludes_non_surging() -> None:
    bars = {
        "SURGE": _bars("SURGE", _rising(), _surge_vol()),
        "FLAT": _bars("FLAT", _rising(), _flat_vol()),
    }
    syms = [c.symbol for c in screen_surge(bars)]  # default min_volume_surge=1.5
    assert "SURGE" in syms
    assert "FLAT" not in syms


def test_momentum_gate_excludes_decliners() -> None:
    bars = {
        "UP": _bars("UP", _rising(), _surge_vol()),
        "DOWN": _bars("DOWN", _falling(), _surge_vol()),
    }
    syms = [c.symbol for c in screen_surge(bars)]  # default min_momentum=0.0
    assert "UP" in syms
    assert "DOWN" not in syms


def test_ranking_prefers_stronger_momentum_and_surge() -> None:
    bars = {
        "BIG": _bars("BIG", _rising(step=1.2), _surge_vol(surge=4000)),
        "SMALL": _bars("SMALL", _rising(step=0.3), _surge_vol(surge=1800)),
    }
    result = screen_surge(bars)
    assert result[0].symbol == "BIG"


def test_advisory_bands_are_ordered() -> None:
    bars = {"GOOD": _bars("GOOD", _rising(), _surge_vol())}
    c: Candidate = screen_surge(bars)[0]
    assert c.atr > 0
    assert c.entry < c.close  # advisory entry waits for a pullback
    assert c.stop < c.entry
    assert c.target > c.entry


def test_top_n_limit() -> None:
    bars = {f"S{i}": _bars(f"S{i}", _rising(step=0.5 + i * 0.1), _surge_vol()) for i in range(5)}
    result = screen_surge(bars, ScreenConfig(top_n=2))
    assert len(result) == 2


def test_insufficient_history_skipped() -> None:
    bars = {"SHORT": _bars("SHORT", _rising(n=10), _surge_vol(n=10, window=2))}
    assert screen_surge(bars) == []


def test_unsorted_bars_are_sorted_defensively() -> None:
    # Codex P2: an out-of-order series must be sorted by ts before window calcs, so the result
    # matches the sorted input (here: a clean surging name that should pass).
    ordered = _bars("X", _rising(), _surge_vol())
    shuffled = [ordered[i] for i in (*range(40, _N), *range(0, 40))]  # second half first
    from_ordered = screen_surge({"X": ordered})
    from_shuffled = screen_surge({"X": shuffled})
    assert len(from_shuffled) == 1
    assert from_shuffled[0].close == from_ordered[0].close
    assert from_shuffled[0].momentum == from_ordered[0].momentum
    assert from_shuffled[0].volume_surge == from_ordered[0].volume_surge
