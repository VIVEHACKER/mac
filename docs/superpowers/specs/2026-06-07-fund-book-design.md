# Fund Book (Barbell Assembler) — Design Spec (2026-06-07)

**Goal:** compose the separate sleeve engines (core basket, hunt basket, momentum/IDEAL) into ONE
fund-level target book with the 50/50 barbell structure and fund-level risk rails. The keystone that
turns three screens into "the fund."

**Architecture:** pure engine `engine/fund_book.py` (sleeve-agnostic composition; no I/O) + a driver
`scripts/fund_book.py` that wires the already-tested PIT assemblers (`build_universe` for core,
`build_hunt_inputs` for hunt) at one `as_of` and assembles them.

---

## 1. Honest framing (load-bearing)

The assembler makes **NO new alpha claim.** It only (1) **composes** already-validated/screened sleeve
targets at the user's **barbell POLICY fractions** (a user decision, not a model output) and (2)
enforces **fund-level risk rails**: an 8% per-name hard cap, Σ(sleeve fractions) ≤ 1.0 (**zero
leverage**), long-only non-negative weights. It invents no signal and reweights nothing within a sleeve.

The barbell (project goal): long 50% = **core ~35% + hunt ~15%**; active 50% = momentum/IDEAL +
the user's discretionary trading + guards. The system generates targets for core, hunt, and momentum;
the **un-allocated remainder (the discretionary part of the active half) is reserve cash** the user
fills with their own trades. Fractions are explicit parameters with documented defaults — overridable.

## 2. Inputs / outputs

- `SleeveTarget(name, fraction, weights)` — `weights: dict[symbol -> sleeve-relative weight]` (each
  sleeve's weights are sleeve-relative, summing to ≤ 1.0; any shortfall is that sleeve's internal
  cash). `fraction` = the fund-level fraction allocated to this sleeve.
- `assemble_fund_book(sleeves, *, max_name_weight=0.08) -> FundBook`.
- `FundPosition(symbol, fund_weight, contributions, capped)` — `contributions` = the per-sleeve
  fund-weight provenance `[(sleeve_name, contribution), ...]`; `capped` = the 8% cap bound this name.
- `FundBook(positions, sleeve_fractions, invested, reserve_cash, max_name_weight, top_name_weight,
  n_positions)`.
- `format_fund_book(book)` — text report whose header restates the honest framing.

## 3. Composition rules

1. **Validate (fail-closed):** each `fraction ∈ [0, 1]`; **Σ fractions ≤ 1.0** else `ValueError`
   (leverage); each sleeve's weights non-negative and summing to ≤ `1.0 + 1e-9` else `ValueError`;
   `0 < max_name_weight ≤ 1`.
2. **Compose:** `fund_weight[sym] = Σ_sleeve sleeve.weights.get(sym, 0) × sleeve.fraction`. A symbol
   present in multiple sleeves **sums** (e.g. a name both core-screened and insider-bought).
3. **Global cap:** cap each `fund_weight` at `max_name_weight`; the **overflow becomes reserve cash**
   — NO cross-sleeve redistribution (that would distort the user's sleeve policy and is the validate-
   safe choice). Set `capped=True` on bound names.
4. **Reserve cash** = `1.0 − Σ final fund weights` (un-allocated sleeve fractions + the active-
   discretionary half + cap overflow). Reported, never silently leveraged away.
5. **Order:** positions sorted by `fund_weight` desc, then symbol asc.

## 4. Tests (engine)

single-sleeve `fund_weight = weight × fraction`; multi-sleeve sum for a shared symbol; global cap binds
+ overflow → reserve + `capped` flag; Σ fractions > 1 raises (leverage guard); a sleeve's weights > 1
raises; reserve = 1 − invested; provenance correct; empty sleeves → empty book, reserve 1.0; ordering.

## 5. Driver

`scripts/fund_book.py`: at one `as_of`, build the core sleeve (`build_universe` → `select_core_basket`)
and the hunt sleeve (`build_hunt_inputs` → `select_hunt_basket`), convert each to a `SleeveTarget`
(core fraction 0.35, hunt 0.15 by default), assemble, and print `format_fund_book`. Momentum/IDEAL is
an optional third sleeve (its target wired in later; for now its fraction is reserve). PIT is inherited
from the two tested assemblers — same `as_of` for all legs.

## 6. Deferred

Momentum/IDEAL sleeve wiring into the driver; live order generation (the book is a target only);
risk/exposure integration (sector/factor exposure of the assembled book); a fund-level forward ledger.
