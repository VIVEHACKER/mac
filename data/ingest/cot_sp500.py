"""CFTC Commitments-of-Traders ingest for S&P 500 futures positioning.

A genuinely orthogonal data source to the price/vol signals: weekly net positioning of
non-commercials (large speculators — "trend / dumb money") vs commercials (hedgers —
"smart money") in S&P 500 Consolidated futures. Used by signals/cot_extreme.py.

Two layers, separated so the parse is testable without the network:
  * ``parse_cot_frame`` — pure: a raw cot_reports DataFrame → tidy weekly series.
  * ``fetch_cot_sp500`` — network: pulls year(s) via ``cot_reports`` and parses them.
    ``cot_reports.cot_year`` writes an ``annual.txt`` into the CWD as a side effect, so
    the fetch runs inside a TemporaryDirectory to keep the repo clean.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

import pandas as pd

MARKET = "S&P 500 Consolidated - CHICAGO MERCANTILE EXCHANGE"
_DATE_COL = "As of Date in Form YYYY-MM-DD"
_MARKET_COL = "Market and Exchange Names"
_NC_LONG = "Noncommercial Positions-Long (All)"
_NC_SHORT = "Noncommercial Positions-Short (All)"
_COMM_LONG = "Commercial Positions-Long (All)"
_COMM_SHORT = "Commercial Positions-Short (All)"
_OI = "Open Interest (All)"

REQUIRED_COLUMNS = (
    _DATE_COL,
    _MARKET_COL,
    _NC_LONG,
    _NC_SHORT,
    _COMM_LONG,
    _COMM_SHORT,
    _OI,
)


def parse_cot_frame(raw: pd.DataFrame, *, market: str = MARKET) -> pd.DataFrame:
    """Tidy weekly series for one market: index = report date, columns nc_net/comm_net/oi.

    ``nc_net`` = non-commercial long − short; ``comm_net`` = commercial long − short
    (the two are near-mirror images, OI aside). Sorted, de-duplicated on date.
    """
    missing = [c for c in REQUIRED_COLUMNS if c not in raw.columns]
    if missing:
        raise ValueError(f"COT frame missing columns: {missing}")
    rows = raw[raw[_MARKET_COL].astype(str).str.strip() == market].copy()
    if rows.empty:
        raise ValueError(f"no rows for market {market!r}")
    out = pd.DataFrame(
        {
            "date": pd.to_datetime(rows[_DATE_COL]),
            "nc_net": pd.to_numeric(rows[_NC_LONG]) - pd.to_numeric(rows[_NC_SHORT]),
            "comm_net": pd.to_numeric(rows[_COMM_LONG]) - pd.to_numeric(rows[_COMM_SHORT]),
            "open_interest": pd.to_numeric(rows[_OI]),
        }
    )
    out = out.dropna().drop_duplicates("date").sort_values("date").set_index("date")
    return out


def fetch_cot_sp500(start_year: int, end_year: int, *, market: str = MARKET) -> pd.DataFrame:
    """Fetch + parse COT for [start_year, end_year] inclusive (network). Tidy weekly series.

    A year that fails to download is skipped with no row contribution (the caller sees a
    shorter series, never a partial/corrupt one); at least one year must succeed.
    """
    import cot_reports as cot

    frames: list[pd.DataFrame] = []
    with tempfile.TemporaryDirectory() as tmp:
        prev = Path.cwd()
        os.chdir(tmp)  # cot_year writes annual.txt to CWD — isolate it
        try:
            for year in range(start_year, end_year + 1):
                with contextlib.suppress(Exception):
                    frames.append(cot.cot_year(year=year, cot_report_type="legacy_fut"))
        finally:
            os.chdir(prev)
    if not frames:
        raise RuntimeError(f"COT fetch produced no data for {start_year}-{end_year}")
    return parse_cot_frame(pd.concat(frames, ignore_index=True), market=market)
