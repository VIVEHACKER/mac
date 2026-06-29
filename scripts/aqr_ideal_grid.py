"""Highest-return-with-defense config search for the IDEAL line — OOS, pre-registered, no snooping.

The user wants the highest-return variant of the IDEAL line that still has SOME downside defense.
The trap (this whole project's lesson): sweeping parameters on the full sample and picking the
best is data-snooping. So this evaluates a SMALL PRE-REGISTERED grid of configs (concentration ×
modest leverage × trailing-stop defense) through the SAME walk-forward (OOS rolling 3y test
windows) on PINNED prices, and selects by an explicit objective:

    maximize average test-window CAGR   SUBJECT TO   worst test-window MaxDD ≤ --maxdd-cap

Every config is reported (no cherry-picking). Picking the best-of-N is itself mild selection, so
the winner is flagged as a candidate that still needs (a) an adversarial robustness check and
(b) live paper OOS before any capital — exactly the discipline the funnel work established.

Output: out/aqr-ideal-grid.md
"""

from __future__ import annotations

import argparse
import statistics
import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.price_snapshot import read_price_snapshot  # noqa: E402
from scripts.aqr_ideal_walkforward import (  # noqa: E402
    BENCHMARK,
    MEGACAPS,
    prefetch,
    run_window,
)

DEFAULT_PRICES = ROOT / "data" / "snapshots" / "prices-ideal-2026-06-27.csv"
DEFAULT_SNAPSHOT = ROOT / "data" / "snapshots" / "fundamentals-2026-06-01-gp2.csv"
DEFAULT_OUT = ROOT / "out" / "aqr-ideal-grid.md"

