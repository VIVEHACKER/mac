# P2: US Mid/Small Universe Ingest — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reusable pipeline that ingests a current ~1,000-name S&P 400+600 (US mid/small) universe — constituents → SEC EDGAR fundamentals + latest price — so `trader compounder-scan` surfaces real ten-bagger candidates instead of megacaps.

**Architecture:** Three small scripts (constituent fetch/parse, generalized EDGAR ingest, batch latest-close) feed the existing catalog; then reuse `snapshot_fundamentals.py` + `trader compounder-scan`. Pure parse/transform/resolve functions are TDD-unit-tested; the network ingest is a runtime step verified by counts + a scan smoke. No compounder-engine code changes (it is already universe-agnostic).

**Tech Stack:** Python 3.12 stdlib (`csv`, `urllib`, `argparse`), `yfinance` (latest closes only, lazy-imported), existing `data.catalog` / `data.universe` / `data.models`, pytest.

Spec: `docs/superpowers/specs/2026-05-31-p2-universe-ingest-design.md`.

---

## File Structure

- Create `scripts/fetch_index_constituents.py` — `parse_ishares_holdings()` (pure) + `write_universe_csv()` (pure) + `main()` (network fetch IJH+IJR → universe CSV).
- Modify `scripts/sec_edgar_ingest.py` — add `resolve_tickers(args)` (pure) + argparse in `main()` (`--universe-csv` / `--tickers`; default = existing megacaps).
- Create `scripts/fetch_latest_closes.py` — `latest_close_bar()` (pure transform) + `main()` (batch `yf.download` → `put_bars`).
- Create `tests/test_scripts/test_fetch_index_constituents.py`
- Create `tests/test_scripts/test_sec_edgar_ingest.py`
- Create `tests/test_scripts/test_fetch_latest_closes.py`

Run tests: `.venv/bin/python -m pytest <path> -q`. Lint `.venv/bin/ruff check <file>`; types `.venv/bin/mypy <file>`. Tests import script modules as `from scripts.X import ...` (works — `scripts` is an importable namespace package in this repo; `paper_drill.py` already does `from scripts.aqr_ideal_walkforward import MEGACAPS`).

**Lazy-import rule:** `yfinance` and any network library must be imported INSIDE `main()` (not at module top), so the pure functions are importable in tests without pulling network deps.

---

## Task 1: iShares holdings parser

**Files:**
- Create: `scripts/fetch_index_constituents.py`
- Test: `tests/test_scripts/test_fetch_index_constituents.py`

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

from scripts.fetch_index_constituents import parse_ishares_holdings

SAMPLE = '''\
"iShares Core S&P Small-Cap ETF"
"Fund Holdings as of","May 30, 2026"
\x20
"Ticker","Name","Sector","Asset Class","Market Value"
"AAA","Alpha Industries","Industrials","Equity","123456.00"
"BBB","Beta Health","Health Care","Equity","98765.00"
"CCC","Gamma Tech","Information Technology","Equity","55555.00"
"-","CASH COLLATERAL","Cash and/or Derivatives","Cash","1000.00"
"USD","USD CASH","Cash and/or Derivatives","Cash","500.00"
'''


def test_parse_returns_only_equity_tickers():
    tickers = parse_ishares_holdings(SAMPLE)
    assert tickers == ["AAA", "BBB", "CCC"]


def test_parse_skips_preamble_and_non_equity():
    # cash/derivative rows and the metadata preamble must be excluded
    tickers = parse_ishares_holdings(SAMPLE)
    assert "USD" not in tickers
    assert "-" not in tickers


def test_parse_empty_or_headerless_returns_empty():
    assert parse_ishares_holdings("garbage\nno header here\n") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_scripts/test_fetch_index_constituents.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.fetch_index_constituents'`.

- [ ] **Step 3: Write minimal implementation**

