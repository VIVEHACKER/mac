# Compounder Funnel — Forward-Return Validation (P5) & Adversarial Audit

> Research record. Does NOT constitute investment advice. Reproduce with
> `scripts/compounder_forward_validation.py` and `scripts/compounder_factor_ic.py`
> (regenerate a fundamentals snapshot first; both take `--snapshot`).

## Question

Does the compounder score (`engine/compounder.py`, `rank_compounders`) actually predict
forward returns — i.e. is the ten-bagger funnel a *return predictor*, or only a *screen*?

## Method (point-in-time, survivorship-aware)

Replay the scan at 8 past as-of dates (2012–2019, Jun 30) over the current ~1,003-name
S&P 400+600 universe, using only fundamentals with `asof ≤ date` (content-hashed snapshot)
and price on/before the date. Measure, per (as-of, horizon ∈ {3y,5y,7y}):

- **Spearman rank IC** — rank-correlation(score, forward total return) across all scored
  names. The standard ranking-power metric; uses every data point.
- **Q5−Q1 quintile spread** and **top-30 watchlist excess** vs the universe median.

Absolute returns are survivorship-inflated (current constituents only), so the *relative*
IC / spread is the **fairer** signal — it *reduces* the bias but does NOT eliminate it. The
relative measure is bias-free only if survivorship is symmetric across the score; it is **not**
for the quality dimension specifically: acquired high-quality compounders (a good outcome, and
exactly the premium-buyout ten-baggers we target) exit the current-constituent set, so the
surviving high-quality names are skewed toward laggards — biasing the quality IC downward. Read
the headline as directional evidence, with this asymmetry as a standing caveat (see audit §4).

## Headline result

| Signal | mean IC 3y | 5y | 7y | IC pooled |
|---|--:|--:|--:|--:|
| `best_score` (the live funnel) | −0.013 | −0.028 | −0.055 | **−0.031** (IC>0 in 7/23 cells) |

The top-30 watchlist *underperformed* the universe median at every horizon
(−8.3% / −8.6% / −14.6%). **The funnel shows no forward predictive value as built.**

### Factor IC decomposition — WHY

Pooled forward IC by signal (full table in `out/compounder-factor-ic.md`):

- **Quality/profitability drags it down:** `quality_composite` (roic+fcf_margin+net_margin)
  −0.079, `profitable_compounder` archetype −0.072 (positive in 3/23), roic −0.056 (2/23),
  net_margin −0.083. The funnel's heaviest archetype is the anti-predictive part. (ICs here
  mirror the live scorer's sector exclusions — FCF ratios dropped for financials.)
- **Value + growth-acceleration are weakly positive (oriented IC):** value is the strongest —
  cheap `ps` +0.089, `pb` +0.054 (oriented; cheap predicts winners → confirms the IC pipeline
  reproduces the value premium); `revenue_growth_acceleration` +0.026; `hypergrowth` +0.022.
- **The buyback prior FAILED:** `share_growth` oriented IC −0.032 (raw +0.032) — the funnel
  penalizes dilution, but on this universe higher share growth predicted winners (likely high-
  growth issuers raising capital). Low-debt also did not help (oriented −0.015).
- **In-sample redesign:** `redesign_composite` (growth-accel + cheap value, NO buyback) IC
  +0.070 — but data-snooped and inside the noise band (see below); NOT a validated edge.

## P0 UPDATE (2026-06-01) — gross profitability resolves the quality confound

The audit's top recommendation was to re-test quality with Novy-Marx **gross profitability
(GP/total assets)** instead of net-margin/NI-ROIC. Implemented: added `gross_profit` /
`cost_of_revenue` to the schema, re-ingested SEC `GrossProfit`/`CostOfRevenue` (aggregate tags
only) + `Assets` for the full 1,003-name universe (GP present for 676 symbols, assets for 998),
pinned snapshot `fundamentals-2026-06-01-gp2` (sha256 `fc6360be`), and re-ran the factor IC.

**Result — the metric definition WAS a real confound (the literature was right):**

| Quality metric | pooled oriented IC | windows positive |
|---|--:|--:|
| net_margin | −0.087 | 5/23 |
| roic (NI-based) | −0.061 | 2/23 |
| quality_composite (net) | −0.084 | 5/23 |
| **gross_profitability (GP/assets)** | **+0.04** | **21/23** |
| **qarp_composite (GP/assets + cheap value)**, pre-registered | **+0.07** | **20/23** |

Swapping net→gross flips the quality sign (≈+0.12 IC swing) and the positive is *consistent*
(≈21/23 windows). So the earlier "quality anti-predicts / it's a screen not a predictor"
headline was **substantially a net-margin metric-definition artifact** — the funnel was
measuring quality the wrong way. `gross_margin` (GP/sales) alone is negative (≈−0.02); it is
asset-**efficiency** (GP/assets), not pricing power, that predicts.

