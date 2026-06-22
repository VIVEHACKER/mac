# Estimate-Revision Signal — Design Spec (2026-06-22)

**Goal:** implement the method learned from the @studying_stone X post / 휴먼스토리 video ("the screen he
looked at every day") as a real signal: **analyst estimate / target-price revision momentum**. The
office-worker's edge wasn't a stock tip — it was *daily monitoring of consensus revisions* (목표주가·EPS
추정·실적속보 turning "red"/up). Implements the long-deferred `signals/revisions.py` (SIIS/merr roadmap)
inside trader-fund where the forward-IC validation infra already lives.

**Architecture:** pure signal `signals/revisions.py` over `EstimateRevision` records → `StrategySignal`
(matching `signals/insider.py`), plus a `forward_ic` helper reusing `engine/ic.py` (spearman rank-IC +
ic_stats) for the validate-before-trust gate. The live FMP/consensus adapter (data fetch) is deferred
(needs an API key); the signal + IC logic are data-source-agnostic and fully tested on records.

---

## 1. Honest framing (load-bearing)

Estimate-revision momentum is a **documented factor** (analyst EPS/target-price upward revisions predict
near-term excess returns — earnings-momentum / PEAD-adjacent). BUT this is a **CANDIDATE signal, not a
return-predictor until forward-IC validated on the actual universe**: analyst target prices carry a
well-known **optimism / herding bias**, coverage is skewed to large caps, and "upgrade ⇒ buy" naive
rules generate many false signals. So the signal emits a *score + honest reason*; whether it has edge is
decided by `forward_ic` (rank-IC vs forward returns), mirroring the insider-signal forward-IC gate. The X
post is **survivorship-biased** (one success); the edge claim rests on the factor + our own IC test, not
the anecdote.

## 2. Input record

`EstimateRevision(symbol, market, as_of, target_price, target_price_prev, eps_estimate,
eps_estimate_prev, n_up, n_down, n_total)` — a per-symbol snapshot of consensus over a trailing window:
current vs prior mean target price, current vs prior mean EPS estimate, and the breadth counts (analysts
revising up / down / total coverage). All fields `float|int|None`; the source adapter (FMP etc.) is
deferred.

## 3. Signal (`revision_signals`)

`revision_signals(revisions, *, weights=DEFAULT_WEIGHTS, min_coverage=3, up_threshold=0.0) ->
list[StrategySignal]`:
1. **Screen:** drop names with `n_total < min_coverage` (thin coverage = noise) → recorded, not scored.
2. **Components** (each guarded, 0 when undefined):
   - `tp_chg = (target_price − target_price_prev) / target_price_prev` (needs both > 0)
   - `eps_chg = (eps_estimate − eps_estimate_prev) / abs(eps_estimate_prev)` (needs prev ≠ 0)
   - `breadth = (n_up − n_down) / n_total` (needs n_total > 0)
3. **Score:** `score = (w_tp·tp_chg + w_eps·eps_chg + w_breadth·breadth)` with `DEFAULT_WEIGHTS =
   {tp:0.35, eps:0.40, breadth:0.25}` (EPS-revision weighted highest — the cleanest revision signal;
   target price down-weighted for its optimism bias). `direction = "up"` if `score > up_threshold`,
   `"down"` if `< −up_threshold`, else `"flat"`. Korean `reason` quotes the three components.
4. Output sorted by score desc, then symbol.

## 4. Validation helper (`forward_ic`)

`forward_ic(scores, forward_returns, *, eff_n=None) -> ICStats` — `scores`/`forward_returns` are
`dict[symbol -> float]`; computes the **Spearman rank-IC** (`engine.ic.spearman`) over the intersecting
symbols and wraps it via `engine.ic.ic_stats`. This is the gate: a signal with near-zero / negative
forward-IC is **not** trusted regardless of the anecdote. (Per-period IC aggregation across dates is the
caller's job — same shape as the insider IC harness; the live multi-period run is deferred with the
data.)

## 5. Fail-closed / edges

`min_coverage ≥ 1`, weights finite; a record with all components undefined → score 0, `"flat"`;
`target_price_prev ≤ 0` or `eps_estimate_prev == 0` → that component contributes 0 (no div-by-zero);
empty input → empty list; `forward_ic` with < 2 overlapping symbols → `ICStats` with `n` small / IC None
(per `ic_stats`).

## 6. Tests

component math (tp/eps/breadth) incl. guards (zero/None prev → 0); weighting blend; direction
thresholds; coverage screen drops thin names; sort order; empty input; `forward_ic` — a constructed
monotone case → IC ≈ +1, an inverted case → ≈ −1, non-overlapping → degenerate; honest reason contains
the components.

## 7. Deferred

Live FMP / 컨센서스 adapter (`EstimateRevision` from an API — needs key); multi-period IC harness +
`out/revisions-ic.md` research report (mirror `scripts/insider_ic.py`, data-gated); wiring into the
momentum sleeve / fund as a 4th signal **only after** the forward-IC gate passes on real data; KR/크립토
coverage.
