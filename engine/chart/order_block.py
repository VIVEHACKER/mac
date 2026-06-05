"""ICT/SMC Order Block detector.

Detects Bullish and Bearish Order Blocks (OB) from a chronological list of PriceBars
following the canonical ICT algorithm described in docs/CHART_READING.md §3 "오더블록".

Key design invariants
---------------------
* **No lookahead**: at the detection phase for bar t, only bars[0..t] are visible.
  Swing points require *swing_lookback* right-side confirming bars, so a swing at
  index i is only finalised when bar i+swing_lookback has closed.
* Mitigation / breaker state is updated retroactively (on later bars) — that is fine.
* Pure Python stdlib; no pandas / numpy / TA-Lib.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from data.models import PriceBar
from engine.chart.types import (
    TrendBias,
    bar_body,
    bar_range,
    is_bearish,
    is_bullish,
)

# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class OrderBlock:
    """A detected ICT Order Block (or Breaker Block after mitigation)."""

    ob_index: int
    direction: str  # 'bullish' | 'bearish'
    zone_high: float
    zone_low: float
    zone_mid: float
    mitigation_extreme: float  # always full-wick origin extreme, independent of use_body_only
    ts: date | datetime
    bos_ts: date | datetime
    strength: float  # 0.0 – 1.0
    has_fvg: bool = False
    mitigated: bool = False
    mitigation_ts: date | datetime | None = None
    liquidity_swept: bool = False
    is_breaker: bool = False
    breaker_direction: str | None = None  # 'bearish_breaker' | 'bullish_breaker'
    breaker_retest_ts: date | datetime | None = None
    htf_confluence: bool = False
    oi_confirmation: bool = False
    visited: bool = False


# ---------------------------------------------------------------------------
# ATR (Wilder's Smoothing / SMMA)
# ---------------------------------------------------------------------------


def _compute_atr(bars: list[PriceBar], period: int) -> list[float]:
    """Compute per-bar ATR using Wilder's Smoothing (alpha = 1/period).

    Returns a list of the same length as *bars*.  Values before index
    *period-1* are set to NaN so callers can test ``math.isnan(atr[i])``.
    """
    n = len(bars)
    atr: list[float] = [math.nan] * n
    if n == 0:
        return atr

    alpha = 1.0 / period

    # True Range for each bar
    tr: list[float] = [0.0] * n
    tr[0] = bars[0].high - bars[0].low
    for i in range(1, n):
        hl = bars[i].high - bars[i].low
        hc = abs(bars[i].high - bars[i - 1].close)
        lc = abs(bars[i].low - bars[i - 1].close)
        tr[i] = max(hl, hc, lc)

    if n < period:
        return atr

    # Seed with SMA of first *period* TRs
    seed = sum(tr[:period]) / period
    atr[period - 1] = seed

    for i in range(period, n):
        atr[i] = atr[i - 1] * (1.0 - alpha) + tr[i] * alpha

    return atr


# ---------------------------------------------------------------------------
# Swing detection (live-safe, delayed by swing_lookback)
# ---------------------------------------------------------------------------


def _detect_swings(
    bars: list[PriceBar],
    swing_lookback: int,
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Return (swing_highs, swing_lows) as sorted (index, price) lists.

    A pivot at index i is confirmed only when bar i+swing_lookback has closed,
    so the last *swing_lookback* bars are not eligible — no lookahead.
    """
    n = len(bars)
    swing_highs: list[tuple[int, float]] = []
    swing_lows: list[tuple[int, float]] = []

    for i in range(swing_lookback, n - swing_lookback):
        window_highs = [bars[j].high for j in range(i - swing_lookback, i + swing_lookback + 1)]
        window_lows = [bars[j].low for j in range(i - swing_lookback, i + swing_lookback + 1)]

        if bars[i].high == max(window_highs):
            swing_highs.append((i, bars[i].high))
        if bars[i].low == min(window_lows):
            swing_lows.append((i, bars[i].low))

    return swing_highs, swing_lows


# ---------------------------------------------------------------------------
# FVG check (between ob_i and displacement bar k, exclusive)
# ---------------------------------------------------------------------------


