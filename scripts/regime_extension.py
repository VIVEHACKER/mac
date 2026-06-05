"""Back-extend the momentum edge to pre-2008 regimes (dotcom bubble + bust).

The validated backtest is 2008-2026 — essentially one long megacap-bull regime. If the
momentum edge is a regime artifact rather than a real premium, it should weaken or vanish
OUT of that sample. This runs the same price-only cross-sectional 12-1 momentum harness
(engine.replication.momentum_replication) on US megacaps back to 1995, split by regime,
and asks the decisive question: does the edge survive 1995-2008 (genuinely out of the
current backtest window, including the 2000-2002 dotcom bust)?

Reuses the cross-market harness (no new engine code). Fundamentals are NOT used: pre-2009
PIT fundamentals are unavailable (EDGAR XBRL starts ~2009), and momentum is the
transferable, data-available leg.

⚠️ Survivorship: only CURRENT megacaps have price history back to 1995, so this tilts
toward eventual winners. It cannot prove the absolute edge, but a NEGATIVE/weak pre-2008
result would still be a strong regime-dependence red flag (survivorship would, if
anything, flatter momentum among survivors).

Usage:
    python -m scripts.regime_extension
    python -m scripts.regime_extension --refetch
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.replication import momentum_replication  # noqa: E402
from scripts.aqr_ideal_walkforward import MEGACAPS  # noqa: E402
from scripts.cross_market_replication import _report_region  # noqa: E402

SNAP_DIR = ROOT / "data" / "snapshots"
CACHE = SNAP_DIR / "cross-market-US-1995.csv"

# Regime windows. 1995-2008 is the headline OOS test (entirely before the validated
# 2008-2026 sample); 2008-2024 is the in-sample regime for side-by-side comparison.
REGIMES = [
    ("US 1995-2024 (full)", "1995-01-01", "2024-12-31"),
    ("US 1995-2008 (OOS vs current backtest)", "1995-01-01", "2008-01-01"),
    ("US 1995-2000 (dotcom bubble)", "1995-01-01", "2000-12-31"),
    ("US 2000-2003 (dotcom bust)", "2000-01-01", "2003-12-31"),
    ("US 2008-2024 (in-sample regime)", "2008-01-01", "2024-12-31"),
]


def _load(start: str, end: str, refetch: bool) -> pd.DataFrame:
    if CACHE.exists() and not refetch:
        return pd.read_csv(CACHE, index_col=0, parse_dates=True)
    import yfinance as yf

    raw = yf.download(MEGACAPS, start=start, end=end, auto_adjust=True, progress=False)
    close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw
    close = close.dropna(axis=1, how="all")
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    close.to_csv(CACHE)
    return close


def main() -> int:
    parser = argparse.ArgumentParser(description="Pre-2008 regime back-extension of momentum.")
    parser.add_argument("--top-n", type=int, default=7)
    parser.add_argument("--refetch", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "out" / "regime-extension.md")
    args = parser.parse_args()

    prices = _load("1995-01-01", "2024-12-31", args.refetch)

    out: list[str] = ["# Momentum edge — pre-2008 regime back-extension", ""]
    out.append(
        "Same price-only 12-1 momentum rule across regimes. The decisive line is "
        "'US 1995-2008': a significant excess THERE (out of the 2008-2026 backtest) is "
        "real-edge evidence; a flat/negative one flags a regime artifact."
    )
    out.append("")

    for label, start, end in REGIMES:
        window = prices.loc[pd.Timestamp(start) : pd.Timestamp(end)]
        avail = int((window.notna().sum() > 260).sum())
        try:
            result = momentum_replication(window, region=label, top_n=args.top_n)
            out += _report_region(result)
            out.append(f"- symbols with >1y data in window: {avail}")
        except ValueError as exc:
            out.append(f"## {label}")
            out.append(f"- skipped: {exc}")
        out.append("")

    out.append("## Honest reading")
    out.append("- ⚠️ Survivorship: current megacaps only -> early windows hold eventual winners;")
    out.append("  absolute numbers are inflated. Compare SIGN/significance across regimes, and")
    out.append("  read the 1995-2000 era as 'momentum among survivors', not the true universe.")
    out.append("- Momentum's documented crash is 2009 (already inside the 2008-2026 sample), not")
    out.append("  2000-2002; this extension mainly adds the bubble + a different macro regime.")
    out.append("- Full V+M+Q cannot be back-extended (no pre-2009 PIT fundamentals) — this tests")
    out.append("  the momentum leg only, the transferable core.")

    report = "\n".join(out)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report + "\n", encoding="utf-8")
    print(report)
    print(f"\nwrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
