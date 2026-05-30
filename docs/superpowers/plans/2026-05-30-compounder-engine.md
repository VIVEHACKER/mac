# Multi-Archetype Compounder-Quality Engine (P1) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a market-agnostic engine that scores stocks as long-term compounder candidates under three archetypes (Profitable Compounder / Hypergrowth Disruptor / Value-Turnaround), ranks them cross-sectionally, and emits per-name evidence dossiers — decision-support for concentrated multi-year ("ten-bagger") investing.

**Architecture:** Pure-stdlib metric functions over PIT `FundamentalRecord`s + latest price → per-name metric dict → cross-sectional Z-scored archetype scorers (0–100 via `normal_cdf`) → ranked candidates → dossiers. Reuses `engine.significance.normal_cdf` and the existing catalog / PIT-universe / fundamentals-snapshot loaders. No new dependencies.

**Tech Stack:** Python 3.12, stdlib only (`math`, `statistics`, `datetime`, `dataclasses`), pytest, existing `data.catalog` / `data.fundamentals_snapshot`.

Spec: `docs/superpowers/specs/2026-05-30-compounder-engine-design.md`.

---

## File Structure

- Create `engine/compounder_metrics.py` — pure PIT metric functions (`float | None`, never raise).
- Create `engine/compounder.py` — dataclasses, cross-sectional Z, 3 archetype scorers, `rank_compounders`.
- Create `engine/compounder_dossier.py` — `Dossier`, `build_dossier`, `format_dossier_markdown`.
- Modify `trader/cli.py` — add `compounder-scan` subcommand + `_run_compounder_scan` handler.
- Create `tests/test_engine/test_compounder_metrics.py`
- Create `tests/test_engine/test_compounder.py`
- Create `tests/test_engine/test_compounder_dossier.py`
- Modify `tests/test_trader_cli.py` — add a `compounder-scan` CLI test.

Run tests with `.venv/bin/python -m pytest`. Lint `.venv/bin/ruff check <files>`; types `.venv/bin/mypy <file>`.

---

## Task 1: Metrics — shared helpers + growth metrics

**Files:**
- Create: `engine/compounder_metrics.py`
- Test: `tests/test_engine/test_compounder_metrics.py`

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

from datetime import date, datetime

from data.models import FundamentalRecord
from engine.compounder_metrics import (
    revenue_cagr,
    revenue_growth_acceleration,
    eps_growth,
)


def _rec(year: int, revenue=None, net_income=None, eps=None, **kw) -> FundamentalRecord:
    return FundamentalRecord(
        symbol="T",
        market="us",
        period_end=date(year, 12, 31),
        asof_ts=datetime(year + 1, 3, 1),
        revenue=revenue,
        net_income=net_income,
        eps=eps,
        **kw,
    )


def test_revenue_cagr_three_year():
    recs = [_rec(2020, revenue=100.0), _rec(2021, revenue=130.0),
            _rec(2022, revenue=169.0), _rec(2023, revenue=219.7)]
    # 100 -> 219.7 over 3y ≈ 30%
    assert revenue_cagr(recs, years=3) == __import__("pytest").approx(0.30, abs=1e-3)


def test_revenue_cagr_returns_none_when_history_short():
    recs = [_rec(2022, revenue=100.0), _rec(2023, revenue=130.0)]
    assert revenue_cagr(recs, years=3) is None


def test_revenue_cagr_none_on_nonpositive_start():
    recs = [_rec(2020, revenue=0.0), _rec(2023, revenue=100.0)]
    assert revenue_cagr(recs, years=3) is None


def test_revenue_growth_acceleration_positive():
    # YoY: 2022/2021-1 = 0.30 ; 2023/2022-1 = 0.40 -> accel = +0.10
    recs = [_rec(2021, revenue=100.0), _rec(2022, revenue=130.0), _rec(2023, revenue=182.0)]
    assert revenue_growth_acceleration(recs) == __import__("pytest").approx(0.10, abs=1e-9)


def test_eps_growth_handles_sign():
    recs = [_rec(2020, eps=-1.0), _rec(2023, eps=1.0)]
    # (1 - (-1)) / abs(-1) = 2.0
    assert eps_growth(recs, years=3) == __import__("pytest").approx(2.0, abs=1e-9)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_engine/test_compounder_metrics.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.compounder_metrics'`.

- [ ] **Step 3: Write minimal implementation**

