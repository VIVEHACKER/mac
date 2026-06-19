"""Hunt basket: the fund's asymmetric-upside sleeve (~15% of the 50/50 barbell).

HONEST FRAMING — read before changing anything:
The alpha source here is the USER's discretionary conviction (track record:
8/10 high-conviction calls, up to 20x over 3y), NOT a validated model. The system
only (1) SURFACES candidates by signal events with conviction, a kill-thesis, and
risk flags for the human to confirm, and (2) ENFORCES survival guards (small
sizing, a per-name cap, breadth, zero leverage) so one name going to zero costs the
fund ~2-3% and a whole-sleeve wipeout ~15%.

Validate-before-trust (harder than the core): NONE of the user's 6 signals is
weight-eligible (insider is only *suggestive* — size-controlled 1y IC +0.128,
t≈2.2; net_issuance was REJECTED as null; foreign_flow is unvalidated; size/
re-rating/CEO/moat have no signal module). So this engine MUST NOT blend signals
into a score. Insider buying is the single primary screen + rank key; net_issuance
and foreign_flow are DESCRIPTIVE FLAGS only. This is a candidate surfacer for human
confirmation, NOT an auto-buy.

Inversion vs the core basket: the core EXCLUDES risky names (high-debt, diluters);
hunt does NOT — those may be the turnaround/hypergrowth being hunted, so risk is
shown as flags and managed by SMALL SIZING + a per-name cap + a fundamental
kill-thesis (0컷, no price stop), not by avoidance. Survival is a sizing property.
No Kelly: it needs a validated edge we do not have.
"""

from __future__ import annotations

import warnings
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from data.models import FundamentalRecord
from engine.compounder import _flags, compute_metrics
from engine.core_basket import _equal_weights_capped, _percentile_ranks
from strategies._base import StrategySignal

MIN_HOLDINGS_WARN: int = 3  # below this the hunt sleeve is degenerate -> warn (non-fatal)
DEFAULT_TARGET_N: int = 6
DEFAULT_MAX_PER_NAME: float = 0.40  # sleeve-relative; *0.15 sleeve ≈ 6% fund (the memory cap)
DEFAULT_SLEEVE_FRACTION: float = 0.15


@dataclass(frozen=True)
class HuntHolding:
    symbol: str
    weight: float  # sleeve-relative (sums to 1.0 across the basket)
    fund_weight: float  # weight * sleeve_fraction (fund-level)
    insider_score: float  # dollar-weighted insider conviction (the rank key)
    insider_reason: str
    signal_flags: tuple[str, ...]  # descriptive only: never affects weight or rank
    sector: str | None
    kill_thesis: str  # fundamental exit condition (NOT price)
    rationale: str


@dataclass(frozen=True)
class HuntBasket:
    holdings: tuple[HuntHolding, ...]
    as_of: date | None
    universe_size: int
    signal_eligible_count: int
    target_n: int
    max_per_name: float
    sleeve_fraction: float
    sleeve_total_fund_weight: float
    max_single_name_fund_loss: float
    excluded: tuple[tuple[str, str], ...]


# ----------------------------------------------------------------- screen
def _signal_eligible(
    insider_signals: dict[str, StrategySignal | None],
) -> tuple[list[str], list[tuple[str, str]]]:
    """Eligible = names with a long insider-buy signal event. The ONLY gate (no distress filters)."""
    eligible: list[str] = []
    excluded: list[tuple[str, str]] = []
    for symbol, sig in insider_signals.items():
        if sig is not None and sig.direction == "long":
            eligible.append(symbol)
        else:
            excluded.append((symbol, "primary 신호 없음 (no insider-buy event)"))
    return eligible, excluded


