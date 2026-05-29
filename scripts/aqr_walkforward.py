"""AQR factor walk-forward using SEC EDGAR PIT fundamentals.

Each rebalance:
1. For each ticker, fetch latest fundamental with asof_ts <= rebal_date.
2. Build price-bar history up to rebal_date.
3. Score via strategies.factor_aqr.rank_aqr_factors (Value + Momentum + Quality Z).
4. Pick top-N by composite, weight inverse-vol, hold 21 days.
5. Compare cumulative vs SPY.
"""

from __future__ import annotations

import math
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.catalog import MarketDataCatalog  # noqa: E402
from data.models import FundamentalRecord, PriceBar  # noqa: E402
from strategies.factor_aqr import rank_aqr_factors  # noqa: E402

MEGACAPS = [
    "AAPL",
    "MSFT",
    "GOOGL",
    "AMZN",
    "NVDA",
    "META",
    "TSLA",
    "BRK-B",
    "JPM",
    "JNJ",
    "V",
    "PG",
    "MA",
    "HD",
    "CVX",
    "KO",
    "PEP",
    "WMT",
    "UNH",
    "XOM",
    "COST",
    "LLY",
    "ABBV",
    "AVGO",
    "MRK",
    "ABT",
    "ORCL",
    "CRM",
    "AMD",
    "NFLX",
]
BENCHMARK = "SPY"
OUT_DIR = ROOT / "out"


def build_pricebars(
    prices: pd.DataFrame, symbol: str, end: pd.Timestamp, lookback_bars: int = 260
) -> list[PriceBar]:
    if symbol not in prices.columns:
        return []
    series = prices[symbol].loc[:end].dropna().tail(lookback_bars)
    if len(series) < lookback_bars:
        return []
    return [
        PriceBar(
            symbol=symbol,
            market="us",
            source_symbol=symbol,
            freq="1d",
            ts=ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
            open=float(v),
            high=float(v),
            low=float(v),
            close=float(v),
            volume=0.0,
            currency="USD",
            source="yfinance",
        )
        for ts, v in series.items()
    ]


def maxdd(equity: pd.Series) -> float:
    peak = equity.cummax()
    return float(((peak - equity) / peak).max())


