"""Pre-register the LATEST closed-bar chart signal for a universe into the forward-OOS ledger.

Run this on a schedule (e.g. a cron every few hours for crypto 4h). Each run appends, for
each symbol, the decision at the most recently *closed* bar — pre-registered with its
timestamp and entry price, never rewritten. Later, scripts/chart_paper_score.py scores the
matured entries against prices that arrived afterwards. That forward record is the only
honest test of whether the backtested edge (docs/CHART_VALIDATION.md) survives out of sample.

Usage:
    python -m scripts.chart_paper_log --symbols BTC/USDT,ETH/USDT,SOL/USDT --tf 4h
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path

from data.ingest.ccxt_crypto import fetch_ccxt_bars
from engine.chart.read import read_chart
from engine.chart_oos import ChartSignalEntry, append_signal

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LEDGER = ROOT / "out" / "chart-oos-ledger.jsonl"
_TF_HOURS = {
    "15m": 0.25,
    "30m": 0.5,
    "1h": 1.0,
    "2h": 2.0,
    "4h": 4.0,
    "6h": 6.0,
    "12h": 12.0,
    "1d": 24.0,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT")
    parser.add_argument("--tf", default="4h")
    parser.add_argument("--direction", default="long", choices=["long", "short"])
    parser.add_argument("--lookback", type=int, default=300)
    parser.add_argument("--exchange", default="binance")
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    args = parser.parse_args()

    end = date.today()
    span_days = max(5, int((args.lookback * _TF_HOURS.get(args.tf, 4.0)) / 24) + 5)
    start = end - timedelta(days=span_days)

    logged = skipped = 0
    for sym in [s.strip() for s in args.symbols.split(",") if s.strip()]:
        try:
            bars = fetch_ccxt_bars(
                sym,
                start,
                end,
                timeframe=args.tf,
                exchange_id=args.exchange,
                intraday=(args.tf != "1d"),
            )
        except Exception as exc:  # noqa: BLE001 - a fetch failure for one symbol must not abort the run
            print(f"  {sym}: FETCH FAIL — {exc}")
            continue
        # drop the last (potentially still-forming) bar → decide on the last CLOSED bar
        closed = bars[:-1]
        if len(closed) < 60:
            print(f"  {sym}: 봉 부족({len(closed)})")
            continue
        window = closed[-args.lookback :]
        read = read_chart(window, direction=args.direction, mean_reversion=True)
        entry = ChartSignalEntry(
            logged_ts=str(closed[-1].ts),
            symbol=sym,
            market="crypto",
            timeframe=args.tf,
            direction=args.direction,
            decision=read.decision.value,
            confluence=read.confluence,
            range_pos=float(read.features.get("range_pos", 0.5)),  # type: ignore[arg-type]
            mean_reversion=True,
            entry_price=closed[-1].close,
        )
        try:
            append_signal(args.ledger, entry)
            logged += 1
            print(
                f"  {sym} @ {entry.logged_ts}: {entry.decision} (conf {entry.confluence:.0f}, "
                f"rp {entry.range_pos:.2f}) → logged"
            )
        except ValueError:
            skipped += 1
            print(f"  {sym} @ {entry.logged_ts}: 이미 기록됨(idempotent)")

    print(f"\n원장: {args.ledger}  | 신규 {logged} · 중복 {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
