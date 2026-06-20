"""Drill: pre-register the assembled fund book into the forward paper-OOS ledger, or score the ledger.

Phase-2 forward OOS for the barbell. Assembles the fund book at one PIT `effective` as_of (core + hunt
[+ momentum if --price-history], reusing scripts/fund_book.py's tested assembly), reads entry prices for
the held symbols from a marks CSV at that date, and appends ONE immutable pre-registered entry (a
dry-run drill — no live trading). `--score` marks the ledger on realised prices vs the benchmark.

Pure ledger I/O + scorer in engine/fund_book_oos.py. Entry prices reuse the engine's own mark loaders so
the recorded buy and the later realised mark come from the same price representation.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.catalog import MarketDataCatalog  # noqa: E402
from data.price_snapshot import read_price_snapshot  # noqa: E402
from engine.core_basket import select_core_basket  # noqa: E402
from engine.fund_book import SleeveTarget, assemble_fund_book, format_fund_book  # noqa: E402
from engine.fund_book_oos import (  # noqa: E402
    append_entry,
    fund_book_to_entry,
    load_ledger,
    load_mark_price_history_csv,
    mark_prices_at_dates,
    score_ledger,
)
from engine.hunt_basket import select_hunt_basket  # noqa: E402
from engine.momentum_basket import momentum_sleeve_target, select_momentum_basket  # noqa: E402
from scripts.aqr_ideal_walkforward import lookup_pit, prefetch  # noqa: E402
from scripts.core_basket import build_universe  # noqa: E402
from scripts.fund_book import (  # noqa: E402
    DEFAULT_DB,
    DEFAULT_PRICES,
    DEFAULT_SECTORS,
    DEFAULT_SNAPSHOT,
    DEFAULT_UNIVERSE,
    load_momentum_universe,
)
from scripts.hunt_basket import build_hunt_inputs  # noqa: E402

DEFAULT_LEDGER = ROOT / "out" / "fund-book-oos.jsonl"


def assemble_book(args, *, as_of):
    """Assemble core + hunt (+ optional momentum) at one resolved cutoff. Returns (book, effective)."""
    common = {
        "snapshot": args.snapshot,
        "prices": args.prices,
        "universe_csv": args.universe_csv,
        "sectors_csv": args.sectors_csv,
    }
    universe, sectors, effective = build_universe(as_of=as_of, **common)
    core = select_core_basket(universe, sectors=sectors, as_of=effective)
    core_weights = {h.symbol: h.weight for h in core.holdings}

    insider_signals, capital_signals, hunt_universe, _sec, _eff = build_hunt_inputs(
        catalog=MarketDataCatalog(args.db), as_of=effective, **common
    )
    hunt = select_hunt_basket(
        insider_signals,
        hunt_universe,
        capital_signals=capital_signals,
        sectors=sectors,
        as_of=effective,
    )
    hunt_weights = {h.symbol: h.weight for h in hunt.holdings}

    sleeves = [
        SleeveTarget("core", args.core_fraction, core_weights),
        SleeveTarget("hunt", args.hunt_fraction, hunt_weights),
    ]
    if args.price_history:
        if args.momentum_snapshot is None:
            print(
                "⚠️  momentum running off the LIVE catalog (NOT reproducible) — "
                "pass --momentum-snapshot to pin fundamentals",
                file=sys.stderr,
            )
        momentum_syms = load_momentum_universe(args.momentum_universe)
        prices = read_price_snapshot(args.price_history, verify=True)
        fund_cache = prefetch(MarketDataCatalog(args.db), snapshot_path=args.momentum_snapshot)
        as_of_dt = datetime.combine(effective, datetime.max.time())
        fund_by_sym = {}
        for sym in momentum_syms:
            rec = lookup_pit(fund_cache.get(sym, []), as_of_dt)
            if rec is not None:
                fund_by_sym[sym.upper()] = rec
        momentum = select_momentum_basket(
            prices,
            fund_by_sym,
            momentum_syms,
            as_of=effective,
            top_n=args.momentum_top_n,
            cap=args.momentum_cap,
        )
        sleeves.append(momentum_sleeve_target(momentum, fraction=args.momentum_fraction))

    book = assemble_fund_book(sleeves, max_name_weight=args.max_name_weight)
    return book, effective


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Fund-book forward paper-OOS drill (record / score)")
    p.add_argument("--as-of", type=str, default=None, help="YYYY-MM-DD PIT cutoff (default latest)")
    p.add_argument(
        "--marks", type=Path, required=True, help="close-price CSV (date + symbol columns)"
    )
    p.add_argument("--benchmark", type=str, default="SPY")
    p.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    p.add_argument("--score", action="store_true", help="score the ledger instead of recording")
    p.add_argument("--dry-run", action="store_true", help="print the entry without appending")
    p.add_argument("--periods-per-year", type=float, default=12.0)
    p.add_argument(
        "--max-staleness-days",
        type=int,
        default=None,
        help="bound carry-forward of stale marks (per symbol), applied at BOTH record and score time; "
        "None = unbounded (entry prices may be stale — pass a bound for forward-OOS integrity)",
    )
    # assembly knobs (mirror scripts/fund_book.py)
    p.add_argument("--core-fraction", type=float, default=0.35)
    p.add_argument("--hunt-fraction", type=float, default=0.15)
    p.add_argument("--max-name-weight", type=float, default=0.08)
    p.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    p.add_argument("--prices", type=Path, default=DEFAULT_PRICES)
    p.add_argument("--universe-csv", type=Path, default=DEFAULT_UNIVERSE)
    p.add_argument("--sectors-csv", type=Path, default=DEFAULT_SECTORS)
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument(
        "--price-history", type=Path, default=None, help="momentum sleeve price history (opt-in)"
    )
    p.add_argument("--momentum-fraction", type=float, default=0.25)
    p.add_argument("--momentum-snapshot", type=Path, default=None)
    p.add_argument("--momentum-top-n", type=int, default=7)
    p.add_argument("--momentum-cap", type=float, default=0.20)
    p.add_argument("--momentum-universe", type=Path, default=None)
    args = p.parse_args(argv)

    try:
        history = load_mark_price_history_csv(args.marks)
        if args.score:
            entries = load_ledger(args.ledger)
            dates = [e.rebal_date for e in entries]
            marks = mark_prices_at_dates(history, dates, max_staleness_days=args.max_staleness_days)
            record = score_ledger(entries, marks, periods_per_year=args.periods_per_year)
            print(record)
            return 0

        as_of = datetime.fromisoformat(args.as_of).date() if args.as_of else None
        book, effective = assemble_book(args, as_of=as_of)
        rebal_date = effective.isoformat()
        # Same staleness bound as scoring (line above) — else a stale entry price would be recorded
        # then dropped at score time, silently recomposing the book (adversarial-review HIGH).
        marks_at = mark_prices_at_dates(
            history, [rebal_date], max_staleness_days=args.max_staleness_days
        ).get(rebal_date, {})
        bench_price = marks_at.get(args.benchmark.upper())
        if bench_price is None or bench_price <= 0.0:
            raise SystemExit(
                f"no positive benchmark price for {args.benchmark} at {rebal_date} in {args.marks}"
            )
        entry = fund_book_to_entry(
            book,
            rebal_date=rebal_date,
            entry_prices=marks_at,
            benchmark_symbol=args.benchmark.upper(),
            benchmark_price=bench_price,
        )
    except (ValueError, FileNotFoundError) as e:
        raise SystemExit(str(e)) from e

    print(format_fund_book(book))
    print(
        f"\nOOS entry for {rebal_date} ({len(entry.weights)} names, invested {entry.invested:.1%}):"
    )
    if args.dry_run:
        print("(dry-run — not appended)")
        return 0
    append_entry(args.ledger, entry)
    print(f"appended to {args.ledger}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
