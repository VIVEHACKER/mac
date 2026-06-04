"""Order-book depth analyser — L2 호가창 분석 (CHART_READING.md §10).

Implements OBI (Order Book Imbalance), VAMP (Volume Adjusted Mid Price),
liquidity-wall detection, cumulative depth, and the unified OrderBookSignal
aggregator.  Pure Python stdlib only; no pandas/numpy.

See docs/CHART_READING.md lines 1893-2074 for the complete specification.
"""

from __future__ import annotations

import math
import statistics
import time
from collections import deque
from dataclasses import dataclass

from data.models import OrderBookLevel, OrderBookSnapshot
from engine.chart.types import OBIRegime

# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WallInfo:
    """A single detected liquidity wall (price level with outsized size)."""

    price: float
    size: float
    suspect: bool = False  # True when spoofing conditions hold


@dataclass(frozen=True)
class OrderBookSignal:
    """Fully assembled order-book analysis result for one snapshot.

    Field names, types, and semantics mirror the spec output table exactly.
    ``obi_zscore`` is None when the rolling window has fewer than 10 samples.
    """

    ts: int  # milliseconds since epoch (exchange or local fallback)
    symbol: str

    # --- best quotes ---
    best_bid: float
    best_ask: float
    spread_abs: float
    spread_pct: float
    spread_is_wide: bool
    mid: float

    # --- VAMP ---
    vamp: float
    delta_vamp: float

    # --- OBI ---
    obi_flat: float
    obi_weighted: float
    regime: OBIRegime
    obi_zscore: float | None

    # --- cumulative depth ---
    cum_bid_depth_in_band: float
    cum_ask_depth_in_band: float
    depth_ratio: float

    # --- walls ---
    bid_walls: tuple[WallInfo, ...]
    ask_walls: tuple[WallInfo, ...]
    nearest_bid_wall_price: float | None
    nearest_bid_wall_vol: float | None
    nearest_ask_wall_price: float | None
    nearest_ask_wall_vol: float | None
    wall_is_suspect: bool

    # --- direction signal ---
    direction: str  # LONG / SHORT / WAIT / NEUTRAL
    strength: float  # 0..1


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

# Fixed LONG/SHORT entry wall-proximity band (spec §"진입 관련성" line 2003,
# condition 4): a non-suspect opposing wall within 0.5% of mid blocks entry.
# This is a microstructure constant, NOT derived from depth_band_pct.
_ENTRY_WALL_BAND_PCT: float = 0.5


# ---------------------------------------------------------------------------
# Stateless helper functions (each maps directly to a spec step)
# ---------------------------------------------------------------------------


def compute_obi_weighted(
    bids: tuple[OrderBookLevel, ...],
    asks: tuple[OrderBookLevel, ...],
    *,
    obi_top_n: int = 10,
) -> tuple[float, float]:
    """Compute both flat and exponentially-decayed weighted OBI.

    Returns ``(obi_flat, obi_weighted)`` in range [-1, 1].

    Spec steps 5 and 6.
    """
    n = min(obi_top_n, len(bids), len(asks))
    if n == 0:
        return 0.0, 0.0

    # --- Step 5: flat OBI ---
    bid_vol_n = sum(bids[i].size for i in range(n))
    ask_vol_n = sum(asks[i].size for i in range(n))
    total_flat = bid_vol_n + ask_vol_n
    obi_flat = (bid_vol_n - ask_vol_n) / total_flat if total_flat > 0 else 0.0

    # --- Step 6: exponentially decayed OBI ---
    weights = [1.0 / (2**i) for i in range(n)]
    weighted_bid = sum(bids[i].size * weights[i] for i in range(n))
    weighted_ask = sum(asks[i].size * weights[i] for i in range(n))
    w_total = weighted_bid + weighted_ask
    obi_weighted = (weighted_bid - weighted_ask) / w_total if w_total > 0 else 0.0

    return obi_flat, obi_weighted