# ------------------------------------------------------------------- rank
def _cheapness_pcts(
    eligible: list[str],
    universe: dict[str, tuple[Sequence[FundamentalRecord], float]],
) -> dict[str, float | None]:
    """Percentile rank of cheapness (mean of -ps, -pb) over the eligible set. None when no data."""
    metrics: dict[str, dict[str, float | None]] = {}
    for s in eligible:
        u = universe.get(s)
        metrics[s] = compute_metrics(u[0], u[1]) if u else {}

    def _neg(key: str) -> list[float | None]:
        out: list[float | None] = []
        for s in eligible:
            v = metrics[s].get(key)
            out.append(-v if v is not None else None)
        return out

    p_ps = dict(zip(eligible, _percentile_ranks(_neg("ps")), strict=True))
    p_pb = dict(zip(eligible, _percentile_ranks(_neg("pb")), strict=True))
    out: dict[str, float | None] = {}
    for s in eligible:
        parts = [p for p in (p_ps[s], p_pb[s]) if p is not None]
        out[s] = sum(parts) / len(parts) if parts else None
    return out


def _rank_candidates(
    eligible: list[str],
    insider_signals: dict[str, StrategySignal | None],
    universe: dict[str, tuple[Sequence[FundamentalRecord], float]],
    *,
    foreign_flow: dict[str, StrategySignal | None] | None,  # accepted but NOT used for ranking
    capital_signals: dict[str, StrategySignal | None] | None,  # accepted but NOT used for ranking
) -> list[tuple[str, float, float | None]]:
    """Sort by insider_score desc, cheapness pct desc (weak tiebreaker), symbol asc.
    foreign_flow / capital_signals are accepted for signature parity but never affect the order.
    A name lacking fundamentals (cheapness None) is ranked NEUTRALLY (0.5 midpoint), not penalized
    to the back — hunt does not silently demote a high-conviction insider buy that has no pinned
    fundamentals (mirrors core_basket's neutral handling of missing metrics)."""
    cheap = _cheapness_pcts(eligible, universe)
    rows: list[tuple[str, float, float | None]] = []
    for s in eligible:
        sig = insider_signals[s]
        score = sig.score if sig is not None else 0.0
        rows.append((s, score, cheap[s]))
    rows.sort(key=lambda r: (-r[1], -(r[2] if r[2] is not None else 0.5), r[0]))
    return rows


# ------------------------------------------------------------------ flags
def _collect_flags(
    symbol: str,
    metrics: dict[str, float | None],
    *,
    foreign: StrategySignal | None,
    capital: StrategySignal | None,
) -> tuple[str, ...]:
    """Descriptive flags only (never affect rank or weight)."""
    flags: list[str] = []
    if foreign is not None:
        flags.append("외국인순매수" if foreign.direction == "long" else "외국인순매도⚠")
    if capital is not None:
        if "large raise" in capital.reason:
            flags.append("대규모조달⚠")
        elif capital.direction == "long":
            flags.append("자사주")
        else:
            flags.append("희석⚠")
    if metrics:
        flags.extend(_flags(metrics))
    return tuple(flags)


def _kill_thesis(insider_reason: str, distress_flags: tuple[str, ...]) -> str:
    """Fundamental (non-price) exit condition: insider reversal OR a hard distress flag. 0컷."""
    distress = [f for f in distress_flags if f in {"high-debt", "negative-fcf", "high-dilution"}]
    distress_txt = f" OR distress({','.join(distress)})" if distress else ""
    return f"진입 근거=내부자 매수 ({insider_reason}); 청산=내부자 순매도 전환{distress_txt}"


# ----------------------------------------------------------------- select
def _rationale(insider_score: float, signal_flags: tuple[str, ...]) -> str:
    base = f"내부자 고확신 ${insider_score:,.0f}"
    extra = [f for f in signal_flags if f in {"외국인순매수", "자사주"}]
    if extra:
        base += " +" + "+".join(extra)
    return base