```python
"""Point-in-time compounder metrics. Pure functions over period_end-sorted
FundamentalRecords. Every function returns float | None and never raises on
missing/degenerate inputs (None = insufficient data)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta

from data.models import FundamentalRecord

_TOL_DAYS = 120


def _sorted(records: Sequence[FundamentalRecord]) -> list[FundamentalRecord]:
    return sorted(records, key=lambda r: r.period_end)


def _latest(records: Sequence[FundamentalRecord]) -> FundamentalRecord | None:
    s = _sorted(records)
    return s[-1] if s else None


def _record_years_before(records: Sequence[FundamentalRecord], years: float) -> FundamentalRecord | None:
    s = _sorted(records)
    if len(s) < 2:
        return None
    target = s[-1].period_end - timedelta(days=round(365.25 * years))
    best, best_diff = None, None
    for r in s[:-1]:
        diff = abs((r.period_end - target).days)
        if best_diff is None or diff < best_diff:
            best, best_diff = r, diff
    if best is not None and best_diff is not None and best_diff <= _TOL_DAYS:
        return best
    return None


def revenue_cagr(records: Sequence[FundamentalRecord], years: int = 3) -> float | None:
    latest = _latest(records)
    start = _record_years_before(records, years)
    if latest is None or start is None or latest.revenue is None or start.revenue is None:
        return None
    if start.revenue <= 0 or latest.revenue <= 0:
        return None
    return (latest.revenue / start.revenue) ** (1.0 / years) - 1.0


def revenue_growth_acceleration(records: Sequence[FundamentalRecord]) -> float | None:
    latest = _latest(records)
    one = _record_years_before(records, 1)
    two = _record_years_before(records, 2)
    if None in (latest, one, two):
        return None
    if not all(r.revenue and r.revenue > 0 for r in (latest, one, two)):
        return None
    yoy_recent = latest.revenue / one.revenue - 1.0
    yoy_prior = one.revenue / two.revenue - 1.0
    return yoy_recent - yoy_prior


def eps_growth(records: Sequence[FundamentalRecord], years: int = 3) -> float | None:
    latest = _latest(records)
    start = _record_years_before(records, years)
    if latest is None or start is None or latest.eps is None or start.eps is None:
        return None
    if start.eps == 0:
        return None
    return (latest.eps - start.eps) / abs(start.eps)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_engine/test_compounder_metrics.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add engine/compounder_metrics.py tests/test_engine/test_compounder_metrics.py
git commit -m "feat(compounder): PIT growth metrics (revenue CAGR, acceleration, EPS growth)"
```

---

## Task 2: Metrics — quality (margins, trend, ROIC, FCF)

**Files:**
- Modify: `engine/compounder_metrics.py`
- Test: `tests/test_engine/test_compounder_metrics.py`

- [ ] **Step 1: Write the failing test** (append)

```python
from engine.compounder_metrics import (  # noqa: E402
    operating_margin,
    net_margin,
    margin_trend,
    roic,
    fcf_margin,
    fcf_conversion,
)


def test_margins():
    r = _rec(2023, revenue=200.0, net_income=40.0, operating_income=60.0)
    assert net_margin(r) == 0.20
    assert operating_margin(r) == 0.30
    assert net_margin(_rec(2023, revenue=0.0, net_income=1.0)) is None


def test_margin_trend_slope_positive():
    # net margins 0.10, 0.20, 0.30 over x=0,1,2 -> OLS slope 0.10
    recs = [_rec(2021, revenue=100.0, net_income=10.0),
            _rec(2022, revenue=100.0, net_income=20.0),
            _rec(2023, revenue=100.0, net_income=30.0)]
    assert margin_trend(recs) == __import__("pytest").approx(0.10, abs=1e-9)


def test_roic_and_fcf():
    r = _rec(2023, revenue=200.0, net_income=40.0, free_cash_flow=30.0,
             total_equity=100.0, total_debt=100.0)
    assert roic(r) == __import__("pytest").approx(0.20, abs=1e-9)  # 40/(100+100)
    assert fcf_margin(r) == __import__("pytest").approx(0.15, abs=1e-9)  # 30/200
    assert fcf_conversion(r) == __import__("pytest").approx(0.75, abs=1e-9)  # 30/40
    assert fcf_conversion(_rec(2023, net_income=-5.0, free_cash_flow=10.0)) is None
    assert roic(_rec(2023, net_income=10.0, total_equity=0.0, total_debt=0.0)) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_engine/test_compounder_metrics.py -q`
Expected: FAIL — ImportError for `operating_margin` etc.

- [ ] **Step 3: Write minimal implementation** (append to `engine/compounder_metrics.py`)

```python
def _ratio(num: float | None, den: float | None, *, den_positive: bool = False) -> float | None:
    if num is None or den is None or den == 0:
        return None
    if den_positive and den <= 0:
        return None
    return num / den


def operating_margin(rec: FundamentalRecord) -> float | None:
    return _ratio(rec.operating_income, rec.revenue, den_positive=True)


def net_margin(rec: FundamentalRecord) -> float | None:
    return _ratio(rec.net_income, rec.revenue, den_positive=True)


def _ols_slope(ys: list[float]) -> float:
    n = len(ys)
    xs = list(range(n))
    mx = sum(xs) / n
    my = sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    if denom == 0:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / denom


def margin_trend(records: Sequence[FundamentalRecord]) -> float | None:
    margins = [m for r in _sorted(records) if (m := net_margin(r)) is not None]
    if len(margins) < 2:
        return None
    return _ols_slope(margins)


def roic(rec: FundamentalRecord) -> float | None:
    if rec.net_income is None or rec.total_equity is None or rec.total_debt is None:
        return None
    capital = rec.total_equity + rec.total_debt
    if capital <= 0:
        return None
    return rec.net_income / capital


def fcf_margin(rec: FundamentalRecord) -> float | None:
    return _ratio(rec.free_cash_flow, rec.revenue, den_positive=True)


def fcf_conversion(rec: FundamentalRecord) -> float | None:
    if rec.net_income is None or rec.net_income <= 0 or rec.free_cash_flow is None:
        return None
    return rec.free_cash_flow / rec.net_income
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_engine/test_compounder_metrics.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add engine/compounder_metrics.py tests/test_engine/test_compounder_metrics.py
git commit -m "feat(compounder): quality metrics (margins, margin trend, ROIC, FCF)"
```

