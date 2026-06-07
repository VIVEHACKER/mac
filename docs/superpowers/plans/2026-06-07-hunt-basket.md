# Hunt Basket Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a pure `engine/hunt_basket.py` that turns (universe + insider signals + flag signals) into a 5–8 name asymmetric-upside candidate basket with small equal-weight sizing, a per-name cap, fund-level survival math, and a fundamental kill-thesis — plus a PIT driver.

**Architecture:** A pure selection engine (insider-event screen → insider-conviction rank → small equal-weight + cap → kill-thesis) that **reuses** validated primitives from `engine.compounder` (`_flags`) and `engine.core_basket` (`_percentile_ranks`, `_equal_weights_capped`) by import, never modifying them. Signals are used as a screen + flags, **never blended into a score** (validate-before-trust). A driver wires catalog insider trades + pinned snapshots (PIT) to the pure engine.

**Tech Stack:** Python 3.12, pure stdlib (`dataclasses`, `warnings`), pytest. No new dependencies. Catalog = DuckDB via `data.catalog`. Signals via `signals.insider` / `signals.capital` / `signals.foreign_flow`.

**Worktree:** `/Users/jjuni/재무관리 모델/trader-fund` (branch `feat/fund-engine`). Python: `/Users/jjuni/재무관리 모델/trader/.venv/bin/python` (run from this cwd; `sys.path.insert(0,'.')` resolves local code).

**Spec:** `docs/superpowers/specs/2026-06-07-hunt-basket-design.md`

---

## File Structure

- **Create** `engine/hunt_basket.py` — pure: `HuntHolding`, `HuntBasket`, `select_hunt_basket`, `format_hunt_basket`, helpers `_signal_eligible`, `_rank_candidates`, `_collect_flags`, `_kill_thesis`. Imports (read-only) `_flags`, `compute_metrics` from `engine.compounder`; `_percentile_ranks`, `_equal_weights_capped` from `engine.core_basket`; `StrategySignal`, `FundamentalRecord` from `data.models`.
- **Create** `tests/test_engine/test_hunt_basket.py` — pytest, synthetic builders (no fixtures, mirrors `test_core_basket.py`).
- **Create** `scripts/hunt_basket.py` — driver: catalog insider trades + pinned fundamentals/prices (PIT) → build signals → `select_hunt_basket` → `format_hunt_basket`.

Convention: **new file only; validated `engine/compounder.py`, `engine/core_basket.py`, `risk/sizing.py`, `signals/*` untouched.**

`StrategySignal` shape (from `data.models`, do not redefine): `StrategySignal(symbol: str, market: str, as_of: date, score: float, direction: str, reason: str)` — frozen dataclass. `insider_buying_signal` always emits `direction="long"`.

---

## Conventions for every task

- Run tests with the venv python from the worktree cwd:
  `/Users/jjuni/재무관리\ 모델/trader/.venv/bin/python -m pytest tests/test_engine/test_hunt_basket.py -v`
- Do **not** run the full pytest suite directly (project memory: exit 144). Run only the hunt-basket test file.
- After each task's tests pass: `ruff check engine/hunt_basket.py tests/test_engine/test_hunt_basket.py` and `mypy engine/hunt_basket.py` clean.
- Commit per task with the `<type>: <description>` format.

---

### Task 1: Module skeleton + dataclasses

**Files:**
- Create: `engine/hunt_basket.py`
- Test: `tests/test_engine/test_hunt_basket.py`

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

from datetime import date, datetime

import pytest

from data.models import FundamentalRecord, StrategySignal
from engine.hunt_basket import (
    HuntBasket,
    HuntHolding,
    format_hunt_basket,
    select_hunt_basket,
)


def _sig(symbol, score, *, direction="long", reason="insider buys", as_of=date(2026, 6, 1)):
    return StrategySignal(
        symbol=symbol, market="us", as_of=as_of, score=score, direction=direction, reason=reason
    )


def _recs(symbol, *, rev, ni, fcf, gp, assets, eq, debt, sh, eps):
    out = []
    for i, year in enumerate((2020, 2021, 2022, 2023)):
        out.append(
            FundamentalRecord(
                symbol=symbol, market="us", period_end=date(year, 12, 31),
                asof_ts=datetime(year + 1, 3, 1), revenue=rev[i], net_income=ni[i],
                free_cash_flow=fcf[i], total_assets=assets, total_equity=eq, total_debt=debt,
                shares_out=sh, eps=eps, gross_profit=gp[i],
            )
        )
    return out


