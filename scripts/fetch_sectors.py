"""Classify universe tickers by SIC (SEC submissions API) into coarse sectors.

Only `financials` (SIC 6000-6799) changes scoring behavior (FCF metrics are
excluded for financials in engine.compounder); other groups are best-effort
display labels. Pure `sic_to_sector` is unit-tested; network fetch is in main().
"""

from __future__ import annotations

import csv
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def sic_to_sector(sic: int | None) -> str:
    """Map a SIC code to a coarse sector group. Only 'financials' affects scoring."""
    if sic is None:
        return "other"
    s = int(sic)
    if 6000 <= s <= 6799:
        return "financials"
    if (3570 <= s <= 3579) or (3670 <= s <= 3699) or (7370 <= s <= 7379):
        return "tech"
    if (2833 <= s <= 2836) or (3840 <= s <= 3851) or (8000 <= s <= 8099):
        return "healthcare"
    if (1300 <= s <= 1399) or (2900 <= s <= 2999):
        return "energy"
    if 4900 <= s <= 4999:
        return "utilities"
    if (1000 <= s <= 1499) or (2800 <= s <= 2899):
        return "materials"
    if 3400 <= s <= 3999:
        return "industrials"
    if (2000 <= s <= 2399) or (5000 <= s <= 5999) or (7000 <= s <= 8999):
        return "consumer"
    return "other"


def main() -> None:
    import argparse
    import json
    import urllib.request

    from data.universe import load_universe_members_csv
    from scripts.sec_edgar_ingest import load_cik_map

    parser = argparse.ArgumentParser(description="Fetch SIC sectors for a universe.")
    parser.add_argument("--universe-csv", type=Path, required=True)
    args = parser.parse_args()

    symbols = sorted({m.symbol.upper() for m in load_universe_members_csv(args.universe_csv)})
    cik_map = load_cik_map()
    out_dir = ROOT / "data" / "sectors"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.universe_csv.stem}-sectors.csv"

    rows: list[tuple[str, str, str]] = []
    for i, sym in enumerate(symbols, 1):
        cik = cik_map.get(sym.replace("-", ".").upper()) or cik_map.get(sym.upper())
        sic = None
        if cik is not None:
            try:
                url = f"https://data.sec.gov/submissions/CIK{cik:010d}.json"
                req = urllib.request.Request(
                    url, headers={"User-Agent": "RegimeResearch jjuni@local.research"}
                )
                with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
                    sub = json.load(r)
                raw_sic = sub.get("sic")
                sic = int(raw_sic) if raw_sic not in (None, "") else None
            except Exception as e:  # noqa: BLE001
                print(f"[{i}/{len(symbols)}] {sym}: ERROR {e}", file=sys.stderr)
        rows.append((sym, "" if sic is None else str(sic), sic_to_sector(sic)))
        time.sleep(0.13)
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["symbol", "sic", "sector"])
        w.writerows(rows)
    fin = sum(1 for _, _, sec in rows if sec == "financials")
    print(f"Wrote {out_path} ({len(rows)} tickers, {fin} financials)")


if __name__ == "__main__":
    main()
