"""Walk-forward validation of IDEAL config (top7_cap20 + port_trail10).

Rolling 5y train / 3y test, step 1y, 2013-2026.
Each test window: report Sharpe, MDD, ann-excess vs SPY.
Aggregate: positive_test_rate, avg_test_excess, worst_test_mdd → model-gate input.
"""

from __future__ import annotations

import math
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.catalog import MarketDataCatalog  # noqa: E402
from data.models import PriceBar  # noqa: E402
from data.price_snapshot import read_price_snapshot  # noqa: E402
from strategies.factor_aqr import rank_aqr_factors  # noqa: E402

MEGACAPS = [
    "AAPL",
    "ABBV",
    "ABT",
    "ACN",
    "ADBE",
    "ALL",
    "AMAT",
    "AMD",
    "AMGN",
    "AMT",
    "AMZN",
    "AVGO",
    "AXP",
    "BA",
    "BAC",
    "BAX",
    "BKNG",
    "BLK",
    "BMY",
    "BNY",
    "BRK-B",
    "C",
    "CAT",
    "CL",
    "CMCSA",
    "COF",
    "COP",
    "COST",
    "CRM",
    "CSCO",
    "CVS",
    "CVX",
    "DD",
    "DE",
    "DHR",
    "DIS",
    "DOW",
    "DUK",
    "EMR",
    "FDX",
    "GD",
    "GE",
    "GEV",
    "GILD",
    "GM",
    "GOOG",
    "GOOGL",
    "GS",
    "HD",
    "HON",
    "IBM",
    "INTC",
    "INTU",
    "ISRG",
    "JNJ",
    "JPM",
    "KO",
    "LIN",
    "LLY",
    "LMT",
    "LOW",
    "LRCX",
    "MA",
    "MCD",
    "MDLZ",
    "MDT",
    "META",
    "MMM",
    "MO",
    "MRK",
    "MS",
    "MSFT",
    "MU",
    "NEE",
    "NFLX",
    "NKE",
    "NOW",
    "NVDA",
    "ORCL",
    "PEP",
    "PFE",
    "PG",
    "PLTR",
    "PM",
    "QCOM",
    "RTX",
    "SBUX",
    "SCHW",
    "SO",
    "SPG",
    "T",
    "TGT",
    "TMO",
    "TMUS",
    "TSLA",
    "TXN",
    "UBER",
    "UNH",
    "UNP",
    "UPS",
    "USB",
    "V",
    "VZ",
    "WFC",
    "WMT",
    "XOM",
]
BENCHMARK = "SPY"
OUT_DIR = ROOT / "out"


def build_pricebars(prices, symbol, end, lookback_bars=260):
    if symbol not in prices.columns:
        return []
    s = prices[symbol].loc[:end].dropna().tail(lookback_bars)
    if len(s) < lookback_bars:
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
        for ts, v in s.items()
    ]


def vol_estimate(prices, symbol, end, window=63):
    if symbol not in prices.columns:
        return 0.30
    r = prices[symbol].loc[:end].pct_change().dropna().tail(window)
    if len(r) < window // 2:
        return 0.30
    return max(float(r.std()) * math.sqrt(252.0), 0.05)


def weights_from_picks(picks, prices, rebal, cap=0.20):
    raw = {p.symbol: 1.0 / vol_estimate(prices, p.symbol, rebal) for p in picks}
    for _ in range(10):
        total = sum(raw.values())
        if total <= 0:
            return {}
        w = {s: x / total for s, x in raw.items()}
        over = {s: x for s, x in w.items() if x > cap}
        if not over:
            return w
        excess = sum(x - cap for x in over.values())
        free = [s for s in w if s not in over]
        for s in over:
            raw[s] = cap * total
        if free:
            ft = sum(raw[s] for s in free)
            if ft > 0:
                for s in free:
                    raw[s] *= (ft + excess * total) / ft
    return {s: x / sum(raw.values()) for s, x in raw.items()}


