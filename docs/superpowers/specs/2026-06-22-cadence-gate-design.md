# Cadence Gate — Design Spec (2026-06-22)

**Goal:** a pure, testable **recording-cadence gate** for the fund-book forward OOS ledger — "is it time
to record the next entry?" — so a scheduled job (cron, later) records on a disciplined ~monthly cadence
instead of every run. The fund-book analog of the IDEAL line's validated **21-business-day** OOS cadence
gate. Completes the operational half of the Phase-2 recording loop.

**Architecture:** standalone pure engine `engine/cadence.py` (no I/O, date math only). Wiring into
`scripts/fund_book_oos.py` (a `--cadence-days N` guard on `--record`) is a small follow-on once this
lands; kept separate so it doesn't touch the OOS engine.

---

## 1. Honest framing

Pure date arithmetic — counts **business days (Mon–Fri)**, no market-holiday calendar (the cadence is
approximate — "about a month between snapshots", not settlement-critical). Holiday awareness is deferred.
Makes no investment claim; it only paces recording.

## 2. Functions

- `business_days_between(start: date, end: date) -> int` — count of business days **after `start`, up to
  and including `end`** (so consecutive weekdays → 1; `end <= start` → 0; spans weekends correctly).
- `is_due(last_recorded: date | None, today: date, *, cadence_business_days: int = 21) -> bool` —
  `True` if `last_recorded is None` (T0 is always due) **or** `business_days_between(last_recorded,
  today) >= cadence_business_days`.
- `next_due_date(last_recorded: date, *, cadence_business_days: int = 21) -> date` — the calendar date
  `cadence_business_days` business days after `last_recorded` (the earliest date `is_due` flips true).
- `CadenceStatus(last_recorded, today, business_days_elapsed, cadence_business_days, due,
  business_days_until_due)` + `cadence_status(last_recorded, today, *, cadence_business_days=21)` — a
  one-shot summary for a status line (`business_days_until_due = max(0, cadence − elapsed)`).

## 3. Fail-closed / validation

`cadence_business_days >= 1` else `ValueError`. `business_days_between` with `end < start` → 0 (not
negative). `next_due_date` requires `cadence_business_days >= 1`. All functions are total (no exceptions
beyond the validation).

## 4. Tests

`business_days_between`: same day → 0; Mon→Tue → 1; Fri→Mon → 1 (weekend skipped); Mon→next-Mon → 5;
end before start → 0; a 21-business-day span lands on the right calendar date. `is_due`: `None` → True
(T0); elapsed `== cadence` → True; elapsed `< cadence` → False; just-recorded today → False.
`next_due_date`: last + 21 business days = the expected calendar date; `is_due` is False the day before
and True on `next_due_date`. `cadence_status`: fields consistent (`due == (elapsed >= cadence)`,
`business_days_until_due` floored at 0). `cadence_business_days < 1` → ValueError.

## 5. Deferred

Market-holiday calendar (NYSE/holidays); wiring into `scripts/fund_book_oos.py --record` as a
`--cadence-days` guard (small follow-on); the cron registration itself (operational, needs the data
environment).
