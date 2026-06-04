"""Volume Profile detector —매물대(볼륨프로파일) analysis.

Implements:
  - build_volume_profile   : construct a binned volume histogram over a bar window
  - classify_profile_shape : D / P / b / B shape classification
  - find_poc               : Point of Control (highest-volume bin mid-price)
  - find_value_area        : CME single-row Value Area (VAH/VAL)
  - find_naked_poc         : filter unmitigated prior-session POCs
  - classify_lvn_hvn       : High/Low Volume Node clustering

Spec: docs/CHART_READING.md §5 "매물대 (볼륨프로파일)".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from data.models import PriceBar

# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VolumeNode:
    """A contiguous cluster of bins classified as HVN or LVN."""

    node_low: float
    node_high: float
    node_vol: float
    node_mid: float
    label: str  # 'HVN' | 'LVN'


@dataclass(frozen=True)
class ValueArea:
    """Value Area boundaries from CME single-row expansion."""

    vah: float  # Value Area High  = bin_high[va_hi_idx]
    val: float  # Value Area Low   = bin_low[va_lo_idx]
    va_pct_actual: float  # Actual fraction of total_vol enclosed (may exceed va_pct)
    va_hi_idx: int
    va_lo_idx: int


@dataclass
class VolumeProfile:
    """Full result of one build_volume_profile() call.

    Fields follow docs/CHART_READING.md §5 "출력 필드" verbatim.
    """

    ts: date | datetime  # last bar's ts
    poc_price: float
    vah: float
    val: float
    va_pct: float  # parameter used (e.g. 0.70)
    va_pct_actual: float  # actual fraction enclosed by the discrete bins
    shape: str  # 'D' | 'P' | 'b' | 'B'
    poc_rel: float  # normalised POC position [0, 1]
    hvn_nodes: list[VolumeNode] = field(default_factory=list)
    lvn_nodes: list[VolumeNode] = field(default_factory=list)
    naked_pocs: list[tuple[float, date | datetime]] = field(default_factory=list)
    total_vol: float = 0.0
    bin_size: float = 0.0
    vol_bins: list[float] = field(default_factory=list)
    global_low: float = 0.0
    global_high: float = 0.0
    # Degenerate / guard flags
    degenerate: bool = False  # True when uniform-dist or thin-profile guard fires
    single_spike: bool = False  # True when max(vol_bins)/total_vol > 0.60


# ---------------------------------------------------------------------------
# Core building block
# ---------------------------------------------------------------------------


def build_volume_profile(
    bars: list[PriceBar],
    *,
    num_bins: int = 50,
    value_area_pct: float = 0.70,
    hvn_threshold: float = 80.0,
    lvn_threshold: float = 20.0,
    bimodal_peak_threshold: float = 0.50,
    poc_shape_upper_threshold: float = 0.60,
    poc_shape_lower_threshold: float = 0.40,
    # NPOC state is managed externally; pass existing list for cross-session tracking
    naked_poc_state: list[tuple[float, date | datetime]] | None = None,
    lookback_sessions: int = 20,
) -> VolumeProfile:
    """Build a VolumeProfile from *bars* (must be chronologically ascending, no lookahead).

    Parameters follow docs/CHART_READING.md §5 "파라미터" table.  Only bars that have
    already closed (i.e. all bars passed in) are used — callers must not pass future bars.
    """
    if not bars:
        raise ValueError("bars must not be empty")

    # ------------------------------------------------------------------
    # Step 1: window collection
    # ------------------------------------------------------------------
    global_low = min(b.low for b in bars)
    global_high = max(b.high for b in bars)
    last_ts = bars[-1].ts
    total_vol_raw = sum(b.volume for b in bars)

    # ------------------------------------------------------------------
    # Step 2: price bin definition
    # ------------------------------------------------------------------
    price_range = global_high - global_low

    if price_range == 0.0:
        # Degenerate: single-price session
        vol_bins: list[float] = [total_vol_raw]
        bin_size = 0.0
        bin_lows = [global_low]
        bin_highs = [global_low]
        poc_price = global_low
        poc_idx = 0
        total_vol = total_vol_raw
        poc_rel = 0.0
        va = ValueArea(vah=global_high, val=global_low, va_pct_actual=1.0, va_hi_idx=0, va_lo_idx=0)
        hvn_nodes: list[VolumeNode] = []
        lvn_nodes: list[VolumeNode] = []
        shape = "D"
        degenerate = True
        single_spike = False
        naked_pocs = _mitigate_npocs(
            naked_poc_state or [], bars, lookback_sessions=lookback_sessions
        )
        return VolumeProfile(
            ts=last_ts,
            poc_price=poc_price,
            vah=va.vah,
            val=va.val,
            va_pct=value_area_pct,
            va_pct_actual=va.va_pct_actual,
            shape=shape,
            poc_rel=poc_rel,
            hvn_nodes=hvn_nodes,
            lvn_nodes=lvn_nodes,
            naked_pocs=naked_pocs,
            total_vol=total_vol,
            bin_size=bin_size,
            vol_bins=vol_bins,
            global_low=global_low,
            global_high=global_high,
            degenerate=degenerate,
            single_spike=single_spike,
        )

    bin_size = price_range / num_bins
    bin_lows = [global_low + i * bin_size for i in range(num_bins)]
    bin_highs = [bin_lows[i] + bin_size for i in range(num_bins)]
    # Fix floating-point drift on last bin
    bin_highs[num_bins - 1] = global_high
    vol_bins = [0.0] * num_bins

    # ------------------------------------------------------------------
    # Step 3: distribute each bar's volume across overlapping bins
    # ------------------------------------------------------------------
    for b in bars:
        span = b.high - b.low
        if span == 0.0:
            # Zero-range bar: all volume goes to the bin containing close
            idx = _bin_for_price(b.close, global_low, bin_size, num_bins)
            vol_bins[idx] += b.volume
        else:
            for i in range(num_bins):
                overlap = min(b.high, bin_highs[i]) - max(b.low, bin_lows[i])
                if overlap > 0.0:
                    vol_bins[i] += b.volume * (overlap / span)

    total_vol = sum(vol_bins)

    # ------------------------------------------------------------------
    # Step 4: POC
    # ------------------------------------------------------------------
    poc_idx = _argmax(vol_bins)
    poc_price = bin_lows[poc_idx] + bin_size * 0.5
    session_max_vol = max(vol_bins)

    # ------------------------------------------------------------------
    # Step 5: Value Area (CME single-row expansion)
    # ------------------------------------------------------------------
    va = _compute_value_area(
        vol_bins=vol_bins,
        bin_lows=bin_lows,
        bin_highs=bin_highs,
        poc_idx=poc_idx,
        total_vol=total_vol,
        value_area_pct=value_area_pct,
    )

    # ------------------------------------------------------------------
    # Guard flags (computed before Step 6 because the degenerate uniform-
    # distribution guard suppresses HVN/LVN labelling per spec line 702:
    # "HVN/LVN/형태 신호 모두 억제").
    # ------------------------------------------------------------------
    single_spike = (session_max_vol / total_vol > 0.60) if total_vol > 0.0 else False
    # Degenerate uniform distribution: spread < 10% of max
    degenerate = False
    if session_max_vol > 0.0:
        vol_spread_ratio = (session_max_vol - min(vol_bins)) / session_max_vol
        if vol_spread_ratio < 0.10:
            degenerate = True

    # ------------------------------------------------------------------
    # Step 6: HVN / LVN clustering
    # Spec "퇴화 균일분포 가드" (line 702): when the distribution is near-uniform
    # there are no structurally meaningful nodes — suppress HVN/LVN entirely.
    # ------------------------------------------------------------------
    if degenerate:
        hvn_nodes, lvn_nodes = [], []
    else:
        hvn_nodes, lvn_nodes = _classify_nodes(
            vol_bins=vol_bins,
            bin_lows=bin_lows,
            bin_highs=bin_highs,
            session_max_vol=session_max_vol,
            hvn_threshold=hvn_threshold,
            lvn_threshold=lvn_threshold,
        )

    # ------------------------------------------------------------------
    # Step 7: shape classification
    # ------------------------------------------------------------------
    poc_rel = (poc_price - global_low) / price_range
    shape = _classify_shape(
        bars=bars,
        vol_bins=vol_bins,
        poc_idx=poc_idx,
        poc_price=poc_price,
        poc_rel=poc_rel,
        va=va,
        global_low=global_low,
        global_high=global_high,
        price_range=price_range,
        session_max_vol=session_max_vol,
        total_vol=total_vol,
        lvn_threshold=lvn_threshold,
        bimodal_peak_threshold=bimodal_peak_threshold,
        poc_shape_upper_threshold=poc_shape_upper_threshold,
        poc_shape_lower_threshold=poc_shape_lower_threshold,
        degenerate=degenerate,
    )

    # ------------------------------------------------------------------
    # Step 8: NPOC mitigation
    # ------------------------------------------------------------------
    naked_pocs = _mitigate_npocs(naked_poc_state or [], bars, lookback_sessions=lookback_sessions)

    return VolumeProfile(
        ts=last_ts,
        poc_price=poc_price,
        vah=va.vah,
        val=va.val,
        va_pct=value_area_pct,
        va_pct_actual=va.va_pct_actual,
        shape=shape,
        poc_rel=poc_rel,
        hvn_nodes=hvn_nodes,
        lvn_nodes=lvn_nodes,
        naked_pocs=naked_pocs,
        total_vol=total_vol,
        bin_size=bin_size,
        vol_bins=vol_bins,
        global_low=global_low,
        global_high=global_high,
        degenerate=degenerate,
        single_spike=single_spike,
    )


# ---------------------------------------------------------------------------
# Public aggregator / named helpers
# ---------------------------------------------------------------------------


def classify_profile_shape(vp: VolumeProfile) -> str:
    """Return the shape string already stored in *vp* (re-exposes for callers)."""
    return vp.shape


def find_poc(vp: VolumeProfile) -> float:
    """Return the Point of Control price from a pre-built VolumeProfile."""
    return vp.poc_price


def find_value_area(
    vp: VolumeProfile,
    pct: float = 0.70,
) -> ValueArea:
    """Recompute (or return) the Value Area at *pct* coverage.

    If *pct* matches *vp.va_pct* the stored boundary is returned directly.
    Otherwise the CME expansion is re-run on *vp.vol_bins*.
    """
    if abs(pct - vp.va_pct) < 1e-9:
        # Already computed at this pct — reconstruct ValueArea from stored fields
        bin_size = vp.bin_size
        num_bins = len(vp.vol_bins)
        bin_lows = [vp.global_low + i * bin_size for i in range(num_bins)]
        bin_highs = [bl + bin_size for bl in bin_lows]
        if num_bins > 0:
            bin_highs[-1] = vp.global_high
        poc_idx = _argmax(vp.vol_bins)
        return _compute_value_area(
            vol_bins=vp.vol_bins,
            bin_lows=bin_lows,
            bin_highs=bin_highs,
            poc_idx=poc_idx,
            total_vol=vp.total_vol,
            value_area_pct=pct,
        )
    # Recompute at new pct
    bin_size = vp.bin_size
    num_bins = len(vp.vol_bins)
    bin_lows = [vp.global_low + i * bin_size for i in range(num_bins)]
    bin_highs = [bl + bin_size for bl in bin_lows]
    if num_bins > 0:
        bin_highs[-1] = vp.global_high
    poc_idx = _argmax(vp.vol_bins)
    return _compute_value_area(
        vol_bins=vp.vol_bins,
        bin_lows=bin_lows,
        bin_highs=bin_highs,
        poc_idx=poc_idx,
        total_vol=vp.total_vol,
        value_area_pct=pct,
    )


def find_naked_poc(
    vp: VolumeProfile,
    current_price: float,
) -> list[float]:
    """Return prices of unmitigated naked POCs from *vp.naked_pocs*."""
    return [price for price, _ in vp.naked_pocs]


def classify_lvn_hvn(vp: VolumeProfile) -> list[VolumeNode]:
    """Return all HVN and LVN nodes from a pre-built VolumeProfile."""
    return vp.hvn_nodes + vp.lvn_nodes


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _bin_for_price(price: float, global_low: float, bin_size: float, num_bins: int) -> int:
    """Return the bin index for a price; clamp to [0, num_bins-1]."""
    if bin_size <= 0.0:
        return 0
    idx = int((price - global_low) / bin_size)
    return max(0, min(num_bins - 1, idx))


def _argmax(seq: list[float]) -> int:
    """Return index of maximum value; lowest index on ties (spec: conservative)."""
    best_idx = 0
    best_val = seq[0]
    for i in range(1, len(seq)):
        if seq[i] > best_val:
            best_val = seq[i]
            best_idx = i
    return best_idx


def _compute_value_area(
    *,
    vol_bins: list[float],
    bin_lows: list[float],
    bin_highs: list[float],
    poc_idx: int,
    total_vol: float,
    value_area_pct: float,
) -> ValueArea:
    """CME single-row expansion to find VAH and VAL.

    Spec: docs/CHART_READING.md §5 step 5 — compare one bin above vs one bin below,
    expand toward the larger, ties go upward (Chicago convention).
    """
    n = len(vol_bins)
    if n == 0 or total_vol <= 0.0:
        lo = bin_lows[0] if bin_lows else 0.0
        hi = bin_highs[0] if bin_highs else 0.0
        return ValueArea(vah=hi, val=lo, va_pct_actual=1.0, va_hi_idx=0, va_lo_idx=0)

    target_vol = total_vol * value_area_pct
    accumulated = vol_bins[poc_idx]
    va_lo_idx = poc_idx
    va_hi_idx = poc_idx

    while accumulated < target_vol:
        above_vol = vol_bins[va_hi_idx + 1] if va_hi_idx + 1 < n else -1.0
        below_vol = vol_bins[va_lo_idx - 1] if va_lo_idx - 1 >= 0 else -1.0

        if above_vol < 0.0 and below_vol < 0.0:
            # All bins exhausted
            break

        if above_vol >= below_vol and above_vol >= 0.0:
            va_hi_idx += 1
            accumulated += above_vol
        elif below_vol > above_vol and below_vol >= 0.0:
            va_lo_idx -= 1
            accumulated += below_vol
        else:
            break

    vah = bin_highs[va_hi_idx]
    val = bin_lows[va_lo_idx]
    va_pct_actual = accumulated / total_vol if total_vol > 0.0 else 0.0

    return ValueArea(
        vah=vah,
        val=val,
        va_pct_actual=va_pct_actual,
        va_hi_idx=va_hi_idx,
        va_lo_idx=va_lo_idx,
    )


def _classify_nodes(
    *,
    vol_bins: list[float],
    bin_lows: list[float],
    bin_highs: list[float],
    session_max_vol: float,
    hvn_threshold: float,
    lvn_threshold: float,
) -> tuple[list[VolumeNode], list[VolumeNode]]:
    """Cluster consecutive same-label bins into HVN / LVN nodes."""
    if session_max_vol <= 0.0:
        return [], []

    labels: list[str | None] = []
    for v in vol_bins:
        pct = v / session_max_vol * 100.0
        if pct >= hvn_threshold:
            labels.append("HVN")
        elif pct <= lvn_threshold:
            labels.append("LVN")
        else:
            labels.append(None)

    hvn_nodes: list[VolumeNode] = []
    lvn_nodes: list[VolumeNode] = []

    i = 0
    n = len(labels)
    while i < n:
        lbl = labels[i]
        if lbl is None:
            i += 1
            continue
        j = i
        cluster_vol = 0.0
        while j < n and labels[j] == lbl:
            cluster_vol += vol_bins[j]
            j += 1
        node_low = bin_lows[i]
        node_high = bin_highs[j - 1]
        node_mid = (node_low + node_high) / 2.0
        node = VolumeNode(
            node_low=node_low,
            node_high=node_high,
            node_vol=cluster_vol,
            node_mid=node_mid,
            label=lbl,
        )
        if lbl == "HVN":
            hvn_nodes.append(node)
        else:
            lvn_nodes.append(node)
        i = j

    return hvn_nodes, lvn_nodes


def _classify_shape(
    *,
    bars: list[PriceBar],
    vol_bins: list[float],
    poc_idx: int,
    poc_price: float,
    poc_rel: float,
    va: ValueArea,
    global_low: float,
    global_high: float,
    price_range: float,
    session_max_vol: float,
    total_vol: float,
    lvn_threshold: float,
    bimodal_peak_threshold: float,
    poc_shape_upper_threshold: float,
    poc_shape_lower_threshold: float,
    degenerate: bool,
) -> str:
    """Step 7: classify profile shape as D / P / b / B (in spec priority order)."""
    # Insufficient data guard
    if len(bars) < 10:
        return "D"

    # Degenerate uniform-distribution guard — suppress shape signals
    if degenerate:
        return "D"

    last_close = bars[-1].close
    va_center = (va.vah + va.val) / 2.0
    va_center_rel = (va_center - global_low) / price_range if price_range > 0.0 else 0.5
    close_rel = (last_close - global_low) / price_range if price_range > 0.0 else 0.5

    # (1) B-shape: check first to avoid misclassification as P/b/D
    n = len(vol_bins)
    # Plateau-aware structural-peak detection.  A real volume peak can straddle
    # two (or more) adjacent equal-volume bins; a naive "strictly greater than
    # BOTH neighbors" test silently misses such flat-topped modes, which lets a
    # genuinely multimodal profile masquerade as bimodal.  We collapse each
    # contiguous run of equal maxima into a single peak (its mid index) and treat
    # a bin as a peak when it is >= each side's *outer* descending neighbour.
    peaks: list[int] = []
    i = 0
    while i < n:
        v = vol_bins[i]
        if v / session_max_vol < bimodal_peak_threshold:
            i += 1
            continue
        # Extend across a flat plateau of equal volume.
        j = i
        while j + 1 < n and vol_bins[j + 1] == v:
            j += 1
        # Edge plateaus have no outer neighbour on that side → treat missing
        # neighbour as 0 (no volume beyond the range), so an edge lobe still
        # counts.  Interior plateaus require strictly-lower volume on BOTH sides.
        left_ok = (i == 0) or (vol_bins[i - 1] < v)
        right_ok = (j == n - 1) or (vol_bins[j + 1] < v)
        if left_ok and right_ok:
            peaks.append((i + j) // 2)
        i = j + 1

    # A genuine B-profile is *bimodal* — exactly two structural lobes ("두 D형")
    # separated by an LVN valley.  Three or more structural peaks (each already
    # filtered to >= bimodal_peak_threshold of session_max) is a multimodal /
    # balanced distribution, NOT a bimodal split → must not classify as B
    # (spec lines 626, 700, 714: ratio/balance ≠ bimodal).
    if len(peaks) == 2:
        p1, p2 = peaks[0], peaks[1]
        # Valley floor between the two peaks.
        valley_slice = vol_bins[p1 : p2 + 1]
        valley_vol = min(valley_slice)
        valley_is_lvn = valley_vol / session_max_vol * 100.0 <= lvn_threshold
        # Split the profile at the valley so each lobe's mass is measured
        # independently (spec guard line 700: "두 거래량 피크가 각각 total_vol의
        # 10% 이상").  A cumulative tail-sum would wrongly absorb unrelated
        # distant volume into a lobe.
        valley_idx = p1 + valley_slice.index(valley_vol)
        lobe1_vol = sum(vol_bins[:valley_idx])
        lobe2_vol = sum(vol_bins[valley_idx + 1 :])
        each_10pct = (lobe1_vol >= total_vol * 0.10) and (lobe2_vol >= total_vol * 0.10)
        if valley_is_lvn and each_10pct:
            return "B"

    # (2) P-shape (spec: use poc_shape_upper_threshold parameter)
    if poc_rel >= poc_shape_upper_threshold and va_center_rel >= 0.55 and close_rel >= 0.50:
        return "P"

    # (3) b-shape (spec: use poc_shape_lower_threshold parameter)
    if poc_rel <= poc_shape_lower_threshold and va_center_rel <= 0.45 and close_rel <= 0.50:
        return "b"

    # (4) Default
    return "D"


def _ts_days_diff(
    ts1: date | datetime,
    ts2: date | datetime,
) -> float:
    """Return (ts1 - ts2).days (approximately) handling mixed date/datetime types."""
    d1 = ts1.date() if isinstance(ts1, datetime) else ts1
    d2 = ts2.date() if isinstance(ts2, datetime) else ts2
    return float((d1 - d2).days)


def _mitigate_npocs(
    npoc_state: list[tuple[float, date | datetime]],
    bars: list[PriceBar],
    *,
    lookback_sessions: int,
) -> list[tuple[float, date | datetime]]:
    """Process bars against existing NPOC list.

    Removes any NPOC that:
    - is touched by a bar's [low, high] range (mitigated), or
    - is stale: (current_bar.ts - formed_ts).days > lookback_sessions * 2
    Returns the surviving (unmitigated) NPOC list.
    """
    if not npoc_state:
        return []

    current_ts = bars[-1].ts
    staleness_days = lookback_sessions * 2
    surviving: list[tuple[float, date | datetime]] = []

    for npoc_price, formed_ts in npoc_state:
        # Staleness check
        if _ts_days_diff(current_ts, formed_ts) > staleness_days:
            continue
        # Mitigation check: did any bar in this window touch the NPOC?
        mitigated = any(b.low <= npoc_price <= b.high for b in bars)
        if not mitigated:
            surviving.append((npoc_price, formed_ts))

    return surviving
