# Countercyclical Bridge (반순환 브릿지) — Design Spec (2026-06-20)

**Goal:** turn the fund's idle *dry powder* into a rule-based, counter-cyclical deployment policy that
buys **more of the already-value-screened durable core anchor** when the market has fallen (drawdown)
**and** the core's own valuations are cheap (value gate) — both required, both PIT. The bridge between
"short-term de-risked cash" and "long-term crash-buy tranches" in the 50/50 barbell.

**Architecture:** pure engine `engine/countercyclical_bridge.py` (no I/O; drawdown math + ladder policy
+ a sleeve-builder that hands the result to `engine/fund_book.py`) + a driver
`scripts/countercyclical_bridge.py` that wires a PIT market price series and the already-tested core
basket at one `as_of`.

---

## 1. Honest framing (load-bearing — read before changing anything)

The bridge makes **NO market-timing alpha claim.** It does not predict bottoms. It is a **rule-based
dry-powder deployment policy**: when (a) a market index has drawn down from its trailing peak **and**
(b) the core anchor's own valuations are cheap, it deploys a budget-capped slice of reserve cash into
**the existing, already-value-screened core basket** — buying the same durable names cheaper, in
**tranches** so it is robust to being early. It invents no signal, picks no new names, and reweights
nothing within the core sleeve (it scales the core's existing weights).

Two guards make it survival-first, not a leveraged bet:
1. **The value gate is an AND, not an OR.** Drawdown alone (price fell) is a falling-knife trap —
   earnings may have fallen further, leaving it still expensive. Deployment is **0 whenever the value
   gate is closed**, regardless of how deep the drawdown is.
2. **It reuses fund_book's rails.** The bridge is just another `SleeveTarget` composed by
   `assemble_fund_book`. The fund-level **8% per-name hard cap** and **Σ(fractions) ≤ 1.0 zero-leverage**
   guard already bound the combined core+bridge exposure — a name already at the cap overflows to
   reserve, never to leverage.

The barbell budget (user policy, this spec): long 50% = core 35% + hunt 15%; active 50% = momentum/IDEAL
25% + **bridge dry powder `bridge_budget` = 15%** + discretionary reserve 10%. `bridge_budget` is the
**maximum** fund fraction the bridge may ever deploy; it is an explicit parameter with a documented
default, overridable.

## 2. Inputs / outputs

- `market_drawdown(prices: Sequence[float]) -> float` — peak-to-last drawdown over the **given** price
  series (the driver passes a trailing-window, PIT-sliced ≤ `as_of` slice). `(peak − last) / peak`,
  clamped to `[0.0, 1.0]`. Empty series or non-positive peak → `ValueError` (fail-closed; a degenerate
  price history must not silently read as "no drawdown").
- `default_value_gate(core_basket, *, threshold: float = 0.55) -> bool` — convenience: `True` iff the
  **median** of the core holdings' `cheapness_pct` ≥ `threshold` (cheaper ⇒ higher percentile). Empty
  basket → `False` (no anchor to deploy into ⇒ gate closed). Decoupled from the policy below.
- `compute_deployment(drawdown: float, value_gate_open: bool, *, budget: float, ladder=DEFAULT_LADDER)
  -> BridgeDeployment` — the policy. `drawdown` clamped to `[0,1]`; if `value_gate_open` is `False` →
  `deployed_fraction = 0.0` (tranche 0). Else `deployed_fraction = budget × ladder_fraction(drawdown)`.
- `BridgeDeployment(deployed_fraction, budget, drawdown, value_gate_open, tranche_index, n_tranches,
  reason)` — `reason` is a Korean one-liner restating the policy decision.
- `bridge_sleeve_target(deployment, core_weights: dict[str, float]) -> SleeveTarget` — returns
  `SleeveTarget("bridge", deployment.deployed_fraction, core_weights)` for `assemble_fund_book`. The
  bridge's sleeve-relative weights **are** the core's weights: it scales the same anchor. If
  `deployed_fraction == 0` the sleeve is still returned (fraction 0) so the book records "bridge armed,
  not deployed".
- `format_deployment(deployment) -> str` — text report whose header restates the honest framing.

## 3. The ladder (deployment curve)

`DEFAULT_LADDER` = step ladder of `(drawdown_threshold, cumulative_fraction_of_budget)`, ascending:

| market drawdown | tranche | cumulative budget deployed |
|---|---|---|
| `< 10%` | 0 | 0 (hold dry powder — no crash) |
| `[10%, 20%)` | 1 | `1/3` |
| `[20%, 30%)` | 2 | `2/3` |
| `≥ 30%` | 3 | `3/3` (full `bridge_budget`) |

Deeper crash ⇒ more deployed (counter-cyclical). `ladder_fraction(dd)` = the cumulative fraction of the
**deepest threshold ≤ dd** (0 if below the first). `deployed_fraction = budget × ladder_fraction × [gate]`.
The ladder is a parameter; `compute_deployment` validates it (see §4). `n_tranches = len(ladder)`,
`tranche_index` = the rung index reached (0..n_tranches).

## 4. Composition rules & fail-closed validation

1. **Validate:** `0 ≤ budget ≤ 1` else `ValueError`; `drawdown` is **clamped** to `[0,1]` (a caller may
   pass a raw ratio); `ladder` non-empty, thresholds **strictly ascending** in `(0, 1]`, cumulative
   fractions **non-decreasing** in `[0, 1]` (last ≤ 1.0) else `ValueError` (a non-monotone ladder is a
   policy bug). `core_weights` non-negative and summing to `≤ 1.0 + 1e-9` (inherited from `SleeveTarget`
   validation in `assemble_fund_book`).
2. **Gate-closed short-circuit:** `value_gate_open is False` ⇒ `deployed_fraction = 0.0`, `tranche_index
   = 0`, reason notes the gate. (Falling-knife guard — §1 guard 1.)
3. **Deploy:** `deployed_fraction = budget × ladder_fraction(clamped_drawdown)`. Invariant
   `0 ≤ deployed_fraction ≤ budget` (a test asserts it across a sweep).
4. **Compose downstream:** the driver appends `bridge_sleeve_target(...)` to the existing
   `[core, hunt]` sleeves and calls `assemble_fund_book`. The fund-level cap, leverage guard, and
   reserve-cash accounting are **fund_book's** job — the bridge adds no new rail. A core name present in
   both `core` and `bridge` sleeves **sums** (core 0.35×w + bridge dep×w), then the 8% cap binds; cap
   overflow → reserve (no redistribution), per fund_book §3.

## 5. Tests (engine)

`market_drawdown`: flat series → 0; monotone-up series → 0 (peak = last); −25% off peak → 0.25; deep
crash clamps to ≤ 1; empty / non-positive peak → raises. `ladder_fraction` / `compute_deployment`:
dd below first threshold → 0; each rung boundary (9.9%/10%/19.9%/20%/29.9%/30%/50%) → exact cumulative;
**gate closed → 0 at every drawdown incl. ≥30%**; `deployed_fraction ≤ budget` over a 0→60% sweep;
non-ascending thresholds → raises; cumulative >1 or non-monotone → raises; `budget` out of `[0,1]` →
raises. `default_value_gate`: median cheapness ≥/< threshold → True/False; empty basket → False; even
count uses the mean-of-two-middle median. `bridge_sleeve_target`: name `bridge`, fraction =
`deployed_fraction`, weights = `core_weights`; fraction 0 still returns a valid sleeve. Integration: a
`[core, hunt, bridge]` assembly composes (core+bridge sum per shared name; 8% cap binds + overflow →
reserve) via `assemble_fund_book` — re-uses fund_book's tested rails, asserts the sum + cap interaction.

## 6. Driver

`scripts/countercyclical_bridge.py`: at one `as_of`, (1) build the core sleeve
(`scripts/core_basket.build_universe → select_core_basket`), (2) load a market index price series
(default SPY from the local snapshot/DuckDB), **PIT-slice to the trailing window ≤ `as_of`**, compute
`market_drawdown`, (3) evaluate `default_value_gate(core)`, (4) `compute_deployment(...)`, (5) print
`format_deployment`, and (6) optionally assemble the full `[core(0.35), hunt(0.15), bridge(dep)]` book
via `assemble_fund_book` and print `format_fund_book`. `--bridge-budget` defaults to `0.15`; market
price source, trailing window (default 252), and `--value-threshold` (0.55) are flags. PIT is inherited:
prices sliced ≤ `as_of`; core/hunt assemblers already enforce their own cutoff.

## 7. Deferred

Market-valuation gate (approach B: market earnings-yield vs 10y, or market P/E percentile) as an
**additional** AND-gate — needs FRED/market-P/E vintage data, kept out for now to preserve the
no-new-data, deterministic-test property. Dry-powder *replenishment* dynamics (how de-risking in calm
regimes refills the bridge budget over time) — this spec computes a **stateless target** deployment for
the current state; the ratchet/refill is a forward-ledger concern. Live order generation (the book is a
target only). The bridge's own forward ledger is partially covered by the Phase-2 paper-OOS drill.
