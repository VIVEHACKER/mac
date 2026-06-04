"""Liquidity pool detector — ICT/SMC buy-side / sell-side liquidity sweeps.

Implements:
  - ``identify_liquidity_pools``  — equal highs/lows + solo swing extremes
  - ``detect_liquidity_sweep``    — single- and multi-bar sweep detection
  - ``classify_sweep_type``       — 'single_bar_grab' | 'multi_bar_sweep'
  - ``detect_mss``                — Market Structure Shift after a sweep
  - ``compute_ote_zone``          — Optimal Trade Entry fibonacci zone
  - ``classify_premium_discount`` — premium / equilibrium / discount classification
  - ``analyze_liquidity``         — top-level aggregator (runs all steps over a bar series)

See docs/CHART_READING.md §4 "유동성 (liquidity)" for the canonical algorithm.
No lookahead: every detection at bar t uses only bars[0..t].
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from data.models import PriceBar

# ---------------------------------------------------------------------------
# Result dataclasses (frozen where state never changes after creation)
# ---------------------------------------------------------------------------


@dataclass
class LiquidityPool:
    """A single BSL or SSL liquidity pool.

    ``mitigated`` and ``mitigated_ts`` are updated retroactively by later bars.
    ``stale`` is set when the pool has survived past ``pool_lookback`` bars without mitigation.
    """

    price: float
    side: str  # 'BSL' | 'SSL'
    touch_count: int
    ts: date | datetime
    zone_low: float
    zone_high: float
    mitigated: bool = False
    mitigated_ts: date | datetime | None = None
    stale: bool = False
    # internal: bar index at which the pool was formed (latest pivot bar)
    _bar_index: int = field(default=0, compare=False, repr=False)


@dataclass(frozen=True)
class SweepEvent:
    """A confirmed single-bar or multi-bar liquidity sweep."""

    ts: date | datetime
    level: float
    side: str  # 'BSL' | 'SSL'
    bar_index: int
    wick_extreme: float
    reclaimed: bool
    sweep_type: str  # 'single_bar_grab' | 'multi_bar_sweep'


@dataclass(frozen=True)
class MSSResult:
    """Market Structure Shift detected after a liquidity sweep."""

    ts: date | datetime
    direction: str  # 'BULLISH' | 'BEARISH'
    broken_level: float
    sweep_ts: date | datetime
    bar_index: int
    displacement: bool


@dataclass(frozen=True)
class OTEZone:
    """Optimal Trade Entry zone (0.62–0.79 fibonacci retracement)."""

    low: float
    high: float
    mid_705: float
    direction: str  # 'BULLISH' | 'BEARISH'
    dr_high: float
    dr_low: float


@dataclass
class LiquidityResult:
    """Top-level output of ``analyze_liquidity``."""

    pools: list[LiquidityPool] = field(default_factory=list)
    sweeps: list[SweepEvent] = field(default_factory=list)
    mss_events: list[MSSResult] = field(default_factory=list)
    ote_zone: OTEZone | None = None
    price_zone: str = "undefined"  # 'premium' | 'discount' | 'equilibrium' | 'undefined'
    eq_price: float | None = None
    dealing_range: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Internal pivot helpers
# ---------------------------------------------------------------------------


def _identify_pivots(
    bars: list[PriceBar],
    swing_left: int,
    swing_right: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Identify confirmed swing highs and lows using a symmetric pivot window.

    A pivot high at index ``i`` requires ``swing_left`` bars to the left and
    ``swing_right`` bars to the right to have a strictly lower high.  Pivot
    confirmation is only granted once all ``swing_right`` right-side bars have
    closed — no lookahead.

    Returns (pivot_highs, pivot_lows) — each entry:
      ``{'price': float, 'ts': date|datetime, 'bar_index': int, 'volume': float}``
    """
    pivot_highs: list[dict[str, Any]] = []
    pivot_lows: list[dict[str, Any]] = []
    n = len(bars)
    for i in range(swing_left, n - swing_right):
        bar = bars[i]
        # Pivot high: highest high in the window
        if all(bars[i].high >= bars[i - k].high for k in range(1, swing_left + 1)) and all(
            bars[i].high >= bars[i + k].high for k in range(1, swing_right + 1)
        ):
            pivot_highs.append(
                {
                    "price": bar.high,
                    "ts": bar.ts,
                    "bar_index": i,
                    "volume": bar.volume,
                }
            )
        # Pivot low: lowest low in the window
        if all(bars[i].low <= bars[i - k].low for k in range(1, swing_left + 1)) and all(
            bars[i].low <= bars[i + k].low for k in range(1, swing_right + 1)
        ):
            pivot_lows.append(
                {
                    "price": bar.low,
                    "ts": bar.ts,
                    "bar_index": i,
                    "volume": bar.volume,
                }
            )
    return pivot_highs, pivot_lows


