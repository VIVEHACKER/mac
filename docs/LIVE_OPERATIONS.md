# Live Operations Runbook

This system must fail closed. Live trading is not enabled by having a strategy
that backtests well; it is enabled only after model promotion, data-quality
checks, pre-trade risk checks, broker reconciliation, and operator runbooks pass.

## Live Gates

The live environment is incomplete unless all of these are set:

```bash
LIVE_TRADING_ENABLED=true
LIVE_TRADING_ACK_RISK=true
LIVE_ORDER_SUBMISSION_ENABLED=true
LIVE_STRATEGY_ID=<approved strategy id>
LIVE_BROKER=<fake|alpaca-paper|alpaca-live>
LIVE_MAX_CAPITAL=<maximum capital this system may use>
LIVE_POLICY_VERSION=<risk policy version>
LIVE_MIN_PAPER_DAYS=30
LIVE_MIN_SHADOW_DAYS=10
LIVE_MIN_PAPER_OOS_PERIODS=6
LIVE_MIN_PAPER_OOS_VS_BACKTEST=0.5
LIVE_PAPER_OOS_BACKTEST_EXCESS=0.08
LIVE_PAPER_OOS_PRICES=<CSV with Date,<symbols>,SPY closes>
LIVE_MAX_LIMIT_DEVIATION=0.03
LIVE_MAX_MARK_DEVIATION=0.02
LIVE_CATALOG_DB=data/store/live-prices.duckdb
```

Check the effective gate:

```bash
uv run trader live-policy
```

`LIVE_MAX_CAPITAL` is converted into a conservative default risk policy. Current
defaults cap one order at 25% of approved capital and daily new notional at 100%
of approved capital.

`LIVE_ORDER_SUBMISSION_ENABLED` is a separate final switch. Keep it unset during
research, backtesting, paper, and shadow drills.

For `alpaca-live`, the default drill requirement is 30 paper days, 10 shadow
days, 6 scoreable paper-OOS ledger periods, and live/backtest excess ratio ≥0.5x
if `LIVE_MIN_PAPER_DAYS`, `LIVE_MIN_SHADOW_DAYS`,
`LIVE_MIN_PAPER_OOS_PERIODS`, and `LIVE_MIN_PAPER_OOS_VS_BACKTEST` are unset.
`LIVE_PAPER_OOS_PRICES` must point to a close-price CSV so the ledger periods can
be scored, not merely counted. Market orders are disabled by default in the live
risk policy; set `LIVE_ALLOW_MARKET_ORDERS=true` only for a documented
exception.

`live-readiness`, `live-submit`, and `live-price-ingest` default to
`LIVE_CATALOG_DB` instead of the research/backtest catalog. Keep live prices in
`data/store/live-prices.duckdb` so long-running research jobs cannot lock the
live gate.

## Model Promotion

Run the promotion-grade validation suite before any strategy can be treated as a
live candidate:

```bash
uv run trader validate-model QQQ,TLT,QID,SDS \
  --start 2008-01-01 \
  --end 2020-12-31 \
  --benchmark SPY \
  --benchmark-market us \
  --universe-csv data/universes/multi-asset-etf-2008.csv \
  --no-fetch \
  --momentum-lookback 63 \
  --ensemble-momentum-lookbacks 63,126 \
  --reversal-lookback 5 \
  --volatility-lookback 21 \
  --risk-filter-lookback 100 \
  --ensemble-risk-filter-lookbacks 100,200 \
  --risk-filter-vote-threshold 0.5 \
  --top-n 1 \
  --rebalance-days 21 \
  --weighting equal \
  --defensive-only \
  --defensive-basket TLT,CASH \
  --defensive-selection-lookback 63 \
  --max-leverage 1.0 \
  --crash-hedge-symbols QID,SDS \
  --crash-hedge-weight 1.0 \
  --crash-hedge-trigger-lookback 5 \
  --crash-hedge-trigger-drawdown 0.05 \
  --crash-hedge-hold-days 5 \
  --crash-hedge-selection-lookback 42 \
  --train-years 5 \
  --test-years 3 \
  --step-years 1 \
  --momentum-lookbacks 63 \
  --reversal-lookbacks 5 \
  --volatility-lookbacks 21 \
  --top-ns 1 \
  --risk-filter-lookbacks 100 \
  --weighting-modes equal \
  --rebalance-days-values 21 \
  --selection-metric annualized-return \
  --fee-stress-bps 2,5,10 \
  --stress-windows gfc-crash:2008-09-01:2009-03-09,covid-crash:2020-02-15:2020-03-23 \
  --min-positive-test-rate 0.60 \
  --min-parameter-positive-rate 0.60 \
  --min-stress-windows 2 \
  --min-stress-return 0.30 \
  --max-stress-drawdown 0.35 \
  --record-gate \
  --strategy-id qqq-tlt-qid-sds-crash
```

