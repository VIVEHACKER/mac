"""PBO / CSCV over the FULL breadth-round family — the combined line's last stat gate.

The 80/20 combined line passed its pre-declared walk-forward bars and fee stress, but
it was SELECTED from a family of candidates tested this round. The honest overfitting
question is therefore not "is the combined line good?" but "when you let this whole
family compete in-sample, does the in-sample winner hold up out-of-sample?" (Bailey
et al. CSCV). The family is everything that competed in the selection:

    8  pre-registered IDEAL grid configs (the original search space)
  + 1  megacap TSMOM            (tested, rejected)
  + 1  megacap low-vol          (tested, diversifier-only)
  + 1  combined IDEAL80/lowvol20 (single pre-declared weight — no weight grid)
  = 11 configs

Each breadth candidate was a SINGLE pre-declared config (no internal grid), so 11 is
the true size of THIS round's family; the project-lifetime search space is larger, so
the reported PBO remains a LOWER bound — same caveat as the IDEAL-grid PBO report.

Rows are aligned on the months common to ALL configs (the sleeves skip different
months), because CSCV requires a shared timeline. Claims are earned via the gate;
nothing here wires capital.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.price_snapshot import read_price_snapshot  # noqa: E402
from engine.significance import (  # noqa: E402
    cscv_pbo,
    deflated_sharpe_ratio,
    effective_n_trials,
    per_period_sharpe,
    probabilistic_sharpe_ratio,
)
from scripts.aqr_ideal_grid import DEFAULT_PRICES, DEFAULT_SNAPSHOT, GRID  # noqa: E402
from scripts.aqr_ideal_walkforward import prefetch, run_window  # noqa: E402
from scripts.combined_ideal_lowvol_walkforward import (  # noqa: E402
    SLEEVE_IDEAL,
    SLEEVE_LOWVOL,
)
from scripts.lowvol_megacap_walkforward import run_lowvol_window  # noqa: E402
from scripts.tsmom_megacap_walkforward import run_tsmom_window  # noqa: E402

FULL_START = pd.Timestamp("2009-06-01")
COMBINED_NAME = "combined ideal80/lowvol20"


def _feasible_cfg(cfg: dict, fee_bps: float) -> dict:
    feasible_cap = max(cfg.get("cap", 0.20), 1.0 / cfg["top_n"] + 1e-9)
    return {**cfg, "cap": feasible_cap, "fee_bps": fee_bps}


def _verdict(pbo: float) -> str:
    if pbo < 0.10:
        return "STRONG — the family's in-sample winner generalises"
    if pbo < 0.25:
        return "PLAUSIBLE — modest overfit risk; forward paper OOS remains the decider"
    if pbo < 0.50:
        return "FRAGILE — meaningful overfit risk; do not raise confidence on backtest alone"
    return "OVERFIT — IS-best is no better than chance OOS; treat the selection as unproven"


def main() -> int:
    parser = argparse.ArgumentParser(description="CSCV/PBO over the breadth-round family.")
    parser.add_argument("--prices", type=Path, default=DEFAULT_PRICES)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--fee-bps", type=float, default=0.0)
    parser.add_argument("--splits", type=int, default=14)
    parser.add_argument("--output", type=Path, default=ROOT / "out" / "breadth-family-pbo.md")
    args = parser.parse_args()

    prices = read_price_snapshot(args.prices, verify=True)
    fund_cache = prefetch(None, snapshot_path=args.snapshot)
    full_end = pd.Timestamp(prices.index.max())
    fee = {"fee_bps": args.fee_bps}

    # date -> return maps for every family member.
    series: dict[str, dict[str, float]] = {}
    ideal_baseline: dict[str, float] = {}
    for cfg in GRID:
        result = run_window(
            FULL_START, full_end, prices, fund_cache, cfg=_feasible_cfg(cfg, args.fee_bps)
        )
        if result is None:
            raise SystemExit(f"run_window returned None for {cfg['name']}")
        series[cfg["name"]] = dict(zip(result["dates"], result["monthly_returns"], strict=True))
        if cfg["name"] == "baseline (validated)":
            ideal_baseline = series[cfg["name"]]

    ts = run_tsmom_window(FULL_START, full_end, prices, fee)
    lv = run_lowvol_window(FULL_START, full_end, prices, fee)
    if ts is None or lv is None or not ideal_baseline:
        raise SystemExit("sleeve runs failed — cannot build the family matrix")
    series["tsmom 12-1 (rejected)"] = dict(zip(ts["dates"], ts["monthly_returns"], strict=True))
    lowvol_map = dict(zip(lv["dates"], lv["monthly_returns"], strict=True))
    series["lowvol top20 (sleeve-only)"] = lowvol_map
    series[COMBINED_NAME] = {
        d: SLEEVE_IDEAL * ideal_baseline[d] + SLEEVE_LOWVOL * lowvol_map[d]
        for d in ideal_baseline
        if d in lowvol_map
    }

    # CSCV needs one shared timeline: intersect the dates of every config.
    common = sorted(set.intersection(*(set(s) for s in series.values())))
    if len(common) < args.splits:
        raise SystemExit(f"only {len(common)} common months — fewer than {args.splits} splits")
    names = list(series)
    matrix = [[series[name][d] for d in common] for name in names]

    pbo = cscv_pbo(matrix, n_splits=args.splits)
    eff_n = effective_n_trials(matrix)
    sharpes = {name: per_period_sharpe(row) for name, row in zip(names, matrix, strict=True)}
    trial_variance = statistics.pvariance(list(sharpes.values())) if len(sharpes) > 1 else 0.0

    combined_row = matrix[names.index(COMBINED_NAME)]
    n_trials_eff = max(1, round(eff_n))
    dsr_eff = deflated_sharpe_ratio(
        combined_row, n_trials=n_trials_eff, trial_sr_variance=trial_variance
    )
    dsr_nominal = deflated_sharpe_ratio(
        combined_row, n_trials=len(names), trial_sr_variance=trial_variance
    )
    psr = probabilistic_sharpe_ratio(combined_row)
    rank = sorted(sharpes.values(), reverse=True).index(sharpes[COMBINED_NAME]) + 1

    verdict = _verdict(pbo.pbo)
    ann = 12.0**0.5
    lines = [
        "# Breadth-round family — PBO / CSCV (combined line's selection-bias gate)",
        "",
        f"**VERDICT: {verdict}**",
        "",
        f"- family: {len(names)} configs (8 IDEAL grid + tsmom + lowvol + combined) | "
        f"months (common): {len(common)} | CSCV splits: {args.splits} | fee {args.fee_bps:.0f}bps",
        f"- **PBO: {pbo.pbo:.3f}** over {pbo.n_combinations} combinations "
        f"(median logit {pbo.median_logit:+.2f}; >0 = generalises)",
        f"- effective N trials (cluster-aware): {eff_n:.2f} of {len(names)} nominal",
        "",
        f"## Selected config — `{COMBINED_NAME}`",
        f"- full-sample Sharpe: {sharpes[COMBINED_NAME]:.3f}/month "
        f"≈ {sharpes[COMBINED_NAME] * ann:.2f} annualised — rank {rank}/{len(names)} in family",
        f"- PSR(SR>0): {psr:.4f}",
        f"- **DSR @ effN={n_trials_eff}: {dsr_eff:.4f}** vs DSR @ nominal N={len(names)}: {dsr_nominal:.4f}",
        "",
        "## Per-config Sharpe (common months)",
        "| config | per-period Sharpe |",
        "|:--|--:|",
    ]
    for name in sorted(names, key=lambda n: sharpes[n], reverse=True):
        lines.append(f"| {name} | {sharpes[name]:.3f} |")
    lines += [
        "",
        "## Honest reading",
        "- The family covers THIS breadth round only. Each breadth candidate was a single",
        "  pre-declared config (no internal weight/parameter grid), so 11 is the honest",
        "  family size for this selection — but the project-lifetime search space is",
        "  larger, so this PBO is a LOWER bound (same caveat as the IDEAL-grid report).",
        "- The combined line is ~80% IDEAL by construction, so the family is strongly",
        "  correlated and effN is small: selecting among near-clones is close to a coin",
        "  flip, which CSCV prices in via the OOS rank of the IS-best.",
        "- Survivorship-inflated inputs (current-constituent megacaps): read SPY-excess,",
        "  not absolute CAGR. Forward paper OOS remains the only source of NEW evidence;",
        "  the combined line still must NOT receive capital until that ledger matures.",
    ]
    report = "\n".join(lines)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report + "\n", encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
