"""Core basket: the fund's durable long-term anchor (~35% of the 50/50 barbell).

HONEST FRAMING — read before changing anything:
This basket makes NO factor-alpha claim. The project's terminal validation
(docs/COMPOUNDER_VALIDATION.md, engine/compounder.py:22-32) found that in this
mid/small-cap survivor universe over 3-5y horizons, NO single factor (gross /
net-quality / value) robustly predicts forward returns after regime+size+sector
controls. The one robust finding: net-margin / ROIC quality *reverse*-predicts.
So this engine (1) EXCLUDES net_margin and roic from ranking, (2) tilts toward
the directionally-supported-if-modest value (low ps/pb) + Novy-Marx gross
profitability (GP/assets), and (3) holds theses (winners are not trimmed until
the hard cap). Survival = diversification (12-15 names) + 8% hard cap + zero
leverage; diversification substitutes for vol-targeting. This is the boring,
durable anchor — asymmetric upside is the hunt basket's job, the validated
momentum edge is the separate IDEAL line. Do not turn this into an alpha claim.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from data.models import FundamentalRecord
from engine.compounder import (
    SECTOR_INVALID_METRICS,
    Z_CLIP,
    _flags,
    _zscores,
    compute_metrics,
)
from engine.significance import normal_cdf

MIN_PRESENT_METRICS: int = 5
MAX_DEBT_TO_EQUITY: float = 3.0
MAX_SHARE_GROWTH: float = 0.15


@dataclass(frozen=True)
class CoreHolding:
    symbol: str
    weight: float
    composite: float
    display_score: float
    cheapness_z: float | None
    gp_z: float | None
    sector: str | None
    flags: tuple[str, ...]
    rationale: str


@dataclass(frozen=True)
class CoreBasket:
    holdings: tuple[CoreHolding, ...]
    as_of: date | None
    universe_size: int
    eligible_count: int
    target_n: int
    max_weight: float
    excluded: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class RebalanceAction:
    symbol: str
    action: str
    target_weight: float
    reason: str


# ----------------------------------------------------------------- screen
def _apply_sector_nulls(
    metrics: dict[str, float | None], sector: str | None
) -> dict[str, float | None]:
    """Null sector-invalid metrics (e.g. FCF/GP for financials) before screening/ranking."""
    invalid = SECTOR_INVALID_METRICS.get(sector or "", frozenset())
    if not invalid:
        return metrics
    return {k: (None if k in invalid else v) for k, v in metrics.items()}


def _screen(
    universe: dict[str, tuple[Sequence[FundamentalRecord], float]],
    sectors: dict[str, str] | None,
) -> tuple[dict[str, dict[str, float | None]], list[tuple[str, str]]]:
    """Return (eligible {symbol: sector-nulled metrics}, excluded [(symbol, reason)])."""
    eligible: dict[str, dict[str, float | None]] = {}
    excluded: list[tuple[str, str]] = []
    for symbol, (records, price) in universe.items():
        sector = (sectors or {}).get(symbol)
        raw = compute_metrics(records, price)
        if not raw:
            excluded.append((symbol, "데이터 없음 (no fundamentals)"))
            continue
        metrics = _apply_sector_nulls(raw, sector)
        present = sum(1 for v in metrics.values() if v is not None)
        if present < MIN_PRESENT_METRICS:
            excluded.append((symbol, f"커버리지 부족 ({present}<{MIN_PRESENT_METRICS} metrics)"))
            continue
        if metrics.get("ps") is None and metrics.get("pb") is None:
            excluded.append((symbol, "밸류 앵커 없음 (no ps/pb)"))
            continue
        is_financial = sector == "financials"
        if not is_financial:
            fcf_m = metrics.get("fcf_margin")
            if fcf_m is not None and fcf_m < 0:
                excluded.append((symbol, f"현금소진 (fcf_margin {fcf_m:.2f}<0)"))
                continue
            de = metrics.get("debt_to_equity")
            if de is not None and de > MAX_DEBT_TO_EQUITY:
                excluded.append((symbol, f"과다부채 (debt/equity {de:.1f}>{MAX_DEBT_TO_EQUITY})"))
                continue
        sg = metrics.get("share_growth")
        if sg is not None and sg > MAX_SHARE_GROWTH:
            excluded.append((symbol, f"연쇄 희석 (share_growth {sg:.0%}>{MAX_SHARE_GROWTH:.0%})"))
            continue
        eligible[symbol] = metrics
    return eligible, excluded


# ------------------------------------------------------------------- rank
def _clip(z: float) -> float:
    return max(-Z_CLIP, min(Z_CLIP, z))


def _rank_eligible(
    eligible: dict[str, dict[str, float | None]],
    *,
    w_value: float,
    w_gp: float,
) -> list[tuple[str, float, float | None, float | None]]:
    """Cross-sectional value-led ranking. Returns [(symbol, composite, cheapness_z, gp_z)]
    sorted by (-composite, symbol). net_margin/roic are deliberately not consulted."""
    symbols = list(eligible)
    if not symbols:
        return []

    def col(key: str) -> list[float | None]:
        return [eligible[s].get(key) for s in symbols]

    z_ps = dict(zip(symbols, _zscores(col("ps")), strict=True))
    z_pb = dict(zip(symbols, _zscores(col("pb")), strict=True))
    z_gp = dict(zip(symbols, _zscores(col("gross_profitability")), strict=True))

    rows: list[tuple[str, float, float | None, float | None]] = []
    for s in symbols:
        # cheapness = mean of available negated value Zs (lower multiple = cheaper = higher)
        chs = [-z for z in (z_ps[s], z_pb[s]) if z is not None]
        cheapness = _clip(sum(chs) / len(chs)) if chs else None
        gp = z_gp[s]
        gp_clipped = _clip(gp) if gp is not None else None

        contrib, wsum = 0.0, 0.0
        if cheapness is not None:
            contrib += w_value * cheapness
            wsum += abs(w_value)
        if gp_clipped is not None:
            contrib += w_gp * gp_clipped
            wsum += abs(w_gp)
        composite = contrib / wsum if wsum > 0 else 0.0
        rows.append((s, composite, cheapness, gp_clipped))

    rows.sort(key=lambda r: (-r[1], r[0]))
    return rows


# ----------------------------------------------------------------- weight
def _equal_weights_capped(symbols: list[str], max_weight: float) -> dict[str, float]:
    """Equal-weight 1/n clamped to max_weight. Under equal weighting the cap is all-or-none:
    n >= 1/max_weight -> 1/n (sums to 1.0); n < 1/max_weight -> each max_weight (sums < 1.0,
    remainder = sleeve cash, since there is no uncapped name to redistribute to)."""
    n = len(symbols)
    if n == 0:
        return {}
    w = min(1.0 / n, max_weight)
    return dict.fromkeys(symbols, w)


def _rationale(cheapness_z: float | None, gp_z: float | None, flags: tuple[str, ...]) -> str:
    bits: list[str] = []
    if cheapness_z is not None:
        bits.append("저평가" if cheapness_z > 0 else "고평가")
    if gp_z is not None:
        bits.append("고GP" if gp_z > 0 else "저GP")
    base = "+".join(bits) if bits else "중립"
    if flags:
        base += f" ⚠{','.join(flags)}"
    return base


def select_core_basket(
    universe: dict[str, tuple[Sequence[FundamentalRecord], float]],
    *,
    sectors: dict[str, str] | None = None,
    target_n: int = 13,
    max_weight: float = 0.08,
    w_value: float = 0.6,
    w_gp: float = 0.4,
    as_of: date | None = None,
) -> CoreBasket:
    if target_n < 1:
        raise ValueError("target_n must be >= 1")
    if not 0.0 < max_weight <= 1.0:
        raise ValueError("max_weight must be in (0, 1]")
    eligible, excluded = _screen(universe, sectors)
    ranked = _rank_eligible(eligible, w_value=w_value, w_gp=w_gp)
    chosen = ranked[:target_n]
    weights = _equal_weights_capped([r[0] for r in chosen], max_weight)
    holdings = []
    for symbol, composite, cheapness_z, gp_z in chosen:
        flags = _flags(eligible[symbol])
        holdings.append(
            CoreHolding(
                symbol=symbol,
                weight=weights[symbol],
                composite=composite,
                display_score=normal_cdf(composite) * 100.0,
                cheapness_z=cheapness_z,
                gp_z=gp_z,
                sector=(sectors or {}).get(symbol),
                flags=flags,
                rationale=_rationale(cheapness_z, gp_z, flags),
            )
        )
    return CoreBasket(
        holdings=tuple(holdings),
        as_of=as_of,
        universe_size=len(universe),
        eligible_count=len(eligible),
        target_n=target_n,
        max_weight=max_weight,
        excluded=tuple(excluded),
    )


# -------------------------------------------------------------- rebalance
def _cap_redistribute(raw: dict[str, float], max_weight: float) -> dict[str, float]:
    """Normalize raw positive weights to sum 1.0 with an iterative hard cap: capped names are
    fixed at max_weight, the remainder is split proportionally among uncapped names until stable."""
    symbols = [s for s, w in raw.items() if w > 0]
    if not symbols:
        return {}
    weights = {s: raw[s] for s in symbols}
    capped: set[str] = set()
    for _ in range(len(symbols) + 1):
        free = [s for s in symbols if s not in capped]
        if not free:
            break
        fixed = sum(max_weight for _ in capped)
        remaining = 1.0 - fixed
        free_total = sum(weights[s] for s in free)
        if free_total <= 0 or remaining <= 0:
            for s in free:
                weights[s] = 0.0
            break
        scaled = {s: weights[s] / free_total * remaining for s in free}
        newly = [s for s in free if scaled[s] > max_weight]
        if not newly:
            for s in free:
                weights[s] = scaled[s]
            break
        for s in newly:
            capped.add(s)
    for s in capped:
        weights[s] = max_weight
    return weights


def rebalance_core_basket(
    held: dict[str, float],
    target: CoreBasket,
    eligible: set[str],
    *,
    target_n: int = 13,
    max_weight: float = 0.08,
) -> tuple[CoreBasket, tuple[RebalanceAction, ...]]:
    target_by_symbol = {h.symbol: h for h in target.holdings}
    actions: list[RebalanceAction] = []

    # 1. Held names: keep if still eligible, drop if thesis broke.
    keep: list[str] = []
    for symbol in held:
        if symbol in eligible:
            keep.append(symbol)
        else:
            actions.append(RebalanceAction(symbol, "drop", 0.0, "스크린 탈락 (thesis break)"))

    # 2. Fill remaining slots from fresh top-ranked names not already kept.
    for h in target.holdings:
        if len(keep) >= target_n:
            break
        if h.symbol not in keep:
            keep.append(h.symbol)
            actions.append(RebalanceAction(h.symbol, "add", 0.0, "신규 편입 (top rank)"))

    # 3. Raw weights: held winners keep grown weight (capped); others equal-weight target.
    eq = 1.0 / max(len(keep), 1)
    raw: dict[str, float] = {}
    for symbol in keep:
        if symbol in held and held[symbol] > eq:
            raw[symbol] = held[symbol]  # let the winner run (pre-cap)
        else:
            raw[symbol] = eq
    final = _cap_redistribute(raw, max_weight)

    # 4. Emit hold / trim_to_cap actions for kept names (adds already recorded with 0 weight).
    add_syms = {a.symbol for a in actions if a.action == "add"}
    holdings: list[CoreHolding] = []
    for symbol in keep:
        w = final.get(symbol, 0.0)
        src = target_by_symbol.get(symbol)
        if symbol not in add_syms:
            if symbol in held and held[symbol] > max_weight:
                actions.append(RebalanceAction(symbol, "trim_to_cap", w, "캡 초과 → 8% 축소"))
            else:
                actions.append(RebalanceAction(symbol, "hold", w, "여전히 적격"))
        else:
            for i, a in enumerate(actions):
                if a.symbol == symbol and a.action == "add":
                    actions[i] = RebalanceAction(symbol, "add", w, a.reason)
                    break
        holdings.append(
            CoreHolding(
                symbol=symbol,
                weight=w,
                composite=src.composite if src else 0.0,
                display_score=src.display_score if src else 0.0,
                cheapness_z=src.cheapness_z if src else None,
                gp_z=src.gp_z if src else None,
                sector=src.sector if src else None,
                flags=src.flags if src else (),
                rationale=src.rationale if src else "보유 유지",
            )
        )
    new_basket = CoreBasket(
        holdings=tuple(holdings),
        as_of=target.as_of,
        universe_size=target.universe_size,
        eligible_count=target.eligible_count,
        target_n=target_n,
        max_weight=max_weight,
        excluded=target.excluded,
    )
    return new_basket, tuple(actions)


# ----------------------------------------------------------------- report
def format_core_basket(basket: CoreBasket) -> str:
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("코어 바스켓 (장기 슬리브 ~35% durable anchor)")
    lines.append(
        "정직한 프레이밍: 팩터 알파 주장 없음. 검증 결론상 어떤 단일 팩터도 "
        "regime+size+sector 통제를 견디며 예측 못 함."
    )
    lines.append(
        "유일 견고 발견 = net-margin/ROIC 역예측(나쁨) → 랭킹에서 제외. "
        "밸류(저 ps/pb)+GP/assets 틸트, 등가중 8%캡, thesis-hold, 레버리지0."
    )
    lines.append("=" * 72)
    asof = basket.as_of.isoformat() if basket.as_of else "latest"
    lines.append(
        f"as_of={asof}  universe={basket.universe_size}  "
        f"eligible={basket.eligible_count}  target_n={basket.target_n}  "
        f"cap={basket.max_weight:.0%}  excluded={len(basket.excluded)}"
    )
    lines.append("-" * 72)
    lines.append(
        f"{'SYM':<8}{'W%':>7}{'SCORE':>7}{'CHEAP_Z':>9}{'GP_Z':>7}  {'SECTOR':<12}RATIONALE"
    )
    for h in basket.holdings:
        cz = f"{h.cheapness_z:+.2f}" if h.cheapness_z is not None else "  n/a"
        gz = f"{h.gp_z:+.2f}" if h.gp_z is not None else "  n/a"
        sec = (h.sector or "-")[:12]
        lines.append(
            f"{h.symbol:<8}{h.weight * 100:>6.2f}{h.display_score:>7.1f}"
            f"{cz:>9}{gz:>7}  {sec:<12}{h.rationale}"
        )
    return "\n".join(lines)