---

## Task 3: Metrics — durability, dilution, valuation

**Files:**
- Modify: `engine/compounder_metrics.py`
- Test: `tests/test_engine/test_compounder_metrics.py`

- [ ] **Step 1: Write the failing test** (append)

```python
from engine.compounder_metrics import (  # noqa: E402
    debt_to_equity,
    share_growth,
    market_cap,
    pe,
    pfcf,
    ps,
    pb,
)


def test_durability_and_dilution():
    r = _rec(2023, total_debt=50.0, total_equity=100.0, shares_out=110.0)
    assert debt_to_equity(r) == 0.5
    recs = [_rec(2020, shares_out=100.0), _rec(2023, shares_out=110.0)]
    # (110/100)^(1/3) - 1 ≈ 0.0323 dilution
    assert share_growth(recs, years=3) == __import__("pytest").approx(0.0323, abs=1e-3)


def test_valuation_ratios():
    r = _rec(2023, revenue=200.0, net_income=40.0, free_cash_flow=20.0,
             total_equity=100.0, eps=4.0, shares_out=10.0)
    assert market_cap(r, price=80.0) == 800.0       # 80 * 10
    assert pe(r, price=80.0) == 20.0                 # 80 / 4
    assert pfcf(r, price=80.0) == 40.0               # 800 / 20
    assert ps(r, price=80.0) == 4.0                  # 800 / 200
    assert pb(r, price=80.0) == 8.0                  # 800 / 100
    assert pe(_rec(2023, eps=-1.0), price=80.0) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_engine/test_compounder_metrics.py -q`
Expected: FAIL — ImportError for `debt_to_equity` etc.

- [ ] **Step 3: Write minimal implementation** (append)

```python
def debt_to_equity(rec: FundamentalRecord) -> float | None:
    return _ratio(rec.total_debt, rec.total_equity, den_positive=True)


def share_growth(records: Sequence[FundamentalRecord], years: int = 3) -> float | None:
    latest = _latest(records)
    start = _record_years_before(records, years)
    if latest is None or start is None or latest.shares_out is None or start.shares_out is None:
        return None
    if start.shares_out <= 0 or latest.shares_out <= 0:
        return None
    return (latest.shares_out / start.shares_out) ** (1.0 / years) - 1.0


def market_cap(rec: FundamentalRecord, price: float) -> float | None:
    if rec.shares_out is None or rec.shares_out <= 0:
        return None
    return price * rec.shares_out


def pe(rec: FundamentalRecord, price: float) -> float | None:
    return _ratio(price, rec.eps, den_positive=True)


def pfcf(rec: FundamentalRecord, price: float) -> float | None:
    mc = market_cap(rec, price)
    return _ratio(mc, rec.free_cash_flow, den_positive=True)


def ps(rec: FundamentalRecord, price: float) -> float | None:
    mc = market_cap(rec, price)
    return _ratio(mc, rec.revenue, den_positive=True)


def pb(rec: FundamentalRecord, price: float) -> float | None:
    mc = market_cap(rec, price)
    return _ratio(mc, rec.total_equity, den_positive=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_engine/test_compounder_metrics.py -q`
Expected: PASS. Then `.venv/bin/ruff check engine/compounder_metrics.py` → clean.

- [ ] **Step 5: Commit**

```bash
git add engine/compounder_metrics.py tests/test_engine/test_compounder_metrics.py
git commit -m "feat(compounder): durability, dilution, valuation metrics"
```

---

## Task 4: Archetype scorers + cross-sectional Z

**Files:**
- Create: `engine/compounder.py`
- Test: `tests/test_engine/test_compounder.py`

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

from datetime import date, datetime

from data.models import FundamentalRecord
from engine.compounder import (
    ArchetypeScore,
    compute_metrics,
    score_archetypes,
)


def _series(symbol, rev, ni, fcf, eq, debt, sh, eps):
    """4 annual records 2020-2023 with constant per-field values except revenue ramp."""
    out = []
    for i, year in enumerate((2020, 2021, 2022, 2023)):
        out.append(FundamentalRecord(
            symbol=symbol, market="us", period_end=date(year, 12, 31),
            asof_ts=datetime(year + 1, 3, 1),
            revenue=rev[i], net_income=ni[i], free_cash_flow=fcf[i],
            total_equity=eq, total_debt=debt, shares_out=sh, eps=eps,
        ))
    return out


