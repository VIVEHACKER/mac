"""Combined-portfolio gate: IDEAL (XSMOM) + low-vol sleeve at a PRE-DECLARED 80/20.

The low-vol line earned "diversifier candidate" (corr 0.53 to IDEAL, down-months
−1.75 vs SPY −3.60 %/mo) but NOT standalone status. Its verdict requires the
COMBINED portfolio to re-pass the gate before any allocation. This script runs that
gate with the honesty constraints declared up front:

  * ONE sleeve weight, fixed before any result was seen: 80% IDEAL / 20% low-vol,
    monthly rebalanced between sleeves. NO weight grid — a searched weight would
    inflate the search space and invalidate the PBO story.
  * PASS requires ALL of (declared 2026-06-11, before running):
      (1) standalone bar:    positive rate >= 60%  AND  avg excess > 0
      (2) no Sharpe give-up: avg test Sharpe >= IDEAL-alone avg (1.41 at fee 0)
      (3) no tail give-up:   worst test MDD <= IDEAL-alone worst (19.19% at fee 0)
    i.e. the sleeve must BUY something (risk) without selling the edge.
  * Anything less -> the sleeve is not adopted; ledger records the failure.

Protocol identical to both sleeves' own validations (same pinned prices, same
15 x 3y windows). Claims are earned via the gate; nothing here wires capital.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.price_snapshot import read_price_snapshot  # noqa: E402
from scripts.aqr_ideal_grid import DEFAULT_PRICES, DEFAULT_SNAPSHOT  # noqa: E402
from scripts.aqr_ideal_walkforward import prefetch, run_window  # noqa: E402
from scripts.lowvol_megacap_walkforward import run_lowvol_window  # noqa: E402

OUT_DIR = ROOT / "out"

SLEEVE_IDEAL = 0.80  # pre-declared; do not search
SLEEVE_LOWVOL = 0.20

# IDEAL-alone aggregates from the canonical pinned run (out/aqr-ideal-walkforward.md).
IDEAL_ALONE_SHARPE = 1.41
IDEAL_ALONE_WORST_MDD = 0.1919


def blend_window(ideal: dict | None, lowvol: dict | None) -> dict | None:
    """Blend two sleeve results into one combined-window metric set.

    Sleeves can skip different months (each checks its own benchmark coverage), so the
    blend aligns on the intersection of rebalance dates and recomputes everything from
    the blended monthly series — no metric is averaged from sleeve-level summaries.
    """
    if not ideal or not lowvol:
        return None
    i_by_date = dict(zip(ideal["dates"], ideal["monthly_returns"], strict=True))
    l_by_date = dict(zip(lowvol["dates"], lowvol["monthly_returns"], strict=True))
    spy_by_date = dict(zip(ideal["dates"], ideal["spy_returns"], strict=True))
    common = sorted(set(i_by_date) & set(l_by_date))
    if len(common) < 12:
        return None

    monthly = [SLEEVE_IDEAL * i_by_date[d] + SLEEVE_LOWVOL * l_by_date[d] for d in common]
    spy = [spy_by_date[d] for d in common]

    equity = 10_000.0
    spy_eq = 10_000.0
    curve = []
    for r, s in zip(monthly, spy, strict=True):
        equity *= 1.0 + r
        spy_eq *= 1.0 + s
        curve.append(equity)
    years = len(monthly) / 12.0
    ann = (equity / 10_000.0) ** (1.0 / years) - 1.0
    spy_ann = (spy_eq / 10_000.0) ** (1.0 / years) - 1.0
    mr = pd.Series(monthly)
    peak = pd.Series(curve).cummax()
    mdd = float(((peak - pd.Series(curve)) / peak).max())
    return {
        "start": ideal["start"],
        "end": ideal["end"],
        "months": len(monthly),
        "ann": ann,
        "spy_ann": spy_ann,
        "excess": ann - spy_ann,
        "sharpe": (mr.mean() / mr.std()) * math.sqrt(12.0) if mr.std() > 0 else 0.0,
        "mdd": mdd,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Combined IDEAL+low-vol 80/20 gate.")
    parser.add_argument("--prices", type=Path, default=DEFAULT_PRICES)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--fee-bps", type=float, default=0.0)
    parser.add_argument(
        "--output", type=Path, default=OUT_DIR / "combined-ideal-lowvol-walkforward.md"
    )
    args = parser.parse_args()

    prices = read_price_snapshot(args.prices, verify=True)
    fund_cache = prefetch(None, snapshot_path=args.snapshot)
    cfg_fee = {"fee_bps": args.fee_bps}

    windows = []
    start_year = 2009
    while start_year + 3 <= 2026:
        windows.append(
            (pd.Timestamp(f"{start_year}-01-01"), pd.Timestamp(f"{start_year + 2}-12-31"))
        )
        start_year += 1

    print(
        f"Combined {SLEEVE_IDEAL:.0%}/{SLEEVE_LOWVOL:.0%} walk-forward: {len(windows)} windows  "
        f"fee {args.fee_bps}bps  [pinned {args.prices.name}]"
    )
    results = []
    for ws, we in windows:
        combined = blend_window(
            run_window(ws, we, prices, fund_cache, cfg=cfg_fee),
            run_lowvol_window(ws, we, prices, cfg_fee),
        )
        if combined:
            results.append(combined)
            print(
                f"  {combined['start'][:10]} → {combined['end'][:10]}  "
                f"ann {combined['ann'] * 100:+6.2f}%  excess {combined['excess'] * 100:+6.2f}%  "
                f"Sharpe {combined['sharpe']:.2f}  MDD {combined['mdd'] * 100:.2f}%"
            )

    df = pd.DataFrame(results)
    pos_rate = float((df["excess"] > 0).mean()) * 100
    avg_excess = float(df["excess"].mean()) * 100
    avg_sharpe = float(df["sharpe"].mean())
    worst_mdd = float(df["mdd"].max()) * 100

    bar1 = pos_rate >= 60.0 and avg_excess > 0.0
    bar2 = avg_sharpe >= IDEAL_ALONE_SHARPE
    bar3 = worst_mdd <= IDEAL_ALONE_WORST_MDD * 100
    if bar1 and bar2 and bar3:
        verdict = (
            "PASS — the 20% low-vol sleeve keeps the edge (bar 1), does not give up Sharpe "
            "(bar 2) and tightens the worst drawdown (bar 3). Eligible for PBO/fee-stress "
            "next; still NOT wired to capital."
        )
    else:
        failed = [
            name
            for name, ok in [
                ("standalone(positive>=60% & excess>0)", bar1),
                (f"sharpe>={IDEAL_ALONE_SHARPE}", bar2),
                (f"worstMDD<={IDEAL_ALONE_WORST_MDD:.2%}", bar3),
            ]
            if not ok
        ]
        verdict = (
            f"FAIL — bars not met: {', '.join(failed)}. The sleeve is NOT adopted; the "
            "IDEAL line stays standalone. Recorded in the research ledger."
        )

    lines = [
        f"# Combined IDEAL {SLEEVE_IDEAL:.0%} + Low-Vol {SLEEVE_LOWVOL:.0%} — Walk-Forward Gate",
        "",
        f"Pinned prices: {args.prices.name} (verify=True). Fee: {args.fee_bps:.0f} bps one-way.",
        "Sleeve weight pre-declared (no grid). Pass bars declared before the run:",
        f"(1) positive>=60% & avg excess>0  (2) avg Sharpe >= {IDEAL_ALONE_SHARPE} "
        f"(3) worst MDD <= {IDEAL_ALONE_WORST_MDD:.2%} — all vs IDEAL-alone at the same fee.",
        "",
        "## Aggregate",
        "",
        f"- Positive test rate: **{pos_rate:.1f}%**",
        f"- Average test annualized excess vs SPY: **{avg_excess:+.2f}%**",
        f"- Average test Sharpe: **{avg_sharpe:.2f}**  (IDEAL alone: {IDEAL_ALONE_SHARPE})",
        f"- Worst test MDD: **{worst_mdd:.2f}%**  (IDEAL alone: {IDEAL_ALONE_WORST_MDD:.2%})",
        "",
        "## Verdict",
        "",
        verdict,
        "",
        "## Windows",
        "",
        "| Test Start | Test End | Ann | SPY Ann | Excess | Sharpe | MDD |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        lines.append(
            f"| {r['start'][:10]} | {r['end'][:10]} | {r['ann'] * 100:+.2f}% | "
            f"{r['spy_ann'] * 100:+.2f}% | {r['excess'] * 100:+.2f}% | {r['sharpe']:.2f} | "
            f"{r['mdd'] * 100:.2f}% |"
        )
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nWrote {args.output}")
    print(f"VERDICT: {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