def test_dataclasses_constructible():
    h = HuntHolding(
        symbol="AAA", weight=0.1667, fund_weight=0.025, insider_score=1_200_000.0,
        insider_reason="insider buys: 3 by 2", signal_flags=("외국인순매수",), sector=None,
        kill_thesis="진입=내부자매수; 청산=순매도전환", rationale="내부자 고확신",
    )
    b = HuntBasket(
        holdings=(h,), as_of=None, universe_size=1, signal_eligible_count=1, target_n=6,
        max_per_name=0.40, sleeve_fraction=0.15, sleeve_total_fund_weight=0.025,
        max_single_name_fund_loss=0.025, excluded=(),
    )
    assert b.holdings[0].symbol == "AAA"
    assert b.max_single_name_fund_loss == 0.025
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv python -m pytest tests/test_engine/test_hunt_basket.py::test_dataclasses_constructible -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'engine.hunt_basket'`

- [ ] **Step 3: Write minimal implementation**

```python
"""Hunt basket: the fund's asymmetric-upside sleeve (~15% of the 50/50 barbell).

HONEST FRAMING — read before changing anything:
The alpha source here is the USER's discretionary conviction (track record:
8/10 high-conviction calls, up to 20x over 3y), NOT a validated model. The system
only (1) SURFACES candidates by signal events with conviction, a kill-thesis, and
risk flags for the human to confirm, and (2) ENFORCES survival guards (small
sizing, a per-name cap, breadth, zero leverage) so one name going to zero costs the
fund ~2-3% and a whole-sleeve wipeout ~15%.

Validate-before-trust (harder than the core): NONE of the user's 6 signals is
weight-eligible (insider is only *suggestive* — size-controlled 1y IC +0.128,
t≈2.2; net_issuance was REJECTED as null; foreign_flow is unvalidated; size/
re-rating/CEO/moat have no signal module). So this engine MUST NOT blend signals
into a score. Insider buying is the single primary screen + rank key; net_issuance
and foreign_flow are DESCRIPTIVE FLAGS only. This is a candidate surfacer for human
confirmation, NOT an auto-buy.

Inversion vs the core basket: the core EXCLUDES risky names (high-debt, diluters);
hunt does NOT — those may be the turnaround/hypergrowth being hunted, so risk is
shown as flags and managed by SMALL SIZING + a per-name cap + a fundamental
kill-thesis (0컷, no price stop), not by avoidance. Survival is a sizing property.
No Kelly: it needs a validated edge we do not have.
"""

from __future__ import annotations

import warnings
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from data.models import FundamentalRecord, StrategySignal

MIN_HOLDINGS_WARN: int = 3  # below this the hunt sleeve is degenerate -> warn (non-fatal)
DEFAULT_TARGET_N: int = 6
DEFAULT_MAX_PER_NAME: float = 0.40  # sleeve-relative; *0.15 sleeve ≈ 6% fund (the memory cap)
DEFAULT_SLEEVE_FRACTION: float = 0.15


@dataclass(frozen=True)
class HuntHolding:
    symbol: str
    weight: float  # sleeve-relative (sums to 1.0 across the basket)
    fund_weight: float  # weight * sleeve_fraction (fund-level)
    insider_score: float  # dollar-weighted insider conviction (the rank key)
    insider_reason: str
    signal_flags: tuple[str, ...]  # descriptive only: never affects weight or rank
    sector: str | None
    kill_thesis: str  # fundamental exit condition (NOT price)
    rationale: str


@dataclass(frozen=True)
class HuntBasket:
    holdings: tuple[HuntHolding, ...]
    as_of: date | None
    universe_size: int
    signal_eligible_count: int
    target_n: int
    max_per_name: float
    sleeve_fraction: float
    sleeve_total_fund_weight: float
    max_single_name_fund_loss: float
    excluded: tuple[tuple[str, str], ...]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv python -m pytest tests/test_engine/test_hunt_basket.py::test_dataclasses_constructible -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add engine/hunt_basket.py tests/test_engine/test_hunt_basket.py
git commit -m "feat(hunt-basket): module skeleton + dataclasses"
```

---

### Task 2: Signal-eligible screen (insider event gate)

**Files:**
- Modify: `engine/hunt_basket.py`
- Test: `tests/test_engine/test_hunt_basket.py`

Screen contract (spec §4.1): iterate the `insider_signals` key set. A name is eligible iff its
insider signal is not None and `direction == "long"`. Fundamentals are optional enrichment (the gate
is the insider event, NOT distress filters). Returns `(eligible_symbols, excluded)` where eligible is
the ordered list of symbols with a long insider signal and excluded is `[(symbol, reason)]` for the
rest.

- [ ] **Step 1: Write the failing test**

```python
def test_screen_gates_on_insider_long_event_only():
    from engine.hunt_basket import _signal_eligible

    insider = {
        "BUY": _sig("BUY", 1_000_000.0, direction="long"),
        "NONE": None,
        "SHORT": _sig("SHORT", 5_000.0, direction="short"),  # not a buy event
    }
    eligible, excluded = _signal_eligible(insider)
    assert "BUY" in eligible
    ex = dict(excluded)
    assert "NONE" in ex and "신호" in ex["NONE"]
    assert "SHORT" in ex


