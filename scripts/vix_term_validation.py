"""Information-content gate for the VIX term-structure signal (signals/vix_term.py).

Question (declared before running, 2026-06-12): do BACKWARDATION days (VIX/VIX3M > 1)
carry information about forward SPY returns? The literature hypothesis is mean
reversion after panic — conditional forward returns ABOVE unconditional.

Pre-declared bars — the signal is INFORMATIVE only if BOTH hold:
  (1) 21d forward SPY mean on backwardation days exceeds the unconditional 21d mean,
      with block-permutation p < 0.05 (blocks of 63 trading days, so clustered stress
      episodes are shuffled as units — daily shuffling would fake independence), and
  (2) the 5d conditional mean is also >= the unconditional 5d mean (sign consistency;
      no significance required).
Anything less -> NO EDGE, recorded in the research ledger. Either way the signal stays
ADVISORY: information content alone never wires it to capital.

Reproducibility: the fetched ^VIX/^VIX3M/SPY closes are written as a content-hashed
price snapshot on first run; pass --prices to re-run byte-identically. Permutations use
a fixed seed.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.price_snapshot import read_price_snapshot, write_price_snapshot  # noqa: E402
from signals.vix_term import BACKWARDATION_THRESHOLD, term_ratio  # noqa: E402

OUT_DIR = ROOT / "out"
SNAP_DIR = ROOT / "data" / "snapshots"
HORIZONS = (5, 21)
BLOCK = 63  # permutation block length: stress episodes move as multi-week units
N_PERMUTATIONS = 5000
SEED = 20260612


def block_permutation_pvalue(
    condition: list[bool], fwd: list[float], *, block: int, n_perm: int, seed: int
) -> tuple[float, float, float]:
    """One-sided p for (conditional mean − unconditional mean) > 0 under block shuffling.

    Returns (observed_conditional_mean, unconditional_mean, p_value). The condition
    labels are permuted in contiguous blocks so autocorrelated stress episodes stay
    intact; the forward-return series is left in place.
    """
    n = len(fwd)
    cond_idx = [i for i, flag in enumerate(condition) if flag]
    if not cond_idx:
        raise ValueError("no condition days — nothing to test")
    uncond_mean = sum(fwd) / n
    obs = sum(fwd[i] for i in cond_idx) / len(cond_idx)

    blocks = [list(range(start, min(start + block, n))) for start in range(0, n, block)]
    rng = random.Random(seed)
    hits = 0
    n_cond = len(cond_idx)
    for _ in range(n_perm):
        order = blocks[:]
        rng.shuffle(order)
        flat = [i for blk in order for i in blk]
        # The same NUMBER of condition days, relocated block-wise.
        sample = flat[:n_cond]
        perm_mean = sum(fwd[i] for i in sample) / n_cond
        if perm_mean - uncond_mean >= obs - uncond_mean:
            hits += 1
    return obs, uncond_mean, (hits + 1) / (n_perm + 1)


def main() -> int:
    parser = argparse.ArgumentParser(description="VIX term-structure information gate.")
    parser.add_argument("--prices", type=Path, default=None, help="pinned snapshot CSV re-run")
    parser.add_argument("--start", default="2010-01-01")
    parser.add_argument("--output", type=Path, default=OUT_DIR / "vix-term-validation.md")
    args = parser.parse_args()

    if args.prices:
        closes = read_price_snapshot(args.prices, verify=True)
        pin_name = args.prices.name
    else:
        import yfinance as yf

        raw = yf.download(
            ["^VIX", "^VIX3M", "SPY"], start=args.start, auto_adjust=True, progress=False
        )
        closes = raw["Close"].dropna()
        stamp = str(closes.index.max().date())
        manifest = write_price_snapshot(closes, SNAP_DIR, f"vix-term-{stamp}")
        pin_name = f"vix-term-{stamp}.csv (sha {manifest.sha256[:12]}…)"
        print(f"Pinned fetched closes -> {pin_name}")

    ratio = closes.apply(lambda row: term_ratio(row["^VIX"], row["^VIX3M"]), axis=1)
    condition_series = ratio > BACKWARDATION_THRESHOLD
    spy = closes["SPY"]

    results: dict[int, tuple[float, float, float, int]] = {}
    for horizon in HORIZONS:
        fwd_series = spy.shift(-horizon) / spy - 1.0
        valid = fwd_series.notna()
        cond = list(condition_series[valid])
        fwd = [float(x) for x in fwd_series[valid]]
        obs, uncond, p = block_permutation_pvalue(
            cond, fwd, block=BLOCK, n_perm=N_PERMUTATIONS, seed=SEED
        )
        results[horizon] = (obs, uncond, p, sum(cond))

    obs21, unc21, p21, n21 = results[21]
    obs5, unc5, _p5, _ = results[5]
    bar1 = obs21 > unc21 and p21 < 0.05
    bar2 = obs5 >= unc5
    if bar1 and bar2:
        verdict = (
            "INFORMATIVE — backwardation days carry forward-return information at the "
            "pre-declared bars. Status: validated ADVISORY regime flag. Still NOT wired "
            "to capital; any strategy use must pass its own walk-forward gate."
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
        "# VIX term-structure signal — information-content gate",
        "",
        f"Data: ^VIX/^VIX3M/SPY daily, {closes.index.min().date()} → {closes.index.max().date()} "
        f"({len(closes)} days) | pin: {pin_name}",
        f"Condition: VIX/VIX3M > {BACKWARDATION_THRESHOLD} (backwardation) — "
        f"{int(condition_series.sum())} days ({condition_series.mean() * 100:.1f}%)",
        f"Test: block permutation (block {BLOCK}d, {N_PERMUTATIONS} perms, seed {SEED}) — "
        "episodes shuffled as units to respect clustering.",
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
        "## Verdict (bars pre-declared in the module docstring)",
        "",
        verdict,
        "",
        "## Honest caveats",
        "- Overlapping forward windows + clustered episodes → the effective sample is the",
        f"  number of stress EPISODES, far below the day count; the {BLOCK}d blocks address",
        "  this but cannot manufacture more episodes. Treat magnitudes as rough.",
        "- 2010+ sample only (^VIX3M availability); excludes 2008 — the single biggest",
        "  backwardation episode is NOT in the test window.",
        "- Single pre-declared threshold (1.0): no grid was searched.",
    ]
    report = "\n".join(lines)
    args.output.write_text(report + "\n", encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
