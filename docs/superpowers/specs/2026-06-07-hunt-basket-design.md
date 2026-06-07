# Hunt Basket Engine — Design Spec

**Date**: 2026-06-07
**Branch**: `feat/fund-engine` (worktree `/Users/jjuni/재무관리 모델/trader-fund`)
**Author**: 쭈니 + Claude
**Status**: Approved (design), pending spec review
**Sibling**: `engine/core_basket.py` (the durable anchor; this is its asymmetric-upside counterpart)

---

## 1. Purpose & Honest Framing

The **hunt basket** is the fund's asymmetric-upside sleeve — **~15% of the 50/50 barbell**. Its
alpha source is **the user's discretionary conviction** (track record: 8/10 high-conviction calls,
up to 20x over 3y), NOT a validated model. The system's job is two things only:

1. **Surface candidates** — screen by signal *events* and present them with conviction magnitude,
   a concrete kill-thesis, and risk flags, so the human can confirm which to hold.
2. **Enforce survival guards** — small position sizing, a per-name hard cap, breadth, and zero
   leverage, so that any single name going to zero costs the *fund* only ~2–3%, and a whole-sleeve
   wipeout costs ~15%.

### Honest constraint (validate-before-trust, harder here than for the core)

Of the user's "6 signals", **none has passed weight-eligibility**: `insider` is *suggestive but
unconfirmed* (size-controlled 1y IC +0.128, t≈2.2, OOS +0.073 in 7/10), `net_issuance` was
**rejected as null**, `foreign_flow` is **unvalidated** (no IC harness), and size / re-rating / CEO /
moat **do not exist as signal modules**. Therefore the hunt basket **must not blend signals into a
score** — that is exactly the disproven trap the core basket avoided. Instead:

- **Insider buying is the single primary screen + rank key** (it has the most forward evidence).
- `net_issuance` and `foreign_flow` are **descriptive flags only**, never blended into the rank.
- The basket is a **candidate surfacer for human confirmation, not an auto-buy.**

This framing must appear verbatim in the engine docstring and report header (no overclaiming).

### How hunt differs from core (a deliberate inversion)

The core basket *excludes* risky names (high-debt, diluters, cash-burners) because it is the durable
anchor. The hunt basket **does the opposite**: those names may be exactly the turnaround /
hypergrowth the user hunts, so hunt **does not screen them out** — it shows them as flags and manages
the risk through **small sizing + a per-name cap + the kill-thesis**, not through avoidance. Survival
in hunt is a *sizing* property, not a *selection* property.

---

## 2. Scope (YAGNI)

**In scope** (this spec):
- `engine/hunt_basket.py` — pure selection + sizing + kill-thesis logic (NEW file).
- `scripts/hunt_basket.py` — driver: catalog insider trades + pinned fundamentals/prices (PIT) →
  build signals → `select_hunt_basket` → `format_hunt_basket`.
- Tests: `tests/test_engine/test_hunt_basket.py`.