def test_compute_metrics_returns_expected_keys():
    recs = _series("AAA", [100, 120, 150, 190], [10, 14, 20, 30], [8, 12, 18, 28], 100.0, 20.0, 50.0, 3.0)
    m = compute_metrics(recs, price=60.0)
    for key in ("revenue_cagr", "margin_trend", "roic", "fcf_margin", "pfcf", "share_growth"):
        assert key in m


def test_profitable_compounder_scores_highest_for_quality_name():
    # quality: high roic/fcf/rising margin ; junk: low everything
    quality = _series("QLT", [100, 110, 121, 133], [20, 24, 30, 40], [18, 22, 28, 38], 100.0, 10.0, 50.0, 5.0)
    junk = _series("JNK", [100, 101, 102, 103], [1, 1, 1, 1], [0, 0, 0, 0], 100.0, 200.0, 60.0, 0.1)
    universe = {"QLT": (quality, 60.0), "JNK": (junk, 5.0)}
    scores = score_archetypes(universe)
    assert scores["QLT"]["profitable_compounder"].score > scores["JNK"]["profitable_compounder"].score
    assert isinstance(scores["QLT"]["profitable_compounder"], ArchetypeScore)


def test_hypergrowth_scores_highest_for_fast_grower_even_if_unprofitable():
    grower = _series("GRW", [100, 160, 256, 410], [-5, -3, 0, 5], [-4, -2, 1, 6], 50.0, 0.0, 40.0, 0.5)
    slow = _series("SLO", [100, 103, 106, 109], [20, 20, 20, 20], [18, 18, 18, 18], 100.0, 0.0, 40.0, 4.0)
    universe = {"GRW": (grower, 30.0), "SLO": (slow, 50.0)}
    scores = score_archetypes(universe)
    assert scores["GRW"]["hypergrowth_disruptor"].score > scores["SLO"]["hypergrowth_disruptor"].score


def test_value_scores_highest_for_cheap_recovering_name():
    cheap = _series("CHP", [100, 100, 105, 115], [2, 4, 8, 14], [3, 6, 10, 16], 200.0, 20.0, 100.0, 1.4)
    pricey = _series("PRC", [100, 110, 121, 133], [30, 33, 36, 40], [28, 31, 34, 38], 50.0, 0.0, 50.0, 8.0)
    universe = {"CHP": (cheap, 8.0), "PRC": (pricey, 300.0)}
    scores = score_archetypes(universe)
    assert scores["CHP"]["value_turnaround"].score > scores["PRC"]["value_turnaround"].score
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_engine/test_compounder.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.compounder'`.

- [ ] **Step 3: Write minimal implementation**

```python
"""Multi-archetype compounder scoring. Cross-sectional Z-scores within the
supplied universe are mapped to 0-100 archetype scores via the normal CDF."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from statistics import mean, pstdev

from data.models import FundamentalRecord
from engine import compounder_metrics as cm
from engine.significance import normal_cdf

ARCHETYPES = ("profitable_compounder", "hypergrowth_disruptor", "value_turnaround")

# (metric_key, weight). Negative weight = lower-is-better. Weights per archetype
# sum to 1.0 over present metrics (renormalized when some are missing).
_WEIGHTS: dict[str, list[tuple[str, float]]] = {
    "profitable_compounder": [
        ("roic", 0.30), ("fcf_margin", 0.25), ("margin_trend", 0.20),
        ("revenue_cagr", 0.15), ("share_growth", -0.10),
    ],
    "hypergrowth_disruptor": [
        ("revenue_cagr", 0.40), ("revenue_growth_acceleration", 0.35), ("margin_trend", 0.25),
    ],
    "value_turnaround": [
        ("pfcf", -0.30), ("pb", -0.20), ("margin_trend", 0.30), ("fcf_margin", 0.20),
    ],
}


@dataclass(frozen=True)
class ArchetypeScore:
    archetype: str
    score: float
    components: dict[str, float]
    flags: tuple[str, ...]


def compute_metrics(records: Sequence[FundamentalRecord], price: float) -> dict[str, float | None]:
    latest = cm._latest(records)
    if latest is None:
        return {}
    return {
        "revenue_cagr": cm.revenue_cagr(records, 3),
        "revenue_growth_acceleration": cm.revenue_growth_acceleration(records),
        "eps_growth": cm.eps_growth(records, 3),
        "operating_margin": cm.operating_margin(latest),
        "net_margin": cm.net_margin(latest),
        "margin_trend": cm.margin_trend(records),
        "roic": cm.roic(latest),
        "fcf_margin": cm.fcf_margin(latest),
        "fcf_conversion": cm.fcf_conversion(latest),
        "debt_to_equity": cm.debt_to_equity(latest),
        "share_growth": cm.share_growth(records, 3),
        "pe": cm.pe(latest, price),
        "pfcf": cm.pfcf(latest, price),
        "ps": cm.ps(latest, price),
        "pb": cm.pb(latest, price),
    }


def _zscores(values: list[float | None]) -> list[float | None]:
    present = [v for v in values if v is not None]
    if len(present) < 2:
        return [0.0 if v is not None else None for v in values]
    mu = mean(present)
    sigma = pstdev(present)
    if sigma == 0:
        return [0.0 if v is not None else None for v in values]
    return [None if v is None else (v - mu) / sigma for v in values]


def _flags(metrics: dict[str, float | None]) -> tuple[str, ...]:
    flags = []
    sg = metrics.get("share_growth")
    if sg is not None and sg > 0.05:
        flags.append("high-dilution")
    de = metrics.get("debt_to_equity")
    if de is not None and de > 2.0:
        flags.append("high-debt")
    mt = metrics.get("margin_trend")
    if mt is not None and mt < 0:
        flags.append("margin-declining")
    fm = metrics.get("fcf_margin")
    if fm is not None and fm < 0:
        flags.append("negative-fcf")
    return tuple(flags)


def score_archetypes(
    universe: dict[str, tuple[Sequence[FundamentalRecord], float]],
) -> dict[str, dict[str, ArchetypeScore]]:
    symbols = list(universe)
    metrics = {s: compute_metrics(universe[s][0], universe[s][1]) for s in symbols}

    # Cross-sectional Z per metric key.
    keys = {k for m in metrics.values() for k in m}
    zmaps: dict[str, dict[str, float | None]] = {}
    for key in keys:
        col = [metrics[s].get(key) for s in symbols]
        zcol = _zscores(col)
        zmaps[key] = dict(zip(symbols, zcol, strict=True))

    out: dict[str, dict[str, ArchetypeScore]] = {}
    for s in symbols:
        out[s] = {}
        for arch, weights in _WEIGHTS.items():
            components: dict[str, float] = {}
            wsum, contrib = 0.0, 0.0
            for key, w in weights:
                z = zmaps[key].get(s)
                if z is None:
                    continue
                signed = z if w >= 0 else -z
                components[key] = signed
                contrib += abs(w) * signed
                wsum += abs(w)
            blended = contrib / wsum if wsum > 0 else 0.0
            score = normal_cdf(blended) * 100.0
            out[s][arch] = ArchetypeScore(arch, score, components, _flags(metrics[s]))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_engine/test_compounder.py -q`
