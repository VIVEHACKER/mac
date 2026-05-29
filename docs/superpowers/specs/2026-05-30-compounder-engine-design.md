# Design Spec — Multi-Archetype Compounder-Quality Engine (P1)

_Date: 2026-05-30. Status: approved (brainstorming). Research-only; not investment advice._

## 1. Purpose & role

A decision-support engine for **concentrated, high-conviction, multi-year ("ten-bagger")
investing**. Given a universe with point-in-time (PIT) fundamentals + price bars, it scores
each stock as a long-term compounder candidate under **three archetypes**, ranks them
cross-sectionally, and emits a **per-name evidence dossier**.

It is NOT an auto-traded strategy and NOT a 10x predictor. Pure quant cannot pick "the 5
that will 10x" — that is investing judgment. This engine generates an evidence-driven
candidate funnel; the final 5–10 picks and hold/exit decisions stay with the operator.

Market-agnostic by design (US first, Korea later).

## 2. Scope

### In scope (P1)
- `engine/compounder_metrics.py` — PIT metric computation (pure functions).
- `engine/compounder.py` — three archetype scorers + cross-sectional ranking funnel.
- `engine/compounder_dossier.py` — per-name evidence dossier (dataclass + markdown).
- `trader compounder-scan` CLI — scan a universe (PIT, as-of), emit ranked candidates +
  dossiers; pins fundamentals to a content-hashed snapshot for reproducibility.
- Full TDD coverage.

### Explicitly OUT (deferred to later phases — see §8)
Universe ingest (P2), alt-data/qualitative enrichment (P3), Korea/DART (P4), PIT
ten-bagger validation (P5), thesis-drift monitoring (P6). Auto-trading / order generation
(this line is a watchlist, never auto-traded). TAM/moat/management qualitative data beyond
quantifiable proxies.

## 3. Architecture (isolated, independently testable units)

```
catalog (PIT fundamentals + bars, pinned to snapshot)
   │
   ▼
compounder_metrics.py   ── per-name raw signals, as-of aware (no look-ahead)
   │
   ▼
compounder.py           ── 3 archetype scorers → cross-sectional Z → rank_compounders()
   │
   ▼
compounder_dossier.py   ── build_dossier(): metrics + trends + scores + flags + rationale
   │
   ▼
trader compounder-scan  ── CLI: universe scan → ranked candidates + dossier report
```

### 3.1 `engine/compounder_metrics.py`
Pure functions over an as-of-filtered, chronologically sorted list of `FundamentalRecord`
plus recent `PriceBar`s. All return `float | None` (None = insufficient data; never raise
on missing inputs).

- **Growth**: `revenue_cagr(records, years)`, `revenue_growth_acceleration(records)`
  (recent YoY minus prior YoY), `eps_growth(records)`.
- **Quality**: `operating_margin(rec)`, `net_margin(rec)`, `margin_trend(records)`
  (slope of margin over time), `roic(rec)` ≈ `net_income / (total_equity + total_debt)`,
  `fcf_margin(rec)` = `free_cash_flow / revenue`, `fcf_conversion(rec)` = `free_cash_flow / net_income`.
- **Durability / reinvestment**: `reinvestment_rate(records)`, `debt_to_equity(rec)`,
  `net_cash(rec)`.
- **Dilution**: `share_growth(records)` (shares_out CAGR; negative = buyback).
- **Valuation** (needs latest price): `pe(rec, price)`, `pfcf(rec, price)`, `ps(rec, price)`,
  `pb(rec, price)`.

Each metric is independently unit-tested against hand-computed synthetic inputs.

### 3.2 `engine/compounder.py`
- `@dataclass ArchetypeScore`: `archetype: str`, `score: float` (0–100), `components:
  dict[str, float]`, `flags: tuple[str, ...]`.
- `@dataclass CandidateScore`: `symbol`, `best_archetype`, `best_score`, `scores:
  dict[str, ArchetypeScore]`, `metrics: dict[str, float | None]`.
- Three scorers, each a pure function of the per-name metrics + the cross-sectional
  context (Z-scores computed within the universe so scoring is relative quality):
  - **Profitable Compounder** (quality-led): high & rising ROIC, FCF margin, positive
    margin trend, low dilution, manageable debt, steady growth.
  - **Hypergrowth Disruptor** (growth-led, profit optional): high revenue CAGR + positive
    acceleration, improving gross/operating margin, growth runway; tolerates negative net
    income and high P/S **only if** margins are improving (unit-economics gate).
  - **Value / Turnaround** (value-led): cheap (low P/FCF, low P/B), margin improving off a
    low base, FCF recovery, debt reduction.
