"""Momentum / IDEAL sleeve: single-as_of AQR momentum basket, the active-half momentum leg.

HONEST FRAMING — read before changing anything:
This sleeve DOES carry the project's one validated edge — 12-1 mega-cap AQR momentum, direction-robust
across regimes (+8.15%/yr walk-forward, but +size fragile: PBO 0.39, significant excess US-only). It is
NOT a new claim and NOT re-tuned here: it wires the validated config (top-7, 20% cap) as a fund leg,
reusing the SAME ranking (strategies.factor_aqr.rank_aqr_factors) and the SAME weighting
(engine.momentum_weights.weights_from_picks) the deployed paper-drill builds. Fidelity to the validated
portfolio is the whole point — do not reimplement the weighting here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from engine.fund_book import SleeveTarget
from engine.momentum_weights import build_pricebars, weights_from_picks
from strategies.factor_aqr import rank_aqr_factors

if TYPE_CHECKING:  # heavy/optional deps used only for typing
    import pandas as pd

    from data.models import FundamentalRecord


@dataclass(frozen=True)
class MomentumHolding:
    symbol: str
    weight: float  # sleeve-relative weight in [0, cap]
    composite: float  # AQR z-sum (value + momentum + quality), higher = better
    value: float  # raw earnings yield
    momentum: float  # raw 12-1 (lookback) return
    quality: float  # raw mean(ROE, FCF yield)
    rank: int  # 1-based rank in the full cross-section (1 = top)
    rationale: str


@dataclass(frozen=True)
class MomentumBasket:
    holdings: tuple[MomentumHolding, ...]
    as_of: date
    universe_size: int  # symbols offered
    eligible_count: int  # names with enough history + fundamentals to rank
    top_n: int
    cap: float
    excluded: tuple[tuple[str, str], ...]  # (symbol, reason) for names dropped before selection


def select_momentum_basket(
    prices: pd.DataFrame,
    fundamentals_by_symbol: dict[str, FundamentalRecord],
    symbols: Sequence[str],
    *,
    as_of: date,
    top_n: int = 7,
    cap: float = 0.20,
    lookback: int = 126,
) -> MomentumBasket:
    """Build the momentum sleeve at one PIT `as_of`: rank the universe by the AQR composite, take the
    top-N, and weight via the validated weights_from_picks (inverse-vol, per-name cap). PIT: bars/vol
    slice `<= as_of`; the caller passes only fundamentals with `asof_ts <= as_of`.

    Empty universe or zero eligible names -> empty basket. weights_from_picks governs the cap feasibility
    (it raises if `len(picks) * cap < 1.0` — a degenerate universe too small for the cap)."""
    if top_n < 1:
        raise ValueError(f"top_n must be >= 1 (got {top_n})")
    if not 0.0 < cap <= 1.0:
        raise ValueError(f"cap must be in (0, 1] (got {cap})")
    # Normalize to uppercase: prices columns + fundamentals keys follow the yfinance/MEGACAPS
    # uppercase convention. Without this, lowercase symbols silently miss prices.columns and
    # vol_estimate falls back to 0.30 for EVERY name -> equal weights instead of the validated
    # inverse-vol (a silent corruption in the default top-7 path). Found by adversarial review.
    symbols = [s.upper() for s in symbols]

    excluded: list[tuple[str, str]] = []
    bars_by_symbol: dict[str, list] = {}
    for sym in symbols:
        bars = build_pricebars(prices, sym, as_of)
        if bars:
            bars_by_symbol[sym] = bars
        else:
            excluded.append((sym, "데이터 부족 (<260 바 또는 가격 없음)"))

    ranked = rank_aqr_factors(bars_by_symbol, fundamentals_by_symbol, lookback=lookback)
    ranked_symbols = {r.symbol for r in ranked}
    for sym in bars_by_symbol:
        if sym not in ranked_symbols:
            excluded.append((sym, "펀더멘털 없음 또는 lookback 바 부족"))

    picks = ranked[:top_n]
    weights = weights_from_picks(picks, prices, as_of, cap=cap) if picks else {}

    holdings: list[MomentumHolding] = []
    for i, fs in enumerate(ranked):
        if fs.symbol not in weights:
            continue
        holdings.append(
            MomentumHolding(
                symbol=fs.symbol,
                weight=weights[fs.symbol],
                composite=fs.composite,
                value=fs.value,
                momentum=fs.momentum,
                quality=fs.quality,
                rank=i + 1,
                rationale=f"AQR 모멘텀 {i + 1}위 (composite={fs.composite:.2f}, 12-1={fs.momentum:.1%})",
            )
        )
    holdings.sort(key=lambda h: (-h.weight, h.symbol))

    return MomentumBasket(
        holdings=tuple(holdings),
        as_of=as_of,
        universe_size=len(symbols),
        eligible_count=len(ranked),
        top_n=top_n,
        cap=cap,
        excluded=tuple(excluded),
    )


def momentum_sleeve_target(basket: MomentumBasket, *, fraction: float = 0.25) -> SleeveTarget:
    """A SleeveTarget for fund_book: the active-half momentum leg at the policy `fraction` (25%),
    weights = the basket's sleeve-relative weights. Empty basket -> fraction sleeve with no weights
    (all of `fraction` falls to reserve in assemble_fund_book)."""
    return SleeveTarget("momentum", fraction, {h.symbol: h.weight for h in basket.holdings})


def format_momentum_basket(basket: MomentumBasket) -> str:
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("모멘텀 / IDEAL 슬리브 (액티브 절반의 12-1 메가캡 AQR 모멘텀 레그)")
    lines.append(
        "프레이밍: 검증된 엣지(방향 robust·+8.15%/yr WF·크기 fragile). 새 주장 아님 — 검증된 "
        "config(top-7, 20%캡)를 펀드 레그로 배선, 동일 랭킹·동일 가중치."
    )
    lines.append("=" * 78)
    lines.append(
        f"as_of={basket.as_of}  universe={basket.universe_size}  eligible={basket.eligible_count}  "
        f"top_n={basket.top_n}  cap={basket.cap:.0%}  n={len(basket.holdings)}"
    )
    lines.append("-" * 78)
    lines.append(f"{'SYM':<8}{'WEIGHT':>8}{'RANK':>6}{'COMPOSITE':>11}  RATIONALE")
    for h in basket.holdings:
        lines.append(
            f"{h.symbol:<8}{h.weight * 100:>7.2f}%{h.rank:>6}{h.composite:>11.2f}  {h.rationale}"
        )
    return "\n".join(lines)
