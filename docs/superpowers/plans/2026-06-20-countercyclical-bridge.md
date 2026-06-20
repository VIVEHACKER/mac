# Countercyclical Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** build `engine/countercyclical_bridge.py` — a rule-based dry-powder deployment policy that buys more of the value-screened core anchor when market drawdown AND core cheapness both hold, in tranches, composed through `engine/fund_book.py`.

**Architecture:** pure engine (drawdown math + step ladder + a `SleeveTarget` builder) reusing fund_book's cap/leverage rails + a PIT driver. No new alpha claim, no new data, no leverage.

**Tech Stack:** Python 3.x, stdlib only (statistics.median), pytest. Test runner: `.venv/bin/python -m pytest`.

Spec: `docs/superpowers/specs/2026-06-20-countercyclical-bridge-design.md`.

---

## File Structure

- Create: `engine/countercyclical_bridge.py` — dataclass `BridgeDeployment`; `market_drawdown`, `ladder_fraction`, `compute_deployment`, `default_value_gate`, `bridge_sleeve_target`, `format_deployment`; `DEFAULT_LADDER`.
- Create: `tests/test_engine/test_countercyclical_bridge.py` — all engine tests incl. fund_book integration.
- Create: `scripts/countercyclical_bridge.py` — PIT driver.
- Modify: `docs/COMPOUNDER_OPERATIONS.md` — add the "반순환 브릿지" section.

---

### Task 1: Drawdown math + deployment dataclass

**Files:**
- Create: `engine/countercyclical_bridge.py`
- Test: `tests/test_engine/test_countercyclical_bridge.py`

- [ ] **Step 1: Write failing tests**

```python
from __future__ import annotations

import pytest

from engine.countercyclical_bridge import market_drawdown


def test_flat_series_has_zero_drawdown():
    assert market_drawdown([100.0, 100.0, 100.0]) == pytest.approx(0.0)


def test_monotone_up_series_has_zero_drawdown():
    # peak == last -> no drawdown
    assert market_drawdown([90.0, 95.0, 100.0]) == pytest.approx(0.0)


def test_off_peak_drawdown_is_peak_to_last():
    # peak 100, last 75 -> 25%
    assert market_drawdown([80.0, 100.0, 75.0]) == pytest.approx(0.25)


def test_deep_crash_clamps_to_one():
    # last below zero is impossible for prices, but guard clamps to <= 1
    assert market_drawdown([100.0, 0.0]) == pytest.approx(1.0)


def test_empty_series_raises():
    with pytest.raises(ValueError):
        market_drawdown([])


def test_non_positive_peak_raises():
    with pytest.raises(ValueError):
        market_drawdown([0.0, 0.0])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_engine/test_countercyclical_bridge.py -q`
Expected: FAIL (ImportError: cannot import name 'market_drawdown').

- [ ] **Step 3: Write minimal implementation**

```python
"""Countercyclical bridge: rule-based dry-powder deployment into the core anchor.

HONEST FRAMING — read before changing anything:
This makes NO market-timing alpha claim. It does not predict bottoms. It is a rule-based dry-powder
deployment policy: when a market index has drawn down from its trailing peak AND the core anchor's own
valuations are cheap, it deploys a budget-capped slice of reserve cash into the EXISTING, already
value-screened core basket — buying the same durable names cheaper, in tranches (robust to being early).
It invents no signal, picks no new names, and scales the core's existing weights only. The value gate
is an AND (falling-knife guard): deployment is 0 whenever the gate is closed, however deep the drawdown.
Budget-capped and composed through fund_book's 8% per-name cap + zero-leverage rails.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from statistics import median

from engine.fund_book import SleeveTarget

# (drawdown_threshold, cumulative_fraction_of_budget), thresholds strictly ascending in (0, 1].
DEFAULT_LADDER: tuple[tuple[float, float], ...] = (
    (0.10, 1.0 / 3.0),
    (0.20, 2.0 / 3.0),
    (0.30, 1.0),
)


@dataclass(frozen=True)
class BridgeDeployment:
    deployed_fraction: float  # fund-level fraction to deploy now (0 <= x <= budget)
    budget: float  # bridge_budget: max fund fraction the bridge may ever deploy
    drawdown: float  # clamped market drawdown in [0, 1]
    value_gate_open: bool
    tranche_index: int  # rung reached, 0..len(ladder)
    n_tranches: int
    reason: str  # Korean one-liner


def market_drawdown(prices: Sequence[float]) -> float:
    """Peak-to-last drawdown over the GIVEN series (caller passes a trailing, PIT-sliced slice).

    (peak - last) / peak, clamped to [0, 1]. Empty series or non-positive peak -> ValueError
    (a degenerate price history must not silently read as 'no drawdown')."""
    if not prices:
        raise ValueError("market_drawdown: empty price series")
    peak = max(prices)
    if peak <= 0.0:
        raise ValueError(f"market_drawdown: non-positive peak {peak}")
    dd = (peak - prices[-1]) / peak
    return min(max(dd, 0.0), 1.0)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_engine/test_countercyclical_bridge.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add engine/countercyclical_bridge.py tests/test_engine/test_countercyclical_bridge.py
git commit -m "feat(bridge): peak-to-last market drawdown + deployment dataclass"
```