**Out of scope** (explicitly deferred):
- **Forward hit-rate ledger** (measuring the user's actual edge over time) — its own increment,
  mirrors `engine/paper_oos.py`. This is the real validation path but needs time, not code.
- **Rebalancer** — the first increment is `select` only (a thesis-hold rebalancer can follow the
  core-basket pattern later).
- `foreign_flow` IC harness (`scripts/foreign_flow_ic.py`), live order wiring, the counter-cyclical
  bridge — separate fund pieces.
- Any change to `engine/compounder.py`, `engine/core_basket.py`, `risk/sizing.py`,
  `signals/*` — **new file only; validated/existing code reused by import, never modified.**

---

## 3. Reuse (import, do not duplicate or modify)

- From `engine.compounder`: `compute_metrics`, `_flags`.
- From `engine.core_basket`: `_percentile_ranks` (cheapness tiebreaker), `_equal_weights_capped`
  (sleeve-relative equal weight + cap), `_cap_redistribute` (if needed for a future rebalancer).
- From `signals.insider`: `insider_buying_signal` (the driver calls it; the engine consumes the
  resulting `StrategySignal`).
- `StrategySignal` (`symbol, market, as_of: date, score: float, direction: str, reason: str`),
  `FundamentalRecord` from `data.models`.

The engine is **pure** (no I/O, no wall-clock, no catalog) so it is unit-testable with synthetic
signals + records, exactly like `core_basket`.

---

## 4. Engine Interface (`engine/hunt_basket.py`)

```python
@dataclass(frozen=True)
class HuntHolding:
    symbol: str
    weight: float            # sleeve-relative (sums to 1.0 across the basket)
    fund_weight: float       # weight * sleeve_fraction (fund-level, for the survival math)
    insider_score: float     # dollar-weighted insider conviction (the rank key)
    insider_reason: str      # from the insider StrategySignal
    signal_flags: tuple[str, ...]   # descriptive only: "외국인순매수", "자사주", "희석⚠", + _flags
    sector: str | None
    kill_thesis: str         # fundamental exit condition (NOT price): signal reversal + distress
    rationale: str

@dataclass(frozen=True)
class HuntBasket:
    holdings: tuple[HuntHolding, ...]
    as_of: date | None
    universe_size: int
    signal_eligible_count: int     # names with a primary (insider-long) signal event
    target_n: int
    max_per_name: float            # sleeve-relative hard cap
    sleeve_fraction: float         # fund-level sleeve size (default 0.15)
    sleeve_total_fund_weight: float   # sum(fund_weight) = sleeve_fraction * sum(weight)
    max_single_name_fund_loss: float  # max(fund_weight) — the survival headline
    excluded: tuple[tuple[str, str], ...]   # (symbol, reason) for names without the signal event

def select_hunt_basket(
    universe: dict[str, tuple[Sequence[FundamentalRecord], float]],
    insider_signals: dict[str, StrategySignal | None],
    *,
    foreign_flow: dict[str, StrategySignal | None] | None = None,   # flags only
    capital_signals: dict[str, StrategySignal | None] | None = None, # net_issuance, flags only
    sectors: dict[str, str] | None = None,
    target_n: int = 6,                 # 5–8 names
    max_per_name: float = 0.40,        # sleeve-relative cap (0.40 * 0.15 ≈ 6% fund, the memory cap)
    sleeve_fraction: float = 0.15,
    as_of: date | None = None,
) -> HuntBasket: ...

def format_hunt_basket(basket: HuntBasket) -> str: ...   # Korean report, honest header + survival math
```

### 4.1 SCREEN (light — signal event, not distress exclusion)

A name is **signal-eligible** iff its primary signal fires:
- `insider_signals[symbol]` is not None **and** `direction == "long"` (an open-market insider-buy
  cluster in the lookback window). `insider_buying_signal` only ever emits `"long"`, so in practice
  the gate is "an insider-buy signal exists."

That is the **only** gate. Hunt deliberately does **not** apply the core's distress filters
(`fcf<0`, `d/e>3`, `share_growth>15%`) — those become flags (§4.3), not exclusions. Names without a
primary signal go to `excluded` with reason `"primary 신호 없음 (no insider-buy event)"`.

The engine iterates the **`insider_signals` key set** (the signal-eligible candidates), not the
`universe` dict. Fundamentals are *optional enrichment*: for a selected name, `universe.get(symbol)`
supplies records+price for `_flags`, cheapness, and sector when present; if absent, the name is still
selectable on the insider event alone, with empty flags / `cheapness_pct=None` / `sector=None`. So a
high-conviction insider buy on a name lacking pinned fundamentals is not silently dropped.

### 4.2 RANK (insider conviction, never a blended score)

Sort signal-eligible names by:
1. `insider_score` descending (dollar-weighted conviction — the one suggestive-IC signal).
2. tiebreak: cheapness percentile descending (a *weak* tiebreaker via `_percentile_ranks` of
   `−ps`/`−pb` over the eligible set; rarely engages since exact dollar ties are unlikely).
3. tiebreak: `symbol` ascending (deterministic).

**No signal is blended into a composite.** `net_issuance` / `foreign_flow` never move the rank.

Take the top `target_n` (default 6).

### 4.3 FLAGS (descriptive, from the non-primary signals + fundamentals)

For each selected name, `signal_flags` collects (shown, never scored):
- from `foreign_flow[symbol]`: `"외국인순매수"` if `direction == "long"`, `"외국인순매도⚠"` if short.
- from `capital_signals[symbol]` (net_issuance): `"자사주"` if long (buyback), `"희석⚠"` if short
  (dilution), `"대규모조달⚠"` if the reason flags a large raise.
- from `engine.compounder._flags(metrics)`: `high-debt`, `margin-declining`, `negative-fcf`,
  `high-dilution` (computed from fundamentals if present; empty if no fundamentals).

These flags are the transparency layer the human uses to judge the candidate.

### 4.4 SIZE & WEIGHT (small equal-weight + hard cap; NO Kelly)

- **Sleeve-relative equal weight** `1/n` via `_equal_weights_capped(symbols, max_per_name)`. For the
  default `target_n=6`, `1/6 ≈ 16.7% < 40%` cap → weights are `1/n`, summing to 1.0. For a degenerate
  `n < 1/max_per_name` (e.g. n=2 → 1/2 > 0.40), each is capped at `max_per_name` and the basket sums
  `< 1.0` (sleeve cash) — and a degenerate (<3 holdings) basket **warns**.
- **No Kelly.** `risk/sizing.size_position`'s half-Kelly needs a *validated* win-prob / payoff; the
  hunt signals are unvalidated, so Kelly here would be "garbage edge in → garbage size out". Equal
  small weight + the hard cap is the honest survival sizing. (When the forward ledger eventually
  *confirms* an edge, Kelly can be revisited — gated on real evidence, not now.)
- **Fund-level survival math** (the headline the user cares about), from `sleeve_fraction`:
  - `fund_weight = weight * sleeve_fraction` (per name).
  - `max_single_name_fund_loss = max(fund_weight)` — if one name goes to 0, the fund loses this.
    For n=6, `(1/6)*0.15 ≈ 2.5%`.
  - `sleeve_total_fund_weight = sleeve_fraction * sum(weight)` — whole-sleeve wipeout ≈ 15%.
  - (This explicit translation supersedes the memory's inconsistent draft figures "−7.5% / −4%".)

### 4.5 KILL-THESIS (fundamental, not price — 0컷)

Per name, a concrete non-price exit condition built from the entry signal + distress flags, e.g.:
```
진입 근거=내부자 매수 $1,200,000 (insider buys: 3 by 2 in 90d); 청산=내부자 순매도 전환 OR distress(high-debt,negative-fcf)
```
There is **no price stop** (0컷). The thesis breaks on a fundamental event (insiders turn sellers, or
a hard distress flag fires), not on drawdown. This is what makes "survive total loss of a name"
acceptable: the position is small and the exit is thesis-driven.

---

## 5. Driver (`scripts/hunt_basket.py`)

Parallel to `scripts/core_basket.py`:
1. Resolve one PIT `effective` cutoff (explicit `--as-of` or the price snapshot's latest date),
   applied to **both** fundamentals and prices (the core's PIT fix).
2. Load catalog `get_insider_trades(symbol, as_of=effective_dt)` per universe symbol → call
   `insider_buying_signal(records, as_of=effective)` → `insider_signals` dict. (insider_trades has
   23,041 rows loaded.) Likewise `net_issuance_signal` from pinned fundamentals and (if KRX flows
   present) `foreign_flow_signal` for flags.
3. Build `universe = {symbol: (records, price)}` at the cutoff; load `sectors`.
4. `select_hunt_basket(...)` → `print(format_hunt_basket(basket))`.
5. CLI flags: `--as-of`, `--target-n`, `--max-per-name`, `--sleeve-fraction`, `--snapshot`,
   `--prices`, `--universe-csv`, `--sectors-csv`. Fail-loud on missing snapshot CSV (gitignored).

---

## 6. Tests (`tests/test_engine/test_hunt_basket.py`)

Pytest, synthetic `StrategySignal` + `FundamentalRecord` builders (project style, no fixtures):
- **Screen**: a name with an insider-long signal is eligible; a name with `insider_signals=None` is
  excluded with reason; a **high-debt / cash-burner name with an insider signal is KEPT** (the
  inversion vs core — risk is flagged, not excluded).
- **Rank**: higher `insider_score` ranks first; ties broken by cheapness then symbol (deterministic);
  **net_issuance / foreign_flow do NOT change the order** (proven by flipping them and asserting the
  rank is unchanged).
- **Flags**: foreign-flow long → `"외국인순매수"`; net_issuance dilution → `"희석⚠"`; fundamental
  `high-debt` surfaced — all present in `signal_flags`, none affecting weight or rank.
- **Size**: n=6 → each `1/6`, sum 1.0, `fund_weight ≈ 0.025`, `max_single_name_fund_loss ≈ 0.025`,
  `sleeve_total_fund_weight ≈ 0.15`; degenerate n=2 with cap → each capped, sum<1.0, **warns**.
- **Kill-thesis**: every holding has a non-empty kill_thesis citing the insider entry + any distress
  flags; no price level appears in it (0컷).
- **Honest framing**: `format_hunt_basket` header states "후보 발굴 / 사용자 확신 / 신호 미검증 /
  insider 주도 / 생존=사이징" and prints the survival math (max single-name fund loss, sleeve total).
- **Edge**: empty universe; no eligible names (all `insider=None`) → empty basket, no crash.

Verification: `gan-harness verify` analog (run only this test file; full pytest forbidden per memory),
ruff/mypy clean, then adversarial multi-lens review + `codex review --uncommitted "한국어로 답변"`.

---

## 7. Module Boundaries (isolation)

- `engine/hunt_basket.py` — *what*: turns (universe + insider signals + flag signals) into a sized,
  kill-thesis-annotated candidate basket; *interface*: `select_hunt_basket` / `format_hunt_basket` +
  dataclasses; *depends on*: `engine.compounder` (`_flags`, read-only), `engine.core_basket`
  (`_percentile_ranks`, `_equal_weights_capped`, read-only), `data.models`. Pure, no I/O.
- `scripts/hunt_basket.py` — *what*: wires catalog + snapshots (PIT) and the signal functions to the
  pure engine and prints; all I/O lives here.

The pure/driver split mirrors `core_basket` ↔ its driver, so the engine is testable with synthetic
data and the driver carries the PIT/snapshot discipline.

---

## 8. Open / Deferred

- **Forward hit-rate ledger** (the real edge measurement) — next increment, `paper_oos.py` pattern.
- Thesis-hold rebalancer for hunt (core pattern), `foreign_flow` IC harness, live order wiring.
- Whether the per-name cap should tighten as the forward ledger reveals the user's realized hit-rate
  (increase size only as evidence accrues — memory: "전진 적중률 확인되며 증액").