def classify_obi_regime(
    obi_weighted: float,
    *,
    strong_threshold: float = 0.6,
    mild_threshold: float = 0.2,
) -> OBIRegime:
    """Map a weighted OBI value to a 5-bucket regime.

    Spec step 7: Cartea et al. / TDS crypto 5-bucket classification.
    ``threshold=0.60`` is the public API alias for ``obi_strong_threshold``.
    """
    if obi_weighted >= strong_threshold:
        return OBIRegime.STRONG_BID
    if obi_weighted >= mild_threshold:
        return OBIRegime.MILD_BID
    if obi_weighted <= -strong_threshold:
        return OBIRegime.STRONG_ASK
    if obi_weighted <= -mild_threshold:
        return OBIRegime.MILD_ASK
    return OBIRegime.NEUTRAL


def compute_obi_zscore(
    obi_series: list[float] | deque[float],
    lookback: int = 20,
) -> float | None:
    """Compute a rolling z-score for the most-recent OBI value.

    Uses sample standard deviation (Bessel-corrected, n-1 denominator) as
    specified in step 11.  Returns None when fewer than 10 samples are present.
    """
    window = list(obi_series)[-lookback:] if len(obi_series) > lookback else list(obi_series)
    if len(window) < 10:
        return None
    current = window[-1]
    mean_obi = statistics.mean(window)
    stdev_obi = statistics.stdev(window)  # sample stdev, Bessel-corrected
    return (current - mean_obi) / stdev_obi if stdev_obi > 0 else 0.0


def detect_order_walls(
    bids: tuple[OrderBookLevel, ...],
    asks: tuple[OrderBookLevel, ...],
    mid_price: float,
    *,
    obi_top_n: int = 10,
    wall_ratio: float = 5.0,
    wall_distance_pct: float = 2.0,
    prev_bid_walls: frozenset[float] | None = None,
    prev_ask_walls: frozenset[float] | None = None,
) -> tuple[list[WallInfo], list[WallInfo]]:
    """Detect liquidity walls in N levels on each side.

    Returns ``(bid_walls, ask_walls)`` each being a list of WallInfo.
    Spec steps 9 and 10.

    ``prev_bid_walls`` / ``prev_ask_walls`` are price sets from the previous
    snapshot; walls absent in the previous snapshot are flagged suspect (step 10a).
    Walls beyond ``wall_distance_pct``% from mid are also flagged suspect (step 10b).
    """
    n = min(obi_top_n, len(bids), len(asks))

    def _walls(
        levels: tuple[OrderBookLevel, ...],
        prev_prices: frozenset[float] | None,
        is_bid: bool,
    ) -> list[WallInfo]:
        if n == 0:
            return []
        vols = [levels[i].size for i in range(n)]
        if not vols:
            return []
        median_vol = statistics.median(vols)
        threshold = wall_ratio * median_vol
        walls: list[WallInfo] = []
        for i in range(n):
            lv = levels[i]
            if lv.size > threshold:
                dist_pct = abs(lv.price - mid_price) / mid_price * 100.0
                # Step 10b: too far from mid
                far_away = dist_pct > wall_distance_pct
                # Step 10a: appeared fresh in this snapshot
                new_wall = prev_prices is not None and lv.price not in prev_prices
                suspect = far_away or new_wall
                walls.append(WallInfo(price=lv.price, size=lv.size, suspect=suspect))
        return walls

    bid_walls = _walls(bids, prev_bid_walls, is_bid=True)
    ask_walls = _walls(asks, prev_ask_walls, is_bid=False)
    return bid_walls, ask_walls


def compute_delta_vamp(
    bids: tuple[OrderBookLevel, ...],
    asks: tuple[OrderBookLevel, ...],
) -> tuple[float, float, float]:
    """Return (vamp, mid, delta_vamp) from best-quote cross-multiply.

    Spec step 4.  delta_vamp = vamp − mid (positive → bullish bias).
    """
    if not bids or not asks:
        mid = 0.0
        return 0.0, mid, 0.0

    best_bid = bids[0].price
    best_bid_vol = bids[0].size
    best_ask = asks[0].price
    best_ask_vol = asks[0].size

    mid = (best_bid + best_ask) / 2.0
    denom = best_bid_vol + best_ask_vol
    if denom <= 0:
        return mid, mid, 0.0
    vamp = (best_bid_vol * best_ask + best_ask_vol * best_bid) / denom
    return vamp, mid, vamp - mid


