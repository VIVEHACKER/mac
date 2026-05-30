# Design Spec — P2: US Mid/Small Universe Ingest (compounder candidates)

_Date: 2026-05-31. Status: approved (brainstorming). Research-only; not investment advice._

## 1. Purpose & scope

P1 delivered the compounder-quality engine but the only ingested universe is megacaps
(S&P 100), which structurally cannot 10x. P2 populates a **current ~1,000-name S&P 400 (mid)
+ S&P 600 (small)** universe with PIT fundamentals + a latest price, so `trader
compounder-scan` surfaces *real* ten-bagger candidates.

P2 is a **data-acquisition step**: no compounder-engine code changes (the engine is already
universe-agnostic). It produces a reusable, market-agnostic ingest pipeline (foundation for
P4 Korea and periodic re-ingest).

**Forward-watchlist framing:** P2 uses *current* index constituents. Survivorship bias is a
P5 (historical hit-rate) concern, NOT a P2 concern — generating today's candidate list
correctly uses today's universe. True PIT/reconstitution membership is explicitly out of P2.

**Key simplification:** `compounder-scan` needs only the *latest* close per name (for the
valuation ratios market_cap / P/E / P/FCF / P/S / P/B). All time-series work is on
fundamentals. So price acquisition is a single batched recent-close fetch, not 1,000
full-history ingests. The bottleneck is the ~1,000 EDGAR companyfacts pulls.

## 2. Components (isolated, single-responsibility)

1. **`scripts/fetch_index_constituents.py`** — fetch current constituents and write a
   universe CSV.
   - Primary source: iShares **IJH** (S&P 400 mid) + **IJR** (S&P 600 small) holdings CSVs
     (publicly downloadable). Parse ticker column, dedup, drop non-equity rows (cash, etc.).
   - Fallback: if the iShares download is blocked (WAF/format change), fall back to the
     Wikipedia "List of S&P 400/600 companies" tables; if all sources fail, exit with a clear
     message asking the operator to supply a constituent CSV.
   - Output: `data/universes/sp400-600-current.csv` in the existing universe schema
     (`universe,symbol,market,start_date,end_date,source,confidence,asset_class,asset_subclass,role`)
     with `universe=SP400_600_CURRENT`, `market=us`, `start_date` = run date,
     `source` = the actual source used, `asset_subclass` = `us-mid-cap`/`us-small-cap`.

2. **Generalized `sec_edgar_ingest.py`** — make the ticker source a parameter.
   - Add argparse: `--universe-csv PATH` (read symbols from a universe CSV) and/or
     `--tickers "A,B,C"`; default keeps the existing hardcoded megacap `TICKERS` for
     backward compatibility.
   - Reuse `load_cik_map()` (SEC `company_tickers.json`) + `build_records()` unchanged.
   - Rate-limit to ≤ 8 requests/sec (SEC fair-use; the existing `User-Agent` header stays).
   - Per ticker: on HTTP/parse failure, log and skip (do not abort the run). Idempotent:
     re-running re-`put_fundamentals` (upsert) so a resumed/partial run is safe.
   - Progress logging every N tickers with success/skip counts.

3. **`scripts/fetch_latest_closes.py`** — populate one recent bar per ticker.
   - One batched `yf.download(tickers, period~"5d")`; for each ticker take the last valid
     close → `PriceBar` → `catalog.put_bars`.
   - Tickers with no price data are logged and skipped.

4. **Reuse (no new code):**
   - `scripts/snapshot_fundamentals.py <name>` — pin the post-ingest fundamentals.
   - `trader compounder-scan ALL --universe-csv data/universes/sp400-600-current.csv
     --snapshot data/snapshots/<name>.csv --as-of <date> --top-n 30
     --output out/compounder-scan-sp400-600.md`.

## 3. Data flow

```
iShares IJH+IJR CSV ─▶ fetch_index_constituents ─▶ data/universes/sp400-600-current.csv
                                                          │
                          ┌───────────────────────────────┴───────────────┐
                          ▼                                                 ▼
   sec_edgar_ingest --universe-csv ─▶ fundamentals_q        fetch_latest_closes ─▶ bars
                          │                                                 │
                          └──────────────▶ snapshot_fundamentals ◀──────────┘
                                                  │
                                     trader compounder-scan ─▶ out/compounder-scan-sp400-600.md
```

## 4. Error handling & feasibility risks

| Risk | Mitigation |
|---|---|
| iShares CSV blocked / format change | Fallback to Wikipedia lists; final fallback = operator-supplied CSV with a clear error. |
| ~1,000 EDGAR pulls (≤8/s, ~10–20 min) | Background job; rate-limited; skip-and-log per-ticker failures; idempotent re-run. |
| Sparse/inconsistent small-cap EDGAR tags | Existing coverage gates (MIN_PRESENT_METRICS=5, per-archetype 0.5) exclude under-covered names — expected, not an error. |
| Concurrent ingest → DuckDB lock | Single ingest session only; document it. |
| Financial-sector FCF artifacts | Surfaced via dossier flags; GICS sector filter needs sector data → deferred to P3. |
| yfinance batch partial failure | Per-ticker skip+log; scan still runs on names that have both fundamentals and a price. |

## 5. Testing (TDD)

- `fetch_index_constituents`: unit-test the **CSV parser** on a saved iShares-format fixture
  (header rows + holdings rows + a trailing cash/non-equity row) → asserts the correct ticker
  set, dedup, and non-equity exclusion. Network fetch itself is not unit-tested.
- `sec_edgar_ingest`: unit-test the **ticker-source selection** — given `--universe-csv` it
  reads symbols from the CSV; given `--tickers` it parses the list; given neither it uses the
  default megacap list. Mock/skip the network `build_records` call (test only ticker
  resolution, not live HTTP).
- `fetch_latest_closes`: unit-test the **"last valid close → PriceBar"** transform on a small
  in-memory frame fixture (no network).
- The live ingest + scan is a runtime step verified by record/ticker counts and a scan smoke,
  not a unit test.

## 6. Out of scope

True PIT / survivorship-free membership (P5), Korea / DART (P4), GICS sector classification &
filter (P3), full price history, any compounder-engine logic change.

## 7. Success criteria

- `data/universes/sp400-600-current.csv` written with ~1,000 unique US mid/small tickers.
- `fundamentals_q` gains records for a large majority of the universe (failures logged; a
  realistic target is ≥ 70% of tickers with ≥ 1 record, given small-cap tag inconsistency).
- A fresh fundamentals snapshot pinned.
- `trader compounder-scan` on the new universe runs deterministically and produces
  `out/compounder-scan-sp400-600.md` with archetype-tagged candidates that include genuine
  small/mid names (not just the megacaps).
- Constituent-parser, ticker-source, and close-transform units are TDD-covered; ruff + mypy
  clean; no regression in the existing suite.
