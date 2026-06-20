# Momentum / IDEAL Sleeve — Design Spec (2026-06-20)

**Goal:** wire the validated 12-1 mega-cap AQR momentum line ("IDEAL") into the fund book as the
active-50% momentum sleeve (~25% of the fund), as a single-`as_of` engine analog of
`select_core_basket` / `select_hunt_basket`. This is the third real sleeve the barbell assembler has
been waiting for (fund-book spec §6 deferred: "Momentum/IDEAL sleeve wiring into the driver").

**Architecture:** the validated portfolio-construction primitives (`build_pricebars`, `vol_estimate`,
`weights_from_picks`) currently live in `scripts/aqr_ideal_walkforward.py` and are duplicated in the
deployed `scripts/paper_drill.py`. Per the chosen approach (extract + re-point walk-forward only):
move the three primitives **verbatim** into a new pure module `engine/momentum_weights.py`, re-point
`scripts/aqr_ideal_walkforward.py` to import them from there, and **leave `paper_drill.py` untouched**
(it keeps its own copy). Then add `engine/momentum_basket.py` (`select_momentum_basket`) on top, reusing
`strategies/factor_aqr.rank_aqr_factors`. This preserves exact fidelity to the validated
`aqr_top7_cap20_trail10_pit110` config (no reimplementation) and keeps the new engine off a
scripts→engine back-import.

---

## 1. Honest framing (load-bearing)

This sleeve **does** carry the project's one validated edge: 12-1 mega-cap momentum, **direction robust
across regimes, +8.15%/yr walk-forward but +size fragile** (PBO 0.39; significant excess US-only — see
the trader-fund memory). The sleeve is the *validated* config wired as a fund leg; it is NOT a new
claim and NOT re-tuned here. The weighting is the deployed `weights_from_picks` (top-7, 20% cap →
equal-weight since 7×0.20≈1.0; inverse-vol only when the cap is slack). The sleeve makes the SAME
portfolio the validated walk-forward / paper-drill builds — fidelity is the whole point of extracting
rather than reimplementing.

## 2. Module 1 — `engine/momentum_weights.py` (extraction, no behavior change)

Move **verbatim** from `scripts/aqr_ideal_walkforward.py` (same signatures, same bodies):

- `build_pricebars(prices, symbol, end, lookback_bars=260) -> list[PriceBar]` — PIT slice
  `prices[symbol].loc[:end]`.
- `vol_estimate(prices, symbol, end, window=63) -> float` — annualized vol, `0.30` fallback.
- `weights_from_picks(picks, prices, rebal, cap=0.20) -> dict[str, float]` — equal-weight when
  `n×cap ≈ 1.0`, else inverse-vol with iterative cap; raises on infeasible cap (`n×cap < 1.0`).

`scripts/aqr_ideal_walkforward.py` then does `from engine.momentum_weights import build_pricebars,
vol_estimate, weights_from_picks` and deletes its local defitions. `scripts/paper_drill.py` is **not**
touched (it has an independent copy by design). No other module imports these (verified: zero importers,
zero tests referenced them), so the re-point is mechanical and low-risk. **New** tests pin the extracted
functions (they had none): equal-weight-at-cap-binding, infeasible-cap raises, inverse-vol path +
iterative cap, vol fallback on short history, PIT slice excludes `> end`.

## 3. Module 2 — `engine/momentum_basket.py` (the sleeve)

- `MomentumHolding(symbol, weight, composite, value, momentum, quality, rank, rationale)`.
- `MomentumBasket(holdings, as_of, universe_size, eligible_count, top_n, cap, excluded)`.
- `select_momentum_basket(prices, fundamentals_by_symbol, symbols, *, as_of, top_n=7, cap=0.20,
  lookback=126) -> MomentumBasket`:
  1. For each `sym in symbols`, `bars = build_pricebars(prices, sym, as_of)`; keep non-empty
     (PIT: `≤ as_of`).
  2. `ranked = rank_aqr_factors(bars_by_symbol, fundamentals_by_symbol, lookback=lookback)` (composite
     desc; needs `> lookback` bars + a fundamentals record, else the name is dropped — recorded in
     `excluded`).
  3. `picks = ranked[:top_n]`.
  4. `weights = weights_from_picks(picks, prices, as_of, cap=cap)`.
  5. Build `MomentumHolding`s (weight, factor components, 1-based `rank`, Korean rationale); sort by
     weight desc then symbol asc. `eligible_count = len(ranked)`, `universe_size = len(symbols)`.
- `momentum_sleeve_target(basket, *, fraction=0.25) -> SleeveTarget` — `SleeveTarget("momentum",
  fraction, {h.symbol: h.weight for h in basket.holdings})` for `assemble_fund_book`.
- `format_momentum_basket(basket) -> str` — text report; header states the validated-edge framing
  (not a new claim).

PIT: `build_pricebars` / `vol_estimate` slice `≤ as_of`; the **caller** passes only PIT-filtered
fundamentals (`asof_ts ≤ as_of`), same contract as core/hunt.

## 4. Composition rules / fail-closed

`weights_from_picks` already guarantees `Σ weights ≤ 1.0` and per-name `≤ cap`; it raises on an
infeasible cap. `select_momentum_basket` validates `top_n ≥ 1`, `0 < cap ≤ 1`; an empty `symbols` or
zero eligible names → empty basket (eligible_count 0), `momentum_sleeve_target` → fraction-0.25 sleeve
with empty weights (all reserve). Down in `assemble_fund_book`, the momentum 0.25 sleeve raises the
fund's Σ(fractions) to `core 0.35 + hunt 0.15 + momentum 0.25 = 0.75 ≤ 1.0` (still zero-leverage; the
remaining 0.25 = bridge dry powder 0.15 + discretionary 0.10). The 8% per-name cap binds a momentum
name only if its `weight×0.25 > 0.08` (i.e. sleeve weight `> 0.32`); with top-7 equal-weight (≈0.143)
it never binds.

## 5. Tests

**momentum_weights:** the 5 extraction tests in §2 (synthetic pandas frames). **momentum_basket:**
single-as_of basket from a tiny synthetic price frame + fundamentals → expected top_n picks + weights
sum ≤ 1.0 + per-name ≤ cap; a name with `≤ lookback` bars is excluded; empty symbols → empty basket;
`momentum_sleeve_target` name/fraction/weights; fund_book integration — `[core, hunt, momentum]`
composes, Σ fractions 0.75, no leverage, momentum names appear; PIT — a price after `as_of` does not
change the basket.

## 6. Driver wiring (`scripts/fund_book.py`)

Add the momentum leg to the existing assembler: a `--momentum-fraction` (default 0.25), a
`--price-history` CSV (a time series read by `data.price_snapshot.read_price_snapshot`, distinct from
the single-date `--prices` snapshot core/hunt use), `--momentum-top-n` (7), `--momentum-cap` (0.20),
`--momentum-universe` (defaults to the `MEGACAPS` list used by the walk-forward). At the resolved
`effective` as_of, build the momentum sleeve and append `momentum_sleeve_target(...)` as the third
`SleeveTarget`. Momentum is optional: if `--price-history` is absent, fall back to the current
core+hunt-only book (momentum fraction → reserve), so the driver still runs without the time series.
Price-history snapshot is gitignored like the others.

## 7. Deferred

Dedupe `paper_drill.py`'s independent weights copy against `engine/momentum_weights.py` (left to a
separate change to avoid touching deployed live code here). A standalone `scripts/momentum_basket.py`
driver (the sleeve is wired straight into fund_book). Korea/crypto momentum universes. The bridge can
later scale the momentum sleeve too (currently it scales only the core anchor).
