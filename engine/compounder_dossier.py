"""Per-name evidence dossier for a compounder candidate."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from engine.compounder import CandidateScore

# Decimal metrics displayed as percent (multiply by 100 when formatting).
# Ratio metrics (fcf_conversion, debt_to_equity, pe, pfcf, ps, pb) are
# intentionally excluded and shown as-is.
_PCT = {
    "revenue_cagr",
    "revenue_growth_acceleration",
    "eps_growth",
    "operating_margin",
    "net_margin",
    "margin_trend",
    "roic",
    "fcf_margin",
    "share_growth",
}
_LABELS = {
    "revenue_cagr": "Revenue CAGR (3y)",
    "revenue_growth_acceleration": "Rev growth accel",
    "eps_growth": "EPS growth (3y)",
    "operating_margin": "Operating margin",
    "net_margin": "Net margin",
    "margin_trend": "Margin trend (slope)",
    "roic": "ROIC",
    "fcf_margin": "FCF margin",
    "fcf_conversion": "FCF conversion",
    "debt_to_equity": "Debt/Equity",
    "share_growth": "Share growth (dilution)",
    "pe": "P/E",
    "pfcf": "P/FCF",
    "ps": "P/S",
    "pb": "P/B",
}


@dataclass(frozen=True)
class Dossier:
    symbol: str
    archetype: str
    score: float
    metrics: dict[str, float | None]
    flags: tuple[str, ...]
    rationale: str
    sector: str = "unknown"
    alt_signals: dict[str, Any] = field(default_factory=dict)


def _rationale(candidate: CandidateScore) -> str:
    arch = candidate.best_archetype.replace("_", " ")
    comps = candidate.scores[candidate.best_archetype].components
    top = sorted(comps.items(), key=lambda kv: kv[1], reverse=True)[:3]
    drivers = ", ".join(f"{_LABELS.get(k, k)} (z={v:+.2f})" for k, v in top)
    flag_txt = (
        f" Flags: {', '.join(candidate.scores[candidate.best_archetype].flags)}."
        if candidate.scores[candidate.best_archetype].flags
        else ""
    )
    driven_by = f", driven by {drivers}" if drivers else ""
    return (
        f"{candidate.symbol} fits the '{arch}' archetype (score {candidate.best_score:.0f}/100)"
        f"{driven_by}.{flag_txt}"
    )


def build_dossier(candidate: CandidateScore, sector: str = "unknown") -> Dossier:
    best = candidate.scores[candidate.best_archetype]
    return Dossier(
        symbol=candidate.symbol,
        archetype=candidate.best_archetype,
        score=candidate.best_score,
        metrics=candidate.metrics,
        flags=best.flags,
        rationale=_rationale(candidate),
        sector=sector,
    )


def _fmt(key: str, value: float | None) -> str:
    if value is None:
        return "n/a"
    if key in _PCT:
        return f"{value * 100:+.1f}%"
    return f"{value:.2f}"


def format_dossier_markdown(d: Dossier) -> str:
    lines = [
        f"### {d.symbol} — {d.archetype.replace('_', ' ')} ({d.score:.0f}/100) [{d.sector}]",
        "",
        d.rationale,
        *(
            ["", "_FCF-based metrics excluded from scoring (not meaningful for financials)._"]
            if d.sector == "financials"
            else []
        ),
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]
    for key in _LABELS:
        if key in d.metrics:
            lines.append(f"| {_LABELS[key]} | {_fmt(key, d.metrics[key])} |")
    if d.flags:
        lines += ["", f"**Flags:** {', '.join(d.flags)}"]
    return "\n".join(lines)
