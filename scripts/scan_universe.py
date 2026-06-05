"""Scan the WHOLE validated universe and emit a ranked recommendation table.

The AQR signal is cross-sectional, so this ranks every name in the validated universe
in a single pass and attaches each name's fair-value band, laddered entry and honest
confidence. This is the answer to "evaluate all tickers, not a handful" — scoped to the
universe where the strategy actually has a validated edge (scanning unvalidated names
would amplify noise, per the P5 forward-validation finding).

Usage:
    python -m scripts.scan_universe                 # latest snapshot date, top 20
    python -m scripts.scan_universe --top 40 --asof 2026-05-27
    python -m scripts.scan_universe --output out/universe-scan.md
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.evaluate_ticker import (  # noqa: E402
    DEFAULT_FUNDAMENTALS,
    DEFAULT_PRICES,
    load_universe,
)
from valuation.recommendation import (  # noqa: E402
    format_scan,
    load_validated_strategy,
    scan_universe,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Rank the validated universe with the AQR signal.")
    parser.add_argument("--asof", help="evaluation date YYYY-MM-DD (default: latest in snapshot)")
    parser.add_argument("--top", type=int, default=20, help="rows to display (default: 20)")
    parser.add_argument("--prices", type=Path, default=DEFAULT_PRICES)
    parser.add_argument("--fundamentals", type=Path, default=DEFAULT_FUNDAMENTALS)
    parser.add_argument("--strategy", default=None, help="strategy id (default: config default)")
    parser.add_argument("--output", type=Path, default=None, help="write markdown to this path")
    args = parser.parse_args()

    asof = datetime.strptime(args.asof, "%Y-%m-%d").date() if args.asof else None
    strategy = load_validated_strategy(args.strategy)
    bars, fundamentals, as_of_dt = load_universe(args.prices, args.fundamentals, asof)

    results = scan_universe(
        bars_by_symbol=bars,
        fundamentals_by_symbol=fundamentals,
        strategy=strategy,
        asof_ts=as_of_dt,
    )
    report = format_scan(results, top=args.top)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report + "\n", encoding="utf-8")
        print(f"wrote {len(results)} ranked names to {args.output}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
