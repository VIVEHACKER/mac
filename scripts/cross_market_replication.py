"""Cross-market replication of the IDEAL momentum edge (Europe / Japan + US control).

Tests whether the transferable core of the strategy — cross-sectional 12-1 momentum —
reproduces on INDEPENDENT markets. Independent markets are genuinely new samples, so a
positive result is the strongest evidence the edge is not US-specific data-mining.

Prices are fetched once from yfinance and cached to data/snapshots/cross-market-*.csv
(reproducible from cache; pass --refetch to refresh). Momentum is a unitless return, so
mixed currencies need no FX conversion (both the top-N and the equal-weight benchmark are
in local-currency returns, so the excess is currency-neutral).

Usage:
    python -m scripts.cross_market_replication
    python -m scripts.cross_market_replication --refetch --start 2010-01-01 --end 2024-12-31
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.price_snapshot import read_price_snapshot  # noqa: E402
from engine.replication import ReplicationResult, momentum_replication  # noqa: E402
from engine.significance import block_bootstrap_sharpe  # noqa: E402
from scripts.aqr_ideal_walkforward import MEGACAPS  # noqa: E402

SNAP_DIR = ROOT / "data" / "snapshots"
US_PRICES = SNAP_DIR / "prices-ideal-2026-06-01.csv"

EUROPE = [
    "ASML.AS",
    "MC.PA",
    "OR.PA",
    "SAP.DE",
    "SIE.DE",
    "TTE.PA",
    "SAN.PA",
    "AIR.PA",
    "SU.PA",
    "ALV.DE",
    "DTE.DE",
    "BAS.DE",
    "BMW.DE",
    "MBG.DE",
    "IBE.MC",
    "ITX.MC",
    "ENEL.MI",
    "ENI.MI",
    "NESN.SW",
    "ROG.SW",
    "NOVN.SW",
    "ABBN.SW",
    "PHIA.AS",
    "DG.PA",
    "BNP.PA",
    "CS.PA",
    "EL.PA",
    "SAN.MC",
]
JAPAN = [
    "7203.T",
    "6758.T",
    "6861.T",
    "9984.T",
    "8306.T",
    "9432.T",
    "6098.T",
    "4063.T",
    "8035.T",
    "6501.T",
    "7974.T",
    "9433.T",
    "8316.T",
    "4502.T",
    "6902.T",
    "7267.T",
    "6594.T",
    "8058.T",
    "8001.T",
    "6273.T",
    "4661.T",
    "9983.T",
    "6367.T",
    "7741.T",
    "8766.T",
]


def _fetch(tickers: list[str], start: str, end: str) -> pd.DataFrame:
    import yfinance as yf

    raw = yf.download(tickers, start=start, end=end, auto_adjust=True, progress=False)
    close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw
    return close.dropna(axis=1, how="all")


def _load_region(
    name: str, tickers: list[str], start: str, end: str, refetch: bool
) -> pd.DataFrame:
    cache = SNAP_DIR / f"cross-market-{name}.csv"
    if cache.exists() and not refetch:
        return pd.read_csv(cache, index_col=0, parse_dates=True)
    prices = _fetch(tickers, start, end)
    cache.parent.mkdir(parents=True, exist_ok=True)
    prices.to_csv(cache)
    return prices


def _verdict(result: ReplicationResult, excess_ir_ci_low: float) -> str:
    # The test is whether the EXCESS over the benchmark is real, not whether the long
    # book has a positive Sharpe (trivially true in a bull market). So the gate is the
    # excess information-ratio CI excluding zero, plus a positive rank-IC.
    if result.mean_rank_ic > 0.0 and result.excess_ann > 0.0 and excess_ir_ci_low > 0.0:
        return "REPLICATES — momentum EXCESS over benchmark is significant (IR CI > 0)"
    if result.mean_rank_ic > 0.0 and result.excess_ann > 0.0:
        return "WEAK-POSITIVE — right sign but excess not significant (supportive only)"
    return "DOES NOT REPLICATE — no momentum excess in this market (red flag)"


def _report_region(result: ReplicationResult) -> list[str]:
    # Bootstrap the EXCESS series (long - benchmark): its Sharpe is the information ratio
    # of the momentum tilt, and a CI excluding zero is the actual significance test.
    boot = (
        block_bootstrap_sharpe(
            result.excess_monthly_returns, n_boot=5000, block_size=6, periods_per_year=12
        )
        if result.n_rebalances > 2
        else None
    )
    ci_low = boot.ci_low if boot else float("nan")
    ci_high = boot.ci_high if boot else float("nan")
    lines = [
        f"## {result.region}",
        f"- **VERDICT: {_verdict(result, ci_low if boot else -1.0)}**",
        f"- symbols: {result.n_symbols} | rebalances: {result.n_rebalances}",
        f"- **mean rank-IC: {result.mean_rank_ic:+.4f}** (momentum→forward-return; >0 = edge present)",
        f"- top-N annualised excess vs equal-weight: {result.excess_ann:+.2%}",
        f"- monthly win rate (top-N beats benchmark): {result.monthly_win_rate:.1%}",
        f"- context: top-N Sharpe {result.long_sharpe:.2f} vs benchmark {result.bench_sharpe:.2f}",
    ]
    if boot:
        lines.append(
            f"- **excess information-ratio bootstrap 95% CI: [{ci_low:.2f}, {ci_high:.2f}]** "
            f"(null p={boot.p_value_null:.3f})"
        )
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description="Cross-market momentum replication.")
    parser.add_argument("--start", default="2010-01-01")
    parser.add_argument("--end", default="2024-12-31")
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--refetch", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "out" / "cross-market-replication.md")
    args = parser.parse_args()

    out: list[str] = ["# Cross-market momentum replication", ""]
    out.append(
        "Same cross-sectional 12-1 momentum rule (top-N equal-weight, monthly rebalance) "
        "run on independent markets. US is the positive control (known edge)."
    )
    out.append("")

    # US positive control (from the pinned IDEAL snapshot).
    us_prices = read_price_snapshot(US_PRICES, verify=True)
    us_cols = [s for s in MEGACAPS if s in us_prices.columns]
    us = momentum_replication(us_prices[us_cols], region="US (control)", top_n=7)
    out += _report_region(us)
    out.append("")

    for name, tickers in (("Europe", EUROPE), ("Japan", JAPAN)):
        prices = _load_region(name, tickers, args.start, args.end, args.refetch)
        result = momentum_replication(prices, region=name, top_n=args.top_n)
        out += _report_region(result)
        out.append("")

    out.append("## Honest reading")
    out.append("- Asness-Moskowitz-Pedersen (2013) find momentum everywhere EXCEPT weak in")
    out.append("  Japan — a weak/negative Japan result is expected, not a harness failure.")
    out.append("- Replication in Europe is genuine new-sample evidence the edge is not US-only.")
    out.append("- This validates the MOMENTUM leg (the transferable core); the full V+M+Q")
    out.append("  composite still needs non-US fundamentals to replicate fully.")
    out.append("- Prices cached (not yet content-hash pinned); yfinance may revise — re-cache")
    out.append("  with --refetch and treat sign/consistency, not 3rd-decimal, as the signal.")

    report = "\n".join(out)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report + "\n", encoding="utf-8")
    print(report)
    print(f"\nwrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
