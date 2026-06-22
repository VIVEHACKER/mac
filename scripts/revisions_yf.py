"""Free (no-key) estimate-revision adapter via yfinance — the no-key path.

True historical consensus-revision time series are paid (FMP / 와이즈리포트). But yfinance exposes the
CURRENT revision state for free: `eps_trend` (estimate now vs 7/30/60/90 days ago) and `eps_revisions`
(up/down analyst counts in the window) and `analyst_price_targets` (mean). This builds a CURRENT
`EstimateRevision` per symbol from those, so `signals.revisions.revision_signals` runs on REAL data with
no API key. Target-price *prev* is not available free -> the tp component is 0 (guarded); EPS-estimate
revision + up/down breadth drive the signal.

Validate-before-trust: this is a single CURRENT cross-section -> forward-IC accrues FORWARD (record the
snapshot, score in N days, like the Phase-2 paper-OOS ledger). A historical IC backfill still needs a
paid revision time series. yfinance values can be stale/rate-limited — treat as best-effort.
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from signals.revisions import EstimateRevision, revision_signals  # noqa: E402

# (eps_trend prior column, eps_revisions up col, eps_revisions down col) per supported window.
_WINDOWS = {
    "30d": ("30daysAgo", "upLast30days", "downLast30days"),
    "7d": ("7daysAgo", "upLast7days", "downLast7Days"),  # note yfinance's capital D in down7
}


def revision_from_yf_frames(
    symbol: str,
    eps_trend,  # DataFrame indexed by period (0q/+1q/0y/+1y) with current/Ndaysago cols
    eps_revisions,  # DataFrame indexed by period with up/down count cols
    price_targets: dict | None,
    *,
    as_of: date,
    period: str = "0y",
    window: str = "30d",
) -> EstimateRevision | None:
    """Pure mapping of yfinance analyst frames -> one EstimateRevision (no network). None if the chosen
    period row is missing. n_total = up + down revisers in the window (thin activity -> screened by
    min_coverage downstream); target_price_prev is None (not free), so the tp component is 0."""
    prior_col, up_col, down_col = _WINDOWS[window]
    try:
        cur = float(eps_trend.loc[period, "current"])
        prev = float(eps_trend.loc[period, prior_col])
    except (KeyError, TypeError, ValueError):
        return None
    n_up = n_down = 0
    try:
        n_up = int(eps_revisions.loc[period, up_col])
        n_down = int(eps_revisions.loc[period, down_col])
    except (KeyError, TypeError, ValueError):
        pass
    tp = None
    if price_targets:
        m = price_targets.get("mean")
        tp = float(m) if m else None
    return EstimateRevision(
        symbol=symbol.upper(),
        market="us",
        as_of=as_of,
        target_price=tp,
        target_price_prev=None,  # not available free
        eps_estimate=cur,
        eps_estimate_prev=prev,
        n_up=n_up,
        n_down=n_down,
        n_total=n_up + n_down,
    )


def fetch_yf_revisions(
    symbols: list[str], *, as_of: date, period: str = "0y", window: str = "30d"
) -> list[EstimateRevision]:
    """Network: fetch current revision state per symbol via yfinance. Best-effort (skips on error)."""
    import yfinance as yf

    out: list[EstimateRevision] = []
    for sym in symbols:
        try:
            t = yf.Ticker(sym)
            rec = revision_from_yf_frames(
                sym,
                t.eps_trend,
                t.eps_revisions,
                t.analyst_price_targets,
                as_of=as_of,
                period=period,
                window=window,
            )
        except Exception:  # noqa: BLE001 — yfinance is flaky; one bad symbol must not kill the run
            rec = None
        if rec is not None:
            out.append(rec)
    return out


_CSV_COLS = [
    "date",
    "symbol",
    "target_price",
    "target_price_prev",
    "eps_estimate",
    "eps_estimate_prev",
    "n_up",
    "n_down",
    "n_total",
]


def append_revisions_csv(path: Path, revs: list[EstimateRevision]) -> int:
    """Append a snapshot to a revisions CSV (the scripts/revisions_ic.py format) so forward-IC accrues
    over time. Append-only: refuses if this as_of date is already recorded. Returns rows written."""
    if not revs:
        return 0
    snap_date = revs[0].as_of.isoformat()
    path = Path(path)
    if path.exists():
        with path.open(newline="", encoding="utf-8") as fh:
            if any((r.get("date") or "").strip() == snap_date for r in csv.DictReader(fh)):
                raise ValueError(
                    f"revisions for {snap_date} already recorded in {path} (append-only)"
                )
    new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(_CSV_COLS)
        for r in revs:
            w.writerow(
                [
                    r.as_of.isoformat(),
                    r.symbol,
                    "" if r.target_price is None else r.target_price,
                    "" if r.target_price_prev is None else r.target_price_prev,
                    "" if r.eps_estimate is None else r.eps_estimate,
                    "" if r.eps_estimate_prev is None else r.eps_estimate_prev,
                    r.n_up,
                    r.n_down,
                    r.n_total,
                ]
            )
    return len(revs)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Free yfinance estimate-revision signal (no API key)")
    p.add_argument("symbols", nargs="*", help="tickers, e.g. CL HD INTC LRCX MU QCOM TGT")
    p.add_argument(
        "--megacaps", action="store_true", help="use the validated MEGACAPS universe (for cron)"
    )
    p.add_argument("--period", default="0y", choices=["0q", "+1q", "0y", "+1y"])
    p.add_argument("--window", default="30d", choices=["30d", "7d"])
    p.add_argument("--min-coverage", type=int, default=3)
    p.add_argument(
        "--max-downgrades", type=int, default=None, help="0 = the video's downgrade filter"
    )
    p.add_argument(
        "--record",
        type=Path,
        default=None,
        help="append this snapshot to a revisions CSV (forward-IC)",
    )
    args = p.parse_args(argv)

    symbols = list(args.symbols)
    if args.megacaps:
        from scripts.aqr_ideal_walkforward import MEGACAPS  # lazy: pulls heavy deps only when used

        symbols = list(MEGACAPS)
    if not symbols:
        p.error("pass tickers or --megacaps")

    as_of = datetime.now().date()  # noqa: DTZ005 — local date is fine for a snapshot label
    revs = fetch_yf_revisions(symbols, as_of=as_of, period=args.period, window=args.window)
    if args.record is not None:
        try:
            n = append_revisions_csv(args.record, revs)
            print(f"recorded {n} revisions for {as_of} -> {args.record}\n")
        except ValueError as e:
            print(f"(record skipped: {e})\n")
    sigs = revision_signals(
        revs, min_coverage=args.min_coverage, max_downgrades=args.max_downgrades
    )
    print(f"estimate-revision signal ({args.period} EPS, {args.window} window, {len(revs)} names)")
    print("-" * 70)
    for s in sigs:
        print(f"  {s.symbol:<6} {s.direction:<5} score={s.score:+.3f}  {s.reason}")
    if not sigs:
        print("  (no names passed the coverage / downgrade screen)")
    print(
        "\nNOTE: single current cross-section. forward-IC accrues forward (record + score later)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
