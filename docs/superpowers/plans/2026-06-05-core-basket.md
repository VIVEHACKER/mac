# Core Basket Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a pure `engine/core_basket.py` that turns a fundamentals universe into a 12–15 name value-led, thesis-hold core basket (the fund's durable ~35% anchor), plus a snapshot-pinned driver script.

**Architecture:** A pure selection engine (screen → value-led rank → equal-weight/cap → thesis-hold rebalance) that **reuses** validated primitives from `engine.compounder` (metrics, Z-scoring, sector-invalid map, flags) by import — never modifying them. Selection deliberately **excludes net_margin/roic** (the project's only durable finding: those reverse-predict). A driver wires PIT snapshots/catalog to the pure engine.

**Tech Stack:** Python 3.12, pure stdlib (`statistics`, `dataclasses`), pytest. No new dependencies. Catalog = DuckDB via `data.catalog`. Snapshots via `data.fundamentals_snapshot` / `data.price_snapshot`.

**Worktree:** `/Users/jjuni/재무관리 모델/trader-fund` (branch `feat/fund-engine`). Python: `/Users/jjuni/재무관리 모델/trader/.venv/bin/python` (run from this cwd; `sys.path.insert(0,'.')` resolves local code).

**Spec:** `docs/superpowers/specs/2026-06-05-core-basket-design.md`

---

## File Structure

- **Create** `engine/core_basket.py` — pure: `CoreHolding`, `CoreBasket`, `RebalanceAction`, `select_core_basket`, `rebalance_core_basket`, `format_core_basket`. Imports (read-only) from `engine.compounder`: `compute_metrics`, `_zscores`, `SECTOR_INVALID_METRICS`, `Z_CLIP`, `_flags`; from `engine.significance`: `normal_cdf`.
- **Create** `tests/test_engine/test_core_basket.py` — pytest, synthetic `FundamentalRecord` builders (no fixtures, mirrors `test_compounder.py`).
- **Create** `scripts/core_basket.py` — driver: pinned snapshots + catalog (PIT) → `select_core_basket` → `format_core_basket`.

Convention: **new files only; validated `engine/compounder.py`, `risk/sizing.py`, `_WEIGHTS` untouched.**

---

## Conventions for every task

- Run tests with the venv python from the worktree cwd:
  `/Users/jjuni/재무관리\ 모델/trader/.venv/bin/python -m pytest tests/test_engine/test_core_basket.py -v`
- Do **not** run the full pytest suite directly (project memory: exit 144). Run only the core-basket test file.
- After each task's tests pass: `ruff check engine/core_basket.py tests/test_engine/test_core_basket.py` and `mypy engine/core_basket.py` clean.
- Commit per task with the `<type>: <description>` format.

---

### Task 1: Module skeleton + dataclasses

**Files:**
- Create: `engine/core_basket.py`
- Test: `tests/test_engine/test_core_basket.py`

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

from datetime import date, datetime

from data.models import FundamentalRecord
from engine.core_basket import (
    CoreBasket,
    CoreHolding,
    RebalanceAction,
    format_core_basket,
    rebalance_core_basket,
    select_core_basket,
)


def _recs(symbol, *, rev, ni, fcf, gp, assets, eq, debt, sh, eps):
    """4 annual records 2020-2023; constant per-field except revenue ramp.

    gp = gross_profit per year (list), assets/eq/debt/sh/eps constant scalars.
    Mirrors tests/test_engine/test_compounder.py builder style."""
    out = []
    for i, year in enumerate((2020, 2021, 2022, 2023)):
        out.append(
            FundamentalRecord(
                symbol=symbol,
                market="us",
                period_end=date(year, 12, 31),
                asof_ts=datetime(year + 1, 3, 1),
                revenue=rev[i],
                net_income=ni[i],
                free_cash_flow=fcf[i],
                total_assets=assets,
                total_equity=eq,
                total_debt=debt,
                shares_out=sh,
                eps=eps,
                gross_profit=gp[i],
            )
        )
    return out


def test_dataclasses_constructible():
    h = CoreHolding(
        symbol="AAA",
        weight=0.0769,
        composite=1.2,
        display_score=88.0,
        cheapness_z=1.0,
        gp_z=0.5,
        sector=None,
        flags=(),
        rationale="저평가+고GP",
    )
    b = CoreBasket(
        holdings=(h,),
        as_of=None,
        universe_size=1,
        eligible_count=1,
        target_n=13,
        max_weight=0.08,
        excluded=(),
    )
    a = RebalanceAction(symbol="AAA", action="hold", target_weight=0.0769, reason="여전히 적격")
    assert b.holdings[0].symbol == "AAA"
    assert a.action == "hold"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv python -m pytest tests/test_engine/test_core_basket.py::test_dataclasses_constructible -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'engine.core_basket'`

- [ ] **Step 3: Write minimal implementation**

```python
"""Core basket: the fund's durable long-term anchor (~35% of the 50/50 barbell).

HONEST FRAMING — read before changing anything:
This basket makes NO factor-alpha claim. The project's terminal validation
(docs/COMPOUNDER_VALIDATION.md, engine/compounder.py:22-32) found that in this
mid/small-cap survivor universe over 3-5y horizons, NO single factor (gross /
net-quality / value) robustly predicts forward returns after regime+size+sector
controls. The one robust finding: net-margin / ROIC quality *reverse*-predicts.
So this engine (1) EXCLUDES net_margin and roic from ranking, (2) tilts toward
the directionally-supported-if-modest value (low ps/pb) + Novy-Marx gross
profitability (GP/assets), and (3) holds theses (winners are not trimmed until
the hard cap). Survival = diversification (12-15 names) + 8% hard cap + zero
leverage; diversification substitutes for vol-targeting. This is the boring,
durable anchor — asymmetric upside is the hunt basket's job, the validated
momentum edge is the separate IDEAL line. Do not turn this into an alpha claim.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from data.models import FundamentalRecord
from engine.compounder import (
    SECTOR_INVALID_METRICS,
    Z_CLIP,
    _flags,
    _zscores,
    compute_metrics,
)
from engine.significance import normal_cdf


@dataclass(frozen=True)
class CoreHolding:
    symbol: str
    weight: float
    composite: float
    display_score: float
    cheapness_z: float | None
    gp_z: float | None
    sector: str | None
    flags: tuple[str, ...]
    rationale: str


@dataclass(frozen=True)
class CoreBasket:
    holdings: tuple[CoreHolding, ...]
    as_of: date | None
    universe_size: int
    eligible_count: int
    target_n: int
    max_weight: float
    excluded: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class RebalanceAction:
    symbol: str
    action: str
    target_weight: float
    reason: str
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv python -m pytest tests/test_engine/test_core_basket.py::test_dataclasses_constructible -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add engine/core_basket.py tests/test_engine/test_core_basket.py
git commit -m "feat(core-basket): module skeleton + dataclasses"
```

---

### Task 2: Screen (eligibility filter, sector-aware)

**Files:**
- Modify: `engine/core_basket.py`
- Test: `tests/test_engine/test_core_basket.py`

Screen contract (spec §4.1): a name is eligible iff coverage ≥ 5 non-None metrics AND at least one of `ps`/`pb` present AND not distressed. Distress (non-financials): `fcf_margin` present and < 0, OR `debt_to_equity` present and > 3.0, OR `share_growth` present and > 0.15. Financials skip the fcf/debt filters (still apply coverage + value-anchor + dilution). Returns `(eligible_metrics, excluded)` where `excluded` is `list[tuple[symbol, reason]]`.

- [ ] **Step 1: Write the failing test**

```python
def test_screen_excludes_cash_burner_and_overlevered_and_diluter():
    from engine.core_basket import _screen

    # healthy: profitable, low debt, cheap
    healthy = _recs("OK", rev=[100, 110, 120, 130], ni=[10, 11, 12, 13],
                    fcf=[8, 9, 10, 11], gp=[40, 44, 48, 52], assets=200.0,
                    eq=100.0, debt=20.0, sh=50.0, eps=2.0)
    # cash burner: negative fcf
    burner = _recs("BURN", rev=[100, 110, 120, 130], ni=[1, 1, 1, 1],
                   fcf=[-5, -6, -7, -8], gp=[40, 44, 48, 52], assets=200.0,
                   eq=100.0, debt=20.0, sh=50.0, eps=0.1)
    # over-levered: d/e > 3
    levered = _recs("LEV", rev=[100, 110, 120, 130], ni=[5, 5, 5, 5],
                    fcf=[5, 5, 5, 5], gp=[40, 44, 48, 52], assets=500.0,
                    eq=50.0, debt=400.0, sh=50.0, eps=1.0)
    # serial diluter: share_growth > 0.15/yr (50 -> ~95 over 3y ≈ 24%/yr)
    diluter = _recs("DIL", rev=[100, 110, 120, 130], ni=[5, 5, 5, 5],
                    fcf=[5, 5, 5, 5], gp=[40, 44, 48, 52], assets=200.0,
                    eq=100.0, debt=20.0, sh=50.0, eps=1.0)
    # rebuild diluter with rising share count
    from data.models import FundamentalRecord
    from datetime import date, datetime
    shares = [50.0, 65.0, 80.0, 95.0]
    diluter = [
        FundamentalRecord(symbol="DIL", market="us", period_end=date(y, 12, 31),
                          asof_ts=datetime(y + 1, 3, 1), revenue=r, net_income=5.0,
                          free_cash_flow=5.0, total_assets=200.0, total_equity=100.0,
                          total_debt=20.0, shares_out=s, eps=1.0, gross_profit=g)
        for y, r, s, g in zip((2020, 2021, 2022, 2023), [100, 110, 120, 130], shares,
                              [40, 44, 48, 52])
    ]

    universe = {
        "OK": (healthy, 30.0), "BURN": (burner, 30.0),
        "LEV": (levered, 30.0), "DIL": (diluter, 30.0),
    }
    eligible, excluded = _screen(universe, sectors=None)
    assert "OK" in eligible
    ex = dict(excluded)
    assert "BURN" in ex and "fcf" in ex["BURN"].lower()
    assert "LEV" in ex and "debt" in ex["LEV"].lower()
    assert "DIL" in ex and ("dilut" in ex["DIL"].lower() or "share" in ex["DIL"].lower())


def test_screen_keeps_financial_with_high_debt_via_pb():
    from engine.core_basket import _screen
    bank = _recs("BANK", rev=[100, 110, 120, 130], ni=[10, 11, 12, 13],
                 fcf=[None, None, None, None], gp=[None, None, None, None],
                 assets=1000.0, eq=100.0, debt=500.0, sh=50.0, eps=2.0)
    universe = {"BANK": (bank, 30.0)}
    eligible, excluded = _screen(universe, sectors={"BANK": "financials"})
    assert "BANK" in eligible  # high d/e tolerated for financials, ranked by pb
    assert dict(excluded).get("BANK") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv python -m pytest tests/test_engine/test_core_basket.py -k screen -v`
Expected: FAIL with `ImportError: cannot import name '_screen'`

- [ ] **Step 3: Write minimal implementation** (append to `engine/core_basket.py`)

```python
MIN_PRESENT_METRICS: int = 5
MAX_DEBT_TO_EQUITY: float = 3.0
MAX_SHARE_GROWTH: float = 0.15


def _apply_sector_nulls(
    metrics: dict[str, float | None], sector: str | None
) -> dict[str, float | None]:
    """Null sector-invalid metrics (e.g. FCF/GP for financials) before screening/ranking."""
    invalid = SECTOR_INVALID_METRICS.get(sector or "", frozenset())
    if not invalid:
        return metrics
    return {k: (None if k in invalid else v) for k, v in metrics.items()}


def _screen(
    universe: dict[str, tuple[Sequence[FundamentalRecord], float]],
    sectors: dict[str, str] | None,
) -> tuple[dict[str, dict[str, float | None]], list[tuple[str, str]]]:
    """Return (eligible {symbol: sector-nulled metrics}, excluded [(symbol, reason)])."""
    eligible: dict[str, dict[str, float | None]] = {}
    excluded: list[tuple[str, str]] = []
    for symbol, (records, price) in universe.items():
        sector = (sectors or {}).get(symbol)
        raw = compute_metrics(records, price)
        if not raw:
            excluded.append((symbol, "데이터 없음 (no fundamentals)"))
            continue
        metrics = _apply_sector_nulls(raw, sector)
        present = sum(1 for v in metrics.values() if v is not None)
        if present < MIN_PRESENT_METRICS:
            excluded.append((symbol, f"커버리지 부족 ({present}<{MIN_PRESENT_METRICS} metrics)"))
            continue
        if metrics.get("ps") is None and metrics.get("pb") is None:
            excluded.append((symbol, "밸류 앵커 없음 (no ps/pb)"))
            continue
        is_financial = sector == "financials"
        if not is_financial:
            fcf_m = metrics.get("fcf_margin")
            if fcf_m is not None and fcf_m < 0:
                excluded.append((symbol, f"현금소진 (fcf_margin {fcf_m:.2f}<0)"))
                continue
            de = metrics.get("debt_to_equity")
            if de is not None and de > MAX_DEBT_TO_EQUITY:
                excluded.append((symbol, f"과다부채 (debt/equity {de:.1f}>{MAX_DEBT_TO_EQUITY})"))
                continue
        sg = metrics.get("share_growth")
        if sg is not None and sg > MAX_SHARE_GROWTH:
            excluded.append((symbol, f"연쇄 희석 (share_growth {sg:.0%}>{MAX_SHARE_GROWTH:.0%})"))
            continue
        eligible[symbol] = metrics
    return eligible, excluded
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv python -m pytest tests/test_engine/test_core_basket.py -k screen -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add engine/core_basket.py tests/test_engine/test_core_basket.py
git commit -m "feat(core-basket): sector-aware eligibility screen"
```

---

### Task 3: Rank (value-led composite, net_margin/roic excluded)

**Files:**
- Modify: `engine/core_basket.py`
- Test: `tests/test_engine/test_core_basket.py`

Rank contract (spec §4.2): on the eligible set, cross-sectional Z (reuse `_zscores`, winsorize at `Z_CLIP`). `cheapness_z` = mean of available `{−Z(ps), −Z(pb)}`; `gp_z` = `Z(gross_profitability)`. `composite` = coverage-renormalized `(w_value*cheapness_z + w_gp*gp_z) / wsum` over present components. `net_margin`/`roic` are never inputs. Returns a list of `(symbol, composite, cheapness_z, gp_z)` for ranking.

- [ ] **Step 1: Write the failing test** (the key honesty test)

```python
def test_rank_prefers_cheap_high_gp_and_ignores_net_margin_roic():
    from engine.core_basket import _rank_eligible, _screen

    # CHEAP_GP: low ps/pb (cheap), high gross profit/assets -> should rank top
    cheap_gp = _recs("CHEAPGP", rev=[100, 100, 100, 100], ni=[5, 5, 5, 5],
                     fcf=[5, 5, 5, 5], gp=[60, 60, 60, 60], assets=100.0,
                     eq=100.0, debt=10.0, sh=10.0, eps=1.0)  # ps≈ (10*30)/100=3, pb=3, gp/assets=0.6
    # EXP_HQ: expensive but very high net_margin & roic -> must NOT rank top
    #   (proves net_margin/roic excluded). High ni -> high net_margin/roic; high price -> expensive.
    exp_hq = _recs("EXPHQ", rev=[100, 100, 100, 100], ni=[40, 40, 40, 40],
                   fcf=[40, 40, 40, 40], gp=[30, 30, 30, 30], assets=100.0,
                   eq=100.0, debt=10.0, sh=10.0, eps=4.0)
    # price 30 vs 100 -> EXPHQ ps≈10, pb≈10 (expensive), gp/assets=0.3
    universe = {"CHEAPGP": (cheap_gp, 30.0), "EXPHQ": (exp_hq, 100.0)}
    eligible, _ = _screen(universe, sectors=None)
    ranked = _rank_eligible(eligible, w_value=0.6, w_gp=0.4)
    order = [r[0] for r in ranked]
    assert order[0] == "CHEAPGP"   # cheap + high GP wins
    assert order[-1] == "EXPHQ"    # high net_margin/roic did NOT lift it
    # sanity: net_margin & roic are not consulted — EXPHQ has the higher ni but still loses
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv python -m pytest tests/test_engine/test_core_basket.py -k rank -v`
Expected: FAIL with `ImportError: cannot import name '_rank_eligible'`

- [ ] **Step 3: Write minimal implementation** (append)

```python
def _clip(z: float) -> float:
    return max(-Z_CLIP, min(Z_CLIP, z))


def _rank_eligible(
    eligible: dict[str, dict[str, float | None]],
    *,
    w_value: float,
    w_gp: float,
) -> list[tuple[str, float, float | None, float | None]]:
    """Cross-sectional value-led ranking. Returns [(symbol, composite, cheapness_z, gp_z)]
    sorted by (-composite, symbol). net_margin/roic are deliberately not consulted."""
    symbols = list(eligible)
    if not symbols:
        return []
    col = lambda key: [eligible[s].get(key) for s in symbols]  # noqa: E731
    z_ps = dict(zip(symbols, _zscores(col("ps")), strict=True))
    z_pb = dict(zip(symbols, _zscores(col("pb")), strict=True))
    z_gp = dict(zip(symbols, _zscores(col("gross_profitability")), strict=True))

    rows: list[tuple[str, float, float | None, float | None]] = []
    for s in symbols:
        # cheapness = mean of available negated value Zs (lower multiple = cheaper = higher)
        chs = [-z for z in (z_ps[s], z_pb[s]) if z is not None]
        cheapness = _clip(sum(chs) / len(chs)) if chs else None
        gp = z_gp[s]
        gp_clipped = _clip(gp) if gp is not None else None

        contrib, wsum = 0.0, 0.0
        if cheapness is not None:
            contrib += w_value * cheapness
            wsum += abs(w_value)
        if gp_clipped is not None:
            contrib += w_gp * gp_clipped
            wsum += abs(w_gp)
        composite = contrib / wsum if wsum > 0 else 0.0
        rows.append((s, composite, cheapness, gp_clipped))

    rows.sort(key=lambda r: (-r[1], r[0]))
    return rows
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv python -m pytest tests/test_engine/test_core_basket.py -k rank -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add engine/core_basket.py tests/test_engine/test_core_basket.py
git commit -m "feat(core-basket): value-led rank (net_margin/roic excluded)"
```

---

### Task 4: Equal-weight + 8% cap + `select_core_basket`

**Files:**
- Modify: `engine/core_basket.py`
- Test: `tests/test_engine/test_core_basket.py`

Weight contract (spec §4.3): top `target_n` by composite, equal-weight `1/n` then clamp to `max_weight`. For n≥13 cap doesn't bind → weights `1/n` sum 1.0. For n≤12 every name hits cap → each `max_weight`, basket sums `< 1.0` (sleeve cash; no redistribution under equal weight). `select_core_basket` ties screen+rank+weight together and returns `CoreBasket`.

- [ ] **Step 1: Write the failing test**

```python
def _many(n, start_price=30.0):
    """Build n healthy, distinct names with varying cheapness so ranking is deterministic."""
    uni = {}
    for i in range(n):
        sym = f"S{i:02d}"
        # vary revenue/gp slightly so composites differ; all eligible & cheap
        recs = _recs(sym, rev=[100, 100, 100, 100], ni=[5, 5, 5, 5], fcf=[5, 5, 5, 5],
                     gp=[40 + i, 40 + i, 40 + i, 40 + i], assets=100.0, eq=100.0,
                     debt=10.0, sh=10.0, eps=1.0)
        uni[sym] = (recs, start_price + i)  # higher i -> pricier (less cheap) but higher gp
    return uni


def test_select_n13_equal_weight_sums_to_one():
    basket = select_core_basket(_many(20), target_n=13, max_weight=0.08)
    assert len(basket.holdings) == 13
    for h in basket.holdings:
        assert h.weight <= 0.08 + 1e-9
    assert abs(sum(h.weight for h in basket.holdings) - 1.0) < 1e-6
    assert basket.eligible_count == 20
    assert basket.universe_size == 20


def test_select_n12_cap_binds_leaves_sleeve_cash():
    basket = select_core_basket(_many(12), target_n=13, max_weight=0.08)
    assert len(basket.holdings) == 12
    for h in basket.holdings:
        assert abs(h.weight - 0.08) < 1e-9
    total = sum(h.weight for h in basket.holdings)
    assert abs(total - 0.96) < 1e-6  # 12 * 0.08, remainder = sleeve cash


def test_select_attaches_scores_and_rationale():
    basket = select_core_basket(_many(13), target_n=13)
    h = basket.holdings[0]
    assert 0.0 <= h.display_score <= 100.0
    assert h.rationale  # non-empty Korean rationale
    assert isinstance(h.flags, tuple)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv python -m pytest tests/test_engine/test_core_basket.py -k select -v`
Expected: FAIL with `NameError`/`AttributeError` (no `select_core_basket` body / `_weights`)

- [ ] **Step 3: Write minimal implementation** (append)

```python
def _equal_weights_capped(symbols: list[str], max_weight: float) -> dict[str, float]:
    """Equal-weight 1/n clamped to max_weight. Under equal weighting the cap is all-or-none:
    n >= 1/max_weight -> 1/n (sums to 1.0); n < 1/max_weight -> each max_weight (sums < 1.0,
    remainder = sleeve cash, since there is no uncapped name to redistribute to)."""
    n = len(symbols)
    if n == 0:
        return {}
    w = min(1.0 / n, max_weight)
    return {s: w for s in symbols}


def _rationale(cheapness_z: float | None, gp_z: float | None, flags: tuple[str, ...]) -> str:
    bits: list[str] = []
    if cheapness_z is not None:
        bits.append("저평가" if cheapness_z > 0 else "고평가")
    if gp_z is not None:
        bits.append("고GP" if gp_z > 0 else "저GP")
    base = "+".join(bits) if bits else "중립"
    if flags:
        base += f" ⚠{','.join(flags)}"
    return base


def select_core_basket(
    universe: dict[str, tuple[Sequence[FundamentalRecord], float]],
    *,
    sectors: dict[str, str] | None = None,
    target_n: int = 13,
    max_weight: float = 0.08,
    w_value: float = 0.6,
    w_gp: float = 0.4,
    as_of: date | None = None,
) -> CoreBasket:
    if target_n < 1:
        raise ValueError("target_n must be >= 1")
    if not 0.0 < max_weight <= 1.0:
        raise ValueError("max_weight must be in (0, 1]")
    eligible, excluded = _screen(universe, sectors)
    ranked = _rank_eligible(eligible, w_value=w_value, w_gp=w_gp)
    chosen = ranked[:target_n]
    weights = _equal_weights_capped([r[0] for r in chosen], max_weight)
    holdings = []
    for symbol, composite, cheapness_z, gp_z in chosen:
        flags = _flags(eligible[symbol])
        holdings.append(
            CoreHolding(
                symbol=symbol,
                weight=weights[symbol],
                composite=composite,
                display_score=normal_cdf(composite) * 100.0,
                cheapness_z=cheapness_z,
                gp_z=gp_z,
                sector=(sectors or {}).get(symbol),
                flags=flags,
                rationale=_rationale(cheapness_z, gp_z, flags),
            )
        )
    return CoreBasket(
        holdings=tuple(holdings),
        as_of=as_of,
        universe_size=len(universe),
        eligible_count=len(eligible),
        target_n=target_n,
        max_weight=max_weight,
        excluded=tuple(excluded),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv python -m pytest tests/test_engine/test_core_basket.py -k select -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add engine/core_basket.py tests/test_engine/test_core_basket.py
git commit -m "feat(core-basket): equal-weight + 8% cap + select_core_basket"
```

---

### Task 5: Thesis-hold rebalancer

**Files:**
- Modify: `engine/core_basket.py`
- Test: `tests/test_engine/test_core_basket.py`

Rebalance contract (spec §5): inputs `held` (symbol→current sleeve weight), freshly selected `target: CoreBasket`, `eligible: set[str]` (names still passing the screen). Logic: held & still eligible → `hold` (kept even if it slipped out of fresh top-N); held & now ineligible → `drop` (thesis break, with reason); fresh top names not held fill to `target_n` → `add`; a held winner above equal-weight keeps grown weight capped at `max_weight` (`trim_to_cap` only if it breaches). Renormalize to sum 1.0 (cap-and-redistribute over uncapped names). Returns `(new_basket, actions)`.

- [ ] **Step 1: Write the failing test**

```python
def test_rebalance_holds_slipped_winner_and_drops_thesis_break():
    target = select_core_basket(_many(13), target_n=13)
    target_syms = [h.symbol for h in target.holdings]
    held = {target_syms[0]: 0.20, "GONE": 0.10, target_syms[1]: 0.05}
    eligible = set(target_syms) | {target_syms[0], target_syms[1]}  # GONE not eligible
    new_basket, actions = rebalance_core_basket(
        held, target, eligible, target_n=13, max_weight=0.08
    )
    amap = {a.symbol: a for a in actions}
    assert amap["GONE"].action == "drop"
    assert "GONE" not in {h.symbol for h in new_basket.holdings}
    # winner above cap is trimmed to the hard cap
    assert amap[target_syms[0]].action == "trim_to_cap"
    assert amap[target_syms[0]].target_weight <= 0.08 + 1e-9
    # all-held eligible names survive; weights sum to 1.0
    assert abs(sum(h.weight for h in new_basket.holdings) - 1.0) < 1e-6
    assert all(h.weight <= 0.08 + 1e-9 for h in new_basket.holdings)


def test_rebalance_adds_to_reach_target_n():
    target = select_core_basket(_many(13), target_n=13)
    held = {target.holdings[0].symbol: 0.08}  # only 1 held
    eligible = {h.symbol for h in target.holdings}
    new_basket, actions = rebalance_core_basket(held, target, eligible, target_n=13)
    adds = [a for a in actions if a.action == "add"]
    assert len(new_basket.holdings) == 13
    assert len(adds) == 12
    assert abs(sum(h.weight for h in new_basket.holdings) - 1.0) < 1e-6
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv python -m pytest tests/test_engine/test_core_basket.py -k rebalance -v`
Expected: FAIL with `NameError: name 'rebalance_core_basket'` (only a `pass` stub from Task 1 import — actually ImportError if not defined; define after seeing fail)

- [ ] **Step 3: Write minimal implementation** (append)

```python
def _cap_redistribute(raw: dict[str, float], max_weight: float) -> dict[str, float]:
    """Normalize raw positive weights to sum 1.0 with an iterative hard cap: capped names are
    fixed at max_weight, the remainder is split proportionally among uncapped names until stable."""
    symbols = [s for s, w in raw.items() if w > 0]
    if not symbols:
        return {}
    weights = {s: raw[s] for s in symbols}
    capped: set[str] = set()
    for _ in range(len(symbols) + 1):
        free = [s for s in symbols if s not in capped]
        if not free:
            break
        fixed = sum(max_weight for _ in capped)
        remaining = 1.0 - fixed
        free_total = sum(weights[s] for s in free)
        if free_total <= 0 or remaining <= 0:
            for s in free:
                weights[s] = 0.0
            break
        scaled = {s: weights[s] / free_total * remaining for s in free}
        newly = [s for s in free if scaled[s] > max_weight]
        if not newly:
            for s in free:
                weights[s] = scaled[s]
            break
        for s in newly:
            capped.add(s)
    for s in capped:
        weights[s] = max_weight
    return weights


def rebalance_core_basket(
    held: dict[str, float],
    target: CoreBasket,
    eligible: set[str],
    *,
    target_n: int = 13,
    max_weight: float = 0.08,
) -> tuple[CoreBasket, tuple[RebalanceAction, ...]]:
    target_by_symbol = {h.symbol: h for h in target.holdings}
    actions: list[RebalanceAction] = []

    # 1. Held names: keep if still eligible, drop if thesis broke.
    keep: list[str] = []
    for symbol in held:
        if symbol in eligible:
            keep.append(symbol)
        else:
            actions.append(RebalanceAction(symbol, "drop", 0.0, "스크린 탈락 (thesis break)"))

    # 2. Fill remaining slots from fresh top-ranked names not already kept.
    for h in target.holdings:
        if len(keep) >= target_n:
            break
        if h.symbol not in keep:
            keep.append(h.symbol)
            actions.append(RebalanceAction(h.symbol, "add", 0.0, "신규 편입 (top rank)"))

    # 3. Raw weights: held winners keep grown weight (capped); others equal-weight target.
    eq = 1.0 / max(len(keep), 1)
    raw: dict[str, float] = {}
    for symbol in keep:
        if symbol in held and held[symbol] > eq:
            raw[symbol] = held[symbol]  # let the winner run (pre-cap)
        else:
            raw[symbol] = eq
    final = _cap_redistribute(raw, max_weight)

    # 4. Emit hold / trim_to_cap actions for kept names (adds already recorded with 0 weight).
    add_syms = {a.symbol for a in actions if a.action == "add"}
    holdings: list[CoreHolding] = []
    for symbol in keep:
        w = final.get(symbol, 0.0)
        src = target_by_symbol.get(symbol)
        if symbol not in add_syms:
            if symbol in held and held[symbol] > max_weight:
                actions.append(RebalanceAction(symbol, "trim_to_cap", w, "캡 초과 → 8% 축소"))
            else:
                actions.append(RebalanceAction(symbol, "hold", w, "여전히 적격"))
        else:
            # update the recorded add action's target weight
            for i, a in enumerate(actions):
                if a.symbol == symbol and a.action == "add":
                    actions[i] = RebalanceAction(symbol, "add", w, a.reason)
                    break
        holdings.append(
            CoreHolding(
                symbol=symbol,
                weight=w,
                composite=src.composite if src else 0.0,
                display_score=src.display_score if src else 0.0,
                cheapness_z=src.cheapness_z if src else None,
                gp_z=src.gp_z if src else None,
                sector=src.sector if src else None,
                flags=src.flags if src else (),
                rationale=src.rationale if src else "보유 유지",
            )
        )
    new_basket = CoreBasket(
        holdings=tuple(holdings),
        as_of=target.as_of,
        universe_size=target.universe_size,
        eligible_count=target.eligible_count,
        target_n=target_n,
        max_weight=max_weight,
        excluded=target.excluded,
    )
    return new_basket, tuple(actions)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv python -m pytest tests/test_engine/test_core_basket.py -k rebalance -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add engine/core_basket.py tests/test_engine/test_core_basket.py
git commit -m "feat(core-basket): thesis-hold rebalancer (drop/add/hold/trim_to_cap)"
```

---

### Task 6: `format_core_basket` report (honest framing header)

**Files:**
- Modify: `engine/core_basket.py`
- Test: `tests/test_engine/test_core_basket.py`

Report contract (spec §4.4, §1): a Korean text report whose header states the honest framing (no alpha claim / durable anchor / excludes net-margin·ROIC), then a holdings table (symbol, weight%, display_score, cheapness_z, gp_z, sector, flags, rationale), then a summary line (universe/eligible/n) and the excluded count.

- [ ] **Step 1: Write the failing test**

```python
def test_format_contains_honest_header_and_holdings():
    basket = select_core_basket(_many(13), target_n=13)
    txt = format_core_basket(basket)
    assert "알파" in txt  # honest framing mentions "no alpha claim"
    assert "net-margin" in txt.lower() or "net_margin" in txt.lower()
    assert "ROIC" in txt or "roic" in txt
    assert basket.holdings[0].symbol in txt
    assert "%" in txt  # weight column rendered as percent
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv python -m pytest tests/test_engine/test_core_basket.py -k format -v`
Expected: FAIL with `NameError: name 'format_core_basket'` body missing

- [ ] **Step 3: Write minimal implementation** (append)

```python
def format_core_basket(basket: CoreBasket) -> str:
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("코어 바스켓 (장기 슬리브 ~35% durable anchor)")
    lines.append(
        "정직한 프레이밍: 팩터 알파 주장 없음. 검증 결론상 어떤 단일 팩터도 "
        "regime+size+sector 통제를 견디며 예측 못 함."
    )
    lines.append(
        "유일 견고 발견 = net-margin/ROIC 역예측(나쁨) → 랭킹에서 제외. "
        "밸류(저 ps/pb)+GP/assets 틸트, 등가중 8%캡, thesis-hold, 레버리지0."
    )
    lines.append("=" * 72)
    asof = basket.as_of.isoformat() if basket.as_of else "latest"
    lines.append(
        f"as_of={asof}  universe={basket.universe_size}  "
        f"eligible={basket.eligible_count}  target_n={basket.target_n}  "
        f"cap={basket.max_weight:.0%}  excluded={len(basket.excluded)}"
    )
    lines.append("-" * 72)
    lines.append(
        f"{'SYM':<8}{'W%':>7}{'SCORE':>7}{'CHEAP_Z':>9}{'GP_Z':>7}  {'SECTOR':<12}RATIONALE"
    )
    for h in basket.holdings:
        cz = f"{h.cheapness_z:+.2f}" if h.cheapness_z is not None else "  n/a"
        gz = f"{h.gp_z:+.2f}" if h.gp_z is not None else "  n/a"
        sec = (h.sector or "-")[:12]
        lines.append(
            f"{h.symbol:<8}{h.weight*100:>6.2f}{h.display_score:>7.1f}"
            f"{cz:>9}{gz:>7}  {sec:<12}{h.rationale}"
        )
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv python -m pytest tests/test_engine/test_core_basket.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: ruff + mypy clean, then commit**

```bash
ruff check engine/core_basket.py tests/test_engine/test_core_basket.py
mypy engine/core_basket.py
git add engine/core_basket.py tests/test_engine/test_core_basket.py
git commit -m "feat(core-basket): format_core_basket report with honest framing"
```

---

### Task 7: Driver script `scripts/core_basket.py`

**Files:**
- Create: `scripts/core_basket.py`

Driver contract (spec §6): load pinned fundamentals + price snapshots + sectors CSV, build the PIT universe at `as_of`, run `select_core_basket`, print `format_core_basket`. No test (I/O script, validated indirectly by smoke run). Pattern: mirror an existing driver. **Read `scripts/evaluate_ticker.py` or `scripts/compounder_forward_validation.py` first** to copy the exact snapshot-load + catalog-read calls (do not invent API — use whatever `load_fundamentals_snapshot` / `load_price_snapshot` / catalog accessor names those scripts use).

- [ ] **Step 1: Inspect the reuse surface (no code yet)**

Run: `.venv python -m pytest -q 2>/dev/null; grep -n "snapshot\|catalog\|as_of\|get_fundamentals" scripts/compounder_forward_validation.py | head -40`
Read the exact loader + catalog calls; note function names and the universe-CSV path used.

- [ ] **Step 2: Write the driver mirroring that pattern**

```python
"""Driver: build the core basket from pinned PIT snapshots and print the report.

Pure engine in engine/core_basket.py; this script carries all I/O (snapshots,
catalog, sectors CSV) and the PIT as_of discipline. Mirrors the compounder driver.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, ".")