def test_screen_keeps_high_debt_name_unlike_core():
    # The inversion: a distressed (high-debt) name WITH an insider buy is eligible (not excluded).
    from engine.hunt_basket import _signal_eligible

    insider = {"RISKY": _sig("RISKY", 800_000.0, direction="long")}
    eligible, excluded = _signal_eligible(insider)
    assert eligible == ["RISKY"]  # hunt does not screen out risk; sizing manages it
    assert excluded == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv python -m pytest tests/test_engine/test_hunt_basket.py -k screen -v`
Expected: FAIL with `ImportError: cannot import name '_signal_eligible'`

- [ ] **Step 3: Write minimal implementation** (append)

```python
def _signal_eligible(
    insider_signals: dict[str, StrategySignal | None],
) -> tuple[list[str], list[tuple[str, str]]]:
    """Eligible = names with a long insider-buy signal event. The ONLY gate (no distress filters)."""
    eligible: list[str] = []
    excluded: list[tuple[str, str]] = []
    for symbol, sig in insider_signals.items():
        if sig is not None and sig.direction == "long":
            eligible.append(symbol)
        else:
            excluded.append((symbol, "primary 신호 없음 (no insider-buy event)"))
    return eligible, excluded
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv python -m pytest tests/test_engine/test_hunt_basket.py -k screen -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add engine/hunt_basket.py tests/test_engine/test_hunt_basket.py
git commit -m "feat(hunt-basket): insider-event eligibility screen"
```

---

### Task 3: Rank by insider conviction (signals never blended)

**Files:**
- Modify: `engine/hunt_basket.py`
- Test: `tests/test_engine/test_hunt_basket.py`

Rank contract (spec §4.2): sort eligible names by `insider_score` desc, then cheapness percentile
desc (weak tiebreaker via `_percentile_ranks` of −ps/−pb over the eligible set; None when no
fundamentals), then symbol asc. `net_issuance` / `foreign_flow` never affect the order. Returns
`[(symbol, insider_score, cheapness_pct)]`.

- [ ] **Step 1: Write the failing test**

```python
def test_rank_by_insider_score_and_flags_do_not_change_order():
    from engine.hunt_basket import _rank_candidates

    insider = {
        "HI": _sig("HI", 2_000_000.0),
        "LO": _sig("LO", 100_000.0),
    }
    universe = {
        "HI": (_recs("HI", rev=[100, 100, 100, 100], ni=[5, 5, 5, 5], fcf=[5, 5, 5, 5],
                     gp=[30, 30, 30, 30], assets=100.0, eq=100.0, debt=10.0, sh=10.0, eps=1.0), 50.0),
        "LO": (_recs("LO", rev=[100, 100, 100, 100], ni=[5, 5, 5, 5], fcf=[5, 5, 5, 5],
                     gp=[30, 30, 30, 30], assets=100.0, eq=100.0, debt=10.0, sh=10.0, eps=1.0), 10.0),
    }
    eligible = ["HI", "LO"]
    # foreign_flow strongly favors LO; it must NOT change the insider-driven order
    foreign = {"LO": _sig("LO", 9_999_999.0, reason="foreign inflow")}
    ranked = _rank_candidates(eligible, insider, universe, foreign_flow=foreign, capital_signals=None)
    assert [r[0] for r in ranked] == ["HI", "LO"]  # insider score leads; flags are inert
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv python -m pytest tests/test_engine/test_hunt_basket.py -k rank -v`
Expected: FAIL with `ImportError: cannot import name '_rank_candidates'`

- [ ] **Step 3: Write minimal implementation** (append)

Add the imports at the top of the file (next to the existing imports):
```python
from engine.compounder import _flags, compute_metrics
from engine.core_basket import _equal_weights_capped, _percentile_ranks
```

Then append:
```python
def _cheapness_pcts(
    eligible: list[str],
    universe: dict[str, tuple[Sequence[FundamentalRecord], float]],
) -> dict[str, float | None]:
    """Percentile rank of cheapness (mean of -ps, -pb) over the eligible set. None when no data."""
    metrics: dict[str, dict[str, float | None]] = {}
    for s in eligible:
        u = universe.get(s)
        metrics[s] = compute_metrics(u[0], u[1]) if u else {}
    neg_ps = [(-m["ps"] if m.get("ps") is not None else None) for m in (metrics[s] for s in eligible)]
    neg_pb = [(-m["pb"] if m.get("pb") is not None else None) for m in (metrics[s] for s in eligible)]
    p_ps = dict(zip(eligible, _percentile_ranks(neg_ps), strict=True))
    p_pb = dict(zip(eligible, _percentile_ranks(neg_pb), strict=True))
    out: dict[str, float | None] = {}
    for s in eligible:
        parts = [p for p in (p_ps[s], p_pb[s]) if p is not None]
        out[s] = sum(parts) / len(parts) if parts else None
    return out


