# Per-Sleeve OOS Attribution — Design Spec (2026-06-22)

**Goal:** extend the fund-book forward paper-OOS ledger so realised forward excess can be **attributed
to each sleeve** (core / hunt / momentum / bridge) — answering "which sleeve is actually earning its keep
live," not just "did the whole fund beat SPY." This is the diagnostic that, once the Phase-2 clock runs,
tells the user where the live edge (or drag) comes from. Fills `fund-book-oos-design.md` §7 deferred
("per-sleeve attribution in the ledger").

**Architecture:** additive change to `engine/fund_book_oos.py` — each pre-registered entry also records
per-sleeve fund-weight contributions (cap-clipped, the same provenance the exposure report uses), and a
new `score_by_sleeve` scores each sleeve's slice separately against the SAME benchmark. The drill gains
a `--by-sleeve` print. No new data, no auth; the existing whole-fund scoring is unchanged.

---

## 1. Honest framing

Still **pure forward observation** (no backtest expectation for the composite). Per-sleeve attribution is
descriptive: it slices the *already-recorded* book by sleeve and scores each slice's realised return vs
the benchmark. It makes no new claim and does not re-weight anything. The momentum sleeve carries the one
validated edge; core/hunt/bridge make none — so a momentum slice beating SPY live is expected-ish, while
core/hunt/bridge slices are watched for whether the screens/policy add value out of sample.

## 2. Record change (backward-compatible)

- `FundBookOOSEntry` gains `sleeve_weights: dict[str, dict[str, float]]` with a **default of `{}`**
  (so old ledger lines without the field still load — `load_ledger` is unaffected; no T0 exists yet
  anyway). `sleeve_weights[sleeve][symbol]` = that sleeve's **cap-clipped fund-weight contribution** to
  the symbol (Σ over all sleeves/symbols == `invested`, identical reconciliation to the exposure
  report's sleeve attribution).
- `fund_book_to_entry` populates it from `book.positions[].contributions`, scaling each position's
  pre-cap contributions by `fund_weight / Σ(raw contributions)` (= 1.0 when not capped; the cap haircut
  is split across the contributing sleeves). A position with zero raw contributions is skipped.

## 3. Scoring

- Refactor the period math into `_period_return_for(weights, entry_prices, marks)` (the current
  `_period_return` becomes a thin caller with `entry.weights`). Behaviour of `score_ledger` is
  **unchanged** (same output; pure internal refactor — the 15 existing tests must still pass).
- `score_by_sleeve(entries, mark_prices, *, periods_per_year=12.0) -> dict[str, FundBookOOSRecord]` —
  for each sleeve appearing in the entries' `sleeve_weights`, score that sleeve's per-entry weights
  (consecutive `(entry_i, entry_{i+1})` pairs, marked at `entry_{i+1}`'s date, renormalised over the
  sleeve's marked symbols) vs the **same** `benchmark_symbol`/`benchmark_price`. Returns one
  `FundBookOOSRecord` per sleeve. A sleeve with no `sleeve_weights` in an entry contributes no period.
  Entries lacking `sleeve_weights` (legacy) → empty result (nothing to attribute).

## 4. Fail-closed / invariants

`score_by_sleeve` reuses the same skip rules (missing marks / non-positive entry price / missing
benchmark mark → period skipped). A sleeve whose weights renormalise to nothing in a period is skipped
for that period (its `_period_return_for` returns None). Σ of sleeve fund-weights per entry ==
`invested` (test-pinned via `fund_book_to_entry`). Empty/one-entry → each sleeve a zero record.

## 5. Tests

`fund_book_to_entry` populates `sleeve_weights`, Σ == invested, cap-clipped overlapping name split across
sleeves; `load_ledger` of a legacy line (no `sleeve_weights`) defaults to `{}`; round-trip with
`sleeve_weights`; `_period_return_for` equals the old `_period_return` on `entry.weights` (refactor
parity); `score_by_sleeve` on a 2-entry ledger gives the known per-sleeve excess (e.g. a momentum slice
+X% vs SPY, a core slice −Y%); a sleeve absent from marks → that sleeve's period skipped; legacy entries
→ empty dict; `score_ledger` output unchanged (regression).

## 6. Driver

`scripts/fund_book_oos.py --score --by-sleeve`: after the whole-fund record, also print
`score_by_sleeve` per-sleeve lines (sleeve, n_periods, cumulative_excess, hit_rate). Recording already
captures `sleeve_weights` automatically via `fund_book_to_entry` (no new flag for record).

## 7. Deferred

Factor attribution within a sleeve; risk-adjusted (vol-scaled) per-sleeve contribution; turnover/cost
attribution. Still no cadence cron / live orders (separate concerns).
