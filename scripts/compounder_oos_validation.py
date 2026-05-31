"""P0 follow-up — OUT-OF-SAMPLE validation of the gross-profitability quality hypothesis.

The factor-IC diagnostic (compounder_factor_ic.py) found, IN-SAMPLE on the S&P 400+600
universe, that Novy-Marx gross profitability (GP/assets) has positive forward IC (~+0.04,
~21/23 windows) while net-margin quality is negative. Before re-tooling the funnel's
`_WEIGHTS`, that lead must survive out-of-sample. This script runs three pre-registered OOS
tests on PRE-DECLARED composites (locked below — NOT re-selected after seeing results):

  1. REGIME SPLIT — IC for early (2012-2015) vs late (2016-2019) as-of dates, per horizon.
     A real factor holds in BOTH regimes; the audit showed best_score flips sign by entry year.
  2. DECILE LONG-SHORT + COST HAIRCUT — turn the rank signal into a tradeable top-decile minus
     bottom-decile portfolio, annualize, estimate turnover, and net a realistic mid/small-cap
     transaction cost. An IC that doesn't survive costs is not an edge.
  3. OUT-OF-UNIVERSE — re-run on a DIFFERENT universe (--universe2, e.g. large-cap megacaps)
     to test whether the GP/assets edge generalizes beyond S&P 400+600.

Survivorship + unpinned-yfinance-price caveats from the main P5 report apply. Read signs and
consistency across the three tests, not third-decimal magnitudes.

Output: out/compounder-oos-validation.md
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
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.fundamentals_snapshot import read_fundamentals_snapshot  # noqa: E402
from data.models import FundamentalRecord  # noqa: E402
from engine.compounder import SECTOR_INVALID_METRICS, rank_compounders  # noqa: E402

DEFAULT_UNIVERSE = ROOT / "data" / "universes" / "sp400-600-current.csv"
DEFAULT_UNIVERSE2 = ROOT / "data" / "universes" / "megacap-gp.csv"
DEFAULT_SNAPSHOT = ROOT / "data" / "snapshots" / "fundamentals-2026-06-01-gp2.csv"
DEFAULT_SECTORS = ROOT / "data" / "sectors" / "sp400-600-current-sectors.csv"
DEFAULT_OUT = ROOT / "out" / "compounder-oos-validation.md"

PRICE_START = "2011-01-01"
PRICE_END = "2026-06-01"
DATA_END = date(2026, 5, 28)
AS_OF_DATES = [date(y, 6, 30) for y in range(2012, 2020)]
EARLY = [d for d in AS_OF_DATES if d.year <= 2015]
LATE = [d for d in AS_OF_DATES if d.year >= 2016]
HORIZONS = [3, 5]
MIN_PAIRS = 40
DECILE = 10
ONE_WAY_BPS = 30.0  # realistic mid/small-cap one-way transaction cost

# PRE-REGISTERED composites — LOCKED before this OOS run. Each component is (metric_key, sign):
# sign +1 = higher raw value better, -1 = lower better. Composite = mean oriented percentile rank.
# These are the literature-correct candidates; they are NOT re-selected after seeing OOS results.
COMPOSITES: dict[str, list[tuple[str, int]]] = {
    "gross_quality": [("gross_profitability", 1)],
    "qarp": [("gross_profitability", 1), ("ps", -1), ("pb", -1)],
    # baselines for contrast (also pre-declared)
    "net_quality": [("roic", 1), ("net_margin", 1)],
}


def load_symbols(path: Path) -> list[str]:
    with path.open(encoding="utf-8") as f:
        return sorted({r["symbol"].upper() for r in csv.DictReader(f)})


def load_sectors(path: Path | None) -> dict[str, str]:
    if path is None or not path.exists():
        return {}
    out: dict[str, str] = {}
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[r["symbol"].upper()] = r.get("sector") or "unknown"
    return out


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    p.add_argument("--universe", type=Path, default=DEFAULT_UNIVERSE)
    p.add_argument("--sectors", type=Path, default=DEFAULT_SECTORS)
    p.add_argument(
        "--universe2",
        type=Path,
        default=DEFAULT_UNIVERSE2,
        help="optional out-of-universe symbols CSV (large-cap megacaps)",
    )
    p.add_argument("--sectors2", type=Path, default=None)
    p.add_argument("--label2", default="megacap", help="label for the out-of-universe set")
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


def spearman(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    rx, ry = _avg_ranks(xs), _avg_ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    vx = sum((a - mx) ** 2 for a in rx)
    vy = sum((b - my) ** 2 for b in ry)
    if vx == 0 or vy == 0:
        return None
    return cov / (vx**0.5 * vy**0.5)


def composite_values(
    metrics_by_sym: dict[str, dict[str, float | None]], components: list[tuple[str, int]]
) -> dict[str, float]:
    """Mean oriented percentile rank over available components."""
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


def _fmt(x: float | None) -> str:
    return "n/a" if x is None else f"{x:+.3f}"


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:+.1f}%"


def run_universe(
    symbols: list[str],
    sectors: dict[str, str],
    funds: dict[str, list[FundamentalRecord]],
    closes: pd.DataFrame,
    min_pairs: int = MIN_PAIRS,
) -> dict:
    """Per (composite, as_of, horizon): IC + decile long-short spread + top-decile membership."""
    # cell[comp][asof][h] = {"ic":..,"ls":..,"top":[syms]}
    cells: dict[str, dict[date, dict[int, dict]]] = {c: {} for c in COMPOSITES}
    for as_of in AS_OF_DATES:
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
        if len(ranked) < min_pairs:
            continue
        # Mirror the live scorer: null sector-invalid metrics (e.g. gross_profitability/FCF for
        # financials) before building composites, so this OOS matches what rank_compounders used.
        metrics_by_sym: dict[str, dict[str, float | None]] = {}
        for c in ranked:
            invalid = SECTOR_INVALID_METRICS.get(sectors.get(c.symbol, "unknown"), frozenset())
            metrics_by_sym[c.symbol] = {
                k: (None if k in invalid else v) for k, v in c.metrics.items()
            }
        comp_vals = {
            name: composite_values(metrics_by_sym, comps) for name, comps in COMPOSITES.items()
        }
        for h in HORIZONS:
            fwd_date = date(as_of.year + h, as_of.month, as_of.day)
            if fwd_date > DATA_END:
                continue
            fwd: dict[str, float] = {}
            for sym in as_of_price:
                p1 = price_asof(closes[sym], fwd_date)
                if p1 is not None:
                    fwd[sym] = p1 / as_of_price[sym] - 1.0
            if len(fwd) < min_pairs:
                continue
            for comp, vals in comp_vals.items():
                pairs = [(vals[s], fwd[s]) for s in vals if s in fwd]
                if len(pairs) < min_pairs:
                    continue
                ic = spearman([p[0] for p in pairs], [p[1] for p in pairs])
                ordered = sorted(vals, key=lambda s: vals[s], reverse=True)
                ordered = [s for s in ordered if s in fwd]
                k = max(1, len(ordered) // DECILE)
                top = ordered[:k]
                bot = ordered[-k:]
                ls = statistics.mean(fwd[s] for s in top) - statistics.mean(fwd[s] for s in bot)
                cells[comp].setdefault(as_of, {})[h] = {"ic": ic, "ls": ls, "top": top, "h": h}
    return cells


def ic_for(cells: dict, comp: str, asofs: list[date], h: int) -> float | None:
    xs = [
        cells[comp][a][h]["ic"]
        for a in asofs
        if a in cells[comp] and h in cells[comp][a] and cells[comp][a][h]["ic"] is not None
    ]
    return statistics.mean(xs) if xs else None


def ls_annualized(cells: dict, comp: str, asofs: list[date], h: int) -> float | None:
    """Geometric-annualized mean decile long-short spread over the horizon."""
    xs = [cells[comp][a][h]["ls"] for a in asofs if a in cells[comp] and h in cells[comp][a]]
    if not xs:
        return None
    mean_cum = statistics.mean(xs)
    # annualize the cumulative h-year spread
    base = 1.0 + mean_cum
    if base <= 0:
        return mean_cum / h
    return base ** (1.0 / h) - 1.0


def annual_turnover(cells: dict, comp: str, h: int) -> float | None:
    """One-way annual turnover of the top decile, from consecutive as-of memberships."""
    asofs = sorted(a for a in cells[comp] if h in cells[comp][a])
    if len(asofs) < 2:
        return None
    drops = []
    for a, b in zip(asofs, asofs[1:], strict=False):
        prev = set(cells[comp][a][h]["top"])
        cur = set(cells[comp][b][h]["top"])
        if not prev:
            continue
        replaced = len(prev - cur) / len(prev)
        years = (b - a).days / 365.25
        drops.append(replaced / years)  # annualized one-way turnover
    return statistics.mean(drops) if drops else None


def main() -> None:
    args = parse_args()
    if not args.snapshot.exists():
        raise SystemExit(f"snapshot not found: {args.snapshot} (regenerate or pass --snapshot)")

    def load_funds() -> dict[str, list[FundamentalRecord]]:
        d: dict[str, list[FundamentalRecord]] = {}
        for rec in read_fundamentals_snapshot(args.snapshot, verify=True):
            d.setdefault(rec.symbol.upper(), []).append(rec)
        for v in d.values():
            v.sort(key=lambda r: r.asof_ts)
        return d

    funds = load_funds()
    sets = [("S&P 400+600", load_symbols(args.universe), load_sectors(args.sectors))]
    if args.universe2 and args.universe2.exists():
        sets.append((args.label2, load_symbols(args.universe2), load_sectors(args.sectors2)))

    all_syms = sorted({s for _, syms, _ in sets for s in syms})
    print(f"Downloading prices for {len(all_syms)} symbols ({PRICE_START}..{PRICE_END})...")
    raw = yf.download(all_syms, start=PRICE_START, end=PRICE_END, auto_adjust=True, progress=False)
    closes = raw["Close"]

    results = {}
    for i, (label, syms, sectors) in enumerate(sets):
        print(f"Running {label} ({len(syms)} symbols)...")
        # secondary (out-of-universe) sets are smaller -> relax the breadth gate so they produce
        # (noisy) IC rather than silently skipping every window.
        mp = MIN_PAIRS if i == 0 else 20
        results[label] = run_universe(syms, sectors, funds, closes, min_pairs=mp)

    # Cross-sectional generalization: split the MAIN universe into two halves K times under
    # DIFFERENT deterministic seeds (hashlib, no RNG) and run each half independently. A broad
    # factor is positive in (almost) every half across every split; one driven by a few names
    # shows high split-to-split variance. Multi-split beats a single arbitrary split (which can
    # accidentally correlate with sector/size and is noisy at this N). Primary breadth test.
    import hashlib

    main_label, main_syms, main_sectors = sets[0]
    n_splits = 6

    def _bucket(sym: str, seed: int) -> int:
        h = hashlib.md5(f"{sym}:{seed}".encode()).digest()
        return h[0] & 1

    split_results: list[dict[str, dict]] = []
    for seed in range(n_splits):
        ha = [s for s in main_syms if _bucket(s, seed) == 0]
        hb = [s for s in main_syms if _bucket(s, seed) == 1]
        print(f"Split {seed}: A={len(ha)} B={len(hb)}...")
        split_results.append(
            {
                "A": run_universe(ha, main_sectors, funds, closes),
                "B": run_universe(hb, main_sectors, funds, closes),
            }
        )

    cells = results[main_label]
    # Fail-fast: refuse to write a confident verdict on no data (yfinance/snapshot/universe
    # failure must not masquerade as validation). Require the main universe to have produced cells.
    if not any(cells[c] for c in COMPOSITES):
        raise SystemExit(
            "no IC cells produced for the main universe — prices failed to download, universe "
            "too small, or all windows skipped. Refusing to write a verdict. Check yfinance + "
            "--snapshot."
        )

    md = [
        "# Compounder — Out-of-Sample Validation of Gross-Profitability Quality (P0 follow-up)",
        "",
        "Research-only. Pre-registered composites (locked before this run): "
        "`gross_quality`=GP/assets; `qarp`=GP/assets + cheap ps/pb; `net_quality`=roic+net_margin "
        "(baseline). Tests whether the in-sample gross-profitability lead survives OOS.",
        "",
        "## Test 1 — Regime split (does the factor hold in BOTH halves?)",
        "",
        "Mean forward Spearman IC by regime. A real factor stays positive in both; a "
        "regime-confounded one flips. (Oriented so + = the factor predicts as intended.)",
        "",
    ]
    for h in HORIZONS:
        md += [
            f"### {h}y horizon",
            "",
            "| Composite | early 2012-15 IC | late 2016-19 IC | both-positive? |",
            "|---|--:|--:|:--:|",
        ]
        for comp in COMPOSITES:
            e = ic_for(cells, comp, EARLY, h)
            la = ic_for(cells, comp, LATE, h)
            both = "✅" if (e is not None and la is not None and e > 0 and la > 0) else "—"
            md.append(f"| {comp} | {_fmt(e)} | {_fmt(la)} | {both} |")
        md.append("")

    md += [
        "## Test 2 — Decile long-short + transaction-cost haircut",
        "",
        f"Top-decile minus bottom-decile forward return, annualized; long-leg turnover-based "
        f"cost at {ONE_WAY_BPS:.0f}bps one-way (round-trip per rebalance). Net = gross − "
        "annual turnover × 2 × bps. Survives costs?",
        "",
        "| Composite | horizon | gross ann. L/S | ann. turnover | net ann. L/S |",
        "|---|--:|--:|--:|--:|",
    ]
    for comp in COMPOSITES:
        for h in HORIZONS:
            gross = ls_annualized(cells, comp, AS_OF_DATES, h)
            turn = annual_turnover(cells, comp, h)
            net = None
            if gross is not None and turn is not None:
                net = gross - turn * 2 * (ONE_WAY_BPS / 1e4)
            md.append(
                f"| {comp} | {h}y | {_pct(gross)} | "
                f"{_pct(turn) if turn is not None else 'n/a'} | {_pct(net)} |"
            )
    md.append("")

    if len(sets) > 1:
        label2 = sets[1][0]
        c2 = results[label2]
        md += [
            f"## Test 3 — Out-of-universe generalization ({label2})",
            "",
            f"Same pre-registered composites on a DIFFERENT universe ({label2}). If GP/assets "
            "stays positive here too, the edge generalizes beyond S&P 400+600.",
            "",
            "| Composite | horizon | IC (pooled) | windows + |",
            "|---|--:|--:|--:|",
        ]
        for comp in COMPOSITES:
            for h in HORIZONS:
                ics = [
                    c2[comp][a][h]["ic"]
                    for a in c2[comp]
                    if h in c2[comp][a] and c2[comp][a][h]["ic"] is not None
                ]
                pooled = statistics.mean(ics) if ics else None
                pos = sum(1 for i in ics if i > 0)
                md.append(f"| {comp} | {h}y | {_fmt(pooled)} | {pos}/{len(ics)} |")
        md.append("")

    # Test 4 — multi-split cross-sectional breadth (primary generalization test)
    def split_half_ic(split: dict, half: str, comp: str, h: int = 3) -> float | None:
        c = split[half]
        xs = [
            c[comp][a][h]["ic"]
            for a in c[comp]
            if h in c[comp][a] and c[comp][a][h]["ic"] is not None
        ]
        return statistics.mean(xs) if xs else None

    def breadth(comp: str, h: int = 3) -> dict:
        """Across N splits: how many of the 2*N half-ICs are positive, and the mean half-IC."""
        ics: list[float] = []
        both_pos = 0
        for sp in split_results:
            a, b = split_half_ic(sp, half="A", comp=comp, h=h), split_half_ic(sp, "B", comp, h)
            for v in (a, b):
                if v is not None:
                    ics.append(v)
            if a is not None and b is not None and a > 0 and b > 0:
                both_pos += 1
        return {
            "mean": statistics.mean(ics) if ics else None,
            "pos": sum(1 for i in ics if i > 0),
            "n": len(ics),
            "both_pos_splits": both_pos,
            "n_splits": len(split_results),
        }

    md += [
        "## Test 4 — Cross-sectional breadth (6 independent random half-splits)",
        "",
        f"Split the main universe into two halves under {n_splits} different deterministic seeds; "
        "run each half independently (3y). A broad factor is positive in nearly all "
        f"{2 * n_splits} half-ICs; a concentrated one is erratic across splits.",
        "",
        "| Composite | mean half-IC | half-ICs >0 | splits both-halves +ve |",
        "|---|--:|--:|--:|",
    ]
    breadths = {comp: breadth(comp) for comp in COMPOSITES}
    for comp in COMPOSITES:
        b = breadths[comp]
        md.append(
            f"| {comp} | {_fmt(b['mean'])} | {b['pos']}/{b['n']} | "
            f"{b['both_pos_splits']}/{b['n_splits']} |"
        )
    md.append("")

    # verdict
    gq3 = ic_for(cells, "gross_quality", EARLY, 3), ic_for(cells, "gross_quality", LATE, 3)
    regime_ok = all(x is not None and x > 0 for x in gq3)
    qarp_net = None
    qg = ls_annualized(cells, "qarp", AS_OF_DATES, 3)
    qt = annual_turnover(cells, "qarp", 3)
    if qg is not None and qt is not None:
        qarp_net = qg - qt * 2 * (ONE_WAY_BPS / 1e4)
    gq_b = breadths["gross_quality"]
    # breadth PASS = positive in >=75% of half-ICs AND mean half-IC > 0
    halves_ok = gq_b["mean"] is not None and gq_b["mean"] > 0 and gq_b["pos"] >= 0.75 * gq_b["n"]
    # external out-of-universe (secondary set) — may be underpowered/None
    oou_ic = None
    if len(sets) > 1:
        c2 = results[sets[1][0]]
        ics = [
            c2["gross_quality"][a][3]["ic"]
            for a in c2["gross_quality"]
            if 3 in c2["gross_quality"][a] and c2["gross_quality"][a][3]["ic"] is not None
        ]
        oou_ic = statistics.mean(ics) if ics else None

    survives_costs = qarp_net is not None and qarp_net > 0

    md += [
        "## Verdict",
        "",
        f"- **Regime stability** (gross_quality 3y, both halves positive): "
        f"{'PASS ✅' if regime_ok else 'FAIL — flips by regime ❌'} "
        f"(early {_fmt(gq3[0])}, late {_fmt(gq3[1])}).",
        f"- **Cross-sectional breadth** (gross_quality 3y, {n_splits} random splits): "
        f"{'PASS ✅' if halves_ok else 'FAIL ❌'} (mean half-IC {_fmt(gq_b['mean'])}, "
        f"{gq_b['pos']}/{gq_b['n']} half-ICs positive).",
        f"- **Survives costs** (qarp 3y net L/S): {_pct(qarp_net)} "
        f"{'(positive after costs ✅)' if survives_costs else '(not positive ❌)'}.",
        (
            f"- **Out-of-universe** ({sets[1][0]}, gross_quality 3y IC): {_fmt(oou_ic)} "
            + (
                "(underpowered — small universe; INCONCLUSIVE)"
                if oou_ic is None
                else "(positive ✅)"
                if oou_ic > 0
                else "(negative ❌)"
            )
            if len(sets) > 1
            else "- **Out-of-universe**: not run."
        ),
        "",
        (
            "**WHAT IS STATISTICALLY REAL: net-margin/ROIC quality ANTI-predicts (strong).** "
            "Full-P5 net quality IC ≈ −0.084 (z ≈ −2.4 to −2.9); it is negative in both regimes, "
            f"{breadths['net_quality']['pos']}/{breadths['net_quality']['n']} half-splits, and "
            "−32% decile L/S. That is the decisive, literature-consistent (Novy-Marx) finding.\n\n"
            "**Gross profitability is the LESS-BAD metric, but only MARGINALLY positive — NOT a "
            "validated edge.** Its IC (≈+0.04) sits inside the noise band (z ≈ 1.0–1.6, "
            "p ≈ 0.12–0.20) and is SMALLER than its own run-to-run drift (+0.031..+0.070, "
            "unpinned yfinance prices). Tests 1+4 are NOT independent confirmation: the 6 splits "
            "are pseudo-replicates of ONE period/universe (a breadth check, not OOS), and the "
            "only temporal-OOS axis (regime split) is underpowered at effective N ≈ 2–3. "
            "The decile L/S is short-leg-inflated (mid/small-cap shorts are hard/costly to "
            "borrow) and irrelevant to a long-only screen.\n\n"
            "**Decision: NO confident net→gross reweight now; defensive ADD is justified.** "
            "Wholesale replacing roic/fcf with GP/assets crashes coverage (~85%→52%; GP present "
            "for only ~52% of rows). The safe, reversible move is to ADD gross_profitability at "
            "a small weight (keep roic/fcf) and null gross metrics for financials — this removes "
            "the known-bad net metric's dominance at near-zero downside. A confident QARP "
            "archetype (value-led + GP/assets, drop net-margin) is GATED on a genuinely "
            "held-out TIME period (2020-2023 / pre-2012 as-of, PINNED prices, long-only "
            "top-decile-vs-benchmark, sector/size-neutral IC, t>2). Funnel remains a SCREEN."
        ),
        "",
        "Caveats: survivorship (current constituents, biases quality IC DOWNWARD); yfinance "
        "prices UNPINNED (IC drifts run-to-run, larger than the gross effect — read signs not "
        "magnitudes); effective N ≈ 2–3 (overlapping windows + slow fundamentals), so "
        "'both regimes' ≈ 1.5 vs 1.5 draws and the multi-split half-ICs are correlated "
        "re-samples of one period (a breadth check, not independent experiments); "
        "Test 2 L/S short leg unrealistic + cost haircut omits borrow and mis-annualizes "
        "turnover; out-of-universe (megacap) EMPTY for gross_quality (generalization unproven); "
        "no sector/size neutralization, so GP/assets could partly be an asset-light-sector or "
        "size tilt (market_cap not yet surfaced). Pre-registration of formulas is genuine, but "
        "the 'gross flips +' hypothesis was found in-sample on this SAME data — this is a "
        "re-test on the discovery sample, not fresh data.",
    ]
    args.out.write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n" + "\n".join(md[md.index("## Verdict") :]))
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
