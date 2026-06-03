"""A — microcap feasibility probe: CAN free data even support systematic microcap validation?

The small-AUM advantage is real, but it lives in TRUE microcaps / special situations — names far
below the S&P 600 (our current universe floors at ~$0.8B; only 2 names are <$300M). Before
building any microcap strategy, this probe answers the honest gate question the user demanded:
can free data (SEC EDGAR + yfinance) support a SURVIVORSHIP-clean microcap backtest, or is
microcap only viable as a live screen for human judgment?

It samples SEC filers OUTSIDE the current universe, then measures, per name:
  - market cap (SEC shares × latest yfinance price) → is it actually microcap?
  - fundamentals coverage (does companyfacts have revenue / gross profit / assets / shares?)
  - price coverage (yfinance bars; ≥3y history?)
  - survivorship signal (alive today vs no/short history → delisted)

The killer for microcap backtesting is survivorship: microcaps delist/go bankrupt at high rates,
yfinance serves ~0 history for delisted names (proven in survivorship_audit), so a backtest on
*surviving* microcaps is massively upward-biased. This probe quantifies that reality.

Output: out/microcap-feasibility.md   (network-heavy; --sample controls cost)
"""

from __future__ import annotations

import argparse
import csv
import sys
import warnings
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CURRENT_UNIVERSE = ROOT / "data" / "universes" / "sp400-600-current.csv"
DEFAULT_OUT = ROOT / "out" / "microcap-feasibility.md"
MICROCAP_MAX = 300e6  # $300M
SMALL_MAX = 2e9  # $2B


