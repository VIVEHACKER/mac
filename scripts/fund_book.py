"""Driver: assemble the 50/50 barbell fund book from the core + hunt sleeves at one PIT as_of.

Pure engine in engine/fund_book.py; this script wires the already-tested PIT assemblers
(scripts/core_basket.build_universe -> select_core_basket; scripts/hunt_basket.build_hunt_inputs ->
select_hunt_basket) at a single as_of, converts each basket to a sleeve-relative SleeveTarget, and
composes them. Fractions are the user's barbell POLICY (overridable): core 35% + hunt 15% = the long
half; the remaining 50% (active/discretionary) stays reserve cash until momentum/IDEAL is wired in.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.catalog import MarketDataCatalog  # noqa: E402
from engine.core_basket import select_core_basket  # noqa: E402
from engine.fund_book import SleeveTarget, assemble_fund_book, format_fund_book  # noqa: E402
from engine.hunt_basket import select_hunt_basket  # noqa: E402
from scripts.core_basket import build_universe  # noqa: E402
from scripts.hunt_basket import build_hunt_inputs  # noqa: E402

DEFAULT_UNIVERSE = ROOT / "data" / "universes" / "sp400-600-current.csv"
DEFAULT_SNAPSHOT = ROOT / "data" / "snapshots" / "fundamentals-2026-06-01-gp2.csv"
DEFAULT_PRICES = ROOT / "data" / "snapshots" / "prices-2026-06-01.csv"
DEFAULT_SECTORS = ROOT / "data" / "sectors" / "sp400-600-current-sectors.csv"
DEFAULT_DB = Path("/Users/jjuni/재무관리 모델/trader/data/store/trader.duckdb")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Fund book assembler (50/50 barbell: core + hunt)")
    p.add_argument("--as-of", type=str, default=None, help="YYYY-MM-DD PIT cutoff (default latest)")
    p.add_argument("--core-fraction", type=float, default=0.35)
    p.add_argument("--hunt-fraction", type=float, default=0.15)
    p.add_argument("--max-name-weight", type=float, default=0.08)
    p.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    p.add_argument("--prices", type=Path, default=DEFAULT_PRICES)
    p.add_argument("--universe-csv", type=Path, default=DEFAULT_UNIVERSE)
    p.add_argument("--sectors-csv", type=Path, default=DEFAULT_SECTORS)
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = p.parse_args(argv)

    as_of = datetime.fromisoformat(args.as_of).date() if args.as_of else None
    common = {
        "snapshot": args.snapshot,
        "prices": args.prices,
        "universe_csv": args.universe_csv,
        "sectors_csv": args.sectors_csv,
    }
    try:
        # Core sleeve.
        universe, sectors, effective = build_universe(as_of=as_of, **common)
        core = select_core_basket(universe, sectors=sectors, as_of=effective)
        core_weights = {h.symbol: h.weight for h in core.holdings}

        # Hunt sleeve (same as_of; insider trades from the catalog).
        insider_signals, capital_signals, hunt_universe, _sec, _eff = build_hunt_inputs(
            catalog=MarketDataCatalog(args.db), as_of=as_of, **common
        )
        hunt = select_hunt_basket(
            insider_signals,
            hunt_universe,
            capital_signals=capital_signals,
            sectors=sectors,
            as_of=effective,
        )
        hunt_weights = {h.symbol: h.weight for h in hunt.holdings}
    except ValueError as e:
        raise SystemExit(str(e)) from e

    book = assemble_fund_book(
        [
            SleeveTarget("core", args.core_fraction, core_weights),
            SleeveTarget("hunt", args.hunt_fraction, hunt_weights),
        ],
        max_name_weight=args.max_name_weight,
    )
    print(format_fund_book(book))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
