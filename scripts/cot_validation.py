"""Information-content gate for the COT positioning-extreme signal (signals/cot_extreme.py).

COT is weekly and TWO-SIDED (crowded-long → bet short, crowded-short → bet long), so the
VIX one-sided test does not transfer directly. Instead we build a per-week contrarian
signed forward return and ask whether EXTREME positioning weeks beat the always-on
contrarian tilt:

    r_contrarian[w] = -sign(cot_index[w] - 50) * fwd_spy[w]
        (speculators lean long  -> contrarian short -> earn -fwd
         speculators lean short -> contrarian long  -> earn +fwd)

    condition[w]   = week is an EXTREME (cot_index >= 90 or <= 10)

Pre-declared bars (declared 2026-06-13, before the run — thresholds live in
signals/cot_extreme.py, no grid searched):
  (1) 13w: mean(r_contrarian | extreme) > mean(r_contrarian | all weeks), with
      block-permutation p < 0.05 (26-week blocks — COT autocorrelation is months-long), and
  (2) 4w: same direction (>=) for sign consistency.
Beating the always-on tilt is the right null: it isolates whether the EXTREME threshold
adds information beyond a permanent contrarian lean. NO EDGE is a recordable outcome.

Data: COT S&P 500 fetched + pinned (content hash in the report); SPY reused from the
existing vix-term price pin (no extra download). ADVISORY only regardless of verdict.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.ingest.cot_sp500 import fetch_cot_sp500  # noqa: E402
from data.price_snapshot import read_price_snapshot  # noqa: E402
from scripts.vix_term_validation import N_PERMUTATIONS, block_permutation_pvalue  # noqa: E402
from signals.cot_extreme import EXTREME_HIGH, EXTREME_LOW, INDEX_WINDOW, cot_index  # noqa: E402

OUT_DIR = ROOT / "out"
SNAP_DIR = ROOT / "data" / "snapshots"
DEFAULT_SPY_PIN = SNAP_DIR / "vix-term-2026-06-12.csv"
HORIZONS_WEEKS = (4, 13)
BLOCK_WEEKS = 26
SEED = 20260613


def _rolling_cot_index(net: pd.Series, window: int) -> pd.Series:
    """COT index (0–100) for each week from the trailing ``window`` of net positions."""
    return net.rolling(window).apply(lambda w: cot_index(list(w), window=window) or 50.0, raw=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="COT positioning-extreme information gate.")
    parser.add_argument("--cot", type=Path, default=None, help="pinned COT CSV (else fetch+pin)")
    parser.add_argument("--spy", type=Path, default=DEFAULT_SPY_PIN)
    parser.add_argument("--start-year", type=int, default=2010)
    parser.add_argument("--end-year", type=int, default=2024)
    parser.add_argument("--output", type=Path, default=OUT_DIR / "cot-validation.md")
    args = parser.parse_args()

    if args.cot:
        cot_df = pd.read_csv(args.cot, parse_dates=["date"]).set_index("date")
        cot_src = args.cot.name
    else:
        cot_df = fetch_cot_sp500(args.start_year, args.end_year)
        out_csv = SNAP_DIR / f"cot-sp500-{cot_df.index.max().date()}.csv"
        cot_df.to_csv(out_csv)
        cot_src = out_csv.name
        print(f"Pinned COT -> {out_csv.name}")
    cot_sha = hashlib.sha256(cot_df.to_csv().encode()).hexdigest()[:12]

    # Weekly SPY (Friday close) from the reused price pin; explicit index names so the
    # as-of joins below never depend on the snapshot's index naming.
    spy_weekly = read_price_snapshot(args.spy, verify=True)["SPY"].dropna().resample("W-FRI").last()
    spy_weekly = spy_weekly.dropna()
    spy_weekly.index.name = "wdate"

    cot_idx = _rolling_cot_index(cot_df["nc_net"], INDEX_WINDOW).dropna()
    cot_idx.index.name = "date"
    cot_tbl = cot_idx.rename("idx").reset_index().sort_values("date")

    results: dict[int, tuple[float, float, float, int]] = {}
    for horizon in HORIZONS_WEEKS:
        fwd_tbl = (spy_weekly.shift(-horizon) / spy_weekly - 1.0).rename("fwd").reset_index()
        merged = pd.merge_asof(
            cot_tbl,
            fwd_tbl.sort_values("wdate"),
            left_on="date",
            right_on="wdate",
            direction="forward",
        ).dropna(subset=["fwd"])
        idx = merged["idx"].to_numpy()
        fwd = merged["fwd"].to_numpy()
        # Continuous contrarian signed return; condition = extreme positioning week.
        lean = [(-1.0 if v > 50.0 else 1.0) for v in idx]
        r_contrarian = [lean[i] * float(fwd[i]) for i in range(len(fwd))]
        condition = [bool(v >= EXTREME_HIGH or v <= EXTREME_LOW) for v in idx]
        if not any(condition):
            raise SystemExit("no extreme weeks in sample — cannot test")
        obs, uncond, p = block_permutation_pvalue(
            condition, r_contrarian, block=BLOCK_WEEKS, n_perm=N_PERMUTATIONS, seed=SEED
        )
        results[horizon] = (obs, uncond, p, sum(condition))

    obs13, unc13, p13, n13 = results[13]
    obs4, unc4, _p4, _ = results[4]
    bar1 = obs13 > unc13 and p13 < 0.05
    bar2 = obs4 >= unc4
    if bar1 and bar2:
        verdict = (
            "INFORMATIVE — extreme COT positioning beats the always-on contrarian tilt at "
            "the pre-declared bars. Validated ADVISORY signal; NOT wired to capital "
            "(strategy use needs its own walk-forward gate)."
        )
    else:
        failed = [
            name for name, ok in [("bar1 13w p<0.05", bar1), ("bar2 4w sign", bar2)] if not ok
        ]
        verdict = (
            f"NO EDGE — failed: {', '.join(failed)}. Extreme positioning adds no information "
            "beyond a constant contrarian lean. Recorded in the research ledger, not pursued."
        )

    pct = lambda x: f"{x * 100:+.2f}%"  # noqa: E731
    lines = [
        "# COT positioning-extreme signal — information-content gate",
        "",
        f"COT (pinned): {cot_src} sha {cot_sha}… — S&P 500 Consolidated, "
        f"{cot_df.index.min().date()} → {cot_df.index.max().date()} ({len(cot_df)} weekly reports)",
        f"SPY (reused pin): {args.spy.name} | COT index window {INDEX_WINDOW}w; "
        f"extreme = >= {EXTREME_HIGH:.0f} or <= {EXTREME_LOW:.0f}",
        f"Test: contrarian-signed fwd return, extreme vs all weeks; block permutation "
        f"({BLOCK_WEEKS}w blocks, {N_PERMUTATIONS} perms, seed {SEED})",
        "",
        "## Results (contrarian signed return)",
        "",
        "| Horizon | extreme mean | all-weeks mean | edge | p (one-sided) | n extreme |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for horizon in HORIZONS_WEEKS:
        obs, unc, p, n_cond = results[horizon]
        lines.append(
            f"| {horizon}w | {pct(obs)} | {pct(unc)} | {pct(obs - unc)} | {p:.4f} | {n_cond} |"
        )
    lines += [
        "",
        "## Verdict (bars pre-declared; thresholds in signals/cot_extreme.py)",
        "",
        verdict,
        "",
        "## Honest caveats",
        "- Orthogonal data source (futures positioning, not price/vol) — genuinely",
        "  independent of the VIX flags, which is the point of adding it.",
        "- Weekly, slow-moving: ~15 years is only ~780 observations and far fewer",
        "  independent EXTREME episodes; magnitudes are rough.",
        "- as-of forward join (COT Tuesday → next SPY weekly close) avoids look-ahead but",
        "  introduces up-to-a-week timing slack.",
    ]
    report = "\n".join(lines)
    args.output.write_text(report + "\n", encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
