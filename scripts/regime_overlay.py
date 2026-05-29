"""Regime-gated mega-cap momentum overlay.

Reuses regime labels from `out/regime-aware-backtest-2008-2026.md` and overlays
top-2 inverse-vol momentum on 30 mega caps, then applies a regime-dependent
leverage multiplier. Reports cumulative return vs SPY with Sharpe and MaxDD.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import pandas as pd
import yfinance as yf

MEGACAPS: list[str] = [
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
DEFENSIVE = "TLT"
REGIMES: tuple[str, ...] = (
    "Panic / Risk-Off Crisis",
    "Recession",
    "Deflation Risk",
    "Inflation Shock",
    "Mixed / Transition",
    "V-Bottom Recovery",
    "Disinflationary Expansion",
    "Easy Money / QE",
)

REGIME_PRESETS: dict[str, dict[str, float]] = {
    "aggressive": {
        "Panic / Risk-Off Crisis": 0.30,
        "Recession": 0.50,
        "Deflation Risk": 0.55,
        "Inflation Shock": 0.85,
        "Mixed / Transition": 0.90,
        "V-Bottom Recovery": 1.30,
        "Disinflationary Expansion": 1.10,
        "Easy Money / QE": 1.50,
    },
    "conservative": {
        "Panic / Risk-Off Crisis": 0.20,
        "Recession": 0.40,
        "Deflation Risk": 0.50,
        "Inflation Shock": 0.70,
        "Mixed / Transition": 0.85,
        "V-Bottom Recovery": 1.00,
        "Disinflationary Expansion": 1.00,
        "Easy Money / QE": 1.00,
    },
    "balanced": {
        "Panic / Risk-Off Crisis": 0.40,
        "Recession": 0.60,
        "Deflation Risk": 0.65,
        "Inflation Shock": 0.85,
        "Mixed / Transition": 0.95,
        "V-Bottom Recovery": 1.10,
        "Disinflationary Expansion": 1.00,
        "Easy Money / QE": 1.20,
    },
    "no_overlay": dict.fromkeys(REGIMES, 1.0),
}
REGIME_LEVERAGE: dict[str, float] = REGIME_PRESETS["aggressive"]

OUT_DIR = Path(__file__).resolve().parent.parent / "out"


def parse_regime_table(md_path: Path) -> pd.DataFrame:
    text = md_path.read_text()
    lines = text.splitlines()
    rows: list[tuple[str, str]] = []
    in_period = False
    for line in lines:
        if line.startswith("## Period Detail"):
            in_period = True
            continue
        if in_period and line.startswith("##"):
            break
        if in_period and re.match(r"^\| 20\d\d-\d\d-\d\d", line):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) >= 2:
                rows.append((cells[0], cells[1]))
    return pd.DataFrame(rows, columns=["as_of", "regime"])


def momentum_weights(
    prices: pd.DataFrame,
    as_of: pd.Timestamp,
    lookback: int = 252,
    reversal: int = 21,
    vol_window: int = 63,
    top_n: int = 2,
) -> dict[str, float]:
    end = as_of
    window = prices.loc[:end].tail(lookback + 5)
    if len(window) < lookback:
        return {}
    rets = window.pct_change().dropna()
    momentum: dict[str, float] = {}
    vols: dict[str, float] = {}
    for sym in MEGACAPS:
        if sym not in window.columns:
            continue
        series = window[sym].dropna()
        if len(series) < lookback:
            continue
        try:
            full = (series.iloc[-1] / series.iloc[-lookback]) - 1.0
            recent = (series.iloc[-1] / series.iloc[-reversal]) - 1.0
            momentum[sym] = float(full - recent)
            sym_rets = rets[sym].dropna().iloc[-vol_window:]
            if len(sym_rets) < vol_window // 2:
                continue
            vols[sym] = float(sym_rets.std()) * math.sqrt(252.0)
        except (KeyError, IndexError):
            continue
    if not momentum:
        return {}
    top = sorted(momentum.items(), key=lambda x: -x[1])[:top_n]
    inv = {s: 1.0 / max(vols.get(s, 0.5), 0.05) for s, _ in top}
    total = sum(inv.values())
    return {s: w / total for s, w in inv.items()}


def maxdd(equity: pd.Series) -> float:
    peak = equity.cummax()
    return float(((peak - equity) / peak).max())


def run_preset(
    preset_name: str, leverage_map: dict[str, float], regime_df: pd.DataFrame, prices: pd.DataFrame
) -> tuple[pd.DataFrame, dict]:
    equity = 10_000.0
    spy_eq = 10_000.0
    records: list[dict] = []

    for _, row in regime_df.iterrows():
        as_of = pd.Timestamp(row["as_of"])
        regime = row["regime"]
        leverage = leverage_map.get(regime, 1.0)

        valid = prices.index[prices.index <= as_of]
        if len(valid) == 0:
            continue
        rebal = valid[-1]

        weights = momentum_weights(prices, rebal)
        if not weights:
            continue

        forward = prices.index[prices.index > rebal][:21]
        if len(forward) < 21:
            break
        end = forward[-1]

        port_ret = 0.0
        for sym, w in weights.items():
            try:
                ret = (prices.loc[end, sym] / prices.loc[rebal, sym]) - 1.0
            except KeyError:
                continue
            port_ret += w * float(ret)
        gated_ret = port_ret * leverage
        # Cash drag when leverage<1: remainder earns 0 (conservative).
        # When leverage>1: borrowing cost not modeled (caveat).

        try:
            spy_ret = float((prices.loc[end, BENCHMARK] / prices.loc[rebal, BENCHMARK]) - 1.0)
        except KeyError:
            continue

        equity *= 1.0 + gated_ret
        spy_eq *= 1.0 + spy_ret

        records.append(
            {
                "as_of": rebal.date(),
                "regime": regime,
                "leverage": leverage,
                "holdings": ",".join(f"{s}:{w:.2f}" for s, w in weights.items()),
                "raw_port_ret": port_ret,
                "gated_port_ret": gated_ret,
                "spy_ret": spy_ret,
                "equity": equity,
                "spy_equity": spy_eq,
            }
        )

    df = pd.DataFrame(records)
    df.to_csv(OUT_DIR / f"regime-overlay-{preset_name}.csv", index=False)
    print(f"[{preset_name}] {len(df)} periods")

    months = len(df)
    years = months / 12.0
    port_ann = (df["equity"].iloc[-1] / 10_000.0) ** (1.0 / years) - 1.0
    spy_ann = (df["spy_equity"].iloc[-1] / 10_000.0) ** (1.0 / years) - 1.0

    port_sharpe = (df["gated_port_ret"].mean() / df["gated_port_ret"].std()) * math.sqrt(12.0)
    spy_sharpe = (df["spy_ret"].mean() / df["spy_ret"].std()) * math.sqrt(12.0)

    port_mdd = maxdd(df["equity"])
    spy_mdd = maxdd(df["spy_equity"])
    beat = float((df["gated_port_ret"] > df["spy_ret"]).mean()) * 100.0

    # Per-regime breakdown
    regime_stats = (
        df.groupby("regime")
        .agg(
            count=("gated_port_ret", "size"),
            avg_port=("gated_port_ret", "mean"),
            avg_spy=("spy_ret", "mean"),
            avg_lev=("leverage", "mean"),
        )
        .assign(excess=lambda x: x["avg_port"] - x["avg_spy"])
        .sort_values("excess", ascending=False)
    )

    lines: list[str] = []
    lines.append("# Regime-Gated Mega-Cap Momentum Overlay\n")
    lines.append(
        f"Period: {df['as_of'].iloc[0]} to {df['as_of'].iloc[-1]} ({months} months, {years:.2f} years)\n"
    )
    lines.append(
        "Caveats: Survivorship bias (current mega caps), no borrow cost on leverage>1, regime labels partly in-sample per copilot docs.\n"
    )
    lines.append("\n## Headline\n")
    lines.append("| Metric | Strategy | SPY |")
    lines.append("|---|---:|---:|")
    lines.append(
        f"| Final Equity ($10K) | ${df['equity'].iloc[-1]:,.0f} | ${df['spy_equity'].iloc[-1]:,.0f} |"
    )
    lines.append(
        f"| Cumulative Return | {(df['equity'].iloc[-1] / 10_000.0 - 1) * 100:+.1f}% | {(df['spy_equity'].iloc[-1] / 10_000.0 - 1) * 100:+.1f}% |"
    )
    lines.append(f"| Annualized | {port_ann * 100:+.2f}% | {spy_ann * 100:+.2f}% |")
    lines.append(f"| Sharpe | {port_sharpe:.2f} | {spy_sharpe:.2f} |")
    lines.append(f"| Max Drawdown | {port_mdd * 100:.2f}% | {spy_mdd * 100:.2f}% |")
    lines.append(f"| Months beating SPY | {beat:.1f}% |  |")

    lines.append("\n## Regime → Leverage Map\n")
    lines.append("| Regime | Leverage |")
    lines.append("|---|---:|")
    for r, lev in leverage_map.items():
        lines.append(f"| {r} | {lev:.2f}x |")

    lines.append("\n## Per-Regime Excess (sorted)\n")
    lines.append("| Regime | Count | Avg Lev | Avg Port | Avg SPY | Excess |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for reg, rstat in regime_stats.iterrows():
        lines.append(
            f"| {reg} | {int(rstat['count'])} | {rstat['avg_lev']:.2f}x | "
            f"{rstat['avg_port'] * 100:+.2f}% | {rstat['avg_spy'] * 100:+.2f}% | "
            f"{rstat['excess'] * 100:+.2f}% |"
        )

    out_text = "\n".join(lines) + "\n"
    (OUT_DIR / f"regime-overlay-{preset_name}.md").write_text(out_text)

    summary = {
        "preset": preset_name,
        "final_equity": float(df["equity"].iloc[-1]),
        "cumulative_pct": (df["equity"].iloc[-1] / 10_000.0 - 1) * 100,
        "annualized_pct": port_ann * 100,
        "sharpe": port_sharpe,
        "max_drawdown_pct": port_mdd * 100,
        "beat_pct": beat,
        "spy_final": float(df["spy_equity"].iloc[-1]),
        "spy_ann_pct": spy_ann * 100,
        "spy_sharpe": spy_sharpe,
        "spy_mdd_pct": spy_mdd * 100,
    }
    return df, summary


def main() -> None:
    regime_md = OUT_DIR / "regime-aware-backtest-2008-2026.md"
    regime_df = parse_regime_table(regime_md)
    print(f"Loaded {len(regime_df)} monthly regime labels")

    universe = MEGACAPS + [BENCHMARK, DEFENSIVE]
    print(f"Downloading {len(universe)} symbols from yfinance...")
    raw = yf.download(
        universe,
        start="2009-01-01",
        end="2026-05-25",
        auto_adjust=True,
        progress=False,
    )
    prices = raw["Close"].dropna(how="all")
    print(f"Prices: {len(prices)} bars\n")

    summaries: list[dict] = []
    for preset_name, leverage_map in REGIME_PRESETS.items():
        _, summary = run_preset(preset_name, leverage_map, regime_df, prices)
        summaries.append(summary)

    print("\n## Preset Comparison\n")
    print(
        f"SPY benchmark: ann +{summaries[0]['spy_ann_pct']:.2f}%, "
        f"Sharpe {summaries[0]['spy_sharpe']:.2f}, MDD {summaries[0]['spy_mdd_pct']:.2f}%\n"
    )
    print(f"{'Preset':<14} {'Ann':>8} {'Sharpe':>7} {'MDD':>7} {'Beat%':>7} {'Final$':>12}")
    print("-" * 60)
    for s in summaries:
        print(
            f"{s['preset']:<14} {s['annualized_pct']:>+7.2f}% {s['sharpe']:>7.2f} "
            f"{s['max_drawdown_pct']:>6.2f}% {s['beat_pct']:>6.1f}% ${s['final_equity']:>11,.0f}"
        )

    comp_lines = [
        "# Regime Overlay Preset Comparison\n",
        f"SPY: ann +{summaries[0]['spy_ann_pct']:.2f}%, Sharpe {summaries[0]['spy_sharpe']:.2f}, MDD {summaries[0]['spy_mdd_pct']:.2f}%\n",
        "| Preset | Ann Return | Sharpe | MaxDD | Beat SPY% | Final $10K |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for s in summaries:
        comp_lines.append(
            f"| {s['preset']} | {s['annualized_pct']:+.2f}% | {s['sharpe']:.2f} | "
            f"{s['max_drawdown_pct']:.2f}% | {s['beat_pct']:.1f}% | ${s['final_equity']:,.0f} |"
        )
    (OUT_DIR / "regime-overlay-comparison.md").write_text("\n".join(comp_lines) + "\n")


if __name__ == "__main__":
    main()