**Caveat (do not over-claim):** the gross edge is **modest** — at effective N≈2–3 the ≈+0.04 is
inside the noise band (z≈1.0). It is a directionally-correct, consistent positive, **not** a
strong validated edge. `qarp` (≈+0.07) is the strongest quality-based composite but was run on
the same data. This **revises the prior recommendation**: do NOT abandon the quality tilt — fix
its metric (net→gross), then validate OOS (next section).

> **Reproducibility note.** Fundamentals are pinned by snapshot (deterministic), but **prices
> come from yfinance and are NOT pinned** — each run pulls slightly different history, so exact
> IC magnitudes drift run-to-run (e.g. gross_profitability ranged +0.035→+0.042 across reruns).
> The SIGN and consistency (gross positive ~20-21/23, net negative ~5/23) are stable; read the
> direction, not the third decimal. Pinning prices is a future hardening step.

## Adversarial audit (5-lens panel) — what survived

A 5-agent panel (code-leak auditor · factor-literature critic · statistics skeptic ·
redesign architect · synthesis) stress-tested the finding. Verified conclusions:

1. **No bug, no PIT leak, no sign error (high confidence).** PIT filtering, as-of price
   selection, Spearman/composite math, and archetype score sign were all code-audited (IC
   matches `scipy.stats.spearmanr`; `asof_ts` are real SEC filing dates, ~39-day median lag).
   The negative quality IC is a genuine property of this data.
2. **It does NOT contradict QMJ / Novy-Marx.** The original quality metrics were *net*-margin
   and NI-based ROIC, **not** gross profitability (GP/total assets). Novy-Marx (2013) showed
   net-bottom-line quality is a weak/perverse proxy and gross profitability is the strong one.
   QMJ (Asness–Frazzini–Pedersen) is a *price-controlled long-short*, mostly large-cap. This is
   long-only raw-quality rank IC on a mid/small-cap *survivor* set — the construction the
   literature predicts will fail. The P0 follow-up added `gross_profit`/`cost_of_revenue` and
   confirmed that GP/assets flips the quality sign, but the edge is still modest and needs OOS
   validation before `_WEIGHTS` changes.
3. **The redesign is NOT a validated edge.** `redesign_composite` was built *after* seeing
   which signals had positive IC (data-snooping). Its +0.070 ≈ the mean of its pre-selected
   components (mostly the value premium); at honest effective N ≈ 2–3 (overlapping windows) it
   is inside the noise band (≈±0.07–0.08). The horizon-rise is a mechanical artifact of nested
   cumulative-return windows, not a strengthening edge. **Do not ship it.**
4. **Magnitude is biased downward.** Survivorship removes acquired high-quality names (a
   *good* outcome, and exactly the premium-buyout ten-baggers the strategy targets), biasing
   the quality IC downward specifically; the 2012–2015 junk-rally regime adds to it (best_score
   IC −0.077 for 2012–2015 as-of dates vs +0.027 for 2016–2018 — the sign flips by entry year).

### Strongest dissent (kept on the record)

The negative quality IC may be *mostly* survivorship + the wrong (net) quality metric. If so,
the original `profitable_compounder` funnel could be **correct**, and acting on "quality anti-
predicts" would steer away from the acquired-at-premium compounders the strategy is built to
find. **Resolve survivorship and gross-profitability before changing any weights.**

## Verdict

The funnel has **no *validated* forward edge yet** → keep using it as an evidence-backed
**screen**, not a return predictor. **Revised by the P0 update:** the prior "quality
anti-predicts" reading was substantially a *net-margin metric-definition artifact* — Novy-Marx
gross profitability (GP/assets) is positive and consistent (≈+0.04, ~21/23). So the right move
is **NOT to drop the quality tilt but to re-measure it (net→gross)**, then validate OOS before
changing `_WEIGHTS`. (Medium confidence — the gross edge is modest, z≈1.0 at low N.)

## OOS follow-up (2026-06-01) — what the OOS tests actually showed (4-lens audited)

`scripts/compounder_oos_validation.py` ran 4 pre-registered tests (regime split, decile L/S +
cost, out-of-universe, 6-split cross-sectional breadth). A 4-lens adversarial audit then
**corrected an overstated "OOS SUPPORTED" headline**. The honest reading:

- **Statistically REAL: net-margin/NI-ROIC quality ANTI-predicts** — negative in both regimes,
  in 11/12 half-splits, and a −32% decile L/S (full-P5 IC −0.084, z ≈ −2.4 to −2.9). Decisive.
- **Gross profitability is the LESS-BAD metric, only MARGINALLY positive — NOT a validated
  edge.** IC ≈ +0.04 sits inside the noise band (z ≈ 1.0–1.6) and is smaller than its own
  run-to-run drift (+0.031..+0.070, unpinned prices). The regime + 6-split breadth are NOT
  independent OOS: effective N ≈ 2–3 (overlapping windows, slow fundamentals); all 12 half-ICs
  (6 splits × 2) are pseudo-replicates of one period/universe — a breadth check, not held-out
  time — and after the financials sector-null, gross_quality breadth softens to 9/12 positive
  (3/6 splits both-halves +ve), while value-led qarp stays 12/12. The decile L/S is
  short-leg-inflated and irrelevant to a long-only screen. The "gross flips +" hypothesis was
  discovered in-sample on this same data — this is a re-test on the discovery sample, not fresh.

