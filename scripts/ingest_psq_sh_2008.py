"""
Ingest PSQ/SH 2008-2015 data from Yahoo Finance into DuckDB.
These inverse ETFs were missing pre-2016 data, causing GFC hedge to fall back to cash.
"""

import datetime
import json
import urllib.request

import duckdb

DB_PATH = "data/store/trader.duckdb"


def fetch_yf(sym: str, start: str, end: str) -> list[dict]:
    """Fetch daily bars from Yahoo Finance v8 API."""
    start_ts = int(datetime.datetime.strptime(start, "%Y-%m-%d").timestamp())
    end_ts = int(datetime.datetime.strptime(end, "%Y-%m-%d").timestamp())
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
        f"?adjusted=true&interval=1d&period1={start_ts}&period2={end_ts}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())

    result = data.get("chart", {}).get("result", [])
    if not result:
        print(f"  WARNING: No data for {sym}")
        return []

    r = result[0]
    timestamps = r.get("timestamp", [])
    indicators = r.get("indicators", {})
    adjclose_list = indicators.get("adjclose", [{}])[0].get("adjclose", [])
    quote = indicators.get("quote", [{}])[0]
    opens = quote.get("open", [])
    highs = quote.get("high", [])
    lows = quote.get("low", [])
    closes = quote.get("close", [])
    volumes = quote.get("volume", [])

    rows = []
    for i, ts in enumerate(timestamps):
        if ts is None:
            continue
        date = datetime.datetime.utcfromtimestamp(ts).date()
        o = opens[i] if i < len(opens) else None
        h = highs[i] if i < len(highs) else None
        lo = lows[i] if i < len(lows) else None
        c = adjclose_list[i] if i < len(adjclose_list) else (closes[i] if i < len(closes) else None)
        v = volumes[i] if i < len(volumes) else None
        if any(x is None for x in [o, h, lo, c]):
            continue
        rows.append({"ts": date, "open": o, "high": h, "low": lo, "close": c, "volume": v or 0})

    return rows


def main():
    con = duckdb.connect(DB_PATH)

    for sym in ["PSQ", "SH"]:
        print(f"\n=== {sym} ===")
        rows = fetch_yf(sym, "2008-01-01", "2015-12-31")
        print(f"  Fetched {len(rows)} bars from Yahoo Finance")

        if not rows:
            print("  SKIP: no data")
            continue

        source_url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?adjusted=true"
        inserted = 0
        skipped = 0
        for row in rows:
            existing = con.execute(
                "SELECT count(*) FROM bars WHERE symbol=? AND ts=? AND freq='1d'", [sym, row["ts"]]
            ).fetchone()[0]
            if existing > 0:
                skipped += 1
                continue
            con.execute(
                """INSERT INTO bars (symbol, market, source_symbol, freq, ts, open, high, low, close, volume, currency, source, ingested_at)
                   VALUES (?, 'us', ?, '1d', ?, ?, ?, ?, ?, ?, 'USD', ?, NOW())""",
                [
                    sym,
                    sym,
                    row["ts"],
                    row["open"],
                    row["high"],
                    row["low"],
                    row["close"],
                    row["volume"],
                    source_url,
                ],
            )
            inserted += 1

        r = con.execute(
            f"SELECT min(ts), max(ts), count(*) FROM bars WHERE symbol='{sym}' AND freq='1d'"
        ).fetchone()
        print(f"  Inserted: {inserted}, Skipped (dup): {skipped}")
        print(f"  Final range: {r}")

    con.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
