"""Driver: assemble the 50/50 barbell fund book from core + hunt (+ optional momentum) at one PIT as_of.

Pure engine in engine/fund_book.py; this script wires the already-tested PIT assemblers
(scripts/core_basket.build_universe -> select_core_basket; scripts/hunt_basket.build_hunt_inputs ->
select_hunt_basket; engine.momentum_basket.select_momentum_basket) at a single resolved as_of, converts
each basket to a sleeve-relative SleeveTarget, and composes them. Fractions are the user's barbell
POLICY (overridable): core 35% + hunt 15% = the long half; momentum/IDEAL 25% is the active-half
validated leg (opt-in via --price-history); the remaining ~25% (bridge dry powder + discretionary) stays
reserve cash.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.catalog import MarketDataCatalog  # noqa: E402
from data.price_snapshot import read_price_snapshot  # noqa: E402
from engine.core_basket import select_core_basket  # noqa: E402
from engine.fund_book import (  # noqa: E402
    FundBook,
    SleeveTarget,
    assemble_fund_book,
    format_fund_book,
)
from engine.fund_exposure import compute_exposure, format_exposure  # noqa: E402
from engine.hunt_basket import select_hunt_basket  # noqa: E402
from engine.momentum_basket import momentum_sleeve_target, select_momentum_basket  # noqa: E402
from scripts.aqr_ideal_walkforward import MEGACAPS, lookup_pit, prefetch  # noqa: E402
from scripts.core_basket import build_universe  # noqa: E402
from scripts.hunt_basket import build_hunt_inputs  # noqa: E402

DEFAULT_UNIVERSE = ROOT / "data" / "universes" / "sp400-600-current.csv"
DEFAULT_SNAPSHOT = ROOT / "data" / "snapshots" / "fundamentals-2026-06-01-gp2.csv"
DEFAULT_PRICES = ROOT / "data" / "snapshots" / "prices-2026-06-01.csv"
DEFAULT_SECTORS = ROOT / "data" / "sectors" / "sp400-600-current-sectors.csv"
DEFAULT_DB = Path("/Users/jjuni/재무관리 모델/trader/data/store/trader.duckdb")


def load_momentum_universe(path: Path | None) -> list[str]:
    """Symbols for the momentum sleeve: a CSV/newline list (first column, header 'symbol'/'ticker'
    skipped), uppercased; defaults to the validated MEGACAPS list when no path is given."""
    if path is None:
        return list(MEGACAPS)
    out: list[str] = []
    for line in path.read_text().splitlines():
        sym = line.split(",")[0].strip().upper()
        if sym and sym not in {"SYMBOL", "TICKER"}:
            out.append(sym)
    return out


def build_fund_book(
    *,
    as_of: date | None = None,
    snapshot: Path = DEFAULT_SNAPSHOT,
    prices: Path = DEFAULT_PRICES,
    universe_csv: Path = DEFAULT_UNIVERSE,
    sectors_csv: Path = DEFAULT_SECTORS,
    db: Path = DEFAULT_DB,
    core_fraction: float = 0.35,
    hunt_fraction: float = 0.15,
    momentum_fraction: float = 0.25,
    max_name_weight: float = 0.08,
    price_history: Path | None = None,
    momentum_snapshot: Path | None = None,
    momentum_universe: Path | None = None,
    momentum_top_n: int = 7,
    momentum_cap: float = 0.20,
) -> tuple[FundBook, dict[str, str]]:
    """단일 PIT as_of에서 바벨(core+hunt+optional momentum)을 조립해 (FundBook, sectors) 반환.

    CLI(main)와 대시보드가 공유하는 단일 출처. 동작은 기존 main()과 동일 — 검증 슬리브
    타겟을 사용자 바벨 정책 비중으로 조립만 하고 펀드레벨 가드(종목캡·Σ비중≤1·롱온리)만 강제.
    """
    common = {
        "snapshot": snapshot,
        "prices": prices,
        "universe_csv": universe_csv,
        "sectors_csv": sectors_csv,
    }
    universe, sectors, effective = build_universe(as_of=as_of, **common)
    core = select_core_basket(universe, sectors=sectors, as_of=effective)
    core_weights = {h.symbol: h.weight for h in core.holdings}

    insider_signals, capital_signals, hunt_universe, _sec, _eff = build_hunt_inputs(
        catalog=MarketDataCatalog(db), as_of=effective, **common
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
        SleeveTarget("core", core_fraction, core_weights),
        SleeveTarget("hunt", hunt_fraction, hunt_weights),
    ]

    if price_history:
        momentum_syms = load_momentum_universe(momentum_universe)
        px = read_price_snapshot(price_history, verify=True)
        fund_cache = prefetch(MarketDataCatalog(db), snapshot_path=momentum_snapshot)
        as_of_dt = datetime.combine(effective, datetime.max.time())
        fund_by_sym = {}
        for sym in momentum_syms:
            rec = lookup_pit(fund_cache.get(sym, []), as_of_dt)
            if rec is not None:
                fund_by_sym[sym.upper()] = rec
        momentum = select_momentum_basket(
            px,
            fund_by_sym,
            momentum_syms,
            as_of=effective,
            top_n=momentum_top_n,
            cap=momentum_cap,
        )
        sleeves.append(momentum_sleeve_target(momentum, fraction=momentum_fraction))

    book = assemble_fund_book(sleeves, max_name_weight=max_name_weight)
    return book, sectors


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Fund book assembler (barbell: core + hunt + momentum)")
    p.add_argument("--as-of", type=str, default=None, help="YYYY-MM-DD PIT cutoff (default latest)")
    p.add_argument("--core-fraction", type=float, default=0.35)
    p.add_argument("--hunt-fraction", type=float, default=0.15)
    p.add_argument("--max-name-weight", type=float, default=0.08)
    p.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    p.add_argument("--prices", type=Path, default=DEFAULT_PRICES)
    p.add_argument("--universe-csv", type=Path, default=DEFAULT_UNIVERSE)
    p.add_argument("--sectors-csv", type=Path, default=DEFAULT_SECTORS)
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument(
        "--price-history",
        type=Path,
        default=None,
        help="time-series price CSV for the momentum sleeve (opt-in); omit to skip momentum",
    )
    p.add_argument("--momentum-fraction", type=float, default=0.25)
    p.add_argument(
        "--momentum-snapshot",
        type=Path,
        default=None,
        help="megacap fundamentals snapshot for momentum (default: live catalog, not reproducible)",
    )
    p.add_argument("--momentum-top-n", type=int, default=7)
    p.add_argument("--momentum-cap", type=float, default=0.20)
    p.add_argument(
        "--momentum-universe",
        type=Path,
        default=None,
        help="CSV/newline symbol list for the momentum sleeve (default: validated MEGACAPS)",
    )
    p.add_argument(
        "--exposure",
        action="store_true",
        help="also print the fund exposure report (sector/sleeve/concentration)",
    )
    args = p.parse_args(argv)

    as_of = datetime.fromisoformat(args.as_of).date() if args.as_of else None
    if args.price_history and args.momentum_snapshot is None:
        print(
            "⚠️  momentum running off the LIVE catalog (NOT reproducible) — "
            "pass --momentum-snapshot to pin fundamentals",
            file=sys.stderr,
        )
    try:
        book, sectors = build_fund_book(
            as_of=as_of,
            snapshot=args.snapshot,
            prices=args.prices,
            universe_csv=args.universe_csv,
            sectors_csv=args.sectors_csv,
            db=args.db,
            core_fraction=args.core_fraction,
            hunt_fraction=args.hunt_fraction,
            momentum_fraction=args.momentum_fraction,
            max_name_weight=args.max_name_weight,
            price_history=args.price_history,
            momentum_snapshot=args.momentum_snapshot,
            momentum_universe=args.momentum_universe,
            momentum_top_n=args.momentum_top_n,
            momentum_cap=args.momentum_cap,
        )
    except (ValueError, FileNotFoundError) as e:
        raise SystemExit(str(e)) from e

    print(format_fund_book(book))
    if args.exposure:
        print()
        print(format_exposure(compute_exposure(book, sectors)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
