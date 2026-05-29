"""VIX-conditional tail hedge overlay on top7_cap20 AQR strategy.

Hypothesis: reduce equity exposure when VIX is elevated to cut MDD < 18%.
Strategy stays top-7 AQR composite picks; hedge only adjusts gross exposure.

Configs (binary or gradient):
  - baseline:     100% exposure always
  - vix25_binary: 50% exposure when monthly avg VIX > 25
  - vix30_binary: 50% exposure when monthly avg VIX > 30
  - vix_gradient: linear ramp 100%→30% as VIX goes 20→35
  - vix_spike:    50% exposure when VIX rose >40% in past 21d

Cash return assumed 0% (could use BIL/SHY for realistic ~3-5% in low rate / 5%+ recent).
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
VIX_SYMBOL = "^VIX"
OUT_DIR = ROOT / "out"

TOP_N = 7
CAP = 0.20


def exposure_baseline(
    vix_now=0.0, vix_21d_avg=0.0, vix_21d_chg_pct=0.0, spy_dd=0.0, port_dd=0.0
) -> float:
    return 1.0


def exposure_vix25(
    vix_now=0.0, vix_21d_avg=0.0, vix_21d_chg_pct=0.0, spy_dd=0.0, port_dd=0.0
) -> float:
    return 0.5 if vix_21d_avg > 25 else 1.0


def exposure_vix30(
    vix_now=0.0, vix_21d_avg=0.0, vix_21d_chg_pct=0.0, spy_dd=0.0, port_dd=0.0
) -> float:
    return 0.5 if vix_21d_avg > 30 else 1.0


def exposure_gradient(
    vix_now=0.0, vix_21d_avg=0.0, vix_21d_chg_pct=0.0, spy_dd=0.0, port_dd=0.0
) -> float:
    v = vix_21d_avg
    raw = 1.0 - (v - 20) / 15.0 * 0.70
    return max(0.3, min(1.0, raw))


def exposure_spike(
    vix_now=0.0, vix_21d_avg=0.0, vix_21d_chg_pct=0.0, spy_dd=0.0, port_dd=0.0
) -> float:
    return 0.5 if vix_21d_chg_pct > 0.40 else 1.0


def exposure_spy_dd5(vix_now, vix_21d_avg, vix_21d_chg_pct, spy_dd=0.0, port_dd=0.0) -> float:
    return 0.5 if spy_dd < -0.05 else 1.0


def exposure_spy_dd10(vix_now, vix_21d_avg, vix_21d_chg_pct, spy_dd=0.0, port_dd=0.0) -> float:
    return 0.5 if spy_dd < -0.10 else 1.0


def exposure_port_trail10(vix_now, vix_21d_avg, vix_21d_chg_pct, spy_dd=0.0, port_dd=0.0) -> float:
    return 0.5 if port_dd < -0.10 else 1.0


def exposure_combo(vix_now, vix_21d_avg, vix_21d_chg_pct, spy_dd=0.0, port_dd=0.0) -> float:
    if port_dd < -0.10 or spy_dd < -0.08:
        return 0.5
    return 1.0


CONFIGS = [
    {"name": "baseline", "fn": exposure_baseline},
    {"name": "vix25_binary", "fn": exposure_vix25},
    {"name": "vix_gradient", "fn": exposure_gradient},
    {"name": "spy_dd5", "fn": exposure_spy_dd5},
    {"name": "spy_dd10", "fn": exposure_spy_dd10},
    {"name": "port_trail10", "fn": exposure_port_trail10},
    {"name": "combo_dd_trail", "fn": exposure_combo},
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


def weights_from_picks(picks, prices, rebal, cap=0.20):
    raw = {p.symbol: 1.0 / vol_estimate(prices, p.symbol, rebal) for p in picks}
    for _ in range(10):
        total = sum(raw.values())
        if total <= 0:
            return {}
        weights = {s: w / total for s, w in raw.items()}
        over = {s: w for s, w in weights.items() if w > cap}
        if not over:
            return weights
        excess = sum(w - cap for w in over.values())
        free = [s for s in weights if s not in over]
        for s in over:
            raw[s] = cap * total
        if free:
            free_total = sum(raw[s] for s in free)
            if free_total > 0:
                for s in free:
                    raw[s] *= (free_total + excess * total) / free_total
    return {s: w / sum(raw.values()) for s, w in raw.items()}


def maxdd(equity: pd.Series) -> float:
    peak = equity.cummax()
    return float(((peak - equity) / peak).max())


def prefetch_fundamentals(catalog: MarketDataCatalog) -> dict[str, list[FundamentalRecord]]:
    cache: dict[str, list[FundamentalRecord]] = {}
    for sym in MEGACAPS:
        records = catalog.get_fundamentals(symbol=sym, market="us", as_of=None, limit=500)
        cache[sym] = sorted(records, key=lambda r: r.asof_ts)
    return cache


def lookup_pit(records: list[FundamentalRecord], as_of_dt: datetime) -> FundamentalRecord | None:
    cand = None
    for rec in records:
        if rec.asof_ts <= as_of_dt:
            cand = rec
        else:
            break
    return cand


def run_hedge(
    cfg: dict,
    prices: pd.DataFrame,
    vix: pd.Series,
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
        as_of_dt = datetime(rebal.year, rebal.month, rebal.day)

        # VIX features
        vix_recent = vix.loc[:rebal].dropna().tail(21)
        if len(vix_recent) < 5:
            continue
        vix_now = float(vix_recent.iloc[-1])
        vix_avg = float(vix_recent.mean())
        vix_start = float(vix_recent.iloc[0])
        vix_chg = (vix_now - vix_start) / max(vix_start, 1e-9)

        # SPY drawdown from 252-day rolling peak
        spy_window = prices[BENCHMARK].loc[:rebal].dropna().tail(252)
        if len(spy_window) >= 21:
            spy_peak = float(spy_window.max())
            spy_dd = (float(spy_window.iloc[-1]) - spy_peak) / max(spy_peak, 1e-9)
        else:
            spy_dd = 0.0

        # Portfolio trailing drawdown from running peak
        port_peak = max([r["equity"] for r in records] + [10_000.0])
        port_dd = (equity - port_peak) / max(port_peak, 1e-9)

        exposure = cfg["fn"](
            vix_now=vix_now,
            vix_21d_avg=vix_avg,
            vix_21d_chg_pct=vix_chg,
            spy_dd=spy_dd,
            port_dd=port_dd,
        )

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
        picks = scores[:TOP_N]
        weights = weights_from_picks(picks, prices, rebal, cap=CAP)
        if not weights:
            continue

        future = prices.index[prices.index > rebal][:21]
        if len(future) < 21:
            break
        end_ts = future[-1]

        port_ret = 0.0
        for sym, w in weights.items():
            try:
                r = float(prices.loc[end_ts, sym] / prices.loc[rebal, sym] - 1.0)
            except (KeyError, TypeError):
                continue
            port_ret += w * r

        try:
            spy_ret = float(prices.loc[end_ts, BENCHMARK] / prices.loc[rebal, BENCHMARK] - 1.0)
        except KeyError:
            continue

        # Apply hedge: equity = exposure × stocks + (1-exposure) × cash(0%)
        net_ret = port_ret * exposure
        equity *= 1.0 + net_ret
        spy_eq *= 1.0 + spy_ret

        records.append(
            {
                "as_of": rebal.date(),
                "vix_avg": vix_avg,
                "vix_chg": vix_chg,
                "exposure": exposure,
                "port_ret_raw": port_ret,
                "port_ret_net": net_ret,
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
    port_sharpe = (df["port_ret_net"].mean() / df["port_ret_net"].std()) * math.sqrt(12.0)
    spy_sharpe = (df["spy_ret"].mean() / df["spy_ret"].std()) * math.sqrt(12.0)
    port_mdd = maxdd(df["equity"])
    spy_mdd = maxdd(df["spy_equity"])
    beat = float((df["port_ret_net"] > df["spy_ret"]).mean()) * 100.0
    avg_exp = float(df["exposure"].mean()) * 100

    df.to_csv(OUT_DIR / f"tail-hedge-{cfg['name']}.csv", index=False)

    return {
        "name": cfg["name"],
        "ann_pct": port_ann * 100,
        "sharpe": port_sharpe,
        "mdd_pct": port_mdd * 100,
        "beat_pct": beat,
        "avg_exposure_pct": avg_exp,
        "final_equity": float(df["equity"].iloc[-1]),
        "spy_ann_pct": spy_ann * 100,
        "spy_sharpe": spy_sharpe,
        "spy_mdd_pct": spy_mdd * 100,
        "months": months,
    }


def main() -> None:
    print("Downloading prices + VIX...")
    raw = yf.download(
        MEGACAPS + [BENCHMARK, VIX_SYMBOL],
        start="2011-01-01",
        end="2026-05-28",
        auto_adjust=True,
        progress=False,
    )
    prices = raw["Close"].dropna(how="all")
    vix = prices[VIX_SYMBOL].dropna()
    prices = prices.drop(columns=[VIX_SYMBOL])
    print(f"Prices: {len(prices)} bars, VIX: {len(vix)} bars")

    catalog = MarketDataCatalog()
    print("Prefetching fundamentals...")
    fund_cache = prefetch_fundamentals(catalog)
    total = sum(len(v) for v in fund_cache.values())
    print(f"Cached {total} records across {len(fund_cache)} symbols")

    rebal_dates = []
    cur = pd.Timestamp("2013-01-31")
    end = pd.Timestamp("2026-04-30")
    while cur <= end:
        rebal_dates.append(cur)
        cur = cur + pd.offsets.MonthEnd(1)

    summaries = []
    for cfg in CONFIGS:
        print(f"\n--- {cfg['name']} ---")
        s = run_hedge(cfg, prices, vix, rebal_dates, fund_cache)
        summaries.append(s)
        if "error" not in s:
            print(
                f"  ann {s['ann_pct']:+.2f}%  Sharpe {s['sharpe']:.2f}  "
                f"MDD {s['mdd_pct']:.2f}%  Exp {s['avg_exposure_pct']:.0f}%"
            )

    spy = summaries[0]
    out = [
        "# VIX Tail-Hedge Overlay on top7_cap20 AQR (50-stock universe)\n",
        f"SPY: ann +{spy['spy_ann_pct']:.2f}%, "
        f"Sharpe {spy['spy_sharpe']:.2f}, MDD {spy['spy_mdd_pct']:.2f}%\n",
        f"Months: {spy['months']}\n",
        "| Config | Ann | Sharpe | MDD | Beat% | Avg Exp | Final $10K |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for s in summaries:
        if "error" in s:
            out.append(f"| {s['name']} | ERROR |  |  |  |  |  |")
        else:
            out.append(
                f"| {s['name']} | {s['ann_pct']:+.2f}% | {s['sharpe']:.2f} | "
                f"{s['mdd_pct']:.2f}% | {s['beat_pct']:.1f}% | "
                f"{s['avg_exposure_pct']:.0f}% | ${s['final_equity']:,.0f} |"
            )

    text = "\n".join(out) + "\n"
    (OUT_DIR / "tail-hedge-comparison.md").write_text(text)
    print("\n" + text)


if __name__ == "__main__":
    main()