---

### Task 2: Ladder + compute_deployment (policy core, gate short-circuit, validation)

**Files:**
- Modify: `engine/countercyclical_bridge.py`
- Test: `tests/test_engine/test_countercyclical_bridge.py`

- [ ] **Step 1: Write failing tests** (append)

```python
from engine.countercyclical_bridge import compute_deployment, ladder_fraction


def test_below_first_threshold_deploys_zero():
    d = compute_deployment(0.05, True, budget=0.15)
    assert d.deployed_fraction == pytest.approx(0.0)
    assert d.tranche_index == 0


@pytest.mark.parametrize(
    "dd,expected_frac_of_budget,rung",
    [
        (0.099, 0.0, 0),
        (0.10, 1.0 / 3.0, 1),
        (0.199, 1.0 / 3.0, 1),
        (0.20, 2.0 / 3.0, 2),
        (0.299, 2.0 / 3.0, 2),
        (0.30, 1.0, 3),
        (0.50, 1.0, 3),
    ],
)
def test_ladder_rung_boundaries(dd, expected_frac_of_budget, rung):
    d = compute_deployment(dd, True, budget=0.15)
    assert d.deployed_fraction == pytest.approx(0.15 * expected_frac_of_budget)
    assert d.tranche_index == rung


def test_gate_closed_deploys_zero_at_every_drawdown():
    for dd in (0.0, 0.15, 0.25, 0.35, 0.60):
        d = compute_deployment(dd, False, budget=0.15)
        assert d.deployed_fraction == pytest.approx(0.0)
        assert d.tranche_index == 0
        assert d.value_gate_open is False


def test_deployed_never_exceeds_budget_over_sweep():
    for i in range(61):
        dd = i / 100.0
        d = compute_deployment(dd, True, budget=0.15)
        assert 0.0 <= d.deployed_fraction <= 0.15 + 1e-12


def test_drawdown_clamped_above_one():
    d = compute_deployment(1.5, True, budget=0.15)
    assert d.drawdown == pytest.approx(1.0)
    assert d.deployed_fraction == pytest.approx(0.15)


def test_budget_out_of_range_raises():
    with pytest.raises(ValueError):
        compute_deployment(0.25, True, budget=1.5)
    with pytest.raises(ValueError):
        compute_deployment(0.25, True, budget=-0.1)


def test_non_ascending_ladder_thresholds_raise():
    with pytest.raises(ValueError):
        compute_deployment(0.25, True, budget=0.15, ladder=((0.20, 0.5), (0.10, 1.0)))


def test_non_monotone_cumulative_raises():
    with pytest.raises(ValueError):
        compute_deployment(0.25, True, budget=0.15, ladder=((0.10, 0.8), (0.20, 0.5)))


def test_cumulative_above_one_raises():
    with pytest.raises(ValueError):
        compute_deployment(0.25, True, budget=0.15, ladder=((0.10, 0.5), (0.20, 1.2)))


def test_threshold_out_of_unit_range_raises():
    with pytest.raises(ValueError):
        compute_deployment(0.25, True, budget=0.15, ladder=((0.0, 0.5),))


def test_ladder_fraction_helper():
    assert ladder_fraction(0.05, DEFAULT_LADDER) == pytest.approx(0.0)
    assert ladder_fraction(0.25, DEFAULT_LADDER) == pytest.approx(2.0 / 3.0)
```

