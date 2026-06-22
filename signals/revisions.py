"""Estimate-revision momentum signal — the "screen he looked at every day".

Learned from the @studying_stone X post / 휴먼스토리 video: the office-worker's edge was not a stock tip
but DAILY MONITORING OF CONSENSUS REVISIONS — analyst target prices, EPS estimates, and the breadth of
up/down revisions ("turning red from May" = upward revisions).

HONEST FRAMING — read before changing anything:
Estimate-revision momentum is a DOCUMENTED factor (EPS/target-price upward revisions predict near-term
excess returns — earnings-momentum / PEAD-adjacent). But this is a CANDIDATE signal, NOT a return-
predictor until forward-IC validated on the actual universe: analyst target prices carry a known
optimism/herding bias, coverage skews to large caps, and naive "upgrade => buy" generates many false
signals. So this emits a score + honest reason; whether it has edge is decided by `forward_ic` (rank-IC
vs forward returns), mirroring the insider-signal forward-IC gate. The X anecdote is survivorship-biased
(one success) — the edge claim rests on the factor + our own IC test, not the story.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from engine.ic import ICStats, ic_stats, spearman
from strategies._base import StrategySignal

DEFAULT_WEIGHTS: dict[str, float] = {"tp": 0.35, "eps": 0.40, "breadth": 0.25}
# EPS-revision weighted highest (cleanest revision signal); target price down-weighted for optimism bias.


@dataclass(frozen=True)
class EstimateRevision:
    symbol: str
    market: str
    as_of: date
    target_price: float | None
    target_price_prev: float | None
    eps_estimate: float | None
    eps_estimate_prev: float | None
    n_up: int
    n_down: int
    n_total: int


def _tp_chg(r: EstimateRevision) -> float:
    # tp 컴포넌트는 현재·이전 타겟이 모두 양수일 때만 정의됨 — 0/누락 타겟이 거짓 다운그레이드를
    # 만들지 않게 현재 타겟도 <=0 이면 미정의 처리(Codex).
    if (
        r.target_price is None
        or r.target_price_prev is None
        or r.target_price <= 0
        or r.target_price_prev <= 0
    ):
        return 0.0
    return (r.target_price - r.target_price_prev) / r.target_price_prev


def _eps_chg(r: EstimateRevision) -> float:
    if r.eps_estimate is None or r.eps_estimate_prev is None or r.eps_estimate_prev == 0:
        return 0.0
    return (r.eps_estimate - r.eps_estimate_prev) / abs(r.eps_estimate_prev)


def _breadth(r: EstimateRevision) -> float:
    if not r.n_total or r.n_total <= 0:
        return 0.0
    return (r.n_up - r.n_down) / r.n_total


def revision_signals(
    revisions: Sequence[EstimateRevision],
    *,
    weights: Mapping[str, float] | None = None,
    min_coverage: int = 3,
    up_threshold: float = 0.0,
    max_downgrades: int | None = None,
) -> list[StrategySignal]:
    """Score each name's consensus-revision momentum. Screen thin coverage (noise); optionally apply the
    method's signature DOWNGRADE FILTER (`max_downgrades`); blend target-price, EPS-estimate, and
    up/down-breadth changes (each guarded against div-by-zero / None). Returns StrategySignals sorted by
    score desc — a CANDIDATE signal, gated by forward_ic (see module header).

    `max_downgrades` is the video's signature rule ("파란색[하향] 하나라도 보이면 거른다"): set 0 to drop
    any name with a target-price/estimate downgrade in the window (more defensive than a continuous score
    given target-price optimism bias — a downgrade off an optimistic baseline is a stronger signal).
    Default None disables the filter (continuous breadth score only)."""
    if min_coverage < 1:
        raise ValueError(f"min_coverage must be >= 1 (got {min_coverage})")
    if max_downgrades is not None and max_downgrades < 0:
        raise ValueError(f"max_downgrades must be >= 0 or None (got {max_downgrades})")
    w = weights or DEFAULT_WEIGHTS

    out: list[StrategySignal] = []
    for r in revisions:
        if (r.n_total or 0) < min_coverage:
            continue  # thin coverage -> noise, screened out (not scored)
        if max_downgrades is not None and r.n_down > max_downgrades:
            continue  # downgrade filter (the video's "any 파란색 -> 거른다" rule)
        tp_chg = _tp_chg(r)
        eps_chg = _eps_chg(r)
        breadth = _breadth(r)
        score = (
            w.get("tp", 0.0) * tp_chg
            + w.get("eps", 0.0) * eps_chg
            + w.get("breadth", 0.0) * breadth
        )
        if score > up_threshold:
            direction = "up"
        elif score < -up_threshold:
            direction = "down"
        else:
            direction = "flat"
        reason = (
            f"목표가 {tp_chg:+.1%}, EPS추정 {eps_chg:+.1%}, "
            f"상향breadth {breadth:+.0%} (n={r.n_total})"
        )
        out.append(
            StrategySignal(
                symbol=r.symbol,
                market=r.market,
                as_of=r.as_of,
                score=score,
                direction=direction,
                reason=reason,
            )
        )
    out.sort(key=lambda s: (-s.score, s.symbol))
    return out


def forward_ic(scores: Mapping[str, float], forward_returns: Mapping[str, float]) -> float | None:
    """Cross-sectional Spearman rank-IC of the signal vs forward returns over the overlapping symbols —
    the validate-before-trust gate. None if < 3 overlapping names (per engine.ic.spearman). Aggregate
    per-date ICs across dates with engine.ic.ic_stats (the multi-period harness is deferred with the
    live data)."""
    syms = sorted(set(scores) & set(forward_returns))
    if len(syms) < 3:
        return None
    xs = [scores[s] for s in syms]
    ys = [forward_returns[s] for s in syms]
    return spearman(xs, ys)


# Validation variants (the H1–H3 hypotheses from the learning note). Each maps a name -> kwargs for
# revision_signals; running the forward-IC of each on the SAME data tests whether the edge is real and
# which formulation is strongest. Data-source-agnostic — feed EstimateRevision from any source.
DEFAULT_VARIANTS: dict[str, dict[str, Any]] = {
    "full": {},  # H1: the blended revision score predicts forward returns
    "eps_only": {"weights": {"eps": 1.0}},  # H3: EPS revision is the cleanest component
    "tp_only": {"weights": {"tp": 1.0}},  # H3: target price is weaker (optimism bias)
    "no_downgrade": {"max_downgrades": 0},  # H2: the downgrade filter is more robust
}


def revision_ic_report(
    snapshots: Mapping[date, Sequence[EstimateRevision]],
    forward_returns: Mapping[date, Mapping[str, float]],
    *,
    variants: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, ICStats]:
    """Forward-IC validation harness (validate-before-trust). For each variant (signal config), compute
    the per-date cross-sectional rank-IC of the signal vs the N-day forward returns and aggregate across
    dates via engine.ic.ic_stats. Returns {variant -> ICStats}. The live data feed (FMP / 와이즈리포트
    consensus -> EstimateRevision, and forward returns from prices) is the deferred seam — this harness is
    source-agnostic so any feed (or a backfill export) drives it. A variant whose mean IC is ~0 / negative
    is NOT trusted regardless of the source anecdote."""
    vmap = variants if variants is not None else DEFAULT_VARIANTS
    dates = sorted(set(snapshots) & set(forward_returns))
    report: dict[str, ICStats] = {}
    for name, kwargs in vmap.items():
        ics: list[float] = []
        for d in dates:
            sigs = revision_signals(snapshots[d], **kwargs)
            scores = {s.symbol: s.score for s in sigs}
            ic = forward_ic(scores, forward_returns[d])
            if ic is not None:
                ics.append(ic)
        report[name] = ic_stats(ics)
    return report