`validate-model` runs:

- walk-forward parameter selection and out-of-sample test windows
- fee stress, such as 2/5/10 bps turnover cost
- local parameter perturbation around the base momentum and risk-filter lookbacks
- named stress windows, such as GFC, COVID crash, and 2022 rates
- live controls: momentum ensemble, risk-filter vote, defensive basket ranking,
  and volatility-targeted exposure capping
- crash-alpha requirement: every tested stress window must clear
  `--min-stress-return`, for example +30% total return
- crash hedge sleeve: `--crash-hedge-symbols` are excluded from normal factor
  ranking and are only used when both benchmark drawdown and trailing return
  breach the trigger threshold

Current 2008-2020 crash-sleeve evidence:

- Full sample: +306.18% total, +11.39% annualized, 36.63% max drawdown
- GFC crash window 2008-09-01 to 2009-03-09: +64.47%
- COVID crash window 2020-02-15 to 2020-03-23: +32.51%
- `validate-model --min-stress-return 0.30`: stress windows PASS, full
  promotion BLOCK because walk-forward positive test rate, fee stress, and
  parameter perturbation are still below live-promotion thresholds

The legacy manual gate remains available when evidence was produced elsewhere:

```bash
uv run trader model-gate \
  --strategy-id qqq-tlt-defensive \
  --params M63/R5/V21/RF100/Top1/equal \
  --windows 8 \
  --positive-test-rate 0.625 \
  --avg-test-excess 0.0208 \
  --worst-test-mdd 0.23 \
  --fee-stress-passed \
  --pit-audit-passed \
  --full-sample-annualized-return 0.18 \
  --full-sample-mdd 0.25 \
  --stress-windows-tested 2 \
  --worst-stress-return 0.35 \
  --stress-passed \
  --registry data/store/research-registry.jsonl
```

The gate blocks weak evidence by default:

- fewer than 8 walk-forward test windows
- positive test rate below 60%
- average test annualized excess not above 0
- worst test drawdown above 30%
- missing fee stress pass
- missing PIT audit pass

For live promotion, prefer `validate-model --record-gate` over manual
`model-gate` because it records fee stress and stress-window context in one run.
The live readiness gate applies an additional stricter evidence check: the latest
approved registry record must include at least two passing stress windows and a
worst stress return of at least +30%.

## Readiness Gate

Before any live submit path can be used, run:

```bash
uv run trader live-readiness \
  --require-order-submission \
  --require-broker-preflight \
  --require-price QQQ:us,TLT:us,QID:us,SDS:us \
  --max-price-age-days 2 \
  --registry data/store/research-registry.jsonl \
  --halt-state data/store/live-halt.json \
  --catalog-db data/store/live-prices.duckdb
```

This fails closed unless all of these are true:

- live environment variables are complete
- `LIVE_ORDER_SUBMISSION_ENABLED=true` when real submission is required
- `--require-broker-preflight` can read the broker account, positions, and market
  clock without placing orders; Alpaca keys, account block flags, positive
  equity, USD currency, minimum buying power, and open-market state are checked
  before readiness can pass
- the latest registry decision for `LIVE_STRATEGY_ID` is approved
- live-grade strategy evidence includes passing stress windows and +30% worst
  stress-window return
- required paper/shadow drill streaks are complete
- persistent halt latch is clear
- required prices exist, are fresh, have a source, and are not research-grade
  sources in live mode