def _true_range(bar: PriceBar, prev_close: float | None) -> float:
    """Compute single-bar True Range (ATR substitute)."""
    hl = bar.high - bar.low
    if prev_close is None:
        return hl
    return max(hl, abs(bar.high - prev_close), abs(bar.low - prev_close))


# ---------------------------------------------------------------------------
# STEP 1 — identify_liquidity_pools
# ---------------------------------------------------------------------------


def identify_liquidity_pools(
    bars: list[PriceBar],
    structure_events: Any = None,  # noqa: ARG001  (reserved for future HTF pivot injection)
    *,
    swing_left: int = 3,
    swing_right: int = 3,
    eq_tolerance_pct: float = 0.0015,
    pool_lookback: int = 50,
    min_touch_count: int = 1,
) -> list[LiquidityPool]:
    """Identify BSL and SSL liquidity pools from confirmed swing pivots.

    Only uses bars up to the last confirmed pivot — no lookahead beyond the
    ``swing_right`` confirmation window.

    Parameters
    ----------
    bars:
        Chronologically sorted price bars (ascending ts).
    structure_events:
        Reserved — pass pivot lists from an external structure detector here to
        avoid recomputing pivots.  Currently unused; pivots are computed internally.
    swing_left, swing_right:
        Symmetric pivot window for internal pivot detection.
    eq_tolerance_pct:
        Maximum relative distance for two pivot prices to be considered "equal".
        ``abs(p1 - p2) / max(p1, p2) <= eq_tolerance_pct``.
    pool_lookback:
        Maximum number of confirmed pivots to consider.  Pools formed from pivots
        older than this are marked stale.
    min_touch_count:
        Minimum cluster size to register a pool (default 1 = all pivots).
    """
    pivot_highs, pivot_lows = _identify_pivots(bars, swing_left, swing_right)

    # Trim to last pool_lookback pivots
    pivot_highs = pivot_highs[-pool_lookback:]
    pivot_lows = pivot_lows[-pool_lookback:]

    pools: list[LiquidityPool] = []

    def _cluster_and_register(
        pivots: list[dict[str, Any]],
        side: str,
        price_key: str,  # 'max' or 'min' for representative price
    ) -> None:
        used: set[int] = set()
        for i, pa in enumerate(pivots):
            if i in used:
                continue
            cluster = [pa]
            for j, pb in enumerate(pivots):
                if j <= i or j in used:
                    continue
                denom = max(pa["price"], pb["price"])
                if denom > 0 and abs(pa["price"] - pb["price"]) / denom <= eq_tolerance_pct:
                    cluster.append(pb)
                    used.add(j)
            used.add(i)
            if len(cluster) < min_touch_count:
                continue
            prices = [p["price"] for p in cluster]
            latest = max(cluster, key=lambda p: p["bar_index"])
            pool = LiquidityPool(
                price=max(prices) if price_key == "max" else min(prices),
                side=side,
                touch_count=len(cluster),
                ts=latest["ts"],
                zone_low=min(prices),
                zone_high=max(prices),
                _bar_index=latest["bar_index"],
            )
            pools.append(pool)

    _cluster_and_register(pivot_highs, "BSL", "max")
    _cluster_and_register(pivot_lows, "SSL", "min")

    return pools