def is_spread_wide(
    bids: tuple[OrderBookLevel, ...],
    asks: tuple[OrderBookLevel, ...],
    mid_price: float,
    *,
    max_spread_pct: float = 0.001,
) -> bool:
    """Return True when the bid-ask spread exceeds ``max_spread_pct`` of mid.

    ``max_spread_pct`` default is 0.001 (= 0.1% per spec param
    ``spread_pct_threshold`` default of 0.10%).
    Spec step 3.
    """
    if not bids or not asks:
        return True
    spread = asks[0].price - bids[0].price
    if mid_price <= 0:
        return True
    return (spread / mid_price) > max_spread_pct


def _compute_cumulative_depth(
    bids: tuple[OrderBookLevel, ...],
    asks: tuple[OrderBookLevel, ...],
    mid: float,
    *,
    depth_band_pct: float = 1.0,
) -> tuple[float, float, float]:
    """Compute cumulative depth within a ±depth_band_pct % band.

    Returns ``(cum_bid, cum_ask, depth_ratio)``; depth_ratio is inf when cum_ask == 0.
    Spec step 8.
    """
    band = mid * depth_band_pct / 100.0
    cum_bid = sum(lv.size for lv in bids if lv.price >= mid - band)
    cum_ask = sum(lv.size for lv in asks if lv.price <= mid + band)
    ratio = cum_bid / cum_ask if cum_ask > 0 else math.inf
    return cum_bid, cum_ask, ratio


def _direction_signal(
    *,
    regime: OBIRegime,
    delta_vamp: float,
    spread_is_wide: bool,
    obi_zscore: float | None,
    ask_walls: list[WallInfo],
    bid_walls: list[WallInfo],
    mid: float,
    obi_weighted: float,
    strong_threshold: float,
    depth_band_pct: float,
) -> tuple[str, float]:
    """Derive direction string and strength scalar per spec "진입 관련성" section."""
    # WAIT conditions (spec: neutral regime, wide spread, or weak z-score)
    if spread_is_wide:
        return "WAIT", 0.0
    if regime == OBIRegime.NEUTRAL:
        return "NEUTRAL", 0.0
    if obi_zscore is not None and abs(obi_zscore) <= 1.0:
        return "WAIT", 0.0

    # Confirmed (non-suspect) walls on the opposing side suppress entry.
    # The spec uses TWO distinct distances and the wider one is the binding gate:
    #   - LONG ENTRY  (spec §"진입 관련성" line 2003, cond 4): no non-suspect ask
    #     wall within a FIXED 0.5% of mid.
    #   - LONG AVOID  (spec line 2007): a confirmed ask wall within depth_band_pct%
    #     of mid acts as resistance → WAIT.
    # Taking the union (max of the two bands) is correct: any confirmed opposing
    # wall inside max(0.5%, depth_band_pct%) blocks the entry. With the default
    # depth_band_pct=1.0 the avoid band (1.0%) dominates the 0.5% entry band; with
    # a smaller depth_band_pct the 0.5% entry constant still applies.
    wall_block_pct = max(_ENTRY_WALL_BAND_PCT, depth_band_pct)

    def _confirmed_near(walls: list[WallInfo]) -> list[WallInfo]:
        return [
            w for w in walls if not w.suspect and abs(w.price - mid) / mid * 100.0 <= wall_block_pct
        ]

    confirmed_ask_walls_near = _confirmed_near(ask_walls)
    confirmed_bid_walls_near = _confirmed_near(bid_walls)

    # LONG conditions (spec: STRONG_BID + delta_vamp > 0 + no confirmed near ask wall)
    if regime == OBIRegime.STRONG_BID and delta_vamp > 0 and not confirmed_ask_walls_near:
        strength = min(1.0, abs(obi_weighted))
        return "LONG", strength

    # SHORT conditions (mirror of LONG)
    if regime == OBIRegime.STRONG_ASK and delta_vamp < 0 and not confirmed_bid_walls_near:
        strength = min(1.0, abs(obi_weighted))
        return "SHORT", strength

    # AVOID conditions (STRONG_ASK regime for long, vice versa)
    if regime in (OBIRegime.STRONG_ASK, OBIRegime.STRONG_BID):
        return "WAIT", 0.0

    return "NEUTRAL", 0.0


