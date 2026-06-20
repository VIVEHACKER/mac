# Fund-Book Forward Paper-OOS Ledger — Design Spec (2026-06-20)

**Goal:** start the **Phase-2 forward out-of-sample** track record for the *assembled barbell fund book*
(core + hunt + momentum + bridge), the way `trader/engine/paper_oos.py` already does for the standalone
IDEAL momentum line. Paper-forward is the only genuinely NEW evidence about whether the assembled fund
survives out of sample; to be evidence (not theatre) each book snapshot is **pre-registered immutably**
with entry prices, then **scored on realised prices** vs a benchmark later. This is the "Phase 2 paper
OOS 드릴" — the time-gated step; the engine produces the record, time produces the evidence.

**Architecture:** a pure ledger engine `engine/fund_book_oos.py` (record I/O + scorer, price-source
agnostic — marks passed in) + a driver `scripts/fund_book_oos.py` that assembles the fund book at an
`as_of`, captures entry prices, and appends one pre-registered entry (a dry-run drill — no live trading).
Mirrors the proven `trader/engine/paper_oos.py` schema/semantics so the two ledgers read alike; kept as a
**separate trader-fund module** (no cross-worktree import).

---

## 1. Honest framing (load-bearing)

Unlike the IDEAL line (which has a backtested +8.15%/yr to overfit-check against), the **assembled
barbell has no single backtested edge**: core + hunt make no alpha claim, only the momentum sleeve does.
So this ledger is **pure forward observation** — realised fund return vs benchmark (SPY), accumulating a
live track record. `vs_backtest` is therefore **optional and defaults to None** for the composite (a
single barbell expectation would be a fabricated number). The momentum sleeve's own overfit-vs-backtest
check stays in the IDEAL ledger; here we observe whether the *assembled fund* beats the benchmark live.
Append-only and immutable: a forward record is only credible if history cannot be rewritten.

## 2. Records

- `FundBookOOSEntry(rebal_date, weights, entry_prices, benchmark_symbol, benchmark_price,
  sleeve_fractions, reserve_cash, invested)` — `weights` = fund-level **invested** weights
  (`symbol -> fund_weight`, summing to `invested ≤ 1.0`; the un-invested remainder is `reserve_cash`).
  `sleeve_fractions` / `reserve_cash` / `invested` are recorded for provenance (which barbell policy
  produced this book) but do not affect scoring.
- `FundBookOOSRecord(n_periods, cumulative_return, cumulative_benchmark, cumulative_excess,
  annualized_excess, hit_rate, excess_sharpe, periods_per_year, vs_backtest)` — same shape as the IDEAL
  `OOSTrackRecord` so reports are comparable.

## 3. Functions (mirror trader/engine/paper_oos.py)

- `load_ledger(path) -> list[FundBookOOSEntry]` / `append_entry(path, entry)` — JSONL, append-only;
  `append_entry` **raises** if `(rebal_date)` already recorded (immutability — the whole point).
- `fund_book_to_entry(book, *, rebal_date, entry_prices, benchmark_symbol, benchmark_price)
  -> FundBookOOSEntry` — convert an assembled `engine.fund_book.FundBook` to an entry: `weights =
  {p.symbol: p.fund_weight for p in book.positions}` (fund-level, capped, already validated),
  `entry_prices` filtered to the held symbols. Raises if any held symbol lacks an entry price (can't
  pre-register a buy with no price — fail closed).
- `load_mark_price_history_csv(path)` / `mark_prices_at_dates(history, dates, *, max_staleness_days)` —
  reused verbatim semantics from the IDEAL ledger (sparse rows, per-symbol freshness, staleness bound).
- `score_ledger(entries, mark_prices, *, periods_per_year=12.0, backtest_excess_ann=None)
  -> FundBookOOSRecord` — each consecutive `(entry_i, entry_{i+1})` is one realised holding period:
  `entry_i`'s weights marked at `entry_{i+1}`'s date, fund return = weight-renormalised realised return,
  excess vs the benchmark over the same window. The still-open final entry is not scored. `vs_backtest`
  only when `backtest_excess_ann` is explicitly given (default None for the composite).

Fund return weights by `fund_weight` and **renormalises over the marked symbols** (same as the IDEAL
scorer), so the reserve-cash fraction is treated as flat (0% return) — correct: idle reserve neither
helps nor hurts the invested book's excess vs benchmark.

## 4. Fail-closed / invariants

`append_entry` refuses a duplicate `rebal_date`. `fund_book_to_entry` raises on a held symbol with no
entry price and on a non-positive `benchmark_price`. `score_ledger` skips periods with no marks /
non-positive entry price (never silently scores a frozen mark — `max_staleness_days` bounds carry-forward
in `mark_prices_at_dates`). Empty/one-entry ledger → `n_periods=0` zero record (no realised period yet).

## 5. Tests

`append_entry` round-trips + refuses a duplicate rebal_date; `fund_book_to_entry` maps an assembled
FundBook's positions → weights and raises on a missing entry price; `score_ledger` over a 2-entry ledger
computes the known weighted excess; renormalisation when only some symbols have marks; benchmark
underperformance → negative excess; `vs_backtest` is None unless a backtest figure is passed; empty/
single-entry → zero record; `mark_prices_at_dates` honours `max_staleness_days` (a stale mark is dropped).

## 6. Driver (the drill)

`scripts/fund_book_oos.py`: assemble the fund book at one `effective` as_of (reuse the
`scripts/fund_book.py` assembly path: core + hunt [+ momentum if `--price-history`]), read entry prices
for the held symbols from the price snapshot at `effective`, build the `FundBookOOSEntry`, and
`append_entry` it to `out/fund-book-oos.jsonl` (default). A `--score` mode loads the ledger + a mark
price-history CSV and prints `score_ledger`. `--dry-run` prints the entry without appending (mechanical
verification, like paper_drill). PIT: entry prices are the marks at/<= `effective`; the ledger date is
`effective`. T0 (the first append) starts the clock.

## 7. Deferred

Per-sleeve attribution in the ledger (which sleeve drove the realised excess); a cadence cron (the
21-business-day gate the IDEAL ledger uses) — added once T0 is recorded and the cadence is chosen;
dedupe the shared `load_mark_price_history_csv` / `mark_prices_at_dates` / scoring math against
`trader/engine/paper_oos.py` (the two are intentionally separate worktree modules for now).
