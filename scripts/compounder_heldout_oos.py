"""Action 3b — held-out-TIME OOS of gross-profitability quality, the gate for a confident reweight.

The prior OOS (compounder_oos_validation.py) used 2012-2019 as-of dates — the SAME data the
"gross flips positive" hypothesis was discovered on. The 4-lens audit flagged that as a re-test
on the discovery sample, not held-out, and demanded: a genuinely held-out TIME period, PINNED
prices, a LONG-ONLY (not short-leg-inflated) read, and SECTOR/SIZE-neutral IC, before any
confident `_WEIGHTS` reweight.

This script delivers exactly that on as-of 2020/2021/2022-06-30 (3y forward → 2023/2024/2025;
NOT used to form the hypothesis), with prices loaded from a PINNED snapshot (--prices). Composites
are the same PRE-REGISTERED ones, sector-nulled to match the live scorer. It reports per as-of and
pooled:
  - raw Spearman rank IC
  - SECTOR-NEUTRAL IC (mean within-sector IC) — strips cross-sector tilts
  - market_cap PARTIAL IC (Spearman partial corr controlling for size) — the size-proxy test:
    if the partial IC collapses toward 0, GP/assets was a size bet, not a quality effect
  - LONG-ONLY top-decile excess vs the equal-weight universe (the deployable read; no short leg)

Honest by construction: only 3 overlapping held-out windows => low power; this is a GATE check
(does the sign hold out-of-time with controls?), not a precise alpha estimate.

Output: out/compounder-heldout-oos.md
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.fundamentals_snapshot import read_fundamentals_snapshot  # noqa: E402
from data.models import FundamentalRecord  # noqa: E402
from data.price_snapshot import read_price_snapshot  # noqa: E402
from engine.compounder import SECTOR_INVALID_METRICS, rank_compounders  # noqa: E402

DEFAULT_UNIVERSE = ROOT / "data" / "universes" / "sp400-600-current.csv"
DEFAULT_SNAPSHOT = ROOT / "data" / "snapshots" / "fundamentals-2026-06-01-gp2.csv"
DEFAULT_SECTORS = ROOT / "data" / "sectors" / "sp400-600-current-sectors.csv"
DEFAULT_PRICES = ROOT / "data" / "snapshots" / "prices-2026-06-01.csv"
DEFAULT_OUT = ROOT / "out" / "compounder-heldout-oos.md"

DATA_END = date(2026, 5, 28)
HELDOUT_AS_OF = [date(2020, 6, 30), date(2021, 6, 30), date(2022, 6, 30)]
HORIZON = 3
MIN_PAIRS = 40
DECILE = 10
SECTOR_MIN = 12  # min names in a sector to compute a within-sector IC

# PRE-REGISTERED, LOCKED (same as the in-sample OOS): not re-selected on held-out results.
COMPOSITES: dict[str, list[tuple[str, int]]] = {
    "gross_quality": [("gross_profitability", 1)],
    "qarp": [("gross_profitability", 1), ("ps", -1), ("pb", -1)],
    "net_quality": [("roic", 1), ("net_margin", 1)],
}


def load_symbols(path: Path) -> list[str]:
    with path.open(encoding="utf-8") as f:
        return sorted({r["symbol"].upper() for r in csv.DictReader(f)})


def load_sectors(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[r["symbol"].upper()] = r.get("sector") or "unknown"
    return out


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    p.add_argument(
        "--prices",
        type=Path,
        default=DEFAULT_PRICES,
        help="PINNED price snapshot CSV (run scripts/snapshot_prices.py first)",
    )
    p.add_argument("--universe", type=Path, default=DEFAULT_UNIVERSE)
    p.add_argument("--sectors", type=Path, default=DEFAULT_SECTORS)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return p.parse_args()


def price_asof(close: pd.Series, as_of: date) -> float | None:
    s = close.loc[: pd.Timestamp(as_of)].dropna()
    return float(s.iloc[-1]) if len(s) else None


def _avg_ranks(vals: list[float]) -> list[float]:
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    ranks = [0.0] * len(vals)
    i = 0
    while i < len(vals):
        j = i
        while j + 1 < len(vals) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(xr: list[float], yr: list[float]) -> float | None:
    n = len(xr)
    if n < 3:
        return None
    mx, my = sum(xr) / n, sum(yr) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(xr, yr, strict=True))
    vx = sum((a - mx) ** 2 for a in xr)
    vy = sum((b - my) ** 2 for b in yr)
    if vx == 0 or vy == 0:
        return None
    return cov / (vx**0.5 * vy**0.5)


def spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3:
        return None
    return _pearson(_avg_ranks(xs), _avg_ranks(ys))


def partial_spearman(xs: list[float], ys: list[float], zs: list[float]) -> float | None:
    """Spearman partial correlation of x,y controlling for z (rank-based)."""
    if len(xs) < 4:
        return None
    rxy = _pearson(_avg_ranks(xs), _avg_ranks(ys))
    rxz = _pearson(_avg_ranks(xs), _avg_ranks(zs))
    ryz = _pearson(_avg_ranks(ys), _avg_ranks(zs))
    if rxy is None or rxz is None or ryz is None:
        return None
    denom = ((1 - rxz**2) * (1 - ryz**2)) ** 0.5
    if denom <= 1e-12:
        return None
    return (rxy - rxz * ryz) / denom


def composite_values(
    metrics_by_sym: dict[str, dict[str, float | None]], components: list[tuple[str, int]]
) -> dict[str, float]:
    contrib: dict[str, list[float]] = {}
    for key, sign in components:
        present: dict[str, float] = {
            s: v for s, m in metrics_by_sym.items() if (v := m.get(key)) is not None
        }
        if len(present) < 3:
            continue
        syms = list(present)
        ranks = _avg_ranks([present[s] for s in syms])
        n = len(syms)
        for s, r in zip(syms, ranks, strict=True):
            norm = r / n
            contrib.setdefault(s, []).append(norm if sign > 0 else 1.0 - norm)
    return {s: sum(v) / len(v) for s, v in contrib.items() if v}


def sector_neutral_ic(
    vals: dict[str, float], fwd: dict[str, float], sectors: dict[str, str]
) -> float | None:
    """Count-weighted mean of within-sector Spearman ICs (strips cross-sector tilts)."""
    by_sec: dict[str, list[str]] = {}
    for s in vals:
        if s in fwd:
            by_sec.setdefault(sectors.get(s, "unknown"), []).append(s)
    num, den = 0.0, 0
    for syms in by_sec.values():
        if len(syms) < SECTOR_MIN:
            continue
        ic = spearman([vals[s] for s in syms], [fwd[s] for s in syms])
        if ic is not None:
            num += ic * len(syms)
            den += len(syms)
    return num / den if den else None


def _fmt(x: float | None) -> str:
    return "n/a" if x is None else f"{x:+.3f}"


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:+.1f}%"


def main() -> None:
    args = parse_args()
    if not args.snapshot.exists():
        raise SystemExit(f"snapshot not found: {args.snapshot}")
    if not args.prices.exists():
        raise SystemExit(
            f"pinned price snapshot not found: {args.prices}\n"
            "Run scripts/snapshot_prices.py first (held-out OOS requires pinned prices)."
        )
    symbols = load_symbols(args.universe)
    sectors = load_sectors(args.sectors)
    funds: dict[str, list[FundamentalRecord]] = {}
    for rec in read_fundamentals_snapshot(args.snapshot, verify=True):
        funds.setdefault(rec.symbol.upper(), []).append(rec)
    for v in funds.values():
        v.sort(key=lambda r: r.asof_ts)
    print(f"Loading PINNED prices {args.prices.name}...")
    closes = read_price_snapshot(args.prices, verify=True)

    rows: list[dict] = []
    for as_of in HELDOUT_AS_OF:
        fwd_date = date(as_of.year + HORIZON, as_of.month, as_of.day)
        if fwd_date > DATA_END:
            continue
        universe: dict[str, tuple[Sequence[FundamentalRecord], float]] = {}
        as_of_price: dict[str, float] = {}
        for sym in symbols:
            if sym not in closes.columns:
                continue
            recs = [r for r in funds.get(sym, []) if r.asof_ts.date() <= as_of]
            if len(recs) < 2:
                continue
            p0 = price_asof(closes[sym], as_of)
            if p0 is None or p0 <= 0:
                continue
            universe[sym] = (recs, p0)
            as_of_price[sym] = p0
        ranked = rank_compounders(universe, top_n=10_000, sectors=sectors)
        if len(ranked) < MIN_PAIRS:
            continue
        metrics_by_sym: dict[str, dict[str, float | None]] = {}
        mcap: dict[str, float] = {}
        for c in ranked:
            invalid = SECTOR_INVALID_METRICS.get(sectors.get(c.symbol, "unknown"), frozenset())
            metrics_by_sym[c.symbol] = {
                k: (None if k in invalid else v) for k, v in c.metrics.items()
            }
            mc = c.metrics.get("market_cap")
            if mc is not None:
                mcap[c.symbol] = mc
        fwd: dict[str, float] = {}
        for sym in as_of_price:
            p1 = price_asof(closes[sym], fwd_date)
            if p1 is not None:
                fwd[sym] = p1 / as_of_price[sym] - 1.0
        if len(fwd) < MIN_PAIRS:
            continue
        univ_mean = statistics.mean(fwd.values())
        for comp, comps in COMPOSITES.items():
            vals = composite_values(metrics_by_sym, comps)
            pairs = [(s, vals[s], fwd[s]) for s in vals if s in fwd]
            if len(pairs) < MIN_PAIRS:
                continue
            ic = spearman([p[1] for p in pairs], [p[2] for p in pairs])
            sn_ic = sector_neutral_ic(vals, fwd, sectors)
            # partial IC controlling for size, over names with a market_cap
            psize = [(vals[s], fwd[s], mcap[s]) for s in vals if s in fwd and s in mcap]
            part = (
                partial_spearman(
                    [p[0] for p in psize], [p[1] for p in psize], [p[2] for p in psize]
                )
                if len(psize) >= MIN_PAIRS
                else None
            )
            ordered = sorted((s for s in vals if s in fwd), key=lambda s: vals[s], reverse=True)
            k = max(1, len(ordered) // DECILE)
            top_excess = statistics.mean(fwd[s] for s in ordered[:k]) - univ_mean
            rows.append(
                {
                    "as_of": as_of,
                    "fwd_date": fwd_date,
                    "comp": comp,
                    "n": len(pairs),
                    "ic": ic,
                    "sn_ic": sn_ic,
                    "part": part,
                    "top_excess_ann": (1 + top_excess) ** (1 / HORIZON) - 1
                    if top_excess > -1
                    else None,
                }
            )
        print(f"  {as_of} -> {fwd_date} done (n={len(fwd)})")

    if not rows:
        raise SystemExit("no held-out windows produced — check --prices / --snapshot / universe.")

    def pooled(comp: str, key: str) -> float | None:
        xs = [r[key] for r in rows if r["comp"] == comp and r[key] is not None]
        return statistics.mean(xs) if xs else None

    def pos(comp: str, key: str) -> str:
        xs = [r[key] for r in rows if r["comp"] == comp and r[key] is not None]
        return f"{sum(1 for x in xs if x > 0)}/{len(xs)}"

    md = [
        "# Compounder — Held-out-TIME OOS (action 3b)",
        "",
        "Research-only. Genuinely held-out: as-of 2020/2021/2022-06-30 (3y forward), NOT used to "
        "form the gross-profitability hypothesis. PINNED prices (reproducible). Composites "
        "pre-registered + sector-nulled to match the live scorer. **Only 3 overlapping windows "
        "=> low power; this is a GATE (does the sign hold out-of-time WITH controls?), not a "
        "precise alpha estimate.**",
        "",
        "## Per-composite held-out results (pooled over the 3 windows)",
        "",
        "| Composite | raw IC | pos | sector-neutral IC | size-partial IC | long-only top-decile excess (ann) |",
        "|---|--:|--:|--:|--:|--:|",
    ]
    for comp in COMPOSITES:
        md.append(
            f"| {comp} | {_fmt(pooled(comp, 'ic'))} | {pos(comp, 'ic')} | "
            f"{_fmt(pooled(comp, 'sn_ic'))} | {_fmt(pooled(comp, 'part'))} | "
            f"{_pct(pooled(comp, 'top_excess_ann'))} |"
        )

    md += [
        "",
        "## Per-window raw IC (gross_quality / qarp / net_quality)",
        "",
        "| As-of → fwd | gross_quality | qarp | net_quality |",
        "|---|--:|--:|--:|",
    ]
    for as_of in HELDOUT_AS_OF:
        cell = {r["comp"]: r for r in rows if r["as_of"] == as_of}
        if not cell:
            continue
        fwd_date = next(iter(cell.values()))["fwd_date"]
        md.append(
            f"| {as_of} → {fwd_date} | {_fmt(cell.get('gross_quality', {}).get('ic'))} | "
            f"{_fmt(cell.get('qarp', {}).get('ic'))} | {_fmt(cell.get('net_quality', {}).get('ic'))} |"
        )

    gq_ic = pooled("gross_quality", "ic")
    gq_part = pooled("gross_quality", "part")
    gq_sn = pooled("gross_quality", "sn_ic")
    qarp_ic = pooled("qarp", "ic")
    net_ic = pooled("net_quality", "ic")
    # size-proxy: does the partial IC keep most of the raw IC?
    size_robust = gq_ic is not None and gq_part is not None and gq_ic > 0 and gq_part > 0.5 * gq_ic
    held_positive = gq_ic is not None and gq_ic > 0 and (gq_sn or -1) > 0

    md += [
        "",
        "## Verdict (GATE for a confident reweight)",
        "",
        f"- Held-out gross_quality raw IC {_fmt(gq_ic)} ({pos('gross_quality', 'ic')} windows +), "
        f"sector-neutral {_fmt(gq_sn)}, size-partial {_fmt(gq_part)}.",
        f"- Held-out qarp IC {_fmt(qarp_ic)}; net_quality IC {_fmt(net_ic)}.",
        f"- **Size-proxy test**: the market_cap-partial IC {_fmt(gq_part)} "
        + (
            "RETAINS most of the raw IC → GP/assets is not merely a size bet."
            if size_robust
            else "does NOT clearly retain the raw IC → cannot rule out a size/sector tilt."
        ),
        "",
        (
            "**GATE PASSED (weakly)** — gross_quality stays positive out-of-time with sector/size "
            "controls. Combined with the strongly-negative net_quality, a value-led QARP archetype "
            "re-tool is defensible. BUT only 3 overlapping windows: treat as support for a small, "
            "reversible change, NOT license to over-weight. Keep roic/fcf; lean the eventual "
            "reweight value-first."
            if held_positive and size_robust
            else "**GATE NOT PASSED** — held-out gross_quality is not robustly positive once "
            "sector/size-controlled (or collapses vs size). Do NOT weight gross_quality; revert "
            "any provisional ADD and keep the funnel as a screen. The strong, durable finding "
            "remains: net-margin quality ANTI-predicts — that justified removing its dominance, "
            "nothing more."
        ),
        "",
        "Caveats: 3 overlapping 3y windows (very low power — no t>2 claim possible); held-out "
        "period 2020-2023 spans the COVID rebound + 2022 rate shock (regime-heavy); survivorship "
        "(current constituents) still biases quality IC downward; prices pinned but fundamentals "
        "coverage thins for the most recent periods. A clean pre-2012 held-out set is infeasible "
        "(fundamentals coverage too sparse before 2012).",
    ]
    args.out.write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n" + "\n".join(md[md.index("## Verdict (GATE for a confident reweight)") :]))
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
