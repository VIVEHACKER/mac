"""Forward-OOS interim status — is the track moving, and does the edge look like it's transferring?

The forward-OOS ledger is TIME-GATED: a period only formally closes at the next rebalance (~21
business days), so score_ledger reports 0 closed periods until then. This gives the INTERIM read
in between: each open entry's live mark-to-market (via engine.paper_oos._period_return) vs its
benchmark and vs the backtest's expected pace — so you can see the live paper position move
without waiting, and it exercises the same scoring math that runs at the formal close (de-risk).

NOT a verdict: an open-period MTM over a handful of days is noisy; the real read is the closed
period. Prices are CURRENT (FinanceDataReader), not pinned — an operational status, not a claim.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.paper_oos import _period_return, load_ledger  # noqa: E402

# Deploy candidate honest after-cost walk-forward pace (fee10), for an interim sanity check.
BACKTEST_ANNUAL_EXCESS = 0.0740


def _latest_closes(symbols: list[str], start: str) -> dict[str, float]:
    import FinanceDataReader as fdr  # noqa: N813

    out: dict[str, float] = {}
    for sym in symbols:
        try:
            df = fdr.DataReader(sym, start)
        except Exception:
            continue
        if df is not None and not df.empty and "Close" in df.columns:
            close = float(df["Close"].iloc[-1])
            if close > 0:
                out[sym] = close
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Forward-OOS interim status (MTM to date).")
    parser.add_argument("--strategy-id", default="aqr_top7_cap20_trail10_pit110")
    parser.add_argument("--ledger", type=Path, default=None)
    parser.add_argument("--start", default="2026-05-01", help="fdr fetch start (recent window).")
    args = parser.parse_args()

    ledger_path = args.ledger or ROOT / "out" / f"paper-oos-ledger-{args.strategy_id}.jsonl"
    if not ledger_path.exists():
        print(f"ledger not found: {ledger_path}")
        return 1
    entries = load_ledger(ledger_path)
    if not entries:
        print(f"ledger empty: {ledger_path}")
        return 1

    symbols = sorted(
        {s for e in entries for s in e.weights} | {e.benchmark_symbol for e in entries}
    )
    print(f"fetching current closes for {len(symbols)} symbols ...")
    marks = _latest_closes(symbols, args.start)
    today = date.today()

    lines = [
        f"# Forward-OOS interim status — {args.strategy_id}",
        "",
        f"entries: {len(entries)} | latest rebalance: {entries[-1].rebal_date} | as-of: {today} "
        "| INTERIM (period not closed; noisy — the real read is the closed period at next rebalance)",
        "",
        "| rebal | bdays held | port MTM | benchmark | excess | backtest-pace | vs pace |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    pct = lambda x: f"{x * 100:+.2f}%" if x is not None else "n/a"  # noqa: E731
    for e in entries:
        port = _period_return(e, marks)
        bench_mark = marks.get(e.benchmark_symbol)
        bench = (
            (bench_mark / e.benchmark_price - 1.0)
            if (bench_mark and e.benchmark_price > 0)
            else None
        )
        excess = (port - bench) if (port is not None and bench is not None) else None
        bdays = int(len(pd.bdate_range(e.rebal_date, today.isoformat()))) - 1
        pace = BACKTEST_ANNUAL_EXCESS * bdays / 252 if bdays > 0 else 0.0
        vs = (excess - pace) if excess is not None else None
        lines.append(
            f"| {e.rebal_date} | {bdays} | {pct(port)} | {pct(bench)} | {pct(excess)} | "
            f"{pct(pace)} | {pct(vs)} |"
        )
    lines += [
        "",
        "## Read (interim — NOT a verdict)",
        "- port MTM = live weighted return of the picks since entry (renormalised over marked names).",
        "- excess = port − benchmark. backtest-pace = +7.40%/yr (fee10) scaled to bdays held.",
        f"- The first FORMAL closed period arrives at the next rebalance (~21 bdays after "
        f"{entries[-1].rebal_date}); until then this is unrealised and noisy. {len(marks)}/"
        f"{len(symbols)} symbols marked.",
    ]
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
