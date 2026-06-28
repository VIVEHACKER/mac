"""Forward out-of-sample (paper) ledger for the chartbloom A-1 finding.

The in-sample event study (merr_corpus/CHARTBLOOM_VALIDATION_RESULTS.md) found that a CHoCH
*without* a supporting same-direction FVG has a negative forward return, while a CHoCH *with*
one is positive — the basis for the ``_CHOCH_FVG_GATE`` deployed in engine/chart/read.py. That
was measured on past data (through 2026-06-19). The only honest confirmation is a forward
record: log each fresh CHoCH at its decision bar, tagged with its FVG-accompaniment, then score
the FVG vs no-FVG forward spread against prices that arrive *later*. A live spread far below the
in-sample +FVG−noFVG figure is the A-1 edge revealing itself as in-sample overfitting.

Mirrors engine/chart_oos.py: price-source agnostic (realised returns passed in), pure, testable.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class ChochSignalEntry:
    logged_ts: str  # ISO datetime of the CHoCH confirmation bar — the moment of the call
    symbol: str
    market: str
    timeframe: str
    direction: str  # 'long' | 'short' (CHoCH direction)
    has_fvg: bool  # supporting same-direction unmitigated FVG present at the bar (gate premise)
    entry_price: float


@dataclass(frozen=True)
class ChochOOSTrackRecord:
    horizon: int
    n_matured: int
    with_fvg_n: int
    with_fvg_mean_fwd: float
    with_fvg_hit_rate: float
    no_fvg_n: int
    no_fvg_mean_fwd: float
    no_fvg_hit_rate: float
    fvg_minus_nofvg: float  # the A-1 spread, OUT OF SAMPLE
    vs_insample: float | None  # live spread / in-sample spread — the overfit-in-the-wild ratio


def entry_key(entry: ChochSignalEntry) -> str:
    """Stable identity for one pre-registered CHoCH signal (one bar, one direction)."""
    return f"{entry.symbol}|{entry.timeframe}|{entry.logged_ts}|{entry.direction}"


def load_chartbloom_ledger(path: Path) -> list[ChochSignalEntry]:
    if not Path(path).exists():
        return []
    entries: list[ChochSignalEntry] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            entries.append(ChochSignalEntry(**json.loads(line)))
    return entries


def append_choch_signal(path: Path, entry: ChochSignalEntry) -> None:
    """Append a pre-registered CHoCH signal. Refuses to rewrite an existing identity.

    The refusal is the point: a forward OOS record is only credible if a signal cannot be
    re-logged (and silently re-tagged) after the fact.
    """
    path = Path(path)
    key = entry_key(entry)
    for existing in load_chartbloom_ledger(path):
        if entry_key(existing) == key:
            raise ValueError(
                f"signal {key} already recorded — the chartbloom OOS ledger is append-only"
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(entry)) + "\n")


def score_chartbloom_ledger(
    entries: list[ChochSignalEntry],
    realized: dict[str, float],
    *,
    horizon: int,
    insample_spread: float | None = None,
) -> ChochOOSTrackRecord:
    """Score matured entries (those with a realised ``horizon``-bar forward return).

    ``realized`` maps ``entry_key(entry) -> direction-signed forward return`` for entries whose
    horizon has elapsed; immature entries are absent and skipped. Returns the with-FVG vs
    no-FVG forward means and the (with − without) spread that A-1 predicts to be positive, plus
    the live/in-sample ratio when the in-sample spread is supplied.
    """
    matured = [(e, realized[entry_key(e)]) for e in entries if entry_key(e) in realized]

    def _stats(rets: list[float]) -> tuple[float, float]:
        if not rets:
            return 0.0, 0.0
        return statistics.mean(rets), sum(1 for r in rets if r > 0) / len(rets)

    wf = [r for e, r in matured if e.has_fvg]
    nf = [r for e, r in matured if not e.has_fvg]
    wf_mean, wf_hit = _stats(wf)
    nf_mean, nf_hit = _stats(nf)
    spread = wf_mean - nf_mean
    vs_insample = (
        spread / insample_spread if insample_spread is not None and insample_spread != 0.0 else None
    )

    return ChochOOSTrackRecord(
        horizon=horizon,
        n_matured=len(matured),
        with_fvg_n=len(wf),
        with_fvg_mean_fwd=wf_mean,
        with_fvg_hit_rate=wf_hit,
        no_fvg_n=len(nf),
        no_fvg_mean_fwd=nf_mean,
        no_fvg_hit_rate=nf_hit,
        fvg_minus_nofvg=spread,
        vs_insample=vs_insample,
    )