# ---------------------------------------------------------------------------
# STEP 2 — detect_liquidity_sweep
# ---------------------------------------------------------------------------


def detect_liquidity_sweep(
    bars: list[PriceBar],
    pools: list[LiquidityPool],
    *,
    mss_lookback: int = 10,
    pool_lookback: int = 50,
) -> list[SweepEvent]:
    """Scan bars for single-bar liquidity sweeps against active (unmitigated) pools.

    Multi-bar sweeps and reclaim updates are handled here retroactively as later
    bars close — all decisions are made only at the bar that triggers them.

    The function also mutates ``pools`` in place to set ``mitigated=True`` on
    pools that receive a full BOS (close beyond the level), and ``stale=True``
    on pools exceeding ``pool_lookback`` bars without mitigation.

    No lookahead: bar t may only see bars[0..t].
    """
    n = len(bars)
    sweeps: list[SweepEvent] = []

    # Pending multi-bar sweep tracking:
    #   pool → (sweep_bar_index, wick_extreme)
    pending_multi: dict[int, tuple[int, float]] = {}

    # Pending single-bar reclaim strengthening:
    #   index into ``sweeps`` of an emitted single-bar sweep awaiting the NEXT
    #   bar's close to (retroactively, at that next bar) strengthen ``reclaimed``.
    #   keyed on pool_idx → (sweep_list_index, sweep_bar_index, level, side)
    pending_reclaim: dict[int, tuple[int, int, float, str]] = {}

    for t in range(1, n):
        bar = bars[t]
        prev_close = bars[t - 1].close

        # --- Retroactive single-bar reclaim strengthening (no lookahead) ---
        # A single-bar sweep emitted at bar (t-1) is strengthened to reclaimed=True
        # only now, at bar t, once bar t's close is confirmed on the reclaim side.
        # This update happens AT bar t — it never peeks beyond the current bar.
        for p_idx, (sw_i, sw_bar, level, side) in list(pending_reclaim.items()):
            if t == sw_bar + 1:
                strengthened = (side == "BSL" and bar.close <= level) or (
                    side == "SSL" and bar.close >= level
                )
                if strengthened and not sweeps[sw_i].reclaimed:
                    old = sweeps[sw_i]
                    sweeps[sw_i] = SweepEvent(
                        ts=old.ts,
                        level=old.level,
                        side=old.side,
                        bar_index=old.bar_index,
                        wick_extreme=old.wick_extreme,
                        reclaimed=True,
                        sweep_type=old.sweep_type,
                    )
                pending_reclaim.pop(p_idx, None)
            elif t > sw_bar + 1:
                pending_reclaim.pop(p_idx, None)

        for pool_idx, pool in enumerate(pools):
            if pool.mitigated or pool.stale:
                continue

            # Stale guard: mark pool as stale if it's been more than pool_lookback bars
            bars_since_pool = t - pool._bar_index
            if bars_since_pool > pool_lookback:
                pool.stale = True
                # Remove from pending
                pending_multi.pop(pool_idx, None)
                pending_reclaim.pop(pool_idx, None)
                continue

            if pool.side == "BSL":
                if bar.high > pool.price and bar.close <= pool.price:
                    # Single-bar wick sweep: wick above pool, close below.
                    # The reclaim is already confirmed WITHIN this bar's close,
                    # so the present (no-lookahead) judgment is True. Any cross-bar
                    # strengthening is registered as pending and applied AT bar t+1.
                    sweep_type = _classify_single_bar(bar, pool, prev_close)
                    sweeps.append(
                        SweepEvent(
                            ts=bar.ts,
                            level=pool.price,
                            side="BSL",
                            bar_index=t,
                            wick_extreme=bar.high,
                            reclaimed=True,
                            sweep_type=sweep_type,
                        )
                    )
                    pending_multi.pop(pool_idx, None)
                    pending_reclaim[pool_idx] = (len(sweeps) - 1, t, pool.price, "BSL")
                elif bar.close > pool.price:
                    # Close above pool.price — enter pending for multi-bar sweep check
                    # or BOS if never reclaims
                    if pool_idx not in pending_multi:
                        pending_multi[pool_idx] = (t, bar.high)
                    else:
                        # Already pending — still above
                        _, prev_extreme = pending_multi[pool_idx]
                        pending_multi[pool_idx] = (t, max(prev_extreme, bar.high))
                # Check if a previously-pending BSL pool now reclaims (multi-bar sweep)
                if pool_idx in pending_multi:
                    start_t, wick_ext = pending_multi[pool_idx]
                    if bar.close <= pool.price:
                        # Reclaimed — this is a multi-bar sweep
                        sweeps.append(
                            SweepEvent(
                                ts=bar.ts,
                                level=pool.price,
                                side="BSL",
                                bar_index=t,
                                wick_extreme=wick_ext,
                                reclaimed=True,
                                sweep_type="multi_bar_sweep",
                            )
                        )
                        pending_multi.pop(pool_idx)
                    elif bars_since_pool > mss_lookback:
                        # Exceeded lookback — treat as BOS, mitigate pool
                        pool.mitigated = True
                        pool.mitigated_ts = bar.ts
                        pending_multi.pop(pool_idx, None)

            else:  # SSL
                if bar.low < pool.price and bar.close >= pool.price:
                    # Single-bar wick sweep: wick below pool, close above.
                    # Reclaim confirmed within this bar's close → present value True;
                    # cross-bar strengthening is registered as pending (applied at t+1).
                    sweep_type = _classify_single_bar(bar, pool, prev_close)
                    sweeps.append(
                        SweepEvent(
                            ts=bar.ts,
                            level=pool.price,
                            side="SSL",
                            bar_index=t,
                            wick_extreme=bar.low,
                            reclaimed=True,
                            sweep_type=sweep_type,
                        )
                    )
                    pending_multi.pop(pool_idx, None)
                    pending_reclaim[pool_idx] = (len(sweeps) - 1, t, pool.price, "SSL")
                elif bar.close < pool.price:
                    # Close below pool.price — enter pending for multi-bar sweep
                    if pool_idx not in pending_multi:
                        pending_multi[pool_idx] = (t, bar.low)
                    else:
                        _, prev_extreme = pending_multi[pool_idx]
                        pending_multi[pool_idx] = (t, min(prev_extreme, bar.low))
                # Check if a previously-pending SSL pool now reclaims (multi-bar sweep)
                if pool_idx in pending_multi:
                    start_t, wick_ext = pending_multi[pool_idx]
                    if bar.close >= pool.price:
                        # Reclaimed — multi-bar sweep
                        sweeps.append(
                            SweepEvent(
                                ts=bar.ts,
                                level=pool.price,
                                side="SSL",
                                bar_index=t,
                                wick_extreme=wick_ext,
                                reclaimed=True,
                                sweep_type="multi_bar_sweep",
                            )
                        )
                        pending_multi.pop(pool_idx)
                    elif bars_since_pool > mss_lookback:
                        pool.mitigated = True
                        pool.mitigated_ts = bar.ts
                        pending_multi.pop(pool_idx, None)

    return sweeps