def maxdd_series(equity):
    peak = equity.cummax()
    return float(((peak - equity) / peak).max())


def prefetch(catalog, snapshot_path=None):
    """Build {symbol: [records sorted by asof]}.

    If ``snapshot_path`` is given, load fundamentals from the content-verified
    snapshot (reproducible, parity with paper_drill); otherwise read the live
    catalog (NOT reproducible — drifts with background re-ingests).
    """
    if snapshot_path is not None:
        from data.fundamentals_snapshot import read_fundamentals_snapshot

        cache: dict[str, list] = {sym: [] for sym in MEGACAPS}
        for rec in read_fundamentals_snapshot(Path(snapshot_path), verify=True):
            cache.setdefault(rec.symbol.upper(), []).append(rec)
        for recs in cache.values():
            recs.sort(key=lambda r: r.asof_ts)
        return cache

    cache = {}
    for sym in MEGACAPS:
        recs = catalog.get_fundamentals(symbol=sym, market="us", as_of=None, limit=500)
        cache[sym] = sorted(recs, key=lambda r: r.asof_ts)
    return cache


def lookup_pit(records, as_of_dt):
    c = None
    for r in records:
        if r.asof_ts <= as_of_dt:
            c = r
        else:
            break
    return c


def run_window(start, end, prices, fund_cache):
    """Run IDEAL strategy over [start, end] and return metrics."""
    equity = 10_000.0
    spy_eq = 10_000.0
    monthly_rets = []
    spy_rets = []
    equity_series = []

    rebal_dates = []
    cur = pd.Timestamp(start) + pd.offsets.MonthEnd(0)
    while cur <= pd.Timestamp(end):
        rebal_dates.append(cur)
        cur = cur + pd.offsets.MonthEnd(1)

    for as_of in rebal_dates:
        valid = prices.index[prices.index <= as_of]
        if len(valid) == 0:
            continue
        rebal = valid[-1]
        as_of_dt = datetime(rebal.year, rebal.month, rebal.day)

        port_peak = max([equity] + ([equity_series[-1]["peak"]] if equity_series else [10_000.0]))
        port_dd = (equity - port_peak) / max(port_peak, 1e-9)
        exposure = 0.5 if port_dd < -0.10 else 1.0

        bars_by_sym, fund_by_sym = {}, {}
        for sym in MEGACAPS:
            fund = lookup_pit(fund_cache.get(sym, []), as_of_dt)
            if fund is None:
                continue
            bars = build_pricebars(prices, sym, rebal)
            if not bars:
                continue
            fund_by_sym[sym.upper()] = fund
            bars_by_sym[sym] = bars

        if len(bars_by_sym) < 5:
            continue
        scores = rank_aqr_factors(bars_by_sym, fund_by_sym, lookback=126)
        if not scores:
            continue
        picks = scores[:7]
        weights = weights_from_picks(picks, prices, rebal, cap=0.20)
        if not weights:
            continue

        future = prices.index[prices.index > rebal][:21]
        if len(future) < 21:
            break
        end_ts = future[-1]

        port_ret = 0.0
        for sym, w in weights.items():
            try:
                p_end, p_reb = prices.loc[end_ts, sym], prices.loc[rebal, sym]
            except KeyError:
                continue
            # A position with a valid entry but a missing FORWARD price (delisting / snapshot gap)
            # is held FLAT (0 return) at its weight — NOT renormalized onto the survivors, which
            # would be look-ahead (using future data-availability to reallocate). float(NaN) is
            # also not caught by except and would poison port_ret, so skip it explicitly (= flat).
            if pd.isna(p_end) or pd.isna(p_reb) or float(p_reb) == 0.0:
                continue
            port_ret += w * float(p_end / p_reb - 1.0)
        net_ret = port_ret * exposure

        try:
            sp_end, sp_reb = prices.loc[end_ts, BENCHMARK], prices.loc[rebal, BENCHMARK]
        except KeyError:
            continue
        if pd.isna(sp_end) or pd.isna(sp_reb) or float(sp_reb) == 0.0:
            continue
        spy_ret = float(sp_end / sp_reb - 1.0)

        equity *= 1.0 + net_ret
        spy_eq *= 1.0 + spy_ret
        monthly_rets.append(net_ret)
        spy_rets.append(spy_ret)
        new_peak = max(port_peak, equity)
        equity_series.append({"date": rebal.date(), "equity": equity, "peak": new_peak})

    if not monthly_rets:
        return None

    df_eq = pd.DataFrame(equity_series)
    months = len(monthly_rets)
    years = months / 12.0
    ann = (equity / 10_000.0) ** (1.0 / years) - 1.0
    spy_ann = (spy_eq / 10_000.0) ** (1.0 / years) - 1.0
    mr = pd.Series(monthly_rets)
    sr = pd.Series(spy_rets)
    sharpe = (mr.mean() / mr.std()) * math.sqrt(12.0) if mr.std() > 0 else 0.0
    spy_sharpe = (sr.mean() / sr.std()) * math.sqrt(12.0) if sr.std() > 0 else 0.0
    mdd = maxdd_series(df_eq["equity"])
    return {
        "start": str(start),
        "end": str(end),
        "months": months,
        "ann": ann,
        "spy_ann": spy_ann,
        "excess": ann - spy_ann,
        "sharpe": sharpe,
        "spy_sharpe": spy_sharpe,
        "mdd": mdd,
        # Full per-rebalance series, consumed by scripts/significance_test.py.
        "monthly_returns": list(monthly_rets),
        "spy_returns": list(spy_rets),
        "dates": [str(p["date"]) for p in equity_series],
    }


