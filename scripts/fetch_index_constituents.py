"""Fetch current S&P 400 (mid) + S&P 600 (small) constituents from iShares
IJH/IJR holdings CSVs and write a universe CSV for the compounder scan.

Pure functions (parse/write) are unit-tested; network fetch lives in main().
"""

from __future__ import annotations

import csv
import io
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# iShares holdings CSV download endpoints (fileType=csv). May change/block;
# main() falls back to a curated list if these fail.
ISHARES_URLS = {
    "us-mid-cap": "https://www.ishares.com/us/products/239763/ishares-core-sp-midcap-etf/1467271812596.ajax?fileType=csv&fileName=IJH_holdings&dataType=fund",
    "us-small-cap": "https://www.ishares.com/us/products/239774/ishares-core-sp-small-cap-etf/1467271812596.ajax?fileType=csv&fileName=IJR_holdings&dataType=fund",
}


def parse_ishares_holdings(text: str) -> list[str]:
    """Extract equity tickers from an iShares holdings CSV.

    The file has a metadata preamble, then a header row whose first column is
    "Ticker", then holdings rows. Keep rows whose Asset Class == "Equity" and
    whose ticker is a real symbol.
    """
    lines = text.splitlines()
    header_idx = None
    for i, line in enumerate(lines):
        # header row starts with the Ticker column (quoted or not)
        first = line.split(",", 1)[0].strip().strip('"')
        if first == "Ticker":
            header_idx = i
            break
    if header_idx is None:
        return []
    reader = csv.DictReader(io.StringIO("\n".join(line.strip() for line in lines[header_idx:])))
    out: list[str] = []
    for row in reader:
        ticker = (row.get("Ticker") or "").strip()
        asset_class = (row.get("Asset Class") or "").strip()
        if asset_class == "Equity" and ticker and ticker not in {"-", "USD"}:
            out.append(ticker.upper())
    return out


UNIVERSE_NAME = "SP400_600_CURRENT"
UNIVERSE_HEADER = (
    "universe,symbol,market,start_date,end_date,source,confidence,asset_class,asset_subclass,role"
)


def write_universe_csv(mapping: dict[str, str], path: Path, *, run_date: date, source: str) -> int:
    """Write a universe CSV (existing schema). ``mapping`` is {ticker: asset_subclass}."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [UNIVERSE_HEADER]
    for ticker in sorted(mapping):
        subclass = mapping[ticker]
        lines.append(
            f"{UNIVERSE_NAME},{ticker},us,{run_date.isoformat()},,{source},"
            f"medium,equity,{subclass},risk"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(mapping)


def _fetch(url: str) -> str:
    import urllib.request

    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 RegimeResearch jjuni@local.research"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        return resp.read().decode("utf-8", errors="replace")


def main() -> None:
    import sys

    out_path = ROOT / "data" / "universes" / "sp400-600-current.csv"
    mapping: dict[str, str] = {}
    source = "ishares"
    for subclass, url in ISHARES_URLS.items():
        try:
            text = _fetch(url)
            tickers = parse_ishares_holdings(text)
            for t in tickers:
                mapping.setdefault(t, subclass)  # first file (mid) wins on dup
            print(f"{subclass}: {len(tickers)} tickers")
        except Exception as e:  # noqa: BLE001
            print(f"{subclass}: FETCH FAILED ({e})", file=sys.stderr)
    if not mapping:
        print(
            "No constituents fetched (iShares blocked?). Supply a CSV of tickers "
            "and re-run, or add a fallback source.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    n = write_universe_csv(mapping, out_path, run_date=date.today(), source=source)
    print(f"Wrote {out_path} ({n} unique tickers)")


if __name__ == "__main__":
    main()
