"""Volume-analysis detector: RVOL, OBV/ADL/CMF, VSA No-Demand/No-Supply, Climax, VDU.

Implements the ``volume_analysis`` concept from docs/CHART_READING.md §6 (lines 742–887).
All detections are lookahead-free: a detection at bar *i* uses only bars[0..i].
VSA confirmations (no_demand_confirmed, no_supply_confirmed) and pivot-based divergences are
emitted with the mandatory 1-bar delay described in the spec.

Crypto optional fields (ob_imbalance/oi_context) are ``None`` when the respective snapshot
data is not supplied.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass

from data.models import OpenInterestRecord, OrderBookSnapshot, PriceBar

# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VolumeBarResult:
    """Per-bar output for the volume-analysis concept."""

    ts: object  # date | datetime — mirrors PriceBar.ts
    symbol: str
    freq: str

    # Relative volume
    rvol: float | None
    rvol_class: str  # 'dry_up'|'normal'|'elevated'|'spike'|'climax'|'undefined'

    # VSA spread / close location
    spread_pct: float | None  # current spread / rolling avg spread
    spread_class: str | None  # 'narrow'|'medium'|'wide'
    close_loc: str  # 'upper'|'mid'|'lower'

    # OBV
    obv: float
    obv_ema: float
    obv_divergence: str | None  # 'bullish'|'bearish'|None

    # ADL
    adl: float
    adl_divergence: str | None  # 'bullish'|'bearish'|None
    adl_gap_distortion: bool

    # CMF
    cmf: float | None
    cmf_signal: str | None  # 'bullish'|'bearish'|'strong'|'neutral'|None

    # VSA No-Demand
    no_demand: bool
    no_demand_confirmed: bool  # emitted at bar i for pattern at bar i-1

    # VSA No-Supply
    no_supply: bool
    no_supply_weak: bool
    no_supply_confirmed: bool  # emitted at bar i for pattern at bar i-1

    # Effort vs Result
    evr_label: (
        str  # 'absorption'|'selling_pressure_test'|'effortless_rise'|'effortless_fall'|'neutral'
    )

    # Climax
    climax_top: bool
    climax_bottom: bool
    climax_bottom_weak: bool

    # Volume Dry-Up
    vdu_zone_end: bool

    # Crypto optional
    ob_imbalance: float | None
    ob_bias: str | None  # 'buy_pressure'|'sell_pressure'|'neutral'
    oi_context: str | None  # 'long_buildup'|'short_buildup'|'short_covering'|'long_unwind'


# ---------------------------------------------------------------------------
# Public helper functions
# ---------------------------------------------------------------------------


def compute_rvol(bars: Sequence[PriceBar], lookback: int = 20) -> list[float | None]:
    """Return per-bar RVOL (current volume / rolling mean volume).

    Returns ``None`` for bars where the rolling window is not yet full, or where
    the rolling mean is zero.
    """
    result: list[float | None] = []
    for i, bar in enumerate(bars):
        if i < lookback - 1:
            result.append(None)
            continue
        window = [bars[j].volume for j in range(i - lookback + 1, i + 1)]
        avg = statistics.mean(window)
        if avg <= 0:
            result.append(None)
        else:
            result.append(bar.volume / avg)
    return result


def classify_rvol(rvol: float | None) -> str:
    """Classify a numeric RVOL value into a labelled bucket."""
    if rvol is None:
        return "undefined"
    if rvol < 0.5:
        return "dry_up"
    if rvol < 1.5:
        return "normal"
    if rvol < 3.0:
        return "elevated"
    if rvol < 4.0:
        return "spike"
    return "climax"


def detect_climax(bar: PriceBar, rvol: float | None, spread_pct: float | None) -> str:
    """Classify a single bar as 'top', 'bottom', 'bottom_weak', or 'none'.

    Does NOT enforce the N-bar high/low requirement; that comparison requires
    neighbour bars and must be done inside the full-series detector.  This helper
    provides the per-bar VSA conditions only, so callers can combine them with the
    rolling-high/low check.
    """
    if rvol is None or spread_pct is None:
        return "none"
    if rvol < 3.0 or spread_pct <= 1.4:
        return "none"
    is_up = bar.close > bar.open
    is_down = bar.close < bar.open
    rng = bar.high - bar.low
    if rng == 0:
        close_loc = "mid"
    elif (bar.close - bar.low) / rng >= 0.7:
        close_loc = "upper"
    elif (bar.close - bar.low) / rng <= 0.3:
        close_loc = "lower"
    else:
        close_loc = "mid"

    if is_up and close_loc in ("mid", "lower"):
        return "top"
    if is_down and close_loc == "upper":
        return "bottom"
    if is_down and close_loc == "mid":
        return "bottom_weak"
    return "none"


def detect_no_supply(bars: Sequence[PriceBar]) -> list[dict[str, bool]]:
    """Return per-bar no_supply flags.

    Returns a list of dicts with keys ``no_supply``, ``no_supply_weak``,
    ``no_supply_confirmed`` (confirmed uses 1-bar lookahead, so bar i-1 is
    the pattern bar; result is emitted at bar i).
    """
    n = len(bars)
    raw: list[dict[str, bool]] = [{"no_supply": False, "no_supply_weak": False} for _ in range(n)]
    # Compute rolling avg vol for spread_pct reference (spread_lookback=14 default)
    # We need spread_pct per bar — recomputed inline here for standalone use.
    spread_lookback = 14
    avg_spread: list[float | None] = []
    for i in range(n):
        if i < spread_lookback - 1:
            avg_spread.append(None)
        else:
            window = [bars[j].high - bars[j].low for j in range(i - spread_lookback + 1, i + 1)]
            avg_spread.append(statistics.mean(window))

    for i in range(2, n):
        if avg_spread[i] is None or avg_spread[i] == 0:
            continue
        spread = bars[i].high - bars[i].low
        sp = spread / avg_spread[i]  # type: ignore[operator]
        if sp >= 0.6:
            continue  # not narrow spread
        if bars[i].close >= bars[i].open:  # down_bar required
            continue
        if bars[i].volume >= min(bars[i - 1].volume, bars[i - 2].volume):
            continue  # volume not < previous two bars
        rng = bars[i].high - bars[i].low
        if rng == 0:
            close_loc = "mid"
        elif (bars[i].close - bars[i].low) / rng >= 0.7:
            close_loc = "upper"
        elif (bars[i].close - bars[i].low) / rng <= 0.3:
            close_loc = "lower"
        else:
            close_loc = "mid"
        if close_loc == "upper":
            raw[i]["no_supply"] = True
        elif close_loc == "mid":
            raw[i]["no_supply_weak"] = True

    # Confirmation: emit at bar i+1 (retroactive, 1-bar delay)
    result: list[dict[str, bool]] = [
        {
            "no_supply": raw[i]["no_supply"],
            "no_supply_weak": raw[i]["no_supply_weak"],
            "no_supply_confirmed": False,
        }
        for i in range(n)
    ]
    for i in range(n - 1):
        if raw[i]["no_supply"] and bars[i + 1].close > bars[i].close:
            result[i + 1]["no_supply_confirmed"] = True
    return result


def detect_no_demand(bars: Sequence[PriceBar]) -> list[dict[str, bool]]:
    """Return per-bar no_demand flags.

    ``no_demand_confirmed`` is emitted at bar i for a pattern at bar i-1.
    """
    n = len(bars)
    spread_lookback = 14
    avg_spread: list[float | None] = []
    for i in range(n):
        if i < spread_lookback - 1:
            avg_spread.append(None)
        else:
            window = [bars[j].high - bars[j].low for j in range(i - spread_lookback + 1, i + 1)]
            avg_spread.append(statistics.mean(window))

    raw: list[bool] = [False] * n
    for i in range(2, n):
        if avg_spread[i] is None or avg_spread[i] == 0:
            continue
        spread = bars[i].high - bars[i].low
        sp = spread / avg_spread[i]  # type: ignore[operator]
        if sp >= 0.6:
            continue
        if bars[i].close <= bars[i].open:  # up_bar required
            continue
        if bars[i].volume >= min(bars[i - 1].volume, bars[i - 2].volume):
            continue
        rng = bars[i].high - bars[i].low
        if rng == 0:
            close_loc = "mid"
        elif (bars[i].close - bars[i].low) / rng >= 0.7:
            close_loc = "upper"
        elif (bars[i].close - bars[i].low) / rng <= 0.3:
            close_loc = "lower"
        else:
            close_loc = "mid"
        if close_loc in ("mid", "lower"):
            raw[i] = True

    result: list[dict[str, bool]] = [
        {"no_demand": raw[i], "no_demand_confirmed": False} for i in range(n)
    ]
    for i in range(n - 1):
        if raw[i] and bars[i + 1].close < bars[i].close:
            result[i + 1]["no_demand_confirmed"] = True
    return result


def detect_vdu(
    bars: Sequence[PriceBar],
    *,
    rvol_period: int = 20,
    vdu_bars: int = 5,
    vdu_vol_threshold: float = 0.50,
) -> list[bool]:
    """Return per-bar ``vdu_zone_end`` flags."""
    n = len(bars)
    rvol_vals = compute_rvol(bars, lookback=rvol_period)
    result = [False] * n
    for i in range(vdu_bars - 1, n):
        window_start = i - vdu_bars + 1
        # (a) all bars volume < avg_vol * threshold
        ok = True
        for j in range(window_start, i + 1):
            rv = rvol_vals[j]
            if rv is None or rv >= vdu_vol_threshold:
                ok = False
                break
        if not ok:
            continue
        # (b) at least vdu_bars-1 bars have decreasing volume
        decreasing = 0
        for j in range(window_start + 1, i + 1):
            if bars[j].volume <= bars[j - 1].volume:
                decreasing += 1
        if decreasing < vdu_bars - 1:
            continue
        # (c) price range contraction: max spread in latter half < max spread in first half
        mid = window_start + vdu_bars // 2
        first_half_spreads = [bars[j].high - bars[j].low for j in range(window_start, mid)]
        latter_half_spreads = [bars[j].high - bars[j].low for j in range(mid, i + 1)]
        if not first_half_spreads or not latter_half_spreads:
            continue
        if max(latter_half_spreads) >= max(first_half_spreads):
            continue
        # (d) last bar rvol < 0.5
        if rvol_vals[i] is None or rvol_vals[i] >= 0.5:  # type: ignore[operator]
            continue
        result[i] = True
    return result


def compute_obv(bars: Sequence[PriceBar]) -> list[float]:
    """Compute On-Balance Volume for the full series."""
    n = len(bars)
    if n == 0:
        return []
    obv: list[float] = [0.0] * n
    obv[0] = bars[0].volume
    for i in range(1, n):
        if bars[i].close > bars[i - 1].close:
            obv[i] = obv[i - 1] + bars[i].volume
        elif bars[i].close < bars[i - 1].close:
            obv[i] = obv[i - 1] - bars[i].volume
        else:
            obv[i] = obv[i - 1]
    return obv


def _compute_obv_ema(obv: list[float], period: int) -> list[float]:
    """EMA-smooth an OBV series (spec §6 step 5 primary formula).

    Strictly causal: ``obv_ema[i]`` depends only on ``obv[0..i]``.  We deliberately
    use the recursive seed ``ema[0] = obv[0]`` rather than back-filling the first
    ``period`` bars with the SMA of ``obv[:period]``.  The SMA-seed variant is a
    *lookahead leak* — for any warm-up bar ``i < period`` it would inject future OBV
    values (``obv[i+1 .. period-1]``) into the emitted ``obv_ema[i]``, so the value
    of an early bar would change once enough future bars arrived.
    """
    n = len(obv)
    if n == 0:
        return []
    ema: list[float] = [0.0] * n
    k = 2.0 / (period + 1)
    ema[0] = obv[0]
    for i in range(1, n):
        ema[i] = obv[i] * k + ema[i - 1] * (1 - k)
    return ema


def detect_obv_divergence(
    bars: Sequence[PriceBar],
    obv: list[float],
    *,
    obv_ema_period: int = 20,
    divergence_lookback: int = 30,
) -> list[str | None]:
    """Return per-bar OBV divergence signals ('bullish'|'bearish'|None).

    Strictly lookahead-free: pivot at index p confirmed only when bar p+1 is known.
    At bar i we only use pivots confirmed up to bar i-1.
    """
    n = len(bars)
    obv_ema = _compute_obv_ema(obv, obv_ema_period)
    result: list[str | None] = [None] * n
    for i in range(n):
        # Collect confirmed pivots within [i-divergence_lookback, i-1]
        # A pivot at p is confirmed when p+1 is known, so p+1 <= i-1 => p <= i-2
        window_start = max(1, i - divergence_lookback)
        # We need at least p-1 as well, so start from window_start+1
        high_pivots: list[int] = []
        low_pivots: list[int] = []
        for p in range(window_start + 1, i - 1):
            # pivot high: close[p] > close[p-1] AND close[p] > close[p+1]
            if bars[p].close > bars[p - 1].close and bars[p].close > bars[p + 1].close:
                high_pivots.append(p)
            # pivot low: close[p] < close[p-1] AND close[p] < close[p+1]
            if bars[p].close < bars[p - 1].close and bars[p].close < bars[p + 1].close:
                low_pivots.append(p)

        if len(high_pivots) >= 2:
            p1, p2 = high_pivots[-2], high_pivots[-1]
            if bars[p2].close > bars[p1].close and obv_ema[p2] < obv_ema[p1]:
                result[i] = "bearish"
                continue

        if len(low_pivots) >= 2:
            p1, p2 = low_pivots[-2], low_pivots[-1]
            if bars[p2].close < bars[p1].close and obv_ema[p2] > obv_ema[p1]:
                result[i] = "bullish"
    return result


def compute_cmf(bars: Sequence[PriceBar], period: int = 21) -> list[float | None]:
    """Compute Chaikin Money Flow for the full series."""
    n = len(bars)
    # First compute MFV per bar
    mfv: list[float] = []
    for bar in bars:
        rng = bar.high - bar.low
        if rng == 0:
            mfv.append(0.0)
        else:
            clv = ((bar.close - bar.low) - (bar.high - bar.close)) / rng
            mfv.append(clv * bar.volume)

    result: list[float | None] = [None] * n
    for i in range(period - 1, n):
        window_vol = sum(bars[j].volume for j in range(i - period + 1, i + 1))
        if window_vol <= 0:
            continue
        window_mfv = sum(mfv[j] for j in range(i - period + 1, i + 1))
        result[i] = window_mfv / window_vol
    return result


def compute_evr(
    bars: Sequence[PriceBar],
    *,
    rvol_period: int = 20,
    spread_lookback: int = 14,
) -> list[str]:
    """Return per-bar Effort vs Result labels."""
    n = len(bars)
    rvol_vals = compute_rvol(bars, lookback=rvol_period)
    avg_spread: list[float | None] = []
    for i in range(n):
        if i < spread_lookback - 1:
            avg_spread.append(None)
        else:
            window = [bars[j].high - bars[j].low for j in range(i - spread_lookback + 1, i + 1)]
            avg_spread.append(statistics.mean(window))

    result: list[str] = []
    for i, bar in enumerate(bars):
        rv = rvol_vals[i]
        avg_sp = avg_spread[i]
        if rv is None or avg_sp is None or avg_sp == 0:
            result.append("neutral")
            continue
        spread = bar.high - bar.low
        spread_ratio = spread / avg_sp
        is_up = bar.close > bar.open
        if rv >= 2.0 and spread_ratio < 0.7:
            result.append("absorption" if is_up else "selling_pressure_test")
        elif rv < 0.7 and spread_ratio > 1.4:
            result.append("effortless_rise" if is_up else "effortless_fall")
        else:
            result.append("neutral")
    return result


# ---------------------------------------------------------------------------
# Internals used by the aggregator
# ---------------------------------------------------------------------------


def _compute_adl(bars: Sequence[PriceBar]) -> list[float]:
    n = len(bars)
    adl: list[float] = [0.0] * n
    mfv_0 = _bar_mfv(bars[0])
    adl[0] = mfv_0
    for i in range(1, n):
        adl[i] = adl[i - 1] + _bar_mfv(bars[i])
    return adl


def _bar_mfv(bar: PriceBar) -> float:
    rng = bar.high - bar.low
    if rng == 0:
        return 0.0
    clv = ((bar.close - bar.low) - (bar.high - bar.close)) / rng
    return clv * bar.volume


def _detect_adl_divergence(
    bars: Sequence[PriceBar],
    adl: list[float],
    divergence_lookback: int = 30,
) -> list[str | None]:
    n = len(bars)
    result: list[str | None] = [None] * n
    for i in range(n):
        window_start = max(1, i - divergence_lookback)
        high_pivots: list[int] = []
        low_pivots: list[int] = []
        for p in range(window_start + 1, i - 1):
            if bars[p].close > bars[p - 1].close and bars[p].close > bars[p + 1].close:
                high_pivots.append(p)
            if bars[p].close < bars[p - 1].close and bars[p].close < bars[p + 1].close:
                low_pivots.append(p)
        if len(high_pivots) >= 2:
            p1, p2 = high_pivots[-2], high_pivots[-1]
            if bars[p2].close > bars[p1].close and adl[p2] < adl[p1]:
                result[i] = "bearish"
                continue
        if len(low_pivots) >= 2:
            p1, p2 = low_pivots[-2], low_pivots[-1]
            if bars[p2].close < bars[p1].close and adl[p2] > adl[p1]:
                result[i] = "bullish"
    return result


def _close_loc(bar: PriceBar) -> str:
    rng = bar.high - bar.low
    if rng == 0:
        return "mid"
    ratio = (bar.close - bar.low) / rng
    if ratio >= 0.7:
        return "upper"
    if ratio <= 0.3:
        return "lower"
    return "mid"


def _spread_class(sp: float) -> str:
    if sp < 0.6:
        return "narrow"
    if sp <= 1.5:
        return "medium"
    return "wide"


def _cmf_signal(cmf_val: float | None, bull: float, bear: float) -> str | None:
    if cmf_val is None:
        return None
    if abs(cmf_val) > 0.25:
        return "strong"
    if cmf_val > bull:
        return "bullish"
    if cmf_val < -bear:
        return "bearish"
    return "neutral"


def _ob_imbalance(
    snapshot: OrderBookSnapshot | None,
    depth: int,
) -> tuple[float | None, str | None]:
    if snapshot is None:
        return None, None
    bids = snapshot.bids[:depth]
    asks = snapshot.asks[:depth]
    bid_vol = sum(lvl.size for lvl in bids)
    ask_vol = sum(lvl.size for lvl in asks)
    total = bid_vol + ask_vol
    if total == 0:
        return 0.0, "neutral"
    imb = (bid_vol - ask_vol) / total
    return imb, None  # bias determined per-threshold in aggregator


def _oi_context(
    bar: PriceBar,
    prev_bar: PriceBar | None,
    oi_current: OpenInterestRecord | None,
    oi_prev: OpenInterestRecord | None,
) -> str | None:
    if oi_current is None or oi_prev is None or prev_bar is None:
        return None
    oi_delta = oi_current.open_interest_amount - oi_prev.open_interest_amount
    price_up = bar.close > prev_bar.close
    oi_up = oi_delta > 0
    if oi_up and price_up:
        return "long_buildup"
    if oi_up and not price_up:
        return "short_buildup"
    if not oi_up and price_up:
        return "short_covering"
    return "long_unwind"


# ---------------------------------------------------------------------------
# Top-level aggregator
# ---------------------------------------------------------------------------

_MIN_AVG_VOLUME_GUARD = 1_000.0


def analyse_volume(
    bars: Sequence[PriceBar],
    *,
    rvol_period: int = 20,
    obv_ema_period: int = 20,
    cmf_period: int = 21,
    cmf_bull_threshold: float = 0.05,
    cmf_bear_threshold: float = 0.05,
    spread_lookback: int = 14,
    climax_rvol_threshold: float = 3.0,
    climax_lookback: int = 20,
    vdu_bars: int = 5,
    vdu_vol_threshold: float = 0.50,
    divergence_lookback: int = 30,
    ob_depth_levels: int = 10,
    ob_imbalance_threshold: float = 0.20,
    # Optional crypto data aligned to bar indices (same length as bars or None)
    order_book_snapshots: Sequence[OrderBookSnapshot | None] | None = None,
    oi_records: Sequence[OpenInterestRecord | None] | None = None,
) -> list[VolumeBarResult]:
    """Run the full volume-analysis concept over *bars* and return one result per bar.

    All parameters default to the spec-canonical values from docs/CHART_READING.md §6.
    ``order_book_snapshots`` and ``oi_records``, when provided, must have the same length
    as ``bars``; entry ``i`` corresponds to bar ``i``.
    """
    n = len(bars)
    if n == 0:
        return []

    # --- Pre-compute rolling avg volume and avg spread ---
    avg_vol: list[float | None] = []
    for i in range(n):
        if i < rvol_period - 1:
            avg_vol.append(None)
        else:
            window = [bars[j].volume for j in range(i - rvol_period + 1, i + 1)]
            avg_vol.append(statistics.mean(window))

    avg_spread_ser: list[float | None] = []
    for i in range(n):
        if i < spread_lookback - 1:
            avg_spread_ser.append(None)
        else:
            window = [bars[j].high - bars[j].low for j in range(i - spread_lookback + 1, i + 1)]
            avg_spread_ser.append(statistics.mean(window))

    # --- Per-bar RVOL ---
    rvol_vals: list[float | None] = []
    for i in range(n):
        av = avg_vol[i]
        if av is None or av <= 0:
            rvol_vals.append(None)
        else:
            rvol_vals.append(bars[i].volume / av)

    # --- OBV + EMA ---
    obv_vals = compute_obv(bars)
    obv_ema_vals = _compute_obv_ema(obv_vals, obv_ema_period)

    # --- ADL ---
    adl_vals = _compute_adl(bars)

    # --- MFV series (for CMF) ---
    mfv: list[float] = [_bar_mfv(b) for b in bars]

    # --- CMF ---
    cmf_vals: list[float | None] = [None] * n
    for i in range(cmf_period - 1, n):
        wv = sum(bars[j].volume for j in range(i - cmf_period + 1, i + 1))
        if wv > 0:
            wmfv = sum(mfv[j] for j in range(i - cmf_period + 1, i + 1))
            cmf_vals[i] = wmfv / wv

    # --- No-Supply / No-Demand raw detection ---
    nd_flags = detect_no_demand(bars)
    ns_flags = detect_no_supply(bars)

    # --- EVR labels ---
    evr_labels = compute_evr(bars, rvol_period=rvol_period, spread_lookback=spread_lookback)

    # --- OBV divergence ---
    obv_div = detect_obv_divergence(
        bars, obv_vals, obv_ema_period=obv_ema_period, divergence_lookback=divergence_lookback
    )

    # --- ADL divergence ---
    adl_div = _detect_adl_divergence(bars, adl_vals, divergence_lookback=divergence_lookback)

    # --- VDU ---
    vdu_flags = detect_vdu(
        bars, rvol_period=rvol_period, vdu_bars=vdu_bars, vdu_vol_threshold=vdu_vol_threshold
    )

    # --- Assemble per-bar results ---
    results: list[VolumeBarResult] = []
    for i, bar in enumerate(bars):
        rv = rvol_vals[i]
        av = avg_vol[i]
        avs = avg_spread_ser[i]

        # Liquidity guard: low avg volume → rvol undefined
        if av is not None and av < _MIN_AVG_VOLUME_GUARD:
            rv = None
            rv_class = "undefined"
        else:
            rv_class = classify_rvol(rv)

        # Spread
        spread = bar.high - bar.low
        if avs is not None and avs > 0:
            sp_pct = spread / avs
            sp_class: str | None = _spread_class(sp_pct)
        else:
            sp_pct = None
            sp_class = None

        close_loc = _close_loc(bar)

        # CMF signal
        cmf_sig = _cmf_signal(cmf_vals[i], cmf_bull_threshold, cmf_bear_threshold)

        # ADL gap distortion guard
        adl_gap = False
        if i > 0 and bars[i - 1].close > 0:
            gap_ratio = abs(bar.open - bars[i - 1].close) / bars[i - 1].close
            adl_gap = gap_ratio > 0.01

        # Climax detection with gap-open guard
        climax_top = False
        climax_bottom = False
        climax_bottom_weak = False
        if (
            rv is not None
            and rv >= climax_rvol_threshold
            and sp_pct is not None
            and sp_pct > 1.4
            and i >= climax_lookback
        ):
            # Gap-open guard: skip if open gaps > 3% from prev close
            gap_flag = False
            if i > 0 and bars[i - 1].close > 0:
                gap_flag = abs(bar.open - bars[i - 1].close) / bars[i - 1].close > 0.03
            if not gap_flag:
                prev_closes = [bars[j].close for j in range(i - climax_lookback, i)]
                is_up = bar.close > bar.open
                is_down = bar.close < bar.open
                if is_up and close_loc in ("mid", "lower") and bar.close > max(prev_closes):
                    climax_top = True
                if is_down and bar.close < min(prev_closes):
                    if close_loc == "upper":
                        climax_bottom = True
                    elif close_loc == "mid":
                        climax_bottom_weak = True

        # Order book (optional)
        ob_snap = order_book_snapshots[i] if order_book_snapshots is not None else None
        ob_imb_val, _ = _ob_imbalance(ob_snap, ob_depth_levels)
        ob_bias_val: str | None = None
        if ob_imb_val is not None:
            if ob_imb_val > ob_imbalance_threshold:
                ob_bias_val = "buy_pressure"
            elif ob_imb_val < -ob_imbalance_threshold:
                ob_bias_val = "sell_pressure"
            else:
                ob_bias_val = "neutral"

        # OI context (optional)
        oi_rec = oi_records[i] if oi_records is not None else None
        oi_prev = oi_records[i - 1] if (oi_records is not None and i > 0) else None
        prev_bar = bars[i - 1] if i > 0 else None
        oi_ctx = _oi_context(bar, prev_bar, oi_rec, oi_prev)

        results.append(
            VolumeBarResult(
                ts=bar.ts,
                symbol=bar.symbol,
                freq=bar.freq,
                rvol=rv,
                rvol_class=rv_class,
                spread_pct=sp_pct,
                spread_class=sp_class,
                close_loc=close_loc,
                obv=obv_vals[i],
                obv_ema=obv_ema_vals[i],
                obv_divergence=obv_div[i],
                adl=adl_vals[i],
                adl_divergence=adl_div[i],
                adl_gap_distortion=adl_gap,
                cmf=cmf_vals[i],
                cmf_signal=cmf_sig,
                no_demand=nd_flags[i]["no_demand"],
                no_demand_confirmed=nd_flags[i]["no_demand_confirmed"],
                no_supply=ns_flags[i]["no_supply"],
                no_supply_weak=ns_flags[i]["no_supply_weak"],
                no_supply_confirmed=ns_flags[i]["no_supply_confirmed"],
                evr_label=evr_labels[i],
                climax_top=climax_top,
                climax_bottom=climax_bottom,
                climax_bottom_weak=climax_bottom_weak,
                vdu_zone_end=vdu_flags[i],
                ob_imbalance=ob_imb_val,
                ob_bias=ob_bias_val,
                oi_context=oi_ctx,
            )
        )
    return results
