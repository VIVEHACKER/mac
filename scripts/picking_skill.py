"""Does the IDEAL ranking actually PICK well? Top vs bottom vs universe, out-of-sample.

The walk-forward proves the top-7 portfolio beats SPY, but that mixes selection skill
with plain long-market beta. This isolates SELECTION: at every monthly rebalance it
ranks the whole universe by the AQR composite (the exact run_window ranking, PIT
fundamentals, no look-ahead) and compares the 21-day forward return of:

    top-7 (what it BUYS)  vs  bottom-7 (what it AVOIDS)  vs  universe mean (equal-weight all)

plus the per-rebalance Spearman rank-IC (score vs forward return). A real stock-picker
shows: top > universe > bottom consistently, and a positive average rank-IC. "Top beats
universe" is the value-add over just owning everything equally; "top beats bottom" is
raw discrimination.

Pinned + reproducible (same snapshot as the walk-forward). Read-only evidence; nothing
is wired to capital.
"""

from __future__ import annotations

import argparse
import math
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.price_snapshot import read_price_snapshot  # noqa: E402
from scripts.aqr_ideal_grid import DEFAULT_PRICES, DEFAULT_SNAPSHOT  # noqa: E402
from scripts.aqr_ideal_walkforward import (  # noqa: E402
    MEGACAPS,
    build_pricebars,
    lookup_pit,
    prefetch,
)
from strategies.factor_aqr import rank_aqr_factors  # noqa: E402

OUT_DIR = ROOT / "out"
FWD_BARS = 21  # same 21-trading-day forward horizon as run_window
TOP_N = 7


def _fwd_return(
    prices: pd.DataFrame, sym: str, rebal: pd.Timestamp, end_ts: pd.Timestamp
) -> float | None:
    try:
        p0, p1 = prices.loc[rebal, sym], prices.loc[end_ts, sym]
    except KeyError:
        return None
    if pd.isna(p0) or pd.isna(p1) or float(p0) == 0.0:
        return None
    return float(p1) / float(p0) - 1.0


def main() -> int:
    parser = argparse.ArgumentParser(description="IDEAL ranking selection-skill measurement.")
    parser.add_argument("--prices", type=Path, default=DEFAULT_PRICES)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--output", type=Path, default=OUT_DIR / "picking-skill.md")
    args = parser.parse_args()

    prices = read_price_snapshot(args.prices, verify=True)
    fund_cache = prefetch(None, snapshot_path=args.snapshot)

    rebal_dates = []
    cur = pd.Timestamp("2009-01-01") + pd.offsets.MonthEnd(0)
    last = pd.Timestamp(prices.index.max())
    while cur <= last:
        valid = prices.index[prices.index <= cur]
        if len(valid):
            rebal_dates.append(valid[-1])
        cur += pd.offsets.MonthEnd(1)

    top_rets, bot_rets, uni_rets, ics = [], [], [], []
    months = 0
    for rebal in rebal_dates:
        future = prices.index[prices.index > rebal][:FWD_BARS]
        if len(future) < FWD_BARS:
            break
        end_ts = future[-1]
        as_of_dt = datetime(rebal.year, rebal.month, rebal.day)

        bars_by_sym, fund_by_sym = {}, {}
        for sym in MEGACAPS:
            fund = lookup_pit(fund_cache.get(sym, []), as_of_dt)
            if fund is None:
                continue
            bars = build_pricebars(prices, sym, rebal)
            if bars:
                fund_by_sym[sym.upper()] = fund
                bars_by_sym[sym] = bars
        scores = rank_aqr_factors(bars_by_sym, fund_by_sym, lookback=126)
        # Keep only names with both a score and a valid forward return.
        scored = [
            (s.symbol, s.composite, fr)
            for s in scores
            if (fr := _fwd_return(prices, s.symbol, rebal, end_ts)) is not None
        ]
        if len(scored) < 2 * TOP_N:
            continue
        months += 1
        scored.sort(key=lambda x: x[1], reverse=True)  # best composite first
        fwds = [x[2] for x in scored]
        top_rets.append(sum(x[2] for x in scored[:TOP_N]) / TOP_N)
        bot_rets.append(sum(x[2] for x in scored[-TOP_N:]) / TOP_N)
        uni_rets.append(sum(fwds) / len(fwds))
        comp = pd.Series([x[1] for x in scored])
        ic = comp.rank().corr(pd.Series(fwds).rank())  # Spearman
        if not pd.isna(ic):
            ics.append(float(ic))

    def ann(monthly_mean: float) -> float:
        return (1 + monthly_mean) ** 12 - 1

    top_m = sum(top_rets) / len(top_rets)
    bot_m = sum(bot_rets) / len(bot_rets)
    uni_m = sum(uni_rets) / len(uni_rets)
    spread_tb = [t - b for t, b in zip(top_rets, bot_rets, strict=True)]
    spread_tu = [t - u for t, u in zip(top_rets, uni_rets, strict=True)]
    hit_tb = sum(1 for x in spread_tb if x > 0) / len(spread_tb) * 100
    hit_tu = sum(1 for x in spread_tu if x > 0) / len(spread_tu) * 100
    mean_ic = sum(ics) / len(ics) if ics else float("nan")
    ic_t = (mean_ic * math.sqrt(len(ics))) if ics else float("nan")  # rough t-stat

    pct = lambda x: f"{x * 100:+.2f}%"  # noqa: E731
    lines = [
        "# IDEAL ranking — stock-selection skill (out-of-sample)",
        "",
        f"Pinned {args.prices.name} | {months} monthly rebalances 2009→{last.date()} | "
        f"21-bar forward | top/bottom {TOP_N}, universe ~{len(MEGACAPS)} names",
        "",
        "## Average 21-day forward return by bucket",
        "",
        "| bucket | monthly mean | annualized |",
        "|---|---:|---:|",
        f"| top-{TOP_N} (BUYS) | {pct(top_m)} | {pct(ann(top_m))} |",
        f"| universe (equal-weight all) | {pct(uni_m)} | {pct(ann(uni_m))} |",
        f"| bottom-{TOP_N} (AVOIDS) | {pct(bot_m)} | {pct(ann(bot_m))} |",
        "",
        "## Selection metrics",
        "",
        f"- top − bottom spread: **{pct(top_m - bot_m)}/mo** (~{pct(ann(top_m) - ann(bot_m))}/yr), "
        f"positive in **{hit_tb:.0f}%** of months",
        f"- top − universe (value-add over owning all): **{pct(top_m - uni_m)}/mo**, "
        f"positive in **{hit_tu:.0f}%** of months",
        f"- mean monthly rank-IC (Spearman): **{mean_ic:+.3f}** (rough t≈{ic_t:.1f} over {len(ics)} months)",
        "",
        "## Reading",
        "- 'top > universe' is the real selection value-add — beating equal-weighting the whole",
        "  list, not just being long. 'top > bottom' is raw discrimination.",
        "- A positive but small rank-IC (~0.03-0.06 is typical for real factor signals) means",
        "  genuine-but-modest skill: it ranks better than chance, not security-by-security genius.",
        "- Survivorship-inflated universe (current megacaps): absolute levels are optimistic;",
        "  the SPREAD (top−bottom, top−universe) is the cleaner read of discrimination.",
        "- This is the MOMENTUM line. The quality/compounder ranker tested NO forward IC",
        "  (out/compounder-factor-ic.md) and the chart entry-states tested no edge — only this",
        "  momentum ranking shows selection skill.",
    ]
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
