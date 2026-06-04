"""Fair Value Gap (FVG) detector — ICT / Smart Money Concepts.

Implements the canonical 3-candle FVG pattern per docs/CHART_READING.md §2.

Pure Python (stdlib only). No pandas / numpy / TA-Lib.

Detection is strictly causal: a feature at bar-index i uses only bars[0..i].
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from statistics import mean

from data.models import PriceBar

# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

_TS = date | datetime


@dataclass
class FVGZone:
    """A single detected Fair Value Gap (may be updated in-place after formation)."""

    fvg_id: str
    symbol: str
    freq: str
    direction: str  # 'bullish' | 'bearish'
    ts: _TS  # displacement candle (c2) timestamp
    formation_bar_idx: int  # index of c3 in the input list

    zone_low: float
    zone_high: float
    zone_mid: float  # CE = (zone_low + zone_high) / 2

    gap_size: float
    gap_size_atr: float

    strength: str  # 'strong' | 'normal' | 'weak'

    mitigated: bool = False
    partial_mitigated_ce: bool = False
    mitigation_type: str = "none"  # 'none' | 'ce' | 'full'
    mitigation_ts: _TS | None = None

    inverted: bool = False
    ifvg_active: bool = False
    ifvg_direction: str | None = None  # 'bullish' | 'bearish'
    inversion_ts: _TS | None = None

    # Internal: True when dropped purely for capacity (max_active_fvgs), NOT by
    # price action. Such zones are excluded from active/mitigated/IFVG monitoring
    # so a capacity eviction never fabricates a mitigation or inversion signal.
    evicted: bool = field(default=False, repr=False)


@dataclass
class FVGResult:
    """Top-level result from running the full FVG pipeline over a bar series."""

    all_fvgs: list[FVGZone] = field(default_factory=list)
    active_fvgs: list[FVGZone] = field(default_factory=list)
    mitigated_fvgs: list[FVGZone] = field(default_factory=list)
    active_ifvgs: list[FVGZone] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _true_range(bar: PriceBar, prev_close: float | None) -> float:
    hl = bar.high - bar.low
    if prev_close is None:
        return hl
    return max(hl, abs(bar.high - prev_close), abs(bar.low - prev_close))


def _compute_atr(bars: list[PriceBar], atr_period: int) -> list[float]:
    """Return per-bar ATR list (same length as bars). ATR[i] uses bars[0..i]."""
    trs: list[float] = []
    atrs: list[float] = []
    for idx, bar in enumerate(bars):
        prev_close = bars[idx - 1].close if idx > 0 else None
        trs.append(_true_range(bar, prev_close))
        window = trs[max(0, idx - atr_period + 1) : idx + 1]
        atrs.append(mean(window))
    return atrs


def _compute_avg_body(bars: list[PriceBar], body_lookback: int) -> list[float]:
    """Return per-bar average-body list.

    avg_body[i] = mean of abs(close-open) for bars[i-body_lookback .. i-1]
    (excludes bar i itself to avoid lookahead).  For bars with < 1 predecessor
    the average is 0.0.
    """
    avg: list[float] = []
    for i in range(len(bars)):
        start = max(0, i - body_lookback)
        window = [abs(bars[j].close - bars[j].open) for j in range(start, i)]
        avg.append(mean(window) if window else 0.0)
    return avg


def _bar_valid(bar: PriceBar) -> bool:
    return (
        bar.high >= bar.low and bar.low <= bar.open <= bar.high and bar.low <= bar.close <= bar.high
    )


def _classify_strength(
    c1: PriceBar,
    c2: PriceBar,
    c3: PriceBar,
    direction: str,
    bars: list[PriceBar],
    c3_idx: int,
    swing_lookback: int,
) -> str:
    """Classify FVG strength per spec Step 8."""
    # STRONG: c3 closes beyond c2 extreme
    if direction == "bullish" and c3.close > c2.high:
        return "strong"
    if direction == "bearish" and c3.close < c2.low:
        return "strong"

    # WEAK: entire 3-candle formation contained within a prior candle's range
    form_high = max(c1.high, c2.high, c3.high)
    form_low = min(c1.low, c2.low, c3.low)
    # bars[i-2-swing_lookback : i-2] where i = c3_idx  →  c1_idx = c3_idx-2
    c1_idx = c3_idx - 2
    lo = max(0, c1_idx - swing_lookback)
    for p_idx in range(lo, c1_idx):
        p = bars[p_idx]
        if p.high >= form_high and p.low <= form_low:
            return "weak"

    return "normal"


def _make_fvg_id(symbol: str, freq: str, ts: _TS, direction: str) -> str:
    return f"{symbol}_{freq}_{ts}_{direction}"


# ---------------------------------------------------------------------------
# Core public API
# ---------------------------------------------------------------------------


def detect_fvg(
    bars: list[PriceBar],
    *,
    min_gap_atr_mult: float = 0.15,
    body_mult: float = 1.15,
    body_lookback: int = 14,
    atr_period: int = 14,
    swing_lookback: int = 5,
    max_active_fvgs: int = 50,
) -> list[FVGZone]:
    """Detect all FVG zones from a chronological bar series.

    Returns a list of FVGZone objects sorted by ts.  Mitigation and IFVG
    state are tracked up to the last bar in *bars* — callers can update
    further by passing a superset of bars.
    """
    if len(bars) < 3:
        return []

    atrs = _compute_atr(bars, atr_period)
    avg_bodies = _compute_avg_body(bars, body_lookback)

    zones: list[FVGZone] = []
    active_count = 0

    for i in range(2, len(bars)):
        c1, c2, c3 = bars[i - 2], bars[i - 1], bars[i]

        # Step 3 — validity
        if not (_bar_valid(c1) and _bar_valid(c2) and _bar_valid(c3)):
            continue

        # Step 4 — gap check
        bull_gap = c3.low - c1.high
        bear_gap = c1.low - c3.high

        if bull_gap <= 0 and bear_gap <= 0:
            continue

        direction = "bullish" if bull_gap > 0 else "bearish"
        gap_size = bull_gap if direction == "bullish" else bear_gap

        # Steps 5a/5b — minimum gap size
        if gap_size < min_gap_atr_mult * atrs[i]:
            continue

        # Step 6 — displacement candle direction
        if direction == "bullish" and c2.close <= c2.open:
            continue
        if direction == "bearish" and c2.close >= c2.open:
            continue

        # Step 7 — displacement candle body-size filter
        # avg_body for c2 = avg_bodies[i-1] which ends at bar i-2 (no lookahead)
        c2_body = abs(c2.close - c2.open)
        avg_b = avg_bodies[i - 1]
        if avg_b > 0 and c2_body < body_mult * avg_b:
            continue

        # Step 8 — strength classification
        strength = _classify_strength(c1, c2, c3, direction, bars, i, swing_lookback)

        # Step 9 — record FVG zone
        if direction == "bullish":
            zone_low = c1.high
            zone_high = c3.low
        else:
            zone_high = c1.low
            zone_low = c3.high

        zone_mid = (zone_low + zone_high) / 2.0
        gap_size_abs = zone_high - zone_low
        gap_size_atr = gap_size_abs / atrs[i] if atrs[i] > 0 else 0.0

        # Enforce max_active_fvgs cap — evict oldest active if needed.
        # Eviction is a *capacity* decision, not a price event: we mark the zone
        # ``evicted`` so the mitigation/IFVG pass skips it entirely. Fabricating a
        # ``full`` mitigation here (the previous behaviour) would let the IFVG
        # monitor invert a zone price never actually touched — a phantom signal.
        if active_count >= max_active_fvgs:
            for old in zones:
                if not old.mitigated and not old.inverted and not old.evicted:
                    old.evicted = True
                    active_count -= 1
                    break

        zone = FVGZone(
            fvg_id=_make_fvg_id(c2.symbol, c2.freq, c2.ts, direction),
            symbol=c2.symbol,
            freq=c2.freq,
            direction=direction,
            ts=c2.ts,
            formation_bar_idx=i,
            zone_low=zone_low,
            zone_high=zone_high,
            zone_mid=zone_mid,
            gap_size=gap_size_abs,
            gap_size_atr=gap_size_atr,
            strength=strength,
        )
        zones.append(zone)
        active_count += 1

    # Step 10 & 11 — mitigation + IFVG tracking (retroactive state update)
    _update_mitigation_and_ifvg(zones, bars)

    return sorted(zones, key=lambda z: z.ts)


def _update_mitigation_and_ifvg(
    zones: list[FVGZone],
    bars: list[PriceBar],
    *,
    ifvg_require_body_close: bool = True,
) -> None:
    """Retroactively update mitigation and IFVG state on all zones.

    Only bars AFTER formation_bar_idx are examined (no lookahead in detection).
    This function is called after detection and purely updates state flags.
    """
    for zone in zones:
        if zone.evicted:
            # Capacity-evicted zone: never participated in price-based state,
            # so it must not accrue mitigation or IFVG inversion.
            continue
        for j in range(zone.formation_bar_idx + 1, len(bars)):
            bar = bars[j]

            # --- Step 10: mitigation tracking ---
            if not zone.mitigated:
                if zone.direction == "bullish":
                    if bar.low <= zone.zone_low:
                        # full mitigation
                        zone.mitigated = True
                        zone.mitigation_type = "full"
                        zone.mitigation_ts = bar.ts
                    elif bar.low <= zone.zone_mid:
                        # CE touch only
                        zone.partial_mitigated_ce = True
                        zone.mitigation_type = "ce"
                        # Do NOT set mitigated=True for CE-only touch
                else:  # bearish
                    if bar.high >= zone.zone_high:
                        zone.mitigated = True
                        zone.mitigation_type = "full"
                        zone.mitigation_ts = bar.ts
                    elif bar.high >= zone.zone_mid:
                        zone.partial_mitigated_ce = True
                        zone.mitigation_type = "ce"

            # --- Step 11: IFVG inversion (only after full mitigation) ---
            if zone.mitigated and not zone.inverted:
                if zone.direction == "bullish":
                    # Inversion: close below zone_low
                    if ifvg_require_body_close:
                        triggered = bar.close < zone.zone_low
                    else:
                        triggered = bar.low < zone.zone_low
                    if triggered:
                        zone.inverted = True
                        zone.ifvg_active = True
                        zone.ifvg_direction = "bearish"
                        zone.inversion_ts = bar.ts
                else:  # bearish FVG inverts to bullish
                    if ifvg_require_body_close:
                        triggered = bar.close > zone.zone_high
                    else:
                        triggered = bar.high > zone.zone_high
                    if triggered:
                        zone.inverted = True
                        zone.ifvg_active = True
                        zone.ifvg_direction = "bullish"
                        zone.inversion_ts = bar.ts

            # --- Step 12: IFVG invalidation ---
            if zone.ifvg_active:
                if zone.ifvg_direction == "bearish":
                    # Originally bullish FVG → bearish IFVG invalidated if close > zone_high
                    if bar.close > zone.zone_high:
                        zone.ifvg_active = False
                else:  # bullish IFVG (originally bearish FVG)
                    if bar.close < zone.zone_low:
                        zone.ifvg_active = False


def classify_fvg_strength(zone: FVGZone, atr: float) -> str:
    """Re-classify a zone's strength using an external ATR value.

    Returns 'strong', 'normal', or 'weak' based on gap_size vs ATR.
    This is a standalone classifier that can be called post-detection.

    - gap_size_atr > 3.0  → marks a potential breakaway gap (returns 'strong' per
      size, but callers should note the high-ATR caveat from the spec).
    - gap_size_atr < min_gap_atr_mult threshold is already filtered at detection
      time; this helper re-evaluates using the caller-supplied ATR.
    """
    if atr <= 0:
        return zone.strength
    ratio = zone.gap_size / atr
    if ratio >= 2.0 or zone.strength == "strong":
        return "strong"
    if ratio >= 0.5:
        return "normal"
    return "weak"


def check_fvg_mitigation(zone: FVGZone, current_bar: PriceBar) -> str:
    """Evaluate mitigation status of a zone against a single bar.

    Returns the mitigation type that *this bar* would trigger:
    ``'full'``, ``'ce'``, or ``'none'``.  Does NOT mutate the zone.
    """
    if zone.direction == "bullish":
        if current_bar.low <= zone.zone_low:
            return "full"
        if current_bar.low <= zone.zone_mid:
            return "ce"
    else:  # bearish
        if current_bar.high >= zone.zone_high:
            return "full"
        if current_bar.high >= zone.zone_mid:
            return "ce"
    return "none"


def detect_ifvg(
    bars: list[PriceBar],
    fvg_zones: list[FVGZone],
) -> list[FVGZone]:
    """Return the subset of fvg_zones that are currently active IFVGs.

    If the supplied zones were produced by ``detect_fvg`` over the same *bars*
    their state is already up-to-date, so this function simply filters.
    If *bars* extends beyond the last bar used when the zones were produced,
    call ``_update_mitigation_and_ifvg`` directly before calling this helper.
    """
    return [z for z in fvg_zones if z.ifvg_active]


# ---------------------------------------------------------------------------
# Top-level aggregator
# ---------------------------------------------------------------------------


def run_fvg(
    bars: list[PriceBar],
    *,
    min_gap_atr_mult: float = 0.15,
    body_mult: float = 1.15,
    body_lookback: int = 14,
    atr_period: int = 14,
    swing_lookback: int = 5,
    ifvg_require_body_close: bool = True,
    max_active_fvgs: int = 50,
) -> FVGResult:
    """Run the complete FVG pipeline over *bars* and return a FVGResult.

    This is the canonical entry point for downstream consumers.  It:
    1. Detects all FVG zones.
    2. Applies mitigation + IFVG state with the caller-supplied
       ``ifvg_require_body_close`` flag.
    3. Partitions zones into active / mitigated / active-IFVG sub-lists.
    """
    zones = detect_fvg(
        bars,
        min_gap_atr_mult=min_gap_atr_mult,
        body_mult=body_mult,
        body_lookback=body_lookback,
        atr_period=atr_period,
        swing_lookback=swing_lookback,
        max_active_fvgs=max_active_fvgs,
    )

    # Re-run mitigation/IFVG pass with caller's ifvg_require_body_close setting.
    # (detect_fvg already ran this internally with the default; re-running is
    # idempotent for the default case and corrects for non-default flag.)
    if not ifvg_require_body_close:
        # Reset state and re-apply with the relaxed flag
        for z in zones:
            z.mitigated = False
            z.partial_mitigated_ce = False
            z.mitigation_type = "none"
            z.mitigation_ts = None
            z.inverted = False
            z.ifvg_active = False
            z.ifvg_direction = None
            z.inversion_ts = None
        _update_mitigation_and_ifvg(zones, bars, ifvg_require_body_close=False)

    result = FVGResult(all_fvgs=zones)
    for z in zones:
        if z.evicted:
            # Dropped for capacity only — not a live active zone nor a real
            # mitigation. Keep it in all_fvgs for auditability, but exclude from
            # the actionable sub-lists.
            continue
        if z.ifvg_active:
            result.active_ifvgs.append(z)
        if z.mitigated:
            result.mitigated_fvgs.append(z)
        else:
            result.active_fvgs.append(z)
    return result
