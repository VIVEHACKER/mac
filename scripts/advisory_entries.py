"""Advisory entry/stop/target levels for a watchlist — for MANUAL execution on Toss.

Runnable glue over engine.advisory. IMPORTANT framing (read it):
  - This does NOT pick stocks. Surge/chart selection measured ~0 edge (see STRATEGIES.md), so
    the SELECTION is YOURS (the tickers you pass) — or, for US, the validated AQR rank.
  - It only frames RISK: ATR-derived entry / stop / target + reward:risk, so your manual entries
    and stops are disciplined and consistent. ATR bands do not predict; they size risk.

KR/US daily OHLCV via FinanceDataReader (free, no KRX login). Example:
  python -m scripts.advisory_entries --symbols 005930,000660,373220 --market kr
  python -m scripts.advisory_entries --symbols AAPL,MSFT,NVDA --market us
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.models import PriceBar  # noqa: E402
from engine.advisory import AdvisoryBand, BandConfig, advisory_bands_for  # noqa: E402

_CURRENCY = {"kr": "KRW", "us": "USD"}


def _aqr_us_picks(top_n: int) -> list[tuple[str, str]]:
    """Top-N US picks from the VALIDATED AQR rank (value+momentum+quality) on the pinned megacap
    universe — the only selection in this repo with a measured (modest, fragile) edge. Returns
    (symbol, 'us') in rank order; bands are then attached from fresh OHLCV in main()."""
    if top_n < 1:  # a negative slice would silently select ~the whole universe (Codex P2)
        return []
    from datetime import datetime

    from data.price_snapshot import read_price_snapshot
    from scripts.aqr_ideal_grid import DEFAULT_PRICES, DEFAULT_SNAPSHOT
    from scripts.aqr_ideal_walkforward import MEGACAPS, build_pricebars, lookup_pit, prefetch
    from strategies.factor_aqr import rank_aqr_factors

    prices = read_price_snapshot(DEFAULT_PRICES, verify=True)
    fund_cache = prefetch(None, snapshot_path=DEFAULT_SNAPSHOT)
    last = prices.index.max()
    as_of = datetime(last.year, last.month, last.day)
    bars_by_sym, fund_by_sym = {}, {}
    for sym in MEGACAPS:
        fund = lookup_pit(fund_cache.get(sym, []), as_of)
        if fund is None:
            continue
        bars = build_pricebars(prices, sym, last)
        if bars:
            fund_by_sym[sym.upper()] = fund
            bars_by_sym[sym] = bars
    scores = rank_aqr_factors(bars_by_sym, fund_by_sym, lookback=126)
    return [(s.symbol, "us") for s in scores[:top_n]]


def _fetch(
    symbols: list[str], market: str, start: str, end: str | None
) -> dict[str, list[PriceBar]]:
    import FinanceDataReader as fdr  # noqa: N813

    currency = _CURRENCY.get(market, "")
    out: dict[str, list[PriceBar]] = {}
    for sym in symbols:
        try:
            # end=None -> fetch to the latest available bar (avoid a stale hard-coded cap).
            df = fdr.DataReader(sym, start) if end is None else fdr.DataReader(sym, start, end)
        except Exception:
            continue
        if df is None or df.empty or "Close" not in df.columns:
            continue
        bars = [
            PriceBar(
                symbol=sym,
                market=market,
                source_symbol=sym,
                ts=idx.date(),
                open=float(getattr(r, "Open", r.Close)),
                high=float(getattr(r, "High", r.Close)),
                low=float(getattr(r, "Low", r.Close)),
                close=float(r.Close),
                volume=float(getattr(r, "Volume", 0.0) or 0.0),
                currency=currency,
            )
            for idx, r in df.iterrows()
            if r.Close > 0
        ]
        if bars:
            out[sym] = bars
    return out


def format_bands(
    bands: list[AdvisoryBand], *, market: str, selection: str = "your watchlist"
) -> str:
    lines = [
        "# Advisory entry/stop/target — RISK FRAMING (not a chart/surge prediction)",
        "",
        f"market={market} | selection: {selection} | these are ATR risk levels for manual "
        "execution on Toss — surge/chart picking measured ~0 edge, so only the AQR selection "
        "(if used) carries a measured edge; the bands frame risk, they do not predict.",
        "",
        "| symbol | close | entry | stop | target | R:R | ATR |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    if not bands:
        lines.append("| (no scorable picks — too little history or zero ATR) | | | | | | |")
    for b in bands:
        lines.append(
            f"| {b.symbol} | {b.close:,.2f} | {b.entry:,.2f} | {b.stop:,.2f} | "
            f"{b.target:,.2f} | {b.reward_risk:.2f} | {b.atr:,.2f} |"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Advisory entry/stop/target for picks.")
    parser.add_argument("--symbols", help="Comma-separated tickers (your watchlist).")
    parser.add_argument(
        "--from-aqr",
        action="store_true",
        help="Auto-select picks from the VALIDATED US AQR rank instead of --symbols (US only).",
    )
    parser.add_argument("--top", type=int, default=7, help="Number of AQR picks (--from-aqr).")
    parser.add_argument("--market", choices=["kr", "us"], default="kr")
    parser.add_argument("--start", default="2025-01-01", help="History start (lookback for ATR).")
    parser.add_argument("--end", default=None, help="History end; default = latest available.")
    parser.add_argument("--atr-window", type=int, default=14)
    args = parser.parse_args()

    if args.from_aqr:
        if args.market != "us":
            print(
                "--from-aqr is US-only (the validated AQR strategy is US megacap); use --market us"
            )
            return 2
        if args.top < 1:
            print("--top must be >= 1")
            return 2
        picks = _aqr_us_picks(args.top)
        selection = f"validated US AQR top-{args.top}"
    else:
        if not args.symbols:
            print("provide --symbols TICKERS or --from-aqr (US validated selection)")
            return 2
        picks = [(s.strip(), args.market) for s in args.symbols.split(",") if s.strip()]
        selection = "your watchlist"
    if not picks:
        print("no picks resolved")
        return 2
    symbols = [p[0] for p in picks]
    bars_by = _fetch(symbols, args.market, args.start, args.end)
    bands = advisory_bands_for(picks, bars_by, BandConfig(atr_window=args.atr_window))
    print(format_bands(bands, market=args.market, selection=selection))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
