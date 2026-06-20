"""Countercyclical bridge: rule-based dry-powder deployment into the core anchor.

HONEST FRAMING — read before changing anything:
This makes NO market-timing alpha claim. It does not predict bottoms. It is a rule-based dry-powder
deployment policy: when a market index has drawn down from its trailing peak AND the core anchor's own
valuations are cheap, it deploys a budget-capped slice of reserve cash into the EXISTING, already
value-screened core basket — buying the same durable names cheaper, in tranches (robust to being early).
It invents no signal, picks no new names, and scales the core's existing weights only. The value gate
is an AND (falling-knife guard): deployment is 0 whenever the gate is closed, however deep the drawdown.
Budget-capped and composed through fund_book's 8% per-name cap + zero-leverage rails.

The barbell budget (user policy): long 50% = core 35% + hunt 15%; active 50% = momentum/IDEAL 25% +
bridge dry powder (bridge_budget, default 15%) + discretionary reserve 10%. bridge_budget is the MAX
fund fraction the bridge may ever deploy — an explicit, overridable parameter.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from statistics import median
from typing import TYPE_CHECKING

from engine.fund_book import SleeveTarget

if TYPE_CHECKING:  # avoid pulling core_basket's heavier deps at runtime (only the type is needed)
    from engine.core_basket import CoreBasket

_TOL = 1e-9

# (drawdown_threshold, cumulative_fraction_of_budget), thresholds strictly ascending in (0, 1].
DEFAULT_LADDER: tuple[tuple[float, float], ...] = (
    (0.10, 1.0 / 3.0),
    (0.20, 2.0 / 3.0),
    (0.30, 1.0),
)


@dataclass(frozen=True)
class BridgeDeployment:
    deployed_fraction: float  # fund-level fraction to deploy now (0 <= x <= budget)
    budget: float  # bridge_budget: max fund fraction the bridge may ever deploy
    drawdown: float  # clamped market drawdown in [0, 1]
    value_gate_open: bool
    tranche_index: int  # rung reached, 0..len(ladder)
    n_tranches: int
    reason: str  # Korean one-liner


def market_drawdown(prices: Sequence[float]) -> float:
    """Peak-to-last drawdown over the GIVEN series (caller passes a trailing, PIT-sliced slice).

    (peak - last) / peak, clamped to [0, 1]. Empty series or non-positive peak -> ValueError
    (a degenerate price history must not silently read as 'no drawdown')."""
    if not prices:
        raise ValueError("market_drawdown: empty price series")
    peak = max(prices)
    if peak <= 0.0:
        raise ValueError(f"market_drawdown: non-positive peak {peak}")
    dd = (peak - prices[-1]) / peak
    return min(max(dd, 0.0), 1.0)


def _validate_ladder(ladder: tuple[tuple[float, float], ...]) -> None:
    if not ladder:
        raise ValueError("ladder must be non-empty")
    prev_t = 0.0
    prev_c = 0.0
    for t, c in ladder:
        if not 0.0 < t <= 1.0:
            raise ValueError(f"ladder threshold {t} out of (0, 1]")
        if t <= prev_t:
            raise ValueError(
                f"ladder thresholds must be strictly ascending (got {t} after {prev_t})"
            )
        if not 0.0 <= c <= 1.0 + _TOL:
            raise ValueError(f"ladder cumulative fraction {c} out of [0, 1]")
        if c < prev_c - _TOL:
            raise ValueError(
                f"ladder cumulative fractions must be non-decreasing (got {c} after {prev_c})"
            )
        prev_t, prev_c = t, c


def ladder_fraction(drawdown: float, ladder: tuple[tuple[float, float], ...]) -> float:
    """Cumulative budget fraction for the deepest ladder threshold <= drawdown (0 if below first)."""
    frac = 0.0
    for t, c in ladder:
        if drawdown >= t:
            frac = c
    return frac


def _rung_index(drawdown: float, ladder: tuple[tuple[float, float], ...]) -> int:
    rung = 0
    for i, (t, _c) in enumerate(ladder, start=1):
        if drawdown >= t:
            rung = i
    return rung


def compute_deployment(
    drawdown: float,
    value_gate_open: bool,
    *,
    budget: float,
    ladder: tuple[tuple[float, float], ...] = DEFAULT_LADDER,
) -> BridgeDeployment:
    """Map (drawdown, value gate) -> a budget-capped deployed fraction via the step ladder. Gate
    closed -> 0 (falling-knife guard). Fail-closed on out-of-range budget / malformed ladder."""
    if not 0.0 <= budget <= 1.0:
        raise ValueError(f"budget {budget} out of [0, 1]")
    _validate_ladder(ladder)
    dd = min(max(drawdown, 0.0), 1.0)
    n = len(ladder)
    if not value_gate_open:
        return BridgeDeployment(
            deployed_fraction=0.0,
            budget=budget,
            drawdown=dd,
            value_gate_open=False,
            tranche_index=0,
            n_tranches=n,
            reason=f"게이트 닫힘(코어 cheapness 미달) → dd={dd:.1%}여도 배치 0 (falling-knife 가드)",
        )
    frac = ladder_fraction(dd, ladder)
    rung = _rung_index(dd, ladder)
    deployed = budget * frac
    return BridgeDeployment(
        deployed_fraction=deployed,
        budget=budget,
        drawdown=dd,
        value_gate_open=True,
        tranche_index=rung,
        n_tranches=n,
        reason=(
            f"dd={dd:.1%} (tranche {rung}/{n}), 게이트 열림 → budget {budget:.1%}의 "
            f"{frac:.1%}={deployed:.1%} 배치"
        ),
    )


def default_value_gate(core_basket: CoreBasket, *, threshold: float = 0.55) -> bool:
    """True iff the median of the core holdings' present cheapness_pct >= threshold. None cheapness is
    ignored; an empty basket or all-None cheapness -> False (no assessable anchor -> gate closed,
    conservative)."""
    present = [h.cheapness_pct for h in core_basket.holdings if h.cheapness_pct is not None]
    if not present:
        return False
    return median(present) >= threshold


def bridge_sleeve_target(
    deployment: BridgeDeployment, core_weights: dict[str, float]
) -> SleeveTarget:
    """A SleeveTarget for fund_book whose fraction is the deployed dry powder and whose weights ARE the
    core's weights — the bridge scales the same anchor (no new names). Returned even at fraction 0
    ('armed, not deployed')."""
    return SleeveTarget("bridge", deployment.deployed_fraction, dict(core_weights))


def format_deployment(deployment: BridgeDeployment) -> str:
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("반순환 브릿지 (dry-powder 배치 — 코어 앵커 폭락매수)")
    lines.append(
        "정직한 프레이밍: 마켓타이밍 알파 주장 없음. 시장 하락 AND 코어 cheapness 동시 충족 시 "
        "budget 한도 내에서 가치-스크린된 코어를 tranche로 더 사는 규칙 기반 배치. 무레버리지."
    )
    lines.append("=" * 78)
    lines.append(
        f"drawdown={deployment.drawdown:.1%}  "
        f"gate={'열림' if deployment.value_gate_open else '닫힘'}  "
        f"tranche={deployment.tranche_index}/{deployment.n_tranches}  "
        f"budget={deployment.budget:.1%}  deployed={deployment.deployed_fraction:.1%}"
    )
    lines.append("-" * 78)
    lines.append(deployment.reason)
    return "\n".join(lines)
