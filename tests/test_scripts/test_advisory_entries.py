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
    assert "RISK FRAMING ONLY" in out  # honest header — not a validated signal
    assert "005930" in out and "000660" in out
    assert "98.00" in out  # entry
    assert "90.00" in out  # stop
    assert "110.00" in out  # target


def test_format_bands_empty_is_explicit() -> None:
    out = format_bands([], market="us")
    assert "no scorable picks" in out
