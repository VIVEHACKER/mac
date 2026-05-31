"""Ingest SEC EDGAR XBRL companyfacts as PIT fundamentals for 30 mega caps.

Stores one FundamentalRecord per (symbol, period_end, asof_ts=filed_date).
Source: https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.catalog import MarketDataCatalog  # noqa: E402
from data.models import FundamentalRecord  # noqa: E402

USER_AGENT = "RegimeResearch jjuni@local.research"

TICKERS = [
    "ACN",
    "ADBE",
    "AET",
    "ALL",
    "AMAT",
    "AMT",
    "AXP",
    "BAX",
    "BKNG",
    "BLK",
    "BMY",
    "BNY",
    "CAT",
    "CL",
    "CMCSA",
    "COF",
    "COP",
    "CVS",
    "DE",
    "DHR",
    "DOW",
    "DUK",
    "EMR",
    "ESRX",
    "FDX",
    "GD",
    "GEV",
    "GOOG",
    "HON",
    "INTU",
    "ISRG",
    "LIN",
    "LMT",
    "LOW",
    "LRCX",
    "MCD",
    "MDLZ",
    "MDT",
    "MU",
    "NEE",
    "NKE",
    "NOW",
    "PFE",
    "PLTR",
    "PM",
    "QCOM",
    "RTX",
    "SBUX",
    "SCHW",
    "SO",
    "SPG",
    "TGT",
    "TMO",
    "TMUS",
    "TWX",
    "TXN",
    "UBER",
    "UNP",
    "UPS",
    "USB",
]

CONCEPT_TAGS: dict[str, list[str]] = {
    "revenue": [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
    ],
    "net_income": ["NetIncomeLoss"],
    "equity": [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ],
    "shares": [
        "CommonStockSharesOutstanding",
        "EntityCommonStockSharesOutstanding",
    ],
    "eps": ["EarningsPerShareDiluted", "EarningsPerShareBasic"],
    "debt_lt": ["LongTermDebt", "LongTermDebtNoncurrent"],
    "debt_st": ["ShortTermBorrowings", "DebtCurrent"],
}

UNIT_FOR: dict[str, str] = {
    "revenue": "USD",
    "net_income": "USD",
    "equity": "USD",
    "ocf": "USD",
    "capex": "USD",
    "shares": "shares",
    "eps": "USD/shares",
    "debt_lt": "USD",
    "debt_st": "USD",
}


def fetch_json(url: str, retries: int = 3, backoff: float = 1.5) -> dict:
    last_exc: Exception | None = None
    for i in range(retries):
        try:
            req = Request(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
            with urlopen(req, timeout=30) as r:
                return json.load(r)
        except (HTTPError, URLError, TimeoutError) as e:
            last_exc = e
            time.sleep(backoff * (i + 1))
    raise RuntimeError(f"fetch failed: {url} :: {last_exc}")


def load_cik_map() -> dict[str, int]:
    payload = fetch_json("https://www.sec.gov/files/company_tickers.json")
    out: dict[str, int] = {}
    for row in payload.values():
        if not isinstance(row, dict):
            continue
        t = str(row.get("ticker") or "").upper()
        cik = row.get("cik_str")
        if t and isinstance(cik, int):
            out[t] = cik
    return out


def extract_concept(facts: dict, tag_choices: list[str], unit: str) -> dict[str, tuple[float, str]]:
    """Return dict[period_end_str -> (value, filed_date_str)] from earliest filing."""
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    dei = facts.get("facts", {}).get("dei", {})
    result: dict[str, tuple[float, str]] = {}
    for tag in tag_choices:
        entries = None
        for source in (us_gaap, dei):
            if tag in source:
                entries = source[tag].get("units", {}).get(unit, [])
                break
        if not entries:
            continue
        for e in entries:
            form = e.get("form", "")
            if not form.startswith(("10-K", "10-Q")):
                continue
            end = e.get("end")
            filed = e.get("filed")
            val = e.get("val")
            if end is None or filed is None or val is None:
                continue
            existing = result.get(end)
            if existing is None or filed < existing[1]:
                result[end] = (float(val), filed)
    return result


def build_records(ticker: str, cik: int) -> list[FundamentalRecord]:
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
    facts = fetch_json(url)

    extracted: dict[str, dict[str, tuple[float, str]]] = {
        key: extract_concept(facts, tags, UNIT_FOR[key]) for key, tags in CONCEPT_TAGS.items()
    }

    all_periods: set[str] = set()
    for d in extracted.values():
        all_periods.update(d.keys())

    records: list[FundamentalRecord] = []
    for period_end_str in sorted(all_periods):
        try:
            period_end = date.fromisoformat(period_end_str)
        except ValueError:
            continue

        rev = extracted["revenue"].get(period_end_str)
        ni = extracted["net_income"].get(period_end_str)
        eq = extracted["equity"].get(period_end_str)
        ocf = extracted["ocf"].get(period_end_str)
        capex = extracted["capex"].get(period_end_str)
        shares = extracted["shares"].get(period_end_str)
        eps = extracted["eps"].get(period_end_str)
        dlt = extracted["debt_lt"].get(period_end_str)
        dst = extracted["debt_st"].get(period_end_str)

        # Need at least revenue or net_income or equity to be a valid period
        if rev is None and ni is None and eq is None:
            continue

        filed_candidates = [x[1] for x in (rev, ni, eq, ocf, capex, shares, eps) if x is not None]
        if not filed_candidates:
            continue
        filed_str = max(filed_candidates)
        try:
            asof = datetime.fromisoformat(filed_str)
        except ValueError:
            continue

        fcf = None
        if ocf is not None and capex is not None:
            # CapEx in SEC reports is positive cash outflow -> subtract
            fcf = ocf[0] - capex[0]

        total_debt = None
        if dlt is not None and dst is not None:
            total_debt = dlt[0] + dst[0]
        elif dlt is not None:
            total_debt = dlt[0]
        elif dst is not None:
            total_debt = dst[0]

        records.append(
            FundamentalRecord(
                symbol=ticker,
                market="us",
                period_end=period_end,
                asof_ts=asof,
                revenue=rev[0] if rev else None,
                net_income=ni[0] if ni else None,
                free_cash_flow=fcf,
                total_equity=eq[0] if eq else None,
                total_debt=total_debt,
                shares_out=shares[0] if shares else None,
                eps=eps[0] if eps else None,
                source="sec_edgar:companyfacts",
            )
        )
    return records


def resolve_tickers(args: argparse.Namespace) -> list[str]:
    """Ticker source: --tickers > --universe-csv > default megacap TICKERS."""
    if getattr(args, "tickers", None):
        return [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    if getattr(args, "universe_csv", None):
        from data.universe import load_universe_members_csv

        members = load_universe_members_csv(args.universe_csv)
        # dedup, preserve first-seen order
        seen: dict[str, None] = {}
        for m in members:
            seen.setdefault(m.symbol.upper(), None)
        return list(seen)
    return TICKERS


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest SEC EDGAR companyfacts fundamentals.")
    parser.add_argument(
        "--universe-csv",
        type=Path,
        default=None,
        help="Read tickers from a universe CSV (symbol column).",
    )
    parser.add_argument(
        "--tickers", default=None, help="Comma-separated tickers (overrides --universe-csv)."
    )
    args = parser.parse_args()
    tickers = resolve_tickers(args)
    print(f"Ingesting {len(tickers)} tickers")
    print("Loading SEC CIK map...")
    cik_map = load_cik_map()
    print(f"Loaded {len(cik_map)} tickers")

    catalog = MarketDataCatalog()
    total = 0
    for i, ticker in enumerate(tickers, 1):
        sec_ticker = ticker.replace("-", ".").upper()
        cik = cik_map.get(sec_ticker) or cik_map.get(ticker.upper())
        if cik is None:
            print(f"[{i:02d}/{len(tickers)}] {ticker}: NO CIK")
            continue
        try:
            records = build_records(ticker, cik)
            catalog.put_fundamentals(records)
            total += len(records)
            print(f"[{i:02d}/{len(tickers)}] {ticker}: CIK={cik}, stored {len(records)} records")
        except Exception as e:
            print(f"[{i:02d}/{len(tickers)}] {ticker}: ERROR {e}")
        time.sleep(0.15)  # SEC fair-use rate limit (~10 req/sec)

    print(f"\nDONE. Total records stored: {total}")


if __name__ == "__main__":
    main()