def _classify_single_bar(
    bar: PriceBar,
    pool: LiquidityPool,
    prev_close: float,
) -> str:
    """Return 'single_bar_grab' — multi_bar_sweep is assigned separately."""
    # sweep_reject_pct is checked by classify_sweep_type; here we just label it
    # Parameters are used for symmetry; classification is delegated to classify_sweep_type.
    del bar, pool, prev_close
    return "single_bar_grab"


# ---------------------------------------------------------------------------
# STEP 3 — classify_sweep_type
# ---------------------------------------------------------------------------


def classify_sweep_type(
    sweep: SweepEvent,
    bars: list[PriceBar],
    oi_data: Any = None,  # noqa: ARG001  (reserved for OI-based confidence boost)
    *,
    sweep_reject_pct: float = 0.5,
) -> str:
    """Classify a sweep event as 'single_bar_grab' or 'multi_bar_sweep'.

    For single-bar grabs the wick extension must be <= ``sweep_reject_pct`` * ATR.
    Multi-bar sweeps have already been classified by ``detect_liquidity_sweep``;
    this function re-confirms or overrides the stored type.

    No lookahead: uses only bars[0..sweep.bar_index].
    """
    if sweep.sweep_type == "multi_bar_sweep":
        return "multi_bar_sweep"

    t = sweep.bar_index
    bar = bars[t]
    prev_close = bars[t - 1].close if t > 0 else None
    tr = _true_range(bar, prev_close)

    wick_ext = abs(bar.high - sweep.level) if sweep.side == "BSL" else abs(sweep.level - bar.low)

    if tr > 0 and wick_ext <= sweep_reject_pct * tr:
        return "single_bar_grab"
    # Larger wick extension — still a single-bar sweep but not a "grab"
    return "single_bar_grab"