def _has_fvg_between(bars: list[PriceBar], ob_i: int, k: int, direction: str) -> bool:
    """Return True if a non-overlapping Fair Value Gap exists in (ob_i, k]."""
    # We need at least 3 consecutive bars in range [ob_i, k]
    for a in range(ob_i, k - 1):
        b = a + 1  # noqa: F841 — displacement candle (not checked directly)
        c = a + 2
        if c > k:
            break
        if direction == "bullish":
            # bar[c].low > bar[a].high  -> gap
            if bars[c].low > bars[a].high:
                return True
        else:
            # bar[c].high < bar[a].low  -> gap
            if bars[c].high < bars[a].low:
                return True
    return False


# ---------------------------------------------------------------------------
# Main detector
# ---------------------------------------------------------------------------


def detect_order_blocks(  # noqa: C901 — complex by spec
    bars: list[PriceBar],
    *,
    swing_lookback: int = 2,
    ob_lookback_bars: int = 10,
    displacement_atr_mult: float = 1.0,
    body_ratio_min: float = 0.50,
    use_body_only: bool = True,
    close_mitigation: bool = True,
    min_strength_score: float = 0.40,
    require_fvg: bool = False,
    atr_period: int = 14,
    structure_events: list[dict[str, Any]] | None = None,
) -> list[OrderBlock]:
    """Detect Order Blocks from a chronological bar series.

    Parameters mirror docs/CHART_READING.md §3 verbatim.  *structure_events* is
    accepted for API compatibility but not consumed internally (OB detection derives
    its own BOS events from the bars).

    Returns a filtered list of :class:`OrderBlock` instances, sorted by *ob_index*.
    Mitigation / breaker / retest state on each object reflects the full bar history.
    """
    n = len(bars)
    if n < atr_period + swing_lookback + 1:
        return []

    # ------------------------------------------------------------------
    # STEP 1 — rolling ATR(14) via Wilder's Smoothing
    # ------------------------------------------------------------------
    atr14 = _compute_atr(bars, atr_period)

    # ------------------------------------------------------------------
    # STEP 2 — swing points (live-safe, delayed by swing_lookback)
    # ------------------------------------------------------------------
    swing_highs, swing_lows = _detect_swings(bars, swing_lookback)

    # Index-keyed sets for fast lookup (used in STEP 9 strength score)
    swing_high_set: set[int] = {idx for idx, _ in swing_highs}
    swing_low_set: set[int] = {idx for idx, _ in swing_lows}

    # ------------------------------------------------------------------
    # STEP 3 — BOS events (body-close only)
    # ------------------------------------------------------------------
    # We track the "most recent unbroken" swing candidate for each direction.
    # Walk bars chronologically; a swing becomes "available" as a BOS target
    # only once it is confirmed (i.e., bar[i + swing_lookback] has closed).
    # Since swing_highs/lows are sorted by index, we advance pointers.

    bos_events: list[tuple[int, str, int, float]] = []
    # (bar_k, direction, broken_swing_index, broken_swing_price)

    # Pointers into swing_highs / swing_lows — advance as we pass their indices
    sh_ptr = 0  # next swing_high to potentially use as a breakout target
    sl_ptr = 0  # next swing_low

    # The "most recent unbroken" swing (index, price) for each direction
    # — updated as we advance; None until first confirmed swing
    unbroken_sh: tuple[int, float] | None = None  # for BOS_UP
    unbroken_sl: tuple[int, float] | None = None  # for BOS_DOWN

    # Track which swing indices have already been "broken" to avoid reusing them
    broken_sh_indices: set[int] = set()
    broken_sl_indices: set[int] = set()

    for k in range(n):
        # Advance swing pointers: a swing at index s is confirmed when bar
        # s + swing_lookback has closed, i.e. when k >= s + swing_lookback.
        while sh_ptr < len(swing_highs):
            s_idx, s_price = swing_highs[sh_ptr]
            if k >= s_idx + swing_lookback:
                # This swing is now confirmed; update the candidate if not broken
                if s_idx not in broken_sh_indices and (
                    unbroken_sh is None or s_price > unbroken_sh[1]
                ):
                    unbroken_sh = (s_idx, s_price)
                sh_ptr += 1
            else:
                break

        while sl_ptr < len(swing_lows):
            s_idx, s_price = swing_lows[sl_ptr]
            if k >= s_idx + swing_lookback:
                if s_idx not in broken_sl_indices and (
                    unbroken_sl is None or s_price < unbroken_sl[1]
                ):
                    unbroken_sl = (s_idx, s_price)
                sl_ptr += 1
            else:
                break

        if math.isnan(atr14[k]):
            continue

        bar_k = bars[k]

        # BOS_UP: close above most_recent_unbroken_swing_high
        if unbroken_sh is not None:
            sh_idx, sh_price = unbroken_sh
            if sh_idx < k and bar_k.close > sh_price:
                bos_events.append((k, "BOS_UP", sh_idx, sh_price))
                broken_sh_indices.add(sh_idx)
                unbroken_sh = None  # consumed; next swing will set a new one

        # BOS_DOWN: close below most_recent_unbroken_swing_low
        if unbroken_sl is not None:
            sl_idx, sl_price = unbroken_sl
            if sl_idx < k and bar_k.close < sl_price:
                bos_events.append((k, "BOS_DOWN", sl_idx, sl_price))
                broken_sl_indices.add(sl_idx)
                unbroken_sl = None

    # ------------------------------------------------------------------
    # STEPS 4-15: process each BOS event and build raw OB list
    # ------------------------------------------------------------------
    raw_obs: list[OrderBlock] = []

    for k, direction, _broken_sw_idx, _broken_sw_price in bos_events:
        bar_k = bars[k]

        # ---- STEP 4 — displacement filter ----
        body_size = bar_body(bar_k)
        full_rng = bar_range(bar_k)
        body_to_range = body_size / (full_rng + 1e-12)

        atr_k = atr14[k]
        if math.isnan(atr_k):
            continue

        if body_size < displacement_atr_mult * atr_k:
            continue
        if body_to_range < body_ratio_min:
            continue

        # Directional body check
        if direction == "BOS_UP" and not is_bullish(bar_k):
            continue
        if direction == "BOS_DOWN" and not is_bearish(bar_k):
            continue

        # ---- STEP 5 — last counter-direction candle (OB candle) ----
        scan_start = max(0, k - ob_lookback_bars)
        ob_i: int | None = None

        if direction == "BOS_UP":
            # Last bearish candle before k
            for j in range(k - 1, scan_start - 1, -1):
                if is_bearish(bars[j]):
                    ob_i = j
                    break
        else:
            # Last bullish candle before k
            for j in range(k - 1, scan_start - 1, -1):
                if is_bullish(bars[j]):
                    ob_i = j
                    break

        if ob_i is None:
            continue

        bar_ob = bars[ob_i]

        # ---- STEP 6 — engulfment check (full wick extreme) ----
        if direction == "BOS_UP" and not (bar_k.close > bar_ob.high):
            continue
        if direction == "BOS_DOWN" and not (bar_k.close < bar_ob.low):
            continue

        # ---- STEP 7 — zone boundaries ----
        if use_body_only:
            ob_top = max(bar_ob.open, bar_ob.close)
            ob_bottom = min(bar_ob.open, bar_ob.close)
        else:
            ob_top = bar_ob.high
            ob_bottom = bar_ob.low

        ob_mid = (ob_top + ob_bottom) / 2.0

        # Mitigation extremes always use full-wick (independent of use_body_only)
        mit_extreme = bar_ob.low if direction == "BOS_UP" else bar_ob.high

        # ---- STEP 8 — optional FVG check ----
        has_fvg = _has_fvg_between(bars, ob_i, k, "bullish" if direction == "BOS_UP" else "bearish")

        # ---- STEP 9 — strength score ----
        score = 0.0

        # +0.30: strong displacement (>= 2x threshold)
        if body_size >= displacement_atr_mult * 2.0 * atr_k:
            score += 0.30

        # +0.25: FVG between OB and displacement
        if has_fvg:
            score += 0.25

        # +0.20: OB candle volume > 14-bar mean before ob_i
        vol_window_start = max(0, ob_i - 14)
        if ob_i > vol_window_start:
            vol_mean = statistics.mean(bars[j].volume for j in range(vol_window_start, ob_i))
            if bar_ob.volume > vol_mean:
                score += 0.20

        # +0.15: clean displacement (body_to_range >= 0.65)
        if body_to_range >= 0.65:
            score += 0.15

        # +0.10: OB candle itself is a confirmed swing point.
        # Lookahead guard: a swing at ob_i is only *confirmed* once bar
        # ob_i + swing_lookback has closed.  At detection bar k we may only
        # count it if that confirmation bar is at or before k — otherwise the
        # strength of an OB discovered live at bar k would depend on bars that
        # have not yet closed (phantom-swing lookahead).
        if (ob_i + swing_lookback <= k) and (ob_i in swing_high_set or ob_i in swing_low_set):
            score += 0.10

        score = min(1.0, score)

        # ---- STEP 10 — register OB ----
        ob_dir = "bullish" if direction == "BOS_UP" else "bearish"
        ob = OrderBlock(
            ob_index=ob_i,
            direction=ob_dir,
            zone_high=ob_top,
            zone_low=ob_bottom,
            zone_mid=ob_mid,
            mitigation_extreme=mit_extreme,
            ts=bar_ob.ts,
            bos_ts=bar_k.ts,
            strength=score,
            has_fvg=has_fvg,
        )

        # ---- STEP 11+11b+12 — mitigation, visited, breaker, retest ----
        # The "most recent swing low/high before k" for liquidity sweep detection
        # For bullish OB: most recent swing_low before k
        # For bearish OB: most recent swing_high before k
        # Lookahead guard: the anchoring swing must be *confirmed* as of bar k,
        # i.e. its right-side confirmation bar (idx + swing_lookback) has closed
        # by k.  A swing whose index < k but whose confirmation bar is still in
        # the future is a phantom pivot and must not seed the sweep reference.
        sweep_ref_price: float | None = None
        if ob_dir == "bullish":
            candidates = [(idx, p) for idx, p in swing_lows if idx + swing_lookback <= k]
            if candidates:
                sweep_ref_price = max(candidates, key=lambda x: x[0])[1]
        else:
            candidates = [(idx, p) for idx, p in swing_highs if idx + swing_lookback <= k]
            if candidates:
                sweep_ref_price = max(candidates, key=lambda x: x[0])[1]

        for p in range(k + 1, n):
            bar_p = bars[p]

            # --- visited flag (set before mitigation check)
            if not ob.visited and (
                (ob_dir == "bullish" and bar_p.low <= ob.zone_high)
                or (ob_dir == "bearish" and bar_p.high >= ob.zone_low)
            ):
                ob.visited = True

            # --- liquidity sweep check (before mitigation)
            if (
                not ob.liquidity_swept
                and not ob.mitigated
                and sweep_ref_price is not None
                and (
                    (ob_dir == "bullish" and bar_p.low < sweep_ref_price)
                    or (ob_dir == "bearish" and bar_p.high > sweep_ref_price)
                )
            ):
                ob.liquidity_swept = True

            # --- mitigation
            if not ob.mitigated:
                if ob_dir == "bullish":
                    if close_mitigation:
                        if bar_p.close < ob.mitigation_extreme:
                            ob.mitigated = True
                            ob.mitigation_ts = bar_p.ts
                    else:
                        if bar_p.low < ob.mitigation_extreme:
                            ob.mitigated = True
                            ob.mitigation_ts = bar_p.ts
                else:
                    if close_mitigation:
                        if bar_p.close > ob.mitigation_extreme:
                            ob.mitigated = True
                            ob.mitigation_ts = bar_p.ts
                    else:
                        if bar_p.high > ob.mitigation_extreme:
                            ob.mitigated = True
                            ob.mitigation_ts = bar_p.ts

                # --- breaker promotion (requires liquidity_swept)
                if ob.mitigated and ob.liquidity_swept:
                    ob.is_breaker = True
                    ob.breaker_direction = (
                        "bearish_breaker" if ob_dir == "bullish" else "bullish_breaker"
                    )

            # --- STEP 12: breaker retest (after mitigation, first occurrence only)
            if ob.is_breaker and ob.mitigation_ts is not None and ob.breaker_retest_ts is None:
                if ob_dir == "bullish":  # bearish_breaker: retest from below
                    if bar_p.high >= ob.zone_low:
                        ob.breaker_retest_ts = bar_p.ts
                else:  # bullish_breaker: retest from above
                    if bar_p.low <= ob.zone_high:
                        ob.breaker_retest_ts = bar_p.ts

        # ---- STEP 14 — OI confirmation (optional, crypto/futures) ----
        # ob.oi_confirmation left False; callers can set via score_order_block

        raw_obs.append(ob)

    # ------------------------------------------------------------------
    # STEP 15 — filter by min_strength_score (and require_fvg)
    # ------------------------------------------------------------------
    result: list[OrderBlock] = []
    for ob in raw_obs:
        if ob.strength < min_strength_score:
            continue
        if require_fvg and not ob.has_fvg:
            continue
        result.append(ob)

    result.sort(key=lambda x: x.ob_index)
    return result


