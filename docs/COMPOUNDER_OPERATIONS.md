# Compounder Watchlist — Operating Runbook

_Research-only. Not investment advice. Last updated: 2026-06-06._

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

**Operating consequence:** the funnel stays a SCREEN with its ORIGINAL `profitable_compounder`
weights. A `gross_profitability` 0.15 ADD was tried then **REVERTED**: the held-out-time gate
(`scripts/compounder_heldout_oos.py`, pinned prices, as-of 2020/2021/2022) did NOT confirm it
(held-out gross_quality IC −0.014, size-partial −0.020 — did not survive a size control). The
value looked strong in those 3 held-out windows but a full-period value confirmation
(`scripts/compounder_value_validation.py`, 11 windows, pinned prices) found even VALUE is NOT
uniformly robust — negative in the 2016-19 growth regime and mostly a small-cap/size effect
after a size control (size-partial IC +0.032 vs raw +0.113). **Terminal conclusion: no single
factor (gross / net-quality / value) survives regime+size+sector controls on this universe → no
`_WEIGHTS` tilt is warranted; the funnel stays an evidence-backed SCREEN with original weights.**
The one durable finding is that net-margin quality anti-predicts, with no validated replacement.
A real long-only backtest (`scripts/compounder_backtest.py`, pinned prices, costs, buy-and-hold)
confirms it in portfolio terms: the top-30 funnel does not beat an equal-weight of the same
rank-eligible universe risk-adjusted (Sharpe 0.76 vs 0.86; +0.3%/yr CAGR but higher vol/DD, 7/13
years = noise) — no reliable selection alpha. Survivorship is audited
(`scripts/survivorship_audit.py`): the relative-excess verdict is insulated from the shared
survivor inflation (residual works against the funnel); full removal needs CRSP (delisted prices
unavailable free — 12/12 probe tickers return 0 bars). Full record + action plan:
`docs/COMPOUNDER_VALIDATION.md`. Runs: `scripts/compounder_oos_validation.py`,
`scripts/compounder_heldout_oos.py`, `scripts/compounder_value_validation.py` (held-out/value use
pinned prices), `scripts/compounder_backtest.py`, `scripts/survivorship_audit.py`.

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

---

## 코어 바스켓 (Core Basket) — 장기 앵커 슬리브

The compounder funnel above is a *screen* (candidate generator). The **core basket** turns that
screen into an actual long sleeve: a diversified, equal-weight portfolio that is the fund's
**durable anchor (~35% of the 50/50 barbell)**. Engine: `engine/core_basket.py`; driver:
`scripts/core_basket.py`; tests: `tests/test_engine/test_core_basket.py`.

### Honest framing (read before changing anything)

This basket makes **NO factor-alpha claim**. The terminal validation (`docs/COMPOUNDER_VALIDATION.md`,
`engine/compounder.py:22-32`) found that in this mid/small-cap survivor universe over 3–5y horizons
**no single factor robustly predicts forward returns** after regime+size+sector controls — and the one
robust finding is that **net-margin / ROIC *reverse*-predict** (high-quality-by-those-metrics
underperforms). So the engine:

1. **EXCLUDES `net_margin` and `roic` from ranking** (they were the reverse-predictors).
2. Tilts only toward the directionally-supported-if-modest **value** (low `ps`/`pb` cheapness,
   `w_value=0.6`) + Novy-Marx **gross profitability** `GP/assets` (`w_gp=0.4`).
3. **Holds theses** — winners are not trimmed until the hard cap.

Survival comes from **breadth (12–15 names) + an 8% per-name hard cap + a per-sector count cap +
zero leverage**, not from a predictive edge; breadth substitutes for vol-targeting. Asymmetric
upside is the *hunt* basket's job; the validated 12-1 momentum edge is the separate **IDEAL** line.
This is the boring, durable anchor — do not turn it into an alpha claim.

### Selection (`select_core_basket`)

- **Screen (eligibility, sector-aware):** drop names with no fundamentals, `<5` present metrics
  (`MIN_PRESENT_METRICS`), no `ps` *and* no `pb` (value anchor required); non-financials also need
  `fcf_margin ≥ 0` and `debt/equity ≤ 3.0`; everyone needs `share_growth ≤ 15%` (no serial
  dilution). Sector-invalid metrics (e.g. FCF/GP for financials) are nulled before screening (same
  `SECTOR_INVALID_METRICS` as the compounder).
