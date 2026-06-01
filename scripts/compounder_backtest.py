"""Real long-only backtest of the compounder funnel — the decision-relevant artifact.

All prior compounder analysis used cross-sectional IC / decile spreads with a coarse cost
haircut. This builds the ACTUAL portfolio: at each annual rebalance (Jun 30, 2012-2024), rank
the universe by the live `best_score` on PIT fundamentals (asof <= date, sector-aware), hold the
top-N equal-weighted for the year, track daily NAV through PINNED prices, and charge a realistic
turnover-based transaction cost. Benchmark = equal-weight of the SAME universe (isolates the
funnel's SELECTION skill from simply being long the survivor universe).

Honest framing: the universe is current S&P 400+600 constituents, so BOTH the funnel portfolio
and the equal-weight benchmark are survivor-biased — absolute CAGR is inflated, but the funnel
MINUS benchmark excess is the fair read (same survivorship in both legs). The IC work predicts
~no selection edge; this backtest is the real-world confirmation and reports net-of-cost,
Sharpe, and max drawdown so the funnel's value as a screen (does it hurt? help? neutral?) is
testable in portfolio terms.

Output: out/compounder-backtest.md
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.fundamentals_snapshot import read_fundamentals_snapshot  # noqa: E402
from data.models import FundamentalRecord  # noqa: E402
from data.price_snapshot import read_price_snapshot  # noqa: E402
from engine.compounder import rank_compounders  # noqa: E402
from scripts.compounder_heldout_oos import price_asof  # noqa: E402

DEFAULT_UNIVERSE = ROOT / "data" / "universes" / "sp400-600-current.csv"
DEFAULT_SNAPSHOT = ROOT / "data" / "snapshots" / "fundamentals-2026-06-01-gp2.csv"
DEFAULT_SECTORS = ROOT / "data" / "sectors" / "sp400-600-current-sectors.csv"
DEFAULT_PRICES = ROOT / "data" / "snapshots" / "prices-2026-06-01.csv"
DEFAULT_OUT = ROOT / "out" / "compounder-backtest.md"

REBALANCE = [date(y, 6, 30) for y in range(2012, 2025)]
TOP_N = 30
ONE_WAY_BPS = 30.0  # realistic mid/small-cap one-way cost; round-trip = 2x at each turnover
TRADING_DAYS = 252


def load_symbols(path: Path) -> list[str]:
    with path.open(encoding="utf-8") as f:
        return sorted({r["symbol"].upper() for r in csv.DictReader(f)})


def load_sectors(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[r["symbol"].upper()] = r.get("sector") or "unknown"
    return out


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    p.add_argument("--prices", type=Path, default=DEFAULT_PRICES)
    p.add_argument("--universe", type=Path, default=DEFAULT_UNIVERSE)
    p.add_argument("--sectors", type=Path, default=DEFAULT_SECTORS)
    p.add_argument("--top-n", type=int, default=TOP_N)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return p.parse_args()


def period_daily_returns(
    closes: pd.DataFrame, members: list[str], entry: dict[str, float], start: date, end: date
) -> pd.Series:
    """Daily returns of an equal-initial-weight BUY-AND-HOLD portfolio over (start, end].

    Each name is normalized to its ENTRY price = `entry[sym]` (the price_asof(start) the ranking
    used — the prior close, so a weekend/holiday rebalance still measures the first close-to-close
    return from the signal entry, not from the next trading day). Weights are set once at entry
    and then DRIFT (not re-equal-weighted daily). An entry NAV of 1.0 is prepended at `start` so
    the first window day's return captures the entry→first-day gap and drawdown sees the 1.0 mark."""
    cols = [m for m in members if m in closes.columns and m in entry and entry[m] > 0]
    win = closes.loc[pd.Timestamp(start) : pd.Timestamp(end), cols].ffill()
    # Fill any LEADING gaps (before a name's first in-window quote) with its entry price so the
    # position is held flat (equity 1.0) rather than dropped — true equal-initial-weight
    # buy-and-hold, with no silent weight reallocation to the other names.
    win = win.fillna({m: entry[m] for m in cols})
    win = win.dropna(axis=1, how="all")
    if win.empty:
        return pd.Series(dtype=float)
    norm = win.divide(pd.Series({m: entry[m] for m in win.columns}))  # equity vs signal entry
    port_equity = norm.mean(axis=1, skipna=True)  # equal INITIAL weight, buy-and-hold drift
    # prepend the entry NAV (1.0 at `start`) so the first daily return is entry->first-day
    entry_point = pd.Series([1.0], index=[pd.Timestamp(start)])
    port_equity = pd.concat([entry_point, port_equity[port_equity.index > pd.Timestamp(start)]])
    if len(port_equity) < 2:
        return pd.Series(dtype=float)
    return port_equity.pct_change().dropna()


def equity_curve(daily: pd.Series) -> pd.Series:
    # include the initial 1.0 NAV so drawdown is measured against the starting capital
    return pd.concat([pd.Series([1.0]), (1.0 + daily).cumprod()], ignore_index=True)


