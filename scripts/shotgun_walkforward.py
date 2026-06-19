"""Emerging Shotgun — daily-proxy backtest of the Biberion hypothesis (H1/H2/H3).

Source idea: docs/STRATEGY_NOTES.md §1 (Biberion documentary). This is a DAILY proxy
of an intraday small-cap basket strategy, so it tests the *concept-level* hypotheses,
not the literal intraday/pyramiding execution:

  H1  regime-gated small-cap basket momentum (5/25 cross + volume) + tight ATR stop
      has positive per-trade EV in clean-trend regimes.
  H2  the SAME setup has negative/zero EV in choppy (weak-trend) regimes
      -> the regime filter is where the edge lives, not the entries.
  H3  restricting to high cross-sectional co-movement improves the edge.

Honest priors / caveats (declared up front, like scripts/lowvol_megacap_walkforward.py):
  - SURVIVORSHIP BIAS: universe = CURRENT S&P 600 small-caps -> optimistic (delisted
    losers excluded). A real test needs point-in-time membership + delisting returns.
  - DAILY PROXY: intraday 5-min entry and winner-pyramiding are NOT modeled. Stops are
    filled at the stop level when the next day's Low pierces it (optimistic-to-neutral).
  - This script only produces EVIDENCE. Nothing here is wired to capital. Verdict bar
    is pre-declared; the result stands whatever it says.

Run:  .venv/bin/python scripts/shotgun_walkforward.py --prices out/shotgun/pinned.pkl
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


# --------------------------------------------------------------------------- #
# Indicators
# --------------------------------------------------------------------------- #
def sma(s: pd.DataFrame, n: int) -> pd.DataFrame:
    return s.rolling(n, min_periods=n).mean()


def atr(high: pd.DataFrame, low: pd.DataFrame, close: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    prev_close = close.shift(1)
    tr = pd.DataFrame(
        np.maximum.reduce(
            [(high - low).values, (high - prev_close).abs().values, (low - prev_close).abs().values]
        ),
        index=close.index,
        columns=close.columns,
    )
    return tr.rolling(n, min_periods=n).mean()


# --------------------------------------------------------------------------- #
# Regime + co-movement series (computed on IWM and the small-cap cross-section)
# --------------------------------------------------------------------------- #
def regime_series(iwm_close: pd.Series, iwm_vol: pd.Series) -> pd.DataFrame:
    ma5 = iwm_close.rolling(5, min_periods=5).mean()
    ma25 = iwm_close.rolling(25, min_periods=25).mean()
    vol25 = iwm_vol.rolling(25, min_periods=25).mean()
    uptrend = ma5 > ma25  # trend STATE — used as the hold/exit condition
    # entry is "right after the 5/25 golden CROSS" (event), not any uptrend day:
    cross_up = uptrend & ~uptrend.shift(1).fillna(False)
    armed = cross_up.rolling(5, min_periods=1).max().astype(bool)  # within 5d of a cross
    on = armed & uptrend & (iwm_vol > vol25)  # entry trigger = fresh cross + volume confirm
    # trend QUALITY: normalized gap |ma5-ma25|/price, and slope of ma25 over 20d.
    gap = (ma5 - ma25).abs() / iwm_close
    slope = ma25.diff(20) / ma25
    # choppiness: count of ma5/ma25 crosses in trailing 40d (more crosses = choppier)
    cross = ((ma5 > ma25).astype(int).diff().abs()).rolling(40, min_periods=40).sum()
    out = pd.DataFrame({"on": on, "hold": uptrend, "gap": gap, "slope": slope, "chop": cross})
    return out


def comovement_series(small_rets: pd.DataFrame, window: int = 20) -> pd.Series:
    """Average cross-sectional |return| dispersion inverse proxy: high co-movement =
    low dispersion of signs. Use fraction of names moving with the median sign."""
    med_sign = np.sign(small_rets.median(axis=1))
    agree = small_rets.apply(np.sign).eq(med_sign, axis=0).mean(axis=1)
    return agree.rolling(window, min_periods=window).mean()


# --------------------------------------------------------------------------- #
# Trade generation (per-name, daily proxy)
# --------------------------------------------------------------------------- #
def run_backtest(data: dict, cfg: dict) -> dict:
    close, high, low, vol = data["close"], data["high"], data["low"], data["volume"]
    names = [c for c in close.columns if c not in ("IWM", "SPY")]
    small_close = close[names]

    reg = regime_series(close["IWM"], vol["IWM"])
    rets = small_close.pct_change()
    como = comovement_series(rets)

    # per-name signals (vectorized)
    nma5 = small_close.rolling(5, min_periods=5).mean()
    nma25 = small_close.rolling(25, min_periods=25).mean()
    nvol25 = vol[names].rolling(25, min_periods=25).mean()
    natr = atr(high[names], low[names], small_close)
    mom20 = small_close.pct_change(20)

    breakout = (small_close > nma25) & (nma5 > nma25) & (vol[names] > nvol25 * cfg["vol_mult"])
    entry_arr = breakout.values & reg["on"].values[:, None]  # numpy bool, regime ON only

    dates = close.index.to_list()
    closev = small_close.values
    lowv = low[names].values
    atrv = natr.values
    mom = mom20.values
    reg_on = reg["on"].values  # entry gate (uptrend + volume)
    reg_hold = reg["hold"].values  # hold/exit gate (uptrend only)
    reg_on = reg["on"].values
    maxhold = cfg["max_hold"]
    stop_k = cfg["stop_k"]
    fee = cfg["fee_bps"] / 1e4

    open_until = dict.fromkeys(names, -1)  # index until which name is locked in a trade
    trades = []
    n_dates = len(dates)
    concurrent = np.zeros(n_dates)  # positions held per day — enforces the cap AT ENTRY
    daily_pnl = np.zeros(n_dates)  # TRUE daily mark-to-market P&L (1/max_conc per slot)
    max_conc = cfg["max_concurrent"]
    for ti in range(25, n_dates - 1):
        if not reg_on[ti]:
            continue
        # candidate names with entry signal today and not currently in a trade
        cands = []
        row = entry_arr[ti]
        for ni, n in enumerate(names):
            if (
                row[ni]
                and ti > open_until[n]
                and np.isfinite(mom[ti, ni])
                and np.isfinite(atrv[ti, ni])
            ):
                cands.append((mom[ti, ni], ni, n))
        if not cands:
            continue
        cands.sort(reverse=True)
        ei = ti + 1  # NEXT-BAR fill: signal known at ti close, executed at ti+1 close
        if ei >= n_dates - 1:
            continue
        for _, ni, n in cands[: cfg["basket_n"]]:
            entry = closev[ei, ni]
            a = atrv[ei, ni]
            if not (entry > 0 and a > 0):
                continue
            stop = entry - stop_k * a
            exit_px, exit_i, reason = None, None, None
            for tj in range(ei + 1, min(ei + 1 + maxhold, n_dates)):
                if lowv[tj, ni] <= stop:
                    exit_px, exit_i, reason = stop, tj, "stop"
                    break
                if not reg_hold[tj]:
                    exit_px, exit_i, reason = closev[tj, ni], tj, "regime_off"
                    break
            if exit_px is None:
                exit_i = min(ei + maxhold, n_dates - 1)
                exit_px, reason = closev[exit_i, ni], "maxhold"
            # Concurrency cap enforced AT ENTRY: only open if a slot is free for the
            # whole holding window (else this signal is missed — no impossible late fill).
            if exit_i > ei and concurrent[ei + 1 : exit_i + 1].max() >= max_conc:
                continue
            concurrent[ei + 1 : exit_i + 1] += 1
            # TRUE daily MTM (no lookahead): close-to-close each held day, exit day at
            # exit_px (stop level / regime-off close). Fee charged on entry & exit days.
            prev = entry
            for tj in range(ei + 1, exit_i + 1):
                px = exit_px if tj == exit_i else closev[tj, ni]
                day_ret = px / prev - 1.0
                if tj == ei + 1:
                    day_ret -= fee  # entry-day cost
                if tj == exit_i:
                    day_ret -= fee  # exit-day cost
                daily_pnl[tj] += day_ret / max_conc  # fixed-slot capital, cash otherwise
                prev = closev[tj, ni]
            ret = (exit_px / entry - 1.0) - 2 * fee
            trades.append(
                {
                    "name": n,
                    "entry_i": ei,  # actual fill bar (next bar after signal ti)
                    "exit_i": exit_i,
                    "ret": ret,
                    "reason": reason,
                    "gap": reg["gap"].values[ti],  # regime metrics from signal day ti
                    "slope": reg["slope"].values[ti],
                    "chop": reg["chop"].values[ti],
                    "como": como.values[ti],
                    "hold": exit_i - ei,
                }
            )
            open_until[n] = exit_i
    return {
        "trades": pd.DataFrame(trades),
        "reg": reg,
        "como": como,
        "dates": dates,
        "close": close,
        "daily_pnl": pd.Series(daily_pnl, index=pd.DatetimeIndex(dates)),
    }


def perf(daily: pd.Series) -> dict:
    d = daily.dropna()
    if d.std() == 0 or len(d) < 60:
        return {"cagr": float("nan"), "sharpe": float("nan"), "maxdd": float("nan"), "n": len(d)}
    eq = (1 + d).cumprod()
    eq = pd.concat([pd.Series([1.0]), eq])  # include starting capital so initial DD counts
    yrs = len(d) / 252
    cagr = eq.iloc[-1] ** (1 / yrs) - 1
    sharpe = d.mean() / d.std() * np.sqrt(252)
    maxdd = (eq / eq.cummax() - 1).min()
    return {"cagr": cagr, "sharpe": sharpe, "maxdd": maxdd, "n": len(d)}


def bucket_stats(tr: pd.DataFrame, col: str, label: str) -> str:
    med = tr[col].median()
    hi = tr[tr[col] >= med]
    lo = tr[tr[col] < med]

    def line(name, g):
        if len(g) == 0:
            return f"  {name:12s} n=0"
        return (
            f"  {name:12s} n={len(g):5d}  EV={g['ret'].mean() * 100:+.2f}%  "
            f"win={(g['ret'] > 0).mean() * 100:4.1f}%  med={g['ret'].median() * 100:+.2f}%"
        )

    return f"{label} (split @ median {med:.4g}):\n{line('HIGH', hi)}\n{line('LOW', lo)}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prices", type=Path, default=ROOT / "out/shotgun/pinned.pkl")
    ap.add_argument("--stop-k", type=float, default=1.0)
    ap.add_argument("--vol-mult", type=float, default=1.5)
    ap.add_argument("--basket-n", type=int, default=10)
    ap.add_argument("--max-hold", type=int, default=20)
    ap.add_argument("--max-concurrent", type=int, default=20)
    ap.add_argument("--fee-bps", type=float, default=5.0)
    ap.add_argument("--output", type=Path, default=ROOT / "out/shotgun/shotgun-walkforward.md")
    args = ap.parse_args()

    import pickle

    with open(args.prices, "rb") as fh:
        data = pickle.load(fh)
    cfg = {
        "stop_k": args.stop_k,
        "vol_mult": args.vol_mult,
        "basket_n": args.basket_n,
        "max_hold": args.max_hold,
        "fee_bps": args.fee_bps,
        "max_concurrent": args.max_concurrent,
    }

    res = run_backtest(data, cfg)
    tr = res["trades"]
    if len(tr) == 0:
        print("NO TRADES — check data/signals")
        return 1

    daily = res["daily_pnl"]  # true daily mark-to-market (built in run_backtest)
    p = perf(daily)
    # benchmarks
    spy = data["close"]["SPY"].pct_change()
    iwm = data["close"]["IWM"].pct_change()
    spy_p, iwm_p = perf(spy), perf(iwm)
    # bias-matched control: daily-rebalanced equal-weight of the available current
    # small-cap names (late-listed names enter as they list — NOT a static buy&hold,
    # but it shares the IDENTICAL survivorship bias, so it is the right decisive control).
    # It carries the IDENTICAL survivorship bias, so beating IT (not IWM) is the only way
    # to claim the regime-timing adds value beyond just owning survivor small-caps.
    _names = [c for c in data["close"].columns if c not in ("IWM", "SPY")]
    ewsc = data["close"][_names].pct_change().mean(axis=1)
    ewsc_p = perf(ewsc)

    # H1/H2: trend quality buckets (slope, gap) and choppiness (inverse)
    h_slope = bucket_stats(tr, "slope", "H1/H2 by IWM 20d slope")
    h_chop = bucket_stats(tr, "chop", "H2 by choppiness (40d cross count)")
    h_como = bucket_stats(tr, "como", "H3 by cross-sectional co-movement")

    overall_ev = tr["ret"].mean()
    win = (tr["ret"] > 0).mean()
    # asymmetry: avg win vs avg loss
    avg_w = tr.loc[tr.ret > 0, "ret"].mean()
    avg_l = tr.loc[tr.ret <= 0, "ret"].mean()
    reason_mix = tr["reason"].value_counts(normalize=True).mul(100).round(1).to_dict()

    # high-trend (slope>=median) EV for verdict
    med_slope = tr["slope"].median()
    trend_ev = tr.loc[tr.slope >= med_slope, "ret"].mean()
    chop_ev = tr.loc[tr.chop >= tr.chop.median(), "ret"].mean()

    h1_pass = trend_ev > 0
    h2_pass = chop_ev < trend_ev  # choppy worse than trend (documentary's core claim)
    sharpe_pass = not np.isnan(p["sharpe"]) and p["sharpe"] > iwm_p["sharpe"]
    # The decisive control: does timing beat the bias-matched EW small-cap buy&hold?
    beats_biasmatched = not np.isnan(p["sharpe"]) and p["sharpe"] > ewsc_p["sharpe"]
    # A long-only small-cap momentum basket on CURRENT constituents that posts a
    # Sharpe this high is a survivorship-bias RED FLAG, not a win.
    survivorship_flag = (not np.isnan(p["sharpe"])) and p["sharpe"] > 1.5

    shape = (
        f"Shotgun SHAPE reproduces: win {win * 100:.0f}% but EV {overall_ev * 100:+.2f}%/trade "
        f"on a {abs(avg_w / avg_l):.2f}x payoff (few winners carry it). "
    )
    h_line = (
        f"H1(trend EV>0)={'PASS' if h1_pass else 'FAIL'}, "
        f"H2(regime-dependence)={'PASS' if h2_pass else 'FAIL'}, "
        f"H3(co-movement)=directional {'+' if tr.loc[tr.como >= tr.como.median(), 'ret'].mean() > tr.loc[tr.como < tr.como.median(), 'ret'].mean() else '-'}. "
    )
    if survivorship_flag:
        verdict = (
            "INCONCLUSIVE — DO NOT TRUST THE HEADLINE NUMBER. "
            + shape
            + f"Portfolio Sharpe {p['sharpe']:.2f} beats IWM/SPY, but this is almost certainly "
            "**survivorship-bias inflation**: the universe is CURRENT S&P 600 small-caps, so the "
            "delisted/bankrupt losers that a real long-only basket would have bought are excluded. "
            + h_line
            + (
                "H2 FAILED — choppy regimes did NOT underperform clean trends, which is itself a "
                "symptom of the bias (when winners are pre-selected, the documentary's "
                "regime-dependence washes out). "
                if not h2_pass
                else ""
            )
            + (
                f"Against the bias-matched EW small-cap buy&hold (same survivorship), the timing "
                f"{'DOES' if beats_biasmatched else 'does NOT'} add value "
                f"(Sharpe {p['sharpe']:.2f} vs {ewsc_p['sharpe']:.2f}). "
            )
            + "VERDICT: not a validated edge. Required to make this meaningful: point-in-time "
            "small-cap membership + delisting returns, then PBO/DSR, then a faithful "
            "intraday/pyramiding model. NOT wired to capital."
        )
    elif h1_pass and h2_pass and sharpe_pass and beats_biasmatched:
        verdict = (
            "REGIME-DEPENDENT EDGE PLAUSIBLE (daily proxy, plausible Sharpe) — "
            + shape
            + h_line
            + "NEXT: PBO/DSR + PIT universe + faithful intraday model before any capital."
        )
    else:
        verdict = (
            "REJECTED/FRAGILE (daily proxy) — "
            + shape
            + h_line
            + "Does not clear the bar as a standalone edge on this proxy. Not wired to capital."
        )

    lines = [
        "# Emerging Shotgun — daily-proxy FULL-SAMPLE concept screen (NOT walk-forward OOS)",
        "",
        "> NOTE: this is a single in-sample full-period run — NO rolling/held-out windows "
        "(unlike the windowed walk-forward scripts in this repo). An in-sample REJECT is "
        "conclusive (OOS can only make it worse); an in-sample PASS would still require a "
        "real walk-forward before any weight.",
        "",
        f"Pinned prices: {args.prices.name}. Universe: current S&P 600 small-caps "
        f"(**survivorship-biased**) + IWM regime gate + SPY benchmark.",
        f"Config: stop_k={args.stop_k} ATR, vol_mult={args.vol_mult}, basket_n={args.basket_n}, "
        f"max_hold={args.max_hold}d, max_concurrent={args.max_concurrent}, fee={args.fee_bps}bps.",
        "",
        "## Caveats (read first)",
        "- SURVIVORSHIP BIAS (current constituents) → optimistic. PIT membership needed.",
        "- DELISTING-GAP BLIND SPOT: tight-stop strategies are specifically vulnerable to "
        "overnight delisting gap-downs that blow THROUGH the stop to near-zero. This universe "
        "has zero delistings, so the shotgun's true tail loss is understated — even vs the "
        "bias-matched EW control (which is also delisting-free). This is the single biggest "
        "reason the bias-matched outperformance is encouraging-but-not-decisive.",
        "- DAILY PROXY: no intraday 5-min entry, no winner pyramiding. Stops filled at stop "
        "when next-day Low pierces. Treat as a CONCEPT screen, not a deployment test.",
        "",
        "## Trades",
        f"- total trades: {len(tr)}  | exit mix: {reason_mix}",
        f"- overall per-trade EV: {overall_ev * 100:+.3f}%  | win rate: {win * 100:.1f}%",
        f"- asymmetry: avg win {avg_w * 100:+.2f}%  vs  avg loss {avg_l * 100:+.2f}%  "
        f"(payoff {abs(avg_w / avg_l):.2f}x)"
        if avg_l
        else "",
        f"- avg hold: {tr['hold'].mean():.1f} days",
        "",
        "## H1 / H2 — regime dependence (the core claim)",
        "```",
        h_slope,
        "",
        h_chop,
        "```",
        f"trend-regime EV (slope≥median): {trend_ev * 100:+.3f}%  | "
        f"choppy EV (chop≥median): {chop_ev * 100:+.3f}%",
        f"H1 (trend EV>0): {'PASS' if h1_pass else 'FAIL'}  | "
        f"H2 (choppy<trend): {'PASS' if h2_pass else 'FAIL'}",
        "",
        "## H3 — cross-sectional co-movement",
        "```",
        h_como,
        "```",
        "",
        "## Portfolio vs benchmarks (net of fees)",
        f"- Shotgun : CAGR {p['cagr'] * 100:+.2f}%  Sharpe {p['sharpe']:.2f}  maxDD {p['maxdd'] * 100:.1f}%  (n={p['n']}d)",
        f"- IWM B&H : CAGR {iwm_p['cagr'] * 100:+.2f}%  Sharpe {iwm_p['sharpe']:.2f}  maxDD {iwm_p['maxdd'] * 100:.1f}%",
        f"- SPY B&H : CAGR {spy_p['cagr'] * 100:+.2f}%  Sharpe {spy_p['sharpe']:.2f}  maxDD {spy_p['maxdd'] * 100:.1f}%",
        f"- **EW small-cap (daily-rebal, available names, bias-matched control)** : CAGR {ewsc_p['cagr'] * 100:+.2f}%  "
        f"Sharpe {ewsc_p['sharpe']:.2f}  maxDD {ewsc_p['maxdd'] * 100:.1f}%",
        f"- Sharpe beats IWM: {'YES' if sharpe_pass else 'NO'}  | "
        f"beats bias-matched EW small-cap: {'YES' if beats_biasmatched else 'NO'} "
        f"← the only survivorship-controlled comparison",
        "",
        "## VERDICT (pre-declared bar)",
        verdict,
    ]
    out = "\n".join(x for x in lines if x is not None)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(out + "\n")
    print(out)
    print(f"\n[written] {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
