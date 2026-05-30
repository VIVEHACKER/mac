"""Fetch current S&P 400 (mid) + S&P 600 (small) constituents from iShares
IJH/IJR holdings CSVs and write a universe CSV for the compounder scan.

Falls back to Wikipedia constituent lists if iShares returns HTML instead of CSV.

Pure functions (parse/write) are unit-tested; network fetch lives in main().
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# iShares holdings CSV download endpoints (fileType=csv). May change/block;
# main() falls back to Wikipedia constituent lists if these fail.
ISHARES_URLS = {
    "us-mid-cap": "https://www.ishares.com/us/products/239763/ishares-core-sp-midcap-etf/1467271812596.ajax?fileType=csv&fileName=IJH_holdings&dataType=fund",
    "us-small-cap": "https://www.ishares.com/us/products/239774/ishares-core-sp-small-cap-etf/1467271812596.ajax?fileType=csv&fileName=IJR_holdings&dataType=fund",
}

# Wikipedia fallback URLs for each subclass
WIKI_URLS = {
    "us-mid-cap": "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies",
    "us-small-cap": "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies",
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


def parse_wikipedia_constituents(html: str) -> list[str]:
    """Extract equity tickers from a Wikipedia S&P 400/600 constituents page.

    Finds the first ``<table class="wikitable sortable ...">`` whose header row
    contains a "Symbol" column, then extracts the ticker from that column for
    every data row.  Returns a deduped, order-preserving list of uppercased
    tickers (dots preserved, e.g. "BRK.B"; whitespace stripped).
    """
    # Find all wikitable blocks
    table_pat = re.compile(
        r'<table[^>]*class="[^"]*wikitable[^"]*"[^>]*>(.*?)</table>',
        re.DOTALL | re.IGNORECASE,
    )
    row_pat = re.compile(r"<tr[^>]*>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
    th_pat = re.compile(r"<th[^>]*>(.*?)</th>", re.DOTALL | re.IGNORECASE)
    td_pat = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL | re.IGNORECASE)
    tag_pat = re.compile(r"<[^>]+>")
    a_pat = re.compile(r"<a[^>]*>(.*?)</a>", re.DOTALL | re.IGNORECASE)

    def _strip_tags(s: str) -> str:
        return tag_pat.sub("", s).strip()

    for table_match in table_pat.finditer(html):
        body = table_match.group(1)
        rows = row_pat.findall(body)
        if not rows:
            continue

        # Locate the header row and find the "Symbol" column index
        header_cells = th_pat.findall(rows[0])
        if not header_cells:
            continue
        symbol_idx: int | None = None
        for idx, cell in enumerate(header_cells):
            if _strip_tags(cell).strip().lower() == "symbol":
                symbol_idx = idx
                break
        if symbol_idx is None:
            continue

        # Collect tickers from data rows
        seen: set[str] = set()
        tickers: list[str] = []
        for row in rows[1:]:
            cells = td_pat.findall(row)
            if symbol_idx >= len(cells):
                continue
            cell_html = cells[symbol_idx]
            # Prefer <a> link text, fall back to plain cell text
            a_match = a_pat.search(cell_html)
            raw = a_match.group(1) if a_match else cell_html
            ticker = _strip_tags(raw).upper()
            if ticker and ticker not in seen:
                seen.add(ticker)
                tickers.append(ticker)
        return tickers

    return []


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
    used_wiki: set[str] = set()  # subclasses that fell back to Wikipedia

    for subclass, url in ISHARES_URLS.items():
        tickers: list[str] = []
        try:
            text = _fetch(url)
            tickers = parse_ishares_holdings(text)
            print(f"{subclass}: {len(tickers)} tickers (iShares)")
        except Exception as e:  # noqa: BLE001
            print(f"{subclass}: iShares FETCH FAILED ({e})", file=sys.stderr)

        if not tickers:
            # iShares yielded nothing (blocked or returned HTML) — try Wikipedia
            wiki_url = WIKI_URLS.get(subclass)
            if wiki_url:
                try:
                    wiki_html = _fetch(wiki_url)
                    tickers = parse_wikipedia_constituents(wiki_html)
                    print(f"{subclass}: {len(tickers)} tickers (Wikipedia fallback)")
                    used_wiki.add(subclass)
                except Exception as e:  # noqa: BLE001
                    print(f"{subclass}: Wikipedia FETCH FAILED ({e})", file=sys.stderr)

        for t in tickers:
            mapping.setdefault(t, subclass)  # first file (mid) wins on dup

    if not mapping:
        print(
            "No constituents fetched from iShares or Wikipedia. Check network access and re-run.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    # Determine source label
    if used_wiki:
        source = "ishares+wikipedia" if len(used_wiki) < len(ISHARES_URLS) else "wikipedia"
    else:
        source = "ishares"

    n = write_universe_csv(mapping, out_path, run_date=date.today(), source=source)
    print(f"Wrote {out_path} ({n} unique tickers)")


if __name__ == "__main__":
    main()
