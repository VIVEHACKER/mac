"""Paper-drill rebalance generator for IDEAL strategy aqr_top7_cap20_trail10.

Workflow:
1. Compute today's AQR composite rankings on 50-stock universe (PIT fundamentals).
2. Pick top-7 with per-symbol 20% cap, inverse-vol weighting.
3. Apply portfolio trailing stop (read prior NAV from state file if present).
4. Generate order intents sized to LIVE_MAX_CAPITAL.
5. Print as live-submit / live-dry-run command list.

User then either:
  A. Runs dry-run commands (no broker interaction) for mechanical verification.
  B. Runs live-submit commands once Alpaca paper keys are populated in .env.

State file: out/paper-drill-state.json (NAV peak, last rebal date).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.catalog import MarketDataCatalog  # noqa: E402
from data.fundamentals_snapshot import read_fundamentals_snapshot  # noqa: E402
from data.models import PriceBar  # noqa: E402
from scripts.aqr_ideal_walkforward import MEGACAPS  # noqa: E402  (validated 106-name universe)
from strategies.factor_aqr import rank_aqr_factors  # noqa: E402

STATE_PATH = ROOT / "out" / "paper-drill-state.json"
ORDERS_PATH = ROOT / "out" / "paper-drill-orders.md"
DEFAULT_SNAPSHOT = ROOT / "data" / "snapshots" / "fundamentals-2026-05-29.csv"

TOP_N = 7
CAP = 0.20
STRATEGY_ID = "aqr_top7_cap20_trail10"
DEFAULT_CAPITAL = 10_000.0


def build_bars(prices, symbol, end, lookback=260):
    if symbol not in prices.columns:
        return []
    s = prices[symbol].loc[:end].dropna().tail(lookback)
    if len(s) < lookback:
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


def vol_est(prices, symbol, end, window=63):
    if symbol not in prices.columns:
        return 0.30
    r = prices[symbol].loc[:end].pct_change().dropna().tail(window)
    if len(r) < window // 2:
        return 0.30
    return max(float(r.std()) * math.sqrt(252.0), 0.05)


def weights_from_picks(picks, prices, rebal, cap=0.20):
    raw = {p.symbol: 1.0 / vol_est(prices, p.symbol, rebal) for p in picks}
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


def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"nav": DEFAULT_CAPITAL, "peak": DEFAULT_CAPITAL, "last_rebal": None, "positions": {}}


def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, default=str))


def build_fundamentals_index(snapshot_path: Path | None, allow_live: bool) -> tuple[dict, str]:
    """Return ({SYMBOL: [records sorted by asof]}, provenance).

    Fails closed: requires a content-verified snapshot for reproducibility. The
    live-catalog path (NOT reproducible — the failure mode that broke Variant N)
    is only taken with an explicit ``allow_live`` opt-in.
    """
    if snapshot_path is not None and snapshot_path.exists():
        records = read_fundamentals_snapshot(snapshot_path, verify=True)
        index: dict[str, list] = defaultdict(list)
        for r in records:
            index[r.symbol.upper()].append(r)
        for recs in index.values():
            recs.sort(key=lambda r: r.asof_ts)
        return dict(index), f"snapshot:{snapshot_path.name}"

    if not allow_live:
        raise SystemExit(
            f"FAIL-CLOSED: fundamentals snapshot not found at {snapshot_path}.\n"
            "Create one with `python scripts/snapshot_fundamentals.py <name>`, or "
            "pass --allow-live-fundamentals to deliberately use the (non-reproducible) "
            "live catalog. Trading on un-pinned fundamentals is what broke Variant N."
        )

    print("⚠️  --allow-live-fundamentals: using LIVE catalog (NOT reproducible).")
    catalog = MarketDataCatalog()
    index = {}
    for sym in MEGACAPS:
        index[sym.upper()] = sorted(
            catalog.get_fundamentals(symbol=sym, market="us", as_of=None, limit=500),
            key=lambda r: r.asof_ts,
        )
    return index, "LIVE-CATALOG (NOT reproducible)"


def pit_lookup(records: list, as_of_dt: datetime):
    """Most recent record with asof_ts <= as_of_dt (point-in-time)."""
    cand = None
    for r in records:
        if r.asof_ts <= as_of_dt:
            cand = r
        else:
            break
    return cand


def main():
    parser = argparse.ArgumentParser(description="IDEAL paper-drill rebalance generator.")
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=DEFAULT_SNAPSHOT,
        help="Pinned fundamentals snapshot CSV (reproducible). Pass a missing path "
        "to force the live-catalog fallback.",
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=None,
        help="Override starting NAV when no state file exists.",
    )
    parser.add_argument(
        "--allow-live-fundamentals",
        action="store_true",
        help="Opt in to the non-reproducible live catalog when no snapshot exists "
        "(fail-closed by default).",
    )
    args = parser.parse_args()

    state = load_state()
    if args.capital is not None and state["last_rebal"] is None:
        state["nav"] = args.capital
        state["peak"] = args.capital
    print(
        f"State loaded: NAV ${state['nav']:.2f}, peak ${state['peak']:.2f}, "
        f"last rebal {state['last_rebal']}"
    )

    nav = state["nav"]
    peak = max(state["peak"], nav)
    drawdown = (nav - peak) / peak
    exposure = 0.5 if drawdown < -0.10 else 1.0
    target_capital = nav * exposure
    print(
        f"DD {drawdown * 100:+.2f}% → exposure {exposure * 100:.0f}% → "
        f"target capital ${target_capital:.2f}"
    )

    print("\nFetching prices...")
    today = pd.Timestamp.now().normalize()
    raw = yf.download(
        MEGACAPS,
        start="2023-01-01",
        end=today + pd.Timedelta(days=1),
        auto_adjust=True,
        progress=False,
    )
    prices = raw["Close"].dropna(how="all")
    rebal = prices.index[-1]
    as_of_dt = datetime(rebal.year, rebal.month, rebal.day)
    print(f"Latest bar: {rebal.date()}")

    print("Pulling PIT fundamentals...")
    fund_index, provenance = build_fundamentals_index(args.snapshot, args.allow_live_fundamentals)
    print(f"Fundamentals source: {provenance}  ({len(MEGACAPS)}-name universe)")
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

    print(f"Universe with data: {len(bars_by_sym)}")
    scores = rank_aqr_factors(bars_by_sym, fund_by_sym, lookback=126)
    picks = scores[:TOP_N]
    weights = weights_from_picks(picks, prices, rebal, cap=CAP)

    # Build order list
    orders = []
    for sym, w in weights.items():
        dollar = target_capital * w
        try:
            price = float(prices.loc[rebal, sym])
        except KeyError:
            continue
        qty = int(dollar / price)
        if qty < 1:
            continue
        orders.append(
            {
                "symbol": sym,
                "side": "buy",
                "qty": qty,
                "price": price,
                "weight": w,
                "dollar": qty * price,
            }
        )

    # Update state
    state["last_rebal"] = str(rebal.date())
    state["positions"] = {o["symbol"]: o["qty"] for o in orders}
    save_state(state)

    # Output as command list
    lines = [
        f"# Paper Drill — {rebal.date()} — {STRATEGY_ID}",
        "",
        f"Fundamentals: {provenance}  |  Universe: {len(MEGACAPS)} names  |  "
        f"Eligible: {len(bars_by_sym)}",
        f"NAV: ${nav:,.2f}  |  Peak: ${peak:,.2f}  |  DD: {drawdown * 100:+.2f}%",
        f"Exposure: {exposure * 100:.0f}%  |  Target deployed: ${target_capital:,.2f}",
        "",
        "## AQR Top-7 picks",
        "",
        "| Symbol | Composite | Weight | Price | Qty | $ Value |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    pick_map = {p.symbol: p for p in picks}
    for o in orders:
        s = pick_map.get(o["symbol"])
        comp = s.composite if s else 0.0
        lines.append(
            f"| {o['symbol']} | {comp:.2f} | {o['weight'] * 100:.1f}% | "
            f"${o['price']:.2f} | {o['qty']} | ${o['dollar']:,.2f} |"
        )

    lines += [
        "",
        "## Dry-run commands (no broker interaction)",
        "",
        "```bash",
    ]
    rebal_key = f"{STRATEGY_ID}-{rebal.date()}"
    for o in orders:
        lines.append(
            f".venv/bin/trader live-dry-run {o['symbol']} --side buy "
            f"--qty {o['qty']} --price {o['price']:.2f} "
            f"--strategy {STRATEGY_ID} --rebalance-key {rebal_key} "
            f"--equity {nav:.2f} --cash {target_capital:.2f}"
        )
    lines += [
        "```",
        "",
        "## Live-submit commands (once Alpaca paper keys set)",
        "",
        "Prerequisites:",
        "- `.env` has real ALPACA_API_KEY / ALPACA_SECRET_KEY (paper)",
        "- `export LIVE_TRADING_ENABLED=true LIVE_TRADING_ACK_RISK=true \\",
        f"    LIVE_STRATEGY_ID={STRATEGY_ID} LIVE_BROKER=alpaca-paper \\",
        f"    LIVE_MAX_CAPITAL={target_capital:.0f} LIVE_POLICY_VERSION=1`",
        "- `.venv/bin/trader live-readiness` → Ready=yes",
        "",
        "```bash",
    ]
    for o in orders:
        lines.append(
            f".venv/bin/trader live-submit {o['symbol']} --side buy "
            f"--qty {o['qty']} --price {o['price']:.2f} "
            f"--strategy {STRATEGY_ID} --rebalance-key {rebal_key}"
        )
    lines += [
        "```",
        "",
        f"Next rebalance: 21 trading days from {rebal.date()} "
        f"(~{(rebal + pd.Timedelta(days=30)).date()})",
    ]

    text = "\n".join(lines) + "\n"
    ORDERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    ORDERS_PATH.write_text(text)
    print("\n" + text)
    print(f"\nWrote {ORDERS_PATH}")


if __name__ == "__main__":
    main()