Expected: PASS (4 passed). Note: `cm._latest` is an internal helper reused here intentionally; it is part of the metrics module's stable surface for this engine.

- [ ] **Step 5: Commit**

```bash
git add engine/compounder.py tests/test_engine/test_compounder.py
git commit -m "feat(compounder): cross-sectional Z + 3 archetype scorers"
```

---

## Task 5: rank_compounders

**Files:**
- Modify: `engine/compounder.py`
- Test: `tests/test_engine/test_compounder.py`

- [ ] **Step 1: Write the failing test** (append)

```python
from engine.compounder import CandidateScore, rank_compounders  # noqa: E402


def test_rank_assigns_best_archetype_and_orders_by_score():
    quality = _series("QLT", [100, 110, 121, 133], [20, 24, 30, 40], [18, 22, 28, 38], 100.0, 10.0, 50.0, 5.0)
    grower = _series("GRW", [100, 160, 256, 410], [-5, -3, 0, 5], [-4, -2, 1, 6], 50.0, 0.0, 40.0, 0.5)
    junk = _series("JNK", [100, 101, 102, 103], [1, 1, 1, 1], [0, 0, 0, 0], 100.0, 250.0, 70.0, 0.1)
    universe = {"QLT": (quality, 60.0), "GRW": (grower, 30.0), "JNK": (junk, 5.0)}

    ranked = rank_compounders(universe, top_n=2)
    assert all(isinstance(c, CandidateScore) for c in ranked)
    assert len(ranked) == 2
    # descending by best_score
    assert ranked[0].best_score >= ranked[1].best_score
    # junk should not be in the top 2
    assert "JNK" not in [c.symbol for c in ranked]
    # best_archetype is the max-scoring archetype for that name
    top = ranked[0]
    assert top.best_archetype == max(top.scores, key=lambda a: top.scores[a].score)


def test_rank_excludes_names_without_metrics():
    empty = []  # no records -> compute_metrics returns {}
    good = _series("OK", [100, 110, 121, 133], [20, 24, 30, 40], [18, 22, 28, 38], 100.0, 10.0, 50.0, 5.0)
    universe = {"OK": (good, 60.0), "BAD": (empty, 10.0)}
    ranked = rank_compounders(universe, top_n=5)
    assert [c.symbol for c in ranked] == ["OK"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_engine/test_compounder.py -q`
Expected: FAIL — ImportError for `CandidateScore` / `rank_compounders`.

- [ ] **Step 3: Write minimal implementation** (append to `engine/compounder.py`)

