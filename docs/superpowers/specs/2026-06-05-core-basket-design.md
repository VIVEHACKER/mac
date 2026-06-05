# Core Basket Engine — Design Spec

**Date**: 2026-06-05
**Branch**: `feat/fund-engine` (worktree `/Users/jjuni/재무관리 모델/trader-fund`)
**Author**: 쭈니 + Claude
**Status**: Approved (approaches), pending spec review

---

## 1. Purpose & Honest Role

The **core basket** is the long-term sleeve's durable anchor — **~35% of the 50/50 barbell fund**
(memory: fund architecture). It is a diversified, thesis-hold basket of **12–15 quality
compounder/value names, equal-weight (~7–8% each), 8% hard cap per name, zero leverage**.

### What this is NOT

This project's terminal factor validation (see `docs/COMPOUNDER_VALIDATION.md`, and the in-code
note in `engine/compounder.py:22-32`) found that **in this mid/small-cap survivor universe over
3–5y horizons, no single factor (gross / net-quality / value) robustly predicts forward returns
after regime + size + sector controls.** The only robust effect is that **net-margin / ROIC quality
*reverse*-predicts** (high net-margin/ROIC names underperform). Value is mostly a small-cap/size
effect and goes negative in the 2016–19 regime; gross-profitability's in-sample edge did not survive
held-out OOS.