# ---------------------------------------------------------------------------
# Stateful per-symbol aggregator
# ---------------------------------------------------------------------------


class OrderBookAnalyser:
    """Stateful per-symbol analyser that accumulates the OBI rolling window.

    Holds a rolling deque of ``obi_weighted`` values for z-score normalisation
    (spec step 11) and the previous snapshot's wall price sets (step 10a).

    Typical usage::

        analyser = OrderBookAnalyser()
        signal = analyser.analyse(snapshot)
    """

    def __init__(
        self,
        *,
        obi_top_n: int = 10,
        spread_pct_threshold: float = 0.10,
        depth_band_pct: float = 1.0,
        wall_size_multiplier: float = 5.0,
        wall_distance_pct: float = 2.0,
        obi_zscore_window: int = 60,
        obi_strong_threshold: float = 0.6,
        obi_mild_threshold: float = 0.2,
    ) -> None:
        self.obi_top_n = obi_top_n
        self.spread_pct_threshold = spread_pct_threshold
        self.depth_band_pct = depth_band_pct
        self.wall_size_multiplier = wall_size_multiplier
        self.wall_distance_pct = wall_distance_pct
        self.obi_zscore_window = obi_zscore_window
        self.obi_strong_threshold = obi_strong_threshold
        self.obi_mild_threshold = obi_mild_threshold

        self._obi_history: deque[float] = deque(maxlen=obi_zscore_window)
        self._prev_bid_wall_prices: frozenset[float] | None = None
        self._prev_ask_wall_prices: frozenset[float] | None = None

    def analyse(self, snapshot: OrderBookSnapshot) -> OrderBookSignal:
        """Run the full 13-step algorithm on one snapshot and return an OrderBookSignal."""
        bids = snapshot.bids
        asks = snapshot.asks

        # Step 1 validation: non-empty
        if not bids or not asks:
            raise ValueError(f"Empty order book for {snapshot.symbol}")

        # Step 2 validation: crossed-book guard + sort order guard
        best_bid = bids[0].price
        best_ask = asks[0].price
        if best_bid >= best_ask:
            raise ValueError(
                f"Crossed book ({snapshot.symbol}): best_bid={best_bid} >= best_ask={best_ask}"
            )
        if len(bids) >= 2 and bids[0].price <= bids[1].price:
            raise ValueError("Bids not sorted descending")
        if len(asks) >= 2 and asks[0].price >= asks[1].price:
            raise ValueError("Asks not sorted ascending")

        # Step 3: spread
        spread_abs = best_ask - best_bid
        mid = (best_bid + best_ask) / 2.0
        spread_pct = spread_abs / mid * 100.0
        wide = spread_pct > self.spread_pct_threshold

        # Step 4: VAMP
        best_bid_vol = bids[0].size
        best_ask_vol = asks[0].size
        denom = best_bid_vol + best_ask_vol
        vamp = (best_bid_vol * best_ask + best_ask_vol * best_bid) / denom if denom > 0 else mid
        delta_vamp = vamp - mid

        # Steps 5 & 6: OBI
        obi_flat, obi_weighted = compute_obi_weighted(bids, asks, obi_top_n=self.obi_top_n)

        # Step 7: regime
        regime = classify_obi_regime(
            obi_weighted,
            strong_threshold=self.obi_strong_threshold,
            mild_threshold=self.obi_mild_threshold,
        )

        # Step 8: cumulative depth
        cum_bid, cum_ask, depth_ratio = _compute_cumulative_depth(
            bids, asks, mid, depth_band_pct=self.depth_band_pct
        )

        # Steps 9 & 10: walls
        bid_walls, ask_walls = detect_order_walls(
            bids,
            asks,
            mid,
            obi_top_n=self.obi_top_n,
            wall_ratio=self.wall_size_multiplier,
            wall_distance_pct=self.wall_distance_pct,
            prev_bid_walls=self._prev_bid_wall_prices,
            prev_ask_walls=self._prev_ask_wall_prices,
        )
        wall_is_suspect = any(w.suspect for w in bid_walls + ask_walls)

        # Step 11: OBI z-score
        self._obi_history.append(obi_weighted)
        obi_zscore = compute_obi_zscore(self._obi_history, lookback=self.obi_zscore_window)

        # Step 12: timestamp (exchange ts or local fallback)
        ts_ms = int(snapshot.ts.timestamp() * 1000) if snapshot.ts else int(time.time() * 1000)

        # Step 13: direction signal
        direction, strength = _direction_signal(
            regime=regime,
            delta_vamp=delta_vamp,
            spread_is_wide=wide,
            obi_zscore=obi_zscore,
            ask_walls=ask_walls,
            bid_walls=bid_walls,
            mid=mid,
            obi_weighted=obi_weighted,
            strong_threshold=self.obi_strong_threshold,
            depth_band_pct=self.depth_band_pct,
        )

        # Update previous-snapshot wall sets for next call (step 10a)
        self._prev_bid_wall_prices = frozenset(w.price for w in bid_walls)
        self._prev_ask_wall_prices = frozenset(w.price for w in ask_walls)

        # Nearest walls
        nearest_bid_wall: WallInfo | None = (
            max(bid_walls, key=lambda w: w.price) if bid_walls else None
        )
        nearest_ask_wall: WallInfo | None = (
            min(ask_walls, key=lambda w: w.price) if ask_walls else None
        )

        return OrderBookSignal(
            ts=ts_ms,
            symbol=snapshot.symbol,
            best_bid=best_bid,
            best_ask=best_ask,
            spread_abs=spread_abs,
            spread_pct=spread_pct,
            spread_is_wide=wide,
            mid=mid,
            vamp=vamp,
            delta_vamp=delta_vamp,
            obi_flat=obi_flat,
            obi_weighted=obi_weighted,
            regime=regime,
            obi_zscore=obi_zscore,
            cum_bid_depth_in_band=cum_bid,
            cum_ask_depth_in_band=cum_ask,
            depth_ratio=depth_ratio,
            bid_walls=tuple(bid_walls),
            ask_walls=tuple(ask_walls),
            nearest_bid_wall_price=nearest_bid_wall.price if nearest_bid_wall else None,
            nearest_bid_wall_vol=nearest_bid_wall.size if nearest_bid_wall else None,
            nearest_ask_wall_price=nearest_ask_wall.price if nearest_ask_wall else None,
            nearest_ask_wall_vol=nearest_ask_wall.size if nearest_ask_wall else None,
            wall_is_suspect=wall_is_suspect,
            direction=direction,
            strength=strength,
        )