# PRE-REGISTERED grid (locked before the run). Levers for "more return with defense":
#   concentration (top_n ↓ = more return + risk), modest leverage (base>1), tighter trail defense.
# Kept small and principled — NOT an exhaustive sweep.
GRID: list[dict] = [
    {
        "name": "baseline (validated)",
        "top_n": 7,
        "base_leverage": 1.0,
        "trail_dd": -0.10,
        "trail_exposure": 0.5,
    },
    {"name": "conc5", "top_n": 5, "base_leverage": 1.0, "trail_dd": -0.10, "trail_exposure": 0.5},
    {"name": "conc3", "top_n": 3, "base_leverage": 1.0, "trail_dd": -0.10, "trail_exposure": 0.5},
    {"name": "lev1.3", "top_n": 7, "base_leverage": 1.3, "trail_dd": -0.10, "trail_exposure": 0.5},
    {
        "name": "conc5_lev1.3",
        "top_n": 5,
        "base_leverage": 1.3,
        "trail_dd": -0.10,
        "trail_exposure": 0.5,
    },
    {
        "name": "conc5_defended",
        "top_n": 5,
        "base_leverage": 1.0,
        "trail_dd": -0.08,
        "trail_exposure": 0.4,
    },
    {
        "name": "conc5_lev1.3_defended",
        "top_n": 5,
        "base_leverage": 1.3,
        "trail_dd": -0.08,
        "trail_exposure": 0.4,
    },
    {
        "name": "conc3_defended",
        "top_n": 3,
        "base_leverage": 1.0,
        "trail_dd": -0.08,
        "trail_exposure": 0.4,
    },
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--prices", type=Path, default=DEFAULT_PRICES)
    p.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    p.add_argument(
        "--maxdd-cap",
        type=float,
        default=0.30,
        help="defense constraint: reject configs whose WORST test-window MaxDD exceeds this",
    )
    p.add_argument(
        "--fee-bps",
        type=float,
        default=0.0,
        help="one-way trading cost (bps) on rebalance turnover for ALL configs (cost-stress: "
        "higher-turnover concentrated/levered configs lose more, testing if the edge survives)",
    )
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return p.parse_args()


def _windows() -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    out = []
    y = 2009
    while y + 3 <= 2026:
        out.append(
            (pd.Timestamp(f"{y}-01-01"), pd.Timestamp(f"{y + 3}-01-01") - pd.Timedelta(days=1))
        )
        y += 1
    return out


def main() -> None:
    args = parse_args()
    for pth, what in ((args.prices, "pinned prices"), (args.snapshot, "fundamentals snapshot")):
        if not pth.exists():
            raise SystemExit(f"{what} not found: {pth}")
    prices = read_price_snapshot(args.prices, verify=True)
    missing = [s for s in [*MEGACAPS, BENCHMARK] if s not in prices.columns]
    if missing:
        raise SystemExit(
            f"pinned price snapshot missing {len(missing)} required symbols (e.g. {missing[:5]}); "
            "the grid would silently rank configs on a smaller universe. Regenerate over "
            f"MEGACAPS + {BENCHMARK}."
        )
    bench = prices[BENCHMARK].dropna()
    if bench.index.min().date() > date(2008, 1, 15) or bench.index.max().date() < date(2026, 1, 31):
        raise SystemExit(
            "pinned prices do not cover 2008-01..2026-01 for the benchmark; regenerate."
        )
    print("Prefetching fundamentals...")
    fund_cache = prefetch(None, snapshot_path=args.snapshot)
    windows = _windows()

    results: list[dict] = []
    for cfg in GRID:
        # A position cap below 1/top_n cannot be satisfied by a fully-invested portfolio
        # (weights_from_picks would normalize past the declared cap). Bump to the feasible floor.
        feasible_cap = max(cfg.get("cap", 0.20), 1.0 / cfg["top_n"] + 1e-9)
        if feasible_cap > cfg.get("cap", 0.20) + 1e-9:
            print(
                f"  [{cfg['name']}] cap bumped {cfg.get('cap', 0.20):.2f}->{feasible_cap:.2f} "
                f"(feasible floor 1/top_n for top_n={cfg['top_n']})"
            )
        cfg = {**cfg, "cap": feasible_cap, "fee_bps": args.fee_bps}
        rows = [run_window(ws, we, prices, fund_cache, cfg=cfg) for ws, we in windows]
        rows = [r for r in rows if r]
        if not rows:
            continue
        anns = [r["ann"] for r in rows]
        excesses = [r["excess"] for r in rows]
        mdds = [r["mdd"] for r in rows]
        sharpes = [r["sharpe"] for r in rows]
        results.append(
            {
                "name": cfg["name"],
                "cfg": cfg,
                "n": len(rows),
                "avg_ann": statistics.mean(anns),
                "avg_excess": statistics.mean(excesses),
                "worst_mdd": max(mdds),
                "avg_sharpe": statistics.mean(sharpes),
                "pos_rate": sum(1 for e in excesses if e > 0) / len(excesses),
            }
        )
        r = results[-1]
        print(
            f"  {r['name']:24} ann {r['avg_ann'] * 100:+5.1f}%  excess {r['avg_excess'] * 100:+5.1f}%  "
            f"Sharpe {r['avg_sharpe']:.2f}  worstMDD {r['worst_mdd'] * 100:.1f}%"
        )

    # selection: max avg CAGR subject to worst-MDD <= cap (the user's "highest return + defense")
    eligible = [r for r in results if r["worst_mdd"] <= args.maxdd_cap]
    winner = max(eligible, key=lambda r: r["avg_ann"]) if eligible else None
    baseline = next((r for r in results if r["name"].startswith("baseline")), None)

    md = [
        "# IDEAL line — highest-return-with-defense config search (pre-registered, OOS)",
        "",
        "Research-only. PRE-REGISTERED grid (concentration × modest leverage × trailing-stop "
        "defense) through walk-forward (rolling 3y OOS test windows, 2009-2025) on PINNED prices "
        "+ PINNED fundamentals. Objective: **max average test CAGR s.t. worst test MaxDD ≤ "
        f"{args.maxdd_cap * 100:.0f}%**. "
        + (
            f"**Trading cost: {args.fee_bps:g} bps one-way on turnover (all metrics are NET).**"
            if args.fee_bps > 0
            else "**No trading cost (gross; pass --fee-bps to cost-stress).**"
        )
        + " All configs shown; the winner is a CANDIDATE, not a validated edge (best-of-N has "
        "mild selection bias — needs the adversarial check + paper OOS below).",
        "",
        "| Config | top_n | pos-cap | lev | trail | avg CAGR | avg excess | Sharpe | worst MDD | win-rate | ≤cap? |",
        "|---|--:|--:|--:|--|--:|--:|--:|--:|--:|:--:|",
    ]
    for r in sorted(results, key=lambda x: x["avg_ann"], reverse=True):
        c = r["cfg"]
        ok = "✅" if r["worst_mdd"] <= args.maxdd_cap else "—"
        md.append(
            f"| {r['name']} | {c['top_n']} | {c['cap'] * 100:.0f}% | {c['base_leverage']:.1f} | "
            f"{c['trail_dd'] * 100:.0f}%/{c['trail_exposure']:.1f} | {r['avg_ann'] * 100:+.1f}% | "
            f"{r['avg_excess'] * 100:+.1f}% | {r['avg_sharpe']:.2f} | {r['worst_mdd'] * 100:.1f}% | "
            f"{r['pos_rate'] * 100:.0f}% | {ok} |"
        )

    md += ["", "## Recommendation", ""]
    if winner and baseline:
        lift = (winner["avg_ann"] - baseline["avg_ann"]) * 100
        if winner["name"] == baseline["name"] or abs(lift) < 1e-9:
            note = (
                "The defense cap selects the validated BASELINE itself — no eligible config beats "
                "it on CAGR within the MaxDD constraint. Keep baseline; no change warranted."
            )
        elif lift <= 0:
            note = (
                f"NOTE: within the defense cap the highest-CAGR eligible config is LOWER-return "
                f"than baseline ({lift:+.1f}pp) — the cap excluded baseline. Tighten/loosen "
                "--maxdd-cap deliberately; this is a defense-vs-return tradeoff, not an edge."
            )
        elif winner["avg_sharpe"] >= baseline["avg_sharpe"] - 0.05:
            note = (
                "Higher CAGR than baseline AND a better/comparable Sharpe → a genuine "
                "return-with-defense improvement candidate."
            )
        else:
            note = (
                "NOTE: the extra CAGR comes with a LOWER Sharpe than baseline — it is return "
                "bought with risk, not free alpha. Size accordingly."
            )
        md += [
            f"**Highest-return config within the {args.maxdd_cap * 100:.0f}% MaxDD defense cap: "
            f"`{winner['name']}`** — avg CAGR {winner['avg_ann'] * 100:+.1f}% "
            f"(vs baseline {baseline['avg_ann'] * 100:+.1f}%, {lift:+.1f}pp), Sharpe "
            f"{winner['avg_sharpe']:.2f}, worst MDD {winner['worst_mdd'] * 100:.1f}%, "
            f"win-rate {winner['pos_rate'] * 100:.0f}%.",
            "",
            note,
            "",
            f"Config: top_n={winner['cfg']['top_n']}, base_leverage={winner['cfg']['base_leverage']}, "
            f"trail {winner['cfg']['trail_dd'] * 100:.0f}%→{winner['cfg']['trail_exposure']:.1f}x.",
        ]
    else:
        md.append(
            "No config satisfied the MaxDD defense cap — loosen --maxdd-cap or keep baseline."
        )
    md += [
        "",
        "Caveats: walk-forward windows overlap (low effective N); current-constituent megacap "
        "universe (survivorship — absolute CAGR inflated, excess fairer); leverage modeled as a "
        "flat exposure multiplier (no borrow cost / margin calls / path-dependent liquidation); "
        "--fee-bps charges target-weight turnover only (a conservative LOWER bound — omits "
        "intra-period drift-rebalancing, so true costs are modestly higher); best-of-N selection "
        "is mild snooping — the winner must pass an adversarial robustness check (window-split / "
        "leverage / survivorship) and live paper OOS before capital.",
    ]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n" + "\n".join(md[md.index("## Recommendation") :]))
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