def _rank_candidates(
    eligible: list[str],
    insider_signals: dict[str, StrategySignal | None],
    universe: dict[str, tuple[Sequence[FundamentalRecord], float]],
    *,
    foreign_flow: dict[str, StrategySignal | None] | None,  # accepted but NOT used for ranking
    capital_signals: dict[str, StrategySignal | None] | None,  # accepted but NOT used for ranking
) -> list[tuple[str, float, float | None]]:
    """Sort by insider_score desc, cheapness pct desc (weak tiebreaker), symbol asc.
    foreign_flow / capital_signals are accepted for signature parity but never affect the order."""
    cheap = _cheapness_pcts(eligible, universe)
    rows: list[tuple[str, float, float | None]] = []
    for s in eligible:
        sig = insider_signals[s]
        score = sig.score if sig is not None else 0.0
        rows.append((s, score, cheap[s]))
    rows.sort(key=lambda r: (-r[1], -(r[2] if r[2] is not None else -1.0), r[0]))
    return rows
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv python -m pytest tests/test_engine/test_hunt_basket.py -k rank -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add engine/hunt_basket.py tests/test_engine/test_hunt_basket.py
git commit -m "feat(hunt-basket): insider-conviction rank (flags never blended)"
```

---

### Task 4: Flags + kill-thesis builders

**Files:**
- Modify: `engine/hunt_basket.py`
- Test: `tests/test_engine/test_hunt_basket.py`

Flags contract (spec §4.3): collect descriptive flags from foreign_flow (`"외국인순매수"` /
`"외국인순매도⚠"`), capital/net_issuance (`"자사주"` / `"희석⚠"`), and `_flags(metrics)`
(`high-debt`, etc.). Kill-thesis (spec §4.5): a non-price string citing the insider entry + distress
flags.

- [ ] **Step 1: Write the failing test**

```python
def test_flags_collects_descriptive_signals_and_distress():
    from engine.hunt_basket import _collect_flags

    metrics = {"debt_to_equity": 3.5, "fcf_margin": -0.1, "share_growth": 0.0, "margin_trend": 0.0}
    flags = _collect_flags(
        "X", metrics,
        foreign=_sig("X", 1.0, direction="long"),
        capital=_sig("X", 1.0, direction="short", reason="net issuance +12% (dilution) [large raise]"),
    )
    assert "외국인순매수" in flags
    assert "희석⚠" in flags
    assert "high-debt" in flags  # from engine.compounder._flags


def test_kill_thesis_is_fundamental_not_price():
    from engine.hunt_basket import _kill_thesis

    kt = _kill_thesis("insider buys: 3 by 2 in 90d", ("high-debt", "negative-fcf"))
    assert "내부자" in kt
    assert "청산" in kt
    assert "$" not in kt and "%" not in kt  # no price level / stop in the kill-thesis (0컷)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv python -m pytest tests/test_engine/test_hunt_basket.py -k "flags or kill" -v`
Expected: FAIL with `ImportError: cannot import name '_collect_flags'`

- [ ] **Step 3: Write minimal implementation** (append)

```python
def _collect_flags(
    symbol: str,
    metrics: dict[str, float | None],
    *,
    foreign: StrategySignal | None,
    capital: StrategySignal | None,
) -> tuple[str, ...]:
    """Descriptive flags only (never affect rank or weight)."""
    flags: list[str] = []
    if foreign is not None:
        flags.append("외국인순매수" if foreign.direction == "long" else "외국인순매도⚠")
    if capital is not None:
        if "large raise" in capital.reason:
            flags.append("대규모조달⚠")
        elif capital.direction == "long":
            flags.append("자사주")
        else:
            flags.append("희석⚠")
    if metrics:
        flags.extend(_flags(metrics))
    return tuple(flags)


def _kill_thesis(insider_reason: str, distress_flags: tuple[str, ...]) -> str:
    """Fundamental (non-price) exit condition: insider reversal OR a hard distress flag. 0컷."""
    distress = [f for f in distress_flags if f in {"high-debt", "negative-fcf", "high-dilution"}]
    distress_txt = f" OR distress({','.join(distress)})" if distress else ""
    return f"진입 근거=내부자 매수 ({insider_reason}); 청산=내부자 순매도 전환{distress_txt}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv python -m pytest tests/test_engine/test_hunt_basket.py -k "flags or kill" -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add engine/hunt_basket.py tests/test_engine/test_hunt_basket.py
git commit -m "feat(hunt-basket): descriptive flags + fundamental kill-thesis"
```

---

### Task 5: `select_hunt_basket` (size + survival math)

**Files:**
- Modify: `engine/hunt_basket.py`
- Test: `tests/test_engine/test_hunt_basket.py`

Select contract (spec §4.4): screen → rank → top `target_n` → sleeve-relative equal weight via
`_equal_weights_capped` → build `HuntHolding`s with fund_weight/flags/kill_thesis → `HuntBasket` with
the survival math (`sleeve_total_fund_weight`, `max_single_name_fund_loss`). Degenerate (<3 holdings)
warns. NO Kelly.

- [ ] **Step 1: Write the failing test**

```python
def _insider_universe(n):
    """n names each with a long insider signal (score descending) + fundamentals."""
    insider, universe = {}, {}
    for i in range(n):
        s = f"H{i:02d}"
        insider[s] = _sig(s, float(1_000_000 - i * 1000))
        universe[s] = (_recs(s, rev=[100, 100, 100, 100], ni=[5, 5, 5, 5], fcf=[5, 5, 5, 5],
                             gp=[30, 30, 30, 30], assets=100.0, eq=100.0, debt=10.0, sh=10.0,
                             eps=1.0), 30.0)
    return insider, universe


def test_select_six_names_equal_weight_and_survival_math():
    insider, universe = _insider_universe(8)
    basket = select_hunt_basket(insider, universe, target_n=6, max_per_name=0.40,
                                sleeve_fraction=0.15)
    assert len(basket.holdings) == 6
    assert abs(sum(h.weight for h in basket.holdings) - 1.0) < 1e-6
    for h in basket.holdings:
        assert abs(h.weight - 1 / 6) < 1e-9
        assert abs(h.fund_weight - (1 / 6) * 0.15) < 1e-9
    assert abs(basket.sleeve_total_fund_weight - 0.15) < 1e-6
    assert abs(basket.max_single_name_fund_loss - (1 / 6) * 0.15) < 1e-9
    assert basket.signal_eligible_count == 8


def test_select_degenerate_two_names_caps_and_warns():
    insider, universe = _insider_universe(2)
    with pytest.warns(UserWarning, match="degenerate"):
        basket = select_hunt_basket(insider, universe, target_n=6, max_per_name=0.40)
    # 1/2 = 0.5 > 0.40 cap -> each capped at 0.40, sum 0.80 (sleeve cash)
    for h in basket.holdings:
        assert abs(h.weight - 0.40) < 1e-9
    assert abs(sum(h.weight for h in basket.holdings) - 0.80) < 1e-6


def test_select_attaches_kill_thesis_and_excludes_signalless():
    insider, universe = _insider_universe(3)
    insider["NOPE"] = None
    basket = select_hunt_basket(insider, universe, target_n=6)
    assert all(h.kill_thesis for h in basket.holdings)
    assert "NOPE" in dict(basket.excluded)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv python -m pytest tests/test_engine/test_hunt_basket.py -k select -v`
