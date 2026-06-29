"""Evaluate a single ticker against the validated IDEAL universe.

Reuses the EXACT data the validated IDEAL walk-forward line uses (pinned price +
fundamentals snapshots over MEGACAPS), so a single-ticker recommendation is just the
cross-sectional AQR ranking sliced to one name + its fair-value band + laddered entry.

Usage:
    python -m scripts.evaluate_ticker AAPL
    python -m scripts.evaluate_ticker NVDA --asof 2026-05-30
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.models import PriceBar  # noqa: E402
from data.price_snapshot import read_price_snapshot  # noqa: E402
from scripts.aqr_ideal_walkforward import MEGACAPS, lookup_pit, prefetch  # noqa: E402
from valuation.recommendation import (  # noqa: E402
    evaluate_ticker,
    format_evaluation,
    load_validated_strategy,
)

DEFAULT_PRICES = ROOT / "data" / "snapshots" / "prices-ideal-2026-06-27.csv"
DEFAULT_FUNDAMENTALS = ROOT / "data" / "snapshots" / "fundamentals-2026-06-01-gp.csv"


def _bars_from_prices(
    prices: pd.DataFrame, symbols: list[str], asof: date
) -> dict[str, list[PriceBar]]:
    """Build {symbol: [PriceBar]} up to ``asof`` from a snapshot of close prices.

    The snapshot stores close only, so OHLC collapse to close; ATR then reads as the
    close-to-close range, which is the right volatility proxy for the entry ladder.
    """

    bars: dict[str, list[PriceBar]] = {}
    for symbol in symbols:
        if symbol not in prices.columns:
            continue
        series = prices[symbol].dropna()
        rows: list[PriceBar] = []
        for ts, close in series.items():
            bar_date = pd.Timestamp(ts).date()
            if bar_date > asof:
                break
            close_f = float(close)
            rows.append(
                PriceBar(
                    symbol=symbol,
                    market="us",
                    source_symbol=symbol,
                    ts=bar_date,
                    open=close_f,
                    high=close_f,
                    low=close_f,
                    close=close_f,
                    volume=0.0,
                )
            )
        if rows:
            bars[symbol] = rows
    return bars


def load_universe(
    prices_path: Path,
    fundamentals_path: Path,
    asof: date | None = None,
    *,
    extra_symbols: tuple[str, ...] = (),
) -> tuple[dict[str, list[PriceBar]], dict, datetime]:
    """Load (bars_by_symbol, fundamentals_by_symbol, as_of_dt) from pinned snapshots.

    Shared by the single-ticker and full-universe scan entry points so both read the
    EXACT data the validated IDEAL line uses (reproducible, content-verified).
    """

    prices = read_price_snapshot(prices_path, verify=True)
    if asof is None:
        asof = pd.Timestamp(prices.index.max()).date()
    as_of_dt = datetime.combine(asof, datetime.min.time())

    symbols = list(dict.fromkeys([*MEGACAPS, *(symbol.upper() for symbol in extra_symbols)]))
    bars = _bars_from_prices(prices, symbols, asof)

    fund_cache = prefetch(None, snapshot_path=fundamentals_path)
    fundamentals: dict = {}
    for symbol, records in fund_cache.items():
        pit = lookup_pit(records, as_of_dt)
        if pit is not None:
            fundamentals[symbol.upper()] = pit
    return bars, fundamentals, as_of_dt


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate a ticker vs the validated IDEAL universe."
    )
    parser.add_argument("ticker", help="ticker symbol, e.g. AAPL")
    parser.add_argument("--asof", help="evaluation date YYYY-MM-DD (default: latest in snapshot)")
    parser.add_argument("--prices", type=Path, default=DEFAULT_PRICES)
    parser.add_argument("--fundamentals", type=Path, default=DEFAULT_FUNDAMENTALS)
    parser.add_argument("--strategy", default=None, help="strategy id (default: config default)")
    args = parser.parse_args()

    asof = datetime.strptime(args.asof, "%Y-%m-%d").date() if args.asof else None
    strategy = load_validated_strategy(args.strategy)
    # Include the queried ticker even if it is not a MEGACAP, so out-of-universe names
    # still surface a price/entry view (with AVOID + capped confidence).
    bars, fundamentals, as_of_dt = load_universe(
        args.prices, args.fundamentals, asof, extra_symbols=(args.ticker.upper(),)
    )

    result = evaluate_ticker(
        ticker=args.ticker,
        bars_by_symbol=bars,
        fundamentals_by_symbol=fundamentals,
        strategy=strategy,
        asof_ts=as_of_dt,
    )
    print(format_evaluation(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