→ The core basket therefore makes **no factor-alpha claim.** It is the intentionally boring,
durable anchor. The asymmetric upside is the **hunt basket's** job; the validated momentum edge is
the **separate IDEAL line** (`scripts/aqr_ideal_walkforward.py`, not part of this fund's core).

### What this IS — the four design commitments

1. **Avoid the known trap.** The ranking **excludes `net_margin` and `roic`** entirely (the durable
   *negative* predictors). This is the one validated, actionable finding and we honor it.
2. **Tilt toward directionally-supported (if modest) signals.** Rank by a **value-led composite**:
   cheapness (low `ps` / `pb`) + Novy-Marx gross-profitability (`gross_profitability` = GP/assets).
   These are weakly positive in-sample; we use them as a *tilt*, not an alpha claim.
3. **Thesis-hold (승자 안 자름).** Once held, a name that remains eligible is kept even if its rank
   slips; winners are not trimmed until they breach the hard cap. Let compounders run.
4. **Survival first.** Diversification (12–15 names) + 8% hard cap + zero leverage. Per the project's
   own finding, *diversification itself substitutes for vol-targeting* (memory: "분산 자체가
   vol-target 대신 작동"). No per-name stop — the core is a thesis-hold; the cap + breadth is the
   risk control.

This honest framing must appear verbatim in the engine docstring and the report header (no
overclaiming, per project convention).

---

## 2. Scope (YAGNI)

**In scope** (this spec):
- `engine/core_basket.py` — pure selection + weighting + thesis-hold rebalance logic (NEW file).
- `scripts/core_basket.py` — driver: load pinned snapshots + catalog → run selection → print report.
- Tests: `tests/test_engine/test_core_basket.py`.

**Out of scope** (explicitly deferred):
- Live order submission / paper-drill wiring for the core sleeve (Phase 1 later increment — the
  basket engine only *produces a target basket*; the runner that turns it into orders is separate).
- The hunt basket, the short-term guard harness, the counter-cyclical bridge (separate fund pieces).
- Any change to `engine/compounder.py`, `risk/sizing.py`, validated code, or `_WEIGHTS`
  (new file only; validated code untouched — project convention "신규 파일만, 검증 코드 무손상").
- A trend/momentum gate (brainstorm Approach 3) — deferred; momentum is the IDEAL line's domain and
  adds overfitting surface.

---

## 3. Universe & Reproducibility

- **Universe**: broad quality-screened (memory-approved). The ~1,000-name S&P 400/600 + large-cap set
  the compounder engine already uses (`data/universes/sp400-600-current.csv`), gated by the screen
  below. Survivorship in the *list* corrupts backtests, **not live forward selection** (a live
  investor picks from currently-listed names) — and the core makes no backtested-alpha claim anyway,
  so the broad list is appropriate. Durability comes from the **quality screen + diversification**,
  not from a cap-size restriction.
- **PIT strict**: all catalog reads pass `as_of=` (visibility gate `asof_ts <= as_of`).
- **Snapshot pinning**: the driver pins fundamentals to `fundamentals-2026-06-01-gp2`
  (sha256 verified) and prices to `prices-2026-06-01` (broad), via
  `data/fundamentals_snapshot.py` / `data/price_snapshot.py` (`verify=True`). Sectors from
  `data/sectors/sp400-600-current-sectors.csv`.
- Universe size, N, weights, cap, and the value/GP blend are **parameters** (defaults below);
  re-pointing to the megacap-106 set is a one-flag change.

---

## 4. Selection Logic (Approach 1: value-led screen)

A new module `engine/core_basket.py`. It **reuses** (imports, does not duplicate or modify):
`compute_metrics`, `SECTOR_INVALID_METRICS`, `_zscores`, `Z_CLIP`, `_flags` from
`engine.compounder`; `normal_cdf` from `engine.significance`.

Input shape mirrors the compounder engine:
`universe: dict[str, tuple[Sequence[FundamentalRecord], float]]` (symbol → (PIT records, price)),
optional `sectors: dict[str, str]`.

### 4.1 SCREEN (eligibility — exclude before ranking)

A name is **eligible** iff all hold:
- **Coverage**: `compute_metrics` yields ≥ `MIN_PRESENT_METRICS` (5) non-None values.
- **Value anchor present**: at least one of `ps`, `pb` is non-None (need something to rank cheapness).
- **Not distressed** (sector-aware):
  - *Non-financials*: exclude if `fcf_margin` is present and **< 0** (cash burner), OR
    `debt_to_equity` present and **> 3.0** (very high leverage), OR `share_growth` present and
    **> 0.15** (heavy serial dilution, >15%/yr).
  - *Financials* (`sectors[s] == "financials"`): skip the FCF and debt filters (banks are
    structurally levered and have no FCF/GP line — `SECTOR_INVALID_METRICS` already nulls those).
    Still apply coverage + value-anchor (`pb`) + the dilution filter. Financials are **fairly
    evaluated, not excluded** (memory line 161: "전면 제외 아님").

Thresholds are deliberately **loose** — the screen removes obvious non-durable junk, it does not try
to find alpha (the universe has none to find). Excluded names carry a reason string.

### 4.2 RANK (value-led composite, net-margin/ROIC excluded)

For the eligible set, compute **cross-sectional Z within the eligible set** (reuse `_zscores`,
winsorize at `Z_CLIP`):
- **cheapness_Z** = mean of available `{ −Z(ps), −Z(pb) }` (negated: lower multiple = cheaper =
  higher score). Use whichever of ps/pb is present; financials → `pb` only (ps less meaningful).
- **gp_Z** = `Z(gross_profitability)` (sector-valid only; financials → None via
  `SECTOR_INVALID_METRICS`).

**composite** = coverage-renormalized weighted sum (same renorm pattern as compounder):
```
contrib = w_value*cheapness_Z + w_gp*gp_Z   (only over present components)
wsum    = sum of |weight| for present components
composite = contrib / wsum                  (0 if wsum == 0)
```
Defaults: `w_value = 0.6`, `w_gp = 0.4` (value is the stronger directional signal per memory).
**`net_margin` and `roic` are never inputs.** A `display_score = normal_cdf(composite) * 100` is
attached for readability (0–100), parallel to `ArchetypeScore.score`.

### 4.3 SELECT & WEIGHT

- **Select** top `target_n` (default **13**) by `composite` descending. Deterministic tie-break:
  `(-composite, symbol)`.
- **Weight**: equal-weight `1/n`, then apply the **8% hard cap** (`max_weight = 0.08`). Because the
  selector is *equal*-weight, the cap is all-or-none: for **n ≥ 13** the cap never binds
  (1/13 ≈ 7.7% < 8%) → weights are simply `1/n` and **sum to 1.0**; for **n ≤ 12** every name hits
  the cap (1/12 ≈ 8.3% > 8%) → each is set to 8% and the basket sums to `n × 0.08 < 1.0`, the
  remainder held as **sleeve cash** (there is no uncapped name to redistribute to under equal
  weighting — cap-and-redistribute only arises in the thesis-hold rebalancer §5, where winners carry
  unequal grown weights). The default `target_n = 13` is chosen precisely so the cap does not bind.
  Weights are **sleeve-relative** (fund-level weight = `sleeve_weight × 0.35`, applied by the fund
  allocator, not here — the basket engine is sleeve-agnostic).

### 4.4 Outputs

```python
@dataclass(frozen=True)
class CoreHolding:
    symbol: str
    weight: float            # sleeve-relative, sums to 1.0 across the basket
    composite: float         # raw value-led composite (sort key)
    display_score: float     # normal_cdf(composite)*100, 0-100
    cheapness_z: float | None
    gp_z: float | None
    sector: str | None
    flags: tuple[str, ...]   # reuse _flags (high-debt, margin-declining, ...)
    rationale: str           # Korean one-liner

@dataclass(frozen=True)
class CoreBasket:
    holdings: tuple[CoreHolding, ...]
    as_of: date | None
    universe_size: int       # names supplied
    eligible_count: int      # passed the screen
    target_n: int
    max_weight: float
    excluded: tuple[tuple[str, str], ...]   # (symbol, reason) for screened-out names

def select_core_basket(
    universe: dict[str, tuple[Sequence[FundamentalRecord], float]],
    *,
    sectors: dict[str, str] | None = None,
    target_n: int = 13,
    max_weight: float = 0.08,
    w_value: float = 0.6,
    w_gp: float = 0.4,
    as_of: date | None = None,
) -> CoreBasket: ...

def format_core_basket(basket: CoreBasket) -> str: ...   # Korean report, honest-framing header
```

---

## 5. Thesis-Hold Rebalancer (increment 2)

```python
@dataclass(frozen=True)
class RebalanceAction:
    symbol: str
    action: str              # "hold" | "add" | "drop" | "trim_to_cap"
    target_weight: float
    reason: str

def rebalance_core_basket(
    held: dict[str, float],          # current symbol -> current sleeve-relative weight
    target: CoreBasket,              # freshly selected basket (eligible+ranked names)
    eligible: set[str],              # names still passing the screen this period
    *,
    target_n: int = 13,
    max_weight: float = 0.08,
) -> tuple[CoreBasket, tuple[RebalanceAction, ...]]: ...
```

Logic (models 승자 안 자름):
1. **Keep** each held name that is **still eligible** (in `eligible`), even if it fell out of the
   fresh top-N → `hold`. A held name that became **ineligible** (now distressed/over-levered) is a
   thesis break → `drop`.
2. **Add** top-ranked fresh names not already held until the basket reaches `target_n` → `add`.
3. **Weights**: a held winner whose current weight exceeds the equal-weight target keeps its grown
   weight (`hold`), **capped at `max_weight`** (`trim_to_cap` only when it breaches 8%). All other
   slots split the remainder equally (capped). Renormalize to sum 1.0 with cap redistribution.
4. Drop reasons and the eligible/ineligible split come from the screen, so a drop always cites *why*
   the thesis broke.

---

## 6. Driver Script (`scripts/core_basket.py`)

Parallel to the existing compounder-scan driver:
1. Load pinned fundamentals snapshot (`fundamentals-2026-06-01-gp2`, verify=True) + price snapshot
   (`prices-2026-06-01`) + sectors CSV.
2. Build `universe = {symbol: (records, price)}` at `as_of` (PIT) from the catalog/snapshot for the
   ~1,000-name list, applying the as_of visibility gate.
3. `select_core_basket(universe, sectors=...)` → `print(format_core_basket(basket))`.
4. CLI flags: `--as-of`, `--target-n`, `--max-weight`, `--w-value`, `--w-gp`, `--universe-csv`,
   `--snapshot`/`--prices` (default to the pinned names). Report goes to stdout; persisted reports
   (if any) to `out/` (gitignored, per project policy).

---

## 7. Tests (`tests/test_engine/test_core_basket.py`)

Pytest, synthetic `FundamentalRecord` builders (no fixtures, project style). Cases:
- **Screen**: cash-burner (`fcf_margin<0`), over-levered (`d/e>3`), serial diluter
  (`share_growth>0.15`) excluded with reasons; coverage-gate (<5 metrics) excluded;
  financial (high d/e, no FCF) **kept** and ranked by `pb` only.
- **Rank — the key honesty test**: a cheap + high-GP name ranks **above** an expensive + low-GP name;
  and a **high-`net_margin`/high-`roic` but expensive** name does **NOT** rank highly — proving
  net_margin/roic are excluded from the composite.
- **Weight**: n=13 → each ≈ 1/13, sum 1.0 (cap does not bind); n=12 → every name capped at 8%,
  sum = 0.96, remainder is sleeve cash (no redistribution under equal weighting).
- **Determinism**: tie-break stable across input ordering.
- **Thesis-hold**: held eligible name kept despite rank slip (`hold`); held now-ineligible name
  dropped (`drop` with reason); winner above equal-weight kept (`hold`); winner above cap trimmed
  (`trim_to_cap`); basket fills to `target_n` with `add`s; weights sum to 1.0.
- **Honest framing**: `format_core_basket` output contains the "no alpha claim / durable anchor /
  excludes net-margin·ROIC" header.

Verification: `gan-harness verify` (L1) PASS, ruff/mypy clean. Then adversarial multi-lens review +
`codex review --uncommitted "한국어로 답변"` (project signature pattern) before commit.

---

## 8. Module Boundaries (isolation check)

- `engine/core_basket.py` — *what*: turns a universe into a target core basket and rebalances it;
  *interface*: `select_core_basket` / `rebalance_core_basket` / `format_core_basket` + dataclasses;
  *depends on*: `engine.compounder` (metrics/Z/sector/flags, read-only import),
  `engine.significance.normal_cdf`, `data.models.FundamentalRecord`. Pure, no I/O, no wall-clock.
- `scripts/core_basket.py` — *what*: wires snapshots + catalog (PIT) to the pure engine and prints;
  *depends on*: the engine + snapshot/catalog loaders. All I/O lives here.

The pure/driver split mirrors `engine/compounder.py` ↔ the compounder-scan driver, so the engine is
unit-testable with synthetic data and the driver carries the PIT/snapshot discipline.

---

## 9. Open / Deferred

- Live runner + paper-drill wiring for the core sleeve (separate Phase 1 increment).
- `net_debt` accuracy (FundamentalRecord has no cash field) — affects DCF, not this basket; deferred.
- Trend/value-trap gate (Approach 3) — revisit only if live behavior shows value traps.
- Whether `_zscores` should be promoted from `engine.compounder` to a shared `engine/_stats.py`
  (a clean refactor, but it touches validated code — deferred; import-reuse for now).
