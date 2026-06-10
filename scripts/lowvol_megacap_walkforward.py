"""Megacap low-volatility line — candidate strategy line #3 in the research ledger.

A THIRD alpha source, distinct from both validated/tested lines: IDEAL is
cross-sectional momentum (relative winners), TSMOM was absolute momentum (rejected —
see docs/STRATEGIES.md research ledger); this is the defensive-equity / low-beta
premium (Frazzini-Pedersen "Betting Against Beta", long-only construction): hold the
N lowest-trailing-vol megacaps, equal weight, always invested, monthly rebalance.

Protocol mirrors scripts/aqr_ideal_walkforward.py exactly (same pinned prices, same
106-name survivorship-tested universe, same 15 x 3y windows, same turnover-based fee
state) so all lines are gate-comparable. Reports correlation vs the IDEAL line and
SPY-down-month behaviour, with the verdict bars declared up front — the honest prior
is that defensive megacaps LAG SPY through this tech-led bull sample, so the realistic
best case is a low-correlation diversifier, and the rules say exactly what that takes.

Claims are earned via the gate: this script only produces evidence; nothing here is
wired into capital allocation.
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
from scripts.aqr_ideal_walkforward import (  # noqa: E402
    BENCHMARK,
    MEGACAPS,
    maxdd_series,
    prefetch,
    run_window,
    vol_estimate,
)

OUT_DIR = ROOT / "out"

DEFAULT_CFG = {
    "vol_window": 63,  # trailing window for the vol rank (same estimator as IDEAL sizing)
    "top_n": 20,  # hold the 20 lowest-vol names, equal weight, always invested
    "min_history": 126,  # require ~6 months of prices before a name is rankable
    "fee_bps": 0.0,
}


def run_lowvol_window(start, end, prices: pd.DataFrame, cfg: dict | None = None) -> dict | None:
    """Run the low-vol line over [start, end]; metrics dict mirrors run_window's schema."""
    cfg = {**DEFAULT_CFG, **(cfg or {})}
    equity = 10_000.0
    spy_eq = 10_000.0
    monthly_rets: list[float] = []
    spy_rets: list[float] = []
    equity_series: list[dict] = []
    prev_weights: dict[str, float] = {}

    rebal_dates = []
    cur = pd.Timestamp(start) + pd.offsets.MonthEnd(0)
    while cur <= pd.Timestamp(end):
        rebal_dates.append(cur)
        cur = cur + pd.offsets.MonthEnd(1)

    for as_of, next_of in zip(rebal_dates[:-1], rebal_dates[1:], strict=False):
        valid_idx = prices.index[prices.index <= as_of]
        end_idx = prices.index[prices.index <= next_of]
        if len(valid_idx) == 0 or len(end_idx) == 0:
            continue
        rebal, end_ts = valid_idx[-1], end_idx[-1]
        if end_ts <= rebal:
            continue

        # Rank tradable names (enough history) by trailing vol, hold the N calmest.
        vols: dict[str, float] = {}
        for sym in MEGACAPS:
            hist = prices[sym].loc[:rebal].dropna()
            if len(hist) < cfg["min_history"]:
                continue
            vols[sym] = vol_estimate(prices, sym, rebal, window=cfg["vol_window"])
        if len(vols) < cfg["top_n"]:
            continue
        calmest = sorted(vols, key=lambda s: vols[s])[: cfg["top_n"]]
        weights = dict.fromkeys(calmest, 1.0 / cfg["top_n"])

        ret = 0.0
        for sym, w in weights.items():
            p0, p1 = prices.at[rebal, sym], prices.at[end_ts, sym]
            if pd.isna(p0) or pd.isna(p1) or float(p0) == 0.0:
                continue  # name went dark intra-month -> conservative 0% on that slice
            ret += w * (float(p1) / float(p0) - 1.0)

        if cfg["fee_bps"] > 0:
            names = set(weights) | set(prev_weights)
            turnover = sum(abs(weights.get(s, 0.0) - prev_weights.get(s, 0.0)) for s in names)
            ret -= turnover * (cfg["fee_bps"] / 1e4)

        try:
            sp_end, sp_reb = prices.at[end_ts, BENCHMARK], prices.at[rebal, BENCHMARK]
        except KeyError:
            continue
        if pd.isna(sp_end) or pd.isna(sp_reb) or float(sp_reb) == 0.0:
            continue
        spy_ret = float(sp_end) / float(sp_reb) - 1.0

        prev_weights = weights
        equity *= 1.0 + ret
        spy_eq *= 1.0 + spy_ret
        monthly_rets.append(ret)
        spy_rets.append(spy_ret)
        peak = max([p["peak"] for p in equity_series] + [equity]) if equity_series else equity
        equity_series.append({"date": rebal.date(), "equity": equity, "peak": max(peak, equity)})

    if not monthly_rets:
        return None
    months = len(monthly_rets)
    years = months / 12.0
    ann = (equity / 10_000.0) ** (1.0 / years) - 1.0
    spy_ann = (spy_eq / 10_000.0) ** (1.0 / years) - 1.0
    mr, sr = pd.Series(monthly_rets), pd.Series(spy_rets)
    return {
        "start": str(start),
        "end": str(end),
        "months": months,
        "ann": ann,
        "spy_ann": spy_ann,
        "excess": ann - spy_ann,
        "sharpe": (mr.mean() / mr.std()) * math.sqrt(12.0) if mr.std() > 0 else 0.0,
        "spy_sharpe": (sr.mean() / sr.std()) * math.sqrt(12.0) if sr.std() > 0 else 0.0,
        "mdd": maxdd_series(pd.DataFrame(equity_series)["equity"]),
        "monthly_returns": list(monthly_rets),
        "spy_returns": list(spy_rets),
        "dates": [str(p["date"]) for p in equity_series],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Megacap low-vol walk-forward (candidate line #3)."
    )
    parser.add_argument("--prices", type=Path, default=DEFAULT_PRICES)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--fee-bps", type=float, default=0.0)
    parser.add_argument("--top-n", type=int, default=DEFAULT_CFG["top_n"])
    parser.add_argument("--output", type=Path, default=OUT_DIR / "lowvol-megacap-walkforward.md")
    args = parser.parse_args()

    # Pinned-only by construction: no live-download fallback exists in this script.
    prices = read_price_snapshot(args.prices, verify=True)
    cfg = {"fee_bps": args.fee_bps, "top_n": args.top_n}

    windows = []
    start_year = 2009
    while start_year + 3 <= 2026:
        windows.append(
            (pd.Timestamp(f"{start_year}-01-01"), pd.Timestamp(f"{start_year + 2}-12-31"))
        )
        start_year += 1

    print(
        f"Low-vol walk-forward: {len(windows)} windows  top{args.top_n}  "
        f"fee {args.fee_bps}bps  [pinned {args.prices.name}]"
    )
    results = []
    for ws, we in windows:
        r = run_lowvol_window(ws, we, prices, cfg)
        if r:
            results.append(r)
            print(
                f"  {r['start'][:10]} → {r['end'][:10]}  ann {r['ann'] * 100:+6.2f}%  "
                f"excess {r['excess'] * 100:+6.2f}%  Sharpe {r['sharpe']:.2f}  MDD {r['mdd'] * 100:.2f}%"
            )

    df = pd.DataFrame(results)
    pos_rate = float((df["excess"] > 0).mean()) * 100
    avg_excess = float(df["excess"].mean()) * 100
    avg_sharpe = float(df["sharpe"].mean())
    worst_mdd = float(df["mdd"].max()) * 100

    full_start, full_end = pd.Timestamp("2009-06-01"), pd.Timestamp(prices.index.max())
    lv_full = run_lowvol_window(full_start, full_end, prices, cfg)
    fund_cache = prefetch(None, snapshot_path=args.snapshot)
    ideal_full = run_window(full_start, full_end, prices, fund_cache, cfg={"fee_bps": args.fee_bps})
    corr = float("nan")
    down_lv = down_spy = float("nan")
    if lv_full and ideal_full:
        n = min(len(lv_full["monthly_returns"]), len(ideal_full["monthly_returns"]))
        a = pd.Series(lv_full["monthly_returns"][-n:])
        b = pd.Series(ideal_full["monthly_returns"][-n:])
        corr = float(a.corr(b))
        spy = pd.Series(lv_full["spy_returns"])
        mask = spy < 0
        if bool(mask.any()):
            down_lv = float(pd.Series(lv_full["monthly_returns"])[mask].mean()) * 100
            down_spy = float(spy[mask].mean()) * 100

    # Same bars as the TSMOM rejection — declared before looking at the numbers.
    standalone = pos_rate >= 60.0 and avg_excess > 0.0
    diversifier = (not math.isnan(corr)) and corr < 0.7 and (down_lv > down_spy)
    if standalone:
        verdict = (
            "STANDALONE CANDIDATE — passes the same walk-forward bar as IDEAL; "
            "proceed to PBO/DSR + fee stress before any allocation."
        )
    elif diversifier:
        verdict = (
            "NOT STANDALONE — DIVERSIFIER CANDIDATE: low correlation to IDEAL and "
            "better-than-SPY down months. Any use must be as a sleeve and must re-pass "
            "the combined-portfolio gate. NOT wired to capital."
        )
    else:
        verdict = (
            "NO EDGE — fails both the standalone bar and the diversifier bar. "
            "Recorded for the research ledger; not pursued."
        )

    lines = [
        f"# Megacap Low-Vol (trailing-63d rank, top{args.top_n} equal-weight) — Walk-Forward",
        "",
        f"Universe: {len(MEGACAPS)} megacaps (same survivorship-tested set as IDEAL); benchmark {BENCHMARK}.",
        f"Pinned prices: {args.prices.name} (verify=True). Fee: {args.fee_bps:.0f} bps one-way on turnover.",
        "",
        "## Aggregate (same protocol as IDEAL)",
        "",
        f"- Positive test rate: **{pos_rate:.1f}%**",
        f"- Average test annualized excess vs SPY: **{avg_excess:+.2f}%**",
        f"- Average test Sharpe: **{avg_sharpe:.2f}**",
        f"- Worst test MDD: **{worst_mdd:.2f}%**",
        "",
        "## Relationship to the validated IDEAL line (full sample)",
        "",
        f"- Monthly-return correlation low-vol vs IDEAL: **{corr:.2f}**",
        f"- SPY-down months: low-vol {down_lv:+.2f}%/mo vs SPY {down_spy:+.2f}%/mo",
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
            f"{r['spy_ann'] * 100:+.2f}% | {r['excess'] * 100:+.2f}% | {r['sharpe']:.2f} | {r['mdd'] * 100:.2f}% |"
        )
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nWrote {args.output}")
    print(f"VERDICT: {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
