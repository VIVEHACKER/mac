# Fund Exposure Report — Design Spec (2026-06-21)

**Goal:** give the assembled barbell book a **risk/exposure view** — sector concentration, per-sleeve
attribution, and name-concentration metrics — so the user can SEE what the composed fund actually holds
before trusting/deploying it. Fills `fund-book-design.md` §6 deferred ("risk/exposure integration —
sector/factor exposure of the assembled book"). The diagnostic that turns a flat target list into a
risk-legible book.

**Architecture:** pure engine `engine/fund_exposure.py` (no I/O; operates on an assembled
`engine.fund_book.FundBook` + an optional `symbol -> sector` map) + an `--exposure` flag on the existing
`scripts/fund_book.py` driver that prints the report under the book.

---

## 1. Honest framing

This is a **descriptive diagnostic, not a risk model** — it aggregates the already-assembled fund
weights (no covariance, no VaR, no factor model). It reports *what is in the book* (sector mix, which
sleeve drove each name, how concentrated), flags simple thresholds, and makes no forward claim. Factor
(value/momentum/quality) exposure is deferred (needs per-name loadings across sleeves).

## 2. Inputs / outputs

- `compute_exposure(book, sectors=None, *, sector_warn=0.40, top_n=5) -> FundExposureReport`.
  `book` = an `engine.fund_book.FundBook`; `sectors` = `dict[symbol -> sector]` (missing/None →
  `"Unknown"`). Weights are the fund-level `fund_weight` (already capped, ≤ `invested`).
- `SectorExposure(sector, weight, n_names)` — `weight` = Σ fund_weight in that sector.
- `SleeveAttribution(sleeve, weight)` — Σ of each position's per-sleeve `contributions` across the book
  (fund_book records `FundPosition.contributions = [(sleeve, contribution), ...]`; a name in two sleeves
  splits here). Σ over all sleeves == `invested` (the cap can clip a name; see §4).
- `FundExposureReport(sector_exposures, sleeve_attribution, n_positions, invested, reserve_cash,
  top_name, top_name_weight, top_n_weight, herfindahl, effective_n, max_sector, max_sector_weight,
  flags)` — `sector_exposures` / `sleeve_attribution` sorted by weight desc then name asc.
- `format_exposure(report) -> str` — text report.

## 3. Metrics

- **Sector exposure**: group positions by `sectors.get(symbol, "Unknown")`, sum `fund_weight`, count
  names. `max_sector` / `max_sector_weight` = the largest.
- **Sleeve attribution**: aggregate `FundPosition.contributions` by sleeve name. This attributes the
  *capped* fund weight: a position bound by the 8% cap contributes its capped weight proportionally to
  its sleeves (see §4) so Σ(sleeve_attribution) == `invested` exactly.
- **Concentration** (over the invested names, weights as-is, NOT renormalised — reserve is real cash):
  `herfindahl = Σ wᵢ²`; `effective_n = (Σ wᵢ)² / Σ wᵢ²` (= invested²/herfindahl; the Herfindahl
  effective number of names, robust to the reserve); `top_name` + `top_name_weight`; `top_n_weight` =
  Σ of the `top_n` largest.
- **Flags** (Korean, descriptive): `sector_warn` breach (`max_sector_weight > sector_warn`); a name at
  the book's `max_name_weight` cap (`p.capped`); degenerate breadth (`effective_n < 5`); empty book.

## 4. Cap-clipped sleeve attribution (the one subtlety)

`FundPosition.contributions` holds the **pre-cap** per-sleeve contributions (fund_book records them
before the 8% cap clips the summed weight). If a name's summed weight was capped, its raw contributions
sum to more than its final `fund_weight`. To keep Σ(sleeve_attribution) == `invested`, scale each
position's contributions by `fund_weight / Σ(raw contributions)` (= 1.0 when not capped). This
distributes the cap haircut proportionally across the contributing sleeves — the honest attribution of
the *actually-held* weight. A test pins this against a capped overlapping name.

## 5. Fail-closed / edge cases

Empty book → empty report (all zeros, `effective_n = 0`, flag "빈 북"). `sectors=None` → every name
"Unknown". A position whose raw contributions sum to 0 (shouldn't happen — a held name has weight)
is skipped in attribution with no divide-by-zero. `sector_warn`/`top_n` validated (`0 < sector_warn ≤
1`, `top_n ≥ 1`).

## 6. Tests

single-sleeve sector grouping + counts; multi-sector `max_sector`; sleeve attribution sums to
`invested`; **capped overlapping name → attribution scaled, still sums to invested**; herfindahl /
effective_n on a known book (e.g. 4 equal 0.10 names → effN 4.0); top_name / top_n_weight; Unknown
sector when `sectors=None` or a symbol missing; flags fire (sector breach, capped name, low effN, empty);
`format_exposure` includes the framing header + sector/sleeve lines.

## 7. Driver

`scripts/fund_book.py --exposure`: after assembling + printing the book, load the sectors map (reuse the
`--sectors-csv` already loaded for core/hunt) and print `format_exposure(compute_exposure(book,
sectors))`. No new data dependency.

## 8. Deferred

Factor (value/momentum/quality) exposure across sleeves (needs per-name loadings unified across the
three engines). Covariance / VaR / beta (this is a descriptive diagnostic, not a risk model). Exposure
deltas vs the prior rebalance (a forward-ledger concern).