Expected: FAIL with `NameError`/signature error (no `select_hunt_basket` body)

- [ ] **Step 3: Write minimal implementation** (append)

```python
def _rationale(insider_score: float, signal_flags: tuple[str, ...]) -> str:
    base = f"내부자 고확신 ${insider_score:,.0f}"
    extra = [f for f in signal_flags if f in {"외국인순매수", "자사주"}]
    if extra:
        base += " +" + "+".join(extra)
    return base


def select_hunt_basket(
    insider_signals: dict[str, StrategySignal | None],
    universe: dict[str, tuple[Sequence[FundamentalRecord], float]],
    *,
    foreign_flow: dict[str, StrategySignal | None] | None = None,
    capital_signals: dict[str, StrategySignal | None] | None = None,
    sectors: dict[str, str] | None = None,
    target_n: int = DEFAULT_TARGET_N,
    max_per_name: float = DEFAULT_MAX_PER_NAME,
    sleeve_fraction: float = DEFAULT_SLEEVE_FRACTION,
    as_of: date | None = None,
) -> HuntBasket:
    if target_n < 1:
        raise ValueError("target_n must be >= 1")
    if not 0.0 < max_per_name <= 1.0:
        raise ValueError("max_per_name must be in (0, 1]")
    if not 0.0 < sleeve_fraction <= 1.0:
        raise ValueError("sleeve_fraction must be in (0, 1]")

    eligible, excluded = _signal_eligible(insider_signals)
    ranked = _rank_candidates(
        eligible, insider_signals, universe, foreign_flow=foreign_flow,
        capital_signals=capital_signals,
    )
    chosen = ranked[:target_n]
    weights = _equal_weights_capped([r[0] for r in chosen], max_per_name)

    holdings: list[HuntHolding] = []
    for symbol, insider_score, _cheap in chosen:
        u = universe.get(symbol)
        metrics = compute_metrics(u[0], u[1]) if u else {}
        flags = _collect_flags(
            symbol, metrics,
            foreign=(foreign_flow or {}).get(symbol),
            capital=(capital_signals or {}).get(symbol),
        )
        sig = insider_signals[symbol]
        reason = sig.reason if sig is not None else ""
        w = weights[symbol]
        holdings.append(
            HuntHolding(
                symbol=symbol, weight=w, fund_weight=w * sleeve_fraction,
                insider_score=insider_score, insider_reason=reason, signal_flags=flags,
                sector=(sectors or {}).get(symbol), kill_thesis=_kill_thesis(reason, flags),
                rationale=_rationale(insider_score, flags),
            )
        )
    if 0 < len(holdings) < MIN_HOLDINGS_WARN:
        warnings.warn(
            f"hunt basket has only {len(holdings)} holdings (< {MIN_HOLDINGS_WARN}); "
            "the sleeve is degenerate — few signal-eligible candidates.",
            stacklevel=2,
        )
    fund_weights = [h.fund_weight for h in holdings]
    return HuntBasket(
        holdings=tuple(holdings), as_of=as_of, universe_size=len(universe),
        signal_eligible_count=len(eligible), target_n=target_n, max_per_name=max_per_name,
        sleeve_fraction=sleeve_fraction, sleeve_total_fund_weight=sum(fund_weights),
        max_single_name_fund_loss=max(fund_weights, default=0.0), excluded=tuple(excluded),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv python -m pytest tests/test_engine/test_hunt_basket.py -k select -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add engine/hunt_basket.py tests/test_engine/test_hunt_basket.py
git commit -m "feat(hunt-basket): select_hunt_basket with equal-weight sizing + survival math"
```

---

### Task 6: `format_hunt_basket` report (honest header + survival math)

**Files:**
- Modify: `engine/hunt_basket.py`
- Test: `tests/test_engine/test_hunt_basket.py`

Report contract (spec §4 / §1): Korean report whose header states the honest framing (candidate
surfacer / user conviction / signals unvalidated / insider-led / survival via sizing) and prints the
survival math (max single-name fund loss, sleeve total), then a holdings table (symbol, weight%,
fund%, insider score, flags, kill-thesis).

- [ ] **Step 1: Write the failing test**

