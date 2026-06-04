"""Wyckoff Accumulation / Distribution schematic detector.

Implements the full Phase A→E detection pipeline from docs/CHART_READING.md §7,
including: SC/BC (climax), AR, ST, Spring/UTAD, Test, SOS/SOW,
Creek/JAC/BUEC (accumulation), LPS/LPSY, phase classification, confidence scoring,
and entry-signal generation.

All detection is strictly no-lookahead: at bar index ``t`` only ``bars[0..t]``
are referenced.  Swing pivots confirm only after ``pivot_lookback`` bars have
closed on both sides.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from data.models import PriceBar  # noqa: E402

# ---------------------------------------------------------------------------
# Parameters dataclass
# ---------------------------------------------------------------------------


@dataclass
class WyckoffParams:
    """Detection parameters with spec-specified defaults."""

    climax_volume_zscore: float = 2.0
    climax_vol_lookback: int = 50
    climax_spread_ratio: float = 1.5
    sc_close_pct: float = 0.30
    bc_close_pct: float = 0.25
    tr_lookback: int = 200
    tr_min_range_pct: float = 0.02
    ar_max_bars: int = 30
    ar_min_retracement_pct: float = 0.05
    st_proximity_pct: float = 0.15
    st_volume_ratio: float = 0.70
    st_spread_ratio: float = 0.80
    spring_break_pct: float = 0.05
    spring_reject_bars: int = 3
    test_volume_ratio: float = 0.60
    utad_break_pct: float = 0.05
    sos_min_move_pct: float = 0.03
    sos_volume_ratio: float = 1.20
    lps_retracement_max: float = 0.50
    lps_volume_ratio: float = 0.80
    lps_above_tr_pct: float = 0.01
    lpsy_max_rally: float = 0.50
    lpsy_below_tr_pct: float = 0.01
    jac_buffer_pct: float = 0.003
    jac_volume_ratio: float = 1.50
    buec_tolerance_pct: float = 0.01
    phase_e_breakout_pct: float = 0.01
    vol_asymmetry_ratio: float = 1.10
    ps_volume_zscore: float = 1.20
    vol_ma_period: int = 20
    spread_ma_period: int = 20
    pivot_lookback: int = 3


# Spec STEP 7 (distribution Phase E) default; mirrors ``WyckoffParams.phase_e_breakout_pct``.
# ``classify_phase`` receives no params, so the spec default is used here.
_PHASE_E_BREAKOUT_PCT: float = 0.01


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WyckoffEvent:
    """A single detected Wyckoff event in the schematic."""

    name: str
    ts: date | datetime
    price: float
    volume: float
    bar_index: int
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WyckoffTR:
    """Trading Range boundaries."""

    low_anchor: float  # TR lower bound
    high_anchor: float  # TR upper bound
    tr_range: float  # high_anchor - low_anchor


@dataclass
class WyckoffSchematic:
    """Full Wyckoff schematic result for a bar series."""

    schematic_type: str | None  # 'accumulation' | 'distribution' | None
    tr: WyckoffTR | None
    events: list[WyckoffEvent] = field(default_factory=list)
    phase: str | None = None  # 'A'|'B'|'C'|'D'|'E'|None
    phase_confidence: float = 0.0
    volume_asymmetry_correct: bool = False
    oi_confirmation: bool = False
    entry_signal: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# STEP 0 — Rolling statistics helpers (no-lookahead: only bars[0..i])
# ---------------------------------------------------------------------------


def _vol_zscore(i: int, bars: list[PriceBar], lookback: int) -> float:
    start = max(0, i - lookback + 1)
    window = [bars[j].volume for j in range(start, i + 1)]
    if len(window) < 2:
        return 0.0
    mean_v = statistics.mean(window)
    std_v = statistics.stdev(window)
    return (bars[i].volume - mean_v) / std_v if std_v > 0 else 0.0


def _spread(bar: PriceBar) -> float:
    return bar.high - bar.low


def _vol_ma(i: int, bars: list[PriceBar], period: int) -> float:
    start = max(0, i - period + 1)
    return statistics.mean(bars[j].volume for j in range(start, i + 1))


def _spread_ma(i: int, bars: list[PriceBar], period: int) -> float:
    start = max(0, i - period + 1)
    return statistics.mean(_spread(bars[j]) for j in range(start, i + 1))


def _atr(bars: list[PriceBar], end_idx: int, period: int = 14) -> float:
    """Simple ATR (no-lookahead) ending at ``end_idx``."""
    trs: list[float] = []
    for j in range(max(1, end_idx - period + 1), end_idx + 1):
        tr = max(
            bars[j].high - bars[j].low,
            abs(bars[j].high - bars[j - 1].close),
            abs(bars[j].low - bars[j - 1].close),
        )
        trs.append(tr)
    return statistics.mean(trs) if trs else _spread(bars[end_idx])


# ---------------------------------------------------------------------------
# STEP 1 — Climax detection (SC / BC)
# ---------------------------------------------------------------------------


def _find_sc(bars: list[PriceBar], p: WyckoffParams) -> WyckoffEvent | None:
    """Find the Selling Climax (lowest-low candidate among SC-qualified bars)."""
    candidates: list[WyckoffEvent] = []
    for i in range(1, len(bars)):
        vz = _vol_zscore(i, bars, p.climax_vol_lookback)
        sp = _spread(bars[i])
        sp_ma = _spread_ma(i, bars, p.spread_ma_period)
        cond_vol = vz >= p.climax_volume_zscore
        cond_price = bars[i].close < bars[i - 1].close and sp >= sp_ma * p.climax_spread_ratio
        denom = max(sp, 1e-9)
        cond_wick = (bars[i].close - bars[i].low) / denom >= p.sc_close_pct
        if cond_vol and cond_price and cond_wick:
            candidates.append(
                WyckoffEvent(
                    name="SC",
                    ts=bars[i].ts,
                    price=bars[i].low,
                    volume=bars[i].volume,
                    bar_index=i,
                    detail={"close": bars[i].close, "vol_zscore": round(vz, 4)},
                )
            )
    if not candidates:
        return None
    return min(candidates, key=lambda e: e.price)


def _find_bc(bars: list[PriceBar], p: WyckoffParams) -> WyckoffEvent | None:
    """Find the Buying Climax (highest-high candidate among BC-qualified bars)."""
    candidates: list[WyckoffEvent] = []
    for i in range(1, len(bars)):
        vz = _vol_zscore(i, bars, p.climax_vol_lookback)
        sp = _spread(bars[i])
        sp_ma = _spread_ma(i, bars, p.spread_ma_period)
        cond_vol = vz >= p.climax_volume_zscore
        cond_price = bars[i].close > bars[i - 1].close and sp >= sp_ma * p.climax_spread_ratio
        denom = max(sp, 1e-9)
        cond_wick = (bars[i].high - bars[i].close) / denom >= p.bc_close_pct
        if cond_vol and cond_price and cond_wick:
            candidates.append(
                WyckoffEvent(
                    name="BC",
                    ts=bars[i].ts,
                    price=bars[i].high,
                    volume=bars[i].volume,
                    bar_index=i,
                    detail={"close": bars[i].close, "vol_zscore": round(vz, 4)},
                )
            )
    if not candidates:
        return None
    return max(candidates, key=lambda e: e.price)


# ---------------------------------------------------------------------------
# STEP 2 — AR (Automatic Rally / Automatic Reaction)
# ---------------------------------------------------------------------------


def _find_ar_accumulation(
    bars: list[PriceBar],
    sc: WyckoffEvent,
    tr_low: float,
    p: WyckoffParams,
) -> WyckoffEvent | None:
    """AR for accumulation: highest high in [sc_idx+1 … sc_idx+ar_max_bars]."""
    start = sc.bar_index + 1
    end = min(sc.bar_index + p.ar_max_bars + 1, len(bars))
    if start >= end:
        return None
    best_price = -math.inf
    best_idx = start
    for j in range(start, end):
        if bars[j].high > best_price:
            best_price = bars[j].high
            best_idx = j
    retracement = (best_price - tr_low) / max(tr_low, 1e-9)
    if retracement < p.ar_min_retracement_pct:
        return None
    return WyckoffEvent(
        name="AR",
        ts=bars[best_idx].ts,
        price=best_price,
        volume=bars[best_idx].volume,
        bar_index=best_idx,
        detail={"retracement_pct": round(retracement, 4)},
    )


def _find_ar_distribution(
    bars: list[PriceBar],
    bc: WyckoffEvent,
    tr_high: float,
    p: WyckoffParams,
) -> WyckoffEvent | None:
    """AR for distribution: lowest low in [bc_idx+1 … bc_idx+ar_max_bars]."""
    start = bc.bar_index + 1
    end = min(bc.bar_index + p.ar_max_bars + 1, len(bars))
    if start >= end:
        return None
    best_price = math.inf
    best_idx = start
    for j in range(start, end):
        if bars[j].low < best_price:
            best_price = bars[j].low
            best_idx = j
    retracement = (tr_high - best_price) / max(tr_high, 1e-9)
    if retracement < p.ar_min_retracement_pct:
        return None
    return WyckoffEvent(
        name="AR",
        ts=bars[best_idx].ts,
        price=best_price,
        volume=bars[best_idx].volume,
        bar_index=best_idx,
        detail={"retracement_pct": round(retracement, 4)},
    )


# ---------------------------------------------------------------------------
# STEP 3 — ST (Secondary Test)
# ---------------------------------------------------------------------------


def _find_sts(
    bars: list[PriceBar],
    ar: WyckoffEvent,
    climax: WyckoffEvent,
    tr: WyckoffTR,
    schematic_type: str,
    p: WyckoffParams,
) -> list[WyckoffEvent]:
    """Scan for Secondary Tests after AR is confirmed."""
    sts: list[WyckoffEvent] = []
    for j in range(ar.bar_index + 1, len(bars)):
        sp = _spread(bars[j])
        if schematic_type == "accumulation":
            proximity = abs(bars[j].low - tr.low_anchor) / max(tr.tr_range, 1e-9)
            cond_prox = proximity <= p.st_proximity_pct
            cond_vol = bars[j].volume < climax.volume * p.st_volume_ratio
            cond_price = bars[j].low > tr.low_anchor * (1 - p.spring_break_pct)
            cond_spread = sp < _spread(bars[climax.bar_index]) * p.st_spread_ratio
        else:  # distribution
            proximity = abs(bars[j].high - tr.high_anchor) / max(tr.tr_range, 1e-9)
            cond_prox = proximity <= p.st_proximity_pct
            cond_vol = bars[j].volume < climax.volume * p.st_volume_ratio
            cond_price = bars[j].high < tr.high_anchor * (1 + p.spring_break_pct)
            cond_spread = sp < _spread(bars[climax.bar_index]) * p.st_spread_ratio

        if cond_prox and cond_vol and cond_price and cond_spread:
            price = bars[j].low if schematic_type == "accumulation" else bars[j].high
            # false-positive guard: if ST volume >= SC volume, flag it
            vol_warn = bars[j].volume >= climax.volume
            sts.append(
                WyckoffEvent(
                    name="ST",
                    ts=bars[j].ts,
                    price=price,
                    volume=bars[j].volume,
                    bar_index=j,
                    detail={"proximity": round(proximity, 4), "vol_warn": vol_warn},
                )
            )
    return sts


# ---------------------------------------------------------------------------
# STEP 4 — Spring (accumulation) & UTAD (distribution)
# ---------------------------------------------------------------------------


def _find_spring(
    bars: list[PriceBar],
    ar: WyckoffEvent,
    tr: WyckoffTR,
    st_list: list[WyckoffEvent],
    p: WyckoffParams,
) -> WyckoffEvent | None:
    """Detect Spring: TR-low break + reclaim within spring_reject_bars."""
    if len(st_list) < 1:
        return None
    start = ar.bar_index + 1
    for i in range(start, len(bars)):
        if not (bars[i].low < tr.low_anchor):
            continue
        break_pct = (tr.low_anchor - bars[i].low) / max(tr.tr_range, 1e-9)
        if break_pct > p.spring_break_pct:
            continue  # guard: too deep = real breakdown
        # Check reclaim within spring_reject_bars (only already-closed bars)
        reclaimed = bars[i].close > tr.low_anchor
        if not reclaimed:
            for k in range(i + 1, min(i + p.spring_reject_bars + 1, len(bars))):
                if bars[k].close > tr.low_anchor:
                    reclaimed = True
                    break
        if not reclaimed:
            continue
        # Classify spring type
        vz = _vol_zscore(i, bars, p.climax_vol_lookback)
        if vz >= p.climax_volume_zscore:
            spring_type = 1
        elif vz >= p.climax_volume_zscore * 0.4:
            spring_type = 2
        else:
            sp = _spread(bars[i])
            sp_ma = _spread_ma(i, bars, p.spread_ma_period)
            spring_type = 3 if sp < sp_ma * 1.0 else 2
        return WyckoffEvent(
            name="Spring",
            ts=bars[i].ts,
            price=bars[i].low,
            volume=bars[i].volume,
            bar_index=i,
            detail={
                "close": bars[i].close,
                "spring_type": spring_type,
                "tr_break_pct": round(break_pct, 4),
                "vol_zscore": round(vz, 4),
            },
        )
    return None


def _find_test_of_spring(
    bars: list[PriceBar],
    spring: WyckoffEvent,
    tr: WyckoffTR,
    p: WyckoffParams,
) -> WyckoffEvent | None:
    """Test-after-Spring: revisit TR.low_anchor area with low volume."""
    start = spring.bar_index + p.spring_reject_bars + 1
    for j in range(start, len(bars)):
        low_near = bars[j].low <= tr.low_anchor * (1.0 + p.st_proximity_pct)
        above_spring = bars[j].low > tr.low_anchor * (1.0 - p.spring_break_pct * 0.5)
        if not (low_near and above_spring):
            continue
        cond_vol = bars[j].volume < spring.volume * p.test_volume_ratio
        cond_close = bars[j].close > tr.low_anchor
        if cond_vol and cond_close:
            return WyckoffEvent(
                name="Test",
                ts=bars[j].ts,
                price=bars[j].low,
                volume=bars[j].volume,
                bar_index=j,
                detail={"close": bars[j].close},
            )
    return None


def _find_utad(
    bars: list[PriceBar],
    ar: WyckoffEvent,
    tr: WyckoffTR,
    st_list: list[WyckoffEvent],
    p: WyckoffParams,
) -> WyckoffEvent | None:
    """UTAD: TR-high break + reversal (close below TR.high_anchor)."""
    if len(st_list) < 1:
        return None
    start = ar.bar_index + 1
    for i in range(start, len(bars)):
        if not (bars[i].high > tr.high_anchor):
            continue
        break_pct = (bars[i].high - tr.high_anchor) / max(tr.tr_range, 1e-9)
        if break_pct > p.utad_break_pct:
            continue
        reclaimed = bars[i].close < tr.high_anchor
        if not reclaimed:
            for k in range(i + 1, min(i + p.spring_reject_bars + 1, len(bars))):
                if bars[k].close < tr.high_anchor:
                    reclaimed = True
                    break
        if not reclaimed:
            continue
        vz = _vol_zscore(i, bars, p.climax_vol_lookback)
        if vz >= p.climax_volume_zscore:
            utad_type = 1
        elif vz >= p.climax_volume_zscore * 0.4:
            utad_type = 2
        else:
            utad_type = 3
        return WyckoffEvent(
            name="UTAD",
            ts=bars[i].ts,
            price=bars[i].high,
            volume=bars[i].volume,
            bar_index=i,
            detail={
                "close": bars[i].close,
                "utad_type": utad_type,
                "tr_break_pct": round(break_pct, 4),
                "vol_zscore": round(vz, 4),
            },
        )
    return None


# ---------------------------------------------------------------------------
# STEP 5 — SOS / SOW
# ---------------------------------------------------------------------------


def _find_sos(
    bars: list[PriceBar],
    start_idx: int,
    last_trough_close: float,
    p: WyckoffParams,
) -> WyckoffEvent | None:
    for i in range(start_idx, len(bars)):
        swing_up_pct = (bars[i].close - last_trough_close) / max(last_trough_close, 1e-9)
        if swing_up_pct < p.sos_min_move_pct:
            continue
        vol_ma = _vol_ma(i, bars, p.vol_ma_period)
        sp_ma = _spread_ma(i, bars, p.spread_ma_period)
        cond_vol = bars[i].volume >= vol_ma * p.sos_volume_ratio and (
            i == 0 or bars[i].volume > bars[i - 1].volume
        )
        cond_spread = _spread(bars[i]) >= sp_ma * 1.1
        cond_close = bars[i].close >= bars[i].open
        if cond_vol and cond_spread and cond_close:
            return WyckoffEvent(
                name="SOS",
                ts=bars[i].ts,
                price=bars[i].close,
                volume=bars[i].volume,
                bar_index=i,
                detail={"swing_up_pct": round(swing_up_pct, 4)},
            )
    return None


def _find_sow(
    bars: list[PriceBar],
    start_idx: int,
    last_peak_close: float,
    p: WyckoffParams,
) -> WyckoffEvent | None:
    for i in range(start_idx, len(bars)):
        swing_down_pct = (last_peak_close - bars[i].close) / max(last_peak_close, 1e-9)
        if swing_down_pct < p.sos_min_move_pct:
            continue
        vol_ma = _vol_ma(i, bars, p.vol_ma_period)
        sp_ma = _spread_ma(i, bars, p.spread_ma_period)
        cond_vol = bars[i].volume >= vol_ma * p.sos_volume_ratio and (
            i == 0 or bars[i].volume > bars[i - 1].volume
        )
        cond_spread = _spread(bars[i]) >= sp_ma * 1.1
        cond_close = bars[i].close <= bars[i].open
        if cond_vol and cond_spread and cond_close:
            return WyckoffEvent(
                name="SOW",
                ts=bars[i].ts,
                price=bars[i].close,
                volume=bars[i].volume,
                bar_index=i,
                detail={"swing_down_pct": round(swing_down_pct, 4), "close": bars[i].close},
            )
    return None


# ---------------------------------------------------------------------------
# STEP 5b — Creek / JAC / BUEC
# ---------------------------------------------------------------------------


def _find_jac(
    bars: list[PriceBar],
    sos: WyckoffEvent,
    creek_resistance: float,
    p: WyckoffParams,
) -> WyckoffEvent | None:
    for i in range(sos.bar_index, len(bars)):
        threshold = creek_resistance * (1 + p.jac_buffer_pct)
        if bars[i].close <= threshold:
            continue
        vol_ma = _vol_ma(i, bars, p.vol_ma_period)
        if bars[i].volume >= vol_ma * p.jac_volume_ratio:
            return WyckoffEvent(
                name="JAC",
                ts=bars[i].ts,
                price=bars[i].close,
                volume=bars[i].volume,
                bar_index=i,
                detail={"creek_resistance": creek_resistance},
            )
    return None


def _find_buec(
    bars: list[PriceBar],
    jac: WyckoffEvent,
    creek_resistance: float,
    p: WyckoffParams,
) -> WyckoffEvent | None:
    for j in range(jac.bar_index + 1, len(bars)):
        near = bars[j].low <= creek_resistance * (1 + p.buec_tolerance_pct)
        close_above = bars[j].close > creek_resistance * (1 - p.buec_tolerance_pct)
        vol_ma = _vol_ma(j, bars, p.vol_ma_period)
        low_vol = bars[j].volume < vol_ma * 0.8
        if near and close_above and low_vol:
            return WyckoffEvent(
                name="BUEC",
                ts=bars[j].ts,
                price=bars[j].low,
                volume=bars[j].volume,
                bar_index=j,
                detail={"creek_resistance": creek_resistance},
            )
    return None


# ---------------------------------------------------------------------------
# STEP 6 — LPS / LPSY
# ---------------------------------------------------------------------------


def _find_lps(
    bars: list[PriceBar],
    sos: WyckoffEvent,
    spring_price: float,
    tr: WyckoffTR,
    p: WyckoffParams,
) -> WyckoffEvent | None:
    sos_thrust = sos.price - spring_price
    max_ret = sos.price - sos_thrust * p.lps_retracement_max
    for i in range(sos.bar_index + 1, len(bars)):
        cond_price = bars[i].low >= max_ret and bars[i].low > tr.low_anchor * (
            1 + p.lps_above_tr_pct
        )
        if not cond_price:
            continue
        vol_ma = _vol_ma(i, bars, p.vol_ma_period)
        cond_vol = (
            bars[i].volume < vol_ma * p.lps_volume_ratio and bars[i].volume < sos.volume * 0.70
        )
        sp_ma = _spread_ma(i, bars, p.spread_ma_period)
        cond_spread = _spread(bars[i]) < sp_ma * 0.90
        if cond_vol and cond_spread:
            return WyckoffEvent(
                name="LPS",
                ts=bars[i].ts,
                price=bars[i].low,
                volume=bars[i].volume,
                bar_index=i,
                detail={"max_retracement": round(max_ret, 4)},
            )
    return None


def _find_lpsy(
    bars: list[PriceBar],
    sow: WyckoffEvent,
    bc_price: float,
    tr: WyckoffTR,
    p: WyckoffParams,
) -> WyckoffEvent | None:
    sow_thrust = bc_price - sow.price
    max_rally = sow.price + sow_thrust * p.lpsy_max_rally
    for i in range(sow.bar_index + 1, len(bars)):
        cond_price = bars[i].high <= max_rally and bars[i].high < tr.high_anchor * (
            1 - p.lpsy_below_tr_pct
        )
        if not cond_price:
            continue
        vol_ma = _vol_ma(i, bars, p.vol_ma_period)
        cond_vol = bars[i].volume < vol_ma * p.lps_volume_ratio
        sp_ma = _spread_ma(i, bars, p.spread_ma_period)
        cond_spread = _spread(bars[i]) < sp_ma * 0.90
        if cond_vol and cond_spread:
            return WyckoffEvent(
                name="LPSY",
                ts=bars[i].ts,
                price=bars[i].high,
                volume=bars[i].volume,
                bar_index=i,
                detail={"max_rally": round(max_rally, 4)},
            )
    return None


# ---------------------------------------------------------------------------
# STEP 7 — Phase classification
# ---------------------------------------------------------------------------


def classify_phase(schematic: WyckoffSchematic) -> str | None:
    """Classify A→E phase from the event set in *schematic*.

    Returns one of ``'A'``, ``'B'``, ``'C'``, ``'D'``, ``'E'``, or ``None``.
    """
    names = {e.name for e in schematic.events}
    has_sc_bc = bool(names & {"SC", "BC"})
    has_ar = "AR" in names
    has_st = "ST" in names
    has_spring_utad = bool(names & {"Spring", "UTAD"})
    has_sos_sow = bool(names & {"SOS", "SOW"})
    has_jac = "JAC" in names

    if not has_sc_bc:
        return None
    if not has_ar:
        return "A"
    if not has_st:
        return "A"
    if not has_spring_utad:
        return "B"
    if not has_sos_sow:
        return "C"

    # Accumulation Phase E: JAC (Jump Across the Creek) confirmed.
    if has_jac:
        return "E"

    # Distribution Phase E (spec STEP 7, line 1294): SOW closes below the TR
    # lower boundary by at least ``phase_e_breakout_pct``.  ``classify_phase``
    # has no access to ``bars``, so the breakout is read from the SOW/LPSY
    # event's recorded *close* (detail["close"]) — strictly a confirmed bar,
    # never a future one.
    if schematic.schematic_type == "distribution" and schematic.tr is not None:
        breakout_level = schematic.tr.low_anchor * (1.0 - _PHASE_E_BREAKOUT_PCT)
        for ev in schematic.events:
            if ev.name not in {"SOW", "LPSY"}:
                continue
            ev_close = ev.detail.get("close", ev.price)
            if isinstance(ev_close, int | float) and ev_close < breakout_level:
                return "E"

    # SOS/SOW (and optionally LPS/LPSY) confirmed but still inside the TR.
    return "D"


# ---------------------------------------------------------------------------
# STEP 8 — Confidence scoring
# ---------------------------------------------------------------------------


def score_phase_confidence(schematic: WyckoffSchematic) -> float:
    """Compute 0→1 phase confidence from event set and quality flags."""
    names = {e.name for e in schematic.events}
    events_dict: dict[str, Any] = {e.name: e for e in schematic.events}
    st_count = sum(1 for e in schematic.events if e.name == "ST")

    contributions: dict[str, float] = {
        "SC": 0.12,
        "AR": 0.08,
        "ST": 0.06,
        "Spring": 0.15,
        "Test": 0.10,
        "SOS": 0.12,
        "LPS": 0.10,
        "JAC": 0.08,
        "BUEC": 0.07,
        "BC": 0.12,
        "PSY": 0.05,
        "UT": 0.07,
        "UTAD": 0.15,
        "SOW": 0.12,
        "LPSY": 0.10,
    }

    score = 0.0
    for event_name, weight in contributions.items():
        if event_name not in names:
            continue
        if event_name == "ST":
            score += min(weight * st_count, weight * 2)
        elif event_name == "Spring":
            spring_ev = events_dict.get("Spring")
            if spring_ev and spring_ev.detail.get("spring_type") == 3:
                score += weight + 0.05
            else:
                score += weight
        else:
            score += weight

    # Volume asymmetry bonus
    if schematic.volume_asymmetry_correct:
        score += 0.07

    # OI confirmation bonus
    if schematic.oi_confirmation:
        score += 0.05

    # Guard: no Spring (trap 4): -0.15
    if schematic.schematic_type == "accumulation" and "Spring" not in names and "SOS" in names:
        score -= 0.15

    # Guard: ST volume >= SC volume: -0.10 per violated ST
    for ev in schematic.events:
        if ev.name == "ST" and ev.detail.get("vol_warn"):
            score -= 0.10

    return max(0.0, min(score, 1.0))


# ---------------------------------------------------------------------------
# Volume asymmetry helper
# ---------------------------------------------------------------------------


def _volume_asymmetry(
    bars: list[PriceBar],
    sc_idx: int,
    ar_idx: int,
    schematic_type: str,
    ratio: float,
) -> bool:
    tr_bars = bars[sc_idx : ar_idx + 1]
    up_bars = [b for b in tr_bars if b.close >= b.open]
    down_bars = [b for b in tr_bars if b.close < b.open]
    if not up_bars or not down_bars:
        return False
    adv_vol = statistics.mean(b.volume for b in up_bars)
    dec_vol = statistics.mean(b.volume for b in down_bars)
    if schematic_type == "accumulation":
        return (adv_vol / max(dec_vol, 1e-9)) >= ratio
    return (dec_vol / max(adv_vol, 1e-9)) >= ratio


# ---------------------------------------------------------------------------
# OI confirmation helper
# ---------------------------------------------------------------------------


def _oi_confirmation(
    oi_data: list[float] | None,
    spring: WyckoffEvent | None,
    sos: WyckoffEvent | None,
) -> bool:
    """Check if OI decreased during Spring and increased after SOS (accumulation)."""
    if oi_data is None or spring is None or sos is None:
        return False
    if spring.bar_index >= len(oi_data) or sos.bar_index >= len(oi_data):
        return False
    oi_at_spring = oi_data[spring.bar_index]
    oi_before_spring = oi_data[max(0, spring.bar_index - 1)]
    oi_at_sos = oi_data[sos.bar_index]
    oi_decreased = oi_at_spring < oi_before_spring
    oi_increased = oi_at_sos > oi_at_spring
    return oi_decreased and oi_increased


# ---------------------------------------------------------------------------
# Entry signal generation
# ---------------------------------------------------------------------------


def get_wyckoff_entry_signal(
    schematic: WyckoffSchematic,
    phase: str | None,
    bar: PriceBar,
    bars: list[PriceBar],
) -> dict[str, Any]:
    """Generate an entry-signal dict from the schematic, phase, and current bar.

    Returns keys: signal, trigger, ts, price, stop_price, target_price, reason.
    """
    avoid = {
        "signal": "avoid",
        "trigger": "",
        "ts": bar.ts,
        "price": bar.close,
        "stop_price": 0.0,
        "target_price": 0.0,
        "reason": "Schematic not established or confidence too low.",
    }
    if phase is None or schematic.phase_confidence < 0.35:
        return avoid
    if schematic.tr is None:
        return avoid

    tr = schematic.tr
    names = {e.name for e in schematic.events}
    events_dict = {e.name: e for e in schematic.events}
    cur_idx = len(bars) - 1
    atr = _atr(bars, cur_idx)

    # Accumulation signals
    if schematic.schematic_type == "accumulation":
        if phase in ("A", "B"):
            return {**avoid, "signal": "wait", "reason": "Phase A/B: TR forming."}
        if phase == "C":
            spring = events_dict.get("Spring")
            test = events_dict.get("Test")
            if test:
                stop = spring.price - 0.5 * atr if spring else tr.low_anchor - 0.5 * atr
                return {
                    "signal": "long",
                    "trigger": "Test",
                    "ts": bar.ts,
                    "price": test.price,
                    "stop_price": round(stop, 6),
                    "target_price": round(tr.high_anchor + tr.tr_range, 6),
                    "reason": "Phase C: Spring Test confirmed. Enter long.",
                }
            if spring and spring.detail.get("spring_type") == 3:
                stop = spring.price - 0.5 * atr
                return {
                    "signal": "long",
                    "trigger": "Spring",
                    "ts": bar.ts,
                    "price": bar.close,
                    "stop_price": round(stop, 6),
                    "target_price": round(tr.high_anchor + tr.tr_range, 6),
                    "reason": "Phase C: Spring Type 3 (low supply). Long ready.",
                }
            return {**avoid, "signal": "wait", "reason": "Phase C: awaiting Test."}
        if phase == "D":
            trigger = ""
            ref_low = tr.low_anchor
            if "BUEC" in names:
                ev = events_dict["BUEC"]
                trigger = "BUEC"
                ref_low = ev.price
            elif "LPS" in names:
                ev = events_dict["LPS"]
                trigger = "LPS"
                ref_low = ev.price
            elif "SOS" in names:
                ev = events_dict["SOS"]
                trigger = "SOS"
                ref_low = ev.price * 0.99
            stop = ref_low - 0.5 * atr
            return {
                "signal": "long",
                "trigger": trigger,
                "ts": bar.ts,
                "price": bar.close,
                "stop_price": round(stop, 6),
                "target_price": round(tr.high_anchor + tr.tr_range, 6),
                "reason": f"Phase D: {trigger} confirmed. Long entry.",
            }
        if phase == "E":
            return {
                "signal": "long",
                "trigger": "JAC",
                "ts": bar.ts,
                "price": bar.close,
                "stop_price": round(tr.high_anchor - 0.5 * atr, 6),
                "target_price": round(tr.high_anchor + tr.tr_range, 6),
                "reason": "Phase E: JAC. Hold long; avoid new entries.",
            }

    # Distribution signals
    if schematic.schematic_type == "distribution":
        if phase in ("A", "B"):
            return {**avoid, "signal": "wait", "reason": "Phase A/B: distribution TR forming."}
        if phase == "C":
            utad = events_dict.get("UTAD")
            if utad:
                stop = utad.price + 0.5 * atr
                return {
                    "signal": "short",
                    "trigger": "UTAD",
                    "ts": bar.ts,
                    "price": bar.close,
                    "stop_price": round(stop, 6),
                    "target_price": round(tr.low_anchor - tr.tr_range, 6),
                    "reason": "Phase C: UTAD trap. Short ready.",
                }
            return {**avoid, "signal": "wait", "reason": "Phase C: awaiting UTAD Test."}
        if phase == "D":
            trigger = ""
            ref_high = tr.high_anchor
            if "LPSY" in names:
                ev = events_dict["LPSY"]
                trigger = "LPSY"
                ref_high = ev.price
            elif "SOW" in names:
                ev = events_dict["SOW"]
                trigger = "SOW"
                ref_high = ev.price * 1.01
            stop = ref_high + 0.5 * atr
            return {
                "signal": "short",
                "trigger": trigger,
                "ts": bar.ts,
                "price": bar.close,
                "stop_price": round(stop, 6),
                "target_price": round(tr.low_anchor - tr.tr_range, 6),
                "reason": f"Phase D: {trigger} confirmed. Short entry.",
            }
        if phase == "E":
            return {
                "signal": "short",
                "trigger": "SOW_breakout",
                "ts": bar.ts,
                "price": bar.close,
                "stop_price": round(tr.low_anchor + 0.5 * atr, 6),
                "target_price": round(tr.low_anchor - tr.tr_range, 6),
                "reason": "Phase E: distribution breakdown. Hold short.",
            }

    return avoid


# ---------------------------------------------------------------------------
# Spring detection convenience wrapper
# ---------------------------------------------------------------------------


def detect_spring(
    bars: list[PriceBar],
    schematic: WyckoffSchematic,
    p: WyckoffParams | None = None,
) -> WyckoffEvent | None:
    """Public wrapper: detect Spring given an already-built schematic."""
    if p is None:
        p = WyckoffParams()
    if schematic.tr is None:
        return None
    ar_events = [e for e in schematic.events if e.name == "AR"]
    st_events = [e for e in schematic.events if e.name == "ST"]
    if not ar_events:
        return None
    return _find_spring(bars, ar_events[0], schematic.tr, st_events, p)


def detect_utad(
    bars: list[PriceBar],
    schematic: WyckoffSchematic,
    p: WyckoffParams | None = None,
) -> WyckoffEvent | None:
    """Public wrapper: detect UTAD given an already-built schematic."""
    if p is None:
        p = WyckoffParams()
    if schematic.tr is None:
        return None
    ar_events = [e for e in schematic.events if e.name == "AR"]
    st_events = [e for e in schematic.events if e.name == "ST"]
    if not ar_events:
        return None
    return _find_utad(bars, ar_events[0], schematic.tr, st_events, p)


# ---------------------------------------------------------------------------
# Top-level aggregator
# ---------------------------------------------------------------------------


def detect_wyckoff_schematic(
    bars: list[PriceBar],
    oi_data: list[float] | None = None,
    p: WyckoffParams | None = None,
) -> WyckoffSchematic:
    """Run the full Wyckoff pipeline over *bars* and return a WyckoffSchematic.

    Sorted by ts on entry. All detection is strictly no-lookahead.
    """
    if p is None:
        p = WyckoffParams()

    bars = sorted(bars, key=lambda b: b.ts)

    # --- False-positive guard: need minimum bars ---
    if len(bars) < max(p.climax_vol_lookback, p.vol_ma_period) + 5:
        return _empty_schematic()

    # --- Volume data quality check (guard 8) ---
    zero_vol_count = sum(1 for b in bars if not b.volume)
    vol_capped = (zero_vol_count / len(bars)) >= 0.20

    # --- Try accumulation first (SC-based) ---
    sc = _find_sc(bars, p)
    bc = _find_bc(bars, p)

    chosen_type: str | None = None
    climax: WyckoffEvent | None = None
    ar: WyckoffEvent | None = None
    tr: WyckoffTR | None = None

    if sc is not None:
        ar_candidate = _find_ar_accumulation(bars, sc, sc.price, p)
        if ar_candidate is not None:
            tr_range = ar_candidate.price - sc.price
            if sc.price > 0 and tr_range / sc.price >= p.tr_min_range_pct:
                chosen_type = "accumulation"
                climax = sc
                ar = ar_candidate
                tr = WyckoffTR(
                    low_anchor=sc.price,
                    high_anchor=ar_candidate.price,
                    tr_range=tr_range,
                )

    # Try distribution if accumulation didn't qualify
    if chosen_type is None and bc is not None:
        ar_candidate = _find_ar_distribution(bars, bc, bc.price, p)
        if ar_candidate is not None:
            tr_range = bc.price - ar_candidate.price
            if ar_candidate.price > 0 and tr_range / ar_candidate.price >= p.tr_min_range_pct:
                chosen_type = "distribution"
                climax = bc
                ar = ar_candidate
                tr = WyckoffTR(
                    low_anchor=ar_candidate.price,
                    high_anchor=bc.price,
                    tr_range=tr_range,
                )

    if chosen_type is None or climax is None or ar is None or tr is None:
        return _empty_schematic()

    events: list[WyckoffEvent] = [climax, ar]

    # --- ST ---
    st_list = _find_sts(bars, ar, climax, tr, chosen_type, p)
    events.extend(st_list)

    # --- Spring / UTAD ---
    spring: WyckoffEvent | None = None
    utad: WyckoffEvent | None = None
    test_ev: WyckoffEvent | None = None

    if chosen_type == "accumulation":
        spring = _find_spring(bars, ar, tr, st_list, p)
        if spring:
            events.append(spring)
            test_ev = _find_test_of_spring(bars, spring, tr, p)
            if test_ev:
                events.append(test_ev)
    else:
        utad = _find_utad(bars, ar, tr, st_list, p)
        if utad:
            events.append(utad)

    # --- SOS / SOW ---
    sos: WyckoffEvent | None = None
    sow: WyckoffEvent | None = None

    if chosen_type == "accumulation" and (spring or test_ev):
        ref = test_ev or spring
        assert ref is not None
        trough_close = ref.detail.get("close", tr.low_anchor)  # type: ignore[union-attr]
        if isinstance(trough_close, float):
            sos = _find_sos(bars, ref.bar_index + 1, trough_close, p)
        if sos:
            events.append(sos)
    elif chosen_type == "distribution" and utad:
        peak_close = utad.detail.get("close", tr.high_anchor)
        if isinstance(peak_close, float):
            sow = _find_sow(bars, utad.bar_index + 1, peak_close, p)
        if sow:
            events.append(sow)

    # --- Creek / JAC / BUEC ---
    jac: WyckoffEvent | None = None
    buec: WyckoffEvent | None = None
    if chosen_type == "accumulation" and sos:
        creek_resistance = ar.price  # simplified: AR high as Creek
        jac = _find_jac(bars, sos, creek_resistance, p)
        if jac:
            events.append(jac)
            buec = _find_buec(bars, jac, creek_resistance, p)
            if buec:
                events.append(buec)

    # --- LPS / LPSY ---
    lps: WyckoffEvent | None = None
    lpsy: WyckoffEvent | None = None

    if chosen_type == "accumulation" and sos:
        spring_ref_price = spring.price if spring else tr.low_anchor
        lps = _find_lps(bars, sos, spring_ref_price, tr, p)
        if lps:
            events.append(lps)
    elif chosen_type == "distribution" and sow:
        bc_ref_price = climax.price if chosen_type == "distribution" else tr.high_anchor
        lpsy = _find_lpsy(bars, sow, bc_ref_price, tr, p)
        if lpsy:
            events.append(lpsy)

    # --- SOS-only guard (guard 5): remove invalid LPS ---
    if lps and not sos:
        events = [e for e in events if e.name != "LPS"]
        lps = None

    # --- Sort events by bar_index ---
    events.sort(key=lambda e: e.bar_index)

    # --- Volume asymmetry ---
    va = _volume_asymmetry(bars, climax.bar_index, ar.bar_index, chosen_type, p.vol_asymmetry_ratio)

    # --- OI confirmation ---
    oi_conf = _oi_confirmation(oi_data, spring, sos) if chosen_type == "accumulation" else False

    # --- Build schematic ---
    schematic = WyckoffSchematic(
        schematic_type=chosen_type,
        tr=tr,
        events=events,
        volume_asymmetry_correct=va,
        oi_confirmation=oi_conf,
    )

    # --- Phase & confidence ---
    phase = classify_phase(schematic)
    schematic.phase = phase

    confidence = score_phase_confidence(schematic)
    if vol_capped:
        confidence = min(confidence, 0.50)
    schematic.phase_confidence = confidence

    # --- Entry signal ---
    if bars:
        schematic.entry_signal = get_wyckoff_entry_signal(schematic, phase, bars[-1], bars)

    return schematic


def _empty_schematic() -> WyckoffSchematic:
    return WyckoffSchematic(
        schematic_type=None,
        tr=None,
        events=[],
        phase=None,
        phase_confidence=0.0,
        entry_signal={
            "signal": "avoid",
            "trigger": "",
            "ts": None,
            "price": 0.0,
            "stop_price": 0.0,
            "target_price": 0.0,
            "reason": "No schematic established.",
        },
    )
