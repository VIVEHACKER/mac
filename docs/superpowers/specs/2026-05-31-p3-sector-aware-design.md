# Design Spec — P3: Sector-Aware Scoring (financials FCF-artifact fix)

_Date: 2026-05-31. Status: approved (brainstorming). Research-only; not investment advice._

## 1. Purpose & scope

The P2 scan surfaced financials/insurance/REITs (FBRT, EQH, BANC) high in the rankings on
spurious FCF metrics — for banks and REITs, free-cash-flow-to-revenue and P/FCF are not
meaningful, so the compounder scorers inflate them. P3 makes scoring **sector-aware**: for
financial-sector names, the FCF-based metrics are excluded and the name is judged on the
metrics that apply (ROIC, growth, margin trend, P/B). This removes the artifact while keeping
legitimate financial value/compounder candidates in the running (no blanket exclusion).

In scope: SIC-based sector classification, a sector map for the universe, and sector-aware
metric handling in the compounder engine + CLI + dossier.

Out of scope: full GICS taxonomy, per-sector weight re-tuning (we only null invalid metrics,
not reweight), special handling for non-financial sectors, alt-data enrichment (insider /
coverage / news), `reinvestment_rate`/`net_cash`. The SP100 re-ingest (to fix P1's AAPL/MSFT
`n/a` after the extract_concept fix) is a separate one-off op, not part of this spec.

## 2. Components (isolated, single-responsibility)

1. **`scripts/fetch_sectors.py`** — classify each universe ticker by SIC.
   - Pure `sic_to_sector(sic: int | None) -> str`: maps a SIC code to a coarse sector group.
     Behavior-relevant rule: **SIC 6000–6799 → `"financials"`** (finance, insurance, real
     estate incl. REITs). Other ranges map to coarse display groups
     (`"tech"`, `"healthcare"`, `"industrials"`, `"consumer"`, `"energy"`, `"materials"`,
     `"utilities"`, `"other"`); only `"financials"` changes scoring behavior. `None` → `"other"`.
   - `main(--universe-csv)`: reuse `load_cik_map()` (from `sec_edgar_ingest`); per ticker fetch
     `https://data.sec.gov/submissions/CIK{cik:010d}.json` → `sic`; rate-limited (≤8/s);
     write `data/sectors/<stem>-sectors.csv` with header `symbol,sic,sector`. Skip-and-log
     on failure; idempotent.

2. **`engine/compounder.py`** — sector-aware metric nulling.
   - Module constant `SECTOR_INVALID_METRICS: dict[str, frozenset[str]] = {"financials":
     frozenset({"fcf_margin", "fcf_conversion", "pfcf"})}`.
   - `score_archetypes(universe, sectors: dict[str, str] | None = None)` — backward compatible
     (sectors defaults to None = no change). When a symbol's sector has invalid metrics, set
     those metric values to `None` in that symbol's metric dict BEFORE cross-sectional
     Z-scoring and archetype weighting. The existing per-archetype coverage gate then recomputes
     on the remaining present metrics (so a financial judged on ROIC/growth/margin_trend/P-B).
   - `rank_compounders(universe, top_n, sectors=None)` — threads `sectors` through.
   - No weight changes; no new archetype. Pure metric-availability adjustment.

3. **`trader/cli.py`** — `compounder-scan` gains `--sectors-csv PATH` (optional). When given,
   load `{symbol: sector}` and pass to `rank_compounders`. When absent, behavior is unchanged
   (sector-agnostic, P2 behavior).

4. **`engine/compounder_dossier.py`** — `Dossier` gains a `sector: str = "unknown"` field;
   `build_dossier(candidate, sector="unknown")`; markdown shows the sector and, for financials,
   a note that FCF-based metrics were excluded from scoring.

## 3. Data flow

```
SEC submissions API (sic per CIK) ─▶ fetch_sectors ─▶ data/sectors/sp400-600-sectors.csv
                                                              │
trader compounder-scan --sectors-csv ─▶ {symbol: sector} ─▶ rank_compounders(sectors)
        │                                                         │
        │   (financial symbols: fcf_margin/fcf_conversion/pfcf → None before Z/scoring)
        ▼                                                         ▼
   out/compounder-scan-*.md  ◀── dossiers (sector shown; FCF-excluded note for financials)
```

## 4. Error handling

- SEC submissions fetch failure / missing `sic` → log + skip (ticker absent from sectors CSV
  → treated as `"other"` at scan time, i.e., no metric nulling).
- `--sectors-csv` missing/unreadable → scan proceeds sector-agnostically with a warning.
- A financial name left with too few non-FCF metrics after nulling → the existing
  MIN_PRESENT_METRICS / per-archetype coverage gates exclude it (expected, not an error).

## 5. Testing (TDD)

- `sic_to_sector`: 6798→financials, 6021→financials, 6411→financials, 7372→tech, 2834→
  healthcare, 3721→industrials, None→other. Boundary: 5999→not financials, 6000→financials,
  6799→financials, 6800→not financials.
- sector-aware scoring: a small universe where a "financial" name has a huge (artifact)
  `fcf_margin`; assert that with `sectors={sym:"financials"}` its fcf_margin/fcf_conversion/
  pfcf are excluded — its profitable/value scores no longer reflect FCF (compare score with
  vs without the sector map; the FCF-driven inflation is gone) and a non-financial name is
  unaffected. Assert `rank_compounders(..., sectors=...)` threads through.
- dossier: `build_dossier(c, sector="financials")` → `Dossier.sector == "financials"` and the
  markdown contains the FCF-excluded note; default sector "unknown" renders without the note.
- CLI: `compounder-scan --sectors-csv <tmp>` runs and the output reflects sector handling
  (extend the existing CLI test fixture with a financial ticker + a sectors CSV).

## 6. Success criteria

- `data/sectors/sp400-600-sectors.csv` written for the universe (≥ a large majority classified).
- Re-running `compounder-scan --sectors-csv ...` on the S&P 400+600 snapshot demonstrably
  re-ranks financials by non-FCF metrics (FBRT/EQH/BANC scores reflect ROIC/P-B/growth, not
  inflated FCF); non-financial top names (TGTX/CYTK/AVAV/TDC…) are essentially unchanged.
- All new units TDD-covered; ruff + mypy clean; existing suite green (sector param is
  backward-compatible — P1/P2 tests unchanged).