from engine.core_basket import format_core_basket, select_core_basket  # noqa: E402


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Core basket selector (PIT, pinned snapshots)")
    p.add_argument("--as-of", type=str, default=None, help="YYYY-MM-DD PIT cutoff")
    p.add_argument("--target-n", type=int, default=13)
    p.add_argument("--max-weight", type=float, default=0.08)
    p.add_argument("--w-value", type=float, default=0.6)
    p.add_argument("--w-gp", type=float, default=0.4)
    p.add_argument("--snapshot", type=str, default="fundamentals-2026-06-01-gp2")
    p.add_argument("--prices", type=str, default="prices-2026-06-01")
    p.add_argument("--universe-csv", type=str, default="data/universes/sp400-600-current.csv")
    p.add_argument("--sectors-csv", type=str, default="data/sectors/sp400-600-current-sectors.csv")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    as_of = datetime.fromisoformat(args.as_of).date() if args.as_of else None
    # Build {symbol: (records, price)} from pinned snapshots at as_of using the SAME
    # loader functions the compounder driver uses (resolved in Step 1).
    universe, sectors = _load_universe(
        snapshot=args.snapshot,
        prices=args.prices,
        universe_csv=Path(args.universe_csv),
        sectors_csv=Path(args.sectors_csv),
        as_of=as_of,
    )
    basket = select_core_basket(
        universe,
        sectors=sectors,
        target_n=args.target_n,
        max_weight=args.max_weight,
        w_value=args.w_value,
        w_gp=args.w_gp,
        as_of=as_of,
    )
    print(format_core_basket(basket))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