def select_hunt_basket(
    insider_signals: dict[str, StrategySignal | None],
    universe: dict[str, tuple[Sequence[FundamentalRecord], float]],
    *,
    foreign_flow: dict[str, StrategySignal | None] | None = None,
    capital_signals: dict[str, StrategySignal | None] | None = None,
    sectors: dict[str, str] | None = None,
    target_n: int = DEFAULT_TARGET_N,
    max_per_name: float = DEFAULT_MAX_PER_NAME,
    sleeve_fraction: float = DEFAULT_SLEEVE_FRACTION,
    as_of: date | None = None,
) -> HuntBasket:
    if target_n < 1:
        raise ValueError("target_n must be >= 1")
    if not 0.0 < max_per_name <= 1.0:
        raise ValueError("max_per_name must be in (0, 1]")
    if not 0.0 < sleeve_fraction <= 1.0:
        raise ValueError("sleeve_fraction must be in (0, 1]")

    eligible, excluded = _signal_eligible(insider_signals)
    ranked = _rank_candidates(
        eligible,
        insider_signals,
        universe,
        foreign_flow=foreign_flow,
        capital_signals=capital_signals,
    )
    chosen = ranked[:target_n]
    weights = _equal_weights_capped([r[0] for r in chosen], max_per_name)

    holdings: list[HuntHolding] = []
    for symbol, insider_score, _cheap in chosen:
        u = universe.get(symbol)
        metrics = compute_metrics(u[0], u[1]) if u else {}
        flags = _collect_flags(
            symbol,
            metrics,
            foreign=(foreign_flow or {}).get(symbol),
            capital=(capital_signals or {}).get(symbol),
        )
        sig = insider_signals[symbol]
        reason = sig.reason if sig is not None else ""
        w = weights[symbol]
        holdings.append(
            HuntHolding(
                symbol=symbol,
                weight=w,
                fund_weight=w * sleeve_fraction,
                insider_score=insider_score,
                insider_reason=reason,
                signal_flags=flags,
                sector=(sectors or {}).get(symbol),
                kill_thesis=_kill_thesis(reason, flags),
                rationale=_rationale(insider_score, flags),
            )
        )
    if 0 < len(holdings) < MIN_HOLDINGS_WARN:
        warnings.warn(
            f"hunt basket has only {len(holdings)} holdings (< {MIN_HOLDINGS_WARN}); "
            "the sleeve is degenerate — few signal-eligible candidates.",
            stacklevel=2,
        )
    fund_weights = [h.fund_weight for h in holdings]
    return HuntBasket(
        holdings=tuple(holdings),
        as_of=as_of,
        universe_size=len(universe),
        signal_eligible_count=len(eligible),
        target_n=target_n,
        max_per_name=max_per_name,
        sleeve_fraction=sleeve_fraction,
        sleeve_total_fund_weight=sum(fund_weights),
        max_single_name_fund_loss=max(fund_weights, default=0.0),
        excluded=tuple(excluded),
    )


# ----------------------------------------------------------------- report
def format_hunt_basket(basket: HuntBasket) -> str:
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("헌트 바스켓 (비대칭 상방 슬리브 ~15%)")
    lines.append(
        "정직한 프레이밍: 알파=사용자 재량 확신, 시스템=후보 발굴+생존 가드. "
        "신호 미검증 → 자동매수 아님, 최종 픽은 사용자."
    )
    lines.append(
        "insider 매수=유일 primary 스크린/랭크, net_issuance·foreign_flow=서술 플래그(점수 블렌드 금지). "
        "생존=작은 사이징+종목당 캡+kill-thesis(0컷), 위험은 사이징으로 관리."
    )
    lines.append("=" * 78)
    asof = basket.as_of.isoformat() if basket.as_of else "latest"
    lines.append(
        f"as_of={asof}  universe={basket.universe_size}  "
        f"signal_eligible={basket.signal_eligible_count}  target_n={basket.target_n}  "
        f"cap={basket.max_per_name:.0%}(sleeve)  sleeve={basket.sleeve_fraction:.0%} of fund"
    )
    lines.append(
        f"생존 수치: 단일종목 0 → 펀드 {basket.max_single_name_fund_loss * 100:.1f}% 손실, "
        f"슬리브 전멸 → 펀드 {basket.sleeve_total_fund_weight * 100:.1f}% 손실  | "
        f"excluded={len(basket.excluded)}"
    )
    lines.append("-" * 78)
    lines.append(f"{'SYM':<8}{'W%':>7}{'FUND%':>7}{'INSIDER$':>14}  FLAGS / KILL-THESIS")
    for h in basket.holdings:
        flags = ",".join(h.signal_flags) if h.signal_flags else "-"
        lines.append(
            f"{h.symbol:<8}{h.weight * 100:>6.2f}{h.fund_weight * 100:>7.2f}"
            f"{h.insider_score:>14,.0f}  [{flags}] {h.kill_thesis}"
        )
    return "\n".join(lines)
