"""Market structure detector — SMC/ICT swing structure, BOS, CHoCH, EQH/EQL.

Implements the ``market_structure`` concept from docs/CHART_READING.md §1 (lines 84-221),
following all 10 algorithm steps verbatim.  Pure Python stdlib only; no pandas/numpy.

No-lookahead guarantee: all detections at bar index ``t`` only reference ``bars[0..t]``.
Swing pivots confirm only after ``swing_right`` bars have closed past the candidate bar.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from data.models import PriceBar
from engine.chart.types import TrendBias

# ---------------------------------------------------------------------------
# Module-local result dataclasses
# ---------------------------------------------------------------------------

_DateLike = date | datetime


class StructureScope(StrEnum):
    SWING = "swing"
    INTERNAL = "internal"


@dataclass
class SwingPivot:
    """A confirmed swing pivot (high or low) with its label."""

    bar_index: int  # index of the actual pivot bar in the input list
    confirmed_at: int  # bar index at which the pivot was confirmed
    price: float
    ts: _DateLike
    pivot_type: str  # 'high' | 'low'
    label: str  # HH | LH | HL | LL | EQH | EQL
    scope: StructureScope
    consumed: bool = False  # True after CHoCH fires on this pivot as its HL/LH level


@dataclass
class StructureEvent:
    """A single emitted market-structure event (pivot, BOS, CHoCH, EQH/EQL, sweep)."""

    event_type: str  # see spec §output_fields
    ts: _DateLike
    direction: str | None  # 'BULLISH' | 'BEARISH' | None
    level: float
    zone_low: float | None
    zone_high: float | None
    label: str | None  # HH | HL | LH | LL | EQH | EQL | None
    trend_bias: str  # snapshot at emit time
    strength: float | None
    touch_count: int | None
    mitigated: bool
    bar_index: int
    pivot_bar_index: int
    structure_scope: str  # 'swing' | 'internal'


@dataclass(frozen=True)
class LiquidityLevel:
    """EQH or EQL cluster zone for the aggregator."""

    zone_high: float
    zone_low: float
    touch_count: int
    ts_first: _DateLike
    ts_last: _DateLike
    level_type: str  # 'EQH' | 'EQL'
    mitigated: bool = False


@dataclass
class MarketStructure:
    """Top-level result returned by :func:`detect_swing_structure`."""

    events: list[StructureEvent]  # chronological, all types
    swing_pivots: list[SwingPivot]
    internal_pivots: list[SwingPivot]
    trend_bias: TrendBias
    int_trend_bias: TrendBias
    liquidity_levels: list[LiquidityLevel]
    structure_levels: dict[str, object]  # live pointers — STEP 9


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _strictly_greater_window(bars: list[PriceBar], i: int, left: int, right: int) -> bool:
    """Return True iff bars[i].high is strictly greater than all neighbours in [i-left..i-1]
    and [i+1..i+right].  Caller must ensure i-left>=0 and i+right<len(bars)."""
    h = bars[i].high
    if not all(bars[j].high < h for j in range(i - left, i)):
        return False
    return all(bars[j].high < h for j in range(i + 1, i + right + 1))


def _strictly_less_window(bars: list[PriceBar], i: int, left: int, right: int) -> bool:
    """Return True iff bars[i].low is strictly less than all neighbours."""
    low = bars[i].low
    if not all(bars[j].low > low for j in range(i - left, i)):
        return False
    return all(bars[j].low > low for j in range(i + 1, i + right + 1))


def _eq_check(a: float, b: float, eq_threshold: float) -> bool:
    denom = max(a, b)
    if denom <= 0.0:
        # Degenerate / non-positive prices — treat exact equality as equal, else not.
        return a == b
    return abs(a - b) / denom <= eq_threshold


def _label_high(p_h: float, prev_sh: float, eq_threshold: float) -> str:
    if _eq_check(p_h, prev_sh, eq_threshold):
        return "EQH"
    return "HH" if p_h > prev_sh else "LH"


def _label_low(p_l: float, prev_sl: float, eq_threshold: float) -> str:
    if _eq_check(p_l, prev_sl, eq_threshold):
        return "EQL"
    return "LL" if p_l < prev_sl else "HL"


def _bias_from_pivots(sh_list: list[SwingPivot], sl_list: list[SwingPivot]) -> TrendBias:
    """STEP 4 — deterministic 2-pivot rule (EQH/EQL excluded)."""
    hh_lh = [p for p in sh_list if p.label in ("HH", "LH")]
    hl_ll = [p for p in sl_list if p.label in ("HL", "LL")]
    if len(hh_lh) < 2 or len(hl_ll) < 2:
        return TrendBias.RANGING
    sh1, sh2 = hh_lh[-1], hh_lh[-2]
    sl1, sl2 = hl_ll[-1], hl_ll[-2]
    if sh1.price > sh2.price and sl1.price > sl2.price:
        return TrendBias.BULLISH
    if sh1.price < sh2.price and sl1.price < sl2.price:
        return TrendBias.BEARISH
    return TrendBias.RANGING


def _next_unbroken(
    pivots: list[SwingPivot], after_index: int, *, confirmed_at_most: int
) -> SwingPivot | None:
    """Find the next pivot in ``pivots`` whose bar_index > after_index and confirmed_at <=
    confirmed_at_most (i.e. already confirmed at this point in the scan)."""
    for p in pivots:
        if p.bar_index > after_index and p.confirmed_at <= confirmed_at_most:
            return p
    return None


# ---------------------------------------------------------------------------
# STEP 1+2 — pivot detection (shared between swing and internal)
# ---------------------------------------------------------------------------


def _detect_raw_pivots(
    bars: list[PriceBar],
    swing_left: int,
    swing_right: int,
    scope: StructureScope,
    eq_threshold: float,
) -> tuple[list[SwingPivot], list[SwingPivot]]:
    """Detect and label all swing highs/lows with the given left/right lookback.

    Pivots are confirmed at bar index ``i + swing_right``.  The caller of higher-level
    functions drives a streaming loop; this function returns the full batch result for
    the whole bar series (suitable for backtesting / testing).

    Returns: (pivot_highs, pivot_lows) — all confirmed, chronologically sorted.
    """
    n = len(bars)
    pivot_highs: list[SwingPivot] = []
    pivot_lows: list[SwingPivot] = []

    prev_sh_price: float | None = None
    prev_sl_price: float | None = None

    # Iterate candidate indices — must have swing_left bars to the left and
    # swing_right bars to the right, all within bounds.
    for i in range(swing_left, n - swing_right):
        # Confirmation happens at bar i + swing_right.
        confirmed_at = i + swing_right

        is_sh = _strictly_greater_window(bars, i, swing_left, swing_right)
        is_sl = _strictly_less_window(bars, i, swing_left, swing_right)

        if is_sh:
            price = bars[i].high
            if prev_sh_price is None:
                label = "HH"  # first pivot — arbitrary seed
                prev_sh_price = price
            else:
                label = _label_high(price, prev_sh_price, eq_threshold)
                prev_sh_price = price
            pivot_highs.append(
                SwingPivot(
                    bar_index=i,
                    confirmed_at=confirmed_at,
                    price=price,
                    ts=bars[i].ts,
                    pivot_type="high",
                    label=label,
                    scope=scope,
                )
            )

        if is_sl:
            price = bars[i].low
            if prev_sl_price is None:
                label = "LL"  # first pivot — arbitrary seed
                prev_sl_price = price
            else:
                label = _label_low(price, prev_sl_price, eq_threshold)
                prev_sl_price = price
            pivot_lows.append(
                SwingPivot(
                    bar_index=i,
                    confirmed_at=confirmed_at,
                    price=price,
                    ts=bars[i].ts,
                    pivot_type="low",
                    label=label,
                    scope=scope,
                )
            )

    return pivot_highs, pivot_lows


# ---------------------------------------------------------------------------
# STEP 8 — EQH / EQL cluster scan (zone-level, not per-pivot)
# ---------------------------------------------------------------------------


def _cluster_eqh_eql(
    pivot_highs: list[SwingPivot],
    pivot_lows: list[SwingPivot],
    eq_threshold: float,
    eqh_lookback: int,
    current_bar: int,
    bars: list[PriceBar],
) -> list[LiquidityLevel]:
    """Return zone-level EQH and EQL liquidity levels formed within eqh_lookback bars."""
    result: list[LiquidityLevel] = []

    def _cluster(pivots: list[SwingPivot], level_type: str) -> None:
        recent = [p for p in pivots if current_bar - p.bar_index <= eqh_lookback]
        if len(recent) < 2:
            return
        used = [False] * len(recent)
        for i, pi in enumerate(recent):
            if used[i]:
                continue
            group = [pi]
            for j, pj in enumerate(recent):
                if j == i or used[j]:
                    continue
                if _eq_check(pi.price, pj.price, eq_threshold):
                    group.append(pj)
                    used[j] = True
            if len(group) >= 2:
                used[i] = True
                prices = [g.price for g in group]
                tss = [g.ts for g in group]
                z_high = max(prices)
                z_low = min(prices)
                ts_first = min(
                    tss,
                    key=lambda t: (
                        t if isinstance(t, datetime) else datetime(t.year, t.month, t.day)
                    ),
                )  # type: ignore[arg-type]
                ts_last = max(
                    tss,
                    key=lambda t: (
                        t if isinstance(t, datetime) else datetime(t.year, t.month, t.day)
                    ),
                )  # type: ignore[arg-type]
                # Check mitigation: any bar after the last group pivot that closes through zone
                mitigated = False
                last_pivot_idx = max(g.bar_index for g in group)
                for b in bars[last_pivot_idx + 1 : current_bar + 1]:
                    if level_type == "EQH" and b.close > z_high:
                        mitigated = True
                        break
                    if level_type == "EQL" and b.close < z_low:
                        mitigated = True
                        break
                result.append(
                    LiquidityLevel(
                        zone_high=z_high,
                        zone_low=z_low,
                        touch_count=len(group),
                        ts_first=ts_first,
                        ts_last=ts_last,
                        level_type=level_type,
                        mitigated=mitigated,
                    )
                )

    _cluster(pivot_highs, "EQH")
    _cluster(pivot_lows, "EQL")
    return result


# ---------------------------------------------------------------------------
# Main streaming detector — STEPS 3-10
# ---------------------------------------------------------------------------


def _run_structure(
    bars: list[PriceBar],
    pivot_highs: list[SwingPivot],
    pivot_lows: list[SwingPivot],
    scope: StructureScope,
    eq_threshold: float,
    eqh_lookback: int,
    use_body_close: bool,
    min_displacement_pct: float,
) -> tuple[list[StructureEvent], TrendBias, dict[str, object]]:
    """Run STEPS 3-10 over pre-detected pivots and bars.

    Returns (events, final_trend_bias, structure_levels_snapshot).
    """
    events: list[StructureEvent] = []
    n = len(bars)
    scope_str = scope.value

    # --- emit pivot events (STEP 3 + STEP 10) ---
    for ph in pivot_highs:
        events.append(
            StructureEvent(
                event_type=f"{scope_str}_pivot",
                ts=bars[ph.confirmed_at].ts,
                direction=None,
                level=ph.price,
                zone_low=None,
                zone_high=None,
                label=ph.label,
                trend_bias=TrendBias.RANGING.value,  # snapshot filled in streaming pass below
                strength=None,
                touch_count=None,
                mitigated=False,
                bar_index=ph.confirmed_at,
                pivot_bar_index=ph.bar_index,
                structure_scope=scope_str,
            )
        )
    for pl in pivot_lows:
        events.append(
            StructureEvent(
                event_type=f"{scope_str}_pivot",
                ts=bars[pl.confirmed_at].ts,
                direction=None,
                level=pl.price,
                zone_low=None,
                zone_high=None,
                label=pl.label,
                trend_bias=TrendBias.RANGING.value,
                strength=None,
                touch_count=None,
                mitigated=False,
                bar_index=pl.confirmed_at,
                pivot_bar_index=pl.bar_index,
                structure_scope=scope_str,
            )
        )

    # Sort pivot events by bar_index (confirmed_at)
    events.sort(key=lambda e: e.bar_index)

    # --- streaming BOS / CHoCH pass (STEPS 4-9) ---
    # We iterate bar by bar; at each bar we first "release" any pivots confirmed at this bar,
    # then check BOS/CHoCH conditions.

    trend_bias = TrendBias.RANGING

    # Pivot lists sorted by confirmed_at
    ph_sorted = sorted(pivot_highs, key=lambda p: p.confirmed_at)
    pl_sorted = sorted(pivot_lows, key=lambda p: p.confirmed_at)

    # Pointers into the sorted pivot lists (next-to-release index)
    ph_ptr = 0
    pl_ptr = 0

    # Confirmed (released) pivot accumulation lists for bias computation
    confirmed_highs: list[SwingPivot] = []
    confirmed_lows: list[SwingPivot] = []

    # "Unbroken" pointers for BOS (STEP 5)
    last_unbroken_sh: SwingPivot | None = None
    last_unbroken_sl: SwingPivot | None = None

    # CHoCH tracking (STEP 6)
    last_hl: SwingPivot | None = None  # most recent HL pivot (for bearish CHoCH trigger)
    last_lh: SwingPivot | None = None  # most recent LH pivot (for bullish CHoCH trigger)

    # STEP 7 — CHoCH-induced bias override.  A confirmed CHoCH flips trend_bias to the new
    # trend direction, and that flip is the AUTHORITATIVE structural transition (STEP 7).  The
    # geometric 2-pivot rule (STEP 4) only *seeds* the initial bias while structure is still
    # RANGING; it must not silently re-overwrite a CHoCH on the next bar, because right after a
    # CHoCH the stale prior geometry (e.g. HH+HL) is still present until new opposite-trend
    # pivots confirm.  Therefore: once a CHoCH sets the override, it persists until the *next*
    # CHoCH flips it again.  Geometry resumes control only after the override is cleared because
    # fresh pivots have re-established a matching bias.  Uses only data closed at bar t (no
    # lookahead).
    choch_override: TrendBias | None = None
    # bar_index of the latest confirmed pivot at the moment the override was set; the override
    # is eligible to yield back to geometry only once a strictly newer pivot has confirmed.
    override_anchor_bi: int = -1

    bos_choch_events: list[StructureEvent] = []

    # Authoritative per-bar bias snapshot, captured from the SAME streaming pass that drives
    # BOS/CHoCH (so pivot-event snapshots reflect STEP-7 CHoCH transitions, not a divergent
    # geometry-only recomputation).
    bias_at: dict[int, str] = {}

    for t in range(n):
        bar = bars[t]

        # Release pivots confirmed at this bar (confirmed_at == t)
        while ph_ptr < len(ph_sorted) and ph_sorted[ph_ptr].confirmed_at == t:
            p = ph_sorted[ph_ptr]
            confirmed_highs.append(p)
            # Update unbroken pointer if None or this pivot is newer
            if last_unbroken_sh is None or p.bar_index > last_unbroken_sh.bar_index:
                last_unbroken_sh = p
            # Update LH tracker for CHoCH
            if p.label == "LH":
                last_lh = p
            ph_ptr += 1

        while pl_ptr < len(pl_sorted) and pl_sorted[pl_ptr].confirmed_at == t:
            p = pl_sorted[pl_ptr]
            confirmed_lows.append(p)
            if last_unbroken_sl is None or p.bar_index > last_unbroken_sl.bar_index:
                last_unbroken_sl = p
            if p.label == "HL":
                last_hl = p
            pl_ptr += 1

        # STEP 4 — geometric 2-pivot bias.  STEP 7 — a live CHoCH override takes precedence
        # over stale geometry.  The override yields back to geometry only once a NEW pivot has
        # confirmed after the CHoCH AND that fresh geometry agrees with the override (structure
        # re-established) — at which point the override is redundant and is cleared.
        geo_bias = _bias_from_pivots(confirmed_highs, confirmed_lows)
        if choch_override is not None:
            newest_bi = max(
                (p.bar_index for p in (confirmed_highs + confirmed_lows)),
                default=-1,
            )
            if newest_bi > override_anchor_bi and geo_bias == choch_override:
                # Fresh structure has confirmed the same direction — geometry retakes control.
                choch_override = None
                trend_bias = geo_bias
            else:
                trend_bias = choch_override
        else:
            trend_bias = geo_bias

        # Break-confirmation price (STEP 5/6, param ``use_body_close``):
        #   body-close mode (default, canonical ICT) → close must pierce the level;
        #   wick-break mode → the bar high (up) / low (down) piercing the level is enough.
        up_break = bar.close if use_body_close else bar.high
        down_break = bar.close if use_body_close else bar.low

        # STEP 5 — BOS detection
        if trend_bias == TrendBias.BULLISH and last_unbroken_sh is not None:
            level = last_unbroken_sh.price
            if up_break > level:
                disp = (up_break - level) / level
                if disp >= min_displacement_pct:
                    bos_choch_events.append(
                        StructureEvent(
                            event_type=f"{scope_str}_BOS",
                            ts=bar.ts,
                            direction="BULLISH",
                            level=level,
                            zone_low=None,
                            zone_high=None,
                            label=None,
                            trend_bias=trend_bias.value,
                            strength=disp,
                            touch_count=None,
                            mitigated=False,
                            bar_index=t,
                            pivot_bar_index=last_unbroken_sh.bar_index,
                            structure_scope=scope_str,
                        )
                    )
                    # Advance unbroken pointer
                    old_bi = last_unbroken_sh.bar_index
                    nxt = _next_unbroken(confirmed_highs, old_bi, confirmed_at_most=t)
                    last_unbroken_sh = nxt
            elif use_body_close and bar.high > level:
                # Wick-only sweep — wick pierces but close stays at/below level (body-close mode).
                bos_choch_events.append(
                    StructureEvent(
                        event_type="liquidity_sweep",
                        ts=bar.ts,
                        direction="BULLISH",
                        level=level,
                        zone_low=None,
                        zone_high=None,
                        label=None,
                        trend_bias=trend_bias.value,
                        strength=None,
                        touch_count=None,
                        mitigated=False,
                        bar_index=t,
                        pivot_bar_index=last_unbroken_sh.bar_index,
                        structure_scope=scope_str,
                    )
                )

        elif trend_bias == TrendBias.BEARISH and last_unbroken_sl is not None:
            level = last_unbroken_sl.price
            if down_break < level:
                disp = (level - down_break) / level
                if disp >= min_displacement_pct:
                    bos_choch_events.append(
                        StructureEvent(
                            event_type=f"{scope_str}_BOS",
                            ts=bar.ts,
                            direction="BEARISH",
                            level=level,
                            zone_low=None,
                            zone_high=None,
                            label=None,
                            trend_bias=trend_bias.value,
                            strength=disp,
                            touch_count=None,
                            mitigated=False,
                            bar_index=t,
                            pivot_bar_index=last_unbroken_sl.bar_index,
                            structure_scope=scope_str,
                        )
                    )
                    old_bi = last_unbroken_sl.bar_index
                    nxt = _next_unbroken(confirmed_lows, old_bi, confirmed_at_most=t)
                    last_unbroken_sl = nxt
            elif use_body_close and bar.low < level:
                # Wick-only sweep — wick pierces but close stays at/above level (body-close mode).
                bos_choch_events.append(
                    StructureEvent(
                        event_type="liquidity_sweep",
                        ts=bar.ts,
                        direction="BEARISH",
                        level=level,
                        zone_low=None,
                        zone_high=None,
                        label=None,
                        trend_bias=trend_bias.value,
                        strength=None,
                        touch_count=None,
                        mitigated=False,
                        bar_index=t,
                        pivot_bar_index=last_unbroken_sl.bar_index,
                        structure_scope=scope_str,
                    )
                )

        # STEP 6 — CHoCH detection
        if trend_bias == TrendBias.BULLISH and last_hl is not None and not last_hl.consumed:
            hl_level = last_hl.price
            if down_break < hl_level:
                disp = (hl_level - down_break) / hl_level
                if disp >= min_displacement_pct:
                    bos_choch_events.append(
                        StructureEvent(
                            event_type=f"{scope_str}_CHoCH",
                            ts=bar.ts,
                            direction="BEARISH",
                            level=hl_level,
                            zone_low=None,
                            zone_high=None,
                            label=None,
                            trend_bias=trend_bias.value,
                            strength=disp,
                            touch_count=None,
                            mitigated=False,
                            bar_index=t,
                            pivot_bar_index=last_hl.bar_index,
                            structure_scope=scope_str,
                        )
                    )
                    last_hl.consumed = True
                    # STEP 7 — flip bias to BEARISH and make it persist (override).
                    trend_bias = TrendBias.BEARISH
                    choch_override = TrendBias.BEARISH
                    override_anchor_bi = max(
                        (p.bar_index for p in (confirmed_highs + confirmed_lows)),
                        default=-1,
                    )
                    # Reset pivot tracking state for the new trend
                    if confirmed_highs:
                        last_unbroken_sh = confirmed_highs[-1]
                    if confirmed_lows:
                        last_unbroken_sl = confirmed_lows[-1]

        elif trend_bias == TrendBias.BEARISH and last_lh is not None and not last_lh.consumed:
            lh_level = last_lh.price
            if up_break > lh_level:
                disp = (up_break - lh_level) / lh_level
                if disp >= min_displacement_pct:
                    bos_choch_events.append(
                        StructureEvent(
                            event_type=f"{scope_str}_CHoCH",
                            ts=bar.ts,
                            direction="BULLISH",
                            level=lh_level,
                            zone_low=None,
                            zone_high=None,
                            label=None,
                            trend_bias=trend_bias.value,
                            strength=disp,
                            touch_count=None,
                            mitigated=False,
                            bar_index=t,
                            pivot_bar_index=last_lh.bar_index,
                            structure_scope=scope_str,
                        )
                    )
                    last_lh.consumed = True
                    # STEP 7 — flip bias to BULLISH and make it persist (override).
                    trend_bias = TrendBias.BULLISH
                    choch_override = TrendBias.BULLISH
                    override_anchor_bi = max(
                        (p.bar_index for p in (confirmed_highs + confirmed_lows)),
                        default=-1,
                    )
                    if confirmed_highs:
                        last_unbroken_sh = confirmed_highs[-1]
                    if confirmed_lows:
                        last_unbroken_sl = confirmed_lows[-1]

        # Capture the authoritative bias for this bar (post BOS/CHoCH) for pivot-event snapshots.
        bias_at[t] = trend_bias.value

    # Stamp each pivot event's trend_bias snapshot with the bias active at its confirmation bar.
    for ev in events:
        ev.trend_bias = bias_at.get(ev.bar_index, TrendBias.RANGING.value)

    # Merge pivot + BOS/CHoCH events, sort by bar_index
    all_events = events + bos_choch_events
    all_events.sort(key=lambda e: e.bar_index)

    # STEP 8 — EQH/EQL cluster zones (zone-level records appended separately)

    # STEP 9 — structure_levels snapshot
    _last_sh = confirmed_highs[-1] if confirmed_highs else None
    _last_sl = confirmed_lows[-1] if confirmed_lows else None
    _last_hl_pivot = next((p for p in reversed(confirmed_lows) if p.label == "HL"), None)
    _last_lh_pivot = next((p for p in reversed(confirmed_highs) if p.label == "LH"), None)
    structure_levels: dict[str, object] = {
        "last_swing_high": (
            {
                "price": _last_sh.price,
                "ts": _last_sh.ts,
                "label": _last_sh.label,
                "bar_index": _last_sh.bar_index,
            }
            if _last_sh
            else None
        ),
        "last_swing_low": (
            {
                "price": _last_sl.price,
                "ts": _last_sl.ts,
                "label": _last_sl.label,
                "bar_index": _last_sl.bar_index,
            }
            if _last_sl
            else None
        ),
        "last_unbroken_swing_high": (
            {
                "price": last_unbroken_sh.price,
                "ts": last_unbroken_sh.ts,
                "bar_index": last_unbroken_sh.bar_index,
            }
            if last_unbroken_sh
            else None
        ),
        "last_unbroken_swing_low": (
            {
                "price": last_unbroken_sl.price,
                "ts": last_unbroken_sl.ts,
                "bar_index": last_unbroken_sl.bar_index,
            }
            if last_unbroken_sl
            else None
        ),
        "last_HL": (
            {
                "price": _last_hl_pivot.price,
                "ts": _last_hl_pivot.ts,
                "bar_index": _last_hl_pivot.bar_index,
            }
            if _last_hl_pivot
            else None
        ),
        "last_LH": (
            {
                "price": _last_lh_pivot.price,
                "ts": _last_lh_pivot.ts,
                "bar_index": _last_lh_pivot.bar_index,
            }
            if _last_lh_pivot
            else None
        ),
        "trend_bias": trend_bias.value,
    }

    return all_events, trend_bias, structure_levels


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_swing_structure(
    bars: list[PriceBar],
    *,
    swing_left: int = 5,
    swing_right: int = 5,
    internal_left: int = 3,
    internal_right: int = 3,
    eq_threshold: float = 0.0015,
    eqh_lookback: int = 50,
    use_body_close: bool = True,
    min_displacement_pct: float = 0.0,
) -> MarketStructure:
    """Detect full SMC market structure over ``bars``.

    This is the top-level aggregator that executes all 10 steps from the spec:
    pivot detection, labelling, trend bias, BOS, CHoCH, EQH/EQL clustering, and
    structure-level tracking.

    Parameters match the spec's parameter table exactly (see docs/CHART_READING.md §1).
    No lookahead: pivots confirm only after ``swing_right`` (or ``internal_right``) bars.
    """
    if not bars:
        return MarketStructure(
            events=[],
            swing_pivots=[],
            internal_pivots=[],
            trend_bias=TrendBias.RANGING,
            int_trend_bias=TrendBias.RANGING,
            liquidity_levels=[],
            structure_levels={},
        )

    # STEP 1 — swing pivots
    pivot_highs, pivot_lows = _detect_raw_pivots(
        bars, swing_left, swing_right, StructureScope.SWING, eq_threshold
    )

    # STEP 2 — internal pivots
    int_pivot_highs, int_pivot_lows = _detect_raw_pivots(
        bars, internal_left, internal_right, StructureScope.INTERNAL, eq_threshold
    )

    # STEPS 3-10 for swing structure
    swing_events, swing_bias, structure_levels = _run_structure(
        bars,
        pivot_highs,
        pivot_lows,
        StructureScope.SWING,
        eq_threshold,
        eqh_lookback,
        use_body_close,
        min_displacement_pct,
    )

    # STEPS 3-10 for internal structure (bias from internal only — never overwrites swing bias)
    int_events, int_bias, int_levels = _run_structure(
        bars,
        int_pivot_highs,
        int_pivot_lows,
        StructureScope.INTERNAL,
        eq_threshold,
        eqh_lookback,
        use_body_close,
        min_displacement_pct,
    )

    # Merge events, sort chronologically
    all_events = swing_events + int_events
    all_events.sort(key=lambda e: e.bar_index)

    # STEP 8 — EQH/EQL liquidity zones (swing scope only — used for targets)
    liquidity_levels = _cluster_eqh_eql(
        pivot_highs, pivot_lows, eq_threshold, eqh_lookback, len(bars) - 1, bars
    )
    # Emit EQH/EQL zone events
    for lz in liquidity_levels:
        all_events.append(
            StructureEvent(
                event_type=lz.level_type,
                ts=lz.ts_last,
                direction=None,
                level=(lz.zone_high + lz.zone_low) / 2.0,
                zone_low=lz.zone_low,
                zone_high=lz.zone_high,
                label=lz.level_type,
                trend_bias=swing_bias.value,
                strength=float(lz.touch_count),
                touch_count=lz.touch_count,
                mitigated=lz.mitigated,
                bar_index=len(bars) - 1,
                pivot_bar_index=len(bars) - 1,
                structure_scope="swing",
            )
        )

    # Add int_trend_bias / int_* keys to structure_levels
    structure_levels["int_trend_bias"] = int_bias.value
    structure_levels["last_int_swing_high"] = int_levels.get("last_swing_high")
    structure_levels["last_int_swing_low"] = int_levels.get("last_swing_low")

    return MarketStructure(
        events=all_events,
        swing_pivots=pivot_highs + pivot_lows,
        internal_pivots=int_pivot_highs + int_pivot_lows,
        trend_bias=swing_bias,
        int_trend_bias=int_bias,
        liquidity_levels=liquidity_levels,
        structure_levels=structure_levels,
    )


def classify_choch_bos(
    bars: list[PriceBar],
    *,
    swing_left: int = 5,
    swing_right: int = 5,
    internal_left: int = 3,
    internal_right: int = 3,
    eq_threshold: float = 0.0015,
    eqh_lookback: int = 50,
    use_body_close: bool = True,
    min_displacement_pct: float = 0.0,
) -> list[StructureEvent]:
    """Return only BOS and CHoCH events (swing + internal) over ``bars``."""
    ms = detect_swing_structure(
        bars,
        swing_left=swing_left,
        swing_right=swing_right,
        internal_left=internal_left,
        internal_right=internal_right,
        eq_threshold=eq_threshold,
        eqh_lookback=eqh_lookback,
        use_body_close=use_body_close,
        min_displacement_pct=min_displacement_pct,
    )
    return [
        e for e in ms.events if e.event_type.endswith("_BOS") or e.event_type.endswith("_CHoCH")
    ]


def compute_trend_bias(events: list[StructureEvent]) -> TrendBias:
    """Return the final trend_bias from a list of structure events.

    Uses the trend_bias snapshot on the last event (by bar_index) that carries a
    non-RANGING bias.  BOS/CHoCH events are preferred; if none exist, the last
    pivot event's snapshot is used.  Falls back to RANGING if the list is empty or
    all snapshots are RANGING.
    """
    if not events:
        return TrendBias.RANGING
    # Prefer the latest BOS/CHoCH event whose bias snapshot is non-RANGING
    action_events = sorted(
        (e for e in events if e.event_type.endswith("_BOS") or e.event_type.endswith("_CHoCH")),
        key=lambda e: e.bar_index,
        reverse=True,
    )
    for ev in action_events:
        try:
            b = TrendBias(ev.trend_bias)
            if b != TrendBias.RANGING:
                return b
        except ValueError:
            pass
    # Fall back to the last event's bias snapshot (covers the case where only pivots
    # are present but the 2-pivot rule has already established a non-RANGING bias)
    for ev in sorted(events, key=lambda e: e.bar_index, reverse=True):
        try:
            b = TrendBias(ev.trend_bias)
            if b != TrendBias.RANGING:
                return b
        except ValueError:
            pass
    return TrendBias.RANGING


def detect_eqh_eql(
    bars: list[PriceBar],
    *,
    swing_left: int = 5,
    swing_right: int = 5,
    eq_threshold: float = 0.0015,
    eqh_lookback: int = 50,
) -> list[LiquidityLevel]:
    """Return all EQH/EQL liquidity zone clusters detected in ``bars``."""
    ms = detect_swing_structure(
        bars,
        swing_left=swing_left,
        swing_right=swing_right,
        eq_threshold=eq_threshold,
        eqh_lookback=eqh_lookback,
    )
    return ms.liquidity_levels
