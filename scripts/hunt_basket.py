"""Driver: build the hunt basket from catalog insider trades + pinned snapshots (PIT) and print it.

Pure engine in engine/hunt_basket.py; this script carries all I/O (catalog insider trades, pinned
fundamentals/prices, sectors) and the PIT as_of discipline. Mirrors scripts/core_basket.py.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.catalog import MarketDataCatalog  # noqa: E402
from data.fundamentals_snapshot import read_fundamentals_snapshot  # noqa: E402
from data.models import FundamentalRecord  # noqa: E402
from data.price_snapshot import read_price_snapshot  # noqa: E402
from engine.hunt_basket import format_hunt_basket, select_hunt_basket  # noqa: E402
from signals.capital import net_issuance_signal  # noqa: E402
from signals.insider import insider_buying_signal  # noqa: E402
from strategies._base import StrategySignal  # noqa: E402

DEFAULT_UNIVERSE = ROOT / "data" / "universes" / "sp400-600-current.csv"
DEFAULT_SNAPSHOT = ROOT / "data" / "snapshots" / "fundamentals-2026-06-01-gp2.csv"
DEFAULT_PRICES = ROOT / "data" / "snapshots" / "prices-2026-06-01.csv"
DEFAULT_SECTORS = ROOT / "data" / "sectors" / "sp400-600-current-sectors.csv"
DEFAULT_DB = Path("/Users/jjuni/재무관리 모델/trader/data/store/trader.duckdb")


def load_symbols(path: Path) -> list[str]:
    with path.open(encoding="utf-8") as f:
        return sorted({r["symbol"].upper() for r in csv.DictReader(f)})


def load_sectors(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[r["symbol"].upper()] = r.get("sector") or "unknown"
    return out


def _price_asof(closes, symbol: str, as_of: date) -> float | None:
    if symbol not in closes.columns:
        return None
    import pandas as pd

    s = closes[symbol].dropna().loc[: pd.Timestamp(as_of)]
    if s.empty:
        return None
    val = float(s.iloc[-1])
    return val if val > 0 else None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Hunt basket selector (PIT, catalog insider + snapshots)"
    )
    p.add_argument("--as-of", type=str, default=None)
    p.add_argument("--target-n", type=int, default=6)
    p.add_argument("--max-per-name", type=float, default=0.40)
    p.add_argument("--sleeve-fraction", type=float, default=0.15)
    p.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    p.add_argument("--prices", type=Path, default=DEFAULT_PRICES)
    p.add_argument("--universe-csv", type=Path, default=DEFAULT_UNIVERSE)
    p.add_argument("--sectors-csv", type=Path, default=DEFAULT_SECTORS)
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = p.parse_args(argv)

    import pandas as pd

    for label, path in (("snapshot", args.snapshot), ("prices", args.prices)):
        if not path.exists():
            raise SystemExit(
                f"{label} not found: {path} (snapshot CSVs are gitignored; regenerate)"
            )

    symbols = load_symbols(args.universe_csv)
    sectors = load_sectors(args.sectors_csv)

    funds: dict[str, list[FundamentalRecord]] = {}
    for rec in read_fundamentals_snapshot(args.snapshot, verify=True):
        funds.setdefault(rec.symbol.upper(), []).append(rec)
    for recs in funds.values():
        recs.sort(key=lambda r: r.asof_ts)

    closes = read_price_snapshot(args.prices, verify=True)
    closes.index = pd.to_datetime(closes.index)
    cov_min, cov_max = closes.index.min().date(), closes.index.max().date()
    effective = datetime.fromisoformat(args.as_of).date() if args.as_of else cov_max
    if effective < cov_min or effective > cov_max:
        raise SystemExit(f"as_of {effective} outside price coverage {cov_min}..{cov_max}")
    # EOD cutoff with microseconds to match signals/insider.py + signals/capital.py _as_cutoff
    # (a bare ...23:59:59 would drop same-day late-evening filings the signal funcs would include).
    cutoff_dt = datetime(effective.year, effective.month, effective.day, 23, 59, 59, 999999)

    catalog = MarketDataCatalog(args.db)
    insider_signals: dict[str, StrategySignal | None] = {}
    capital_signals: dict[str, StrategySignal | None] = {}
    universe: dict[str, tuple[Sequence[FundamentalRecord], float]] = {}
    for sym in symbols:
        trades = catalog.get_insider_trades(sym, market="us", as_of=cutoff_dt, limit=0)
        insider_signals[sym] = insider_buying_signal(trades, as_of=effective)
        recs = [r for r in funds.get(sym, []) if r.asof_ts.date() <= effective]
        price = _price_asof(closes, sym, effective) if recs else None
        if recs and price is not None:
            # signals + universe stay symmetric: both exist only for fully-evaluated names
            universe[sym] = (recs, price)
            capital_signals[sym] = net_issuance_signal(recs, as_of=effective)

    basket = select_hunt_basket(
        insider_signals,
        universe,
        capital_signals=capital_signals,
        sectors=sectors,
        target_n=args.target_n,
        max_per_name=args.max_per_name,
        sleeve_fraction=args.sleeve_fraction,
        as_of=effective,
    )
    print(format_hunt_basket(basket))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
