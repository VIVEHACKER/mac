"""Fund exposure report: a descriptive risk/exposure view of the assembled barbell book.

HONEST FRAMING — read before changing anything:
This is a DESCRIPTIVE DIAGNOSTIC, not a risk model. It aggregates the already-assembled fund weights
(no covariance, no VaR, no factor model, no forward claim) so the user can SEE what the composed fund
holds before trusting/deploying it: sector concentration, which sleeve drove each name, and how
concentrated the book is. Factor (value/momentum/quality) exposure is deferred (needs per-name loadings
unified across the three sleeve engines).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.fund_book import FundBook

_TOL = 1e-9


@dataclass(frozen=True)
class SectorExposure:
    sector: str
    weight: float  # sum of fund_weight in this sector
    n_names: int


@dataclass(frozen=True)
class SleeveAttribution:
    sleeve: str
    weight: float  # sum of (cap-clipped) per-sleeve contributions across the book


@dataclass(frozen=True)
class FundExposureReport:
    sector_exposures: tuple[SectorExposure, ...]
    sleeve_attribution: tuple[SleeveAttribution, ...]
    n_positions: int
    invested: float
    reserve_cash: float
    top_name: str | None
    top_name_weight: float
    top_n_weight: float
    herfindahl: float
    effective_n: float
    max_sector: str | None
    max_sector_weight: float
    flags: tuple[str, ...]


def compute_exposure(
    book: FundBook,
    sectors: dict[str, str] | None = None,
    *,
    sector_warn: float = 0.40,
    top_n: int = 5,
) -> FundExposureReport:
    """Descriptive exposure of an assembled FundBook: sector mix, per-sleeve attribution (cap-clipped to
    sum to invested), name-concentration (Herfindahl effective-N, top-N). Missing sectors -> 'Unknown'."""
    if not 0.0 < sector_warn <= 1.0:
        raise ValueError(f"sector_warn must be in (0, 1] (got {sector_warn})")
    if top_n < 1:
        raise ValueError(f"top_n must be >= 1 (got {top_n})")
    sectors = sectors or {}

    # Sector exposure.
    sector_w: dict[str, float] = {}
    sector_n: dict[str, int] = {}
    for p in book.positions:
        sec = sectors.get(p.symbol) or "Unknown"
        sector_w[sec] = sector_w.get(sec, 0.0) + p.fund_weight
        sector_n[sec] = sector_n.get(sec, 0) + 1
    sector_exposures = tuple(
        SectorExposure(sec, sector_w[sec], sector_n[sec])
        for sec in sorted(sector_w, key=lambda s: (-sector_w[s], s))
    )

    # Sleeve attribution: scale each position's PRE-cap contributions to its actual (capped)
    # fund_weight so the total reconciles to invested exactly (the cap haircut is split across the
    # contributing sleeves proportionally).
    sleeve_w: dict[str, float] = {}
    for p in book.positions:
        raw = sum(c for _name, c in p.contributions)
        if raw <= 0.0:
            continue
        scale = p.fund_weight / raw
        for name, c in p.contributions:
            sleeve_w[name] = sleeve_w.get(name, 0.0) + c * scale
    sleeve_attribution = tuple(
        SleeveAttribution(name, sleeve_w[name])
        for name in sorted(sleeve_w, key=lambda s: (-sleeve_w[s], s))
    )

    # Concentration (weights as-is; reserve is real cash, not renormalised away).
    weights = sorted((p.fund_weight for p in book.positions), reverse=True)
    herfindahl = sum(w * w for w in weights)
    effective_n = (book.invested**2 / herfindahl) if herfindahl > _TOL else 0.0
    top_name = book.positions[0].symbol if book.positions else None
    top_name_weight = weights[0] if weights else 0.0
    top_n_weight = sum(weights[:top_n])

    max_sector = sector_exposures[0].sector if sector_exposures else None
    max_sector_weight = sector_exposures[0].weight if sector_exposures else 0.0

    flags: list[str] = []
    if not book.positions:
        flags.append("빈 북 — 포지션 없음")
    if max_sector_weight > sector_warn:
        flags.append(f"섹터 집중: {max_sector} {max_sector_weight:.1%} > {sector_warn:.0%}")
    if any(p.capped for p in book.positions):
        n_capped = sum(1 for p in book.positions if p.capped)
        flags.append(f"종목당 캡 바인딩: {n_capped}개 (초과분 reserve로)")
    if book.positions and effective_n < 5.0:
        flags.append(f"브레드스 낮음: 유효 종목수 {effective_n:.1f} < 5")

    return FundExposureReport(
        sector_exposures=sector_exposures,
        sleeve_attribution=sleeve_attribution,
        n_positions=len(book.positions),
        invested=book.invested,
        reserve_cash=book.reserve_cash,
        top_name=top_name,
        top_name_weight=top_name_weight,
        top_n_weight=top_n_weight,
        herfindahl=herfindahl,
        effective_n=effective_n,
        max_sector=max_sector,
        max_sector_weight=max_sector_weight,
        flags=tuple(flags),
    )


def format_exposure(report: FundExposureReport) -> str:
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("펀드 익스포저 리포트 (조립된 바벨 북의 리스크 뷰)")
    lines.append(
        "프레이밍: 서술적 진단 — 리스크 모델 아님(공분산/VaR/팩터 없음). 조립된 비중을 "
        "섹터·슬리브·집중도로 보여줄 뿐, 전망 주장 없음."
    )
    lines.append("=" * 78)
    lines.append(
        f"invested={report.invested:.1%}  reserve={report.reserve_cash:.1%}  n={report.n_positions}  "
        f"유효종목수={report.effective_n:.1f}  top={report.top_name}({report.top_name_weight:.1%})"
    )
    lines.append("-" * 78)
    lines.append("섹터:")
    for se in report.sector_exposures:
        lines.append(f"  {se.sector:<14}{se.weight * 100:>7.2f}%  ({se.n_names}종목)")
    lines.append("슬리브 기여:")
    for a in report.sleeve_attribution:
        lines.append(f"  {a.sleeve:<14}{a.weight * 100:>7.2f}%")
    if report.flags:
        lines.append("-" * 78)
        lines.append("플래그:")
        for f in report.flags:
            lines.append(f"  ⚠ {f}")
    return "\n".join(lines)
