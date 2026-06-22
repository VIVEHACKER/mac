"""Driver: forward-IC validation of the estimate-revision signal (H1-H5 from the learning note).

Source-agnostic: feed a revisions CSV (any export — FMP, 와이즈리포트 scrape, backfill) + a price
snapshot; this computes N-day forward returns and runs `signals.revisions.revision_ic_report` across
dates, printing per-variant ICStats. The live data feed is the only remaining dependency — the machinery
(signal + IC harness) is fully tested. A variant whose mean IC is ~0 / negative is NOT trusted.

Revisions CSV columns: date,symbol,target_price,target_price_prev,eps_estimate,eps_estimate_prev,
n_up,n_down,n_total  (one row per symbol per as_of snapshot date).
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.price_snapshot import read_price_snapshot  # noqa: E402
from signals.revisions import EstimateRevision, revision_ic_report  # noqa: E402


def _f(row: dict[str, str], key: str) -> float | None:
    v = (row.get(key) or "").strip()
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _i(row: dict[str, str], key: str) -> int:
    v = (row.get(key) or "").strip()
    try:
        return int(float(v)) if v else 0
    except ValueError:
        return 0


def load_revisions_csv(path: Path) -> dict[date, list[EstimateRevision]]:
    """Parse a revisions CSV into {as_of date -> [EstimateRevision]}. Missing numeric cells -> None/0."""
    out: dict[date, list[EstimateRevision]] = {}
    with Path(path).open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fields = reader.fieldnames or []
        if "date" not in fields or "symbol" not in fields:
            raise ValueError(f"revisions CSV needs 'date' and 'symbol' columns (got {fields})")
        for row in reader:
            d = date.fromisoformat((row["date"]).strip()[:10])
            out.setdefault(d, []).append(
                EstimateRevision(
                    symbol=row["symbol"].strip().upper(),
                    market=(row.get("market") or "us").strip() or "us",
                    as_of=d,
                    target_price=_f(row, "target_price"),
                    target_price_prev=_f(row, "target_price_prev"),
                    eps_estimate=_f(row, "eps_estimate"),
                    eps_estimate_prev=_f(row, "eps_estimate_prev"),
                    n_up=_i(row, "n_up"),
                    n_down=_i(row, "n_down"),
                    n_total=_i(row, "n_total"),
                )
            )
    return out


def forward_returns_from_prices(
    prices,  # wide DataFrame (date index x symbol columns), from read_price_snapshot
    snapshots: Mapping[date, Sequence[EstimateRevision]],
    *,
    fwd_days: int = 21,
) -> dict[date, dict[str, float]]:
    """For each snapshot date, the forward return symbol -> (close at first index >= date+fwd_days) /
    (close at last index <= date) - 1. Symbols/dates without usable marks are skipped (PIT: the entry
    price is at/<= the snapshot date; the forward price is strictly after)."""
    import pandas as pd

    out: dict[date, dict[str, float]] = {}
    idx = prices.index
    for d, revs in snapshots.items():
        ts = pd.Timestamp(d)
        entry_pos = idx.searchsorted(ts, side="right") - 1  # last index <= d
        fwd_pos = idx.searchsorted(ts + pd.Timedelta(days=fwd_days), side="left")  # first >= d+fwd
        if entry_pos < 0 or fwd_pos >= len(idx) or fwd_pos <= entry_pos:
            continue
        row: dict[str, float] = {}
        for rv in revs:
            s = rv.symbol
            if s not in prices.columns:
                continue
            buy = prices[s].iloc[entry_pos]
            sell = prices[s].iloc[fwd_pos]
            if pd.notna(buy) and pd.notna(sell) and buy > 0:
                row[s] = float(sell) / float(buy) - 1.0
        if row:
            out[d] = row
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Estimate-revision signal forward-IC validation")
    p.add_argument(
        "--revisions", type=Path, required=True, help="revisions CSV (see module docstring)"
    )
    p.add_argument(
        "--prices", type=Path, required=True, help="price snapshot (long: symbol,date,close)"
    )
    p.add_argument("--fwd-days", type=int, default=21)
    args = p.parse_args(argv)

    try:
        snaps = load_revisions_csv(args.revisions)
        prices = read_price_snapshot(args.prices, verify=False)
        fwd = forward_returns_from_prices(prices, snaps, fwd_days=args.fwd_days)
        report = revision_ic_report(snaps, fwd)
    except (ValueError, FileNotFoundError) as e:
        raise SystemExit(str(e)) from e

    print(f"estimate-revision forward-IC ({args.fwd_days}d fwd, {len(snaps)} snapshot dates)")
    print("-" * 60)
    for variant, st in report.items():
        mean = f"{st.mean:+.3f}" if st.mean is not None else "  n/a"
        t = f"{st.t_stat:+.2f}" if st.t_stat is not None else " n/a"
        print(f"  {variant:<13} n={st.n:<3} meanIC={mean}  t={t}  pos={st.positive}/{st.n}")
    print("\nverdict: trust a variant only if mean IC > 0 with t > ~2 (and out-of-sample holds).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
