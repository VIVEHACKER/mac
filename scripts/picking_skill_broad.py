"""Does a BROAD universe raise selection skill? (lever B test)

The megacap measurement gave rank-IC +0.018 and concluded the 106-name, high-beta-
homogeneous universe was the ceiling. The economic claim: cross-sectional momentum/
factor selection has MORE to exploit across a wide, dispersed universe. This re-runs
the same skill measurement on the broad pinned universe (~1000 names: prices-2026-06-01
∩ fundamentals-2026-06-01-gp2) and compares the rank-IC + decile spread to the megacap
reference.

Same ranking (rank_aqr_factors composite, PIT fundamentals, no look-ahead), same 21-day
forward, deciles instead of fixed top-7 (a 1000-name universe needs proportional
buckets). Pre-declared read (2026-06-15): if broad rank-IC materially exceeds the
megacap +0.018 AND the top-decile beats the universe, the UNIVERSE was the binding
constraint and lever B is real; if not, breadth alone does not help and the gap is
deeper (data quality / PIT — lever A).

CAVEAT up front: sp400-600-current is CURRENT constituents (survivorship-inflated, same
as the megacap set), and prices start 2011 — so this isolates the BREADTH effect on the
same survivorship terms, not a survivorship fix. Read-only; nothing wired to capital.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.fundamentals_snapshot import read_fundamentals_snapshot  # noqa: E402
from data.price_snapshot import read_price_snapshot  # noqa: E402
from scripts.aqr_ideal_walkforward import build_pricebars, lookup_pit  # noqa: E402
from scripts.picking_skill import _fwd_return  # noqa: E402
from strategies.factor_aqr import rank_aqr_factors  # noqa: E402

OUT_DIR = ROOT / "out"
SNAP = ROOT / "data" / "snapshots"
FWD_BARS = 21
MEGACAP_IC = 0.018  # picking-skill.md reference (106-name universe)


def main() -> int:
    parser = argparse.ArgumentParser(description="Broad-universe selection-skill (lever B).")
    parser.add_argument("--prices", type=Path, default=SNAP / "prices-2026-06-01.csv")
    parser.add_argument("--snapshot", type=Path, default=SNAP / "fundamentals-2026-06-01-gp2.csv")
    parser.add_argument("--output", type=Path, default=OUT_DIR / "picking-skill-broad.md")
    args = parser.parse_args()

    prices = read_price_snapshot(args.prices, verify=True)
    records = read_fundamentals_snapshot(args.snapshot, verify=True)
    funds: dict[str, list] = {}
    for r in records:
        funds.setdefault(r.symbol.upper(), []).append(r)
    for v in funds.values():
        v.sort(key=lambda r: r.asof_ts)

    universe = sorted({c.upper() for c in prices.columns} & set(funds))
    first = pd.Timestamp(prices.index.min())
    last = pd.Timestamp(prices.index.max())

    rebal_dates = []
    cur = first + pd.offsets.MonthEnd(13)  # ~1y history before first rebalance
    while cur <= last:
        valid = prices.index[prices.index <= cur]
        if len(valid):
            rebal_dates.append(valid[-1])
        cur += pd.offsets.MonthEnd(1)

    ics: list[float] = []
    top_d, bot_d, uni_m = [], [], []
    months = 0
    for rebal in rebal_dates:
        future = prices.index[prices.index > rebal][:FWD_BARS]
        if len(future) < FWD_BARS:
            break
        end_ts = future[-1]
        as_of_dt = datetime(rebal.year, rebal.month, rebal.day)
        bars_by_sym, fund_by_sym = {}, {}
        for sym in universe:
            fund = lookup_pit(funds.get(sym, []), as_of_dt)
            if fund is None:
                continue
            bars = build_pricebars(prices, sym, rebal)
            if bars:
                fund_by_sym[sym] = fund
                bars_by_sym[sym] = bars
        scores = rank_aqr_factors(bars_by_sym, fund_by_sym, lookback=126)
        scored = [
            (s.composite, fr)
            for s in scores
            if (fr := _fwd_return(prices, s.symbol, rebal, end_ts)) is not None
        ]
        if len(scored) < 30:
            continue
        months += 1
        scored.sort(key=lambda x: x[0], reverse=True)
        fwds = [x[1] for x in scored]
        d = max(1, len(scored) // 10)
        top_d.append(sum(fwds[:d]) / d)
        bot_d.append(sum(fwds[-d:]) / d)
        uni_m.append(sum(fwds) / len(fwds))
        ic = pd.Series([x[0] for x in scored]).rank().corr(pd.Series(fwds).rank())
        if not pd.isna(ic):
            ics.append(float(ic))

    mean_ic = sum(ics) / len(ics) if ics else 0.0
    ic_t = mean_ic * (len(ics) ** 0.5) if ics else 0.0
    td, bd, um = (sum(x) / len(x) for x in (top_d, bot_d, uni_m))
    improved = mean_ic > MEGACAP_IC and td > um

    pct = lambda x: f"{x * 100:+.2f}%"  # noqa: E731
    verdict = (
        f"UNIVERSE WAS THE CEILING — broad rank-IC {mean_ic:+.3f} exceeds the megacap "
        f"{MEGACAP_IC:+.3f}; lever B (breadth) is real. Next: build a broad deploy candidate "
        "(equal-/vol-weighted top decile) and run the FULL walk-forward + PBO + crash gate."
        if improved
        else f"BREADTH DOES NOT HELP — broad rank-IC {mean_ic:+.3f} vs megacap {MEGACAP_IC:+.3f}. "
        "More names did not raise discrimination; the gap is deeper (data quality / "
        "survivorship / PIT = lever A, paid). Not a free win."
    )
    lines = [
        "# Broad-universe selection skill (lever B) vs megacap",
        "",
        f"Universe: {len(universe)} names (prices ∩ fundamentals) | {months} monthly rebalances "
        f"{first.date()}→{last.date()} | deciles | 21-bar forward",
        "",
        "| metric | broad (~1000) | megacap (106, ref) |",
        "|---|---:|---:|",
        f"| mean rank-IC | **{mean_ic:+.3f}** (t≈{ic_t:.1f}) | {MEGACAP_IC:+.3f} (t≈0.3) |",
        f"| top decile fwd | {pct(td)} | — |",
        f"| universe fwd | {pct(um)} | — |",
        f"| bottom decile fwd | {pct(bd)} | — |",
        f"| top decile − universe | {pct(td - um)} | +0.96%/mo (top-7) |",
        "",
        "## Verdict (pre-declared)",
        "",
        verdict,
        "",
        "## Honest caveats",
        "- sp400-600-current = CURRENT constituents (survivorship-inflated, like the megacap",
        "  set); prices from 2011. This isolates BREADTH on the same survivorship terms — it",
        "  does NOT fix survivorship (that is lever A / PIT data).",
        "- Higher broad IC would still need a full walk-forward + PBO + crash re-validation",
        "  before it could become a deploy candidate; this is a skill-ceiling probe, not a gate.",
    ]
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