```python
"""Fetch current S&P 400 (mid) + S&P 600 (small) constituents from iShares
IJH/IJR holdings CSVs and write a universe CSV for the compounder scan.

Pure functions (parse/write) are unit-tested; network fetch lives in main().
"""

from __future__ import annotations

import csv
import io
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
    reader = csv.DictReader(io.StringIO("\n".join(lines[header_idx:])))
    out: list[str] = []
    for row in reader:
        ticker = (row.get("Ticker") or "").strip()
        asset_class = (row.get("Asset Class") or "").strip()
        if asset_class == "Equity" and ticker and ticker not in {"-", "USD"}:
            out.append(ticker.upper())
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_scripts/test_fetch_index_constituents.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add scripts/fetch_index_constituents.py tests/test_scripts/test_fetch_index_constituents.py
git commit -m "feat(p2): iShares holdings parser (equity tickers)"
```

---

## Task 2: universe CSV writer + fetch main

**Files:**
- Modify: `scripts/fetch_index_constituents.py`
- Test: `tests/test_scripts/test_fetch_index_constituents.py`

- [ ] **Step 1: Write the failing test** (append)

```python
from datetime import date  # noqa: E402

from scripts.fetch_index_constituents import write_universe_csv  # noqa: E402


def test_write_universe_csv_schema(tmp_path):
    out = tmp_path / "u.csv"
    # write_universe_csv writes a {ticker: subclass} mapping; dedup is main()'s job.
    mapping = {"AAA": "us-mid-cap", "BBB": "us-small-cap"}
    n = write_universe_csv(mapping, out, run_date=date(2026, 5, 31), source="ishares")
    assert n == 2
    text = out.read_text(encoding="utf-8")
    header = text.splitlines()[0]
    assert header == (
        "universe,symbol,market,start_date,end_date,source,confidence,"
        "asset_class,asset_subclass,role"
    )
    assert "SP400_600_CURRENT,AAA,us,2026-05-31,,ishares,medium,equity,us-mid-cap,risk" in text
    assert "SP400_600_CURRENT,BBB,us,2026-05-31,,ishares,medium,equity,us-small-cap,risk" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_scripts/test_fetch_index_constituents.py::test_write_universe_csv_schema_and_dedup -q`
Expected: FAIL — ImportError for `write_universe_csv`.

- [ ] **Step 3: Write minimal implementation** (append to `scripts/fetch_index_constituents.py`)

```python
from datetime import date  # add to imports at top of file

UNIVERSE_NAME = "SP400_600_CURRENT"
UNIVERSE_HEADER = (
    "universe,symbol,market,start_date,end_date,source,confidence,"
    "asset_class,asset_subclass,role"
)


def write_universe_csv(
    mapping: dict[str, str], path: Path, *, run_date: date, source: str
) -> int:
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
```

