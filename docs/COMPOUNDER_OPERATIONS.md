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
- **Isn't:** a guarantee or a forward-return predictor, or a substitute for reading the
  business. P5 (forward-return validation) is now **done** and found NO established
  forward edge — see "Forward-return validation" below. Treat the ranking as a screen.

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

1. **No validated forward edge (P5 done)** — the funnel is built on quantitative *precursors*
   of past compounders, and the forward-return validation found NO reliable edge (`best_score`
   mean Spearman IC −0.031 across 8 dates × 3 horizons; top-30 underperformed the universe). It
   is a **screen**, not a return predictor. See "Forward-return validation" below + the full
   record (incl. the quality-drag confound) in `docs/COMPOUNDER_VALIDATION.md`.
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

## Forward-return validation (P5 — done) → full record: `docs/COMPOUNDER_VALIDATION.md`

PIT replay at 8 as-of dates (2012–2019) × horizons {3y,5y,7y}, scored by Spearman rank IC and
quintile spread, then a 5-lens adversarial audit and a P0 gross-profitability follow-up.
**Bottom line: the funnel has no *validated* forward edge** — `best_score` mean IC ≈ −0.034
(negative at every horizon; top-30 watchlist underperformed the universe).

**P0 update (the key finding):** the drag was the quality family AS MEASURED — net_margin
(−0.087), NI-ROIC (−0.061) — but **Novy-Marx GROSS profitability (GP/assets) is POSITIVE and
consistent (≈+0.04, ~21/23 windows)**, and a QARP composite (GP/assets + cheap value) is ≈+0.07.
So "quality anti-predicts" was substantially a **net-margin metric-definition artifact**: the
fix is to re-measure quality (net→gross), NOT to drop the quality tilt. The gross edge is still
modest (z≈1.0 at N≈2–3) → promising, not yet validated.

**Operating consequence:** keep the funnel as a screen; do **not** change `_WEIGHTS` yet. The
path to a real edge (re-tool `profitable_compounder` to gross profitability, build a QARP
composite, validate strictly OOS, reconstruct PIT membership) is the action plan in
`docs/COMPOUNDER_VALIDATION.md`.

Re-run (regenerate the snapshot first, then):
```bash
SNAP=data/snapshots/fundamentals-$(date +%F).csv
.venv/bin/python scripts/compounder_forward_validation.py --snapshot $SNAP  # -> out/compounder-forward-validation.md
.venv/bin/python scripts/compounder_factor_ic.py          --snapshot $SNAP  # -> out/compounder-factor-ic.md
```

## Roadmap to strengthen

- **P5 follow-ups (the validated path to an edge)** — see the action plan in
  `docs/COMPOUNDER_VALIDATION.md`: (1) add gross profitability (GP/total_assets) and re-test
  quality the literature-correct way; (2) pre-register a QARP composite and validate strictly
  out-of-sample (hold-out + walk-forward + Russell 2000 + cost haircut); (3) reconstruct PIT
  index membership to remove survivorship. Do NOT change `_WEIGHTS` before these pass.
- **Alt-data / qualitative** — insider buying (Form 4), low analyst coverage (undiscovered),
  institutional accumulation, news narrative → enrich the dossier's `alt_signals` hook.
- **Broaden universe** — Russell 2000 / micro-caps (where 10x more often starts).
- **P4 Korea** — DART OpenAPI for Korean mid/small (the engine is market-agnostic).
- **Sector-specific metrics** — proper bank/REIT/insurer valuation instead of FCF exclusion only.
