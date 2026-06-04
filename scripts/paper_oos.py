"""Forward-OOS track-record report for the paper-drill ledger.

Scores the pre-registered picks in out/paper-oos-ledger-<strategy_id>.jsonl against
realised prices and reports the LIVE excess vs the benchmark, accumulated over closed
holding periods, alongside the backtest expectation. A live excess far below the
backtested figure is overfitting revealing itself — the whole point of paper OOS.

The ledger is populated automatically every time `paper_drill.py` runs (unless
--no-record-oos). This script needs only realised prices (yfinance, or --prices CSV).

Usage:
    python -m scripts.paper_oos --strategy-id aqr_top5_cap20_trail10_pit110
    python -m scripts.paper_oos --strategy-id aqr_top7_cap20_trail10_pit110 --prices cache.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.paper_oos import OOSTrackRecord, load_ledger, score_ledger  # noqa: E402

OUT_DIR = ROOT / "out"
BACKTEST_EXCESS_ANN = 0.08  # validated IDEAL SPY-excess (+~8%/yr); the live yardstick


def _fetch_closes(symbols: list[str], start: str, end: str) -> pd.DataFrame:
    import yfinance as yf

    raw = yf.download(symbols, start=start, end=end, auto_adjust=True, progress=False)
    return raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw


def _mark_prices_at(prices: pd.DataFrame, dates: list[str]) -> dict[str, dict[str, float]]:
    marks: dict[str, dict[str, float]] = {}
    for date in dates:
        window = prices.loc[: pd.Timestamp(date)]
        if window.empty:
            continue
        row = window.iloc[-1]
        marks[date] = {sym: float(row[sym]) for sym in prices.columns if pd.notna(row[sym])}
    return marks


def _interpret(record: OOSTrackRecord) -> str:
    if record.n_periods == 0:
        return "paper OOS not started — no closed holding periods yet (run paper_drill monthly)."
    if record.n_periods < 6:
        return (
            f"only {record.n_periods} closed period(s) — far too short to judge alpha "
            "(MinTRL ~21 months). Treat as an implementation/kill-switch check, not edge proof."
        )
    if record.vs_backtest is not None and record.vs_backtest < 0.5:
        return (
            f"live excess is {record.vs_backtest:.0%} of the backtested figure — a large "
            "shortfall is the classic sign of backtest overfitting; investigate before scaling."
        )
    return "live excess broadly tracking the backtest expectation — continue accumulating."


def main() -> int:
    parser = argparse.ArgumentParser(description="Forward-OOS track record for paper drill.")
    parser.add_argument("--strategy-id", required=True)
    parser.add_argument("--prices", type=Path, default=None, help="CSV of closes (else yfinance)")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    ledger_path = OUT_DIR / f"paper-oos-ledger-{args.strategy_id}.jsonl"
    entries = load_ledger(ledger_path)

    out: list[str] = [f"# Forward-OOS track record — {args.strategy_id}", ""]
    if not entries:
        out.append(f"No ledger yet at `{ledger_path.name}`. Run paper_drill.py to start recording.")
        report = "\n".join(out)
        print(report)
        return 0

    symbols = sorted({s for e in entries for s in e.weights} | {entries[0].benchmark_symbol})
    dates = [e.rebal_date for e in entries]
    if args.prices:
        prices = pd.read_csv(args.prices, index_col=0, parse_dates=True)
    else:
        prices = _fetch_closes(symbols, start=min(dates), end=str(pd.Timestamp.now().date()))

    marks = _mark_prices_at(prices, dates)
    record = score_ledger(entries, marks, backtest_excess_ann=BACKTEST_EXCESS_ANN)

    out += [
        f"- entries recorded: {len(entries)} | closed periods scored: {record.n_periods}",
        f"- **live cumulative excess vs benchmark: {record.cumulative_excess:+.2%}** "
        f"(portfolio {record.cumulative_return:+.2%} vs benchmark {record.cumulative_benchmark:+.2%})",
        f"- annualised live excess: {record.annualized_excess:+.2%} "
        f"(backtest {BACKTEST_EXCESS_ANN:+.0%} → ratio "
        f"{record.vs_backtest:.2f}x)"
        if record.vs_backtest is not None
        else "",
        f"- monthly hit rate: {record.hit_rate:.0%} | excess Sharpe (annualised): {record.excess_sharpe:.2f}",
        "",
        f"**Reading:** {_interpret(record)}",
    ]
    report = "\n".join(line for line in out if line is not None)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report + "\n", encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
