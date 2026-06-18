"""Quality-gated momentum/volume surge screener — ADVISORY, NOT a validated edge.

Catches "surging" names (momentum + volume spike) while excluding 잡주 (junk) via a liquidity
gate and an optional quality gate. Pure and market-agnostic: it works on any OHLCV PriceBar
series (pykrx for KR, Alpaca for US), so it can rank both markets the user trades on Toss.

DISCIPLINE (read this): this is a SCREEN + ADVISORY entry/exit bands, NOT a proven signal.
Prior measurement in this repo showed raw cross-sectional momentum picking has ~zero rank-IC
and 52w-high proximity has none; the surge/volume and catalyst dimensions are UNVALIDATED.
So output is advisory-only and must be edge-validated (forward rank-IC, like scripts/
picking_skill.py) before it is ever wired to capital. The entry/stop/target are ATR-derived
suggestions for a human to act on manually, not an instruction the system should auto-fire.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import fmean, pstdev

from data.models import PriceBar


@dataclass(frozen=True)
class ScreenConfig:
    momentum_lookback: int = 60  # bars over which "momentum" (price run-up) is measured
    surge_window: int = 5  # recent window whose volume is compared to the baseline
    volume_base_window: int = 20  # trailing baseline window for average volume
    atr_window: int = 14
    min_avg_dollar_volume: float = 0.0  # liquidity gate — the primary 잡주 exclusion
    min_quality: float = 0.0  # quality gate (only applied when a quality map is supplied)
    min_momentum: float = 0.0  # require a positive run-up by default
    min_volume_surge: float = 1.5  # recent avg volume must be >= this x the baseline
    entry_pullback_atr: float = 0.5  # advisory entry waits for a small ATR pullback
    stop_atr_mult: float = 2.0
    target_atr_mult: float = 3.0
    top_n: int = 20


@dataclass(frozen=True)
class Candidate:
    symbol: str
    market: str
    close: float
    momentum: float
    volume_surge: float
    avg_dollar_volume: float
    quality: float | None
    atr: float
    entry: float  # ADVISORY entry (close minus a small ATR pullback)
    stop: float  # ADVISORY stop (entry minus stop_atr_mult x ATR)
    target: float  # ADVISORY target (entry plus target_atr_mult x ATR)
    score: float
    reasons: tuple[str, ...] = field(default_factory=tuple)


def _atr(bars: list[PriceBar], window: int) -> float:
    """Simple ATR: mean true range over the last ``window`` bars."""
    if len(bars) < 2:
        return 0.0
    trs: list[float] = []
    for prev, cur in zip(bars[-window - 1 :], bars[-window:], strict=False):
        trs.append(max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close)))
    return fmean(trs) if trs else 0.0


def _avg_dollar_volume(bars: list[PriceBar], window: int) -> float:
    recent = bars[-window:]
    if not recent:
        return 0.0
    return fmean([b.close * b.volume for b in recent])


def _momentum(bars: list[PriceBar], lookback: int) -> float:
    if len(bars) <= lookback:
        return 0.0
    past = bars[-1 - lookback].close
    if past <= 0:
        return 0.0
    return bars[-1].close / past - 1.0


def _volume_surge(bars: list[PriceBar], surge_window: int, base_window: int) -> float:
    """Recent average volume divided by the trailing baseline average volume (>1 = surge)."""
    if len(bars) < base_window + surge_window:
        return 0.0
    recent = bars[-surge_window:]
    base = bars[-base_window - surge_window : -surge_window]
    base_avg = fmean([b.volume for b in base]) if base else 0.0
    recent_avg = fmean([b.volume for b in recent]) if recent else 0.0
    if base_avg <= 0:
        return 0.0
    return recent_avg / base_avg


def _z_scores(values: list[float]) -> list[float]:
    if len(values) < 2:
        return [0.0 for _ in values]
    mu = fmean(values)
    sigma = pstdev(values)
    if sigma == 0:
        return [0.0 for _ in values]
    return [(v - mu) / sigma for v in values]


def screen_surge(
    bars_by_symbol: dict[str, list[PriceBar]],
    config: ScreenConfig | None = None,
    *,
    quality: dict[str, float] | None = None,
) -> list[Candidate]:
    """Rank quality-gated surging names. ``quality`` (optional) maps symbol -> a quality metric
    (e.g. a z-score the caller computed from fundamentals); when supplied, names below
    ``config.min_quality`` are excluded. Returns up to ``config.top_n`` ADVISORY candidates."""
    cfg = config or ScreenConfig()
    min_bars = max(
        cfg.momentum_lookback + 1, cfg.volume_base_window + cfg.surge_window, cfg.atr_window + 1
    )

    passed: list[tuple[str, str, float, float, float, float, float | None, float]] = []
    for symbol, bars in bars_by_symbol.items():
        if len(bars) < min_bars:
            continue
        last = bars[-1]
        adv = _avg_dollar_volume(bars, cfg.volume_base_window)
        if adv < cfg.min_avg_dollar_volume:  # liquidity gate — exclude 잡주
            continue
        q = quality.get(symbol) if quality is not None else None
        if quality is not None and (q is None or q < cfg.min_quality):  # quality gate
            continue
        mom = _momentum(bars, cfg.momentum_lookback)
        if mom < cfg.min_momentum:
            continue
        surge = _volume_surge(bars, cfg.surge_window, cfg.volume_base_window)
        if surge < cfg.min_volume_surge:
            continue
        atr = _atr(bars, cfg.atr_window)
        passed.append((symbol, last.market, last.close, mom, surge, adv, q, atr))

    if not passed:
        return []

    mom_z = _z_scores([row[3] for row in passed])
    surge_z = _z_scores([row[4] for row in passed])
    candidates: list[Candidate] = []
    for i, (symbol, market, close, mom, surge, adv, q, atr) in enumerate(passed):
        score = mom_z[i] + surge_z[i]
        entry = close - cfg.entry_pullback_atr * atr
        candidates.append(
            Candidate(
                symbol=symbol,
                market=market,
                close=close,
                momentum=mom,
                volume_surge=surge,
                avg_dollar_volume=adv,
                quality=q,
                atr=atr,
                entry=entry,
                stop=entry - cfg.stop_atr_mult * atr,
                target=entry + cfg.target_atr_mult * atr,
                score=score,
                reasons=(
                    f"momentum={mom:+.1%}",
                    f"volume_surge={surge:.2f}x",
                    f"adv=${adv:,.0f}",
                ),
            )
        )
    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates[: cfg.top_n]
