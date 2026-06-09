# Deployment Readiness — IDEAL line (`aqr_top7_cap20_trail10`)

_Last assessed: 2026-05-29. Research-only; not investment advice._

## Verdict

**PAPER-deployable now. NOT real-money-deployable until the operator gates below
pass.** The strategy is statistically sound and the order pipeline is
reproducible and mechanically wired through the production risk path, but
real-capital deployment is blocked on human/time gates (paper OOS duration,
broker keys, live kill-switch test) that code cannot satisfy.

| Dimension | Status |
|---|---|
| Statistical edge (OOS) | ✅ strong — Sharpe 1.40, PSR 100%, bootstrap CI [0.92, 1.92] |
| Fundamental-coverage robustness | ✅ robust — no directional decay to 25% coverage |
| Reproducibility (fundamentals) | ✅ pinned via content-hashed snapshot |
| Backtest↔order-gen parity | ✅ both can pin the same snapshot |
| Pre-trade risk path / kill switch | ✅ code present; ⚠️ untested under a live broker |
| Paper-trading OOS | ❌ zero days completed |
| Live env + broker keys | ❌ operator action required |
| Independent (Codex) code review | ⚠️ blocked by usage limit (resets 2026-05-31) |

## What this means

Variant N (the trader-CLI line) is **out**: non-reproducible (CAGR 19.91% →
14.04% when fundamental coverage changed) and concentration-fragile (`top_n=2`).
The IDEAL line (`top_7 + 20% cap + portfolio trailing stop`) is the deployment
candidate — its diversification is exactly what makes it robust where Variant N
is not.

## Done this session (2026-05-29)

- **Reproducibility root-cause fix.** `data/fundamentals_snapshot.py` pins
  `fundamentals_q` to a content-hashed CSV (hash excludes provenance `source`,
  normalizes NaN/inf, fails closed on a missing manifest). `scripts/snapshot_fundamentals.py`
  captures the current dataset (`data/snapshots/fundamentals-2026-05-29.csv`,
  7,291 records). 11 tests.
- **Productionized `paper_drill.py`**: validated 106-name universe, **fails
  closed** unless fundamentals are pinned to a snapshot (or `--allow-live-fundamentals`),
  records data provenance in the orders file.
- **Backtest↔order-gen parity**: `aqr_ideal_walkforward.py --snapshot` lets the
  promotion backtest pin the same snapshot the live rebalance uses.
- **Statistical battery** (`scripts/significance_test.py`): PSR / Deflated Sharpe
  Ratio / circular block bootstrap, 33 tests. See `out/significance-report.md`.
- **Coverage-robustness test** (`scripts/ideal_fundamental_sensitivity.py`):
  see `out/ideal-fundamental-sensitivity.md`.
- Adversarial multi-agent audit (this document reflects its findings).

## Statistical evidence (current data, pinned)

- Full-sample monthly Sharpe **1.40**, annualized return ~24%, MDD 18.5%.
- PSR(SR>0) **100%**; bootstrap 95% CI **[0.92, 1.92]**, recentered null p < 1e-4.
- Walk-forward 15 windows: **86.7% positive (13/15), +8.15%/yr avg excess**, avg
  Sharpe 1.41, worst-window MDD 19.19% — reproducible from the pinned snapshots
  `prices-ideal-2026-06-01` (sha `cff8205…`) + `fundamentals-2026-06-01-gp2`:
  `TRADER_REQUIRE_PINNED=1 uv run python scripts/aqr_ideal_walkforward.py --prices
  data/snapshots/prices-ideal-2026-06-01.csv --snapshot
  data/snapshots/fundamentals-2026-06-01-gp2.csv` (= `out/aqr-ideal-walkforward.md`).
  Supersedes the earlier "93.3% / +7.67%" figure, which traced to a registry record
  with an empty `command` field and was not reproducible.
- Deflated Sharpe stays ~99.7% under the sampling-variance V even at N=104
  trials; the regime-proxy V collapses it, but that V is a pessimistic upper
  bound (see report §1/§3). The **bootstrap** is the most assumption-light signal.
- Coverage robustness: Sharpe holds ~1.4 with **no directional decay** when
  25–100% of fundamentals are randomly retained (directional shift −0.027 vs
  Variant N's −0.22).

### Honest caveats (do not omit when deciding)

- "Robust to coverage" ≠ "robust to the exact 3,383→7,291 record swap that broke
  Variant N" — that specific swap is untestable (the pre-ingest snapshot is gone).
- Survivorship: the 106-name universe is PIT-sourced but `pit-2008-backfill`, not
  a true historical membership feed. Real alpha is estimated at +5–8%/yr after
  this and fees.
- **Prices are NOT pinned** — `yfinance` OHLC can revise, so the *price* leg of a
  backtest is not byte-reproducible (the observed Variant N break was
  fundamentals, with prices identical; still, pin prices before claiming full
  end-to-end reproducibility).

## Remaining gaps to real money (ranked)

| # | Gap | Owner | Severity |
|---|---|---|---|
| 1 | Zero paper-trading OOS — run ≥30 (ideally 90–180) paper days first | operator | blocker |
| 2 | Live kill-switch / halt latch never exercised against a real broker | operator + code | blocker |
| 3 | Broker (Alpaca) keys not provisioned/verified; live-prices DB not populated | operator | blocker |
| 4 | Independent Codex review incomplete (usage limit → 2026-05-31) | operator | blocker |
| 5 | Fundamentals refresh + re-snapshot is manual & unscheduled (stale-data risk) | code/ops | high |
| 6 | 21-trading-day rebalance cadence not calendar-gated in code | code | high |
| 7 | Kelly sizing not applied (defaults to full exposure); cap ≤0.10–0.25 | operator/code | high |
| 8 | Single-strategy concentration; no portfolio-level diversification/hedge | operator | high |
| 9 | Price leg not pinned (see caveat); tax/fee drag unmodeled | code | medium |

## Go / no-go checklist (before any real capital)

1. `python scripts/snapshot_fundamentals.py fundamentals-<date>` — pin data.
   Pass `--top-n <N>` matching the strategy under test in steps 2-3 (5 = conc5,
   7 = baseline); paper_drill DEFAULTS to top5, so always make N explicit.
2. `python scripts/aqr_ideal_walkforward.py --top-n <N> --snapshot data/snapshots/<name>.csv` —
   confirm Sharpe ≥ ~1.35 on the **pinned** data (parity).
3. `python scripts/paper_drill.py --top-n <N> --snapshot data/snapshots/<name>.csv` — orders
   header must read `Fundamentals: snapshot:<name>` (never `LIVE-CATALOG`).
4. Run the `live-dry-run` block; confirm pre-trade gates pass.
5. Paper-trade ≥30 days; reconcile fills (`trader live-reconcile`).
6. Complete the Codex review (after 2026-05-31).
7. Set `LIVE_*` gates (see `LIVE_OPERATIONS.md`), start at ≤5% capital, Kelly ≤0.25.

Live activation (`LIVE_TRADING_ENABLED`, broker keys, capital) is an explicit
operator decision and is intentionally NOT automated.