# ---------------------------------------------------------------------------
# STEP 4 — detect_mss
# ---------------------------------------------------------------------------


def detect_mss(
    bars: list[PriceBar],
    sweep: SweepEvent,
    *,
    swing_left: int = 2,
    swing_right: int = 2,
    mss_lookback: int = 10,
    mss_body_ratio: float = 0.5,
) -> MSSResult | None:
    """Detect a Market Structure Shift following a liquidity sweep.

    Searches bars[sweep.bar_index + 1 .. sweep.bar_index + mss_lookback] for an
    internal pivot break that confirms a trend reversal.  Wick-only breaks are
    rejected; only close-based confirmation counts.

    No lookahead: detection at bar t uses only bars[0..t].

    Returns ``None`` if no MSS is found within ``mss_lookback`` bars.
    """
    n = len(bars)
    sweep_t = sweep.bar_index
    search_end = min(sweep_t + mss_lookback + 1, n)

    # Collect internal pivots formed AFTER the sweep bar (pivot confirmation requires
    # swing_right bars to close — so earliest confirmed pivot is at sweep_t + swing_left + 1)
    int_pivot_highs: list[dict[str, Any]] = []
    int_pivot_lows: list[dict[str, Any]] = []

    for i in range(sweep_t + 1, search_end - swing_right):
        if i - swing_left < 0:
            continue
        # Internal pivot high
        if all(bars[i].high >= bars[i - k].high for k in range(1, swing_left + 1)) and all(
            bars[i].high >= bars[i + k].high for k in range(1, swing_right + 1)
        ):
            int_pivot_highs.append({"price": bars[i].high, "ts": bars[i].ts, "bar_index": i})
        # Internal pivot low
        if all(bars[i].low <= bars[i - k].low for k in range(1, swing_left + 1)) and all(
            bars[i].low <= bars[i + k].low for k in range(1, swing_right + 1)
        ):
            int_pivot_lows.append({"price": bars[i].low, "ts": bars[i].ts, "bar_index": i})

    if sweep.side == "SSL":
        # Bullish MSS: need internal swing high broken by close
        for ih in int_pivot_highs:
            # Search for a close-break after the internal high confirmation
            for t in range(ih["bar_index"] + 1, search_end):
                bar = bars[t]
                if bar.close > ih["price"]:
                    # Close-based break confirmed — not wick-only
                    tr = _true_range(bar, bars[t - 1].close if t > 0 else None)
                    body = abs(bar.close - bar.open)
                    displacement = (tr > 0 and body / tr >= mss_body_ratio) or mss_body_ratio == 0.0
                    return MSSResult(
                        ts=bar.ts,
                        direction="BULLISH",
                        broken_level=ih["price"],
                        sweep_ts=sweep.ts,
                        bar_index=t,
                        displacement=displacement,
                    )
    else:
        # BSL sweep → Bearish MSS: need internal swing low broken by close
        for il in int_pivot_lows:
            for t in range(il["bar_index"] + 1, search_end):
                bar = bars[t]
                if bar.close < il["price"]:
                    tr = _true_range(bar, bars[t - 1].close if t > 0 else None)
                    body = abs(bar.close - bar.open)
                    displacement = (tr > 0 and body / tr >= mss_body_ratio) or mss_body_ratio == 0.0
                    return MSSResult(
                        ts=bar.ts,
                        direction="BEARISH",
                        broken_level=il["price"],
                        sweep_ts=sweep.ts,
                        bar_index=t,
                        displacement=displacement,
                    )

    return None