def main() -> None:
    catalog = MarketDataCatalog()

    print(f"Downloading {len(MEGACAPS) + 1} symbols from yfinance...")
    raw = yf.download(
        MEGACAPS + [BENCHMARK],
        start="2011-01-01",
        end="2026-05-25",
        auto_adjust=True,
        progress=False,
    )
    prices = raw["Close"].dropna(how="all")
    print(f"Prices: {len(prices)} bars")

    # Generate month-end rebalance dates from 2013-01-31 to 2026-04-30
    rebal_dates = []
    cur = pd.Timestamp("2013-01-31")
    end = pd.Timestamp("2026-04-30")
    while cur <= end:
        rebal_dates.append(cur)
        cur = cur + pd.offsets.MonthEnd(1)

    print(f"Rebalances: {len(rebal_dates)}")

    equity = 10_000.0
    spy_eq = 10_000.0
    records = []

    for as_of in rebal_dates:
        valid = prices.index[prices.index <= as_of]
        if len(valid) == 0:
            continue
        rebal = valid[-1]

        # Pull PIT fundamentals + bars for each ticker
        bars_by_sym: dict[str, list[PriceBar]] = {}
        fund_by_sym: dict[str, FundamentalRecord] = {}
        for sym in MEGACAPS:
            funds = catalog.get_fundamentals(
                symbol=sym,
                market="us",
                as_of=datetime(rebal.year, rebal.month, rebal.day),
                limit=1,
            )
            if not funds:
                continue
            bars = build_pricebars(prices, sym, rebal)
            if not bars:
                continue
            fund_by_sym[sym.upper()] = funds[0]
            bars_by_sym[sym] = bars

        if len(bars_by_sym) < 5:
            continue

        scores = rank_aqr_factors(bars_by_sym, fund_by_sym, lookback=126)
        if not scores:
            continue

        # Top 2 by composite, equal weight (could do inverse-vol)
        top = scores[:2]

        # 21-day forward return
        future = prices.index[prices.index > rebal][:21]
        if len(future) < 21:
            break
        end_ts = future[-1]

        port_ret = 0.0
        weights_str = []
        for s in top:
            sym = s.symbol
            try:
                ret = float(prices.loc[end_ts, sym] / prices.loc[rebal, sym] - 1.0)
            except (KeyError, TypeError):
                continue
            port_ret += 0.5 * ret
            weights_str.append(f"{sym}({s.composite:.1f})")

        try:
            spy_ret = float(prices.loc[end_ts, BENCHMARK] / prices.loc[rebal, BENCHMARK] - 1.0)
        except KeyError:
            continue

        equity *= 1.0 + port_ret
        spy_eq *= 1.0 + spy_ret

        records.append(
            {
                "as_of": rebal.date(),
                "picks": ",".join(weights_str),
                "port_ret": port_ret,
                "spy_ret": spy_ret,
                "equity": equity,
                "spy_equity": spy_eq,
            }
        )

    df = pd.DataFrame(records)
    df.to_csv(OUT_DIR / "aqr-walkforward.csv", index=False)
    print(f"Recorded {len(df)} months")

    months = len(df)
    years = months / 12.0
    port_ann = (df["equity"].iloc[-1] / 10_000.0) ** (1.0 / years) - 1.0
    spy_ann = (df["spy_equity"].iloc[-1] / 10_000.0) ** (1.0 / years) - 1.0
    port_sharpe = (df["port_ret"].mean() / df["port_ret"].std()) * math.sqrt(12.0)
    spy_sharpe = (df["spy_ret"].mean() / df["spy_ret"].std()) * math.sqrt(12.0)
    port_mdd = maxdd(df["equity"])
    spy_mdd = maxdd(df["spy_equity"])
    beat = float((df["port_ret"] > df["spy_ret"]).mean()) * 100.0

    out = [
        "# AQR Factor Walk-Forward (PIT Fundamentals)",
        "",
        f"Period: {df['as_of'].iloc[0]} to {df['as_of'].iloc[-1]} "
        f"({months} months, {years:.2f} years)",
        "PIT source: SEC EDGAR companyfacts (2064 records across 30 mega caps)",
        "Strategy: top-2 by AQR composite (Value+Momentum+Quality Z), equal weight, 21d hold",
        "Caveats: survivorship bias (current mega caps), no fees modeled.",
        "",
        "## Headline",
        "",
        "| Metric | Strategy | SPY |",
        "|---|---:|---:|",
        f"| Final Equity ($10K) | ${df['equity'].iloc[-1]:,.0f} | ${df['spy_equity'].iloc[-1]:,.0f} |",
        f"| Cumulative Return | {(df['equity'].iloc[-1] / 10_000.0 - 1) * 100:+.1f}% | {(df['spy_equity'].iloc[-1] / 10_000.0 - 1) * 100:+.1f}% |",
        f"| Annualized | {port_ann * 100:+.2f}% | {spy_ann * 100:+.2f}% |",
        f"| Sharpe | {port_sharpe:.2f} | {spy_sharpe:.2f} |",
        f"| Max Drawdown | {port_mdd * 100:.2f}% | {spy_mdd * 100:.2f}% |",
        f"| Months beating SPY | {beat:.1f}% |  |",
        "",
        "## Last 10 picks",
        "",
        "| Date | Picks (composite) | Port Ret | SPY Ret |",
        "|---|---|---:|---:|",
    ]
    for _, r in df.tail(10).iterrows():
        out.append(
            f"| {r['as_of']} | {r['picks']} | {r['port_ret'] * 100:+.2f}% | {r['spy_ret'] * 100:+.2f}% |"
        )

    (OUT_DIR / "aqr-walkforward-summary.md").write_text("\n".join(out) + "\n")
    print("\n".join(out))


if __name__ == "__main__":
    main()