The report includes `Operational Confidence` and `Confidence Deductions`. This
score is an execution-readiness score only; it is not a profit forecast. Any
`blocked-*` band means real order submission remains a no-go regardless of the
numeric score. The report also prints `Next Actions` for each blocking class so
the operator can move from a blocked state to the next concrete remediation
without guessing.

The live execution runner also checks the broker market clock immediately before
real submission (`dry_run=False`). If the market is closed, the batch is blocked
without recording intents, so the same orders remain retryable at the next open.

The current crash-sleeve candidate intentionally fails full promotion. Do not set
`LIVE_STRATEGY_ID` to that candidate for real-money submission until
`validate-model` records an approved latest decision.

## Halt Latch

The halt latch is persistent and survives process restart.

```bash
uv run trader live-halt status
uv run trader live-halt activate --reason "broker position mismatch"
uv run trader live-halt clear --reason "mismatch reconciled"
```

Clearing a halt is an operator action and should be recorded in the daily report.

## Broker-Grade Price Ingest

Use Alpaca latest bars to replace Yahoo/manual research prices before readiness:

```bash
export ALPACA_API_KEY=...
export ALPACA_SECRET_KEY=...

uv run trader live-price-ingest QQQ,TLT,QID,SDS \
  --feed iex \
  --catalog-db data/store/live-prices.duckdb
```

Stored bars use a source such as `alpaca:iex:latest_bar`, which the live quality
gate treats as broker-grade. Yahoo, manual, fixture, test, and missing sources
remain blockers.

For intraday/live operation, run the WebSocket stream instead of relying on a
one-shot latest-bar poll:

```bash
uv run trader live-price-stream QQQ,TLT,QID,SDS \
  --feed sip \
  --timeout-seconds 90 \
  --catalog-db data/store/live-prices.duckdb
```

Use `--feed sip` when the account has SIP market-data entitlement; `--feed iex`
is acceptable for paper drills but is not a full-market NBBO substitute. Use
`--timeout-seconds` for smoke tests and credential drills; omit it for the
long-running production stream supervisor.

## Order Gate Dry Run

Use `live-dry-run` to exercise idempotency, halt, and pre-trade checks without
touching a real broker:

```bash
uv run trader live-dry-run QQQ \
  --side buy \
  --qty 2 \
  --price 100 \
  --max-order-notional 1000 \
  --order-log data/store/live-orders.jsonl \
  --halt-state data/store/live-halt.json
```

Use the fake broker path to test downstream order behavior:

```bash
uv run trader live-dry-run QQQ --side buy --qty 2 --price 100 --submit-fake
uv run trader live-dry-run QQQ --side buy --qty 2 --price 100 --submit-fake --fake-mode partial
uv run trader live-dry-run QQQ --side buy --qty 2 --price 100 --submit-fake --fake-mode reject
uv run trader live-dry-run QQQ --side buy --qty 2 --price 100 --submit-fake --fake-mode timeout
```

`--fake-mode timeout` latches halt because order state is uncertain.

Expected local drill evidence:

- normal `live-dry-run`: `Status | accepted`
- `--submit-fake --fake-mode timeout`: exit code 3, CRITICAL
  `broker_uncertain_submit` event, and persistent halt latch
- no real broker credentials are required for these mechanical checks

## Live Submit Path

`live-submit` is the only CLI path intended to place a real order. It defaults to
shadow mode; `--submit` is required for broker submission, and `--ack-live-order`
is required with `--submit`.

Shadow the live path without broker submission:

```bash
uv run trader live-submit QQQ \
  --side buy \
  --qty 2 \
  --price 100 \
  --as-of 2026-05-25 \
  --order-log data/store/live-orders.jsonl \
  --halt-state data/store/live-halt.json
```

Submit to Alpaca only after `live-readiness` passes and the strategy is approved:

```bash
uv run trader live-submit QQQ \
  --side buy \
  --qty 2 \
  --price 100 \
  --order-type limit \
  --limit-price 99.50 \
  --submit \
  --ack-live-order \
  --broker alpaca-live \
  --order-log data/store/live-orders.jsonl \
  --halt-state data/store/live-halt.json
```

