"""Information-coefficient (IC) primitives for signal validation.

Cross-sectional Spearman rank IC is the standard "does this signal predict forward returns?"
measure: per date, rank the cross-section by the signal and by the forward return, correlate the
ranks; average across dates. ``partial_spearman`` answers "is the IC just a size tilt?" by
controlling for a third variable (e.g. market cap). ``ic_stats`` turns a list of per-date ICs into
mean / t-stat / hit-rate, with an effective-N haircut for the autocorrelation of overlapping
forward windows (a naive t-stat over overlapping windows overstates significance).

Standard-library only (no scipy), matching the existing compounder IC scripts so results are
directly comparable.
"""

from __future__ import annotations

from dataclasses import dataclass


def average_ranks(values: list[float]) -> list[float]:
    """1-based ranks with ties resolved to their average (so [10,20,20,40] -> [1,2.5,2.5,4])."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(values):
        j = i
        while j + 1 < len(values) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys, strict=True))
    vx = sum((a - mx) ** 2 for a in xs)
    vy = sum((b - my) ** 2 for b in ys)
    if vx <= 0 or vy <= 0:
        return None
    return cov / (vx**0.5 * vy**0.5)


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Spearman rank correlation (Pearson on average-ranks). None if n<3 or a side is constant."""
    if len(xs) < 3:
        return None
    return _pearson(average_ranks(xs), average_ranks(ys))


def partial_spearman(xs: list[float], ys: list[float], zs: list[float]) -> float | None:
    """Rank partial correlation of x,y controlling for z — "is the x->y IC just a z (size) tilt?"."""
    if len(xs) < 4:
        return None
    rxy = _pearson(average_ranks(xs), average_ranks(ys))
    rxz = _pearson(average_ranks(xs), average_ranks(zs))
    ryz = _pearson(average_ranks(ys), average_ranks(zs))
    if rxy is None or rxz is None or ryz is None:
        return None
    denom = ((1 - rxz**2) * (1 - ryz**2)) ** 0.5
    if denom <= 1e-12:
        return None
    return (rxy - rxz * ryz) / denom


@dataclass(frozen=True)
class ICStats:
    n: int
    mean: float | None
    std: float | None
    t_stat: float | None  # mean / (std / sqrt(eff_n)); eff_n defaults to n
    positive: int  # count of ICs > 0


def ic_stats(ics: list[float], eff_n: float | None = None) -> ICStats:
    """Summarize per-date ICs. ``eff_n`` (effective sample size) defaults to ``len(ics)``; pass a
    smaller value to haircut the t-stat for overlapping/autocorrelated forward windows."""
    n = len(ics)
    if n == 0:
        return ICStats(n=0, mean=None, std=None, t_stat=None, positive=0)
    mean = sum(ics) / n
    positive = sum(1 for x in ics if x > 0)
    if n < 2:
        return ICStats(n=n, mean=mean, std=None, t_stat=None, positive=positive)
    var = sum((x - mean) ** 2 for x in ics) / (n - 1)
    std = var**0.5
    use_n = n if eff_n is None else max(1.0, min(eff_n, float(n)))
    t_stat = None if std <= 0 else mean / (std / use_n**0.5)
    return ICStats(n=n, mean=mean, std=std, t_stat=t_stat, positive=positive)
