"""P5 — does the compounder score predict forward returns?

Replays the compounder scan at past as-of dates using ONLY point-in-time data
(fundamentals asof <= date, price on/before date), then measures whether the score
ranks forward returns over MULTIPLE horizons (3y / 5y / 7y). The compounder thesis is
multi-year, so a 3y test alone is unfair — re-rating dominates 3y windows. This script
answers: does a longer hold reveal an edge a 3y window hides?

Two complementary signals per (as-of, horizon):
  1. Q5-Q1 quintile spread — median forward return of the top score-quintile minus the
     bottom. Robust to outliers; coarse (5 buckets).
  2. Spearman rank IC — rank correlation between score and forward return across ALL
     scored names. Uses every data point; the standard quant ranking-power metric.

Why SPREAD/IC, not absolute 10x hit-rate: the universe is *current* S&P 400+600
constituents (survivors), so absolute forward returns are survivorship-INFLATED. The
top-vs-bottom spread and the rank IC are fair because survivorship hits all quintiles
similarly — what's tested is the score's *relative* ranking power.

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

PRICE_START = "2011-01-01"
PRICE_END = "2026-06-01"
DATA_END = date(2026, 5, 28)  # forward date must have a close on/before this
AS_OF_DATES = [date(y, 6, 30) for y in range(2012, 2020)]  # 2012..2019
HORIZONS = [3, 5, 7]
N_QUINTILES = 5
MIN_NAMES = N_QUINTILES * 8  # need >=40 scored names with a forward price to bucket
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


def _avg_ranks(vals: list[float]) -> list[float]:
    """Average ranks (1-based), ties share the mean rank."""
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
    """Spearman rank correlation (Pearson on average-ranks). None if degenerate."""
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


def quintile_medians(pairs: list[tuple[float, float]]) -> list[tuple[int, int, float]]:
    """pairs = [(score, fwd_return)]. Returns [(quintile 1..5, n, median_fwd)] low->high score."""
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


def _fmt_pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:+.1f}%"


def _fmt_ic(x: float | None) -> str:
    return "n/a" if x is None else f"{x:+.3f}"


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

    print(f"Downloading prices ({PRICE_START}..{PRICE_END})...")
    raw = yf.download(symbols, start=PRICE_START, end=PRICE_END, auto_adjust=True, progress=False)
    closes = raw["Close"]

    # results keyed by (as_of, horizon)
    results: list[dict] = []
    for as_of in AS_OF_DATES:
        # PIT rank once per as-of (horizon-independent), then reuse across horizons.
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
        if len(ranked) < MIN_NAMES:
            print(f"  {as_of}: only {len(ranked)} ranked — skip")
            continue
        scores = {c.symbol: c.best_score for c in ranked}
        order_desc = [c.symbol for c in ranked]  # already sorted best-first

        for h in HORIZONS:
            fwd_date = date(as_of.year + h, as_of.month, as_of.day)
            if fwd_date > DATA_END:
                continue  # no forward price available -> don't fake a short horizon
            fwd: dict[str, float] = {}
            for sym in order_desc:
                p1 = price_asof(closes[sym], fwd_date)
                if p1 is not None:
                    fwd[sym] = p1 / as_of_price[sym] - 1.0
            pairs = [(scores[s], fwd[s]) for s in order_desc if s in fwd]
            if len(pairs) < MIN_NAMES:
                continue
            qm = quintile_medians(pairs)
            univ_med = statistics.median(r for _, r in pairs)
            top = [fwd[s] for s in order_desc[:WATCHLIST_N] if s in fwd]
            results.append(
                {
                    "as_of": as_of,
                    "fwd_date": fwd_date,
                    "h": h,
                    "n": len(pairs),
                    "q5_q1": qm[-1][2] - qm[0][2],
                    "ic": spearman([p[0] for p in pairs], [p[1] for p in pairs]),
                    "univ_med": univ_med,
                    "top_med": statistics.median(top) if top else None,
                    "top_excess": (statistics.median(top) - univ_med) if top else None,
                }
            )
            r = results[-1]
            print(
                f"  {as_of}->{fwd_date} h={h}y n={r['n']} "
                f"Q5-Q1={_fmt_pct(r['q5_q1'])} IC={_fmt_ic(r['ic'])} "
                f"top{WATCHLIST_N}excess={_fmt_pct(r['top_excess'])}"
            )

    # Fail loudly rather than writing a confident "no edge" verdict on no data (a yfinance /
    # snapshot / universe failure must not masquerade as empirical evidence).
    if not results:
        raise SystemExit(
            "no forward windows produced — prices failed to download, universe too small, or "
            "all horizons skipped. Refusing to write a verdict. Check yfinance + --snapshot."
        )

    # ---- aggregate per horizon ----
    def agg(h: int) -> dict:
        rows = [r for r in results if r["h"] == h]
        spreads = [r["q5_q1"] for r in rows]
        ics = [r["ic"] for r in rows if r["ic"] is not None]
        tex = [r["top_excess"] for r in rows if r["top_excess"] is not None]
        return {
            "h": h,
            "k": len(rows),
            "mean_spread": statistics.mean(spreads) if spreads else None,
            "spread_pos": sum(1 for s in spreads if s > 0),
            "mean_ic": statistics.mean(ics) if ics else None,
            "ic_pos": sum(1 for i in ics if i > 0),
            "n_ic": len(ics),
            "mean_top_excess": statistics.mean(tex) if tex else None,
            "top_pos": sum(1 for t in tex if t > 0),
            "n_top": len(tex),
        }

    aggs = [agg(h) for h in HORIZONS]

    def horizon_label(a: dict) -> str:
        ic, sp, tex = a["mean_ic"], a["mean_spread"], a["mean_top_excess"]
        if ic is None:
            return "no data"
        frac_ic = a["ic_pos"] / a["n_ic"] if a["n_ic"] else 0
        if ic >= 0.05 and frac_ic >= 0.75 and (tex or 0) > 0:
            return "EDGE"
        if ic >= 0.02 and frac_ic >= 0.6:
            return "weak"
        if ic <= -0.02 or (sp or 0) < 0:
            return "negative"
        return "none"

    labels = {a["h"]: horizon_label(a) for a in aggs}

    # ---- markdown ----
    md = [
        "# Compounder Score — Forward-Return Validation (P5)",
        "",
        "Research-only. Does NOT constitute investment advice.",
        "",
        f"PIT replay at {len(AS_OF_DATES)} as-of dates (2012-2019) x horizons "
        f"({'/'.join(f'{h}y' for h in HORIZONS)}) = **{len(results)} windows actually produced** "
        f"(of {len(AS_OF_DATES) * len(HORIZONS)} possible; long-horizon cells past the data end "
        "are dropped, e.g. 2019@7y). Ranking uses only fundamentals with asof <= date and price "
        "on/before date; the forward return is the measured outcome. Rank is computed once per "
        "as-of and reused across horizons.",
        "",
        "**Survivorship caveat:** the universe is *current* S&P 400+600 constituents, so "
        "absolute forward returns are inflated (failed/delisted names absent). The **Q5-Q1 "
        "spread** and **Spearman rank IC** are the fair signals — survivorship hits all "
        "quintiles similarly, so what's tested is the score's *relative* ranking power.",
        "",
        "## Spearman rank IC (score vs forward return; the cleaner signal, uses all names)",
        "",
        "| As-of | " + " | ".join(f"{h}y" for h in HORIZONS) + " |",
        "|---|" + "|".join("--:" for _ in HORIZONS) + "|",
    ]
    by_asof = {a: {r["h"]: r for r in results if r["as_of"] == a} for a in AS_OF_DATES}
    for a in AS_OF_DATES:
        if not by_asof[a]:
            continue
        cells = " | ".join(
            _fmt_ic(by_asof[a][h]["ic"]) if h in by_asof[a] else "—" for h in HORIZONS
        )
        md.append(f"| {a} | {cells} |")

    md += [
        "",
        "## Q5-Q1 quintile spread (median fwd return: top score-quintile minus bottom)",
        "",
        "| As-of | " + " | ".join(f"{h}y" for h in HORIZONS) + " |",
        "|---|" + "|".join("--:" for _ in HORIZONS) + "|",
    ]
    for a in AS_OF_DATES:
        if not by_asof[a]:
            continue
        cells = " | ".join(
            _fmt_pct(by_asof[a][h]["q5_q1"]) if h in by_asof[a] else "—" for h in HORIZONS
        )
        md.append(f"| {a} | {cells} |")

    md += [
        "",
        "## Aggregate per horizon",
        "",
        "| Horizon | windows | mean IC | IC>0 | mean Q5-Q1 | spread>0 | mean top-30 excess | top>0 | label |",
        "|---|--:|--:|--:|--:|--:|--:|--:|---|",
    ]
    for ag in aggs:
        md.append(
            f"| {ag['h']}y | {ag['k']} | {_fmt_ic(ag['mean_ic'])} | {ag['ic_pos']}/{ag['n_ic']} | "
            f"{_fmt_pct(ag['mean_spread'])} | {ag['spread_pos']}/{ag['k']} | "
            f"{_fmt_pct(ag['mean_top_excess'])} | {ag['top_pos']}/{ag['n_top']} | **{labels[ag['h']]}** |"
        )

    # Does the edge grow with horizon?
    ics_by_h = {a["h"]: a["mean_ic"] for a in aggs if a["mean_ic"] is not None}
    rising = len(ics_by_h) >= 2 and all(
        b > a for a, b in zip(list(ics_by_h.values()), list(ics_by_h.values())[1:], strict=False)
    )
    best_h = max(ics_by_h, key=lambda h: ics_by_h[h]) if ics_by_h else None
    any_edge = any(labels[h] == "EDGE" for h in HORIZONS)

    md += [
        "",
        "## Verdict",
        "",
        (
            f"**A forward edge appears at the {best_h}y horizon** "
            f"(mean IC {_fmt_ic(ics_by_h.get(best_h)) if best_h else 'n/a'}): the multi-year "
            "compounding thesis is supported — the 3y NULL was a horizon artifact. Use the "
            "ranking as a return signal at long holds, still gated by human conviction."
            if any_edge
            else "**No reliable forward edge at any horizon (3/5/7y).** The compounder score's "
            "rank IC stays near zero across horizons, so the 3y NULL is NOT merely a "
            "horizon artifact — quality/growth precursors appear largely priced in even at "
            "5-7y on this universe. **Use the funnel as an evidence-backed SCREEN to seed "
            "human conviction, NOT as a return predictor.**"
        ),
        "",
        (
            f"IC trend across horizons: {' -> '.join(f'{h}y {_fmt_ic(ics_by_h.get(h))}' for h in HORIZONS if h in ics_by_h)}"
            f" ({'rising with horizon — longer holds help' if rising else 'no clear rise with horizon'})."
        ),
        "",
        "Caveats (cut both ways): survivorship (above) puts a floor under ALL quintiles and may "
        "compress the spread; overlapping windows are not independent (low power); no transaction "
        "costs/taxes; PIT index membership not reconstructed (current constituents only); IC over "
        "multi-year returns is noisier than monthly factor IC. The honest reading is about the "
        "*direction and consistency* of the signal, not precise magnitudes.",
    ]
    args.out.write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n" + "\n".join(md[md.index("## Aggregate per horizon") :]))
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