```python
def test_format_contains_honest_header_and_survival_math():
    insider, universe = _insider_universe(6)
    basket = select_hunt_basket(insider, universe, target_n=6)
    txt = format_hunt_basket(basket)
    assert "후보" in txt  # candidate surfacer
    assert "미검증" in txt  # signals unvalidated
    assert "내부자" in txt  # insider-led
    assert "단일종목" in txt or "단일" in txt  # survival math headline
    assert "%" in txt
    assert basket.holdings[0].symbol in txt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv python -m pytest tests/test_engine/test_hunt_basket.py -k format -v`
Expected: FAIL with `NameError: name 'format_hunt_basket'` body missing

- [ ] **Step 3: Write minimal implementation** (append)

```python
def format_hunt_basket(basket: HuntBasket) -> str:
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("헌트 바스켓 (비대칭 상방 슬리브 ~15%)")
    lines.append(
        "정직한 프레이밍: 알파=사용자 재량 확신, 시스템=후보 발굴+생존 가드. "
        "신호 미검증 → 자동매수 아님, 최종 픽은 사용자."
    )
    lines.append(
        "insider 매수=유일 primary 스크린/랭크, net_issuance·foreign_flow=서술 플래그(점수 블렌드 금지). "
        "생존=작은 사이징+종목당 캡+kill-thesis(0컷), 위험은 사이징으로 관리."
    )
    lines.append("=" * 78)
    asof = basket.as_of.isoformat() if basket.as_of else "latest"
    lines.append(
        f"as_of={asof}  universe={basket.universe_size}  "
        f"signal_eligible={basket.signal_eligible_count}  target_n={basket.target_n}  "
        f"cap={basket.max_per_name:.0%}(sleeve)  sleeve={basket.sleeve_fraction:.0%} of fund"
    )
    lines.append(
        f"생존 수치: 단일종목 0 → 펀드 {basket.max_single_name_fund_loss * 100:.1f}% 손실, "
        f"슬리브 전멸 → 펀드 {basket.sleeve_total_fund_weight * 100:.1f}% 손실  | "
        f"excluded={len(basket.excluded)}"
    )
    lines.append("-" * 78)
    lines.append(f"{'SYM':<8}{'W%':>7}{'FUND%':>7}{'INSIDER$':>14}  FLAGS / KILL-THESIS")
    for h in basket.holdings:
        flags = ",".join(h.signal_flags) if h.signal_flags else "-"
        lines.append(
            f"{h.symbol:<8}{h.weight * 100:>6.2f}{h.fund_weight * 100:>7.2f}"
            f"{h.insider_score:>14,.0f}  [{flags}] {h.kill_thesis}"
        )
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv python -m pytest tests/test_engine/test_hunt_basket.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: ruff + mypy clean, then commit**

```bash
ruff check engine/hunt_basket.py tests/test_engine/test_hunt_basket.py
mypy engine/hunt_basket.py
git add engine/hunt_basket.py tests/test_engine/test_hunt_basket.py
git commit -m "feat(hunt-basket): format_hunt_basket report with honest header + survival math"
```

---

### Task 7: Edge tests (empty / no-eligible / flags-inert)

**Files:**
- Modify: `tests/test_engine/test_hunt_basket.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_select_empty_universe_no_crash():
    basket = select_hunt_basket({}, {}, target_n=6)
    assert basket.holdings == ()
    assert basket.signal_eligible_count == 0
    assert basket.max_single_name_fund_loss == 0.0


def test_select_no_eligible_when_all_signals_none():
    insider = {"A": None, "B": None}
    universe = {}
    basket = select_hunt_basket(insider, universe, target_n=6)
    assert basket.holdings == ()
    assert len(basket.excluded) == 2


def test_capital_dilution_flag_does_not_change_rank():
    insider, universe = _insider_universe(3)
    # heavy dilution flag on the top name must not demote it (flags are inert for rank)
    capital = {"H00": _sig("H00", 1.0, direction="short", reason="net issuance +20% (dilution)")}
    basket = select_hunt_basket(insider, universe, target_n=6, capital_signals=capital)
    assert basket.holdings[0].symbol == "H00"  # still first (highest insider score)
    assert "희석⚠" in basket.holdings[0].signal_flags
