"""Fetch one recent close per ticker (the compounder scan only needs the latest
price for valuation ratios) and store it as a PriceBar. yfinance is imported
lazily inside main() so the pure transform is test-importable without it."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.models import PriceBar  # noqa: E402


def latest_close_bar(symbol: str, series: Sequence[tuple[date, float | None]]) -> PriceBar | None:
    """Build a PriceBar from the last NON-None (date, close) in ``series``."""
    last: tuple[date, float] | None = None
    for d, close in series:
        if close is not None:
            last = (d, float(close))
    if last is None:
        return None
    d, close = last
    return PriceBar(
        symbol=symbol,
        market="us",
        source_symbol=symbol,
        freq="1d",
        ts=d,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=0.0,
        currency="USD",
        source="yfinance",
    )


def main() -> None:
    import argparse

    import pandas as pd
    import yfinance as yf

    from data.catalog import MarketDataCatalog
    from data.universe import load_universe_members_csv

    parser = argparse.ArgumentParser(description="Fetch latest close per universe ticker.")
    parser.add_argument("--universe-csv", type=Path, required=True)
    args = parser.parse_args()

    symbols = sorted({m.symbol.upper() for m in load_universe_members_csv(args.universe_csv)})
    print(f"Fetching latest closes for {len(symbols)} tickers...")
    raw = yf.download(symbols, period="5d", auto_adjust=True, progress=False)
    closes = raw.get("Close", raw)
    catalog = MarketDataCatalog()
    stored = 0
    for sym in symbols:
        if sym not in closes.columns:
            continue
        series = [
            (ts.date() if hasattr(ts, "date") else ts, (None if pd.isna(v) else float(v)))
            for ts, v in closes[sym].items()
        ]
        bar = latest_close_bar(sym, series)
        if bar is not None:
            catalog.put_bars([bar])
            stored += 1
    print(f"Stored latest close for {stored}/{len(symbols)} tickers")


if __name__ == "__main__":
    main()