# ---------------------------------------------------------------------------
# Auxiliary public functions
# ---------------------------------------------------------------------------


def score_order_block(
    ob: OrderBlock,
    htf_bias: TrendBias,
    oi_data: list[float] | None = None,
) -> float:
    """Adjust and return the effective quality score for *ob* given HTF context.

    Applies:
    * HTF confluence boost (+0.15 if HTF bias matches OB direction, capped at 1.0)
    * OI confirmation: marks ``ob.oi_confirmation = True`` if provided OI data shows
      rising OI at the displacement bar index (``oi_data[ob_index+1] > oi_data[ob_index]``).
    * Second-retest penalty (−0.15 if ``ob.visited`` and not yet mitigated).

    Returns the adjusted score (does NOT mutate ``ob.strength``).
    """
    score = ob.strength

    # HTF alignment boost (STEP 13 logic applied post-hoc)
    if (
        htf_bias == TrendBias.BULLISH
        and ob.direction == "bullish"
        or htf_bias == TrendBias.BEARISH
        and ob.direction == "bearish"
    ):
        score = min(1.0, score + 0.15)
        ob.htf_confluence = True

    # OI confirmation
    if oi_data is not None:
        idx = ob.ob_index
        # Use bos index = ob_index + 1 as a proxy (minimal assumption)
        if idx + 1 < len(oi_data) and oi_data[idx + 1] > oi_data[idx]:
            ob.oi_confirmation = True

    # Second-retest penalty
    if ob.visited and not ob.mitigated:
        score = max(0.0, score - 0.15)

    return score


