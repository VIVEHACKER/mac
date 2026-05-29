"""AQR factor walk-forward with diversification + vol-targeting variants.

Sweep configs to find balanced (return, Sharpe, MDD) sweet spot:
  - top-2 base (NFLX concentration problem)
  - top-3 / top-5 / top-7 with per-symbol cap
  - inverse-vol weighting
  - post-hoc monthly vol-targeting overlay (smooth equity curve)

Uses SEC EDGAR PIT fundamentals stored by sec_edgar_ingest.py.
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
    # Original 30 mega caps (current winners — survivorship-biased subset)
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
    # 20 historical S&P 100 names that underperformed / faded (survivorship-test)
    "INTC",
    "IBM",
    "CSCO",
    "T",
    "VZ",
    "GE",
    "F",
    "GM",
    "MO",
    "C",
    "WFC",
    "BAC",
    "GS",
    "MS",
    "DD",
    "DIS",
    "BA",
    "MMM",
    "GILD",
    "AMGN",
]
BENCHMARK = "SPY"
OUT_DIR = ROOT / "out"


CONFIGS = [
    {"name": "top2_eq", "top_n": 2, "cap": 0.50, "weighting": "equal", "vol_target": None},
    {"name": "top3_cap40", "top_n": 3, "cap": 0.40, "weighting": "inverse-vol", "vol_target": None},
    {"name": "top5_cap25", "top_n": 5, "cap": 0.25, "weighting": "inverse-vol", "vol_target": None},
    {"name": "top7_cap20", "top_n": 7, "cap": 0.20, "weighting": "inverse-vol", "vol_target": None},
    {"name": "top5_vol22", "top_n": 5, "cap": 0.25, "weighting": "inverse-vol", "vol_target": 0.22},
    {"name": "top5_vol18", "top_n": 5, "cap": 0.25, "weighting": "inverse-vol", "vol_target": 0.18},
]


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


def vol_estimate(prices: pd.DataFrame, symbol: str, end: pd.Timestamp, window: int = 63) -> float:
    if symbol not in prices.columns:
        return 0.30
    rets = prices[symbol].loc[:end].pct_change().dropna().tail(window)
    if len(rets) < window // 2:
        return 0.30
    return max(float(rets.std()) * math.sqrt(252.0), 0.05)


def weights_from_picks(
    picks, prices: pd.DataFrame, rebal: pd.Timestamp, cap: float, weighting: str
) -> dict[str, float]:
    n = len(picks)
    if n == 0:
        return {}
    if weighting == "equal":
        raw = {p.symbol: 1.0 for p in picks}
    else:
        raw = {p.symbol: 1.0 / vol_estimate(prices, p.symbol, rebal) for p in picks}
    # Apply per-symbol cap iteratively
    for _ in range(10):
        total = sum(raw.values())
        if total <= 0:
            return {}
        weights = {s: w / total for s, w in raw.items()}
        over = {s: w for s, w in weights.items() if w > cap}
        if not over:
            return weights
        # Cap and redistribute
        excess = sum(w - cap for w in over.values())
        free_syms = [s for s in weights if s not in over]
        for s in over:
            raw[s] = cap * total
        if free_syms:
            free_total = sum(raw[s] for s in free_syms)
            for s in free_syms:
                raw[s] *= (
                    (free_total + excess * total) / max(free_total, 1e-9) if free_total > 0 else 1.0
                )
    return {s: w / sum(raw.values()) for s, w in raw.items()}


def maxdd(equity: pd.Series) -> float:
    peak = equity.cummax()
    return float(((peak - equity) / peak).max())


def prefetch_fundamentals(catalog: MarketDataCatalog) -> dict[str, list[FundamentalRecord]]:
    """Load all fundamentals once per symbol, sorted ascending by asof_ts."""
    cache: dict[str, list[FundamentalRecord]] = {}
    for sym in MEGACAPS:
        records = catalog.get_fundamentals(symbol=sym, market="us", as_of=None, limit=500)
        cache[sym] = sorted(records, key=lambda r: r.asof_ts)
    return cache


def lookup_pit_fundamental(
    records: list[FundamentalRecord], as_of_dt: datetime
) -> FundamentalRecord | None:
    candidate: FundamentalRecord | None = None
    for rec in records:
        if rec.asof_ts <= as_of_dt:
            candidate = rec
        else:
            break
    return candidate


def run_config(
    cfg: dict,
    prices: pd.DataFrame,
    rebal_dates: list[pd.Timestamp],
    fund_cache: dict[str, list[FundamentalRecord]],
) -> dict:
    equity = 10_000.0
    spy_eq = 10_000.0
    records = []

    for as_of in rebal_dates:
        valid = prices.index[prices.index <= as_of]
        if len(valid) == 0:
            continue
        rebal = valid[-1]

        bars_by_sym: dict[str, list[PriceBar]] = {}
        fund_by_sym: dict[str, FundamentalRecord] = {}
        as_of_dt = datetime(rebal.year, rebal.month, rebal.day)
        for sym in MEGACAPS:
            records_for_sym = fund_cache.get(sym, [])
            fund = lookup_pit_fundamental(records_for_sym, as_of_dt)
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

        picks = scores[: cfg["top_n"]]
        weights = weights_from_picks(picks, prices, rebal, cfg["cap"], cfg["weighting"])
        if not weights:
            continue

        future = prices.index[prices.index > rebal][:21]
        if len(future) < 21:
            break
        end_ts = future[-1]

        port_ret = 0.0
        for sym, w in weights.items():
            try:
                ret = float(prices.loc[end_ts, sym] / prices.loc[rebal, sym] - 1.0)
            except (KeyError, TypeError):
                continue
            port_ret += w * ret

        try:
            spy_ret = float(prices.loc[end_ts, BENCHMARK] / prices.loc[rebal, BENCHMARK] - 1.0)
        except KeyError:
            continue

        # Vol targeting overlay (post-hoc rolling vol estimate -> scale)
        if cfg["vol_target"] is not None and len(records) >= 6:
            recent_rets = pd.Series([r["port_ret_raw"] for r in records[-6:]])
            realized_vol = float(recent_rets.std()) * math.sqrt(12.0)
            if realized_vol > 0:
                scale = min(cfg["vol_target"] / realized_vol, 1.3)  # cap leverage 1.3x
                gated_ret = port_ret * scale
            else:
                gated_ret = port_ret
        else:
            gated_ret = port_ret

        equity *= 1.0 + gated_ret
        spy_eq *= 1.0 + spy_ret

        records.append(
            {
                "as_of": rebal.date(),
                "port_ret_raw": port_ret,
                "port_ret": gated_ret,
                "spy_ret": spy_ret,
                "equity": equity,
                "spy_equity": spy_eq,
            }
        )

    df = pd.DataFrame(records)
    if df.empty:
        return {"name": cfg["name"], "error": "no_records"}

    months = len(df)
    years = months / 12.0
    port_ann = (df["equity"].iloc[-1] / 10_000.0) ** (1.0 / years) - 1.0
    spy_ann = (df["spy_equity"].iloc[-1] / 10_000.0) ** (1.0 / years) - 1.0
    port_sharpe = (df["port_ret"].mean() / df["port_ret"].std()) * math.sqrt(12.0)
    spy_sharpe = (df["spy_ret"].mean() / df["spy_ret"].std()) * math.sqrt(12.0)
    port_mdd = maxdd(df["equity"])
    spy_mdd = maxdd(df["spy_equity"])
    beat = float((df["port_ret"] > df["spy_ret"]).mean()) * 100.0

    df.to_csv(OUT_DIR / f"aqr-div-{cfg['name']}.csv", index=False)

    return {
        "name": cfg["name"],
        "final_equity": float(df["equity"].iloc[-1]),
        "cum_pct": (df["equity"].iloc[-1] / 10_000.0 - 1) * 100,
        "ann_pct": port_ann * 100,
        "sharpe": port_sharpe,
        "mdd_pct": port_mdd * 100,
        "beat_pct": beat,
        "spy_ann_pct": spy_ann * 100,
        "spy_sharpe": spy_sharpe,
        "spy_mdd_pct": spy_mdd * 100,
        "months": months,
    }


def main() -> None:
    print("Downloading prices...")
    raw = yf.download(
        MEGACAPS + [BENCHMARK],
        start="2011-01-01",
        end="2026-05-25",
        auto_adjust=True,
        progress=False,
    )
    prices = raw["Close"].dropna(how="all")
    print(f"Prices: {len(prices)} bars")

    catalog = MarketDataCatalog()
    print("Prefetching fundamentals (one-shot DB read)...")
    fund_cache = prefetch_fundamentals(catalog)
    total = sum(len(v) for v in fund_cache.values())
    print(f"Cached {total} fundamental records across {len(fund_cache)} symbols")

    rebal_dates = []
    cur = pd.Timestamp("2013-01-31")
    end = pd.Timestamp("2026-04-30")
    while cur <= end:
        rebal_dates.append(cur)
        cur = cur + pd.offsets.MonthEnd(1)

    summaries = []
    for cfg in CONFIGS:
        print(f"\n--- {cfg['name']} ---")
        s = run_config(cfg, prices, rebal_dates, fund_cache)
        summaries.append(s)
        if "error" not in s:
            print(
                f"  ann {s['ann_pct']:+.2f}%  Sharpe {s['sharpe']:.2f}  "
                f"MDD {s['mdd_pct']:.2f}%  Beat {s['beat_pct']:.1f}%"
            )

    spy = summaries[0]
    out = [
        "# AQR Diversification + Vol-Target Sweep\n",
        f"SPY benchmark: ann +{spy['spy_ann_pct']:.2f}%, "
        f"Sharpe {spy['spy_sharpe']:.2f}, MDD {spy['spy_mdd_pct']:.2f}%\n",
        f"Months: {spy['months']}",
        "",
        "| Config | Ann | Sharpe | MDD | Beat% | Final $10K |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for s in summaries:
        if "error" in s:
            out.append(f"| {s['name']} | ERROR |  |  |  |  |")
        else:
            out.append(
                f"| {s['name']} | {s['ann_pct']:+.2f}% | {s['sharpe']:.2f} | "
                f"{s['mdd_pct']:.2f}% | {s['beat_pct']:.1f}% | ${s['final_equity']:,.0f} |"
            )

    text = "\n".join(out) + "\n"
    (OUT_DIR / "aqr-diversified-comparison.md").write_text(text)
    print("\n" + text)


if __name__ == "__main__":
    main()
