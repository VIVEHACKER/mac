"""Information-content gate for the VIX peak-decline signal (signals/vix_peak.py).

Same protocol as scripts/vix_term_validation.py (shared block-permutation harness,
same pre-declared bars, declared 2026-06-13 before the run):
  (1) 21d forward SPY mean on condition days > unconditional, 63d-block permutation
      p < 0.05, and
  (2) 5d conditional mean >= unconditional (sign consistency).
Condition constants (stress peak >= 30, retreat to <= 80% of peak) were declared in
the module — no grid was searched. NO EDGE is a recordable outcome.

Runs PINNED by default (data/snapshots/vix-term-2026-06-12.csv — the same ^VIX/SPY
pin the vix_term gate wrote), so the default invocation is byte-reproducible.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.price_snapshot import read_price_snapshot  # noqa: E402
from scripts.vix_term_validation import (  # noqa: E402
    BLOCK,
    HORIZONS,
    N_PERMUTATIONS,
    block_permutation_pvalue,
)
from signals.vix_peak import DECLINE_RATIO, PEAK_WINDOW, STRESS_LEVEL  # noqa: E402

OUT_DIR = ROOT / "out"
DEFAULT_PIN = ROOT / "data" / "snapshots" / "vix-term-2026-06-12.csv"
SEED = 20260613


def main() -> int:
    parser = argparse.ArgumentParser(description="VIX peak-decline information gate.")
    parser.add_argument("--prices", type=Path, default=DEFAULT_PIN)
    parser.add_argument("--output", type=Path, default=OUT_DIR / "vix-peak-validation.md")
    args = parser.parse_args()

    closes = read_price_snapshot(args.prices, verify=True)
    vix = closes["^VIX"]
    spy = closes["SPY"]

    rolling_peak = vix.rolling(PEAK_WINDOW).max()
    condition_series = (rolling_peak >= STRESS_LEVEL) & (vix <= rolling_peak * DECLINE_RATIO)

    results: dict[int, tuple[float, float, float, int]] = {}
    for horizon in HORIZONS:
        fwd_series = spy.shift(-horizon) / spy - 1.0
        valid = fwd_series.notna() & rolling_peak.notna()
        cond = list(condition_series[valid])
        fwd = [float(x) for x in fwd_series[valid]]
        obs, uncond, p = block_permutation_pvalue(
            cond, fwd, block=BLOCK, n_perm=N_PERMUTATIONS, seed=SEED
        )
        results[horizon] = (obs, uncond, p, sum(cond))

    obs21, unc21, p21, _n21 = results[21]
    obs5, unc5, _p5, _ = results[5]
    bar1 = obs21 > unc21 and p21 < 0.05
    bar2 = obs5 >= unc5
    if bar1 and bar2:
        verdict = (
            "INFORMATIVE — peak-decline days carry forward-return information at the "
            "pre-declared bars. Status: validated ADVISORY regime flag. NOT wired to "
            "capital; any strategy use must pass its own walk-forward gate."
        )
    else:
        failed = [
            name for name, ok in [("bar1 21d p<0.05", bar1), ("bar2 5d sign", bar2)] if not ok
        ]
        verdict = (
            f"NO EDGE — failed: {', '.join(failed)}. The flag stays unvalidated advisory; "
            "recorded in the research ledger, not pursued."
        )

    pct = lambda x: f"{x * 100:+.2f}%"  # noqa: E731
    lines = [
        "# VIX peak-decline signal — information-content gate",
        "",
        f"Data (pinned): {args.prices.name}, {closes.index.min().date()} → "
        f"{closes.index.max().date()} ({len(closes)} days)",
        f"Condition: trailing {PEAK_WINDOW}d VIX peak >= {STRESS_LEVEL:.0f} AND "
        f"current <= {DECLINE_RATIO:.0%} of peak — "
        f"{int(condition_series.sum())} days ({condition_series.mean() * 100:.1f}%)",
        f"Test: block permutation (block {BLOCK}d, {N_PERMUTATIONS} perms, seed {SEED}).",
        "",
        "## Results",
        "",
        "| Horizon | conditional mean | unconditional mean | edge | p (one-sided) | n cond |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for horizon in HORIZONS:
        obs, unc, p, n_cond = results[horizon]
        lines.append(
            f"| {horizon}d | {pct(obs)} | {pct(unc)} | {pct(obs - unc)} | {p:.4f} | {n_cond} |"
        )
    lines += [
        "",
        "## Verdict (bars pre-declared; constants declared in signals/vix_peak.py)",
        "",
        verdict,
        "",
        "## Honest caveats",
        "- Same episode-clustering caveat as vix_term: the effective sample is the number",
        "  of resolved stress EPISODES, far below the day count.",
        "- Two declared constants (30 / 0.8) — untuned, but they ARE researcher choices;",
        "  a different pair was never tried and never will be on this dataset.",
        "- Overlaps heavily with vix_term days (both fire around the same episodes);",
        "  the two flags are NOT independent evidence.",
    ]
    report = "\n".join(lines)
    args.output.write_text(report + "\n", encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
