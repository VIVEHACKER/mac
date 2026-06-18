"""Does the advisory rule EARN trading-signal status? (research-to-earn, not relabeling)

The advisory tool is currently risk-framing only. To "raise it to a trading signal" honestly we
must MEASURE whether the full rule — VALIDATED AQR selection + ATR entry(close-0.5ATR limit) /
stop(-2ATR) / target(+3ATR) — produces positive expectancy that BEATS simply buying-and-holding
the same AQR picks. If the ATR bands add value, the advisory earns signal status; if they don't
(get stopped out / miss runners by waiting for a pullback), it stays a risk ruler and we say so.

Method (walk-forward, no lookahead):
  - At each monthly rebalance T, AQR-rank the pinned megacap universe -> top-N picks (the only
    selection with a measured edge).
  - For each pick, fetch daily OHLCV (FinanceDataReader) and simulate the advisory trade over the
    next FWD_BARS days: a limit entry at close-0.5ATR; if a day's LOW touches it, fill; then exit
    at stop (LOW<=stop, conservative if same-day as target) or target (HIGH>=target), else at the
    window's last close. Unfilled = cash (0 return) — the real cost of waiting for a pullback.
  - Baseline = buy at close_T, hold FWD_BARS (what the AQR selection alone delivers).

Pre-declared bar: signal-grade ONLY IF advisory mean per-period return > baseline AND > 0. Else
the bands subtract value. CAVEATS: UNPINNED fdr OHLCV + survivorship (current megacaps) => optimistic.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.models import PriceBar  # noqa: E402
from data.price_snapshot import read_price_snapshot  # noqa: E402
from engine.screener import _atr  # noqa: E402
from scripts.aqr_ideal_grid import DEFAULT_PRICES, DEFAULT_SNAPSHOT  # noqa: E402
from scripts.aqr_ideal_walkforward import (  # noqa: E402
    MEGACAPS,
    build_pricebars,
    lookup_pit,
    prefetch,
)
from strategies.factor_aqr import rank_aqr_factors  # noqa: E402

FWD_BARS = 21
TOP_N = 7
ATR_WINDOW = 14
ENTRY_PULLBACK = 0.5
STOP_MULT = 2.0
TARGET_MULT = 3.0


def _fetch_ohlcv(codes: list[str], start: str, end: str) -> dict[str, list[PriceBar]]:
    import FinanceDataReader as fdr  # noqa: N813

    out: dict[str, list[PriceBar]] = {}
    for code in codes:
        try:
            df = fdr.DataReader(code, start, end)
        except Exception:
            continue
        if df is None or df.empty or "Close" not in df.columns:
            continue
        bars = [
            PriceBar(
                symbol=code,
                market="us",
                source_symbol=code,
                ts=idx.date(),
                open=float(getattr(r, "Open", r.Close)),
                high=float(getattr(r, "High", r.Close)),
                low=float(getattr(r, "Low", r.Close)),
                close=float(r.Close),
                volume=float(getattr(r, "Volume", 0.0) or 0.0),
                currency="USD",
            )
            for idx, r in df.iterrows()
            if r.Close > 0
        ]
        if len(bars) > ATR_WINDOW + 2:
            out[code] = bars
    return out


def _simulate_advisory(bars: list[PriceBar], t_index: int) -> float | None:
    """Advisory-rule return for one pick entered at bar t_index, over the next FWD_BARS.
    None if there is not enough forward history."""
    if t_index < ATR_WINDOW + 1 or t_index + FWD_BARS >= len(bars):
        return None
    atr = _atr(bars[: t_index + 1], ATR_WINDOW)
    if atr <= 0:
        return None
    close = bars[t_index].close
    entry = close - ENTRY_PULLBACK * atr
    stop = entry - STOP_MULT * atr
    target = entry + TARGET_MULT * atr
    if entry <= 0 or stop <= 0:
        return None
    window = bars[t_index + 1 : t_index + 1 + FWD_BARS]
    filled = False
    for bar in window:
        if not filled:
            if bar.low <= entry:  # limit pullback hit
                filled = True
            else:
                continue
        # once filled, check exits intrabar (conservative: stop before target if both touched)
        if bar.low <= stop:
            return (stop - entry) / entry
        if bar.high >= target:
            return (target - entry) / entry
    if filled:
        return (window[-1].close - entry) / entry
    return 0.0  # never filled = stayed in cash (cost of waiting for a pullback)


def _baseline(bars: list[PriceBar], t_index: int) -> float | None:
    if t_index + FWD_BARS >= len(bars):
        return None
    c0 = bars[t_index].close
    return bars[t_index + FWD_BARS].close / c0 - 1.0 if c0 > 0 else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Does the advisory rule earn signal status?")
    parser.add_argument("--start", default="2022-01-01")
    parser.add_argument("--end", default="2026-05-31")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "out" / "advisory-signal-validation.md"
    )
    args = parser.parse_args()

    prices = read_price_snapshot(DEFAULT_PRICES, verify=True)
    fund_cache = prefetch(None, snapshot_path=DEFAULT_SNAPSHOT)
    print(f"fetching OHLCV for {len(MEGACAPS)} megacaps {args.start}..{args.end} ...")
    ohlcv = _fetch_ohlcv(list(MEGACAPS), args.start, args.end)
    print(f"fetched {len(ohlcv)} names with OHLCV")
    ts_index = {code: {b.ts: i for i, b in enumerate(bars)} for code, bars in ohlcv.items()}

    rebal_dates = []
    cur = pd.Timestamp(args.start) + pd.offsets.MonthEnd(13)
    last = pd.Timestamp(prices.index.max())
    while cur <= last:
        valid = prices.index[prices.index <= cur]
        if len(valid):
            rebal_dates.append(valid[-1])
        cur += pd.offsets.MonthEnd(1)

    adv_period, base_period = [], []
    adv_trades, wins, fills = [], 0, 0
    months = 0
    for rebal in rebal_dates:
        as_of = datetime(rebal.year, rebal.month, rebal.day)
        bars_by, fund_by = {}, {}
        for sym in MEGACAPS:
            fund = lookup_pit(fund_cache.get(sym, []), as_of)
            if fund is None:
                continue
            pbars = build_pricebars(prices, sym, rebal)
            if pbars:
                fund_by[sym.upper()] = fund
                bars_by[sym] = pbars
        scores = rank_aqr_factors(bars_by, fund_by, lookback=126)
        picks = [s.symbol for s in scores[:TOP_N]]
        adv_rets, base_rets = [], []
        for sym in picks:
            bars = ohlcv.get(sym) or ohlcv.get(sym.upper())
            if not bars:
                continue
            rebal_date = rebal.date()
            idx = ts_index.get(sym, ts_index.get(sym.upper(), {})).get(rebal_date)
            if idx is None:  # align to nearest prior trading day in the OHLCV feed
                prior = [b.ts for b in bars if b.ts <= rebal_date]
                if not prior:
                    continue
                idx = len(prior) - 1
            adv = _simulate_advisory(bars, idx)
            base = _baseline(bars, idx)
            if adv is None or base is None:
                continue
            adv_rets.append(adv)
            base_rets.append(base)
            adv_trades.append(adv)
            if adv != 0.0:
                fills += 1
                if adv > 0:
                    wins += 1
        if len(adv_rets) >= 3:
            months += 1
            adv_period.append(sum(adv_rets) / len(adv_rets))
            base_period.append(sum(base_rets) / len(base_rets))

    def avg(xs: list[float]) -> float:
        return sum(xs) / len(xs) if xs else 0.0

    adv_mean, base_mean = avg(adv_period), avg(base_period)
    fill_rate = fills / len(adv_trades) if adv_trades else 0.0
    win_rate = wins / fills if fills else 0.0
    earns = adv_mean > base_mean and adv_mean > 0
    pct = lambda x: f"{x * 100:+.2f}%"  # noqa: E731
    verdict = (
        f"SIGNAL-GRADE (caveated) — advisory rule {pct(adv_mean)}/period beats buy-hold "
        f"{pct(base_mean)} AND is positive. The ATR bands add value on the AQR selection. Next: "
        "pin a survivorship-controlled OHLCV snapshot + slippage and re-validate before capital."
        if earns
        else f"NOT A SIGNAL — advisory rule {pct(adv_mean)}/period vs buy-hold {pct(base_mean)} "
        f"(fill rate {fill_rate:.0%}, win rate {win_rate:.0%}). The ATR entry/stop/target bands do "
        "NOT beat simply holding the AQR picks: waiting for a -0.5ATR pullback misses runners and "
        "the -2ATR stop cuts winners. Confirms the bands are RISK FRAMING, not a profit signal — "
        "advisory stays a discipline ruler, NOT raised to a trading signal."
    )
    lines = [
        "# Does the advisory rule earn trading-signal status? (first read — UNPINNED, survivorship)",
        "",
        f"AQR top-{TOP_N} selection | {months} monthly rebalances {args.start}..{args.end} | "
        f"{FWD_BARS}-bar window | entry close-{ENTRY_PULLBACK}ATR, stop -{STOP_MULT}ATR, "
        f"target +{TARGET_MULT}ATR",
        "",
        "| metric | value |",
        "|---|---:|",
        f"| advisory rule mean/period | **{pct(adv_mean)}** |",
        f"| buy-hold AQR picks mean/period | {pct(base_mean)} |",
        f"| advisory − baseline | {pct(adv_mean - base_mean)} |",
        f"| fill rate (pullback hit) | {fill_rate:.0%} |",
        f"| win rate (of filled) | {win_rate:.0%} |",
        f"| trades simulated | {len(adv_trades)} |",
        "",
        "## Verdict (pre-declared)",
        "",
        verdict,
        "",
        "## Caveats",
        "- UNPINNED fdr OHLCV + survivorship (current megacaps) => optimistic.",
        "- Daily-bar intrabar approximation (stop assumed before target on same-day touch).",
        "- Single pre-declared band config (0.5/2/3); no tuning (tuning until it passes = p-hacking).",
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
