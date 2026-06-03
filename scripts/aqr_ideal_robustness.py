"""P0 robustness gate for the conc5 candidate — is its edge real, or best-of-8 + survivorship luck?

The 4-lens adversarial review flagged conc5 (top5) as the highest-return-with-defense CANDIDATE
but disputed whether its lift over baseline (top7) is genuine or (a) best-of-8 selection on
overlapping walk-forward windows and (b) survivorship over-weighting of winners. This resolves it
with three tests on PINNED prices + PINNED fundamentals:

  1. DEFLATED SHARPE (DSR): conc5's continuous-backtest Sharpe deflated for the 8-config grid
     selection (trial_sr_variance from all 8 configs' Sharpes). DSR > 0.95 ⇒ survives selection.
  2. PAIRED EXCESS BOOTSTRAP: circular-block bootstrap of the (conc5 − baseline) monthly excess
     series. If prob(excess Sharpe > 0) is high / null p-value low, conc5 genuinely beats baseline
     beyond noise (the paired test cancels the shared market beta — the right comparison).
  3. NON-OVERLAPPING WINDOWS: conc5 vs baseline on DISJOINT 3y windows (no overlap inflation) —
     the honest small-N count of how often conc5 actually wins.

Verdict gate: conc5's edge is "real enough to register + paper-test" only if DSR clears AND the
paired excess is bootstrap-significant AND conc5 wins a majority of disjoint windows. Otherwise
the dissent is right and baseline top7 stands.

Output: out/aqr-ideal-robustness.md
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
    block_bootstrap_sharpe,
    deflated_sharpe_ratio,
    per_period_sharpe,
    probabilistic_sharpe_ratio,
)
from scripts.aqr_ideal_grid import GRID  # noqa: E402
from scripts.aqr_ideal_walkforward import BENCHMARK, MEGACAPS, prefetch, run_window  # noqa: E402

DEFAULT_PRICES = ROOT / "data" / "snapshots" / "prices-ideal-2026-06-01.csv"
DEFAULT_SNAPSHOT = ROOT / "data" / "snapshots" / "fundamentals-2026-06-01-gp2.csv"
DEFAULT_OUT = ROOT / "out" / "aqr-ideal-robustness.md"
FULL_START = pd.Timestamp("2009-01-01")
FULL_END = pd.Timestamp("2026-05-27")
# disjoint (non-overlapping) 3y windows
DISJOINT = [
    (pd.Timestamp("2009-01-01"), pd.Timestamp("2011-12-31")),
    (pd.Timestamp("2012-01-01"), pd.Timestamp("2014-12-31")),
    (pd.Timestamp("2015-01-01"), pd.Timestamp("2017-12-31")),
    (pd.Timestamp("2018-01-01"), pd.Timestamp("2020-12-31")),
    (pd.Timestamp("2021-01-01"), pd.Timestamp("2023-12-31")),
    (pd.Timestamp("2024-01-01"), pd.Timestamp("2026-05-27")),
]
MONTHS_PER_YEAR = 12
BLOCK = 6  # ~6-month blocks preserve momentum autocorrelation in monthly returns


def feasible(cfg: dict) -> dict:
    return {**cfg, "cap": max(cfg.get("cap", 0.20), 1.0 / cfg["top_n"] + 1e-9)}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--prices", type=Path, default=DEFAULT_PRICES)
    p.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    p.add_argument("--candidate", default="conc5")
    p.add_argument("--baseline", default="baseline (validated)")
    p.add_argument("--n-boot", type=int, default=10_000)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return p.parse_args()


def _fmt(x: float | None) -> str:
    return "n/a" if x is None else f"{x:+.3f}"


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:+.1f}%"


def main() -> None:
    args = parse_args()
    for pth, what in ((args.prices, "pinned prices"), (args.snapshot, "fundamentals snapshot")):
        if not pth.exists():
            raise SystemExit(f"{what} not found: {pth}")
    prices = read_price_snapshot(args.prices, verify=True)
    missing = [s for s in [*MEGACAPS, BENCHMARK] if s not in prices.columns]
    if missing:
        raise SystemExit(
            f"pinned prices missing required symbols (e.g. {missing[:5]}); regenerate."
        )
    print("Prefetching fundamentals...")
    fund_cache = prefetch(None, snapshot_path=args.snapshot)

    by_name = {c["name"]: feasible(c) for c in GRID}
    if args.candidate not in by_name or args.baseline not in by_name:
        raise SystemExit(f"candidate/baseline not in GRID names: {list(by_name)}")
    cand_cfg, base_cfg = by_name[args.candidate], by_name[args.baseline]

    # 1) continuous full-span monthly return series for ALL configs (for DSR trial variance) +
    #    candidate/baseline aligned series for the paired test.
    print("Running full-span continuous backtests for all configs...")
    full = {
        name: run_window(FULL_START, FULL_END, prices, fund_cache, cfg=cfg)
        for name, cfg in by_name.items()
    }
    full = {k: v for k, v in full.items() if v}
    trial_pp_sharpes = [
        per_period_sharpe(r["monthly_returns"])
        for r in full.values()
        if len(r["monthly_returns"]) > 2
    ]
    trial_sr_var = statistics.pvariance(trial_pp_sharpes) if len(trial_pp_sharpes) > 1 else 0.0
    n_trials = len(trial_pp_sharpes)

    cand, base = full[args.candidate], full[args.baseline]
    # align candidate & baseline monthly returns by rebalance date for the paired excess
    cmap = dict(zip(cand["dates"], cand["monthly_returns"], strict=True))
    bmap = dict(zip(base["dates"], base["monthly_returns"], strict=True))
    common = sorted(set(cmap) & set(bmap))
    excess = [cmap[d] - bmap[d] for d in common]

    cand_ret = cand["monthly_returns"]
    cand_dsr = deflated_sharpe_ratio(cand_ret, n_trials=n_trials, trial_sr_variance=trial_sr_var)
    cand_psr = probabilistic_sharpe_ratio(cand_ret)
    cand_boot = block_bootstrap_sharpe(
        cand_ret, n_boot=args.n_boot, block_size=BLOCK, periods_per_year=MONTHS_PER_YEAR
    )
    excess_boot = block_bootstrap_sharpe(
        excess, n_boot=args.n_boot, block_size=BLOCK, periods_per_year=MONTHS_PER_YEAR
    )

    # 3) non-overlapping windows
    disjoint_rows = []
    for ws, we in DISJOINT:
        rc = run_window(ws, we, prices, fund_cache, cfg=cand_cfg)
        rb = run_window(ws, we, prices, fund_cache, cfg=base_cfg)
        if rc and rb:
            disjoint_rows.append(
                {
                    "start": ws.date(),
                    "end": we.date(),
                    "cand_ann": rc["ann"],
                    "base_ann": rb["ann"],
                    "cand_excess": rc["ann"] - rb["ann"],
                    "cand_sharpe": rc["sharpe"],
                }
            )
    cand_wins = sum(1 for r in disjoint_rows if r["cand_excess"] > 0)

    # verdict gates
    dsr_ok = cand_dsr > 0.95
    # paired excess is significant only if BOTH the observed-resample confidence is high AND the
    # recentered-null p-value is low (the report treats both as part of significance).
    excess_ok = excess_boot.prob_sharpe_gt_zero > 0.95 and excess_boot.p_value_null < 0.05
    disjoint_ok = cand_wins > len(disjoint_rows) / 2
    passed = dsr_ok and excess_ok and disjoint_ok

    md = [
        f"# IDEAL `{args.candidate}` robustness gate — real edge or selection/survivorship luck?",
        "",
        "Research-only. PINNED prices + fundamentals. Resolves the 4-lens dispute on whether "
        f"`{args.candidate}` genuinely beats `{args.baseline}` or is best-of-{n_trials} selection "
        "on overlapping windows + survivorship. Continuous full-span backtest, paired excess "
        "bootstrap, and disjoint windows.",
        "",
        "## 1. Deflated Sharpe (selection-bias corrected)",
        "",
        f"- `{args.candidate}` full-span monthly Sharpe (annualized): "
        f"{per_period_sharpe(cand_ret) * (MONTHS_PER_YEAR**0.5):.2f} over {len(cand_ret)} months",
        f"- trial Sharpe variance across {n_trials} grid configs: {trial_sr_var:.5f} (per-period)",
        f"- **PSR (vs 0): {cand_psr:.3f}**  •  **DSR (deflated for {n_trials} trials): "
        f"{cand_dsr:.3f}**  → {'CLEARS 0.95 ✅' if dsr_ok else 'below 0.95 ❌'}",
        f"- bootstrap Sharpe 95% CI: [{cand_boot.ci_low:.2f}, {cand_boot.ci_high:.2f}], "
        f"P(Sharpe>0)={cand_boot.prob_sharpe_gt_zero:.3f}",
        "",
        "## 2. Paired excess vs baseline (does it beat baseline beyond noise?)",
        "",
        f"- mean monthly excess (`{args.candidate}` − `{args.baseline}`): "
        f"{statistics.mean(excess) * 100:+.3f}%/mo over {len(common)} aligned months",
        f"- excess-series annualized Sharpe: {excess_boot.point_sharpe:.2f}, 95% CI "
        f"[{excess_boot.ci_low:.2f}, {excess_boot.ci_high:.2f}]",
        f"- **P(excess Sharpe > 0) = {excess_boot.prob_sharpe_gt_zero:.3f}**, null p-value "
        f"{excess_boot.p_value_null:.3f} → {'significant ✅' if excess_ok else 'NOT significant ❌'}",
        "",
        "## 3. Non-overlapping 3y windows (no overlap inflation)",
        "",
        "| Window | candidate | baseline | excess |",
        "|---|--:|--:|--:|",
    ]
    for r in disjoint_rows:
        md.append(
            f"| {r['start']} → {r['end']} | {_pct(r['cand_ann'])} | {_pct(r['base_ann'])} | "
            f"{_pct(r['cand_excess'])} |"
        )
    md += [
        "",
        f"**candidate beats baseline in {cand_wins}/{len(disjoint_rows)} disjoint windows** → "
        f"{'majority ✅' if disjoint_ok else 'NOT a majority ❌'}",
        "",
        "## Verdict",
        "",
        (
            f"**`{args.candidate}`'s edge SURVIVES the robustness gate** (DSR {cand_dsr:.2f}>0.95, "
            f"paired-excess P {excess_boot.prob_sharpe_gt_zero:.2f}>0.95, "
            f"{cand_wins}/{len(disjoint_rows)} disjoint windows). It is a defensible candidate to "
            "REGISTER (own strategy-id) and PAPER-test — still gated on live paper OOS before "
            "capital; the backtest CAGR is survivorship-inflated (use SPY-excess for expectation)."
            if passed
            else f"**`{args.candidate}`'s edge does NOT clear the robustness gate** — "
            f"DSR {cand_dsr:.2f} ({'ok' if dsr_ok else 'FAIL'}), paired-excess P "
            f"{excess_boot.prob_sharpe_gt_zero:.2f} ({'ok' if excess_ok else 'FAIL'}), disjoint "
            f"{cand_wins}/{len(disjoint_rows)} ({'ok' if disjoint_ok else 'FAIL'}). The 4-lens "
            "dissent holds: the lift is consistent with best-of-N selection + survivorship, NOT a "
            "validated edge. KEEP the deployed baseline; do not register the candidate."
        ),
        "",
        "Caveats: monthly-return Sharpe at ~17y is still low-N; DSR trial variance uses the 8 grid "
        "configs (the actual search breadth); the universe is current-constituent megacaps "
        "(survivorship inflates ABSOLUTE returns for both legs — the paired excess is the fair "
        "read but concentration interacts with survivorship); a real edge still needs forward "
        "paper OOS, not just a deeper backtest.",
    ]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n" + "\n".join(md[md.index("## Verdict") :]))
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