- **Rank (percentile, NOT z-score):** each factor is mapped to its cross-sectional **percentile
  rank in `[0,1]`** before blending — dispersion-invariant and bounded, so the blend weights
  genuinely control influence. `composite = (w_value·cheapness + w_gp·gp_pct) / Σ|w|` where
  `cheapness` = mean of the percentile ranks of negated `ps`/`pb` (cheaper ⇒ higher percentile) and
  `gp_pct` = percentile rank of `GP/assets`. Defaults `w_value=0.6`, `w_gp=0.4`. A z-score blend was
  deliberately **rejected**: GP's fat right tail rails at the clip ceiling and silently makes the
  composite GP-led rather than value-led; percentiles make "value-led" actually true. `net_margin`
  and `roic` are never consulted (a test enforces this). Ties share the average rank; a lone present
  value maps to the 0.5 neutral midpoint.
- **Sector cap:** at most `max_per_sector=4` names per sector (≈31% of a 13-name basket) so no
  single sector dominates the anchor; if the cap starves the basket below `target_n`, the highest-
  ranked overflow names backfill (breadth beats an empty slot). Unknown-sector names are uncapped.
- **Weight:** equal-weight `1/n` clamped to the **8% hard cap** (`max_weight=0.08`, `target_n=13`).
  Under equal weighting the cap is all-or-none: with `n ≥ 1/cap` each name gets `1/n` (sums to 1.0);
  with fewer names each gets the cap and the remainder is sleeve cash. If the basket ends up with
  `<3` holdings the engine emits a (non-fatal) `MIN_HOLDINGS_WARN` — the anchor is degenerate, check
  universe/screen coverage.
- **Output:** `CoreBasket` (holdings + `as_of`, `universe_size`, `eligible_count`, `excluded`
  reasons). Each `CoreHolding` carries `weight`, `composite` (the `[0,1]` percentile-space blend), a
  0–100 `display_score` (`= composite·100`, a percentile-space score, **not** a probability),
  `cheapness_pct`, `gp_pct`, sector, `flags`, and a Korean rationale (저평가/고평가 split at the 0.5
  percentile midpoint).

**Known property (not a bug):** financials have `GP/assets` nulled (sector-invalid), so they rank on
**cheapness alone** — cheap-leveraged names (e.g. mortgage REITs) can surface near the top. That is
correct value-screen behavior: forcing a quality floor here would re-introduce the very `net_margin`/
`roic` tilt the validation found *reverse*-predicts. It is contained by the per-sector count cap, the
transparent `flags` (high-debt / margin-declining), and the fact that this is a screen, not auto-trading
— the human confirms before any buy.

### Rebalance (`rebalance_core_basket`) — thesis-hold

Given current `held` weights, the freshly-ranked `target` basket, and the set of still-`eligible`
symbols:

1. **Keep** held names still in `eligible`; **drop** any that fell out of the screen
   (`"스크린 탈락 (thesis break)"`) — exit is screen-failure, not a price stop.
2. **Fill** remaining slots from fresh top-ranked names (`"신규 편입"`).
3. **Let winners run:** a held name whose grown weight exceeds equal-weight keeps that weight
   (pre-cap); others reset to equal-weight. `_cap_redistribute` then normalizes to sum 1.0 with an
   iterative 8% hard cap (`trim_to_cap` → `"캡 초과 → 8% 축소"`).

Emits `RebalanceAction`s (`add` / `hold` / `trim_to_cap` / `drop`) for the audit trail.

### Driver (PIT, snapshot-pinned)

```bash
cd "/Users/jjuni/재무관리 모델/trader-fund"
# Omit --as-of to use the snapshot's latest date (reproducible PIT). Do NOT pass $(date +%F):
# the pinned price snapshot ends a few days back, so a future as_of raises the coverage ValueError.
.venv/bin/python scripts/core_basket.py
# defaults: --target-n 13 --max-weight 0.08 --w-value 0.6 --w-gp 0.4
#           --snapshot data/snapshots/fundamentals-*.csv --prices data/snapshots/prices-*.csv
# For a historical cut, pass an --as-of WITHIN the price snapshot's coverage, e.g. --as-of 2024-06-28.
```

