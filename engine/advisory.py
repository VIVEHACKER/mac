"""ATR advisory entry/stop/target bands for VALIDATED picks (evidence-aligned).

The session's measurements closed the surge-chasing lane (top surge picks underperform), so
this does NOT select stocks. It takes picks from a VALIDATED selector (the AQR quality+momentum
strategy, which has a measured — if modest, fragile — edge) and attaches ATR-derived ADVISORY
entry / stop / target levels for a human to act on manually (the user trades on Toss, no API).

Split of concerns, on purpose:
  * SELECTION (which stocks) = validated AQR rank — the only part with a measured edge.
  * TIMING/RISK (where to enter, where to cut, where to take) = ATR levels off CURRENT OHLCV —
    advisory risk framing, NOT a prediction. ATR bands do not add edge (chart-only reading
    measured ~zero IC); they frame risk consistently so position sizing and stops are disciplined.

Bands are "as of now" (computed from current bars), so they use live OHLCV, not a pinned
snapshot — they are an operational advisory, not a backtested claim.
"""

from __future__ import annotations

from dataclasses import dataclass

from data.models import PriceBar
from engine.screener import _atr


@dataclass(frozen=True)
class BandConfig:
    atr_window: int = 14
    entry_pullback_atr: float = 0.5  # advisory entry waits for a small ATR pullback below close
    stop_atr_mult: float = 2.0  # stop = entry - this x ATR
    target_atr_mult: float = 3.0  # target = entry + this x ATR


@dataclass(frozen=True)
class AdvisoryBand:
    symbol: str
    market: str
    close: float
    atr: float
    entry: float  # ADVISORY entry (pullback below close)
    stop: float  # ADVISORY stop (risk cap)
    target: float  # ADVISORY target (take-profit)
    reward_risk: float  # (target - entry) / (entry - stop)


def advisory_band(
    symbol: str, market: str, bars: list[PriceBar], config: BandConfig | None = None
) -> AdvisoryBand | None:
    """ATR advisory band for one symbol, or None if there is too little history or zero ATR
    (no volatility => risk cannot be framed). Bars are sorted defensively before ATR."""
    cfg = config or BandConfig()
    if len(bars) < cfg.atr_window + 1:
        return None
    ordered = sorted(bars, key=lambda b: b.ts)
    atr = _atr(ordered, cfg.atr_window)
    if atr <= 0:
        return None
    close = ordered[-1].close
    entry = close - cfg.entry_pullback_atr * atr
    stop = entry - cfg.stop_atr_mult * atr
    target = entry + cfg.target_atr_mult * atr
    risk = entry - stop
    reward_risk = (target - entry) / risk if risk > 0 else 0.0
    return AdvisoryBand(symbol, market, close, atr, entry, stop, target, reward_risk)


def advisory_bands_for(
    picks: list[tuple[str, str]],
    bars_by_symbol: dict[str, list[PriceBar]],
    config: BandConfig | None = None,
) -> list[AdvisoryBand]:
    """Advisory bands for an ordered list of VALIDATED (symbol, market) picks, preserving the
    selector's rank order. Picks with missing/short history or zero ATR are skipped (their risk
    cannot be framed) rather than guessed."""
    bands: list[AdvisoryBand] = []
    for symbol, market in picks:
        bars = bars_by_symbol.get(symbol)
        if not bars:
            continue
        band = advisory_band(symbol, market, bars, config)
        if band is not None:
            bands.append(band)
    return bands
