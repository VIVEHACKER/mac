"""Paper-drill rebalance generator for the IDEAL line (default: conc5 candidate
aqr_top5_cap20_trail10_pit110; pass --top-n 7 --strategy-id aqr_top7_cap20_trail10_pit110
for the validated baseline).

Workflow:
1. Compute today's AQR composite rankings on the validated 106-name universe (PIT fundamentals).
2. Pick top-N (default 5) with per-symbol 20% cap, inverse-vol weighting.
3. Apply portfolio trailing stop (read prior NAV from state file if present).
4. Generate order intents sized to LIVE_MAX_CAPITAL.
5. Print as live-submit / live-dry-run command list.

User then either:
  A. Runs dry-run commands (no broker interaction) for mechanical verification.
  B. Runs live-submit commands once Alpaca paper keys are populated in .env.

State file: out/paper-drill-state-<strategy_id>.json (per-strategy NAV/peak/positions track).

The default fundamentals snapshot CSV is gitignored; only its .manifest.json is committed, as the
content hash pin. The CSV is a PRESERVED ARTIFACT, not a regenerable one: restore it from your own
backup, after which the loader's verify=True confirms it matches the committed manifest. Do NOT
re-pin it under the same name to "recreate" it — `snapshot_fundamentals.py <name>` rewrites BOTH
the CSV and its manifest from the (possibly drifted) live catalog, which would overwrite the
committed pin and let verify=True bless drifted data (the exact Variant-N trap). To trade on fresh
data, pin a NEW name, re-run the walk-forward, and re-register. Running without the synced snapshot
fails closed by design.
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
from engine.paper_oos import PaperOOSEntry, append_entry  # noqa: E402
from scripts.aqr_ideal_walkforward import (  # noqa: E402  (validated 106-name universe)
    BENCHMARK,
    MEGACAPS,
)
from strategies.factor_aqr import rank_aqr_factors  # noqa: E402

OUT_DIR = ROOT / "out"
LEGACY_STATE_PATH = OUT_DIR / "paper-drill-state.json"  # pre-conc5 single-strategy state (top7)
DEFAULT_SNAPSHOT = ROOT / "data" / "snapshots" / "fundamentals-2026-06-01-gp2.csv"

# Defaults = the conc5 candidate (top5), which cleared both P0 gates (cost-stress + robustness)
# and is registered model-gate APPROVED as aqr_top5_cap20_trail10_pit110 — this is the config
# being PAPER-tested. Pass --top-n 7 --strategy-id aqr_top7_cap20_trail10_pit110 to run the
# validated baseline instead. (Neither is LIVE; live promotion is a separate operator gate.)
TOP_N = 5
CAP = 0.20
# top_n -> model-gate strategy_id. Guarantees the order/label/rebalance-key/LIVE_STRATEGY_ID id
# stays consistent with --top-n: a top7 run must NOT inherit the top5 default id (which would
# attach orders to the wrong model-gate record). For any top_n not in this map, --strategy-id
# must be passed explicitly.
KNOWN_STRATEGY_IDS = {5: "aqr_top5_cap20_trail10_pit110", 7: "aqr_top7_cap20_trail10_pit110"}
STRATEGY_ID = KNOWN_STRATEGY_IDS[TOP_N]
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
    n = len(picks)
    if n == 0:
        return {}
    total_cap = n * cap
    # Infeasible: n names each <= cap cannot be fully invested when n*cap < 1 (e.g. --top-n 4 with
    # cap 0.20 maxes out at 0.80). Reject rather than silently emit equal weights above the cap
    # (Codex P2) — the supported top5/top7 configs are feasible; a custom top_n must raise the cap.
    if total_cap < 1.0 - 1e-6:
        raise ValueError(
            f"infeasible cap: {n} names x cap {cap:.4f} = {total_cap:.4f} < 1.0; cannot fully "
            f"invest within the per-symbol cap (raise cap to >= {1.0 / n:.4f} or hold fewer names)."
        )
    # When the cap binds for EVERY name (n*cap ≈ 1, e.g. the top5 default: 5 * 0.20 == 1.0) there is
    # no slack to redistribute and the iterative solver below can leave a weight a hair above `cap`
    # after 10 rounds. Equal weight is the unique feasible split (== cap when n*cap == 1), so emit it
    # exactly and never breach the advertised per-symbol cap (Codex P2). 1e-6 tol catches the
    # walk-forward's cap = 1/n + 1e-9 too, keeping validation and order generation consistent.
    if total_cap <= 1.0 + 1e-6:
        return {p.symbol: 1.0 / n for p in picks}
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


def state_path_for(strategy_id: str) -> Path:
    """Per-strategy state file. Paper-trading top5 and top7 must NOT share one NAV/peak/positions
    track — otherwise one strategy's drawdown drives the other's exposure and order sizing, and
    --capital is ignored once any strategy has rebalanced (Codex P2)."""
    return OUT_DIR / f"paper-drill-state-{strategy_id}.json"


def orders_path_for(strategy_id: str) -> Path:
    """Per-strategy order report. Running top5 then top7 (as the ops procedure instructs) must not
    overwrite one strategy's audit artifact with the other's (Codex P2)."""
    return OUT_DIR / f"paper-drill-orders-{strategy_id}.md"


def load_state(path: Path, strategy_id: str):
    # Carry the pre-conc5 single-strategy state forward to the top7 BASELINE so its ongoing paper
    # NAV/peak/positions aren't reset to DEFAULT_CAPITAL on first namespaced run (Codex P2). The
    # legacy file predates conc5, so it can only belong to the then-sole strategy (top7).
    if (
        not path.exists()
        and strategy_id == KNOWN_STRATEGY_IDS.get(7)
        and LEGACY_STATE_PATH.exists()
    ):
        state = json.loads(LEGACY_STATE_PATH.read_text())
        state["strategy_id"] = strategy_id
        print(f"Migrated legacy {LEGACY_STATE_PATH.name} -> {path.name} (baseline paper track).")
        return state
    if path.exists():
        state = json.loads(path.read_text())
        saved = state.get("strategy_id")
        if saved is not None and saved != strategy_id:
            raise SystemExit(
                f"state file {path.name} is tagged strategy {saved!r}, not {strategy_id!r}; "
                "refusing to size orders from another strategy's NAV/positions."
            )
        state["strategy_id"] = strategy_id
        return state
    return {
        "nav": DEFAULT_CAPITAL,
        "peak": DEFAULT_CAPITAL,
        "last_rebal": None,
        "positions": {},
        "strategy_id": strategy_id,
    }


def save_state(state, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, default=str))


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
    parser.add_argument(
        "--top-n",
        type=int,
        default=TOP_N,
        help="names to hold (default 5 = conc5 candidate; 7 = baseline)",
    )
    parser.add_argument(
        "--strategy-id",
        default=None,
        help="strategy-id tag for the rebalance key / live-submit lines. If omitted it is "
        "derived from --top-n (5->top5, 7->top7); required explicitly for any other --top-n.",
    )
    parser.add_argument(
        "--no-record-oos",
        dest="record_oos",
        action="store_false",
        help="skip appending this rebalance to the pre-registered forward-OOS ledger "
        "(recording is on by default — it is how paper trading becomes evidence).",
    )
    args = parser.parse_args()
    top_n = args.top_n
    if top_n < 1:
        raise SystemExit(f"--top-n must be >= 1 (got {top_n}).")
    # Derive the id from top_n unless explicitly given, so --top-n and the strategy id can never
    # disagree (Codex P2: --top-n 7 without --strategy-id must not mislabel a top7 book as top5).
    expected_id = KNOWN_STRATEGY_IDS.get(top_n)
    strategy_id = args.strategy_id or expected_id
    if strategy_id is None:
        raise SystemExit(
            f"--strategy-id is required for --top-n {top_n}: no known model-gate id "
            f"(known top_n: {sorted(KNOWN_STRATEGY_IDS)}). Pass --strategy-id so the rebalance "
            "key / LIVE_STRATEGY_ID / order labels match the registered strategy."
        )
    # An explicit id for a KNOWN top_n must equal the registered id — otherwise --top-n 7 with the
    # top5 id would generate top7 orders/keys tagged as top5, attaching them to the wrong model-gate
    # record (Codex P2). For an experimental config, add it to KNOWN_STRATEGY_IDS instead.
    if args.strategy_id and expected_id and args.strategy_id != expected_id:
        raise SystemExit(
            f"--strategy-id {args.strategy_id!r} conflicts with --top-n {top_n}: the registered "
            f"id is {expected_id!r}. Omit --strategy-id to derive it, or use the matching top_n — "
            "the id must label the correct model-gate record."
        )

    state_path = state_path_for(strategy_id)
    state = load_state(state_path, strategy_id)
    if args.capital is not None and state["last_rebal"] is None:
        state["nav"] = args.capital
        state["peak"] = args.capital
    print(
        f"State [{state_path.name}]: NAV ${state['nav']:.2f}, peak ${state['peak']:.2f}, "
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
        [*MEGACAPS, BENCHMARK],  # benchmark fetched too, for the forward-OOS ledger
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
    picks = scores[:top_n]
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
    save_state(state, state_path)

    # Pre-register this rebalance in the append-only forward-OOS ledger (the record that
    # turns paper trading into evidence). Idempotent: re-running the same date is refused.
    if args.record_oos:
        ledger_path = OUT_DIR / f"paper-oos-ledger-{strategy_id}.jsonl"
        try:
            bench_price = float(prices.loc[rebal, BENCHMARK])
        except KeyError:
            bench_price = 0.0
        entry_prices = {
            o["symbol"]: float(o["price"]) for o in orders if o["price"] and o["price"] > 0
        }
        entry = PaperOOSEntry(
            rebal_date=str(rebal.date()),
            strategy_id=strategy_id,
            weights={o["symbol"]: o["weight"] for o in orders},
            entry_prices=entry_prices,
            benchmark_symbol=BENCHMARK,
            benchmark_price=bench_price,
        )
        try:
            append_entry(ledger_path, entry)
            print(f"Recorded forward-OOS entry -> {ledger_path.name}")
        except ValueError as exc:
            print(f"OOS ledger: {exc}")

    # Output as command list
    lines = [
        f"# Paper Drill — {rebal.date()} — {strategy_id}",
        "",
        f"Fundamentals: {provenance}  |  Universe: {len(MEGACAPS)} names  |  "
        f"Eligible: {len(bars_by_sym)}",
        f"NAV: ${nav:,.2f}  |  Peak: ${peak:,.2f}  |  DD: {drawdown * 100:+.2f}%",
        f"Exposure: {exposure * 100:.0f}%  |  Target deployed: ${target_capital:,.2f}",
        "",
        f"## AQR Top-{top_n} picks",
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
    rebal_key = f"{strategy_id}-{rebal.date()}"
    for o in orders:
        lines.append(
            f".venv/bin/trader live-dry-run {o['symbol']} --side buy "
            f"--qty {o['qty']} --price {o['price']:.2f} "
            f"--strategy {strategy_id} --rebalance-key {rebal_key} "
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
        f"    LIVE_STRATEGY_ID={strategy_id} LIVE_BROKER=alpaca-paper \\",
        f"    LIVE_MAX_CAPITAL={target_capital:.0f} LIVE_POLICY_VERSION=1`",
        "- `.venv/bin/trader live-readiness` → Ready=yes",
        "",
        "```bash",
    ]
    for o in orders:
        lines.append(
            f".venv/bin/trader live-submit {o['symbol']} --side buy "
            f"--qty {o['qty']} --price {o['price']:.2f} "
            f"--strategy {strategy_id} --rebalance-key {rebal_key}"
        )
    lines += [
        "```",
        "",
        f"Next rebalance: 21 trading days from {rebal.date()} "
        f"(~{(rebal + pd.Timedelta(days=30)).date()})",
    ]

    text = "\n".join(lines) + "\n"
    orders_path = orders_path_for(strategy_id)
    orders_path.parent.mkdir(parents=True, exist_ok=True)
    orders_path.write_text(text)
    print("\n" + text)
    print(f"\nWrote {orders_path}")


if __name__ == "__main__":
    main()