```

- [ ] **Step 2: Run tests to verify they fail then pass**

Run: `.venv python -m pytest tests/test_engine/test_hunt_basket.py -k "empty or no_eligible or dilution" -v`
Expected: the three pass once the engine from Tasks 1–6 is in place (they exercise existing behavior).

- [ ] **Step 3: Full file + lint**

Run: `.venv python -m pytest tests/test_engine/test_hunt_basket.py -q` → all pass.
Run: `ruff check engine/hunt_basket.py tests/test_engine/test_hunt_basket.py` and `mypy engine/hunt_basket.py` → clean.

- [ ] **Step 4: Commit**

```bash
git add tests/test_engine/test_hunt_basket.py
git commit -m "test(hunt-basket): edge cases (empty, no-eligible, flag inertness)"
```

---

### Task 8: Driver script `scripts/hunt_basket.py`

**Files:**
- Create: `scripts/hunt_basket.py`

Driver contract (spec §5): resolve one PIT cutoff (explicit `--as-of` or price snapshot's latest
date), build insider signals from the catalog (`get_insider_trades` → `insider_buying_signal`) and
flag signals from pinned fundamentals (`net_issuance_signal`), assemble `universe`, run
`select_hunt_basket`, print `format_hunt_basket`. Mirror `scripts/core_basket.py` for snapshot/PIT
loading. No test (I/O script; validated by smoke run).

- [ ] **Step 1: Re-read the core driver for the exact loader calls**

Run: `grep -n "read_fundamentals_snapshot\|read_price_snapshot\|load_symbols\|load_sectors\|effective\|to_datetime\|cov_min\|cov_max" scripts/core_basket.py`
Copy its snapshot-load + PIT-cutoff resolution + coverage-validation pattern verbatim (do not invent loader names).

- [ ] **Step 2: Find the catalog accessor for insider trades**

Run: `grep -n "def get_insider_trades\|class .*Catalog\|def __init__" data/catalog.py | head`
Note the exact `get_insider_trades(symbol, market="us", as_of=<datetime>, limit=0)` signature and how to open the catalog (DB path `/Users/jjuni/재무관리 모델/trader/data/store/trader.duckdb`).

- [ ] **Step 3: Write the driver**

```python
"""Driver: build the hunt basket from catalog insider trades + pinned snapshots (PIT) and print it.

Pure engine in engine/hunt_basket.py; this script carries all I/O (catalog insider trades, pinned
fundamentals/prices, sectors) and the PIT as_of discipline. Mirrors scripts/core_basket.py.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.catalog import Catalog  # noqa: E402  (use the actual class name found in Step 2)
from data.fundamentals_snapshot import read_fundamentals_snapshot  # noqa: E402
from data.models import FundamentalRecord, StrategySignal  # noqa: E402
from data.price_snapshot import read_price_snapshot  # noqa: E402
from engine.hunt_basket import format_hunt_basket, select_hunt_basket  # noqa: E402
from signals.capital import net_issuance_signal  # noqa: E402
from signals.insider import insider_buying_signal  # noqa: E402

DEFAULT_UNIVERSE = ROOT / "data" / "universes" / "sp400-600-current.csv"
DEFAULT_SNAPSHOT = ROOT / "data" / "snapshots" / "fundamentals-2026-06-01-gp2.csv"
DEFAULT_PRICES = ROOT / "data" / "snapshots" / "prices-2026-06-01.csv"
DEFAULT_SECTORS = ROOT / "data" / "sectors" / "sp400-600-current-sectors.csv"
DEFAULT_DB = Path("/Users/jjuni/재무관리 모델/trader/data/store/trader.duckdb")


def load_symbols(path: Path) -> list[str]:
    with path.open(encoding="utf-8") as f:
        return sorted({r["symbol"].upper() for r in csv.DictReader(f)})


def load_sectors(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[r["symbol"].upper()] = r.get("sector") or "unknown"
    return out


def _price_asof(closes, symbol: str, as_of: date) -> float | None:
    if symbol not in closes.columns:
        return None
    import pandas as pd

    s = closes[symbol].dropna().loc[: pd.Timestamp(as_of)]
    if s.empty:
        return None
    val = float(s.iloc[-1])
    return val if val > 0 else None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Hunt basket selector (PIT, catalog insider + snapshots)")
    p.add_argument("--as-of", type=str, default=None)
    p.add_argument("--target-n", type=int, default=6)
    p.add_argument("--max-per-name", type=float, default=0.40)
    p.add_argument("--sleeve-fraction", type=float, default=0.15)
    p.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    p.add_argument("--prices", type=Path, default=DEFAULT_PRICES)
    p.add_argument("--universe-csv", type=Path, default=DEFAULT_UNIVERSE)
    p.add_argument("--sectors-csv", type=Path, default=DEFAULT_SECTORS)
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = p.parse_args(argv)

    import pandas as pd

    for label, path in (("snapshot", args.snapshot), ("prices", args.prices)):
        if not path.exists():
            raise SystemExit(f"{label} not found: {path} (snapshot CSVs are gitignored; regenerate)")

    symbols = load_symbols(args.universe_csv)
    sectors = load_sectors(args.sectors_csv)

    funds: dict[str, list[FundamentalRecord]] = {}
    for rec in read_fundamentals_snapshot(args.snapshot, verify=True):
        funds.setdefault(rec.symbol.upper(), []).append(rec)
    for recs in funds.values():
        recs.sort(key=lambda r: r.asof_ts)

    closes = read_price_snapshot(args.prices, verify=True)
    closes.index = pd.to_datetime(closes.index)
    cov_min, cov_max = closes.index.min().date(), closes.index.max().date()
    effective = datetime.fromisoformat(args.as_of).date() if args.as_of else cov_max
    if effective < cov_min or effective > cov_max:
        raise SystemExit(f"as_of {effective} outside price coverage {cov_min}..{cov_max}")
    cutoff_dt = datetime(effective.year, effective.month, effective.day, 23, 59, 59)

    catalog = Catalog(str(args.db))
    insider_signals: dict[str, StrategySignal | None] = {}
    capital_signals: dict[str, StrategySignal | None] = {}
    universe: dict[str, tuple[Sequence[FundamentalRecord], float]] = {}
    for sym in symbols:
        trades = catalog.get_insider_trades(sym, market="us", as_of=cutoff_dt, limit=0)
        insider_signals[sym] = insider_buying_signal(trades, as_of=effective)
        recs = [r for r in funds.get(sym, []) if r.asof_ts.date() <= effective]
        if recs:
            capital_signals[sym] = net_issuance_signal(recs, as_of=effective)
            price = _price_asof(closes, sym, effective)
            if price is not None:
                universe[sym] = (recs, price)

    basket = select_hunt_basket(
        insider_signals, universe, capital_signals=capital_signals, sectors=sectors,
        target_n=args.target_n, max_per_name=args.max_per_name,
        sleeve_fraction=args.sleeve_fraction, as_of=effective,
    )
    print(format_hunt_basket(basket))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

**Note:** In Steps 1–2 confirm the real `Catalog` class name and `get_insider_trades` signature; if they differ, adjust the import and call (do not fabricate). If the catalog has no insider rows for the universe at the cutoff, the basket will be empty — that is acceptable for the smoke run (the pure engine is the deliverable).

- [ ] **Step 4: Smoke-run the driver**

Run: `.venv python -m scripts.hunt_basket --as-of 2026-06-01 --snapshot ../trader/data/snapshots/fundamentals-2026-06-01-gp2.csv --prices ../trader/data/snapshots/prices-2026-06-01.csv | head -25`
Expected: the honest-framing header + survival math + a holdings table (or an empty basket if no insider rows match the universe at the cutoff — acceptable).

- [ ] **Step 5: ruff clean + commit**

```bash
ruff check scripts/hunt_basket.py
git add scripts/hunt_basket.py
git commit -m "feat(hunt-basket): catalog-insider + snapshot PIT driver script"
```

---

### Task 9: Docs update (VALUATION.md)

**Files:**
- Modify: `docs/VALUATION.md` (append a "헌트 바스켓" section after the core-basket section).

Per project convention (memory: "문서 동반 갱신 규칙"), a T2+ feature updates docs.

- [ ] **Step 1: Append the section**

Append a "## 헌트 바스켓" section to `docs/VALUATION.md` describing: role (~15% asymmetric sleeve),
honest framing (candidate surfacer, user conviction, signals unvalidated → insider-only screen, no
blended score, no Kelly), selection (insider event screen + conviction rank + descriptive flags),
sizing (small equal-weight + per-name cap + fund-level survival math), kill-thesis (0컷), the
`select_hunt_basket` / `format_hunt_basket` API, and the driver command.

- [ ] **Step 2: Commit**

```bash
git add docs/VALUATION.md
git commit -m "docs(hunt-basket): document hunt basket engine + driver"
```

---

## Self-Review

**Spec coverage:**
- §1 honest framing → Task 1 docstring + Task 6 header (test asserts header content). ✓
- §3 reuse (import-only) → Task 3 imports `_flags`/`compute_metrics`/`_percentile_ranks`/`_equal_weights_capped`. ✓
- §4.1 screen (insider-event gate, no distress exclusion) → Task 2 (incl. the keep-high-debt inversion test). ✓
- §4.1 universe-vs-signals key set + optional fundamentals → Task 3 `_cheapness_pcts` uses `universe.get`, Task 5 uses `universe.get` for flags. ✓
- §4.2 rank (insider only, flags never blended) → Task 3 (incl. flip-foreign-flow inertness test). ✓
- §4.3 flags → Task 4 `_collect_flags`. §4.5 kill-thesis → Task 4 `_kill_thesis` (no-price test). ✓
- §4.4 size + survival math + no Kelly → Task 5 (equal-weight, degenerate-warn, survival fields). ✓
- §5 driver (PIT, catalog insider) → Task 8. §6 tests → woven in + Task 7. §7 boundaries → file structure. ✓
- §8 deferred (forward ledger, rebalancer, foreign IC, live wiring) → not implemented (correct). ✓

**Placeholder scan:** Task 8 marks the two discover-from-source points (Catalog class name, get_insider_trades signature) explicitly with instructions to confirm/adjust — the rest is full code. No TBD/TODO.

**Type consistency:** `HuntHolding`/`HuntBasket` fields defined in Task 1 are used identically in Tasks 5/6. `_signal_eligible` (Task 2) returns `(list, list)` consumed by `_rank_candidates` (Task 3) and `select_hunt_basket` (Task 5). `_rank_candidates` returns `[(symbol, score, cheapness)]` consumed by Task 5. `_collect_flags`/`_kill_thesis` signatures (Task 4) match their Task 5 calls. `select_hunt_basket`/`format_hunt_basket` signatures match the spec §4 verbatim. `_equal_weights_capped(symbols, max_weight)` and `_percentile_ranks(values)` are the real core_basket signatures. ✓

---

## Final verification (after all tasks)

- Run only `tests/test_engine/test_hunt_basket.py` (full `pytest` forbidden per memory: exit 144).
- Adversarial multi-lens review (5–6 lenses) + `codex review --uncommitted "한국어로 답변"` + `codex review --uncommitted "적대적 리뷰: 버그/보안/엣지/로직 오류. 한국어로 답변"` (project signature pattern). Fix findings, re-commit.
- Update memory `project_jaemu_trader.md` + `CONTEXT.md` with the hunt-basket increment.