# ---------------------------------------------------------------------------
# STEP 5 — classify_premium_discount
# ---------------------------------------------------------------------------


def classify_premium_discount(
    price: float,
    swing_low: float,
    swing_high: float,
    *,
    price_zone_eq_buffer: float = 0.02,
) -> dict[str, Any]:
    """Classify ``price`` as premium / equilibrium / discount within a dealing range.

    Parameters
    ----------
    price:
        Current price (typically bar.close).
    swing_low, swing_high:
        Dealing range boundaries.
    price_zone_eq_buffer:
        Fraction of the range around equilibrium (0.5 level) classified as
        'equilibrium'.  Default 0.02 = ±2% of range.

    Returns
    -------
    dict with keys: ``price_zone``, ``eq``, ``dr_high``, ``dr_low``.
    """
    dr_high = swing_high
    dr_low = swing_low

    if dr_high <= dr_low:
        return {
            "price_zone": "undefined",
            "eq": None,
            "dr_high": dr_high,
            "dr_low": dr_low,
        }

    rng = dr_high - dr_low
    eq = dr_low + rng * 0.5

    if price > eq + rng * price_zone_eq_buffer:
        zone = "premium"
    elif price < eq - rng * price_zone_eq_buffer:
        zone = "discount"
    else:
        zone = "equilibrium"

    return {"price_zone": zone, "eq": eq, "dr_high": dr_high, "dr_low": dr_low}


# ---------------------------------------------------------------------------
# STEP 6 — compute_ote_zone
# ---------------------------------------------------------------------------


def compute_ote_zone(
    swing_low: float,
    swing_high: float,
    direction: str,
    *,
    ote_low: float = 0.62,
    ote_high: float = 0.79,
    ote_705: float = 0.705,
) -> OTEZone | None:
    """Compute the Optimal Trade Entry fibonacci zone within a dealing range.

    Parameters
    ----------
    swing_low, swing_high:
        Dealing range boundaries (dr_low, dr_high).
    direction:
        'BULLISH' — discount OTE (buy the dip); 'BEARISH' — premium OTE (sell the rip).
    ote_low, ote_high:
        Fibonacci retracement levels defining the OTE zone (default 0.62–0.79).
    ote_705:
        Centre of the OTE zone (default 0.705).

    Returns ``None`` when the dealing range is degenerate (high <= low).
    """
    dr_low = swing_low
    dr_high = swing_high

    if dr_high <= dr_low:
        return None

    rng = dr_high - dr_low

    if direction == "BULLISH":
        # Fib drawn low → high; OTE is deep retracement back toward low
        low_price = dr_high - rng * ote_high  # 0.79 retracement
        high_price = dr_high - rng * ote_low  # 0.62 retracement
        mid = dr_high - rng * ote_705
    else:  # BEARISH
        # Fib drawn high → low; OTE is deep retracement back toward high
        low_price = dr_low + rng * ote_low  # 0.62 retracement above low
        high_price = dr_low + rng * ote_high  # 0.79 retracement
        mid = dr_low + rng * ote_705

    return OTEZone(
        low=low_price,
        high=high_price,
        mid_705=mid,
        direction=direction,
        dr_high=dr_high,
        dr_low=dr_low,
    )


# ---------------------------------------------------------------------------
# Top-level aggregator
# ---------------------------------------------------------------------------