`_load_universe` must be filled in Step 1's discovered API. If the compounder driver has a reusable `load_universe`-style helper, import it; otherwise replicate its snapshot-load + per-symbol `(records, price)` assembly inline. **Do not fabricate loader names** — use what exists.

- [ ] **Step 3: Smoke-run the driver**

Run: `.venv python -m scripts.core_basket --target-n 13 --as-of 2026-06-01 | head -30`
Expected: the honest-framing header + a 13-row holdings table (or, if snapshots aren't present in this worktree, a clear FileNotFound naming the missing snapshot — acceptable; the pure engine is the deliverable, the driver is validated when snapshots exist).

- [ ] **Step 4: ruff clean + commit**

```bash
ruff check scripts/core_basket.py
git add scripts/core_basket.py
git commit -m "feat(core-basket): snapshot-pinned driver script"
```

---

### Task 8: Docs update (VALUATION.md / fund architecture)

**Files:**
- Modify: `docs/VALUATION.md` (append a "코어 바스켓" section) — or the fund-architecture doc if one exists; check first.

Per project convention (memory: "문서 동반 갱신 규칙"), a T2+ feature updates docs.

- [ ] **Step 1: Find the right doc**

Run: `ls docs/ | grep -iE "valuation|fund|architecture"`
Append to `docs/VALUATION.md` (or the fund doc) a short section describing the core basket: role (~35% durable anchor), honest framing (no alpha claim, net-margin/ROIC excluded), selection (value-led screen + GP tilt), thesis-hold, 8% cap, the `select_core_basket`/`rebalance_core_basket`/`format_core_basket` API, and the driver command.

- [ ] **Step 2: Commit**

```bash
git add docs/
git commit -m "docs(core-basket): document core basket engine + driver"
```

---

## Self-Review

**Spec coverage:**
- §1 honest framing → Task 1 docstring + Task 6 header (test asserts header content). ✓
- §3 universe/PIT/snapshots → Task 7 driver. ✓
- §4.1 screen → Task 2. §4.2 rank (net_margin/roic excluded) → Task 3 (honesty test). §4.3 weight/cap → Task 4. §4.4 outputs → Tasks 1/4/6. ✓
- §5 thesis-hold rebalancer → Task 5. ✓
- §6 driver → Task 7. §7 tests → woven into each task. §8 boundaries → file structure. ✓
- §9 deferred items → not implemented (correct). ✓

**Placeholder scan:** `_load_universe` in Task 7 is intentionally discovered-in-Step-1, not fabricated — flagged explicitly with instruction to use existing loader names (the one place the exact catalog/snapshot API must be read from source, since it differs across this repo's drivers). All code-bearing steps show full code. No TBD/TODO.

**Type consistency:** `CoreHolding`/`CoreBasket`/`RebalanceAction` fields defined in Task 1 are used identically in Tasks 4/5/6. `_screen` returns `(dict, list)` consumed by `_rank_eligible` (Task 3) and `select_core_basket` (Task 4). `select_core_basket`/`rebalance_core_basket`/`format_core_basket` signatures match the spec §4–§5 verbatim. `_cap_redistribute` used only in Task 5 (equal-weight selector in Task 4 uses the simpler `_equal_weights_capped`, matching the §4.3 vs §5 distinction). ✓

---

## Final verification (after all tasks)

- `gan-harness verify` (L1) PASS — do NOT run full `pytest` directly (project memory: exit 144).
- Adversarial multi-lens review (5–6 lenses) + `codex review --uncommitted "한국어로 답변"` + `codex review --uncommitted "적대적 리뷰: 버그/보안/엣지/로직 오류. 한국어로 답변"` (project signature pattern). Fix findings, re-commit.
- Update memory `project_jaemu_trader.md` with the core-basket increment.