```python
@dataclass(frozen=True)
class CandidateScore:
    symbol: str
    best_archetype: str
    best_score: float
    scores: dict[str, ArchetypeScore]
    metrics: dict[str, float | None]


def rank_compounders(
    universe: dict[str, tuple[Sequence[FundamentalRecord], float]],
    top_n: int = 20,
) -> list[CandidateScore]:
    all_scores = score_archetypes(universe)
    candidates: list[CandidateScore] = []
    for symbol, arch_scores in all_scores.items():
        metrics = compute_metrics(universe[symbol][0], universe[symbol][1])
        if not metrics:  # insufficient data -> excluded
            continue
        best_arch = max(arch_scores, key=lambda a: arch_scores[a].score)
        candidates.append(
            CandidateScore(
                symbol=symbol,
                best_archetype=best_arch,
                best_score=arch_scores[best_arch].score,
                scores=arch_scores,
                metrics=metrics,
            )
        )
    candidates.sort(key=lambda c: c.best_score, reverse=True)
    return candidates[:top_n]
```

Note: `score_archetypes` already skips empty-metric names implicitly (their Z are None → score 50 baseline); `rank_compounders` excludes them explicitly via the `if not metrics` guard so they never appear.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_engine/test_compounder.py -q`
Expected: PASS. Then `.venv/bin/mypy engine/compounder.py engine/compounder_metrics.py` → clean.

- [ ] **Step 5: Commit**

```bash
git add engine/compounder.py tests/test_engine/test_compounder.py
git commit -m "feat(compounder): rank_compounders with best-archetype assignment"
```

---

## Task 6: Dossier

**Files:**
- Create: `engine/compounder_dossier.py`
- Test: `tests/test_engine/test_compounder_dossier.py`

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

from datetime import date, datetime

from data.models import FundamentalRecord
from engine.compounder import rank_compounders
from engine.compounder_dossier import Dossier, build_dossier, format_dossier_markdown


def _series(symbol, rev, ni, fcf, eq, debt, sh, eps):
    out = []
    for i, year in enumerate((2020, 2021, 2022, 2023)):
        out.append(FundamentalRecord(
            symbol=symbol, market="us", period_end=date(year, 12, 31),
            asof_ts=datetime(year + 1, 3, 1),
            revenue=rev[i], net_income=ni[i], free_cash_flow=fcf[i],
            total_equity=eq, total_debt=debt, shares_out=sh, eps=eps,
        ))
    return out


def test_build_dossier_carries_archetype_and_alt_signals_hook():
    q = _series("QLT", [100, 110, 121, 133], [20, 24, 30, 40], [18, 22, 28, 38], 100.0, 10.0, 50.0, 5.0)
    ranked = rank_compounders({"QLT": (q, 60.0)}, top_n=1)
    d = build_dossier(ranked[0])
    assert isinstance(d, Dossier)
    assert d.symbol == "QLT"
    assert d.archetype in ("profitable_compounder", "hypergrowth_disruptor", "value_turnaround")
    assert d.alt_signals == {}  # P1 leaves empty; P3 fills
    assert "roic" in d.metrics


def test_format_dossier_markdown_contains_key_fields():
    q = _series("QLT", [100, 110, 121, 133], [20, 24, 30, 40], [18, 22, 28, 38], 100.0, 10.0, 50.0, 5.0)
    d = build_dossier(rank_compounders({"QLT": (q, 60.0)}, top_n=1)[0])
    md = format_dossier_markdown(d)
    assert "QLT" in md
    assert "ROIC" in md or "roic" in md
    assert d.rationale in md
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_engine/test_compounder_dossier.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.compounder_dossier'`.

- [ ] **Step 3: Write minimal implementation**

```python
"""Per-name evidence dossier for a compounder candidate."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from engine.compounder import CandidateScore

_PCT = {"revenue_cagr", "revenue_growth_acceleration", "eps_growth", "operating_margin",
        "net_margin", "margin_trend", "roic", "fcf_margin", "share_growth"}
_LABELS = {
    "revenue_cagr": "Revenue CAGR (3y)", "revenue_growth_acceleration": "Rev growth accel",
    "eps_growth": "EPS growth (3y)", "operating_margin": "Operating margin",
    "net_margin": "Net margin", "margin_trend": "Margin trend (slope)", "roic": "ROIC",
    "fcf_margin": "FCF margin", "fcf_conversion": "FCF conversion", "debt_to_equity": "Debt/Equity",
    "share_growth": "Share growth (dilution)", "pe": "P/E", "pfcf": "P/FCF", "ps": "P/S", "pb": "P/B",
}


@dataclass(frozen=True)
class Dossier:
    symbol: str
    archetype: str
    score: float
    metrics: dict[str, float | None]
    flags: tuple[str, ...]
    rationale: str
    alt_signals: dict[str, Any] = field(default_factory=dict)


def _rationale(candidate: CandidateScore) -> str:
    arch = candidate.best_archetype.replace("_", " ")
    comps = candidate.scores[candidate.best_archetype].components
    top = sorted(comps.items(), key=lambda kv: kv[1], reverse=True)[:3]
    drivers = ", ".join(f"{_LABELS.get(k, k)} (z={v:+.2f})" for k, v in top)
    flag_txt = f" Flags: {', '.join(candidate.scores[candidate.best_archetype].flags)}." \
        if candidate.scores[candidate.best_archetype].flags else ""
    return (f"{candidate.symbol} fits the '{arch}' archetype (score {candidate.best_score:.0f}/100), "
            f"driven by {drivers}.{flag_txt}")


def build_dossier(candidate: CandidateScore) -> Dossier:
    best = candidate.scores[candidate.best_archetype]
    return Dossier(
        symbol=candidate.symbol,
        archetype=candidate.best_archetype,
        score=candidate.best_score,
        metrics=candidate.metrics,
        flags=best.flags,
        rationale=_rationale(candidate),
    )


def _fmt(key: str, value: float | None) -> str:
    if value is None:
        return "n/a"
    if key in _PCT:
        return f"{value * 100:+.1f}%"
    return f"{value:.2f}"


def format_dossier_markdown(d: Dossier) -> str:
    lines = [
        f"### {d.symbol} — {d.archetype.replace('_', ' ')} ({d.score:.0f}/100)",
        "",
        d.rationale,
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]
    for key in _LABELS:
        if key in d.metrics:
            lines.append(f"| {_LABELS[key]} | {_fmt(key, d.metrics[key])} |")
    if d.flags:
        lines += ["", f"**Flags:** {', '.join(d.flags)}"]
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_engine/test_compounder_dossier.py -q`
Expected: PASS. Then `.venv/bin/ruff check engine/compounder_dossier.py` → clean.

