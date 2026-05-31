# Compounder Watchlist — Operating Runbook

_Research-only. Not investment advice. Last updated: 2026-05-31._

The compounder line is a **decision-support funnel** for concentrated, multi-year
("ten-bagger") investing. It ranks a universe of stocks under three archetypes and emits
per-name evidence dossiers. It is **NOT** a 10x predictor and **NOT** auto-traded — the final
5–10 picks and the hold/exit decisions are yours. The system's job is to make those bets
evidence-driven and to surface candidates you'd never screen by hand.

## What it is / isn't

- **Is:** a quantitative candidate generator + dossier. Narrows ~1,000 names to a ranked,
  archetype-tagged shortlist with the metrics behind each score.
- **Isn't:** a guarantee, a backtest of forward 10x returns (that validation — P5 — is not
  done; see Limitations), or a substitute for reading the business.

## Monthly procedure

Run as a single session (no concurrent catalog writers — DuckDB single-writer).

```bash
cd "/Users/jjuni/재무관리 모델/trader"

# 1. Pin the current fundamentals (do this whenever EDGAR data was refreshed).
.venv/bin/python scripts/snapshot_fundamentals.py fundamentals-$(date +%F)

# 2. (Periodic — quarterly is fine) refresh the universe + sectors if constituents drift.
.venv/bin/python scripts/fetch_index_constituents.py            # -> data/universes/sp400-600-current.csv
.venv/bin/python scripts/fetch_sectors.py --universe-csv data/universes/sp400-600-current.csv

# 3. Refresh latest prices (needed for valuation ratios — only the latest close is used).
.venv/bin/python scripts/fetch_latest_closes.py --universe-csv data/universes/sp400-600-current.csv

# 4. Scan (sector-aware, snapshot-pinned, reproducible).
.venv/bin/trader compounder-scan ALL \
  --universe-csv data/universes/sp400-600-current.csv \
  --snapshot data/snapshots/fundamentals-$(date +%F).csv \
  --sectors-csv data/sectors/sp400-600-current-sectors.csv \
  --as-of $(date +%F) --top-n 30 --no-fetch \
  --output out/compounder-scan-$(date +%F).md
```

To re-fetch EDGAR fundamentals for the whole universe (slower, ~15–20 min):
`.venv/bin/python scripts/sec_edgar_ingest.py --universe-csv data/universes/sp400-600-current.csv`

Confirm the scan header shows the snapshot name (reproducible), not a live read.

## Reading a dossier

Each entry: `### TICKER — <archetype> (<score>/100) [<sector>]` + a one-line rationale (top
driver Z-scores + flags) + a metric table.

- **Archetypes** (best-fit shown): **profitable compounder** (quality-led: ROIC, FCF margin,
  rising margins), **hypergrowth disruptor** (growth-led, profit optional if margins improve),
  **value / turnaround** (cheap on P/FCF·P/B, recovering margins).
- **Score (0–100):** cross-sectional rank within the universe (normal-CDF of a blended,
  ±3-winsorized Z). 50 ≈ universe average; >85 ≈ top-decile on that archetype's metrics. It is
  *relative*, not an absolute quality measure.
- **Flags:** `high-dilution` (share growth >5%/yr), `high-debt` (D/E >2), `margin-declining`,
  `negative-fcf`. Treat flags as deductions to verify.
- **Coverage gate:** a name needs ≥5 non-None metrics and ≥50% of an archetype's metric weight
  present to be scored — sparse-data names are excluded (intentional).
- **`[financials]` + "FCF excluded" note:** banks/insurers/REITs (SIC 6000–6799) are scored
  WITHOUT FCF ratios (meaningless for them) — judged on ROIC/growth/P-B. FCF values still show
  in the table for reference but are not in the score.

## Conviction checklist (the human layer the quant can't do)

Before adding a name to the concentrated book, the dossier is the START, not the answer:

- [ ] **Read the business** — what does it sell, to whom, and why is the TAM large/growing?
- [ ] **Durable moat?** — pricing power, switching costs, network effects (the dossier has no moat data).
- [ ] **Management/founder** — ownership, capital-allocation track record, incentives.
- [ ] **Verify the flags** — is `margin-declining` cyclical or structural? Is dilution funding growth or distress?
- [ ] **Reproduce the metrics** — spot-check ROIC/growth against the latest 10-K (EDGAR data can be sparse/lagged).
- [ ] **Valuation vs growth** — is the price paying for growth you believe is durable?
- [ ] **Position sizing** — concentrated ≠ reckless. Size to survive a 50% drawdown on any single name.

## Known limitations (do not omit when deciding)

1. **Not validated for forward 10x** — the funnel is built on quantitative *precursors* of past
   compounders, not a proven predictor. P5 (PIT historical hit-rate backtest) is not done.
2. **Survivorship** — the universe is *current* S&P 400+600 constituents. Good for a forward
   watchlist; a historical backtest on it would be survivorship-biased (delisted names absent).
3. **Data quality** — SEC `companyfacts` tags are inconsistent; the ingest now merges revenue
   tags across the ASC-606 transition (fix `21a44c3`), but some names still show `n/a` metrics
   → their scores lean on fewer signals. Read the dossier's `n/a` rows.
4. **Margin-trend winsorization** — per-period net margins are clipped to ±100% before the slope
   (fix `ea0371f`) to stop tiny-revenue-period artifacts (e.g. +4307%); a legitimate >100% margin
   period is also clipped (rare).
5. **Financials** — scored on ROIC/P-B/growth (FCF excluded); a cheap financial can still rank
   high legitimately, but sector-specific quirks (REIT FFO, bank net-interest) aren't fully modeled.
6. **Megacap universe** (`--pit-universe SP100_PIT_2008`) surfaces large compounders, not 10x
   candidates — use the S&P 400+600 universe for ten-bagger hunting.

## Roadmap to strengthen

- **P5** — PIT forward-return backtest (needs historical prices + delisting data): does a high
  compounder score actually precede outperformance?
- **Alt-data / qualitative** — insider buying (Form 4), low analyst coverage (undiscovered),
  institutional accumulation, news narrative → enrich the dossier's `alt_signals` hook.
- **Broaden universe** — Russell 2000 / micro-caps (where 10x more often starts).
- **P4 Korea** — DART OpenAPI for Korean mid/small (the engine is market-agnostic).
- **Sector-specific metrics** — proper bank/REIT/insurer valuation instead of FCF exclusion only.
