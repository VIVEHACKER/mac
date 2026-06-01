"""Survivorship audit — quantify the bias the compounder validation cannot eliminate, and prove
which conclusions survive it.

Full survivorship-free validation needs CRSP-grade PIT index membership + delisted-name returns,
which are not available free (empirically: yfinance serves ~0 history for delisted tickers — see
the live check this script runs). So this audit does the achievable, honest thing instead:

  1. FORWARD survivorship (measurable): how much of the current 1,003-name universe even existed
     (had prices) at each past rebalance — young names that IPO'd/joined later are absent from
     early backtests, biasing them.
  2. The KEY robustness argument, with numbers: the backtest's funnel leg AND its equal-weight
     benchmark are both drawn from the SAME names-present-at-each-rebalance set, so the
     funnel-minus-benchmark EXCESS is survivorship-NEUTRAL — the "no selection edge" verdict does
     NOT depend on survivorship; only the inflated ABSOLUTE CAGR does.
  3. The hard limit, evidence-backed: a live probe of known-delisted tickers showing free price
     data is unavailable, so the BACKWARD bias (2012 names now dead) cannot be removed here.

Output: out/survivorship-audit.md
"""

from __future__ import annotations

import argparse
import csv
import sys
import warnings
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from collections.abc import Sequence  # noqa: E402

from data.fundamentals_snapshot import read_fundamentals_snapshot  # noqa: E402
from data.models import FundamentalRecord  # noqa: E402
from data.price_snapshot import read_price_snapshot  # noqa: E402
from engine.compounder import rank_compounders  # noqa: E402
from scripts.compounder_heldout_oos import price_asof  # noqa: E402

DEFAULT_UNIVERSE = ROOT / "data" / "universes" / "sp400-600-current.csv"
DEFAULT_SNAPSHOT = ROOT / "data" / "snapshots" / "fundamentals-2026-06-01-gp2.csv"
DEFAULT_SECTORS = ROOT / "data" / "sectors" / "sp400-600-current-sectors.csv"
DEFAULT_PRICES = ROOT / "data" / "snapshots" / "prices-2026-06-01.csv"
DEFAULT_OUT = ROOT / "out" / "survivorship-audit.md"
AS_OF = [date(y, 6, 30) for y in range(2012, 2025)]
# Known acquired/delisted/merged S&P mid-large caps (2015-2023) — the live data-availability probe.
DELISTED_PROBE = [
    "CELG",
    "XLNX",
    "ATVI",
    "RTN",
    "MON",
    "ETFC",
    "XEC",
    "TIF",
    "MYL",
    "ALXN",
    "CXO",
    "VAR",
]


def load_symbols(path: Path) -> list[str]:
    with path.open(encoding="utf-8") as f:
        return sorted({r["symbol"].upper() for r in csv.DictReader(f)})


