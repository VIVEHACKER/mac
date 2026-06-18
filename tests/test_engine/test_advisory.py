"""Tests for ATR advisory entry/stop/target bands (evidence-aligned, for validated picks)."""

from __future__ import annotations

from datetime import date, timedelta

from data.models import PriceBar
from engine.advisory import AdvisoryBand, BandConfig, advisory_band, advisory_bands_for


def _bars(
    symbol: str, closes: list[float], *, flat: bool = False, market: str = "kr"
) -> list[PriceBar]:
    start = date(2026, 1, 1)
    out = []
    for i, c in enumerate(closes):
        hi = c if flat else c * 1.02
        lo = c if flat else c * 0.98
        out.append(
            PriceBar(
                symbol=symbol,
                market=market,
                source_symbol=symbol,
                ts=start + timedelta(days=i),
                open=c,
                high=hi,
                low=lo,
                close=c,
                volume=1000.0,
            )
        )
    return out


def test_band_levels_are_ordered() -> None:
    band = advisory_band("X", "kr", _bars("X", [100.0 + i * 0.5 for i in range(40)]))
    assert band is not None
    assert band.atr > 0
    assert band.stop < band.entry < band.close
    assert band.target > band.entry


def test_reward_risk_matches_config_multiples() -> None:
    cfg = BandConfig(stop_atr_mult=2.0, target_atr_mult=3.0)
    band = advisory_band("X", "kr", _bars("X", [100.0] * 40), config=cfg)
    # flat=False default -> ATR>0; with equal-ish closes the RR is target_mult/stop_mult = 1.5.
    assert band is not None
    assert round(band.reward_risk, 4) == 1.5


def test_insufficient_history_returns_none() -> None:
    assert advisory_band("X", "kr", _bars("X", [100.0] * 5)) is None  # < atr_window+1


def test_zero_atr_returns_none() -> None:
    # No volatility (high=low=close, constant) => risk cannot be framed.
    assert advisory_band("X", "kr", _bars("X", [100.0] * 40, flat=True)) is None


def test_unsorted_bars_handled() -> None:
    ordered = _bars("X", [100.0 + i * 0.5 for i in range(40)])
    shuffled = [ordered[i] for i in (*range(20, 40), *range(0, 20))]
    assert advisory_band("X", "kr", shuffled) == advisory_band("X", "kr", ordered)


def test_bands_for_preserves_order_and_skips_unscorable() -> None:
    bars_by = {
        "A": _bars("A", [100.0 + i * 0.5 for i in range(40)]),
        "B": _bars("B", [50.0 + i * 0.3 for i in range(40)]),
        "FLAT": _bars("FLAT", [100.0] * 40, flat=True),  # zero ATR -> skipped
        "SHORT": _bars("SHORT", [100.0] * 5),  # too little history -> skipped
    }
    picks = [("A", "kr"), ("MISSING", "kr"), ("FLAT", "kr"), ("SHORT", "kr"), ("B", "kr")]
    bands = advisory_bands_for(picks, bars_by)
    assert [b.symbol for b in bands] == ["A", "B"]  # order preserved, unscorable/missing skipped
    assert all(isinstance(b, AdvisoryBand) for b in bands)