Mirrors `scripts/compounder_forward_validation.py`'s snapshot/PIT assembly. **Single cutoff for both
legs:** `--as-of` resolves to one `effective` date applied to *both* fundamentals (`asof_ts ≤
effective`) and price (last close on/before `effective`); when omitted it defaults to the price
snapshot's latest date — never "all fundamentals + latest price", which would mix periods and leak. An
`as_of` outside the price snapshot's coverage raises `ValueError` rather than silently truncating. A
name needs `≥2` in-window fundamental records and a positive in-window close to enter the universe.
Snapshots are content-hash pinned (`verify=True` fails loud on drift); the CSVs are gitignored (only
manifests tracked), so on a clean checkout regenerate via `scripts/snapshot_fundamentals.py` /
`scripts/snapshot_prices.py` or pass `--snapshot` / `--prices`. Prints `format_core_basket` — a report
whose header restates the honest framing so the no-alpha caveat travels with every run.

---

## 반순환 브릿지 (Countercyclical Bridge) — 폭락매수 dry-powder 슬리브

The fund holds idle reserve cash (the discretionary half of the active 50% + any cap overflow). The
**countercyclical bridge** turns a budget-capped slice of that dry powder into a *rule-based*
deployment policy: when the market has fallen **and** the core anchor is cheap, it buys **more of the
existing core basket** in tranches. Engine: `engine/countercyclical_bridge.py`; driver:
`scripts/countercyclical_bridge.py`; tests: `tests/test_engine/test_countercyclical_bridge.py`
(+ `tests/test_scripts/test_countercyclical_bridge_driver.py` for PIT). Spec:
`docs/superpowers/specs/2026-06-20-countercyclical-bridge-design.md`.

### Honest framing (read before changing anything)

The bridge makes **NO market-timing alpha claim.** It does not predict bottoms. It invents no signal
and picks **no new names** — it scales the *already-value-screened* core weights. Two guards keep it
survival-first, not a leveraged bet:

1. **The value gate is an AND, not an OR.** Drawdown alone (price fell) is a falling-knife trap —
   earnings may have fallen further, leaving it still expensive. Deployment is **0 whenever the value
   gate is closed**, regardless of drawdown depth.
2. **It reuses fund_book's rails.** The bridge is just another `SleeveTarget` composed by
   `assemble_fund_book` (see the fund-book spec). The fund-level **8% per-name hard cap** and
   **Σ(fractions) ≤ 1.0 zero-leverage** guard bound the combined core+bridge exposure — a name already
   at the cap overflows to reserve, never to leverage.

### The barbell budget (user policy)

long 50% = core 35% + hunt 15%; active 50% = momentum/IDEAL 25% + **bridge dry powder
`bridge_budget` = 15%** + discretionary reserve 10%. `bridge_budget` is the **maximum** fund fraction
the bridge may ever deploy — an explicit, overridable default.

### Deployment (`compute_deployment`)

- **Market drawdown** (`market_drawdown`): peak-to-last over a trailing, PIT-sliced price window
  (`(peak − last) / peak`, clamped `[0,1]`; the driver passes the last `--window=252` closes ≤ `as_of`).
- **Value gate** (`default_value_gate`): `True` iff the **median** of the core holdings' present
  `cheapness_pct` ≥ `0.55` (None ignored; empty / all-None ⇒ closed, conservative). Approach B (a
  market-P/E / earnings-yield macro gate) is a documented **deferred** additive AND-gate — kept out to
  preserve the no-new-data, deterministic-test property.
- **Step ladder** (`DEFAULT_LADDER`, deeper crash ⇒ more deployed): drawdown `<10%` → 0 (hold dry
  powder); `[10,20%)` → `1/3`; `[20,30%)` → `2/3`; `≥30%` → `3/3` of `bridge_budget`.
  `deployed_fraction = bridge_budget × ladder_fraction(drawdown) × [gate open]`, invariant
  `0 ≤ deployed_fraction ≤ bridge_budget` (test-enforced over a 0→60% sweep). Fail-closed on
  out-of-range `budget` or a non-ascending / non-monotone ladder.
- **Output** (`bridge_sleeve_target`): `SleeveTarget("bridge", deployed_fraction, core_weights)` — the
  bridge's sleeve-relative weights **are** the core's weights. A core name then sums
  `core 0.35×w + bridge dep×w` in `assemble_fund_book`, capped at 8% (overflow → reserve, no
  redistribution). Returned even at `deployed_fraction = 0` ("armed, not deployed").

### Driver (PIT)

```bash
cd "/Users/jjuni/재무관리 모델/trader-fund"
.venv/bin/python scripts/countercyclical_bridge.py --as-of 2024-06-28 --book
# defaults: --bridge-budget 0.15 --value-threshold 0.55 --window 252
#           --market-csv data/snapshots/spy-history.csv  (date,close; gitignored like the other snapshots)
# Prints format_deployment (drawdown / gate / tranche / deployed); --book also assembles
# [core(0.35), hunt(0.15), bridge(dep)] via fund_book and prints format_fund_book.
```

