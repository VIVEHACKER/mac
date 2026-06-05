"""Persistent equity reference tracker for the portfolio kill-switch.

The kill-switch needs two reference points the broker account alone does not carry:
  - reference_equity: start-of-day equity (the daily-loss latch baseline)
  - peak_equity: all-time equity high-water mark (the sleeve / trailing latch baseline)

Both must survive across process invocations (each live-submit cycle is a fresh process), so they
live in a small JSON file alongside the halt state. Update once per cycle with the current account
equity; the returned refs are fed to ``process_order_intents``.
"""

from __future__ import annotations

import fcntl
import json
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path


@dataclass(frozen=True)
class EquityRefs:
    reference_equity: float
    peak_equity: float


class EquityTrackStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def update(
        self,
        current_equity: float,
        *,
        today: date | None = None,
        prior_close: float | None = None,
    ) -> EquityRefs:
        if current_equity <= 0:
            raise ValueError("current_equity must be positive")
        if today is None:
            today = datetime.now(UTC).date()
        state = self._read()
        # Peak floor includes the broker's prior close so the -25% sleeve latch is armed correctly
        # even on the very first observation (the account may have been higher yesterday).
        peak = max(state.get("peak", 0.0), current_equity, prior_close or 0.0)
        prev_last = state.get("last_equity")
        if prior_close is not None and prior_close > 0:
            # Authoritative broker prior-session close = the daily-loss baseline. Robust to first
            # deploy / new account / deleted state and timezone (Codex P1). Stable intraday, so it
            # is used on every cycle, not only on the day-roll.
            day_start_equity = float(prior_close)
            day_start_date = today.isoformat()
        elif state.get("day_start_date") != today.isoformat():
            # Local fallback (no broker prior-close, e.g. fake/paper): the prior session's last
            # observed equity, or current on first ever. A brand-new local state on a real broker
            # WITHOUT prior-close cannot detect an at-open gap on day 1 — that is why live wiring
            # feeds broker last_equity above.
            day_start_equity = float(prev_last) if prev_last is not None else current_equity
            day_start_date = today.isoformat()
        else:
            day_start_equity = float(state["day_start_equity"])
            day_start_date = state["day_start_date"]
        # Use the peak the locked write actually persisted (it may be higher if another process
        # bumped it after our read) — otherwise this cycle's kill-switch would be checked against a
        # stale lower peak and could miss the -25% latch (Codex P1).
        merged_peak = self._write(
            {
                "peak": peak,
                "day_start_equity": day_start_equity,
                "day_start_date": day_start_date,
                "last_equity": current_equity,
            }
        )
        return EquityRefs(reference_equity=day_start_equity, peak_equity=merged_peak)

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, payload: dict) -> float:
        # Serialize concurrent writers for this account with an exclusive file lock: under the lock,
        # re-read the on-disk peak and keep the max (no high-water regression), then rename a
        # PER-PROCESS temp into place atomically (no shared-temp collision / FileNotFoundError).
        # Returns the merged peak so the caller checks the kill-switch against the value actually
        # persisted, not a stale pre-merge one. Fixes the overlapping-writer race (Codex P1/P2).
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        with open(lock_path, "w", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                merged_peak = max(payload["peak"], self._read().get("peak", 0.0))
                merged = {**payload, "peak": merged_peak}
                tmp = self.path.with_suffix(self.path.suffix + f".{os.getpid()}.tmp")
                tmp.write_text(
                    json.dumps(merged, indent=2, sort_keys=True) + "\n", encoding="utf-8"
                )
                tmp.replace(self.path)
                return merged_peak
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
