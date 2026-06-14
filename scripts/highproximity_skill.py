"""52-week-high proximity (George-Hwang 2004) vs raw momentum — a DIFFERENT alpha source.

Distinct from the momentum/quality/chart/COT/VIX signals already tested: George & Hwang
(2004) show that nearness to the 52-week high predicts cross-sectional returns BETTER
than raw past return — investors anchor on the high and underreact near it. It is
price-only, economically motivated, and not a momentum/quality rehash.

    proximity[i] = P[t] / max(P over trailing 252 trading days)   (in (0,1], 1 = at the high)

Tested apples-to-apples vs raw 126d momentum on the MEGACAP universe (where the
momentum family's edge lives), same picking-skill harness (21d forward, Spearman IC).

MULTIPLE-TESTING HONESTY (declared 2026-06-15): this is roughly the 7th signal probed
in this program. A single nominal p<0.05 is NOT significant after ~7 tries — the
Bonferroni-adjusted bar is ~p<0.007. So the pre-declared ADOPT bar is deliberately
strict:
  ADOPT-CANDIDATE only if rank-IC(proximity) > rank-IC(raw) AND IC t-stat > 2.7
  (≈ Bonferroni p<0.007 for ~7 tests).
Anything less → NO EDGE, and this CLOSES the factor-zoo selection lane (chaining more
signals until one passes is p-hacking). Read-only; nothing wired to capital.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.price_snapshot import read_price_snapshot  # noqa: E402
from scripts.aqr_ideal_grid import DEFAULT_PRICES  # noqa: E402
from scripts.aqr_ideal_walkforward import MEGACAPS  # noqa: E402
from scripts.picking_skill import _fwd_return  # noqa: E402
from scripts.residual_momentum_skill import _bucket  # noqa: E402

OUT_DIR = ROOT / "out"
MOM_LOOKBACK = 126
HIGH_WINDOW = 252  # 52 weeks
FWD_BARS = 21
ADOPT_T = 2.7  # ≈ Bonferroni p<0.007 for ~7 signal tests


def _signals(prices: pd.DataFrame, sym: str, rebal: pd.Timestamp) -> tuple[float, float] | None:
    """(raw_126d_momentum, 52w-high proximity) for one name, or None on short history."""
    px = prices[sym].loc[:rebal].dropna()
    if len(px) <= HIGH_WINDOW:
        return None
    p_now = float(px.iloc[-1])
    p_then = float(px.iloc[-1 - MOM_LOOKBACK])
    high = float(px.iloc[-HIGH_WINDOW:].max())
    if p_then <= 0 or high <= 0:
        return None
    return (p_now / p_then - 1.0, p_now / high)


def main() -> int:
    parser = argparse.ArgumentParser(description="52w-high proximity vs raw momentum.")
    parser.add_argument("--prices", type=Path, default=DEFAULT_PRICES)
    parser.add_argument("--output", type=Path, default=OUT_DIR / "highproximity-skill.md")
    args = parser.parse_args()

    prices = read_price_snapshot(args.prices, verify=True)
    last = pd.Timestamp(prices.index.max())
    rebal_dates = []
    cur = pd.Timestamp("2009-01-01") + pd.offsets.MonthEnd(0)
    while cur <= last:
        valid = prices.index[prices.index <= cur]
        if len(valid):
            rebal_dates.append(valid[-1])
        cur += pd.offsets.MonthEnd(1)

    mom_stats, hi_stats = [], []
    months = 0
    for rebal in rebal_dates:
        future = prices.index[prices.index > rebal][:FWD_BARS]
        if len(future) < FWD_BARS:
            break
        end_ts = future[-1]
        mom_scored, hi_scored = [], []
        for sym in MEGACAPS:
            sig = _signals(prices, sym, rebal)
            fwd = _fwd_return(prices, sym, rebal, end_ts)
            if sig is None or fwd is None:
                continue
            mom_scored.append((sig[0], fwd))
            hi_scored.append((sig[1], fwd))
        if len(mom_scored) < 14:
            continue
        months += 1
        mom_stats.append(_bucket(mom_scored))
        hi_stats.append(_bucket(hi_scored))

    def col(stats: list, j: int) -> float:
        return sum(s[j] for s in stats) / len(stats)

    mom_ic, hi_ic = col(mom_stats, 3), col(hi_stats, 3)
    n = len(hi_stats)
    hi_ic_t = hi_ic * (n**0.5)
    hi_tu = col(hi_stats, 0) - col(hi_stats, 1)
    adopt = hi_ic > mom_ic and hi_ic_t > ADOPT_T

    pct = lambda x: f"{x * 100:+.2f}%"  # noqa: E731
    verdict = (
        f"ADOPT-CANDIDATE — proximity rank-IC {hi_ic:+.3f} (t≈{hi_ic_t:.1f}) beats raw AND clears "
        f"the multiple-testing bar (t>{ADOPT_T}). Next: full walk-forward + PBO + crash gate."
        if adopt
        else f"NO EDGE — proximity IC {hi_ic:+.3f} (t≈{hi_ic_t:.1f}) fails the strict bar "
        f"(needs > raw {mom_ic:+.3f} AND t>{ADOPT_T}). **Factor-zoo selection lane CLOSED** — "
        "7+ signals probed; chaining more until one passes would be p-hacking. The honest "
        "conclusion: available-data signal engineering does not raise this book's selection skill."
    )
    lines = [
        "# 52-week-high proximity (George-Hwang) vs raw momentum",
        "",
        f"Pinned {args.prices.name} | {months} rebalances | megacap universe | "
        f"21-bar forward | strict adopt bar: IC>raw AND t>{ADOPT_T} (Bonferroni ~7 tests)",
        "",
        "| signal | top-7 | universe | bottom-7 | top−uni | rank-IC | IC t |",
        "|---|---:|---:|---:|---:|---:|---:|",
        f"| raw momentum | {pct(col(mom_stats, 0))} | {pct(col(mom_stats, 1))} | "
        f"{pct(col(mom_stats, 2))} | {pct(col(mom_stats, 0) - col(mom_stats, 1))} | {mom_ic:+.3f} | "
        f"{mom_ic * (n**0.5):.1f} |",
        f"| 52w-high proximity | {pct(col(hi_stats, 0))} | {pct(col(hi_stats, 1))} | "
        f"{pct(col(hi_stats, 2))} | {pct(hi_tu)} | {hi_ic:+.3f} | {hi_ic_t:.1f} |",
        "",
        "## Verdict (pre-declared, multiple-testing adjusted)",
        "",
        verdict,
        "",
        "## Honest caveats",
        "- ~7th signal in the program — a nominal p<0.05 is meaningless here; the t>2.7 bar",
        "  is the honest threshold and is deliberately hard to clear.",
        "- Megacap universe, survivorship-inflated; relative IC/spread is the read.",
        "- Single pre-declared definition (126d mom, 252d high); no windows tuned.",
    ]
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