def load_sectors(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[r["symbol"].upper()] = r.get("sector") or "unknown"
    return out


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--prices", type=Path, default=DEFAULT_PRICES)
    p.add_argument("--universe", type=Path, default=DEFAULT_UNIVERSE)
    p.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    p.add_argument("--sectors", type=Path, default=DEFAULT_SECTORS)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument(
        "--probe-delisted",
        action="store_true",
        help="live yfinance probe of known-delisted tickers (network)",
    )
    return p.parse_args()


def probe_delisted_availability() -> list[tuple[str, int]]:
    """Return [(ticker, n_bars)] from yfinance for known-delisted names (evidence of the limit)."""
    import yfinance as yf

    warnings.filterwarnings("ignore")
    out: list[tuple[str, int]] = []
    for t in DELISTED_PROBE:
        try:
            h = yf.download(
                t, start="2013-01-01", end="2024-01-01", auto_adjust=True, progress=False
            )
            n = int(h["Close"].dropna().shape[0]) if "Close" in h else 0
        except Exception:
            n = -1
        out.append((t, n))
    return out


def main() -> None:
    args = parse_args()
    if not args.prices.exists():
        raise SystemExit(f"pinned prices not found: {args.prices}")
    if not args.snapshot.exists():
        # The rank-eligible counts (the audit's core comparison to the backtest) need the
        # fundamentals snapshot; without it they'd silently be 0 and the report would mislead.
        raise SystemExit(
            f"fundamentals snapshot not found: {args.snapshot}\n"
            "The rank-eligible counts require it — pass --snapshot or regenerate."
        )
    symbols = load_symbols(args.universe)
    sectors = load_sectors(args.sectors)
    closes = read_price_snapshot(args.prices, verify=True)
    n_total = len(symbols)
    funds: dict[str, list[FundamentalRecord]] = {}
    for rec in read_fundamentals_snapshot(args.snapshot, verify=True):
        funds.setdefault(rec.symbol.upper(), []).append(rec)
    for v in funds.values():
        v.sort(key=lambda r: r.asof_ts)

    # forward survivorship at each past as-of: PRICE-only availability vs the actual
    # RANK-ELIGIBLE count the backtest uses (price + >=2 PIT records + rank_compounders gates).
    present: list[tuple[date, int, int]] = []
    for as_of in AS_OF:
        priced = [
            s for s in symbols if s in closes.columns and price_asof(closes[s], as_of) is not None
        ]
        n_price = len(priced)
        universe: dict[str, tuple[Sequence[FundamentalRecord], float]] = {}
        for s in priced:
            recs = [r for r in funds.get(s, []) if r.asof_ts.date() <= as_of]
            if len(recs) < 2:
                continue
            p0 = price_asof(closes[s], as_of)
            if p0 is not None and p0 > 0:
                universe[s] = (recs, p0)
        n_elig = len(rank_compounders(universe, top_n=10_000, sectors=sectors)) if universe else 0
        present.append((as_of, n_price, n_elig))

    probe: list[tuple[str, int]] | None = None
    if args.probe_delisted:
        print("Probing yfinance for delisted tickers (network)...")
        probe = probe_delisted_availability()

    md = [
        "# Survivorship Audit — what the compounder validation can and cannot remove",
        "",
        "Research-only. Full survivorship-free validation needs CRSP-grade PIT membership + "
        "delisted returns (not free). This audit quantifies the measurable bias and proves which "
        "conclusions survive it.",
        "",
        f"Current universe: **{n_total}** names (S&P 400+600 as of the snapshot date).",
        "",
        "## 1. Forward survivorship — how much of the current universe existed in the past",
        "",
        "Two columns: names with a PRICE at each rebalance, and the actual RANK-ELIGIBLE count "
        "the backtest uses (price + ≥2 PIT fundamental records + `rank_compounders` coverage "
        "gates). The rest IPO'd / joined later, or lack enough fundamental history. Early years "
        "cover a much smaller, older slice of today's list — and the eligible set is smaller "
        "still, because pre-2014 EDGAR fundamental coverage is thin.",
        "",
        "| As-of | priced | % | rank-eligible | % |",
        "|---|--:|--:|--:|--:|",
    ]
    for as_of, n_price, n_elig in present:
        md.append(
            f"| {as_of} | {n_price} | {100 * n_price / n_total:.0f}% | "
            f"{n_elig} | {100 * n_elig / n_total:.0f}% |"
        )

    first_price, first_elig = present[0][1], present[0][2]
    md += [
        "",
        f"So the 2012 backtest's actual SELECTION universe is only ~{first_elig} of today's "
        f"{n_total} names ({100 * first_elig / n_total:.0f}%; {first_price} have a price but "
        f"fewer clear the fundamental/coverage gates); it fills in over time. This is a real, "
        "sizeable forward bias — and crucially it hits the funnel and the benchmark identically "
        "(both are built from the same rank-eligible set each period).",
        "",
        "## 2. Why the 'no selection edge' verdict is LARGELY (not fully) survivorship-insulated",
        "",
        "In `compounder_backtest.py`, at each rebalance BOTH legs are built from the SAME "
        "rank-eligible set: the funnel holds the top-30 by score, the benchmark holds ALL "
        "eligible names equal-weight. Survivorship distorts the *shared* set identically, so it "
        "inflates the common ABSOLUTE CAGR without creating the relative gap — the **funnel − "
        "benchmark excess (small, risk-adjusted-negative — see `out/compounder-backtest.md` for "
        "the current figures) is insulated from the SHARED inflation.** IMPORTANT CAVEAT (not "
        "over-claiming): this is only conditional on the "
        "survivor universe. Adding back the missing delisted/acquired names need NOT hit both legs "
        "equally — if those dead names had systematically different scores OR returns, they would "
        "enter the all-name benchmark always but the top-30 only when high-scoring, so the excess "
        "COULD shift. So the verdict is robust to the SHARED level bias, but not *proven* immune "
        "to the composition of the missing names (which we cannot load — §3). Direction of doubt: "
        "if dead names were disproportionately low-score losers, the benchmark would drop more "
        "than the top-30 and the funnel would look BETTER than the small excess in "
        "`out/compounder-backtest.md` — i.e. the bias plausibly works AGAINST the funnel here, "
        "making the 'no edge' read, if anything, generous to the funnel.",
        "",
        "What survivorship DOES bias: the absolute return level, and the QUALITY-IC sign "
        "specifically (acquired high-quality names exit the survivor set, dragging measured "
        "quality IC down — i.e. against, not for, any pro-quality conclusion). Both are documented "
        "in `docs/COMPOUNDER_VALIDATION.md`.",
        "",
        "## 3. The hard limit — backward survivorship cannot be removed with free data",
        "",
        "Removing the backward bias (2012-era names now delisted/acquired) needs their prices. "
        "yfinance does not serve delisted tickers:",
        "",
    ]
    if probe is not None:
        zero = sum(1 for _, n in probe if n == 0)  # genuine zero-bar (not request errors)
        errs = sum(1 for _, n in probe if n < 0)  # request failures — NOT evidence of delisting
        served = sum(1 for _, n in probe if n > 0)
        if errs:
            md.append(
                f"⚠️ INCONCLUSIVE probe: {errs}/{len(probe)} requests errored (offline/rate-limited) "
                "— request failures are NOT counted as zero-bar evidence. Re-run online."
            )
            md.append("")
        md += [
            f"Live probe of {len(probe)} known-delisted S&P names: **{zero} returned genuine "
            f"zero bars**, {served} returned data, {errs} errored.",
            "",
            "| ticker | yfinance bars |",
            "|---|--:|",
        ]
        for t, n in probe:
            md.append(f"| {t} | {n if n >= 0 else 'request error'} |")
    else:
        md.append(
            f"(Run with `--probe-delisted` to attach the live evidence; the probe set is "
            f"{len(DELISTED_PROBE)} known-delisted S&P names, which returned 0 usable yfinance "
            "bars when last run — see `out/survivorship-audit.md` regenerated with the flag.)"
        )
    md += [
        "",
        "## Verdict",
        "",
        "- **Forward bias: quantified** (above) and shared by both backtest legs.",
        "- **Core verdict (no selection edge): insulated from the SHARED survivor inflation** "
        "(it is a relative excess between two legs of the same survivor set), and the plausible "
        "direction of the residual (missing-name composition) works AGAINST the funnel — so the "
        "verdict is conservative, though not *proven* immune to that composition.",
        "- **Backward bias: cannot be removed free** (delisted prices unavailable; full PIT "
        "membership needs CRSP). It biases the QUALITY result DOWNWARD (against pro-quality "
        "claims), so the documented 'net-margin quality anti-predicts' finding is conservative, "
        "not flattered, by it.",
        "",
        "Net (stated conditionally): UNDER the survivor universe, no conclusion here is "
        "overturned — the no-edge verdict is a relative excess insulated from the shared "
        "inflation, and the quality finding is biased against itself. But this is CONDITIONAL: if "
        "the missing delisted/acquired names have systematically different scores or returns, the "
        "relative excess could shift, so full immunity is NOT proven. A survivorship-free re-test "
        "(CRSP PIT membership + delisted returns) is the only way to lift the conditionality and "
        "the absolute-return / quality-magnitude caveats; it is a paid-data task, not a code task.",
    ]
    args.out.write_text("\n".join(md) + "\n", encoding="utf-8")
    print(
        "\n".join(
            md[
                : md.index(
                    "## 2. Why the 'no selection edge' verdict is LARGELY (not fully) "
                    "survivorship-insulated"
                )
            ]
        )
    )
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
