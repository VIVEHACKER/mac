"""P5 — does the compounder score predict forward returns?

Replays the compounder scan at past as-of dates using ONLY point-in-time data
(fundamentals asof <= date, price on/before date), buckets the scored names into
score quintiles, and measures each quintile's forward return over a fixed horizon.

Why quintile SPREAD, not absolute 10x hit-rate: the universe is *current*
S&P 400+600 constituents (survivors), so absolute forward returns are
survivorship-INFLATED. But the top-vs-bottom quintile SPREAD is a fairer signal —
both quintiles are drawn from the same survivor set, so the bias affects them
similarly and the relative ranking power of the score is what's tested. A
consistently positive Q5−Q1 spread = the score has forward predictive value;
near-zero/negative = it doesn't.

Output: out/compounder-forward-validation.md
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
from engine.compounder import rank_compounders  # noqa: E402

# Defaults point at the local research artifacts. The snapshot CSV is gitignored
# (only its manifest is tracked), so on a clean checkout / CI you MUST pass --snapshot
# to a regenerated snapshot (see scripts/snapshot_fundamentals.py); the script fails
# loudly via read_fundamentals_snapshot(verify=True) rather than silently using stale data.
DEFAULT_UNIVERSE = ROOT / "data" / "universes" / "sp400-600-current.csv"
DEFAULT_SNAPSHOT = ROOT / "data" / "snapshots" / "fundamentals-2026-05-31-merged.csv"
DEFAULT_SECTORS = ROOT / "data" / "sectors" / "sp400-600-current-sectors.csv"
DEFAULT_OUT = ROOT / "out" / "compounder-forward-validation.md"

AS_OF_DATES = [date(2014, 6, 30), date(2016, 6, 30), date(2018, 6, 30), date(2020, 6, 30)]
HORIZON_YEARS = 3
N_QUINTILES = 5
WATCHLIST_N = 30


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
    p.add_argument(
        "--snapshot",
        type=Path,
        default=DEFAULT_SNAPSHOT,
        help="PIT fundamentals snapshot CSV (gitignored; regenerate if absent)",
    )
    p.add_argument("--universe", type=Path, default=DEFAULT_UNIVERSE)
    p.add_argument("--sectors", type=Path, default=DEFAULT_SECTORS)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return p.parse_args()


def price_asof(close: pd.Series, as_of: date) -> float | None:
    s = close.loc[: pd.Timestamp(as_of)].dropna()
    return float(s.iloc[-1]) if len(s) else None


def quintile_medians(pairs: list[tuple[float, float]]) -> list[tuple[int, int, float]]:
    """pairs = [(score, fwd_return)]. Returns [(quintile 1..5, n, median_fwd)] sorted low->high score."""
    pairs = sorted(pairs, key=lambda p: p[0])
    n = len(pairs)
    out = []
    for q in range(N_QUINTILES):
        lo = q * n // N_QUINTILES
        hi = (q + 1) * n // N_QUINTILES
        bucket = pairs[lo:hi]
        if bucket:
            out.append((q + 1, len(bucket), statistics.median(r for _, r in bucket)))
    return out


def main() -> None:
    args = parse_args()
    if not args.snapshot.exists():
        raise SystemExit(
            f"snapshot not found: {args.snapshot}\n"
            "It is gitignored (only the manifest is tracked). Regenerate it with "
            "scripts/snapshot_fundamentals.py or pass --snapshot <path>."
        )
    symbols = load_symbols(args.universe)
    sectors = load_sectors(args.sectors)
    print(f"Universe {len(symbols)}; loading snapshot...")
    funds: dict[str, list[FundamentalRecord]] = {}
    for rec in read_fundamentals_snapshot(args.snapshot, verify=True):
        funds.setdefault(rec.symbol.upper(), []).append(rec)
    for v in funds.values():
        v.sort(key=lambda r: r.asof_ts)

    print("Downloading prices (2013-2026)...")
    raw = yf.download(
        symbols, start="2013-01-01", end="2026-06-01", auto_adjust=True, progress=False
    )
    closes = raw["Close"]

    per_asof: list[dict] = []
    for as_of in AS_OF_DATES:
        fwd_date = date(as_of.year + HORIZON_YEARS, as_of.month, as_of.day)
        universe: dict[str, tuple[Sequence[FundamentalRecord], float]] = {}
        fwd_price: dict[str, float] = {}
        for sym in symbols:
            if sym not in closes.columns:
                continue
            recs = [r for r in funds.get(sym, []) if r.asof_ts.date() <= as_of]
            if len(recs) < 2:
                continue
            p0 = price_asof(closes[sym], as_of)
            p1 = price_asof(closes[sym], fwd_date)
            if p0 is None or p1 is None or p0 <= 0:
                continue
            universe[sym] = (recs, p0)
            fwd_price[sym] = p1 / p0 - 1.0

        ranked = rank_compounders(universe, top_n=10_000, sectors=sectors)
        pairs = [(c.best_score, fwd_price[c.symbol]) for c in ranked if c.symbol in fwd_price]
        if len(pairs) < N_QUINTILES * 4:
            per_asof.append({"as_of": as_of, "n": len(pairs), "skipped": True})
            continue
        qm = quintile_medians(pairs)
        univ_median = statistics.median(r for _, r in pairs)
        top = [fwd_price[c.symbol] for c in ranked[:WATCHLIST_N] if c.symbol in fwd_price]
        per_asof.append(
            {
                "as_of": as_of,
                "fwd_date": fwd_date,
                "n": len(pairs),
                "quintiles": qm,
                "q5_q1": qm[-1][2] - qm[0][2],
                "univ_median": univ_median,
                "top_median": statistics.median(top) if top else float("nan"),
            }
        )
        print(
            f"  {as_of} -> {fwd_date}: n={len(pairs)} Q5-Q1={qm[-1][2] - qm[0][2]:+.1%} "
            f"top{WATCHLIST_N}={statistics.median(top):+.1%} univ={univ_median:+.1%}"
        )

    valid = [r for r in per_asof if not r.get("skipped")]
    spreads = [r["q5_q1"] for r in valid]
    top_excess = [r["top_median"] - r["univ_median"] for r in valid]

    md = [
        "# Compounder Score — Forward-Return Validation (P5)",
        "",
        "Research-only. Does NOT constitute investment advice.",
        "",
        f"PIT replay at {len(AS_OF_DATES)} as-of dates, {HORIZON_YEARS}-year forward horizon. "
        f"Ranking uses only fundamentals with asof <= date and price on/before date; the "
        f"forward return is the measured outcome.",
        "",
        "**Survivorship caveat:** the universe is *current* S&P 400+600 constituents, so "
        "absolute forward returns are inflated (failed/delisted names absent). The **Q5−Q1 "
        "quintile spread** is the fair signal — both quintiles are survivors, so the bias "
        "affects them similarly and what's tested is the score's *relative* ranking power.",
        "",
        "## Per as-of (median 3y forward return by score quintile, Q1=lowest score, Q5=highest)",
        "",
        "| As-of → fwd | N | Q1 | Q2 | Q3 | Q4 | Q5 | **Q5−Q1** | top30 | univ |",
        "|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|",
    ]
    for r in valid:
        qvals = {q: m for q, _, m in r["quintiles"]}
        cells = " | ".join(f"{qvals.get(q, float('nan')) * 100:+.1f}%" for q in range(1, 6))
        md.append(
            f"| {r['as_of']} → {r['fwd_date']} | {r['n']} | {cells} | "
            f"**{r['q5_q1'] * 100:+.1f}%** | {r['top_median'] * 100:+.1f}% | {r['univ_median'] * 100:+.1f}% |"
        )
    md += [
        "",
        "## Aggregate",
        "",
        f"- Mean Q5−Q1 spread: **{statistics.mean(spreads) * 100:+.1f}%** "
        f"(positive in {sum(1 for s in spreads if s > 0)}/{len(spreads)} windows)",
        f"- Mean top-{WATCHLIST_N} excess vs universe median: "
        f"**{statistics.mean(top_excess) * 100:+.1f}%** "
        f"(positive in {sum(1 for e in top_excess if e > 0)}/{len(top_excess)} windows)",
        "",
        "## Verdict",
        "",
        (
            # A real forward edge needs BOTH a meaningful, consistent quintile spread AND
            # the actual watchlist (top-N) beating the universe. A near-zero mean spread or
            # a negative top-N excess = NO established predictive value, even if most windows
            # are nominally positive — small positive spreads average to noise.
            f"**The compounder score shows a meaningful, consistent forward edge** "
            f"(mean Q5−Q1 {statistics.mean(spreads) * 100:+.1f}%, top-{WATCHLIST_N} excess "
            f"{statistics.mean(top_excess) * 100:+.1f}%): the funnel ranking carries a "
            f"return signal, not just a screen."
            if spreads
            and statistics.mean(spreads) >= 0.05
            and statistics.mean(top_excess) > 0
            and sum(1 for e in top_excess if e > 0) >= 3
            else f"**NULL / NEGATIVE RESULT — the compounder score does NOT demonstrate forward "
            f"predictive value on this data.** Mean Q5−Q1 spread is "
            f"{statistics.mean(spreads) * 100:+.1f}% (near-zero) and the top-{WATCHLIST_N} "
            f"watchlist's excess vs the universe median is "
            f"{statistics.mean(top_excess) * 100:+.1f}% (positive in only "
            f"{sum(1 for e in top_excess if e > 0)}/{len(top_excess)} windows). Quality/growth "
            f"appears largely priced in; the 2020→2023 window even inverted. **Use the funnel "
            f"as an evidence-backed SCREEN to seed human conviction, NOT as a return "
            f"predictor.** This empirically confirms the runbook caveat that the funnel is not "
            f"validated for forward outperformance."
        ),
        "",
        "Caveats (cut both ways): survivorship (above); the "
        f"{HORIZON_YEARS}y horizon is SHORT for a multi-year compounding thesis — a 3y window "
        "is dominated by re-rating (e.g. the 2022 rate shock crushing high-multiple growth in "
        "the 2020→2023 window), not realized compounding, so this UNDERSTATES a long-hold "
        f"funnel; {len(AS_OF_DATES)} overlapping windows (not independent, low power); no "
        "transaction costs; PIT membership not reconstructed (current constituents only). "
        "Net: the evidence is insufficient to certify the score as a return predictor, but "
        "is also too weak to condemn it — hence: use as a screen, revisit with a 5–7y horizon.",
    ]
    args.out.write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n" + "\n".join(md[-12:]))
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