The submit path rechecks model approval, halt state, data freshness, idempotency,
pre-trade notional limits, daily order count, daily new notional, symbol weight,
gross exposure, cash fraction, broker account blocks, and uncertain submit state.
It also blocks when the submitted `--price` deviates from the latest live catalog
close by more than `LIVE_MAX_MARK_DEVIATION` or `--max-mark-deviation`. Any
uncertain broker submit activates the persistent halt latch.

## Reconciliation

After paper/live submission, compare target positions with broker positions:

```bash
uv run trader live-reconcile \
  --broker alpaca-paper \
  --expected QQQ:us:2,TLT:us:0 \
  --halt-state data/store/live-halt.json
```

Any mismatch activates the persistent halt latch unless `--no-halt-on-mismatch`
is explicitly used for a drill.

## Data Quality

Live candidates must run with required price checks:

```bash
uv run trader quality \
  --require-price QQQ:us,TLT:us,SPY:us \
  --max-price-age-days 5 \
  --live-policy \
  --strict
```

`--live-policy` warns when a required live price comes from a research-grade
source such as Yahoo, manual, fixture, or test data. Treat that as a blocker for
real money until a broker-grade or licensed live source is wired.

## Paper and Shadow Drill

Before any real order:

1. Run 30 trading days in paper mode.
2. Run 10 trading days in shadow mode: create order intents and risk verdicts,
   but do not submit to a real broker.
3. Confirm daily reports exist for every run.
4. Confirm duplicate order count is zero.
5. Confirm broker/internal position mismatch count is zero.
6. Confirm halt drills block new orders.

Record drill completion in the local log:

```bash
uv run trader live-drill record --mode paper --day 2026-05-25
uv run trader live-drill record --mode shadow --day 2026-05-25
uv run trader live-drill status --day 2026-05-25
```

## Forward-OOS track record (paper as evidence)

`paper_drill.py` is the operational gate (implementation/kill-switch/fills). To make
paper trading *statistical evidence* about the edge, each rebalance is also appended,
pre-registered and immutable, to `out/paper-oos-ledger-<strategy_id>.jsonl` (on by
default; `--no-record-oos` to skip). The ledger refuses to rewrite an existing
(date, strategy), so history cannot be back-edited.

Monthly, score the accumulated record against realised prices:

```bash
python -m scripts.paper_oos --strategy-id aqr_top5_cap20_trail10_pit110
```

It reports the **live cumulative excess vs benchmark**, annualised live excess, and the
ratio to the backtested +8%/yr. A live excess far below backtest (ratio < 0.5) is
overfitting revealing itself. Honest caveats baked into the report: < 6 closed periods =
implementation check only, not alpha proof (MinTRL ~21 months); only closed holding
periods are scored (no MTM hindsight on the open position).

## Incident Response

Immediately activate halt on:

- unknown broker position
- duplicate order
- uncertain submit state
- stale required data
- policy breach
- missing heartbeat
- API key exposure

Then:

1. Stop the scheduler.
2. Pull broker orders and positions from the broker UI/API.
3. Compare against `data/store/live-orders.jsonl`.
4. Cancel open orders if they are not intentional.
5. Restore the internal ledger only after broker state is known.
6. Clear halt with a reason only after the root cause is documented.

---

## IDEAL line monthly operating procedure (top5 conc5 candidate / top7 baseline)

Two registered strategies share this universe:
- `aqr_top7_cap20_trail10_pit110` — the validated **baseline**; keep it LIVE until conc5 clears paper.
- `aqr_top5_cap20_trail10_pit110` — **conc5**, model-gate APPROVED, in PAPER OOS (3-6 mo) before any
  live capital. This is `paper_drill.py`'s DEFAULT config.

Universe: 106 PIT names (`scripts/aqr_ideal_walkforward.py:MEGACAPS`). Rebalance cadence: 21 trading
days. Each strategy keeps its OWN NAV/peak/positions track
(`out/paper-drill-state-<strategy_id>.json`) — they are never cross-contaminated.

