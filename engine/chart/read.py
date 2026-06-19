"""Confluence aggregator — fuses all 11 chart-reading detectors into an entry decision.

This is the top of the ``engine/chart`` stack.  It runs every applicable detector over the
*latest* bar's state, builds a weighted directional vote map across three layers
(HTF bias / LTF trigger / derived condition), scores confluence, applies the hard-VETO
rules, and decides one of ENTER_NOW / SCALE_IN / WAIT_FOR_PULLBACK / AVOID.

Spec: docs/CHART_READING.md "진입 타이밍 컨플루언스 프레임워크" + "구현 모듈 매핑" (lines 2309-2736).

No lookahead: the decision at the final bar uses only ``bars[0..-1]``.  Every detector this
module calls is itself causal, so feeding the full series and reading the latest state is
safe.  Stock inputs (no ``order_book`` / ``oi_records``) degrade gracefully — those votes are
simply absent from the signal map (``signal_active = 0``), exactly as the spec's worked
example 2 (KOSPI large-cap) describes.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

from data.models import (
    CryptoFundingRecord,
    OpenInterestRecord,
    OrderBookSnapshot,
    PriceBar,
)
from engine.chart.candles import CandlePattern, detect_candlestick_patterns
from engine.chart.fvg import FVGResult, run_fvg
from engine.chart.liquidity import LiquidityResult, analyze_liquidity
from engine.chart.open_interest import OISignal, analyze_open_interest
from engine.chart.order_block import OrderBlock, detect_order_blocks, score_order_block
from engine.chart.orderbook import OrderBookSignal, analyze_order_book
from engine.chart.patterns import ChartPattern, detect_chart_patterns
from engine.chart.structure import (
    MarketStructure,
    compute_trend_bias,
    detect_swing_structure,
)
from engine.chart.types import (
    ChartRead,
    EntryContext,
    EntryState,
    OBIRegime,
    SignalContribution,
    TrendBias,
    confluence_score,
    decide_entry_state,
    signals_to_map,
)
from engine.chart.volume import VolumeBarResult, analyse_volume
from engine.chart.volume_profile import VolumeProfile, build_volume_profile
from engine.chart.wyckoff import (
    WyckoffSchematic,
    classify_phase,
    detect_wyckoff_schematic,
    get_wyckoff_entry_signal,
    score_phase_confidence,
)

# ---------------------------------------------------------------------------
# Layer tags (for reporting / grouping)
# ---------------------------------------------------------------------------

LAYER_HTF = "htf"
LAYER_LTF = "ltf"
LAYER_DERIVED = "derived"

# Base weights (docs/CHART_READING.md "기본 가중치 참조 테이블").
_W_STRUCTURE = 0.30
_W_STRUCTURE_COMBO = 0.40
# A-1 검증(CHARTBLOOM_VALIDATION_RESULTS.md): FVG 미동반 CHoCH(반전)는 forward 음수(t=-2.3@+24,
# n=1591). 동일방향 FVG가 없는 CHoCH-driven 구조 가중을 이 값으로 캡한다.
_W_STRUCTURE_NOFVG_CHOCH = 0.15
# 게이트 토글. forward-OOS 재확인 전 보수적 기본 ON(음의 신호 약화 방향이라 false-ENTER만 감소).
# 끄려면 False. (테스트는 read._CHOCH_FVG_GATE monkeypatch)
_CHOCH_FVG_GATE = True
_W_SWEEP = 0.30
_W_SWEEP_SOLO = 0.10
_W_VOLUME_PROFILE = 0.20
_W_VOLUME_PROFILE_STALE = 0.10
_W_FVG_STRONG = 0.65
_W_FVG_NORMAL = 0.45
_W_FVG_WEAK = 0.25
_W_OB_CONF = 0.65
_W_OB_SOLO = 0.45
_W_OB_BREAKER = 0.30
_W_CANDLE_TRIPLE = 0.45
_W_CANDLE_DUAL = 0.35
_W_CANDLE_SINGLE = 0.25
_W_CANDLE_DOJI = 0.15
_W_VOLUME_CONFIRMED = 0.75
_W_VOLUME_VDU = 0.65
_W_VOLUME_OBV = 0.45
_W_PATTERN_BREAKOUT = 0.65
_W_PATTERN_PRE = 0.40
_W_OBI = 0.25
_W_OBI_HIGH_VOL = 0.10
_W_OI_BULL = 0.70
_W_OI_EXTREME = 0.90
_W_WYCKOFF = 0.75

_VOLUME_PROFILE_STALE_SESSIONS = 20

# Mean-reversion location gate (docs/CHART_VALIDATION.md): on mean-reverting assets
# (crypto 4h) ENTER signals fired in the PREMIUM half of the recent range chase strength
# that reverts (premium ACT −0.76%/10봉, discount ACT +1.0%/10봉, IC −0.21). When
# ``mean_reversion=True`` we veto premium-chase longs (and discount-chase shorts).
_RANGE_LOCATION_N = 50
_PREMIUM_CHASE_THRESHOLD = 0.6


@dataclass
class _Features:
    """Loose bag of detector outputs, used to build the signal map and report."""

    structure: MarketStructure | None = None
    liquidity: LiquidityResult | None = None
    volume_profile: VolumeProfile | None = None
    fvgs: FVGResult | None = None
    order_blocks: list[OrderBlock] | None = None
    candles: list[CandlePattern] | None = None
    volume: list[VolumeBarResult] | None = None
    patterns: list[ChartPattern] | None = None
    order_book: OrderBookSignal | None = None
    oi: OISignal | None = None
    wyckoff: WyckoffSchematic | None = None


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------


def _atr14(bars: list[PriceBar], period: int = 14) -> float:
    """Wilder-free simple ATR over the last ``period`` bars (no lookahead)."""
    n = len(bars)
    if n < 2:
        return 0.0
    trs: list[float] = []
    start = max(1, n - period)
    for j in range(start, n):
        tr = max(
            bars[j].high - bars[j].low,
            abs(bars[j].high - bars[j - 1].close),
            abs(bars[j].low - bars[j - 1].close),
        )
        trs.append(tr)
    return statistics.mean(trs) if trs else (bars[-1].high - bars[-1].low)


def _bias_dir(bias: TrendBias) -> int:
    if bias is TrendBias.BULLISH:
        return 1
    if bias is TrendBias.BEARISH:
        return -1
    return 0


def _intended_sign(direction: str) -> int:
    return 1 if direction == "long" else -1


def _zone_dir_token(direction: str) -> str:
    return "bullish" if direction == "long" else "bearish"


def _range_position(bars: list[PriceBar], n: int = _RANGE_LOCATION_N) -> float:
    """Where the last close sits in the trailing ``n``-bar high/low range (0=low, 1=high).

    Causal: uses only ``bars`` (up to the decision bar). Premium when high, discount when low.
    """
    seg = bars[-n:] if len(bars) >= n else bars
    hi = max(b.high for b in seg)
    lo = min(b.low for b in seg)
    close = bars[-1].close
    return (close - lo) / (hi - lo) if hi > lo else 0.5


def _price_in_zone(price: float, low: float, high: float) -> bool:
    return low <= price <= high


# ---------------------------------------------------------------------------
# Detector runner
# ---------------------------------------------------------------------------


def _run_detectors(
    bars: list[PriceBar],
    *,
    order_book: OrderBookSnapshot | None,
    oi_records: list[OpenInterestRecord] | None,
    funding_records: list[CryptoFundingRecord] | None,
) -> _Features:
    """Run every applicable detector over ``bars`` and return their latest-state outputs.

    Detectors that need data we do not have (order book, open interest) are skipped, so the
    corresponding votes are absent from the signal map (stock mode degrades gracefully).
    """
    feat = _Features()

    try:
        feat.structure = detect_swing_structure(bars)
    except (ValueError, IndexError):
        feat.structure = None

    try:
        feat.liquidity = analyze_liquidity(bars)
    except (ValueError, IndexError):
        feat.liquidity = None

    if bars:
        try:
            feat.volume_profile = build_volume_profile(bars)
        except (ValueError, IndexError):
            feat.volume_profile = None

    try:
        feat.fvgs = run_fvg(bars)
    except (ValueError, IndexError):
        feat.fvgs = None

    try:
        feat.order_blocks = detect_order_blocks(bars)
    except (ValueError, IndexError):
        feat.order_blocks = None

    try:
        feat.candles = detect_candlestick_patterns(bars)
    except (ValueError, IndexError):
        feat.candles = None

    try:
        feat.volume = analyse_volume(bars)
    except (ValueError, IndexError):
        feat.volume = None

    try:
        feat.patterns = detect_chart_patterns(bars)
    except (ValueError, IndexError):
        feat.patterns = None

    if order_book is not None:
        try:
            feat.order_book = analyze_order_book(order_book)
        except (ValueError, IndexError, ZeroDivisionError):
            feat.order_book = None

    if oi_records:
        try:
            oi_signals = analyze_open_interest(bars, oi_records, funding_records)
            feat.oi = oi_signals[-1] if oi_signals else None
        except (ValueError, IndexError, ZeroDivisionError):
            feat.oi = None

    try:
        feat.wyckoff = detect_wyckoff_schematic(bars)
    except (ValueError, IndexError):
        feat.wyckoff = None

    return feat


# ---------------------------------------------------------------------------
# HTF bias layer
# ---------------------------------------------------------------------------


def _has_supporting_fvg(feat: _Features, bias: TrendBias) -> bool:
    """bias 방향과 같은 미완화 active FVG가 존재하는가 — CHoCH를 만든 임펄스의 흔적.

    A-1 검증(CHARTBLOOM_VALIDATION_RESULTS.md): FVG 미동반 CHoCH 단독은 forward 음수였음.
    """
    res = feat.fvgs
    if res is None:
        return False
    want = "bullish" if bias is TrendBias.BULLISH else "bearish"
    for z in res.active_fvgs:
        if z.direction == want and not z.mitigated and z.mitigation_type != "full":
            return True
    return False


def _structure_vote(feat: _Features, direction: str) -> SignalContribution | None:
    ms = feat.structure
    if ms is None:
        return None
    bias = ms.trend_bias
    if bias is TrendBias.RANGING:
        # RANGING forces structure weight to 0 (spec: 계층1 게이트).
        return SignalContribution(
            name="structure",
            weight=0.0,
            direction=0,
            layer=LAYER_HTF,
            note="RANGING — 구조 중립",
        )
    weight = _W_STRUCTURE
    note = "추세 구조 정렬"
    # internal_CHoCH + BOS combo upgrades the weight.
    int_types = {e.event_type for e in ms.events}
    has_int_choch = any(t.endswith("CHoCH") and t.startswith("internal") for t in int_types)
    has_int_bos = any(t.endswith("BOS") and t.startswith("internal") for t in int_types)
    if has_int_choch and has_int_bos:
        weight = _W_STRUCTURE_COMBO
        note = "internal CHoCH+BOS 콤보"
    # A-1 게이트: 최신 구조정의 이벤트가 CHoCH(반전)인데 동일방향 FVG 미동반이면 가중 캡.
    # 근거: 검증서 A-1 — FVG 없는 CHoCH 단독은 음의 기대값. (forward-OOS 재확인 전 기본 ON)
    if _CHOCH_FVG_GATE:
        defining = [e for e in ms.events if e.event_type.endswith(("CHoCH", "BOS"))]
        if defining:
            latest = max(defining, key=lambda e: e.bar_index)
            if (
                latest.event_type.endswith("CHoCH")
                and not _has_supporting_fvg(feat, bias)
                and weight > _W_STRUCTURE_NOFVG_CHOCH
            ):
                weight = _W_STRUCTURE_NOFVG_CHOCH
                note += " · FVG 미동반 CHoCH 가중 캡(A-1)"
    d = _bias_dir(bias)
    aligned = 1 if d == _intended_sign(direction) else -1
    return SignalContribution(
        name="structure",
        weight=weight,
        direction=aligned,
        layer=LAYER_HTF,
        note=note,
    )


def _sweep_vote(feat: _Features, direction: str) -> SignalContribution | None:
    liq = feat.liquidity
    if liq is None or not liq.sweeps:
        return None
    intended = _intended_sign(direction)
    # SSL sweep → bullish bias; BSL sweep → bearish bias.
    last_sweep = liq.sweeps[-1]
    sweep_dir = 1 if last_sweep.side == "SSL" else -1
    has_mss = bool(liq.mss_events)
    weight = _W_SWEEP if has_mss else _W_SWEEP_SOLO
    note = "유동성 스윕 + MSS 확인" if has_mss else "유동성 스윕 단독(MSS 미확인)"
    aligned = 1 if sweep_dir == intended else -1
    return SignalContribution(
        name="liquidity_sweep",
        weight=weight,
        direction=aligned,
        layer=LAYER_HTF,
        note=note,
    )


def _volume_profile_vote(
    feat: _Features, bars: list[PriceBar], direction: str
) -> SignalContribution | None:
    vp = feat.volume_profile
    if vp is None or vp.degenerate:
        return None
    price = bars[-1].close
    weight = _W_VOLUME_PROFILE
    note = f"프로파일 shape={vp.shape}"
    # D-shape with price inside the value area → neutralised to 0.10 (spec gate).
    in_va = vp.val <= price <= vp.vah
    if vp.shape == "D" and in_va:
        weight = _W_VOLUME_PROFILE_STALE
        note = "D-shape VA 내부 — 중립"
    # P-shape favours longs (support below), b-shape favours shorts (resistance above).
    if vp.shape == "P":
        vp_dir = 1
    elif vp.shape == "b":
        vp_dir = -1
    else:
        # D / B — supportive of the prevailing direction if price is reacting off value.
        vp_dir = _intended_sign(direction) if not in_va else 0
    if vp_dir == 0:
        return SignalContribution(
            name="volume_profile",
            weight=weight,
            direction=0,
            layer=LAYER_HTF,
            note=note,
        )
    aligned = 1 if vp_dir == _intended_sign(direction) else -1
    return SignalContribution(
        name="volume_profile",
        weight=weight,
        direction=aligned,
        layer=LAYER_HTF,
        note=note,
    )


# ---------------------------------------------------------------------------
# LTF trigger layer
# ---------------------------------------------------------------------------


def _fvg_vote(feat: _Features, bars: list[PriceBar], direction: str) -> SignalContribution | None:
    res = feat.fvgs
    if res is None:
        return None
    want = _zone_dir_token(direction)
    price = bars[-1].close
    best = None
    for z in res.active_fvgs:
        if z.direction != want or z.mitigated or z.mitigation_type == "ce":
            continue
        if z.formation_bar_idx == len(bars) - 1:
            continue  # same bar as formation — no trigger yet
        if not _price_in_zone(price, z.zone_low, z.zone_high):
            continue
        if best is None or abs(z.zone_mid - price) < abs(best.zone_mid - price):
            best = z
    if best is None:
        return None
    if best.strength == "strong":
        weight = _W_FVG_STRONG
    elif best.strength == "weak":
        weight = _W_FVG_WEAK
    else:
        weight = _W_FVG_NORMAL
    return SignalContribution(
        name="fvg",
        weight=weight,
        direction=1,
        layer=LAYER_LTF,
        note=f"가격이 {best.strength} FVG zone 내부",
    )


def _order_block_vote(
    feat: _Features, bars: list[PriceBar], direction: str
) -> SignalContribution | None:
    obs = feat.order_blocks
    if not obs:
        return None
    want = _zone_dir_token(direction)
    price = bars[-1].close
    htf_bias = feat.structure.trend_bias if feat.structure is not None else TrendBias.RANGING
    best = None
    best_score = 0.0
    for ob in obs:
        if ob.direction != want or ob.mitigated:
            continue
        # HTF 역방향 OB → weight 0 (skip).
        if htf_bias is TrendBias.BULLISH and ob.direction != "bullish":
            continue
        if htf_bias is TrendBias.BEARISH and ob.direction != "bearish":
            continue
        if not _price_in_zone(price, ob.zone_low, ob.zone_high):
            continue
        score = score_order_block(ob, htf_bias)
        if best is None or score > best_score:
            best, best_score = ob, score
    if best is None:
        return None
    if best.is_breaker:
        weight = _W_OB_BREAKER
        note = "브레이커 OB 리테스트"
    elif best.htf_confluence and best.has_fvg:
        weight = _W_OB_CONF
        note = "unmitigated OB + HTF confluence + FVG"
    else:
        weight = _W_OB_SOLO
        note = "단독 OB zone 진입"
    if best.visited:
        weight = max(0.0, weight - 0.15)
        note += " (재테스트 −0.15)"
    return SignalContribution(
        name="order_block",
        weight=weight,
        direction=1,
        layer=LAYER_LTF,
        note=note,
    )


def _candle_vote(
    feat: _Features, bars: list[PriceBar], direction: str
) -> SignalContribution | None:
    pats = feat.candles
    if not pats:
        return None
    last_idx = len(bars) - 1
    want = _zone_dir_token(direction)
    # Only the most recent (signal on last or penultimate bar) pattern is timing-relevant.
    candidate = None
    for p in reversed(pats):
        if p.bar_i >= last_idx - 1:
            candidate = p
            break
    if candidate is None or candidate.mitigated:
        return None
    if candidate.direction not in (want, "neutral"):
        # Opposing candle pattern — count as a counter-signal at single weight.
        return SignalContribution(
            name="candle",
            weight=_W_CANDLE_SINGLE,
            direction=-1,
            layer=LAYER_LTF,
            note=f"역방향 {candidate.pattern_name}",
        )
    # strength=1 → WAIT (no vote); strength 2 needs confirmation; strength 3 immediate.
    if candidate.strength <= 1:
        return None
    confirmed = False
    if candidate.bar_i < last_idx and candidate.signal_high is not None:
        nb = bars[-1]
        if want == "bullish":
            confirmed = nb.close > candidate.signal_high
        else:
            confirmed = candidate.signal_low is not None and nb.close < candidate.signal_low
    if candidate.strength == 2 and not confirmed:
        return None
    if candidate.strength >= 3:
        weight = _W_CANDLE_TRIPLE
    elif "doji" in candidate.pattern_name or candidate.pattern_name == "spinning_top":
        weight = _W_CANDLE_DOJI
    else:
        weight = _W_CANDLE_DUAL
    direction_token = 1 if candidate.direction == want else 0
    if direction_token == 0:
        return None
    return SignalContribution(
        name="candle",
        weight=weight,
        direction=1,
        layer=LAYER_LTF,
        note=f"{candidate.pattern_name} (strength={candidate.strength})",
    )


def _volume_vote(feat: _Features, direction: str) -> SignalContribution | None:
    vols = feat.volume
    if not vols:
        return None
    last = vols[-1]
    want_bull = direction == "long"
    # Absorption against the trade direction → AVOID-grade counter vote.
    if last.evr_label == "absorption" and want_bull:
        return SignalContribution(
            name="volume",
            weight=_W_VOLUME_CONFIRMED,
            direction=-1,
            layer=LAYER_LTF,
            note="absorption — 롱 부적합",
        )
    if want_bull:
        if last.no_supply_confirmed and last.cmf_signal in ("bullish", "strong"):
            return SignalContribution(
                name="volume",
                weight=_W_VOLUME_CONFIRMED,
                direction=1,
                layer=LAYER_LTF,
                note="no_supply confirmed + CMF bullish",
            )
        if last.vdu_zone_end and last.rvol_class in ("elevated", "spike", "climax"):
            return SignalContribution(
                name="volume",
                weight=_W_VOLUME_VDU,
                direction=1,
                layer=LAYER_LTF,
                note="VDU 브레이크아웃",
            )
        if last.obv_divergence == "bullish":
            return SignalContribution(
                name="volume",
                weight=_W_VOLUME_OBV,
                direction=1,
                layer=LAYER_LTF,
                note="OBV 강세 다이버전스",
            )
    else:
        if last.no_demand_confirmed and last.cmf_signal == "bearish":
            return SignalContribution(
                name="volume",
                weight=_W_VOLUME_CONFIRMED,
                direction=1,
                layer=LAYER_LTF,
                note="no_demand confirmed + CMF bearish",
            )
        if last.obv_divergence == "bearish":
            return SignalContribution(
                name="volume",
                weight=_W_VOLUME_OBV,
                direction=1,
                layer=LAYER_LTF,
                note="OBV 약세 다이버전스",
            )
    return None


def _pattern_vote(feat: _Features, direction: str) -> SignalContribution | None:
    pats = feat.patterns
    if not pats:
        return None
    want = _zone_dir_token(direction)
    candidate = None
    for p in pats:
        if p.direction != want or p.mitigated:
            continue
        if p.strength < 0.4:
            continue
        if candidate is None or p.strength > candidate.strength:
            candidate = p
    if candidate is None:
        return None
    if candidate.ts_breakout is not None:
        weight = _W_PATTERN_BREAKOUT
        note = f"{candidate.pattern_type} 브레이크아웃"
    else:
        weight = _W_PATTERN_PRE
        note = f"{candidate.pattern_type} pre-breakout"
    return SignalContribution(
        name="chart_pattern",
        weight=weight,
        direction=1,
        layer=LAYER_LTF,
        note=note,
    )


# ---------------------------------------------------------------------------
# Derived condition layer
# ---------------------------------------------------------------------------


def _obi_vote(feat: _Features, direction: str) -> SignalContribution | None:
    ob = feat.order_book
    if ob is None:
        return None
    want_bull = direction == "long"
    weight = _W_OBI
    # zscore within ±1.0 → WAIT (neutralise to 0 weight).
    if ob.obi_zscore is not None and abs(ob.obi_zscore) <= 1.0:
        return SignalContribution(
            name="obi",
            weight=0.0,
            direction=0,
            layer=LAYER_DERIVED,
            note="OBI zscore ±1.0 이내 — WAIT",
        )
    if want_bull:
        favorable = (
            ob.regime is OBIRegime.STRONG_BID
            and ob.delta_vamp > 0
            and not ob.spread_is_wide
            and not ob.wall_is_suspect
        )
        obi_dir = 1 if favorable else (-1 if ob.regime is OBIRegime.STRONG_ASK else 0)
    else:
        favorable = (
            ob.regime is OBIRegime.STRONG_ASK
            and ob.delta_vamp < 0
            and not ob.spread_is_wide
            and not ob.wall_is_suspect
        )
        obi_dir = 1 if favorable else (-1 if ob.regime is OBIRegime.STRONG_BID else 0)
    if obi_dir == 0:
        return None
    return SignalContribution(
        name="obi",
        weight=weight,
        direction=obi_dir,
        layer=LAYER_DERIVED,
        note=f"OBI regime={ob.regime.value}",
    )


def _oi_vote(feat: _Features, direction: str) -> SignalContribution | None:
    oi = feat.oi
    if oi is None:
        return None
    from engine.chart.types import OIQuadrant

    want_bull = direction == "long"
    # SHORT_COVER / LONG_LIQ → WAIT (weight 0).
    if oi.quadrant in (OIQuadrant.SHORT_COVER, OIQuadrant.LONG_LIQ):
        return SignalContribution(
            name="open_interest",
            weight=0.0,
            direction=0,
            layer=LAYER_DERIVED,
            note=f"OI {oi.quadrant.value} — WAIT",
        )
    weight = _W_OI_BULL
    note = f"OI {oi.quadrant.value}"
    if oi.long_squeeze_extreme or oi.short_squeeze_extreme:
        weight = _W_OI_EXTREME
        note += " squeeze_extreme"
    if want_bull:
        oi_dir = 1 if oi.quadrant is OIQuadrant.BULL_TREND and oi.oi_buildup else 0
    else:
        oi_dir = 1 if oi.quadrant is OIQuadrant.BEAR_TREND and oi.oi_buildup else 0
    if oi_dir == 0:
        return None
    return SignalContribution(
        name="open_interest",
        weight=weight,
        direction=1,
        layer=LAYER_DERIVED,
        note=note,
    )


def _wyckoff_vote(
    feat: _Features, bars: list[PriceBar], direction: str
) -> SignalContribution | None:
    sch = feat.wyckoff
    if sch is None or sch.schematic_type is None:
        return None
    phase = classify_phase(sch)
    if phase in (None, "A", "B"):
        return None
    conf = score_phase_confidence(sch)
    if conf < 0.4:
        return None
    sig = get_wyckoff_entry_signal(sch, phase, bars[-1], bars)
    want_signal = "long" if direction == "long" else "short"
    if sig.get("signal") != want_signal:
        return None
    return SignalContribution(
        name="wyckoff",
        weight=_W_WYCKOFF,
        direction=1,
        layer=LAYER_DERIVED,
        note=f"Wyckoff Phase {phase} (conf={conf:.2f})",
    )


# ---------------------------------------------------------------------------
# Public: build_signal_map
# ---------------------------------------------------------------------------


def build_signal_map(
    feat: _Features,
    bars: list[PriceBar],
    direction: str,
) -> list[SignalContribution]:
    """Build the full list of weighted directional votes across all three layers.

    Each detector contributes at most one vote.  Detectors with no data or no active trigger
    contribute nothing (the spec's ``signal_active = 0``).  Zero-weight neutral votes are
    retained for reporting (they show *why* a concept was downgraded) but never affect the
    score, since ``confluence_score`` filters ``weight <= 0``.
    """
    votes: list[SignalContribution | None] = [
        # HTF bias layer
        _structure_vote(feat, direction),
        _sweep_vote(feat, direction),
        _volume_profile_vote(feat, bars, direction),
        # LTF trigger layer
        _fvg_vote(feat, bars, direction),
        _order_block_vote(feat, bars, direction),
        _candle_vote(feat, bars, direction),
        _volume_vote(feat, direction),
        _pattern_vote(feat, direction),
        # derived condition layer
        _obi_vote(feat, direction),
        _oi_vote(feat, direction),
        _wyckoff_vote(feat, bars, direction),
    ]
    return [v for v in votes if v is not None]


# ---------------------------------------------------------------------------
# Public: apply_hard_veto
# ---------------------------------------------------------------------------


def apply_hard_veto(
    feat: _Features,
    direction: str,
) -> tuple[bool, str | None]:
    """Evaluate the hard-VETO rules; a True result forces AVOID regardless of score.

    Rules (docs/CHART_READING.md "하드 VETO 조건"):
      1. ``swing_CHoCH`` against the intended direction — structural thesis invalidated.
      2. ``cascade_long`` (long) / ``cascade_short`` (short).
      3. ``fr_state = LONG_HEAVY`` (long) / ``SHORT_HEAVY`` (short) — overcrowding.
      4. HTF ``trend_bias = RANGING``.
    """
    from engine.chart.types import FundingState

    intended = _intended_sign(direction)

    ms = feat.structure
    if ms is not None:
        if ms.trend_bias is TrendBias.RANGING:
            return True, "HTF trend_bias=RANGING — 방향성 진입 보류"
        # Latest swing CHoCH event against the intended direction.
        swing_chochs = [
            e for e in ms.events if e.event_type.endswith("CHoCH") and e.structure_scope == "swing"
        ]
        if swing_chochs:
            latest = max(swing_chochs, key=lambda e: e.bar_index)
            ev_dir = 1 if latest.direction == "BULLISH" else -1
            if ev_dir == -intended:
                return True, "swing CHoCH가 진입 방향과 반대 — 구조 thesis 무효화"

    oi = feat.oi
    if oi is not None:
        if direction == "long" and oi.cascade_long:
            return True, "cascade_long=True — 롱 캐스케이드 청산 위험"
        if direction == "short" and oi.cascade_short:
            return True, "cascade_short=True — 숏 캐스케이드 청산 위험"
        if direction == "long" and oi.funding_state is FundingState.LONG_HEAVY:
            return True, "fr_state=LONG_HEAVY — 롱 과열(overcrowding)"
        if direction == "short" and oi.funding_state is FundingState.SHORT_HEAVY:
            return True, "fr_state=SHORT_HEAVY — 숏 과열(overcrowding)"

    return False, None


# ---------------------------------------------------------------------------
# Public: get_invalidation_level
# ---------------------------------------------------------------------------


def get_invalidation_level(
    state: EntryState,
    context: EntryContext,
    direction: str,
) -> float | None:
    """Compute the invalidation price for an entry decision per the spec's table.

    Returns ``None`` when no anchoring level is available (e.g. the relevant detector
    produced no sweep / OB / spring reference).
    """
    atr = context.atr14
    intended_long = direction == "long"

    if state is EntryState.ENTER_NOW:
        # Prefer the most specific basis available: spring > sweep > OB mitigation.
        if context.spring_low is not None and intended_long:
            return context.spring_low
        if context.sweep_extreme is not None:
            return (
                context.sweep_extreme - 0.5 * atr
                if intended_long
                else context.sweep_extreme + 0.5 * atr
            )
        if context.ob_mitigation_extreme is not None:
            return (
                context.ob_mitigation_extreme - 0.5 * atr
                if intended_long
                else context.ob_mitigation_extreme + 0.5 * atr
            )
        return None

    if state is EntryState.SCALE_IN:
        # BUEC / LPS (Wyckoff): TR_high × 0.97 below = range re-entry invalidation.
        if context.tr_high is not None:
            return context.tr_high * 0.97
        if context.ob_mitigation_extreme is not None:
            return (
                context.ob_mitigation_extreme - 0.5 * atr
                if intended_long
                else context.ob_mitigation_extreme + 0.5 * atr
            )
        return None

    if state is EntryState.WAIT_FOR_PULLBACK:
        # Bullish: HTF swing low (HL) close break; Bearish: HTF swing high (LH) close break.
        return context.htf_swing_low if intended_long else context.htf_swing_high

    return None


# ---------------------------------------------------------------------------
# Entry-zone & context assembly
# ---------------------------------------------------------------------------


def _nearest_entry_zone(
    feat: _Features, bars: list[PriceBar], direction: str
) -> tuple[float, float] | None:
    """Return the nearest unmitigated FVG/OB zone in the trade direction, or None."""
    want = _zone_dir_token(direction)
    price = bars[-1].close
    best: tuple[float, float] | None = None
    best_dist = math.inf

    res = feat.fvgs
    if res is not None:
        for z in res.active_fvgs:
            if z.direction != want or z.mitigated:
                continue
            dist = abs(z.zone_mid - price)
            if dist < best_dist:
                best, best_dist = (z.zone_low, z.zone_high), dist

    obs = feat.order_blocks
    if obs:
        for ob in obs:
            if ob.direction != want or ob.mitigated:
                continue
            dist = abs(ob.zone_mid - price)
            if dist < best_dist:
                best, best_dist = (ob.zone_low, ob.zone_high), dist

    return best


def _build_context(feat: _Features, bars: list[PriceBar], direction: str) -> EntryContext:
    atr = _atr14(bars)
    sweep_extreme: float | None = None
    liq = feat.liquidity
    if liq is not None and liq.sweeps:
        sweep_extreme = liq.sweeps[-1].wick_extreme

    ob_mit: float | None = None
    obs = feat.order_blocks
    want = _zone_dir_token(direction)
    if obs:
        candidates = [ob for ob in obs if ob.direction == want and not ob.mitigated]
        if candidates:
            ob_mit = candidates[-1].mitigation_extreme

    spring_low: float | None = None
    tr_high: float | None = None
    sch = feat.wyckoff
    if sch is not None and sch.tr is not None:
        tr_high = sch.tr.high_anchor
        for ev in sch.events:
            if ev.name == "Spring":
                spring_low = ev.price

    htf_swing_low: float | None = None
    htf_swing_high: float | None = None
    ms = feat.structure
    if ms is not None:
        lvls = ms.structure_levels
        last_hl = lvls.get("last_HL") if isinstance(lvls, dict) else None
        last_lh = lvls.get("last_LH") if isinstance(lvls, dict) else None
        if isinstance(last_hl, dict):
            htf_swing_low = last_hl.get("price")
        if isinstance(last_lh, dict):
            htf_swing_high = last_lh.get("price")

    return EntryContext(
        direction=direction,
        atr14=atr,
        sweep_extreme=sweep_extreme,
        ob_mitigation_extreme=ob_mit,
        spring_low=spring_low,
        tr_high=tr_high,
        htf_swing_low=htf_swing_low,
        htf_swing_high=htf_swing_high,
    )


# ---------------------------------------------------------------------------
# Public: read_chart
# ---------------------------------------------------------------------------


def read_chart(
    bars: list[PriceBar],
    *,
    htf_bars: list[PriceBar] | None = None,
    order_book: OrderBookSnapshot | None = None,
    oi_records: list[OpenInterestRecord] | None = None,
    funding_records: list[CryptoFundingRecord] | None = None,
    direction: str = "long",
    mean_reversion: bool = False,
    params: dict[str, object] | None = None,
) -> ChartRead:
    """Read a chart and return an entry decision fusing all 11 detectors.

    Parameters
    ----------
    bars:
        LTF price bars, ascending ts.  The decision is made at ``bars[-1]`` and never peeks
        beyond it (no lookahead).
    htf_bars:
        Optional higher-timeframe bars.  When supplied, the trend-bias / structure layer is
        derived from these instead of ``bars`` (HTF establishes directional context).
    order_book:
        Optional L2 snapshot (crypto).  Absent → the OBI vote is skipped.
    oi_records, funding_records:
        Optional open-interest / funding series (crypto perps).  Absent → OI vote skipped and
        OI-based hard-VETO rules cannot fire.
    direction:
        ``'long'`` or ``'short'`` — the trade thesis being evaluated.
    params:
        Reserved for future per-detector overrides (currently unused).
    """
    del params  # reserved
    if direction not in ("long", "short"):
        raise ValueError(f"direction must be 'long' or 'short', got {direction!r}")
    if not bars:
        raise ValueError("bars must not be empty")

    feat = _run_detectors(
        bars,
        order_book=order_book,
        oi_records=oi_records,
        funding_records=funding_records,
    )

    # HTF structure (and thus trend_bias) is preferably derived from htf_bars.
    htf_structure = None
    if htf_bars:
        try:
            htf_structure = detect_swing_structure(htf_bars)
        except (ValueError, IndexError):
            htf_structure = None
    if htf_structure is not None:
        feat.structure = htf_structure

    trend_bias = feat.structure.trend_bias if feat.structure is not None else TrendBias.RANGING
    if feat.structure is not None and not feat.structure.events:
        trend_bias = TrendBias.RANGING
    elif feat.structure is not None:
        trend_bias = compute_trend_bias(feat.structure.events)
        feat.structure.trend_bias = trend_bias

    contributions = build_signal_map(feat, bars, direction)
    signal_map = signals_to_map(contributions)
    score = confluence_score(signal_map)

    vetoed, veto_reason = apply_hard_veto(feat, direction)

    # Mean-reversion location gate: on mean-reverting assets, chasing the premium half of
    # the range is a validated loser — veto premium-chase longs / discount-chase shorts.
    range_pos = _range_position(bars)
    if mean_reversion and not vetoed:
        if direction == "long" and range_pos > _PREMIUM_CHASE_THRESHOLD:
            vetoed = True
            veto_reason = f"평균회귀 모드: 프리미엄 추격(range_pos={range_pos:.2f}) — 되돌림 대기"
        elif direction == "short" and range_pos < (1.0 - _PREMIUM_CHASE_THRESHOLD):
            vetoed = True
            veto_reason = (
                f"평균회귀 모드: 디스카운트 추격 숏(range_pos={range_pos:.2f}) — 되돌림 대기"
            )

    decision = decide_entry_state(score, veto=vetoed)

    context = _build_context(feat, bars, direction)
    invalidation = get_invalidation_level(decision, context, direction)
    entry_zone = _nearest_entry_zone(feat, bars, direction)

    reasons = _build_reasons(contributions, vetoed, veto_reason, score, decision)

    last = bars[-1]
    return ChartRead(
        symbol=last.symbol,
        market=last.market,
        timeframe=last.freq,
        asof=str(last.ts),
        direction=direction,
        decision=decision,
        confluence=round(score, 2),
        trend_bias=trend_bias,
        entry_zone=entry_zone,
        invalidation=invalidation,
        contributions=contributions,
        features={
            "atr14": context.atr14,
            "veto_reason": veto_reason or "",
            "n_active_votes": sum(1 for c in contributions if c.weight > 0),
            "range_pos": round(range_pos, 3),
            "mean_reversion": mean_reversion,
        },
        reasons=reasons,
        vetoed=vetoed,
    )


def _build_reasons(
    contributions: list[SignalContribution],
    vetoed: bool,
    veto_reason: str | None,
    score: float,
    decision: EntryState,
) -> list[str]:
    """Assemble human-readable Korean reason strings explaining each active vote."""
    reasons: list[str] = []
    if vetoed and veto_reason:
        reasons.append(f"[VETO] {veto_reason}")
    for c in contributions:
        if c.weight <= 0:
            reasons.append(f"[{c.layer}] {c.name}: 중립/하향 — {c.note}")
            continue
        sign = "+" if c.direction > 0 else ("-" if c.direction < 0 else "0")
        reasons.append(f"[{c.layer}] {c.name}: 가중치 {c.weight:.2f} 방향 {sign} — {c.note}")
    reasons.append(f"컨플루언스 점수 {score:.1f} → {decision.value}")
    return reasons


# ---------------------------------------------------------------------------
# Public: format_chart_read
# ---------------------------------------------------------------------------


def format_chart_read(read: ChartRead) -> str:
    """Render a ChartRead as a Korean terminal report (what the CLI prints)."""
    dir_kr = "롱" if read.direction == "long" else "숏"
    lines: list[str] = []
    lines.append("=" * 64)
    lines.append(f" 차트 리딩 — {read.symbol} ({read.market}) {read.timeframe} @ {read.asof}")
    lines.append("=" * 64)
    # The validation verdict travels WITH the output, not buried in docs: entry states
    # have no predictive edge (ENTER 적중률 38-42%, IC 전 horizon 음수 — CHART_VALIDATION.md).
    lines.append(" ⚠ ADVISORY — 검증 결과 진입 신호 엣지 없음. 진입 트리거로 사용 금지,")
    lines.append("   참고용 컨텍스트 전용 (docs/CHART_VALIDATION.md).")
    lines.append("-" * 64)
    lines.append(f" 방향        : {dir_kr}")
    lines.append(f" 결정        : {read.decision.value}")
    lines.append(f" 컨플루언스  : {read.confluence:.1f} / 100")
    lines.append(f" HTF 추세    : {read.trend_bias.value}")
    if read.vetoed:
        lines.append(" VETO        : 발동 (점수 무관 AVOID 고정)")

    lines.append("-" * 64)
    lines.append(" 개념별 투표 (계층 · 가중치 · 방향):")
    if not read.contributions:
        lines.append("   (활성 신호 없음)")
    else:
        for c in read.contributions:
            sign = "▲" if c.direction > 0 else ("▼" if c.direction < 0 else "·")
            lines.append(f"   [{c.layer:<7}] {c.name:<15} w={c.weight:.2f} {sign}  {c.note}")

    lines.append("-" * 64)
    if read.entry_zone is not None:
        lines.append(f" 진입 zone   : {read.entry_zone[0]:.4f} ~ {read.entry_zone[1]:.4f}")
    else:
        lines.append(" 진입 zone   : (없음)")
    if read.invalidation is not None:
        lines.append(f" 무효화 레벨 : {read.invalidation:.4f}")
    else:
        lines.append(" 무효화 레벨 : (산출 불가)")

    lines.append("-" * 64)
    lines.append(" 근거:")
    for r in read.reasons:
        lines.append(f"   - {r}")
    lines.append("=" * 64)
    return "\n".join(lines)