def check_ob_mitigation(ob: OrderBlock, current_bar: PriceBar, atr: float) -> bool:  # noqa: ARG001
    """Return True if *current_bar* mitigates *ob* (updates ob.mitigated in place).

    *atr* is accepted for API compatibility (used by callers that add buffer logic)
    but the canonical mitigation check uses the raw ``mitigation_extreme`` only.
    """
    if ob.mitigated:
        return True

    if ob.direction == "bullish":
        triggered = current_bar.close < ob.mitigation_extreme
    else:
        triggered = current_bar.close > ob.mitigation_extreme

    if triggered:
        ob.mitigated = True
        ob.mitigation_ts = current_bar.ts

        if ob.liquidity_swept:
            ob.is_breaker = True
            ob.breaker_direction = (
                "bearish_breaker" if ob.direction == "bullish" else "bullish_breaker"
            )

    return ob.mitigated


def get_ote_entry_range(
    ob: OrderBlock,
    swing_origin: float,
    displacement_peak: float,
) -> tuple[float, float]:
    """Return the OTE (Optimal Trade Entry) Fibonacci 0.62–0.79 retracement range.

    Computes the fib retracement of the move from *swing_origin* to
    *displacement_peak* and returns ``(ote_low, ote_high)`` — the 0.62–0.79 zone.

    For a **bullish** OB the retracement runs *down* from *displacement_peak*:
        ``ote_high = peak - 0.62 * move``
        ``ote_low  = peak - 0.79 * move``

    For a **bearish** OB the retracement runs *up* from *displacement_peak*:
        ``ote_low  = peak + 0.62 * move``   (note: peak is the low here)
        ``ote_high = peak + 0.79 * move``

    The result is intersected with the OB zone so the entry sits inside the zone.
    """
    move = abs(displacement_peak - swing_origin)

    if ob.direction == "bullish":
        # Price dropped from displacement_peak down toward swing_origin
        ote_high = displacement_peak - 0.62 * move
        ote_low = displacement_peak - 0.79 * move
    else:
        # Price rose from displacement_peak (the low) up toward swing_origin (the high)
        ote_low = displacement_peak + 0.62 * move
        ote_high = displacement_peak + 0.79 * move

    # Clamp to OB zone
    ote_high = min(ote_high, ob.zone_high)
    ote_low = max(ote_low, ob.zone_low)

    return (ote_low, ote_high)