**Decision: defensive ADD now, confident reweight GATED.** A wholesale net→gross replace would
crash archetype coverage ~85%→52% (GP present for only ~52% of rows). So we did the reversible
ADD (item 3a) and gate the confident value-led QARP reweight on a true held-out time period
(item 3b).

## Held-out-time gate (2026-06-01) — 3b result

`scripts/compounder_heldout_oos.py` used pinned prices (`prices-2026-06-01`) and tested as-of
2020/2021/2022-06-30 with 3y forward returns. **Gate result: NOT PASSED for gross_quality.**

| Composite | raw IC | pos | sector-neutral IC | size-partial IC | long-only top-decile excess |
|---|--:|--:|--:|--:|--:|
| gross_quality | −0.014 | 1/3 | +0.008 | −0.020 | −3.9% |
| qarp | +0.191 | 3/3 | +0.160 | +0.173 | +31.0% |
| net_quality | −0.045 | 0/3 | −0.057 | +0.022 | −11.8% |

Read this conservatively: only 3 overlapping windows, so it does NOT crown QARP as an alpha.
But it is decisive for the pending decision: **gross_quality did NOT confirm out-of-time** (raw
−0.014, 1/3 windows, and the market_cap-partial IC −0.020 means it does not survive a size
control — it may even have been a size/sector tilt). The in-sample +0.04 was period-specific.

**Consequence — the defensive gross ADD was REVERTED** (engine/compounder.py): validate-before-
trust does not allow an unconfirmed signal to stay weighted in the live funnel. gross_profitability
remains MEASURED (`compute_metrics`) for diagnostics and sector-nulled for financials, but is NOT
a scoring weight. The funnel reverts to its prior `profitable_compounder` weights and stays a
SCREEN. The held-out-DURABLE finding is **VALUE** (qarp +0.191, 3/3, +31% long-only top-decile,
size-robust) and the durable NEGATIVE is net-margin quality — a future value-led redesign is the
right direction, but needs more power than 3 overlapping windows before touching `_WEIGHTS`.

## Action plan (the path to an actual edge)

| # | Action | Why | Priority |
|---|---|---|---|
| 1 | Funnel stays a SCREEN, not a return predictor. Do NOT ship the in-sample `redesign_composite`/`qarp` as alpha. | All composites sit inside the noise band at honest N. | P0 |
| 2 | ✅ DONE (2026-06-01) — added `gross_profit`/`cost_of_revenue`, re-ingested SEC GP/COGS/Assets (1,003 names), re-ran IC. | GP/assets +ve while net quality −ve: the net metric was the confound (Novy-Marx). | P0 |
| 3a | ↩️ TRIED then REVERTED (2026-06-01) — a `gross_profitability` 0.15 ADD was shipped, then removed after 3b failed to confirm it out-of-time. gross_profitability stays measured (diagnostics) + sector-nulled for financials, but is NOT a `_WEIGHTS` input. | Validate-before-trust: an unconfirmed signal must not stay weighted live. | P0 |
| 3b | ✅ TESTED / ❌ NOT PASSED (2026-06-01) — held-out-time gate, pinned prices (2020/2021/2022 as-of, 3y fwd): `gross_quality` raw IC −0.014 (1/3), size-partial −0.020, top-decile −3.9%. Blocked the reweight and triggered the 3a revert. The durable held-out signal is VALUE (qarp +0.191, 3/3, +31% top-decile). | Only a properly-powered held-out test earns a confident reweight; gross did not pass. | P0 |
| 4 | Reconstruct point-in-time index membership (restore delisted/acquired names). | Acquired compounders exiting the current set biases quality IC downward; biggest single threat to the whole conclusion. | P1 |
| 5 | ✅ DONE (2026-06-01) — surfaced `market_cap` in `compute_metrics` (diagnostic only, not a `_WEIGHTS` input); used in 3b's size-partial IC. | Enables testing whether GP/assets is a size tilt (it did not survive — see 3b) + size-neutral validation IC. | P1 |
| 6 | Price pinning ✅ DONE (`data/price_snapshot.py`, `prices-2026-06-01` sha256 4a9de78e) — used by 3b. Next: a properly-powered value-led held-out study (more windows / pre-2012 if coverage allows) before any `_WEIGHTS` change. | Pinned prices remove the run-to-run IC drift that confounded earlier reads. | P2 |

## Caveats (always cite when deciding)

Survivorship (above); effective N ≈ 2–3 (overlapping windows → low power, marginal
significance); horizon trends are overlap artifacts; no transaction costs/taxes/turnover in the
main P5 run; PIT index membership not reconstructed; net-margin quality was the wrong proxy for
academic gross-profitability quality; value self-check only half-passes (pb/ps clearly negative,
pe/pfcf near-zero). `market_cap` is now surfaced and used in action 3b's size-partial IC, but
the original OOS table above is still not a fully sector/size-neutral study. Read signs and
direction, not precise magnitudes.
