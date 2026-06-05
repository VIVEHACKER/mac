"""Open Interest (OI) detector for crypto perpetual/futures markets.

Implements the full 4-quadrant OI analysis described in docs/CHART_READING.md §11:
  - 4-quadrant classification (BULL_TREND / BEAR_TREND / SHORT_COVER / LONG_LIQ / NEUTRAL)
  - Rolling OI z-score and extreme/buildup-zone flags
  - Squeeze risk detection (long / short)
  - Cascade liquidation signals
  - OI-price divergence (bearish / bullish)
  - Funding-rate state classification
  - Aggregated ``OISignal`` per bar with direction and strength

No lookahead: bar i may only see bars[0..i] and oi/fr records whose ts <= bars[i].ts.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import date, datetime

from data.models import CryptoFundingRecord, OpenInterestRecord, PriceBar
from engine.chart.types import FundingState, OIQuadrant

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

_Ts = date | datetime


@dataclass(frozen=True)
class OISignal:
    """Per-bar aggregated open-interest analysis result."""

    ts: _Ts
    symbol: str

    # 4-quadrant
    quadrant: OIQuadrant

    # OI values
    oi_value: float | None
    oi_chg_pct: float | None
    oi_zscore: float | None
    oi_buildup: bool
    oi_buildup_streak: int
    oi_extreme: bool
    oi_capitulation: bool

    # Funding
    funding_rate: float | None
    funding_period_hours: float
    funding_state: FundingState | None

    # Squeeze risk
    long_squeeze_risk: bool
    short_squeeze_risk: bool
    long_squeeze_extreme: bool
    short_squeeze_extreme: bool

    # Cascade liquidation
    cascade_long: bool
    cascade_short: bool

    # Divergence
    bearish_div: bool
    bullish_div: bool

    # Summary
    direction: str  # LONG / SHORT / WAIT / AVOID / NEUTRAL
    strength: float  # 0.0 – 1.0  (10 flags / 10)


# ---------------------------------------------------------------------------
# Sub-routines
# ---------------------------------------------------------------------------


def classify_oi_quadrant(price_change: float, oi_change: float) -> OIQuadrant:
    """Map scalar price and OI changes to a 4-quadrant classification.

    Args:
        price_change: close[i] - close[i-1]  (positive = price up)
        oi_change: fractional change in OI, i.e. (oi[i] - oi[i-1]) / oi[i-1]
    """
    price_up = price_change > 0
    price_dn = price_change < 0
    oi_rising = oi_change > 0
    oi_falling = oi_change < 0

    if price_up and oi_rising:
        return OIQuadrant.BULL_TREND
    if price_dn and oi_rising:
        return OIQuadrant.BEAR_TREND
    if price_up and oi_falling:
        return OIQuadrant.SHORT_COVER
    if price_dn and oi_falling:
        return OIQuadrant.LONG_LIQ
    return OIQuadrant.NEUTRAL


def compute_oi_zscore(oi_series: list[float | None], lookback: int = 50) -> float | None:
    """Compute the rolling OI z-score for the last value in *oi_series*.

    Uses population std-dev (pstdev) per the spec.
    Returns None when fewer than ``lookback // 2`` valid samples are available.
    """
    valid = [v for v in oi_series if v is not None]
    if len(valid) < max(1, lookback // 2):
        return None
    mean_oi = statistics.mean(valid)
    stdev_oi = statistics.pstdev(valid)
    last = oi_series[-1]
    if last is None:
        return None
    if stdev_oi == 0:
        return 0.0
    return (last - mean_oi) / stdev_oi


def detect_oi_squeeze_risk(
    *,
    fr_state: FundingState | None,
    oi_rising: bool,
    oi_zscore: float | None,
    zscore_buildup_threshold: float = 1.0,
    zscore_extreme_threshold: float = 2.0,
) -> tuple[bool, bool, bool, bool]:
    """Return (long_squeeze_risk, short_squeeze_risk, long_squeeze_extreme, short_squeeze_extreme).

    Per spec §Step 7: squeeze risk requires BOTH directional funding AND oi rising with
    z-score >= buildup threshold. Funding alone is insufficient.
    """
    if fr_state is None:
        return False, False, False, False

    buildup_ok = oi_rising and oi_zscore is not None and oi_zscore >= zscore_buildup_threshold
    extreme_ok = oi_zscore is not None and abs(oi_zscore) >= zscore_extreme_threshold

    long_squeeze_risk = fr_state in (FundingState.LONG_HEAVY, FundingState.LONG_LEAN) and buildup_ok
    short_squeeze_risk = (
        fr_state in (FundingState.SHORT_HEAVY, FundingState.SHORT_LEAN) and buildup_ok
    )
    long_squeeze_extreme = fr_state is FundingState.LONG_HEAVY and extreme_ok
    short_squeeze_extreme = fr_state is FundingState.SHORT_HEAVY and extreme_ok

    return long_squeeze_risk, short_squeeze_risk, long_squeeze_extreme, short_squeeze_extreme


def detect_cascade_liquidation(
    *,
    price_prev: float,
    price_curr: float,
    oi_prev: float,
    oi_curr: float,
    cascade_price_move_pct: float = 0.02,
    cascade_oi_drop_pct: float = 0.03,
) -> tuple[bool, bool]:
    """Return (cascade_long, cascade_short) for a single bar transition.

    cascade_long : price drops >= threshold AND OI drops >= threshold
    cascade_short: price rises >= threshold AND OI drops >= threshold
    """
    if price_prev <= 0 or oi_prev <= 0:
        return False, False

    price_chg_pct = abs(price_curr - price_prev) / price_prev
    oi_drop_pct = (oi_prev - oi_curr) / oi_prev  # positive when OI falls

    if price_chg_pct < cascade_price_move_pct or oi_drop_pct < cascade_oi_drop_pct:
        return False, False

    cascade_long = price_curr < price_prev  # price dropped
    cascade_short = price_curr > price_prev  # price rose
    return cascade_long, cascade_short


def classify_funding_state(
    funding_rate: float,
    *,
    funding_neutral_threshold: float = 0.0001,
    funding_extreme_threshold: float = 0.0005,
) -> FundingState:
    """Classify a per-period funding rate into a FundingState enum value."""
    if funding_rate >= funding_extreme_threshold:
        return FundingState.LONG_HEAVY
    if funding_rate >= funding_neutral_threshold:
        return FundingState.LONG_LEAN
    if funding_rate <= -funding_extreme_threshold:
        return FundingState.SHORT_HEAVY
    if funding_rate <= -funding_neutral_threshold:
        return FundingState.SHORT_LEAN
    return FundingState.NEUTRAL


# ---------------------------------------------------------------------------
# Direction resolver (spec §"진입 관련성")
# ---------------------------------------------------------------------------


def _resolve_direction(  # noqa: PLR0911,PLR0912
    *,
    quadrant: OIQuadrant,
    cascade_long: bool,
    cascade_short: bool,
    short_squeeze_extreme: bool,
    long_squeeze_extreme: bool,
    short_squeeze_risk: bool,
    long_squeeze_risk: bool,
    oi_capitulation: bool,
) -> str:
    if cascade_long or cascade_short:
        return "AVOID"
    if short_squeeze_extreme:
        return "LONG"
    if long_squeeze_extreme:
        return "SHORT"
    if short_squeeze_risk:
        return "LONG"
    if long_squeeze_risk:
        return "SHORT"
    if quadrant is OIQuadrant.BULL_TREND and not long_squeeze_risk:
        return "LONG"
    if quadrant is OIQuadrant.BEAR_TREND and not short_squeeze_risk:
        return "SHORT"
    if quadrant in (OIQuadrant.SHORT_COVER, OIQuadrant.LONG_LIQ):
        return "WAIT"
    if oi_capitulation:
        return "WAIT"
    return "NEUTRAL"


# ---------------------------------------------------------------------------
# Timestamp alignment helpers
# ---------------------------------------------------------------------------


def _ts_to_comparable(ts: _Ts) -> float:
    """Convert a date or datetime to a float for comparison."""
    if isinstance(ts, datetime):
        return ts.timestamp()
    # date  → treat as midnight
    return datetime(ts.year, ts.month, ts.day).timestamp()


def _align_records(
    bars: list[PriceBar],
    records: list[OpenInterestRecord] | list[CryptoFundingRecord],
    *,
    bar_duration_ms: int,
) -> list[float | None]:
    """Forward-fill `records` onto the `bars` timeline.

    For each bar b at index i, select the most-recent record whose ts <= b.ts.
    Records more than 2 × bar_duration_ms before the bar ts are treated as stale
    and replaced with None.

    Returns a list of floats (OI amount preferred / funding rate) or None.
    """
    # Build sorted (timestamp_s, value) pairs
    pairs: list[tuple[float, float]] = []
    for rec in records:
        if isinstance(rec, OpenInterestRecord):
            val = rec.open_interest_amount  # Amount preferred
            ts_s = _ts_to_comparable(rec.ts)
        elif isinstance(rec, CryptoFundingRecord):
            val = rec.funding_rate
            ts_s = _ts_to_comparable(rec.ts)
        else:
            continue
        pairs.append((ts_s, val))
    pairs.sort(key=lambda x: x[0])

    staleness_s = (2 * bar_duration_ms) / 1000.0
    result: list[float | None] = []

    for bar in bars:
        bar_ts_s = _ts_to_comparable(bar.ts)
        best: float | None = None
        best_ts: float = -math.inf
        for ts_s, val in pairs:
            if ts_s <= bar_ts_s and ts_s > best_ts:
                best_ts = ts_s
                best = val
        # staleness check
        if best is not None and (bar_ts_s - best_ts) > staleness_s:
            best = None
        result.append(best)

    return result


# ---------------------------------------------------------------------------
# Funding period auto-detection
# ---------------------------------------------------------------------------


def _detect_funding_period(fr_records: list[CryptoFundingRecord]) -> float:
    """Detect funding settlement period in hours from history.

    Measures consecutive timestamp gaps (up to 5 pairs) and returns their median.
    Falls back to 8.0 h when fewer than 2 records are available.
    """
    if len(fr_records) < 2:
        return 8.0
    sorted_recs = sorted(fr_records, key=lambda r: _ts_to_comparable(r.ts))
    gaps_h: list[float] = []
    for i in range(1, min(6, len(sorted_recs))):
        dt_s = _ts_to_comparable(sorted_recs[i].ts) - _ts_to_comparable(sorted_recs[i - 1].ts)
        gaps_h.append(dt_s / 3600.0)
    if not gaps_h:
        return 8.0
    return statistics.median(gaps_h)


# ---------------------------------------------------------------------------
# Main aggregator
# ---------------------------------------------------------------------------


def analyze_open_interest(  # noqa: PLR0912,PLR0913,PLR0914,PLR0915
    bars: list[PriceBar],
    oi_records: list[OpenInterestRecord],
    funding_records: list[CryptoFundingRecord] | None = None,
    *,
    oi_zscore_window: int = 50,
    zscore_buildup_threshold: float = 1.0,
    zscore_extreme_threshold: float = 2.0,
    buildup_streak_bars: int = 3,
    funding_neutral_threshold: float = 0.0001,
    funding_extreme_threshold: float = 0.0005,
    cascade_price_move_pct: float = 0.02,
    cascade_oi_drop_pct: float = 0.03,
    capitulation_oi_drop_pct: float = 0.30,
    divergence_lookback: int = 10,
) -> list[OISignal]:
    """Run the full OI analysis pipeline over a bar series.

    Parameters match the spec table (docs/CHART_READING.md §11 Parameters).
    Bars must be sorted chronologically (ascending ts). Only bars[0..i] and records
    with ts <= bars[i].ts are consulted at bar i — no lookahead.

    Returns one OISignal per bar.
    """
    if not bars:
        return []

    fr_records = funding_records or []

    # ------------------------------------------------------------------
    # Step 0.5 — detect funding period
    # ------------------------------------------------------------------
    funding_period_hours = _detect_funding_period(fr_records)

    # ------------------------------------------------------------------
    # Step 1 — align OI and funding records onto bar timeline
    # ------------------------------------------------------------------
    # Estimate bar duration from first two bars; fall back to 1 h
    if len(bars) >= 2:
        dur_s = abs(_ts_to_comparable(bars[1].ts) - _ts_to_comparable(bars[0].ts))
        bar_duration_ms = int(dur_s * 1000)
    else:
        bar_duration_ms = 3_600_000  # 1 h default

    oi_aligned: list[float | None] = _align_records(
        bars,
        oi_records,
        bar_duration_ms=bar_duration_ms,  # type: ignore[arg-type]
    )
    fr_aligned: list[float | None] = (
        _align_records(bars, fr_records, bar_duration_ms=bar_duration_ms)  # type: ignore[arg-type]
        if fr_records
        else [None] * len(bars)
    )

    # ------------------------------------------------------------------
    # Step 2 — bar-by-bar OI change rates
    # ------------------------------------------------------------------
    oi_chg: list[float | None] = [None] * len(bars)
    oi_rising: list[bool] = [False] * len(bars)
    oi_falling: list[bool] = [False] * len(bars)

    for i in range(1, len(bars)):
        prev = oi_aligned[i - 1]
        curr = oi_aligned[i]
        if prev is not None and curr is not None and prev != 0:
            chg = (curr - prev) / prev
            oi_chg[i] = chg
            oi_rising[i] = chg > 0
            oi_falling[i] = chg < 0

    # ------------------------------------------------------------------
    # Guard: feed stagnation (3+ consecutive zero changes → treat as None)
    # ------------------------------------------------------------------
    zero_streak = 0
    for i in range(1, len(bars)):
        if oi_chg[i] is not None and oi_chg[i] == 0.0:
            zero_streak += 1
            if zero_streak >= 3:
                oi_chg[i] = None
                oi_rising[i] = False
                oi_falling[i] = False
        else:
            zero_streak = 0

    # ------------------------------------------------------------------
    # Step 3 — 4-quadrant per bar
    # ------------------------------------------------------------------
    quadrants: list[OIQuadrant] = [OIQuadrant.NEUTRAL] * len(bars)
    for i in range(1, len(bars)):
        oi_change = oi_chg[i]
        if oi_change is None:
            continue
        price_change = bars[i].close - bars[i - 1].close
        quadrants[i] = classify_oi_quadrant(price_change, oi_change)

    # ------------------------------------------------------------------
    # Step 4 — rolling OI z-score
    # ------------------------------------------------------------------
    oi_zscore_list: list[float | None] = [None] * len(bars)
    oi_extreme_list: list[bool] = [False] * len(bars)

    for i in range(len(bars)):
        start = max(0, i - oi_zscore_window + 1)
        window_vals = [v for v in (oi_aligned[j] for j in range(start, i + 1)) if v is not None]
        if len(window_vals) < oi_zscore_window // 2:
            continue
        curr = oi_aligned[i]
        if curr is None:
            continue
        mean_oi = statistics.mean(window_vals)
        stdev_oi = statistics.pstdev(window_vals)
        z = (curr - mean_oi) / stdev_oi if stdev_oi > 0 else 0.0
        oi_zscore_list[i] = z
        oi_extreme_list[i] = abs(z) >= zscore_extreme_threshold

    # ------------------------------------------------------------------
    # Step 5 — OI accumulation streak
    # ------------------------------------------------------------------
    oi_buildup_streak_list: list[int] = [0] * len(bars)
    oi_buildup_list: list[bool] = [False] * len(bars)

    for i in range(1, len(bars)):
        streak = 0
        for j in range(i, 0, -1):
            if oi_chg[j] is not None and oi_chg[j] > 0:  # type: ignore[operator]
                streak += 1
            else:
                break
        oi_buildup_streak_list[i] = streak
        oi_buildup_list[i] = streak >= buildup_streak_bars

    # ------------------------------------------------------------------
    # Step 6 — funding rate classification
    # ------------------------------------------------------------------
    fr_state_list: list[FundingState | None] = [None] * len(bars)
    for i in range(len(bars)):
        rate = fr_aligned[i]
        if rate is not None:
            fr_state_list[i] = classify_funding_state(
                rate,
                funding_neutral_threshold=funding_neutral_threshold,
                funding_extreme_threshold=funding_extreme_threshold,
            )

    # ------------------------------------------------------------------
    # Step 7 — squeeze risk
    # ------------------------------------------------------------------
    long_squeeze_risk_list: list[bool] = [False] * len(bars)
    short_squeeze_risk_list: list[bool] = [False] * len(bars)
    long_squeeze_extreme_list: list[bool] = [False] * len(bars)
    short_squeeze_extreme_list: list[bool] = [False] * len(bars)

    for i in range(len(bars)):
        lsr, ssr, lse, sse = detect_oi_squeeze_risk(
            fr_state=fr_state_list[i],
            oi_rising=oi_rising[i],
            oi_zscore=oi_zscore_list[i],
            zscore_buildup_threshold=zscore_buildup_threshold,
            zscore_extreme_threshold=zscore_extreme_threshold,
        )
        long_squeeze_risk_list[i] = lsr
        short_squeeze_risk_list[i] = ssr
        long_squeeze_extreme_list[i] = lse
        short_squeeze_extreme_list[i] = sse

    # ------------------------------------------------------------------
    # Step 8 — cascade liquidation & capitulation
    # ------------------------------------------------------------------
    cascade_long_list: list[bool] = [False] * len(bars)
    cascade_short_list: list[bool] = [False] * len(bars)
    oi_capitulation_list: list[bool] = [False] * len(bars)

    for i in range(1, len(bars)):
        prev_oi = oi_aligned[i - 1]
        curr_oi = oi_aligned[i]
        if prev_oi is None or curr_oi is None:
            pass
        elif bars[i - 1].close > 0:
            cl, cs = detect_cascade_liquidation(
                price_prev=bars[i - 1].close,
                price_curr=bars[i].close,
                oi_prev=prev_oi,
                oi_curr=curr_oi,
                cascade_price_move_pct=cascade_price_move_pct,
                cascade_oi_drop_pct=cascade_oi_drop_pct,
            )
            cascade_long_list[i] = cl
            cascade_short_list[i] = cs

        # Capitulation: window max OI drop
        start = max(0, i - oi_zscore_window + 1)
        window_oi = [v for v in (oi_aligned[j] for j in range(start, i + 1)) if v is not None]
        curr_oi_i = oi_aligned[i]
        if window_oi and curr_oi_i is not None:
            oi_max_window = max(window_oi)
            if oi_max_window > 0:
                drop = (oi_max_window - curr_oi_i) / oi_max_window
                oi_capitulation_list[i] = drop >= capitulation_oi_drop_pct

    # ------------------------------------------------------------------
    # Step 9 — OI-price divergence
    # ------------------------------------------------------------------
    bearish_div_list: list[bool] = [False] * len(bars)
    bullish_div_list: list[bool] = [False] * len(bars)

    for i in range(len(bars)):
        start = max(0, i - divergence_lookback + 1)
        oi_window = [v for v in (oi_aligned[j] for j in range(start, i + 1)) if v is not None]
        if len(oi_window) < divergence_lookback // 2:
            continue
        curr_oi = oi_aligned[i]
        if curr_oi is None:
            continue
        # pstdev == 0 means all OI identical → stale feed guard
        if statistics.pstdev(oi_window) == 0:
            continue

        # A divergence requires the price to actually *make* a new N-bar extreme: the
        # current close must strictly break the prior window's high/low. Comparing with
        # ``>=``/``<=`` against a window that includes the current bar makes a *flat*
        # price register as both a new high and a new low, firing bearish_div and
        # bullish_div on the same bar (logically incoherent). Using the strictly prior
        # window (bars[start..i-1]) fixes that and matches the spec intent ("가격은 N봉
        # 고점/저점 갱신"). With no prior bar (i == start) neither extreme can form.
        prior_prices = [bars[j].close for j in range(start, i)]
        price_new_high = bool(prior_prices) and bars[i].close > max(prior_prices)
        price_new_low = bool(prior_prices) and bars[i].close < min(prior_prices)
        oi_new_high = curr_oi >= max(oi_window)
        oi_new_low = curr_oi <= min(oi_window)

        bearish_div_list[i] = price_new_high and not oi_new_high
        bullish_div_list[i] = price_new_low and not oi_new_low

    # ------------------------------------------------------------------
    # Step 10 — assemble OISignal per bar
    # ------------------------------------------------------------------
    signals: list[OISignal] = []
    symbol = bars[0].symbol if bars else ""

    for i, bar in enumerate(bars):
        flags = [
            oi_buildup_list[i],
            oi_extreme_list[i],
            long_squeeze_risk_list[i],
            short_squeeze_risk_list[i],
            long_squeeze_extreme_list[i],
            short_squeeze_extreme_list[i],
            cascade_long_list[i],
            cascade_short_list[i],
            bearish_div_list[i],
            bullish_div_list[i],
        ]
        strength = sum(1 for f in flags if f) / 10.0

        direction = _resolve_direction(
            quadrant=quadrants[i],
            cascade_long=cascade_long_list[i],
            cascade_short=cascade_short_list[i],
            short_squeeze_extreme=short_squeeze_extreme_list[i],
            long_squeeze_extreme=long_squeeze_extreme_list[i],
            short_squeeze_risk=short_squeeze_risk_list[i],
            long_squeeze_risk=long_squeeze_risk_list[i],
            oi_capitulation=oi_capitulation_list[i],
        )

        fr_s = fr_state_list[i]

        signals.append(
            OISignal(
                ts=bar.ts,
                symbol=bar.symbol or symbol,
                quadrant=quadrants[i],
                oi_value=oi_aligned[i],
                oi_chg_pct=oi_chg[i],
                oi_zscore=oi_zscore_list[i],
                oi_buildup=oi_buildup_list[i],
                oi_buildup_streak=oi_buildup_streak_list[i],
                oi_extreme=oi_extreme_list[i],
                oi_capitulation=oi_capitulation_list[i],
                funding_rate=fr_aligned[i],
                funding_period_hours=funding_period_hours,
                funding_state=fr_s,
                long_squeeze_risk=long_squeeze_risk_list[i],
                short_squeeze_risk=short_squeeze_risk_list[i],
                long_squeeze_extreme=long_squeeze_extreme_list[i],
                short_squeeze_extreme=short_squeeze_extreme_list[i],
                cascade_long=cascade_long_list[i],
                cascade_short=cascade_short_list[i],
                bearish_div=bearish_div_list[i],
                bullish_div=bullish_div_list[i],
                direction=direction,
                strength=strength,
            )
        )

    return signals
