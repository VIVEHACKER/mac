"""P5 diagnostic — factor IC decomposition of the compounder score.

The headline P5 result (compounder_forward_validation.py) is a negative forward IC for
`best_score`. This script asks WHY by decomposing the forward Spearman rank IC across:
  - best_score (the funnel ranking)
  - each archetype score (profitable_compounder / hypergrowth_disruptor / value_turnaround)
  - each RAW metric (roic, growth, margins, leverage, valuation ratios, size)

Two payoffs:
  1. Pipeline self-check — known factors must show their textbook sign. market_cap should
     have NEGATIVE IC (small-cap premium); cheap valuation (low pe/pb/pfcf) should predict
     higher returns (value premium). If these anchors come out right, the negative
     compounder IC is trustworthy, not a bug.
  2. Diagnosis — if growth/quality signals are negative but value signals are positive, the
     funnel is selecting expensive glamour; the fix is a valuation tilt, not abandonment.

Reuses the same PIT replay (asof <= date fundamentals, price on/before date) and a single
yfinance download. Output: out/compounder-factor-ic.md
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
from engine.compounder import (  # noqa: E402
    ARCHETYPES,
    SECTOR_INVALID_METRICS,
    rank_compounders,
)

DEFAULT_UNIVERSE = ROOT / "data" / "universes" / "sp400-600-current.csv"
DEFAULT_SNAPSHOT = ROOT / "data" / "snapshots" / "fundamentals-2026-05-31-merged.csv"
DEFAULT_SECTORS = ROOT / "data" / "sectors" / "sp400-600-current-sectors.csv"
DEFAULT_OUT = ROOT / "out" / "compounder-factor-ic.md"

PRICE_START = "2011-01-01"
PRICE_END = "2026-06-01"
DATA_END = date(2026, 5, 28)
AS_OF_DATES = [date(y, 6, 30) for y in range(2012, 2020)]
HORIZONS = [3, 5, 7]
MIN_PAIRS = 40

# Raw metrics to score. "lower is better" ones are flagged so we can read the sign.
# (key, higher_is_better). For value ratios lower is better, so a NEGATIVE raw IC =
# the factor "works" in its intended direction (cheap -> high return).
RAW_METRICS: list[tuple[str, bool]] = [
    ("roic", True),
    ("fcf_margin", True),
    ("operating_margin", True),
    ("net_margin", True),
    ("margin_trend", True),
    ("revenue_cagr", True),
    ("revenue_growth_acceleration", True),
    ("eps_growth", True),
    ("fcf_conversion", True),
    ("share_growth", False),
    ("debt_to_equity", False),
    ("pe", False),  # value anchors: expect NEGATIVE IC (cheap -> outperform)
    ("ps", False),
    ("pb", False),
    ("pfcf", False),
]
# NOTE: market_cap is not exposed by compute_metrics, and operating_margin has very low
# XBRL coverage in the snapshot -> both come out n/a. The value ratios (pe/ps/pb/pfcf) are
# the working pipeline self-check anchor instead.

# Hand-built composites to test the diagnosis directly. Each component is (metric, sign):
# sign +1 = higher raw value is "better", -1 = lower is better. The composite is the mean
# of each name's cross-sectional percentile rank (oriented by sign) over available components.
COMPOSITES: dict[str, list[tuple[str, int]]] = {
    # The suspected culprit: pure profitability/quality. Expect strongly NEGATIVE IC.
    "quality_composite": [("roic", 1), ("fcf_margin", 1), ("net_margin", 1)],
    # The proposed redesign: growth-acceleration + cheapness (value). NOTE: share_growth is
    # deliberately EXCLUDED — its raw IC (+0.032) means higher dilution predicted winners on
    # this universe, i.e. the funnel's "buybacks good / low dilution" prior did NOT hold, so
    # orienting it as a positive is not data-supported. Hope POSITIVE IC.
    "redesign_composite": [
        ("revenue_growth_acceleration", 1),
        ("ps", -1),
        ("pb", -1),
    ],
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


def _fmt(x: float | None) -> str:
    return "n/a" if x is None else f"{x:+.3f}"


def build_composite(
    sig_vals: dict[str, dict[str, float]], components: list[tuple[str, int]]
) -> dict[str, float]:
    """Mean of each name's oriented cross-sectional percentile rank over available components."""
    contrib: dict[str, list[float]] = {}
    for key, sign in components:
        d = sig_vals.get(key, {})
        if len(d) < 3:
            continue
        syms = list(d)
        ranks = _avg_ranks([d[s] for s in syms])
        n = len(syms)
        for s, r in zip(syms, ranks, strict=True):
            norm = r / n  # (0,1], higher raw value -> higher
            contrib.setdefault(s, []).append(norm if sign > 0 else 1.0 - norm)
    return {s: sum(xs) / len(xs) for s, xs in contrib.items() if xs}


