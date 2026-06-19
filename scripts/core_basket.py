"""Driver: build the core basket from pinned PIT snapshots and print the report.

The pure engine lives in engine/core_basket.py; this script carries all I/O
(fundamentals + price snapshots, sectors CSV) and the PIT as_of discipline.
It mirrors scripts/compounder_forward_validation.py's snapshot/PIT assembly.

Snapshots are pinned content-hash artifacts (verify=True fails loud on drift).
Their CSVs are gitignored (only manifests are tracked), so on a clean checkout
regenerate via scripts/snapshot_fundamentals.py / scripts/snapshot_prices.py or
pass --snapshot / --prices to a regenerated file.
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

from data.fundamentals_snapshot import read_fundamentals_snapshot  # noqa: E402
from data.models import FundamentalRecord  # noqa: E402
from data.price_snapshot import read_price_snapshot  # noqa: E402
from engine.core_basket import format_core_basket, select_core_basket  # noqa: E402

DEFAULT_UNIVERSE = ROOT / "data" / "universes" / "sp400-600-current.csv"
DEFAULT_SNAPSHOT = ROOT / "data" / "snapshots" / "fundamentals-2026-06-01-gp2.csv"
DEFAULT_PRICES = ROOT / "data" / "snapshots" / "prices-2026-06-01.csv"
DEFAULT_SECTORS = ROOT / "data" / "sectors" / "sp400-600-current-sectors.csv"


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
    """Last close on/before as_of (PIT). as_of is always concrete (resolved by build_universe)."""
    if symbol not in closes.columns:
        return None
    import pandas as pd

    s = closes[symbol].dropna().loc[: pd.Timestamp(as_of)]
    if s.empty:
        return None
    val = float(s.iloc[-1])
    return val if val > 0 else None


def build_universe(
    *,
    snapshot: Path,
    prices: Path,
    universe_csv: Path,
    sectors_csv: Path,
    as_of: date | None,
) -> tuple[dict[str, tuple[Sequence[FundamentalRecord], float]], dict[str, str], date]:
    """Build the PIT universe. Returns (universe, sectors, effective_as_of).

    PIT discipline (one cutoff for BOTH legs): when as_of is None it resolves to the price
    snapshot's latest date (the data's natural "now") and is applied to fundamentals AND
    prices alike — never "all fundamentals + latest price", which would mix periods and leak."""
    import pandas as pd

    for label, p in (("snapshot", snapshot), ("prices", prices)):
        if not p.exists():
            raise SystemExit(
                f"{label} not found: {p}\n"
                "Snapshot CSVs are gitignored (only manifests are tracked). Regenerate via "
                "scripts/snapshot_fundamentals.py / scripts/snapshot_prices.py, or pass "
                f"--{'snapshot' if label == 'snapshot' else 'prices'} <path>."
            )
    symbols = load_symbols(universe_csv)
    sectors = load_sectors(sectors_csv)

    funds: dict[str, list[FundamentalRecord]] = {}
    for rec in read_fundamentals_snapshot(snapshot, verify=True):
        funds.setdefault(rec.symbol.upper(), []).append(rec)
    for recs in funds.values():
        recs.sort(key=lambda r: r.asof_ts)

    closes = read_price_snapshot(prices, verify=True)
    closes.index = pd.to_datetime(closes.index)
    cov_min, cov_max = closes.index.min().date(), closes.index.max().date()

    effective = as_of or cov_max  # default to the snapshot's latest date (reproducible, PIT)
    if effective < cov_min or effective > cov_max:
        raise ValueError(
            f"as_of {effective} is outside the price snapshot coverage {cov_min}..{cov_max}; "
            "pass --as-of within range or regenerate the price snapshot."
        )

    universe: dict[str, tuple[Sequence[FundamentalRecord], float]] = {}
    for sym in symbols:
        recs = [r for r in funds.get(sym, []) if r.asof_ts.date() <= effective]
        if len(recs) < 2:
            continue
        price = _price_asof(closes, sym, effective)
        if price is None:
            continue
        universe[sym] = (recs, price)
    return universe, sectors, effective


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Core basket selector (PIT, pinned snapshots)")
    p.add_argument("--as-of", type=str, default=None, help="YYYY-MM-DD PIT cutoff (default latest)")
    p.add_argument("--target-n", type=int, default=13)
    p.add_argument("--max-weight", type=float, default=0.08)
    p.add_argument("--w-value", type=float, default=0.6)
    p.add_argument("--w-gp", type=float, default=0.4)
    p.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    p.add_argument("--prices", type=Path, default=DEFAULT_PRICES)
    p.add_argument("--universe-csv", type=Path, default=DEFAULT_UNIVERSE)
    p.add_argument("--sectors-csv", type=Path, default=DEFAULT_SECTORS)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    as_of = datetime.fromisoformat(args.as_of).date() if args.as_of else None
    universe, sectors, effective = build_universe(
        snapshot=args.snapshot,
        prices=args.prices,
        universe_csv=args.universe_csv,
        sectors_csv=args.sectors_csv,
        as_of=as_of,
    )
    basket = select_core_basket(
        universe,
        sectors=sectors,
        target_n=args.target_n,
        max_weight=args.max_weight,
        w_value=args.w_value,
        w_gp=args.w_gp,
        as_of=effective,  # record the resolved PIT date (reproducible), not None
    )
    print(format_core_basket(basket))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
