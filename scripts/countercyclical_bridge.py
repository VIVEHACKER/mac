"""Driver: evaluate the countercyclical bridge at one PIT as_of and (optionally) assemble the full book.

Pure engine in engine/countercyclical_bridge.py; this script wires a market index price series
(PIT-sliced to a trailing window <= as_of) and the already-tested core basket, computes the drawdown +
value gate, prints the deployment, and assembles [core(0.35), hunt(0.15), bridge(dep)] via fund_book.
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.catalog import MarketDataCatalog  # noqa: E402
from engine.core_basket import select_core_basket  # noqa: E402
from engine.countercyclical_bridge import (  # noqa: E402
    bridge_sleeve_target,
    compute_deployment,
    default_value_gate,
    format_deployment,
    market_drawdown,
)
from engine.fund_book import SleeveTarget, assemble_fund_book, format_fund_book  # noqa: E402
from engine.hunt_basket import select_hunt_basket  # noqa: E402
from scripts.core_basket import build_universe  # noqa: E402
from scripts.fund_book import (  # noqa: E402
    DEFAULT_PRICES,
    DEFAULT_SECTORS,
    DEFAULT_SNAPSHOT,
    DEFAULT_UNIVERSE,
)
from scripts.hunt_basket import build_hunt_inputs  # noqa: E402

DEFAULT_DB = Path("/Users/jjuni/재무관리 모델/trader/data/store/trader.duckdb")
DEFAULT_MARKET_CSV = ROOT / "data" / "snapshots" / "spy-history.csv"


def load_market_prices(path: Path, as_of: date | None, window: int) -> list[float]:
    """Read (date, close) rows, keep rows <= as_of, return the last `window` closes oldest->newest.

    CSV must have a 'date' (YYYY-MM-DD) and a 'close' column. PIT: nothing after as_of is used."""
    rows: list[tuple[date, float]] = []
    with path.open(newline="") as fh:
        for r in csv.DictReader(fh):
            d = datetime.fromisoformat(r["date"]).date()
            if as_of is not None and d > as_of:
                continue
            rows.append((d, float(r["close"])))
    rows.sort(key=lambda x: x[0])
    closes = [c for _d, c in rows]
    return closes[-window:] if window > 0 else closes


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Countercyclical bridge (dry-powder deployment)")
    p.add_argument("--as-of", type=str, default=None, help="YYYY-MM-DD PIT cutoff (default latest)")
    p.add_argument("--bridge-budget", type=float, default=0.15)
    p.add_argument("--value-threshold", type=float, default=0.55)
    p.add_argument(
        "--window", type=int, default=252, help="trailing trading days for drawdown peak"
    )
    p.add_argument("--market-csv", type=Path, default=DEFAULT_MARKET_CSV)
    p.add_argument("--core-fraction", type=float, default=0.35)
    p.add_argument("--hunt-fraction", type=float, default=0.15)
    p.add_argument("--max-name-weight", type=float, default=0.08)
    p.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    p.add_argument("--prices", type=Path, default=DEFAULT_PRICES)
    p.add_argument("--universe-csv", type=Path, default=DEFAULT_UNIVERSE)
    p.add_argument("--sectors-csv", type=Path, default=DEFAULT_SECTORS)
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--book", action="store_true", help="also assemble + print the full fund book")
    args = p.parse_args(argv)

    as_of = datetime.fromisoformat(args.as_of).date() if args.as_of else None
    common = {
        "snapshot": args.snapshot,
        "prices": args.prices,
        "universe_csv": args.universe_csv,
        "sectors_csv": args.sectors_csv,
    }
    try:
        universe, sectors, effective = build_universe(as_of=as_of, **common)
        core = select_core_basket(universe, sectors=sectors, as_of=effective)
        core_weights = {h.symbol: h.weight for h in core.holdings}

        prices = load_market_prices(args.market_csv, as_of, args.window)
        drawdown = market_drawdown(prices)
        gate = default_value_gate(core, threshold=args.value_threshold)
        deployment = compute_deployment(drawdown, gate, budget=args.bridge_budget)
    except (ValueError, FileNotFoundError) as e:
        raise SystemExit(str(e)) from e

    print(format_deployment(deployment))

    if args.book:
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
        book = assemble_fund_book(
            [
                SleeveTarget("core", args.core_fraction, core_weights),
                SleeveTarget("hunt", args.hunt_fraction, hunt_weights),
                bridge_sleeve_target(deployment, core_weights),
            ],
            max_name_weight=args.max_name_weight,
        )
        print()
        print(format_fund_book(book))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