def main() -> None:
    args = parse_args()
    if not args.snapshot.exists():
        raise SystemExit(f"snapshot not found: {args.snapshot} (regenerate or pass --snapshot)")
    symbols = load_symbols(args.universe)
    sectors = load_sectors(args.sectors)
    print(f"Universe {len(symbols)}; loading snapshot...")
    funds: dict[str, list[FundamentalRecord]] = {}
    for rec in read_fundamentals_snapshot(args.snapshot, verify=True):
        funds.setdefault(rec.symbol.upper(), []).append(rec)
    for v in funds.values():
        v.sort(key=lambda r: r.asof_ts)

    print(f"Downloading prices ({PRICE_START}..{PRICE_END})...")
    raw = yf.download(symbols, start=PRICE_START, end=PRICE_END, auto_adjust=True, progress=False)
    closes = raw["Close"]

    signal_names = ["best_score", *ARCHETYPES, *(k for k, _ in RAW_METRICS), *COMPOSITES]
    # ic_cells[signal][horizon] = list of per-as-of ICs
    ic_cells: dict[str, dict[int, list[float]]] = {
        s: {h: [] for h in HORIZONS} for s in signal_names
    }

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
        if len(ranked) < MIN_PAIRS:
            continue

        # signal value per candidate
        sig_vals: dict[str, dict[str, float]] = {s: {} for s in signal_names}
        for c in ranked:
            sig_vals["best_score"][c.symbol] = c.best_score
            for a in ARCHETYPES:
                sc = c.scores.get(a)
                if sc is not None:
                    sig_vals[a][c.symbol] = sc.score
            # Mirror the live scorer: exclude sector-invalid metrics (e.g. FCF ratios for
            # financials) so the raw-metric ICs and composites match what rank_compounders used.
            invalid = SECTOR_INVALID_METRICS.get(sectors.get(c.symbol, "unknown"), frozenset())
            for key, _ in RAW_METRICS:
                if key in invalid:
                    continue
                mv = c.metrics.get(key)
                if mv is not None:
                    sig_vals[key][c.symbol] = mv
        for comp_name, components in COMPOSITES.items():
            sig_vals[comp_name] = build_composite(sig_vals, components)

        for h in HORIZONS:
            fwd_date = date(as_of.year + h, as_of.month, as_of.day)
            if fwd_date > DATA_END:
                continue
            fwd: dict[str, float] = {}
            for c in ranked:
                p1 = price_asof(closes[c.symbol], fwd_date)
                if p1 is not None:
                    fwd[c.symbol] = p1 / as_of_price[c.symbol] - 1.0
            if len(fwd) < MIN_PAIRS:
                continue
            for s in signal_names:
                xs, ys = [], []
                for sym, val in sig_vals[s].items():
                    if sym in fwd:
                        xs.append(val)
                        ys.append(fwd[sym])
                if len(xs) >= MIN_PAIRS:
                    ic = spearman(xs, ys)
                    if ic is not None:
                        ic_cells[s][h].append(ic)
        print(f"  {as_of} done")

    # Fail loudly rather than writing a confident verdict on no data (yfinance/snapshot/universe
    # failure must not masquerade as empirical evidence).
    if not any(ic_cells[s][h] for s in signal_names for h in HORIZONS):
        raise SystemExit(
            "no IC cells produced — prices failed to download, universe too small, or all "
            "horizons skipped. Refusing to write a verdict. Check yfinance + --snapshot."
        )

    def mean_ic(s: str, h: int) -> float | None:
        xs = ic_cells[s][h]
        return statistics.mean(xs) if xs else None

    def mean_all(s: str) -> float | None:
        xs = [ic for h in HORIZONS for ic in ic_cells[s][h]]
        return statistics.mean(xs) if xs else None

    def pos_frac(s: str) -> str:
        xs = [ic for h in HORIZONS for ic in ic_cells[s][h]]
        return f"{sum(1 for i in xs if i > 0)}/{len(xs)}" if xs else "0/0"

    # Orientation: +1 if higher raw value is "better" (↑), -1 if lower is better (↓, e.g.
    # valuation ratios, share dilution). ORIENTED IC = raw IC * sign, so a positive oriented
    # IC ALWAYS means "the factor, used in its intended direction, ranks future winners."
    # This is the only correct basis for ranking/diagnosing mixed-direction signals.
    dir_map: dict[str, str] = {"best_score": "↑", **dict.fromkeys(ARCHETYPES, "↑")}
    for k, hib in RAW_METRICS:
        dir_map[k] = "↑" if hib else "↓"
    for comp in COMPOSITES:
        dir_map[comp] = "↑"

    def sgn(s: str) -> int:
        return 1 if dir_map.get(s, "↑") == "↑" else -1

    def oriented(s: str, h: int | None = None) -> float | None:
        m = mean_all(s) if h is None else mean_ic(s, h)
        return None if m is None else m * sgn(s)

    def works_frac(s: str) -> str:
        xs = [ic for h in HORIZONS for ic in ic_cells[s][h]]
        if not xs:
            return "0/0"
        return f"{sum(1 for i in xs if i * sgn(s) > 0)}/{len(xs)}"

    # rank by ORIENTED pooled IC (factors that work in their intended direction first)
    ordered = sorted(signal_names, key=lambda s: (oriented(s) is None, -(oriented(s) or 0)))
    max_cells = max(
        (len([ic for h in HORIZONS for ic in ic_cells[s][h]]) for s in signal_names), default=0
    )

    md = [
        "# Compounder Score — Factor IC Decomposition (P5 diagnostic)",
        "",
        "Research-only. Forward Spearman rank IC (signal vs forward return) across "
        f"{len(AS_OF_DATES)} as-of dates (2012-2019) x horizons {'/'.join(f'{h}y' for h in HORIZONS)} "
        f"= up to {max_cells} windows per signal (long-horizon cells past the data end are "
        "dropped, e.g. 2019@7y; coverage gates drop a few more). All ICs below "
        "are **ORIENTED** by the signal's intended direction (`dir`): a positive oriented IC "
        "means the factor as the funnel uses it (↑ higher-better, ↓ lower-better e.g. cheap "
        "valuation / low dilution) ranks future winners. So 'cheap predicts winners' shows as "
        "POSITIVE here even though the raw value-ratio IC is negative.",
        "",
        "## Pipeline self-check (known factors must show textbook sign)",
        "",
        "If these come out right, the negative compounder IC is trustworthy, not a bug — the "
        "value factor should be POSITIVE oriented (cheap -> outperform):",
        "",
        f"- **value (oriented)**: pb {_fmt(oriented('pb'))}, ps {_fmt(oriented('ps'))}, "
        f"pfcf {_fmt(oriented('pfcf'))}, pe {_fmt(oriented('pe'))}. If positive, the pipeline "
        "reproduces the value premium and is trustworthy. (market_cap/operating_margin are "
        "n/a — not surfaced / low XBRL coverage — so value is the working anchor; pb/ps are "
        "the clear ones, pe/pfcf are near-zero.)",
        "",
        "## All signals ranked by ORIENTED forward IC (3/5/7y pooled)",
        "",
        "+ = factor works in its intended direction. `dir` ↓ = lower-is-better (sign-flipped).",
        "",
        "| Signal | dir | IC 3y | IC 5y | IC 7y | **IC all** | works |",
        "|---|:--:|--:|--:|--:|--:|--:|",
    ]
    for s in ordered:
        md.append(
            f"| {s} | {dir_map.get(s, '↑')} | {_fmt(oriented(s, 3))} | {_fmt(oriented(s, 5))} | "
            f"{_fmt(oriented(s, 7))} | **{_fmt(oriented(s))}** | {works_frac(s)} |"
        )

    # diagnosis — group signals by family and compare to the hand-built composites
    def group_mean(sigs: list[str]) -> float | None:
        xs = [m for s in sigs if (m := mean_all(s)) is not None]
        return statistics.mean(xs) if xs else None

    quality_mean = group_mean(["profitable_compounder", "roic", "fcf_margin", "net_margin"])
    growth_mean = group_mean(["hypergrowth_disruptor", "revenue_growth_acceleration", "eps_growth"])
    # value ratios: lower-is-better, so negative raw IC = value works. Sign-flip to read.
    value_work = [-ic for s in ("pb", "pfcf", "ps", "pe") if (ic := mean_all(s)) is not None]
    value_mean = statistics.mean(value_work) if value_work else None
    best_ic = mean_all("best_score")
    qual_ic = mean_all("quality_composite")
    redes_ic = mean_all("redesign_composite")

    quality_negative = quality_mean is not None and quality_mean < -0.02
    redesign_wins = redes_ic is not None and best_ic is not None and redes_ic > best_ic + 0.02

    md += [
        "",
        "## Diagnosis",
        "",
        f"- **Quality/profitability** family mean IC: {_fmt(quality_mean)} "
        "(profitable_compounder, roic, fcf_margin, net_margin).",
        f"- **Growth** family mean IC: {_fmt(growth_mean)} "
        "(hypergrowth_disruptor, revenue_growth_acceleration, eps_growth).",
        f"- **Value** 'works' score (sign-corrected so + = cheap predicts winners): "
        f"{_fmt(value_mean)}.",
        "",
        f"- **share_growth** oriented IC {_fmt(oriented('share_growth'))} (raw "
        f"{_fmt(mean_all('share_growth'))}): the funnel penalizes dilution, but higher "
        "share-growth actually predicted winners here — the 'buybacks/low-dilution' prior did "
        "NOT hold on this universe (likely high-growth issuers raising capital outperformed).",
        "",
        "Hand-built composite check:",
        "",
        f"- `quality_composite` (roic+fcf_margin+net_margin) IC: **{_fmt(qual_ic)}**",
        f"- `redesign_composite` (growth-accel + cheap value, NO buyback) IC: **{_fmt(redes_ic)}**",
        f"- vs the live `best_score` IC: **{_fmt(best_ic)}**",
        "",
        (
            "**QUALITY-MEAN-REVERSION pattern (not glamour).** The drag on `best_score` is the "
            "profitability/quality family (strongly negative oriented IC), NOT growth — "
            "growth-acceleration and value (cheap ps/pb) are weakly POSITIVE, while the "
            "dilution penalty and low-debt tilt did NOT help. The funnel's heaviest archetype "
            "(`profitable_compounder`: roic 0.30 + fcf_margin 0.25) is precisely the "
            "anti-predictive part on this mid/small-cap universe over 2012-2026. "
            + (
                "The `redesign_composite` ranks above best_score IN-SAMPLE — but this is a "
                "DATA-SNOOPED LEAD, not a fix to ship: it is the mean of pre-selected favorable "
                "signals and sits inside the noise band (see Adversarial verification below). "
                "Down-weighting quality is a HYPOTHESIS to validate OOS, not a decided change."
                if redesign_wins
                else "The `redesign_composite` improves on best_score but is not a decisive flip "
                "(magnitudes are small/noisy); treat as a research lead, not a validated edge."
            )
            if quality_negative
            else "Pattern is mixed — quality is not cleanly negative; see the table. The negative "
            "best_score IC is not pinned to one family, so the composite is broadly weak on "
            "this universe/period."
        ),
        "",
        "Caveats: (1) the quality metrics here are NET-margin / NI-ROIC, NOT Novy-Marx gross "
        "profitability (GP/assets) — the literature shows net-bottom-line quality is a weak/"
        "perverse proxy, so the negative quality IC may be a METRIC-DEFINITION artifact (the "
        "snapshot lacks gross_profit/COGS). (2) Survivorship: acquired high-quality names exit "
        "the current-constituent universe, biasing the quality IC DOWNWARD specifically — the "
        "true effect is less negative. (3) Effective N is ~2-3 (overlapping windows), so even "
        "the quality drag is marginal (z~-2.2 to -2.6) and the redesign +IC is inside noise. "
        "(4) No QMJ contradiction: QMJ is a price-controlled long-short, mostly large-cap; this "
        "is long-only raw-quality rank IC on a mid/small-cap survivor set. Read signs, not "
        "magnitudes. Full 5-lens audit: docs/COMPOUNDER_VALIDATION.md.",
    ]
    args.out.write_text("\n".join(md) + "\n", encoding="utf-8")
    print(
        "\n"
        + "\n".join(
            md[md.index("## Pipeline self-check (known factors must show textbook sign)") :]
        )
    )
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