- [ ] **Step 5: Commit**

```bash
git add engine/compounder_dossier.py tests/test_engine/test_compounder_dossier.py
git commit -m "feat(compounder): per-name evidence dossier + markdown"
```

---

## Task 7: CLI `compounder-scan`

**Files:**
- Modify: `trader/cli.py` (add subparser near the `factor-portfolio` parser ~line 325, and a handler; register in the command dispatch table where other handlers are registered)
- Test: `tests/test_trader_cli.py`

- [ ] **Step 1: Write the failing test** (append to `tests/test_trader_cli.py`, reuse existing `_long_bars` / `MarketDataCatalog`)

```python
def test_compounder_scan_runs_and_reports(tmp_path, capsys) -> None:
    catalog_db = tmp_path / "catalog.duckdb"
    catalog = MarketDataCatalog(catalog_db)
    catalog.put_bars(_long_bars("AAA", 10.0, 0.0010))
    catalog.put_bars(_long_bars("BBB", 10.0, 0.0002))
    catalog.put_fundamentals([
        FundamentalRecord("AAA", "us", date(2023, 12, 31), datetime(2024, 3, 1),
                           revenue=200.0, net_income=40.0, free_cash_flow=30.0,
                           total_equity=100.0, total_debt=10.0, shares_out=50.0, eps=5.0),
        FundamentalRecord("AAA", "us", date(2020, 12, 31), datetime(2021, 3, 1),
                           revenue=100.0, net_income=10.0, free_cash_flow=8.0,
                           total_equity=100.0, total_debt=10.0, shares_out=50.0, eps=2.0),
        FundamentalRecord("BBB", "us", date(2023, 12, 31), datetime(2024, 3, 1),
                           revenue=110.0, net_income=2.0, free_cash_flow=1.0,
                           total_equity=100.0, total_debt=200.0, shares_out=60.0, eps=0.2),
        FundamentalRecord("BBB", "us", date(2020, 12, 31), datetime(2021, 3, 1),
                           revenue=100.0, net_income=2.0, free_cash_flow=1.0,
                           total_equity=100.0, total_debt=200.0, shares_out=55.0, eps=0.2),
    ])

    result = cli.main([
        "compounder-scan", "AAA,BBB",
        "--as-of", "2024-06-30",
        "--top-n", "2",
        "--no-fetch",
        "--catalog-db", str(catalog_db),
    ])
    captured = capsys.readouterr()
    assert result == 0
    assert "AAA" in captured.out
    assert "/100" in captured.out  # archetype score rendered
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_trader_cli.py::test_compounder_scan_runs_and_reports -q`
Expected: FAIL — argparse `invalid choice: 'compounder-scan'`.

- [ ] **Step 3: Write minimal implementation**

In `trader/cli.py`, add imports near the other engine imports:

```python
from engine.compounder import rank_compounders
from engine.compounder_dossier import build_dossier, format_dossier_markdown
```

Add the subparser (after the `factor-portfolio` block, before `walk-forward`):

```python
    compounder = sub.add_parser(
        "compounder-scan",
        help="Score a universe as long-term compounder candidates (3 archetypes) + dossiers.",
    )
    _add_market_symbols_args(compounder)
    compounder.add_argument("--as-of", default=date.today().isoformat())
    compounder.add_argument("--top-n", type=int, default=20)
    compounder.add_argument("--archetype", default=None,
                            choices=["profitable_compounder", "hypergrowth_disruptor", "value_turnaround"])
    compounder.add_argument("--snapshot", type=Path, default=None,
                            help="Pin fundamentals to a content-verified snapshot CSV.")
    compounder.add_argument("--no-fetch", action="store_true", help="Use stored bars only.")
    _add_pit_universe_args(compounder)
    compounder.add_argument("--output", type=Path)
    compounder.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)
```

Register the handler in the dispatch table (find the dict/if-chain mapping command name → handler, e.g. near where `"factor-portfolio": _run_factor_portfolio` appears) and add `"compounder-scan": _run_compounder_scan`.