(`DEFAULT_LADDER` is already imported transitively; add `from engine.countercyclical_bridge import DEFAULT_LADDER` to the test imports.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_engine/test_countercyclical_bridge.py -q`
Expected: FAIL (ImportError: cannot import name 'compute_deployment').

- [ ] **Step 3: Write minimal implementation** (append to `engine/countercyclical_bridge.py`)

```python
_TOL = 1e-9


def _validate_ladder(ladder: tuple[tuple[float, float], ...]) -> None:
    if not ladder:
        raise ValueError("ladder must be non-empty")
    prev_t = 0.0
    prev_c = 0.0
    for t, c in ladder:
        if not 0.0 < t <= 1.0:
            raise ValueError(f"ladder threshold {t} out of (0, 1]")
        if t <= prev_t:
            raise ValueError(f"ladder thresholds must be strictly ascending (got {t} after {prev_t})")
        if not 0.0 <= c <= 1.0 + _TOL:
            raise ValueError(f"ladder cumulative fraction {c} out of [0, 1]")
        if c < prev_c - _TOL:
            raise ValueError(f"ladder cumulative fractions must be non-decreasing (got {c} after {prev_c})")
        prev_t, prev_c = t, c


def ladder_fraction(drawdown: float, ladder: tuple[tuple[float, float], ...]) -> float:
    """Cumulative budget fraction for the deepest ladder threshold <= drawdown (0 if below first)."""
    frac = 0.0
    rung = 0
    for i, (t, c) in enumerate(ladder, start=1):
        if drawdown >= t:
            frac = c
            rung = i
    return frac


def _rung_index(drawdown: float, ladder: tuple[tuple[float, float], ...]) -> int:
    rung = 0
    for i, (t, _c) in enumerate(ladder, start=1):
        if drawdown >= t:
            rung = i
    return rung


def compute_deployment(
    drawdown: float,
    value_gate_open: bool,
    *,
    budget: float,
    ladder: tuple[tuple[float, float], ...] = DEFAULT_LADDER,
) -> BridgeDeployment:
    """Map (drawdown, value gate) -> a budget-capped deployed fraction via the step ladder. Gate
    closed -> 0 (falling-knife guard). Fail-closed on out-of-range budget / malformed ladder."""
    if not 0.0 <= budget <= 1.0:
        raise ValueError(f"budget {budget} out of [0, 1]")
    _validate_ladder(ladder)
    dd = min(max(drawdown, 0.0), 1.0)
    n = len(ladder)
    if not value_gate_open:
        return BridgeDeployment(
            deployed_fraction=0.0,
            budget=budget,
            drawdown=dd,
            value_gate_open=False,
            tranche_index=0,
            n_tranches=n,
            reason=f"게이트 닫힘(코어 cheapness 미달) → dd={dd:.1%}여도 배치 0 (falling-knife 가드)",
        )
    frac = ladder_fraction(dd, ladder)
    rung = _rung_index(dd, ladder)
    deployed = budget * frac
    return BridgeDeployment(
        deployed_fraction=deployed,
        budget=budget,
        drawdown=dd,
        value_gate_open=True,
        tranche_index=rung,
        n_tranches=n,
        reason=(
            f"dd={dd:.1%} (tranche {rung}/{n}), 게이트 열림 → budget {budget:.1%}의 "
            f"{frac:.1%}={deployed:.1%} 배치"
        ),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_engine/test_countercyclical_bridge.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add engine/countercyclical_bridge.py tests/test_engine/test_countercyclical_bridge.py
git commit -m "feat(bridge): step-ladder deployment policy + AND value-gate short-circuit"
```

---

### Task 3: default_value_gate (median core cheapness, None-safe, empty -> False)

**Files:**
- Modify: `engine/countercyclical_bridge.py`
- Test: `tests/test_engine/test_countercyclical_bridge.py`

- [ ] **Step 1: Write failing tests** (append)

```python
from engine.core_basket import CoreBasket, CoreHolding
from engine.countercyclical_bridge import default_value_gate


def _holding(symbol: str, cheapness: float | None) -> CoreHolding:
    return CoreHolding(
        symbol=symbol,
        weight=0.077,
        composite=0.5,
        display_score=50.0,
        cheapness_pct=cheapness,
        gp_pct=0.5,
        sector="Tech",
        flags=(),
        rationale="",
    )


def _basket(*cheapness: float | None) -> CoreBasket:
    holdings = tuple(_holding(f"S{i}", c) for i, c in enumerate(cheapness))
    return CoreBasket(
        holdings=holdings,
        as_of=None,
        universe_size=len(holdings),
        eligible_count=len(holdings),
        excluded=(),
    )


def test_value_gate_open_when_median_cheapness_at_or_above_threshold():
    assert default_value_gate(_basket(0.6, 0.7, 0.8), threshold=0.55) is True


def test_value_gate_closed_when_median_cheapness_below_threshold():
    assert default_value_gate(_basket(0.2, 0.3, 0.4), threshold=0.55) is False


def test_value_gate_ignores_none_cheapness():
    # present values 0.6, 0.7 -> median 0.65 >= 0.55
    assert default_value_gate(_basket(0.6, None, 0.7), threshold=0.55) is True


def test_value_gate_all_none_is_closed():
    assert default_value_gate(_basket(None, None), threshold=0.55) is False


def test_value_gate_empty_basket_is_closed():
    assert default_value_gate(_basket(), threshold=0.55) is False
```

NOTE: confirm `CoreBasket`'s field names by reading `engine/core_basket.py` (the `_basket` helper above
must match its constructor exactly — `holdings, as_of, universe_size, eligible_count, excluded`). If the
real constructor differs, adjust the helper; do NOT change `core_basket.py`.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_engine/test_countercyclical_bridge.py -q -k value_gate`
Expected: FAIL (ImportError: cannot import name 'default_value_gate').

- [ ] **Step 3: Write minimal implementation** (append)

```python
def default_value_gate(core_basket: "CoreBasket", *, threshold: float = 0.55) -> bool:  # noqa: F821
    """True iff the median of the core holdings' present cheapness_pct >= threshold. None cheapness is
    ignored; an empty basket or all-None cheapness -> False (no assessable anchor -> gate closed,
    conservative)."""
    present = [h.cheapness_pct for h in core_basket.holdings if h.cheapness_pct is not None]
    if not present:
        return False
    return median(present) >= threshold
```

Add the import guard at the top of the file (TYPE_CHECKING to avoid a runtime cycle since fund_book is
already imported and core_basket pulls in heavier deps):

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.core_basket import CoreBasket
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_engine/test_countercyclical_bridge.py -q -k value_gate`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add engine/countercyclical_bridge.py tests/test_engine/test_countercyclical_bridge.py
git commit -m "feat(bridge): core-cheapness median value gate (None-safe, empty->closed)"
```

---

### Task 4: bridge_sleeve_target + format_deployment

**Files:**
- Modify: `engine/countercyclical_bridge.py`
- Test: `tests/test_engine/test_countercyclical_bridge.py`

- [ ] **Step 1: Write failing tests** (append)

```python
from engine.countercyclical_bridge import bridge_sleeve_target, format_deployment
from engine.fund_book import SleeveTarget


def test_bridge_sleeve_target_scales_core_weights():
    d = compute_deployment(0.25, True, budget=0.15)  # deploys 0.10
    sleeve = bridge_sleeve_target(d, {"A": 0.5, "B": 0.5})
    assert isinstance(sleeve, SleeveTarget)
    assert sleeve.name == "bridge"
    assert sleeve.fraction == pytest.approx(0.10)
    assert sleeve.weights == {"A": 0.5, "B": 0.5}


def test_bridge_sleeve_target_zero_deployment_is_valid_sleeve():
    d = compute_deployment(0.05, True, budget=0.15)  # deploys 0
    sleeve = bridge_sleeve_target(d, {"A": 1.0})
    assert sleeve.fraction == pytest.approx(0.0)
    assert sleeve.weights == {"A": 1.0}


def test_format_deployment_restates_framing():
    d = compute_deployment(0.25, True, budget=0.15)
    text = format_deployment(d)
    assert "알파" in text  # honest framing header present
    assert "배치" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_engine/test_countercyclical_bridge.py -q -k "sleeve_target or format"`
Expected: FAIL (ImportError).

- [ ] **Step 3: Write minimal implementation** (append)

```python
def bridge_sleeve_target(
    deployment: BridgeDeployment, core_weights: dict[str, float]
) -> SleeveTarget:
    """A SleeveTarget for fund_book whose fraction is the deployed dry powder and whose weights ARE the
    core's weights — the bridge scales the same anchor (no new names). Returned even at fraction 0
    ('armed, not deployed')."""
    return SleeveTarget("bridge", deployment.deployed_fraction, dict(core_weights))


def format_deployment(deployment: BridgeDeployment) -> str:
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("반순환 브릿지 (dry-powder 배치 — 코어 앵커 폭락매수)")
    lines.append(
        "정직한 프레이밍: 마켓타이밍 알파 주장 없음. 시장 하락 AND 코어 cheapness 동시 충족 시 "
        "budget 한도 내에서 가치-스크린된 코어를 tranche로 더 사는 규칙 기반 배치. 무레버리지."
    )
    lines.append("=" * 78)
    lines.append(
        f"drawdown={deployment.drawdown:.1%}  gate={'열림' if deployment.value_gate_open else '닫힘'}  "
        f"tranche={deployment.tranche_index}/{deployment.n_tranches}  "
        f"budget={deployment.budget:.1%}  deployed={deployment.deployed_fraction:.1%}"
    )
    lines.append("-" * 78)
    lines.append(deployment.reason)
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_engine/test_countercyclical_bridge.py -q`
Expected: PASS (full file).

- [ ] **Step 5: Commit**

```bash
git add engine/countercyclical_bridge.py tests/test_engine/test_countercyclical_bridge.py
git commit -m "feat(bridge): SleeveTarget builder + Korean deployment report"
```

---

### Task 5: Integration with fund_book (core + hunt + bridge)

**Files:**
- Test: `tests/test_engine/test_countercyclical_bridge.py`

- [ ] **Step 1: Write failing/forcing test** (append)

```python
from engine.fund_book import assemble_fund_book


def test_bridge_sums_into_core_and_respects_cap_via_fund_book():
    core_weights = {"A": 0.5, "B": 0.5}
    d = compute_deployment(0.35, True, budget=0.15)  # full budget -> 0.15
    core = SleeveTarget("core", 0.35, core_weights)
    hunt = SleeveTarget("hunt", 0.15, {"C": 1.0})
    bridge = bridge_sleeve_target(d, core_weights)
    book = assemble_fund_book([core, hunt, bridge], max_name_weight=0.08)
    w = {p.symbol: p.fund_weight for p in book.positions}
    # A: core 0.35*0.5=0.175 + bridge 0.15*0.5=0.075 = 0.25 -> capped at 0.08
    assert w["A"] == pytest.approx(0.08)
    assert any(p.symbol == "A" and p.capped for p in book.positions)
    # C: hunt 0.15*1.0=0.15 -> capped at 0.08
    assert w["C"] == pytest.approx(0.08)
    # leverage guard intact: fractions 0.35+0.15+0.15=0.65 <= 1.0
    assert book.reserve_cash > 0.0


def test_zero_deployment_bridge_does_not_change_book():
    core_weights = {"A": 1.0}
    d = compute_deployment(0.0, True, budget=0.15)  # deploys 0
    book_without = assemble_fund_book([SleeveTarget("core", 0.35, core_weights)], max_name_weight=1.0)
    book_with = assemble_fund_book(
        [SleeveTarget("core", 0.35, core_weights), bridge_sleeve_target(d, core_weights)],
        max_name_weight=1.0,
    )
    assert book_with.invested == pytest.approx(book_without.invested)
```

- [ ] **Step 2: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_engine/test_countercyclical_bridge.py -q`
Expected: PASS (uses only already-built functions — this locks in the cross-engine contract).

- [ ] **Step 3: Commit**

```bash
git add tests/test_engine/test_countercyclical_bridge.py
git commit -m "test(bridge): fund_book composition — core+bridge sum, 8% cap, no leverage"
```

---

### Task 6: PIT driver

**Files:**
- Create: `scripts/countercyclical_bridge.py`

- [ ] **Step 1: Write the driver** (model on `scripts/fund_book.py` — same imports/wiring)

```python
"""Driver: evaluate the countercyclical bridge at one PIT as_of and (optionally) assemble the full book.

Pure engine in engine/countercyclical_bridge.py; this script wires a market index price series
(PIT-sliced to a trailing window <= as_of) and the already-tested core basket, computes the drawdown +
value gate, prints the deployment, and assembles [core(0.35), hunt(0.15), bridge(dep)] via fund_book.
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.catalog import MarketDataCatalog  # noqa: E402
from engine.core_basket import select_core_basket  # noqa: E402
from engine.countercyclical_bridge import (  # noqa: E402
    bridge_sleeve_target,
    compute_deployment,
    default_value_gate,
    format_deployment,
    market_drawdown,
)
from engine.fund_book import SleeveTarget, assemble_fund_book, format_fund_book  # noqa: E402
from engine.hunt_basket import select_hunt_basket  # noqa: E402
from scripts.core_basket import build_universe  # noqa: E402
from scripts.fund_book import (  # noqa: E402
    DEFAULT_PRICES,
    DEFAULT_SECTORS,
    DEFAULT_SNAPSHOT,
    DEFAULT_UNIVERSE,
)
from scripts.hunt_basket import build_hunt_inputs  # noqa: E402

DEFAULT_DB = Path("/Users/jjuni/재무관리 모델/trader/data/store/trader.duckdb")
DEFAULT_MARKET_CSV = ROOT / "data" / "snapshots" / "spy-history.csv"


def load_market_prices(path: Path, as_of: date | None, window: int) -> list[float]:
    """Read (date, close) rows, keep rows <= as_of, return the last `window` closes oldest->newest.

    CSV must have a 'date' (YYYY-MM-DD) and a 'close' column. PIT: nothing after as_of is used."""
    rows: list[tuple[date, float]] = []
    with path.open(newline="") as fh:
        for r in csv.DictReader(fh):
            d = datetime.fromisoformat(r["date"]).date()
            if as_of is not None and d > as_of:
                continue
            rows.append((d, float(r["close"])))
    rows.sort(key=lambda x: x[0])
    closes = [c for _d, c in rows]
    return closes[-window:] if window > 0 else closes


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Countercyclical bridge (dry-powder deployment)")
    p.add_argument("--as-of", type=str, default=None, help="YYYY-MM-DD PIT cutoff (default latest)")
    p.add_argument("--bridge-budget", type=float, default=0.15)
    p.add_argument("--value-threshold", type=float, default=0.55)
    p.add_argument("--window", type=int, default=252, help="trailing trading days for drawdown peak")
    p.add_argument("--market-csv", type=Path, default=DEFAULT_MARKET_CSV)
    p.add_argument("--core-fraction", type=float, default=0.35)
    p.add_argument("--hunt-fraction", type=float, default=0.15)
    p.add_argument("--max-name-weight", type=float, default=0.08)
    p.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    p.add_argument("--prices", type=Path, default=DEFAULT_PRICES)
    p.add_argument("--universe-csv", type=Path, default=DEFAULT_UNIVERSE)
    p.add_argument("--sectors-csv", type=Path, default=DEFAULT_SECTORS)
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--book", action="store_true", help="also assemble + print the full fund book")
    args = p.parse_args(argv)

    as_of = datetime.fromisoformat(args.as_of).date() if args.as_of else None
    common = {
        "snapshot": args.snapshot,
        "prices": args.prices,
        "universe_csv": args.universe_csv,
        "sectors_csv": args.sectors_csv,
    }
    try:
        universe, sectors, effective = build_universe(as_of=as_of, **common)
        core = select_core_basket(universe, sectors=sectors, as_of=effective)
        core_weights = {h.symbol: h.weight for h in core.holdings}

        prices = load_market_prices(args.market_csv, as_of, args.window)
        drawdown = market_drawdown(prices)
        gate = default_value_gate(core, threshold=args.value_threshold)
        deployment = compute_deployment(drawdown, gate, budget=args.bridge_budget)
    except (ValueError, FileNotFoundError) as e:
        raise SystemExit(str(e)) from e

    print(format_deployment(deployment))

    if args.book:
        insider_signals, capital_signals, hunt_universe, _sec, _eff = build_hunt_inputs(
            catalog=MarketDataCatalog(args.db), as_of=as_of, **common
        )
        hunt = select_hunt_basket(
            insider_signals, hunt_universe, capital_signals=capital_signals,
            sectors=sectors, as_of=effective,
        )
        hunt_weights = {h.symbol: h.weight for h in hunt.holdings}
        book = assemble_fund_book(
            [
                SleeveTarget("core", args.core_fraction, core_weights),
                SleeveTarget("hunt", args.hunt_fraction, hunt_weights),
                bridge_sleeve_target(deployment, core_weights),
            ],
            max_name_weight=args.max_name_weight,
        )
        print()
        print(format_fund_book(book))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Smoke-check it imports + parses args** (no live data needed)

Run: `.venv/bin/python -c "import scripts.countercyclical_bridge as m; print(m.main.__doc__ is not None or True)"`
Then: `.venv/bin/python scripts/countercyclical_bridge.py --help`
Expected: import succeeds; `--help` prints the argument list. (A full data run needs the local snapshot/DuckDB + a `spy-history.csv`; that is `Not-tested` in the commit, like fund_book's driver.)

- [ ] **Step 3: Lint + typecheck the new files**

Run: `.venv/bin/python -m ruff check engine/countercyclical_bridge.py scripts/countercyclical_bridge.py tests/test_engine/test_countercyclical_bridge.py`
Run: `.venv/bin/python -m mypy engine/countercyclical_bridge.py scripts/countercyclical_bridge.py`
Expected: clean. Fix any finding before committing.

- [ ] **Step 4: Commit**

```bash
git add scripts/countercyclical_bridge.py
git commit -m "feat(bridge): PIT driver — drawdown + value gate -> deployment + optional full book"
```

---

### Task 7: Docs — COMPOUNDER_OPERATIONS.md bridge section

**Files:**
- Modify: `docs/COMPOUNDER_OPERATIONS.md`

- [ ] **Step 1: Append a "반순환 브릿지" section** after the core-basket section, documenting: honest
framing (no timing alpha), the AND value gate (falling-knife guard), the step ladder
(10/20/30% -> 1/3, 2/3, 3/3 of `bridge_budget=0.15`), that it scales core weights and is composed by
fund_book (8% cap + zero leverage), and the engine/driver/test paths. Mirror the depth/voice of the
existing core-basket section (`docs/COMPOUNDER_OPERATIONS.md:159-219`).

- [ ] **Step 2: Verify the engine suite is still green**

Run: `.venv/bin/python -m pytest tests/test_engine/test_countercyclical_bridge.py tests/test_engine/test_fund_book.py tests/test_engine/test_core_basket.py tests/test_engine/test_hunt_basket.py -q`
Expected: PASS.

- [ ] **Step 3: Commit** (path-limited add — a concurrent worker also edits this doc)

```bash
git add docs/COMPOUNDER_OPERATIONS.md
git commit -m "docs(bridge): document the countercyclical dry-powder bridge"
```

---

## Self-Review

**Spec coverage:** §2 inputs/outputs → Tasks 1–4 (`market_drawdown` T1, `compute_deployment`/`ladder_fraction` T2, `default_value_gate` T3, `bridge_sleeve_target`/`format_deployment` T4, `BridgeDeployment` T1). §3 ladder → T2 (`DEFAULT_LADDER`, boundaries). §4 validation → T2 (budget/ladder) + T3 (None/empty) + T5 (fund_book cap/leverage). §5 tests → T1–T5. §6 driver → T6. §7 deferred → not built (correct). All covered.

**Placeholder scan:** no TBD/TODO; every code step shows complete code. T7 step 1 is prose (a doc-writing task) but specifies exact content + a reference anchor — acceptable for a docs task.

**Type consistency:** `BridgeDeployment` fields (T1) are read consistently in T2/T4; `compute_deployment(drawdown, value_gate_open, *, budget, ladder)` signature identical across T2/T4/T5/T6; `bridge_sleeve_target(deployment, core_weights)` identical T4/T5/T6; `SleeveTarget(name, fraction, weights)` matches `engine/fund_book.py`; `CoreHolding`/`CoreBasket` helper in T3 flagged to verify against `engine/core_basket.py` before use.