def main():
    import argparse

    parser = argparse.ArgumentParser(description="IDEAL walk-forward validation.")
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=None,
        help="Pin fundamentals to a content-verified snapshot CSV (reproducible, "
        "parity with paper_drill). Omit to read the live catalog.",
    )
    parser.add_argument(
        "--prices",
        type=Path,
        default=None,
        help="Pin prices to a content-verified price snapshot CSV (reproducible). Must cover "
        "MEGACAPS + the SPY benchmark. Omit to download live from yfinance (NOT reproducible).",
    )
    args = parser.parse_args()

    if args.prices:
        print(f"Loading PINNED prices {args.prices.name}...")
        prices = read_price_snapshot(args.prices, verify=True)
        missing = [s for s in [*MEGACAPS, BENCHMARK] if s not in prices.columns]
        if missing:
            raise SystemExit(
                f"pinned price snapshot missing {len(missing)} required symbols "
                f"(e.g. {missing[:5]}); regenerate it over MEGACAPS + {BENCHMARK}."
            )
        # The walk-forward needs effective coverage matching the live download (2008-01..2026-05):
        # build_pricebars uses a 260-bar (~1y) lookback before the first 2009 rebalance, and the
        # last 2023-2025 window rebalances through ~Dec 2025 and needs 21 trading days AFTER for
        # forward returns. Reject a snapshot that can't cover both — otherwise run_window silently
        # shortens/skips windows while the summary still labels them fixed 3y tests.
        # Check the BENCHMARK's OWN non-NaN coverage (not the global matrix min/max): excess is
        # computed vs SPY, so a snapshot where SPY has leading/trailing gaps — even if another
        # symbol spans the range — would yield NaN SPY/excess and corrupt the model-gate inputs.
        bench = prices[BENCHMARK].dropna()
        lo, hi = bench.index.min().date(), bench.index.max().date()
        if lo > date(2008, 1, 15) or hi < date(2026, 1, 31):
            raise SystemExit(
                f"pinned price snapshot {BENCHMARK} coverage {lo}..{hi} cannot cover the "
                "2009-2025 walk-forward with its ~1y lookback + 21-bar forward (needs "
                "≤2008-01-15 .. ≥2026-01-31); regenerate over the full 2008-01..2026-05 span."
            )
    else:
        print("Downloading prices (LIVE yfinance — NOT reproducible)...")
        raw = yf.download(
            MEGACAPS + [BENCHMARK],
            start="2008-01-01",
            end="2026-05-28",
            auto_adjust=True,
            progress=False,
        )
        prices = raw["Close"].dropna(how="all")
    print(f"{len(prices)} bars")

    catalog = MarketDataCatalog()
    print("Prefetching fundamentals...")
    fund_cache = prefetch(catalog, snapshot_path=args.snapshot)
    src = f"snapshot:{args.snapshot.name}" if args.snapshot else "LIVE-CATALOG (not reproducible)"
    print(f"Cached {sum(len(v) for v in fund_cache.values())} records  [{src}]")

    # Rolling: 5y train / 3y test, step 1y
    # Train is just for fundamental data availability; AQR uses point-in-time
    # so we report test-window metrics only.
    windows = []
    start_year = 2009
    while start_year + 3 <= 2026:
        test_start = pd.Timestamp(f"{start_year}-01-01")
        test_end = pd.Timestamp(f"{start_year + 3}-01-01") - pd.Timedelta(days=1)
        windows.append((test_start, test_end))
        start_year += 1

    print(f"\nWalk-forward windows: {len(windows)}")
    results = []
    for ws, we in windows:
        r = run_window(ws, we, prices, fund_cache)
        if r:
            results.append(r)
            print(
                f"  {r['start'][:10]} → {r['end'][:10]}  "
                f"ann {r['ann'] * 100:+6.2f}%  excess {r['excess'] * 100:+6.2f}%  "
                f"Sharpe {r['sharpe']:.2f}  MDD {r['mdd'] * 100:.2f}%"
            )

    df = pd.DataFrame(results)
    df.to_csv(OUT_DIR / "aqr-ideal-walkforward.csv", index=False)

    pos_rate = float((df["excess"] > 0).mean()) * 100
    avg_excess = float(df["excess"].mean()) * 100
    worst_mdd = float(df["mdd"].max()) * 100
    avg_sharpe = float(df["sharpe"].mean())

    summary = [
        "# IDEAL AQR Walk-Forward (top7_cap20 + port_trail10)\n",
        "Universe: 50 stocks (mega + faded), 50-stock survivorship-tested",
        f"Test windows: {len(df)} × 3-year rolling, step 1y",
        "",
        "## Aggregate (model-gate inputs)",
        "",
        f"- Positive test rate: **{pos_rate:.1f}%**",
        f"- Average test annualized excess: **{avg_excess:+.2f}%**",
        f"- Worst test MDD: **{worst_mdd:.2f}%**",
        f"- Average test Sharpe: **{avg_sharpe:.2f}**",
        "",
        "## Windows",
        "",
        "| Test Start | Test End | Months | Ann | SPY Ann | Excess | Sharpe | MDD |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        summary.append(
            f"| {r['start'][:10]} | {r['end'][:10]} | {r['months']} | "
            f"{r['ann'] * 100:+.2f}% | {r['spy_ann'] * 100:+.2f}% | "
            f"{r['excess'] * 100:+.2f}% | {r['sharpe']:.2f} | {r['mdd'] * 100:.2f}% |"
        )

    text = "\n".join(summary) + "\n"
    (OUT_DIR / "aqr-ideal-walkforward.md").write_text(text)
    print("\n" + text)
    print("\nFor model-gate:")
    print(f"  --windows {len(df)}")
    print(f"  --positive-test-rate {pos_rate / 100:.4f}")
    print(f"  --avg-test-excess {avg_excess / 100:.4f}")
    print(f"  --worst-test-mdd {worst_mdd / 100:.4f}")


if __name__ == "__main__":
    main()
