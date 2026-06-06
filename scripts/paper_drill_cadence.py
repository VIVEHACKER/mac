"""Cadence gate for the AQR forward-OOS paper drill.

paper_drill.py itself has no rebalance-timing gate — every invocation stamps a rebalance
with *today's* date. Running it daily would therefore append a new ledger row every day and
destroy the 21-trading-day cadence the strategy was validated at. This wrapper runs the drill
ONLY when 21 business days have elapsed since the last pre-registered rebalance, so a daily
cron produces exactly one rebalance per period — the honest forward-OOS record.

Usage (cron daily):
    python -m scripts.paper_drill_cadence --strategy-id aqr_top7_cap20_trail10_pit110 \
        --top-n 7 --snapshot data/snapshots/fundamentals-2026-06-01-gp.csv
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
DEFAULT_CADENCE = 21  # trading days the IDEAL line was validated at


def business_days_between(start: date, end: date) -> int:
    """Count business days (Mon–Fri) strictly after ``start`` up to and including ``end``.

    Holidays are not modelled (the drill is reproducible from a pinned snapshot and a 1-day
    slip around a holiday does not change the validated cadence materially).
    """
    if end <= start:
        return 0
    days = 0
    cur = start
    while cur < end:
        cur += timedelta(days=1)
        if cur.weekday() < 5:  # Mon=0 .. Fri=4
            days += 1
    return days


def is_rebalance_due(
    last_rebal: date | None, today: date, *, cadence: int = DEFAULT_CADENCE
) -> bool:
    """True when a new rebalance is due: never rebalanced, or >= cadence business days since."""
    if last_rebal is None:
        return True
    return business_days_between(last_rebal, today) >= cadence


def _last_rebal_date(strategy_id: str) -> date | None:
    # Import here so the pure cadence helpers stay dependency-free and trivially testable.
    from engine.paper_oos import load_ledger

    ledger = OUT / f"paper-oos-ledger-{strategy_id}.jsonl"
    entries = load_ledger(ledger)
    if not entries:
        return None
    return max(date.fromisoformat(e.rebal_date) for e in entries)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy-id", default="aqr_top7_cap20_trail10_pit110")
    parser.add_argument("--top-n", type=int, default=7)
    parser.add_argument("--snapshot", default="data/snapshots/fundamentals-2026-06-01-gp.csv")
    parser.add_argument("--cadence", type=int, default=DEFAULT_CADENCE)
    parser.add_argument("--today", default="", help="override today (ISO) for testing")
    args = parser.parse_args()

    today = date.fromisoformat(args.today) if args.today else date.today()
    last = _last_rebal_date(args.strategy_id)
    if not is_rebalance_due(last, today, cadence=args.cadence):
        elapsed = business_days_between(last, today) if last else 0
        print(
            f"[cadence] 리밸런스 미도래 — 마지막 {last}, {elapsed}/{args.cadence} 영업일 경과. skip."
        )
        return 0

    print(f"[cadence] 리밸런스 도래 (마지막 {last}) — paper_drill 실행")
    cmd = [
        sys.executable,
        "-m",
        "scripts.paper_drill",
        "--top-n",
        str(args.top_n),
        "--snapshot",
        args.snapshot,
        "--strategy-id",
        args.strategy_id,
    ]
    return subprocess.run(cmd, cwd=ROOT, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
