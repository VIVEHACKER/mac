"""Stress-window evidence for the IDEAL deploy candidate (closes a live-readiness gap).

`trader live-readiness` blocks on "stress windows 0 < 2 / worst stress return missing /
full-sample drawdown missing". This script MEASURES that evidence — it does not invent
a pass. It runs the validated IDEAL engine (scripts.aqr_ideal_walkforward.run_window)
over historical crisis windows on the pinned snapshot and reports, per window, the
strategy's total return, SPY's, and the EXCESS.

Two criteria, both reported, because the gate's default is the wrong shape for this
strategy:
  * GATE (absolute): worst-window total return >= +30%. This was calibrated for
    crash-HEDGED sleeves (QQQ/TLT + inverse) that PROFIT in crashes. A long-only
    megacap-momentum book cannot meet it — it falls WITH the market (less, ideally).
    Reporting it shows exactly how far, and surfaces the calibration mismatch.
  * STRATEGY-APPROPRIATE (relative): does the book lose LESS than SPY in each crisis
    (positive excess)? For a long-only excess strategy with a drawdown de-risk, this is
    the meaningful "did the risk controls help" question.

This script only produces evidence + a report; it does NOT write the research registry
or change any gate threshold (lowering the bar to force a pass would be gaming). The
operator decides whether to add a hedge sleeve or adopt a strategy-appropriate gate.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.price_snapshot import read_price_snapshot  # noqa: E402
from scripts.aqr_ideal_grid import DEFAULT_PRICES, DEFAULT_SNAPSHOT  # noqa: E402
from scripts.aqr_ideal_walkforward import prefetch, run_window  # noqa: E402

OUT_DIR = ROOT / "out"
GATE_MIN_WORST_STRESS = 0.30  # research_registry.LIVE_PROMOTION_GATE.min_worst_stress_return

# (window label, strategy total return, SPY total return, excess, window MDD) — None when untested.
StressRow = tuple[str, float | None, float | None, float | None, float | None]

# Crisis windows with enough pinned history (data starts 2008-01) for the 126-day
# momentum lookback. Peak-to-trough-ish spans; each contains >=2 monthly rebalances.
STRESS_WINDOWS = [
    ("GFC 2008-09→2009-06", "2008-09-01", "2009-06-30"),
    ("2018 Q4 selloff", "2018-09-01", "2018-12-31"),
    ("COVID 2020-02→2020-06", "2020-02-01", "2020-06-30"),
    ("2022 bear", "2022-01-01", "2022-10-31"),
]


def _total_return(monthly: list[float]) -> float:
    eq = 1.0
    for r in monthly:
        eq *= 1.0 + r
    return eq - 1.0


def main() -> int:
    parser = argparse.ArgumentParser(description="IDEAL stress-window evidence (no gaming).")
    parser.add_argument("--prices", type=Path, default=DEFAULT_PRICES)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--fee-bps", type=float, default=0.0)
    parser.add_argument("--output", type=Path, default=OUT_DIR / "aqr-ideal-stress-windows.md")
    args = parser.parse_args()

    prices = read_price_snapshot(args.prices, verify=True)
    fund_cache = prefetch(None, snapshot_path=args.snapshot)
    cfg = {"fee_bps": args.fee_bps}

    rows: list[StressRow] = []
    for name, start, end in STRESS_WINDOWS:
        result = run_window(pd.Timestamp(start), pd.Timestamp(end), prices, fund_cache, cfg=cfg)
        if result is None:
            rows.append((name, None, None, None, None))
            continue
        strat = _total_return(result["monthly_returns"])
        spy = _total_return(result["spy_returns"])
        rows.append((name, strat, spy, strat - spy, float(result["mdd"])))

    strat_returns = [r[1] for r in rows if r[1] is not None]
    excesses = [r[3] for r in rows if r[3] is not None]
    worst_stress = min(strat_returns) if strat_returns else None
    worst_excess = min(excesses) if excesses else None
    full = run_window(
        pd.Timestamp("2009-06-01"), pd.Timestamp(prices.index.max()), prices, fund_cache, cfg=cfg
    )
    full_mdd: float | None = float(full["mdd"]) if full else None

    gate_pass = worst_stress is not None and worst_stress >= GATE_MIN_WORST_STRESS
    relative_pass = worst_excess is not None and worst_excess > 0.0

    pct = lambda x: "n/a" if x is None else f"{x * 100:+.2f}%"  # noqa: E731
    lines = [
        "# IDEAL deploy candidate — stress-window evidence",
        "",
        f"Engine: aqr_ideal_walkforward.run_window (validated) | pinned {args.prices.name} | "
        f"fee {args.fee_bps:.0f}bps | windows tested: {len(strat_returns)}",
        f"Full-sample (2009-2026) max drawdown: **{pct(full_mdd)}**",
        "",
        "## Per-window total return",
        "",
        "| Crisis window | strategy | SPY | excess | window MDD |",
        "|---|---:|---:|---:|---:|",
    ]
    for win_name, s_ret, spy_ret, exc, win_mdd in rows:
        lines.append(
            f"| {win_name} | {pct(s_ret)} | {pct(spy_ret)} | {pct(exc)} | {pct(win_mdd)} |"
        )
    lines += [
        "",
        "## Two readings (both honest)",
        "",
        f"- **GATE (absolute, worst >= +{GATE_MIN_WORST_STRESS:.0%}):** worst stress total "
        f"return {pct(worst_stress)} → **{'PASS' if gate_pass else 'FAIL'}**. This bar was "
        "built for crash-HEDGED sleeves that profit in crashes; a long-only momentum book "
        "structurally cannot meet it (it falls with the market). The FAIL is expected and "
        "does NOT mean the strategy is broken — it means the gate shape is wrong for it.",
        f"- **STRATEGY-APPROPRIATE (relative, excess > 0 every crisis):** worst window excess "
        f"vs SPY {pct(worst_excess)} → **{'PASS' if relative_pass else 'FAIL'}**. For a "
        "long-only excess strategy with a drawdown de-risk, 'lose less than buy-and-hold in "
        "every crisis' is the meaningful risk-control test.",
        "",
        "## What this unblocks / what it doesn't",
        "- CLOSES the 'stress windows missing / worst stress return missing / full-sample "
        "drawdown missing' evidence gap: those are now MEASURED above.",
        "- Does NOT flip the live gate to green: the absolute +30% bar still fails (correctly, "
        "for a long-only book). This script will NOT lower that threshold to force a pass.",
        "- Operator decision required: (a) add a crash-hedge sleeve and FULLY re-validate, or "
        "(b) consciously adopt a strategy-appropriate stress gate (relative-to-SPY) in "
        "research_registry.LIVE_PROMOTION_GATE. Either is a deliberate call, not an "
        "auto-applied change.",
    ]
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