**Reproducibility is mandatory.** A background re-ingest of `fundamentals_q`
(3,383 → 7,291 records) silently broke the Variant N backtest (CAGR 19.91% →
14.04%). Always pin fundamentals to a content-hashed snapshot before generating
orders, so the picks are auditable and replayable.

### Each rebalance

```bash
# 1. (Once per data refresh) Pin the current fundamentals. Records the sha256.
.venv/bin/python scripts/snapshot_fundamentals.py fundamentals-$(date +%F)

# 2. Generate the rebalance against the PINNED snapshot (reproducible). ALWAYS pass --top-n so the
#    strategy can never switch silently (paper_drill.py defaults to top5/conc5):
#    conc5 paper candidate:
.venv/bin/python scripts/paper_drill.py --top-n 5 \
    --snapshot data/snapshots/fundamentals-$(date +%F).csv
#    validated baseline (run until conc5 clears paper OOS):
.venv/bin/python scripts/paper_drill.py --top-n 7 \
    --snapshot data/snapshots/fundamentals-$(date +%F).csv
#    --strategy-id is derived from --top-n (5->top5, 7->top7); a conflicting explicit id is rejected.

#    Output (per strategy, so the two runs don't overwrite each other):
#      out/paper-drill-orders-aqr_top5_cap20_trail10_pit110.md
#      out/paper-drill-orders-aqr_top7_cap20_trail10_pit110.md
#    (each header records the snapshot name + hash provenance)
```

Each orders file header shows `Fundamentals: snapshot:<name>` — confirm it is a
snapshot, NOT `LIVE-CATALOG (NOT reproducible)`. If it shows the live fallback,
the snapshot path was wrong; do not trade those orders.

### Mechanical verification (no broker)

Run the `live-dry-run` block printed in the strategy's
`out/paper-drill-orders-<strategy_id>.md`. These go through the full pre-trade
risk path (`risk/pretrade.py`) without broker calls.

### Paper, then live (operator decision — fail closed)

Live is gated by the env vars in "Live Gates" above plus `live-readiness`. For
`alpaca-live`, `live-readiness` also fails closed until the current strategy's
`out/paper-oos-ledger-<strategy_id>.jsonl` has at least
`LIVE_MIN_PAPER_OOS_PERIODS` scoreable closed periods using
`LIVE_PAPER_OOS_PRICES`, and the live/backtest excess ratio is at least
`LIVE_MIN_PAPER_OOS_VS_BACKTEST`. The recommended path before any live capital:

1. Alpaca **paper** keys in `.env`; run the `live-submit` block monthly for
   `LIVE_MIN_PAPER_DAYS` (default 30) and at least
   `LIVE_MIN_PAPER_OOS_PERIODS` scoreable closed ledger periods (default 6) —
   really 3-6 months for this strategy.
2. Validate broker reachability with
   `trader live-readiness --require-order-submission --require-broker-preflight ...`.
3. Validate fills reconcile (`trader live-reconcile`).
4. Only then consider `alpaca-live` with ≤5% of capital and Kelly ≤ 0.25.

### Pre-deployment reproducibility re-validation

Before promoting, confirm the backtest still matches on the pinned snapshot:

```bash
# Re-run the walk-forward / model-gate against the snapshot, not the live DB.
.venv/bin/python scripts/aqr_ideal_walkforward.py        # full WF (yfinance prices + catalog funds)
.venv/bin/python scripts/significance_test.py            # PSR/DSR/bootstrap battery
.venv/bin/python scripts/ideal_fundamental_sensitivity.py # coverage-robustness (ROBUST as of 2026-05-29)
```

Statistical evidence as of 2026-05-29 (see `out/significance-report.md`,
`out/ideal-fundamental-sensitivity.md`): monthly Sharpe 1.40, bootstrap 95% CI
[0.92, 1.92], PSR 100%; Sharpe holds ~1.4 with no directional decay even at 25%
fundamental coverage. Variant N (trader-CLI line) is NOT deployable — it is both
non-reproducible and concentration-fragile.

---

## 실매매 활성화 체크리스트 (2026-07-03 — 단일 정본)

