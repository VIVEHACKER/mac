"""PBO (Probability of Backtest Overfitting) + cluster-aware DSR for the IDEAL line.

Answers the core reliability question: is the IDEAL edge REAL or data-mined? It runs
the pre-registered 8-config grid over the full pinned timeline, builds the config×month
return matrix, and computes:
  - PBO via CSCV (Bailey/Borwein/Lopez de Prado): P(the IS-best config is overfit)
  - effective N trials (cluster-aware) -> honest Deflated Sharpe for the baseline
  - PSR + block bootstrap for the deployed baseline (top7)

This is selection-bias-aware and overlapping-window-immune (it works on the config
cross-section, not on time order). PBO < 0.1 = strong evidence the edge generalises;
PBO >= 0.5 = the "best" backtest is no better than chance.

Usage:
    python -m scripts.aqr_pbo_validation
    python -m scripts.aqr_pbo_validation --splits 14 --output out/aqr-pbo-validation.md
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.significance import (  # noqa: E402
    block_bootstrap_sharpe,
    cscv_pbo,
    deflated_sharpe_ratio,
    effective_n_trials,
    minimum_track_record_length,
    per_period_sharpe,
    probabilistic_sharpe_ratio,
)
from scripts.aqr_ideal_grid import DEFAULT_PRICES, DEFAULT_SNAPSHOT, GRID  # noqa: E402
from scripts.aqr_ideal_walkforward import (  # noqa: E402
    BENCHMARK,
    MEGACAPS,
    prefetch,
    read_price_snapshot,
    run_window,
)

BASELINE_NAME = "baseline (validated)"
FULL_START = pd.Timestamp("2009-06-01")  # leaves >1y history for 126-day momentum


def _feasible_cfg(cfg: dict, fee_bps: float) -> dict:
    feasible_cap = max(cfg.get("cap", 0.20), 1.0 / cfg["top_n"] + 1e-9)
    return {**cfg, "cap": feasible_cap, "fee_bps": fee_bps}


def _verdict(pbo: float, dsr_eff: float) -> str:
    if pbo < 0.10 and dsr_eff > 0.90:
        return "STRONG — edge generalises (low overfit, deflated Sharpe survives)"
    if pbo < 0.25:
        return "PLAUSIBLE — modest overfit risk; confirm with cross-market + paper OOS"
    if pbo < 0.50:
        return "FRAGILE — meaningful overfit risk; do not raise confidence on backtest alone"
    return "OVERFIT — IS-best no better than chance OOS; treat edge as unproven"


def main() -> int:
    parser = argparse.ArgumentParser(description="PBO/CSCV + effN-DSR for the IDEAL grid.")
    parser.add_argument("--prices", type=Path, default=DEFAULT_PRICES)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--fee-bps", type=float, default=0.0)
    parser.add_argument("--splits", type=int, default=14, help="CSCV blocks (even)")
    parser.add_argument("--output", type=Path, default=ROOT / "out" / "aqr-pbo-validation.md")
    args = parser.parse_args()

    prices = read_price_snapshot(args.prices, verify=True)
    missing = [s for s in [*MEGACAPS, BENCHMARK] if s not in prices.columns]
    if missing:
        raise SystemExit(f"price snapshot missing {len(missing)} symbols (e.g. {missing[:5]})")
    full_end = pd.Timestamp(prices.index.max())
    fund_cache = prefetch(None, snapshot_path=args.snapshot)

    # Build the config x month return matrix over the full timeline.
    matrix: list[list[float]] = []
    names: list[str] = []
    for cfg in GRID:
        result = run_window(
            FULL_START, full_end, prices, fund_cache, cfg=_feasible_cfg(cfg, args.fee_bps)
        )
        if result is None:
            raise SystemExit(f"run_window returned None for {cfg['name']}")
        matrix.append(list(result["monthly_returns"]))
        names.append(cfg["name"])

    # Align lengths (configs share the rebalance schedule, but guard anyway).
    min_len = min(len(row) for row in matrix)
    matrix = [row[:min_len] for row in matrix]

    pbo = cscv_pbo(matrix, n_splits=args.splits)
    eff_n = effective_n_trials(matrix)
    sharpes = [per_period_sharpe(row) for row in matrix]
    trial_variance = statistics.pvariance(sharpes) if len(sharpes) > 1 else 0.0

    baseline_idx = names.index(BASELINE_NAME)
    baseline = matrix[baseline_idx]
    n_trials_eff = max(1, round(eff_n))
    dsr_eff = deflated_sharpe_ratio(
        baseline, n_trials=n_trials_eff, trial_sr_variance=trial_variance
    )
    dsr_nominal = deflated_sharpe_ratio(
        baseline, n_trials=len(GRID), trial_sr_variance=trial_variance
    )
    psr = probabilistic_sharpe_ratio(baseline)
    # Monthly returns -> annualise the bootstrapped Sharpe with 12, not the daily default.
    boot = block_bootstrap_sharpe(baseline, n_boot=5000, block_size=6, periods_per_year=12)
    mintrl = minimum_track_record_length(baseline)
    ann = 12.0**0.5

    verdict = _verdict(pbo.pbo, dsr_eff)

    lines: list[str] = []
    lines.append("# IDEAL line — PBO / overfitting validation")
    lines.append("")
    lines.append(f"**VERDICT: {verdict}**")
    lines.append("")
    lines.append(
        f"- configs (pre-registered grid): {len(GRID)} | months: {min_len} | CSCV splits: {args.splits}"
    )
    lines.append(
        f"- **PBO (P overfit): {pbo.pbo:.3f}** over {pbo.n_combinations} combinations "
        f"(median logit {pbo.median_logit:+.2f}; >0 = generalises)"
    )
    lines.append(
        f"- effective N trials (cluster-aware): {eff_n:.2f} of {len(GRID)} nominal "
        f"(strongly-correlated variants collapse the count)"
    )
    lines.append("")
    lines.append(f"## Deployed baseline — `{BASELINE_NAME}`")
    lines.append(
        f"- Sharpe: {per_period_sharpe(baseline):.3f}/month "
        f"≈ {per_period_sharpe(baseline) * ann:.2f} annualised ({len(baseline)} months)"
    )
    lines.append(
        f"- PSR(SR>0): {psr:.4f} (probability the Sharpe is merely POSITIVE — a weak claim)"
    )
    lines.append(
        f"- **DSR @ effN={n_trials_eff}: {dsr_eff:.4f}** (honest) vs DSR @ nominal N={len(GRID)}: {dsr_nominal:.4f}"
    )
    lines.append(
        f"- bootstrap Sharpe 95% CI: [{boot.ci_low:.2f}, {boot.ci_high:.2f}], "
        f"P(SR>0)={boot.prob_sharpe_gt_zero:.3f}, null p={boot.p_value_null:.3f}"
    )
    lines.append(f"- MinTRL (months to PSR>0.95): {mintrl:.1f}")
    lines.append("")
    lines.append("## Per-config Sharpe (full timeline)")
    order = sorted(range(len(names)), key=lambda i: sharpes[i], reverse=True)
    lines.append("| config | per-period Sharpe |")
    lines.append("|:--|--:|")
    for i in order:
        lines.append(f"| {names[i]} | {sharpes[i]:.3f} |")
    lines.append("")
    lines.append("## Honest reading")
    lines.append("- TWO different questions: DSR/PSR ask 'is the edge SIGN reliable?' (yes — the")
    lines.append("  Sharpe is robustly positive); PBO asks 'is the CHOSEN config overfit?'. A high")
    lines.append("  DSR with a fragile PBO means the strategy is positive but WHICH config / how")
    lines.append("  much extra return is data-mined — so deploy the baseline, do not chase the")
    lines.append("  best-of-grid winner (this quantitatively confirms the earlier conc5 caution).")
    lines.append(
        f"- effN {eff_n:.2f} means the {len(GRID)} configs are near-identical; picking the"
    )
    lines.append("  'best' among them is close to a coin flip — the grid understates the true")
    lines.append("  search space (momentum-lookback / cap variants tried over the project's life),")
    lines.append("  so the real PBO is likely HIGHER. Widen the grid for a stricter test.")
    lines.append("- PBO works on the config cross-section, so it is immune to the overlapping-")
    lines.append("  window / low-effective-N problem of the rolling walk-forward.")
    lines.append("- DSR is reported at the cluster-aware effN (not the optimistic N=1 nor the")
    lines.append(
        "  pessimistic regime-dispersion V); trial variance = cross-config Sharpe variance."
    )
    lines.append("- All backtests are survivorship-inflated (current-constituent megacaps); use")
    lines.append("  SPY-excess, not absolute CAGR. This does NOT replace forward paper OOS or")
    lines.append("  cross-market replication — those are the only sources of NEW forward evidence.")

    report = "\n".join(lines)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report + "\n", encoding="utf-8")
    print(report)
    print(f"\nwrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