- `rank_compounders(universe_metrics, top_n) -> list[CandidateScore]`: compute all three
  archetype scores per name, assign best-fit archetype, rank overall and per-archetype,
  return top-N. Names with insufficient history are excluded with a recorded reason.

### 3.3 `engine/compounder_dossier.py`
- `@dataclass Dossier`: symbol, archetype, score, the metric table, trend direction per
  metric, red flags (e.g., high dilution, debt spike, margin rolling over, negative FCF
  without margin improvement), and a one-paragraph generated rationale.
- `format_dossier_markdown(dossier) -> str`.
- **Extension hook (for P3)**: `Dossier` carries an optional `alt_signals: dict[str, Any]`
  slot (insider buying, analyst-coverage count, institutional flow, earnings surprise,
  news narrative) that P1 leaves empty and P3 populates.

### 3.4 CLI `trader compounder-scan`
Args: positional `symbols`/`ALL`, `--pit-universe` / `--universe-csv`, `--as-of`,
`--snapshot` (fundamentals pin, fail-closed like `paper_drill`), `--top-n`, `--archetype`
(filter), `--output`. Reuses the existing catalog + PIT-universe + snapshot loaders.

## 4. Data flow & PIT discipline

All fundamentals accessed strictly as-of (`asof_ts <= as_of`) — no look-ahead. This is
mandatory because P5 (historical validation) replays the screen at past dates. Fundamentals
are pinned to a content-hashed snapshot (`data/fundamentals_snapshot.py`) so a scan is
deterministic and auditable.

## 5. Testing (TDD)

- **metrics**: each function vs hand-computed synthetic `FundamentalRecord` series
  (known revenue CAGR, rising/falling margin, ROIC, dilution, FCF conversion). Edge cases:
  None fields, negative earnings (hypergrowth path), zero equity/revenue (guarded).
- **scorers**: four synthetic profiles — ideal Profitable Compounder, ideal Hypergrowth,
  ideal Value, and "junk" — assert each ideal scores highest under its own archetype and
  junk scores low under all three.
- **rank_compounders**: small synthetic universe → correct ordering, best-fit assignment,
  insufficient-history exclusion.
- **dossier**: contains required metrics + correct flags; markdown renders.

## 6. Error handling

- Insufficient history (< required quarters) → excluded with reason, never crash.
- Missing fundamental fields → archetype-appropriate (hypergrowth tolerates missing profit;
  value/compounder require profitability inputs, else those archetypes score low/excluded).
- Division by zero (zero equity/revenue/NI) → guarded, returns None.

## 7. Honest limitations (and how each is addressed)

| # | Limitation | Disposition |
|---|---|---|
| 1 | Current universe is megacap-only → only "large compounders", not 10x candidates | **Overcome via P2**: engine is universe-agnostic; CLI takes a universe arg; P2 ingests US small/mid (where 10x lives) as the immediate next phase. |
| 2 | Quant precursors do not predict 10x; base rates are low | **Mitigated, NOT eliminated** (irreducible). P5 measures the screen's historical hit-rate on real past ten-baggers; multi-archetype + P3 alt-data cross-confirm. Any "10x guarantee" claim is rejected. |
| 3 | No qualitative (moat/management) data | **Overcome via P3** quantifiable proxies + alt-data: insider buying (Form 4), low analyst coverage (undiscovered), institutional accumulation (flows), earnings surprise, on-demand news/narrative (web search). P1 leaves the `alt_signals` hook empty for P3 to fill. |

## 8. Roadmap (phases after P1)

- **P2** — US small/mid universe ingest (SEC EDGAR bulk via existing `sec_edgar_ingest.py`;
  ~Russell-2000-scale), snapshotted. Produces real ten-bagger candidates.
- **P3** — alt-data / qualitative enrichment of the dossier (Form 4 insider, coverage,
  flows, earnings surprise, web narrative).
- **P4** — Korea via DART OpenAPI (market-agnostic engine already supports it).
- **P5** — PIT historical validation: replay the screen at past dates, measure hit-rate on
  known ten-baggers, control for survivorship/look-ahead.
- **P6** — thesis-drift monitor for held names (fundamentals still compounding? exit on
  thesis break).

Each phase is its own spec → plan → implementation cycle.

## 9. Success criteria (P1)

- All metric/scorer/rank/dossier units pass TDD; ruff + mypy clean.
- `trader compounder-scan ALL --pit-universe SP100_PIT_2008 --snapshot <pin>` runs
  deterministically and emits a ranked, archetype-tagged candidate report with dossiers.
- Engine accepts an arbitrary universe with zero code change (only data) — verified by the
  CLI universe arg, so P2 is purely a data step.
