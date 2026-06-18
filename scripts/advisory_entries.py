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


def _fetch(symbols: list[str], market: str, start: str, end: str) -> dict[str, list[PriceBar]]:
    import FinanceDataReader as fdr  # noqa: N813

    currency = _CURRENCY.get(market, "")
    out: dict[str, list[PriceBar]] = {}
    for sym in symbols:
        try:
            df = fdr.DataReader(sym, start, end)
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


def format_bands(bands: list[AdvisoryBand], *, market: str) -> str:
    lines = [
        "# Advisory entry/stop/target — RISK FRAMING ONLY (not a validated signal)",
        "",
        f"market={market} | selection is YOURS (surge/chart picking measured ~0 edge); these are "
        "ATR risk levels for manual execution on Toss.",
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
    parser = argparse.ArgumentParser(description="Advisory entry/stop/target for a watchlist.")
    parser.add_argument("--symbols", required=True, help="Comma-separated tickers (your picks).")
    parser.add_argument("--market", choices=["kr", "us"], default="kr")
    parser.add_argument("--start", default="2025-06-01")
    parser.add_argument("--end", default="2026-05-31")
    parser.add_argument("--atr-window", type=int, default=14)
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        print("no symbols given")
        return 2
    bars_by = _fetch(symbols, args.market, args.start, args.end)
    picks = [(s, args.market) for s in symbols]
    bands = advisory_bands_for(picks, bars_by, BandConfig(atr_window=args.atr_window))
    print(format_bands(bands, market=args.market))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