def load_current(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with path.open(encoding="utf-8") as f:
        return {r["symbol"].upper() for r in csv.DictReader(f)}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sample", type=int, default=200, help="how many SEC filers to probe")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if args.sample <= 0:
        raise SystemExit("--sample must be a positive integer (it bounds a network-heavy probe)")
    warnings.filterwarnings("ignore")
    import yfinance as yf

    from scripts.sec_edgar_ingest import build_records, load_cik_map

    current = load_current(CURRENT_UNIVERSE)
    print("Loading SEC filer list...")
    cik_map = load_cik_map()
    # candidates = SEC filers NOT already in our small/mid universe; deterministic stride sample
    candidates = sorted(t for t in cik_map if t.upper() not in current)
    stride = max(1, len(candidates) // args.sample)
    sample = candidates[::stride][: args.sample]
    print(f"{len(cik_map)} filers; probing {len(sample)} (stride {stride})...")

    rows: list[dict] = []
    for i, tkr in enumerate(sample, 1):
        cik = cik_map[tkr]
        rec = {
            "ticker": tkr,
            "bars": 0,
            "last": "",
            "mktcap": None,
            "has_rev": False,
            "has_gp": False,
            "has_assets": False,
            "price_err": False,  # request FAILED (transient) — distinct from genuine 0-bar
            "fund_err": False,
        }
        last_px = None
        try:
            h = yf.download(
                tkr, start="2012-01-01", end="2026-06-01", auto_adjust=True, progress=False
            )
            raw_close = h["Close"] if "Close" in h else pd.Series(dtype=float)
            # yfinance 1.3 returns a 1-column DataFrame for a single ticker (MultiIndex); reduce
            # to a Series so iloc[-1] is a scalar, not a 1-row Series (which float() rejects).
            if isinstance(raw_close, pd.DataFrame):
                raw_close = raw_close.iloc[:, 0]
            close = raw_close.dropna()
            rec["bars"] = int(len(close))
            if rec["bars"]:
                rec["last"] = str(close.index[-1].date())
                last_px = float(close.iloc[-1])
        except Exception:
            rec["price_err"] = True  # could not determine — NOT evidence of zero history
        try:
            recs = build_records(tkr, cik)
            if recs:
                latest = recs[-1]
                rec["has_rev"] = latest.revenue is not None
                rec["has_gp"] = latest.gross_profit is not None
                rec["has_assets"] = latest.total_assets is not None
                if rec["bars"] and last_px and latest.shares_out:
                    rec["mktcap"] = last_px * latest.shares_out
        except Exception:
            rec["fund_err"] = True
        rows.append(rec)
        if i % 25 == 0:
            print(f"  {i}/{len(sample)}")

    n_sampled = len(rows)
    price_err = sum(1 for r in rows if r["price_err"])
    fund_err = sum(1 for r in rows if r["fund_err"])
    # Coverage stats use SUCCESS-only denominators, separately per data source — a transient
    # yfinance/SEC failure is "unknown", NOT "missing", and must not flip the verdict on a flaky
    # run. PRICE stats over price-probed-OK; FUNDAMENTAL/mktcap stats require BOTH requests OK
    # (mktcap needs price × SEC shares).
    price_ok = [r for r in rows if not r["price_err"]]
    n = len(price_ok)  # price-coverage denominator
    with_px = [r for r in price_ok if r["bars"] > 0]
    alive = [r for r in price_ok if r["bars"] >= 750]  # ~3y of trading days
    both_ok = [r for r in rows if not r["price_err"] and not r["fund_err"]]
    n_both = len(both_ok)  # mktcap/fundamental denominator (both sources succeeded)
    with_caps = [r for r in both_ok if r["mktcap"] is not None]
    micro = [r for r in with_caps if r["mktcap"] < MICROCAP_MAX]
    micro_alive = [r for r in micro if r["bars"] >= 750]
    micro_funds = [r for r in micro if r["has_rev"] and r["has_assets"]]

    def pct(k: int) -> str:
        return f"{100 * k / n:.0f}%" if n else "n/a"

    md = [
        "# Microcap Feasibility Probe (A) — can free data support systematic microcap validation?",
        "",
        "Research-only. Honest gate before building any microcap strategy. Sampled "
        f"{n_sampled} SEC filers OUTSIDE the current S&P 400+600 universe (deterministic stride). "
        f"Of these, {price_err} price requests and {fund_err} fundamental requests ERRORED "
        "(transient/throttled) and are EXCLUDED from the relevant coverage denominators below "
        f"(not counted as missing data). Price coverage is over {n} price-probed names; "
        f"market-cap/fundamental coverage is over {n_both} names where BOTH requests succeeded.",
        "",
        "## Coverage of the successfully-probed sample",
        "",
        f"- Any yfinance price history: **{len(with_px)}/{n}** ({pct(len(with_px))})",
        f"- ≥3y of bars (≈ alive & tradable): **{len(alive)}/{n}** ({pct(len(alive))})",
        f"- Market cap computable (SEC shares × price), of {n_both} both-ok: {len(with_caps)}",
        f"- Of those, TRUE microcap (<$300M): **{len(micro)}**",
        f"  - microcap WITH ≥3y price history: **{len(micro_alive)}**",
        f"  - microcap WITH revenue+assets fundamentals: **{len(micro_funds)}**",
        "",
        "## The survivorship reality (the killer for microcap backtests)",
        "",
        f"Of {len(with_px)} names with ANY history, {len(with_px) - len(alive)} have <3y of bars "
        "(recent listings OR names that delisted/stopped trading). Combined with the proven fact "
        "that yfinance serves ~0 history for already-delisted tickers (`survivorship_audit.py`: "
        "12/12 dead names = 0 bars), the microcaps we CAN load are precisely the SURVIVORS — and "
        "microcap survivors are the most upward-biased subset in equities (the high base-rate of "
        "microcap delistings/bankruptcies is entirely invisible). A backtest on loadable "
        "microcaps would therefore overstate returns far more severely than the S&P-universe "
        "studies already flagged.",
        "",
        "## Verdict",
        "",
    ]
    inconclusive = len(micro) < 10  # too few microcaps sampled to judge coverage either way
    feasible_screen = (not inconclusive) and len(micro_funds) >= 0.3 * len(micro)
    md += [
        (
            f"**INCONCLUSIVE — only {len(micro)} microcaps in the sample.** That is not evidence of "
            "thin coverage; the stride sample just didn't hit enough sub-$300M names. Re-run with a "
            "larger --sample (or a market-cap-targeted candidate list) before drawing a "
            "supportability conclusion."
            if inconclusive
            else "**Microcap is viable as a LIVE SCREEN, NOT as a systematically-validatable "
            f"backtest.** Fundamentals are obtainable for a fair share of live microcaps "
            f"({len(micro_funds)} of {len(micro)} sampled microcaps had revenue+assets), so we CAN "
            "rank live microcap candidates for human judgment. But we CANNOT honestly backtest a "
            "microcap strategy on free data: the loadable set is survivor-only, and the (invisible) "
            "delisted base rate is what dominates microcap risk. Any microcap backtest IC/return "
            "here would be a survivorship artifact — exactly the trap this project refuses to fall "
            "into."
            if feasible_screen
            else "**Microcap is not well-supported even as a screen on this free data** — "
            f"fundamentals coverage for sampled microcaps was thin ({len(micro_funds)}/{len(micro)}). "
            "Neither a screen nor a backtest is reliably buildable without a paid microcap data "
            "vendor."
        ),
        "",
        "Implication for the user's 'higher returns via small-AUM' goal: the small-AUM edge is "
        "real, but capturing it in microcaps is a HUMAN-JUDGMENT / special-situations game that "
        "free data can SCREEN for but cannot VALIDATE — so it must be run as a conviction process "
        "(deep per-name work), not a backtested system. Honest backtesting of the small-AUM edge "
        "needs a survivorship-free microcap dataset (CRSP / paid), the same paid-data wall as the "
        "S&P survivorship audit.",
        "",
        "Caveats: sample is a deterministic stride over SEC tickers (includes some ADRs/funds/"
        "SPACs); market caps use latest SEC shares × latest price (approximate); 'alive' proxied "
        "by ≥3y bars. Fundamental ERRORS are excluded as 'unknown' but this conflates transient "
        "throttling with permanent companyfacts-absence — so fundamental coverage here is an "
        "UPPER bound (true microcap fundamental availability is ≤ shown). The verdict does not "
        "hinge on the exact fraction: it rests on the survivorship argument (delisted microcaps "
        "are unloadable), which is unaffected. Increase --sample for a tighter estimate.",
    ]
    args.out.parent.mkdir(parents=True, exist_ok=True)  # out/ is gitignored; may not exist
    args.out.write_text("\n".join(md) + "\n", encoding="utf-8")
    start_i = next((i for i, line in enumerate(md) if line.startswith("## Coverage")), 0)
    print("\n" + "\n".join(md[start_i:]))
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