Single `as_of` for all legs; the market series is PIT-sliced (`> as_of` rows dropped) and the core/hunt
assemblers enforce their own cutoff. The report header restates the honest framing so the no-alpha
caveat travels with every run.

---

## 모멘텀 / IDEAL 슬리브 (Momentum Sleeve) — 액티브 절반의 검증된 레그

Unlike the core / hunt / bridge sleeves (which make **no** alpha claim), the momentum sleeve **carries
the project's one validated edge**: 12-1 mega-cap AQR momentum — direction-robust across regimes,
**+8.15%/yr walk-forward but +size fragile** (PBO 0.39; significant excess US-only, see the trader-fund
memory). It is the active-half **momentum leg (~25% of the fund)**. Engine:
`engine/momentum_basket.py`; weights: `engine/momentum_weights.py`; wired into `scripts/fund_book.py`;
tests: `tests/test_engine/test_momentum_basket.py` + `test_momentum_weights.py`. Spec:
`docs/superpowers/specs/2026-06-20-momentum-sleeve-design.md`.

### Honest framing (read before changing anything)

This sleeve is the **validated config wired as a fund leg** — NOT a new claim and NOT re-tuned. It reuses
the SAME ranking (`strategies.factor_aqr.rank_aqr_factors`, AQR composite = z(value)+z(momentum)+
z(quality)) and the SAME weighting (`weights_from_picks`, top-N + per-name cap) the deployed
`aqr_top7_cap20_trail10_pit110` paper-drill builds. Those weight primitives (`build_pricebars`,
`vol_estimate`, `weights_from_picks`) were **extracted verbatim** from `scripts/aqr_ideal_walkforward.py`
into `engine/momentum_weights.py` (no behavior change) so the sleeve produces the EXACT validated
portfolio; `scripts/paper_drill.py` keeps its own independent copy (deferred dedupe). Fidelity is the
whole point — do not reimplement the weighting.

### Selection (`select_momentum_basket`)

At one PIT `as_of`: build each symbol's `PriceBar` series (`build_pricebars`, `≤ as_of`, needs ≥260
bars), `rank_aqr_factors` the cross-section (composite desc; a name with `≤ lookback` bars or no
fundamentals is dropped → `excluded`), take the **top-N** (default 7), and weight via
`weights_from_picks` (default cap 0.20 → inverse-vol with iterative cap; equal-weight when N×cap≈1.0).
`weights_from_picks` **raises** on an infeasible cap (`len(picks)×cap < 1.0` — a universe too small for
the cap); an empty universe / zero eligible → empty basket. PIT: bars/vol slice `≤ as_of`; the **caller**
passes only fundamentals with `asof_ts ≤ as_of` (the driver does this via `lookup_pit`). Output is a
`MomentumBasket` (holdings with weight, factor components, 1-based rank, Korean rationale).

### Composition

`momentum_sleeve_target(basket, fraction=0.25)` → `SleeveTarget("momentum", 0.25, weights)`. In
`assemble_fund_book` the fund's Σ(fractions) becomes `core 0.35 + hunt 0.15 + momentum 0.25 = 0.75 ≤ 1.0`
(still zero-leverage; the remaining 0.25 = bridge dry powder 0.15 + discretionary 0.10). The 8% per-name
cap binds a momentum name only if `weight × 0.25 > 0.08` (sleeve weight `> 0.32`); with top-7 it never
binds.

### Driver (PIT, opt-in)

```bash
cd "/Users/jjuni/재무관리 모델/trader-fund"
.venv/bin/python scripts/fund_book.py --as-of 2024-06-28 \
    --price-history data/snapshots/megacap-history.csv \
    --momentum-snapshot data/snapshots/megacap-fundamentals.csv
# omit --price-history -> core+hunt book only (momentum -> reserve). defaults: --momentum-fraction 0.25
#   --momentum-top-n 7 --momentum-cap 0.20. --momentum-snapshot omitted -> live catalog (not reproducible).
```

Momentum needs a **time-series** price history (distinct from the single-date `--prices` snapshot
core/hunt use) and megacap fundamentals; both are gitignored like the other snapshots, so the full
momentum data run is environment-local. The single resolved `effective` cutoff drives all legs.