(Note: `date.today()` in `main()` is fine — this is a script, not a workflow.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_scripts/test_fetch_index_constituents.py -q`
Expected: PASS. Then `.venv/bin/ruff check scripts/fetch_index_constituents.py` → clean.

- [ ] **Step 5: Commit**

```bash
git add scripts/fetch_index_constituents.py tests/test_scripts/test_fetch_index_constituents.py
git commit -m "feat(p2): universe CSV writer + iShares fetch main"
```

---

## Task 3: generalize EDGAR ingest to a ticker source

**Files:**
- Modify: `scripts/sec_edgar_ingest.py`
- Test: `tests/test_scripts/test_sec_edgar_ingest.py`

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

import argparse

from scripts.sec_edgar_ingest import resolve_tickers


def _args(**kw) -> argparse.Namespace:
    base = {"universe_csv": None, "tickers": None}
    base.update(kw)
    return argparse.Namespace(**base)


def test_resolve_from_tickers_flag():
    assert resolve_tickers(_args(tickers="aaa, bbb,CCC")) == ["AAA", "BBB", "CCC"]


def test_resolve_default_is_megacaps():
    from scripts.sec_edgar_ingest import TICKERS

    assert resolve_tickers(_args()) == TICKERS


def test_resolve_from_universe_csv(tmp_path):
    csv_path = tmp_path / "u.csv"
    csv_path.write_text(
        "universe,symbol,market,start_date,end_date,source,confidence,"
        "asset_class,asset_subclass,role\n"
        "SP400_600_CURRENT,AAA,us,2026-05-31,,ishares,medium,equity,us-mid-cap,risk\n"
        "SP400_600_CURRENT,BBB,us,2026-05-31,,ishares,medium,equity,us-small-cap,risk\n",
        encoding="utf-8",
    )
    assert resolve_tickers(_args(universe_csv=csv_path)) == ["AAA", "BBB"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_scripts/test_sec_edgar_ingest.py -q`
Expected: FAIL — ImportError for `resolve_tickers`.

- [ ] **Step 3: Write minimal implementation**

Add to imports at top of `scripts/sec_edgar_ingest.py`:

```python
import argparse
```

Add the resolver (place it above `main()`):

```python
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
```

Replace the top of `main()` so it parses args and iterates the resolved list. Change:

```python
def main() -> None:
    print("Loading SEC CIK map...")
```

to:

```python
def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest SEC EDGAR companyfacts fundamentals.")
    parser.add_argument("--universe-csv", type=Path, default=None,
                        help="Read tickers from a universe CSV (symbol column).")
    parser.add_argument("--tickers", default=None, help="Comma-separated tickers (overrides --universe-csv).")
    args = parser.parse_args()
    tickers = resolve_tickers(args)
    print(f"Ingesting {len(tickers)} tickers")
    print("Loading SEC CIK map...")
```

Then in the existing loop, replace every `TICKERS` reference with the local `tickers`:
- `for i, ticker in enumerate(TICKERS, 1):` → `for i, ticker in enumerate(tickers, 1):`
- `f"[{i:02d}/{len(TICKERS)}] ..."` (3 occurrences) → `len(tickers)`

Leave the module-level `TICKERS` list intact (the default).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_scripts/test_sec_edgar_ingest.py -q`
Expected: PASS (3 passed). Then `.venv/bin/ruff check scripts/sec_edgar_ingest.py` → clean.

- [ ] **Step 5: Commit**

```bash
git add scripts/sec_edgar_ingest.py tests/test_scripts/test_sec_edgar_ingest.py
git commit -m "feat(p2): generalize EDGAR ingest to --universe-csv/--tickers"
```

---

## Task 4: batch latest-close fetch

**Files:**
- Create: `scripts/fetch_latest_closes.py`
- Test: `tests/test_scripts/test_fetch_latest_closes.py`

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

from datetime import date

from scripts.fetch_latest_closes import latest_close_bar


def test_latest_close_bar_builds_pricebar():
    series = [(date(2026, 5, 27), 9.5), (date(2026, 5, 28), 10.25), (date(2026, 5, 29), None)]
    bar = latest_close_bar("AAA", series)
    assert bar is not None
    assert bar.symbol == "AAA"
    assert bar.close == 10.25  # last NON-None close
    assert bar.ts.date() == date(2026, 5, 28)
    assert bar.market == "us"


def test_latest_close_bar_none_when_no_valid_close():
    assert latest_close_bar("BBB", [(date(2026, 5, 28), None)]) is None
    assert latest_close_bar("CCC", []) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_scripts/test_fetch_latest_closes.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.fetch_latest_closes'`.

- [ ] **Step 3: Write minimal implementation**

```python
"""Fetch one recent close per ticker (the compounder scan only needs the latest
price for valuation ratios) and store it as a PriceBar. yfinance is imported
lazily inside main() so the pure transform is test-importable without it."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from datetime import date, datetime
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
        ts=datetime(d.year, d.month, d.day),
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
    closes = raw["Close"] if "Close" in raw else raw
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_scripts/test_fetch_latest_closes.py -q`
Expected: PASS. Then `.venv/bin/ruff check scripts/fetch_latest_closes.py` and `.venv/bin/mypy scripts/fetch_latest_closes.py` → clean.

- [ ] **Step 5: Commit**

```bash
git add scripts/fetch_latest_closes.py tests/test_scripts/test_fetch_latest_closes.py
git commit -m "feat(p2): batch latest-close fetch (PriceBar transform)"
```

---

## Task 5: runtime ingest + scan (operational)

**Files:** none (runtime execution + verification). This task is NOT unit-tested; it runs the pipeline and verifies by counts + a scan smoke. Run it as a single session (no concurrent ingest — DuckDB lock).

- [ ] **Step 1: Fetch constituents**

Run: `.venv/bin/python scripts/fetch_index_constituents.py`
Expected: `data/universes/sp400-600-current.csv` written with ~800–1,000 unique tickers.
If it exits 2 (iShares blocked): STOP and report to the controller — a fallback constituent source or an operator-supplied CSV is needed before continuing. Do not fabricate tickers.

- [ ] **Step 2: Ingest fundamentals (long-running; run in background)**

Run: `.venv/bin/python scripts/sec_edgar_ingest.py --universe-csv data/universes/sp400-600-current.csv`
Expected: ~10–20 min; per-ticker success/skip lines; final "Total records stored: N". Many small-caps will have partial or no companyfacts — those are logged and skipped (expected). Target: ≥ 70% of tickers store ≥ 1 record.

- [ ] **Step 3: Fetch latest closes**

Run: `.venv/bin/python scripts/fetch_latest_closes.py --universe-csv data/universes/sp400-600-current.csv`
Expected: "Stored latest close for N/M tickers" with N a large majority.

- [ ] **Step 4: Pin a fresh snapshot**

Run: `.venv/bin/python scripts/snapshot_fundamentals.py fundamentals-2026-05-31`
Expected: prints record/symbol counts + a new sha256 (now includes the mid/small names).

- [ ] **Step 5: Run the compounder scan**

Run:
```bash
.venv/bin/trader compounder-scan ALL \
  --universe-csv data/universes/sp400-600-current.csv \
  --snapshot data/snapshots/fundamentals-2026-05-31.csv \
  --as-of 2026-05-31 --top-n 30 --no-fetch \
  --output out/compounder-scan-sp400-600.md
```
Expected: exit 0; `out/compounder-scan-sp400-600.md` lists ~30 archetype-tagged candidates that include genuine mid/small names (not megacaps). Coverage-gated sparse names are correctly absent. Sanity-check a few dossiers for plausible metrics.

- [ ] **Step 6: Full gate + commit the universe CSV + sample output is gitignored**

Run:
```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check scripts/ engine/ trader/cli.py
.venv/bin/mypy scripts/fetch_index_constituents.py scripts/fetch_latest_closes.py scripts/sec_edgar_ingest.py
```
Expected: all pass/clean. (`out/` is gitignored, so the scan report stays local; `data/universes/sp400-600-current.csv` is NOT gitignored and was committed implicitly — confirm with `git status`.)

```bash
git add data/universes/sp400-600-current.csv
git commit -m "feat(p2): ingest S&P 400+600 universe; compounder candidates live"
```

---

## Notes for the implementer

- **Single ingest session only** — concurrent DuckDB writers lock (a prior session's background ingest caused exactly this). Don't run Task 5 steps 2/3 in parallel.
- **iShares may block** — if `fetch_index_constituents` exits 2, escalate; do not invent a ticker list. A fallback (Wikipedia S&P 400/600 tables) can be added as a follow-up, but get operator input first.
- **Idempotent** — re-running the EDGAR ingest is safe (`put_fundamentals` upserts), so a partial/interrupted run can be resumed by re-running.
- **Engine unchanged** — do not modify `engine/compounder*.py`. P2 is data only. If the scan reveals a NEW engine bug (not just sparse data), report it; don't fix the engine inside P2.
- **`date.today()`** is acceptable in these scripts (they are not Workflow scripts).
- Follow existing script patterns (`sys.path.insert(0, str(ROOT))`, `# noqa: E402` for post-path imports).