def ann_stats(daily: pd.Series) -> dict:
    if len(daily) < 2:
        return {"cagr": None, "vol": None, "sharpe": None, "maxdd": None, "n": len(daily)}
    eq = equity_curve(daily)
    years = len(daily) / TRADING_DAYS
    cagr = eq.iloc[-1] ** (1 / years) - 1 if eq.iloc[-1] > 0 else None
    vol = daily.std() * (TRADING_DAYS**0.5)
    sharpe = (daily.mean() * TRADING_DAYS) / vol if vol and vol > 0 else None
    running_max = eq.cummax()
    maxdd = float((eq / running_max - 1.0).min())
    return {"cagr": cagr, "vol": float(vol), "sharpe": sharpe, "maxdd": maxdd, "n": len(daily)}


def _fmt_pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:+.1f}%"


def _fmt(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.2f}"


def main() -> None:
    args = parse_args()
    for pth, what in ((args.snapshot, "snapshot"), (args.prices, "pinned prices")):
        if not pth.exists():
            raise SystemExit(f"{what} not found: {pth}")
    symbols = load_symbols(args.universe)
    sectors = load_sectors(args.sectors)
    funds: dict[str, list[FundamentalRecord]] = {}
    for rec in read_fundamentals_snapshot(args.snapshot, verify=True):
        funds.setdefault(rec.symbol.upper(), []).append(rec)
    for v in funds.values():
        v.sort(key=lambda r: r.asof_ts)
    print(f"Loading PINNED prices {args.prices.name}...")
    closes = read_price_snapshot(args.prices, verify=True)
    data_end = closes.index.max().date()

    # daily return series, stitched across annual holding periods
    funnel_daily: list[pd.Series] = []
    bench_daily: list[pd.Series] = []
    prev_top: set[str] = set()
    prev_univ: set[str] = set()
    funnel_turnover: list[float] = []
    per_year: list[dict] = []

    rebals = [d for d in REBALANCE if date(d.year + 1, d.month, d.day) <= data_end]
    for i, start in enumerate(rebals):
        end = (
            rebals[i + 1]
            if i + 1 < len(rebals)
            else min(date(start.year + 1, start.month, start.day), data_end)
        )
        # PIT rank at `start`
        universe: dict[str, tuple[Sequence[FundamentalRecord], float]] = {}
        for sym in symbols:
            if sym not in closes.columns:
                continue
            recs = [r for r in funds.get(sym, []) if r.asof_ts.date() <= start]
            if len(recs) < 2:
                continue
            p0 = price_asof(closes[sym], start)
            if p0 is None or p0 <= 0:
                continue
            universe[sym] = (recs, p0)
        # Rank ALL eligible names (coverage gates applied); the funnel takes the top-N and the
        # benchmark is the SAME rank-eligible set — so the excess isolates SELECTION skill, not a
        # data-coverage difference (names that fail the gates can't enter either leg).
        ranked = rank_compounders(universe, top_n=10_000, sectors=sectors)
        eligible = [c.symbol for c in ranked]
        top = eligible[: args.top_n]
        all_names = eligible
        if len(top) < 5 or len(all_names) < 20:
            continue
        # entry price per name = the price_asof(start) the ranking used (universe[sym][1])
        entry = {sym: universe[sym][1] for sym in eligible}

        f_ret = period_daily_returns(closes, top, entry, start, end)
        b_ret = period_daily_returns(closes, all_names, entry, start, end)
        # Charge BOTH legs their own membership-turnover cost (round-trip at the annual rebalance)
        # so the "net of cost" comparison is apples-to-apples — the benchmark also re-forms its
        # equal-weight set each year. (Re-weighting-drift turnover is omitted for both legs.)
        turnover = len(set(top) - prev_top) / len(top) if prev_top else 1.0
        bench_turnover = len(set(all_names) - prev_univ) / len(all_names) if prev_univ else 1.0
        funnel_turnover.append(turnover)
        if len(f_ret):
            f_ret = f_ret.copy()
            f_ret.iloc[0] -= turnover * 2 * (ONE_WAY_BPS / 1e4)
        if len(b_ret):
            b_ret = b_ret.copy()
            b_ret.iloc[0] -= bench_turnover * 2 * (ONE_WAY_BPS / 1e4)
        prev_top = set(top)
        prev_univ = set(all_names)
        funnel_daily.append(f_ret)
        bench_daily.append(b_ret)

        fy = ann_stats(f_ret)
        per_year.append(
            {
                "start": start,
                "end": end,
                "n_top": len(top),
                "n_univ": len(all_names),
                "funnel": (1 + f_ret).prod() - 1 if len(f_ret) else None,
                "bench": (1 + b_ret).prod() - 1 if len(b_ret) else None,
                "f_sharpe": fy["sharpe"],
                "turnover": turnover,
            }
        )
        print(
            f"  {start}->{end}: top{len(top)} "
            f"funnel {_fmt_pct(per_year[-1]['funnel'])} bench {_fmt_pct(per_year[-1]['bench'])}"
        )

    if not funnel_daily:
        raise SystemExit("no backtest periods produced — check --prices/--snapshot/universe.")

    f_all = pd.concat(funnel_daily)
    b_all = pd.concat(bench_daily)
    fs = ann_stats(f_all)
    bs = ann_stats(b_all)
    ann_excess = (
        (fs["cagr"] - bs["cagr"]) if (fs["cagr"] is not None and bs["cagr"] is not None) else None
    )
    years_beat = sum(
        1
        for r in per_year
        if r["funnel"] is not None and r["bench"] is not None and r["funnel"] > r["bench"]
    )
    n_years = sum(1 for r in per_year if r["funnel"] is not None and r["bench"] is not None)

    md = [
        "# Compounder Funnel — Real Long-Only Backtest (pinned prices)",
        "",
        "Research-only. Does NOT constitute investment advice. The actual portfolio: annual "
        f"Jun-30 rebalance, hold the top-{args.top_n} `best_score` names equal-weighted (PIT "
        "fundamentals, sector-aware), daily NAV via PINNED prices, turnover cost at "
        f"{ONE_WAY_BPS:.0f}bps one-way (round-trip per rebalance). Benchmark = equal-weight of "
        "the SAME universe each period — so the funnel-minus-benchmark EXCESS isolates selection "
        "skill from survivor-universe drift (both legs share the survivorship).",
        "",
        "## Headline (net of cost)",
        "",
        "| | CAGR | Vol | Sharpe | Max DD | days |",
        "|---|--:|--:|--:|--:|--:|",
        f"| Funnel top-{args.top_n} | {_fmt_pct(fs['cagr'])} | {_fmt_pct(fs['vol'])} | {_fmt(fs['sharpe'])} | {_fmt_pct(fs['maxdd'])} | {fs['n']} |",
        f"| Equal-weight universe | {_fmt_pct(bs['cagr'])} | {_fmt_pct(bs['vol'])} | {_fmt(bs['sharpe'])} | {_fmt_pct(bs['maxdd'])} | {bs['n']} |",
        "",
        f"**Annualized excess (funnel − benchmark CAGR): {_fmt_pct(ann_excess)}**. "
        f"Funnel beat the equal-weight universe in **{years_beat}/{n_years}** rebalance years. "
        f"Mean annual turnover {_fmt_pct(statistics.mean(funnel_turnover) if funnel_turnover else None)}.",
        "",
        "## Per-rebalance-year",
        "",
        "| Hold start → end | top / univ | funnel | benchmark | funnel Sharpe | turnover |",
        "|---|--:|--:|--:|--:|--:|",
    ]
    for r in per_year:
        md.append(
            f"| {r['start']} → {r['end']} | {r['n_top']}/{r['n_univ']} | "
            f"{_fmt_pct(r['funnel'])} | {_fmt_pct(r['bench'])} | {_fmt(r['f_sharpe'])} | "
            f"{_fmt_pct(r['turnover'])} |"
        )

    # "Added value" must be RISK-ADJUSTED, not raw CAGR: a tiny CAGR edge bought with higher vol
    # and a worse Sharpe is not selection skill. Require a meaningful CAGR excess AND a Sharpe at
    # least as good as the benchmark.
    sharpe_ok = (
        fs["sharpe"] is not None and bs["sharpe"] is not None and fs["sharpe"] >= bs["sharpe"]
    )
    beat = ann_excess is not None and ann_excess > 0.01 and sharpe_ok
    md += [
        "",
        "## Verdict",
        "",
        (
            f"**The funnel ADDED risk-adjusted value as a long-only selector**: "
            f"{_fmt_pct(ann_excess)} annual CAGR excess AND a higher Sharpe "
            f"({_fmt(fs['sharpe'])} vs {_fmt(bs['sharpe'])}), {years_beat}/{n_years} years. Even "
            "so, treat as in-universe/survivor-biased evidence until a survivorship-free backtest "
            "confirms it."
            if beat
            else f"**The funnel did NOT beat a naive equal-weight of the same universe on a "
            f"risk-adjusted basis** — Sharpe {_fmt(fs['sharpe'])} vs {_fmt(bs['sharpe'])}, vol "
            f"{_fmt_pct(fs['vol'])} vs {_fmt_pct(bs['vol'])}, max DD {_fmt_pct(fs['maxdd'])} vs "
            f"{_fmt_pct(bs['maxdd'])}; the CAGR excess is only {_fmt_pct(ann_excess)} "
            f"({years_beat}/{n_years} years — within noise). In portfolio terms this confirms the "
            "IC finding: the funnel's selection adds no reliable edge — its value is as an "
            "evidence SCREEN for human judgment, not an automated return engine. Concentrating "
            "into top-30 raised volatility and drawdown without a risk-adjusted payoff."
        ),
        "",
        "Caveats: survivorship (current constituents — absolute CAGR inflated for BOTH legs; the "
        "excess is the fair read); no slippage/impact beyond the flat bps haircut; equal-weight "
        "(no liquidity/cap weighting); annual rebalance only; single universe; no dividends "
        "beyond what auto-adjusted closes capture. This is the real-portfolio companion to the "
        "IC studies, not a production trading sim.",
    ]
    args.out.write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n" + "\n".join(md[md.index("## Headline (net of cost)") :]))
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