시스템 측 준비는 완료됐다. 남은 것은 **① 브로커 키 ② 실증 시간게이트** 둘뿐이며,
아래 순서를 건너뛰는 코드 경로는 존재하지 않는다(전부 fail-closed 게이트).

### 현재 완료된 시스템 측 항목
- 정산 무결성: reconcile-in-flight 자가치유 + live-submit FillPoll + 원장 fsync(파일+디렉터리)
- 섹터 집중 캡: pretrade 강제 + `LIVE_MAX_SECTOR_WEIGHT`(기본 0.35) + `data/sectors/sp100-pit-2008-sectors.csv` 자동 로드 — 실주문(`--submit`)은 섹터맵 없으면 차단
- 주문 회수: `trader live-cancel <client_order_id>` (게이트 무관 상시 가용 — halt 중에도)
- 델타 주문안: `trader rebalance-plan` — 매도 우선 델타 + 라이브 게이트 사전검증 + `out/rebalance-plan-*.json`
- 가격 신선도: price-ingest cron 설치됨(키 없으면 yahoo EOD, 키 설정 시 같은 cron이 IEX로 자동 전환)
- forward-OOS 원장: cadence cron 가동 중(T0 2026-06-05, 21영업일 주기)

### Phase A — 브로커 키 (사용자 액션, ~10분)
1. https://alpaca.markets 가입 → **Paper** API 키 발급
2. `.env`에 `ALPACA_API_KEY=...` / `ALPACA_SECRET_KEY=...`
3. 검증: `.venv/bin/trader live-price-ingest SPY --source alpaca` 성공 = 키 정상
   (이 순간부터 price-ingest cron이 IEX로 자동 전환)

### Phase B — 페이퍼 루프 가동 (매월, 반자동)
```bash
export LIVE_TRADING_ENABLED=true LIVE_TRADING_ACK_RISK=true \
  LIVE_STRATEGY_ID=aqr_top7_cap20_trail10_pit110 LIVE_BROKER=alpaca-paper \
  LIVE_MAX_CAPITAL=10000 LIVE_POLICY_VERSION=1 LIVE_ORDER_SUBMISSION_ENABLED=true
.venv/bin/trader rebalance-plan --top-n 7   # 델타 주문안 생성(사전검증 포함)
# → out/rebalance-plan-*.json 검토 → ALL PASS 확인 → 출력된 live-submit 명령 실행(사용자 승인)
.venv/bin/trader live-drill --kind paper    # 드릴 일수 기록
.venv/bin/trader live-reconcile --from-store  # 제출 후 정산(자가치유 포함)
```
- 미체결 이탈 주문: `trader live-cancel <id>` → `rebalance-plan` 재생성
- 상태 확인: `trader live-readiness` / 사고 시: `trader live-halt`

### Phase C — 실증 시간게이트 (코드로 단축 불가)
alpaca-live 전환에는 하드 플로어가 강제된다(ack로도 못 내림):
- paper 드릴 연속 **30일** + shadow **10일**
- forward-OOS **닫힌 기간 6개**(≈2026-12월 초 도달, T0=06-05) + vs_backtest **≥0.5x**
- `LIVE_ACCEPT_REDUCED_VALIDATION`은 드릴 일수만 완화 가능 — **운영 .env에 상시 설정 금지**

### Phase D — 라이브 전환 (전부 충족 후)
1. Alpaca **Live** 키 발급, `.env` 교체 + `LIVE_BROKER=alpaca-live`
2. `trader live-readiness --require-order-submission` → **Ready | yes** 확인
3. 첫 달은 `LIVE_MAX_CAPITAL=10000` 유지(소액), 이후 성과 보고 증액 재검토
4. 매 제출은 Phase B와 동일 반자동 루프(자동 제출 없음, `--ack-live-order` 수동)

### 미해결/주의
- 수동 브로커 어댑터(`manual-*`)는 `cancel_order` 미구현 — live-cancel이 명시 거부함
  (수동 주문은 브로커에서 직접 취소). API 어댑터 통합 시 cancel_order 구현 필요.
