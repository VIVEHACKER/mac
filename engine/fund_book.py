"""Fund book: the barbell assembler that composes the sleeve engines into ONE fund target.

HONEST FRAMING — read before changing anything:
This makes NO new alpha claim. It only (1) COMPOSES already-validated/screened sleeve targets (core
basket, hunt basket, momentum/IDEAL) at the user's barbell POLICY fractions — a user decision, not a
model output — and (2) enforces FUND-LEVEL risk rails: an 8% per-name hard cap, Σ(sleeve fractions)
≤ 1.0 (ZERO leverage), long-only non-negative weights. It invents no signal and reweights nothing
within a sleeve.

The barbell (project goal): long 50% = core ~35% + hunt ~15%; active 50% = momentum/IDEAL + the
user's discretionary trading + guards. The system generates targets for core / hunt / momentum; the
un-allocated remainder (the discretionary part of the active half) is RESERVE CASH the user fills with
their own trades. Cap overflow also falls to reserve — never silently redistributed (that would distort
the sleeve policy) and never leveraged away.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

_WEIGHT_SUM_TOL = 1e-9


@dataclass(frozen=True)
class SleeveTarget:
    name: str
    fraction: float  # fund-level fraction allocated to this sleeve, in [0, 1]
    weights: dict[str, float]  # symbol -> sleeve-relative weight (sums to <= 1.0; shortfall = cash)


@dataclass(frozen=True)
class FundPosition:
    symbol: str
    fund_weight: float
    contributions: tuple[tuple[str, float], ...]  # [(sleeve_name, fund-weight contribution), ...]
    capped: bool  # the per-name hard cap bound this name (overflow went to reserve)


@dataclass(frozen=True)
class FundBook:
    positions: tuple[FundPosition, ...]
    sleeve_fractions: tuple[tuple[str, float], ...]
    invested: float
    reserve_cash: float
    max_name_weight: float
    top_name_weight: float
    n_positions: int


def assemble_fund_book(
    sleeves: Sequence[SleeveTarget], *, max_name_weight: float = 0.08
) -> FundBook:
    """Compose sleeve targets into one fund book. fund_weight[sym] = Σ sleeve.weights[sym]*fraction,
    capped per name at max_name_weight (overflow -> reserve cash, no redistribution). Fail-closed on
    leverage (Σ fractions > 1), over-weighted sleeves, negative weights, and out-of-range inputs."""
    if not 0.0 < max_name_weight <= 1.0:
        raise ValueError("max_name_weight must be in (0, 1]")

    frac_sum = 0.0
    for s in sleeves:
        if not 0.0 <= s.fraction <= 1.0:
            raise ValueError(f"sleeve {s.name!r} fraction {s.fraction} out of range [0, 1]")
        frac_sum += s.fraction
        wsum = 0.0
        for sym, w in s.weights.items():
            if w < 0.0:
                raise ValueError(f"sleeve {s.name!r} has a negative weight for {sym} ({w})")
            wsum += w
        if wsum > 1.0 + _WEIGHT_SUM_TOL:
            raise ValueError(f"sleeve {s.name!r} weights sum to {wsum:.4f} > 1.0")
    if frac_sum > 1.0 + _WEIGHT_SUM_TOL:
        raise ValueError(f"sleeve fractions sum to {frac_sum:.4f} > 1.0 (leverage not allowed)")

    # Compose: a symbol present in several sleeves sums its contributions (preserve sleeve order).
    raw: dict[str, float] = {}
    contribs: dict[str, list[tuple[str, float]]] = {}
    for s in sleeves:
        for sym, w in s.weights.items():
            if w == 0.0:
                continue
            c = w * s.fraction
            raw[sym] = raw.get(sym, 0.0) + c
            contribs.setdefault(sym, []).append((s.name, c))

    positions: list[FundPosition] = []
    for sym, fw in raw.items():
        capped = fw > max_name_weight
        positions.append(
            FundPosition(
                symbol=sym,
                fund_weight=min(fw, max_name_weight),
                contributions=tuple(contribs[sym]),
                capped=capped,
            )
        )
    positions.sort(key=lambda p: (-p.fund_weight, p.symbol))

    invested = sum(p.fund_weight for p in positions)
    return FundBook(
        positions=tuple(positions),
        sleeve_fractions=tuple((s.name, s.fraction) for s in sleeves),
        invested=invested,
        reserve_cash=1.0 - invested,
        max_name_weight=max_name_weight,
        top_name_weight=positions[0].fund_weight if positions else 0.0,
        n_positions=len(positions),
    )


def format_fund_book(book: FundBook) -> str:
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("펀드 북 (50/50 바벨 조립 — core + hunt + momentum)")
    lines.append(
        "정직한 프레이밍: 알파 주장 없음. 검증된 슬리브 타겟을 사용자 바벨 정책 비중으로 "
        "조립만 하고, 펀드레벨 가드(종목당 캡·Σ비중≤1 무레버리지·롱온리)만 강제."
    )
    lines.append("리저브 현금 = 미할당 비중(재량 액티브 절반) + 캡 초과분. 재분배·레버리지 없음.")
    lines.append("=" * 78)
    fracs = "  ".join(f"{n}={f:.0%}" for n, f in book.sleeve_fractions)
    lines.append(
        f"sleeves: {fracs or '(none)'}  | invested={book.invested:.1%}  "
        f"reserve={book.reserve_cash:.1%}  cap={book.max_name_weight:.0%}  n={book.n_positions}"
    )
    lines.append("-" * 78)
    lines.append(f"{'SYM':<8}{'FUND%':>8}  {'CAP':>4}  PROVENANCE (sleeve→fund%)")
    for p in book.positions:
        prov = ", ".join(f"{n}→{c * 100:.2f}%" for n, c in p.contributions)
        cap = "⚠" if p.capped else "-"
        lines.append(f"{p.symbol:<8}{p.fund_weight * 100:>7.2f}  {cap:>4}  {prov}")
    return "\n".join(lines)