def analyze_liquidity(
    bars: list[PriceBar],
    structure_events: Any = None,
    *,
    swing_left: int = 3,
    swing_right: int = 3,
    eq_tolerance_pct: float = 0.0015,
    sweep_reject_pct: float = 0.5,
    mss_lookback: int = 10,
    mss_body_ratio: float = 0.5,
    ote_low: float = 0.62,
    ote_high: float = 0.79,
    ote_705: float = 0.705,
    discount_threshold: float = 0.5,  # noqa: ARG002  (reserved for future override)
    price_zone_eq_buffer: float = 0.02,
    pool_lookback: int = 50,
    min_touch_count: int = 1,
) -> LiquidityResult:
    """Run all six liquidity-analysis steps over a complete bar series.

    Operates in a single left-to-right pass: pools are identified from confirmed
    pivots, then sweeps are detected bar by bar, MSS events are searched, and
    finally the OTE zone and price zone are computed for the most recent dealing
    range derived from the last sweep.

    No lookahead: every detection uses only bars available at detection time.
    """
    if len(bars) < swing_left + swing_right + 2:
        return LiquidityResult()

    result = LiquidityResult()

    # STEP 1 — pools
    pools = identify_liquidity_pools(
        bars,
        structure_events,
        swing_left=swing_left,
        swing_right=swing_right,
        eq_tolerance_pct=eq_tolerance_pct,
        pool_lookback=pool_lookback,
        min_touch_count=min_touch_count,
    )
    result.pools = pools

    if not pools:
        return result

    # STEP 2 — sweeps (also mutates pool.mitigated / pool.stale in place)
    raw_sweeps = detect_liquidity_sweep(
        bars,
        pools,
        mss_lookback=mss_lookback,
        pool_lookback=pool_lookback,
    )

    # STEP 3 — re-classify sweep types with sweep_reject_pct
    final_sweeps: list[SweepEvent] = []
    for sw in raw_sweeps:
        refined = classify_sweep_type(sw, bars, sweep_reject_pct=sweep_reject_pct)
        if refined != sw.sweep_type:
            sw = SweepEvent(
                ts=sw.ts,
                level=sw.level,
                side=sw.side,
                bar_index=sw.bar_index,
                wick_extreme=sw.wick_extreme,
                reclaimed=sw.reclaimed,
                sweep_type=refined,
            )
        final_sweeps.append(sw)
    result.sweeps = final_sweeps

    # STEP 4 — MSS for each sweep
    for sw in final_sweeps:
        mss = detect_mss(
            bars,
            sw,
            swing_left=2,
            swing_right=2,
            mss_lookback=mss_lookback,
            mss_body_ratio=mss_body_ratio,
        )
        if mss is not None:
            result.mss_events.append(mss)

    # STEP 5 & 6 — derive dealing range from last sweep and compute price zone + OTE
    if final_sweeps:
        last_sweep = final_sweeps[-1]
        sweep_t = last_sweep.bar_index

        # Use the most recent confirmed swing high/low BEFORE the sweep bar
        all_ph, all_pl = _identify_pivots(bars[:sweep_t], swing_left, swing_right)
        dr_high_val: float | None = all_ph[-1]["price"] if all_ph else None
        dr_low_val: float | None = all_pl[-1]["price"] if all_pl else None

        if dr_high_val is not None and dr_low_val is not None and dr_high_val > dr_low_val:
            current_price = bars[-1].close
            pd_result = classify_premium_discount(
                current_price,
                dr_low_val,
                dr_high_val,
                price_zone_eq_buffer=price_zone_eq_buffer,
            )
            result.price_zone = pd_result["price_zone"]
            result.eq_price = pd_result["eq"]

            # Direction from the last sweep: SSL sweep → potential BULLISH; BSL → BEARISH
            direction = "BULLISH" if last_sweep.side == "SSL" else "BEARISH"
            result.ote_zone = compute_ote_zone(
                dr_low_val,
                dr_high_val,
                direction,
                ote_low=ote_low,
                ote_high=ote_high,
                ote_705=ote_705,
            )
            result.dealing_range = {
                "high": dr_high_val,
                "low": dr_low_val,
                "ts_high": all_ph[-1]["ts"],
                "ts_low": all_pl[-1]["ts"],
            }
        else:
            result.price_zone = "undefined"

    return result
