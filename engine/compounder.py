"""Multi-archetype compounder scoring. Cross-sectional Z-scores within the
supplied universe are mapped to 0-100 archetype scores via the normal CDF."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from statistics import mean, pstdev

from data.models import FundamentalRecord
from engine import compounder_metrics as cm
from engine.significance import normal_cdf

ARCHETYPES = ("profitable_compounder", "hypergrowth_disruptor", "value_turnaround")

# (metric_key, weight). Negative weight = lower-is-better. Weights per archetype
# sum to 1.0 over present metrics (renormalized when some are missing).
_WEIGHTS: dict[str, list[tuple[str, float]]] = {
    "profitable_compounder": [
        ("roic", 0.30),
        ("fcf_margin", 0.25),
        ("margin_trend", 0.20),
        ("revenue_cagr", 0.15),
        ("share_growth", -0.10),
    ],
    "hypergrowth_disruptor": [
        ("revenue_cagr", 0.40),
        ("revenue_growth_acceleration", 0.35),
        ("margin_trend", 0.25),
    ],
    "value_turnaround": [
        ("pfcf", -0.30),
        ("pb", -0.20),
        ("margin_trend", 0.30),
        ("fcf_margin", 0.20),
    ],
}


@dataclass(frozen=True)
class ArchetypeScore:
    archetype: str
    score: float
    components: dict[str, float]
    flags: tuple[str, ...]


def compute_metrics(records: Sequence[FundamentalRecord], price: float) -> dict[str, float | None]:
    latest = cm._latest(records)
    if latest is None:
        return {}
    return {
        "revenue_cagr": cm.revenue_cagr(records, 3),
        "revenue_growth_acceleration": cm.revenue_growth_acceleration(records),
        "eps_growth": cm.eps_growth(records, 3),
        "operating_margin": cm.operating_margin(latest),
        "net_margin": cm.net_margin(latest),
        "margin_trend": cm.margin_trend(records),
        "roic": cm.roic(latest),
        "fcf_margin": cm.fcf_margin(latest),
        "fcf_conversion": cm.fcf_conversion(latest),
        "debt_to_equity": cm.debt_to_equity(latest),
        "share_growth": cm.share_growth(records, 3),
        "pe": cm.pe(latest, price),
        "pfcf": cm.pfcf(latest, price),
        "ps": cm.ps(latest, price),
        "pb": cm.pb(latest, price),
    }


def _zscores(values: list[float | None]) -> list[float | None]:
    present = [v for v in values if v is not None]
    if len(present) < 2:
        return [0.0 if v is not None else None for v in values]
    mu = mean(present)
    sigma = pstdev(present)
    if sigma == 0:
        return [0.0 if v is not None else None for v in values]
    return [None if v is None else (v - mu) / sigma for v in values]


def _flags(metrics: dict[str, float | None]) -> tuple[str, ...]:
    flags = []
    sg = metrics.get("share_growth")
    if sg is not None and sg > 0.05:
        flags.append("high-dilution")
    de = metrics.get("debt_to_equity")
    if de is not None and de > 2.0:
        flags.append("high-debt")
    mt = metrics.get("margin_trend")
    if mt is not None and mt < 0:
        flags.append("margin-declining")
    fm = metrics.get("fcf_margin")
    if fm is not None and fm < 0:
        flags.append("negative-fcf")
    return tuple(flags)


def score_archetypes(
    universe: dict[str, tuple[Sequence[FundamentalRecord], float]],
) -> dict[str, dict[str, ArchetypeScore]]:
    symbols = list(universe)
    metrics = {s: compute_metrics(universe[s][0], universe[s][1]) for s in symbols}

    # Cross-sectional Z per metric key.
    keys = {k for m in metrics.values() for k in m}
    zmaps: dict[str, dict[str, float | None]] = {}
    for key in keys:
        col = [metrics[s].get(key) for s in symbols]
        zcol = _zscores(col)
        zmaps[key] = dict(zip(symbols, zcol, strict=True))

    out: dict[str, dict[str, ArchetypeScore]] = {}
    for s in symbols:
        out[s] = {}
        for arch, weights in _WEIGHTS.items():
            components: dict[str, float] = {}
            wsum, contrib = 0.0, 0.0
            for key, w in weights:
                z = zmaps[key].get(s)
                if z is None:
                    continue
                signed = z if w >= 0 else -z
                components[key] = signed
                contrib += abs(w) * signed
                wsum += abs(w)
            blended = contrib / wsum if wsum > 0 else 0.0
            score = normal_cdf(blended) * 100.0
            out[s][arch] = ArchetypeScore(arch, score, components, _flags(metrics[s]))
    return out