# ---------------------------------------------------------------------------
# Module-level convenience wrapper
# ---------------------------------------------------------------------------


def analyze_order_book(
    snapshot: OrderBookSnapshot,
    *,
    analyser: OrderBookAnalyser | None = None,
    obi_top_n: int = 10,
    spread_pct_threshold: float = 0.10,
    depth_band_pct: float = 1.0,
    wall_size_multiplier: float = 5.0,
    wall_distance_pct: float = 2.0,
    obi_zscore_window: int = 60,
    obi_strong_threshold: float = 0.6,
    obi_mild_threshold: float = 0.2,
) -> OrderBookSignal:
    """Top-level aggregator: run the full order-book analysis on a single snapshot.

    When ``analyser`` is supplied, uses it (preserving its rolling OBI history).
    Otherwise creates a stateless one-shot analyser with the provided parameters.
    """
    if analyser is None:
        analyser = OrderBookAnalyser(
            obi_top_n=obi_top_n,
            spread_pct_threshold=spread_pct_threshold,
            depth_band_pct=depth_band_pct,
            wall_size_multiplier=wall_size_multiplier,
            wall_distance_pct=wall_distance_pct,
            obi_zscore_window=obi_zscore_window,
            obi_strong_threshold=obi_strong_threshold,
            obi_mild_threshold=obi_mild_threshold,
        )
    return analyser.analyse(snapshot)