Add the handler:

```python
def _run_compounder_scan(args: argparse.Namespace) -> int:
    as_of = _parse_date(args.as_of)
    catalog = MarketDataCatalog(args.catalog_db)
    pit_members = _load_pit_universe(catalog, args, market=args.market)
    symbols = _symbols_for_request(args.symbols, pit_members)

    # Fundamentals: snapshot (reproducible) or live catalog.
    if args.snapshot is not None:
        from data.fundamentals_snapshot import read_fundamentals_snapshot
        from collections import defaultdict
        idx: dict[str, list] = defaultdict(list)
        for rec in read_fundamentals_snapshot(args.snapshot, verify=True):
            idx[rec.symbol.upper()].append(rec)
        funds_by_symbol = {s: sorted(idx.get(s.upper(), []), key=lambda r: r.asof_ts) for s in symbols}
    else:
        funds_by_symbol = {
            s: sorted(catalog.get_fundamentals(symbol=s, market=args.market, as_of=None, limit=500),
                      key=lambda r: r.asof_ts)
            for s in symbols
        }

    universe: dict[str, tuple[list, float]] = {}
    for s in symbols:
        recs = [r for r in funds_by_symbol.get(s, []) if r.asof_ts.date() <= as_of]
        if not recs:
            continue
        bars = catalog.get_bars(_catalog_symbol(s, args.market), market=args.market)
        if not bars:
            continue
        universe[s] = (recs, float(bars[-1].close))

    ranked = rank_compounders(universe, top_n=args.top_n)
    if args.archetype:
        ranked = [c for c in ranked if c.best_archetype == args.archetype]

    lines = [f"# Compounder Scan — as-of {as_of} — {len(universe)} names scored", ""]
    for c in ranked:
        lines.append(format_dossier_markdown(build_dossier(c)))
        lines.append("")
    return _emit("\n".join(lines), args.output)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_trader_cli.py::test_compounder_scan_runs_and_reports -q`
Expected: PASS. If `_load_pit_universe` requires a PIT universe arg, the positional `symbols` path returns `pit_members=[]` and `_symbols_for_request("AAA,BBB", [])` yields `["AAA","BBB"]` — confirm by reading `_symbols_for_request` (tests/test_trader_cli.py already exercises it).

- [ ] **Step 5: Commit**

```bash
git add trader/cli.py tests/test_trader_cli.py
git commit -m "feat(compounder): trader compounder-scan CLI + dossier report"
```

---

## Task 8: End-to-end smoke + final gate

**Files:** none (verification only)

- [ ] **Step 1: Run the engine on the real pinned snapshot (large-cap universe)**

Run:
```bash
.venv/bin/trader compounder-scan ALL --pit-universe SP100_PIT_2008 \
  --snapshot data/snapshots/fundamentals-2026-05-29.csv \
  --as-of 2026-05-28 --top-n 15 --no-fetch \
  --output out/compounder-scan-sp100.md
```
Expected: exit 0; `out/compounder-scan-sp100.md` lists 15 archetype-tagged dossiers. (These are "highest-quality large compounders", NOT 10x candidates — real ten-bagger candidates require the P2 small/mid universe. This smoke only proves the engine runs end-to-end on real data.)

- [ ] **Step 2: Full test + lint + types**

Run:
```bash
.venv/bin/python -m pytest tests/test_engine/test_compounder_metrics.py tests/test_engine/test_compounder.py tests/test_engine/test_compounder_dossier.py tests/test_trader_cli.py -q
.venv/bin/ruff check engine/compounder_metrics.py engine/compounder.py engine/compounder_dossier.py trader/cli.py
.venv/bin/mypy engine/compounder_metrics.py engine/compounder.py engine/compounder_dossier.py
```
Expected: all pass, ruff clean, mypy clean.

- [ ] **Step 3: Codex review (if usage available; resets 2026-05-31)**

Run: `codex review "engine/compounder*.py 적대적 리뷰: 통계/로직 오류, 룩어헤드, 엣지케이스. 한국어로 답변."`
If usage-limited, note it and rely on the TDD suite + a code-reviewer subagent.

- [ ] **Step 4: Commit any review fixes**

```bash
git add -A && git commit -m "fix(compounder): address review findings"
```

---

## Notes for the implementer

- **PIT discipline is non-negotiable**: the CLI filters `r.asof_ts.date() <= as_of`. Never use a record whose `asof_ts` is after the scan date — P5 (historical validation) depends on this being correct.
- **Cross-sectional scoring** means a single-name universe scores everything at z=0 → 50/100. That is expected; the engine is comparative. Tests use ≥2 names for meaningful ranking.
- **YAGNI**: do not add universe ingest, alt-data, Korea, validation, or monitoring here — those are P2–P6.
- Match existing `cli.py` patterns for arg helpers (`_add_market_symbols_args`, `_add_pit_universe_args`, `_emit`, `_parse_date`, `_catalog_symbol`, `_load_pit_universe`, `_symbols_for_request`) — read their definitions before wiring Task 7.
