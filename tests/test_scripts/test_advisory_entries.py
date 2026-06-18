"""Tests for the advisory-entries CLI formatting (pure; the fdr fetch is network, not tested)."""

from __future__ import annotations

from engine.advisory import AdvisoryBand
from scripts.advisory_entries import format_bands


def _band(symbol: str) -> AdvisoryBand:
    return AdvisoryBand(
        symbol=symbol,
        market="kr",
        close=100.0,
        atr=4.0,
        entry=98.0,
        stop=90.0,
        target=110.0,
        reward_risk=1.5,
    )


def test_format_bands_renders_levels_and_risk_label() -> None:
    out = format_bands([_band("005930"), _band("000660")], market="kr")
    assert "RISK FRAMING" in out  # honest header — bands frame risk, not a prediction
    assert "005930" in out and "000660" in out
    assert "98.00" in out  # entry
    assert "90.00" in out  # stop
    assert "110.00" in out  # target


def test_format_bands_empty_is_explicit() -> None:
    out = format_bands([], market="us")
    assert "no scorable picks" in out


def test_format_bands_shows_selection_label() -> None:
    out = format_bands([_band("X")], market="us", selection="validated US AQR top-7")
    assert "validated US AQR top-7" in out


def test_aqr_us_picks_returns_ranked_us_tuples() -> None:
    # Validated-selection wiring: ranks the pinned AQR megacap universe (no network).
    from scripts.advisory_entries import _aqr_us_picks

    picks = _aqr_us_picks(5)
    assert len(picks) == 5
    assert all(market == "us" for _, market in picks)
    assert all(isinstance(sym, str) and sym for sym, _ in picks)
