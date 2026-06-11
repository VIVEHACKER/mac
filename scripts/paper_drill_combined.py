"""Forward-OOS paper drill for the COMBINED line (IDEAL 80% + low-vol 20%).

The combined line cleared every backtest gate available (walk-forward 3 pre-declared
bars, fee stress, breadth-family PBO 0.330 — same evidence level as the deployed IDEAL
candidate). From here the ONLY source of new evidence is forward paper time, so this
script enrols the line in its own pre-registered, append-only forward-OOS ledger:

    out/paper-oos-ledger-combined_ideal80_lowvol20_pit110.jsonl

Isolation: own strategy-id → own state file and ledger (state_path_for guards against
cross-strategy contamination). scripts/paper_drill.py and the running IDEAL cadence
cron are NOT touched.

Cadence: the gate is built in (21 trading days, same as the validated protocol) so a
daily cron can call this script directly; off-cadence runs print skip and exit 0.

Weights each rebalance (mirrors the validated blend construction):
    0.8 x IDEAL top-7 weights (AQR rank, cap 0.20 — identical to paper_drill)
  + 0.2 x low-vol top-20 equal weights (63d trailing vol rank — identical to the
          validated lowvol sleeve)

Fidelity note (recorded, not hidden): the validated blend applies the −10% trail
INSIDE the IDEAL sleeve (run_window internals); this paper drill applies the trail at
book level — the same convention the IDEAL line's own paper drill uses. The difference
activates only in drawdown and in the conservative direction (it de-risks the low-vol
sleeve too). Live prices (yfinance latest bar) are correct here: a forward ledger
records the future as it arrives; pinning is for backtests.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.paper_oos import PaperOOSEntry, append_entry  # noqa: E402
from scripts.aqr_ideal_walkforward import BENCHMARK, MEGACAPS  # noqa: E402
from scripts.paper_drill import (  # noqa: E402
    CAP,
    DEFAULT_SNAPSHOT,
    OUT_DIR,
    build_bars,
    build_fundamentals_index,
    load_state,
    pit_lookup,
    save_state,
    state_path_for,
    vol_est,
    weights_from_picks,
)
from scripts.paper_drill_cadence import is_rebalance_due  # noqa: E402
from strategies.factor_aqr import rank_aqr_factors  # noqa: E402

STRATEGY_ID = "combined_ideal80_lowvol20_pit110"
SLEEVE_IDEAL = 0.80  # pre-declared in the validated combined gate; do not search
SLEEVE_LOWVOL = 0.20
IDEAL_TOP_N = 7
LOWVOL_TOP_N = 20
VOL_WINDOW = 63
MIN_HISTORY = 126


def lowvol_weights(prices: pd.DataFrame, rebal: pd.Timestamp) -> dict[str, float]:
    """Equal weights over the LOWVOL_TOP_N lowest-trailing-vol megacaps (validated sleeve)."""
    vols: dict[str, float] = {}
    for sym in MEGACAPS:
        hist = prices[sym].loc[:rebal].dropna() if sym in prices.columns else pd.Series()
        if len(hist) < MIN_HISTORY:
            continue
        vols[sym] = vol_est(prices, sym, rebal, window=VOL_WINDOW)
    if len(vols) < LOWVOL_TOP_N:
        return {}
    calmest = sorted(vols, key=lambda s: vols[s])[:LOWVOL_TOP_N]
    return dict.fromkeys(calmest, 1.0 / LOWVOL_TOP_N)


def blend_paper_weights(ideal: dict[str, float], lowvol: dict[str, float]) -> dict[str, float]:
    """0.8/0.2 sleeve blend, merged per symbol (a name can sit in both sleeves)."""
    if not ideal or not lowvol:
        return {}
    out: dict[str, float] = {}
    for sym, w in ideal.items():
        out[sym] = out.get(sym, 0.0) + SLEEVE_IDEAL * w
    for sym, w in lowvol.items():
        out[sym] = out.get(sym, 0.0) + SLEEVE_LOWVOL * w
    return out


def _last_ledger_date(ledger_path: Path) -> date | None:
    from engine.paper_oos import load_ledger

    entries = load_ledger(ledger_path)
    if not entries:
        return None
    return max(date.fromisoformat(e.rebal_date) for e in entries)


def main() -> int:
    parser = argparse.ArgumentParser(description="Combined-line forward-OOS paper drill.")
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--allow-live-fundamentals", action="store_true")
    parser.add_argument("--capital", type=float, default=None)
    parser.add_argument("--cadence", type=int, default=21)
    parser.add_argument("--force", action="store_true", help="bypass the cadence gate")
    parser.add_argument("--today", default="", help="override today (ISO) for testing")
    args = parser.parse_args()

    ledger_path = OUT_DIR / f"paper-oos-ledger-{STRATEGY_ID}.jsonl"
    today = date.fromisoformat(args.today) if args.today else date.today()
    last = _last_ledger_date(ledger_path)
    if not args.force and not is_rebalance_due(last, today, cadence=args.cadence):
        print(f"[cadence] combined 리밸런스 미도래 — 마지막 {last}. skip.")
        return 0

    state_path = state_path_for(STRATEGY_ID)
    state = load_state(state_path, STRATEGY_ID)
    if args.capital is not None and state["last_rebal"] is None:
        state["nav"] = args.capital
        state["peak"] = args.capital
    nav = state["nav"]
    peak = max(state["peak"], nav)
    drawdown = (nav - peak) / peak
    exposure = 0.5 if drawdown < -0.10 else 1.0
    target_capital = nav * exposure
    print(
        f"State [{state_path.name}]: NAV ${nav:.2f}, DD {drawdown * 100:+.2f}% "
        f"→ exposure {exposure * 100:.0f}%"
    )

    print("Fetching prices...")
    now = pd.Timestamp.now().normalize()
    raw = yf.download(
        [*MEGACAPS, BENCHMARK],
        start="2023-01-01",
        end=now + pd.Timedelta(days=1),
        auto_adjust=True,
        progress=False,
    )
    prices = raw["Close"].dropna(how="all")
    rebal = prices.index[-1]
    print(f"Latest bar: {rebal.date()}")

    # IDEAL sleeve — identical to paper_drill's top-7 flow.
    from datetime import datetime as _dt

    fund_index, provenance = build_fundamentals_index(args.snapshot, args.allow_live_fundamentals)
    as_of_dt = _dt(rebal.year, rebal.month, rebal.day)
    bars_by_sym, fund_by_sym = {}, {}
    for sym in MEGACAPS:
        cand = pit_lookup(fund_index.get(sym.upper(), []), as_of_dt)
        if cand is None:
            continue
        bars = build_bars(prices, sym, rebal)
        if not bars:
            continue
        fund_by_sym[sym.upper()] = cand
        bars_by_sym[sym] = bars
    scores = rank_aqr_factors(bars_by_sym, fund_by_sym, lookback=126)
    ideal_w = weights_from_picks(scores[:IDEAL_TOP_N], prices, rebal, cap=CAP)

    # Low-vol sleeve — identical to the validated lowvol construction.
    lowvol_w = lowvol_weights(prices, rebal)

    weights = blend_paper_weights(ideal_w, lowvol_w)
    if not weights:
        print("FAIL-CLOSED: a sleeve produced no weights — not recording a rebalance.")
        return 2

    entry_prices: dict[str, float] = {}
    for sym in weights:
        try:
            price = float(prices.loc[rebal, sym])
        except KeyError:
            continue
        if price > 0:
            entry_prices[sym] = price
    missing = sorted(set(weights) - set(entry_prices))
    if missing:
        print(f"FAIL-CLOSED: no entry price for {missing} — not recording.")
        return 2

    state["last_rebal"] = str(rebal.date())
    state["positions"] = {
        sym: int(target_capital * w / entry_prices[sym]) for sym, w in weights.items()
    }
    save_state(state, state_path)

    entry = PaperOOSEntry(
        rebal_date=str(rebal.date()),
        strategy_id=STRATEGY_ID,
        weights=weights,
        entry_prices=entry_prices,
        benchmark_symbol=BENCHMARK,
        benchmark_price=float(prices.loc[rebal, BENCHMARK]),
    )
    try:
        append_entry(ledger_path, entry)
        print(f"Recorded combined forward-OOS entry ({len(weights)} names) -> {ledger_path.name}")
    except ValueError as exc:
        print(f"OOS ledger: {exc}")
        return 0

    print(f"Fundamentals: {provenance}")
    top = sorted(weights.items(), key=lambda kv: kv[1], reverse=True)
    print("| Symbol | Weight | Entry |")
    for sym, w in top:
        print(f"| {sym} | {w * 100:.2f}% | ${entry_prices[sym]:.2f} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
